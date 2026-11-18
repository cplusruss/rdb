import io
from pathlib import Path

import docx
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, RGBColor
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QTextFormat

import rdb_model as m
from rdb_markup import marked_bytes
from rdb_contacts import FIELDS, KEYS, cell_text
from rdb_import import HL

PICTURE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".tif", ".tiff", ".bmp"}
HIGHLIGHT = {hexc: idx for idx, hexc in HL.items() if hexc}
GREY = "808080"
URGENT = "D42020"
LINK = "0000FF"
NOTE_INDENT = Inches(0.5)


def _num_for(document, style_name):
    numbering = document.part.numbering_part.element
    style_num = document.styles[style_name].element.pPr.numPr.numId.val
    num = numbering.add_num(numbering.num_having_numId(style_num).abstractNumId.val)
    num.add_lvlOverride(ilvl=0).add_startOverride(1)
    return num.numId


def _set_num(p, num_id, ilvl):
    numpr = p._p.get_or_add_pPr().get_or_add_numPr()
    numpr.get_or_add_ilvl().val = ilvl
    numpr.get_or_add_numId().val = num_id


def _shade(run, color):
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), color[1:].upper())
    run._r.get_or_add_rPr().append(shd)


def _background(run, color):
    if color.lower() in HIGHLIGHT:
        run.font.highlight_color = HIGHLIGHT[color.lower()]
    else:
        _shade(run, color)


def _shade_paragraph(p, color):
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), color[1:].upper())
    p._p.get_or_add_pPr().append(shd)


def _link(p, url):
    h = OxmlElement("w:hyperlink")
    h.set(qn("r:id"), p.part.relate_to(url, RT.HYPERLINK, is_external=True))
    p._p.append(h)
    return h


def _text_run(p, text, f, meta, checked, link):
    r = p.add_run(text)
    if link is not None:
        link.append(r._r)
    r.bold = f.fontWeight() > QFont.Weight.Normal or None
    r.italic = f.fontItalic() or None
    r.underline = f.fontUnderline() or link is not None or None
    r.font.strike = f.fontStrikeOut() or checked or None
    fg = ""
    if f.hasProperty(QTextFormat.Property.ForegroundBrush):
        # a dark document draws the mirror; the file gets the colour as written
        fg = QColor(f.stringProperty(m.ORIG_FG) or f.foreground().color()).name()
    fg = (
        GREY
        if checked
        else (LINK if link is not None else (fg or meta["fg"])[1:].upper())
    )
    if fg:
        r.font.color.rgb = RGBColor.from_string(fg.upper())
    bg = ""
    if (
        f.hasProperty(QTextFormat.Property.BackgroundBrush)
        and f.background().style() != Qt.BrushStyle.NoBrush
    ):
        bg = QColor(f.stringProperty(m.ORIG_BG) or f.background().color()).name()
    bg = bg or meta["bg"]
    if bg:
        _background(r, bg)
    return r


def _block_runs(p, block, store, meta, checked):
    it = block.begin()
    while not it.atEnd():
        fr = it.fragment()
        it += 1
        f = fr.charFormat()
        if f.isImageFormat():
            img = f.toImageFormat()
            if Path(img.name()).suffix.lower() in PICTURE_EXT:
                width = Inches(img.width() / 96) if img.width() else None
                p.add_run().add_picture(
                    io.BytesIO(marked_bytes(store, img.name())), width=width
                )
            else:
                p.add_run(f"[{img.name()}]")
            continue
        link = _link(p, f.anchorHref()) if f.isAnchor() and f.anchorHref() else None
        for i, part in enumerate(m.frag_plain(fr).split(" ")):
            if i:
                _text_run(p, "", f, meta, checked, link).add_break()
            if part:
                _text_run(p, part, f, meta, checked, link)


def _has_content(block):
    it = block.begin()
    while not it.atEnd():
        fr = it.fragment()
        if fr.text().strip() or fr.charFormat().isImageFormat():
            return True
        it += 1
    return False


def _note_paragraphs(document, store, pid):
    for b in m.iter_blocks(m.open_doc(store, pid, m.NOTE_MODE)):
        if _has_content(b):
            p = document.add_paragraph()
            # the shading goes in before the indent: pPr children have a fixed order
            _shade_paragraph(p, m.DEFAULT_SETTINGS["note_bg"])
            p.paragraph_format.left_indent = NOTE_INDENT
            _block_runs(p, b, store, m.get_meta(b), False)


def _contacts_table(document, rows):
    table = document.add_table(rows=1, cols=len(FIELDS), style="Table Grid")
    for cell, (_, label) in zip(table.rows[0].cells, FIELDS):
        cell.paragraphs[0].add_run(label).bold = True
    for r in rows:
        for cell, k in zip(table.add_row().cells, KEYS):
            cell.text = cell_text(r, k)


def export_docx(store, projects, path):
    document = docx.Document()
    for i, (pid, title, mode) in enumerate(projects):
        if i:
            document.add_paragraph()
        document.add_paragraph().add_run(title).bold = True
        if store.has_contacts(pid):
            _contacts_table(document, store.get_contacts(pid))
            continue
        if not store.has_project(pid):
            continue
        doc = m.open_doc(store, pid, mode)
        num_id, note = None, ""
        for b in m.iter_blocks(doc):
            if m.kind(b) in (m.TODO, m.HEADING, m.SECTION):
                # a note follows the whole item, its sub-bullets included
                if note:
                    _note_paragraphs(document, store, note)
                note = m.note_link(b) if m.is_todo(b) else ""
            if m.is_section(b):
                # no header line: a bold one would re-import as a project; checked items restart numbering
                num_id = None
                continue
            if not _has_content(b):
                continue
            meta = m.get_meta(b)
            checked = m.is_checked(b)
            if m.is_heading(b):
                # numbering restarts after a heading, like the gutter numbers
                num_id = None
                p = document.add_paragraph(style=f"Heading {m.heading_level(b)}")
            elif m.is_todo(b):
                p = document.add_paragraph(style="List Number")
                if num_id is None:
                    num_id = _num_for(document, "List Number")
                _set_num(p, num_id, 0)
            elif b.textList() is not None:
                # ponytail: the default template stops at "List Bullet 3", so notes deeper than 2 flatten to 2
                level = min(b.textList().format().indent() + 1, 3)
                p = document.add_paragraph(style=f"List Bullet {level}")
            else:
                p = document.add_paragraph()
            if meta["u"]:
                r = p.add_run("! ")
                r.bold = True
                r.font.color.rgb = RGBColor.from_string(URGENT)
            _block_runs(p, b, store, meta, checked)
        if note:
            _note_paragraphs(document, store, note)
    document.save(str(path))
    return len(projects)
