import copy

from PySide6.QtCore import (
    QDir,
    QEvent,
    QMimeData,
    QPoint,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QCursor,
    QDrag,
    QFont,
    QIcon,
    QKeySequence,
    QPainter,
    QPen,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QHeaderView,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QComboBox,
    QDialog,
    QApplication,
    QDialogButtonBox,
    QFileDialog,
    QFontComboBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QKeySequenceEdit,
    QLabel,
    QLayout,
    QLineEdit,
    QHBoxLayout,
    QMenu,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

import rdb_assoc
import rdb_color
import rdb_model as m
import rdb_status
import rdb_theme
from rdb_editor import PART_SIZE, paint_part_glyph
from rdb_settings_preview import PANES, Sandbox
from rdb_findbar import (
    BAR_SIZES,
    fold,
    fuzzy_hit,
    fuzzy_scored,
    glyph_button,
    item_widget,
    size_item,
    terms_of,
)


class ColorButton(QPushButton):
    changed = Signal()

    def __init__(self, key=None, at=None, row=None):
        # key: the colour setting this edits, at/row: its place in a palette row
        super().__init__()
        self.key, self.at, self.row = key, at, row
        self.setFixedSize(40, 16)
        self.setAutoDefault(False)
        self.color = ""
        self.clicked.connect(self.pick)
        rdb_theme.follow(self, ColorButton._sheet, now=False)

    def set_color(self, color):
        self.color = color
        self._sheet()
        self.changed.emit()

    def drawn(self):
        if self.key is None:
            return rdb_theme.shade(self.color).name()
        if self.row is not None:
            return m.shown({self.key: self.row.colors()}, self.key)[self.at]
        return m.shown({self.key: self.color}, self.key)

    def _sheet(self):
        if self.color:
            self.setStyleSheet(
                f"background: {self.drawn()}; border: 1px solid {rdb_theme.text('swatch_btn_edge')};"
                + rdb_theme.radius_text()
            )

    def pick(self):
        # parented to the window, not this button: the button's colour style sheet would cascade into it
        c = rdb_color.get_color(self.window(), QColor(self.color))
        if c.isValid():
            self.set_color(c.name())


class ColorRow(QWidget):
    changed = Signal()

    def __init__(self, n, key=None):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self.buttons = [ColorButton(key, i, self) for i in range(n)]
        for b in self.buttons:
            b.setFixedSize(22, 16)
            b.changed.connect(self.changed)
            lay.addWidget(b)
        lay.addStretch(1)

    def set_colors(self, colors):
        # every colour first: a button is drawn from the whole row
        for b, c in zip(self.buttons, colors, strict=True):
            b.color = c
        for b in self.buttons:
            b.set_color(b.color)

    def colors(self):
        return [b.color for b in self.buttons]


class PathEdit(QWidget):
    changed = Signal()

    def __init__(self):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self.edit = QLineEdit()
        self.edit.textChanged.connect(self.changed)
        pick = QPushButton("...")
        pick.setAutoDefault(False)
        pick.setFixedWidth(28)
        pick.clicked.connect(self.pick)
        lay.addWidget(self.edit, 1)
        lay.addWidget(pick)

    def text(self):
        return self.edit.text()

    def setText(self, text):
        self.edit.setText(text)

    def pick(self):
        folder = QFileDialog.getExistingDirectory(
            self.window(), "Folder", self.edit.text()
        )
        if folder:
            self.edit.setText(QDir.toNativeSeparators(folder))


class KeyEdit(QKeySequenceEdit):
    def __init__(self):
        super().__init__()
        self.setMaximumSequenceLength(1)
        self.setClearButtonEnabled(True)
        self.good = ""
        self.keySequenceChanged.connect(self._check)

    def text(self):
        return self.keySequence().toString(QKeySequence.SequenceFormat.PortableText)

    def setText(self, text):
        self.good = text
        self.setKeySequence(
            QKeySequence.fromString(text, QKeySequence.SequenceFormat.PortableText)
        )

    def _check(self):
        if m.is_key(self.text()):
            self.good = self.text()
        else:
            self.setText(self.good)


MIME = "application/x-rdb-finditem"
OWNER = "application/x-rdb-finditem-owner"


class FlowLayout(QLayout):
    def __init__(self, parent):
        super().__init__(parent)
        self.list = []
        self.setSpacing(4)
        self.setContentsMargins(4, 4, 4, 4)

    def addItem(self, item):
        self.list.append(item)

    def count(self):
        return len(self.list)

    def itemAt(self, i):
        return self.list[i] if 0 <= i < len(self.list) else None

    def takeAt(self, i):
        return self.list.pop(i) if 0 <= i < len(self.list) else None

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._flow(QRect(0, 0, width, 0), False)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._flow(rect, True)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self.list:
            size = size.expandedTo(item.minimumSize())
        mg = self.contentsMargins()
        return size + QSize(mg.left() + mg.right(), mg.top() + mg.bottom())

    def _flow(self, rect, apply):
        mg = self.contentsMargins()
        r = rect.adjusted(mg.left(), mg.top(), -mg.right(), -mg.bottom())
        x, y, line = r.x(), r.y(), 0
        for item in self.list:
            size = item.sizeHint()
            if x + size.width() > r.right() + 1 and line:
                x, y, line = r.x(), y + line + self.spacing(), 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), size))
            x += size.width() + self.spacing()
            line = max(line, size.height())
        return y + line - rect.y() + mg.bottom()


def _payload(mime):
    kind, _, rest = bytes(mime.data(MIME)).decode().partition(":")
    return kind, rest


class _Surface(QFrame):
    # entries: (widget, payload) per draggable child; the children let mouse events through to this frame
    def __init__(self, editor):
        super().__init__()
        self.editor, self.press, self.entries = editor, None, []
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setAcceptDrops(True)

    def entry_at(self, pos):
        return next(
            (i for i, (w, _) in enumerate(self.entries) if w.geometry().contains(pos)),
            None,
        )

    def event(self, e):
        # the children pass mouse events through, so their tooltips are shown from here
        if e.type() == QEvent.Type.ToolTip:
            at = self.entry_at(e.pos())
            tip = "" if at is None else self.entries[at][0].toolTip()
            if tip:
                QToolTip.showText(e.globalPos(), tip, self)
            else:
                QToolTip.hideText()
            return True
        return super().event(e)

    def mousePressEvent(self, e):
        at = self.entry_at(e.position().toPoint())
        left = e.button() == Qt.MouseButton.LeftButton
        self.press = (
            (e.globalPosition().toPoint(), at) if left and at is not None else None
        )

    def mouseMoveEvent(self, e):
        if self.press is None or not e.buttons() & Qt.MouseButton.LeftButton:
            return
        far = (e.globalPosition().toPoint() - self.press[0]).manhattanLength()
        if far >= QApplication.startDragDistance():
            at, self.press = self.press[1], None
            self.drag(at)

    def mouseReleaseEvent(self, _e):
        self.press = None

    def drag(self, at):
        w, payload = self.entries[at]
        mime = QMimeData()
        mime.setData(MIME, payload.encode())
        mime.setData(OWNER, str(id(self.editor)).encode())
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(w.grab())
        done = drag.exec(Qt.DropAction.MoveAction)
        # a drop nobody took, away from the preview, takes the item off the bar
        outside = not self.rect().contains(self.mapFromGlobal(QCursor.pos()))
        if (
            done == Qt.DropAction.IgnoreAction
            and payload.startswith("idx:")
            and outside
        ):
            self.editor.remove(int(payload[4:]))

    def dragEnterEvent(self, e):
        # a part stays in the editor it came from
        mime = e.mimeData()
        if mime.hasFormat(MIME) and bytes(mime.data(OWNER)).decode() == str(
            id(self.editor)
        ):
            e.acceptProposedAction()


class _Preview(_Surface):
    def __init__(self, editor, dividers):
        super().__init__(editor)
        self.dividers = dividers
        self.lay = QHBoxLayout(self)
        self.lay.setContentsMargins(4, 2, 4, 2)
        self.lay.setSpacing(6)
        self.mark = QFrame(self)
        self.mark.setStyleSheet("background: palette(highlight)")
        self.mark.hide()

    def slot_at(self, x):
        return sum(1 for w, _ in self.entries if x > w.geometry().center().x())

    def show_mark(self, slot):
        n, gap = len(self.entries), self.lay.spacing() // 2
        if not n:
            x = self.editor.empty_at() + gap
        elif slot < n:
            x = self.entries[slot][0].geometry().left() - gap
        else:
            x = self.entries[-1][0].geometry().right() + gap
        self.mark.setGeometry(x - 1, 2, 2, self.height() - 4)
        self.mark.raise_()
        self.mark.show()

    def dragMoveEvent(self, e):
        self.show_mark(self.slot_at(e.position().toPoint().x()))
        e.acceptProposedAction()

    def dragLeaveEvent(self, _e):
        self.mark.hide()

    def dropEvent(self, e):
        self.mark.hide()
        slot = self.slot_at(e.position().toPoint().x())
        kind, rest = _payload(e.mimeData())
        e.acceptProposedAction()
        if kind == "idx":
            self.editor.move(int(rest), slot)
        else:
            self.editor.add(rest or m.FIND_DIV, slot)

    def contextMenuEvent(self, e):
        at = self.entry_at(e.pos())
        if at is None or not self.dividers:
            return
        menu = QMenu(self)
        menu.addAction("Insert Divider")
        if menu.exec(e.globalPos()) is not None:
            self.editor.insert_divider(at)


class _Tray(_Surface):
    def __init__(self, editor):
        super().__init__(editor)
        self.flow = FlowLayout(self)

    def dropEvent(self, e):
        kind, rest = _payload(e.mimeData())
        e.acceptProposedAction()
        if kind == "idx":
            self.editor.remove(int(rest))


class FindEditor(QWidget):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.size, self._items, self.dividers = "Medium", list(m.FIND_KEYS), []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)
        self.preview, self.tray = _Preview(self, True), _Tray(self)
        self.edit = QLineEdit()
        self.edit.setFixedWidth(60)
        self.fixed = [
            glyph_button(name, self, checkable=False)
            for name in ("isolate", "pin", "close")
        ]
        self.bar_w = {k: item_widget(k, self) for k in m.FIND_KEYS}
        self.tray_w = {k: item_widget(k, self) for k in m.FIND_KEYS}
        self.chip = QLabel("Divider")
        self.chip.setFrameStyle(QFrame.Shape.Panel | QFrame.Shadow.Raised)
        self.chip.setMargin(3)
        on_bar = (self.edit, *self.fixed, *self.bar_w.values())
        for w in (*on_bar, self.chip, *self.tray_w.values()):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            w.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            w.setParent(self.preview if w in on_bar else self.tray)
            w.hide()
        defaults = QPushButton("Defaults")
        defaults.setAutoDefault(False)
        defaults.clicked.connect(self.defaults)
        lay.addWidget(self.preview)
        lay.addWidget(self.tray)
        lay.addWidget(defaults, 0, Qt.AlignmentFlag.AlignLeft)
        self._build()

    def empty_at(self):
        return self.edit.geometry().right()

    def items(self):
        return list(self._items)

    def set_items(self, items):
        m.check_setting("find_items", items)
        self._items = list(items)
        self._build()

    def set_size(self, size):
        self.size = size
        self._build()

    def _build(self):
        lay = self.preview.lay
        while lay.count():
            lay.takeAt(0).widget().hide()
        for d in self.dividers:
            d.hide()
            d.deleteLater()
        self.dividers = []
        placed = []
        for key in self._items:
            if key == m.FIND_DIV:
                w = item_widget(key, self)
                w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                self.dividers.append(w)
            else:
                w = self.bar_w[key]
            placed.append(w)
        self.preview.entries = [(w, f"idx:{i}") for i, w in enumerate(placed)]
        self.edit.setFixedHeight(BAR_SIZES[self.size][2])
        lay.addWidget(self.edit)
        self.edit.show()
        for w in (*placed, *self.fixed):
            lay.addWidget(w)
            size_item(w, self.size)
            w.show()
        flow = self.tray.flow
        while flow.count():
            flow.takeAt(0).widget().hide()
        missing = [k for k in m.FIND_KEYS if k not in self._items]
        self.tray.entries = [(self.tray_w[k], f"key:{k}") for k in missing]
        self.tray.entries.append((self.chip, "div:"))
        for w, _ in self.tray.entries:
            flow.addWidget(w)
            if w is not self.chip:
                size_item(w, self.size)
            w.show()
        # a grid cell does not pass its width on to the flow, so the height is set for the preview's width
        self.tray.setMinimumHeight(
            flow.heightForWidth(self.preview.minimumSizeHint().width())
        )
        self.updateGeometry()

    def _edit(self, items):
        self.set_items(items)
        self.changed.emit()

    def move(self, index, slot):
        new = self.items()
        key = new.pop(index)
        new.insert(slot - (slot > index), key)
        self._edit(new)

    def add(self, key, slot):
        new = self.items()
        new.insert(slot, key)
        self._edit(new)

    def remove(self, index):
        new = self.items()
        del new[index]
        self._edit(new)

    def insert_divider(self, index):
        self.add(m.FIND_DIV, index + 1)

    def defaults(self):
        self._edit(list(m.DEFAULT_SETTINGS["find_items"]))


CHIP_PAD, CHIP_SCALE = 6, 2
CHIP_GLYPH = max(w for w, _ in PART_SIZE.values())
CHIP_HIGH = max(h for _, h in PART_SIZE.values())


PART_NAMES = {
    "number": "Number",
    "check": "Checkbox",
    "bubble": "Bubble",
    "note": "Note",
    "mark": "Marker",
    "status": "Status",
    "fold": "Box",
    "swatch": "Color",
    "count": "Count",
}


class PartChip(QWidget):
    def __init__(self, key, look):
        super().__init__()
        self.key, self.look = key, look
        self.setToolTip(PART_NAMES[key])
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def sizeHint(self):
        w, h = PART_SIZE[self.key]
        return QSize(
            CHIP_PAD * 2 + w * CHIP_SCALE, CHIP_PAD * 2 + CHIP_HIGH * CHIP_SCALE
        )

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.translate(self.width() / 2, self.height() / 2)
        p.scale(CHIP_SCALE, CHIP_SCALE)
        paint_part_glyph(p, self.key, QPoint(0, 0), self.look)


class GutterEditor(QWidget):
    changed = Signal()

    def __init__(self, key, look):
        super().__init__()
        self.key, self.keys = key, m._KINDS[key][1]
        self._items = list(m.DEFAULT_SETTINGS[key])
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)
        self.preview, self.tray = _Preview(self, False), _Tray(self)
        self.preview.setMinimumHeight(30)
        self.preview.lay.setSpacing(4)
        self.bar_w = {k: PartChip(k, look) for k in self.keys}
        self.tray_w = {k: PartChip(k, look) for k in self.keys}
        for w in (*self.bar_w.values(), *self.tray_w.values()):
            w.setParent(self.preview if w in self.bar_w.values() else self.tray)
            w.hide()
        defaults = QPushButton("Defaults")
        defaults.setAutoDefault(False)
        defaults.clicked.connect(self.defaults)
        lay.addWidget(self.preview)
        lay.addWidget(self.tray)
        lay.addWidget(defaults, 0, Qt.AlignmentFlag.AlignLeft)
        self._build()

    def empty_at(self):
        return self.preview.lay.contentsMargins().left()

    def items(self):
        return list(self._items)

    def set_items(self, items):
        m.check_setting(self.key, items)
        self._items = list(items)
        self._build()

    def _build(self):
        lay = self.preview.lay
        while lay.count():
            if (w := lay.takeAt(0).widget()) is not None:
                w.hide()
        placed = [self.bar_w[k] for k in self._items]
        self.preview.entries = [(w, f"idx:{i}") for i, w in enumerate(placed)]
        for w in placed:
            lay.addWidget(w)
            w.show()
        lay.addStretch(1)
        flow = self.tray.flow
        while flow.count():
            flow.takeAt(0).widget().hide()
        self.tray.entries = [
            (self.tray_w[k], f"key:{k}") for k in self.keys if k not in self._items
        ]
        for w, _ in self.tray.entries:
            flow.addWidget(w)
            w.show()
        self.tray.setMinimumHeight(
            max(30, flow.heightForWidth(self.preview.minimumSizeHint().width()))
        )
        self.updateGeometry()

    def _edit(self, items):
        self.set_items(items)
        self.changed.emit()

    def move(self, index, slot):
        new = self.items()
        key = new.pop(index)
        new.insert(slot - (slot > index), key)
        self._edit(new)

    def add(self, key, slot):
        new = self.items()
        new.insert(slot, key)
        self._edit(new)

    def remove(self, index):
        new = self.items()
        del new[index]
        self._edit(new)

    def defaults(self):
        self._edit(list(m.DEFAULT_SETTINGS[self.key]))


class _IconCell(QWidget):
    clicked = Signal()

    def __init__(self, icon, color):
        super().__init__()
        self.icon, self.color, self.current = icon, color, False
        self.setFixedSize(26, 24)

    def paintEvent(self, _e):
        p = QPainter(self)
        if self.current:
            p.setPen(QPen(rdb_theme.color("hot_line"), 1))
            p.setBrush(rdb_theme.color("hot_fill"))
            p.drawRect(self.rect().adjusted(0, 0, -1, -1))
        rdb_status.paint_status(p, QRectF(self.rect()), self.icon, self.color, 1.6)
        p.end()

    def mousePressEvent(self, _e):
        self.clicked.emit()


class IconBrowser(QFrame):
    # every status glyph in the colour of the selected row, its icon marked; a click picks
    picked = Signal(str)
    PER_ROW = 10

    def __init__(self):
        super().__init__()
        self.setFrameShape(QFrame.Shape.StyledPanel)
        grid = QGridLayout(self)
        grid.setContentsMargins(3, 3, 3, 3)
        grid.setSpacing(1)
        self.cells = {}
        for i, icon in enumerate(rdb_status.ICON_KEYS):
            cell = _IconCell(icon, "#808080")
            cell.clicked.connect(lambda icon=icon: self.picked.emit(icon))
            grid.addWidget(cell, i // self.PER_ROW, i % self.PER_ROW)
            self.cells[icon] = cell
        self.setFixedSize(self.sizeHint())

    def show_row(self, st):
        # st None: no row selected, nothing to pick for
        self.setEnabled(st is not None)
        for icon, cell in self.cells.items():
            cell.color = "#808080" if st is None else st["color"]
            cell.current = st is not None and icon == st["icon"]
            cell.update()


class _Centred(QStyledItemDelegate):
    # the icon sits in the middle of its cell; the colour swatch keeps its colour inside the
    # selection
    def paint(self, p, opt, index):
        o = QStyleOptionViewItem(opt)
        self.initStyleOption(o, index)
        style, w = o.widget.style(), o.widget
        # copies: the option's members are reset below
        icon = QIcon(o.icon)
        feat = QStyleOptionViewItem.ViewItemFeature
        swatch = QBrush(o.backgroundBrush)
        o.icon = QIcon()
        o.features = o.features & ~feat.HasDecoration
        o.displayAlignment = Qt.AlignmentFlag.AlignCenter
        o.backgroundBrush = QBrush()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, o, p, w)
        r = opt.rect
        if swatch.style() != Qt.BrushStyle.NoBrush:
            p.fillRect(r.adjusted(2, 2, -2, -2), swatch)
        if not icon.isNull():
            size = o.decorationSize
            at = QRect(QPoint(), size)
            at.moveCenter(r.center())
            icon.paint(p, at)


class StatusEditor(QWidget):
    # the ordered list of statuses: one row each, icon, name and colour; the Done flag is set
    # from the board
    changed = Signal()
    HEADS = ("Icon", "Name", "Color")
    ICON, NAME, COLOR = range(3)

    def __init__(self):
        super().__init__()
        self._items = copy.deepcopy(m.DEFAULT_STATUSES)
        self.table = t = QTableWidget(0, len(self.HEADS))
        t.setHorizontalHeaderLabels(list(self.HEADS))
        rdb_theme.follow(t, rdb_theme.grid_sheet)
        t.setShowGrid(True)
        t.setGridStyle(Qt.PenStyle.SolidLine)
        t.setAlternatingRowColors(False)
        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        t.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        t.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        vh = t.verticalHeader()
        vh.hide()
        vh.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        vh.setMinimumSectionSize(1)
        vh.setDefaultSectionSize(max(t.fontMetrics().height() + 2, 16))
        t.setIconSize(QSize(14, 14))
        hh = t.horizontalHeader()
        hh.setHighlightSections(False)
        hh.setStretchLastSection(False)
        for c in range(len(self.HEADS)):
            hh.setSectionResizeMode(
                c,
                QHeaderView.ResizeMode.Stretch
                if c == self.NAME
                else QHeaderView.ResizeMode.ResizeToContents,
            )
        buttons = QHBoxLayout()
        for attr, text, run in (
            ("btn_add", "Add", self.add),
            ("btn_remove", "Remove", self.remove),
            ("btn_up", "Up", lambda: self.move(-1)),
            ("btn_down", "Down", lambda: self.move(1)),
            ("btn_restore", "Restore Default", self.defaults),
        ):
            b = QPushButton(text)
            b.setAutoDefault(False)
            b.clicked.connect(lambda _=False, run=run: run())
            setattr(self, attr, b)
            buttons.addWidget(b)
        buttons.addStretch(1)
        self.centred = _Centred(t)
        for c in (self.ICON, self.COLOR):
            t.setItemDelegateForColumn(c, self.centred)
        self.browser = IconBrowser()
        self.browser.picked.connect(
            lambda icon: self._row() >= 0 and self.set_icon(self._row(), icon)
        )
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.addWidget(t, 1)
        col.addLayout(buttons)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addLayout(col, 1)
        row.addWidget(self.browser, 0, Qt.AlignmentFlag.AlignTop)
        t.itemSelectionChanged.connect(self._show_row)
        t.currentCellChanged.connect(self._show_row)
        self._build()
        t.itemChanged.connect(self._edited)
        t.cellClicked.connect(self._clicked)
        rdb_theme.follow(self, StatusEditor._restyle, now=False)

    def items(self):
        return copy.deepcopy(self._items)

    def set_items(self, items):
        m.check_setting("statuses", items)
        self._items = copy.deepcopy(items)
        self._build()

    def _restyle(self):
        self._build(self._row())

    def _row(self):
        t = self.table
        rows = {i.row() for i in t.selectedIndexes()}
        return min(rows) if rows else t.currentRow()

    def _select(self, row):
        self.table.setCurrentCell(row, self.NAME)
        self.table.selectRow(row)

    def _show_row(self):
        row = self._row()
        self.browser.show_row(self._items[row] if 0 <= row < len(self._items) else None)

    def _build(self, select=-1):
        t = self.table
        t.blockSignals(True)
        t.setRowCount(len(self._items))
        for r, st in enumerate(self._items):
            icon = QTableWidgetItem()
            icon.setIcon(rdb_status.status_icon(st["icon"], st["color"], 14))
            name = QTableWidgetItem(st["name"])
            color = QTableWidgetItem()
            color.setBackground(rdb_theme.shade(st["color"]))
            fixed = ~Qt.ItemFlag.ItemIsEditable
            for it in (icon, color):
                it.setFlags(it.flags() & fixed)
            for c, it in enumerate((icon, name, color)):
                t.setItem(r, c, it)
        t.blockSignals(False)
        if 0 <= select < len(self._items):
            self._select(select)
        self._show_row()

    def _edit(self, items, select=-1):
        m.check_setting("statuses", items)
        self._items = items
        self._build(select)
        self.changed.emit()

    def _set(self, row, **fields):
        new = self.items()
        new[row].update(fields)
        self._edit(new, row)

    def set_icon(self, row, icon):
        self._set(row, icon=icon)

    def set_color(self, row, color):
        self._set(row, color=color)

    def _edited(self, item):
        st = self._items[item.row()]
        if item.column() == self.NAME:
            text = item.text().strip()
            self.table.blockSignals(True)
            item.setText(text or st["name"])
            self.table.blockSignals(False)
            if text and text != st["name"]:
                st["name"] = text
                self.changed.emit()

    def _clicked(self, row, col):
        st = self._items[row]
        if col == self.COLOR:
            c = rdb_color.get_color(self.window(), QColor(st["color"]))
            if c.isValid():
                self.set_color(row, c.name())

    def add(self):
        new = self.items()
        new.append(rdb_status.new_status({r["key"] for r in new}))
        self._edit(new, len(new) - 1)
        self.table.editItem(self.table.item(len(new) - 1, self.NAME))

    def remove(self):
        row = self._row()
        if row < 0:
            return
        new = self.items()
        del new[row]
        self._edit(new, min(row, len(new) - 1))

    def move(self, step):
        row = self._row()
        to = row + step
        if row < 0 or not 0 <= to < len(self._items):
            return
        new = self.items()
        new[row], new[to] = new[to], new[row]
        self._edit(new, to)

    def defaults(self):
        self._edit(copy.deepcopy(m.DEFAULT_STATUSES))


class TagManager(QWidget):
    # counts() -> [(name, uses)], rename(old, new) and delete(names) act on the database at once;
    # sets is (get, put) of the saved tag selections: [{"name", "tags"}]
    def __init__(self, counts, rename, delete, sets):
        super().__init__()
        self._counts, self._rename, self._delete = counts, rename, delete
        self._get_sets, self._put_sets = sets
        self.table = t = QTableWidget(0, 2)
        t.setHorizontalHeaderLabels(["Tag", "Uses"])
        rdb_theme.follow(t, rdb_theme.grid_sheet)
        t.setShowGrid(True)
        t.setGridStyle(Qt.PenStyle.SolidLine)
        t.setAlternatingRowColors(False)
        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        t.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        t.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        vh = t.verticalHeader()
        vh.hide()
        vh.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        vh.setMinimumSectionSize(1)
        vh.setDefaultSectionSize(t.fontMetrics().height() + 2)
        t.horizontalHeader().setHighlightSections(False)
        t.horizontalHeader().setStretchLastSection(True)
        self.delete_btn = QPushButton("Delete")
        self.delete_btn.setAutoDefault(False)
        self.delete_btn.clicked.connect(self._delete_selected)
        self.error = QLabel()
        row = QHBoxLayout()
        row.addWidget(self.delete_btn)
        row.addWidget(self.error, 1)
        self.search = QLineEdit()
        self.search.textChanged.connect(self._apply_search)
        self.set_name = QLineEdit()
        self.save_btn = QPushButton("Save")
        self.save_btn.setAutoDefault(False)
        self.save_btn.clicked.connect(self._save_set)
        self.set_list = QListWidget()
        self.set_list.setMaximumHeight(self.set_list.fontMetrics().height() * 5)
        self.set_list.currentRowChanged.connect(self._pick_set)
        self.set_delete_btn = QPushButton("Delete")
        self.set_delete_btn.setAutoDefault(False)
        self.set_delete_btn.clicked.connect(self._delete_set)
        save = QHBoxLayout()
        save.addWidget(self.set_name, 1)
        save.addWidget(self.save_btn)
        sets = QHBoxLayout()
        sets.addWidget(self.set_list, 1)
        sets.addWidget(self.set_delete_btn, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout(self)
        col.setContentsMargins(6, 4, 6, 4)
        col.addWidget(self.search)
        col.addWidget(t, 1)
        col.addLayout(row)
        col.addLayout(save)
        col.addLayout(sets)
        self._load()
        t.itemChanged.connect(self._edited)

    def _apply_search(self):
        terms = [w for w in (t.lstrip("#") for t in terms_of(self.search.text())) if w]
        t = self.table
        for r in range(t.rowCount()):
            name = fold(t.item(r, 0).text())
            hit = all(fuzzy_hit(w, name) is not None for w in terms)
            t.setRowHidden(r, not hit)

    def _load(self):
        t = self.table
        t.blockSignals(True)
        rows = self._counts()
        t.setRowCount(len(rows))
        for r, (name, uses) in enumerate(rows):
            tag = QTableWidgetItem(name)
            n = QTableWidgetItem(str(uses))
            n.setFlags(n.flags() & ~Qt.ItemFlag.ItemIsEditable)
            t.setItem(r, 0, tag)
            t.setItem(r, 1, n)
        t.blockSignals(False)
        self._apply_search()
        self._load_sets()

    def _load_sets(self):
        self.set_list.blockSignals(True)
        self.set_list.clear()
        self.set_list.addItems([s["name"] for s in self._get_sets()])
        self.set_list.blockSignals(False)

    def _save_set(self):
        name = self.set_name.text().strip()
        tags = [
            self.table.item(r, 0).text()
            for r in sorted({i.row() for i in self.table.selectedIndexes()})
            if not self.table.isRowHidden(r)
        ]
        if not name:
            self.error.setText("Name required")
            return
        if not tags:
            self.error.setText("No tags selected")
            return
        self.error.clear()
        sets = self._get_sets()
        entry = {"name": name, "tags": tags}
        at = next(
            (i for i, s in enumerate(sets) if s["name"].lower() == name.lower()), None
        )
        if at is None:
            sets.append(entry)
        else:
            sets[at] = entry
        self._put_sets(sets)
        self._load_sets()

    def _delete_set(self):
        r = self.set_list.currentRow()
        if r < 0:
            return
        sets = self._get_sets()
        del sets[r]
        self._put_sets(sets)
        self._load_sets()

    def _pick_set(self, r):
        if r < 0:
            return
        keys = {t.lower() for t in self._get_sets()[r]["tags"]}
        t = self.table
        t.clearSelection()
        for row in range(t.rowCount()):
            if t.item(row, 0).text().lower() in keys:
                t.item(row, 0).setSelected(True)
                t.item(row, 1).setSelected(True)

    def _edited(self, item):
        names = [n for n, _ in self._counts()]
        old, new = names[item.row()], item.text().strip().removeprefix("#")
        self.error.clear()
        if new == old:
            self._load()
            return
        if not m.tag_name_ok(new):
            self.error.setText(f"#{new}: not a valid tag")
            self._load()
            return
        self._rename(old, new)
        self._load()

    def _delete_selected(self):
        rows = sorted(
            {
                i.row()
                for i in self.table.selectedIndexes()
                if not self.table.isRowHidden(i.row())
            }
        )
        if not rows:
            return
        n = len(rows)
        yes, no = QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No
        ask = QMessageBox.warning(
            self, "Delete", f"Delete {n} tag{'s' * (n != 1)}?", yes | no, no
        )
        if ask != yes:
            return
        names = [n for n, _ in self._counts()]
        self._delete([names[r] for r in rows])
        self.error.clear()
        self._load()


# a setting that applies only while a checkbox has this state: key -> (checkbox key, state)
GATES = {"vim_start": ("vim", True), "urgent_eod": ("urgent_at_once", False)}
STYLE_OF = {f"{lvl}_font": lvl for lvl, _s, _z in m.LIST_LEVELS}
# page -> group -> keys, in the order shown; every schema key is on exactly one page
PAGES = {
    "General": {
        "Behaviour": (
            "on_check",
            "autosave_ms",
            "scrollbars",
            "esc_leaves",
            "ctrl_arrows",
            "tooltips",
            "confirm_delete",
        ),
        "Snips": ("snip_folder", "snip_max", "snip_format", "snip_quality"),
        "Reminders": (
            "urgent_at_once",
            "urgent_eod",
            "urgent_count",
            "notepad_hide_urgent",
        ),
        "Dump": ("dump_key",),
        "rdoc": ("rdoc_path",),
    },
    "Items": {
        "Rows": (
            "line_height",
            "keep_in_heading",
            "line_hl",
            "line_hl_color",
            "blank_todo",
        ),
        "Body": ("body_font", "body_style", "body_size", "body_fg"),
        "Note": ("notepad_wrap",),
        "Zoom": ("card_zoom",),
    },
    "Gutter": {
        "Item rows": ("gutter_items",),
        "Heading rows": ("gutter_heads",),
        "Pad": ("gutter_lpad", "gutter_rpad"),
        "Separator": ("gutter_sep", "gutter_sep_color"),
        "Tags": ("tag_gutter", "tag_gutter_w"),
    },
    "Kanban": {
        "Statuses": ("statuses",),
        "Title": (
            "kanban_title_fill",
            "kanban_title_ink",
            "kanban_title_fg",
            "kanban_title_font",
            "kanban_title_size",
            "kanban_counts",
        ),
        "Card": (
            "kanban_card_bg",
            "kanban_card_border",
            "kanban_radius",
            "kanban_shadow",
            "kanban_pad",
        ),
        "Layout": ("kanban_card_w", "kanban_gap"),
    },
    "Headings": {
        "H1": (
            "h1_font",
            "h1_style",
            "h1_size",
            "h1_fg",
            "h1_bg",
            "h1_border",
            "h1_sel",
            "h1_cursor",
        ),
        "H2": (
            "h2_font",
            "h2_style",
            "h2_size",
            "h2_fg",
            "h2_rule",
            "h2_rule_px",
        ),
        "H3": ("h3_font", "h3_style", "h3_size", "h3_fg"),
    },
    "Tags": {
        "Tag": (
            "tag_font",
            "tag_weight",
            "tag_size",
            "tag_pad",
            "tag_bold",
            "tag_fg",
            "tag_fill",
            "tag_border",
        )
    },
    "Links": {
        "Chip": (
            "chip_font",
            "chip_style",
            "chip_weight",
            "chip_size",
            "chip_icon",
            "chip_pad",
            "chip_vpad",
            "chip_bold",
            "chip_fg",
            "chip_fill",
            "chip_border",
        ),
        "Hyperlink": ("link_color", "link_visited", "link_cursor"),
    },
    "Completed": {
        "Checked": ("strike", "grey", "italic"),
        "In Completed": (
            "sec_strike",
            "sec_grey",
            "sec_italic",
            "sec_size",
            "sec_rule_px",
        ),
    },
    "Divider": {"Divider": ("divider_color", "divider_px")},
    "Note": {"Note": ("note_bg", "note_border")},
    "Vim": {"Vim": ("vim", "vim_start")},
    "Hover": {"Hover": ("hover_accent", "hover_glow", "chip_hover_ms", "preview_px")},
    "Find bar": {"Find bar": ("find_icon", "find_items")},
    "Map": {
        "Grid": ("grid_show", "grid_style", "snap"),
        "New card": ("card_w", "card_h"),
        "Card": ("card_title_rule", "card_footer_rule", "card_color_scope"),
        "Preview": (
            "preview_count",
            "preview_fill",
            "card_numbers",
            "card_checkboxes",
            "card_headings",
        ),
        "Text": ("text_size",),
    },
    "Timeline": {
        "Chart": (
            "tl_fields",
            "tl_weekdays",
            "tl_today_fill",
            "tl_today_line",
            "tl_grid",
            "tl_day_bg",
            "tl_weekend_bg",
            "tl_today_bg",
            "tl_grid_color",
        ),
        "Dates": ("tl_date_fmt",),
    },
    "Fonts": {
        "UI": ("ui_font", "ui_weight", "ui_size"),
        "Map": ("map_font", "map_weight", "map_size"),
        "Claude": ("claude_font", "claude_weight", "claude_size"),
    },
    "Colors": {
        "Editor": (
            "editor_bg",
            "check_color",
            "number_color",
            "checked_color",
            "sec_color",
            "sec_rule_color",
            "urgent_color",
        ),
        "Map": (
            "map_bg",
            "grid_minor",
            "card_bg",
            "card_border",
            "card_select_bg",
            "card_select_border",
            "frame_color",
            "card_title_rule_color",
            "card_footer_rule_color",
        ),
        "Claude": ("claude_fg", "claude_bg"),
        "Bubbles": ("bubble_bg", "bubble_bg_vivid", "bubble_fg", "bubble_fg_vivid"),
    },
    "Text Copy": {
        "File": ("text_copy", "text_format"),
        "Include": (
            "text_checked",
            "text_subs",
            "text_notes",
            "text_notepads",
            "text_headings",
            "text_contacts",
            "text_blocks",
        ),
    },
    "Title Bar": {"Title Bar": ("theme", "mode", "tiles_side")},
}
_listed = [k for groups in PAGES.values() for keys in groups.values() for k in keys]
if sorted(_listed) != sorted(m.DEFAULT_SETTINGS):
    raise ValueError(
        f"settings pages: missing {set(m.DEFAULT_SETTINGS) - set(_listed)}, "
        f"doubled {sorted(k for k in set(_listed) if _listed.count(k) > 1)}"
    )
ROWS = {row[2]: row for row in m.SCHEMA}
# label/control pairs a kind takes in a row of a group
SLOTS = {m.FONT: 2, m.PATH: 3, m.COLORS: 3, m.FINDITEMS: 3, m.GUTTERPARTS: 3}


class SettingsDialog(QDialog):
    # any control changed: the window applies values() at once
    changed = Signal()

    def __init__(self, parent, settings, prefs=None, extra=()):
        # prefs: the app's QSettings, where the page last shown is kept; extra: (name, page)
        # widgets the caller built, shown under the controls of the page of that name
        super().__init__(parent)
        self._loading = False
        self._prefs = prefs
        self.setWindowTitle("Settings")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)
        self.finder = QLineEdit()
        self.finder.setObjectName("finder")
        self.finder.installEventFilter(self)
        self.finder.textChanged.connect(self._find)
        outer.addWidget(self.finder)
        self.found = QListWidget(self)
        self.found.setObjectName("found")
        self.found.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.found.hide()
        self.found.itemClicked.connect(lambda _item: self._jump())
        self.flash = None
        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.timeout.connect(self._unflash)
        QShortcut(QKeySequence("Ctrl+F"), self, self._focus_finder)
        self.sandbox = Sandbox(settings)
        self.widgets = {}
        self.labels = {}
        self.slots = {}
        self.panes = {}
        self.sides = []
        self.list = QListWidget()
        # the stack keeps one size for every page; its tab bar is the list beside it
        self.tabs = QTabWidget(self)
        self.tabs.tabBar().hide()
        self.tabs.setDocumentMode(True)
        extras = dict(extra)
        extras_all = list(extras)
        for name, groups in PAGES.items():
            self.tabs.addTab(self._page(name, groups, extras.pop(name, None)), name)
        for name, page in extras.items():
            self.tabs.addTab(page, name)
        for i in range(self.tabs.count()):
            self.list.addItem(self.tabs.tabText(i))
        self._index = self._build_index(extras_all)
        # every pane starts at the same place: the controls beside it share one width
        wide = max(side.sizeHint().width() for side in self.sides)
        for side in self.sides:
            side.setFixedWidth(wide)
        self.list.setFixedWidth(self.list.sizeHintForColumn(0) + 24)
        row = QHBoxLayout()
        row.addWidget(self.list)
        row.addWidget(self.tabs, 1)
        outer.addLayout(row, 1)
        for src, _on in GATES.values():
            self.widgets[src][1].toggled.connect(self._gate)
        self.widgets["find_icon"][1].currentTextChanged.connect(
            self.widgets["find_items"][1].set_size
        )
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.RestoreDefaults
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults).clicked.connect(
            lambda: self.set_values(m.DEFAULT_SETTINGS)
        )
        outer.addWidget(buttons)
        self.set_values(settings)
        self.sandbox.settings.update(self.values())
        for name, slot in self.slots.items():
            self.panes[name] = pane = PANES[name](self.sandbox)
            slot.addWidget(pane, 0, Qt.AlignmentFlag.AlignTop)
            pane.refresh()
        if prefs is not None:
            names = [self.tabs.tabText(i) for i in range(self.tabs.count())]
            last = prefs.value("settings/tab")
            # a page renamed or dropped since opens the first one
            if last in names:
                self.tabs.setCurrentIndex(names.index(last))
        self.list.setCurrentRow(self.tabs.currentIndex())
        self.list.currentRowChanged.connect(self.tabs.setCurrentIndex)
        self.tabs.currentChanged.connect(self._shown)
        self.changed.connect(self._repaint_pane)

    def _build_index(self, extra_names):
        # (page, group, label, key); an extra page has the name alone
        index = []
        for page, groups in PAGES.items():
            for group, keys in groups.items():
                index += [(page, group, ROWS[k][4], k) for k in keys]
        return index + [(name, "", "", None) for name in extra_names]

    def _find(self):
        terms = terms_of(self.finder.text())
        self.found.clear()
        if terms:
            hits = []
            for order, (page, group, label, key) in enumerate(self._index):
                text = fold(" ".join((page, group, label)))
                scores = [fuzzy_scored(t, text) for t in terms]
                if any(s is None for s in scores):
                    continue
                low = fold(label)
                in_label = bool(label) and all(
                    fuzzy_hit(t, low) is not None for t in terms
                )
                hits.append((not in_label, -sum(s[0] for s in scores), order))
            for *_rank, order in sorted(hits):
                page, group, label, key = self._index[order]
                row = " / ".join(p for p in (page, group, label) if p)
                item = QListWidgetItem(row)
                item.setData(Qt.ItemDataRole.UserRole, order)
                self.found.addItem(item)
        if self.found.count():
            self.found.setCurrentRow(0)
            self._place_found()
            self.found.show()
            self.found.raise_()
        else:
            self.found.hide()

    def _place_found(self):
        rows = min(self.found.count(), 12)
        box = self.finder.geometry()
        h = rows * self.found.sizeHintForRow(0) + 2 * self.found.frameWidth()
        self.found.setGeometry(box.left(), box.bottom() + 1, box.width(), h)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.found.count():
            self._place_found()

    def _focus_finder(self):
        self.finder.setFocus()
        self.finder.selectAll()

    def eventFilter(self, obj, event):
        if obj is self.finder and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                n = self.found.count()
                if n:
                    step = -1 if key == Qt.Key.Key_Up else 1
                    self.found.setCurrentRow(
                        max(0, min(n - 1, self.found.currentRow() + step))
                    )
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._jump()
                return True
            if key == Qt.Key.Key_Escape and self.finder.text():
                self.finder.clear()
                return True
        return super().eventFilter(obj, event)

    def _jump(self):
        item = self.found.currentItem()
        if item is None:
            return
        page, _group, _label, key = self._index[item.data(Qt.ItemDataRole.UserRole)]
        names = [self.tabs.tabText(i) for i in range(self.tabs.count())]
        self.tabs.setCurrentIndex(names.index(page))
        self.finder.clear()
        if key is None:
            self.tabs.currentWidget().setFocus()
            return
        w = self.widgets[key][1]
        w.setFocus()
        self._frame(self.labels.get(key), w)

    def _frame(self, label, w):
        self._unflash()
        box = w.parentWidget()
        box.layout().activate()
        rect = w.geometry()
        if label is not None and label is not w:
            rect = rect.united(label.geometry())
        frame = QFrame(box)
        frame.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        color = self.palette().highlight().color().name()
        frame.setStyleSheet(f"QFrame {{ border: 2px solid {color}; }}")
        frame.setGeometry(rect.adjusted(-2, -2, 2, 2))
        frame.show()
        frame.raise_()
        self.flash = frame
        self._flash_timer.start(1200)

    def _unflash(self):
        if self.flash is not None:
            self.flash.deleteLater()
            self.flash = None

    def done(self, result):
        if self._prefs is not None:
            self._prefs.setValue(
                "settings/tab", self.tabs.tabText(self.tabs.currentIndex())
            )
        self.sandbox.close()
        super().done(result)

    def _shown(self, i):
        self.list.setCurrentRow(i)
        self._repaint_pane()

    def _repaint_pane(self):
        # only the page in view is drawn again; each page is drawn again when it comes into view
        self.sandbox.settings.update(self.values())
        name = self.tabs.tabText(self.tabs.currentIndex())
        if name in self.panes:
            self.panes[name].refresh()

    def _page(self, name, groups, extra):
        page = QWidget()
        col = QVBoxLayout(page)
        col.setContentsMargins(6, 4, 6, 4)
        col.setSpacing(6)
        top = QHBoxLayout()
        top.setSpacing(10)
        side = QWidget()
        left = QVBoxLayout(side)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(4)
        # beside a preview the controls take two pairs a row, so the pane keeps its place
        per_row = 2 if name in PANES else 3
        if name == "Kanban":
            # the status table and its icon browser take the page's spare height; the groups and
            # the preview sit below it
            groups = dict(groups)
            col.addWidget(self._group("Statuses", groups.pop("Statuses"), per_row), 1)
        for group, keys in groups.items():
            left.addWidget(self._group(group, keys, per_row), 0)
        if name == "General":
            left.addWidget(self._files_box())
        left.addStretch(1)
        top.addWidget(side)
        top.addStretch(1)
        if name in PANES:
            self.sides.append(side)
            holder = QVBoxLayout()
            holder.setContentsMargins(0, 0, 0, 0)
            self.slots[name] = holder
            top.addLayout(holder)
        col.addLayout(top)
        if extra is None:
            if name != "Kanban":
                col.addStretch(1)
        else:
            col.addWidget(extra, 1)
        return page

    def _group(self, title, keys, per_row):
        box = QGroupBox(title)
        inner = QGridLayout(box)
        inner.setContentsMargins(6, 4, 6, 4)
        inner.setHorizontalSpacing(6)
        inner.setVerticalSpacing(3)
        row = used = 0
        spans = False
        if all(ROWS[k][5] == m.BOOL for k in keys):
            per_row = 3
        for key in keys:
            _tab, _group, _key, _default, label, kind, *args = ROWS[key]
            w = self.make_widget(kind, args, key)
            self.widgets[key] = (kind, w)
            if key in STYLE_OF:
                # a family lists its own styles
                w.currentFontChanged.connect(
                    lambda *_, lvl=STYLE_OF[key]: self._restyle(lvl)
                )
            else:
                self._watch(kind, w)
            need = min(SLOTS.get(kind, 1), per_row)
            if used + need > per_row:
                row, used = row + 1, 0
            if kind == m.STATUSES:
                inner.addWidget(w, row, 0, 1, per_row * 2)
                inner.setRowStretch(row, 1)
            elif kind in (m.FINDITEMS, m.GUTTERPARTS):
                inner.addWidget(w, row, 0, 1, per_row * 2, Qt.AlignmentFlag.AlignLeft)
            elif kind == m.BOOL:
                # the box first, its label beside it: the checkbox is its own label
                w.setText(label)
                self.labels[key] = w
                inner.addWidget(w, row, used * 2, 1, 2, Qt.AlignmentFlag.AlignLeft)
            else:
                self.labels[key] = QLabel(label)
                inner.addWidget(self.labels[key], row, used * 2)
                # a control keeps its natural width; a path or palette fills its row
                fill = kind in (m.PATH, m.COLORS)
                align = Qt.AlignmentFlag(0) if fill else Qt.AlignmentFlag.AlignLeft
                inner.addWidget(w, row, used * 2 + 1, 1, need * 2 - 1, align)
                if fill:
                    inner.setColumnStretch(used * 2 + need * 2 - 1, 1)
                    spans = True
            used += need
        # the spare width goes after the last column, not between the controls
        if not spans:
            inner.setColumnStretch(inner.columnCount(), 1)
        return box

    def _restyle(self, lvl, stored=None):
        # a family lists its own styles; the chosen one stays if the new family has it
        combo = self.widgets[f"{lvl}_style"][1]
        family = self.widgets[f"{lvl}_font"][1].currentFont().family()
        styles = m.family_styles(family)
        pick = m.pick_style(styles, combo.currentText() if stored is None else stored)
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(styles or [pick])
        combo.setCurrentText(pick)
        combo.blockSignals(False)
        self._changed()

    def _files_box(self):
        # acts on the system at once; OK and Cancel only concern the settings
        box = QGroupBox("Files")
        row = QHBoxLayout(box)
        row.setContentsMargins(6, 4, 6, 4)
        self.assoc = QPushButton()
        self.assoc.setObjectName("assoc")
        self.assoc.setAutoDefault(False)
        self.assoc.clicked.connect(self._toggle_assoc)
        self.assoc_state = QLabel()
        self.assoc_state.setObjectName("assoc_state")
        row.addWidget(self.assoc)
        row.addWidget(self.assoc_state, 1)
        self._show_assoc()
        return box

    def _show_assoc(self, error=""):
        on = rdb_assoc.status()
        self.assoc.setText("Remove association" if on else "Associate .rdb")
        self.assoc_state.setText(error or (".rdb: rdb.exe" if on else ".rdb: none"))

    def _toggle_assoc(self):
        try:
            if rdb_assoc.status():
                rdb_assoc.remove()
            else:
                rdb_assoc.associate()
        except OSError as err:
            self._show_assoc(str(err))
            return
        self._show_assoc()

    def make_widget(self, kind, args, key):
        if kind == m.BOOL:
            return QCheckBox()
        if kind == m.INT:
            w = QSpinBox()
            w.setRange(args[0], args[1])
            w.setSuffix(args[2])
            return w
        if kind == m.CHOICE:
            w = QComboBox()
            w.addItems(list(args[0]))
            return w
        if kind == m.STYLE:
            return QComboBox()
        if kind == m.COLOR:
            return ColorButton(key)
        if kind == m.FONT:
            return QFontComboBox()
        if kind == m.PATH:
            return PathEdit()
        if kind == m.KEY:
            return KeyEdit()
        if kind == m.FINDITEMS:
            return FindEditor()
        if kind == m.GUTTERPARTS:
            return GutterEditor(key, self.sandbox.settings)
        if kind == m.STATUSES:
            return StatusEditor()
        if kind == m.COLORS:
            return ColorRow(len(m.DEFAULT_SETTINGS[key]), key)
        raise ValueError(f"unknown setting kind {kind!r}")

    def _watch(self, kind, w):
        sig = {
            m.BOOL: "toggled",
            m.INT: "valueChanged",
            m.CHOICE: "currentTextChanged",
            m.STYLE: "currentTextChanged",
            m.FONT: "currentFontChanged",
            m.KEY: "keySequenceChanged",
        }.get(kind, "changed")
        getattr(w, sig).connect(self._changed)

    def _changed(self, *_):
        if not self._loading:
            self.changed.emit()

    def set_values(self, settings):
        # one change for the whole set (opening, Restore Defaults), not one per control
        self._loading = True
        try:
            self._set_values(settings)
        finally:
            self._loading = False
        self._gate()
        self.changed.emit()

    def _gate(self, *_):
        for key, (src, on) in GATES.items():
            enabled = self.widgets[src][1].isChecked() == on
            self.widgets[key][1].setEnabled(enabled)
            self.labels[key].setEnabled(enabled)

    def _set_values(self, settings):
        for key, (kind, w) in self.widgets.items():
            v = settings[key]
            if kind == m.BOOL:
                w.setChecked(v)
            elif kind == m.INT:
                w.setValue(v)
            elif kind == m.CHOICE:
                w.setCurrentText(v)
            elif kind == m.COLOR:
                w.set_color(v)
            elif kind == m.STYLE:
                self._restyle(key[: -len("_style")], v)
            elif kind == m.FONT:
                w.setCurrentFont(QFont(v))
            elif kind in (m.PATH, m.KEY):
                w.setText(v)
            elif kind == m.FINDITEMS:
                # the combo signals no change when it already shows the stored size
                w.set_size(settings["find_icon"])
                w.set_items(v)
            elif kind in (m.GUTTERPARTS, m.STATUSES):
                w.set_items(v)
            else:
                w.set_colors(v)

    def values(self):
        out = {}
        for key, (kind, w) in self.widgets.items():
            if kind == m.BOOL:
                v = w.isChecked()
            elif kind == m.INT:
                v = w.value()
            elif kind in (m.CHOICE, m.STYLE):
                v = w.currentText()
            elif kind == m.COLOR:
                v = w.color
            elif kind == m.FONT:
                v = w.currentFont().family()
            elif kind in (m.PATH, m.KEY):
                v = w.text()
            elif kind in (m.FINDITEMS, m.GUTTERPARTS, m.STATUSES):
                v = w.items()
            else:
                v = w.colors()
            m.check_setting(key, v)
            out[key] = v
        return out
