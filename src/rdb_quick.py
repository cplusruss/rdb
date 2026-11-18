import datetime
from collections import Counter, namedtuple

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QPainter,
    QPen,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QStyle,
    QStyledItemDelegate,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

import rdb_model as m
from rdb_editor import MARK_CELL_K, _MarkCell, paint_marker
from rdb_finder import Ranker
from rdb_insert import GLYPHS
import rdb_map
import rdb_theme
import rdb_timeline as T
from rdb_map import ALIGN_L, _col
from rdb_timeline import (
    ALIGN_R,
    DAYS,
    MAX_DAYS,
    START,
    DatePopup,
    _whole,
    set_field,
    start_text,
)

HEADS = ("h1", "h2", "h3")
ENTER = (Qt.Key.Key_Return, Qt.Key.Key_Enter)
QuickItem = namedtuple("QuickItem", "entry text mark urgent start days")
NAV = (
    Qt.Key.Key_Up,
    Qt.Key.Key_Down,
    Qt.Key.Key_PageUp,
    Qt.Key.Key_PageDown,
    Qt.Key.Key_Home,
    Qt.Key.Key_End,
    Qt.Key.Key_Left,
    Qt.Key.Key_Right,
)
ROW, PAD, INDENT = 20, 6, 16
SHADE = {"h1": 0.15, "h2": 0.3, "h3": 0.45}
DIM = 0.55


def pickable(e):
    if e.kind == "card":
        return e.card["type"] in m.PROJECT_TYPES
    return e.kind in HEADS


def quick_entries(entries):
    # boards ride along as the tree's structure
    return [e for e in entries if e.kind == "board" or pickable(e)]


def quick_counts(entries):
    return Counter(e.pid for e in entries if e.kind == "item")


def quick_label(e):
    if e.kind in ("card", "board"):
        return " > ".join((*e.path, e.text))
    # the path of a heading: boards and card, then the headings above it
    cut = len(e.board_path) + 1
    return " > ".join(e.path[:cut]) + " | " + " > ".join((*e.path[cut:], e.text))


def quick_loc(e):
    return {"pid": e.pid, "head": "" if e.kind == "card" else e.text}


def quick_default(entries, store):
    entries = [e for e in entries if pickable(e)]
    last = store.get_quick_add()
    if last:
        for want in (last, {"pid": last["pid"], "head": ""}):
            hit = next((e for e in entries if quick_loc(e) == want), None)
            if hit is not None:
                return hit
    lists = [e for e in entries if e.kind == "card" and e.card["type"] == "list"]
    inbox = [e for e in lists if e.board_path == ("inbox",)]
    return next(iter(inbox or lists), None)


def add_item(ed, head, text, mark, urgent, start, days):
    # one undo step, through the editor's own Enter path (homes, numbering, task chaining).
    # ed shows the target document; head is its heading's block number, or None for the card
    doc = ed.document()
    hb = None if head is None else doc.findBlockByNumber(head)
    if hb is not None and not m.is_heading(hb):
        raise ValueError(f"block {head} is no heading")
    keep = QTextCursor(ed.textCursor())
    keep.setKeepPositionOnInsert(True)
    edit = QTextCursor(doc)
    edit.beginEditBlock()
    try:
        tail = m.group_tail(doc, hb)
        if not tail.isValid():
            # Completed opens the list: an empty todo above it takes the item
            sec = m.section_block(doc)
            QTextCursor(sec).insertBlock(sec.blockFormat(), QTextCharFormat())
            tail = doc.begin()
            m.set_meta(tail, **m.todo_meta(), **m.NEW_ITEM)
        # a lone empty todo at the end of the open area is the list's blank line, not an item
        blank = m.owner_todo(tail) if hb is None else None
        if blank is not None and (
            len(m.item_blocks(blank)) > 1 or m.item_text(blank) or m.is_checked(blank)
        ):
            blank = None
        ed.setTextCursor(QTextCursor(tail))
        ed.new_todo()
        cur = ed.textCursor()
        block = cur.block()
        cur.insertText(text)
        ed._autolink_range(block.position(), block.position() + block.length())
        # urgent is P1, and a todo holds one marker
        if urgent:
            mark = "p1"
        if mark:
            ed.mark_item(block, mark)
        if ed.timeline:
            if start:
                set_field(doc, block, START, start)
            set_field(doc, block, DAYS, str(days))
        if blank is not None:
            m.remove_item(doc, [blank])
    finally:
        edit.endEditBlock()
    ed.setTextCursor(keep)


class _Cell(_MarkCell):
    on = False

    def paintEvent(self, e):
        p = QPainter(self)
        if self.on or self._hot:
            p.setPen(QPen(rdb_theme.color("on_line" if self.on else "hot_line"), 1))
            p.setBrush(rdb_theme.color("on_fill_quick" if self.on else "hot_fill"))
            p.drawRect(self.rect().adjusted(0, 0, -1, -1))
        paint_marker(p, QRectF(self.rect()), self.mark, MARK_CELL_K, faint=True)
        p.end()


class _Options(QWidget):
    # one Tab stop: 0-4 and the arrows pick the marker, u toggles Urgent
    def __init__(self):
        super().__init__()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.mark = ""
        self.urgent = QCheckBox("Urgent")
        self.urgent.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        row = QHBoxLayout(self)
        row.setContentsMargins(3, 3, 3, 3)
        row.setSpacing(1)
        self.cells = {}
        for mk in m.MARKS:
            cell = self.cells[mk] = _Cell(mk)
            cell.clicked.connect(lambda mk=mk: self.set_mark(mk))
            row.addWidget(cell)
        row.addSpacing(12)
        row.addWidget(self.urgent)
        row.addStretch()
        self.set_mark("")

    def set_mark(self, mark):
        self.mark = mark
        for mk, cell in self.cells.items():
            cell.on = mk == mark
            cell.update()

    def keyPressEvent(self, e):
        k = e.key()
        if e.modifiers():
            super().keyPressEvent(e)
        elif Qt.Key.Key_0 <= k <= Qt.Key.Key_4:
            self.set_mark(("", *m.PRIOS)[k - Qt.Key.Key_0])
        elif k in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            i = m.MARKS.index(self.mark) + (1 if k == Qt.Key.Key_Right else -1)
            self.set_mark(m.MARKS[min(max(i, 0), len(m.MARKS) - 1)])
        elif k == Qt.Key.Key_U:
            self.urgent.toggle()
        else:
            super().keyPressEvent(e)

    def paintEvent(self, e):
        if self.hasFocus():
            p = QPainter(self)
            p.setPen(self.palette().highlight().color())
            p.drawRect(self.rect().adjusted(0, 0, -1, -1))
            p.end()


def guide_x(depth):
    # the centre of a row's chevron, which is also the guide line down its children
    return PAD + INDENT * depth + 6


class Node:
    def __init__(self, entry, depth, parent, count, pick):
        self.entry, self.depth, self.parent, self.count = entry, depth, parent, count
        self.pick = pick
        self.kind, self.text = entry.kind, entry.text
        self.children, self.sibs, self.item = [], None, None
        self.dim, self.marks = False, ()

    @property
    def shown(self):
        return [c for c in self.children if not c.item.isHidden()]

    @property
    def more(self):
        later = self.sibs[self.sibs.index(self) + 1 :]
        return any(not s.item.isHidden() for s in later)


def _up(e):
    # the key of what holds e: a board's id path, a card's pid, a heading's (pid, block)
    if e.kind in ("board", "card"):
        return e.board_path
    last = e.trail[-1]
    return (e.pid, last.block) if last.kind == "block" else e.pid


def _own(e):
    if e.kind == "board":
        return (*e.board_path, e.pid)
    return e.pid if e.kind == "card" else (e.pid, e.block)


def _nodes(kids, key, depth, parent, counts, seen, choose):
    seen.add(key)
    out = []
    # cards come before the boards beside them
    for e in sorted(kids.get(key, ()), key=lambda e: e.kind == "board"):
        n = Node(
            e, depth, parent, counts[e.pid] if e.kind == "card" else None, choose(e)
        )
        n.sibs = out
        n.children = _nodes(kids, _own(e), depth + 1, n, counts, seen, choose)
        # a board with nothing to pick is no place to go
        if e.kind != "board" or n.pick or n.children:
            out.append(n)
    return out


def quick_tree(entries, counts, choose=pickable):
    kids = {}
    for e in entries:
        kids.setdefault(_up(e), []).append(e)
    seen = set()
    roots = _nodes(kids, (), 0, None, counts, seen, choose)
    if lost := set(kids) - seen:
        raise ValueError(f"entries under {sorted(map(str, lost))} have no parent")
    return roots


def _flat(nodes):
    for n in nodes:
        yield n
        yield from _flat(n.children)


def _mix(a, b, t):
    return QColor.fromRgbF(
        *(x * (1 - t) + y * t for x, y in zip(a.getRgbF()[:3], b.getRgbF()[:3]))
    )


def _folder(p, c):
    pts = ((-8, 6), (-8, -5), (-3, -5), (-1, -3), (8, -3), (8, 6))
    p.drawPolygon([QPointF(c.x() + dx, c.y() + dy) for dx, dy in pts])


class _Delegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return QSize(0, ROW)

    def paint(self, p, option, index):
        n = self.parent().nodes[index.data(Qt.ItemDataRole.UserRole)]
        r, pal, font = option.rect, option.palette, option.font
        ink, base = pal.text().color(), pal.base().color()
        if option.state & QStyle.StateFlag.State_Selected:
            p.fillRect(r, _col("card_select_bg"))
            p.fillRect(r.left(), r.top(), 3, r.height(), _col("card_select_border"))
        elif option.state & QStyle.StateFlag.State_MouseOver:
            p.fillRect(r, _mix(base, ink, 0.07))
        cy, x0 = r.center().y(), PAD + INDENT * n.depth
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        self.guides(p, n, r, pal.mid().color())
        fg = _mix(ink, base, max(SHADE.get(n.kind, 0), DIM * n.dim))
        p.setPen(QPen(fg, 1))
        if n.shown:
            cx = guide_x(n.depth)
            open_ = ((-3, -1), (0, 2), (3, -1))
            shut = ((-1, -3), (2, 0), (-1, 3))
            pts = open_ if n.item.isExpanded() else shut
            p.drawPolyline([QPointF(cx + a, cy + b) for a, b in pts])
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QPointF(x0 + 22, cy)
        if n.kind in HEADS:
            badge = QRectF(c.x() - 8, cy - 6, 16, 12)
            p.setBrush(_mix(base, fg, 0.18))
            p.drawRoundedRect(badge, 3, 3)
            small = QFont(font)
            small.setPointSizeF(font.pointSizeF() * 0.75)
            small.setBold(True)
            p.setFont(small)
            p.drawText(badge, Qt.AlignmentFlag.AlignCenter, n.kind.upper())
        else:
            p.setPen(QPen(fg, 1.2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            (_folder if n.kind == "board" else GLYPHS[n.entry.card["type"]])(p, c)
        right = r.right() - PAD
        if n.count is not None:
            p.setFont(font)
            p.setPen(_mix(ink, base, 0.5))
            num = str(n.count)
            box = QRectF(r.left(), r.top(), r.width() - PAD, r.height())
            p.drawText(box, ALIGN_R, num)
            right -= QFontMetrics(font).horizontalAdvance(num) + PAD
        self.label(
            p, n, font, QRectF(x0 + 36, r.top(), right - x0 - 36, r.height()), fg
        )
        p.restore()

    def guides(self, p, n, r, color):
        pen = QPen(color, 1)
        pen.setDashPattern([1, 1])
        p.setPen(pen)
        cy, a = r.center().y(), n
        while a.parent is not None:
            gx = guide_x(a.depth - 1)
            if a is n:
                p.drawLine(gx, r.top(), gx, r.bottom() if n.more else cy)
                p.drawLine(gx, cy, gx + (8 if n.shown else 14), cy)
            elif a.more:
                p.drawLine(gx, r.top(), gx, r.bottom())
            a = a.parent

    def label(self, p, n, font, box, fg):
        bold = QFont(font)
        bold.setBold(True)
        p.setClipRect(box)
        x = box.left()
        # matched letters bold in the select colour
        for i, ch in enumerate(n.text):
            hit = i in n.marks
            f = bold if hit or n.kind == "board" else font
            p.setFont(f)
            p.setPen(_col("card_select_border") if hit else fg)
            w = QFontMetrics(f).horizontalAdvance(ch)
            p.drawText(QRectF(x, box.top(), w + 1, box.height()), ALIGN_L, ch)
            x += w


class _Tree(QTreeWidget):
    def __init__(self):
        super().__init__()
        self.nodes, self.roots = [], []
        self.setHeaderHidden(True)
        self.setIndentation(0)
        self.setRootIsDecorated(False)
        self.setUniformRowHeights(True)
        self.setMouseTracking(True)
        self.setExpandsOnDoubleClick(False)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setItemDelegate(_Delegate(self))

    def load(self, roots):
        self.roots, self.nodes = roots, list(_flat(roots))
        for i, n in enumerate(self.nodes):
            n.item = QTreeWidgetItem([""])
            n.item.setData(0, Qt.ItemDataRole.UserRole, i)
            (n.parent.item.addChild if n.parent else self.addTopLevelItem)(n.item)

    def node(self, item):
        if item is None:
            return None
        return self.nodes[item.data(0, Qt.ItemDataRole.UserRole)]

    def mousePressEvent(self, e):
        pt = e.position().toPoint()
        n = self.node(self.itemAt(pt))
        if n is not None and n.shown and abs(pt.x() - guide_x(n.depth)) <= 6:
            n.item.setExpanded(not n.item.isExpanded())
        else:
            super().mousePressEvent(e)


class _Loc(QLineEdit):
    nav = Signal(int)

    def keyPressEvent(self, e):
        if e.key() in NAV and not e.modifiers():
            self.nav.emit(e.key())
        else:
            super().keyPressEvent(e)


def _dates(text):
    # (start, end or None) of a typed Start, None when it is empty
    text = text.strip()
    if not text:
        return None
    return T.parse_dates(text, T.DATE_FMT, datetime.date.fromisoformat(m.today()))


def _fails(fn, *args):
    try:
        fn(*args)
    except ValueError:
        return True
    return False


class QuickAdd(QDialog):
    # three Tab stops: the location, the item text, the options (and a timeline's Start, Days)
    choose = staticmethod(pickable)

    def __init__(self, parent, entries, default, counts, urgent=False):
        super().__init__(parent)
        self.setWindowTitle("Quick Add")
        self.ranker = Ranker(entries)
        self.item = None
        self.loc, self.text = _Loc(), QLineEdit()
        self.tree = _Tree()
        self.tree.load(quick_tree(entries, counts, self.choose))
        self.nodes = self.tree.nodes
        self.by_entry = {id(n.entry): n for n in self.nodes}
        self.opts = _Options()
        self.urgent = self.opts.urgent
        self.urgent.setChecked(urgent)
        self.start, self.days = QLineEdit(), QLineEdit("1")
        self.dates = QWidget()
        row = QHBoxLayout(self.dates)
        row.setContentsMargins(0, 0, 0, 0)
        for label, field in (("Start", self.start), ("Days", self.days)):
            row.addWidget(QLabel(label))
            row.addWidget(field)
        self.readout = QLabel()
        form = QFormLayout()
        form.addRow("Location", self.loc)
        form.addRow(self.tree)
        form.addRow("Text", self.text)
        form.addRow("Marker", self.opts)
        form.addRow(self.dates)
        col = QVBoxLayout(self)
        col.addLayout(form)
        col.addWidget(self.readout)
        self.resize(560, 520)
        self.fields = (self.loc, self.text, self.start, self.days)
        self.cal = None
        self.start.installEventFilter(self)
        self.start.textEdited.connect(self._range_days)
        for f in self.fields:
            f.textEdited.connect(lambda _, f=f: f.setStyleSheet(""))
        for a, b in zip(
            (self.loc, self.text, self.opts, self.start),
            (self.text, self.opts, self.start, self.days),
        ):
            self.setTabOrder(a, b)
        self.loc.textEdited.connect(self.typed)
        self.loc.nav.connect(self.step)
        self.tree.currentItemChanged.connect(lambda *_: self.chosen())
        self.tree.itemDoubleClicked.connect(self.picked)
        self.fill("", default)
        if default is not None:
            self.loc.setText(quick_label(default))
        self.loc.setFocus()
        self.loc.selectAll()

    @property
    def mark(self):
        return self.opts.mark

    @property
    def node(self):
        return self.tree.node(self.tree.currentItem())

    @property
    def choice(self):
        n = self.node
        return n.entry if n is not None and n.pick else None

    def visible(self):
        def walk(nodes):
            for n in nodes:
                if not n.item.isHidden():
                    yield n
                    if n.item.isExpanded():
                        yield from walk(n.children)

        return list(walk(self.tree.roots))

    def fill(self, query, pick):
        # pick: the entry to select, else the best choosable hit of a typed query
        if query.strip():
            hits = {id(h.entry): h for h in self.ranker.rank(query)[0]}
            at = next(
                (self.by_entry[i] for i, h in hits.items() if self.by_entry[i].pick),
                None,
            )
            for n in self.nodes:
                h = hits.get(id(n.entry))
                n.dim, n.marks = h is None, () if h is None else h.marks
                n.item.setHidden(True)
                n.item.setExpanded(True)
            for n in self.nodes:
                if not n.dim:
                    while n is not None and n.item.isHidden():
                        n.item.setHidden(False)
                        n = n.parent
        else:
            at = None if pick is None else self.by_entry[id(pick)]
            for n in self.nodes:
                n.dim, n.marks = False, ()
                n.item.setHidden(False)
                n.item.setExpanded(n.kind == "board")
            up = None if at is None else at.parent
            while up is not None:
                up.item.setExpanded(True)
                up = up.parent
        self.go(at)
        self.chosen()

    def go(self, node):
        self.tree.clearSelection()
        self.tree.setCurrentItem(None if node is None else node.item)
        if node is not None:
            self.tree.scrollToItem(
                node.item, QAbstractItemView.ScrollHint.PositionAtCenter
            )

    def showEvent(self, e):
        super().showEvent(e)
        if self.node is not None:
            self.tree.scrollToItem(
                self.node.item, QAbstractItemView.ScrollHint.PositionAtCenter
            )

    def typed(self, text):
        self.fill(text, None)

    def step(self, key):
        rows, n = self.visible(), self.node
        if not rows:
            return
        i = rows.index(n) if n in rows else -1
        K = Qt.Key
        page = max(1, self.tree.viewport().height() // ROW - 1)
        if key in (K.Key_Left, K.Key_Right):
            if n is not None:
                self.fold(n, key == K.Key_Right)
            return
        i += {
            K.Key_Up: -1,
            K.Key_Down: 1,
            K.Key_PageUp: -page,
            K.Key_PageDown: page,
        }.get(key, 0)
        i = {K.Key_Home: 0, K.Key_End: len(rows) - 1}.get(key, i)
        self.go(rows[min(max(i, 0), len(rows) - 1)])

    def fold(self, n, right):
        kids, open_ = n.shown, n.item.isExpanded()
        if right and kids:
            if open_:
                self.go(kids[0])
            else:
                n.item.setExpanded(True)
        elif not right:
            if open_ and kids:
                n.item.setExpanded(False)
            elif n.parent is not None:
                self.go(n.parent)

    def picked(self, item):
        n = self.tree.node(item)
        if n.pick:
            self.commit()
        else:
            item.setExpanded(not item.isExpanded())

    def chosen(self):
        e = self.choice
        self.readout.setText("" if e is None else quick_label(e))
        self.dates.setVisible(e is not None and e.card["type"] == "timeline")
        self.loc.setStyleSheet("")

    def keyPressEvent(self, e):
        if e.key() in ENTER:
            n = self.node
            if self.loc.hasFocus() and n is not None and not n.pick:
                n.item.setExpanded(not n.item.isExpanded())
            else:
                self.commit()
        else:
            super().keyPressEvent(e)

    def eventFilter(self, obj, e):
        if obj is self.start and (
            (
                e.type() == QEvent.Type.MouseButtonPress
                and e.button() == Qt.MouseButton.LeftButton
            )
            or (
                e.type() == QEvent.Type.KeyPress
                and (
                    e.key() == Qt.Key.Key_F4
                    or (
                        e.key() == Qt.Key.Key_Down
                        and e.modifiers() & Qt.KeyboardModifier.AltModifier
                    )
                )
            )
        ):
            self.open_calendar()
            return e.type() == QEvent.Type.KeyPress
        return super().eventFilter(obj, e)

    def open_calendar(self):
        if self.cal is None:
            self.cal = DatePopup(self, rdb_map._cfg)
            self.cal.picked.connect(self._picked)
        try:
            got = _dates(self.start.text())
        except ValueError:
            got = None
        at = self.start.mapToGlobal(QPoint(0, self.start.height()))
        self.cal.popup_at(at, None if got is None else got[0])

    def _picked(self, start, end):
        text = start_text(start) + ("" if end is None else "-" + start_text(end))
        self.start.setText(text)
        self.start.setStyleSheet("")
        self._range_days(text)

    def _range_days(self, text):
        # a typed range sets Days as it goes; text still being typed is not an error yet
        try:
            got = _dates(text)
        except ValueError:
            return
        if got is not None and got[1] is not None:
            self.days.setText(str((got[1] - got[0]).days + 1))

    def commit(self):
        e, text = self.choice, self.text.text().strip()
        bad = {self.loc: e is None, self.text: not text}
        if self.dates.isVisibleTo(self):
            bad[self.start] = _fails(_dates, self.start.text())
            bad[self.days] = _fails(_whole, self.days.text(), 0, MAX_DAYS)
        for f, flag in bad.items():
            f.setStyleSheet(rdb_theme.error("red", "QLineEdit") if flag else "")
        first = next((f for f, flag in bad.items() if flag), None)
        if first is not None:
            first.setFocus()
            return
        timeline = self.dates.isVisibleTo(self)
        self.item = QuickItem(
            e,
            text,
            self.mark,
            self.urgent.isChecked(),
            self.start.text().strip() if timeline else "",
            int(self.days.text()) if timeline else 1,
        )
        self.accept()
