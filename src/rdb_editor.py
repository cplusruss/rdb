import base64
import json
import math
import re
import shutil
import time
from pathlib import Path
from collections import namedtuple
from contextlib import contextmanager

from PySide6.QtCore import (
    QEvent,
    QFileInfo,
    QBuffer,
    QByteArray,
    QIODevice,
    QMimeData,
    QObject,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QSize,
    QSizeF,
    Qt,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QAbstractTextDocumentLayout,
    QBrush,
    QColor,
    QDesktopServices,
    QLinearGradient,
    QFont,
    QFontDatabase,
    QFontMetrics,
    QFontMetricsF,
    QGuiApplication,
    QIcon,
    QImage,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
    QPolygonF,
    QShortcut,
    QSyntaxHighlighter,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextDocumentFragment,
    QTextFormat,
    QTextImageFormat,
    QTextListFormat,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QInputDialog,
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFileIconProvider,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from rdb_model import (
    group_tail,
    h1_colour,
    item_id,
    head_spots,
    move_groups,
    step_groups,
    ALIGNS,
    MARKS,
    all_struck,
    block_align,
    block_aligned,
    clear_strike,
    normalize,
    numbered,
    H1_PAD,
    INK,
    NEW_ITEM,
    ORIG_BG,
    NOTE_PAD,
    PREVIEWS,
    SEC_GAP,
    HEADING,
    TODO,
    create_note,
    done_mode,
    duplicate_note,
    fragment_plain,
    ensure_ids,
    ensure_task_ids,
    flatten_tables,
    paste_meta,
    send_home,
    unit_above,
    unit_block,
    layout_block,
    apply_view,
    attach_images,
    bullet_for,
    PROPS,
    SIZE_SHARE,
    image_sizes,
    rename_image_sizes,
    set_image_size,
    CHAR_COLORS,
    char_clear,
    char_color,
    char_has,
    char_normalize,
    char_toggle,
    counts,
    fragment_html,
    mirror_range,
    set_char_colour,
    get_meta,
    heading_level,
    image_label,
    image_labels,
    drop_image_meta,
    image_names,
    set_image_label,
    is_checked,
    is_collapsed,
    is_divider,
    is_folded,
    is_heading,
    is_section,
    is_text_doc,
    is_todo,
    item_blocks,
    item_images,
    note_font,
    open_doc,
    EDITOR_FONT,
    item_of,
    iter_blocks,
    make_heading,
    load_preview,
    make_divider,
    make_note,
    make_todo,
    move_item_step,
    note_link,
    note_open,
    note_pitch,
    owner_todo,
    panel_height,
    preview_font,
    preview_rows,
    remove_block,
    remove_item,
    section_block,
    selected_todos,
    set_collapsed,
    fold_headings,
    set_all_folded,
    set_folded,
    set_list_level,
    set_meta,
    set_note_open,
    start_task,
    task_start,
    todo_meta,
    toggle_check,
    toggle_checks,
    today,
    trash_project,
    uniform_bg,
    unfold_to,
    unit_head,
    unit_of,
    FOLDABLE,
    fold_owner,
    CAPTION,
    CAPTION_PAD,
    CHAR_ATTRS,
    FILE_PAD,
    PRIOS,
    STARS,
    apply_captions,
    caption_diff,
    chip_span,
    chip_widen,
    detected_links,
    group_completed,
    has_home,
    home_new,
    insert_link,
    link_format,
    is_path_link,
    is_sorted,
    link_span,
    mark_of,
    pasted_path_href,
    path_label,
    path_local,
    path_open_url,
    place_item,
    set_marker,
    set_status,
    status_of,
    set_sorted,
    sort_open,
    run_text,
    move_pill,
    pill_home,
    lone_tag,
    tag_reveal,
    tag_runs,
    tag_spots,
    tag_span,
    unsort_open,
    url_text,
)
import rdb_model
import rdb_rdoc
import rdb_bar
import rdb_status
import rdb_markup
import rdb_theme
from rdb_bar import THEMES, CaptionButton, TitleBar, bevel_face
from rdb_links import LinksGrid
from rdb_timeline import NUDGE_KEYS, TimelineBody, nudge
from rdb_urgent_page import PID as URGENT_PID
from rdb_vim import ITEMS_MIME, VimEngine

# the names and sizes of the images in a text selection, keyed by image
IMAGE_META_MIME = "application/x-rdb-image-meta"
CTRL = Qt.KeyboardModifier.ControlModifier
SHIFT = Qt.KeyboardModifier.ShiftModifier
ALT = Qt.KeyboardModifier.AltModifier
# Word writes list numbers and bullets as literal text in front of each pasted paragraph
WORD_MARKER = re.compile(
    r"^[\s\u00a0]*(?:\d{1,3}[.)]|[A-Za-z][.)]|[ivxlcdm]{1,6}[.)]"
    r"|[\u2022\u00b7\u00a7\uf0b7\uf0a7o-])[\s\u00a0]+"
)
HIGHLIGHTS = {Qt.Key.Key_4: "#00ffff"}
FORMAT_KEYS = {
    Qt.Key.Key_B: "bold",
    Qt.Key.Key_I: "italic",
    Qt.Key.Key_U: "underline",
    Qt.Key.Key_S: "strike",
}
HEADING_KEYS = {
    Qt.Key.Key_0: 0,
    Qt.Key.Key_1: 1,
    Qt.Key.Key_2: 2,
    Qt.Key.Key_3: 3,
}
# a path link's bubble: room before the label for the glyph, and after it
# how far in the glyph sits, per chip style (a pill's round end, a token's accent bar)
GLYPH_IN = {"box": 3, "pill": 4, "token": 5}
GLYPH_GAP = 4


def glyph_in(s):
    # GLYPH_IN is the inset at a pad of 3; more pad moves the glyph further in
    return GLYPH_IN[s["chip_style"]] + s["chip_pad"] - 3


def chip_room(s):
    # room before a chip's label: the inset, the icon and the gap after it
    return glyph_in(s) + s["chip_icon"] + GLYPH_GAP


FG_GREY = "#808080"
FG_RED = "#ff0000"
URGENT = "#d42020"
MO = QTextCursor.MoveOperation
VIM_MODES = ("normal", "visual", "vline")
NAV_KEYS = {
    Qt.Key.Key_Left,
    Qt.Key.Key_Right,
    Qt.Key.Key_Up,
    Qt.Key.Key_Down,
    Qt.Key.Key_Home,
    Qt.Key.Key_End,
    Qt.Key.Key_PageUp,
    Qt.Key.Key_PageDown,
    Qt.Key.Key_Shift,
    Qt.Key.Key_Control,
    Qt.Key.Key_Alt,
    Qt.Key.Key_Meta,
    Qt.Key.Key_CapsLock,
}
RENAME_ENDS = {
    Qt.Key.Key_Left,
    Qt.Key.Key_Right,
    Qt.Key.Key_Up,
    Qt.Key.Key_Down,
    Qt.Key.Key_Home,
    Qt.Key.Key_End,
    Qt.Key.Key_PageUp,
    Qt.Key.Key_PageDown,
}
BOLD8 = QFont("Tahoma", 8, QFont.Weight.Bold)


def sep_x():
    return rdb_model.GUTTER - 3


# the gap between a tag chip and the text after it, in px at 100 %
TAG_GAP = 2
GRIP_INK = "#000000"


def _shown(s, key):
    return QColor(rdb_model.shown(s, key))


def _shade(c):
    return rdb_theme.shade(c)


GRIP_EDGE = 16
ZOOM = (0.5, 3.0)
ZOOM_STEP = 0.1


class _ZoomHub(QObject):
    changed = Signal(float)


_hub = _ZoomHub()
_zoom = [1.0]
# a layout keeps a pointer to its paint device, so a device lives as long as the process
_devices = {}


def zoom_hub():
    return _hub


def get_zoom():
    return _zoom[0]


def set_zoom(z):
    z = round(min(max(z, ZOOM[0]), ZOOM[1]), 2)
    if z != _zoom[0]:
        _zoom[0] = z
        _hub.changed.emit(z)


def _dpi(z=None):
    base = round(QGuiApplication.primaryScreen().logicalDotsPerInchY())
    return base, round(base * (get_zoom() if z is None else z))


def doc_zoom(doc):
    # the factor the document's layout applies: editors of different zooms can show different documents
    base = round(QGuiApplication.primaryScreen().logicalDotsPerInchY())
    return (doc.property("rdb_dpi") or base) / base


def eff_zoom():
    # the layout's device has a whole dpi, so the factor it applies is dpi / base, not quite the set one
    base, dpi = _dpi()
    return dpi / base


def _tag_font(s, z):
    f = QFont(s["tag_font"])
    f.setPointSizeF(s["tag_size"] * z)
    f.setWeight(rdb_model.tag_weight(s))
    return f


def gutter_tag(block, s, z):
    # the item's only tag when its chip is drawn in the tag gutter: (run, name, chip width);
    # None for several tags, no tag gutter, or a slot without room for a pad either side and a letter
    if not s["tag_gutter"] or (run := lone_tag(block)) is None:
        return None
    name = run_text(block.document().findBlock(run[0]), *run)[1:]
    fm, pad = QFontMetricsF(_tag_font(s, z)), s["tag_pad"] * z
    slot = (s["tag_gutter_w"] - 9) * z
    if slot < 2 * pad + fm.horizontalAdvance(name[0]):
        return None
    return run, name, min(slot, fm.horizontalAdvance(name) + 2 * pad)


def _lone(block, s):
    # the tag that hides inline: the one whose chip is in the tag gutter
    chip = gutter_tag(block, s, doc_zoom(block.document()))
    return None if chip is None else chip[0]


def _has_px(doc):
    for b in iter_blocks(doc):
        it = b.begin()
        while not it.atEnd():
            if (
                it.fragment()
                .charFormat()
                .hasProperty(QTextFormat.Property.FontPixelSize)
            ):
                return True
            it += 1
    return False


def _device(dpi):
    if dpi not in _devices:
        img = QImage(1, 1, QImage.Format.Format_ARGB32)
        dpm = round(dpi / 0.0254)
        img.setDotsPerMeterX(dpm)
        img.setDotsPerMeterY(dpm)
        _devices[dpi] = img
    return _devices[dpi]


# top / bottom of the block's text lines, centre of its first line, right edge of its widest line
Geom = namedtuple("Geom", "top bottom cy right")
# a block's band in document coordinates: its text lines plus the margins around them
Row = namedtuple("Row", "block top bottom cy")
Grip = namedtuple("Grip", "head start moved dead", defaults=(False,))
Pill = namedtuple("Pill", "span start moved dead", defaults=(False, False))
# the display-only look of a pill drag, per document, read by the highlighter: the characters
# a lift takes away (cut, with each one's advance: adv) drawn as one blank of width room, letter
# spacing added after blanks (gaps: position -> px), and a span drawn invisible while the
# dropped pill glides in (ghost)
Lift = namedtuple("Lift", "cut adv room gaps ghost")
LIFTS = {}
HAIR = " "


class Session:
    # a run of keys that is one undo step: later keys join the edit block of the first
    def __init__(self, block=None, steps=0):
        self.block, self.steps, self.joined = block, steps, False


def fresh_meta(block, todo=True):
    if todo and block.textList() is None:
        set_meta(block, **todo_meta(), **NEW_ITEM)
    else:
        set_meta(block, c=today(), d="", u=False, fg="", bg="", k="", **NEW_ITEM)
    # leaving a list on Enter resets the whole block format, margin included; a heading's
    # padding must go once the block is a todo
    layout_block(block)


def _plain(block):
    return block.isVisible() and not is_section(block)


def _blocked(block):
    return block.isValid() and (is_section(block) or not block.isVisible())


def _flat_mime(src):
    # Qt turns a table in html, Qt rich text or markdown into a table frame, which items cannot move across
    sources = []
    if src.hasFormat("text/markdown"):
        sources.append((bytes(src.data("text/markdown")).decode("utf-8"), True))
    if src.hasFormat("application/x-qrichtext"):
        sources.append(
            (bytes(src.data("application/x-qrichtext")).decode("utf-8"), False)
        )
    if src.hasHtml():
        sources.append((src.html(), False))
    for text, markdown in sources:
        if (flat := flatten_tables(text, markdown)) is not None:
            out = QMimeData()
            out.setHtml(flat)
            return out
    return src


# white-space:pre marks a code editor's coloured copy; a web page's pre-wrap is not one
CODE_HTML = re.compile(r"<pre\b|white-space:\s*pre(?!-)", re.IGNORECASE)


def _text_source(src):
    if src.hasFormat("text/markdown") or src.hasFormat("application/x-qrichtext"):
        return False
    return src.hasText() and (not src.hasHtml() or bool(CODE_HTML.search(src.html())))


def _trimmed(line, left, right):
    body = line.strip()
    if not body:
        return " " if line and not (left or right) else ""
    lead = " " if not left and line[0].isspace() else ""
    tail = " " if not right and line[-1].isspace() else ""
    return lead + body + tail


FILE_TEXT_MAX = 8 * 1024 * 1024


def file_text(path):
    # a dropped file's text for a Note card; None for a folder, a binary or an oversized file
    p = Path(path)
    if not p.is_file() or p.stat().st_size > FILE_TEXT_MAX:
        return None
    raw = p.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = raw.decode("utf-16")
    elif b"\0" in raw[:65536]:
        return None
    else:
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            # most non-UTF-8 text files here are Windows-1252
            text = raw.decode("cp1252", errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _text_mime(src, trim, c):
    text = src.text()
    if trim:
        # split, not splitlines: a final newline ends the paste on a line of its own. An edge
        # that joins text on the caret's line keeps one space, unless a space is already there;
        # characterAt reads a block's end as a paragraph separator, which is a space too
        lines = text.replace("\r\n", "\n").split("\n")
        doc = c.document()
        at = c.selectionStart()
        left = at == 0 or doc.characterAt(at - 1).isspace()
        right = doc.characterAt(c.selectionEnd()).isspace()
        out = [_trimmed(line, True, True) for line in lines]
        out[0] = _trimmed(lines[0], left, right or len(lines) > 1)
        out[-1] = _trimmed(lines[-1], left or len(lines) > 1, right)
        text = "\n".join(out)
    out = QMimeData()
    out.setText(text)
    return out


def _unit_head(block):
    # the item or heading a block belongs to (its own number), -1 for free text outside items
    head = unit_head(block)
    return -1 if head is None else head.blockNumber()


def pop_at(pop, view, rect):
    # under rect, flipped above it where the screen ends
    pop.adjustSize()
    vp = view.viewport()
    at = vp.mapToGlobal(QPoint(int(rect.left()), int(rect.bottom()) + 2))
    geo = view.window().screen().availableGeometry()
    y = at.y()
    if y + pop.height() > geo.bottom():
        y = vp.mapToGlobal(QPoint(0, int(rect.top()) - 2)).y() - pop.height()
    pop.move(rdb_theme.on_screen(view, pop.size(), QPoint(at.x(), y)))
    pop.show()


def _span_cursor(blocks):
    c = QTextCursor(blocks[0])
    last = blocks[-1]
    c.setPosition(last.position() + last.length() - 1, QTextCursor.MoveMode.KeepAnchor)
    return c


# part -> (width, height) at 100%
ITEM_SIZE = {
    "number": (rdb_model.GUTTER_PART_W["number"], 16),
    "check": (rdb_model.GUTTER_PART_W["check"], 13),
    "bubble": (rdb_model.GUTTER_PART_W["bubble"], 10),
    "note": (rdb_model.GUTTER_PART_W["note"], 11),
    "mark": (rdb_model.GUTTER_PART_W["mark"], 16),
    "status": (rdb_model.GUTTER_PART_W["status"], 16),
}
HEAD_GAP = {"fold": 5, "swatch": 6, "count": 5}
PART_SIZE = {
    **ITEM_SIZE,
    "fold": (13, 13),
    "swatch": (10, 10),
    "count": (20, 16),
}


def _columns(g, ox, z, parts):
    # the item row parts at 100%, times the zoom z: the first at 4, 3 px between each, centred on
    # the row, packed left in the order given; text from the gutter, the separator at sep_x();
    # the note top is a whole pixel; a P badge is 12 wide and overhangs its slot by a pixel a side
    out, x = {}, rdb_model.GUTTER_LPAD
    for k in parts:
        w, h = ITEM_SIZE[k]
        top = g.cy - h / 2 * z
        out[k] = QRectF(x * z + ox, round(top) if k == "note" else top, w * z, h * z)
        x += w + 3
    return out


def _head_columns(g, ox, z, parts):
    # a heading row parts, the first at 8, packed left in the order given; the fold box is about
    # 70% of the row, whole pixels keep the lines crisp, an odd side the plus centred; the count
    # runs on when last
    out, x = {}, round(8 * z + ox)
    for i, k in enumerate(parts):
        if k == "fold":
            side = round(0.7 * (g.bottom - g.top)) | 1
            out[k] = QRectF(x, round(g.cy - side / 2), side, side)
            x += side + HEAD_GAP[k] * z
        elif k == "swatch":
            out[k] = QRectF(x, g.cy - 5 * z, 10 * z, 10 * z)
            x += 10 * z + HEAD_GAP[k] * z
        else:
            last = i == len(parts) - 1
            out[k] = QRectF(x, g.top, (32 if last else 20) * z, g.bottom - g.top)
            x += 20 * z + HEAD_GAP[k] * z
    return out


def _chars(block, a, b):
    # (UTF-16 position, character) of each character of the document range a..b in a block
    at = a
    for ch in run_text(block, a, b):
        yield at, ch
        at += _u16(ch, 0, 1)


def _lay_i(lay, rel, end=False):
    # a block-relative position as an index into its layout, which counts a preedit run (the
    # line-start gap of a pill drag, or an input method's text) before the characters after it;
    # the end of a run that the preedit follows stays before it
    at = lay.preeditAreaPosition()
    shift = 0 <= at < rel if end else 0 <= at <= rel
    return rel + len(lay.preeditAreaText()) if shift else rel


def _toward(v, to, k):
    # one frame of an eased approach; the last half pixel snaps
    v += (to - v) * k
    return to if abs(to - v) < 0.5 else v


class ItemHighlighter(QSyntaxHighlighter):
    def __init__(self, doc, settings, caret=lambda: -1, visited=lambda: ()):
        super().__init__(doc)
        self.settings = settings
        self.caret = caret
        self.visited = visited

    def _px(self, f, frag):
        # the layout zooms point sizes, not a pasted pixel size: that is scaled here, for display only
        px = frag.charFormat().font().pixelSize()
        ez = doc_zoom(self.document())
        if px > 0 and ez != 1.0:
            f.setProperty(QTextFormat.Property.FontPixelSize, round(px * ez))

    def _heading(self, block):
        # the heading look is a display overlay: the stored char formats stay plain
        lvl = heading_level(block)
        s = self.settings
        own = h1_colour(block, "hf") if lvl == 1 else ""
        it = block.begin()
        while not it.atEnd():
            frag = it.fragment()
            f = QTextCharFormat()
            hf = rdb_model.list_font(s, f"h{lvl}")
            f.setFont(hf)
            if not frag.charFormat().hasProperty(QTextFormat.Property.ForegroundBrush):
                f.setForeground(_shade(own) if own else _shown(s, f"h{lvl}_fg"))
            self._px(f, frag)
            self.setFormat(frag.position() - block.position(), frag.length(), f)
            it += 1

    def _bubble(self, block, frag, f):
        # a path link is drawn as a bubble by the editor: its first character (FILE_PAD) is
        # stretched to make room for the glyph and its last one for the right padding
        s, ez = self.settings, doc_zoom(self.document())
        font = QFont(s["chip_font"])
        font.setPointSizeF(s["chip_size"])
        font.setWeight(rdb_model.chip_weight(s))
        adv = QFontMetricsF(font).horizontalAdvance(FILE_PAD)
        f.setFontUnderline(False)
        f.setForeground(_shown(s, "chip_fg"))
        f.setFontFamilies([s["chip_font"]])
        f.setFontPointSize(s["chip_size"])
        f.setFontWeight(rdb_model.chip_weight(s))
        if frag.charFormat().font().pixelSize() > 0:
            # a pasted pixel size would outrank the point size: the chip's size in pixels instead
            dpi = QGuiApplication.primaryScreen().logicalDotsPerInchY()
            f.setProperty(
                QTextFormat.Property.FontPixelSize,
                round(s["chip_size"] * dpi / 72 * ez),
            )
        start, n = frag.position() - block.position(), frag.length()
        self.setFormat(start, n, f)
        for at, gap in (
            (start, (chip_room(s) - adv) * ez),
            (start + n - 1, s["chip_pad"] * ez),
        ):
            g = QTextCharFormat(f)
            g.setFontLetterSpacingType(QFont.SpacingType.AbsoluteSpacing)
            g.setFontLetterSpacing(max(gap, 0))
            self.setFormat(at, 1, g)

    def _completed(self, f, frag):
        s = self.settings
        if s["sec_strike"]:
            f.setFontStrikeOut(True)
        if s["sec_grey"]:
            f.setForeground(_shown(s, "sec_color"))
        if s["sec_italic"]:
            f.setFontItalic(True)
        if s["sec_size"]:
            # a pasted explicit size shifts by the delta instead of being replaced
            size = (
                frag.charFormat().fontPointSize()
                or self.document().defaultFont().pointSizeF()
            )
            f.setFontPointSize(size + s["sec_size"])

    def highlightBlock(self, text):
        self._item(text)
        self._lifted(self.currentBlock())

    def _lifted(self, block):
        # a pill drag's look goes over everything else (Lift)
        lift = LIFTS.get(self.document())
        if lift is None:
            return
        base = block.position()
        end = base + block.length() - 1
        for at, w in lift.gaps.items():
            if base <= at < end:
                self._space(at - base, w)
        room = lift.room
        for at, adv in lift.adv.items():
            # the first character takes the whole room, the rest none
            if not base <= at < end:
                room = 0
            elif self.document().characterAt(at) == "￼":
                self._squeeze(at, room)
            else:
                g = QTextCharFormat(self.format(at - base))
                g.setForeground(QColor(0, 0, 0, 0))
                self.setFormat(at - base, 1, g)
                self._space(at - base, room - adv)
            room = 0
        if lift.ghost is not None:
            for at in range(max(lift.ghost[0], base), min(lift.ghost[1], end)):
                g = QTextCharFormat(self.format(at - base))
                g.setForeground(QColor(0, 0, 0, 0))
                self.setFormat(at - base, 1, g)

    def _space(self, at, w):
        # w px more after the character at block offset at, over any spacing it already has
        g = QTextCharFormat(self.format(at))
        own = (
            g.fontLetterSpacing()
            if g.fontLetterSpacingType() == QFont.SpacingType.AbsoluteSpacing
            else 0
        )
        g.setFontLetterSpacingType(QFont.SpacingType.AbsoluteSpacing)
        g.setFontLetterSpacing(own + w)
        self.setFormat(at, 1, g)

    def _squeeze(self, at, room):
        # an image chip narrowed to room px; its height stays, so the line does not shrink
        c = QTextCursor(self.document())
        c.setPosition(at + 1)
        name = c.charFormat().toImageFormat().name()
        img = self.document().resource(
            QTextDocument.ResourceType.ImageResource, QUrl(name)
        )
        g = QTextImageFormat()
        g.setWidth(room)
        g.setHeight(img.height())
        self.setFormat(at - self.currentBlock().position(), 1, g)

    def _item(self, text):
        block = self.currentBlock()
        # state 1 from the Completed header on, so a block knows where it sits without a scan
        below = self.previousBlockState() == 1
        self.setCurrentBlockState(int(below or is_section(block)))
        if is_heading(block):
            self._heading(block)
            return
        owner = owner_todo(block) if below else None
        fold = owner is not None and is_checked(owner)
        fg = get_meta(block)["fg"] if is_todo(block) else ""
        checked = is_checked(block)
        found = detected_links(block)
        it = block.begin()
        while not it.atEnd():
            frag = it.fragment()
            fc = frag.charFormat()
            if fc.isAnchor() and is_path_link(fc.anchorHref()) and frag.length() > 1:
                f = QTextCharFormat()
                self._bubble(block, frag, f)
                it += 1
                continue
            stored = fc.anchorHref() if fc.isAnchor() else ""
            # a detected address inside this fragment is drawn as a link in pieces
            fa, fz = frag.position(), frag.position() + frag.length()
            at = fa
            for s, e, h in [
                (max(s, fa), min(e, fz), h) for s, e, h in found if s < fz and fa < e
            ] + [(fz, fz, "")]:
                for lo, hi, href, drawn in ((at, s, stored, False), (s, e, h, True)):
                    if hi > lo:
                        g = self._look(frag, fg, fold, checked, href, drawn)
                        if g.properties():
                            self.setFormat(lo - block.position(), hi - lo, g)
                at = e
            it += 1
        self._tags(block, fold or checked, fold)

    def _look(self, frag, fg, fold, checked, href, drawn):
        f = QTextCharFormat()
        if fg and not frag.charFormat().hasProperty(
            QTextFormat.Property.ForegroundBrush
        ):
            f.setForeground(_shade(fg))
        if href:
            # the link look is a display overlay: the stored colour stays; a done look goes over it
            seen = href in self.visited()
            f.setForeground(
                _shown(self.settings, "link_visited" if seen else "link_color")
            )
            if drawn:
                f.setFontUnderline(True)
        if fold:
            self._completed(f, frag)
        elif checked:
            if self.settings["strike"]:
                f.setFontStrikeOut(True)
            if self.settings["grey"]:
                f.setForeground(_shown(self.settings, "checked_color"))
            if self.settings["italic"]:
                f.setFontItalic(True)
        self._px(f, frag)
        return f

    def _tags(self, block, done, fold):
        # the tag look goes over what the item already has: strike, italic, a done colour and size
        s, ez = self.settings, doc_zoom(self.document())
        size = s["tag_size"] + (s["sec_size"] if fold else 0)
        base = block.position()
        lone = _lone(block, s)
        for a, b in tag_runs(block):
            r0, r1 = tag_reveal(block, (a, b), lone)
            shown = r0 <= self.caret() <= r1
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                it += 1
                lo = max(a, frag.position())
                hi = min(b, frag.position() + frag.length())
                if lo >= hi:
                    continue
                f = QTextCharFormat(self.format(lo - base))
                if not done:
                    f.setForeground(_shown(s, "tag_fg"))
                f.setFontFamilies([s["tag_font"]])
                f.setFontPointSize(size)
                f.setFontWeight(rdb_model.tag_weight(s))
                if frag.charFormat().font().pixelSize() > 0:
                    dpi = QGuiApplication.primaryScreen().logicalDotsPerInchY()
                    f.setProperty(
                        QTextFormat.Property.FontPixelSize, round(size * dpi / 72 * ez)
                    )
                self.setFormat(lo - base, hi - lo, f)
            if (a, b) == lone and not shown:
                for at, ch in _chars(block, r0, r1):
                    self._hide(at - base, ch, 0)
                continue
            # room for the chip's pad, and a gap after it, so the words around stay clear
            pad = s["tag_pad"] * ez
            last = QTextCharFormat(self.format(b - 1 - base))
            last.setFontLetterSpacingType(QFont.SpacingType.AbsoluteSpacing)
            last.setFontLetterSpacing(pad + TAG_GAP * ez)
            self.setFormat(b - 1 - base, 1, last)
            if not shown:
                self._hide(a - base, "#", pad)

    def _hide(self, at, ch, room):
        # a 1 px glyph whose spacing cancels its advance: the character stays in the text and
        # leaves only the room, the chip's left pad for a '#'
        g = QTextCharFormat(self.format(at))
        font = g.font()
        font.setPixelSize(1)
        g.setProperty(QTextFormat.Property.FontPixelSize, 1)
        g.setFontLetterSpacingType(QFont.SpacingType.AbsoluteSpacing)
        g.setFontLetterSpacing(room - QFontMetricsF(font).horizontalAdvance(ch))
        g.setForeground(QColor(0, 0, 0, 0))
        self.setFormat(at, len(ch.encode("utf-16-le")) // 2, g)


PRIO_FILL = {"p1": "#d42020", "p2": "#ef7d00", "p3": "#f2c200", "p4": "#1e6fd0"}
PRIO_INK = {"p1": "#ffffff", "p2": "#ffffff", "p3": "#3a3000", "p4": "#ffffff"}
STAR_FILL = {
    "ys": "#f5c518",
    "os": "#f58a1f",
    "rb": "#d93025",
    "bi": "#1a73e8",
    "gc": "#188038",
    "pq": "#8430ce",
}


MARK_NAMES = (
    "None",
    "P1",
    "P2",
    "P3",
    "P4",
    "Yellow Star",
    "Orange Star",
    "Red Exclamation",
    "Blue Info",
    "Green Check",
    "Purple Question",
)


def _star(cx, cy, r):
    pts = []
    for i in range(10):
        a = math.pi * (i / 5 - 0.5)
        d = r if i % 2 == 0 else r * 0.45
        pts.append(QPointF(cx + d * math.cos(a), cy + d * math.sin(a)))
    return QPolygonF(pts)


def paint_marker(p, rect, mark, k, faint=False, hot=False):
    # a marker centred in rect, 11 px at 100%: P1..P4 as rounded badges, ys/os as stars, the
    # others as discs; an empty slot draws a placeholder outline star when faint, darker when hot
    cx, cy = rect.center().x(), rect.center().y()
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    font = QFont("Tahoma")
    font.setBold(True)
    if mark in PRIOS:
        box = QRectF(cx - 6 * k, cy - 5.5 * k, 12 * k, 11 * k)
        fill = _shade(PRIO_FILL[mark])
        p.setBrush(fill)
        p.drawRoundedRect(box, 2.5 * k, 2.5 * k)
        font.setPixelSize(max(round(8 * k), 5))
        p.setFont(font)
        p.setPen(rdb_theme.ink_on(fill, PRIO_INK[mark]))
        p.drawText(box, Qt.AlignmentFlag.AlignCenter, mark.upper())
    elif mark in ("ys", "os"):
        # a pale fill vanishes on a light bar: a 1 px black outline keeps the shape
        p.setPen(QPen(_shade("#000000"), 1))
        p.setBrush(_shade(STAR_FILL[mark]))
        p.drawPolygon(_star(cx, cy + 0.4 * k, 5.6 * k))
    elif mark in STARS:
        disc = QRectF(cx - 5.5 * k, cy - 5.5 * k, 11 * k, 11 * k)
        fill = _shade(STAR_FILL[mark])
        p.setBrush(fill)
        p.drawEllipse(disc)
        pen = QPen(rdb_theme.ink_on(fill), 1.7 * k)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        # drawn, not typeset: the glyphs stay crisp whatever fonts the machine has
        dot = 0.05 * k
        if mark == "gc":
            p.drawPolyline(
                QPolygonF(
                    [
                        QPointF(cx - 2.6 * k, cy + 0.2 * k),
                        QPointF(cx - 0.6 * k, cy + 2.4 * k),
                        QPointF(cx + 2.8 * k, cy - 2.2 * k),
                    ]
                )
            )
        elif mark == "rb":
            p.drawLine(QPointF(cx, cy - 3.4 * k), QPointF(cx, cy + 0.6 * k))
            p.drawLine(QPointF(cx, cy + 3 * k), QPointF(cx, cy + 3 * k + dot))
        elif mark == "bi":
            p.drawLine(QPointF(cx, cy - 3 * k), QPointF(cx, cy - 3 * k + dot))
            p.drawLine(QPointF(cx, cy - 0.8 * k), QPointF(cx, cy + 3.2 * k))
        else:
            hook = QPainterPath(QPointF(cx - 1.9 * k, cy - 2.3 * k))
            hook.arcTo(QRectF(cx - 1.9 * k, cy - 4.6 * k, 3.8 * k, 3.8 * k), 180, -200)
            hook.lineTo(QPointF(cx, cy + 0.4 * k))
            p.drawPath(hook)
            p.drawLine(QPointF(cx, cy + 3 * k), QPointF(cx, cy + 3 * k + dot))
    elif faint:
        # greys are tuned per mode: the mirror of a mid grey is too dim against the dark bar
        grey = ("#e0e0e0", "#8c8c8c") if rdb_theme.dark() else ("#404040", "#808080")
        p.setPen(QPen(QColor(grey[not hot]), max(k, 1)))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPolygon(_star(cx, cy + 0.4 * k, 5.2 * k))
    p.restore()


MARK_CELL_K = 1.35


class _MarkCell(QWidget):
    clicked = Signal()

    def __init__(self, mark):
        super().__init__()
        self.mark = mark
        self.setFixedSize(22, 20)
        self.setMouseTracking(True)
        self._hot = False

    def enterEvent(self, e):
        self._hot = True
        self.update()

    def leaveEvent(self, e):
        self._hot = False
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        if self._hot:
            p.setPen(QPen(rdb_theme.color("hot_line"), 1))
            p.setBrush(rdb_theme.color("hot_fill"))
            p.drawRect(self.rect().adjusted(0, 0, -1, -1))
        self._glyph(p, QRectF(self.rect()))
        p.end()

    def _glyph(self, p, rect):
        paint_marker(p, rect, self.mark, MARK_CELL_K, faint=True)

    def mousePressEvent(self, e):
        self.clicked.emit()


class _StatusCell(_MarkCell):
    # one row of the status picker: the icon at twice the gutter size, then the name
    K, ICON = 2.0, 28

    def __init__(self, mark):
        super().__init__(mark)
        r = rdb_model.STATUS_BY_KEY.get(mark)
        self.name = r["name"] if r else "No Status"
        self.setMinimumSize(
            self.ICON + 6 + self.fontMetrics().horizontalAdvance(self.name) + 10,
            self.ICON,
        )
        self.setMaximumSize(16777215, self.ICON)

    def _glyph(self, p, rect):
        r = rdb_model.STATUS_BY_KEY.get(self.mark)
        icon, color = (r["icon"], r["color"]) if r else ("", "")
        box = QRectF(rect.left(), rect.top(), self.ICON, rect.height())
        rdb_status.paint_status(p, box, icon, color, self.K, faint=True)
        p.setPen(self.palette().color(QPalette.ColorRole.WindowText))
        p.drawText(
            rect.adjusted(self.ICON + 6, 0, 0, 0),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self.name,
        )


class MarkerPopup(QFrame):
    # the marker picker: priorities (none, P1..P4) over the superstars; 0-4 pick a priority
    picked = Signal(str)

    def __init__(self, parent):
        super().__init__(parent, Qt.WindowType.Popup)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        rdb_theme.follow(self, rdb_theme.popup_frame)
        grid = QGridLayout(self)
        grid.setContentsMargins(3, 3, 3, 3)
        grid.setSpacing(1)
        self.cells = {}
        for r, marks in enumerate((("", *PRIOS), STARS)):
            for c, mk in enumerate(marks):
                cell = _MarkCell(mk)
                cell.clicked.connect(lambda mk=mk: self.pick(mk))
                grid.addWidget(cell, r, c)
                self.cells[mk] = cell
        self.setFixedSize(self.sizeHint())

    def pick(self, mark):
        self.picked.emit(mark)
        self.close()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.close()
        elif Qt.Key.Key_0 <= e.key() <= Qt.Key.Key_4 and not e.modifiers() & (
            CTRL | SHIFT | ALT
        ):
            self.pick(("", *PRIOS)[e.key() - Qt.Key.Key_0])
        else:
            super().keyPressEvent(e)


class StatusPopup(QFrame):
    # the status picker: none, then each status in order; 0 picks none, 1-9 the first nine
    picked = Signal(str)

    def __init__(self, parent):
        super().__init__(parent, Qt.WindowType.Popup)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        rdb_theme.follow(self, rdb_theme.popup_frame)
        row = QVBoxLayout(self)
        row.setContentsMargins(3, 3, 3, 3)
        row.setSpacing(1)
        self.cells = {}
        self.keys = ["", *(r["key"] for r in rdb_model.STATUS_LIST)]
        for key in self.keys:
            cell = _StatusCell(key)
            cell.clicked.connect(lambda key=key: self.pick(key))
            row.addWidget(cell)
            self.cells[key] = cell
        self.setFixedSize(self.sizeHint())

    def pick(self, key):
        self.picked.emit(key)
        self.close()

    def keyPressEvent(self, e):
        n = e.key() - Qt.Key.Key_0
        if e.key() == Qt.Key.Key_Escape:
            self.close()
        elif (
            Qt.Key.Key_0 <= e.key() <= Qt.Key.Key_9
            and n < len(self.keys)
            # the number pad's digits carry the keypad modifier
            and not e.modifiers() & (CTRL | SHIFT | ALT)
        ):
            self.pick(self.keys[n])
        else:
            super().keyPressEvent(e)


class LinkDialog(QDialog):
    def __init__(self, parent, label, url, pick_label):
        super().__init__(parent)
        self.setWindowTitle("Link")
        self.label, self.url = QLineEdit(label), QLineEdit(url)
        form = QFormLayout(self)
        form.addRow("Label", self.label)
        form.addRow("URL", self.url)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.rdoc = buttons.addButton("rdoc", QDialogButtonBox.ButtonRole.ActionRole)
        self.rdoc.setAutoDefault(False)
        self.rdoc.clicked.connect(self._fill_rdoc)
        self.built = ""
        form.addRow(buttons)
        self.resize(420, self.sizeHint().height())
        field = self.url if label and not url and not pick_label else self.label
        field.setFocus()
        field.selectAll()

    def _fill_rdoc(self):
        # rdoc is asked only on a click, so a stale, busy or closed rdoc never blocks the
        # dialog; a Label the user wrote stays, one this button built is replaced
        reply = rdb_rdoc.spot()
        self.rdoc.setEnabled(bool(reply))
        if not reply:
            return
        self.url.setText(rdb_rdoc.spot_href(reply))
        if self.label.text().strip() in ("", self.built):
            self.built = rdb_rdoc.spot_label(reply)
            self.label.setText(self.built)


class _Swatch(QWidget):
    clicked = Signal()

    def __init__(self, colour, key=""):
        super().__init__()
        self.colour, self.key = colour, key
        self.setFixedSize(18, 18)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        p.setPen(QPen(rdb_theme.color("swatch_edge"), 1))
        p.setBrush(_shade(self.colour or "#ffffff"))
        p.drawRoundedRect(r, 3, 3)
        if not self.colour:
            p.setPen(QPen(QColor(URGENT), 1.2))
            p.drawLine(r.bottomLeft(), r.topRight())

    def mousePressEvent(self, e):
        self.clicked.emit()


def _tool(text):
    b = QToolButton()
    b.setText(text)
    b.setCheckable(True)
    b.setAutoRaise(True)
    b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    b.setFixedSize(24, 22)
    return b


def _align_icon(align):
    # five 1 px lines, the odd ones shorter, set left, centre, right or full
    pm = QPixmap(16, 16)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    ink = QApplication.palette().buttonText()
    for i, y in enumerate((2, 5, 8, 11, 14)):
        n = 14 if align == Qt.AlignmentFlag.AlignJustify or i % 2 == 0 else 9
        x = {
            Qt.AlignmentFlag.AlignHCenter: 1 + (14 - n) // 2,
            Qt.AlignmentFlag.AlignRight: 15 - n,
        }.get(align, 1)
        p.fillRect(x, y, n, 1, ink)
    p.end()
    return QIcon(pm)


class FormatRow(QWidget):
    # B I U S toggles and the Aa normalize button on the selection of cursor(); changed() runs after each;
    # align adds the paragraph alignment buttons
    def __init__(self, cursor, changed, align=False):
        super().__init__()
        self._cursor, self._changed = cursor, changed
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        self.buttons = {}
        for attr, label in (
            ("bold", "B"),
            ("italic", "I"),
            ("underline", "U"),
            ("strike", "S"),
        ):
            b = _tool(label)
            f = b.font()
            f.setBold(attr == "bold")
            f.setItalic(attr == "italic")
            f.setUnderline(attr == "underline")
            f.setStrikeOut(attr == "strike")
            b.setFont(f)
            b.clicked.connect(lambda _=False, a=attr: self.toggle(a))
            row.addWidget(b)
            self.buttons[attr] = b
        self.normal = _tool("Aa")
        self.normal.setCheckable(False)
        self.normal.clicked.connect(lambda _=False: self.normalize())
        row.addWidget(self.normal)
        self.aligns = {}
        if align:
            row.addSpacing(6)
            for a in ALIGNS:
                b = _tool("")
                b.setIcon(_align_icon(a))
                b.setIconSize(QSize(16, 16))
                b.clicked.connect(lambda _=False, a=a: self.align(a))
                row.addWidget(b)
                self.aligns[a] = b
        self.refresh()

    def toggle(self, attr):
        char_toggle(self._cursor(), attr)
        self.refresh()
        self._changed()

    def align(self, a):
        block_align(self._cursor(), a)
        self.refresh()
        self._changed()

    def normalize(self):
        char_normalize(self._cursor())
        self.refresh()
        self._changed()

    def refresh(self):
        c = self._cursor()
        for attr, b in self.buttons.items():
            b.setChecked(char_has(c, attr))
        cur = block_aligned(c) if self.aligns else None
        for a, b in self.aligns.items():
            b.setChecked(a == cur)

    def key(self, e):
        if e.modifiers() & (CTRL | SHIFT | ALT):
            return False
        if e.key() == Qt.Key.Key_N:
            self.normalize()
        elif e.key() in FORMAT_KEYS:
            self.toggle(FORMAT_KEYS[e.key()])
        else:
            return False
        return True


class HeadingRow(QWidget):
    # H1 H2 H3: level() is the target's level (0 = todo), apply(n) sets it, 0 turns it back into a todo
    def __init__(self, level, apply, changed):
        super().__init__()
        self._level, self._apply, self._changed = level, apply, changed
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        self.buttons = {}
        for n in (1, 2, 3):
            b = _tool(f"H{n}")
            b.clicked.connect(lambda _=False, n=n: self.set(n))
            row.addWidget(b)
            self.buttons[n] = b
        self.refresh()

    def set(self, n):
        self._apply(n)
        self.refresh()
        self._changed()

    def refresh(self):
        cur = self._level()
        for n, b in self.buttons.items():
            b.setChecked(n == cur)

    def key(self, e):
        n = HEADING_KEYS.get(e.key())
        if n is None or e.modifiers() & (CTRL | SHIFT | ALT):
            return False
        self.set(n)
        return True


def _mark_icon(mark):
    pm = QPixmap(40, 36)
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    paint_marker(p, QRectF(0, 0, 20, 18), mark, MARK_CELL_K)
    p.end()
    return QIcon(pm)


def _status_icon(rec):
    pm = QPixmap(40, 36)
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    rdb_status.paint_status(
        p, QRectF(0, 0, 20, 18), rec["icon"], rec["color"], MARK_CELL_K
    )
    p.end()
    return QIcon(pm)


# what the QuickMenu's item rows read and do: mark() -> the item's marker, set_mark(mark),
# status() -> the item's status key, set_status(key),
# tags() -> every project tag name, item_tags() -> the item's tags lower case, set_tag(name, on), link()
ItemOps = namedtuple(
    "ItemOps", "mark set_mark status set_status tags item_tags set_tag link"
)


class MarkRow(QWidget):
    # P1..P4 then the stars as checkable buttons over the item's one marker slot
    def __init__(self, mark, set_mark, changed):
        super().__init__()
        self._mark, self._set, self._changed = mark, set_mark, changed
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        self.buttons = {}
        for mk in (*PRIOS, *STARS):
            b = _tool("")
            b.setIcon(_mark_icon(mk))
            b.setIconSize(QSize(20, 18))
            b.clicked.connect(lambda _=False, mk=mk: self.pick(mk))
            row.addWidget(b)
            self.buttons[mk] = b
        self.refresh()

    def pick(self, mk):
        self._set("" if mk == self._mark() else mk)
        self._changed()

    def refresh(self):
        cur = self._mark()
        for mk, b in self.buttons.items():
            b.setChecked(mk == cur)


class StatusRow(MarkRow):
    # each configured status as a checkable button over the item's one status
    def __init__(self, status, set_status, changed):
        QWidget.__init__(self)
        self._mark, self._set, self._changed = status, set_status, changed
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        self.buttons = {}
        for rec in rdb_model.STATUS_LIST:
            b = _tool("")
            b.setIcon(_status_icon(rec))
            b.setIconSize(QSize(20, 18))
            b.setToolTip(rec["name"])
            b.clicked.connect(lambda _=False, key=rec["key"]: self.pick(key))
            row.addWidget(b)
            self.buttons[rec["key"]] = b
        self.refresh()


class TagChecks(QFrame):
    # a check box per tag name; toggle(name, on) runs on each change and the pop-out stays open
    def __init__(self, parent, names, on, toggle):
        super().__init__(parent, Qt.WindowType.Popup)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        rdb_theme.follow(self, rdb_theme.popup_frame)
        col = QVBoxLayout(self)
        col.setContentsMargins(6, 4, 6, 4)
        col.setSpacing(2)
        self.boxes = {}
        for key in sorted(names):
            b = QCheckBox(names[key])
            b.setChecked(key in on)
            b.toggled.connect(lambda v, n=names[key]: toggle(n, v))
            col.addWidget(b)
            self.boxes[key] = b


class SwatchRows(QWidget):
    # one row each: bg, fg (and a timeline's bar, from the bg swatches): none, pale, vivid
    picked = Signal(str, str)

    def __init__(self, settings, bar=False, keys=None):
        super().__init__()
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(4)
        for row, key in enumerate(
            keys or (("bg", "fg", "bar") if bar else ("bg", "fg"))
        ):
            grid.addWidget(QLabel(key), row, 0)
            src = {"bar": "bg", "line": "fg"}.get(key, key)
            colours = ["", *settings[f"bubble_{src}"], *settings[f"bubble_{src}_vivid"]]
            for col, colour in enumerate(colours, 1):
                s = _Swatch(colour, key)
                s.clicked.connect(lambda k=key, c=colour: self.picked.emit(k, c))
                grid.addWidget(s, row, col)


class H1ColourPopup(QFrame):
    # an H1's bg, line and fg: the QuickMenu's swatch rows, and a button that clears them all
    picked = Signal(str, str)
    reset = Signal()

    def __init__(self, parent, settings):
        super().__init__(parent, Qt.WindowType.Popup)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        rdb_theme.follow(self, rdb_theme.popup_frame)
        col = QVBoxLayout(self)
        col.setContentsMargins(6, 6, 6, 6)
        col.setSpacing(4)
        self.rows = SwatchRows(settings, keys=("bg", "line", "fg"))
        self.rows.picked.connect(self.pick)
        col.addWidget(self.rows)
        self.reset_btn = QPushButton("Reset Heading Colors")
        self.reset_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.reset_btn.clicked.connect(
            lambda _=False: (self.reset.emit(), self.close())
        )
        col.addWidget(self.reset_btn)

    def pick(self, key, colour):
        self.picked.emit(key, colour)
        self.close()


# the QuickMenu's cursor keys (arrows, vim hjkl, Tab / Shift+Tab)
POPUP_MOVES = frozenset(
    (
        Qt.Key.Key_Left,
        Qt.Key.Key_Right,
        Qt.Key.Key_Up,
        Qt.Key.Key_Down,
        Qt.Key.Key_H,
        Qt.Key.Key_J,
        Qt.Key.Key_K,
        Qt.Key.Key_L,
        Qt.Key.Key_Tab,
        Qt.Key.Key_Backtab,
    )
)
VIM_MOVES = frozenset((Qt.Key.Key_H, Qt.Key.Key_J, Qt.Key.Key_K, Qt.Key.Key_L))


PREVIEW_MAX, PREVIEW_PAD = 480, 4
IMAGE_FILES = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
CHIP_TEXT_MAX = 320
CHIP_LOOK = (
    "chip_style",
    "chip_font",
    "chip_size",
    "chip_weight",
    "chip_pad",
    "chip_icon",
)


def preview_sizes(sizes, screen, edge):
    # each image's longest edge is exactly edge px, up or down; all together, side by side, they
    # are scaled down further to fit the screen
    fit = [edge / max(w, h) for w, h in sizes]
    wide = sum(w * f for (w, _), f in zip(sizes, fit)) + PREVIEW_PAD * (len(sizes) + 1)
    high = max(h * f for (_, h), f in zip(sizes, fit)) + 2 * PREVIEW_PAD
    k = min(1, screen.width() / wide, screen.height() / high)
    return [
        QSize(max(1, round(w * f * k)), max(1, round(h * f * k)))
        for (w, h), f in zip(sizes, fit)
    ]


class HoverPreview(QFrame):
    # a popup beside a gutter icon; the mouse passes through it
    def __init__(self):
        super().__init__(
            None,
            Qt.WindowType.ToolTip
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowTransparentForInput,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        rdb_theme.follow(self, rdb_theme.popup_frame)


class NotePreview(HoverPreview):
    # the stored text of a note, clipped to PREVIEW_MAX square and the screen
    def __init__(self, doc, screen):
        super().__init__()
        self.doc = doc
        wide = min(PREVIEW_MAX, screen.width()) - 2 * PREVIEW_PAD
        for name in image_names(doc):
            img = doc.resource(QTextDocument.ResourceType.ImageResource, QUrl(name))
            if img.width() / img.devicePixelRatio() > wide:
                img = img.scaledToWidth(
                    round(wide * self.devicePixelRatioF()),
                    Qt.TransformationMode.SmoothTransformation,
                )
                img.setDevicePixelRatio(self.devicePixelRatioF())
                doc.addResource(
                    QTextDocument.ResourceType.ImageResource, QUrl(name), img
                )
        doc.setDocumentMargin(PREVIEW_PAD)
        doc.setTextWidth(wide + 2 * PREVIEW_PAD)
        self.setFixedSize(
            min(PREVIEW_MAX, screen.width(), math.ceil(doc.idealWidth())),
            min(PREVIEW_MAX, screen.height(), math.ceil(doc.size().height())),
        )

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        ctx = QAbstractTextDocumentLayout.PaintContext()
        ctx.palette = self.palette()
        ctx.palette.setColor(
            QPalette.ColorRole.Text, self.palette().color(QPalette.ColorRole.WindowText)
        )
        ctx.clip = QRectF(self.rect())
        p.setClipRect(self.rect())
        self.doc.documentLayout().draw(p, ctx)


class ImagePreview(HoverPreview):
    # the images of one item, shown beside its gutter icon
    def __init__(self, images, screen, edge):
        super().__init__()
        dpr = self.devicePixelRatioF()
        row = QHBoxLayout(self)
        row.setContentsMargins(*[PREVIEW_PAD] * 4)
        row.setSpacing(PREVIEW_PAD)
        self.sizes = preview_sizes(
            [
                (i.width() / i.devicePixelRatio(), i.height() / i.devicePixelRatio())
                for i in images
            ],
            screen,
            edge,
        )
        for img, size in zip(images, self.sizes):
            px = img.scaled(
                size * dpr,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            px.setDevicePixelRatio(dpr)
            lab = QLabel()
            lab.setPixmap(QPixmap.fromImage(px))
            lab.setFixedSize(size)
            row.addWidget(lab)


def _pen_icon(kind):
    pm = QPixmap(16, 16)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    c = QColor(rdb_markup.PENS[kind][0])
    c.setAlpha(200 if kind == "yellow" else 255)
    p.setPen(QPen(QColor("#444444"), 1))
    p.setBrush(c)
    p.translate(8, 8)
    p.rotate(45)
    wide = 6 if kind == "yellow" else 3
    p.drawRect(QRectF(-wide / 2, -7, wide, 11))
    p.setBrush(QColor("#444444"))
    p.drawPolygon([QPointF(-wide / 2, 4), QPointF(wide / 2, 4), QPointF(0, 7.5)])
    p.end()
    return QIcon(pm)


def _clear_icon():
    pm = QPixmap(16, 16)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.translate(8, 8)
    p.rotate(-40)
    p.setPen(QPen(QColor("#555555"), 1))
    p.setBrush(QColor("#f0a0a0"))
    p.drawRect(QRectF(-6, -3.5, 7, 7))
    p.setBrush(QColor("#d8d8d8"))
    p.drawRect(QRectF(1, -3.5, 5, 7))
    p.end()
    return QIcon(pm)


class FitImage(QWidget):
    # one picture stretched to fill the widget, with its markup over it; with a pen picked a drag
    # draws a stroke, reported in image pixels
    stroked = Signal(dict)

    def __init__(self):
        super().__init__()
        self.img = None
        self.strokes = []
        self.tool = None
        self.live = None
        self.setMinimumSize(32, 32)

    def show_image(self, img, strokes):
        self.img, self.strokes, self.live = img, strokes, None
        self.update()

    def shown_size(self):
        return QSizeF(self.size())

    def _to_image(self, pt):
        x = pt.x() * self.img.width() / self.width()
        y = pt.y() * self.img.height() / self.height()
        return [
            round(min(max(x, 0), self.img.width()), 1),
            round(min(max(y, 0), self.img.height()), 1),
        ]

    def mousePressEvent(self, e):
        left = e.button() == Qt.MouseButton.LeftButton
        if self.tool and self.img is not None and left:
            w = rdb_markup.pen_width(self.img.width(), self.img.height(), self.tool)
            self.live = {"k": self.tool, "w": w, "p": [self._to_image(e.position())]}
            self.update()

    def mouseMoveEvent(self, e):
        if self.live is not None:
            self.live["p"].append(self._to_image(e.position()))
            self.update()

    def mouseReleaseEvent(self, e):
        if self.live is not None and e.button() == Qt.MouseButton.LeftButton:
            done, self.live = self.live, None
            self.update()
            self.stroked.emit(done)

    def paintEvent(self, _e):
        if self.img is None:
            return
        size, dpr = self.shown_size(), self.devicePixelRatioF()
        px = self.img.scaled(
            round(size.width() * dpr),
            round(size.height() * dpr),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        px.setDevicePixelRatio(dpr)
        p = QPainter(self)
        p.drawImage(0, 0, px)
        p.scale(size.width() / self.img.width(), size.height() / self.img.height())
        p.setClipRect(QRectF(0, 0, self.img.width(), self.img.height()))
        rdb_markup.paint_strokes(p, self.strokes + ([self.live] if self.live else []))


class ViewerBar(QWidget):
    # the viewer's own title bar, in the main window's look for the theme: icon, title, then the
    # pin where Minimize would be, Maximize and Close
    colors = TitleBar.colors
    sync_look = TitleBar.sync_look
    toggle_maximized = TitleBar.toggle_maximized

    def __init__(self, host, title, theme):
        super().__init__(host)
        t = THEMES[theme]
        self.host, self.theme, self.frame, self.edge = (
            host,
            theme,
            t["frame"],
            t["edge"],
        )
        self.family = (
            None
            if theme == "Classic"
            else rdb_bar.icon_family(QFontDatabase.families())
        )
        self.dark, self._dwm, self._ink = False, None, None
        self.setFixedHeight(t["height"])
        self.icon = QLabel()
        self.icon.setFixedSize(20, t["height"])
        self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title = QLabel(title)
        if theme == "Classic":
            # the native classic caption font: Tahoma 8 bold
            font = QFont(rdb_bar.FONT)
            font.setBold(True)
            self.title.setFont(font)
        self.title.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        for w in (self.icon, self.title):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.caption = {k: CaptionButton(k) for k in ("pin", "max", "close")}
        self.caption["pin"].setCheckable(True)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, t["right"], 0)
        row.setSpacing(0)
        row.addWidget(self.icon)
        row.addWidget(self.title, 1)
        for k, b in self.caption.items():
            b.set_theme(theme)
            if k == "close":
                row.addSpacing(t["gap"])
            row.addWidget(b, 0, Qt.AlignmentFlag.AlignVCenter)
        self.caption["max"].clicked.connect(self.toggle_maximized)
        self.caption["close"].clicked.connect(host.close)

    def active(self):
        return self.host.isActiveWindow()

    def sync(self):
        h = self.host
        h.setContentsMargins(*[0 if h.isMaximized() else self.frame] * 4)
        self.icon.setPixmap(h.windowIcon().pixmap(16, 16))
        self.caption["max"].kind = "restore" if h.isMaximized() else "max"
        self.sync_look()
        for w in self.caption.values():
            w.update()
        self.update()

    def paintEvent(self, _e):
        QPainter(self).fillRect(self.rect(), self.colors()[0])

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.host.windowHandle().startSystemMove()

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.toggle_maximized()


class ImageWindow(QWidget):
    # one image of an item at a time in a window of its own, with a tab per image when there are
    # several; Esc closes, Numpad + and - step, Delete removes the shown image from the item,
    # Ctrl+C copies it with its markup, Ctrl+Z undoes the last stroke or clear.
    # load(name) -> QImage; remove(name) asks the owner to drop the image, which then calls
    # set_names; strokes(name) -> the markup; save(name, strokes) keeps it
    closed = Signal()

    def __init__(self, title, names, load, remove, strokes, save, theme):
        super().__init__(None, Qt.WindowType.Window)
        self.names, self._load, self._remove = list(names), load, remove
        self._strokes, self._save, self.undo_stack = strokes, save, []
        self.setWindowTitle(title)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.bar = ViewerBar(self, title, theme)
        self.pin = self.bar.caption["pin"]
        self.pin.toggled.connect(self._pinned)
        tools = QHBoxLayout()
        tools.setContentsMargins(2, 2, 2, 2)
        tools.setSpacing(2)
        self.pens = {}
        for kind in rdb_markup.PENS:
            b = QToolButton()
            b.setIcon(_pen_icon(kind))
            b.setToolTip(kind.title())
            b.setCheckable(True)
            b.setAutoRaise(True)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(lambda on, k=kind: self._pick(k, on))
            self.pens[kind] = b
            tools.addWidget(b)
        self.clear_btn = QToolButton()
        self.clear_btn.setIcon(_clear_icon())
        self.clear_btn.setToolTip("Clear")
        self.clear_btn.setAutoRaise(True)
        self.clear_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.clear_btn.clicked.connect(self.clear_markup)
        tools.addWidget(self.clear_btn)
        tools.addStretch(1)
        self.tabs = QListWidget()
        self.tabs.setFixedWidth(40)
        self.tabs.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.tabs.currentRowChanged.connect(self._show)
        self.view = FitImage()
        self.view.stroked.connect(self._stroked)
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self.tabs)
        body.addWidget(self.view, 1)
        page = QVBoxLayout(self)
        page.setContentsMargins(0, 0, 0, 0)
        page.setSpacing(0)
        page.addWidget(self.bar)
        page.addLayout(tools)
        page.addLayout(body, 1)
        self.resizer = rdb_bar.EdgeResizer(self)
        self.bar.sync()
        self._fill(0)
        self._size_to_image()

    def _size_to_image(self):
        # the canvas opens in the shape of the first image, so it starts undistorted
        img = self.view.img
        dpr = img.devicePixelRatio()
        iw, ih = img.width() / dpr, img.height() / dpr
        avail = QGuiApplication.primaryScreen().availableGeometry()
        k = min(1.0, 0.8 * avail.width() / iw, 0.8 * avail.height() / ih)
        self.layout().activate()
        extra = self.size() - self.view.size()
        self.resize(round(iw * k) + extra.width(), round(ih * k) + extra.height())

    def _pinned(self, on):
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on)
        self.show()

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() in (QEvent.Type.WindowStateChange, QEvent.Type.ActivationChange):
            self.bar.sync()

    def paintEvent(self, e):
        super().paintEvent(e)
        rdb_bar.paint_border(self)

    def _pick(self, kind, on):
        for k, b in self.pens.items():
            b.setChecked(on and k == kind)
        self.view.tool = kind if on else None

    def _fill(self, row):
        self.tabs.blockSignals(True)
        self.tabs.clear()
        self.tabs.addItems([str(i + 1) for i in range(len(self.names))])
        self.tabs.setVisible(len(self.names) > 1)
        self.tabs.setCurrentRow(row)
        self.tabs.blockSignals(False)
        self._show(row)

    def _show(self, row):
        if row >= 0:
            name = self.names[row]
            marks = self._strokes(name)
            self.view.show_image(self._load(name), marks)
            self.clear_btn.setEnabled(bool(marks))

    def _name(self):
        return self.names[self.tabs.currentRow()]

    def _set(self, name, marks):
        self._save(name, marks)
        if name == self._name():
            self.view.strokes = marks
            self.view.update()
            self.clear_btn.setEnabled(bool(marks))

    def _stroked(self, stroke):
        name = self._name()
        before = self._strokes(name)
        self.undo_stack.append((name, before))
        self._set(name, [*before, stroke])

    def clear_markup(self):
        name = self._name()
        before = self._strokes(name)
        if before:
            self.undo_stack.append((name, before))
            self._set(name, [])

    def undo_markup(self):
        while self.undo_stack:
            name, before = self.undo_stack.pop()
            if name in self.names:
                self.tabs.setCurrentRow(self.names.index(name))
                self._set(name, before)
                return

    def copy_image(self):
        QApplication.clipboard().setImage(
            rdb_markup.marked(self.view.img, self.view.strokes)
        )

    def step(self, d):
        self.tabs.setCurrentRow(
            min(max(self.tabs.currentRow() + d, 0), len(self.names) - 1)
        )

    def keyPressEvent(self, e):
        key = e.key()
        keypad = e.modifiers() & Qt.KeyboardModifier.KeypadModifier
        ctrl = e.modifiers() & Qt.KeyboardModifier.ControlModifier
        if key == Qt.Key.Key_Escape:
            self.close()
        elif keypad and key in (Qt.Key.Key_Plus, Qt.Key.Key_Minus):
            self.step(1 if key == Qt.Key.Key_Plus else -1)
        elif key == Qt.Key.Key_Delete:
            self._remove(self._name())
        elif ctrl and key == Qt.Key.Key_Z:
            self.undo_markup()
        elif ctrl and key == Qt.Key.Key_C:
            self.copy_image()
        else:
            super().keyPressEvent(e)

    def set_names(self, names):
        # the item no longer holds some of the images: their tabs go, and the window with the last
        row, shown = self.tabs.currentRow(), self._name()
        self.names = [n for n in self.names if n in names]
        if not self.names:
            self.close()
            return
        last = len(self.names) - 1
        self._fill(self.names.index(shown) if shown in self.names else min(row, last))

    def closeEvent(self, e):
        self.resizer.show_cursor(rdb_bar.NO_EDGE)
        self.closed.emit()
        super().closeEvent(e)


class SwatchPopup(QFrame):
    # the QuickMenu. cursor() -> QTextCursor whose selection is the B I U S target;
    # changed() runs after any change; heading = (level(), apply(n)) adds the H1 H2 H3 row;
    # colours() -> bool shows or hides the swatch and marker rows (None = always shown); align adds
    # the paragraph alignment buttons; ops (ItemOps) adds the marker row and the # and link buttons
    picked = Signal(str, str)

    def __init__(
        self,
        parent,
        settings,
        cursor,
        changed,
        heading=None,
        colours=None,
        align=False,
        move=None,
        bar=False,
        ops=None,
    ):
        super().__init__(parent, Qt.WindowType.Popup)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        rdb_theme.follow(self, rdb_theme.popup_frame)
        self._changed, self._colours, self._ops = changed, colours, ops
        self._moves = POPUP_MOVES if settings["vim"] else POPUP_MOVES - VIM_MOVES
        col = QVBoxLayout(self)
        col.setContentsMargins(6, 6, 6, 6)
        col.setSpacing(4)
        top = QHBoxLayout()
        top.setSpacing(8)
        self.fmt = FormatRow(cursor, self._after, align)
        top.addWidget(self.fmt)
        self.head = None
        if heading is not None:
            self.head = HeadingRow(*heading, self._after)
            top.addWidget(self.head)
        self._move = move
        if move is not None:
            b = _tool("Move To")
            b.setCheckable(False)
            b.setFixedWidth(b.fontMetrics().horizontalAdvance("Move To") + 12)
            b.clicked.connect(lambda _=False: self.move_to())
            top.addWidget(b)
        self.marks = self.statuses = self.tag_btn = self.link_btn = None
        if ops is not None:
            # imported here: rdb_findbar imports rdb_finder, which imports this module
            from rdb_findbar import glyph_button, size_glyph

            self.tag_btn = glyph_button("tag", self, checkable=False)
            self.link_btn = glyph_button("link", self, checkable=False)
            self.tag_btn.clicked.connect(lambda _=False: self.pop_tags())
            self.link_btn.clicked.connect(lambda _=False: self.link())
            for b in (self.tag_btn, self.link_btn):
                size_glyph(b, 16, 22)
                b.setFixedWidth(24)
                top.addWidget(b)
        top.addStretch(1)
        col.addLayout(top)
        if ops is not None:
            self.marks = MarkRow(ops.mark, ops.set_mark, self._after)
            col.addWidget(self.marks, 0, Qt.AlignmentFlag.AlignLeft)
            if rdb_model.STATUS_LIST:
                self.statuses = StatusRow(ops.status, ops.set_status, self._after)
                col.addWidget(self.statuses, 0, Qt.AlignmentFlag.AlignLeft)
        self.rows = SwatchRows(settings, bar)
        self.rows.picked.connect(self.pick)
        col.addWidget(self.rows)
        # the keyboard cursor: a ring over one button or swatch
        self.at = (0, 0)
        self.ring = QFrame(self)
        rdb_theme.follow(self.ring, rdb_theme.ring_sheet)
        self.ring.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.refresh()

    @property
    def cells(self):
        # rows of controls as laid out: the button row, the marker row, the status row, then each
        # shown swatch row
        marks = list(self.marks.buttons.values()) if self.marks else []
        stats = list(self.statuses.buttons.values()) if self.statuses else []
        top = sorted(
            (
                b
                for b in self.findChildren(QToolButton)
                if b not in marks and b not in stats and not b.isHidden()
            ),
            key=lambda b: b.mapTo(self, QPoint()).x(),
        )
        out = [top]
        if marks and (self.marks.isVisible() or self._colours is None):
            out.append(marks)
        if stats and (self.statuses.isVisible() or self._colours is None):
            out.append(stats)
        if self.rows.isVisible() or self._colours is None:
            grid, rows = self.rows.layout(), {}
            for i in range(grid.count()):
                w = grid.itemAt(i).widget()
                if isinstance(w, _Swatch):
                    r, c, _, _ = grid.getItemPosition(i)
                    rows.setdefault(r, []).append((c, w))
            out += [[w for _, w in sorted(rows[r])] for r in sorted(rows)]
        return out

    def _centre_x(self, w):
        return w.mapTo(self, w.rect().center()).x()

    def _place_ring(self):
        cells = self.cells
        r = min(self.at[0], len(cells) - 1)
        self.at = (r, min(self.at[1], len(cells[r]) - 1))
        w = cells[r][self.at[1]]
        self.ring.setGeometry(
            QRect(w.mapTo(self, QPoint()), w.size()).adjusted(-2, -2, 2, 2)
        )
        self.ring.raise_()
        self.ring.show()

    def showEvent(self, e):
        super().showEvent(e)
        self._place_ring()

    def _step(self, key):
        cells, (r, c) = self.cells, self.at
        if key in (Qt.Key.Key_Left, Qt.Key.Key_H):
            c = max(c - 1, 0)
        elif key in (Qt.Key.Key_Right, Qt.Key.Key_L):
            c = min(c + 1, len(cells[r]) - 1)
        elif key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            flat = [(i, j) for i, row in enumerate(cells) for j in range(len(row))]
            n = flat.index((r, c)) + (1 if key == Qt.Key.Key_Tab else -1)
            r, c = flat[n % len(flat)]
        else:
            nr = r + (1 if key in (Qt.Key.Key_Down, Qt.Key.Key_J) else -1)
            if 0 <= nr < len(cells):
                x = self._centre_x(cells[r][c])
                c = min(
                    range(len(cells[nr])),
                    key=lambda j: abs(self._centre_x(cells[nr][j]) - x),
                )
                r = nr
        self.at = (r, c)
        self._place_ring()

    def _press(self):
        w = self.cells[self.at[0]][self.at[1]]
        if isinstance(w, QToolButton):
            w.click()
        else:
            w.clicked.emit()

    def _after(self):
        self.refresh()
        self._changed()

    def refresh(self):
        self.fmt.refresh()
        if self.head is not None:
            self.head.refresh()
        for row in (self.marks, self.statuses):
            if row is not None:
                row.refresh()
        if self._colours is not None:
            shown = self._colours()
            self.rows.setVisible(shown)
            if self.marks is not None:
                self.marks.setVisible(shown)
                self.tag_btn.setVisible(shown)
            if self.statuses is not None:
                self.statuses.setVisible(shown)
            self.adjustSize()
            if self.isVisible():
                self._place_ring()

    def move_to(self):
        self.close()
        self._move()

    def link(self):
        self.close()
        self._ops.link()

    def pop_tags(self):
        names = {n.lower(): n for n in self._ops.tags()}
        mine = self._ops.item_tags()
        for t in mine:
            names.setdefault(t, t)
        if not names:
            return
        pop = TagChecks(self, names, mine, self._ops.set_tag)
        pop.move(self.tag_btn.mapToGlobal(self.tag_btn.rect().bottomLeft()))
        pop.show()

    def keyPressEvent(self, e):
        k, bare = e.key(), not e.modifiers() & (CTRL | ALT)
        if bare and k in self._moves:
            self._step(k)
        elif bare and k in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self._press()
        elif (
            self._move is not None
            and e.key() == Qt.Key.Key_M
            and not e.modifiers() & (CTRL | SHIFT | ALT)
        ):
            self.move_to()
        elif not (self.fmt.key(e) or (self.head is not None and self.head.key(e))):
            super().keyPressEvent(e)

    def pick(self, key, colour):
        self.picked.emit(key, colour)
        self.close()


def paint_disc(p, r, line, fill, z):
    p.setPen(QPen(line, 1.5 * z))
    p.setBrush(fill)
    h = 0.75 * z
    p.drawEllipse(r.adjusted(h, h, -h, -h))


def paint_check(p, check, checked, color):
    k = check.width() / 13
    if checked:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawRoundedRect(check, 3 * k, 3 * k)
        pen = QPen(rdb_theme.ink_on(color), 1.8 * k)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        x, y = check.left(), check.top()
        p.drawPolyline(
            QPolygonF(
                [
                    QPointF(x + 3 * k, y + 6.8 * k),
                    QPointF(x + 5.6 * k, y + 9.4 * k),
                    QPointF(x + 10 * k, y + 3.8 * k),
                ]
            )
        )
    else:
        p.setPen(QPen(_shade("#7a7a7a"), 1.5 * k))
        p.setBrush(_shade("#ffffff"))
        h = 0.75 * k
        p.drawRoundedRect(check.adjusted(h, h, -h, -h), 3 * k, 3 * k)


def paint_note_icon(p, rect, fill, edge, z):
    p.save()
    p.translate(round(rect.left()), round(rect.top()))
    p.scale(z, z)
    # a 9x11 sheet in 1 px lines with its top right corner folded over 3 px; whole pixels
    # and no antialiasing keep every line crisp
    p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    for rx, ry, w, h in ((1, 1, 4, 9), (5, 4, 3, 6), (6, 2, 1, 1)):
        p.fillRect(rx, ry, w, h, fill)
    for rx, ry, w, h in (
        (0, 0, 6, 1),
        (0, 10, 9, 1),
        (0, 0, 1, 11),
        (8, 3, 1, 8),
        (6, 1, 1, 1),
        (7, 2, 1, 1),
        (5, 1, 1, 3),
        (6, 3, 2, 1),
    ):
        p.fillRect(rx, ry, w, h, edge)
    p.restore()


def paint_picture_icon(p, r):
    # a glossy picture icon: a bevelled frame, a blue sky, a green hill and a small sun
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    side = min(r.width(), r.height())
    box = QRectF(r.center().x() - side / 2, r.center().y() - side / 2, side, side)
    rad = side * 0.16
    frame = box.adjusted(0.5, 0.5, -0.5, -0.5)
    g = QLinearGradient(frame.topLeft(), frame.bottomLeft())
    g.setColorAt(0, QColor("#ffffff"))
    g.setColorAt(1, QColor("#c9d6e6"))
    p.setPen(QPen(QColor("#5f7a9c"), 1))
    p.setBrush(g)
    p.drawRoundedRect(frame, rad, rad)
    inner = frame.adjusted(side * 0.12, side * 0.12, -side * 0.12, -side * 0.12)
    clip = QPainterPath()
    clip.addRoundedRect(inner, rad * 0.5, rad * 0.5)
    p.setClipPath(clip)
    sky = QLinearGradient(inner.topLeft(), inner.bottomLeft())
    sky.setColorAt(0, QColor("#4aa8ee"))
    sky.setColorAt(1, QColor("#c4e6fb"))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(sky)
    p.drawRect(inner)
    hill = QLinearGradient(
        inner.left(), inner.center().y(), inner.left(), inner.bottom()
    )
    hill.setColorAt(0, QColor("#86d24c"))
    hill.setColorAt(1, QColor("#2f8f2f"))
    p.setBrush(hill)
    p.drawEllipse(
        QRectF(
            inner.left() - inner.width() * 0.25,
            inner.top() + inner.height() * 0.55,
            inner.width() * 1.5,
            inner.height() * 1.2,
        )
    )
    sun = inner.width() * 0.26
    p.setPen(QPen(QColor("#f0b400"), max(0.5, side / 14)))
    p.setBrush(QColor("#ffe45c"))
    p.drawEllipse(QRectF(inner.right() - sun * 1.5, inner.top() + sun * 0.4, sun, sun))
    shine = QLinearGradient(inner.topLeft(), inner.bottomLeft())
    shine.setColorAt(0, QColor(255, 255, 255, 110))
    shine.setColorAt(1, QColor(255, 255, 255, 0))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(shine)
    p.drawRect(QRectF(inner.left(), inner.top(), inner.width(), inner.height() * 0.5))
    p.restore()


def paint_part_glyph(p, key, at, s):
    # a sample of one gutter part at 100%, centred on the point at, as a row paints it
    w, h = PART_SIZE[key]
    r = QRectF(at.x() - w / 2, at.y() - h / 2, w, h)
    if key == "number":
        p.setFont(BOLD8)
        p.setPen(_shown(s, "number_color"))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, "1")
    elif key == "check":
        paint_check(p, r, False, _shown(s, "check_color"))
    elif key == "bubble":
        paint_disc(p, r, _shade("#aaaaaa"), _shade("#ffffff"), 1.0)
    elif key == "note":
        paint_note_icon(p, r, _shown(s, "note_bg"), _shown(s, "note_border"), 1.0)
    elif key == "mark":
        paint_marker(p, r, "p1", 1.0)
    elif key == "status":
        done = next(x for x in rdb_model.DEFAULT_STATUSES if x["key"] == "completed")
        rdb_status.paint_status(p, r, done["icon"], done["color"], 1.0)
    elif key == "fold":
        _paint_fold_box(p, r, _shade(GRIP_INK), True)
    elif key == "swatch":
        paint_disc(p, r, _shown(s, "h1_border"), _shown(s, "h1_bg"), 1.0)
    else:
        font = QFont(BOLD8)
        font.setPixelSize(11)
        p.setFont(font)
        p.setPen(_shade(GRIP_INK))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, "3")


def _paint_fold_box(p, r, ink, plus, partial=False):
    # drawn in whole device pixels, with the box and its bars of one parity: otherwise a
    # fractional screen scale, zoom or position puts the cross a pixel off centre
    dpr = p.device().devicePixelRatioF()
    box = p.transform().mapRect(r)
    side = max(5, round(box.width() * dpr))
    stroke = max(1, round(side / 9))
    if (side - stroke) % 2:
        side -= 1
    inset = round(2 * side / 9)
    mid = (side - stroke) // 2
    bar = side - 2 * inset
    p.save()
    p.resetTransform()
    p.scale(1 / dpr, 1 / dpr)
    p.translate(round(box.left() * dpr), round(box.top() * dpr))
    p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    for x, y, w, h in (
        (0, 0, side, stroke),
        (0, side - stroke, side, stroke),
        (0, 0, stroke, side),
        (side - stroke, 0, stroke, side),
    ):
        p.fillRect(x, y, w, h, ink)
    if partial:
        q = side // 3 + (side - side // 3) % 2
        p.fillRect((side - q) // 2, (side - q) // 2, q, q, ink)
    else:
        p.fillRect(inset, mid, bar, stroke, ink)
    if plus and not partial:
        p.fillRect(mid, inset, stroke, bar, ink)
    p.restore()


def _paint_folder(p, r):
    p.save()
    p.translate(r.topLeft())
    p.scale(r.width() / 12, r.height() / 12)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QPen(QColor("#a07810"), 1))
    p.setBrush(QColor("#f0c040"))
    p.drawRoundedRect(QRectF(0.5, 1.5, 5, 3), 1, 1)
    p.drawRoundedRect(QRectF(0.5, 3.5, 11, 7), 1.2, 1.2)
    p.restore()


# the platform's file-type icons (made on first use: it needs the application), per path
SHELL = None
ICONS = {}


def file_icon(local):
    # the icon the shell shows for this file, None when the platform gives none
    global SHELL
    if local not in ICONS:
        if SHELL is None:
            SHELL = QFileIconProvider()
        icon = SHELL.icon(QFileInfo(local))
        ICONS[local] = None if icon.isNull() else icon
    return ICONS[local]


def _paint_chip(p, r, s, missing, z, hot=False, tag=False):
    style, edge = s["chip_style"], r.adjusted(0.5, 0.5, -0.5, -0.5)
    kind = "tag" if tag else "chip"
    accent = _shade(URGENT) if missing else _shown(s, "hover_accent")
    fill = _shade("#fbe3e3") if missing else _shown(s, f"{kind}_fill")
    border = _shade(URGENT) if missing else _shown(s, f"{kind}_border")
    if hot:
        # hot: a soft halo in the accent colour, a brighter body, an accent edge
        rad = {"box": 4 * z, "pill": r.height() / 2, "token": 3 * z}[style]
        p.setPen(Qt.PenStyle.NoPen)
        for grow, alpha in ((3, 40), (1.5, 80)) if s["hover_glow"] else ():
            accent.setAlpha(alpha)
            p.setBrush(accent)
            g = grow * z
            p.drawRoundedRect(r.adjusted(-g, -g, g, g), rad + g, rad + g)
        accent.setAlpha(255)
        fill, border = fill.lighter(112), accent
    if style == "box":
        p.setPen(QPen(border, 1))
        p.setBrush(fill)
        p.drawRoundedRect(edge, 4 * z, 4 * z)
    elif style == "pill":
        rad = r.height() / 2
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 30))
        p.drawRoundedRect(r.translated(0, z), rad, rad)
        g = QLinearGradient(r.topLeft(), r.bottomLeft())
        g.setColorAt(0, _shade("#ffffff"))
        g.setColorAt(1, fill)
        p.setPen(QPen(border, 1))
        p.setBrush(g)
        p.drawRoundedRect(edge, rad, rad)
    elif style == "token":
        p.setPen(QPen(border, 1))
        p.setBrush(fill)
        p.drawRoundedRect(edge, 3 * z, 3 * z)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(accent)
        p.drawRoundedRect(
            QRectF(edge.left(), edge.top(), 3 * z, edge.height()), 1.5 * z, 1.5 * z
        )
    else:
        raise ValueError(f"chip_style {style!r}")


def _paint_file(p, r):
    p.save()
    p.translate(r.topLeft())
    p.scale(r.width() / 12, r.height() / 12)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QPen(_shade("#707070"), 1))
    p.setBrush(_shade("#ffffff"))
    p.drawPolygon(
        QPolygonF(
            [
                QPointF(2.5, 0.5),
                QPointF(7.5, 0.5),
                QPointF(10, 3),
                QPointF(10, 11.5),
                QPointF(2.5, 11.5),
            ]
        )
    )
    p.drawPolyline(QPolygonF([QPointF(7.5, 0.5), QPointF(7.5, 3), QPointF(10, 3)]))
    p.restore()


# a '#' that opens a tag (as m.TAG_RX needs) with the word typed so far; an empty word counts
TAG_START = re.compile(r"(?<![^\s(\[{\"'])#[\w-]*")


def _u16(text, a, b):
    return rdb_model._utf16(text[a:b])


def _edits_text(e):
    if e.modifiers() & (CTRL | ALT):
        return False
    return (
        e.key() in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete)
        or e.text().isprintable()
        and e.text() != ""
    )


class TagList(QListWidget):
    # the fuzzy list of project tags under the caret while a #tag is typed; the editor's
    # tag_source() -> names is read once per token
    def __init__(self, ed):
        super().__init__(ed.viewport())
        self.ed = ed
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.names, self.start, self.shut, self.loaded = {}, None, None, None
        self.setStyleSheet(
            "QListWidget::item{padding:2px 6px 2px 5px}"
            "QListWidget::item:selected{background:palette(highlight);"
            "color:palette(highlighted-text)}"
        )
        self.itemClicked.connect(lambda _i: self.accept())
        self.hide()

    def token(self):
        # (document position of '#', position after the word, word before the caret) at the caret
        ed = self.ed
        c = ed.textCursor()
        if c.hasSelection() or ed.mode in VIM_MODES or ed.note or ed.isReadOnly():
            return None
        block, col = c.block(), c.positionInBlock()
        text = block.text()
        for mt in TAG_START.finditer(text):
            a, b = _u16(text, 0, mt.start()), _u16(text, 0, mt.end())
            if a < col <= b:
                word = text.encode("utf-16-le")[2 * (a + 1) : 2 * col]
                return (
                    block.position() + a,
                    block.position() + b,
                    word.decode("utf-16-le"),
                )
        return None

    def refresh(self, typed):
        # after a key: a key that changed the text may show the list; any key may narrow or hide it
        ed = self.ed
        tok = self.token() if ed.tag_source is not None else None
        if tok is None:
            self.start = self.shut = None
            self.hide()
            return
        if tok[0] == self.shut:
            self.hide()
            return
        if tok[0] != self.start:
            if not typed:
                self.hide()
                return
            self.start, self.loaded = tok[0], None
            self._load(tok[0])
        found = rdb_model.fuzzy_tags(list(self.names), tok[2].lower())
        if not found:
            self.hide()
            return
        f = rdb_model.list_font(ed.settings, "body")
        f.setPointSizeF(max(f.pointSizeF(), ed.settings["chip_size"]) * ed.zoom)
        self.setFont(f)
        self.clear()
        self.addItems([self.names[k] for k in found])
        self.setCurrentRow(0)
        vp = ed.viewport()
        row = self.sizeHintForRow(0)
        h = min(len(found), 8) * row + 2 * self.frameWidth()
        w = max(self.sizeHintForColumn(0) + 2 * self.frameWidth() + 8, 120)
        cr = ed.cursorRect()
        y = cr.bottom() + 1
        if y + h > vp.height():
            y = max(cr.top() - h, 0)
        self.setGeometry(min(cr.left(), max(vp.width() - w, 0)), y, w, h)
        self.show()
        self.raise_()

    def _load(self, at):
        if self.loaded != at:
            self.names = {n.lower(): n for n in self.ed.tag_source()}
            self.loaded = at

    def fix_case(self, before):
        # a typed tag takes the case of the known tag it equals ignoring case, as the word before
        # the caret and as the tag ends
        tok = self.token()
        if tok is not None:
            self._recase(tok[0], tok[2])
            if before is None or before[0] == tok[0]:
                return
        if before is not None:
            self._recase(before[0], None)

    def _recase(self, at, typed):
        doc = self.ed.document()
        block = doc.findBlock(at)
        if is_heading(block):
            return
        text = block.text()
        u16 = text.encode("utf-16-le")
        idx = len(u16[: 2 * (at - block.position())].decode("utf-16-le"))
        mt = TAG_START.match(text, idx)
        if mt is None:
            return
        word = mt.group()[1:] if typed is None else typed
        low = word.lower()
        if not low:
            return
        self._load(at)
        new = self.names.get(low)
        if new is None or new == word or len(new) != len(word):
            return
        caret = self.ed.textCursor().position()
        c = QTextCursor(doc)
        c.joinPreviousEditBlock()
        c.setPosition(at + 1)
        c.setPosition(
            at + 1 + _u16(word, 0, len(word)), QTextCursor.MoveMode.KeepAnchor
        )
        c.insertText(new)
        c.endEditBlock()
        keep = QTextCursor(doc)
        keep.setPosition(caret)
        self.ed.setTextCursor(keep)

    def recheck(self):
        if self.isVisible() and self.token() is None:
            self.start = self.shut = None
            self.hide()

    def key(self, e):
        # True when the open list took the key
        k, mods = e.key(), e.modifiers() & (CTRL | SHIFT | ALT)
        if mods:
            return False
        if k == Qt.Key.Key_Escape:
            self.shut = self.start
            self.hide()
        elif k in (Qt.Key.Key_Tab, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.accept()
        elif k in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            row = self.currentRow() + (1 if k == Qt.Key.Key_Down else -1)
            self.setCurrentRow(min(max(row, 0), self.count() - 1))
        else:
            return False
        return True

    def accept(self):
        item, tok = self.currentItem(), self.token()
        self.hide()
        self.start = self.shut = None
        if item is None or tok is None:
            return
        doc = self.ed.document()
        end = tok[1] + (doc.characterAt(tok[1]) == " ")
        c = QTextCursor(doc)
        c.beginEditBlock()
        c.setPosition(tok[0])
        c.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        c.insertText(f"#{item.text()} ")
        c.endEditBlock()
        self.ed.setTextCursor(c)


def _edge_scroll(bar, y, height):
    # scrolls a view whose pointer is near its top or bottom edge; True when it moved
    if y < GRIP_EDGE:
        step = -min(2 + (GRIP_EDGE - y) // 2, 24)
    elif y > height - GRIP_EDGE:
        step = min(2 + (y - height + GRIP_EDGE) // 2, 24)
    else:
        return False
    before = bar.value()
    bar.setValue(before + step)
    return bar.value() != before


class DragOverlay(QWidget):
    # the live preview of a heading drag. It sits over the viewport, opaque, and paints the rows
    # from grabs of the real view: the dragged group lifts out as a translucent image, the rows
    # close up where it was and open a gap of its height where it would land. The document is
    # not touched until the drop. Rows are in document coordinates; grabs of the view at
    # several scroll values (windows) are kept, so rows that slide in from off screen have pixels.
    TIME = 0.12
    SETTLE = 0.14
    LIFT = 0.82

    def __init__(self, ed, heads, head, spots, start):
        super().__init__(ed)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.ed, self.bar = ed, ed.verticalScrollBar()
        self.setGeometry(ed.viewport().geometry())
        self.bg = ed.viewport().palette().base().color()
        self.wins, self.k = {}, 1.0
        self.rows = ed.row_geoms()
        self.heads = heads
        doc = ed.document()
        ranges = [(h.blockNumber(), group_tail(doc, h).blockNumber()) for h in heads]
        self.own = [
            any(a <= r.block.blockNumber() <= b for a, b in ranges) for r in self.rows
        ]
        # runs of consecutive own rows: (first row, past the last row, height, height of runs above)
        self.spans, self.h = [], 0.0
        i = 0
        while i < len(self.rows):
            if not self.own[i]:
                i += 1
                continue
            j = i
            while j < len(self.rows) and self.own[j]:
                j += 1
            h = self.rows[j - 1].bottom - self.rows[i].top
            self.spans.append((i, j, h, self.h))
            self.h += h
            i = j
        self.top = self.rows[self.spans[0][0]].top
        grabbed = next(
            sp
            for sp in self.spans
            if self.rows[sp[0]].block.blockNumber()
            <= head.blockNumber()
            <= self.rows[sp[1] - 1].block.blockNumber()
        )
        # where the grabbed run sits in the lifted image, and where the image returns to
        self.home = self.rows[grabbed[0]].top - grabbed[3]
        self.spots = spots
        if len(self.spans) == 1:
            self.spots = [s for s in spots if abs(self._land(s[0]) - self.top) >= 1]
        self.spot = None
        self.off = start.y() + self.bar.value() - self.home
        self.mouse = start
        self.cur = self.src = self.dst = [0.0] * len(self.rows)
        self.gap = self.gap_src = self.gap_dst = self.top
        self.t0 = self.ts0 = time.monotonic()
        self.settling, self.rest = False, True
        self.seen = self.scroll = self.bar.value()
        self.lift_y = self.lift_from = self.lift_to = 0.0
        self.done = False
        ed.setCursorWidth(0)
        self._capture(self.bar.value())
        self._ensure()
        self._build_image()
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self._tick)
        self.timer.start(16)
        self.show()
        self.raise_()

    def _land(self, y):
        # where a gap at spot y opens once the groups have left their places
        return y - sum(
            h for i, j, h, _ in self.spans if self.rows[j - 1].bottom <= y + 0.5
        )

    def _offsets(self, spot):
        if spot is None:
            return [0.0] * len(self.rows)
        y, out = spot[0], []
        for i, r in enumerate(self.rows):
            if self.own[i]:
                out.append(0.0)
                continue
            o = -sum(h for _, j, h, _ in self.spans if j <= i)
            if r.top >= y - 0.5:
                o += self.h
            out.append(o)
        return out

    def targets(self):
        return {
            r.block.blockNumber(): o
            for i, (r, o) in enumerate(zip(self.rows, self.dst))
            if not self.own[i]
        }

    def image_rect(self):
        y = self.lift_y if self.settling else self.mouse.y() - self.off
        return QRectF(0, y, self.width(), self.image.height() / self.k)

    def _capture(self, v):
        bar, keep = self.bar, self.bar.value()
        # signals off: the view repaints at the new value without scrolling for real
        bar.blockSignals(True)
        try:
            bar.setValue(v)
            at = bar.value()
            pm = self.ed.viewport().grab()
            bar.setValue(keep)
        finally:
            bar.blockSignals(False)
        self.k = pm.width() / self.width()
        pm.setDevicePixelRatio(1.0)
        self.wins[at] = pm
        return at, pm

    def _find(self, top, bottom, make=False):
        # a row at the very top or bottom of the document reaches past it: that part is margin
        vh = self.height()
        top, bottom = max(top, 0), min(bottom, self.bar.maximum() + vh)
        for v, pm in self.wins.items():
            if v <= top and bottom <= v + vh:
                return v, pm
        if not make:
            return None
        stride = max(vh // 2, 1)
        return self._capture(min(int(top // stride) * stride, self.bar.maximum()))

    def _ensure(self):
        # grabs for every row that shows anywhere between where it is and where it is going
        vh, s = self.height(), self.bar.value()
        for i, r in enumerate(self.rows):
            if self.own[i]:
                continue
            lo, hi = sorted((self.src[i], self.dst[i]))
            if r.bottom + hi - s < 0 or r.top + lo - s > vh:
                continue
            self._find(round(r.top), round(r.bottom), True)

    def _build_image(self):
        w, k = self.width(), self.k
        hh = min(round(self.h), self.height())
        img = QPixmap(round(w * k), round(hh * k))
        img.fill(self.bg)
        p = QPainter(img)
        p.scale(k, k)
        for a, b, _, above in self.spans:
            for i in range(a, b):
                r = self.rows[i]
                y = round(above) + round(r.top) - round(self.rows[a].top)
                if y >= hh:
                    break
                self._find(round(r.top), round(r.bottom), True)
                self._blit(p, i, y)
        p.end()
        self.image = img

    def _blit(self, p, i, y):
        r = self.rows[i]
        t0, t1 = round(r.top), round(r.bottom)
        hit = self._find(t0, t1)
        if hit is None:
            return
        v, pm = hit
        k = self.k
        top, bottom = max(t0, 0), min(t1, self.bar.maximum() + self.height())
        p.drawPixmap(
            QRectF(0, y + top - t0, self.width(), bottom - top),
            pm,
            QRectF(0, (top - v) * k, pm.width(), (bottom - top) * k),
        )

    def _advance(self, now):
        e = min((now - self.t0) / self.TIME, 1.0)
        e = 1 - (1 - e) ** 3
        self.cur = [a + (b - a) * e for a, b in zip(self.src, self.dst)]
        self.gap = self.gap_src + (self.gap_dst - self.gap_src) * e
        if self.settling:
            f = min((now - self.ts0) / self.SETTLE, 1.0)
            f = 1 - (1 - f) ** 3
            self.lift_y = self.lift_from + (self.lift_to - self.lift_from) * f
            return e < 1 or f < 1
        return e < 1

    def _retarget(self, spot, land):
        now = time.monotonic()
        self._advance(now)
        self.src, self.dst = list(self.cur), self._offsets(spot)
        self.gap_src, self.gap_dst = self.gap, land
        self.t0 = now
        self.spot, self.rest = spot, False
        self._ensure()

    def move_to(self, pt):
        self.mouse = pt
        self._choose()
        self.update()

    def _choose(self):
        if self.settling:
            return
        ref = self.mouse.y() - self.off + self.bar.value()
        cands = [(None, self.home)] + [(s, self._land(s[0])) for s in self.spots]
        best = min(cands, key=lambda c: abs(c[1] - ref))
        now = next(c for c in cands if c[0] is self.spot)
        # a pointer hovering between two spots does not flip the gap back and forth
        if best[0] is not self.spot and abs(best[1] - ref) + 3 < abs(now[1] - ref):
            self._retarget(*best)

    def _tick(self):
        if not self.settling:
            _edge_scroll(self.bar, self.mouse.y(), self.height())
            if self.bar.value() != self.seen:
                self.seen = self.bar.value()
                self._choose()
                self._ensure()
                self.update()
            if self.rest:
                return
        if self._advance(time.monotonic()):
            self.update()
        elif self.settling:
            self.finish()
        else:
            self.rest = True
            self.update()

    def drop(self):
        # the chosen spot; from here the view stays where it is until the image has landed
        self.scroll = self.bar.value()
        return self.spot

    def settle(self):
        # the document already holds the move (or nothing moved): the image glides into the gap
        self.settling, self.rest = True, False
        self._advance(time.monotonic())
        self.lift_from = self.mouse.y() - self.off
        self.lift_to = self.home if self.spot is None else self._land(self.spot[0])
        self.lift_to -= self.scroll
        self.lift_y = self.lift_from
        self.ts0 = time.monotonic()
        self.update()

    def cancel(self):
        self.scroll = self.bar.value()
        if self.spot is not None:
            self._retarget(None, self.top)
        self.settle()

    def finish(self):
        if self.done:
            return
        self.done = True
        self.timer.stop()
        self.hide()
        self.ed.setCursorWidth(self.ed._cursor_width(self.ed.mode))
        if self.ed._drag is self:
            self.ed._drag = None
        self.deleteLater()

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), self.bg)
        s = self.scroll if self.settling else self.bar.value()
        vh = self.height()
        tint = QColor(self.palette().highlight().color())
        tint.setAlpha(28)
        if self.spot is None and self.rest:
            for a, b, h, _ in self.spans:
                p.fillRect(QRectF(0, self.rows[a].top - s, self.width(), h), tint)
        else:
            p.fillRect(QRectF(0, self.gap - s, self.width(), self.h), tint)
        for i, r in enumerate(self.rows):
            if self.own[i]:
                continue
            y = round(r.top) + round(self.cur[i]) - s
            if y > vh or y + (r.bottom - r.top) < 0:
                continue
            self._blit(p, i, y)
        rect = self.image_rect()
        shadow = QColor(0, 0, 0, 22)
        for grow in (3, 2, 1):
            p.fillRect(rect.adjusted(-grow, -grow + 2, grow, grow + 2), shadow)
        p.setOpacity(self.LIFT)
        p.drawPixmap(rect, self.image, QRectF(self.image.rect()))
        p.setOpacity(1.0)
        p.setPen(QColor(0, 0, 0, 70))
        p.drawRect(rect.adjusted(0, 0, -1, -1))


class PillDrag(QWidget):
    # the floating pill of a pill drag: it follows the pointer, and glides into its new place or
    # back home when the drag ends. The rows make room under it through the highlighter (Lift);
    # the document is not touched until the drop.
    EASE = 0.35  # the share of the way to its target a gap or room closes per frame
    GLIDE = 0.14  # s

    def __init__(self, ed, image, offset, start, radius):
        super().__init__(ed)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setGeometry(ed.viewport().geometry())
        self.ed, self.image, self.offset, self.mouse = ed, image, offset, start
        self.radius = radius
        self.pos = QPointF(start - offset)
        # the landing spot under the pointer; the open gaps (spot -> px); glide: (from, to(),
        # start time, then()) while it flies; cancel: going home
        self.spot, self.gaps, self.glide, self.cancel = None, {}, None, False
        # (pointer, scroll) the spot was last aimed at
        self.aimed = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(ed._pill_tick)
        self.timer.start(16)
        self.show()
        self.raise_()

    def finish(self):
        self.timer.stop()
        self.hide()
        self.deleteLater()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.pos, self.image.deviceIndependentSize())
        rad = self.radius
        p.setPen(Qt.PenStyle.NoPen)
        # a soft shadow below: the pill is lifted off the page
        for grow, alpha in ((5, 10), (3.5, 18), (2, 28)):
            p.setBrush(QColor(0, 0, 0, alpha))
            g = r.adjusted(-grow, -grow, grow, grow).translated(0, 3)
            p.drawRoundedRect(g, rad + grow, rad + grow)
        clip = QPainterPath()
        clip.addRoundedRect(r, rad, rad)
        p.setClipPath(clip)
        p.drawPixmap(r.topLeft(), self.image)


class _LineNumbers(QWidget):
    # the narrow left margin of a Note card: one number per paragraph
    def __init__(self, editor):
        super().__init__(editor)
        self.ed = editor
        self.hide()

    def paintEvent(self, e):
        ed = self.ed
        p = QPainter(self)
        p.fillRect(self.rect(), ed.palette().color(QPalette.ColorRole.Base))
        p.setFont(ed._font_z(BOLD8))
        p.setPen(_shown(ed.settings, "number_color"))
        for b in ed._visible_blocks():
            g = ed._geom(b)
            if g is not None:
                p.drawText(
                    QRectF(0, g.top, self.width() - 4 * ed._z, 2 * (g.cy - g.top)),
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    str(b.blockNumber() + 1),
                )
        p.end()


class Editor(QTextEdit):
    # a preview or scratch editor: its Ctrl+wheel zooms only itself, for as long as it lives
    local_zoom = False
    leave = Signal()
    notice = Signal(str)
    mode_changed = Signal(str)
    status_changed = Signal()
    done_changed = Signal(str)
    note_requested = Signal(str)
    link_opened = Signal(str, bool)
    sort_changed = Signal(str)
    sort_changing = Signal()
    # the document or its layout changed, for the timeline beside the editor
    laid_out = Signal()
    field_requested = Signal()
    today_requested = Signal()
    # Ctrl+wheel over a timeline's text: wider (True) or narrower days, the fonts untouched
    time_zoom = Signal(bool)
    move_requested = Signal(object)
    # a priority mark was set: the urgent banner follows it without waiting for a save
    marked = Signal()
    fold_toggled = Signal()

    def __init__(self, store, settings):
        super().__init__()
        self.image_wins = []
        self.store = store
        self.pid = ""
        # the page of a note document: plain rich text where = types itself and no line takes a note
        self.note = False
        # note document pid -> whether the store holds it, for the links of the shown list
        self._live = {}
        # whether the scroll range is stretched for the preview of the last todo
        self._extending = False
        self.verticalScrollBar().rangeChanged.connect(self._tail_room)
        # this list's Completed choice: "place", "section", or "" for the Settings default
        self.done = ""
        # "prio" while this list keeps its open items in priority order
        self.sort = ""
        self.lang = ""
        self.syntax = None
        # the block number whose marker slot the mouse is over
        self._mark_hover = -1
        # the block number whose status slot the mouse is over
        self._status_hover = -1
        # (start, end) of the path chip under the mouse, None when it is over none
        self._chip_hover = None
        # (start, end) of the tag chip under the mouse; tags the find bar hit
        self._tag_hover = None
        # (start, end) of the image chip under the mouse
        self._image_hover = None
        self.found_tags = frozenset()
        self.partial_heads = frozenset()
        # open headings the find filter left with nothing showing; revealed: the one just clicked
        self.empty_heads, self.revealed = frozenset(), None
        # path link href -> 0 missing, 1 folder, 2 file
        self._paths = {}
        # per document: image name -> logical width its scaled resource was made for
        self._fit = {}
        self._originals, self._shown_imgs = {}, {}
        # the (part, block number) whose gutter icon the mouse is over, and the preview it opened
        self._hover_at, self.img_pop, self._hover_args = None, None, None
        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.timeout.connect(lambda: self._img_show(*self._hover_args))
        self.verticalScrollBar().valueChanged.connect(self._img_hide)
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.setInterval(50)
        self._fit_timer.timeout.connect(self.fit_images)
        self._steps = (0, 0)
        # a links project is an aggregator: numbers and bubbles, no checkboxes, nothing to check
        self.links = False
        # a timeline project: items carry dates, a new one starts where the task above ends
        self.timeline = False
        # the urgent page: it gathers items of several lists, so it has no Completed section or sort of its own
        self.pinned = False
        self.settings = settings
        self.mode = "insert"
        self.vim = VimEngine(self)
        self._caret = self.cursorWidth()
        # the zoom factor as the layout applies it (its device has a whole dpi); every painted size reads it
        self._z = 1.0
        self._own = None
        self.viewport().setMouseTracking(True)
        self.numbers = _LineNumbers(self)
        self.verticalScrollBar().valueChanged.connect(self.numbers.update)
        self._look()
        self._settling = 0
        # the caret's place before the move being settled, and the arrow key doing it (+1, -1)
        self._at = self._dir = 0
        self._snapping = False
        self.cursorPositionChanged.connect(self._chip_snap)
        self.cursorPositionChanged.connect(self._keep_visible)
        self._tag_run = self._tag_mark = None
        self._tag_chars = self.document().characterCount()
        self._grip = self._drag = None
        self._pill = self._tdrag = None
        self._rename = self._tagging = None
        # item ids of the selected H1s, and the one a Ctrl+Shift+click or Shift+arrow runs from
        self._sel, self._anchor = set(), None
        self.cursorPositionChanged.connect(self._tag_caret)
        self.cursorPositionChanged.connect(self._rename_caret)
        self.cursorPositionChanged.connect(self._sync_caret)
        # () -> every tag name of the project; None turns the #tag list off
        self.tag_source = None
        self.tagpop = TagList(self)
        self.cursorPositionChanged.connect(lambda: self.tagpop.recheck())
        self.verticalScrollBar().valueChanged.connect(lambda _v: self.tagpop.hide())
        # Qt repaints only the caret; the current-line band needs the old and new rows redrawn
        self.cursorPositionChanged.connect(
            lambda: self.settings["line_hl"] and self.viewport().update()
        )
        self.keymap = {
            (CTRL, Qt.Key.Key_D): self.toggle_current,
            (CTRL, Qt.Key.Key_Minus): self.toggle_current,
            (CTRL, Qt.Key.Key_U): lambda: self.toggle_format("underline"),
            (CTRL, Qt.Key.Key_K): self.link_current,
            (ALT, Qt.Key.Key_1): self.toggle_p1,
            (ALT, Qt.Key.Key_2): lambda: self.mark_current("p2"),
            (ALT, Qt.Key.Key_3): lambda: self.mark_current("p3"),
            (ALT, Qt.Key.Key_4): lambda: self.mark_current("p4"),
            (ALT, Qt.Key.Key_0): lambda: self.mark_current(""),
            (CTRL, Qt.Key.Key_N): self.new_todo,
            (CTRL, Qt.Key.Key_Equal): self.note_edit,
            (CTRL | SHIFT, Qt.Key.Key_Minus): self.insert_divider,
            (CTRL, Qt.Key.Key_X): self.cut_item,
            (CTRL, Qt.Key.Key_C): self.copy_item,
            (CTRL, Qt.Key.Key_Up): lambda: self.move_step(-1),
            (CTRL, Qt.Key.Key_Down): lambda: self.move_step(1),
            (CTRL, Qt.Key.Key_B): lambda: self.toggle_format("bold"),
            (CTRL, Qt.Key.Key_I): lambda: self.toggle_format("italic"),
            (CTRL, Qt.Key.Key_0): lambda: self.set_heading(0),
            (CTRL, Qt.Key.Key_1): lambda: self.set_heading(1),
            (CTRL, Qt.Key.Key_2): lambda: self.set_heading(2),
            (CTRL, Qt.Key.Key_3): lambda: self.set_heading(3),
            (CTRL | SHIFT, Qt.Key.Key_Return): self.toggle_all_folds,
            (CTRL | SHIFT, Qt.Key.Key_Enter): self.toggle_all_folds,
            (CTRL | SHIFT, Qt.Key.Key_G): lambda: self.set_fg(FG_GREY),
            (CTRL | SHIFT, Qt.Key.Key_R): lambda: self.set_fg(FG_RED),
            (CTRL | SHIFT, Qt.Key.Key_L): lambda: self.set_list_style(None),
            (CTRL | SHIFT, Qt.Key.Key_N): lambda: self.set_list_style(
                QTextListFormat.Style.ListDecimal
            ),
            (Qt.KeyboardModifier.NoModifier, Qt.Key.Key_Tab): lambda: self.indent(1),
            (SHIFT, Qt.Key.Key_Backtab): lambda: self.indent(-1),
            (Qt.KeyboardModifier.NoModifier, Qt.Key.Key_Backtab): lambda: self.indent(
                -1
            ),
        }
        for key, col in HIGHLIGHTS.items():
            self.keymap[(CTRL, key)] = lambda col=col: self.toggle_highlight(col)
        _hub.changed.connect(self._on_zoom)
        self._apply_zoom()

    @property
    def zoom(self):
        return self._z

    def _font_z(self, base):
        f = QFont(base)
        f.setPointSizeF(base.pointSizeF() * self._z)
        return f

    def setDocument(self, doc):
        super().setDocument(doc)
        self._apply_zoom()

    def _apply_zoom(self, keep=False):
        # the layout scales fonts, block margins, indents and the document margin by the dpi of its
        # paint device, so a device of dpi * factor zooms the display and leaves the document alone
        doc = self.document()
        self._margin()
        base, dpi = _dpi(self._own)
        self._z = dpi / base
        if (doc.property("rdb_dpi") or base) != dpi:
            bar = self.verticalScrollBar()
            ratio = bar.value() / max(doc.size().height(), 1)
            doc.documentLayout().setPaintDevice(_device(dpi))
            doc.setProperty("rdb_dpi", dpi)
            doc.markContentsDirty(0, doc.characterCount())
            self.fit_images()
            if keep:
                bar.setValue(round(ratio * doc.size().height()))
            # the highlight sizes pasted pixel text and the tag chips by the layout's zoom
            if self.settings["tag_gutter"] or _has_px(doc):
                self._rehighlight()
        self._note_margin()
        self.viewport().update()

    def _gutter_slack(self):
        # the part of the gutter that is not whole indent steps
        if self.text_page:
            return 0
        return rdb_model.GUTTER - rdb_model.INDENT_W * rdb_model.GUTTER_STEPS

    def _margin(self):
        # the tag gutter is a display-only left margin of the root frame (the layout scales it with
        # the zoom); body_html strips it from the saved text. Qt records a frame format change as
        # an undo step with no opt-out, and undoing it would drop the offset under a drawn gutter,
        # so the history goes
        doc = self.document()
        root = doc.rootFrame()
        fmt = root.frameFormat()
        want = doc.documentMargin() + self._tag_w() + self._gutter_slack()
        if fmt.leftMargin() == want:
            return
        modified = doc.isModified()
        doc.blockSignals(True)
        try:
            fmt.setLeftMargin(want)
            root.setFrameFormat(fmt)
            doc.clearUndoRedoStacks()
            self._steps = (0, 0)
            doc.setModified(modified)
        finally:
            doc.blockSignals(False)

    def _zoom_to(self, z):
        if not self.local_zoom:
            set_zoom(z)
            return
        self._own = round(min(max(z, ZOOM[0]), ZOOM[1]), 2)
        self._on_zoom(self._own)

    def _on_zoom(self, _z):
        self._grip_abort()
        self._pill_stop()
        self._apply_zoom(True)
        self.setCursorWidth(self._cursor_width(self.mode))

    @property
    def store(self):
        return self._store

    @store.setter
    def store(self, store):
        # painting must not query the database: the visited set is read when a store is attached
        self._store = store
        self.close_image_windows()
        self._originals, self._shown_imgs, self._fit = {}, {}, {}
        self._visited = set() if store is None else set(store.get_visited_links())

    def visited_links(self):
        return self._visited

    def mark_visited(self, href):
        if not href or is_path_link(href) or href in self.visited_links():
            return
        # another editor on this store may have marked links since: its marks are kept
        self._visited = {*self.store.get_visited_links(), href}
        self.store.put_visited_links(sorted(self._visited))
        self._rehighlight()

    def _rehighlight(self):
        # nothing in the document changes: no modified flag, and no signal to restart autosave
        doc = self.document()
        modified = doc.isModified()
        doc.blockSignals(True)
        try:
            for h in doc.findChildren(ItemHighlighter):
                h.rehighlight()
            doc.setModified(modified)
        finally:
            doc.blockSignals(False)
        self.viewport().update()

    def _tag_caret(self):
        # a tag shows its '#' while the caret is in it: only entering or leaving a tag redraws
        # its line, and as a paint-only change that is not a content change
        doc = self.document()
        pos = self.textCursor().position()
        run = self._tag_run_at(doc.findBlock(pos), pos)
        old, self._tag_run = self._tag_run, run
        # a live cursor, so the old tag is still found after lines are added above it
        mark, self._tag_mark = self._tag_mark, None
        if run is not None:
            self._tag_mark = QTextCursor(doc)
            self._tag_mark.setPosition(run[0])
        if old == run and (mark is None or mark.position() == run[0]):
            return
        marks = [c for c in (mark, self._tag_mark) if c is not None]
        if doc.characterCount() == self._tag_chars:
            self._tag_redraw(marks)
        else:
            # an edit block is open: a rehighlight inside one swallows its contentsChange,
            # which leaves the lines it added unstyled
            QTimer.singleShot(0, self, lambda: self._tag_redraw(marks))

    def _tag_run_at(self, block, pos):
        # the tag whose '#' shows with the caret at pos
        lone = _lone(block, self.settings)
        for r in tag_runs(block):
            lo, hi = tag_reveal(block, r, lone)
            if lo <= pos <= hi:
                return r
        return None

    def _tag_item_edit(self, pos, *_):
        # a tag hides only while it is the item's only one: an edit in one block can change
        # what another shows
        if not self.settings["tag_gutter"]:
            return
        blocks = item_of(self.document().findBlock(pos))
        if len(blocks) > 1:
            marks = [QTextCursor(b) for b in blocks]
            QTimer.singleShot(0, self, lambda: self._tag_redraw(marks))

    def _count_chars(self, *_):
        self._tag_chars = self.document().characterCount()

    def _tag_redraw(self, marks):
        doc = self.document()
        modified = doc.isModified()
        doc.blockSignals(True)
        try:
            for c in marks:
                if c.document() == doc:
                    for h in doc.findChildren(ItemHighlighter):
                        h.rehighlightBlock(c.block())
            doc.setModified(modified)
        finally:
            doc.blockSignals(False)
        # the caret-only rehighlight fires no contentsChanged, and a revealed '#' can rewrap a line
        self._tail_room()
        self.viewport().update()

    def _look(self):
        f = rdb_model.list_font(self.settings, "body")
        if self.text_page:
            f = rdb_model.note_font(f)
        self.setFont(f)
        self.document().setDefaultFont(f)
        self.apply_wrap()
        self._note_margin()
        pal = self.palette()
        bg = _shown(self.settings, "editor_bg")
        pal.setColor(QPalette.ColorRole.Base, bg)
        pal.setColor(QPalette.ColorRole.Text, _shown(self.settings, "body_fg"))
        # windows11 paints a text view's viewport with Window instead of Base; Classic keeps
        # the app's Window, which shows around the viewport
        if not rdb_theme.win11():
            bg = QApplication.palette(self).color(QPalette.ColorRole.Window)
        pal.setColor(QPalette.ColorRole.Window, bg)
        self.setPalette(pal)

    def apply_wrap(self):
        wrap = not self.text_page or self.settings["notepad_wrap"]
        self.setLineWrapMode(
            QTextEdit.LineWrapMode.WidgetWidth
            if wrap
            else QTextEdit.LineWrapMode.NoWrap
        )

    def number_w(self):
        if not self.text_page:
            return 0
        digits = len(str(self.document().blockCount()))
        adv = QFontMetricsF(self._font_z(BOLD8)).horizontalAdvance("0")
        return math.ceil(digits * adv + 8 * self._z)

    def _note_margin(self):
        w = self.number_w()
        if w != self.viewport().x() - self.frameWidth():
            self.setViewportMargins(w, 0, 0, 0)
        self.numbers.setGeometry(
            self.frameWidth(), self.frameWidth(), w, self.viewport().height()
        )
        self.numbers.setVisible(w > 0)
        self.numbers.update()

    def set_document(self, doc):
        self._grip_abort()
        self._pill_stop()
        self._rename = self._tagging = None
        self._sel, self._anchor = set(), None
        self._tag_mark = None
        self.tagpop.start = self.tagpop.shut = self.tagpop.loaded = None
        for old in doc.findChildren(ItemHighlighter):
            old.setDocument(None)
            old.deleteLater()
        self.setDocument(doc)
        self._count_chars()
        doc.contentsChange.connect(
            self._count_chars, Qt.ConnectionType.UniqueConnection
        )
        doc.contentsChange.connect(
            self._tag_item_edit, Qt.ConnectionType.UniqueConnection
        )
        set_sorted(doc, self.sort == "prio")
        self._paths = {}
        self._fit = {}
        doc.contentsChange.connect(self._fit_check, Qt.ConnectionType.UniqueConnection)
        doc.contentsChange.connect(self._img_hide, Qt.ConnectionType.UniqueConnection)
        self.fit_images()
        # selections hold cursors of the previous document
        self.setExtraSelections([])
        args = (
            doc,
            self.settings,
            lambda: self.textCursor().position(),
            self.visited_links,
        )
        if self.text_page:
            # rdb_syntax subclasses ItemHighlighter
            from rdb_syntax import SyntaxHighlighter

            self.syntax = SyntaxHighlighter(*args, lang=self.lang)
            self.syntax.flush()
        else:
            self.syntax = None
            ItemHighlighter(*args)
        doc.contentsChanged.connect(self._sync_view, Qt.ConnectionType.UniqueConnection)
        self._live = {}
        self._sync_notes()
        self._tail_room()
        self._steps = (doc.availableUndoSteps(), doc.availableRedoSteps())
        self._look()
        self.vim.reset()
        self._set_mode(self.settings["vim_start"] if self.settings["vim"] else "insert")
        self.laid_out.emit()

    def refresh(self):
        self._look()
        self._margin()
        for b in iter_blocks(self.document()):
            if is_heading(b) or is_section(b):
                layout_block(b)
        self._set_mode(self.mode if self.settings["vim"] else "insert")
        for h in self.document().findChildren(ItemHighlighter):
            if h is not self.syntax:
                h.rehighlight()
        if self.syntax is not None:
            self.syntax.flush()
        self.fit_images()
        self.viewport().update()

    def _h1_live(self):
        return self._rename is None and not self.isReadOnly()

    def _h1_selected(self):
        return self._h1_live() and heading_level(self.textCursor().block()) == 1

    def _h1_picked(self, block):
        if not self._h1_live() or heading_level(block) != 1:
            return False
        # a selection replaces the keyboard's: the caret's heading counts only without one
        if self._sel:
            return item_id(block) in self._sel
        return block == self.textCursor().block()

    def _sel_heads(self):
        # the selected H1s in document order; the one under the caret counts
        if not self._h1_live():
            return []
        return [
            b
            for b in iter_blocks(self.document())
            if heading_level(b) == 1 and self._h1_picked(b)
        ]

    def _sel_set(self, ids, anchor=None):
        self._sel = set(ids)
        if anchor is not None or not ids:
            self._anchor = anchor
        self._sync_caret()

    def _clear_sel(self):
        if self._sel or self._anchor is not None:
            self._sel_set(())

    def _sel_click(self, block, extend):
        doc = self.document()
        if not item_id(block):
            ensure_ids(doc)
        mine = item_id(block)
        ids = {item_id(b) for b in self._sel_heads()}
        if not extend:
            self._sel_set(ids ^ {mine}, mine)
            return
        anchor = (
            self._anchor if self._anchor in ids else item_id(self.textCursor().block())
        )
        if anchor not in ids:
            anchor = mine
        a = next(b for b in iter_blocks(doc) if item_id(b) == anchor)
        lo, hi = sorted((a.blockNumber(), block.blockNumber()))
        ids |= {
            item_id(b)
            for b in iter_blocks(doc)
            if heading_level(b) == 1 and lo <= b.blockNumber() <= hi and b.isVisible()
        }
        self._sel_set(ids, anchor)

    def _sel_shift(self, step):
        # Shift+Up/Down on a folded H1: the run of selected headings grows or shrinks
        block = self.textCursor().block()
        heads = self._sel_heads()
        ids = {item_id(b) for b in heads}
        if self._anchor not in ids:
            self._anchor = item_id(block)
        toward = [
            b for b in heads if (b.blockNumber() - block.blockNumber()) * step > 0
        ]
        if self._anchor != item_id(block) and any(
            item_id(b) == self._anchor for b in toward
        ):
            nxt = min(toward, key=lambda b: abs(b.blockNumber() - block.blockNumber()))
            ids.discard(item_id(block))
        else:
            nxt = block.next() if step > 0 else block.previous()
            while nxt.isValid() and not nxt.isVisible():
                nxt = nxt.next() if step > 0 else nxt.previous()
            if not (nxt.isValid() and heading_level(nxt) == 1 and is_folded(nxt)):
                return
            ids.add(item_id(nxt))
        self._sel_set(ids, self._anchor)
        self.setTextCursor(QTextCursor(nxt))

    def _sel_step(self, step):
        doc, c = self.document(), self.textCursor()
        heads = self._sel_heads()
        own = next(
            (
                h
                for h in heads
                if h.blockNumber()
                <= c.blockNumber()
                <= group_tail(doc, h).blockNumber()
            ),
            heads[0],
        )
        col = c.positionInBlock() if c.block() == own else 0
        mine = item_id(own)
        with self._settled():
            if step_groups(doc, heads, step) is None:
                return
            apply_view(doc, self.check_mode)
            at = next(b for b in iter_blocks(doc) if item_id(b) == mine)
            c = QTextCursor(at)
            c.setPosition(at.position() + min(col, at.length() - 1))
            self.setTextCursor(c)
        self.laid_out.emit()

    def _sel_key(self, e):
        # True when the key is used up by the selection of H1s
        key, mods = e.key(), e.modifiers() & (CTRL | SHIFT | ALT)
        if not self._h1_live():
            return False
        block = self.textCursor().block()
        heads = self._sel_heads() or ([block] if heading_level(block) == 2 else [])
        if (
            not mods
            and key in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and heads
            and not self.textCursor().hasSelection()
            and self.vim.prompt is None
        ):
            # Ctrl+Enter's toggle: the caret is hidden here, so Enter must not split a heading
            if heads == [block] and group_tail(self.document(), block) == block:
                # nothing to open or close: Enter starts the heading's first line
                if self.mode not in VIM_MODES:
                    c = self.textCursor()
                    c.movePosition(MO.EndOfBlock)
                    self.setTextCursor(c)
                return False
            if len(heads) == 1:
                self.set_fold(heads[0])
            else:
                shut = not any(is_folded(h) for h in heads)
                for h in heads:
                    self.set_fold(h, shut)
            return True
        if (
            mods == SHIFT
            and key in (Qt.Key.Key_Up, Qt.Key.Key_Down)
            and not self.textCursor().hasSelection()
            and heading_level(block) == 1
            and is_folded(block)
        ):
            self._sel_shift(-1 if key == Qt.Key.Key_Up else 1)
            return True
        if mods == CTRL and key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            return False
        if self._sel and (
            key == Qt.Key.Key_Escape
            or key
            in (
                Qt.Key.Key_Return,
                Qt.Key.Key_Enter,
                Qt.Key.Key_Backspace,
                Qt.Key.Key_Delete,
            )
            or (key in RENAME_ENDS and not mods)
            or _edits_text(e)
        ):
            self._clear_sel()
            return key == Qt.Key.Key_Escape
        return False

    def _sync_caret(self):
        self.setCursorWidth(self._cursor_width(self.mode))
        self.viewport().update()

    def _cursor_width(self, mode):
        if self._h1_selected() or (self._sel and self._h1_live()):
            return 0
        if mode not in VIM_MODES:
            return self._caret
        avg = QFontMetrics(self.document().defaultFont()).averageCharWidth()
        return round(avg * self._z)

    def _set_mode(self, mode):
        self.vim.cancel()
        self.setOverwriteMode(mode == "replace")
        self.setCursorWidth(self._cursor_width(mode))
        if mode != self.mode:
            self.mode = mode
            self.mode_changed.emit(mode)

    @property
    def check_mode(self):
        return done_mode(self.done, self.settings)

    def _keep_caret(self, fn):
        # fn moves items; the caret stays on its item, line and column, the scroll where it was
        doc, cur = self.document(), self.textCursor()
        block = cur.block()
        head = owner_todo(block) or block
        offset, col = block.blockNumber() - head.blockNumber(), cur.positionInBlock()
        bar = self.verticalScrollBar()
        scroll = bar.value()
        edit = QTextCursor(doc)
        edit.beginEditBlock()
        try:
            # the caret's item needs an id to be found again after the move
            ensure_ids(doc)
            uid = get_meta(head).get("i")
            fn()
        finally:
            edit.endEditBlock()
        if uid is not None:
            b = doc.findBlockByNumber(unit_block(doc, uid).blockNumber() + offset)
            c = QTextCursor(b)
            c.setPosition(b.position() + min(col, b.length() - 1))
            self.setTextCursor(c)
        # moving the caret scrolls it into view, and the item may lie far from where it was
        bar.setValue(scroll)
        self.viewport().update()

    def _home(self, first, last, above=False):
        # an open item made under Completed goes to its place in the open area, following the
        # item it was made next to (the one below it when it was made above); blocks first..last
        doc = self.document()
        if is_sorted(doc):
            home_new(doc)
        sec = section_block(doc)
        last = min(last, doc.blockCount() - 1)
        if (sec is None or last < sec.blockNumber()) and self.check_mode == "move":
            # a checked item made in the open area belongs under Completed, as if checked there
            if any(
                is_checked(doc.findBlockByNumber(n)) for n in range(first, last + 1)
            ):
                self._keep_caret(lambda: normalize(doc, "move"))
            return
        if sec is None:
            return
        strays = [
            b
            for n in range(
                max(first, sec.blockNumber() + 1), min(last, doc.blockCount() - 1) + 1
            )
            if is_todo(b := doc.findBlockByNumber(n)) and not is_checked(b)
        ]
        if not strays:
            # a pasted item under Completed has no home yet; the section keeps its groups
            self._keep_caret(lambda: group_completed(doc))
            return
        if above:
            below = doc.findBlockByNumber(last + 1)
            after = item_of(below)[0] if below.isValid() else None
        else:
            after = unit_above(strays[0])
        self._keep_caret(lambda: send_home(doc, strays, after))

    def set_done(self, done):
        prev, self.done = self.done, done
        try:
            self._keep_caret(lambda: normalize(self.document(), self.check_mode))
        except Exception:
            self.done = prev
            apply_view(self.document(), self.check_mode)
            raise
        self.done_changed.emit(done)

    def _sync_notes(self):
        # a link seen for the first time (a paste, an undo) is checked against the store: a
        # document in the trash comes back, and an open preview reads its lines
        seen = {}
        for b in iter_blocks(self.document()):
            if nt := note_link(b):
                seen[nt] = seen.get(nt, False) or note_open(b)
        for nt in self._live.keys() - seen.keys():
            del self._live[nt]
        fresh = []
        for nt, opened in seen.items():
            if nt in self._live:
                continue
            fresh.append(nt)
            if not self.store.has_project(nt) and self.store.in_trash(nt):
                self.store.restore_project(nt)
            self._live[nt] = self.store.has_project(nt)
            if opened and self._live[nt]:
                load_preview(self.store, nt)
        return fresh

    def _own_notes(self, fresh):
        # an undo can bring back an item whose note another list has taken since (Move To): that
        # list's item gets a copy, so no note document is held by two items. The shown list is
        # left alone: an edit here would break the redo. A pasteboard has no undo, and its
        # source still holds the note it is taking. The urgent page shows copies, so the card that
        # holds the note is the row's own source, not another list
        if self.pid == URGENT_PID or not self.document().isUndoRedoEnabled():
            return
        for nt in fresh:
            if not self._live[nt]:
                continue
            for pid, row in self.store.note_holders(nt):
                if pid == self.pid:
                    continue
                data = self.store.get_project(pid)
                data["meta"][row]["nt"] = duplicate_note(self.store, nt)
                self.store.put_project(
                    pid, data["html"], data["meta"], data["collapsed"]
                )

    def _tail_room(self, *_):
        # the layout leaves the last block's bottom margin out of the document height, so the
        # preview panel of a last todo gets that room as extra scroll range
        if self._extending:
            return
        last = self.document().lastBlock()
        room = last.blockFormat().bottomMargin() if self.preview_rows(last) else 0
        size = int(self.document().size().height())
        self._extending = True
        self.verticalScrollBar().setMaximum(
            max(0, size - self.viewport().height()) + room
        )
        self._extending = False

    def has_note(self, block):
        nt = note_link(block)
        return is_todo(block) and bool(nt) and self._live.get(nt, False)

    def _sync_view(self):
        doc = self.document()
        self._note_margin()
        if doc.property("rdb_restyle"):
            doc.setProperty("rdb_restyle", False)
            # after this signal has reached the find bar, whose re-hide must not be undone
            QTimer.singleShot(0, self, self._rehighlight)
        # gutter numbers depend on every heading above a row: an edit renumbers rows far from the
        # lines Qt repaints, so the whole gutter strip is repainted
        self.viewport().update(
            0,
            0,
            int(self._text_left()) + int(self._origin().x()),
            self.viewport().height(),
        )
        fresh = self._sync_notes()
        self._tail_room()
        apply_view(doc, self.check_mode)
        steps = doc.availableUndoSteps(), doc.availableRedoSteps()
        was, self._steps = self._steps, steps
        # an undo or a redo may have crossed a toggle of the list: the layout says which side it is on.
        # A redo moves as many steps (give or take one) from the redo to the undo stack as it replays;
        # an edit that clears a pending redo adds its own few steps instead
        crossed = steps[0] < was[0] or (
            steps[1] < was[1] and abs(steps[0] - was[0] + steps[1] - was[1]) <= 1
        )
        sectioned = section_block(doc) is not None
        if (
            crossed
            and sectioned != (self.check_mode == "move")
            and (sectioned or any(is_checked(b) for b in iter_blocks(doc)))
        ):
            self.done = "section" if sectioned else "place"
            apply_view(doc, self.check_mode)
            self.done_changed.emit(self.done)
        # an undo or a redo across the sort toggle: the home numbers say which side it is on
        if crossed:
            homed = has_home(doc)
            if homed != (self.sort == "prio") and (
                homed or any(is_todo(b) for b in iter_blocks(doc))
            ):
                self.sort = "prio" if homed else ""
                set_sorted(doc, homed)
                self.sort_changed.emit(self.sort)
        # the Completed captions follow the headings; the change joins the edit that caused it
        stale = caption_diff(doc)
        if stale:
            c = QTextCursor(doc)
            c.joinPreviousEditBlock()
            try:
                apply_captions(stale)
            finally:
                c.endEditBlock()
        self._keep_visible()
        self._own_notes(fresh)
        self.laid_out.emit()

    def _in_chip(self, pos):
        r = chip_span(self.document(), pos)
        return r if r and r[0] < pos else None

    def _chip_snap(self):
        # a path chip is one object: neither end of the selection rests inside one
        if self._snapping:
            return
        c = self.textCursor()
        a, p = c.anchor(), c.position()
        old, self._at = self._at, p
        ia, ip = self._in_chip(a), self._in_chip(p)
        if not (ia or ip):
            return
        if a == p:
            s, e = ip
            if self._dir > 0:
                p = s if old < s else e
            elif self._dir < 0:
                p = s
            else:
                p = s if p - s < e - p else e
            a = p
        else:
            if ia:
                a = ia[0] if a < p else ia[1]
            if ip:
                s, e = ip
                if self._dir > 0:
                    p = s if old < s else e
                else:
                    p = s if self._dir < 0 or p < a else e
        n = QTextCursor(self.document())
        n.setPosition(a)
        n.setPosition(p, QTextCursor.MoveMode.KeepAnchor)
        self._snapping = True
        try:
            self.setTextCursor(n)
        finally:
            self._snapping = False
        self._at = p

    def _keep_visible(self):
        # while a key or an undo runs the caret passes through lines on its way; it settles at the end
        if self._settling:
            return
        block = self.textCursor().block()
        if not block.isVisible() and fold_owner(block):
            # a caret that lands in a folded group (a paste, a move, a jump) opens the folds above it
            unfold_to(self.document(), block)
            apply_view(self.document(), self.check_mode)
        if not block.isVisible():
            self._leave(block)

    @contextmanager
    def _settled(self, history=False):
        self._settling += 1
        try:
            yield
        finally:
            self._settling -= 1
            # history restores text, not the view: a caret it leaves in a fold goes to the heading
            if history and (head := fold_owner(self.textCursor().block())):
                self.setTextCursor(QTextCursor(head))
            self._keep_visible()

    def undo(self):
        with self._settled(True):
            super().undo()
        self._sync_caret()

    def redo(self):
        with self._settled(True):
            super().redo()
        self._sync_caret()

    def _origin(self):
        return QPointF(
            -self.horizontalScrollBar().value(), -self.verticalScrollBar().value()
        )

    def _geom(self, block):
        lay = block.layout()
        if not block.isVisible() or lay.lineCount() == 0:
            return None
        pos = lay.position() + self._origin()
        lines = [lay.lineAt(i) for i in range(lay.lineCount())]
        first, last = lines[0], lines[-1]
        return Geom(
            pos.y() + first.y(),
            pos.y() + last.y() + last.height(),
            pos.y() + first.y() + first.height() / 2,
            pos.x() + max(ln.x() + ln.naturalTextWidth() for ln in lines),
        )

    def _item_cols(self, g, ox=None, z=None):
        return _columns(
            g,
            self._origin().x() if ox is None else ox,
            self._z if z is None else z,
            self.settings["gutter_items"],
        )

    @property
    def text_page(self):
        return is_text_doc(self.document())

    def set_lang(self, lang):
        self.lang = lang
        self.syntax.lang = lang
        self.syntax.flush()

    def _tag_w(self):
        s = self.settings
        return s["tag_gutter_w"] if s["tag_gutter"] and not self.text_page else 0

    def _text_left(self):
        # where the text starts, from the origin
        if self.text_page:
            return 0
        return (rdb_model.GUTTER + self._tag_w()) * self._z

    def _sep_x(self):
        # the last gutter's separator, from the origin
        return self._text_left() - 3 * self._z

    def _head_cols(self, g, ox=None, z=None):
        return _head_columns(
            g,
            self._origin().x() if ox is None else ox,
            self._z if z is None else z,
            self.settings["gutter_heads"],
        )

    def marker_rect(self, block):
        g = self._geom(block)
        return None if g is None else self._item_cols(g).get("mark")

    def status_rect(self, block):
        g = self._geom(block)
        return None if g is None else self._item_cols(g).get("status")

    def fold_rect(self, block):
        g = self._geom(block)
        if g is None or heading_level(block) not in FOLDABLE or self.text_page:
            return None
        return self._head_cols(g).get("fold")

    def row_geoms(self):
        # ponytail: every block on each call; cache per layout if lists pass a few thousand rows
        self.document().size()
        z, scroll = self._z, self.verticalScrollBar().value()
        out = []
        for b in iter_blocks(self.document()):
            g = self._geom(b)
            if g is None:
                continue
            f = b.blockFormat()
            out.append(
                Row(
                    b,
                    g.top + scroll - f.topMargin() * z,
                    g.bottom + scroll + f.bottomMargin() * z,
                    g.cy + scroll,
                )
            )
        # the bands meet halfway: line leading and collapsed margins leave gaps or overlaps
        for i in range(len(out) - 1):
            edge = (out[i].bottom + out[i + 1].top) / 2
            out[i] = out[i]._replace(bottom=edge)
            out[i + 1] = out[i + 1]._replace(top=edge)
        return out

    def gutter_rects(self, block):
        g = self._geom(block)
        if g is None:
            return None
        cols = self._item_cols(g)
        return cols.get("check"), cols.get("bubble")

    def _block_at(self, y):
        # not cursorForPosition: Qt's hit test assumes block rects rise in order, but a hidden row
        # keeps the rect of its last layout (the document origin if never laid out, the old size
        # after a zoom), so it lands on wrong rows
        b = self.document().begin()
        while b.isValid():
            g = self._geom(b)
            if g is not None and g.bottom > y:
                return b
            b = b.next()
        return self.document().lastBlock()

    def _rows(self):
        first = self._block_at(0)
        lo = (owner_todo(first) or first).blockNumber()
        # the top line may lie in the margin under a todo, where its preview panel still shows
        above = owner_todo(first.previous())
        if above is not None and preview_rows(above) is not None:
            lo = above.blockNumber()
        hi = self._block_at(self.viewport().height()).blockNumber()
        rows = []
        for b, n in numbered(self.document(), self.timeline):
            bn = b.blockNumber()
            if bn > hi:
                break
            if bn >= lo:
                rows.append((b, n))
        return rows

    def paintEvent(self, e):
        rows = self._rows()
        ox = self._origin().x()
        z = self._z
        p = QPainter(self.viewport())
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        if self.settings["line_hl"]:
            g = self._geom(self.textCursor().block())
            if g is not None:
                p.fillRect(
                    QRectF(0, g.top, self.viewport().width(), g.bottom - g.top),
                    _shown(self.settings, "line_hl_color"),
                )
        for b, _ in rows:
            if is_heading(b):
                g = self._geom(b)
                if g is not None:
                    self._paint_heading(p, b, g, ox)
                continue
            bg = get_meta(b)["bg"] if is_todo(b) else ""
            g = self._geom(b) if bg else None
            if g is not None:
                left = self._sep_x() + ox
                # the fill ends with the text; a blank line has none, so it spans the row
                right = g.right + 3 * z if b.text().strip() else self.viewport().width()
                p.setBrush(_shade(bg))
                p.drawRoundedRect(
                    QRectF(left, g.top, right - left, g.bottom - g.top),
                    3 * z,
                    3 * z,
                )
            if is_todo(b):
                self._paint_note_panel(p, b)
        for b in self._visible_blocks():
            self._paint_bubbles(p, b)
            if is_divider(b) and (g := self._geom(b)) is not None:
                self._paint_divider(p, b, g, ox)
        p.end()
        super().paintEvent(e)
        p = QPainter(self.viewport())
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        gap = None
        for b, n in rows:
            g = self._geom(b)
            if g is None:
                continue
            if is_section(b):
                self._paint_section(p, b, g, ox)
                gap = g
            elif self.text_page:
                continue
            elif is_heading(b):
                self._paint_fold(p, b, g, ox)
            else:
                self._paint_todo(p, b, n, g, ox)
        if self.settings["gutter_sep"] and not self.text_page:
            # the Completed label runs across the gutter edge: the line skips its row
            h, sep = self.viewport().height(), _shown(self.settings, "gutter_sep_color")
            spans = [(0, h)] if gap is None else [(0, gap.top), (gap.bottom, h)]
            xs = [sep_x() * z]
            if self._tag_w():
                xs.append(self._sep_x())
            for x in xs:
                for y0, y1 in spans:
                    p.fillRect(QRectF(x + ox, y0, 1, y1 - y0), sep)
        p.end()

    def preview_rows(self, todo):
        return preview_rows(todo) if self.has_note(todo) else None

    def _panel_rect(self, todo):
        rows, g = self.preview_rows(todo), self._geom(todo)
        if rows is None or g is None:
            return None
        z = self._z
        left = self._sep_x() + self._origin().x()
        top = round(g.bottom) + NOTE_PAD * z
        # the half pixel puts the 1 px border on whole pixels
        return QRectF(
            left + 0.5,
            top + 0.5,
            self.viewport().width() - 4 * z - left - 1,
            panel_height(len(rows)) * z - 1,
        )

    def _paint_note_panel(self, p, todo):
        r = self._panel_rect(todo)
        if r is None:
            return
        s, z = self.settings, self._z
        p.setPen(QPen(_shown(s, "note_border"), 1))
        p.setBrush(_shown(s, "note_bg"))
        p.drawRoundedRect(r, 3 * z, 3 * z)
        rows = self.preview_rows(todo)
        font = self._font_z(preview_font())
        fm, pitch = QFontMetrics(font), note_pitch() * z
        p.setFont(font)
        width = int(r.width() - 8 * z)
        more = PREVIEWS[note_link(todo)][1]
        for i, line in enumerate(rows):
            box = QRectF(
                r.left() + 4 * z, r.top() + NOTE_PAD * z + i * pitch, width, pitch
            )
            if more and i == len(rows) - 1:
                p.setPen(_shade("#7a7a7a"))
                p.drawText(
                    box,
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    line,
                )
            else:
                p.setPen(_shade(INK))
                text = fm.elidedText(line, Qt.TextElideMode.ElideRight, width)
                p.drawText(
                    box,
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    text,
                )
        p.setPen(Qt.PenStyle.NoPen)

    def _paint_note_icon(self, p, rect, block):
        s = self.settings
        fill, edge = _shown(s, "note_bg"), _shown(s, "note_border")
        if note_open(block):
            fill, edge = edge, edge.darker(160)
        paint_note_icon(p, rect, fill, edge, self._z)

    def _heading_rect(self, g, ox):
        # the one place that decides how far a heading's box and rule reach sideways
        top, bottom = round(g.top), round(g.bottom)
        z = self._z
        return QRectF(4 * z + ox, top, self.viewport().width() - 8 * z, bottom - top)

    def _paint_divider(self, p, block, g, ox):
        s, z = self.settings, self._z
        r = self._heading_rect(g, ox)
        cur = self.textCursor()
        picked = cur.block() == block or (
            cur.hasSelection()
            and cur.selectionStart() <= block.position() < cur.selectionEnd()
        )
        h = max(1, round(s["divider_px"] * z))
        p.fillRect(
            QRectF(r.left(), round(g.cy - h / 2), r.width(), h),
            self.palette().highlight().color()
            if picked
            else _shown(s, "divider_color"),
        )

    def _paint_heading(self, p, block, g, ox):
        s, z = self.settings, self._z
        r = self._heading_rect(g, ox)
        lvl = heading_level(block)
        if lvl == 1:
            # the first block of a document gets no top margin, only the document margin
            top = H1_PAD * z
            if not block.previous().isValid():
                top = min(top, self.document().documentMargin() * z)
            bg, border = self.h1_colours(block)
            p.setPen(QPen(border, 1))
            p.setBrush(bg)
            box = r.adjusted(0.5, -top + 0.5, -0.5, H1_PAD * z - 0.5)
            p.drawRect(box)
            if self._h1_picked(block):
                inner = box.adjusted(0.5, 0.5, -0.5, -0.5)
                inner.setRight(self._sep_x() + ox)
                p.fillRect(inner, _shown(s, "h1_sel"))
        elif lvl == 2:
            # a timeline's rule runs to the edge, on into the chart beside it
            right = self.viewport().width() if self.timeline else r.right()
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(_shown(s, "h2_rule"))
            p.drawRect(
                QRectF(r.left(), r.bottom() + z, right - r.left(), s["h2_rule_px"] * z)
            )
        p.setPen(Qt.PenStyle.NoPen)

    def h1_colours(self, block):
        fill, line = h1_colour(block), h1_colour(block, "ho")
        if line:
            border = _shade(line)
        elif fill:
            border = _shade(fill).darker(150)
        else:
            border = _shown(self.settings, "h1_border")
        return (_shade(fill) if fill else _shown(self.settings, "h1_bg")), border

    def h1_swatch_rect(self, block):
        g = self._geom(block)
        if (
            g is None
            or heading_level(block) != 1
            or self.isReadOnly()
            or self.text_page
        ):
            return None
        return self._head_cols(g).get("swatch")

    def open_count(self, head):
        tail, n, b = group_tail(self.document(), head).blockNumber(), 0, head.next()
        while b.isValid() and b.blockNumber() <= tail:
            n += is_todo(b) and not is_checked(b)
            b = b.next()
        return n

    def set_h1_colour(self, block, colour, key="hc"):
        self.set_h1_colours([block], **{key: colour})

    def set_h1_colours(self, blocks, **keys):
        c = QTextCursor(self.document())
        c.beginEditBlock()
        for block in blocks:
            set_meta(block, **keys)
        c.endEditBlock()
        doc = self.document()
        doc.setModified(True)
        # the text colour is a display overlay; as a paint-only change it is not a content change
        doc.blockSignals(True)
        try:
            for h in doc.findChildren(ItemHighlighter):
                for block in blocks:
                    h.rehighlightBlock(block)
        finally:
            doc.blockSignals(False)
        self.viewport().update()

    def reset_h1_colours(self):
        heads = [b for b in iter_blocks(self.document()) if heading_level(b) == 1]
        self.set_h1_colours(heads, hc="", ho="", hf="")

    def h1_colour_popup(self, block):
        pop = H1ColourPopup(self, self.settings)
        keys = {"bg": "hc", "line": "ho", "fg": "hf"}
        pop.picked.connect(lambda k, c: self.set_h1_colour(block, c, keys[k]))
        pop.reset.connect(self.reset_h1_colours)
        pop_at(pop, self, self.h1_swatch_rect(block))

    def _paint_fold(self, p, block, g, ox):
        if heading_level(block) not in FOLDABLE:
            return
        if (sw := self.h1_swatch_rect(block)) is not None:
            bg, border = self.h1_colours(block)
            paint_disc(p, sw, border, bg, self._z)
        z, cols = self._z, self._head_cols(g, ox)
        if "count" in cols:
            font = QFont(self.font())
            font.setPixelSize(max(8, round(11 * z)))
            p.save()
            p.setFont(font)
            p.setPen(_shade(GRIP_INK))
            p.drawText(
                cols["count"],
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                str(self.open_count(block)),
            )
            p.restore()
        if "fold" in cols:
            _paint_fold_box(
                p,
                cols["fold"],
                _shade(GRIP_INK),
                is_folded(block) or block.blockNumber() in self.empty_heads,
                block.blockNumber() in self.partial_heads,
            )

    def _paint_section(self, p, block, g, ox):
        z = self._z
        label = f"Completed ({counts(self.document())['done']})"
        # nothing above the first block to partition from, and Qt gives it no top margin
        if block.previous().isValid():
            rule = round(self.settings["sec_rule_px"] * z)
            gap = round(SEC_GAP * z)
            p.fillRect(
                QRect(0, round(g.top) - gap - rule, self.viewport().width(), rule),
                _shown(self.settings, "sec_rule_color"),
            )
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_shade("#555555"))
        cy = g.cy
        if is_collapsed(self.document()):
            tri = [(10, cy - 4), (10, cy + 4), (16, cy)]
        else:
            tri = [(8, cy - 3), (16, cy - 3), (12, cy + 3)]
        p.drawPolygon(
            QPolygonF([QPointF(x * z + ox, cy + (y - cy) * z) for x, y in tri])
        )
        font = self._font_z(BOLD8)
        p.setFont(font)
        p.setPen(_shade("#555555"))
        width = QFontMetrics(font).horizontalAdvance(label)
        p.drawText(
            QRectF(24 * z + ox, cy - 9 * z, width + 4 * z, 18 * z),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            label,
        )
        p.setPen(QPen(_shade("#d0d0d0"), 1))
        p.drawLine(
            QPointF(24 * z + ox + width + 10 * z, cy),
            QPointF(self.viewport().width() - 8 * z, cy),
        )

    def _paint_check(self, p, block, check):
        paint_check(p, check, is_checked(block), _shown(self.settings, "check_color"))

    def _paint_todo(self, p, block, n, g, ox):
        m, z = get_meta(block), self._z
        cols = self._item_cols(g, ox, z)
        urgent = _shown(self.settings, "urgent_color")
        if cap := block.blockFormat().stringProperty(CAPTION):
            self._paint_caption(p, self.caption_rect(block), cap)
        if m["u"]:
            blocks = [b for b in item_blocks(block) if b.isVisible()]
            last = self._geom(blocks[-1])
            p.fillRect(QRectF(ox, g.top, 3 * z, last.bottom - g.top), urgent)
        # a blank todo has no checkbox or star to see or click
        filled = bool(block.text().strip())
        if "check" in cols and not self.links and filled:
            self._paint_check(p, block, cols["check"])
        if "number" in cols:
            p.setFont(self._font_z(BOLD8))
            p.setPen(_shown(self.settings, "number_color"))
            p.drawText(cols["number"], Qt.AlignmentFlag.AlignCenter, str(n))
        if "bubble" in cols:
            paint_disc(
                p,
                cols["bubble"],
                _shade(m["fg"] or "#aaaaaa"),
                _shade(m["bg"] or "#ffffff"),
                z,
            )
        if "note" in cols and self.has_note(block):
            self._paint_note_icon(p, cols["note"], block)
        if "mark" in cols and filled:
            # the empty slot always shows its placeholder, so the priority field can be seen
            hot = self._mark_hover == block.blockNumber()
            paint_marker(p, cols["mark"], mark_of(m), z, faint=True, hot=hot)
        if "status" in cols and filled:
            st = m.get("st", "")
            rec = rdb_model.STATUS_BY_KEY.get(st)
            hot = self._status_hover == block.blockNumber()
            rdb_status.paint_status(
                p,
                cols["status"],
                rec["icon"] if rec else "",
                rec["color"] if rec else "",
                z,
                faint=hot,
                hot=hot,
                missing=bool(st) and rec is None,
            )
        chip = self._gutter_tag(block, g, ox)
        if chip is not None and not self._unseen(*chip[0]):
            span, r, text = chip
            _paint_chip(p, r, self.settings, False, z, self._tag_hot(span), True)
            font, pad = _tag_font(self.settings, z), self.settings["tag_pad"] * z
            p.setFont(font)
            p.setPen(_shown(self.settings, "tag_fg"))
            p.drawText(
                r,
                Qt.AlignmentFlag.AlignCenter,
                QFontMetricsF(font).elidedText(
                    text, Qt.TextElideMode.ElideRight, r.width() - 2 * pad
                ),
            )

    def _tag_hot(self, span):
        return span == self._tag_hover or span in self.found_tags

    def _gutter_tag(self, block, g, ox=None):
        # the todo's only tag as a chip in the tag gutter on its first line: (span, rect, name)
        chip = None if self.text_page else gutter_tag(block, self.settings, self._z)
        if chip is None:
            return None
        run, name, width = chip
        z = self._z
        left = rdb_model.GUTTER * z + (self._origin().x() if ox is None else ox) + 3 * z
        h = block.layout().lineAt(0).height() + 2 * (self.settings["chip_vpad"] - 2) * z
        return run, QRectF(left, g.cy - h / 2, width, h), name

    def caption_rect(self, block):
        g = self._geom(block)
        if g is None or not block.blockFormat().stringProperty(CAPTION):
            return None
        z = self._z
        left = self._text_left() + self._origin().x()
        return QRectF(
            left,
            g.top - CAPTION_PAD * z,
            self.viewport().width() - 4 * z - left,
            CAPTION_PAD * z,
        )

    def _paint_caption(self, p, r, text):
        font = self._font_z(self.document().defaultFont())
        font.setBold(True)
        font.setPointSizeF(max(font.pointSizeF() - 1.5 * self._z, 6 * self._z))
        p.setFont(font)
        p.setPen(_shown(self.settings, "sec_color"))
        fm = QFontMetrics(font)
        p.drawText(
            r.adjusted(0, 0, 0, -2 * self._z),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
            fm.elidedText(text, Qt.TextElideMode.ElideRight, int(r.width())),
        )

    def _visible_blocks(self):
        b = self._block_at(0).previous()
        b = b if b.isValid() else self.document().begin()
        h = self.viewport().height()
        while b.isValid():
            g = self._geom(b)
            if g is not None and g.top > h:
                break
            yield b
            b = b.next()

    def _path_state(self, href):
        if href not in self._paths:
            local = Path(path_local(href))
            self._paths[href] = (1 if local.is_dir() else 2) if local.exists() else 0
        return self._paths[href]

    def _paint_bubbles(self, p, block):
        lay = block.layout()
        if lay is None or not block.isVisible() or lay.lineCount() == 0:
            return
        z, org = self._z, lay.position() + self._origin()
        it = block.begin()
        while not it.atEnd():
            fr = it.fragment()
            it += 1
            f = fr.charFormat()
            if self._unseen(fr.position(), fr.position() + fr.length()):
                continue
            if f.isImageFormat() and not self.text_page:
                self._paint_image_chip(p, block, fr, lay, org, z)
                continue
            if not (f.isAnchor() and is_path_link(f.anchorHref())):
                continue
            state = self._path_state(f.anchorHref())
            rel = fr.position() - block.position()
            a, end = _lay_i(lay, rel), _lay_i(lay, rel + fr.length(), True)
            for i in range(lay.lineCount()):
                ln = lay.lineAt(i)
                s = max(a, ln.textStart())
                e = min(end, ln.textStart() + ln.textLength())
                if s >= e:
                    continue
                x0, x1 = ln.cursorToX(s)[0], ln.cursorToX(e)[0]
                st = self.settings
                # V pad 0 keeps the chip 2 px inside its line, 2 fills the line, more reaches past it
                v = (st["chip_vpad"] - 2) * z
                r = QRectF(
                    org.x() + x0, org.y() + ln.y() - v, x1 - x0, ln.height() + 2 * v
                )
                hot = self._chip_hover is not None and (
                    self._chip_hover[0] <= fr.position() < self._chip_hover[1]
                )
                _paint_chip(p, r, st, state == 0, z, hot)
                if s == a:
                    side = st["chip_icon"] * z
                    glyph = QRectF(
                        r.left() + glyph_in(st) * z,
                        r.center().y() - side / 2,
                        side,
                        side,
                    )
                    local = path_local(f.anchorHref())
                    icon = file_icon(local) if state == 2 else None
                    if icon is not None:
                        icon.paint(p, glyph.toAlignedRect())
                    else:
                        (
                            _paint_folder
                            if state == 1 or (state == 0 and not Path(local).suffix)
                            else _paint_file
                        )(p, glyph)
        if not is_heading(block):
            for span, r in self.tag_chips(block):
                _paint_chip(p, r, self.settings, False, z, self._tag_hot(span), True)
        p.setPen(Qt.PenStyle.NoPen)

    def tag_origin(self, block):
        return block.layout().position() + self._origin()

    def tag_rects(self, block):
        for _, r in self.tag_chips(block):
            yield r

    def tag_chips(self, block):
        # the highlighter left the chip's pad inside the text: after a hidden '#', and as
        # spacing after the last letter, followed by the gap
        z, st, lay = self._z, self.settings, block.layout()
        org, caret = self.tag_origin(block), self.textCursor().position()
        pad, gap = st["tag_pad"] * z, TAG_GAP * z
        v = (st["chip_vpad"] - 2) * z
        lone = _lone(block, st)
        for ta, tb in tag_runs(block):
            a, b = (_lay_i(lay, t - block.position(), t == tb) for t in (ta, tb))
            r0, r1 = tag_reveal(block, (ta, tb), lone)
            if ((ta, tb) == lone and not r0 <= caret <= r1) or self._unseen(ta, tb):
                continue
            hidden = not r0 <= caret <= r1
            for i in range(lay.lineCount()):
                ln = lay.lineAt(i)
                s = max(a, ln.textStart())
                e = min(b, ln.textStart() + ln.textLength())
                if s >= e:
                    continue
                x0, x1 = ln.cursorToX(s)[0], ln.cursorToX(e)[0]
                if s > a or not hidden:
                    x0 -= pad
                if e == b:
                    x1 -= gap
                else:
                    x1 += pad
                yield (
                    (ta, tb),
                    QRectF(
                        org.x() + x0, org.y() + ln.y() - v, x1 - x0, ln.height() + 2 * v
                    ),
                )

    def _panel_at(self, pos):
        for b, _ in self._rows():
            if (
                is_todo(b)
                and (r := self._panel_rect(b)) is not None
                and r.contains(pos)
            ):
                return b
        return None

    def _hit(self, pos):
        if (todo := self._panel_at(pos)) is not None:
            return "panel", todo
        block = self.cursorForPosition(self._on_line(pos).toPoint()).block()
        g = self._geom(block)
        if g is None or (self._rename is not None and block == self._rename.block):
            return "", block
        # the checkbox may overhang the text lines of a small font
        row = g.top <= pos.y() < g.bottom
        if is_section(block):
            return ("section" if row else ""), block
        ox, z = self._origin().x(), self._z
        h1_box = heading_level(block) == 1 and (
            g.top - H1_PAD * z <= pos.y() < g.bottom + H1_PAD * z
        )
        if (r := self.h1_swatch_rect(block)) is not None and r.adjusted(
            -1, -1, 1, 1
        ).contains(pos):
            return "swatch", block
        if h1_box and not self.isReadOnly():
            return "grip", block
        if pos.x() - ox >= self._text_left():
            return "", block
        if h1_box:
            return "fold", block
        if (r := self.fold_rect(block)) is not None and r.adjusted(
            -2, -2, 2, 2
        ).contains(pos):
            return "fold", block
        # an H2's handle is its gutter, as an H1's is its whole row
        if (
            heading_level(block) == 2
            and row
            and not self.isReadOnly()
            and pos.x() - ox < self._sep_x()
        ):
            return "grip", block
        if is_todo(block):
            cols = self._item_cols(g, ox, z)
            filled = bool(block.text().strip())
            if (
                "check" in cols
                and not self.links
                and filled
                and cols["check"].contains(pos)
            ):
                return "check", block
            if "bubble" in cols and cols["bubble"].adjusted(-2, -2, 2, 2).contains(pos):
                return "bubble", block
            if (
                "note" in cols
                and self.has_note(block)
                and cols["note"].adjusted(-2, -2, 2, 2).contains(pos)
            ):
                return "note", block
            if (
                "mark" in cols
                and filled
                and cols["mark"].adjusted(-2, -2, 2, 2).contains(pos)
            ):
                return "marker", block
            if (
                "status" in cols
                and filled
                and cols["status"].adjusted(-2, -2, 2, 2).contains(pos)
            ):
                return "status", block
            if (chip := self._gutter_tag(block, g)) is not None and chip[1].contains(
                pos
            ):
                return "", block
        return ("gutter" if row else ""), block

    def _on_line(self, pos):
        # Qt's hit test takes a point between two lines as past every hidden block, so it lands
        # at the end of the document: such a point moves onto the visible line of its band
        g = self._geom(self.cursorForPosition(pos.toPoint()).block())
        if g is not None and g.top <= pos.y() <= g.bottom:
            return pos
        # the Completed row never takes the caret
        rows = [r for r in self.row_geoms() if not is_section(r.block)]
        if not rows:
            return pos
        y = pos.y() + self.verticalScrollBar().value()
        g = self._geom(next((r for r in rows if y < r.bottom), rows[-1]).block)
        return QPointF(pos.x(), min(max(pos.y(), g.top + 1), g.bottom - 1))

    def _on_line_event(self, e):
        pos = self._on_line(e.position())
        if pos == e.position():
            return e
        return QMouseEvent(
            e.type(), pos, e.globalPosition(), e.button(), e.buttons(), e.modifiers()
        )

    def _gutter_click(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return False
        what, block = self._hit(e.position())
        if what == "section":
            doc = self.document()
            set_collapsed(doc, not is_collapsed(doc))
            apply_view(doc, self.check_mode)
            doc.setModified(True)
            self.viewport().update()
        elif what == "fold":
            self.set_fold(block)
        elif what == "swatch":
            self.h1_colour_popup(block)
        elif what == "grip" and heading_level(block) == 1 and e.modifiers() & CTRL:
            self._sel_click(block, bool(e.modifiers() & SHIFT))
        elif what == "grip":
            if heading_level(block) == 1 and block not in self._sel_heads():
                self._clear_sel()
            self._grip = Grip(block, e.position().toPoint(), False)
        elif what == "check":
            self.toggle_item(block)
        elif what == "bubble":
            self.open_swatches(block)
        elif what == "note":
            self.note_click(block)
        elif what == "marker":
            self.open_marker(block)
        elif what == "status":
            self.open_status(block)
        return what in (
            "section",
            "fold",
            "swatch",
            "grip",
            "check",
            "bubble",
            "note",
            "panel",
            "marker",
            "status",
        )

    def clicked_h1(self, pos):
        what, block = self._hit(QPointF(pos))
        return block if what == "grip" and heading_level(block) == 1 else None

    def rename_heading(self, block):
        self._clear_sel()
        c = QTextCursor(block)
        c.movePosition(MO.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
        self._rename = Session(block, self.document().availableUndoSteps())
        self._set_mode("insert")
        self.setTextCursor(c)
        self._sync_caret()
        self.setFocus()

    def _rename_caret(self):
        if self._rename is not None and self.textCursor().block() != self._rename.block:
            self._end_rename()

    def _end_rename(self, keep=True):
        r, self._rename = self._rename, None
        self._sync_caret()
        if r is None or not r.block.isValid():
            return False
        undone = False
        if not keep or not r.block.text().strip():
            # everything typed was one undo step: undoing it restores the old text
            while self.document().availableUndoSteps() > r.steps:
                self.undo()
                undone = True
            c = QTextCursor(r.block)
            c.movePosition(MO.EndOfBlock)
            self.setTextCursor(c)
        return undone

    def _rename_key(self, e):
        # True when the key ends the rename and is used up; a move or undo ends it and goes on
        key = e.key()
        if key == Qt.Key.Key_Escape:
            self._end_rename(False)
            return True
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not e.modifiers() & (
            CTRL | SHIFT
        ):
            self._end_rename()
            return True
        if e.matches(QKeySequence.StandardKey.Undo):
            # a rollback was this key's undo
            return self._end_rename()
        if key in RENAME_ENDS:
            self._end_rename()
        return False

    @contextmanager
    def _joined(self, r):
        if r is None:
            yield
            return
        doc = self.document()
        steps, c = doc.availableUndoSteps(), QTextCursor(doc)
        (c.joinPreviousEditBlock if r.joined else c.beginEditBlock)()
        try:
            yield
        finally:
            c.endEditBlock()
        r.joined = r.joined or doc.availableUndoSteps() != steps

    def _grip_heads(self):
        head, sel = self._grip.head, self._sel_heads()
        return sel if head in sel else [head]

    def _grip_spots(self):
        # (y, before): above a heading of the group's level or higher, or at the end of the open items
        doc = self.document()
        sec = section_block(doc)
        allowed = {
            None if b is None else b.blockNumber()
            for b in head_spots(doc, self._grip_heads())
        }
        rows = self.row_geoms()
        out = [(r.top, r.block) for r in rows if r.block.blockNumber() in allowed]
        if (
            not any(is_section(r.block) for r in rows)
            and (None if sec is None else sec.blockNumber()) in allowed
        ):
            out.append((rows[-1].bottom, sec))
        return out

    def _grip_drag(self, pt):
        grip = self._grip
        if not grip.moved:
            if abs(pt.y() - grip.start.y()) < QApplication.startDragDistance():
                return
            self._grip = grip._replace(moved=True)
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            if self._drag is not None:
                self._drag.finish()
            self._drag = DragOverlay(
                self, self._grip_heads(), grip.head, self._grip_spots(), grip.start
            )
        self._drag.move_to(pt)

    def _grip_cancel(self):
        self._grip = self._grip._replace(dead=True)
        self.viewport().setCursor(Qt.CursorShape.IBeamCursor)
        self._drag.cancel()

    def _grip_abort(self):
        # the view changed under the drag (zoom, resize, a new document): no way back to the rows
        if self._grip is not None:
            self._grip = self._grip._replace(dead=True)
            self.viewport().setCursor(Qt.CursorShape.IBeamCursor)
        if self._drag is not None:
            self._drag.finish()

    def _in_h1_gutter(self, head, pt):
        box = self.fold_rect(head)
        return pt.x() - self._origin().x() < self._sep_x() and (
            box is None or not box.adjusted(-2, -2, 2, 2).contains(pt)
        )

    def _grip_drop(self):
        grip, drag = self._grip, self._drag
        self._grip = None
        if grip.dead:
            return
        if not grip.moved:
            if heading_level(grip.head) == 1:
                self._clear_sel()
                if self._in_h1_gutter(grip.head, grip.start):
                    self.setTextCursor(QTextCursor(grip.head))
                    # a mouse selection, which Ctrl+click adds to
                    if not item_id(grip.head):
                        ensure_ids(self.document())
                    self._sel_set({item_id(grip.head)}, item_id(grip.head))
                else:
                    self.set_fold(grip.head)
            return
        spot = drag.drop()
        if spot is not None:
            doc = self.document()
            # the caret passes through the moved lines; a folded group must not open for it
            # one outer edit block: the find filter re-hides its lines when the edit ends, after this view
            edit = QTextCursor(doc)
            edit.beginEditBlock()
            # by id, before the move: the cut leaves grip.head naming some other block
            mine = item_id(grip.head)
            try:
                with self._settled():
                    moved = move_groups(doc, drag.heads, spot[1])
                    if moved is not None:
                        apply_view(doc, self.check_mode)
                        if len(drag.heads) > 1:
                            moved = next(
                                b for b in iter_blocks(doc) if item_id(b) == mine
                            )
                        self.setTextCursor(QTextCursor(moved))
            finally:
                edit.endEditBlock()
            if moved is not None:
                self.laid_out.emit()
            self.verticalScrollBar().setValue(drag.scroll)
        drag.settle()

    def mouseReleaseEvent(self, e):
        if self._pill is not None:
            self._pill_drop()
        elif self._grip is not None:
            self._grip_drop()
        else:
            super().mouseReleaseEvent(e)
            return
        what, block = self._hit(e.position())
        pt = e.position().toPoint()
        chip = self._chip_at(pt)
        link = chip is not None or bool(self.href_at(pt))
        self.viewport().setCursor(
            self._shape(what, block, chip, self._tag_at(pt), e.position(), link)
        )
        e.accept()

    def set_fold(self, block, flag=None):
        doc = self.document()
        if flag is None and block.blockNumber() in self.empty_heads:
            # it draws as closed only because the filter hid its lines, so a click opens it
            self.revealed = block
        else:
            set_folded(doc, block, not is_folded(block) if flag is None else flag)
        self._folded(block)

    def has_folds(self):
        return bool(fold_headings(self.document()))

    def folds_open(self):
        # a heading hidden inside a folded one does not count as open
        return any(
            not is_folded(h) and fold_owner(h) is None
            for h in fold_headings(self.document())
        )

    def toggle_all_folds(self):
        if not self.has_folds():
            return
        doc = self.document()
        set_all_folded(doc, self.folds_open())
        self._folded(fold_owner(self.textCursor().block()))

    def _folded(self, head):
        # a caret inside the group goes to the heading before the group's lines hide
        doc = self.document()
        if fold_owner(self.textCursor().block()):
            self.setTextCursor(QTextCursor(head))
        apply_view(doc, self.check_mode)
        doc.setModified(True)
        self.viewport().update()
        self.laid_out.emit()
        self.fold_toggled.emit()

    def _shape(self, what, block, chip, tag, pos, link):
        if not (what or chip is not None or tag is not None or link):
            return Qt.CursorShape.IBeamCursor
        if link and not what:
            hand = self.settings["link_cursor"] == "Hand"
            return (
                Qt.CursorShape.PointingHandCursor
                if hand
                else Qt.CursorShape.ArrowCursor
            )
        pointer = heading_level(block) == 1 and self.settings["h1_cursor"] == "Pointer"
        if what == "grip" and not pointer:
            fold = self.fold_rect(block)
            if fold is None or not fold.adjusted(-2, -2, 2, 2).contains(pos):
                return Qt.CursorShape.OpenHandCursor
        return Qt.CursorShape.ArrowCursor

    def mouseMoveEvent(self, e):
        if self._pill is not None:
            if not self._pill.dead:
                self._pill_drag(e.position().toPoint())
            e.accept()
            return
        if self._grip is not None:
            if not self._grip.dead:
                self._grip_drag(e.position().toPoint())
            e.accept()
            return
        super().mouseMoveEvent(self._on_line_event(e))
        what, block = self._hit(e.position())
        chip = self._chip_at(e.position().toPoint())
        tag = self._tag_at(e.position().toPoint())
        link = chip is not None or bool(self.href_at(e.position().toPoint()))
        shape = self._shape(what, block, chip, tag, e.position(), link)
        self.viewport().setCursor(shape)
        hover = block.blockNumber() if what == "marker" else -1
        status = block.blockNumber() if what == "status" else -1
        if (hover, status) != (self._mark_hover, self._status_hover):
            self._mark_hover, self._status_hover = hover, status
            self.viewport().update(
                0,
                0,
                int(self._text_left()) + int(self._origin().x()),
                self.viewport().height(),
            )
        if chip != self._chip_hover:
            self._chip_hover = chip
            self.viewport().update()
        if tag != self._tag_hover:
            self._tag_hover = tag
            self.viewport().update()
        chip = self._image_at(e.position())
        self._image_hover = None if chip is None else (chip[3], chip[3] + 1)
        over = None
        if chip is not None:
            over = ("image", chip[1])
        elif what == "note":
            over = ("note", block.blockNumber())
        if chip is not None and any(chip[1] in w.names for w in self.image_wins):
            over = None
        if over != self._hover_at:
            self._img_hide()
            if chip is not None and over is not None:
                self._img_wait(over, self._shown(chip[1]), chip[2])
            elif over is not None:
                self._img_wait(over, block, self._item_cols(self._geom(block))["note"])

    def _img_wait(self, key, what, rect):
        # the preview opens once the mouse has rested on the chip for the hover delay
        self._hover_at = key
        self._hover_args = (key, what, rect)
        self.viewport().update()
        if self.settings["chip_hover_ms"]:
            self._hover_timer.start(self.settings["chip_hover_ms"])
        else:
            self._img_show(*self._hover_args)

    def _image_at(self, pt):
        # (block, name, rect) of the image chip under the point, None over none; lists only
        if self.text_page:
            return None
        at = self.cursorForPosition(pt.toPoint()).position()
        for pos in (at - 1, at):
            for b, p, _, fmt in self._image_frags(pos, pos):
                if p != pos:
                    continue
                a, z = (
                    self.cursorRect(self._textcursor_at(p)),
                    self.cursorRect(self._textcursor_at(p + 1)),
                )
                r = QRectF(a.left(), a.top(), z.left() - a.left(), a.height())
                if r.contains(pt):
                    return b, fmt.name(), r, p
        return None

    def _img_show(self, key, what, rect):
        geo = self.window().screen().availableGeometry()
        if key[0] == "image":
            self.img_pop = ImagePreview([what], geo.size(), self.settings["preview_px"])
        else:
            doc = open_doc(
                self.store, note_link(what), "stay", text=True, readonly=True
            )
            doc.setDefaultFont(note_font(EDITOR_FONT))
            self.img_pop = NotePreview(doc, geo.size())
        self._hover_at = key
        pop_at(self.img_pop, self, rect)

    def _img_hide(self, *_):
        self._hover_timer.stop()
        if self._hover_at is not None:
            self._hover_at = None
            self.viewport().update()
        if self.img_pop is not None:
            self.img_pop.close()
            self.img_pop.deleteLater()
            self.img_pop = None

    def open_images(self, block, name):
        # one window per item: a second click raises it
        names = [n for _, _, n in item_images(owner_todo(block) or block)]
        win = next((w for w in self.image_wins if set(w.names) & set(names)), None)
        if win is None:
            title = " ".join(block.text().replace("\ufffc", " ").split())
            win = ImageWindow(
                title[:80] or "Image",
                names,
                self._original,
                self._remove_image,
                self.store.get_markup,
                self.save_markup,
                self.settings["theme"],
            )
            win.closed.connect(lambda w=win: self.image_wins.remove(w))
            self.image_wins.append(win)
            win.show()
        win.tabs.setCurrentRow(win.names.index(name))
        win.raise_()
        win.activateWindow()

    def close_image_windows(self):
        for win in list(self.image_wins):
            win.close()

    def _remove_image(self, name):
        # one undo step on the open page; the names the item still holds, None when refused
        doc = self.document()
        hit = next(
            (
                (b, pos, length)
                for b, pos, length, fmt in self._image_frags(0, doc.characterCount())
                if fmt.name() == name
            ),
            None,
        )
        if hit is None or self.isReadOnly():
            self.notice.emit("Image not deleted")
            return None
        block, pos, length = hit
        c = QTextCursor(doc)
        c.setPosition(pos)
        c.setPosition(pos + length, QTextCursor.MoveMode.KeepAnchor)
        c.beginEditBlock()
        c.removeSelectedText()
        self._drop_stale_image_meta(block)
        c.endEditBlock()
        left = {n for _, _, n in item_images(owner_todo(block) or block)}
        for win in list(self.image_wins):
            if name in win.names:
                win.set_names(left)
        return left

    def _ask_image_name(self, default):
        # (name, accepted): the box opens on the default, selected
        return QInputDialog.getText(self, "Name", "", text=default)

    def rename_image(self, block, name):
        label, ok = self._ask_image_name(image_label(block, name))
        if ok and label.strip():
            set_image_label(block, name, label.strip())
            self.fit_images()

    def _textcursor_at(self, pos):
        c = QTextCursor(self.document())
        c.setPosition(pos)
        return c

    def _pill_caret(self, span):
        # the first place after the pill that touches no tag, else the last before it: no '#' shows
        block = self.document().findBlock(span[0])
        lone = _lone(block, self.settings)
        tags = [tag_reveal(block, r, lone) for r in tag_runs(block)]
        lo = block.position()
        hi = lo + block.length() - 1
        return next(
            (
                p
                for p in [*range(span[1], hi + 1), *range(span[0] - 1, lo - 1, -1)]
                if not any(a <= p <= b for a, b in tags)
            ),
            span[1],
        )

    def _unseen(self, a, b):
        # a pill a drag has lifted, or the dropped one while the floating pill glides in
        lift = LIFTS.get(self.document())
        return lift is not None and any(
            s is not None and s[0] < b and a < s[1] for s in (lift.cut, lift.ghost)
        )

    def _pill_rects(self, span):
        # viewport rects of a pill, one per line it runs over
        block = self.document().findBlock(span[0])
        if span in tag_runs(block):
            return [r for s, r in self.tag_chips(block) if s == span]
        lay, org = block.layout(), self.tag_origin(block)
        v = (self.settings["chip_vpad"] - 2) * self._z
        a, b = (_lay_i(lay, p - block.position(), p == span[1]) for p in span)
        out = []
        for i in range(lay.lineCount()):
            ln = lay.lineAt(i)
            s, e = max(a, ln.textStart()), min(b, ln.textStart() + ln.textLength())
            if s < e:
                x0, x1 = ln.cursorToX(s)[0], ln.cursorToX(e)[0]
                out.append(
                    QRectF(
                        org.x() + x0, org.y() + ln.y() - v, x1 - x0, ln.height() + 2 * v
                    )
                )
        return out

    def _pos_pt(self, pos):
        # the viewport point of a document position: its x, on the top of its line
        block = self.document().findBlock(pos)
        lay = block.layout()
        i = _lay_i(lay, pos - block.position())
        ln = lay.lineForTextPosition(i)
        org = self.tag_origin(block)
        return QPointF(org.x() + ln.cursorToX(i)[0], org.y() + ln.y())

    def _gap_of(self, spot):
        # where the gap of a landing spot opens: ("start", spot) as hair spaces before a line,
        # ("blank", q) as spacing after the blank at q (the spots either side of one blank
        # share it), ("none", None) at a line end, where nothing follows to move aside
        d = self._tdrag
        if spot not in d.ways:
            doc = self.document()
            block = doc.findBlock(spot)
            end = block.position() + block.length() - 1
            if spot == block.position():
                d.ways[spot] = ("start", spot)
            elif spot < end and doc.characterAt(spot) in " \t":
                d.ways[spot] = ("blank", spot)
            elif doc.characterAt(spot - 1) in " \t":
                d.ways[spot] = ("blank", spot - 1)
            else:
                d.ways[spot] = ("none", None)
        return d.ways[spot]

    def _slot_x(self, spot, x_at):
        # the middle of a spot's open gap, so a pointer over the gap keeps it; x_at gives a
        # position's x on the spot's own line (after a soft wrap, the character after a trailing
        # blank starts the next line). The gap ends at the spot unless the spot is before its blank
        x = x_at(spot)
        way = self._gap_of(spot)
        if way[0] == "none":
            return x
        g = sum(v for s, v in self._tdrag.gaps.items() if self._gap_of(s) == way)
        if way == ("blank", spot):
            x = x_at(spot + 1)
        return x - g / 2

    def _pill_spot(self, pt):
        # the landing spot nearest the pointer on the row band it is over (above or below every
        # band, the nearest row), None over a row with none. Not Qt's hit test: a point inside an
        # open line-start gap hits the next row, so the gap closes and opens again every frame
        y = pt.y() + self.verticalScrollBar().value()
        rows = self.row_geoms()
        row = next((r for r in rows if r.top <= y < r.bottom), None)
        if row is None:
            rows = [r for r in rows if not is_section(r.block)]
            if not rows:
                return None
            row = next((r for r in rows if y < r.bottom), rows[-1])
        block = row.block
        spots = tag_spots(block)
        if not spots:
            return None
        lay, org, base = block.layout(), self.tag_origin(block), block.position()
        lines = [lay.lineAt(i) for i in range(lay.lineCount())]
        ln = next(
            (n for n in lines if org.y() + n.y() + n.height() > pt.y()), lines[-1]
        )

        def x_at(q):
            return org.x() + ln.cursorToX(_lay_i(lay, q - base))[0]

        x = {}
        for p in spots:
            i = _lay_i(lay, p - base)
            if ln.textStart() <= i <= ln.textStart() + ln.textLength():
                x[p] = self._slot_x(p, x_at)
        if not x:
            # a wrapped line inside one long word or link
            return None
        # spots that share a gap tie: the later one, the same each time
        return min(x, key=lambda p: (abs(x[p] - pt.x()), -p))

    def _pill_aim(self):
        # only a pointer move or a scroll aims again: the gaps that open under a still pointer
        # re-wrap rows, and aiming against them each frame flips the spot to and fro
        d = self._tdrag
        key = (d.mouse, self.verticalScrollBar().value())
        if key != d.aimed:
            d.aimed = key
            d.spot = self._pill_spot(d.mouse)

    def _pill_drag(self, pt):
        pill = self._pill
        if not pill.moved:
            if (pt - pill.start).manhattanLength() < QApplication.startDragDistance():
                return
            self._pill_lift(pill)
        d = self._tdrag
        if d is None:
            return
        d.mouse = pt
        d.pos = QPointF(pt - d.offset)
        d.update()
        self._pill_aim()

    def _pill_lift(self, pill):
        # the pill comes off the page as a floating image; its own place starts to close
        self._pill_end()
        rects = self._pill_rects(pill.span)
        if not rects:
            self._pill = pill._replace(dead=True)
            return
        self._pill = pill._replace(moved=True)
        self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
        self.setCursorWidth(0)
        doc = self.document()
        rect = next((r for r in rects if r.contains(QPointF(pill.start))), rects[0])
        box = rect.toAlignedRect()
        z = self._z
        rad = {"box": 4 * z, "pill": box.height() / 2, "token": 3 * z}[
            self.settings["chip_style"]
        ]
        d = PillDrag(
            self, self.viewport().grab(box), pill.start - box.topLeft(), pill.start, rad
        )
        lft, r, d0, d1 = pill_home(doc, *pill.span)
        block = doc.findBlock(d0)
        lay = block.layout()
        # each lifted character's advance on the page, which its spacing cancels exactly
        d.adv = {}
        for at in range(d0, d1):
            i = _lay_i(lay, at - block.position())
            ln = lay.lineForTextPosition(i)
            d.adv[at] = ln.cursorToX(i + 1)[0] - ln.cursorToX(i)[0]
        # the room the pill and its blank take: the width of a gap that fits it
        d.w = sum(d.adv.values())
        d.room, d.home, d.cut, d.doc = d.w, (lft, r), (d0, d1), doc
        # where the floating image sits against its first character, to glide home
        d.back = QPointF(box.topLeft()) - self._pos_pt(d0)
        d.ways, d.units, d.ghost = {}, {}, None
        # an edit from elsewhere during the drag ends it: its positions would be stale. The
        # drag's own look stays off this signal (_relook), and so does its drop (moving)
        d.moving = False
        doc.contentsChange.connect(self._pill_edited)
        d.touched = {doc.findBlock(d0).blockNumber()}
        self._tdrag = d
        self._lift_apply(set(d.touched))

    def _lift_apply(self, numbers):
        # the drag's look onto the rows whose blocks (by number) it changed
        d, doc = self._tdrag, self._tdrag.doc
        gaps, heads = {}, {}
        for s, g in d.gaps.items():
            kind, at = self._gap_of(s)
            if kind == "blank":
                gaps[at] = gaps.get(at, 0) + g
            elif kind == "start":
                n = doc.findBlock(s).blockNumber()
                heads[n] = heads.get(n, 0) + g
        LIFTS[doc] = Lift(d.cut, d.adv, d.room, gaps, None)
        d.touched |= numbers
        blocks = [doc.findBlockByNumber(n) for n in numbers]
        for b in blocks:
            g = heads.get(b.blockNumber(), 0)
            n = round(g / self._hair(b)) if g else 0
            if n:
                b.layout().setPreeditArea(0, HAIR * n)
            elif b.layout().preeditAreaText():
                b.layout().setPreeditArea(-1, "")
        self._relook(blocks)

    def _hair(self, block):
        # the width of a hair space before a line, measured once per row per drag
        d = self._tdrag
        n = block.blockNumber()
        if n not in d.units:
            lay = block.layout()
            lay.setPreeditArea(0, HAIR * 20)
            self._relook([block])
            ln = lay.lineAt(0)
            d.units[n] = (ln.cursorToX(20)[0] - ln.cursorToX(0)[0]) / 20
        return d.units[n]

    def _relook(self, blocks):
        # a display-only change: the document stays quiet and keeps its modified flag
        doc = self.document()
        modified = doc.isModified()
        doc.blockSignals(True)
        try:
            for b in blocks:
                for h in doc.findChildren(ItemHighlighter):
                    h.rehighlightBlock(b)
                # a preedit change alone does not lay the block out again
                doc.markContentsDirty(b.position(), b.length())
                doc.documentLayout().blockBoundingRect(b)
            doc.setModified(modified)
        finally:
            doc.blockSignals(False)

    def _lift_clear(self, d):
        doc = d.doc
        LIFTS.pop(doc, None)
        blocks = [doc.findBlockByNumber(n) for n in d.touched]
        blocks = [b for b in blocks if b.isValid()]
        for b in blocks:
            if b.layout().preeditAreaText():
                b.layout().setPreeditArea(-1, "")
        d.touched = set()
        if doc is self.document():
            self._relook(blocks)

    def _pill_tick(self):
        d = self._tdrag
        if d is None:
            return
        if d.glide is None:
            _edge_scroll(
                self.verticalScrollBar(), d.mouse.y(), self.viewport().height()
            )
            self._pill_aim()
        else:
            start, to, t0, then = d.glide
            t = min(1.0, (time.monotonic() - t0) / d.GLIDE)
            d.pos = start + (to() - start) * (1 - (1 - t) ** 3)
            d.update()
            if t >= 1.0:
                then()
                return
        if d.ghost is None:
            self._pill_ease()

    def _pill_ease(self):
        # one frame of the rows making room: the gap at the aimed spot opens, the others close,
        # and the pill's own place opens again while the pointer is over it or the pill goes home
        d, doc = self._tdrag, self._tdrag.doc
        home = d.cancel or (d.spot is not None and d.home[0] <= d.spot <= d.home[1])
        want = {} if home or d.spot is None else {d.spot: d.w}
        numbers = set()
        room = _toward(d.room, d.w if home else 0, d.EASE)
        if room != d.room:
            d.room = room
            numbers.add(doc.findBlock(d.cut[0]).blockNumber())
        gaps = {}
        for s in d.gaps.keys() | want.keys():
            g = _toward(d.gaps.get(s, 0), want.get(s, 0), d.EASE)
            if g != d.gaps.get(s, 0):
                numbers.add(doc.findBlock(s).blockNumber())
            if g:
                gaps[s] = g
        d.gaps = gaps
        if numbers:
            self._lift_apply(numbers)

    def _pill_home(self):
        # the floating pill glides back while its place opens again
        d = self._tdrag
        d.cancel, d.spot = True, None
        d.glide = (
            QPointF(d.pos),
            lambda: self._pos_pt(d.cut[0]) + d.back,
            time.monotonic(),
            self._pill_end,
        )

    def _pill_end(self):
        d = self._tdrag
        if d is None:
            return
        self._tdrag = None
        d.doc.contentsChange.disconnect(self._pill_edited)
        d.finish()
        self._lift_clear(d)
        self.setCursorWidth(self._cursor_width(self.mode))

    def _pill_edited(self, *_):
        if self._tdrag is not None and not self._tdrag.moving:
            self._pill_stop()

    def _pill_cancel(self):
        # Esc: the pill goes home; the release that follows does nothing
        self._pill = self._pill._replace(dead=True)
        self.viewport().setCursor(Qt.CursorShape.IBeamCursor)
        self._pill_home()

    def _pill_stop(self):
        # a drag ends at once: a lost focus, or a view that changed under it
        if self._pill is not None:
            self._pill = self._pill._replace(dead=True)
            self.viewport().setCursor(Qt.CursorShape.IBeamCursor)
        self._pill_end()

    def _pill_drop(self):
        pill, d = self._pill, self._tdrag
        self._pill = None
        if pill.dead:
            return
        doc = self.document()
        if not pill.moved:
            # a click: an image chip opens its pictures
            chip = self._image_at(QPointF(pill.start))
            if doc.characterAt(pill.span[0]) == "￼" and chip is not None:
                self.open_images(chip[0], chip[1])
            return
        spot = d.spot
        if spot is None or d.home[0] <= spot <= d.home[1]:
            self._pill_home()
            return
        self._lift_clear(d)
        d.moving = True
        # the caret passes through the moved line; a folded group must not open for it
        with self._settled():
            at = move_pill(doc, *pill.span, spot)
            new = (at, at + pill.span[1] - pill.span[0])
            self.setTextCursor(self._textcursor_at(self._pill_caret(new)))
        d.moving = False
        block = doc.findBlock(at)
        rects = self._pill_rects(new)
        if not rects and (chip := self._gutter_tag(block, self._geom(block))):
            # it became the row's only tag, shown in the tag gutter
            rects = [chip[1]]
        if not rects:
            self._pill_end()
            return
        to = QPointF(rects[0].toAlignedRect().topLeft())
        d.ghost = new
        LIFTS[doc] = Lift(None, {}, 0, {}, new)
        d.touched = {block.blockNumber()}
        self._relook([block])
        d.glide = (QPointF(d.pos), lambda: to, time.monotonic(), self._pill_end)

    def href_at(self, pt):
        href = self.anchorAt(pt)
        if href:
            return href
        c = self.cursorForPosition(pt)
        # the caret lands on either side of the hovered character: the one under pt is the
        # character before the caret when pt is left of the caret and on the same line
        here = self.cursorRect(c)
        pos = c.position()
        if pt.x() < here.x():
            if pos == c.block().position():
                return ""
            prev = QTextCursor(c)
            prev.setPosition(pos - 1)
            if self.cursorRect(prev).y() != here.y():
                return ""
            pos -= 1
        return next((h for s, e, h in detected_links(c.block()) if s <= pos < e), "")

    def _chip_at(self, pt):
        if not is_path_link(self.anchorAt(pt)):
            return None
        pos = self.cursorForPosition(pt).position()
        # the caret lands on whichever side of the hovered character is nearer
        doc = self.document()
        return chip_span(doc, pos) or chip_span(doc, pos - 1)

    def _tag_chip_at(self, pt):
        # the span of the lone tag whose gutter chip is under pt
        if pt.x() - self._origin().x() >= self._text_left():
            return None
        block = self.cursorForPosition(self._on_line(QPointF(pt)).toPoint()).block()
        g = self._geom(block)
        if g is None or not is_todo(block):
            return None
        chip = self._gutter_tag(block, g)
        return chip[0] if chip is not None and chip[1].contains(QPointF(pt)) else None

    def _tag_at(self, pt):
        if (span := self._tag_chip_at(pt)) is not None:
            return span
        pos = self.cursorForPosition(pt).position()
        doc = self.document()
        # a heading draws no tags, so none can be opened there
        if is_heading(doc.findBlock(pos)):
            return None
        span = tag_span(doc, pos) or tag_span(doc, pos - 1)
        # a lone tag hidden inline is zero width: its edge is not the chip
        if span is None or not any(
            s == span for s, _ in self.tag_chips(doc.findBlock(span[0]))
        ):
            return None
        return span

    def tag_text_at(self, pt):
        span = self._tag_at(pt)
        if span is None:
            return None
        return run_text(self.document().findBlock(span[0]), *span)

    def leaveEvent(self, e):
        super().leaveEvent(e)
        self._img_hide()
        if (
            self._mark_hover != -1
            or self._status_hover != -1
            or self._chip_hover is not None
            or self._tag_hover is not None
            or self._image_hover is not None
        ):
            self._mark_hover = self._status_hover = -1
            self._chip_hover = None
            self._tag_hover = None
            self._image_hover = None
            self.viewport().update()

    def focusInEvent(self, e):
        super().focusInEvent(e)
        self._paths = {}

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        self._img_hide()
        self.tagpop.hide()
        if self._grip is not None and self._grip.moved and not self._grip.dead:
            self._grip_cancel()
        if self._pill is not None and self._pill.moved:
            self._pill_stop()

    def hideEvent(self, e):
        # a closing card or window: the drag's look must not outlive it on a kept document
        self._pill_stop()
        super().hideEvent(e)

    def viewportEvent(self, e):
        # a scrollbar appearing narrows the viewport without resizing the editor
        if e.type() == QEvent.Type.Resize:
            self._fit_timer.start()
        return super().viewportEvent(e)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._note_margin()
        self.tagpop.hide()
        self._grip_abort()
        self._pill_stop()

    def wheelEvent(self, e):
        if e.modifiers() & CTRL and self.timeline:
            if dy := e.angleDelta().y():
                self.time_zoom.emit(dy > 0)
            e.accept()
            return
        if e.modifiers() & CTRL and (self.local_zoom or not self.isReadOnly()):
            dy = e.angleDelta().y()
            if dy:
                cur = get_zoom() if self._own is None else self._own
                self._zoom_to(round(cur + (ZOOM_STEP if dy > 0 else -ZOOM_STEP), 1))
            e.accept()
            return
        super().wheelEvent(e)

    def mousePressEvent(self, e):
        if (
            e.button() == Qt.MouseButton.MiddleButton
            and e.modifiers() & CTRL
            and (self.local_zoom or not self.isReadOnly())
        ):
            self._zoom_to(1.0)
            e.accept()
            return
        if self._rename is not None:
            at = self.cursorForPosition(self._on_line(e.position()).toPoint())
            if at.block() != self._rename.block:
                self._end_rename()
        if self._sel and e.button() == Qt.MouseButton.LeftButton:
            what, block = self._hit(e.position())
            if not (what in ("grip", "swatch") and heading_level(block) == 1):
                self._clear_sel()
        if self._gutter_click(e):
            e.accept()
            return
        pt = e.position().toPoint()
        if self._pill is None:
            # a pill still gliding to its place lands at once
            self._pill_end()
        plain = not self.isReadOnly() and not e.modifiers()
        if e.button() == Qt.MouseButton.LeftButton and (
            chip := self._image_at(e.position())
        ):
            self._img_hide()
            if plain:
                # it opens on a release that did not drag it
                self._pill = Pill((chip[3], chip[3] + 1), pt)
                self.setFocus(Qt.FocusReason.MouseFocusReason)
            else:
                self.open_images(chip[0], chip[1])
            e.accept()
            return
        if e.button() == Qt.MouseButton.LeftButton and (
            (not self.isReadOnly() and (span := self._tag_at(pt)))
            or (plain and (span := self._chip_at(pt)))
        ):
            if self._tag_chip_at(pt) is None:
                self._pill = Pill(span, pt)
            if span in tag_runs(self.document().findBlock(span[0])):
                self.setTextCursor(self._textcursor_at(self._pill_caret(span)))
            else:
                # a path chip: the caret snaps to its nearer edge (_chip_snap)
                self.setTextCursor(self.cursorForPosition(pt))
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            e.accept()
            return
        super().mousePressEvent(self._on_line_event(e))

    def mouseDoubleClickEvent(self, e):
        what, block = self._hit(e.position())
        if e.button() == Qt.MouseButton.LeftButton and what in ("note", "panel"):
            if what == "note":
                # the click before this one toggled the preview
                self.note_click(block)
            self.note_page(block)
            e.accept()
            return
        if self._gutter_click(e):
            e.accept()
            return
        href = self.href_at(e.position().toPoint())
        if (
            not href
            and e.button() == Qt.MouseButton.LeftButton
            and not self.isReadOnly()
            and self._tag_at(e.position().toPoint())
        ):
            self._pill = None
            self.setTextCursor(self.cursorForPosition(e.position().toPoint()))
            if self.settings["vim"]:
                self._set_mode("insert")
            e.accept()
            return
        if href and e.button() == Qt.MouseButton.LeftButton:
            shift = bool(e.modifiers() & SHIFT)
            if is_path_link(href) and (shift or not rdb_rdoc.parse_href(href)):
                # the default app, or with Shift the folder that holds it
                QDesktopServices.openUrl(path_open_url(href, shift))
            else:
                # the window opens it in the default browser; an rdoc spot goes to rdoc
                self.mark_visited(href)
                self.link_opened.emit(href, shift)
            e.accept()
            return
        super().mouseDoubleClickEvent(self._on_line_event(e))

    def _leave(self, block):
        b = block.next()
        while b.isValid() and not b.isVisible():
            b = b.next()
        if b.isValid():
            c = QTextCursor(b)
        else:
            b = block.previous()
            while b.isValid() and not _plain(b):
                b = b.previous()
            if not b.isValid():
                return
            c = QTextCursor(b)
            c.movePosition(QTextCursor.MoveOperation.EndOfBlock)
        self.setTextCursor(c)

    def keyPressEvent(self, e):
        history = e.matches(QKeySequence.StandardKey.Undo) or e.matches(
            QKeySequence.StandardKey.Redo
        )
        if self._grip is not None and self._grip.moved:
            if e.key() == Qt.Key.Key_Escape and not self._grip.dead:
                self._grip_cancel()
            e.accept()
            return
        if self._pill is None:
            # a key lands a gliding pill at once, before it can edit
            self._pill_end()
        elif self._pill.moved:
            if e.key() == Qt.Key.Key_Escape and not self._pill.dead:
                self._pill_cancel()
            e.accept()
            return
        if (
            e.key() == Qt.Key.Key_Delete
            and not e.modifiers() & (CTRL | SHIFT | ALT)
            and self._delete_hovered()
        ):
            e.accept()
            return
        self._img_hide()
        self._dir = {Qt.Key.Key_Left: -1, Qt.Key.Key_Right: 1}.get(e.key(), 0)
        if self._rename is not None and self._rename_key(e):
            e.accept()
            return
        if self._sel_key(e):
            e.accept()
            return
        before, run = self._tag_keys(e)
        try:
            with self._settled(history), self.vim.typing(e), self._joined(run):
                if not (self.tagpop.isVisible() and self.tagpop.key(e)):
                    self._autolink_typed(e)
                    self._key(e)
                    self.tagpop.refresh(_edits_text(e))
                if before is not None or self._tagging is not None:
                    self.tagpop.fix_case(before)
                    if self.tagpop.token() is None:
                        self._tagging = None
        finally:
            self._dir = 0

    def _key(self, e):
        mods = e.modifiers() & (CTRL | SHIFT | ALT)
        key = e.key()
        if key == Qt.Key.Key_Escape:
            self._escape()
            e.accept()
            return
        c = self.textCursor()
        block = c.block()
        if is_section(block) and key not in NAV_KEYS:
            self._leave(block)
            e.accept()
            return
        if self._divider_key(e, c):
            e.accept()
            return
        if self.timeline and (mods, key) in NUDGE_KEYS:
            nudge(self.document(), self._nudged(c), *NUDGE_KEYS[mods, key])
            e.accept()
            return
        if self.timeline and key == Qt.Key.Key_F2 and not mods:
            self.field_requested.emit()
            e.accept()
            return
        # a pending vim count, operator, argument or search line keeps the key
        idle = not (self.vim.buf or self.vim.prompt is not None)
        if idle and key == Qt.Key.Key_QuoteLeft and not mods and self.image_menu():
            e.accept()
            return
        if idle and key == Qt.Key.Key_QuoteLeft and not mods:
            # the QuickMenu (item colour popup) opens in every mode; marks are jumped to with '
            self.swatches_current()
            e.accept()
            return
        if (
            idle
            and key == Qt.Key.Key_Equal
            and not self.note
            and not mods & (CTRL | ALT)
            and self.mode == "normal"
        ):
            self.note_toggle()
            e.accept()
            return
        if (
            idle
            and self.timeline
            and self.mode == "normal"
            and key == Qt.Key.Key_T
            and not mods
        ):
            # t is the till-motion elsewhere; here it brings today to the first column
            self.today_requested.emit()
            e.accept()
            return
        if (
            key in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and mods == CTRL
            and block in fold_headings(self.document())
        ):
            # before vim, which reads Ctrl+Enter as <CR>
            self.set_fold(block)
            e.accept()
            return
        if self.mode in VIM_MODES and self.vim.key(e):
            e.accept()
            return
        if not c.hasSelection() and (
            (
                key == Qt.Key.Key_Backspace
                and c.atBlockStart()
                and _blocked(block.previous())
            )
            or (key == Qt.Key.Key_Delete and c.atBlockEnd() and _blocked(block.next()))
        ):
            e.accept()
            return
        if key == Qt.Key.Key_Underscore and mods == CTRL | SHIFT:
            key = Qt.Key.Key_Minus
        if (
            self.text_page
            and key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab)
            and not mods & (CTRL | ALT)
        ):
            if key == Qt.Key.Key_Tab and not mods and self.mode not in VIM_MODES:
                c = self.textCursor()
                c.insertText("	")
                self.setTextCursor(c)
            e.accept()
            return
        if (
            key == Qt.Key.Key_Tab
            and not mods
            and self.mode not in VIM_MODES
            and not c.hasSelection()
            and (
                " " in block.text()[: c.positionInBlock()]
                or (
                    not self.note
                    and not block.text().strip()
                    and (is_todo(block) or block.textList() is not None)
                )
            )
        ):
            # on an item's second or later line, or a blank line, Tab is text, not an indent
            c.insertText("\t")
            self.setTextCursor(c)
            e.accept()
            return
        fn = self.keymap.get((mods, key))
        if fn:
            fn()
            return
        if (
            key in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete)
            and not mods & (ALT | SHIFT)
            and self._chip_delete(key, bool(mods & CTRL))
        ):
            e.accept()
            return
        if (
            key == Qt.Key.Key_Backspace
            and not mods & (CTRL | ALT)
            and c.atBlockStart()
            and (c.hasSelection() or block.textList() is None)
        ):
            # QTextEdit turns Backspace at the start of an indented block into an outdent, and the
            # gutter is an indent
            c.deletePreviousChar()
            self.setTextCursor(c)
            e.accept()
            return
        if e.text():
            self._plain_edge()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and mods == CTRL:
            # a line break inside the item, as Shift+Enter makes it
            super().keyPressEvent(QKeyEvent(e.type(), key, SHIFT, e.text()))
            return
        if key not in (Qt.Key.Key_Return, Qt.Key.Key_Enter) or mods & SHIFT:
            super().keyPressEvent(e)
            return
        was_todo = is_todo(block)
        split_at_start = (
            not c.hasSelection() and c.atBlockStart() and bool(block.text())
        )
        scroll = self.verticalScrollBar().value()
        c.beginEditBlock()
        if (
            (self.text_page or (was_todo and self.settings["blank_todo"]))
            and not c.hasSelection()
            and not block.text()
        ):
            # Qt answers Enter on an empty block with a block format by clearing the format,
            # not by adding a line: a Note card's blank lines and stacked blank todos need the
            # block made here
            tc = self.textCursor()
            tc.insertBlock()
            self.setTextCursor(tc)
        else:
            super().keyPressEvent(e)
        block = self.textCursor().block()
        # splitting at column 0 moves the text (and its meta) into the new block
        new = block.previous() if split_at_start else block
        # a note page keeps plain lines plain; only a todo line begets a todo
        fresh_meta(new, was_todo or not self.note)
        self._task(new)
        self._home(new.blockNumber(), new.blockNumber(), split_at_start)
        f = self.currentCharFormat()
        f.setFontStrikeOut(False)
        self.setCurrentCharFormat(f)
        c.endEditBlock()
        self._restore_view(scroll)

    def _restore_view(self, scroll):
        # inside an open edit block a new line has no layout yet, and scrolling the caret to it
        # lands at the top of the document
        self.verticalScrollBar().setValue(scroll)
        self.ensureCursorVisible()

    def _autolink_typed(self, e):
        # the token before the caret becomes a link when Space, Tab or Enter ends it
        if (
            e.key()
            not in (
                Qt.Key.Key_Space,
                Qt.Key.Key_Tab,
                Qt.Key.Key_Return,
                Qt.Key.Key_Enter,
            )
            or e.modifiers() & (CTRL | ALT)
            or self.isReadOnly()
            or self._rename is not None
            or self.mode != "insert"
        ):
            return
        c = self.textCursor()
        block = c.block()
        if c.hasSelection() or is_heading(block) or is_section(block):
            return
        text, col = block.text(), c.positionInBlock()
        prefix = text.encode("utf-16-le")[: 2 * col].decode("utf-16-le")
        after = text[len(prefix) :][:1]
        if after and not (after.isspace() or after in ".,;:!?\"'>)]}"):
            return
        last = rdb_model.auto_links(prefix)[-1:]
        # between the token and the caret: its trimmed punctuation, then only whitespace
        if not last or any(ch.isspace() for ch in prefix[last[0][1] :].rstrip()):
            return
        s, end, href = last[0]
        lo = block.position() + _u16(prefix, 0, s)
        hi = block.position() + _u16(prefix, 0, end)
        if self._anchored(block, lo, hi):
            return
        self._link_span(lo, hi, href)
        self._unlink_format()

    @staticmethod
    def _anchored(block, lo, hi):
        it = block.begin()
        while not it.atEnd():
            fr = it.fragment()
            if (
                fr.position() < hi
                and fr.position() + fr.length() > lo
                and fr.charFormat().isAnchor()
            ):
                return True
            it += 1
        return False

    def _link_span(self, lo, hi, href):
        c = QTextCursor(self.document())
        c.setPosition(lo)
        c.setPosition(hi, QTextCursor.MoveMode.KeepAnchor)
        c.mergeCharFormat(link_format(href))

    def _autolink_range(self, lo, hi):
        # every web or email address in lo..hi that no link covers yet; True when one linked
        hit, b = False, self.document().findBlock(lo)
        while b.isValid() and b.position() < hi:
            if not (is_heading(b) or is_section(b) or is_divider(b)):
                text = b.text()
                for s, e, href in rdb_model.auto_links(text):
                    a = b.position() + _u16(text, 0, s)
                    z = b.position() + _u16(text, 0, e)
                    if a < hi and z > lo and not self._anchored(b, a, z):
                        self._link_span(a, z, href)
                        hit = True
            b = b.next()
        return hit

    def _tag_keys(self, e):
        # (the tag token before the key, the session the key joins): typing in a tag is one undo
        # step with the case corrections it makes; vim's insert already joins its own
        if self._rename is not None:
            return None, self._rename
        enter = e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (
            e.modifiers() & (CTRL | ALT)
        )
        if not (_edits_text(e) or enter) or self.tag_source is None or self.note:
            self._tagging = None
            return None, None
        before = self.tagpop.token()
        if before is None and e.text() != "#":
            self._tagging = None
            return None, None
        if self.settings["vim"] and self.vim.ins is not None:
            return before, None
        if self._tagging is None:
            self._tagging = Session()
        return before, self._tagging

    def _divider_key(self, e, c):
        # a divider has no text: typing does nothing, Enter opens an item under it and Backspace or Delete takes the line away;
        # Backspace at the start of the line under one takes the divider, not the line's text
        key, block = e.key(), c.block()
        if self.mode in VIM_MODES:
            return False
        if is_divider(block) and (
            key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab)
            or (key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and e.modifiers() & CTRL)
        ):
            return True
        if c.hasSelection() or e.modifiers() & (CTRL | ALT):
            return False
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and is_divider(block):
            self.new_todo()
            return True
        gone = None
        if key in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete) and is_divider(block):
            gone = block
        elif (
            key == Qt.Key.Key_Backspace
            and c.atBlockStart()
            and is_divider(block.previous())
        ):
            gone = block.previous()
        if gone is None:
            return is_divider(block) and e.text().isprintable() and e.text() != ""
        doc, n = self.document(), gone.blockNumber()
        edit = QTextCursor(doc)
        edit.beginEditBlock()
        remove_block(doc, gone)
        edit.endEditBlock()
        if gone == block and n > 0:
            c = QTextCursor(doc.findBlockByNumber(n - 1))
            c.movePosition(MO.EndOfBlock)
        else:
            c = QTextCursor(doc.findBlockByNumber(n))
        self.setTextCursor(c)
        return True

    def insert_divider(self):
        block = self.textCursor().block()
        if is_section(block) or self.isReadOnly():
            return
        caret = self.textCursor().position()
        c = QTextCursor(unit_of(block)[-1])
        c.beginEditBlock()
        c.movePosition(MO.EndOfBlock)
        c.insertBlock(QTextBlockFormat(), QTextCharFormat())
        make_divider(c.block())
        c.endEditBlock()
        keep = QTextCursor(self.document())
        keep.setPosition(caret)
        self.setTextCursor(keep)

    def _delete_hovered(self):
        # Delete with the mouse over a chip (path link, tag, image) takes the whole chip, one undo step
        span = self._chip_hover or self._tag_hover or self._image_hover
        if span is None or self.isReadOnly() or self.textCursor().hasSelection():
            return False
        c = QTextCursor(self.document())
        c.setPosition(span[0])
        c.setPosition(span[1], QTextCursor.MoveMode.KeepAnchor)
        c.removeSelectedText()
        self._img_hide()
        self._chip_hover = self._tag_hover = self._image_hover = None
        self.viewport().update()
        return True

    def _chip_delete(self, key, word):
        # Backspace or Delete (or the word form) that reaches into a chip takes all of it
        c = self.textCursor()
        if c.hasSelection():
            return False
        back = key == Qt.Key.Key_Backspace
        c.movePosition(
            (MO.PreviousWord if back else MO.NextWord)
            if word
            else (MO.PreviousCharacter if back else MO.NextCharacter),
            QTextCursor.MoveMode.KeepAnchor,
        )
        lo, hi = c.selectionStart(), c.selectionEnd()
        s, e = chip_widen(self.document(), lo, hi)
        if (s, e) == (lo, hi):
            return False
        c.setPosition(s)
        c.setPosition(e, QTextCursor.MoveMode.KeepAnchor)
        c.removeSelectedText()
        self.setTextCursor(c)
        return True

    def _plain_edge(self):
        # text typed or pasted at a link's trailing edge is plain: the link does not grow;
        # inside a web link's label typing still extends it
        f = self.currentCharFormat()
        if not f.isAnchor():
            return
        href = f.anchorHref()
        if not is_path_link(href):
            c = QTextCursor(self.textCursor())
            c.setPosition(c.selectionEnd())
            moved = c.movePosition(QTextCursor.MoveOperation.NextCharacter)
            if (
                moved
                and c.positionInBlock() > 0
                and c.charFormat().anchorHref() == href
            ):
                return
        self._unlink_format()

    def _unlink_format(self):
        f = self.currentCharFormat()
        for prop in (
            QTextFormat.Property.IsAnchor,
            QTextFormat.Property.AnchorHref,
            QTextFormat.Property.TextUnderlineStyle,
            QTextFormat.Property.ForegroundBrush,
            rdb_model.ORIG_FG,
        ):
            f.clearProperty(prop)
        self.setCurrentCharFormat(f)

    def _task(self, block):
        if self.timeline and is_todo(block):
            start_task(block)

    def _nudged(self, cursor):
        todos = selected_todos(cursor)
        return todos or [b for b in [owner_todo(cursor.block())] if b is not None]

    def set_preview(self, todo, flag):
        if flag:
            load_preview(self.store, note_link(todo))
        c = QTextCursor(self.document())
        c.beginEditBlock()
        set_note_open(todo, flag)
        c.endEditBlock()
        self._tail_room()
        self.viewport().update()

    def note_toggle(self):
        todo = owner_todo(self.textCursor().block())
        if todo is not None and self.has_note(todo):
            self.set_preview(todo, not note_open(todo))

    def note_click(self, todo):
        self.set_preview(todo, not note_open(todo))

    def note_edit(self):
        todo = owner_todo(self.textCursor().block())
        if todo is not None:
            self.note_page(todo)

    def note_page(self, todo):
        # the note document opens as a page, made first when the item has none
        if self.note:
            return
        nt = note_link(todo)
        if not self.has_note(todo):
            nt = create_note(self.store)
            c = QTextCursor(self.document())
            c.beginEditBlock()
            set_meta(todo, nt=nt)
            c.endEditBlock()
        self.note_requested.emit(nt)

    def note_delete(self, todo):
        yes, no = QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No
        if QMessageBox.warning(self, "Delete", "Delete note?", yes | no, no) != yes:
            return
        nt = note_link(todo)
        c = QTextCursor(self.document())
        c.beginEditBlock()
        set_meta(todo, nt="", no=False)
        layout_block(todo)
        c.endEditBlock()
        trash_project(self.store, nt)
        self.viewport().update()

    def note_menu(self, pos):
        what, todo = self._hit(QPointF(pos))
        if what not in ("note", "panel"):
            return None
        menu = QMenu(self)
        menu.addAction("Open Note").triggered.connect(lambda: self.note_page(todo))
        menu.addAction("Delete Note").triggered.connect(lambda: self.note_delete(todo))
        return menu

    def contextMenuEvent(self, e):
        chip = self._image_at(QPointF(e.pos()))
        if chip is not None and not self.isReadOnly():
            self._img_hide()
            block, name = chip[:2]
            menu = QMenu(self)
            menu.addAction("Rename").triggered.connect(
                lambda: self.rename_image(block, name)
            )
            menu.addAction("Delete").triggered.connect(lambda: self._remove_image(name))
            menu.exec(e.globalPos())
            menu.deleteLater()
            return
        menu = self.note_menu(e.pos())
        if menu is None:
            super().contextMenuEvent(e)
            return
        menu.exec(e.globalPos())
        menu.deleteLater()

    def _escape(self):
        if self.settings["vim"]:
            self.vim.escape()
        elif self.settings["esc_leaves"]:
            self.leave.emit()

    def _seek(self, block, step):
        while block.isValid() and not _plain(block):
            block = block.next() if step > 0 else block.previous()
        return block

    def _goto(self, block):
        if block.isValid():
            self.setTextCursor(QTextCursor(block))

    def cut_item(self):
        if self.textCursor().hasSelection():
            self.cut()
        else:
            self.delete_item()

    def copy_item(self):
        # no selection: the item under the caret, as Ctrl+X cuts it; its note is duplicated on paste
        block = self.textCursor().block()
        if self.textCursor().hasSelection():
            self.copy()
        elif not is_section(block):
            QApplication.clipboard().setMimeData(self.item_mime(unit_of(block), False))

    def _to_clipboard(self, blocks):
        QApplication.clipboard().setMimeData(self.item_mime(blocks, True))

    def createMimeDataFromSelection(self):
        # the stock mime data fills its plain text from the fragment on every read
        frag = self.textCursor().selection()
        md = QMimeData()
        md.setHtml(fragment_html(frag))
        md.setText(fragment_plain(frag))
        a, z = self.textCursor().selectionStart(), self.textCursor().selectionEnd()
        carried = {}
        for b, pos, _, fmt in self._image_frags(a, z):
            if a <= pos < z:
                bf = b.blockFormat()
                entry = {
                    "in": image_labels(bf.stringProperty(PROPS["in"])).get(
                        fmt.name(), ""
                    ),
                    "is": image_sizes(bf.stringProperty(PROPS["is"])).get(
                        fmt.name(), ""
                    ),
                }
                if entry["in"] or entry["is"]:
                    carried[fmt.name()] = entry
        if carried:
            md.setData(IMAGE_META_MIME, json.dumps(carried).encode())
        return md

    def cut(self):
        # the image names and sizes of the cut text leave its block, in the same undo step
        c = self.textCursor()
        if not c.hasSelection():
            super().cut()
            return
        start = c.selectionStart()
        c.beginEditBlock()
        super().cut()
        self._drop_stale_image_meta(self.document().findBlock(start))
        c.endEditBlock()

    def _drop_stale_image_meta(self, block):
        here = block.position()
        drop_image_meta(
            block, {f.name() for _, _, _, f in self._image_frags(here, here)}
        )

    def item_mime(self, blocks, move):
        # move: the items are cut, so their note documents go with them; a copy duplicates them
        frag = _span_cursor(blocks).selection()
        html = fragment_html(frag)
        md = QMimeData()
        md.setHtml(html)
        md.setText(fragment_plain(frag))
        tmp = QTextDocument()
        tmp.setHtml(html)
        images = {
            name: base64.b64encode(self.store.get_image(name)).decode()
            for name in image_names(tmp)
        }
        payload = {
            "html": html,
            "meta": [get_meta(b) for b in blocks],
            "images": images,
            "markup": {n: self.store.get_markup(n) for n in images},
            "move": move,
        }
        md.setData(ITEMS_MIME, json.dumps(payload).encode())
        return md

    def _held(self, nt, ignore):
        # whether an item other than the ones being pasted keeps this note: the shown list, or
        # any other stored one
        if any(note_link(b) == nt for b in iter_blocks(self.document())):
            return True
        holders = self.store.note_holders(nt)
        return any(p != self.pid and p not in ignore for p, _ in holders)

    def paste_metas(self, payload, ignore=()):
        # meta for pasted items: a moved item keeps its note document unless another item
        # keeps it (then, like a copy, it gets a duplicate); a note page holds no notes
        out = []
        for mt in payload["meta"]:
            nt = mt.get("nt", "")
            if nt and (self.note or not self.store.has_project(nt)):
                nt = ""
            elif nt and (not payload["move"] or self._held(nt, ignore)):
                nt = duplicate_note(self.store, nt)
            # the block is laid out as soon as it has its meta, so an open preview needs its lines now
            if nt and mt.get("no"):
                load_preview(self.store, nt)
            out.append(
                paste_meta({**mt, "nt": nt, "no": bool(nt) and mt.get("no", False)})
            )
        return out

    def delete_item(self):
        doc = self.document()
        block = self.textCursor().block()
        if is_section(block):
            return
        blocks = unit_of(block)
        self._to_clipboard(blocks)
        first = blocks[0].blockNumber()
        # a lone Completed header must not outlive the last item
        if all(b in blocks or is_section(b) for b in iter_blocks(doc)):
            blocks = list(iter_blocks(doc))
        remove_item(doc, blocks)
        b = doc.findBlockByNumber(min(first, doc.blockCount() - 1))
        after = self._seek(b, 1)
        self._goto(after if after.isValid() else self._seek(b, -1))

    def move_step(self, step):
        if len(self._sel_heads()) > 1:
            self._sel_step(step)
            return
        c = self.textCursor()
        doc = self.document()
        first, last = doc.findBlock(c.selectionStart()), doc.findBlock(c.selectionEnd())
        if c.hasSelection() and _unit_head(first) != _unit_head(last):
            span = last.blockNumber() - first.blockNumber()
            head_off = c.selectionStart() - first.position()
            tail_off = c.selectionEnd() - last.position()
            forward = c.position() >= c.anchor()
            moved = move_item_step(
                doc, first, step, last, self.settings["keep_in_heading"]
            )
            if moved is None:
                return
            # the run moved whole: the selection keeps its lines and which end the caret is on
            start = moved.position() + head_off
            tail = doc.findBlockByNumber(moved.blockNumber() + span)
            end = tail.position() + min(tail_off, tail.length() - 1)
            n = QTextCursor(doc)
            n.setPosition(start if forward else end)
            n.setPosition(end if forward else start, QTextCursor.MoveMode.KeepAnchor)
            self.setTextCursor(n)
            return
        col = c.positionInBlock()
        moved = move_item_step(
            doc, c.block(), step, keep=self.settings["keep_in_heading"]
        )
        if moved is not None:
            c = QTextCursor(moved)
            c.setPosition(moved.position() + min(col, moved.length() - 1))
            self.setTextCursor(c)

    def _opens_todo(self, block):
        # a note page opens plain lines, and a todo line only beside a todo line
        return not self.note or owner_todo(block) is not None

    def open_below(self):
        self.new_todo(self._opens_todo(self.textCursor().block()))
        self._set_mode("insert")

    def open_above(self):
        block = self.textCursor().block()
        todo = owner_todo(block)
        if todo is None:
            todo = block
        c = QTextCursor(todo)
        c.beginEditBlock()
        c.insertBlock(c.blockFormat(), QTextCharFormat())
        new = c.block().previous()
        if self._opens_todo(block):
            make_todo(new)
            set_meta(new, **todo_meta(), **NEW_ITEM)
            layout_block(new)
            self._task(new)
        else:
            fresh_meta(new, False)
        self.setTextCursor(QTextCursor(new))
        self._home(new.blockNumber(), new.blockNumber(), True)
        c.endEditBlock()
        self._set_mode("insert")

    def toggle_current(self):
        todos = selected_todos(self.textCursor())
        if len(todos) > 1:
            self.toggle_items(todos)
        else:
            self.toggle_item(self.textCursor().block())

    def toggle_items(self, todos):
        if self.links:
            return
        doc, mode = self.document(), self.check_mode
        bar = self.verticalScrollBar()
        scroll = bar.value()
        edit = QTextCursor(doc)
        edit.beginEditBlock()
        try:
            ensure_ids(doc)
            # what keeps the caret's place once the items have moved: the unit after the last of
            # them, else the one above the first, else the first itself
            nxt = item_blocks(todos[-1])[-1].next()
            land = (
                nxt if nxt.isValid() and not is_section(nxt) else unit_above(todos[0])
            )
            stay = get_meta(land if land is not None else todos[0])["i"]
            toggle_checks(doc, todos, mode)
        finally:
            edit.endEditBlock()
        # in place nothing moves, and the selection stays
        if mode == "move":
            self.setTextCursor(QTextCursor(unit_block(doc, stay)))
        # moving the caret scrolls it into view, and the moved items lie far below or above
        bar.setValue(scroll)
        self.viewport().update()

    def toggle_item(self, block):
        todo = owner_todo(block)
        if self.links or todo is None:
            return
        cur = self.textCursor()
        follow = owner_todo(cur.block()) == todo
        offset, col = cur.blockNumber() - todo.blockNumber(), cur.positionInBlock()
        was = todo.blockNumber(), len(item_blocks(todo)), is_checked(todo)
        bar = self.verticalScrollBar()
        scroll = bar.value()
        mode = self.check_mode
        edit = QTextCursor(self.document())
        edit.beginEditBlock()
        try:
            moved = toggle_check(self.document(), todo, mode)
        finally:
            edit.endEditBlock()
        if follow:
            b = self._took_place(*was) if mode == "move" else None
            if b is not None:
                self.setTextCursor(QTextCursor(b))
            else:
                b = self.document().findBlockByNumber(moved.blockNumber() + offset)
                c = QTextCursor(b)
                c.setPosition(b.position() + min(col, b.length() - 1))
                self.setTextCursor(c)
        # moving the caret scrolls it into view, and the moved item lies far below or above
        bar.setValue(scroll)
        self.viewport().update()

    def _took_place(self, i, k, was_checked):
        # after an item of k blocks left block i for or from Completed: the start of the item that
        # took its place, else the previous one; None when it was the only item
        doc = self.document()
        if was_checked:
            if section_block(doc) is None:
                return None
            around = (i + k, i + k - 1)
        else:
            around = (i, i - 1)
        for n in around:
            b = doc.findBlockByNumber(n)
            if b.isValid() and not is_section(b):
                return owner_todo(b) or b
        return None

    def mark_item(self, todo, mark):
        # one undo step; in a sorted list the item also takes its place among its group
        doc = self.document()

        def apply():
            set_marker(todo, mark)
            if is_sorted(doc):
                place_item(doc, todo)

        if is_sorted(doc):
            self._keep_caret(apply)
        else:
            apply()
        self.viewport().update()
        self.marked.emit()

    def set_item_status(self, todo, key):
        # one undo step; the Done link can move the item, so the caret is kept as for a check
        doc = self.document()

        def apply():
            done = set_status(doc, todo, key, self.check_mode)
            if is_sorted(doc):
                place_item(doc, done)

        self._keep_caret(apply)
        self.viewport().update()
        self.marked.emit()

    def kanban_drop(self, uid, key, before):
        doc = self.document()

        def apply():
            done = rdb_model.kanban_drop(doc, uid, key, before, self.check_mode)
            if is_sorted(doc):
                place_item(doc, done)

        with self._settled():
            self._keep_caret(apply)
        self.viewport().update()
        self.marked.emit()

    def move_heading(self, uid, before):
        # before None: the end of the open items, above Completed
        doc = self.document()
        head = unit_block(doc, uid)
        above = section_block(doc) if before is None else unit_block(doc, before)
        with self._settled():
            self._keep_caret(lambda: move_groups(doc, [head], above))
            apply_view(doc, self.check_mode)
        self.viewport().update()
        self.marked.emit()

    def open_status(self, block):
        todo = owner_todo(block)
        rect = self.status_rect(block if todo is None else todo)
        if todo is None or rect is None:
            return
        pop = StatusPopup(self)
        pop.picked.connect(lambda key: self.set_item_status(todo, key))
        pop_at(pop, self, rect)

    def mark_current(self, mark):
        todo = owner_todo(self.textCursor().block())
        if todo is not None:
            self.mark_item(todo, mark)

    def toggle_p1(self):
        todo = owner_todo(self.textCursor().block())
        if todo is not None:
            self.mark_item(todo, "" if mark_of(get_meta(todo)) == "p1" else "p1")

    def open_marker(self, block):
        todo = owner_todo(block)
        rect = self.marker_rect(block if todo is None else todo)
        if todo is None or rect is None:
            return
        pop = MarkerPopup(self)
        pop.picked.connect(lambda mark: self.mark_item(todo, mark))
        pop_at(pop, self, rect)

    def set_sort(self, sort):
        doc = self.document()
        self.sort_changing.emit()
        self._keep_caret(lambda: (sort_open if sort else unsort_open)(doc))
        self.sort = sort
        self.sort_changed.emit(sort)

    def toggle_format(self, attr):
        # a selection, or in vim normal mode the whole item, else the typing format
        c = self.textCursor()
        if c.hasSelection() or self.mode in VIM_MODES:
            char_toggle(self._quick_target(c.block()), attr)
            return
        get, put = CHAR_ATTRS[attr]
        f = QTextCharFormat()
        put(f, not get(self.currentCharFormat()))
        self.mergeCurrentCharFormat(f)

    def _ask_link(self, label, url, pick_label):
        dlg = LinkDialog(self, label, url, pick_label)
        return (dlg.label.text(), dlg.url.text()) if dlg.exec() else None

    def _put_link(self, cursor, label, url):
        if not url.strip():
            return
        cursor.beginEditBlock()
        insert_link(cursor, label, url)
        cursor.endEditBlock()
        self.setTextCursor(cursor)
        self._unlink_format()

    def link_current(self):
        # the link under the caret is edited whole; else the selection becomes the label
        c = self.textCursor()
        doc = self.document()
        start = c.selectionStart()
        span = link_span(doc.findBlock(start), start)
        if span is not None:
            s, e, href = span
            c = QTextCursor(doc)
            c.setPosition(s)
            c.setPosition(e, QTextCursor.MoveMode.KeepAnchor)
            label = c.selectedText()
            spot = rdb_rdoc.parse_href(href)
            url = path_local(href) if is_path_link(href) and not spot else href
            label = label[len(FILE_PAD) :] if label.startswith(FILE_PAD) else label
        else:
            label = c.selectedText().replace("\u2029", " ").replace("\u2028", " ")
            url = url_text(QApplication.clipboard().text())
        got = self._ask_link(label, url, False)
        if got is not None:
            self._put_link(c, *got)

    def edit_link_at(self, pos):
        self.setTextCursor(self.cursorForPosition(pos))
        self.link_current()

    def _link_prompt(self, label, url):
        got = self._ask_link(label, url, True)
        if got is not None:
            self._put_link(self.textCursor(), *got)

    def _paste_files(self, src):
        # a Note card opens a dropped text file as its text, as Notepad++ does; others are links
        paths = [
            u.toLocalFile().replace("/", "\\") for u in src.urls() if u.isLocalFile()
        ]
        for local in paths:
            text = file_text(local)
            if text is None:
                self._link_prompt(path_label(local), local)
                continue
            md = QMimeData()
            md.setText(text)
            self.insertFromMimeData(md, keep_blanks=True)
        return bool(paths)

    def _paste_link(self, src):
        # a lone URL, local path or dropped file asks for its label first; Esc drops it
        paths = [
            u.toLocalFile().replace("/", "\\") for u in src.urls() if u.isLocalFile()
        ]
        text = src.text() if src.hasText() else ""
        if not paths and (href := pasted_path_href(text)):
            paths = [path_local(href)]
        for local in paths:
            self._link_prompt(path_label(local), local)
        if not paths and (url := url_text(text)):
            self._link_prompt(url, url)
            return True
        return bool(paths)

    def swatches_current(self):
        self.open_swatches(self.textCursor().block())

    def _quick_target(self, block):
        # the selection if any, else every block of the item the QuickMenu was opened on
        c = self.textCursor()
        if c.hasSelection():
            return c
        return _span_cursor(item_of(block))

    def set_heading(self, level, block=None):
        block = self.textCursor().block() if block is None else block
        if is_section(block):
            return
        c = QTextCursor(block)
        c.beginEditBlock()
        if level:
            make_heading(block, level)
            # a heading folds by its id; one made here has it in the same undo step
            ensure_ids(self.document())
        else:
            make_todo(block)
            self._home(block.blockNumber(), block.blockNumber())
        c.endEditBlock()
        self._sync_caret()

    def open_swatches(self, block):
        todo = owner_todo(block)
        row = block if todo is None else todo
        rects = self.gutter_rects(row)
        if rects is None:
            return
        anchor = next(
            (r for r in (rects[1], rects[0]) if r is not None),
            QRectF(self._origin().x() + 4 * self._z, self._geom(row).cy, 0, 0),
        )
        pop = SwatchPopup(
            self,
            self.settings,
            lambda: self._quick_target(block),
            self.viewport().update,
            (lambda: heading_level(block), lambda n: self.set_heading(n, block)),
            lambda: owner_todo(block) is not None,
            self.note,
            None
            if self.note
            or (owner_todo(block) is None and not self.textCursor().hasSelection())
            else lambda: self.move_requested.emit(block),
            self.timeline,
            None if self.note else self._item_ops(block),
        )
        pop.picked.connect(lambda key, colour: self._pick(block, key, colour))
        pop_at(pop, self, anchor)

    def _item_ops(self, block):
        def mark():
            todo = owner_todo(block)
            return "" if todo is None else mark_of(get_meta(todo))

        def status():
            todo = owner_todo(block)
            return "" if todo is None else status_of(todo)

        return ItemOps(
            mark,
            lambda mk: self.mark_item(owner_todo(block), mk),
            status,
            lambda key: self.set_item_status(owner_todo(block), key),
            self.tag_source or (lambda: ()),
            lambda: self.item_tags(block),
            lambda name, on: self.set_item_tag(block, name, on),
            lambda: self.link_item(block),
        )

    def item_tags(self, block):
        return {
            run_text(b, a, e)[1:].lower()
            for b in item_of(block)
            for a, e in tag_runs(b)
        }

    def set_item_tag(self, block, name, on):
        # add #name at the end of the item once, or strip every copy with a space beside it; one undo step
        doc, key = self.document(), name.lower()
        c = QTextCursor(doc)
        c.beginEditBlock()
        if on and key not in self.item_tags(block):
            last = item_of(block)[-1]
            c.setPosition(last.position() + last.length() - 1)
            f = c.charFormat()
            if f.isAnchor():
                f = QTextCharFormat()
            sep = " " if last.text() and not last.text().endswith(" ") else ""
            c.insertText(f"{sep}#{name}", f)
        elif not on:
            runs = [
                (a, e)
                for b in item_of(block)
                for a, e in tag_runs(b)
                if run_text(b, a, e)[1:].lower() == key
            ]
            for a, e in reversed(runs):
                if doc.characterAt(a - 1) == " ":
                    a -= 1
                elif doc.characterAt(e) == " ":
                    e += 1
                c.setPosition(a)
                c.setPosition(e, QTextCursor.MoveMode.KeepAnchor)
                c.removeSelectedText()
        c.endEditBlock()
        self.viewport().update()

    def link_item(self, block):
        self.setTextCursor(self._quick_target(block))
        self.link_current()

    def _pick(self, block, key, colour):
        c = self.textCursor()
        if key == "bar":
            # a task's own bar colour, apart from its label: every task a selection touches
            todos = self._nudged(c) if c.hasSelection() else [owner_todo(block)]
            c.beginEditBlock()
            for t in todos:
                set_meta(t, tb=colour)
            c.endEditBlock()
        elif c.hasSelection():
            char_color(c, key, colour)
        else:
            # the item colour is set and the pasted character colour under it removed, or it would hide it
            set_meta(owner_todo(block), **{key: colour})
            char_clear(_span_cursor(item_of(block)), key)
        self.viewport().update()

    def format_menu(self, parent, pos):
        # the Formatting submenu of the editor menu for the block under pos, None on the Completed
        # header. Each entry runs what its key or QuickMenu control runs, on the selection when the
        # click is inside it, else on the item under the click (a right click does not move the caret)
        at = self.cursorForPosition(pos)
        block, c = at.block(), self.textCursor()
        if is_section(block):
            return None
        inside = (
            c.hasSelection() and c.selectionStart() <= at.position() <= c.selectionEnd()
        )
        target = c if inside else _span_cursor(item_of(block))
        todo = owner_todo(block)
        menu = QMenu("Formatting", parent)

        def add(into, text, run, key=None, checked=None):
            act = into.addAction(text)
            if key:
                act.setShortcut(key)
            if checked is not None:
                act.setCheckable(True)
                act.setChecked(checked)
            act.triggered.connect(lambda _=False: (run(), self.viewport().update()))
            return act

        def submenu(title):
            # parented, so the wrapper going away does not delete it
            sub = QMenu(title, menu)
            menu.addMenu(sub)
            return sub

        def place():
            if not inside:
                self.setTextCursor(at)

        for attr, text, key in (
            ("bold", "Bold", "Ctrl+B"),
            ("italic", "Italic", "Ctrl+I"),
            ("underline", "Underline", "Ctrl+U"),
            ("strike", "Strikethrough", None),
        ):
            add(
                menu,
                text,
                lambda a=attr: char_toggle(target, a),
                key,
                char_has(target, attr),
            )
        add(menu, "Normalize Font", lambda: char_normalize(target))
        menu.addSeparator()
        if not self.note:
            sub = submenu("Heading")
            for n, text in enumerate(("Todo", "Heading 1", "Heading 2", "Heading 3")):
                on = heading_level(block) == n if n else is_todo(block)
                add(sub, text, lambda n=n: self.set_heading(n, block), f"Ctrl+{n}", on)
        if todo is not None:
            sub = submenu("Priority")
            cur = mark_of(get_meta(todo))
            for i, (mark, text) in enumerate(zip(MARKS, MARK_NAMES)):
                if mark == "ys":
                    sub.addSeparator()
                key = f"Alt+{i}" if i < 5 else None
                add(
                    sub,
                    text,
                    lambda mk=mark: self.mark_item(todo, mk),
                    key,
                    mark == cur,
                )
            sub = submenu("Status")
            cur = status_of(todo)
            for key, text, rec in (
                ("", "None", None),
                *((r["key"], r["name"], r) for r in rdb_model.STATUS_LIST),
            ):
                act = add(
                    sub,
                    text,
                    lambda key=key: self.set_item_status(todo, key),
                    None,
                    key == cur,
                )
                if rec is not None:
                    act.setIcon(rdb_status.status_icon(rec["icon"], rec["color"], 16))
        if self.note:
            sub = submenu("Alignment")
            cur = block_aligned(target)
            for a, text in zip(ALIGNS, ("Left", "Center", "Right", "Justify")):
                add(sub, text, lambda a=a: block_align(target, a), None, a == cur)
        menu.addSeparator()
        add(menu, "Insert Link...", lambda: (place(), self.link_current()), "Ctrl+K")
        if todo is not None:
            add(menu, "Colours...", lambda: (place(), self.open_swatches(block)))
        return menu

    def new_todo(self, todo=True):
        block = self.textCursor().block()
        owner = owner_todo(block)
        last = item_blocks(owner)[-1] if owner is not None else block
        c = QTextCursor(last)
        scroll = self.verticalScrollBar().value()
        c.beginEditBlock()
        c.movePosition(QTextCursor.MoveOperation.EndOfBlock)
        c.insertBlock(c.blockFormat(), QTextCharFormat())
        # the new block copied the margins of the item it follows, its preview's room included
        if todo:
            make_todo(c.block())
            set_meta(c.block(), **todo_meta(), **NEW_ITEM)
            layout_block(c.block())
            self._task(c.block())
        else:
            fresh_meta(c.block(), False)
        self.setTextCursor(c)
        self._home(c.blockNumber(), c.blockNumber())
        c.endEditBlock()
        self._restore_view(scroll)

    def indent(self, step):
        block = self.textCursor().block()
        if is_heading(block):
            return
        if is_todo(block):
            if step > 0 and owner_todo(block.previous()) is not None:
                c = QTextCursor(block)
                c.beginEditBlock()
                make_note(c, 1)
                c.endEditBlock()
            return
        lst = block.textList()
        new = (lst.format().indent() if lst is not None else 0) + step
        c = QTextCursor(block)
        c.beginEditBlock()
        if new < 1:
            make_todo(block)
            self._home(block.blockNumber(), block.blockNumber())
        elif new <= 6:
            set_list_level(c, new)
        c.endEditBlock()

    def toggle_highlight(self, col):
        cur = self.currentCharFormat()
        # in dark mode the brush is the mirror; the colour as written is the one to compare
        mine = QColor(cur.stringProperty(ORIG_BG) or cur.background().color())
        on = cur.background().style() != Qt.BrushStyle.NoBrush and mine.name() == col
        f = QTextCharFormat()
        if on:
            f.setBackground(QBrush(Qt.BrushStyle.NoBrush))
            f.setProperty(ORIG_BG, "")
        else:
            set_char_colour(f, "bg", col)
        self.mergeCurrentCharFormat(f)

    def set_fg(self, col):
        f = QTextCharFormat()
        set_char_colour(f, "fg", col)
        self.mergeCurrentCharFormat(f)

    def set_list_style(self, style):
        c = self.textCursor()
        block = c.block()
        if is_todo(block) or is_heading(block):
            return
        lst = block.textList()
        level = lst.format().indent() if lst else 1
        style = style or bullet_for(level)
        if lst is None:
            set_list_level(c, 1, style)
            if not get_meta(block)["c"]:
                set_meta(block, c=today())
            return
        f = lst.format()
        f.setStyle(style)
        lst.setFormat(f)

    def canInsertFromMimeData(self, src):
        return (
            src.hasImage()
            or any(u.isLocalFile() for u in src.urls())
            or super().canInsertFromMimeData(src)
        )

    def insertFromMimeData(self, src, keep_blanks=False):
        here = self.textCursor().block()
        if is_section(here) or (is_divider(here) and not src.hasFormat(ITEMS_MIME)):
            return
        if src.hasImage():
            self._paste_image(src)
            return
        if src.hasFormat(ITEMS_MIME):
            self._paste_items(src)
            return
        carried = (
            json.loads(bytes(src.data(IMAGE_META_MIME)).decode())
            if src.hasFormat(IMAGE_META_MIME)
            else {}
        )
        if not self.text_page:
            local = [Path(u.toLocalFile()) for u in src.urls() if u.isLocalFile()]
            pics = [f for f in local if f.suffix.lower() in IMAGE_FILES]
            for f in pics:
                self._paste_image_file(f)
            if pics:
                rest = [f for f in local if f not in pics]
                if not rest:
                    return
                src = QMimeData()
                src.setUrls([QUrl.fromLocalFile(str(f)) for f in rest])
        if self.text_page and self._paste_files(src):
            return
        if self._paste_link(src):
            return
        from_word = src.hasHtml() and "mso-list" in src.html()
        as_text = _text_source(src)
        src = (
            _text_mime(src, not keep_blanks, self.textCursor())
            if as_text
            else _flat_mime(src)
        )
        doc = self.document()
        c = self.textCursor()
        first = c.selectionStart()
        orig = get_meta(doc.findBlock(first))
        self._plain_edge()
        c.beginEditBlock()
        super().insertFromMimeData(src)
        pasted = QTextCursor(doc)
        pasted.setPosition(first)
        pasted.setPosition(
            self.textCursor().position(), QTextCursor.MoveMode.KeepAnchor
        )
        if as_text:
            # the highlighter draws each level's style; the caret's format must not spread
            pasted.setCharFormat(QTextCharFormat())
        else:
            char_normalize(pasted)
        last = self.textCursor().blockNumber()
        self._drop_unknown_images(first, last)
        renamed = self._unshare_images(first, last)
        if not keep_blanks:
            self._drop_blank_lines(doc.findBlock(first).blockNumber())
        last = self.textCursor().blockNumber()
        b = doc.findBlock(first)
        # pasting into an empty block replaces its whole format, meta included
        if orig["k"] == TODO and not get_meta(b)["c"]:
            set_meta(b, **orig)
        while b.isValid() and b.blockNumber() <= last:
            if from_word and (hit := WORD_MARKER.match(b.text())):
                cut = QTextCursor(b)
                cut.setPosition(
                    b.position() + hit.end(), QTextCursor.MoveMode.KeepAnchor
                )
                cut.removeSelectedText()
            self._drop_page_colours(b)
            # pasted blocks arrive without the gutter margin and without item meta
            layout_block(b)
            if not get_meta(b)["c"]:
                fresh_meta(b, not self.note)
                self._task(b)
            # a fully struck line is a done item: checked, and the strike becomes the overlay
            if is_todo(b) and all_struck(b):
                clear_strike(b)
                set_meta(b, d=today())
            # a uniformly highlighted line takes the highlight as its item colour
            if is_todo(b) and (bg := uniform_bg(b)):
                char_clear(_span_cursor([b]), "bg")
                set_meta(b, bg=bg)
            b = b.next()
        self._carry_image_meta(carried, renamed, first, self.textCursor().position())
        mirror_range(doc, first, self.textCursor().position())
        self._home(doc.findBlock(first).blockNumber(), last)
        linked = self._autolink_range(first, self.textCursor().position())
        c.endEditBlock()
        if linked and self.currentCharFormat().isAnchor():
            self._unlink_format()

    def _carry_image_meta(self, carried, renamed, first, end):
        # the pasted images keep the names and sizes they had; renamed: new image name -> old
        for b, pos, _, fmt in self._image_frags(first, end):
            entry = carried.get(renamed.get(fmt.name(), fmt.name()))
            if entry and pos < end:
                if entry["in"]:
                    set_image_label(b, fmt.name(), entry["in"])
                if entry["is"]:
                    set_image_size(b, fmt.name(), entry["is"])

    @staticmethod
    def _drop_page_colours(block):
        # a block background is the source page's theme (Chromium copies computed styles);
        # a text box copy carries its theme on the spans alone. A dark background anywhere
        # means a dark theme, whose text colours (even transparent) and backgrounds all go
        bf = block.blockFormat()
        brushes = [bf.background()]
        if brushes[0].style() != Qt.BrushStyle.NoBrush:
            bf.clearProperty(QTextFormat.Property.BackgroundBrush)
            QTextCursor(block).setBlockFormat(bf)
        it = block.begin()
        while not it.atEnd():
            brushes.append(it.fragment().charFormat().background())
            it += 1
        if any(
            b.style() != Qt.BrushStyle.NoBrush and b.color().lightness() < 128
            for b in brushes
        ):
            for key in CHAR_COLORS:
                char_clear(_span_cursor([block]), key)

    def _drop_blank_lines(self, top):
        # a pasted list's empty and space-only lines would each become an item; the cursor's
        # own line is the rest of the target line and stays
        doc, end = self.document(), self.textCursor().blockNumber()
        for n in range(end - 1, top - 1, -1):
            b = doc.findBlockByNumber(n)
            if b.text().strip():
                continue
            c = QTextCursor(b)
            if n:
                c.setPosition(b.position() - 1)
                c.setPosition(
                    b.position() + b.length() - 1, QTextCursor.MoveMode.KeepAnchor
                )
            else:
                c.movePosition(
                    QTextCursor.MoveOperation.NextBlock, QTextCursor.MoveMode.KeepAnchor
                )
            c.removeSelectedText()

    def _paste_items(self, src, ignore=()):
        payload = json.loads(bytes(src.data(ITEMS_MIME)).decode())
        meta = payload["meta"]
        tmp = QTextDocument()
        tmp.setHtml(payload["html"])
        self._adopt_images(tmp, payload["images"], meta, payload["markup"])
        if tmp.blockCount() != len(meta):
            raise ValueError(f"{tmp.blockCount()} pasted blocks, {len(meta)} meta rows")
        doc = self.document()
        block = self.textCursor().block()
        last = unit_of(block)[-1]
        first, count = last.blockNumber() + 1, doc.blockCount()
        c = QTextCursor(last)
        c.beginEditBlock()
        try:
            c.movePosition(MO.EndOfBlock)
            c.insertBlock(QTextBlockFormat(), QTextCharFormat())
            c.insertFragment(QTextDocumentFragment(tmp))
            if doc.blockCount() != count + len(meta):
                raise RuntimeError(
                    f"pasted {doc.blockCount() - count} blocks, expected {len(meta)}"
                )
            for i, mt in enumerate(self.paste_metas(payload, ignore)):
                b = doc.findBlockByNumber(first + i)
                set_meta(b, **mt)
                layout_block(b)
            tail = doc.findBlockByNumber(first + len(meta) - 1)
            mirror_range(
                doc,
                doc.findBlockByNumber(first).position(),
                tail.position() + tail.length(),
            )
            # a pasted heading folds at once, with no id still to add in an undo step of its own
            ensure_ids(doc, (HEADING,))
            if image_names(tmp):
                attach_images(doc, self.store)
                self.fit_images(force=True)
            if self.timeline:
                ensure_task_ids(doc)
            end = QTextCursor(doc.findBlockByNumber(first + len(meta) - 1))
            end.movePosition(MO.EndOfBlock)
            self.setTextCursor(end)
            self._home(first, first + len(meta) - 1)
        finally:
            c.endEditBlock()

    def _new_image_name(self, ext):
        taken = self.store.image_names()
        n = 1
        while f"img/{self.pid}_{n}{ext}" in taken:
            n += 1
        return f"img/{self.pid}_{n}{ext}"

    def _adopt_images(self, tmp, images, meta, markup):
        # images travel with a cut item; a name already used for other bytes, or already on this
        # page, gets a new name: a size belongs to a name, so each image on a page has its own
        rename = {}
        taken = self.store.image_names()
        on_page = set(image_names(self.document()))
        for name, b64 in images.items():
            data = base64.b64decode(b64)
            if (
                name in taken
                and name not in on_page
                and self.store.get_image(name) == data
            ):
                continue
            if name in taken:
                rename[name] = self._new_image_name(Path(name).suffix)
            self.store.put_image(rename.get(name, name), data)
            self.store.put_markup(rename.get(name, name), markup.get(name, []))
            self._forget_image(rename.get(name, name))
        rename_image_sizes(meta, rename)
        for b in iter_blocks(tmp):
            it = b.begin()
            while not it.atEnd():
                fr = it.fragment()
                it += 1
                f = fr.charFormat()
                if f.isImageFormat() and f.toImageFormat().name() in rename:
                    img = f.toImageFormat()
                    img.setName(rename[img.name()])
                    c = QTextCursor(tmp)
                    c.setPosition(fr.position())
                    c.setPosition(
                        fr.position() + fr.length(), QTextCursor.MoveMode.KeepAnchor
                    )
                    c.setCharFormat(img)

    def _drop_unknown_images(self, first_pos, last_block):
        # an image the file does not hold (a web <img>, a data url) would make the file unopenable
        doc = self.document()
        known = self.store.image_names()
        spans = []
        b = doc.findBlock(first_pos)
        while b.isValid() and b.blockNumber() <= last_block:
            it = b.begin()
            while not it.atEnd():
                fr = it.fragment()
                f = fr.charFormat()
                if f.isImageFormat() and f.toImageFormat().name() not in known:
                    spans.append((fr.position(), fr.length()))
                it += 1
            b = b.next()
        for pos, length in reversed(spans):
            c = QTextCursor(doc)
            c.setPosition(pos)
            c.setPosition(pos + length, QTextCursor.MoveMode.KeepAnchor)
            c.removeSelectedText()

    def _unshare_images(self, first_pos, last_block):
        # a pasted copy of an image already on the page gets its own name: a size belongs to a name
        doc = self.document()
        first = doc.findBlock(first_pos).blockNumber()
        frags = list(self._image_frags(0, doc.characterCount()))
        inside = lambda b: first <= b.blockNumber() <= last_block  # noqa: E731
        seen = {f.name() for b, _, _, f in frags if not inside(b)}
        copies, renamed = [], {}
        for b, pos, length, fmt in frags:
            if inside(b) and fmt.name() in seen:
                copies.append((pos, length, fmt))
            seen.add(fmt.name())
        for pos, length, fmt in copies:
            new = self._new_image_name(Path(fmt.name()).suffix)
            self.store.put_image(new, self.store.get_image(fmt.name()))
            self.store.put_markup(new, self.store.get_markup(fmt.name()))
            renamed[new] = fmt.name()
            fmt.setName(new)
            c = QTextCursor(doc)
            c.setPosition(pos)
            c.setPosition(pos + length, QTextCursor.MoveMode.KeepAnchor)
            c.setCharFormat(fmt)
        if copies:
            self.fit_images()
        return renamed

    def _paste_image(self, src):
        data = src.imageData()
        img = data if isinstance(data, QImage) else data.toImage()
        name = self._new_image_name(".png")
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        if not img.save(buf, "PNG"):
            raise OSError(f"cannot encode pasted image {name}")
        self._insert_image(name, bytes(ba), img, None)

    def _paste_image_file(self, path):
        data = path.read_bytes()
        img = QImage.fromData(data)
        if img.isNull():
            raise ValueError(f"{path} cannot be decoded as an image")
        name = self._new_image_name(path.suffix.lower())
        self._insert_image(name, data, img, path.stem)

    def _insert_image(self, name, data, img, label):
        # label None: a list asks for the chip name, a note page needs none
        self.store.put_image(name, data)
        self.document().addResource(
            QTextDocument.ResourceType.ImageResource, QUrl(name), img
        )
        self._forget_image(name)
        c = self.textCursor()
        if label is None:
            label = ""
            if not self.text_page:
                todo = owner_todo(c.block()) or c.block()
                default = f"image {len(item_images(todo)) + 1}"
                text, ok = self._ask_image_name(default)
                label = text.strip() if ok else default
        c.beginEditBlock()
        c.insertImage(name)
        if label:
            set_image_label(c.block(), name, label)
        c.endEditBlock()
        self.fit_images()

    def _forget_image(self, name):
        self._originals.pop(name, None)
        self._shown_imgs.pop(name, None)
        self._fit.pop(name, None)

    def _shown(self, name):
        # the picture as drawn anywhere but on a chip: with its markup
        if name not in self._shown_imgs:
            self._shown_imgs[name] = rdb_markup.marked(
                self._original(name), self.store.get_markup(name)
            )
        return self._shown_imgs[name]

    def save_markup(self, name, strokes):
        self.store.put_markup(name, strokes)
        self._forget_image(name)
        self._img_hide()
        self.fit_images()

    def _original(self, name):
        if name not in self._originals:
            img = QImage.fromData(self.store.get_image(name))
            if img.isNull():
                raise ValueError(f"image {name} cannot be decoded")
            self._originals[name] = img
        return self._originals[name]

    def _image_frags(self, first, last):
        # (block, position, length, format) of the images in the blocks first..last
        doc = self.document()
        b = doc.findBlock(first)
        while b.isValid() and b.position() <= last:
            it = b.begin()
            while not it.atEnd():
                fr = it.fragment()
                it += 1
                f = fr.charFormat()
                if f.isImageFormat():
                    yield b, fr.position(), fr.length(), f.toImageFormat()
            b = b.next()

    def _fit_check(self, pos, removed, added):
        if added and any(self._image_frags(pos, pos + added)):
            self._fit_timer.start()

    def _page_width(self):
        # the layout width an image may fill, in unzoomed px. The vertical scrollbar counts as
        # always shown: a width that follows it would flip it on and off for ever near its threshold
        doc, z = self.document(), self._z
        wide = self.viewport().width()
        bar = self.verticalScrollBar()
        if (
            self.verticalScrollBarPolicy() != Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            and not bar.isVisible()
        ):
            wide -= bar.sizeHint().width()
        fmt = doc.rootFrame().frameFormat()
        return wide / z - fmt.leftMargin() - fmt.rightMargin()

    def _chip_font(self, z=1.0):
        # the font of a link chip's label
        st = self.settings
        font = QFont(st["chip_font"])
        font.setPointSizeF(st["chip_size"] * z)
        font.setWeight(rdb_model.chip_weight(st))
        return font

    def _chip_text(self, label):
        return QFontMetricsF(self._chip_font()).elidedText(
            label, Qt.TextElideMode.ElideRight, CHIP_TEXT_MAX
        )

    def _chip_image(self, label):
        # nothing to see: the chip is painted over it (_paint_image_chip) and this only gives the
        # line the chip's width and a text line's height
        st = self.settings
        wide = (
            chip_room(st)
            + QFontMetricsF(self._chip_font()).horizontalAdvance(self._chip_text(label))
            + st["chip_pad"]
        )
        img = QImage(
            math.ceil(wide),
            QFontMetrics(self.font()).lineSpacing(),
            QImage.Format.Format_ARGB32,
        )
        img.fill(0)
        return img

    def _paint_image_chip(self, p, block, fr, lay, org, z):
        # through the code of a link chip: the same frame, the glyph where a link chip has its own,
        # the label in the chip font
        name = fr.charFormat().toImageFormat().name()
        a = _lay_i(lay, fr.position() - block.position())
        ln = lay.lineForTextPosition(a)
        x0, x1 = ln.cursorToX(a)[0], ln.cursorToX(a + 1)[0]
        st = self.settings
        v = (st["chip_vpad"] - 2) * z
        r = QRectF(org.x() + x0, org.y() + ln.y() - v, x1 - x0, ln.height() + 2 * v)
        _paint_chip(p, r, st, False, z, self._hover_at == ("image", name))
        side = st["chip_icon"] * z
        paint_picture_icon(
            p,
            QRectF(r.left() + glyph_in(st) * z, r.center().y() - side / 2, side, side),
        )
        p.setFont(self._chip_font(z))
        p.setPen(_shown(st, "chip_fg"))
        p.drawText(
            QPointF(r.left() + chip_room(st) * z, org.y() + ln.y() + ln.ascent()),
            self._chip_text(image_label(block, name)),
        )

    def _chip_images(self, doc, chips):
        # a list shows each image as a chip with its name: a resource drawn in the chip style
        st = self.settings
        for name, (block, spans) in chips.items():
            label = image_label(block, name)
            key = (
                label,
                *(st[k] for k in CHIP_LOOK),
                QFontMetricsF(self.font()).lineSpacing(),
            )
            if self._fit.get(name) == key:
                continue
            doc.addResource(
                QTextDocument.ResourceType.ImageResource,
                QUrl(name),
                self._chip_image(label),
            )
            self._fit[name] = key
            for pos, length in spans:
                doc.markContentsDirty(pos, length)

    def fit_images(self, force=False):
        # display only: the document gets a resource scaled to the page; stored bytes and html stay
        doc = self.document()
        if force:
            self._fit = {}
        z = self._z
        wide = self._page_width()
        targets, spans, chips = {}, {}, {}
        for b, pos, length, fmt in self._image_frags(0, doc.characterCount()):
            if fmt.hasProperty(QTextFormat.Property.ImageWidth) or fmt.hasProperty(
                QTextFormat.Property.ImageHeight
            ):
                continue
            name, bf = fmt.name(), b.blockFormat()
            if not self.text_page:
                chips.setdefault(name, (b, []))[1].append((pos, length))
                continue
            size = image_sizes(bf.stringProperty(PROPS["is"])).get(name, "l")
            page = wide - bf.leftMargin() - bf.rightMargin()
            page -= bf.indent() * doc.indentWidth()
            img = self._original(name)
            native = img.width() / img.devicePixelRatio()
            want = max(1, int(min(native, SIZE_SHARE[size] * page)))
            targets[name] = min(targets.get(name, want), want)
            spans.setdefault(name, []).append((pos, length))
        if not self.text_page:
            self._chip_images(doc, chips)
            return
        for name, want in targets.items():
            key = (want, round(z * 100))
            if self._fit.get(name) == key:
                continue
            img = self._shown(name)
            native = img.width() / img.devicePixelRatio()
            if want < native or z != 1:
                px = min(
                    img.width(), max(1, round(want * z * self.devicePixelRatioF()))
                )
                img = img.scaledToWidth(px, Qt.TransformationMode.SmoothTransformation)
                img.setDevicePixelRatio(px / want)
            doc.addResource(QTextDocument.ResourceType.ImageResource, QUrl(name), img)
            self._fit[name] = key
            for pos, length in spans[name]:
                doc.markContentsDirty(pos, length)

    def _image_at_caret(self):
        c = self.textCursor()
        if c.hasSelection():
            lo, hi = c.selectionStart(), c.selectionEnd()
        else:
            lo, hi = max(c.position() - 1, 0), c.position() + 1
        for b, pos, length, fmt in self._image_frags(lo, hi):
            if pos < hi and pos + length > lo:
                return b, fmt.name()
        return None

    def image_menu(self):
        hit = self._image_at_caret()
        if hit is None or self.isReadOnly() or not self.text_page:
            return False
        block, name = hit
        cur = image_sizes(block.blockFormat().stringProperty(PROPS["is"])).get(
            name, "l"
        )
        menu = QMenu(self)
        acts = {}
        for key, label in (("s", "Small"), ("m", "Medium"), ("l", "Large")):
            a = menu.addAction(label)
            a.setCheckable(True)
            a.setChecked(key == cur)
            acts[a] = key
        pick = menu.exec(self.viewport().mapToGlobal(self.cursorRect().bottomLeft()))
        if pick is not None and acts[pick] != cur:
            set_image_size(block, name, acts[pick])
            self.fit_images()
        return True


class BevelToggle(QAbstractButton):
    # a classic Windows push button that stays sunken while checked
    def __init__(self, text):
        super().__init__()
        self.setText(text)
        self.setCheckable(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def sizeHint(self):
        return QSize(self.fontMetrics().horizontalAdvance(self.text()) + 18, 20)

    def paintEvent(self, e):
        p = QPainter(self)
        down = self.isChecked() or self.isDown()
        bevel_face(p, self.width(), self.height(), down)
        p.setPen(rdb_theme.color("bar_ink"))
        p.drawText(
            self.rect().translated(int(down), int(down)),
            Qt.AlignmentFlag.AlignCenter,
            self.text(),
        )


class EyeButton(QAbstractButton):
    # open eye: checked items stay in place; struck through: they gather under Completed
    def __init__(self):
        super().__init__()
        self.section = False
        self.ink = INK
        self.setFixedSize(16, 16)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        eye = QPainterPath(QPointF(1, 8))
        eye.cubicTo(4, 2.5, 12, 2.5, 15, 8)
        eye.cubicTo(12, 13.5, 4, 13.5, 1, 8)
        p.setPen(QPen(self.ink, 1.3))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(eye)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self.ink)
        p.drawEllipse(QPointF(8, 8), 2.4, 2.4)
        if self.section:
            p.setPen(
                QPen(self.ink, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            )
            p.drawLine(QPointF(2.5, 13.5), QPointF(13.5, 2.5))


class FoldAllButton(QAbstractButton):
    # the editor's fold box: plus when a click expands every heading, minus when it collapses them
    def __init__(self):
        super().__init__()
        self.plus = False
        self.ink = INK
        self.setFixedSize(16, 16)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def paintEvent(self, e):
        _paint_fold_box(QPainter(self), QRectF(1, 1, 13, 13), self.ink, self.plus)


class SortButton(QAbstractButton):
    # three bars shortening downwards: checked, the open items are kept in priority order
    def __init__(self):
        super().__init__()
        self.setCheckable(True)
        self.ink = INK
        self.setFixedSize(16, 16)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def paintEvent(self, e):
        p = QPainter(self)
        if self.isChecked():
            p.setPen(QPen(rdb_theme.color("on_line"), 1))
            p.setBrush(rdb_theme.color("on_fill_sort"))
            p.drawRoundedRect(QRectF(0.5, 0.5, 15, 15), 3, 3)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self.ink)
        for y, w in ((3, 11), (7, 8), (11, 5)):
            p.drawRect(QRectF(2.5, y, w, 2))
        p.drawPolygon(QPolygonF([QPointF(12, 8), QPointF(15, 8), QPointF(13.5, 12)]))


class KanbanButton(QAbstractButton):
    # three columns of cards: checked, the list shows as a board
    def __init__(self):
        super().__init__()
        self.setCheckable(True)
        self.ink = INK
        self.setFixedSize(16, 16)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def paintEvent(self, e):
        p = QPainter(self)
        if self.isChecked():
            p.setPen(QPen(rdb_theme.color("on_line"), 1))
            p.setBrush(rdb_theme.color("on_fill_sort"))
            p.drawRoundedRect(QRectF(0.5, 0.5, 15, 15), 3, 3)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self.ink)
        for x, h in ((2.5, 10), (6.5, 7), (10.5, 4)):
            p.drawRect(QRectF(x, 3, 3, h))


class EditorPage(QWidget):
    view_changed = Signal(str)
    kanban_changed = Signal(bool)
    kanban_done_changed = Signal(bool)
    widths_changed = Signal(dict)
    kanban_status_only_changed = Signal(bool)
    order_changed = Signal(list)
    statuses_edited = Signal(list)
    done_changed = Signal(str)
    sort_changed = Signal(str)
    fields_toggled = Signal(bool)
    wrap_toggled = Signal(bool)
    lang_changed = Signal(str)

    def __init__(self, store, settings):
        super().__init__()
        self.settings = settings
        self._state = ""
        self._bg = self._fg = ""
        self._drawn = False
        self.editor = Editor(store, settings)
        self.editor.setFrameShape(QFrame.Shape.NoFrame)
        self.grid = LinksGrid(settings)
        self.body = QStackedWidget()
        self.body.addWidget(self.editor)
        self.body.addWidget(self.grid)
        self.tl = TimelineBody(self.editor, settings)
        self.body.addWidget(self.tl)
        self.cards_btn = BevelToggle("Cards")
        self.cards_btn.hide()
        self.cards_btn.clicked.connect(self.toggle_view)
        self.png_btn = BevelToggle("PNG")
        self.png_btn.setCheckable(False)
        self.png_btn.hide()
        self.png_btn.clicked.connect(self.save_png)
        self.fields_btn = BevelToggle("Fields")
        self.fields_btn.hide()
        self.fields_btn.clicked.connect(self.fields_toggled)
        self.eye = EyeButton()
        self.eye.clicked.connect(self.toggle_done)
        # rdb_kanban reaches this module for the marker and fold painters
        from rdb_kanban import KanbanBoard

        self.board = KanbanBoard(self.editor)
        self.body.addWidget(self.board)
        self.board.leave.connect(self._leave_board)
        self.board.widths_changed.connect(self.widths_changed)
        self.board.order_changed.connect(self.order_changed)
        self.board.statuses_edited.connect(self.statuses_edited)
        self.kanban = False
        self.kanban_status_btn = BevelToggle("Status Only")
        self.kanban_status_btn.hide()
        self.kanban_status_btn.clicked.connect(self._kanban_status_clicked)
        self.kanban_done_btn = BevelToggle("Completed")
        self.kanban_done_btn.setChecked(True)
        self.kanban_done_btn.hide()
        self.kanban_done_btn.clicked.connect(self._kanban_done_clicked)
        self.kanban_btn = KanbanButton()
        self.kanban_btn.setToolTip("Kanban View")
        self.kanban_btn.hide()
        self.kanban_btn.clicked.connect(self.toggle_kanban)
        self.sort_btn = SortButton()
        self.sort_btn.setToolTip("Sort by Priority")
        self.sort_btn.clicked.connect(self.toggle_sort)
        self.fold_btn = FoldAllButton()
        self.fold_btn.hide()
        self.fold_btn.clicked.connect(self.editor.toggle_all_folds)
        self.editor.laid_out.connect(self._show_fold_btn)
        for seq, fn in (
            ("Ctrl+Shift+V", self.toggle_view),
            ("Ctrl+Shift+D", self.toggle_done),
            ("Ctrl+Shift+K", self.toggle_kanban),
            ("Ctrl+Shift+C", self.copy_emf),
        ):
            QShortcut(
                QKeySequence(seq),
                self,
                context=Qt.ShortcutContext.WidgetWithChildrenShortcut,
            ).activated.connect(fn)
        self.editor.mode_changed.connect(self._render_state)
        self.editor.status_changed.connect(self._render_state)
        self.editor.done_changed.connect(self._done_changed)
        self.editor.sort_changed.connect(self._sort_changed)
        # rdb_findbar reaches this module through rdb_finder
        from rdb_notebar import NoteBar

        self.notebar = NoteBar(self.editor)
        self.notebar.wrap_toggled.connect(self.wrap_toggled)
        self.notebar.lang_changed.connect(self.lang_changed)
        self.header = QFrame()
        self.header.setObjectName("header")
        self.title = QLabel()
        # only the weight is pinned, so family and size keep following the application font
        bold = QFont()
        bold.setBold(True)
        self.title.setFont(bold)
        # a note page shows the item it belongs to after "Note"
        self.context = QLabel()
        self.context.hide()
        self.state = QLabel()
        row = QHBoxLayout(self.header)
        row.setContentsMargins(8, 3, 8, 3)
        row.addWidget(self.title)
        row.addWidget(self.context)
        row.addStretch(1)
        row.addWidget(self.cards_btn)
        row.addWidget(self.fields_btn)
        row.addWidget(self.png_btn)
        row.addWidget(self.kanban_status_btn)
        row.addWidget(self.kanban_done_btn)
        row.addWidget(self.kanban_btn)
        row.addWidget(self.sort_btn)
        row.addWidget(self.eye)
        row.addWidget(self.fold_btn)
        row.addWidget(self.state)
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        col.addWidget(self.header)
        col.addWidget(self.notebar)
        col.addWidget(self.body)
        self.refresh()
        rdb_theme.follow(self, EditorPage._style, now=False)

    def refresh(self):
        self._style()
        self.fields_btn.setChecked(self.settings["tl_fields"])
        self.editor.refresh()
        self.tl.sync()

    def _style(self):
        # the strip wears the card's colours, as the map draws them
        tone = QColor if self._drawn else _shade
        rdb_theme.strip_sheet(
            self.header,
            tone(self._bg).name()
            if self._bg
            else _shown(self.settings, "card_bg").name(),
        )
        ink = tone(self._fg or INK)
        for label in (self.title, self.state, self.context):
            pal = label.palette()
            pal.setColor(
                QPalette.ColorRole.WindowText,
                _shade("#707070") if label is self.context else ink,
            )
            label.setPalette(pal)
        self.eye.ink = self.sort_btn.ink = self.fold_btn.ink = ink
        self.kanban_btn.ink = ink
        self._show_eye()
        self.kanban_btn.update()
        self.sort_btn.update()
        self.fold_btn.update()

    def set_colors(self, bg, fg, drawn=False):
        # drawn: the colours are already as shown, not a card's stored ones
        self._bg, self._fg, self._drawn = bg, fg, drawn
        self._style()

    def set_title(self, text):
        self.title.setText(text)

    def set_links(self, on, view, timeline=False):
        self.editor.links = on
        self.editor.note = False
        self.notebar.hide()
        self.context.hide()
        self.cards_btn.setVisible(on)
        self.eye.setVisible(not (on or timeline))
        self.kanban_btn.setVisible(not (on or timeline or self.editor.pinned))
        self.sort_btn.setVisible(not timeline)
        self.set_timeline(timeline)
        self.show_view(view if on else "list")

    def set_notepad(self, lang):
        self.editor.note = True
        self.notebar.sync_lang(lang)
        self.notebar.sync_wrap(self.settings["notepad_wrap"])
        self.notebar.sync()
        self.notebar.show()
        self.eye.hide()
        self.kanban_btn.hide()
        self.sort_btn.hide()

    def set_timeline(self, on):
        self.editor.timeline = on
        self.png_btn.setVisible(on)
        self.fields_btn.setVisible(on)
        self.fields_btn.setChecked(self.settings["tl_fields"])
        if on:
            self.kanban_btn.hide()
            self.tl.attach()
            # a reparented widget stays hidden until shown, and a hidden editor takes no focus
            self.editor.show()
            self._seed_task()
        elif self.tl.holds_editor():
            self.body.insertWidget(0, self.editor)

    def _seed_task(self):
        # the blank line of a new timeline is its first task; a fresh document has nothing to undo
        doc = self.editor.document()
        first = doc.begin()
        if (
            doc.blockCount() == 1
            and is_todo(first)
            and not first.text()
            and task_start(first) is None
        ):
            doc.setUndoRedoEnabled(False)
            start_task(first)
            doc.setUndoRedoEnabled(True)
            doc.setModified(False)

    def set_note(self, text):
        self.editor.links = False
        self.editor.note = True
        self.title.setText("Note")
        self.notebar.hide()
        self.context.setText(text)
        self.context.show()
        self.cards_btn.hide()
        self.eye.hide()
        self.kanban_btn.hide()
        self.sort_btn.hide()
        self.set_timeline(False)
        self.show_view("list")

    def set_done(self, done):
        self.editor.done = done
        self._show_eye()

    def set_sort(self, sort):
        self.editor.sort = sort
        self.sort_btn.setChecked(bool(sort))

    def toggle_sort(self, *_):
        if not (self.editor.note or self.editor.pinned):
            self.editor.set_sort("" if self.editor.sort else "prio")

    def _sort_changed(self, sort):
        self.sort_btn.setChecked(bool(sort))
        self.sort_changed.emit(sort)

    def toggle_done(self, *_):
        if not (
            self.editor.links
            or self.editor.note
            or self.editor.timeline
            or self.editor.pinned
        ):
            self.editor.set_done(
                "place" if self.editor.check_mode == "move" else "section"
            )

    def _show_eye(self):
        self.eye.section = self.editor.check_mode == "move"
        self.eye.setToolTip(
            "Keep Checked in Place" if self.eye.section else "Move Checked to Completed"
        )
        self.eye.update()

    def _show_fold_btn(self):
        self.fold_btn.setVisible(self.editor.has_folds())
        self.fold_btn.plus = not self.editor.folds_open()
        self.fold_btn.setToolTip("Expand All" if self.fold_btn.plus else "Collapse All")
        self.fold_btn.update()

    def _done_changed(self, done):
        self._show_eye()
        self.done_changed.emit(done)

    def show_view(self, view):
        cards = view == "cards"
        timeline = self.editor.timeline
        board = self.kanban and not (cards or timeline)
        self.body.setCurrentWidget(
            self.tl
            if timeline
            else self.grid
            if cards
            else self.board
            if board
            else self.editor
        )
        self.cards_btn.setChecked(cards)
        self.kanban_done_btn.setVisible(board)
        self.kanban_status_btn.setVisible(board)
        (self.editor if timeline else self.body.currentWidget()).setFocus()

    def _kanban_done_clicked(self, on):
        self.board.set_done(on)
        self.kanban_done_changed.emit(on)

    def set_kanban_done(self, on):
        self.kanban_done_btn.setChecked(bool(on))
        self.board.set_done(bool(on))

    def set_kanban_widths(self, widths):
        self.board.set_widths(widths)

    def _kanban_status_clicked(self, on):
        self.board.set_status_only(on)
        self.kanban_status_only_changed.emit(on)

    def set_kanban_status_only(self, on):
        self.kanban_status_btn.setChecked(bool(on))
        self.board.set_status_only(bool(on))

    def set_kanban_order(self, order):
        self.board.set_order(order)

    def set_kanban(self, on):
        self.kanban = bool(on)
        self.kanban_btn.setChecked(self.kanban)
        self.show_view("cards" if self.cards_shown else "list")

    def _leave_board(self, uid):
        block = unit_block(self.editor.document(), uid)
        self.editor.setTextCursor(QTextCursor(fold_owner(block) or block))
        self.set_kanban(False)
        self.kanban_changed.emit(False)
        self.editor.ensureCursorVisible()

    def toggle_kanban(self, *_):
        ed = self.editor
        if not (ed.links or ed.note or ed.timeline or ed.pinned):
            self.set_kanban(not self.kanban)
            self.kanban_changed.emit(self.kanban)
        else:
            self.kanban_btn.setChecked(self.kanban)

    def save_png(self, *_):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export", f"{self.title.text()}.png", "PNG (*.png)"
        )
        if path:
            self.tl.save_png(path)

    def copy_emf(self, *_):
        if not self.editor.timeline:
            return
        inkscape = shutil.which("inkscape")
        if inkscape is None:
            QMessageBox.warning(self, "Copy EMF", "inkscape not found")
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self.tl.copy_emf(inkscape)
        finally:
            QApplication.restoreOverrideCursor()

    @property
    def cards_shown(self):
        return self.body.currentWidget() is self.grid

    def toggle_view(self, *_):
        if self.editor.links:
            view = "list" if self.cards_shown else "cards"
            self.show_view(view)
            self.view_changed.emit(view)

    def set_state(self, text):
        self._state = text
        self._render_state()

    def _render_state(self, *_):
        tag = self.editor.vim.label()
        self.state.setText(f"{self._state}  {tag}" if tag else self._state)
