import datetime
import itertools
import math
import re
import time
from collections import namedtuple
from functools import lru_cache, partial

from PySide6.QtCore import (
    QEvent,
    QMimeData,
    QPointF,
    QRect,
    QRectF,
    QSize,
    QSizeF,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QAbstractTextDocumentLayout,
    QBrush,
    QColor,
    QCursor,
    QFont,
    QFontMetricsF,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPainterPathStroker,
    QPalette,
    QPen,
    QPolygonF,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsItem,
    QGraphicsObject,
    QGraphicsTextItem,
    QGraphicsView,
    QGridLayout,
    QLabel,
    QHBoxLayout,
    QLineEdit,
    QToolButton,
    QMenu,
    QMessageBox,
    QProxyStyle,
    QStyle,
    QStyleFactory,
    QWidget,
)

from rdb_contacts import (
    ContactPopup,
    count_text,
    dropped_files,
    guess_contact,
    parse_vcards,
    read_files,
)
from rdb_editor import MarkerPopup, SwatchPopup, SwatchRows, paint_marker, pop_at
import rdb_model
import rdb_theme
from rdb_model import (
    CHAR_ATTRS,
    DEFAULT_SETTINGS,
    DAY,
    block_align,
    block_aligned,
    body_html,
    mirror_range,
    strip_body,
    char_color,
    char_toggle,
    span_text,
    state_text,
)
from rdb_timeline import WEEKDAYS, day_bg, day_line, luma_ink

CELL = 32
MIN_CELLS = (4, 2)
GRIP_PX, GRIP_SLOP = 6, 4
GRIP_HIT = GRIP_PX + 2 * GRIP_SLOP
# the edges a grip moves (-1 left/top, 1 right/bottom) and its cursor; corners come
# first so they win where hit areas meet
GRIPS = {
    (-1, -1): Qt.CursorShape.SizeFDiagCursor,
    (1, -1): Qt.CursorShape.SizeBDiagCursor,
    (1, 1): Qt.CursorShape.SizeFDiagCursor,
    (-1, 1): Qt.CursorShape.SizeBDiagCursor,
    (0, -1): Qt.CursorShape.SizeVerCursor,
    (1, 0): Qt.CursorShape.SizeHorCursor,
    (0, 1): Qt.CursorShape.SizeVerCursor,
    (-1, 0): Qt.CursorShape.SizeHorCursor,
}
PAD = 8
TITLE_H, FOOT_H, LINE_H = 18, 16, 15
TL_ROW, TL_NAMES, TL_NAME_MIN = 11, 0.4, 40
TOP, BOX, MARK = TITLE_H + 3, 9, 12
INSET = 2
SHADOW = 3
ZOOM_TEXT = 0.5  # 0: preview text grows with zoom, 1: keeps its 100% on-screen size
TYPES = ("list", "notepad", "board", "links", "page", "contacts", "timeline")

# (light, dark) per name; configure() binds each to the current mode as a module QColor
_TONES = {
    "INK": ("#1e1e1e", "#dedede"),
    "SHADOW_COL": ("#46000000", "#78000000"),
    "PEEK_SHADOW_COL": ("#0e000000", "#30000000"),
    "RED": ("#d42020", "#ff6b6b"),
    "MORE": ("#777777", "#8a8a8a"),
    "TL_HEAD_INK": ("#404040", "#c8c8c8"),
    "FOOT_INK": ("#555555", "#a0a0a0"),
    "LINK": ("#0645ad", "#4ea1ff"),
    "CHIP_BG": ("#e6e6e6", "#3a3a3a"),
    "CHIP_INK": ("#444444", "#d0d0d0"),
    "PAGE_BG": ("#e0e0e0", "#333333"),
    "GRIP_FILL": ("#ffffff", "#2b2b2b"),
    "BOX_FILL": ("#ffffff", "#1c1c1c"),
    "LABEL_FILL": ("#dcffffff", "#dc202020"),
    "BADGE_INK": ("#ffffff", "#101010"),
    "EDIT_FILL": ("#ffffff", "#1c1c1c"),
}
INK = SHADOW_COL = PEEK_SHADOW_COL = RED = MORE = TL_HEAD_INK = FOOT_INK = LINK = (
    CHIP_BG
) = CHIP_INK = PAGE_BG = GRIP_FILL = BOX_FILL = LABEL_FILL = BADGE_INK = EDIT_FILL = (
    LASSO_PEN
) = LASSO_FILL = None
MOZ_URL = "text/x-moz-url"
URL = re.compile(r"https?://\S+\Z", re.I)
ZOOM = (0.1, 5)
ZOOM_STEP = 1.15
# the find bar's verdict on a box: whether it matches, and the title letters it matched
FindMark = namedtuple("FindMark", "hit marks")
FIND_DIM = 0.35
FIT_AGAIN = 0.55
CFG_KEYS = (
    "vim",
    "grid_show",
    "grid_style",
    "snap",
    "card_w",
    "card_h",
    "map_font",
    "map_size",
    "map_weight",
    "text_size",
    "map_bg",
    "grid_minor",
    "card_bg",
    "card_border",
    "card_select_bg",
    "card_select_border",
    "frame_color",
    "card_checkboxes",
    "card_numbers",
    "card_title_rule",
    "card_footer_rule",
    "card_title_rule_color",
    "card_footer_rule_color",
    "card_color_scope",
    "bubble_bg",
    "bubble_fg",
    "bubble_bg_vivid",
    "bubble_fg_vivid",
    "note_bg",
    "note_border",
    "checked_color",
    "tl_weekdays",
    "tl_day_bg",
    "tl_weekend_bg",
    "tl_today_fill",
    "tl_today_bg",
    "tl_grid",
    "tl_grid_color",
)
_cfg = {k: DEFAULT_SETTINGS[k] for k in CFG_KEYS}


class _NoRubberBand(QProxyStyle):
    def drawControl(self, element, option, painter, widget=None):
        if element != QStyle.ControlElement.CE_RubberBand:
            super().drawControl(element, option, painter, widget)


CTRL = Qt.KeyboardModifier.ControlModifier
SHIFT = Qt.KeyboardModifier.ShiftModifier
MODS = CTRL | SHIFT | Qt.KeyboardModifier.AltModifier
ARROWS = {
    Qt.Key.Key_Left: (-1, 0),
    Qt.Key.Key_Right: (1, 0),
    Qt.Key.Key_Up: (0, -1),
    Qt.Key.Key_Down: (0, 1),
}
VIM = {
    Qt.Key.Key_H: (-1, 0),
    Qt.Key.Key_L: (1, 0),
    Qt.Key.Key_K: (0, -1),
    Qt.Key.Key_J: (0, 1),
}
DIRS = ARROWS | VIM
ENTER = (Qt.Key.Key_Return, Qt.Key.Key_Enter)
LEFT = Qt.MouseButton.LeftButton
ALIGN_L = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
ALIGN_C = Qt.AlignmentFlag.AlignCenter
CENTER = Qt.AlignmentFlag.AlignCenter


def configure(settings):
    _cfg.update({k: settings[k] for k in CFG_KEYS})
    dark = rdb_theme.dark()
    for name, (light, night) in _TONES.items():
        globals()[name] = QColor(night if dark else light)
    # cosmetic: rfcascade's lasso is 2 screen px at any zoom, with square corners
    global LASSO_PEN, LASSO_FILL
    rgb = (76, 194, 255) if dark else (40, 100, 220)
    LASSO_PEN = QPen(QColor(*rgb, 200), 2, Qt.PenStyle.DashLine)
    LASSO_PEN.setCosmetic(True)
    LASSO_PEN.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
    LASSO_FILL = QBrush(QColor(*rgb, 30))


def _col(key):
    return QColor(rdb_model.shown(_cfg, key))


configure(DEFAULT_SETTINGS)


def _weight():
    return rdb_model.WEIGHTS[_cfg["map_weight"]]


def _font(delta=0, bold=False, k=1.0):
    return _make_font(_cfg["map_font"], _cfg["map_size"], _weight(), delta, bold, k)


# a card paint asks for the same few fonts hundreds of times
@lru_cache(maxsize=None)
def _make_font(family, size, weight, delta, bold, k):
    f = QFont(family)
    f.setPointSizeF((size + delta) * k)
    f.setWeight(QFont.Weight.Bold if bold else weight)
    if k != 1.0:
        # hinted advances at tiny point sizes break letter spacing once scaled up
        f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return f


def snap(v):
    return round(v / CELL) * CELL if _cfg["snap"] else v


def fit(p, font, s, w):
    p.setFont(font)
    return p.fontMetrics().elidedText(s, Qt.TextElideMode.ElideRight, int(w))


def union(items):
    r = items[0].sceneBoundingRect()
    for i in items[1:]:
        r = r.united(i.sceneBoundingRect())
    return r


class _Box(QGraphicsObject):
    def __init__(self, title, w, h, lo):
        super().__init__()
        self.title, self._lo, self._hover, self._rs = title, lo, False, None
        self.find = None
        self.w = self.h = 0
        self.set_cells(w, h)
        self.setAcceptHoverEvents(True)
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )

    def set_cells(self, w, h):
        self.prepareGeometryChange()
        self.w, self.h = max(w, self._lo[0]), max(h, self._lo[1])
        self.update()

    def set_title(self, t):
        self.title = t
        self.update()

    def _zoom(self):
        views = self.scene().views() if self.scene() else []
        return views[0].transform().m11() if views else 1.0

    def _dim(self, p):
        if self.find is not None and not self.find.hit:
            p.setOpacity(FIND_DIM)

    def _hull(self):
        return QRectF(0, 0, self.w * CELL, self.h * CELL)

    def _grips(self):
        # grip -> (drawn square, hit area), inside the hull and constant on screen; they
        # only shrink when the box is under three hit areas across, so its body stays grabbable
        r = self._hull()
        k = min(
            1 / self._zoom(), r.width() / (3 * GRIP_HIT), r.height() / (3 * GRIP_HIT)
        )
        g, s = GRIP_PX * k, GRIP_SLOP * k
        out = {}
        for ax, ay in GRIPS:
            c = r.center() + QPointF(
                ax * (r.width() - g) / 2, ay * (r.height() - g) / 2
            )
            d = QRectF(c.x() - g / 2, c.y() - g / 2, g, g)
            out[(ax, ay)] = d, d.adjusted(-s, -s, s, s).intersected(r)
        return out

    def _grip_at(self, pos, slop=True):
        return next(
            (
                g
                for g, (drawn, hit) in self._grips().items()
                if (hit if slop else drawn).contains(pos)
            ),
            None,
        )

    def _draw_grips(self, p):
        if not (self.isSelected() or self._hover):
            return
        pen = QPen(_col("card_select_border"), 1)
        pen.setCosmetic(True)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        p.setPen(pen)
        p.setBrush(GRIP_FILL)
        for d, _ in self._grips().values():
            # the 1 px outline stays inside the 6 px square
            h = d.width() / (2 * GRIP_PX)
            p.drawRect(d.adjusted(h, h, -h, -h))
        p.restore()

    def hoverEnterEvent(self, e):
        self._hover = True
        self.update()

    def hoverMoveEvent(self, e):
        g = self._grip_at(e.pos())
        self.setCursor(Qt.CursorShape.ArrowCursor if g is None else GRIPS[g])

    def hoverLeaveEvent(self, e):
        self._hover = False
        self.unsetCursor()
        self.update()

    def itemChange(self, change, value):
        # a resize moves the top/left edge in whole cells from the start position: the
        # opposite edge must stay exactly put, so no snapping while one is under way
        if (
            change == QGraphicsItem.GraphicsItemChange.ItemPositionChange
            and self._rs is None
        ):
            value = QPointF(snap(value.x()), snap(value.y()))
        return super().itemChange(change, value)

    def mousePressEvent(self, e):
        g = self._grip_at(e.pos()) if e.button() == LEFT else None
        if g is None:
            super().mousePressEvent(e)
            return
        if not self.isSelected():
            self.scene().clearSelection()
            self.setSelected(True)
        self._rs = (g, e.scenePos(), self.pos(), self.w, self.h)
        e.accept()

    def mouseMoveEvent(self, e):
        if self._rs is None:
            super().mouseMoveEvent(e)
            return
        (ax, ay), p0, pos, w0, h0 = self._rs
        d = e.scenePos() - p0
        self.set_cells(w0 + ax * round(d.x() / CELL), h0 + ay * round(d.y() / CELL))
        # dragging the left/top edge moves the item by what the size lost
        self.setPos(
            pos.x() + (w0 - self.w) * CELL * (ax < 0),
            pos.y() + (h0 - self.h) * CELL * (ay < 0),
        )

    def mouseReleaseEvent(self, e):
        if self._rs is None:
            super().mouseReleaseEvent(e)
        self._rs = None


def _empty(kind):
    if kind == "board":
        return {"count": 0, "open": 0, "done": 0, "urgent": 0, "cards": []}
    if kind == "contacts":
        return {"count": 0, "preview": []}
    if kind == "timeline":
        return {"open": 0, "done": 0, "urgent": 0, "preview": [], "bars": []}
    return {"open": 0, "done": 0, "urgent": 0, "preview": []}


def _shape(summary):
    if "bars" in summary:
        return "bars"
    return "cards" if "cards" in summary else "count" if "count" in summary else "rows"


class CardItem(_Box):
    def __init__(self, pid, title, w=None, h=None):
        super().__init__(
            title,
            _cfg["card_w"] if w is None else w,
            _cfg["card_h"] if h is None else h,
            MIN_CELLS,
        )
        self.pid = pid
        self._type = "list"
        self.summary = _empty("list")
        self.bg = self.fg = self.url = ""
        # "" follows the card_color_scope setting
        self.scope = ""
        self.view = "list"
        self.kanban = False
        self.kanban_done = True
        self.kanban_w = {}
        self.kanban_status_only = False
        self.kanban_order = []
        self.done = ""
        self.sort = ""
        self.lang = ""
        self.zoom = 0.0
        self.tags = []
        self._snap = None
        # the board that receives snips wears a camera in its title strip
        self.snip = False
        # a drag is hovering over this board, about to drop into it
        self.hot = False
        # first preview row shown; display only, never saved
        self.scroll = 0

    @property
    def type(self):
        return self._type

    @type.setter
    def type(self, kind):
        if kind not in TYPES:
            raise ValueError(kind)
        self._type = kind
        # a timeline keeps its checked rows in place
        if kind == "timeline":
            self.done = "place"
        # a card must paint before its first summary arrives, whichever is set first
        if _shape(self.summary) != _shape(_empty(kind)):
            self.summary = _empty(kind)
        self.update()

    def boundingRect(self):
        return QRectF(0, 0, self.w * CELL, self.h * CELL)

    def shape(self):
        path = QPainterPath()
        path.addRect(self.boundingRect().adjusted(INSET, INSET, -INSET, -INSET))
        return path

    def _hull(self):
        s = INSET + (SHADOW if self._type == "board" else 0)
        return self.boundingRect().adjusted(INSET, INSET, -s, -s)

    def set_summary(self, d):
        self.summary = d
        self.scroll = self._off()
        self.update()

    def set_snapshot(self, img):
        self._snap = img
        self.update()

    def clone(self):
        # a look-alike for a copy drag: no id, so no map or save knows it
        c = CardItem("", self.title, self.w, self.h)
        c.type = self.type
        c.bg, c.fg, c.url, c.scope = self.bg, self.fg, self.url, self.scope
        c.view, c.kanban, c.done, c.sort, c.lang, c.zoom, c.tags = (
            self.view,
            self.kanban,
            self.done,
            self.sort,
            self.lang,
            self.zoom,
            list(self.tags),
        )
        c.kanban_done, c.kanban_w = self.kanban_done, dict(self.kanban_w)
        c.kanban_status_only = self.kanban_status_only
        c.kanban_order = list(self.kanban_order)
        c.summary, c._snap = self.summary, self._snap
        c.setPos(self.pos())
        return c

    def edit_spec(self):
        return QRectF(INSET + 1, INSET, self.w * CELL - 2 * INSET - 2, TITLE_H), _font(
            0, True
        )

    def readout(self):
        s = self.summary
        if self._type in ("page", "notepad"):
            return ""
        if self._type == "board":
            urgent = f"  {s['urgent']} urgent" if s["urgent"] > 0 else ""
            return f"{s['count']} cards  {s['open']} open{urgent}"
        if self._type == "contacts":
            return count_text(s["count"])
        if self._type == "timeline":
            span = span_text(s["bars"])
            return f"{span}  {state_text(s)}" if span else state_text(s)
        return state_text(s, self._type == "links")

    def _lines(self):
        if self._type == "page":
            return []
        return self.summary["cards" if self._type == "board" else "preview"]

    def _dense(self):
        # zoomed in, preview rows grow slower than the card so more of them fit
        return min(1.0, self._zoom() ** -ZOOM_TEXT)

    def _room(self):
        lh = LINE_H * self._dense()
        shadow = SHADOW if self._type == "board" else 0
        return int((self.h * CELL - 2 * INSET - shadow - FOOT_H - 2 - TOP) // lh)

    def _hi(self):
        # a scrolled card spends its top row on the -N readout, so the last row is reached
        # one offset later than the room alone says
        n, room = len(self._lines()), self._room()
        return n - room + 1 if n > room else 0

    def _off(self):
        return min(self.scroll, self._hi())

    def _shown(self):
        off = self._off()
        room = self._room() - (off > 0)
        rows = self._lines()[off:]
        if len(rows) > room:
            room -= 1
        return rows[: max(room, 0)]

    def _top(self):
        return TOP + (LINE_H * self._dense() if self._off() else 0)

    def scroll_rows(self, d):
        if self._type in ("list", "notepad", "links", "contacts"):
            self.scroll = min(max(self._off() + d, 0), self._hi())
            self.update()

    def _num_w(self):
        rows = self._shown() if _cfg["card_numbers"] and self._type != "board" else []
        nums = [str(e["n"]) for e in rows if e["n"] is not None]
        if not nums:
            return 0.0
        k = self._dense()
        fm = QFontMetricsF(_font(0, True, k))
        return max(fm.horizontalAdvance(n) for n in nums) + 4 * k

    def _box(self, i, nw=None, k=None):
        k = self._dense() if k is None else k
        nw = self._num_w() if nw is None else nw
        lh, box = LINE_H * k, BOX * k
        return QRectF(6 + nw, self._top() + i * lh + (lh - box) / 2, box, box)

    def _boxes(self):
        # links cards aggregate, they are not a todo list
        return _cfg["card_checkboxes"] and self._type == "list"

    def row_at(self, pos):
        pos = pos - QPointF(INSET, INSET)
        lh = LINE_H * self._dense()
        w = self.w * CELL - 2 * INSET
        top = self._top()
        for i, e in enumerate(self._shown()):
            if QRectF(6, top + i * lh, w - 12, lh).contains(pos):
                return e
        return None

    def row_hit(self, pos):
        if self._type != "links":
            return None
        e = self.row_at(pos)
        return None if e is None or e.get("kind") == "heading" else e["block"]

    def _mark(self, i, nw=None, k=None):
        k = self._dense() if k is None else k
        nw = self._num_w() if nw is None else nw
        lh = LINE_H * k
        x = 6 + nw + ((BOX + 4) * k if self._boxes() else 0)
        return QRectF(x, self._top() + i * lh, MARK * k, lh)

    def _hit(self, pos, rect):
        if self._boxes():
            pos = pos - QPointF(INSET, INSET)
            for i, e in enumerate(self._shown()):
                if e.get("kind") != "heading" and rect(i).contains(pos):
                    return e["block"]
        return None

    def preview_hit(self, pos):
        return self._hit(pos, self._box)

    def num_hit(self, pos):
        # the gutter number of an item row: the block it opens the list at
        nw = self._num_w()
        e = self.row_at(pos) if nw else None
        if e is None or e["n"] is None or not 6 <= pos.x() - INSET < 6 + nw:
            return None
        return e["block"]

    def mark_hit(self, pos):
        return self._hit(pos, self._mark)

    def _plus(self):
        # the footer's right-hand end, in the coordinates of the drawn face; left of the
        # corner grip, which grows on the card as the map zooms out
        right = self._grips()[(1, 1)][1].left() - INSET
        h = self.h * CELL - 2 * INSET
        return QRectF(right - FOOT_H, h - FOOT_H, FOOT_H, FOOT_H)

    def add_hit(self, pos):
        return self._type == "contacts" and self._plus().contains(
            pos - QPointF(INSET, INSET)
        )

    def paint(self, p, opt, widget=None):
        self._dim(p)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.save()
        p.translate(INSET, INSET)
        w, h = self.w * CELL - 2 * INSET, self.h * CELL - 2 * INSET
        if self._type == "board":
            # an offset shadow makes a board read as a stack of cards
            w, h = w - SHADOW, h - SHADOW
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(SHADOW_COL)
            p.drawRoundedRect(QRectF(SHADOW, SHADOW, w, h), 4, 4)
        self._face(p, w, h)
        p.restore()
        self._draw_grips(p)

    def _face(self, p, w, h):
        sel, kind = self.isSelected(), self._type
        edge = _col("card_select_border")
        found = self.find is not None and self.find.hit
        pen = (
            QPen(edge, 2 if sel or self.hot or found else 1)
            if sel or self._hover or self.hot or found
            else QPen(_col("card_border"), 1)
        )
        i = pen.widthF() / 2
        scope = self.scope or _cfg["card_color_scope"]
        head = scope == "header" and self.bg and not sel
        p.setPen(pen)
        p.setBrush(
            _col("card_select_bg")
            if sel
            else _col("card_bg")
            if head or not self.bg
            else rdb_theme.shade(self.bg)
        )
        face = QRectF(0, 0, w, h).adjusted(i, i, -i, -i)
        p.drawRoundedRect(face, 4, 4)
        if head:
            clip = QPainterPath()
            clip.addRoundedRect(face, 4, 4)
            p.save()
            p.setClipPath(clip)
            p.fillRect(QRectF(0, 0, w, TITLE_H), rdb_theme.shade(self.bg))
            p.restore()
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(face, 4, 4)

        m = 1
        if _cfg["card_title_rule"]:
            p.setPen(_col("card_title_rule_color"))
            p.drawLine(QPointF(m, TITLE_H - 0.5), QPointF(w - m, TITLE_H - 0.5))
        if _cfg["card_footer_rule"]:
            p.setPen(_col("card_footer_rule_color"))
            p.drawLine(QPointF(m, h - FOOT_H + 0.5), QPointF(w - m, h - FOOT_H + 0.5))
        p.setPen(rdb_theme.shade(self.fg) if self.fg else INK)
        self._title(p, w)

        if kind == "page":
            self._page(p, w, h)
        elif kind == "timeline":
            self._bars(p, w, h)
        else:
            self._preview(p, w)
        self._foot(p, w, h)

    def _camera(self, p, w):
        x, y = w - 6 - CAMERA_W, (TITLE_H - 9) / 2
        p.save()
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(p.pen().color(), 1))
        p.drawRoundedRect(QRectF(x + 0.5, y + 1.5, CAMERA_W - 1, 7), 1.5, 1.5)
        p.drawLine(QPointF(x + 3.5, y + 0.5), QPointF(x + 6.5, y + 0.5))
        p.drawEllipse(QPointF(x + CAMERA_W / 2, y + 5), 2, 2)
        p.restore()

    def _title(self, p, w):
        marks = () if self.find is None else self.find.marks
        bold = _font(0, True)
        room = w - 12 - (CAMERA_W + 4 if self.snip else 0)
        if self.snip:
            self._camera(p, w)
        t = fit(p, bold, self.title, room)
        if not marks:
            p.drawText(QRectF(6, 0, room, TITLE_H), ALIGN_L, t)
            return
        # the matched letters stand out bold on an otherwise regular title
        plain, x = _font(0), 6.0
        for i, ch in enumerate(t):
            p.setFont(bold if i in marks else plain)
            adv = p.fontMetrics().horizontalAdvance(ch)
            p.drawText(QRectF(x, 0, adv + 2, TITLE_H), ALIGN_L, ch)
            x += adv

    def _page(self, p, w, h):
        area = QRectF(4, TITLE_H + 2, w - 8, h - TITLE_H - FOOT_H - 4)
        if self._snap is None:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(PAGE_BG)
            p.drawRect(area)
            return
        s = self._snap.size().scaled(
            area.size().toSize(), Qt.AspectRatioMode.KeepAspectRatio
        )
        left = area.left() + (area.width() - s.width()) / 2
        p.drawImage(QRectF(left, area.top(), s.width(), s.height()), self._snap)

    def _bars(self, p, w, h):
        s, k = _cfg, self._dense()
        area = QRectF(8, TITLE_H + 6, w - 16, h - TITLE_H - FOOT_H - 12)
        hr, pitch = TL_ROW * k, LINE_H * k
        head = (3 if s["tl_weekdays"] else 2) * hr
        dated = [r for r in self.summary["bars"] if r["start"] is not None]
        days = []
        if dated:
            first = min(r["start"] for r in dated)
            end = max(r["start"] + max(r["days"], 1) * DAY for r in dated)
            today = datetime.date.fromisoformat(rdb_model.today())
            if first - 3 * DAY <= today <= end + 3 * DAY:
                first, end = min(first, today), max(end, today + DAY)
            days = [first + i * DAY for i in range((end - first).days)]
        nw = max(TL_NAME_MIN, TL_NAMES * area.width())
        left, top, bot = area.left() + nw, area.top() + head, area.bottom()
        dw = (area.right() - left) / len(days) if days else 0
        rows = self.summary["bars"][: max(int((area.height() - head) // pitch), 0)]
        p.fillRect(
            QRectF(area.left(), area.top(), area.width(), head),
            rdb_theme.color("head_bg"),
        )
        small = _font(-1, False, k)
        p.setFont(small)
        p.setPen(Qt.PenStyle.NoPen)
        for i, day in enumerate(days):
            x = left + i * dw
            p.fillRect(
                QRectF(x, area.top() + hr, dw, bot - area.top() - hr), day_bg(s, day)
            )
            if s["tl_grid"] and dw >= 4:
                p.fillRect(
                    QRectF(x, area.top() + hr, 1, bot - area.top() - hr),
                    day_line(s, day),
                )
        if s["tl_grid"] and dw >= 4:
            for j in range(len(rows)):
                p.fillRect(
                    QRectF(left, top + (j + 1) * pitch - 1, area.right() - left, 1),
                    day_line(s, None),
                )
        p.setPen(rdb_theme.color("head_rule"))
        for y in (area.top() + hr, top - 0.5):
            p.drawLine(QPointF(area.left(), y), QPointF(area.right(), y))
        i = 0
        for (year, mon), g in itertools.groupby(days, lambda d: (d.year, d.month)):
            x0, i = left + i * dw, i + len(list(g))
            x1 = left + i * dw
            p.setPen(rdb_theme.color("head_rule"))
            p.drawLine(QPointF(x0, area.top()), QPointF(x0, top))
            if x1 - x0 > 6:
                p.setPen(TL_HEAD_INK)
                p.drawText(
                    QRectF(x0 + 3, area.top(), x1 - x0 - 6, hr),
                    ALIGN_L,
                    fit(p, small, f"{rdb_model.MONTHS[mon - 1]} {year}", x1 - x0 - 6),
                )
        p.setPen(TL_HEAD_INK)
        for i, day in enumerate(days):
            x = left + i * dw
            if dw >= 10:
                p.drawText(QRectF(x, top - hr, dw, hr), ALIGN_C, str(day.day))
            if s["tl_weekdays"] and dw >= 7:
                wd = WEEKDAYS[day.weekday()]
                p.drawText(
                    QRectF(x, area.top() + hr, dw, hr),
                    ALIGN_C,
                    wd if dw >= 14 else wd[0],
                )
        under, bar = False, pitch - 4
        for j, r in enumerate(rows):
            y = top + j * pitch
            ind = 6 if under and not r["level"] else 0
            under = under or bool(r["level"])
            p.setPen(_col("checked_color") if r["done"] else INK)
            p.drawText(
                QRectF(area.left() + 2 + ind, y, nw - 4 - ind, pitch),
                ALIGN_L,
                fit(p, _font(0, bool(r["level"]), k), r["text"], nw - 4 - ind),
            )
            if r["start"] is None:
                continue
            x = left + (r["start"] - days[0]).days * dw
            cw, cy = max(r["days"], 1) * dw, y + pitch / 2
            p.setPen(Qt.PenStyle.NoPen)
            if r["level"]:
                p.setBrush(INK)
                p.drawPolygon(
                    QPolygonF(
                        [
                            QPointF(x, cy - bar / 2),
                            QPointF(x + cw, cy - bar / 2),
                            QPointF(x + cw, cy + bar / 2),
                            QPointF(x + cw - 1.5, cy),
                            QPointF(x + 1.5, cy),
                            QPointF(x, cy + bar / 2),
                        ]
                    )
                )
                continue
            base = rdb_theme.shade(r["bar"]) if r["bar"] else _col("frame_color")
            ink = luma_ink(base.darker(145) if r["pct"] >= 50 else base)
            if r["done"]:
                base.setAlpha(90)
            p.setBrush(base)
            if not r["days"]:
                d = bar / 2 + 1
                c = QPointF(x + cw / 2, cy)
                p.drawPolygon(
                    QPolygonF(
                        [
                            QPointF(c.x(), cy - d),
                            QPointF(c.x() + d, cy),
                            QPointF(c.x(), cy + d),
                            QPointF(c.x() - d, cy),
                        ]
                    )
                )
                continue
            box = QRectF(x, cy - bar / 2, cw, bar)
            p.drawRoundedRect(box, 1.5, 1.5)
            if r["pct"]:
                p.setBrush(base.darker(145))
                p.drawRoundedRect(
                    QRectF(x, box.top(), cw * r["pct"] / 100, bar), 1.5, 1.5
                )
            label = f"{r['days']}d"
            p.setFont(small)
            if p.fontMetrics().horizontalAdvance(label) + 4 <= cw:
                p.setPen(rdb_theme.shade(r["fg"]) if r["fg"] else ink)
                p.drawText(box, ALIGN_C, label)

    def _preview(self, p, w):
        rows, shown, off = self._lines(), self._shown(), self._off()
        k = self._dense()
        # the number column is per card; each row asking again repeats the shown-rows walk
        row = (
            self._board_row
            if self._type == "board"
            else partial(self._list_row, nw=self._num_w())
        )
        y = TOP
        # a heading row is not an item: the readouts count items
        above = sum(e.get("kind") != "heading" for e in rows[:off])
        if off:
            self._more(p, y, w, k, f"-{above}")
            y += LINE_H * k
        for i, e in enumerate(shown):
            row(p, e, i, y, w, k)
            y += LINE_H * k
        hidden = sum(e.get("kind") != "heading" for e in rows[off + len(shown) :])
        if hidden:
            self._more(p, y, w, k, f"+{hidden}")

    def _more(self, p, y, w, k, t):
        p.setFont(_font(0, False, k))
        p.setPen(MORE)
        p.drawText(QRectF(6, y, w - 12, LINE_H * k), ALIGN_L, t)

    def _bang(self, p, x, y, k):
        p.setPen(RED)
        p.drawText(
            QRectF(x, y, 8 * k, LINE_H * k),
            ALIGN_L,
            fit(p, _font(0, True, k), "!", 8 * k),
        )
        return x + 9 * k

    def _heading_row(self, p, e, y, w, k):
        p.setPen(INK)
        p.drawText(
            QRectF(6, y, w - 12, LINE_H * k),
            ALIGN_L,
            fit(p, _font(2 - e["level"], True, k), e["text"], w - 12),
        )

    def _list_row(self, p, e, i, y, w, k, nw):
        if e.get("kind") == "heading":
            self._heading_row(p, e, y, w, k)
            return
        lh = LINE_H * k
        x = 6.0
        if nw and e["n"] is not None:
            p.setFont(_font(0, True, k))
            p.setPen(INK)
            p.drawText(QRectF(x, y, nw - 4 * k, lh), ALIGN_C, str(e["n"]))
        x += nw
        if self._boxes():
            p.setPen(QPen(_col("card_border"), k))
            p.setBrush(BOX_FILL)
            h = 0.5 * k
            p.drawRoundedRect(self._box(i, nw, k).adjusted(h, h, -h, -h), 2 * k, 2 * k)
            x += (BOX + 4) * k
        # with checkboxes on every item row has a marker slot to click, else only a marked row
        if self._type == "list" and (self._boxes() or e["p"]):
            paint_marker(p, self._mark(i, nw, k), e["p"], k, faint=True)
            x += (MARK + 3) * k
        elif e["urgent"]:
            x = self._bang(p, x, y, k)
        t = fit(p, _font(0, False, k), e["text"], w - 6 - x)
        if e["bg"]:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(rdb_theme.shade(e["bg"]))
            tw = p.fontMetrics().horizontalAdvance(t)
            p.drawRoundedRect(
                QRectF(x - 2 * k, y + k, tw + 4 * k, lh - 2 * k), 3 * k, 3 * k
            )
        ink = LINK if self._type == "links" else INK
        p.setPen(rdb_theme.shade(e["fg"]) if e["fg"] else ink)
        p.drawText(QRectF(x, y, w - 6 - x, lh), ALIGN_L, t)

    def _board_row(self, p, e, i, y, w, k):
        lh = LINE_H * k
        x = self._bang(p, 6.0, y, k) if e["urgent"] > 0 else 6.0
        n = str(e["open"])
        p.setFont(_font(0, False, k))
        nw = p.fontMetrics().horizontalAdvance(n)
        t = fit(p, _font(0, False, k), e["title"], w - 12 - nw - x)
        p.setPen(INK)
        p.drawText(QRectF(x, y, w - 12 - nw - x, lh), ALIGN_L, t)
        p.drawText(QRectF(w - 6 - nw, y, nw, lh), ALIGN_L, n)

    def _foot(self, p, w, h):
        end = w - 6
        if self._type == "contacts":
            plus = self._plus()
            end, c = plus.left(), plus.center()
            p.setPen(QPen(FOOT_INK, 1.5))
            p.drawLine(QPointF(c.x() - 4, c.y()), QPointF(c.x() + 4, c.y()))
            p.drawLine(QPointF(c.x(), c.y() - 4), QPointF(c.x(), c.y() + 4))
        p.setPen(FOOT_INK)
        t = fit(p, _font(-1), self.readout(), end - 6)
        y = h - FOOT_H
        p.drawText(QRectF(6, y, end - 6, FOOT_H), ALIGN_L, t)
        x = 6 + p.fontMetrics().horizontalAdvance(t) + (8 if t else 0)
        p.save()
        p.setClipRect(QRectF(0, y, end, FOOT_H))
        p.setPen(Qt.PenStyle.NoPen)
        for tag in self.tags:
            r = QRectF(x, y + 2, p.fontMetrics().horizontalAdvance(tag) + 8, FOOT_H - 4)
            p.setBrush(CHIP_BG)
            p.drawRoundedRect(r, 3, 3)
            p.setPen(CHIP_INK)
            p.drawText(r, CENTER, tag)
            p.setPen(Qt.PenStyle.NoPen)
            x += r.width() + 3
            if x >= end:
                break
        p.restore()


class FrameItem(_Box):
    def __init__(self, title, rect):
        super().__init__(
            title, round(rect.width() / CELL), round(rect.height() / CELL), (1, 1)
        )
        self.setPos(rect.topLeft())
        self.setZValue(-1)

    @property
    def rect(self):
        return QRectF(0, 0, self.w * CELL, self.h * CELL)

    def boundingRect(self):
        return self.rect.adjusted(-PAD, -PAD, PAD, PAD)

    def _label(self):
        fm = QFontMetricsF(_font(-1, True))
        t = fm.elidedText(
            self.title, Qt.TextElideMode.ElideRight, max(1, self.w * CELL - 24)
        )
        return QRectF(8, -7, fm.horizontalAdvance(t) + 8, 14), t

    def shape(self):
        ring = QPainterPath()
        ring.addRoundedRect(self.rect, 4, 4)
        st = QPainterPathStroker()
        st.setWidth(14)
        out = st.createStroke(ring)
        for r in [*(h for _, h in self._grips().values()), self._label()[0]]:
            pr = QPainterPath()
            pr.addRect(r)
            out = out.united(pr)
        return out

    def edit_spec(self):
        lab = self._label()[0]
        return QRectF(8, -8, min(self.w * CELL - 16, max(lab.width(), 160)), 16), _font(
            -1, True
        )

    def itemChange(self, change, value):
        value = super().itemChange(change, value)
        if (
            change == QGraphicsItem.GraphicsItemChange.ItemPositionChange
            and self.scene()
            and self._rs is None
        ):
            d = value - self.pos()
            if not d.isNull():
                area = self.mapRectToScene(self.rect)
                for c in self.scene().items():
                    if (
                        isinstance(c, CardItem)
                        and not c.isSelected()
                        and area.contains(c.sceneBoundingRect())
                    ):
                        c.moveBy(d.x(), d.y())
        return value

    def paint(self, p, opt, widget=None):
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect
        frame = _col("frame_color")
        if self._hover:
            fill = QColor(frame)
            fill.setAlpha(40)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(fill)
            p.drawRoundedRect(r, 4, 4)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(frame, 1.5))
        p.drawRoundedRect(r, 4, 4)
        lab, _ = self._label()
        if self.title:
            p.setBrush(LABEL_FILL)
            p.setPen(QPen(frame, 1))
            p.drawRoundedRect(lab, 3, 3)
            p.setPen(frame)
            p.drawText(
                lab,
                Qt.AlignmentFlag.AlignCenter,
                fit(p, _font(-1, True), self.title, lab.width() - 8),
            )
        if self.isSelected():
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(_col("card_select_border"), 1, Qt.PenStyle.DashLine))
            p.drawRoundedRect(r.adjusted(-3, -3, 3, 3), 4, 4)
        self._draw_grips(p)


IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")
IMG_MAX, IMG_MIN = (16, 10), 2
TEXT_CELLS, TEXT_MIN, TEXT_PAD = (6, 2), (2, 1), (7, 5)
NOTE_INSET = 4
CAPTION_DROP, CAPTION_MIN, CAPTION_PAD = 4, 7, 3
OBJECT_MARK = chr(0xFFFC)
CAMERA_W = 12
CTRL_FORMAT = {
    Qt.Key.Key_B: "bold",
    Qt.Key.Key_I: "italic",
    Qt.Key.Key_U: "underline",
}
AL_LEFT, AL_HCENTER = Qt.AlignmentFlag.AlignLeft, Qt.AlignmentFlag.AlignHCenter
CTRL_ALIGN = {
    Qt.Key.Key_E: AL_HCENTER,
    Qt.Key.Key_L: AL_LEFT,
    Qt.Key.Key_R: Qt.AlignmentFlag.AlignRight,
    Qt.Key.Key_J: Qt.AlignmentFlag.AlignJustify,
}


def image_cells(iw, ih):
    # native pixels, shrunk (never enlarged) to fit IMG_MAX keeping aspect, whole cells
    s = min(1.0, IMG_MAX[0] * CELL / iw, IMG_MAX[1] * CELL / ih)
    return tuple(
        min(cap, max(IMG_MIN, math.ceil(px * s / CELL - 1e-9)))
        for px, cap in ((iw, IMG_MAX[0]), (ih, IMG_MAX[1]))
    )


def text_font():
    return QFont(_cfg["map_font"], _cfg["text_size"], _weight())


def caption_of(text):
    # the first line with text in it; U+FFFC is the mark an inline image leaves in plain text
    return next(
        (s for s in map(str.strip, text.replace(OBJECT_MARK, "").splitlines()) if s), ""
    )


def caption_font():
    return QFont(
        _cfg["map_font"],
        max(CAPTION_MIN, _cfg["text_size"] - CAPTION_DROP),
        _weight(),
    )


def caption_strip():
    return math.ceil(QFontMetricsF(caption_font()).height()) + CAPTION_PAD


def caption_cells(iw, ih, w, h):
    # the block height whose image area, under the strip, still holds the image as drawn at w x h
    drawn = QSize(iw, ih).scaled(w * CELL, h * CELL, Qt.AspectRatioMode.KeepAspectRatio)
    return max(h, math.ceil((drawn.height() + caption_strip()) / CELL))


class Block(_Box):
    def __init__(self, bid, w, h, lo):
        super().__init__("", w, h, lo)
        self.bid = bid

    def boundingRect(self):
        return QRectF(0, 0, self.w * CELL, self.h * CELL)

    def _outline(self, p):
        found = self.find is not None and self.find.hit
        if self.isSelected() or self._hover or found:
            width = 2 if found else 1
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(_col("card_select_border"), width))
            h = width / 2
            p.drawRect(self.boundingRect().adjusted(h, h, -h, -h))
        self._draw_grips(p)


def _note_sheet(p, x, y):
    # the editor's note icon (Editor._paint_note_icon): a 9x11 sheet with its top right
    # corner folded over, whole pixels and no antialiasing
    p.save()
    p.translate(x, y)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    fill, edge = _col("note_bg"), _col("note_border")
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


class ImageItem(Block):
    kind = "image"

    def __init__(self, bid, name, image, w, h):
        super().__init__(bid, w, h, (IMG_MIN, IMG_MIN))
        self.name, self.image = name, image
        # pid of the note document opened by a double click; "" until one is made
        self.note = ""
        # first line of that note, drawn above the image; "" = none
        self.caption = ""
        self.iw, self.ih = (0, 0) if image is None else (image.width(), image.height())

    def clone(self):
        c = ImageItem("", self.name, self.image, self.w, self.h)
        c.iw, c.ih, c.note, c.caption = self.iw, self.ih, self.note, self.caption
        c.setPos(self.pos())
        return c

    def rects(self):
        # the image rect, then the caption's strip and its text elided to the image width (None
        # and "" without a caption); the pair is centred in the block
        W, H = self.w * CELL, self.h * CELL
        strip = caption_strip() if self.caption else 0
        s = self.image.size().scaled(W, H - strip, Qt.AspectRatioMode.KeepAspectRatio)
        x, top = (W - s.width()) / 2, (H - s.height() - strip) / 2
        img = QRectF(x, top + strip, s.width(), s.height())
        if not strip:
            return img, None, ""
        text = QFontMetricsF(caption_font()).elidedText(
            self.caption, Qt.TextElideMode.ElideRight, s.width()
        )
        return img, QRectF(x, top, s.width(), strip), text

    def _outline(self, p):
        if self.note and not (self.caption and self.image is not None):
            _note_sheet(p, self.w * CELL - 9 - NOTE_INSET, NOTE_INSET)
        super()._outline(p)

    def paint(self, p, opt, widget=None):
        self._dim(p)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = self.boundingRect()
        if self.image is None:
            p.setPen(QPen(RED, 1))
            p.setBrush(PAGE_BG)
            p.drawRect(r.adjusted(0.5, 0.5, -0.5, -0.5))
            p.drawLine(r.topLeft(), r.bottomRight())
            p.drawLine(r.topRight(), r.bottomLeft())
            p.setPen(INK)
            p.drawText(
                r.adjusted(4, 4, -4, -4),
                Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                f"missing {self.name}",
            )
            self._outline(p)
            return
        img, cap, text = self.rects()
        p.drawImage(img, self.image)
        if cap is not None:
            p.setFont(caption_font())
            p.setPen(INK)
            p.drawText(cap, AL_LEFT | Qt.AlignmentFlag.AlignVCenter, text)
        self._outline(p)


class _TextEdit(QGraphicsTextItem):
    done = Signal()
    menu = Signal()

    def __init__(self, item):
        super().__init__(item)
        self._over = self.suspended = False
        self.document().setDocumentMargin(0)
        self.setFont(text_font())
        self.setDefaultTextColor(INK)
        self.setTextWidth(item.w * CELL - 2 * TEXT_PAD[0])
        self.setPos(*TEXT_PAD)
        self.setHtml(item.html())
        if rdb_theme.dark():
            mirror_doc(self.document())
            self.document().clearUndoRedoStacks()
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
        c = self.textCursor()
        c.movePosition(QTextCursor.MoveOperation.End)
        self.setTextCursor(c)

    def finish(self):
        if not self._over:
            self._over = True
            self.done.emit()

    def _format(self, attr):
        c = self.textCursor()
        if c.hasSelection():
            char_toggle(c, attr)
            return
        # no selection: the format of the text typed next
        get, put = CHAR_ATTRS[attr]
        f = QTextCharFormat()
        put(f, not get(c.charFormat()))
        c.mergeCharFormat(f)
        self.setTextCursor(c)

    def _align(self, align):
        c = self.textCursor()
        # Ctrl+E on centred text sets it left again
        if align == AL_HCENTER and block_aligned(c) == AL_HCENTER:
            align = AL_LEFT
        block_align(c, align)

    def keyPressEvent(self, e):
        k, mods = e.key(), e.modifiers() & MODS
        if k == Qt.Key.Key_Escape or (k in ENTER and mods == CTRL):
            self.finish()
        elif k == Qt.Key.Key_QuoteLeft and not mods:
            self.menu.emit()
        elif mods == CTRL and k in CTRL_FORMAT:
            self._format(CTRL_FORMAT[k])
        elif mods == CTRL and k in CTRL_ALIGN:
            self._align(CTRL_ALIGN[k])
        elif e.matches(QKeySequence.StandardKey.Paste) and _picture_only():
            pass
        elif e.matches(QKeySequence.StandardKey.Copy):
            self._copy(False)
        elif e.matches(QKeySequence.StandardKey.Cut):
            self._copy(True)
        elif e.matches(QKeySequence.StandardKey.Paste):
            first = self.textCursor().selectionStart()
            super().keyPressEvent(e)
            mirror_range(self.document(), first, self.textCursor().position())
        else:
            super().keyPressEvent(e)

    def _copy(self, cut):
        # the clipboard gets the colours as written, not as drawn
        c = self.textCursor()
        if not c.hasSelection():
            return
        frag = c.selection()
        md = QMimeData()
        md.setHtml(rdb_model.fragment_html(frag))
        md.setText(frag.toPlainText())
        QApplication.clipboard().setMimeData(md)
        if cut:
            c.removeSelectedText()

    def paint(self, p, opt, widget=None):
        opt.state &= ~QStyle.StateFlag.State_HasFocus
        super().paint(p, opt, widget)

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        if not self.suspended:
            self.finish()


def _picture_only():
    mime = QApplication.clipboard().mimeData()
    return mime.hasImage() and not (mime.hasText() or mime.hasHtml())


def mirror_doc(doc):
    mirror_range(doc, 0, doc.characterCount())


def texts(items):
    return [i for i in items if isinstance(i, TextItem)]


class TextItem(Block):
    kind = "text"
    editor = None

    def __init__(self, bid="", w=TEXT_CELLS[0], h=TEXT_CELLS[1], html="", bg=""):
        super().__init__(bid, w, h, TEXT_MIN)
        self.bg = bg
        self.doc = QTextDocument()
        self.doc.setDocumentMargin(0)
        self._shown_doc = self._shown_key = None
        if html:
            self.doc.setHtml(strip_body(html))
        self.restyle()

    def clone(self):
        c = TextItem("", self.w, self.h, self.html(), self.bg)
        c.setPos(self.pos())
        return c

    def restyle(self):
        self.doc.setDefaultFont(text_font())
        self._width()
        self.update()

    def _width(self):
        self.doc.setTextWidth(self.w * CELL - 2 * TEXT_PAD[0])

    def _display(self):
        # the document as drawn: itself in light mode, a copy with mirrored colours in dark
        if not rdb_theme.dark():
            return self.doc
        key = (self.doc.revision(), self.doc.textWidth(), self.doc.defaultFont())
        if self._shown_key != key:
            d = self.doc.clone()
            d.setDefaultFont(self.doc.defaultFont())
            d.setDocumentMargin(0)
            d.setTextWidth(self.doc.textWidth())
            mirror_doc(d)
            self._shown_doc, self._shown_key = d, key
        return self._shown_doc

    def html(self):
        return body_html(self.doc)

    def format_all(self, key):
        c = QTextCursor(self.doc)
        c.select(QTextCursor.SelectionType.Document)
        if key in CTRL_FORMAT:
            char_toggle(c, CTRL_FORMAT[key])
        else:
            align = CTRL_ALIGN[key]
            # as in the editor: Ctrl+E on centred text sets it left again
            if align == AL_HCENTER and block_aligned(c) == AL_HCENTER:
                align = AL_LEFT
            block_align(c, align)
        self.update()

    def plain(self):
        return self.doc.toPlainText()

    def set_cells(self, w, h):
        super().set_cells(w, h)
        if self.editor is not None:
            self.editor.setTextWidth(self.w * CELL - 2 * TEXT_PAD[0])

    def fit(self):
        # grows in whole cells to hold the text being edited, never shrinks
        need = self.editor.document().size().height() + 2 * TEXT_PAD[1]
        cells = math.ceil(need / CELL - 1e-9)
        if cells > self.h:
            self.set_cells(self.w, cells)

    def finish(self):
        ed, self.editor = self.editor, None
        self.doc.setHtml(body_html(ed.document()))
        ed.setParentItem(None)
        self.scene().removeItem(ed)
        self.update()

    def paint(self, p, opt, widget=None):
        self._dim(p)
        r = self.boundingRect()
        if self.bg:
            p.fillRect(r, rdb_theme.shade(self.bg))
        if self.editor is None:
            self._width()
            ctx = QAbstractTextDocumentLayout.PaintContext()
            ctx.palette.setColor(QPalette.ColorRole.Text, INK)
            p.save()
            p.translate(*TEXT_PAD)
            p.setClipRect(
                QRectF(0, 0, self.doc.textWidth(), r.height() - 2 * TEXT_PAD[1])
            )
            self._display().documentLayout().draw(p, ctx)
            p.restore()
        self._outline(p)


class TitleEditor(QGraphicsTextItem):
    committed = Signal(str)
    cancelled = Signal()

    def __init__(self, parent, text, rect, font):
        super().__init__(text, parent)
        self._done = False
        self.setFont(font)
        self.setDefaultTextColor(INK)
        self.document().setDocumentMargin(2)
        self.setTextWidth(rect.width())
        self.setPos(rect.topLeft())
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
        c = self.textCursor()
        c.select(c.SelectionType.Document)
        self.setTextCursor(c)

    def _finish(self, sig, *args):
        if not self._done:
            self._done = True
            sig.emit(*args)

    def paint(self, p, opt, widget=None):
        p.setPen(QPen(_col("card_select_border"), 1))
        p.setBrush(EDIT_FILL)
        p.drawRect(self.boundingRect().adjusted(0.5, 0.5, -0.5, -0.5))
        opt.state &= ~QStyle.StateFlag.State_HasFocus
        super().paint(p, opt, widget)

    def keyPressEvent(self, e):
        if e.key() in ENTER:
            self._finish(self.committed, self.toPlainText().strip())
        elif e.key() == Qt.Key.Key_Escape:
            self._finish(self.cancelled)
        else:
            super().keyPressEvent(e)

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        # another window or popup taking over (the context menu that opened this editor, an
        # app switch) leaves the edit open; the view gives the focus back on return
        if e.reason() not in (
            Qt.FocusReason.ActiveWindowFocusReason,
            Qt.FocusReason.PopupFocusReason,
        ):
            self._finish(self.committed, self.toPlainText().strip())


class CardStylePopup(QFrame):
    styled = Signal(list)

    def __init__(self, parent, cards):
        super().__init__(parent, Qt.WindowType.Popup)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        rdb_theme.follow(self, rdb_theme.popup_frame)
        self._cards = cards
        grid = QGridLayout(self)
        grid.setContentsMargins(6, 6, 6, 6)
        grid.setSpacing(4)
        rows = SwatchRows(_cfg)
        rows.picked.connect(self._pick)
        grid.addWidget(rows, 0, 0, 1, 2)
        grid.addWidget(QLabel("fill"), 1, 0)
        fill = QHBoxLayout()
        fill.setSpacing(2)
        own = cards[0].scope or _cfg["card_color_scope"]
        for scope in ("card", "header"):
            b = QToolButton()
            b.setText(scope)
            b.setCheckable(True)
            b.setChecked(scope == own)
            b.setAutoRaise(True)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(lambda _=False, s=scope: self._pick("scope", s))
            fill.addWidget(b)
        fill.addStretch(1)
        grid.addLayout(fill, 1, 1)
        grid.addWidget(QLabel("tags"), 2, 0)
        self._tags = QLineEdit(", ".join(cards[0].tags))
        self._tags.returnPressed.connect(self._apply_tags)
        grid.addWidget(self._tags, 2, 1)
        self._tags.setFocus()

    def _pick(self, key, colour):
        for c in self._cards:
            setattr(c, key, colour)
            c.update()
        self._done()

    def _apply_tags(self):
        tags = [t.strip() for t in self._tags.text().split(",") if t.strip()]
        for c in self._cards:
            c.tags = list(tags)
            c.update()
        self._done()

    def _done(self):
        self.styled.emit(self._cards)
        self.close()


PEEK_W, PEEK_PAD, PEEK_GAP, PEEK_SHADOW, PEEK_LINES = 340, 8, 6, 5, 20
WRAP = Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop


class RowPeek(QWidget):
    # a card row's full text, its note and its readout, beside the cursor while it rests
    # on the row; read-only, never takes focus
    def __init__(self, parent):
        super().__init__(
            parent, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.row, self.parts, self.mark, self.panel = None, [], "", None
        self.mark_rect = None

    def _text(self, x, y, w, font, ink, text):
        r = QFontMetricsF(font).boundingRect(QRectF(x, y, w, 1e5), WRAP, text)
        self.parts.append((QRectF(x, y, w, r.height()), font, ink, text))
        return y + r.height()

    def show_row(self, card, e, note, at):
        self.row, self.parts, self.panel = e, [], None
        x, y, w = PEEK_PAD, PEEK_PAD, PEEK_W - 2 * PEEK_PAD
        board = card.type == "board"
        self.mark = "" if board else e.get("p", "")
        head = _font(0, e.get("kind") == "heading")
        if self.mark:
            self.mark_rect = QRectF(x, y, MARK, QFontMetricsF(head).height())
        ink = (
            rdb_theme.shade(e["fg"])
            if e.get("fg")
            else LINK
            if card.type == "links"
            else INK
        )
        dx = MARK + 4 if self.mark else 0
        text = e["title"] if board else e["text"]
        y = self._text(x + dx, y, w - dx, head, ink, text)
        small = _font(-1)
        if note is not None:
            lines = note.replace("￼", "").rstrip().split("\n")
            top, n = y + PEEK_GAP, len(lines) - PEEK_LINES
            y = self._text(
                x + 4, top + 3, w - 8, small, INK, "\n".join(lines[:PEEK_LINES])
            )
            if n > 0:
                y = self._text(x + 4, y, w - 8, small, MORE, f"+{n} lines")
            self.panel = QRectF(x, top, w, y + 3 - top)
            y = self.panel.bottom()
        if board:
            urgent = f"  {e['urgent']} urgent" if e["urgent"] > 0 else ""
            meta = f"{e['open']} open{urgent}"
        else:
            num = f"#{e['n']}" if e.get("n") is not None else ""
            made = f"created {e['c']}" if e.get("c") else ""
            meta = "  ".join(t for t in (num, made) if t)
        if meta:
            y = self._text(x, y + PEEK_GAP, w, small, FOOT_INK, meta)
        self.resize(PEEK_W + PEEK_SHADOW, round(y + PEEK_PAD) + PEEK_SHADOW)
        # beside the cursor, flipped above or left where the screen ends
        scr = QApplication.screenAt(at)
        geo = scr.availableGeometry()
        px, py = at.x() + 14, at.y() + 18
        if px + self.width() > geo.right():
            px = at.x() - 14 - self.width()
        if py + self.height() > geo.bottom():
            py = at.y() - 6 - self.height()
        self.move(max(px, geo.left()), max(py, geo.top()))
        self.show()
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        face = QRectF(0.5, 0.5, PEEK_W - 1, self.height() - PEEK_SHADOW - 1)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(PEEK_SHADOW_COL)
        for d in range(1, PEEK_SHADOW + 1):
            p.drawRoundedRect(face.translated(d, d), 4, 4)
        p.setPen(QPen(_col("card_border"), 1))
        p.setBrush(_col("card_bg"))
        p.drawRoundedRect(face, 4, 4)
        if self.panel is not None:
            p.setPen(QPen(_col("note_border"), 1))
            p.setBrush(_col("note_bg"))
            p.drawRect(self.panel.adjusted(0.5, 0.5, -0.5, -0.5))
        if self.mark:
            paint_marker(p, self.mark_rect, self.mark, 1.0)
        for r, font, ink, text in self.parts:
            p.setFont(font)
            p.setPen(ink)
            p.drawText(r, WRAP, text)


def drop_images(mime):
    files = (u.toLocalFile() for u in mime.urls())
    return [f for f in files if f.lower().endswith(IMAGE_EXT)]


def drop_url(mime):
    found = [u.toString() for u in mime.urls()]
    if mime.hasFormat(MOZ_URL):
        found.append(bytes(mime.data(MOZ_URL)).decode("utf-16-le").split("\n")[0])
    if mime.hasText():
        found.append(mime.text())
    return next((u for u in map(str.strip, found) if URL.match(u)), None)


PAN_BUTTONS = (Qt.MouseButton.MiddleButton, Qt.MouseButton.RightButton)


def _glyph(p, item, g, ink):
    kind = item.type if isinstance(item, CardItem) else item.kind
    p.setPen(QPen(ink, 1.5))
    p.setBrush(Qt.BrushStyle.NoBrush)
    if kind == "list":
        for k in (0.2, 0.5, 0.8):
            y = g.top() + k * g.height()
            p.drawLine(QPointF(g.left(), y), QPointF(g.right(), y))
    elif kind == "notepad":
        for k, w in ((0.2, 1), (0.5, 1), (0.8, 0.6)):
            y = g.top() + k * g.height()
            p.drawLine(QPointF(g.left(), y), QPointF(g.left() + w * g.width(), y))
    elif kind == "board":
        p.drawRect(g.adjusted(3, 0, 0, -3))
        p.setBrush(_icon_fill(item))
        p.drawRect(g.adjusted(0, 3, -3, 0))
    elif kind == "links":
        p.drawEllipse(QRectF(g.left(), g.top() + 4, 9, 8))
        p.drawEllipse(QRectF(g.right() - 9, g.top() + 4, 9, 8))
    elif kind == "page":
        p.drawRect(g)
        p.drawLine(QPointF(g.left(), g.top() + 4), QPointF(g.right(), g.top() + 4))
    elif kind == "timeline":
        for k, (x, w) in enumerate(((0, 0.6), (0.3, 0.7), (0.15, 0.5))):
            y = g.top() + (k + 0.5) * g.height() / 3
            p.drawLine(
                QPointF(g.left() + x * g.width(), y),
                QPointF(g.left() + (x + w) * g.width() - 1, y),
            )
    elif kind == "contacts":
        p.drawEllipse(QPointF(g.center().x(), g.top() + 4), 3.5, 3.5)
        p.drawArc(QRectF(g.left() + 1, g.top() + 9, g.width() - 2, 12), 0, 180 * 16)
    elif kind == "text":
        p.setFont(_font(1, True))
        p.drawText(g, ALIGN_C, "T")
    else:
        p.drawRect(g)
        p.drawPolyline(
            [
                QPointF(g.left(), g.bottom() - 2),
                QPointF(g.left() + 6, g.top() + 6),
                QPointF(g.left() + 10, g.bottom() - 5),
                QPointF(g.right(), g.bottom() - 2),
            ]
        )


def _icon_fill(item):
    if isinstance(item, ImageItem):
        return PAGE_BG
    return rdb_theme.shade(item.bg) if item.bg else _col("card_bg")


class MapView(QGraphicsView):
    enter = Signal(str)
    enter_row = Signal(str, int)
    created = Signal(object, str)
    renamed = Signal(object, str)
    remove = Signal()
    changed = Signal()
    check_item = Signal(str, int)
    mark_item = Signal(str, int, str)
    contacts_added = Signal(str, list)
    insert_request = Signal(QPointF)
    styled = Signal(list)
    move_into = Signal(list, object)
    cut_cards = Signal(list)
    copy_cards = Signal(list)
    duplicate_cards = Signal(list)
    copy_dropped = Signal(list, QPointF, object)
    paste_cards = Signal(QPointF)
    url_dropped = Signal(str, QPointF, object)
    images_dropped = Signal(list, QPointF)
    block_added = Signal(object)
    image_opened = Signal(object)
    snip_chosen = Signal(str)
    link_clicked = Signal(str, int, bool)
    open_external = Signal(str)
    view_toggled = Signal(object, str)
    refresh_request = Signal(object)
    undo_request = Signal()
    redo_request = Signal()
    quick_add = Signal()

    def __init__(self, scene):
        super().__init__(scene)
        # the window says whether the card clipboard holds something to paste
        self.clip_ready = lambda: False
        # the window reads a note's plain text for the row peek
        self.note_text = None
        self.peek = RowPeek(self)
        self.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._pan = self._pan0 = self._ed = self._g0 = None
        self._panned = False
        self._fit_t = -math.inf
        self.cursor_cell = None
        self._band = None
        # the drag hovering a board: its icon, the board and the hidden dragged items
        self._drop = None
        self._cancel = False
        # the left press under way: where, whether Ctrl was down, the box under it, and whether
        # the drag has started; _copy: a copy drag's originals and the look-alikes it moves
        self._press = self._copy = None
        # true while a key nudge is being signalled, so a run of nudges can be one edit
        self.nudging = False
        # Qt's own rubber band is hidden; the band is drawn rfcascade-style in drawBackground
        self._no_band_style = None
        rdb_theme.follow(self, MapView._set_style)
        self.rubberBandChanged.connect(self._band_changed)
        self.viewport().setAcceptDrops(True)
        scene.selectionChanged.connect(self._selection_changed)

    def _set_style(self):
        # a proxy owns its base, so each theme gets a fresh one; the view owns the proxy so that
        # it outlives the view's own teardown
        style = _NoRubberBand(QStyleFactory.create(rdb_theme.raw("style")))
        style.setParent(self)
        old, self._no_band_style = self._no_band_style, style
        self.setStyle(style)
        if old is not None:
            old.deleteLater()

    def _selection_changed(self):
        if self.cursor_cell is not None and self.scene().selectedItems():
            self.cursor_cell = None
            self.viewport().update()

    def _cursor_rect(self):
        return QRectF(self.cursor_cell, QSizeF(CELL, CELL))

    def _set_cursor(self, pt):
        self.cursor_cell = QPointF(snap(pt.x()), snap(pt.y()))
        self.viewport().update()

    def _move_cursor(self, d):
        self.cursor_cell = self.cursor_cell + QPointF(d[0] * CELL, d[1] * CELL)
        cell = self._cursor_rect().adjusted(1, 1, -1, -1)
        card = next(
            (i for i in self.scene().items(cell) if isinstance(i, CardItem)), None
        )
        if card is not None:
            # selecting clears the cursor (_selection_changed), so Enter goes in
            self.scene().clearSelection()
            card.setSelected(True)
            self.ensureVisible(card)
            return
        self.ensureVisible(self._cursor_rect())
        self.viewport().update()

    def _at(self):
        if self.cursor_cell is not None:
            return QPointF(self.cursor_cell)
        c = self.mapToScene(self.viewport().rect().center())
        return QPointF(snap(c.x()), snap(c.y()))

    def place(self):
        # rfcascade's priority: grid cursor, the one selected box, else the viewport centre
        one = [i for i in self.scene().selectedItems() if isinstance(i, _Box)]
        if self.cursor_cell is None and len(one) == 1:
            return QPointF(one[0].pos())
        return self._at()

    def _band_changed(self, rect, _from, _to):
        if rect.isNull():
            self._band = None
        else:
            s = self.mapToScene(rect).boundingRect()
            x0, y0 = (
                math.floor(s.left() / CELL) * CELL,
                math.floor(s.top() / CELL) * CELL,
            )
            x1, y1 = (
                math.ceil(s.right() / CELL) * CELL,
                math.ceil(s.bottom() / CELL) * CELL,
            )
            self._band = QRectF(x0, y0, x1 - x0, y1 - y0)
        self.viewport().update()

    def drawBackground(self, p, r):
        p.fillRect(r, _col("map_bg"))
        if _cfg["grid_show"]:
            xs = range(math.floor(r.left() / CELL), math.floor(r.right() / CELL) + 1)
            ys = range(math.floor(r.top() / CELL), math.floor(r.bottom() / CELL) + 1)
            line = _col("grid_minor")
            if _cfg["grid_style"] == "dots":
                pen = QPen(line, 2)
                pen.setCosmetic(True)
                p.setPen(pen)
                p.drawPoints([QPointF(x * CELL, y * CELL) for x in xs for y in ys])
            else:
                p.setPen(QPen(line, 0))
                for i in xs:
                    p.drawLine(
                        QPointF(i * CELL, r.top()), QPointF(i * CELL, r.bottom())
                    )
                for i in ys:
                    p.drawLine(
                        QPointF(r.left(), i * CELL), QPointF(r.right(), i * CELL)
                    )
        # rfcascade's empty-cell highlight and lasso: painted here so cards stay on top
        p.setPen(LASSO_PEN)
        p.setBrush(LASSO_FILL)
        k = 3 / self.transform().m11()
        if self.cursor_cell is not None:
            p.drawRect(self._cursor_rect().adjusted(k, k, -k, -k))
        if self._band is not None:
            p.drawRect(self._band.adjusted(k, k, -k, -k))

    def drawForeground(self, p, r):
        if self._drop is None:
            return
        first, at = self._drop["first"], self._drop["at"]
        box = QRectF(at.x() - CELL / 2, at.y() - CELL / 2, CELL, CELL)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(_col("card_border"), 1))
        p.setBrush(_icon_fill(first))
        p.drawRoundedRect(box, 4, 4)
        fg = first.fg if isinstance(first, CardItem) else ""
        _glyph(p, first, box.adjusted(8, 8, -8, -8), rdb_theme.shade(fg) if fg else INK)
        n = self._drop["n"]
        if n > 1:
            badge = QRectF(box.right() - 9, box.top() - 5, 14, 14)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(_col("card_select_border"))
            p.drawEllipse(badge)
            p.setPen(BADGE_INK)
            p.setFont(_font(-1, True))
            p.drawText(badge, ALIGN_C, str(n))

    def _dragged(self, g0):
        if self._copy is not None:
            return list(self._copy["clones"])
        return [
            i
            for i in self.scene().selectedItems()
            if isinstance(i, (CardItem, Block))
            and i in g0
            and i.pos() != QPointF(*g0[i][:2])
            and (i.w, i.h) == g0[i][2:]
        ]

    def _board_under(self, pt, dragged):
        # a copy drag never drops into a board that is one of its originals
        out = dragged + (self._copy["src"] if self._copy else [])
        under = next(
            (i for i in self.items(pt) if isinstance(i, CardItem) and i not in out),
            None,
        )
        return under if under is not None and under.type == "board" else None

    def _hint(self, pt):
        dragged = self._dragged(self._g0)
        board = self._board_under(pt, dragged) if dragged else None
        if board is None:
            self._unhint()
            return
        if self._drop is not None and self._drop["board"] is not board:
            self._drop["board"].hot = False
            self._drop["board"].update()
        for i in dragged:
            i.setOpacity(0)
        board.hot = True
        board.update()
        self._drop = {
            "board": board,
            "at": self.mapToScene(pt),
            "first": min(dragged, key=lambda i: (i.y(), i.x())),
            "n": len(dragged),
            "items": dragged,
        }
        self.viewport().update()

    def _unhint(self):
        if self._drop is None:
            return
        for i in self._drop["items"]:
            i.setOpacity(1)
        self._drop["board"].hot = False
        self._drop["board"].update()
        self._drop = None
        self.viewport().update()

    def _restore(self, g0):
        # a frame first: moving it carries the cards inside, which then land on their own spot
        for i, (x, y, w, h) in sorted(
            g0.items(), key=lambda kv: not isinstance(kv[0], FrameItem)
        ):
            i.setPos(x, y)
            if (i.w, i.h) != (w, h):
                i.set_cells(w, h)

    def _start_copy(self):
        item = self._press["item"]
        if getattr(item, "pid", "") == "inbox":
            return
        sc = self.scene()
        if not item.isSelected():
            sc.clearSelection()
            item.setSelected(True)
        src = [
            i
            for i in sc.selectedItems()
            if isinstance(i, (CardItem, Block)) and getattr(i, "pid", "") != "inbox"
        ]
        # the press may have nudged the originals before the drag was known to be a copy
        self._restore(self._g0)
        made = [i.clone() for i in src]
        sc.clearSelection()
        for c in made:
            sc.addItem(c)
            c.setSelected(True)
        self._copy = {
            "src": src,
            "clones": made,
            "from": self.mapToScene(self._press["pos"].toPoint()),
        }

    def _copy_to(self, pt):
        d = self.mapToScene(pt) - self._copy["from"]
        for src, c in zip(self._copy["src"], self._copy["clones"]):
            c.setPos(src.pos() + d)

    def _end_copy(self):
        sc = self.scene()
        for c in self._copy["clones"]:
            sc.removeItem(c)
        sc.clearSelection()
        for i in self._copy["src"]:
            i.setSelected(True)
        self._copy = None

    def cancel_drag(self):
        if self._g0 is None or self._cancel:
            return False
        if self._copy is not None:
            self._unhint()
            self._end_copy()
            self._cancel = True
            return True
        if self._geom() == self._g0:
            return False
        self._unhint()
        self._restore(self._g0)
        self._cancel = True
        return True

    def _geom(self):
        return {
            i: (i.x(), i.y(), i.w, i.h)
            for i in self.scene().items()
            if isinstance(i, _Box)
        }

    def zoom_step(self, up, anchor=QGraphicsView.ViewportAnchor.AnchorViewCenter):
        s = ZOOM_STEP if up else 1 / ZOOM_STEP
        if ZOOM[0] <= self.transform().m11() * s <= ZOOM[1]:
            keep = self.transformationAnchor()
            self.setTransformationAnchor(anchor)
            self.scale(s, s)
            self.setTransformationAnchor(keep)

    def wheelEvent(self, e):
        if e.modifiers() & CTRL:
            item = self.itemAt(e.position().toPoint())
            if isinstance(item, CardItem) and (dy := e.angleDelta().y()):
                item.scroll_rows(-1 if dy > 0 else 1)
            e.accept()
            return
        self.zoom_step(
            e.angleDelta().y() > 0, QGraphicsView.ViewportAnchor.AnchorUnderMouse
        )

    def _row_under(self, pt):
        item = self.itemAt(pt)
        if not isinstance(item, CardItem):
            return None, None
        return item, item.row_at(item.mapFromScene(self.mapToScene(pt)))

    def viewportEvent(self, e):
        t = e.type()
        if t == QEvent.Type.ToolTip:
            item, row = self._row_under(e.pos())
            if row is None:
                self.peek.hide()
            else:
                nt = row.get("nt")
                note = self.note_text(nt) if nt else None
                self.peek.show_row(item, row, note, e.globalPos())
            return True
        if self.peek.isVisible() and (
            t in (QEvent.Type.Leave, QEvent.Type.Wheel, QEvent.Type.MouseButtonPress)
            or t == QEvent.Type.MouseMove
            and self._row_under(e.position().toPoint())[1] is not self.peek.row
        ):
            self.peek.hide()
        return super().viewportEvent(e)

    def view_state(self):
        vp = self.viewport()
        c = self.mapToScene(vp.width() // 2, vp.height() // 2)
        return {"zoom": self.transform().m11(), "cx": c.x(), "cy": c.y()}

    def set_view_state(self, d):
        z = min(max(d["zoom"], ZOOM[0]), ZOOM[1])
        self.resetTransform()
        self.scale(z, z)
        vp = self.viewport()
        # centerOn truncates the scroll value, which breaks the get/set round trip
        self.horizontalScrollBar().setValue(round(d["cx"] * z - vp.width() // 2))
        self.verticalScrollBar().setValue(round(d["cy"] * z - vp.height() // 2))

    def _body_at(self, e):
        # the card under the cursor with the point in its coordinates; a drawn grip is no
        # body, but a row or checkbox beats the slop around one
        pt = e.position().toPoint()
        item = self.itemAt(pt)
        if not isinstance(item, CardItem):
            return None, None
        pos = item.mapFromScene(self.mapToScene(pt))
        return (None, None) if item._grip_at(pos, slop=False) else (item, pos)

    def _check_hit(self, e):
        item, pos = self._body_at(e)
        if item is not None:
            blk = item.preview_hit(pos)
            if blk is not None:
                self.check_item.emit(item.pid, blk)
                return True
        return False

    def _mark_hit(self, e):
        item, pos = self._body_at(e)
        blk = None if item is None else item.mark_hit(pos)
        if blk is None:
            return False
        pop = MarkerPopup(self)
        pop.picked.connect(lambda mark: self.mark_item.emit(item.pid, blk, mark))
        pop_at(pop, self, QRect(e.position().toPoint(), QSize(0, 0)))
        return True

    def _add_hit(self, e):
        item, pos = self._body_at(e)
        if item is None or not item.add_hit(pos):
            return False
        self._add_contact(item)
        return True

    def _add_contact(self, card, prefill=None):
        pop = ContactPopup(self, prefill)
        pop.added.connect(lambda row: self.contacts_added.emit(card.pid, [row]))
        pop_at(pop, self, self.mapFromScene(card.sceneBoundingRect()).boundingRect())

    def _paste_contacts(self, boxes, cards):
        text = QApplication.clipboard().text().strip()
        if (
            len(boxes) != 1
            or len(cards) != 1
            or cards[0].type != "contacts"
            or self._can_paste()
            or not text
        ):
            return False
        if text.upper().startswith("BEGIN:VCARD"):
            try:
                rows = parse_vcards(text)
            except ValueError as e:
                QMessageBox.warning(self, "Import", str(e))
            else:
                self.contacts_added.emit(cards[0].pid, rows)
        else:
            self._add_contact(cards[0], guess_contact(text))
        return True

    def _link_hit(self, e):
        item, pos = self._body_at(e)
        if item is not None:
            blk = item.row_hit(pos)
            if blk is not None:
                return item.pid, blk
        return None

    def fit_all(self, use_selection=True):
        boxes = [i for i in self.scene().items() if isinstance(i, _Box)]
        if not boxes:
            self.resetTransform()
            self.centerOn(0, 0)
            return
        things = [i for i in boxes if not isinstance(i, FrameItem)]
        sel = [i for i in things if i.isSelected()]
        if use_selection and sel and len(sel) < len(things):
            boxes = sel
        m = CELL * 0.12
        r = union(boxes).adjusted(-m, -m, m, m)
        self.fitInView(r, Qt.AspectRatioMode.KeepAspectRatio)
        s = self.transform().m11()
        k = min(max(s, ZOOM[0]), ZOOM[1]) / s
        if k != 1:
            self.scale(k, k)
            self.centerOn(r.center())

    def reset_zoom(self, pos):
        # 100%, the scene point under pos staying under it
        at = self.mapToScene(pos)
        self.resetTransform()
        d = self.mapFromScene(at) - pos
        self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() + d.x())
        self.verticalScrollBar().setValue(self.verticalScrollBar().value() + d.y())

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.MiddleButton and e.modifiers() & CTRL:
            self.reset_zoom(e.position().toPoint())
            e.accept()
            return
        if e.button() in PAN_BUTTONS:
            self._pan = self._pan0 = e.position()
            self._panned = False
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        if e.button() == LEFT:
            if self._check_hit(e) or self._mark_hit(e) or self._add_hit(e):
                e.accept()
                return
            self._g0 = self._geom()
            self._cancel = False
            pt = e.position().toPoint()
            item = self.itemAt(pt)
            if item is None:
                self._set_cursor(self.mapToScene(pt))
            elif isinstance(item, (CardItem, Block)) and not item._grip_at(
                item.mapFromScene(self.mapToScene(pt))
            ):
                self._press = {
                    "pos": e.position(),
                    "ctrl": bool(e.modifiers() & CTRL),
                    "item": item,
                    "started": False,
                }
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._pan is not None:
            d = e.position() - self._pan
            self._pan = e.position()
            self._panned |= (
                e.position() - self._pan0
            ).manhattanLength() >= QApplication.startDragDistance()
            h, v = self.horizontalScrollBar(), self.verticalScrollBar()
            h.setValue(h.value() - int(d.x()))
            v.setValue(v.value() - int(d.y()))
            return
        if self._cancel:
            return
        if self._press is not None and not self._press["started"]:
            far = (e.position() - self._press["pos"]).manhattanLength()
            if far >= QApplication.startDragDistance():
                self._press["started"] = True
                if self._press["ctrl"] or e.modifiers() & CTRL:
                    self._start_copy()
        if self._copy is None:
            super().mouseMoveEvent(e)
        else:
            self._copy_to(e.position().toPoint())
        if self._g0 is not None:
            self._hint(e.position().toPoint())

    def mouseReleaseEvent(self, e):
        if e.button() in PAN_BUTTONS and self._pan is not None:
            self._pan = None
            self.unsetCursor()
            if e.button() == Qt.MouseButton.RightButton and not self._panned:
                self._context(e.position().toPoint())
            return
        super().mouseReleaseEvent(e)
        if e.button() == LEFT and self._g0 is not None:
            g0, self._g0, self._press = self._g0, None, None
            cancelled, self._cancel = self._cancel, False
            self._unhint()
            if not cancelled and self._geom() != g0:
                self._released(e, g0)

    def _released(self, e, g0):
        if self._copy is not None:
            made, src = self._copy["clones"], self._copy["src"]
            under = self._board_under(e.position().toPoint(), made)
            delta = made[0].pos() - src[0].pos()
            self._end_copy()
            # dropped on its own start cell, outside any board, a copy would only stack up
            if under is not None or not delta.isNull():
                self.copy_dropped.emit(src, delta, under)
            return
        moved = self._dragged(g0)
        under = self._board_under(e.position().toPoint(), moved)
        if moved and under is not None:
            self._restore(g0)
            self.move_into.emit(moved, under)
        else:
            self.changed.emit()

    def mouseDoubleClickEvent(self, e):
        if e.button() == LEFT and self._check_hit(e):
            e.accept()
            return
        if e.button() == LEFT:
            item, pos = self._body_at(e)
            if item is not None and (blk := item.num_hit(pos)) is not None:
                self.enter_row.emit(item.pid, blk)
                e.accept()
                return
        shift = bool(e.modifiers() & SHIFT)
        if e.button() == LEFT and (hit := self._link_hit(e)) is not None:
            self.link_clicked.emit(*hit, shift)
            e.accept()
            return
        item = self.itemAt(e.position().toPoint())
        if isinstance(item, CardItem) and item.type == "page" and shift and item.url:
            self.open_external.emit(item.url)
            return
        if isinstance(item, CardItem):
            self.enter.emit(item.pid)
            return
        if isinstance(item, TextItem) and e.button() == LEFT:
            self.edit_text(item)
            return
        if isinstance(item, ImageItem) and e.button() == LEFT:
            self.image_opened.emit(item)
            return
        if item is None and e.button() == LEFT:
            self._set_cursor(self.mapToScene(e.position().toPoint()))
            self.insert_request.emit(self._at())
            return
        super().mouseDoubleClickEvent(e)

    def _contacts_drop(self, e):
        pt = e.position().toPoint()
        card = next((i for i in self.items(pt) if isinstance(i, CardItem)), None)
        if card is None or card.type != "contacts":
            return None, []
        return card, dropped_files(e.mimeData())

    def _drag(self, e):
        if (
            drop_url(e.mimeData()) is None
            and not drop_images(e.mimeData())
            and not self._contacts_drop(e)[1]
        ):
            e.ignore()
        else:
            e.acceptProposedAction()

    def dragEnterEvent(self, e):
        self._drag(e)

    def dragMoveEvent(self, e):
        self._drag(e)

    def dropEvent(self, e):
        card, files = self._contacts_drop(e)
        if files:
            rows = read_files(self, files)
            if rows is not None:
                self.contacts_added.emit(card.pid, rows)
            e.acceptProposedAction()
            return
        pics, url = drop_images(e.mimeData()), drop_url(e.mimeData())
        if url is None and not pics:
            e.ignore()
            return
        pt = e.position().toPoint()
        at = self.mapToScene(pt)
        cell = QPointF(snap(at.x()), snap(at.y()))
        if pics:
            self.images_dropped.emit(pics, cell)
        else:
            card = next((i for i in self.items(pt) if isinstance(i, CardItem)), None)
            self.url_dropped.emit(url, cell, card)
        e.acceptProposedAction()

    def keyPressEvent(self, e):
        if self._ed is not None:
            super().keyPressEvent(e)
            return
        if self._copy is not None:
            # the look-alikes are no cards: only Esc acts while they are dragged
            if e.key() == Qt.Key.Key_Escape:
                self.cancel_drag()
            return
        k, mods = e.key(), e.modifiers() & MODS
        # h/j/k/l are vim keys: with Vim off only the arrows move
        dirs = DIRS if _cfg["vim"] else ARROWS
        sc = self.scene()
        boxes = [i for i in sc.selectedItems() if isinstance(i, _Box)]
        cards = [i for i in boxes if isinstance(i, CardItem)]
        blocks = [i for i in boxes if isinstance(i, Block)]
        if k in dirs and mods == (CTRL if k in ARROWS else SHIFT):
            for i in boxes:
                i.moveBy(dirs[k][0] * CELL, dirs[k][1] * CELL)
            if boxes:
                self.nudging = True
                self.changed.emit()
                self.nudging = False
        elif k == Qt.Key.Key_Escape and self.cancel_drag():
            pass
        elif mods == CTRL and (k in CTRL_FORMAT or k in CTRL_ALIGN) and texts(blocks):
            # the editing keys of a text block, on the whole text of every selected one
            for t in texts(blocks):
                t.format_all(k)
            self.styled.emit(texts(blocks))
        elif k == Qt.Key.Key_Z and mods == CTRL and self._g0 is None:
            self.undo_request.emit()
        elif self._g0 is None and (
            (k == Qt.Key.Key_Y and mods == CTRL)
            or (k == Qt.Key.Key_Z and mods == CTRL | SHIFT)
        ):
            self.redo_request.emit()
        elif k in dirs and not mods and self.cursor_cell is not None:
            self._move_cursor(dirs[k])
        elif k in dirs and not mods:
            self._navigate(dirs[k])
        elif k in ENTER and len(cards) == 1:
            self.enter.emit(cards[0].pid)
        elif k in ENTER and len(boxes) == 1 and isinstance(boxes[0], TextItem):
            self.edit_text(boxes[0])
        elif k in ENTER and not boxes:
            self.insert_request.emit(self._at())
        elif k == Qt.Key.Key_T and not mods:
            self.new_text_at(self.place())
        elif k == Qt.Key.Key_Q and not mods:
            self.quick_add.emit()
        elif k == Qt.Key.Key_X and mods == CTRL and (cards or blocks):
            self.cut_cards.emit(cards + blocks)
        elif k == Qt.Key.Key_C and mods == CTRL and (cards or blocks):
            self.copy_cards.emit(cards + blocks)
        elif k == Qt.Key.Key_V and mods == CTRL:
            if not self._paste_contacts(boxes, cards):
                self.paste_cards.emit(self._at())
        elif k == Qt.Key.Key_QuoteLeft and not mods and cards:
            self._style(cards)
        elif k == Qt.Key.Key_QuoteLeft and not mods and len(texts(blocks)) == 1:
            self._style_text(texts(blocks)[0])
        elif k == Qt.Key.Key_N and mods == CTRL:
            self._new_card()
        elif k == Qt.Key.Key_F2 and len(boxes) == 1 and not blocks:
            self._rename(boxes[0])
        elif k == Qt.Key.Key_G and mods == CTRL and cards:
            self._group(cards)
        elif k == Qt.Key.Key_F and not mods:
            now = time.monotonic()
            self.fit_all(now - self._fit_t > FIT_AGAIN)
            self._fit_t = now
        elif k == Qt.Key.Key_Delete and sc.selectedItems():
            self.remove.emit()
        else:
            super().keyPressEvent(e)

    def _navigate(self, d):
        cards = [i for i in self.scene().items() if isinstance(i, CardItem)]
        sel = [c for c in cards if c.isSelected()]
        if not cards:
            return
        if sel:
            ref = union(sel).center()

            def rel(c):
                v = c.sceneBoundingRect().center() - ref
                return v.x() * d[0] + v.y() * d[1], abs(v.x() * d[1] - v.y() * d[0])

            ahead = [c for c in cards if not c.isSelected() and rel(c)[0] > 0]
            if not ahead:
                return
            best = min(ahead, key=lambda c: rel(c)[0] + 2 * rel(c)[1])
        else:
            mid = self.mapToScene(self.viewport().rect().center())
            best = min(
                cards,
                key=lambda c: (c.sceneBoundingRect().center() - mid).manhattanLength(),
            )
        self.scene().clearSelection()
        best.setSelected(True)
        self.ensureVisible(best)

    def _edit(self, item, text, done):
        rect, font = item.edit_spec()
        ed = TitleEditor(item, text, rect, font)
        self._ed = ed

        def close(t):
            self._ed = None
            ed.setParentItem(None)
            self.scene().removeItem(ed)
            self.setFocus()
            done(t)

        ed.committed.connect(close)
        ed.cancelled.connect(lambda: close(""))
        self.scene().setFocus()
        ed.setFocus()

    def _rename(self, item):
        def done(t):
            if t:
                item.set_title(t)
                self.renamed.emit(item, t)

        self._edit(item, item.title, done)

    def new_text_at(self, pos):
        t = TextItem()
        t.setPos(pos)
        self.scene().clearSelection()
        self.scene().addItem(t)
        t.setSelected(True)
        self.edit_text(t)

    def edit_text(self, item):
        ed = item.editor = _TextEdit(item)
        self._ed = ed
        ed.document().contentsChanged.connect(item.fit)
        ed.done.connect(lambda: self._text_done(item))
        ed.menu.connect(lambda: self._text_menu(item, ed))
        self.scene().setFocus()
        ed.setFocus()

    def commit_text(self):
        if isinstance(self._ed, _TextEdit):
            self._ed.finish()

    def _text_done(self, item):
        self._ed = None
        item.finish()
        self.setFocus()
        # Esc never cancels; only empty text removes the block
        empty = not item.plain().strip()
        if empty:
            self.scene().removeItem(item)
        if item.bid:
            self.changed.emit()
        elif not empty:
            self.block_added.emit(item)

    def _text_menu(self, item, ed):
        def target():
            c = ed.textCursor()
            if not c.hasSelection():
                c.select(QTextCursor.SelectionType.Document)
            return c

        def pick(key, colour):
            # bg on selected text marks that text; with no selection it fills the block
            if key == "bg" and not ed.textCursor().hasSelection():
                item.bg = colour
                item.update()
            else:
                char_color(target(), key, colour)

        def resume():
            ed.suspended = False
            if self._ed is ed:
                ed.setFocus()

        pop = SwatchPopup(self, _cfg, target, item.update, align=True)
        pop.picked.connect(pick)
        pop.destroyed.connect(resume)
        ed.suspended = True
        pop_at(pop, self, self.mapFromScene(item.sceneBoundingRect()).boundingRect())

    def _context(self, pt):
        if self._ed is not None:
            return
        item = self.itemAt(pt)
        item = item if isinstance(item, _Box) else None
        if item is not None:
            item.setSelected(True)
        at = self.mapToScene(pt)
        menu = self.context_menu(item, QPointF(snap(at.x()), snap(at.y())))
        menu.exec(self.viewport().mapToGlobal(pt))
        menu.deleteLater()

    def context_menu(self, item, at):
        # each entry does what its key does, on the selection the key would act on
        sel = [i for i in self.scene().selectedItems() if isinstance(i, _Box)]
        cards = [i for i in sel if isinstance(i, CardItem)]
        blocks = [i for i in sel if isinstance(i, Block)]
        menu = QMenu(self)

        def add(label, fn, on=True):
            a = menu.addAction(label)
            a.setEnabled(on)
            a.triggered.connect(lambda _checked=False: fn())

        def cut_delete():
            add("Duplicate", lambda: self.duplicate_cards.emit(cards + blocks))
            add("Cut", lambda: self.cut_cards.emit(cards + blocks))
            if isinstance(item, CardItem) and self._can_paste():
                add("Paste", lambda: self.paste_cards.emit(at))
            add("Delete", self.remove.emit)

        if item is None:

            def on_cell(fn):
                self.scene().clearSelection()
                self._set_cursor(at)
                fn(self._at())

            add("Insert...", lambda: on_cell(self.insert_request.emit))
            if self._can_paste():
                add("Paste", lambda: on_cell(self.paste_cards.emit))
        elif isinstance(item, CardItem):
            add(
                "Open Board" if item.type == "board" else "Open",
                lambda: self.enter.emit(item.pid),
                len(cards) == 1,
            )
            if item.type == "board":
                snips = menu.addAction("Receive Snips")
                snips.setCheckable(True)
                snips.setChecked(item.snip)
                snips.setEnabled(sel == [item])
                snips.triggered.connect(
                    lambda _checked=False: self.snip_chosen.emit(item.pid)
                )
            if item.type == "contacts":
                add("Add Contact...", lambda: self._add_contact(item), sel == [item])
            if item.type == "links":
                view = "list" if item.view == "cards" else "cards"
                add(
                    "Cards View" if view == "cards" else "List View",
                    lambda: self.view_toggled.emit(item, view),
                )
            if item.type == "page":
                for label, sig in (
                    ("Open in Browser", lambda: self.open_external.emit(item.url)),
                    ("Refresh Preview", lambda: self.refresh_request.emit(item)),
                ):
                    add(label, sig, bool(item.url))
            add("Rename", lambda: self._rename(item), sel == [item])
            add("Style...", lambda: self._style(cards))
            menu.addSeparator()
            cut_delete()
        elif isinstance(item, FrameItem):
            add("Rename", lambda: self._rename(item), sel == [item])
            add("Delete", self.remove.emit)
        elif isinstance(item, TextItem):
            add("Edit", lambda: self.edit_text(item), sel == [item])
            add("Style...", lambda: self._style_text(item), sel == [item])
            menu.addSeparator()
            cut_delete()
        else:
            add("Note", lambda: self.image_opened.emit(item), sel == [item])
            add(
                "Copy Image",
                lambda: QApplication.clipboard().setImage(item.image),
                item.image is not None,
            )
            menu.addSeparator()
            cut_delete()
        return menu

    def _can_paste(self):
        return self.clip_ready() or not QApplication.clipboard().image().isNull()

    def _style_text(self, item):
        self.edit_text(item)
        self._text_menu(item, item.editor)

    def _style(self, cards):
        cards = sorted(cards, key=lambda c: (c.y(), c.x()))
        pop = CardStylePopup(self, cards)
        pop.styled.connect(self.styled)
        rect = self.mapFromScene(cards[0].sceneBoundingRect()).boundingRect()
        pop_at(pop, self, rect)

    def _new_card(self):
        vp = self.viewport()
        pt = vp.mapFromGlobal(QCursor.pos())
        if not vp.rect().contains(pt):
            pt = vp.rect().center()
        self.create_card_at("list", self.mapToScene(pt))

    def create_card_at(self, kind, pos):
        side = _cfg["card_w"] if kind == "page" else None
        c = CardItem("", "", side, side)
        c.type = kind
        c.setPos(pos)
        self.scene().clearSelection()
        self.scene().addItem(c)
        c.setSelected(True)

        def done(t):
            if t:
                c.set_title(t)
                self.created.emit(c, t)
            else:
                self.scene().removeItem(c)

        self._edit(c, "", done)

    def _group(self, cards):
        f = FrameItem("", union(cards).adjusted(-CELL, -CELL, CELL, CELL))
        self.scene().addItem(f)

        def done(t):
            if t:
                f.set_title(t)
                self.changed.emit()
            else:
                self.scene().removeItem(f)

        self._edit(f, "", done)
