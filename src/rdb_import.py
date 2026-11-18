import datetime
import json
import os
import re
import sys
from pathlib import Path

import docx
from docx.enum.text import WD_COLOR_INDEX
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.hyperlink import Hyperlink
from docx.text.paragraph import Paragraph
from PySide6.QtGui import (
    QColor,
    QFont,
    QGuiApplication,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextImageFormat,
    QTextListFormat,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rdb_model as rdb  # noqa: E402
import rdb_paths  # noqa: E402
from rdb_store import Store  # noqa: E402

HL = {
    WD_COLOR_INDEX.AUTO: None,
    WD_COLOR_INDEX.BLACK: "#000000",
    WD_COLOR_INDEX.BLUE: "#0000ff",
    WD_COLOR_INDEX.BRIGHT_GREEN: "#00ff00",
    WD_COLOR_INDEX.DARK_BLUE: "#000080",
    WD_COLOR_INDEX.DARK_RED: "#800000",
    WD_COLOR_INDEX.DARK_YELLOW: "#808000",
    WD_COLOR_INDEX.GRAY_25: "#c0c0c0",
    WD_COLOR_INDEX.GRAY_50: "#808080",
    WD_COLOR_INDEX.GREEN: "#008000",
    WD_COLOR_INDEX.PINK: "#ff00ff",
    WD_COLOR_INDEX.RED: "#ff0000",
    WD_COLOR_INDEX.TEAL: "#008080",
    WD_COLOR_INDEX.TURQUOISE: "#00ffff",
    WD_COLOR_INDEX.VIOLET: "#800080",
    WD_COLOR_INDEX.WHITE: "#ffffff",
    WD_COLOR_INDEX.YELLOW: "#ffff00",
}
NUMBERED = {
    "decimal": QTextListFormat.Style.ListDecimal,
    "lowerLetter": QTextListFormat.Style.ListLowerAlpha,
    "upperLetter": QTextListFormat.Style.ListUpperAlpha,
    "lowerRoman": QTextListFormat.Style.ListLowerRoman,
    "upperRoman": QTextListFormat.Style.ListUpperRoman,
}
SKIP = {qn("w:sectPr"), qn("w:bookmarkStart"), qn("w:bookmarkEnd")}
EMU_PER_PX = 9525


def read_projects(path):
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return {
        ln[2:].split(None, 1)[1].strip().lower() for ln in lines if ln.startswith("x ")
    }


class Importer:
    def __init__(self, src, rdb_path, keep=None):
        self.src, self.rdb_path, self.keep = Path(src), Path(rdb_path), keep
        self.store = None
        self.d = docx.Document(str(self.src))
        self.date = datetime.date.fromtimestamp(self.src.stat().st_mtime).isoformat()
        self.projects = {}
        self.items = self.images = 0
        self.cur = self.project("Inbox", "inbox")
        self.numfmt = self.read_numbering()

    def project(self, title, pid=None):
        key = title.lower()
        if key not in self.projects:
            taken = {p["pid"] for p in self.projects.values()} | {"inbox"}
            doc = QTextDocument()
            rdb.prep_doc(doc)
            self.projects[key] = {
                "pid": pid or rdb.make_id(title, taken),
                "title": title,
                "doc": doc,
                "cursor": QTextCursor(doc),
                "empty": True,
            }
        return self.projects[key]

    def read_numbering(self):
        numbering = self.d.part.numbering_part.element
        abstract = {
            a.get(qn("w:abstractNumId")): a
            for a in numbering.findall(qn("w:abstractNum"))
        }
        out = {}
        for num in numbering.findall(qn("w:num")):
            a = abstract[num.find(qn("w:abstractNumId")).get(qn("w:val"))]
            for lvl in a.findall(qn("w:lvl")):
                fmt = lvl.find(qn("w:numFmt"))
                key = (num.get(qn("w:numId")), lvl.get(qn("w:ilvl")))
                out[key] = fmt.get(qn("w:val")) if fmt is not None else "bullet"
        return out

    def list_info(self, p):
        ppr = p._p.pPr
        num_pr = ppr.numPr if ppr is not None else None
        style_ppr = p.style.element.pPr
        if num_pr is None and style_ppr is not None:
            num_pr = style_ppr.numPr  # "List Number" etc. carry numbering on the style
        if num_pr is None or num_pr.numId is None or num_pr.numId.val == 0:
            return 1, rdb.bullet_for(1)
        ilvl = str(num_pr.ilvl.val) if num_pr.ilvl is not None else "0"
        # Word nests with "List Bullet 2" etc., each a one-level list, so the style name carries depth
        nested = re.fullmatch(r"List (?:Bullet|Number) (\d)", p.style.name)
        level = max(int(ilvl) + 1, int(nested.group(1)) if nested else 1)
        fmt = self.numfmt[(str(num_pr.numId.val), ilvl)]
        if fmt in ("bullet", "none"):
            return level, rdb.bullet_for(level)
        return level, NUMBERED[fmt]

    def is_header(self, p):
        text = p.text.strip()
        if p.style.name != "Normal" or not text or len(text) > 60:
            return False
        if p._p.pPr is not None and p._p.pPr.numPr is not None:
            return False
        if self.keep is not None and text.lower() not in self.keep:
            return False
        runs = [r for r in p.runs if r.text.strip()]
        return bool(runs) and all(r.bold for r in runs)

    def run(self):
        self.store = Store(self.rdb_path, create=True)
        for el in self.d.element.body.iterchildren():
            if el.tag == qn("w:p"):
                self.paragraph(Paragraph(el, self.d._body))
            elif el.tag == qn("w:tbl"):
                self.table(Table(el, self.d._body))
            elif el.tag not in SKIP:
                raise ValueError(f"unhandled body element {el.tag}")
        self.write()

    def paragraph(self, p):
        style, text = p.style.name, p.text.strip()
        if style == "Title":
            if text:
                parsed = datetime.datetime.strptime(text, "%A, %B %d, %Y")
                self.date = parsed.date().isoformat()
            return
        if self.is_header(p):
            self.cur = self.project(text)
            return
        if not text and not p._p.xpath(".//a:blip"):
            return
        c = self.new_item(*self.list_info(p))
        bold = style.startswith("Heading")
        for part in p.iter_inner_content():
            if isinstance(part, Hyperlink):
                for r in part.runs:
                    self.add_run(c, r, part.address, bold)
            else:
                self.add_run(c, part, None, bold)
        self.finish_item(c)

    def table(self, t):
        saved, self.cur = self.cur, self.projects["inbox"]
        for row in t.rows:
            text = "\t".join(cell.text.strip() for cell in row.cells)
            if text.strip():
                c = self.new_item(1, rdb.bullet_for(1))
                c.insertText(text)
                self.finish_item(c)
        self.cur = saved

    def new_item(self, level, style):
        c = self.cur["cursor"]
        if self.cur["empty"]:
            self.cur["empty"] = False
        else:
            c.insertBlock(QTextBlockFormat(), QTextCharFormat())
        if level == 1:
            rdb.set_meta(c.block(), **rdb.todo_meta())
        else:
            rdb.set_meta(c.block(), k="")
            rdb.set_list_level(c, level - 1, None if style in rdb.BULLETS else style)
        return c

    def finish_item(self, c):
        block = c.block()
        done = rdb.is_todo(block) and rdb.all_struck(block)
        if done:
            # checked state now carries "done"; the strike becomes a display overlay
            rdb.clear_strike(block)
        rdb.set_meta(block, c=self.date, d=self.date if done else "")
        self.items += 1

    def add_run(self, c, r, href, bold):
        for blip in r._r.xpath(".//a:blip"):
            self.add_image(c, r, blip)
        if not r.text:
            return
        f = QTextCharFormat()
        if r.bold or bold:
            f.setFontWeight(QFont.Weight.Bold)
        if r.italic:
            f.setFontItalic(True)
        if r.underline:
            f.setFontUnderline(True)
        if r.font.strike:
            f.setFontStrikeOut(True)
        if r.font.color.type is not None and r.font.color.rgb is not None:
            f.setForeground(QColor(f"#{r.font.color.rgb}"))
        if r.font.highlight_color is not None and HL[r.font.highlight_color]:
            f.setBackground(QColor(HL[r.font.highlight_color]))
        if href:
            f.setAnchor(True)
            f.setAnchorHref(href)
            f.setForeground(QColor("#0000ff"))
            f.setFontUnderline(True)
        c.insertText(r.text.replace("\n", "\u2028"), f)

    def add_image(self, c, r, blip):
        part = self.d.part.related_parts[blip.get(qn("r:embed"))]
        self.images += 1
        ext = Path(part.partname).suffix.lower()
        name = f"img/{self.cur['pid']}_{self.images}{ext}"
        self.store.put_image(name, part.blob)
        fmt = QTextImageFormat()
        fmt.setName(name)
        extent = r._r.xpath(".//wp:extent")
        if extent:
            w = int(extent[0].get("cx")) / EMU_PER_PX
            h = int(extent[0].get("cy")) / EMU_PER_PX
            k = min(1.0, 600 / w) if w else 1.0
            fmt.setWidth(w * k)
            fmt.setHeight(h * k)
        # ponytail: emf/wmf are copied but Qt cannot draw them; convert if any matter
        c.insertImage(fmt)

    def write(self):
        cards = []
        for i, pr in enumerate(self.projects.values()):
            rdb.normalize(pr["doc"], "move")
            rdb.save_project(self.store, pr["pid"], pr["doc"])
            x, y = (i % 5) * 256.0, (i // 5) * 160.0
            cards.append(
                {"id": pr["pid"], "title": pr["title"], "x": x, "y": y, "w": 7, "h": 4}
            )
        rdb.save_map(self.store, {"cards": cards, "frames": []})
        self.store.close()
        print(f"{len(cards)} projects, {self.items} items, {self.images} images")


CLIP_KEYS = ("title", "source", "created")


def _json_escape(mt):
    # pairs are scanned left to right, so a "\\" pair is never split
    return mt.group(0) if mt.group(1) in '"\\/bfnrtu' else mt.group(1)


def _front_matter(path):
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"{path}: no front matter")
    out = {}
    for ln in lines[1 : lines.index("---", 1)]:
        key, sep, value = ln.partition(":")
        key, value = key.strip(), value.strip()
        if sep and key in CLIP_KEYS and not ln[:1].isspace():
            if value.startswith('"'):
                # the clipper writes escapes neither YAML nor JSON define (LaTeX \$): keep the char
                value = json.loads(re.sub(r"\\(.)", _json_escape, value))
            out[key] = value
    return out


def clippings(folder):
    out = []
    for f in Path(folder).glob("*.md"):
        fm = _front_matter(f)
        out.append((fm["created"], fm["title"] or f.stem, fm["source"]))
    return sorted(out)


def import_clippings(store, folder, title):
    mp = rdb.load_map(store)
    if any(c["title"] == title for c in mp["cards"]):
        raise ValueError(f"a card titled {title!r} is already on the map")
    taken = (
        rdb.card_ids(store)
        | set(store.project_ids())
        | set(store.board_ids())
        | set(store.trash_ids())
    )
    pid = rdb.make_id(title, taken)
    settings = rdb.load_settings(store)
    doc = rdb.open_doc(store, pid, settings["on_check"])
    created = {}
    for day, name, url in clippings(folder):
        if url in created:
            continue
        created[url] = day
        rdb.append_link(doc, url, settings["on_check"])
        rdb.relabel_links(doc, url, name)
    for b in rdb.iter_blocks(doc):
        href = b.begin().fragment().charFormat().anchorHref() if rdb.is_todo(b) else ""
        if href in created:
            rdb.set_meta(b, c=created[href])
    rdb.save_project(store, pid, doc)
    cell = 32  # the map grid cell (rdb_map.CELL; the map module is GUI-heavy)
    x = min((c["x"] for c in mp["cards"]), default=0)
    y = max((c["y"] + c["h"] * cell for c in mp["cards"]), default=0) + cell
    w, h = settings["card_w"], settings["card_h"]
    mp["cards"].append(rdb.new_card(pid, title, x, y, "links", w, h))
    rdb.save_map(store, mp)
    return pid, len(created)


def convert_json_dir(data_dir, rdb_path):
    data_dir = Path(data_dir)
    store = Store(rdb_path, create=True)
    store.put_map(json.loads((data_dir / "map.json").read_text(encoding="utf-8")))
    if (data_dir / "settings.json").exists():
        store.put_settings(
            json.loads((data_dir / "settings.json").read_text(encoding="utf-8"))
        )
    for f in sorted(data_dir.glob("*.json")):
        if f.stem in ("map", "settings"):
            continue
        d = json.loads(f.read_text(encoding="utf-8"))
        store.put_project(f.stem, d["html"], d["meta"], d["collapsed"])
    for f in sorted((data_dir / "img").glob("*")):
        store.put_image(f"img/{f.name}", f.read_bytes())
    n = len(store.project_ids())
    store.close()
    return n


def main():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QGuiApplication(sys.argv)  # noqa: F841 -- QTextDocument fonts need a gui app
    if len(sys.argv) < 2:
        sys.exit("usage: rdb_import.py <source.docx> [out.rdb] [projects file]")
    src = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else rdb_paths.default_file()
    keep = read_projects(sys.argv[3]) if len(sys.argv) > 3 else None
    Importer(src, out, keep).run()


if __name__ == "__main__":
    main()
