from collections import namedtuple

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QFont, QImage, QPixmap
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

import rdb_model as m
import rdb_theme
from rdb_quick import QuickAdd
from rdb_timeline import DatePopup

ItemView = namedtuple("ItemView", "lines images start")
CTRL = Qt.KeyboardModifier.ControlModifier
CTRL_SHIFT = CTRL | Qt.KeyboardModifier.ShiftModifier
PIC_H = 360
MARGIN = 32


def open_items(doc):
    # the open items of the Dump card; the list's lone blank line is no item
    return [
        b
        for b in m.iter_blocks(doc)
        if m.is_todo(b)
        and not m.is_checked(b)
        and any(x.text().strip() for x in m.item_blocks(b))
    ]


def item_view(store, todo):
    lines, names = [], []
    for b in m.item_blocks(todo):
        parts, it = [], b.begin()
        while not it.atEnd():
            frag = it.fragment()
            f = frag.charFormat()
            if f.isImageFormat():
                names.append(f.toImageFormat().name())
            else:
                parts.append(frag.text())
            it += 1
        if line := "".join(parts).strip():
            lines.append(line)
    images = []
    for name in names:
        img = QImage.fromData(store.get_image(name))
        if img.isNull():
            raise ValueError(f"{store.path}: image {name!r} does not decode")
        images.append(img)
    return ItemView(lines, images, m.task_start(todo))


class PickCard(QuickAdd):
    # the QuickAdd location tree alone: no text, marker or dates, and the pick is the card
    def __init__(self, parent, entries, default, counts):
        super().__init__(parent, entries, default, counts)
        self.setWindowTitle("Move To")
        self.target = None
        self.form = self.layout().itemAt(0).layout()
        for field in (self.text, self.opts, self.dates):
            self.form.setRowVisible(field, False)

    def chosen(self):
        super().chosen()
        self.dates.hide()

    def commit(self):
        e = self.choice
        if e is None:
            self.loc.setStyleSheet(rdb_theme.error("red", "QLineEdit"))
            self.loc.setFocus()
            return
        self.target = e
        self.accept()


class TriagePage(QWidget):
    move = Signal()
    date = Signal(object, object)
    urgent = Signal()
    delete = Signal()
    leave = Signal()
    undo = Signal()
    redo = Signal()

    def __init__(self, settings):
        super().__init__()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.views, self.at, self.acted = [], 0, 0
        self.body = QLabel()
        font = QFont(self.body.font())
        font.setPointSize(font.pointSize() + 4)
        self.body.setFont(font)
        self.body.setWordWrap(True)
        self.body.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.pics = QWidget()
        self.pic_col = QVBoxLayout(self.pics)
        self.pic_col.setContentsMargins(0, 0, 0, 0)
        self.pic_col.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.state = QLabel()
        col = QVBoxLayout(self)
        col.setContentsMargins(MARGIN, MARGIN, MARGIN, 12)
        col.addWidget(self.body)
        col.addWidget(self.pics, 1)
        col.addWidget(self.state)
        self.cal = DatePopup(self, settings)
        self.cal.picked.connect(self.date)

    @property
    def current(self):
        return self.views[self.at] if self.views else None

    def load(self, views, at=None):
        self.views = views
        if at is not None:
            self.at = at
        self.at = max(0, min(self.at, len(views) - 1))
        self.render()

    def render(self):
        cur = self.current
        self.body.setText("" if cur is None else "\n".join(cur.lines))
        while self.pic_col.count():
            old = self.pic_col.takeAt(0).widget()
            old.setParent(None)
            old.deleteLater()
        for img in () if cur is None else cur.images:
            lab = QLabel()
            lab.setPixmap(self.fit(img))
            self.pic_col.addWidget(lab)
        self.state.setText(
            f"{self.at + 1} of {len(self.views)}" if self.views else "0 left"
        )

    def fit(self, img):
        pix = QPixmap.fromImage(img)
        room = max(self.width() - 2 * MARGIN, 64)
        if pix.width() > room or pix.height() > PIC_H:
            pix = pix.scaled(
                room,
                PIC_H,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        return pix

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.current is not None:
            self.render()

    def step(self, d):
        self.at = max(0, min(self.at + d, len(self.views) - 1))
        self.render()

    def act(self, signal):
        if self.views:
            self.acted = self.at
            signal.emit()

    def keyPressEvent(self, e):
        k, mods = e.key(), e.modifiers()
        K = Qt.Key
        if mods == CTRL and k == K.Key_Z:
            self.undo.emit()
        elif (mods == CTRL and k == K.Key_Y) or (mods == CTRL_SHIFT and k == K.Key_Z):
            self.redo.emit()
        elif mods != Qt.KeyboardModifier.NoModifier:
            super().keyPressEvent(e)
        elif k == K.Key_Escape:
            self.leave.emit()
        elif k in (K.Key_Space, K.Key_Right):
            self.step(1)
        elif k == K.Key_Left:
            self.step(-1)
        elif k == K.Key_M:
            self.act(self.move)
        elif k == K.Key_U:
            self.act(self.urgent)
        elif k == K.Key_X:
            self.act(self.delete)
        elif k == K.Key_D and self.views:
            self.acted = self.at
            at = self.mapToGlobal(QPoint(MARGIN, MARGIN))
            self.cal.popup_at(at, self.current.start)
        else:
            super().keyPressEvent(e)
