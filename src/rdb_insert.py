import datetime
from collections import namedtuple

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QGraphicsScene,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
)

import rdb_theme
from rdb_contacts import blank_row, summary
from rdb_map import CELL, CardItem, MapView, TextItem

Kind = namedtuple("Kind", "label kind text key")
KINDS = (
    Kind("List", "list", "Numbered checkbox items", "Ctrl+N"),
    Kind("Note", "notepad", "Text page", ""),
    Kind("Text", "text", "Rich text on the map", "t"),
    Kind("Board", "board", "Cards inside a card", ""),
    Kind("Links", "links", "Link collection", ""),
    Kind("Timeline", "timeline", "Gantt chart of tasks", ""),
    Kind("Web page", "page", "Title and tags of a page", ""),
    Kind("Contacts", "contacts", "Names, phones and emails", ""),
)
NAME_FONT = QFont("Tahoma", 8, QFont.Weight.Bold)
GLYPH = 16
NAV = (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown)
TEXT_HTML = "<b>Notes</b><br>Review layout"
TITLES = {
    "list": "Bench",
    "notepad": "Meeting",
    "board": "Projects",
    "links": "Datasheets",
    "timeline": "Schedule",
    "page": "example.com",
    "contacts": "People",
}
ITEMS = ("Order the boards", "Check the schematic", "Update the BOM")
NOTES = ("Agenda", "Review layout", "Order the boards")
LINKS = ("example.com/datasheet", "example.com/parts", "example.com/spec")
PEOPLE = (
    ("Ada Byron", "555-0100"),
    ("Grace Hopper", "555-0101"),
    ("Alan Turing", "555-0102"),
)
BOARD = {
    "count": 3,
    "open": 6,
    "urgent": 1,
    "done": 2,
    "cards": [
        {"title": "Hardware", "open": 4, "urgent": 1},
        {"title": "Firmware", "open": 2, "urgent": 0},
        {"title": "Testing", "open": 0, "urgent": 0},
    ],
}


def contains(q, s):
    at = s.lower().find(q.lower())
    return None if at < 0 else at


def _list(p, c):
    for dy in (-5, 0, 5):
        p.drawRect(QRectF(c.x() - 7, c.y() + dy - 1.5, 3, 3))
        p.drawLine(QPointF(c.x() - 2, c.y() + dy), QPointF(c.x() + 7, c.y() + dy))


def _notepad(p, c):
    for dy, half in ((-5, 7), (0, 7), (5, 4)):
        p.drawLine(
            QPointF(c.x() - 7, c.y() + dy), QPointF(c.x() - 7 + 2 * half, c.y() + dy)
        )


def _text(p, c):
    p.drawLine(QPointF(c.x() - 6, c.y() - 6), QPointF(c.x() + 6, c.y() - 6))
    p.drawLine(QPointF(c.x(), c.y() - 6), QPointF(c.x(), c.y() + 6))


def _board(p, c):
    for dx in (-7, 1):
        for dy in (-7, 1):
            p.drawRect(QRectF(c.x() + dx, c.y() + dy, 6, 6))


def _links(p, c):
    p.drawRoundedRect(QRectF(c.x() - 8, c.y() - 3, 10, 6), 3, 3)
    p.drawRoundedRect(QRectF(c.x() - 2, c.y() - 3, 10, 6), 3, 3)


def _timeline(p, c):
    for dy, dx, w in ((-5, -7, 9), (0, -2, 10), (5, -5, 7)):
        p.drawRect(QRectF(c.x() + dx, c.y() + dy - 1.5, w, 3))


def _page(p, c):
    p.drawEllipse(c, 7, 7)
    p.drawEllipse(c, 3, 7)
    p.drawLine(QPointF(c.x() - 7, c.y()), QPointF(c.x() + 7, c.y()))


def _contacts(p, c):
    p.drawEllipse(QPointF(c.x(), c.y() - 3), 3.5, 3.5)
    p.drawArc(QRectF(c.x() - 7, c.y() + 2, 14, 12), 0, 180 * 16)


GLYPHS = {
    "list": _list,
    "notepad": _notepad,
    "text": _text,
    "board": _board,
    "links": _links,
    "timeline": _timeline,
    "page": _page,
    "contacts": _contacts,
}


def bar(text, level, start, days, pct=0, done=False):
    return {
        "text": text,
        "level": level,
        "start": start,
        "days": days,
        "pct": pct,
        "done": done,
        "bar": "",
        "fg": "",
    }


# a heading over a finished task and the task that follows it
BARS = (
    bar("Launch", 1, datetime.date(2026, 9, 28), 9),
    bar("Design", 0, datetime.date(2026, 9, 28), 4, 100, True),
    bar("Build", 0, datetime.date(2026, 10, 2), 5, 40),
)


def rows(texts, urgent=None, bubble=""):
    return [
        {
            "text": t,
            "bg": bubble if i == 2 else "",
            "fg": "",
            "urgent": i == urgent,
            "p": "p1" if i == urgent else "",
            "block": i,
            "n": i + 1,
        }
        for i, t in enumerate(texts)
    ]


def sample(kind, settings):
    if kind == "text":
        return TextItem(html=TEXT_HTML)
    item = CardItem("", TITLES[kind])
    item.type = kind
    if kind == "page":
        item.tags = ["ic", "pcb"]
    elif kind == "board":
        item.set_summary(BOARD)
    elif kind == "contacts":
        item.set_summary(
            summary([{**blank_row(), "name": n, "phone": t} for n, t in PEOPLE], None)
        )
    elif kind == "timeline":
        item.set_summary(
            {"open": 1, "done": 1, "urgent": 0, "preview": [], "bars": list(BARS)}
        )
    elif kind == "notepad":
        preview = [{**r, "n": None} for r in rows(NOTES)]
        item.set_summary({"open": 0, "done": 0, "urgent": 0, "preview": preview})
    elif kind == "list":
        preview = rows(ITEMS, 1, settings["bubble_bg"][0])
        item.set_summary({"open": 3, "done": 2, "urgent": 1, "preview": preview})
    else:
        item.set_summary({"open": 3, "done": 0, "urgent": 0, "preview": rows(LINKS)})
    return item


class Stage(MapView):
    def __init__(self):
        # the view does not own its scene
        scene = QGraphicsScene()
        super().__init__(scene)
        self.stage = scene
        self.setEnabled(False)
        self.item = None

    def show_item(self, item):
        sc = self.stage
        sc.clear()
        self.item = item
        if item is not None:
            sc.addItem(item)
            sc.setSceneRect(item.sceneBoundingRect().adjusted(-CELL, -CELL, CELL, CELL))
            self.fit()

    def fit(self):
        r, vp = self.sceneRect(), self.viewport()
        s = min(1.0, vp.width() / r.width(), vp.height() / r.height())
        self.resetTransform()
        self.scale(s, s)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.item is not None:
            self.fit()


class RowDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return QSize(0, 2 * QFontMetrics(option.font).height() + 6)

    def paint(self, p, option, index):
        k = KINDS[index.data(Qt.ItemDataRole.UserRole)]
        r, pal, font = option.rect, option.palette, option.font
        sel = bool(option.state & QStyle.StateFlag.State_Selected)
        fg = pal.highlightedText().color() if sel else pal.text().color()
        dim = QColor(fg)
        dim.setAlpha(150)
        fm = QFontMetrics(font)
        left, right = r.left() + 6 + GLYPH + 8, r.right() - 6
        y = r.top() + 3 + QFontMetrics(NAME_FONT).ascent()
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(r, pal.highlight() if sel else pal.base())
        p.setPen(QPen(fg, 1.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        GLYPHS[k.kind](p, QPointF(r.left() + 6 + GLYPH / 2, r.center().y()))
        p.setFont(NAME_FONT)
        p.drawText(QPointF(left, y), k.label)
        p.setFont(font)
        p.setPen(dim)
        if k.key:
            p.drawText(QPointF(right - fm.horizontalAdvance(k.key), y), k.key)
        text = fm.elidedText(k.text, Qt.TextElideMode.ElideRight, right - left)
        p.drawText(QPointF(left, y + fm.height()), text)
        p.restore()


class InsertDialog(QDialog):
    def __init__(self, parent, settings):
        super().__init__(parent)
        self.setWindowTitle("Insert")
        self.settings, self.shown, self.choice = settings, [], None
        self.edit = QLineEdit()
        self.list = QListWidget()
        self.list.setItemDelegate(RowDelegate(self.list))
        self.list.setUniformItemSizes(True)
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.preview = Stage()
        rdb_theme.follow(self, rdb_theme.list_sheet)
        panes = QHBoxLayout()
        panes.setContentsMargins(0, 0, 0, 0)
        panes.setSpacing(0)
        panes.addWidget(self.list, 2)
        panes.addWidget(self.preview, 3)
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        col.addWidget(self.edit)
        col.addLayout(panes, 1)
        self.edit.textChanged.connect(self.refilter)
        self.edit.returnPressed.connect(self.accept_row)
        self.edit.installEventFilter(self)
        self.list.itemDoubleClicked.connect(lambda _i: self.accept_row())
        self.list.currentRowChanged.connect(self.show_preview)
        self.resize(560, 300)
        self.refilter("")

    def refilter(self, q):
        scored = [(s, k) for k in KINDS if (s := contains(q, k.label)) is not None]
        scored.sort(key=lambda t: t[0])
        self.shown = [k for _, k in scored]
        # rebuilding the rows must not preview a row of the old list
        self.list.blockSignals(True)
        self.list.clear()
        for k in self.shown:
            item = QListWidgetItem(self.list)
            item.setData(Qt.ItemDataRole.UserRole, KINDS.index(k))
        self.list.setCurrentRow(0)
        self.list.blockSignals(False)
        self.show_preview(self.list.currentRow())

    def show_preview(self, row):
        kind = self.shown[row].kind if row >= 0 else None
        self.preview.show_item(None if kind is None else sample(kind, self.settings))

    def eventFilter(self, obj, e):
        if e.type() == QEvent.Type.KeyPress and e.key() in NAV:
            QApplication.sendEvent(self.list, e)
            return True
        return super().eventFilter(obj, e)

    def accept_row(self):
        r = self.list.currentRow()
        if r >= 0:
            self.choice = self.shown[r].kind
            self.accept()
