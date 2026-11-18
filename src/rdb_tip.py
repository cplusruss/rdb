from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QPainter, QPainterPath, QPalette, QPen, QPolygonF
from PySide6.QtWidgets import QApplication, QWidget

PAD_X, PAD_Y, RADIUS = 11, 6, 7
ARROW_W, ARROW_H = 16, 11
# the arrow tip sits this far in from the balloon's left edge, as in the Windows balloon
ARROW_IN = 22
# text size against the application font
FONT_SCALE = 1.4
GAP = 2
HIDE_ON = {
    QEvent.Type.MouseButtonPress,
    QEvent.Type.MouseButtonDblClick,
    QEvent.Type.KeyPress,
    QEvent.Type.Wheel,
    QEvent.Type.WindowDeactivate,
}
ON = True
_tips = None


def place(anchor, w, h, screen):
    # anchor, screen: global QRects; w, h: balloon size with its arrow
    # -> (top-left, arrow points down, arrow tip x from the balloon's left)
    cx = anchor.center().x()
    x = max(screen.left(), min(cx - ARROW_IN, screen.right() + 1 - w))
    down = anchor.top() - GAP - h >= screen.top()
    y = anchor.top() - GAP - h if down else anchor.bottom() + 1 + GAP
    lo, hi = RADIUS + ARROW_W // 2, w - RADIUS - ARROW_W // 2
    return QPoint(x, y), down, max(lo, min(cx - x, hi))


class Balloon(QWidget):
    def __init__(self):
        super().__init__(
            None, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.text, self.down, self.ax, self.anchor = "", True, ARROW_IN, None

    def show_for(self, widget):
        self.anchor, self.text = widget, widget.toolTip()
        font = QApplication.font()
        font.setPointSizeF(font.pointSizeF() * FONT_SCALE)
        self.setFont(font)
        fm = self.fontMetrics()
        w = fm.horizontalAdvance(self.text) + 2 * PAD_X + 1
        w = max(w, 2 * RADIUS + ARROW_W + 1)
        h = fm.height() + 2 * PAD_Y + ARROW_H + 1
        anchor = QRect(widget.mapToGlobal(QPoint(0, 0)), widget.size())
        screen = (widget.screen() or QApplication.primaryScreen()).availableGeometry()
        at, self.down, self.ax = place(anchor, w, h, screen)
        self.setGeometry(QRect(at.x(), at.y(), w, h))
        self.show()
        self.raise_()
        self.update()

    def dismiss(self):
        self.anchor = None
        self.hide()

    def paintEvent(self, _e):
        pal = QApplication.palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width() - 1, self.height() - 1
        top = 0.5 if self.down else ARROW_H + 0.5
        body = QRectF(0.5, top, w, h - ARROW_H)
        x = self.ax + 0.5
        if self.down:
            tri = (QPointF(x - ARROW_W / 2, body.bottom() - 1), QPointF(x, h + 0.5))
            tri += (QPointF(x + ARROW_W / 2, body.bottom() - 1),)
        else:
            tri = (QPointF(x - ARROW_W / 2, body.top() + 1), QPointF(x, 0.5))
            tri += (QPointF(x + ARROW_W / 2, body.top() + 1),)
        path = QPainterPath()
        path.addRoundedRect(body, RADIUS, RADIUS)
        arrow = QPainterPath()
        arrow.addPolygon(QPolygonF(tri))
        arrow.closeSubpath()
        path = path.united(arrow)
        p.setPen(QPen(pal.color(QPalette.ColorRole.ToolTipText), 1))
        p.setBrush(pal.color(QPalette.ColorRole.ToolTipBase))
        p.drawPath(path)
        p.drawText(body, Qt.AlignmentFlag.AlignCenter, self.text)
        p.end()


class _Tips(QObject):
    # every widget's tool tip shows as the balloon; with tips off none shows at all
    def __init__(self):
        super().__init__()
        self.balloon = Balloon()

    def eventFilter(self, obj, e):
        t = e.type()
        if t == QEvent.Type.ToolTip:
            if not isinstance(obj, QWidget) or not obj.toolTip():
                return False
            if ON and obj.isVisible():
                self.balloon.show_for(obj)
            return True
        if self.balloon.anchor is not None and (
            t in HIDE_ON
            or (
                t in (QEvent.Type.Leave, QEvent.Type.Hide)
                and obj is self.balloon.anchor
            )
        ):
            self.balloon.dismiss()
        return False


def configure(s):
    global ON, _tips
    ON = s["tooltips"]
    if _tips is None:
        _tips = _Tips()
        QApplication.instance().installEventFilter(_tips)
    if not ON:
        _tips.balloon.dismiss()
