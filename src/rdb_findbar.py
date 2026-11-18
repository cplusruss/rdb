import math

from PySide6.QtCore import QPoint, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import (
    QIcon,
    QPainter,
    QPalette,
    QPen,
    QPixmap,
    QPolygonF,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

import rdb_model as m
import rdb_paths
import rdb_theme
from rdb_finder import CHAR, MarkToggle, fuzzy_match, term_match
from rdb_map import Block, CardItem, FindMark, TextItem

# a term must earn CHAR for each letter typed: a scattered match may not lose more to its gaps
# than the word starts and the prefix give back
MIN_PER_CHAR = CHAR
SEL_BG, SEL_CUR = "#ffff66", "#ff9632"
ENTER = (Qt.Key.Key_Return, Qt.Key.Key_Enter)
SHIFT = Qt.KeyboardModifier.ShiftModifier


def _gear_points(teeth=8, outer=7.5, inner=5.4):
    out = []
    for i in range(teeth):
        a = 2 * math.pi * i / teeth
        for r, d in ((inner, -0.3), (outer, -0.18), (outer, 0.18), (inner, 0.3)):
            out += [8 + r * math.cos(a + d), 8 + r * math.sin(a + d)]
    return tuple(out)


# rdoc's glyph style on a 16x16 grid: bars on whole device pixels, a thin bar takes the stroke weight, polygons antialiased
GLYPHS = {
    "pin": (("p", 5, 2, 11, 2, 10, 8, 6, 8), ("r", 3, 8, 10, 2), ("r", 7, 10, 2, 5)),
    "isolate": (("p", 1, 2, 15, 2, 10, 8, 10, 14, 6, 12, 6, 8),),
    "restore": (("p", 1, 8.5, 3.2, 6.3, 6.5, 9.6, 12.8, 2.6, 15, 4.8, 6.5, 14),),
    "gear": (("p", *_gear_points()), ("o", 8, 8, 2.4)),
    "close": (("l", 3.5, 3.5, 12.5, 12.5), ("l", 12.5, 3.5, 3.5, 12.5)),
    "tag": (
        ("r", 2, 5, 12, 2),
        ("r", 2, 9, 12, 2),
        ("r", 5, 2, 2, 12),
        ("r", 9, 2, 2, 12),
    ),
    "docBold": (
        ("r", 3, 2, 3, 12),
        ("r", 3, 2, 7, 3),
        ("r", 3, 6, 8, 3),
        ("r", 3, 11, 8, 3),
        ("r", 9, 3, 3, 4),
        ("r", 10, 8, 3, 4),
    ),
    "docItalic": (("r", 7, 2, 6, 2), ("r", 3, 12, 6, 2), ("l", 10.5, 3.5, 6.5, 12.5)),
    "docUnderline": (
        ("r", 3, 1, 2, 9),
        ("r", 11, 1, 2, 9),
        ("r", 4, 9, 8, 2),
        ("r", 2, 13, 12, 2),
    ),
    "docColor": (
        ("l", 3.5, 11, 8, 2),
        ("l", 8, 2, 12.5, 11),
        ("r", 5.5, 8, 5, 2),
        ("r", 2, 13, 12, 2),
    ),
    "docBullets": (
        ("r", 1, 1, 4, 4),
        ("r", 7, 2, 8, 2),
        ("r", 1, 6, 4, 4),
        ("r", 7, 7, 8, 2),
        ("r", 1, 11, 4, 4),
        ("r", 7, 12, 8, 2),
    ),
    "docNumbers": (
        ("l", 2, 4, 4, 2),
        ("l", 4, 2, 4, 7),
        ("l", 2.5, 9.5, 6, 9.5),
        ("l", 6, 9.5, 6, 11.5),
        ("l", 6, 11.5, 2.5, 14.5),
        ("l", 2.5, 14.5, 6.5, 14.5),
        ("r", 8, 4, 7, 2),
        ("r", 8, 11, 7, 2),
    ),
    "wrap": (
        ("r", 1, 2, 14, 2),
        ("r", 1, 7, 11, 2),
        ("r", 12, 7, 2, 6),
        ("r", 7, 12, 5, 2),
        ("p", 3, 13, 7, 10.5, 7, 15.5),
    ),
    "link": (
        ("r", 2, 5, 5, 2),
        ("r", 2, 9, 5, 2),
        ("r", 2, 5, 2, 6),
        ("r", 9, 5, 5, 2),
        ("r", 9, 9, 5, 2),
        ("r", 12, 5, 2, 6),
        ("r", 5, 7, 6, 2),
    ),
}
STROKE = 1.5
# the find bar's status icons by size setting: marker scale, glyph size and the row height they share
BAR_SIZES = {"Small": (1.0, 16, 20), "Medium": (1.25, 18, 25), "Large": (1.6, 22, 32)}


def glyph_icon(name, colour, dpr, size=16):
    px = round(size * dpr)
    pm = QPixmap(px, px)
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(colour)
    p.scale(size / 16, size / 16)
    # device pixels per grid unit: the stroke and bar edges land on whole device pixels
    d = dpr * size / 16
    stroke = max(1, round(STROKE * d)) / d

    def snap(v):
        return round(v * d) / d

    def thick(v, w):
        c = v + w / 2
        if abs(c - 8) <= 1.5:
            return c - stroke / 2
        return v if c < 8 else v + w - stroke

    for kind, *a in GLYPHS[name]:
        if kind == "l":
            p.setPen(
                QPen(colour, stroke, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            )
            p.drawLine(QPointF(a[0], a[1]), QPointF(a[2], a[3]))
            p.setPen(Qt.PenStyle.NoPen)
            continue
        if kind == "o":
            # a round hole through what is drawn so far
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            p.drawEllipse(QPointF(a[0], a[1]), a[2], a[2])
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            continue
        if kind == "p":
            p.drawPolygon(
                QPolygonF([QPointF(a[i], a[i + 1]) for i in range(0, len(a), 2)])
            )
            continue
        x, y, w, h = a
        if w < h:
            x, w = thick(x, w), stroke
        else:
            y, h = thick(y, h), stroke
        x0, y0 = snap(x), snap(y)
        p.drawRect(
            QRectF(
                x0,
                y0,
                stroke if w == stroke else snap(x + w) - x0,
                stroke if h == stroke else snap(y + h) - y0,
            )
        )
    p.end()
    return QIcon(pm)


TIPS = {
    "pin": "Pin",
    "isolate": "Filter",
    "restore": "Restore View",
    "gear": "Options",
    "close": "Close",
    "tag": "Tags",
    "docBold": "Bold",
    "docItalic": "Italic",
    "docUnderline": "Underline",
    "docColor": "Color",
    "docBullets": "Bullets",
    "docNumbers": "Numbering",
    "wrap": "Wrap",
    "link": "Link",
}


def glyph_button(name, owner, checkable=True):
    btn = QToolButton()
    btn.glyph = name
    btn.setToolTip(TIPS[name])
    btn.setCheckable(checkable)
    btn.setAutoRaise(True)
    btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    glyph, row = BAR_SIZES["Medium"][1:]
    btn.setIconSize(QSize(glyph, glyph))
    btn.setFixedHeight(row)
    rdb_theme.follow(owner, lambda _o: draw_glyph(btn))
    return btn


def draw_glyph(btn):
    # the Active group: a button drawn while disabled would keep the grey once enabled
    btn.setIcon(
        glyph_icon(
            btn.glyph,
            btn.palette().color(
                QPalette.ColorGroup.Active, QPalette.ColorRole.ButtonText
            ),
            btn.devicePixelRatioF(),
            btn.iconSize().width(),
        )
    )


def size_glyph(btn, glyph, row):
    btn.setIconSize(QSize(glyph, glyph))
    btn.setFixedHeight(row)
    draw_glyph(btn)


class Divider(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedWidth(1)

    def paintEvent(self, _ev):
        QPainter(self).fillRect(self.rect(), self.palette().mid())


def item_widget(key, owner):
    if key == m.FIND_DIV:
        return Divider()
    if key == "tag":
        return glyph_button("tag", owner, checkable=False)
    return MarkToggle(key, BAR_SIZES["Medium"][0])


def size_item(w, size):
    k, glyph, row = BAR_SIZES[size]
    if isinstance(w, MarkToggle):
        w.set_k(k)
    elif isinstance(w, Divider):
        w.setFixedHeight(row)
    else:
        size_glyph(w, glyph, row)


def fold(text):
    # a character that lowers to several stays as it is: positions in the folded text are positions in the text
    return "".join(c.lower() if len(c.lower()) == 1 else c for c in text)


def terms_of(query):
    return fold(query).split()


def scored(term, low):
    r = term_match(term, low, ())
    return r if r is not None and r[0] >= MIN_PER_CHAR * len(term) else None


def fuzzy_scored(term, low):
    r = fuzzy_match(term, low)
    return r if r is not None and r[0] >= MIN_PER_CHAR * len(term) else None


def fuzzy_hit(term, low):
    r = fuzzy_scored(term, low)
    return r and r[1]


def term_hit(term, low):
    if not m.TAG_RX.fullmatch(term):
        r = scored(term, low)
        return r and r[1]
    # a #tag term stays inside one tag and starts on its first letter:
    # gaps across the line or a mid-word start matched unrelated items
    best = None
    for mt in m.TAG_RX.finditer(low):
        r = fuzzy_scored(term, mt.group()) if mt.group()[1] == term[1] else None
        if r and (best is None or r[0] > best[0]):
            best = r[0], tuple(mt.start() + i for i in r[1])
    return best and best[1]


def is_head_term(term):
    # a lone + is the character
    return len(term) > 1 and term[0] == "+"


def merged_hits(terms, found):
    # the positions of every hit, None unless all words hit and, as with checked tags, any #tag does
    is_tag = [m.TAG_RX.fullmatch(t) is not None for t in terms]
    tags = [f for f, tag in zip(found, is_tag) if tag]
    words = [f for f, tag in zip(found, is_tag) if not tag]
    if None in words or (tags and all(f is None for f in tags)):
        return None
    return tuple(sorted({i for p in found if p for i in p}))


def match_all(terms, low):
    if not terms:
        return None
    return merged_hits(terms, [term_hit(t, low) for t in terms])


def runs(positions):
    out = []
    for i in positions:
        if out and out[-1][0] + out[-1][1] == i:
            out[-1][1] += 1
        else:
            out.append([i, 1])
    return [tuple(r) for r in out]


class _Edit(QLineEdit):
    def __init__(self, bar):
        super().__init__()
        self.bar = bar

    def keyPressEvent(self, e):
        if e.key() in ENTER:
            self.bar.step(-1 if e.modifiers() & SHIFT else 1)
        elif e.key() == Qt.Key.Key_Escape:
            self.bar.escape()
        else:
            super().keyPressEvent(e)


class TagList(QListWidget):
    # a selected row is a checked tag; the gestures are decided here, not by the view
    MAX_ROWS = 14

    def __init__(self, pop):
        super().__init__()
        self.pop, self.anchor, self.paint, self.last = pop, None, None, None
        self.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setStyleSheet(
            "QListWidget::item:selected { background: #000080; color: #ffffff; }"
        )
        self.itemSelectionChanged.connect(pop.synced)

    def fit(self):
        n = min(self.count(), self.MAX_ROWS)
        rows = self.sizeHintForRow(0) * n if self.count() else 0
        self.setFixedHeight(rows + 2 * self.frameWidth())
        bar = self.verticalScrollBar().sizeHint().width() if self.count() > n else 0
        self.setMinimumWidth(
            self.sizeHintForColumn(0) + 2 * self.frameWidth() + bar + 8
        )

    def _put(self, rows, on):
        for r in rows:
            self.item(r).setSelected(on)

    def _span(self, a, b):
        return range(min(a, b), max(a, b) + 1)

    def mousePressEvent(self, e):
        it = self.itemAt(e.position().toPoint())
        if it is None or e.button() != Qt.MouseButton.LeftButton:
            return
        row, mods = self.row(it), e.modifiers()
        shift = bool(mods & SHIFT)
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        self.blockSignals(True)
        if shift:
            if self.anchor is None:
                self.anchor = row
            if not ctrl:
                self._put(range(self.count()), False)
            self._put(self._span(self.anchor, row), True)
            self.paint = None
        else:
            self.paint = not it.isSelected()
            self.anchor = self.last = row
            self._put([row], self.paint)
        self.blockSignals(False)
        self.pop.synced()

    def mouseMoveEvent(self, e):
        if self.paint is None or not e.buttons() & Qt.MouseButton.LeftButton:
            return
        it = self.itemAt(e.position().toPoint())
        if it is None or self.row(it) == self.last:
            return
        row = self.row(it)
        self.blockSignals(True)
        self._put(self._span(self.last, row), self.paint)
        self.blockSignals(False)
        self.last = row
        self.pop.synced()

    def mouseReleaseEvent(self, e):
        self.paint = None

    def mouseDoubleClickEvent(self, e):
        self.mousePressEvent(e)


class TagPop(QFrame):
    # tag key (lower case) -> list row; the pop-out stays open while several are checked
    def __init__(self, bar):
        super().__init__(bar, Qt.WindowType.Popup)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.bar, self.rows, self.checked, self.sets = bar, {}, set(), []
        self.sets_box = QComboBox()
        self.sets_box.setHidden(True)
        self.sets_box.currentIndexChanged.connect(self._pick_set)
        self.all_on, self.all_off = QCheckBox("All On"), QCheckBox("All Off")
        self.listbox = TagList(self)
        self.all_on.clicked.connect(lambda: self.select_all(True))
        self.all_off.clicked.connect(lambda: self.select_all(False))
        self.col = QVBoxLayout(self)
        self.col.setContentsMargins(6, 4, 6, 4)
        self.col.setSpacing(2)
        self.col.addWidget(self.sets_box)
        head = QHBoxLayout()
        head.addWidget(self.all_on)
        head.addWidget(self.all_off)
        head.addStretch(1)
        self.col.addLayout(head)
        self.col.addWidget(self.listbox)
        self.show_all()

    def load(self, tags):
        self.checked &= tags.keys()
        lb = self.listbox
        lb.blockSignals(True)
        for key in [k for k in self.rows if k not in tags]:
            lb.takeItem(lb.row(self.rows.pop(key)))
        for i, key in enumerate(sorted(tags)):
            label = tags[key].removeprefix("#")
            it = self.rows.get(key)
            if it is None:
                it = QListWidgetItem(label)
                it.setData(Qt.ItemDataRole.UserRole, key)
                lb.insertItem(i, it)
                it.setSelected(key in self.checked)
                self.rows[key] = it
            elif it.text() != label:
                it.setText(label)
        lb.blockSignals(False)
        lb.fit()
        self.show_all()
        self.sync_set()
        if self.isVisible():
            self.adjustSize()
        self.bar.show_tag_count()

    def load_sets(self, sets):
        self.sets = sets
        box = self.sets_box
        box.blockSignals(True)
        box.clear()
        box.addItems(["", *(s["name"] for s in sets)])
        box.blockSignals(False)
        box.setHidden(not sets)
        self.sync_set()
        if self.isVisible():
            self.adjustSize()

    def _set_keys(self):
        # a tag missing from this list is not part of the set here
        return [
            {f"#{t.lower()}" for t in s["tags"]} & self.rows.keys() for s in self.sets
        ]

    def sync_set(self):
        keys, now = self._set_keys(), self.sets_box.currentIndex()
        if now > 0 and keys[now - 1] == self.checked:
            return
        at = next((i + 1 for i, k in enumerate(keys) if k and k == self.checked), 0)
        self.sets_box.blockSignals(True)
        self.sets_box.setCurrentIndex(at)
        self.sets_box.blockSignals(False)

    def _pick_set(self, i):
        if i <= 0:
            return
        keys = self._set_keys()[i - 1]
        self.listbox.blockSignals(True)
        for key, it in self.rows.items():
            it.setSelected(key in keys)
        self.listbox.blockSignals(False)
        self.synced()

    def synced(self):
        now = {k for k, it in self.rows.items() if it.isSelected()}
        self.show_all()
        changed = now != self.checked
        self.checked = now
        self.sync_set()
        if changed:
            self.bar.show_tag_count()
            self.bar.refresh()

    def show_all(self):
        n, total = len(self.checked), len(self.rows)
        for box, on in (
            (self.all_on, total > 0 and n == total),
            (self.all_off, n == 0),
        ):
            box.blockSignals(True)
            box.setChecked(on)
            box.blockSignals(False)

    def select(self, key, on):
        self.rows[key].setSelected(on)

    def select_all(self, on):
        self.listbox.blockSignals(True)
        for it in self.rows.values():
            it.setSelected(on)
        self.listbox.blockSignals(False)
        self.synced()

    def reset(self):
        self.checked.clear()
        self.listbox.blockSignals(True)
        self.listbox.clearSelection()
        self.listbox.blockSignals(False)
        self.show_all()
        self.sync_set()
        self.bar.show_tag_count()


class BarOptions(QFrame):
    def __init__(self, bar):
        super().__init__(bar, Qt.WindowType.Popup)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.hide_heads = QCheckBox("Hide Headings")
        col = QVBoxLayout(self)
        col.setContentsMargins(6, 4, 6, 4)
        col.addWidget(self.hide_heads)


class FindBar(QFrame):
    # host: open(rerun) / run(terms, hide[, marks]) -> matches / show(i, move) / clear(); back gets the focus on close
    # a host with marked set also filters by marker
    def __init__(self, host, back, pin_key):
        super().__init__()
        self.host, self.back, self.pin_key = host, back, pin_key
        self.total, self.at = 0, -1
        # a text page: plain text search, none of the item filters
        self.plain = False
        self._s = None
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.edit = _Edit(self)
        self.isolate = glyph_button("isolate", self)
        self.pin_btn = glyph_button("pin", self)
        self.close_btn = glyph_button("close", self, checkable=False)
        for w in (self.isolate, self.pin_btn, self.close_btn):
            w.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        row = QHBoxLayout(self)
        row.setContentsMargins(4, 2, 4, 2)
        row.setSpacing(6)
        row.addWidget(self.edit, 1)
        self.row, self.dividers = row, []
        self.toggles = (
            {k: item_widget(k, self) for k in (*m.PRIOS, *m.STARS)}
            if host.marked
            else {}
        )
        for b in self.toggles.values():
            b.toggled.connect(self.refresh)
        # the optional widgets wait unplaced until configure lays them out from find_items
        self.optional = dict(self.toggles)
        if host.marked:
            self.tag_btn = item_widget("tag", self)
            self.tag_pop = TagPop(self)
            self.tag_sets = list
            self.tag_btn.clicked.connect(self.pop_tags)
            self.optional["tag"] = self.tag_btn
        for w in self.optional.values():
            w.setParent(self)
            w.hide()
        if host.marked:
            self.restore_btn = glyph_button("restore", self, checkable=False)
            self.restore_btn.setEnabled(False)
            self.restore_btn.clicked.connect(self.restore)
            row.addWidget(self.restore_btn)
            host.reset = self.reset_query
        if host.marked and host.heads_option:
            self.gear_btn = glyph_button("gear", self, checkable=False)
            self.options = BarOptions(self)
            self.gear_btn.clicked.connect(self.pop_options)
            self.hide_heads_key = f"{pin_key}_hide_heads"
            host.hide_heads = rdb_paths.settings().value(
                self.hide_heads_key, False, type=bool
            )
            self.options.hide_heads.setChecked(host.hide_heads)
            self.options.hide_heads.toggled.connect(self._set_hide_heads)
        row.addWidget(self.isolate)
        row.addWidget(self.pin_btn)
        if host.marked and host.heads_option:
            row.addWidget(self.gear_btn)
        row.addWidget(self.close_btn)
        self.edit.textChanged.connect(self.refresh)
        self.isolate.toggled.connect(self.refresh)
        self.close_btn.clicked.connect(self.dismiss)
        # a pinned bar is parked hidden until its page is on show
        self.parked = rdb_paths.settings().value(pin_key, False, type=bool)
        self.pin_btn.setChecked(self.parked)
        self.pin_btn.toggled.connect(self._save_pin)
        self.isolate_key = f"{pin_key}_isolate"
        self.isolate.setChecked(
            rdb_paths.settings().value(self.isolate_key, False, type=bool)
        )
        self.isolate.toggled.connect(
            lambda on: rdb_paths.settings().setValue(self.isolate_key, on)
        )
        self.hide()

    def set_plain(self, on):
        self.plain = on
        if self._s is not None:
            self.configure(self._s)

    def configure(self, s):
        self._s = s
        size = s["find_icon"]
        for b in (self.isolate, self.pin_btn, self.close_btn):
            size_item(b, size)
        if self.host.marked:
            size_item(self.restore_btn, size)
            self.restore_btn.setVisible(not self.plain)
        if self.host.marked and self.host.heads_option:
            size_item(self.gear_btn, size)
        for w in (*self.dividers, *self.optional.values()):
            self.row.removeWidget(w)
        for d in self.dividers:
            d.hide()
            d.deleteLater()
        self.dividers = []
        # without markers there is nothing for a divider to separate
        items = s["find_items"] if self.host.marked and not self.plain else []
        for at, key in enumerate(items, 1):
            if key == m.FIND_DIV:
                w = item_widget(key, self)
                self.dividers.append(w)
            else:
                w = self.optional[key]
            self.row.insertWidget(at, w)
            size_item(w, size)
            w.show()
        for key, w in self.optional.items():
            if key not in items:
                w.hide()
                if key in self.toggles:
                    w.setChecked(False)
                elif self.tag_pop.checked:
                    self.tag_pop.reset()
                    self.refresh()

    def _save_pin(self, on):
        rdb_paths.settings().setValue(self.pin_key, on)

    def dismiss(self):
        self.pin_btn.setChecked(False)
        self.close_bar(True)

    def escape(self):
        if self.pin_btn.isChecked():
            self.back.setFocus()
        else:
            self.close_bar(True)

    def suspend(self):
        # the page is going away: a pinned bar waits hidden with its query for the next page
        if self.isHidden():
            return
        if self.pin_btn.isChecked():
            self.host.clear()
            self.sync_restore()
            self.hide()
            self.total, self.at = 0, -1
            self.parked = True
        else:
            self.close_bar()

    def resume(self):
        if not self.parked:
            return
        self.parked = False
        self.host.open(self.rerun)
        self.show()
        self.refresh()

    def show_tag_count(self):
        n = len(self.tag_pop.checked)
        self.tag_btn.setText(str(n) if n else "")
        self.tag_btn.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
            if n
            else Qt.ToolButtonStyle.ToolButtonIconOnly
        )

    def filter_tag(self, tag):
        self.open()
        self.edit.setText("")
        self.tag_pop.load(self.host.tags())
        self.tag_pop.reset()
        self.tag_pop.select(tag.lower(), True)
        self.isolate.setChecked(True)

    def _set_hide_heads(self, on):
        self.host.hide_heads = on
        rdb_paths.settings().setValue(self.hide_heads_key, on)
        self.refresh()

    def pop_options(self):
        self.options.adjustSize()
        self.options.show()
        at = self.gear_btn.mapToGlobal(self.gear_btn.rect().topRight())
        at = QPoint(at.x() - self.options.width(), at.y() - self.options.height())
        self.options.move(rdb_theme.on_screen(self, self.options.frameSize(), at))

    def pop_tags(self):
        self.tag_pop.load_sets(self.tag_sets())
        self.tag_pop.adjustSize()
        self.tag_pop.show()
        top = self.tag_btn.mapToGlobal(self.tag_btn.rect().topLeft())
        at = QPoint(top.x(), top.y() - self.tag_pop.height())
        self.tag_pop.move(rdb_theme.on_screen(self, self.tag_pop.frameSize(), at))

    def _run(self):
        args = terms_of(self.edit.text()), self.isolate.isChecked()
        if not self.toggles:
            return self.host.run(*args)
        marks = frozenset(k for k, b in self.toggles.items() if b.isChecked())
        self.tag_pop.load(self.host.tags())
        n = self.host.run(*args, marks, frozenset(self.tag_pop.checked))
        self.sync_restore()
        return n

    def sync_restore(self):
        if self.host.marked:
            self.restore_btn.setEnabled(self.host.saved is not None)

    def reset_query(self):
        self.edit.blockSignals(True)
        self.edit.setText("")
        self.edit.blockSignals(False)
        for b in self.toggles.values():
            b.blockSignals(True)
            b.setChecked(False)
            b.blockSignals(False)
        self.tag_pop.reset()
        self.refresh()

    def restore(self):
        # the view comes back below, so the query emptying must not end the search itself
        self.host.was_active = False
        self.reset_query()
        self.host.restore()
        self.sync_restore()

    def refresh(self, *_):
        if self.isHidden():
            return
        self.total = self._run()
        self.at = 0 if self.total else -1
        if self.total:
            self.host.show(0)

    def rerun(self):
        # the page changed under the bar: match again, keep the current match and leave the caret
        if self.isHidden():
            return
        self.total = self._run()
        self.at = min(max(self.at, 0), self.total - 1) if self.total else -1
        if self.total:
            self.host.show(self.at, False)

    def open(self):
        if self.isHidden():
            self.host.open(self.rerun)
            self.show()
            self.refresh()
        self.edit.setFocus()
        self.edit.selectAll()

    def close_bar(self, focus=False):
        if self.isHidden():
            return
        self.host.clear()
        self.sync_restore()
        self.hide()
        self.total, self.at = 0, -1
        for b in self.toggles.values():
            b.setChecked(False)
        if self.toggles:
            self.tag_pop.hide()
            self.tag_pop.reset()
        if focus:
            self.back.setFocus()

    def step(self, d):
        if self.total:
            self.at = (self.at + d) % self.total
            self.host.show(self.at)


def _mark(cursor, colour):
    s = QTextEdit.ExtraSelection()
    s.cursor = cursor
    s.format.setBackground(rdb_theme.shade(colour))
    return s


class EditorFinder:
    marked = True
    heads_option = True

    def __init__(self, editor):
        self.ed = editor
        self.hits, self.at, self.folded = [], -1, False
        self.doc = self.rerun = None
        # the lines under the +word headings, None without one; whole: all of them stay shown
        self.scope, self.whole = None, False
        # the view before the search began: (fold set, Completed collapsed, scroll); reset is the bar's
        self.saved, self.reset, self.was_active = None, None, False
        # hid: the blocks the filter hides now; held: those kept hidden after a toggle ended the search
        self.hid, self.held, self.fold_seen = [], [], set()
        self.hide_heads = False

    def open(self, rerun):
        self.doc, self.rerun = self.ed.document(), rerun
        self.doc.contentsChanged.connect(rerun)
        self.ed.fold_toggled.connect(self._toggled)

    def _toggled(self):
        # a user's fold or unfold ends the search; what it hid stays hidden until the done
        # checkbox, except under the headings just toggled
        if not (self.folded and (self.was_active or self.held)):
            return
        now = set(self.doc.property("rdb_folds") or ())
        changed, self.fold_seen = now ^ self.fold_seen, now
        toggled = set()
        for h in m.iter_blocks(self.doc):
            if m.heading_level(h) and (
                m.get_meta(h)["i"] in changed or h == self.ed.revealed
            ):
                tail = m.group_tail(self.doc, h).blockNumber()
                toggled.update(range(h.blockNumber(), tail + 1))
        self.ed.revealed = None
        held = self.hid if self.was_active else self.held
        self.held = [b for b in held if b.blockNumber() not in toggled]
        # the query emptying here keeps the view
        self.was_active = False
        self.reset()

    def restore(self):
        folds, collapsed, scroll = self.saved
        self.saved, self.held, self.folded = None, [], False
        self._partial(frozenset(), frozenset())
        doc, ed = self.doc, self.ed
        doc.setProperty("rdb_folds", folds)
        m.set_collapsed(doc, collapsed)
        m.apply_view(doc, ed.check_mode)
        if owner := m.fold_owner(ed.textCursor().block()):
            ed.setTextCursor(QTextCursor(owner))
        ed._keep_visible()
        ed.viewport().update()
        ed.laid_out.emit()
        ed.verticalScrollBar().setValue(scroll)

    def _unfold(self):
        if self.folded:
            m.apply_view(self.doc, self.ed.check_mode)
            self.folded = False
        self._partial(frozenset(), frozenset())

    def _fold(self):
        # display only: a block's visibility is not part of the document
        keep = set(self.scope) if self.whole else set()
        for n, _ in self.hits:
            b = self.doc.findBlockByNumber(n)
            keep.add((m.owner_todo(b) or b).blockNumber())
        gone, self.hid = set(), []
        used = self._hit_heads(keep) if self.hide_heads else None
        for b in m.iter_blocks(self.doc):
            if not b.isVisible() or m.is_section(b):
                continue
            n = b.blockNumber()
            if (
                m.is_heading(b)
                and (self.scope is None or n in self.scope)
                and (used is None or n in used)
            ):
                continue
            if (m.owner_todo(b) or b).blockNumber() not in keep:
                b.setVisible(False)
                gone.add(b.blockNumber())
                self.hid.append(b)
                self.doc.markContentsDirty(b.position(), b.length())
        self.folded = True
        self.fold_seen = set(self.doc.property("rdb_folds") or ())
        self._partial(*self._holders(gone))
        self.ed._keep_visible()

    def _hit_heads(self, keep):
        # the headings with a kept line in their group, and the headings above those
        out, chain = set(), {}
        for b in m.iter_blocks(self.doc):
            if m.is_section(b):
                chain = {}
                continue
            lvl, n = m.heading_level(b), b.blockNumber()
            if lvl:
                chain = {k: v for k, v in chain.items() if k < lvl}
                chain[lvl] = n
            if n in keep:
                out.update(chain.values())
        return out

    def _hold(self):
        self.held = [b for b in self.held if b.isValid()]
        for b in self.held:
            if b.isVisible():
                b.setVisible(False)
                self.doc.markContentsDirty(b.position(), b.length())
        self.folded = True
        self._partial(*self._holders({b.blockNumber() for b in self.held}))
        self.ed._keep_visible()

    def _holders(self, gone):
        # the open H1 and H2 whose group lost a line to the filter: (some lines still show, none do)
        lost, shows, h1, h2 = set(), set(), None, None
        for b in m.iter_blocks(self.doc):
            if m.is_section(b):
                h1 = h2 = None
                continue
            lvl = m.heading_level(b)
            if lvl == 1:
                h1, h2, above = b, None, ()
            elif lvl == 2:
                h2, above = b, (h1,)
            else:
                above = (h1, h2)
            heads = {h.blockNumber() for h in above if h and not m.is_folded(h)}
            if b.blockNumber() in gone:
                lost |= heads
            elif not lvl and b.isVisible():
                shows |= heads
        return frozenset(lost & shows), frozenset(lost - shows)

    def _partial(self, heads, empty):
        if (heads, empty) != (self.ed.partial_heads, self.ed.empty_heads):
            self.ed.partial_heads, self.ed.empty_heads = heads, empty
            self.ed.viewport().update()

    def tags(self):
        out = {}
        for b in m.iter_blocks(self.doc):
            for a, e in m.tag_runs(b):
                t = m.run_text(b, a, e)
                out.setdefault(t.lower(), t)
        return out

    def _under(self, heads):
        # (the lines of the headings that match, with the headings; [(heading, positions)])
        scope, found, owner = set(), [], 0
        for b in m.iter_blocks(self.doc):
            if m.is_section(b):
                owner = 0
                continue
            level = m.heading_level(b)
            if level and (not owner or level <= owner):
                hit = [term_hit(h, fold(b.text())) for h in heads]
                owner = 0 if all(p is None for p in hit) else level
                if owner:
                    pos = tuple(sorted({i for p in hit if p for i in p}))
                    found.append((b.blockNumber(), pos))
            if owner:
                scope.add(b.blockNumber())
        return scope, found

    def run(self, terms, hide, marks=frozenset(), tags=frozenset()):
        active = bool(terms or marks or tags)
        if active:
            self.held = []
        if active and not self.was_active and self.saved is None:
            self.saved = (
                list(self.doc.property("rdb_folds") or ()),
                m.is_collapsed(self.doc),
                self.ed.verticalScrollBar().value(),
            )
        ended = self.was_active and not active
        self.was_active = active
        self._unfold()
        self.hits, self.at = [], -1
        heads = [t[1:] for t in terms if is_head_term(t)]
        terms = [t for t in terms if not is_head_term(t)]
        self.scope, found = self._under(heads) if heads else (None, [])
        self.whole = bool(heads) and not (terms or marks or tags)
        # a fold hides text a search still finds; showing the hit opens it
        away = m.folded_away(self.doc)
        for b in m.iter_blocks(self.doc):
            if (
                (terms or marks or tags)
                and (self.scope is None or b.blockNumber() in self.scope)
                and (b.isVisible() or b.blockNumber() in away)
                and not m.is_section(b)
                and self._marked(b, terms, marks, tags)
            ):
                # a filter without a query marks no text: the item is the hit
                pos = match_all(terms, fold(b.text())) if terms else ()
                if pos is not None:
                    self.hits.append((b.blockNumber(), pos))
        if self.whole:
            self.hits = found
        if hide and (terms or marks or tags):
            for n, _ in self.hits:
                m.unfold_to(self.doc, self.doc.findBlockByNumber(n))
            m.apply_view(self.doc, self.ed.check_mode)
        if heads or (hide and (terms or marks or tags)):
            self._fold()
        elif self.held:
            self._hold()
        self._marks()
        if ended and hide:
            self.restore()
        elif ended:
            self.saved = None
        return len(self.hits)

    @staticmethod
    def _marked(b, terms, marks, tags):
        if not marks and not tags:
            return True
        owner = m.owner_todo(b)
        # without a query the item's first line stands for the item
        if owner is None or not (terms or owner == b):
            return False
        if marks and m.marker(owner) not in marks:
            return False
        return not tags or any(
            m.run_text(ib, a, e).lower() in tags
            for ib in m.item_blocks(owner)
            for a, e in m.tag_runs(ib)
        )

    def _marks(self):
        sels, found = [], set()
        for i, (n, pos) in enumerate(self.hits):
            block = self.doc.findBlockByNumber(n)
            start = block.position()
            # a matched tag shows as a hover chip, so its text takes no mark; a heading draws no chips
            tagged = [
                r
                for r in (() if m.is_heading(block) else m.tag_runs(block))
                if any(r[0] <= start + p < r[1] for p in pos)
            ]
            found.update(tagged)
            pos = [p for p in pos if not any(a <= start + p < b for a, b in tagged)]
            for at, length in runs(pos):
                c = QTextCursor(self.doc)
                c.setPosition(start + at)
                c.setPosition(start + at + length, QTextCursor.MoveMode.KeepAnchor)
                sels.append(_mark(c, SEL_CUR if i == self.at else SEL_BG))
        self.ed.setExtraSelections(sels)
        self._found(found)

    def _found(self, found):
        if found != self.ed.found_tags:
            self.ed.found_tags = frozenset(found)
            self.ed.viewport().update()

    def show(self, i, move=True):
        self.at = i
        self._marks()
        if move:
            n, pos = self.hits[i]
            c = QTextCursor(self.doc.findBlockByNumber(n))
            if not c.block().isVisible():
                m.unfold_to(self.doc, c.block())
                m.apply_view(self.doc, self.ed.check_mode)
                if self.folded:
                    self._fold()
            at = c.block().position() + (pos[0] if pos else 0)
            if not pos or any(a <= at < b for a, b in self.ed.found_tags):
                # inside or beside any run the caret would reveal the raw #tag
                b = c.block()
                tags = m.tag_runs(b)
                span = range(b.position(), b.position() + len(b.text()) + 1)
                at = next(
                    (p for p in span if not any(x <= p <= y for x, y in tags)),
                    b.position(),
                )
            c.setPosition(at)
            self.ed.setTextCursor(c)
            bar = self.ed.verticalScrollBar()
            mid = self.ed.viewport().height() // 2
            bar.setValue(bar.value() + self.ed.cursorRect().center().y() - mid)

    def clear(self):
        self.doc.contentsChanged.disconnect(self.rerun)
        self.ed.fold_toggled.disconnect(self._toggled)
        self.saved, self.was_active, self.held = None, False, []
        self._unfold()
        self.hits, self.at = [], -1
        self.ed.setExtraSelections([])
        self._found(frozenset())


class MapFinder:
    marked = False

    def __init__(self, view, texts):
        # texts() -> pid -> the texts of that list's items, headings and notes
        self.view, self.texts_of = view, texts
        self.texts, self.found, self.touched, self.hidden = {}, [], [], []

    def open(self, rerun):
        self.texts = self.texts_of()

    def _match(self, item, terms):
        if isinstance(item, CardItem):
            title, fields = item.title, [*item.tags, *self.texts.get(item.pid, ())]
        elif isinstance(item, TextItem):
            title, fields = "", [item.plain()]
        else:
            return None
        low, found = fold(title), []
        for t in terms:
            hit = term_hit(t, low)
            if hit is None and any(term_hit(t, fold(f)) is not None for f in fields):
                hit = ()
            found.append(hit)
        return merged_hits(terms, found)

    def _restore(self):
        for item in self.touched:
            item.find = None
            item.update()
        for item in self.hidden:
            item.setVisible(True)
        self.found, self.touched, self.hidden = [], [], []

    def run(self, terms, hide):
        self._restore()
        if not terms:
            return 0
        boxes = [
            i for i in self.view.scene().items() if isinstance(i, (CardItem, Block))
        ]
        for item in sorted(boxes, key=lambda i: (i.y(), i.x())):
            marks = self._match(item, terms)
            item.find = FindMark(marks is not None, marks or ())
            item.update()
            self.touched.append(item)
            if marks is not None:
                self.found.append(item)
            elif hide:
                item.setVisible(False)
                self.hidden.append(item)
        return len(self.found)

    def show(self, i, move=True):
        if move:
            item = self.found[i]
            self.view.scene().clearSelection()
            item.setSelected(True)
            self.view.centerOn(item)

    def clear(self):
        self._restore()


class KanbanFinder:
    marked = True
    # the board hides headings without items itself
    heads_option = False

    def __init__(self, board):
        self.board = board
        self.hits, self.at = [], -1
        self.shown, self.marks = None, {}
        self.rerun, self.busy = None, False
        # the view is never changed: nothing is saved to restore; the rest is the bar's
        self.saved, self.reset, self.was_active = None, None, False

    def open(self, rerun):
        self.rerun = rerun
        self.board.composed.connect(self._composed)

    def _composed(self):
        # a recompose covers a document edit, statuses, Status Only and the Completed toggle;
        # one that run() itself causes is not a change
        if not self.busy:
            self.rerun()

    def _source(self):
        self.busy = True
        try:
            return self.board.find_source()
        finally:
            self.busy = False

    def restore(self):
        pass

    def tags(self):
        out = {}
        for it in self._source()[0]:
            for mt in m.TAG_RX.finditer(it["text"]):
                out.setdefault(mt.group().lower(), mt.group())
        return out

    @staticmethod
    def _scope(heads, terms):
        # the headings under a matching +word heading, and their sub-headings
        out, owner = set(), 0
        for h in heads:
            if owner and h["level"] <= owner:
                owner = 0
            if not owner:
                low = fold(h["text"])
                if any(term_hit(t, low) is not None for t in terms):
                    owner = h["level"]
            if owner:
                out.add(h["id"])
        return out

    @staticmethod
    def _marked(it, terms, marks, tags):
        if marks and it["mark"] not in marks:
            return False
        return not tags or any(
            mt.group().lower() in tags for mt in m.TAG_RX.finditer(it["text"])
        )

    def run(self, terms, hide, marks=frozenset(), tags=frozenset()):
        active = bool(terms or marks or tags)
        self.was_active = active
        items, heads = self._source()
        heads_t = [t[1:] for t in terms if is_head_term(t)]
        terms = [t for t in terms if not is_head_term(t)]
        scope = self._scope(heads, heads_t) if heads_t else None
        self.hits, self.at = [], -1
        for it in items if active else ():
            if scope is not None and it["group"] not in scope:
                continue
            if not self._marked(it, terms, marks, tags):
                continue
            # a filter without a query marks no text: the item is the hit
            pos = match_all(terms, fold(it["text"])) if terms else ()
            if pos is not None:
                self.hits.append((it["id"], pos))
        self.shown = {i for i, _ in self.hits} if hide and active else None
        self.marks = dict(self.hits)
        self.board.set_find(self.shown, self.marks, None)
        return len(self.hits)

    def show(self, i, move=True):
        self.at = i
        uid = self.hits[i][0]
        self.board.set_find(self.shown, self.marks, uid)
        if move:
            self.board.reveal_item(uid)

    def clear(self):
        self.board.composed.disconnect(self._composed)
        self.hits, self.at, self.was_active = [], -1, False
        self.shown, self.marks = None, {}
        self.board.set_find(None, {}, None)
