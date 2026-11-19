import hashlib

from PySide6.QtCore import (
    QEvent,
    QItemSelectionModel,
    QPointF,
    QRectF,
    QSize,
    Qt,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QImage,
    QPainter,
    QPalette,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QFrame,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QStyle,
    QStyledItemDelegate,
)

import rdb_model as m
import rdb_theme
from rdb_model import item_of, iter_blocks, remove_items

MIN_TILE = 180
GAP = 16
PAD = 4
TITLE_H = 18
# one column is the widest a tile gets (just under 2 * MIN_TILE), so no snapshot needs more
SNAP_MAX = 2 * MIN_TILE
# a link a claude reply adds, and one of those that did not load
MARKS = {"new": QColor(217, 119, 87, 80), "dead": QColor(208, 58, 58, 80)}
URL_ROLE = Qt.ItemDataRole.UserRole
SNAP_ROLE = Qt.ItemDataRole.UserRole + 1
CTRL = Qt.KeyboardModifier.ControlModifier
SHIFT = Qt.KeyboardModifier.ShiftModifier
CHORD = CTRL | Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier
ENTER = (Qt.Key.Key_Return, Qt.Key.Key_Enter)
ALIGN_L = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter


def fit_tiles(width):
    cols = max(1, (width + GAP) // (MIN_TILE + GAP))
    return cols, max(1, (width - (cols + 1) * GAP) // cols)


def snap_rect(w):
    return QRectF(PAD, TITLE_H + 2, w - 2 * PAD, w - 2 * PAD)


def tile_h(w):
    return int(snap_rect(w).bottom()) + PAD


def snap_name(url):
    return f"img/link_{hashlib.sha1(url.encode()).hexdigest()[:16]}.png"


def link_entries(doc):
    # (href, label) per distinct href in document order; the label is where the href first appears
    out = {}
    for b in iter_blocks(doc):
        # a stored link split by formatting joins its runs; a detected URL is its own label
        runs = {}
        for s, e, href in m.link_runs(b):
            runs[href] = runs.get(href, "") + m.run_text(b, s, e)
        for s, e, href in m.detected_links(b):
            runs.setdefault(href, m.run_text(b, s, e))
        for href, text in runs.items():
            out.setdefault(href, " ".join(text.split()) or href)
    return list(out.items())


def link_items(doc, hrefs):
    # the items (block lists, document order) with a link to one of hrefs, wherever in the item it is
    out = {}
    for b in iter_blocks(doc):
        if any(h in hrefs for _, _, h in m.block_links(b)):
            item = item_of(b)
            out.setdefault(item[0].blockNumber(), item)
    return list(out.values())


def _pixmap(url, png):
    if not png:
        return None
    img = QImage.fromData(png)
    if img.isNull():
        raise ValueError(f"snapshot of {url} is not an image")
    if max(img.width(), img.height()) > SNAP_MAX:
        img = img.scaled(
            SNAP_MAX,
            SNAP_MAX,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    return QPixmap.fromImage(img)


class _Card(QStyledItemDelegate):
    def __init__(self, grid):
        super().__init__(grid)
        self.grid = grid

    def sizeHint(self, option, index):
        return QSize(self.grid.tile, tile_h(self.grid.tile))

    def paint(self, p, option, index):
        # rdb_map imports the editor, which imports this module, so CardItem._face is mirrored here
        cfg = self.grid.settings
        sel = bool(option.state & QStyle.StateFlag.State_Selected)
        font = QFont(cfg["map_font"])
        font.setPointSizeF(cfg["map_size"])
        font.setWeight(QFont.Weight.Bold)
        w, h = option.rect.width(), option.rect.height()
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.translate(option.rect.topLeft())
        pen = (
            QPen(QColor(m.shown(cfg, "card_select_border")), 2)
            if sel
            else QPen(QColor(m.shown(cfg, "card_border")), 1)
        )
        i = pen.widthF() / 2
        p.setPen(pen)
        p.setBrush(QColor(m.shown(cfg, "card_select_bg" if sel else "card_bg")))
        p.drawRoundedRect(QRectF(0, 0, w, h).adjusted(i, i, -i, -i), 4, 4)
        if cfg["card_title_rule"]:
            p.setPen(QColor(m.shown(cfg, "card_title_rule_color")))
            p.drawLine(QPointF(1, TITLE_H - 0.5), QPointF(w - 1, TITLE_H - 0.5))
        p.setFont(font)
        p.setPen(rdb_theme.color("card_ink"))
        title = QFontMetrics(font).elidedText(
            index.data(), Qt.TextElideMode.ElideRight, w - 12
        )
        p.drawText(QRectF(6, 0, w - 12, TITLE_H), ALIGN_L, title)
        area = snap_rect(w)
        pm = index.data(SNAP_ROLE)
        if pm is None:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(rdb_theme.color("snap_bg"))
            p.drawRect(area)
            font.setWeight(QFont.Weight.Normal)
            p.setFont(font)
            p.setPen(rdb_theme.color("snap_ink"))
            p.drawText(
                area,
                Qt.AlignmentFlag.AlignCenter,
                QUrl(index.data(URL_ROLE)).host().removeprefix("www."),
            )
        else:
            fitted = pm.size().scaled(
                area.size().toSize(), Qt.AspectRatioMode.KeepAspectRatio
            )
            p.drawPixmap(
                QRectF(
                    area.x() + (area.width() - fitted.width()) / 2,
                    area.y() + (area.height() - fitted.height()) / 2,
                    fitted.width(),
                    fitted.height(),
                ),
                pm,
                QRectF(pm.rect()),
            )
        mark = self.grid.marks.get(index.data(URL_ROLE))
        if mark:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(MARKS[mark])
            p.drawRoundedRect(QRectF(0, 0, w, h), 4, 4)
        p.restore()


class LinksGrid(QListWidget):
    opened = Signal(str, bool)
    leave = Signal()
    edited = Signal(object)

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.marks = {}
        self.filter = QLineEdit(self)
        self.filter.textChanged.connect(self._refilter)
        self.filter.installEventFilter(self)
        self.setViewportMargins(
            GAP // 2, self.filter.sizeHint().height() + 12, GAP // 2, 0
        )
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.setFrameShape(QFrame.Shape.NoFrame)
        # the filter strip and side margins sit outside the viewport
        self.setBackgroundRole(QPalette.ColorRole.Base)
        self.setAutoFillBackground(True)
        self.setItemDelegate(_Card(self))
        self._relayout()

    def _relayout(self):
        extent = QStyle.PixelMetric.PM_ScrollBarExtent
        bars = self.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
        # Qt keeps the bar room out of the row width even while the bar is hidden, and wraps
        # a row that reaches the viewport width, so a layout that fills it exactly loses a column
        room = (
            self.maximumViewportSize().width()
            - (
                self.style().pixelMetric(extent, None, self.verticalScrollBar())
                if bars
                else 0
            )
            - 1
        )
        self.tile = fit_tiles(room + GAP)[1]
        size = QSize(self.tile + GAP, tile_h(self.tile) + GAP)
        if size != self.gridSize():
            self.setGridSize(size)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.filter.setGeometry(
            GAP, 4, self.width() - 2 * GAP, self.filter.sizeHint().height()
        )
        self._relayout()

    def setVerticalScrollBarPolicy(self, policy):
        super().setVerticalScrollBarPolicy(policy)
        self._relayout()

    def focus_filter(self):
        self.filter.setFocus()
        self.filter.selectAll()

    def _refilter(self):
        # rdb_finder imports the editor, which imports this module
        from rdb_finder import match

        terms = self.filter.text().lower().split()
        for row in range(self.count()):
            item = self.item(row)
            hay = (item.text().lower(), item.data(URL_ROLE).split("://", 1)[-1].lower())
            hide = not all(any(match(t, s) for s in hay) for t in terms)
            self.setRowHidden(row, hide)
            if hide:
                item.setSelected(False)
        if self.currentRow() < 0 or self.isRowHidden(self.currentRow()):
            self.setCurrentRow(
                next((r for r in range(self.count()) if not self.isRowHidden(r)), -1)
            )

    def _leave(self):
        if not self.settings["vim"]:
            self.leave.emit()

    def eventFilter(self, obj, e):
        if obj is self.filter and e.type() == QEvent.Type.KeyPress:
            if e.key() == Qt.Key.Key_Down:
                self.setFocus()
                return True
            if e.key() == Qt.Key.Key_Escape:
                if self.filter.text():
                    self.filter.clear()
                else:
                    self._leave()
                return True
        return super().eventFilter(obj, e)

    def fill(self, entries, snaps):
        keep = self.currentItem().data(URL_ROLE) if self.currentItem() else None
        picked = {i.data(URL_ROLE) for i in self.selectedItems()}
        self.clear()
        for url, text in entries:
            item = QListWidgetItem(text)
            item.setData(URL_ROLE, url)
            item.setData(SNAP_ROLE, _pixmap(url, snaps.get(url)))
            self.addItem(item)
            item.setSelected(url in picked)
            if url == keep:
                self.setCurrentItem(item, QItemSelectionModel.SelectionFlag.NoUpdate)
        self._refilter()

    def mouseDoubleClickEvent(self, e):
        super().mouseDoubleClickEvent(e)
        item = self.itemAt(e.position().toPoint())
        if item is not None and e.button() == Qt.MouseButton.LeftButton:
            self.opened.emit(item.data(URL_ROLE), bool(e.modifiers() & SHIFT))

    def _delete(self):
        hrefs = {i.data(URL_ROLE) for i in self.selectedItems()}
        if not hrefs:
            return
        if self.settings["confirm_delete"]:
            yes, no = QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No
            ans = QMessageBox.warning(
                self, "Delete", "Delete selected links?", yes | no, no
            )
            if ans != yes:
                return
        self.edited.emit(lambda doc: remove_items(doc, link_items(doc, hrefs)))

    def keyPressEvent(self, e):
        text = e.text()
        if e.key() == Qt.Key.Key_Escape:
            self._leave()
            e.accept()
        elif e.key() in ENTER and self.currentItem() is not None:
            self.opened.emit(self.currentItem().data(URL_ROLE), False)
        elif e.key() == Qt.Key.Key_Delete:
            self._delete()
        elif e.modifiers() == CTRL and e.key() == Qt.Key.Key_L:
            self.focus_filter()
        elif e.modifiers() == CTRL and e.key() == Qt.Key.Key_Z:
            self.edited.emit(lambda doc: doc.undo())
        elif e.modifiers() == CTRL and e.key() == Qt.Key.Key_Y:
            self.edited.emit(lambda doc: doc.redo())
        elif text and text.isprintable() and not e.modifiers() & CHORD:
            self.filter.setFocus()
            self.filter.end(False)
            self.filter.insert(text)
        else:
            super().keyPressEvent(e)
