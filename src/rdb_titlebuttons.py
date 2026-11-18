import copy
import re
from collections import namedtuple

from PySide6.QtCore import (
    QEvent,
    QObject,
    QPoint,
    QPointF,
    QRect,
    QSignalBlocker,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontInfo,
    QFontMetrics,
    QIcon,
    QPainter,
    QPixmap,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

import rdb_color
import rdb_model as m
import rdb_theme
from rdb_finder import Target
from rdb_quick import quick_counts
from rdb_triage import PickCard

KEY = "title_tiles"
FONT = QFont("Tahoma", 8)
BORDER_DARKER = 140
TILE_W = 22
TILE_PAD = 6
MAX_TEXT = 4
PRESETS = (
    ("#ffffff", "#2a5db0"),
    ("#000000", "#ffd84a"),
    ("#ffffff", "#b02a2a"),
    ("#ffffff", "#2f8f3f"),
    ("#ffffff", "#7a3fa0"),
    ("#000000", "#c0c0c0"),
    ("#ffffff", "#e07b00"),
    ("#000000", "#7fd4e8"),
    ("#ffffff", "#000000"),
)
GONE = {"text": "?", "fg": "#808080", "bg": "#d4d0c8"}
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
LINK_KEYS = {"kind", "text", "fg", "bg", "target"}
MENU_KEYS = {"kind", "text", "fg", "bg", "items"}
DIVIDER_KEYS = {"kind"}
DIVIDER_W = 6
MAX_DEPTH = 8
ARROW_W = 8
# px between a tile and the bar under it, and the bar's thickness
ACTIVE_GAP, ACTIVE_BAR = 1, 3
NEW_LINK = {
    "kind": "link",
    "text": "",
    "fg": "#ffffff",
    "bg": "#336699",
    "target": {"pid": ""},
}
NEW_MENU = {"kind": "menu", "text": "", "fg": "#ffffff", "bg": "#336699"}

Place = namedtuple("Place", "kind path titles")


def _check(entries, where, depth):
    if not isinstance(entries, list):
        raise ValueError(f"{where}: expected a list, got {entries!r}")
    for i, e in enumerate(entries):
        at = f"{where}[{i}]"
        if not isinstance(e, dict):
            raise ValueError(f"{at}: expected an object, got {e!r}")
        kind = e.get("kind")
        if kind not in ("link", "menu", "divider"):
            raise ValueError(f"{at}: unknown kind {kind!r}")
        keys = {"link": LINK_KEYS, "menu": MENU_KEYS, "divider": DIVIDER_KEYS}[kind]
        if set(e) != keys:
            raise ValueError(f"{at}: keys {sorted(e)} should be {sorted(keys)}")
        if kind == "divider":
            continue
        for k in ("text", "fg", "bg"):
            if not isinstance(e[k], str):
                raise ValueError(f"{at}: {k} is {e[k]!r}, not text")
        if not 1 <= len(e["text"]) <= MAX_TEXT:
            raise ValueError(
                f"{at}: text {e['text']!r} is not 1 to {MAX_TEXT} characters"
            )
        for k in ("fg", "bg"):
            if not HEX.match(e[k]):
                raise ValueError(f"{at}: {k} {e[k]!r} is not #rrggbb")
        if kind == "link":
            t = e["target"]
            if (
                not isinstance(t, dict)
                or set(t) != {"pid"}
                or not isinstance(t["pid"], str)
                or not t["pid"].strip()
            ):
                raise ValueError(f"{at}: target {t!r} is not a place id")
            continue
        if depth + 1 > MAX_DEPTH:
            raise ValueError(f"{at}: menus nest deeper than depth {MAX_DEPTH}")
        _check(e["items"], f"{at}.items", depth + 1)


def validate(entries):
    _check(entries, KEY, 0)


def load_tiles(store):
    entries = store.get_title_tiles()
    validate(entries)
    return entries


def title_text(title):
    # the first letters of a title, for a tile's text
    return re.sub(r"[^A-Za-z0-9]", "", title)[:MAX_TEXT].upper() or title[:MAX_TEXT]


def tile_width(text, menu=False):
    f = QFont(FONT)
    f.setBold(True)
    return max(TILE_W, QFontMetrics(f).horizontalAdvance(text) + TILE_PAD) + (
        ARROW_W if menu else 0
    )


def depth(entry):
    if entry["kind"] != "menu":
        return 0
    return 1 + max((depth(e) for e in entry["items"] if e["kind"] == "menu"), default=0)


def swatch(look):
    bg, fg = rdb_theme.shade(look["bg"]), rdb_theme.shade(look["fg"])
    pm = QPixmap(12, 12)
    pm.fill(bg)
    p = QPainter(pm)
    p.fillRect(4, 4, 4, 4, fg)
    p.setPen(bg.darker(BORDER_DARKER))
    p.drawRect(0, 0, 11, 11)
    p.end()
    return pm


def draw_tile(p, w, h, look, k, edge, down, arrow=False):
    # device pixels, w x h; k is the device pixels per logical pixel, edge the border width in device pixels
    bg, fg = rdb_theme.shade(look["bg"]), rdb_theme.shade(look["fg"])
    p.fillRect(0, 0, w, h, bg)
    shade = bg.darker(BORDER_DARKER)
    for r in (
        QRect(0, 0, w, edge),
        QRect(0, h - edge, w, edge),
        QRect(0, 0, edge, h),
        QRect(w - edge, 0, edge, h),
    ):
        p.fillRect(r, shade)
    f = QFont(FONT)
    f.setBold(True)
    f.setPixelSize(round(QFontInfo(f).pixelSize() * k))
    p.setFont(f)
    p.setPen(fg)
    nudge = edge if down else 0
    aw = round(ARROW_W * k) if arrow else 0
    p.drawText(
        QRect(nudge, nudge, w - aw, h), Qt.AlignmentFlag.AlignCenter, look["text"]
    )
    if arrow:
        cx, cy, r = w - aw // 2 - edge + nudge, h // 2 + nudge, round(2.5 * k)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(fg)
        p.drawPolygon(
            QPolygonF(
                [
                    QPointF(cx - r, cy - r / 2),
                    QPointF(cx + r, cy - r / 2),
                    QPointF(cx, cy + r / 2 + 0.5),
                ]
            )
        )


class Snapshot:
    # every place of the open file at one moment: cards and boards by id, and the notes of image blocks
    def __init__(self, win, image_note):
        self.store = win.store
        self.cards, self.images = {}, {}
        for path, titles, mp in win.maps():
            for c in mp["cards"]:
                self.cards[c["id"]] = Place(
                    c["type"], list(path), (*titles, c["title"])
                )
            for b in mp["blocks"]:
                if b["kind"] == "image" and b["note"]:
                    self.images[b["note"]] = Place(
                        "note", list(path), (*titles, image_note)
                    )

    def find(self, pid):
        if pid in self.cards:
            return self.cards[pid]
        if m.is_note_pid(pid) and self.store.has_project(pid):
            for holder, _ in self.store.note_holders(pid):
                if holder in self.cards:
                    c = self.cards[holder]
                    return Place("note", c.path, (*c.titles, "Note"))
            return self.images.get(pid)
        return None

    def label(self, pid):
        place = self.find(pid)
        return pid if place is None else " / ".join(place.titles)


class Places:
    def __init__(self, win, image_note):
        self.win, self.image_note = win, image_note
        self._kept = None

    def snapshot(self):
        # every write goes through the store's one connection, so its change count tells when the walk is stale
        store = self.win.store
        key = (store, store.db.total_changes)
        if self._kept is None or self._kept[0] != key:
            self._kept = (key, Snapshot(self.win, self.image_note))
        return self._kept[1]

    def on_screen(self):
        w = self.win
        if w.on_urgent or w.stack.currentWidget() is w.triage:
            return set()
        if w.pid:
            return {w.pid, w.owner} if w.owner else {w.pid}
        return {w.board_path[-1]} if w.board_path else set()

    def current(self):
        # (id, title) of the place on screen that a tile can point to, None at the top map
        w = self.win
        if w.on_urgent or w.stack.currentWidget() is w.triage:
            return None
        if w.pid:
            if not w.owner:
                return w.pid, w.cards[w.pid].title
            return w.pid, w.cards[
                w.owner
            ].title if w.owner in w.cards else self.image_note
        return (w.board_path[-1], w.crumb_titles[-1]) if w.board_path else None

    def open(self, pid):
        place = self.snapshot().find(pid)
        if place is None:
            return False
        path = tuple(place.path)
        if place.kind == "board":
            target = Target("board", (*path, pid), None, None)
        else:
            target = Target("note" if place.kind == "note" else "card", path, pid, None)
        self.win.goto(target)
        return True

    def pick(self, pid):
        full = self.win.finder_entries()
        entries = [e for e in full if e.kind in ("board", "card")]
        default = next((e for e in entries if e.pid == pid), None)
        dlg = PlacePicker(self.win, entries, default, quick_counts(full))
        got = dlg.target if dlg.exec() else None
        dlg.deleteLater()
        return None if got is None else got.pid


class PlacePicker(PickCard):
    # the Quick Add location tree with every board and card choosable
    choose = staticmethod(lambda e: True)

    def __init__(self, parent, entries, default, counts):
        super().__init__(parent, entries, default, counts)
        self.setWindowTitle("Target")


class Tile(QAbstractButton):
    # painted in device pixels so the 1 px border and the glyphs stay sharp at any scale
    def __init__(self, entry, height, look=None):
        super().__init__()
        self.entry = entry
        self.height_ = height
        self.active = False
        self.pressable = True
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.set_look(look or entry)

    def set_look(self, look):
        self.look = look
        self.setFixedSize(
            tile_width(look["text"], look.get("kind") == "menu"), self.height_
        )
        self.update()

    def paintEvent(self, e):
        dpr = self.devicePixelRatioF()
        p = QPainter(self)
        p.scale(1 / dpr, 1 / dpr)
        # floor: a rounded-up edge lands outside the widget
        draw_tile(
            p,
            int(self.width() * dpr),
            int(self.height() * dpr),
            self.look,
            dpr,
            1,
            self.isDown() and self.pressable,
            self.look.get("kind") == "menu",
        )
        p.end()


class _Drag:
    # press-and-move along the strip reorders; the release that ends a drag is not a click
    strip = None

    def mousePressEvent(self, e):
        left = e.button() == Qt.MouseButton.LeftButton
        self._press = e.position().toPoint() if left else None
        self._moved = False
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._press is not None and (e.buttons() & Qt.MouseButton.LeftButton):
            moved = e.position().toPoint() - self._press
            if moved.manhattanLength() >= QApplication.startDragDistance():
                self._moved = True
        if self._moved:
            self.strip.drag(self, e.globalPosition().toPoint())
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        moved, self._moved = self._moved, False
        if moved:
            self.setDown(False)
            self.strip.commit_order(self)
            return
        super().mouseReleaseEvent(e)

    def contextMenuEvent(self, e):
        self.strip.menu(self.strip.buttons().index(self)).exec(e.globalPos())


class LinkTile(_Drag, Tile):
    def __init__(self, strip, entry, height):
        super().__init__(entry, height)
        self.strip = strip
        self.clicked.connect(lambda: strip.open(self.entry["target"]["pid"]))


class _Dropdown(QMenu):
    # items carry their index path in action data; a right-click on one asks the strip for its menu
    def __init__(self, strip, items, path, look=None, parent=None):
        super().__init__(
            strip if parent is None else parent
        )  # sub-menus die with the top drop-down
        self.strip, self.path = strip, path
        if look is not None:
            self.setTitle(look["text"])
            self.setIcon(QIcon(swatch(look)))
        for k, it in enumerate(items):
            p = (*path, k)
            if it["kind"] == "menu":
                a = self.addMenu(_Dropdown(strip, it["items"], p, it, self))
            elif it["kind"] == "divider":
                a = self.addSeparator()
            else:
                gone = strip.is_gone(it)
                a = self.addAction(QIcon(swatch(GONE if gone else it)), it["text"])
                a.setEnabled(not gone)
                a.triggered.connect(
                    lambda _=False, pid=it["target"]["pid"]: strip.open(pid)
                )
            a.setData(p)

    def path_of(self, action):
        return tuple(action.data())

    def contextMenuEvent(self, e):
        a = self.actionAt(e.pos())
        if a is not None:
            self.strip.item_menu(self.path_of(a)).exec(e.globalPos())
            self.strip.close_drop()


class MenuTile(_Drag, Tile):
    def __init__(self, strip, entry, height):
        super().__init__(entry, height)
        self.strip = strip
        self.clicked.connect(lambda: strip.popup(self))

    def dropdown(self):
        return _Dropdown(
            self.strip, self.entry["items"], (self.strip.buttons().index(self),)
        )


class DividerTile(_Drag, QAbstractButton):
    # an etched vertical line between tiles, moved and removed like one
    def __init__(self, strip, entry, height):
        super().__init__()
        self.strip, self.entry = strip, entry
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedSize(DIVIDER_W, height)

    def paintEvent(self, e):
        p = QPainter(self)
        x, pal = self.width() // 2 - 1, self.palette()
        p.fillRect(QRect(x, 2, 1, self.height() - 4), pal.mid())
        p.fillRect(QRect(x + 1, 2, 1, self.height() - 4), pal.light())
        p.end()


class GoneTile(_Drag, Tile):
    # a link whose place no longer exists: kept so Settings can repoint it
    def __init__(self, strip, entry, height):
        super().__init__(entry, height, GONE)
        self.strip = strip
        # enabled so it still takes a right-click and a drag; it has no click and no press nudge
        self.pressable = False


class LinkEditor(QWidget):
    edited = Signal()

    def __init__(self, places, height, entry=None, menu=False):
        super().__init__()
        self.menu, self.places = menu, places
        e = dict(entry or (NEW_MENU if menu else NEW_LINK))
        self.colors = {"fg": e["fg"], "bg": e["bg"]}
        self.text = QLineEdit(e["text"])
        self.text.setMaxLength(MAX_TEXT)
        self.text.setFixedWidth(48)
        self.buttons = {}
        for k in ("fg", "bg"):
            b = self.buttons[k] = QPushButton()
            b.setFixedWidth(80)
            b.clicked.connect(lambda _=False, k=k: self._pick(k))
        self.presets = []
        for fg, bg in PRESETS:
            t = Tile({"text": "Aa", "fg": fg, "bg": bg}, height)
            t.clicked.connect(lambda _=False, fg=fg, bg=bg: self._preset(fg, bg))
            self.presets.append(t)
        lay = QGridLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        top = QHBoxLayout()
        for label, w in (
            ("Text", self.text),
            ("Foreground", self.buttons["fg"]),
            ("Background", self.buttons["bg"]),
        ):
            top.addWidget(QLabel(label))
            top.addWidget(w)
        top.addStretch(1)
        lay.addLayout(top, 0, 0)
        row = QHBoxLayout()
        row.setSpacing(2)
        for t in self.presets:
            row.addWidget(t)
        row.addStretch(1)
        lay.addLayout(row, 1, 0)
        self.text.textChanged.connect(self.edited)
        if not menu:
            self.pid = e["target"]["pid"]
            self.target = QLineEdit()
            self.target.setReadOnly(True)
            choose = QPushButton("Choose...")
            choose.setAutoDefault(False)
            choose.clicked.connect(self._choose)
            self.current_btn = QPushButton("Current View")
            self.current_btn.setAutoDefault(False)
            self.current_btn.setEnabled(places.current() is not None)
            self.current_btn.clicked.connect(self._current)
            row = QHBoxLayout()
            row.addWidget(QLabel("Target"))
            row.addWidget(self.target, 1)
            row.addWidget(choose)
            row.addWidget(self.current_btn)
            lay.addLayout(row, 2, 0)
            self._show_target()
        self._paint()

    def _show_target(self):
        self.target.setText(self.places.snapshot().label(self.pid) if self.pid else "")

    def _paint(self):
        for k, b in self.buttons.items():
            b.setText(self.colors[k])
            face = rdb_theme.shade(self.colors[k])
            ink = "#000000" if face.lightness() > 127 else "#ffffff"
            b.setStyleSheet(f"background: {face.name()}; color: {ink}")

    def set_entry(self, e):
        with QSignalBlocker(self.text):
            self.text.setText(e["text"])
        if not self.menu:
            self.pid = e["target"]["pid"]
            self._show_target()
        self.colors = {"fg": e["fg"], "bg": e["bg"]}
        self._paint()

    def set_color(self, key, name):
        self.colors[key] = name
        self._paint()
        self.edited.emit()

    def _preset(self, fg, bg):
        self.colors = {"fg": fg, "bg": bg}
        self._paint()
        self.edited.emit()

    def _pick(self, key):
        # parented to the window, not the button: the button's colour style sheet would cascade into it
        c = rdb_color.get_color(self.window(), QColor(self.colors[key]))
        if c.isValid():
            self.set_color(key, c.name())

    def _choose(self):
        pid = self.places.pick(self.pid)
        if pid is not None:
            self.set_target(pid)

    def _current(self):
        pid, title = self.places.current()
        if not self.text.text():
            self.text.setText(title_text(title))
        self.set_target(pid)

    def set_target(self, pid):
        self.pid = pid
        self._show_target()
        self.edited.emit()

    def entry(self):
        # a menu comes back without its items; the caller keeps them
        if self.menu:
            return {"kind": "menu", "text": self.text.text(), **self.colors}
        return {
            "kind": "link",
            "text": self.text.text(),
            **self.colors,
            "target": {"pid": self.pid},
        }

    def valid(self):
        e = self.entry()
        return bool(e["text"]) and (self.menu or bool(self.pid))


class LinkDialog(QDialog):
    def __init__(self, strip, entry=None, menu=False):
        super().__init__(strip.win)
        self.setWindowTitle("Menu" if menu else "Link")
        self.editor = LinkEditor(strip.places, strip.tile_h, entry, menu)
        self.preview = Tile(self.editor.entry(), strip.tile_h)
        self.box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.box.accepted.connect(self.accept)
        self.box.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)
        lay.addWidget(self.editor)
        row = QHBoxLayout()
        row.addWidget(self.preview)
        row.addStretch(1)
        row.addWidget(self.box)
        lay.addLayout(row)
        self.editor.edited.connect(self._sync)
        self._sync()

    def entry(self):
        return self.editor.entry()

    def _sync(self):
        self.preview.entry = e = self.editor.entry()
        self.preview.set_look(e)
        self.box.button(QDialogButtonBox.StandardButton.Ok).setEnabled(
            self.editor.valid()
        )


class StripPreview(QWidget):
    # the strip at 2x, in order: click selects, drag reorders, Delete removes, double-click asks for the editor
    selected_changed = Signal(int)
    activated = Signal(int)
    GAP, MARGIN = 2, 4

    def __init__(self, strip):
        super().__init__()
        self.strip, self.selected, self._press, self._dragging = strip, -1, None, False
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.refresh()

    def count(self):
        return len(self.strip.entries)

    def _cell(self, e):
        # (width, height) in preview pixels
        w = (
            DIVIDER_W
            if e["kind"] == "divider"
            else tile_width(e["text"], e["kind"] == "menu")
        )
        return 2 * w, 2 * self.strip.tile_h

    def tile_rect(self, i):
        x = self.MARGIN
        for e in self.strip.entries[:i]:
            x += self._cell(e)[0] + self.GAP
        w, h = self._cell(self.strip.entries[i])
        return QRect(x, self.MARGIN, w, h)

    def sizeHint(self):
        h = 2 * self.strip.tile_h + 2 * self.MARGIN
        n = self.count()
        w = self.tile_rect(n - 1).right() + 1 + self.MARGIN if n else 2 * self.MARGIN
        return QSize(w, h)

    def refresh(self):
        self.setFixedSize(self.sizeHint())
        self.update()

    def select(self, i):
        if i != self.selected:
            self.selected = i
            self.selected_changed.emit(i)
        self.update()

    def _at(self, pos):
        return next(
            (
                i
                for i in range(self.count())
                if self.tile_rect(i).adjusted(-1, 0, 1, 0).contains(pos)
            ),
            -1,
        )

    def paintEvent(self, e):
        dpr = self.devicePixelRatioF()
        p = QPainter(self)
        p.scale(1 / dpr, 1 / dpr)
        p.fillRect(
            0,
            0,
            int(self.width() * dpr),
            int(self.height() * dpr),
            self.palette().window(),
        )
        for i, entry in enumerate(self.strip.entries):
            r = self.tile_rect(i)
            x, y, w, h = (int(v * dpr) for v in (r.x(), r.y(), r.width(), r.height()))
            if entry["kind"] == "divider":
                p.fillRect(QRect(x + w // 2 - 2, y + 4, 2, h - 8), self.palette().mid())
                p.fillRect(QRect(x + w // 2, y + 4, 2, h - 8), self.palette().light())
            else:
                p.save()
                p.translate(x, y)
                look = GONE if self.strip.is_gone(entry) else entry
                draw_tile(p, w, h, look, 2 * dpr, 2, False, entry["kind"] == "menu")
                p.restore()
            if i == self.selected:
                p.setPen(rdb_theme.selection_pen())
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(QRect(x - 2, y - 2, w + 3, h + 3))
        p.end()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.setFocus()
            self.select(self._at(e.position().toPoint()))
            self._press = e.position().toPoint() if self.selected >= 0 else None
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._press is None or not (e.buttons() & Qt.MouseButton.LeftButton):
            return
        pos = e.position().toPoint()
        far = (pos - self._press).manhattanLength() >= QApplication.startDragDistance()
        if not self._dragging and not far:
            return
        self._dragging = True
        to = sum(
            1
            for j in range(self.count())
            if j != self.selected and self.tile_rect(j).center().x() < pos.x()
        )
        if to != self.selected:
            i, self.selected = self.selected, to
            self.strip.move(i, to)
            self.selected_changed.emit(to)

    def mouseReleaseEvent(self, e):
        self._press, self._dragging = None, False

    def mouseDoubleClickEvent(self, e):
        i = self._at(e.position().toPoint())
        if i >= 0:
            self.activated.emit(i)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Delete and self.selected >= 0:
            self.strip.remove(self.selected)
        elif e.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right) and self.count():
            step = -1 if e.key() == Qt.Key.Key_Left else 1
            self.select(max(0, min(self.count() - 1, self.selected + step)))
        else:
            super().keyPressEvent(e)


class _StepKeys(QObject):
    # numpad + and - open the next and previous tile's place from anywhere in the main window,
    # the list editor included; one-line fields keep them for typing
    def __init__(self, strip):
        super().__init__(strip)
        self.strip = strip

    def eventFilter(self, obj, ev):
        if (
            ev.type() == QEvent.Type.KeyPress
            and ev.modifiers() == Qt.KeyboardModifier.KeypadModifier
            and ev.key() in (Qt.Key.Key_Plus, Qt.Key.Key_Minus)
            and isinstance(obj, QWidget)
            and not isinstance(obj, (QLineEdit, QAbstractSpinBox, QComboBox))
            and obj.window() is self.strip.win
            and self.strip.links()
        ):
            self.strip.step(1 if ev.key() == Qt.Key.Key_Plus else -1)
            return True
        return False


class ButtonStrip(QWidget):
    changed = Signal()

    def __init__(self, bar_h):
        super().__init__()
        # the tile leaves 1 px above and, below, the gap and the bar of an active tile, so the bar never grows
        self.tile_h = bar_h - 1 - ACTIVE_GAP - ACTIVE_BAR
        self.win = self.places = self.snap = None
        self.entries, self._buttons, self._gone = [], [], []
        self._target = self._drop = None
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self.lay = QHBoxLayout(self)
        self.lay.setContentsMargins(0, 1, 2, ACTIVE_GAP + ACTIVE_BAR)
        self.lay.setSpacing(1)
        self.hide()

    def paintEvent(self, e):
        # a bar below each tile leading to the place on screen, the tile's width, in the caption text colour
        p = QPainter(self)
        for b in self._buttons:
            if isinstance(b, Tile) and b.active:
                g = b.geometry()
                p.fillRect(
                    QRect(g.x(), g.bottom() + 1 + ACTIVE_GAP, g.width(), ACTIVE_BAR),
                    self.palette().windowText(),
                )
        p.end()

    def attach(self, win, image_note):
        self.win, self.places = win, Places(win, image_note)
        self._keys = _StepKeys(self)
        QApplication.instance().installEventFilter(self._keys)

    def links(self):
        # every link a tile or drop-down leads to, in strip order
        def walk(entries):
            for e in entries:
                if e["kind"] == "menu":
                    yield from walk(e["items"])
                elif e["kind"] == "link" and not self.is_gone(e):
                    yield e["target"]["pid"]

        return list(walk(self.entries)) if self.entries else []

    def step(self, d):
        pids = self.links()
        if not pids:
            return
        here = self.places.on_screen()
        at = next((i for i, pid in enumerate(pids) if pid in here), None)
        if at is None:
            at = -1 if d > 0 else 0
        self.open(pids[(at + d) % len(pids)])

    def load(self, entries):
        self.entries = entries
        self._refresh()

    def _refresh(self):
        self.snap = self.places.snapshot() if self.entries else None
        self._rebuild()

    def sync(self):
        # after any navigation or removal: a target may have been deleted, and the active tile changes
        if not self.entries:
            return
        self.snap = self.places.snapshot()
        if self._gone != [self.is_gone(e) for e in self.entries]:
            self._rebuild()
        else:
            self.set_active()

    def is_gone(self, e):
        return e["kind"] == "link" and self.snap.find(e["target"]["pid"]) is None

    def buttons(self):
        return list(self._buttons)

    def _rebuild(self):
        for b in self._buttons:
            self.lay.removeWidget(b)
            b.setParent(None)
            b.deleteLater()
        self._buttons = []
        self._gone = [self.is_gone(e) for e in self.entries]
        for e, gone in zip(self.entries, self._gone, strict=True):
            if gone:
                b = GoneTile(self, e, self.tile_h)
            elif e["kind"] == "divider":
                b = DividerTile(self, e, self.tile_h)
            else:
                b = (MenuTile if e["kind"] == "menu" else LinkTile)(
                    self, e, self.tile_h
                )
            self.lay.addWidget(b, 0, Qt.AlignmentFlag.AlignVCenter)
            self._buttons.append(b)
        self.setVisible(bool(self.entries))
        self.updateGeometry()
        self.set_active()

    def set_active(self):
        # the link and menu tiles leading to the place on screen get the bar below
        here = self.places.on_screen()

        def leads(e):
            if e["kind"] == "menu":
                return any(leads(x) for x in e["items"])
            return e["kind"] == "link" and e["target"]["pid"] in here

        for b in self._buttons:
            if isinstance(b, Tile):
                b.active = leads(b.entry)
        self.update()

    def open(self, pid):
        if not self.places.open(pid):
            self.sync()

    def commit(self, entries):
        validate(entries)
        self.win.store.put_title_tiles(entries)
        self.entries = entries
        self._refresh()
        self.changed.emit()

    # an item is addressed by its index path: (2,) is the third bar entry, (2, 0, 1) the second item of the first
    # sub-menu of the third; an int is the one-element path
    @staticmethod
    def _path(i):
        return (i,) if isinstance(i, int) else tuple(i)

    @staticmethod
    def _items(entries, parent):
        for k in parent:
            entries = entries[k]["items"]
        return entries

    def item(self, i):
        path = self._path(i)
        return self._items(self.entries, path[:-1])[path[-1]]

    def _insert(self, parent, entry):
        entries = copy.deepcopy(self.entries)
        self._items(entries, parent).append(entry)
        self.commit(entries)

    def add_link(self, text, fg, bg, pid, parent=()):
        self._insert(
            parent,
            {"kind": "link", "text": text, "fg": fg, "bg": bg, "target": {"pid": pid}},
        )

    def add_menu(self, text, fg, bg, parent=()):
        self._insert(
            parent, {"kind": "menu", "text": text, "fg": fg, "bg": bg, "items": []}
        )

    def add_divider(self, parent=()):
        self._insert(parent, {"kind": "divider"})

    def edit(self, i, entry):
        path = self._path(i)
        entries = copy.deepcopy(self.entries)
        self._items(entries, path[:-1])[path[-1]] = entry
        self.commit(entries)

    def remove(self, i):
        path = self._path(i)
        entries = copy.deepcopy(self.entries)
        del self._items(entries, path[:-1])[path[-1]]
        self.commit(entries)

    def relocate(self, i, parent, index=None):
        # move an item into the menu at `parent` (the bar for ()), at `index` or appended
        path, parent = self._path(i), tuple(parent)
        if parent[: len(path)] == path:
            raise ValueError(f"cannot move {path} into itself")
        entries = copy.deepcopy(self.entries)
        dest = self._items(
            entries, parent
        )  # taken before the pop so a shifted index cannot misdirect it
        item = self._items(entries, path[:-1]).pop(path[-1])
        dest.insert(len(dest) if index is None else index, item)
        self.commit(entries)

    def move(self, i, j):
        path = self._path(i)
        self.relocate(path, path[:-1], j)

    def drop_target(self, btn, x):
        # a menu tile under the pointer's middle half takes a link or menu dragged onto it; a divider only reorders
        if btn.entry["kind"] == "divider" or 1 + depth(btn.entry) > MAX_DEPTH:
            return None
        for b in self._buttons:
            g = b.geometry()
            if (
                b is not btn
                and b.entry["kind"] == "menu"
                and g.x() + g.width() // 4 <= x < g.right() - g.width() // 4
            ):
                return b
        return None

    def drag(self, btn, global_pos):
        x = self.mapFromGlobal(global_pos).x()
        if self._target is not None:
            self._target.setDown(False)
        self._target = self.drop_target(btn, x)
        if self._target is not None:
            self._target.setDown(True)
            return
        to = sum(
            1 for b in self._buttons if b is not btn and b.geometry().center().x() < x
        )
        if to != self._buttons.index(btn):
            self._buttons.remove(btn)
            self._buttons.insert(to, btn)
            self.lay.removeWidget(btn)
            self.lay.insertWidget(to, btn)
            self.lay.activate()

    def _index(self, entry):
        return next(n for n, e in enumerate(self.entries) if e is entry)

    def commit_order(self, btn):
        target, self._target = self._target, None
        if target is not None:
            target.setDown(False)
            self.relocate(self._index(btn.entry), (self._index(target.entry),))
            return
        new = [b.entry for b in self._buttons]
        if any(a is not b for a, b in zip(new, self.entries, strict=True)):
            self.commit(new)

    def menu(self, i=None):
        m = QMenu(self)
        here = self.places.current() is not None
        m.addAction("Add Link...", lambda: self.prompt_add_link())
        m.addAction("Add Menu...", lambda: self.prompt_add_menu())
        m.addAction("Add Current...", lambda: self.prompt_add_current()).setEnabled(
            here
        )
        m.addAction("Add Divider", lambda: self.add_divider())
        if i is not None:
            if (e := self.entries[i])["kind"] == "menu":
                m.addSeparator()
                m.addAction(
                    f"Add Link to {e['text']}...", lambda: self.prompt_add_link((i,))
                )
                m.addAction(
                    f"Add Menu to {e['text']}...", lambda: self.prompt_add_menu((i,))
                )
                m.addAction(
                    f"Add Current to {e['text']}...",
                    lambda: self.prompt_add_current((i,)),
                ).setEnabled(here)
                m.addAction(
                    f"Add Divider to {e['text']}", lambda: self.add_divider((i,))
                )
            m.addSeparator()
            if e["kind"] != "divider":
                m.addAction("Edit...", lambda: self.prompt_edit(i))
            m.addAction("Remove", lambda: self.remove(i))
        return m

    def item_menu(self, path):
        m = QMenu(self)
        kind = self.item(path)["kind"]
        if kind == "menu":
            m.addAction("Add Link...", lambda: self.prompt_add_link(path))
            m.addAction("Add Menu...", lambda: self.prompt_add_menu(path))
            m.addAction("Add Divider", lambda: self.add_divider(path))
            m.addSeparator()
        if kind != "divider":
            m.addAction("Edit...", lambda: self.prompt_edit(path))
        m.addAction("Remove", lambda: self.remove(path))
        return m

    def popup(self, tile):
        self._drop = m = tile.dropdown()
        if not m.isEmpty():
            m.exec(tile.mapToGlobal(QPoint(0, tile.height())))
        m.deleteLater()
        self._drop = None

    def close_drop(self):
        if self._drop is not None:
            self._drop.close()

    def prompt_add_link(self, parent=(), entry=None):
        dlg = LinkDialog(self, entry)
        if dlg.exec():
            e = dlg.entry()
            self.add_link(e["text"], e["fg"], e["bg"], e["target"]["pid"], parent)

    def prompt_add_current(self, parent=()):
        # the place on screen as a link, its tile text the first letters of its title
        pid, title = self.places.current()
        text = title_text(title)
        self.prompt_add_link(parent, {**NEW_LINK, "text": text, "target": {"pid": pid}})

    def prompt_add_menu(self, parent=()):
        dlg = LinkDialog(self, menu=True)
        if dlg.exec():
            e = dlg.entry()
            self.add_menu(e["text"], e["fg"], e["bg"], parent)

    def prompt_edit(self, i):
        cur = self.item(i)
        dlg = LinkDialog(self, cur, cur["kind"] == "menu")
        if dlg.exec():
            self.edit(i, {**cur, **dlg.entry()})


class TitleTab(QWidget):
    # the Settings tab: the strip at 2x, its tree and an editor for the selected item; edits apply to the open
    # file at once, and revert() puts back the strip the tab opened with
    def __init__(self, strip):
        super().__init__()
        self.strip = strip
        self.before = copy.deepcopy(strip.entries)
        pv = self.preview = StripPreview(strip)
        scroll = QScrollArea()
        scroll.setWidget(pv)
        scroll.setFixedHeight(
            pv.height() + scroll.horizontalScrollBar().sizeHint().height() + 4
        )
        led = self.link_editor = LinkEditor(strip.places, strip.tile_h)
        med = self.menu_editor = LinkEditor(strip.places, strip.tile_h, menu=True)
        stack = self.stack = QStackedWidget()
        for w in (QWidget(), led, med):
            stack.addWidget(w)
        tree = self.tree = QTreeWidget()
        tree.setHeaderHidden(True)
        tree.setFixedHeight(150)
        self._busy, self._count, self.path = False, len(strip.entries), None
        pv.selected_changed.connect(lambda i: self._select((i,) if i >= 0 else None))
        tree.currentItemChanged.connect(self._tree)
        pv.activated.connect(lambda i: self._activate((i,)))
        led.edited.connect(lambda: self._edit(led))
        med.edited.connect(lambda: self._edit(med))
        strip.changed.connect(self._changed)
        self.add_link_btn = QPushButton("Add Link")
        self.add_menu_btn = QPushButton("Add Menu")
        self.add_current_btn = QPushButton("Add Current")
        self.add_current_btn.setEnabled(strip.places.current() is not None)
        self.add_divider_btn = QPushButton("Add Divider")
        self.up_btn, self.down_btn = QPushButton("Up"), QPushButton("Down")
        self.out_btn, self.remove_btn = QPushButton("Out"), QPushButton("Remove")
        self.add_link_btn.clicked.connect(lambda: self._add(strip.prompt_add_link))
        self.add_menu_btn.clicked.connect(
            lambda: self._add(lambda p: strip.add_menu("NM", *PRESETS[0], p))
        )
        self.add_current_btn.clicked.connect(
            lambda: self._add(strip.prompt_add_current)
        )
        self.add_divider_btn.clicked.connect(lambda: self._add(strip.add_divider))
        self.up_btn.clicked.connect(lambda: self._step(-1))
        self.down_btn.clicked.connect(lambda: self._step(1))
        self.out_btn.clicked.connect(self._out)
        self.remove_btn.clicked.connect(
            lambda: self.path is not None and strip.remove(self.path)
        )
        foot = QHBoxLayout()
        for b in (
            self.add_link_btn,
            self.add_menu_btn,
            self.add_current_btn,
            self.add_divider_btn,
            self.up_btn,
            self.down_btn,
            self.out_btn,
            self.remove_btn,
        ):
            b.setAutoDefault(False)
            foot.addWidget(b)
        foot.addStretch(1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(6)
        lay.addWidget(scroll)
        lay.addWidget(tree)
        lay.addWidget(stack)
        lay.addLayout(foot)
        lay.addStretch(1)
        self._fill()

    def release(self):
        self.strip.changed.disconnect(self._changed)

    def revert(self):
        if self.strip.entries != self.before:
            self.strip.commit(copy.deepcopy(self.before))

    def _fill(self):
        strip = self.strip

        def add(parent, entries, path):
            for k, e in enumerate(entries):
                text = "Divider" if e["kind"] == "divider" else e["text"]
                it = QTreeWidgetItem(parent, [text])
                it.setData(0, Qt.ItemDataRole.UserRole, (*path, k))
                if e["kind"] in ("link", "menu"):
                    it.setIcon(0, QIcon(swatch(GONE if strip.is_gone(e) else e)))
                if e["kind"] == "menu":
                    add(it, e["items"], (*path, k))

        with QSignalBlocker(self.tree):
            self.tree.clear()
            add(self.tree.invisibleRootItem(), strip.entries, ())
            self.tree.expandAll()

    def _item(self, path):
        if path is None:
            return None
        it = self.tree.topLevelItem(path[0])
        for k in path[1:]:
            it = it.child(k) if it is not None else None
        return it

    def _valid(self, path):
        items = self.strip.entries
        for n, k in enumerate(path):
            if k >= len(items):
                return False
            if n < len(path) - 1:
                if items[k]["kind"] != "menu":
                    return False
                items = items[k]["items"]
        return True

    def _tree(self, cur, _prev):
        self._select(
            tuple(cur.data(0, Qt.ItemDataRole.UserRole)) if cur is not None else None
        )

    def _select(self, path, load=True):
        self.path = path
        with QSignalBlocker(self.tree):
            self.tree.setCurrentItem(self._item(path))
        pv = self.preview
        pv.selected = path[0] if path is not None and len(path) == 1 else -1
        pv.update()
        if load:
            self._load(path)

    def _load(self, path):
        if path is None:
            self.stack.setCurrentIndex(0)
            return
        e = self.strip.item(path)
        if e["kind"] == "divider":
            self.stack.setCurrentIndex(0)
            return
        ed = self._editor(e["kind"])
        ed.set_entry(e)
        self.stack.setCurrentWidget(ed)

    def _editor(self, kind):
        return {"link": self.link_editor, "menu": self.menu_editor}[kind]

    def _activate(self, path):
        kind = self.strip.item(path)["kind"]
        if kind == "divider":
            return
        ed = self._editor(kind)
        ed.text.setFocus()
        ed.text.selectAll()

    def _edit(self, ed):
        path = self.path
        if not ed.valid() or path is None:
            return
        strip, entry = self.strip, ed.entry()
        if ed.menu:
            entry = {**strip.item(path), **entry}
        self._busy = True
        try:
            strip.edit(path, entry)
        finally:
            self._busy = False

    def _items_at(self, parent):
        return self.strip.entries if not parent else self.strip.item(parent)["items"]

    def _add(self, add):
        strip, path = self.strip, self.path
        parent = (
            ()
            if path is None
            else path
            if strip.item(path)["kind"] == "menu"
            else path[:-1]
        )
        before = len(self._items_at(parent))
        add(parent)
        n = len(self._items_at(parent))
        if n > before:
            self._select((*parent, n - 1))

    def _step(self, d):
        strip, path = self.strip, self.path
        if path is None:
            return
        k = path[-1] + d
        if 0 <= k < len(self._items_at(path[:-1])):
            strip.move(path, k)
            self._select((*path[:-1], k))

    def _out(self):
        path = self.path
        if path is not None and len(path) > 1:
            self.strip.relocate(path, (), path[0] + 1)
            self._select((path[0] + 1,))

    def _changed(self):
        n, pv = len(self.strip.entries), self.preview
        grew, self._count = n > self._count, n
        pv.refresh()
        self._fill()
        path = (n - 1,) if grew else self.path
        while path is not None and not self._valid(path):
            path = path[:-1] or ((min(path[0], n - 1),) if n else None)
        self._select(path, not self._busy)
