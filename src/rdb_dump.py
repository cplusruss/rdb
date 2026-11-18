from collections import namedtuple

from PySide6.QtCore import QEvent, QPoint, QRect, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QCursor,
    QGuiApplication,
    QImage,
    QPainter,
    QPen,
    QRegion,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

import rdb_theme
from rdb_model import url_text

# text, url and image are alternatives; extra is a second line under the item
DumpItem = namedtuple("DumpItem", "text url image extra", defaults=("", "", None, ""))
SNIP_DELAY_MS = 120
CTRL_SHIFT = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
DIM = QColor(0, 0, 0, 110)
# a widget waiting for its deferred delete stays referenced, so no wrapper frees it mid-event
KEEP = set()


def retire(widget):
    KEEP.add(widget)
    widget.destroyed.connect(lambda *_: KEEP.discard(widget))
    widget.hide()
    widget.deleteLater()


def grab_screen(screen):
    return screen.grabWindow(0)


def span(a, b):
    # both corner pixels are inside; QRect.normalized() shrinks a rect drawn the other way
    return QRect(
        QPoint(min(a.x(), b.x()), min(a.y(), b.y())),
        QPoint(max(a.x(), b.x()), max(a.y(), b.y())),
    )


def crop(pix, r):
    # r in the pixmap's logical coordinates; the image is in device pixels, None when empty
    ratio = pix.devicePixelRatio()
    dev = QRect(
        round(r.x() * ratio),
        round(r.y() * ratio),
        round(r.width() * ratio),
        round(r.height() * ratio),
    ).intersected(pix.rect())
    if dev.isEmpty():
        return None
    return pix.copy(dev).toImage()


def clip_items(md, title):
    if md is None:
        return []
    text = md.text() if md.hasText() else ""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if url := url_text(text):
        items = [DumpItem(url=url)]
    elif lines:
        items = [DumpItem(text=ln) for ln in lines]
    elif md.hasImage():
        data = md.imageData()
        items = [DumpItem(image=data if isinstance(data, QImage) else data.toImage())]
    else:
        return []
    if title:
        items[0] = items[0]._replace(extra=title)
    return items


class SnipOverlay(QWidget):
    def __init__(self, screen, pix, finish):
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self.pix, self.finish = pix, finish
        self.a = self.b = None
        self.setGeometry(screen.geometry())
        self.setCursor(Qt.CursorShape.CrossCursor)

    def selection(self):
        return span(self.a, self.b)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.a = self.b = e.position().toPoint()

    def mouseMoveEvent(self, e):
        if self.a is not None:
            self.b = e.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton or self.a is None:
            return
        self.b = e.position().toPoint()
        rect = None if self.a == self.b else self.selection()
        self.a = None
        self.finish(None if rect is None else crop(self.pix, rect))

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.finish(None)

    def paintEvent(self, e):
        p = QPainter(self)
        p.drawPixmap(self.rect(), self.pix)
        if self.a is None:
            p.fillRect(self.rect(), DIM)
            return
        sel = self.selection()
        p.setClipRegion(QRegion(self.rect()) - QRegion(sel))
        p.fillRect(self.rect(), DIM)
        p.setClipping(False)
        p.setPen(QPen(QColor("#ffffff"), 1))
        p.drawRect(sel)


class Snipper:
    # one frozen overlay per screen; done gets the cropped image, or None for Esc or no region
    def __init__(self, done, grab=grab_screen):
        self.done, self.over = done, False
        screens = QGuiApplication.screens()
        self.overlays = [SnipOverlay(s, grab(s), self.finish) for s in screens]
        for ov in self.overlays:
            ov.show()
        here = QGuiApplication.screenAt(QCursor.pos())
        self.overlays[screens.index(here) if here in screens else 0].activateWindow()

    def finish(self, img):
        if self.over:
            return
        self.over = True
        for ov in self.overlays:
            retire(ov)
        self.done(img)


class DumpBox(QDialog):
    def __init__(self, file, title, grab=grab_screen):
        super().__init__(
            None,
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.file, self.title, self.grab = file, title, grab
        self.filed, self.snipper = 0, None
        self.snip_delay_ms = SNIP_DELAY_MS
        self.edit = QLineEdit()
        self.state = QLabel()
        col = QVBoxLayout(self)
        col.setContentsMargins(8, 8, 8, 6)
        col.setSpacing(4)
        col.addWidget(self.edit)
        col.addWidget(self.state)
        self.setFixedWidth(420)
        rdb_theme.follow(self, rdb_theme.edge_sheet)
        self.edit.returnPressed.connect(self.enter)
        self.finished.connect(lambda _: retire(self))
        self.show_count()

    def show_count(self):
        self.state.setText(f"{self.filed} filed")

    def popup(self):
        if self.snipper is not None:
            return
        scr = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        self.adjustSize()
        mid = QPoint(self.width() // 2, self.height() // 2)
        self.move(scr.availableGeometry().center() - mid)
        self.show()
        self.raise_()
        self.activateWindow()
        self.edit.setFocus()

    def put(self, item):
        self.file(item)
        self.filed += 1
        self.show_count()

    def enter(self):
        text = self.edit.text().strip()
        if not text:
            self.reject()
            return
        self.edit.clear()
        self.put(DumpItem(text=text))

    def grab_clipboard(self):
        items = clip_items(QApplication.clipboard().mimeData(), self.title)
        if not items:
            self.state.setText("empty clipboard")
        for item in items:
            self.put(item)

    def start_snip(self):
        self.hide()
        QTimer.singleShot(self.snip_delay_ms, self.run_snip)

    def run_snip(self):
        self.snipper = Snipper(self.snipped, self.grab)

    def snipped(self, img):
        self.snipper = None
        if img is not None:
            self.put(DumpItem(image=img))
        self.popup()

    def event(self, e):
        # the main window owns Ctrl+Shift+S; the box takes it before the shortcut map does
        if e.type() == QEvent.Type.ShortcutOverride and e.modifiers() == CTRL_SHIFT:
            if e.key() in (Qt.Key.Key_V, Qt.Key.Key_S):
                e.accept()
                return True
        return super().event(e)

    def keyPressEvent(self, e):
        if e.modifiers() == CTRL_SHIFT and e.key() == Qt.Key.Key_V:
            self.grab_clipboard()
        elif e.modifiers() == CTRL_SHIFT and e.key() == Qt.Key.Key_S:
            self.start_snip()
        else:
            super().keyPressEvent(e)
