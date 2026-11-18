import math
import re
import string

from PySide6.QtCore import QEventLoop, QPoint, QPointF, QRect, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QIcon,
    QImage,
    QLinearGradient,
    QPainter,
    QPalette,
    QPixmap,
    QPolygonF,
    QPen,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

BASE = (
    "#ffffff",
    "#000000",
    "#e7e6e6",
    "#44546a",
    "#4472c4",
    "#ed7d31",
    "#a5a5a5",
    "#ffc000",
    "#5b9bd5",
    "#70ad47",
)
STANDARD = (
    "#c00000",
    "#ff0000",
    "#ffc000",
    "#ffff00",
    "#92d050",
    "#00b050",
    "#00b0f0",
    "#0070c0",
    "#002060",
    "#7030a0",
)
RECENT_KEY, RECENT_MAX = "recentCustomColors", 10
EXPANDED_KEY = "colorSelectorExpanded"
SW, PITCH, PAD = 16, 18, 2
GREY = QColor("#808080")

_store = None
_style = None
_shade = None
_unshade = None


def configure(get_json, set_json, style=None, shade=None, unshade=None):
    # get_json(key, default) and set_json(key, value) keep the recents and expanded state; style(popup) lets the app theme it;
    # shade(color) is how a colour is drawn, for an app whose colours are shown mirrored: the picker still returns the original;
    # unshade(color) is its inverse, for a colour read off the screen
    global _store, _style, _shade, _unshade
    _store, _style, _shade, _unshade = (get_json, set_json), style, shade, unshade


def drawn(c):
    return QColor(c) if _shade is None else QColor(_shade(c))


def _get(key, default):
    return _need()[0](key, default)


def _set(key, value):
    _need()[1](key, value)


def _need():
    if _store is None:
        raise RuntimeError("colorpopup.configure(get_json, set_json) was not called")
    return _store


def pipette_icon(size):
    # the 16 x 16 pipette glyph, stroked in the button-text colour at whole device pixels
    dpr = QApplication.instance().devicePixelRatio()
    pm = QPixmap(round(size * dpr), round(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(size / 16, size / 16)
    stroke = max(1, round(1.5 * dpr * size / 16)) / (dpr * size / 16)
    col = QApplication.palette().buttonText().color()
    p.setBrush(Qt.NoBrush)
    for x1, y1, x2, y2, w in ((11.6, 4.4, 13.1, 2.9, 3.6), (8.4, 4.4, 11.6, 7.6, 1.6)):
        p.setPen(QPen(col, w if w > 2.6 else stroke, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
    p.setPen(Qt.NoPen)
    p.setBrush(col)
    pts = (8.5, 5.5, 10.5, 7.5, 5.5, 12.5, 1.4, 14.6, 3.5, 10.5)
    p.drawPolygon(
        QPolygonF([QPointF(pts[i], pts[i + 1]) for i in range(0, len(pts), 2)])
    )
    p.end()
    return QIcon(pm)


def _mix(name, to, f):
    c, t = QColor(name), QColor(to)
    return QColor(
        *(round(a + (b - a) * f) for a, b in zip(c.getRgb()[:3], t.getRgb()[:3]))
    ).name()


def _theme():
    # white and black have no tint to take, so they get plain grey ramps
    ramps = {
        0: ("#f2f2f2", "#d9d9d9", "#bfbfbf", "#a6a6a6", "#808080"),
        1: ("#7f7f7f", "#595959", "#3f3f3f", "#262626", "#0c0c0c"),
    }
    cols = []
    for i, b in enumerate(BASE):
        ramp = ramps.get(i) or (
            _mix(b, "#ffffff", 0.8),
            _mix(b, "#ffffff", 0.6),
            _mix(b, "#ffffff", 0.4),
            _mix(b, "#000000", 0.25),
            _mix(b, "#000000", 0.5),
        )
        cols.append((b, *ramp))
    return tuple(zip(*cols))


THEME = _theme()


def parse_hex(text, alpha):
    t = text.strip().lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]+", t):
        return QColor()
    if len(t) == 3:
        t = "".join(ch * 2 for ch in t)
    if len(t) == 8 and not alpha or len(t) not in (6, 8):
        return QColor()
    c = QColor(int(t[0:2], 16), int(t[2:4], 16), int(t[4:6], 16))
    if len(t) == 8:
        c.setAlpha(int(t[6:8], 16))
    return c


def _invalid_ink(widget):
    dark = widget.palette().color(QPalette.Window).lightness() < 128
    return "#ff6b6b" if dark else "#c00000"


def format_hex(c, alpha):
    return "#%02x%02x%02x" % (c.red(), c.green(), c.blue()) + (
        "%02x" % c.alpha() if alpha else ""
    )


def _frame(p, r, col):
    w, h = r.width(), r.height()
    p.fillRect(r.x(), r.y(), w, 1, col)
    p.fillRect(r.x(), r.y() + h - 1, w, 1, col)
    p.fillRect(r.x(), r.y() + 1, 1, h - 2, col)
    p.fillRect(r.x() + w - 1, r.y() + 1, 1, h - 2, col)


def _checker(p, r):
    p.fillRect(r, QColor("#ffffff"))
    for y in range(0, r.height(), 4):
        for x in range(0, r.width(), 4):
            if (x + y) // 4 % 2:
                p.fillRect(
                    r.x() + x,
                    r.y() + y,
                    min(4, r.width() - x),
                    min(4, r.height() - y),
                    QColor("#c0c0c0"),
                )


def _hold_popups():
    # the popups this one opened over (the quick panel): out of the way while the dropper has the screen
    held = []
    while (w := QApplication.activePopupWidget()) is not None:
        w.hide()
        held.append(w)
    return held


def _restore(held):
    for w in reversed(held):
        w.show()


class _Cell:
    def __init__(self, dev, ratio, color, kind):
        # dev: whole device pixels, what is painted; rect: the same in logical pixels, what is hit-tested
        self.dev, self.color, self.kind = dev, color, kind
        self.rect = QRect(
            round(dev.x() / ratio),
            round(dev.y() / ratio),
            max(round(dev.width() / ratio), 1),
            max(round(dev.height() / ratio), 1),
        )


class _Swatches(QWidget):
    picked = Signal(QColor, str)
    hovered = Signal(str)
    typed = Signal(str)

    def __init__(self, parent, current, recent, none):
        super().__init__(parent)
        self.current = QColor(current)
        self.cells, self.labels = [], []
        self.hover = self.focus_cell = self._down = None
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        # the grid is laid out in whole device pixels: one swatch size, one gap, a 1 px border at every scale
        self.ratio = d = self.devicePixelRatioF()
        sw, gap, pad, sec = (round(v * d) for v in (SW, PITCH - SW, PAD, 3))
        pitch = sw + gap
        lh = round(self.fontMetrics().height() * d)
        w = 2 * pad + 9 * pitch + sw
        y = pad
        if none:
            self.cells.append(
                _Cell(QRect(pad, y, w - 2 * pad, pitch), d, QColor(0, 0, 0, 0), "none")
            )
            y += pitch + sec
        sections = [
            ("Theme", [c for row in THEME for c in row], "theme"),
            ("Standard", STANDARD, "standard"),
            ("Recent", recent[:RECENT_MAX], "recent"),
        ]
        for label, colors, kind in sections:
            if not colors:
                continue
            self.labels.append(
                (
                    QRect(round(pad / d), round(y / d), round(w / d), round(lh / d)),
                    label,
                )
            )
            y += lh + round(d)
            for i, name in enumerate(colors):
                self.cells.append(
                    _Cell(
                        QRect(pad + i % 10 * pitch, y + i // 10 * pitch, sw, sw),
                        d,
                        QColor(name),
                        kind,
                    )
                )
            y += (len(colors) + 9) // 10 * pitch + sec
        self.setFixedSize(math.ceil(w / d), math.ceil((y - sec + pad) / d))
        self.focus_cell = next(
            (c for c in self.cells if self.is_current(c) and c.kind != "none"), None
        ) or next(c for c in self.cells if c.kind == "theme")

    def set_current(self, color):
        self.current = QColor(color)
        self.update()

    def is_current(self, cell):
        if cell.kind == "none":
            return self.current.alpha() == 0
        return cell.color.rgb() == self.current.rgb() and self.current.alpha() > 0

    def _at(self, pos):
        return next((c for c in self.cells if c.rect.contains(pos)), None)

    def paintEvent(self, e):
        p = QPainter(self)
        pal = self.palette()
        p.setPen(pal.color(QPalette.WindowText))
        for r, text in self.labels:
            p.drawText(r, Qt.AlignLeft | Qt.AlignVCenter, text)
        p.save()
        p.scale(1 / self.ratio, 1 / self.ratio)
        for c in self.cells:
            r = c.dev
            lit = c is self.hover or (c is self.focus_cell and self.hasFocus())
            p.fillRect(
                r, pal.color(QPalette.Base) if c.kind == "none" else drawn(c.color)
            )
            if self.is_current(c):
                _frame(p, r.adjusted(-1, -1, 1, 1), pal.color(QPalette.Highlight))
            _frame(p, r, QColor("#000000") if lit else GREY)
            if lit:
                _frame(p, r.adjusted(1, 1, -1, -1), QColor("#ffffff"))
        p.restore()
        p.setPen(pal.color(QPalette.WindowText))
        for c in self.cells:
            if c.kind == "none":
                p.drawText(
                    c.rect.adjusted(4, 0, 0, 0),
                    Qt.AlignLeft | Qt.AlignVCenter,
                    "No Colour",
                )

    def mouseMoveEvent(self, e):
        c = self._at(e.position().toPoint())
        if c is not self.hover:
            self.hover = c
            self.hovered.emit(c.color.name() if c and c.kind != "none" else "")
            self.update()

    def leaveEvent(self, e):
        self.hover = None
        self.hovered.emit("")
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._down = self._at(e.position().toPoint())

    def mouseReleaseEvent(self, e):
        c = self._at(e.position().toPoint())
        if e.button() == Qt.LeftButton and c is not None and c is self._down:
            self.focus_cell = c
            self.picked.emit(c.color, c.kind)

    def focusInEvent(self, e):
        super().focusInEvent(e)
        self.update()

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        self.update()

    def _move(self, dx, dy):
        f = self.focus_cell.rect
        if dy:
            ahead = [c for c in self.cells if (c.rect.y() - f.y()) * dy > 0]
            if ahead:
                near = min(abs(c.rect.y() - f.y()) for c in ahead)
                ahead = [c for c in ahead if abs(c.rect.y() - f.y()) == near]
        else:
            ahead = [
                c
                for c in self.cells
                if c.rect.y() == f.y() and (c.rect.x() - f.x()) * dx > 0
            ]
            if ahead:
                near = min(abs(c.rect.x() - f.x()) for c in ahead)
                ahead = [c for c in ahead if abs(c.rect.x() - f.x()) == near]
        if ahead:
            self.focus_cell = min(
                ahead, key=lambda c: abs(c.rect.center().x() - f.center().x())
            )
            self.update()

    def keyPressEvent(self, e):
        k = e.key()
        step = {
            Qt.Key_Left: (-1, 0),
            Qt.Key_Right: (1, 0),
            Qt.Key_Up: (0, -1),
            Qt.Key_Down: (0, 1),
        }.get(k)
        if step:
            self._move(*step)
        elif k in (Qt.Key_Return, Qt.Key_Enter):
            self.picked.emit(self.focus_cell.color, self.focus_cell.kind)
        elif (
            e.text()
            and (e.text() == "#" or e.text() in string.hexdigits)
            and not e.modifiers() & (Qt.ControlModifier | Qt.AltModifier)
        ):
            self.typed.emit(e.text())
        else:
            super().keyPressEvent(e)


class _Pad(QWidget):
    def _frame(self, p):
        _frame(p, self.rect(), GREY)


class _SV(_Pad):
    changed = Signal(float, float)

    def __init__(self, parent, w, h):
        super().__init__(parent)
        self.setFixedSize(w, h)
        self.h = self.s = self.v = 0.0

    def set(self, h, s, v):
        self.h, self.s, self.v = h, s, v
        self.update()

    def value(self):
        return self.s, self.v

    def point_for(self, s, v):
        return QPoint(
            round(s * (self.width() - 1)), round((1 - v) * (self.height() - 1))
        )

    def _from(self, pos):
        s = min(max(pos.x() / (self.width() - 1), 0.0), 1.0)
        v = min(max(1 - pos.y() / (self.height() - 1), 0.0), 1.0)
        self.changed.emit(s, v)

    def mousePressEvent(self, e):
        self._from(e.position())

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.LeftButton:
            self._from(e.position())

    def paintEvent(self, e):
        p = QPainter(self)
        r = self.rect().adjusted(1, 1, -1, -1)
        p.fillRect(r, QColor.fromHsvF(self.h, 1, 1))
        g = QLinearGradient(r.left(), 0, r.right() + 1, 0)
        g.setColorAt(0, QColor(255, 255, 255))
        g.setColorAt(1, QColor(255, 255, 255, 0))
        p.fillRect(r, g)
        g = QLinearGradient(0, r.top(), 0, r.bottom() + 1)
        g.setColorAt(0, QColor(0, 0, 0, 0))
        g.setColorAt(1, QColor(0, 0, 0))
        p.fillRect(r, g)
        self._frame(p)
        c = self.point_for(self.s, self.v)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(Qt.NoBrush)
        p.setPen(QColor("#000000"))
        p.drawEllipse(QPointF(c.x() + 0.5, c.y() + 0.5), 5, 5)
        p.setPen(QColor("#ffffff"))
        p.drawEllipse(QPointF(c.x() + 0.5, c.y() + 0.5), 4, 4)


class _Bar(_Pad):
    changed = Signal(float)

    def __init__(self, parent, w, h):
        super().__init__(parent)
        self.setFixedSize(w, h)
        self.frac = 0.0

    def point_for(self, frac):
        return QPoint(self.width() // 2, round(self._y(frac) * (self.height() - 1)))

    def _from(self, pos):
        self.changed.emit(self._frac(min(max(pos.y() / (self.height() - 1), 0.0), 1.0)))

    def mousePressEvent(self, e):
        self._from(e.position())

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.LeftButton:
            self._from(e.position())

    def _marker(self, p):
        y = self.point_for(self.frac).y()
        _frame(p, QRect(0, y - 2, self.width(), 5), QColor("#000000"))
        _frame(p, QRect(1, y - 1, self.width() - 2, 3), QColor("#ffffff"))


class _Hue(_Bar):
    def set(self, h):
        self.frac = h
        self.update()

    def value(self):
        return self.frac

    def _y(self, frac):
        return frac

    def _frac(self, y):
        return min(y, 359 / 360)

    def paintEvent(self, e):
        p = QPainter(self)
        r = self.rect().adjusted(1, 1, -1, -1)
        g = QLinearGradient(0, r.top(), 0, r.bottom() + 1)
        for i in range(7):
            g.setColorAt(i / 6, QColor.fromHsvF(i / 6 % 1.0, 1, 1))
        p.fillRect(r, g)
        self._frame(p)
        self._marker(p)


class _AlphaBar(_Bar):
    def __init__(self, parent, w, h):
        super().__init__(parent, w, h)
        self.rgb = QColor()
        self.a = 255

    def set(self, rgb, a):
        self.rgb, self.a = QColor(rgb), a
        self.frac = a / 255
        self.update()

    def value(self):
        return self.a

    def _y(self, frac):
        return 1 - frac

    def _frac(self, y):
        return 1 - y

    def paintEvent(self, e):
        p = QPainter(self)
        r = self.rect().adjusted(1, 1, -1, -1)
        _checker(p, r)
        g = QLinearGradient(0, r.top(), 0, r.bottom() + 1)
        rgb = drawn(self.rgb)
        g.setColorAt(0, rgb)
        g.setColorAt(1, QColor(rgb.red(), rgb.green(), rgb.blue(), 0))
        p.fillRect(r, g)
        self._frame(p)
        self._marker(p)


class _Compare(_Pad):
    def __init__(self, parent, old, h):
        super().__init__(parent)
        self.old, self.new = QColor(old), QColor(old)
        self.setMinimumSize(40, h)

    def paintEvent(self, e):
        p = QPainter(self)
        r = self.rect().adjusted(1, 1, -1, -1)
        mid = r.x() + r.width() // 2
        for col, box in (
            (self.old, QRect(r.x(), r.y(), mid - r.x(), r.height())),
            (self.new, QRect(mid, r.y(), r.right() + 1 - mid, r.height())),
        ):
            _checker(p, box)
            p.fillRect(box, drawn(col))
        self._frame(p)


class _Popup(QFrame):
    closed = Signal()

    def __init__(self, parent, current, alpha, none):
        super().__init__(parent, Qt.Popup)
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)
        self.alpha, self.result = alpha, QColor()
        cur = QColor(current) if QColor(current).isValid() else QColor("#000000")
        if not alpha:
            cur.setAlpha(255)
        self.old = cur
        self.expanded = bool(_get(EXPANDED_KEY, False))
        self.hsv = [0.0, 0.0, 0.0]
        self.rgb, self.a = QColor(), 255
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(3)
        self.swatches = _Swatches(self, cur, _get(RECENT_KEY, []), none)
        self.swatches.picked.connect(self._grid_pick)
        self.swatches.hovered.connect(lambda s: self.status.setText(s))
        self.swatches.typed.connect(self._typed)
        lay.addWidget(self.swatches)
        self.status = QLabel()
        self.status.setFixedHeight(self.fontMetrics().height())
        lay.addWidget(self.status)
        row = QHBoxLayout()
        row.setSpacing(3)
        self.hex = QLineEdit()
        self.hex.setFixedWidth(self.fontMetrics().horizontalAdvance("#00000000") + 14)
        self.hex.textEdited.connect(self._hex_edited)
        row.addWidget(self.hex)
        self.drop = QToolButton()
        self.drop.setObjectName("colorDropper")
        self.drop.setIcon(pipette_icon(16))
        self.drop.setToolTip("Eyedropper")
        self.drop.setAutoRaise(True)
        self.drop.clicked.connect(self._dropper)
        row.addWidget(self.drop)
        row.addStretch()
        self.more = QToolButton()
        self.more.setAutoRaise(True)
        self.more.setFixedWidth(
            max(self.fontMetrics().horizontalAdvance(t) for t in ("More...", "Less"))
            + 14
        )
        self.more.clicked.connect(self._toggle)
        row.addWidget(self.more)
        lay.addLayout(row)
        self._build_picker(lay)
        self.setFocusProxy(self.swatches)
        self._set_rgb(cur)
        self.a = cur.alpha()
        self._update()
        self._apply_expanded()

    def _build_picker(self, lay):
        self.picker = QWidget(self)
        pl = QVBoxLayout(self.picker)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(3)
        top = QHBoxLayout()
        top.setSpacing(4)
        sv_w = self.swatches.width() - 20 - (20 if self.alpha else 0)
        self.sv = _SV(self.picker, sv_w, 100)
        self.sv.changed.connect(lambda s, v: self._set_hsv(self.hsv[0], s, v))
        self.hue = _Hue(self.picker, 16, 100)
        self.hue.changed.connect(lambda h: self._set_hsv(h, self.hsv[1], self.hsv[2]))
        top.addWidget(self.sv)
        top.addWidget(self.hue)
        self.alpha_bar = None
        if self.alpha:
            self.alpha_bar = _AlphaBar(self.picker, 16, 100)
            self.alpha_bar.changed.connect(self._alpha_changed)
            top.addWidget(self.alpha_bar)
        top.addStretch()
        pl.addLayout(top)
        grid = QGridLayout()
        grid.setSpacing(2)
        self.spins = {}
        for i, (k, hi) in enumerate(
            (("r", 255), ("g", 255), ("b", 255), ("h", 359), ("s", 255), ("v", 255))
        ):
            s = QSpinBox()
            s.setRange(0, hi)
            s.setButtonSymbols(QSpinBox.NoButtons)
            s.setFixedWidth(self.fontMetrics().horizontalAdvance("0000") + 8)
            s.valueChanged.connect(self._from_rgb if k in "rgb" else self._from_hsv)
            self.spins[k] = s
            grid.addWidget(QLabel(k.upper()), i // 3, i % 3 * 2)
            grid.addWidget(s, i // 3, i % 3 * 2 + 1)
        grid.setColumnStretch(6, 1)
        pl.addLayout(grid)
        bottom = QHBoxLayout()
        bottom.setSpacing(3)
        self.compare = _Compare(self.picker, self.old, 22)
        bottom.addWidget(self.compare, 1)
        self.ok = QPushButton("OK")
        self.cancel = QPushButton("Cancel")
        for b in (self.ok, self.cancel):
            b.setFixedWidth(52)
            b.setAutoDefault(False)
            bottom.addWidget(b)
        self.ok.clicked.connect(lambda: self._finish(self.new_color(), True))
        self.cancel.clicked.connect(self.close)
        pl.addLayout(bottom)
        lay.addWidget(self.picker)

    def new_color(self):
        c = QColor(self.rgb)
        c.setAlpha(self.a if self.alpha else 255)
        return c

    def _set_rgb(self, c):
        self.rgb = QColor(c.red(), c.green(), c.blue())
        h, s, v = self.rgb.hsvHueF(), self.rgb.hsvSaturationF(), self.rgb.valueF()
        if h < 0:
            h = self.hsv[0]  # grey has no hue of its own: keep the one the picker had
        if v == 0:
            s = self.hsv[1]
        self.hsv = [h, s, v]

    def _set_hsv(self, h, s, v, skip=None):
        self.hsv = [h, s, v]
        self.rgb = QColor.fromHsvF(h, s, v)
        self._update(skip)

    def _update(self, skip=None):
        r = self.rgb
        if skip != "hex":
            self.hex.setText(format_hex(self.new_color(), self.alpha))
            self._mark_valid()
        h, s, v = self.hsv
        vals = {
            "r": r.red(),
            "g": r.green(),
            "b": r.blue(),
            "h": round(h * 360) % 360,
            "s": round(s * 255),
            "v": round(v * 255),
        }
        for k, sp in self.spins.items():
            if skip == "rgb" and k in "rgb" or skip == "hsv" and k in "hsv":
                continue
            sp.blockSignals(True)
            sp.setValue(vals[k])
            sp.blockSignals(False)
        self.sv.set(h, s, v)
        self.hue.set(h)
        if self.alpha_bar:
            self.alpha_bar.set(r, self.a)
        self.compare.new = self.new_color()
        self.compare.update()
        self.swatches.set_current(self.new_color())

    def _from_rgb(self):
        s = self.spins
        self._set_rgb(QColor(s["r"].value(), s["g"].value(), s["b"].value()))
        self._update("rgb")

    def _from_hsv(self):
        s = self.spins
        self._set_hsv(
            s["h"].value() / 360, s["s"].value() / 255, s["v"].value() / 255, "hsv"
        )

    def _alpha_changed(self, frac):
        self.a = round(frac * 255)
        self._update()

    def _mark_valid(self, ok=True):
        pal = self.hex.palette()
        pal.setColor(
            QPalette.Text,
            self.palette().color(QPalette.Text) if ok else QColor(_invalid_ink(self)),
        )
        self.hex.setPalette(pal)

    def _hex_edited(self, text):
        c = parse_hex(text, self.alpha)
        if not c.isValid():
            self._mark_valid(False)
            return
        self._set_rgb(c)
        if len(text.strip().lstrip("#")) == 8:
            self.a = c.alpha()
        self._update("hex")

    def _typed(self, t):
        self.hex.setFocus()
        self.hex.setText("#" + ("" if t == "#" else t))

    def _grid_pick(self, c, kind):
        c = QColor(c)
        if kind != "none":
            c.setAlpha(self.a if self.alpha else 255)
        self._finish(c, False)

    def _finish(self, c, remember):
        if remember:
            name = c.name()
            _set(
                RECENT_KEY,
                ([name] + [n for n in _get(RECENT_KEY, []) if n != name])[:RECENT_MAX],
            )
        self.result = c
        self.close()

    def _dropper(self):
        self.hide()
        c = pick_from_screen()
        self.show()
        if not c.isValid():
            return
        if _unshade is not None:
            c = QColor(_unshade(c))
        self._set_rgb(c)
        self._update()
        if not self.expanded:
            self._finish(self.new_color(), True)

    def _toggle(self):
        self.expanded = not self.expanded
        _set(EXPANDED_KEY, self.expanded)
        self._apply_expanded()

    def _apply_expanded(self):
        self.picker.setVisible(self.expanded)
        self.more.setText("Less" if self.expanded else "More...")
        self.layout().activate()
        self.adjustSize()
        self.fit()

    def fit(self):
        screen = (
            QApplication.screenAt(self.pos()) or QApplication.primaryScreen()
        ).availableGeometry()
        self.move(
            max(screen.left(), min(self.x(), screen.right() - self.width())),
            max(screen.top(), min(self.y(), screen.bottom() - self.height())),
        )

    def _enter(self):
        if self.focusWidget() is self.hex:
            c = parse_hex(self.hex.text(), self.alpha)
            if not c.isValid():
                self._mark_valid(False)
                return
            self._set_rgb(c)
            if len(self.hex.text().strip().lstrip("#")) == 8:
                self.a = c.alpha()
        self._finish(self.new_color(), True)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.close()
        elif e.key() in (Qt.Key_Return, Qt.Key_Enter):
            if isinstance(self.focusWidget(), QPushButton):
                self.focusWidget().click()
            else:
                self._enter()
        else:
            super().keyPressEvent(e)

    def closeEvent(self, e):
        super().closeEvent(e)
        self.closed.emit()


class Dropper(QWidget):
    # a frozen screenshot of one screen; a left click takes the pixel under it, Esc or any other button cancels. A loupe
    # of CELLS x CELLS device pixels at ZOOM x follows the pointer, with the hex of the pixel under it
    CELLS, ZOOM, RIM, STRIP, OFFSET = 11, 8, 2, 18, 16
    done = Signal()

    def __init__(self, shot, geometry):
        super().__init__(
            None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.shot, self.image, self.result = shot, shot.toImage(), QColor()
        self.at = None  # the mouse, once it is over this screen
        self.setGeometry(geometry)
        self.setCursor(Qt.CrossCursor)
        self.setMouseTracking(True)

    def _ratio(self):
        return self.image.width() / self.width()

    def _pixel(self, p):
        x = min(
            max(int(p.x() * self.image.width() / self.width()), 0),
            self.image.width() - 1,
        )
        y = min(
            max(int(p.y() * self.image.height() / self.height()), 0),
            self.image.height() - 1,
        )
        return x, y

    def _sample(self, p):
        return QColor(self.image.pixel(*self._pixel(p)))

    def hex_at(self):
        return self._sample(self.at).name()

    def loupe_image(self):
        n, z, rim = self.CELLS, self.ZOOM, self.RIM
        ix, iy = self._pixel(self.at)
        area = n * z
        out = QImage(
            area + 2 * rim,
            area + 2 * rim + self.STRIP,
            QImage.Format_ARGB32_Premultiplied,
        )
        out.setDevicePixelRatio(1)
        out.fill(QColor("#ffffff"))
        p = QPainter(out)
        for j in range(n):
            for i in range(n):
                x, y = ix - n // 2 + i, iy - n // 2 + j
                inside = 0 <= x < self.image.width() and 0 <= y < self.image.height()
                p.fillRect(
                    rim + i * z,
                    rim + j * z,
                    z,
                    z,
                    QColor(self.image.pixel(x, y)) if inside else GREY,
                )
        mid = QRect(rim + n // 2 * z, rim + n // 2 * z, z, z)
        _frame(p, mid, QColor("#000000"))
        _frame(p, mid.adjusted(1, 1, -1, -1), QColor("#ffffff"))
        _frame(p, out.rect(), QColor("#000000"))
        sample = self._sample(self.at)
        p.fillRect(rim + 2, area + 2 * rim + 3, 12, 12, sample)
        _frame(p, QRect(rim + 2, area + 2 * rim + 3, 12, 12), QColor("#000000"))
        f = QFont(QApplication.font())
        f.setPixelSize(11)
        p.setFont(f)
        p.setPen(QColor("#000000"))
        p.drawText(
            QRect(rim + 18, area + 2 * rim, area - 18, self.STRIP),
            Qt.AlignLeft | Qt.AlignVCenter,
            self.hex_at(),
        )
        p.end()
        out.setDevicePixelRatio(self._ratio())
        return out

    def loupe_rect(self):
        img = self.loupe_image()
        ratio = img.devicePixelRatio()
        w, h = round(img.width() / ratio), round(img.height() / ratio)
        o, x, y = self.OFFSET, int(self.at.x()), int(self.at.y())
        left = x + o if x + o + w <= self.width() else x - o - w
        top = y + o if y + o + h <= self.height() else y - o - h
        return QRect(max(left, 0), max(top, 0), w, h)

    def paintEvent(self, e):
        p = QPainter(self)
        p.drawPixmap(self.rect(), self.shot)
        if self.at is not None:
            p.drawImage(self.loupe_rect().topLeft(), self.loupe_image())

    def showEvent(self, e):
        here = QCursor.pos() - self.geometry().topLeft()
        if self.rect().contains(here):
            self.at = QPointF(here)

    def enterEvent(self, e):
        self.grabKeyboard()

    def leaveEvent(self, e):
        self.at = None
        self.update()

    def mouseMoveEvent(self, e):
        self.at = e.position()
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.result = self._sample(e.position())
        self.done.emit()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.done.emit()


def run_dropper(ds):
    loop = QEventLoop()
    app = QApplication.instance()

    def lost(state=Qt.ApplicationActive):
        # focus went elsewhere or a screen disappeared: the frozen screenshots are stale
        if state != Qt.ApplicationActive:
            loop.quit()

    app.applicationStateChanged.connect(lost)
    app.screenRemoved.connect(loop.quit)
    try:
        for d in ds:
            d.done.connect(loop.quit)
            d.show()
        first = next((d for d in ds if d.at is not None), ds[0])
        first.activateWindow()
        first.grabKeyboard()
        loop.exec()
        return next((d.result for d in ds if d.result.isValid()), QColor())
    finally:
        app.applicationStateChanged.disconnect(lost)
        app.screenRemoved.disconnect(loop.quit)
        for d in ds:
            d.releaseKeyboard()
            d.close()
            d.deleteLater()


def grab_screens():
    return [(s.grabWindow(0), s.geometry()) for s in QApplication.screens()]


def pick_screen():
    return run_dropper([Dropper(pm, g) for pm, g in grab_screens()])


def pick_from_screen():
    # the dropper with every open popup (the quick panel) out of the screenshot, and back after
    held = _hold_popups()
    QApplication.processEvents()
    try:
        return pick_screen()
    finally:
        _restore(held)


def get_color(parent, current, alpha=False, none=False):
    # QColorDialog.getColor's contract (an invalid colour when nothing is picked), as a popup at the cursor
    p = _Popup(parent, current, alpha, none)
    if _style is not None:
        _style(p)
    p.ensurePolished()
    p.layout().activate()
    p.adjustSize()
    at = QCursor.pos()
    screen = (
        QApplication.screenAt(at) or QApplication.primaryScreen()
    ).availableGeometry()
    p.move(
        min(at.x(), screen.right() - p.width()),
        min(at.y(), screen.bottom() - p.height()),
    )
    loop = QEventLoop()
    p.closed.connect(loop.quit)
    p.show()
    p.fit()  # the shown size is final; the one before polish was not
    p.swatches.setFocus()
    loop.exec()
    p.deleteLater()
    return p.result
