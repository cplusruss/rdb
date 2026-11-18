import ctypes
import sys
import winreg
from ctypes import wintypes

from PySide6.QtCore import (
    QAbstractNativeEventFilter,
    QEvent,
    QLineF,
    QObject,
    QPoint,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
)
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QWidget,
)

import rdb_theme

BAR_H = 19
MARGIN = 5
FRAME = 4
DB = 16
GWL_STYLE, WS_CAPTION = -16, 0x00C00000
WM_STYLECHANGED, WM_NCCALCSIZE = 0x007D, 0x0083
WM_SETTINGCHANGE = 0x001A
ORANGE = QColor("#e07100")
CREAM = QColor("#ffe9d2")
CLOSE_RED = QColor("#c42b1c")
WIN11_H, NATIVE_BTN = 32, (46, 32)
THEMES = {
    "Classic": {"height": BAR_H, "frame": FRAME, "edge": MARGIN, "gap": 2, "right": 2},
    "Win11": {"height": WIN11_H, "frame": 0, "edge": 4, "gap": 0, "right": 0},
    "Win11 Compact": {"height": BAR_H, "frame": 0, "edge": 4, "gap": 0, "right": 0},
}
ICON_FAMILIES = ("Segoe Fluent Icons", "Segoe MDL2 Assets")
GLYPHS = {"min": "\ue921", "max": "\ue922", "restore": "\ue923", "close": "\ue8bb"}
# the image viewer pin, drawn by CaptionButton: Pin, and Unpin while checked
PIN_GLYPHS = ("\ue718", "\ue77a")
CAPTION_TIPS = {
    "min": "Minimize",
    "max": "Maximize",
    "restore": "Restore Down",
    "close": "Close",
    "pin": "Pin",
}
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWA_SYSTEMBACKDROP_TYPE = 38
WIN11_BUILD, MICA_BUILD = 22000, 22621
FONT = QFont("Tahoma", 8)
E = Qt.Edge
NO_EDGE = Qt.Edge(0)
CURSORS = {
    E.LeftEdge: Qt.CursorShape.SizeHorCursor,
    E.RightEdge: Qt.CursorShape.SizeHorCursor,
    E.TopEdge: Qt.CursorShape.SizeVerCursor,
    E.BottomEdge: Qt.CursorShape.SizeVerCursor,
    E.LeftEdge | E.TopEdge: Qt.CursorShape.SizeFDiagCursor,
    E.RightEdge | E.BottomEdge: Qt.CursorShape.SizeFDiagCursor,
    E.RightEdge | E.TopEdge: Qt.CursorShape.SizeBDiagCursor,
    E.LeftEdge | E.BottomEdge: Qt.CursorShape.SizeBDiagCursor,
}
# window events that change what the bar shows; WindowIconChange never reaches changeEvent,
# so the icon set after construction is picked up on activation
SYNC_EVENTS = (
    QEvent.Type.WindowStateChange,
    QEvent.Type.ActivationChange,
    QEvent.Type.WindowIconChange,
)

GetWindowLongW = ctypes.windll.user32.GetWindowLongW
GetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int)
GetWindowLongW.restype = ctypes.c_long
DwmSetWindowAttribute = ctypes.windll.dwmapi.DwmSetWindowAttribute
DwmSetWindowAttribute.argtypes = (
    wintypes.HWND,
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.DWORD,
)
DwmSetWindowAttribute.restype = ctypes.c_long


def dwm_set(hwnd, attr, value):
    v = ctypes.c_int(value)
    hr = DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), ctypes.sizeof(v))
    if hr:
        raise OSError(f"DwmSetWindowAttribute({attr}) failed: {hr & 0xFFFFFFFF:#x}")


def win_build():
    return sys.getwindowsversion().build


def apps_use_dark():
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except FileNotFoundError:
        # Windows before 1903 has no app mode: it is light
        return False


def icon_family(families):
    for f in ICON_FAMILIES:
        if f in families:
            return f
    raise RuntimeError("Segoe Fluent Icons / Segoe MDL2 Assets not installed")


def caption_size(theme):
    if theme == "Classic":
        return 16, 14
    h = THEMES[theme]["height"]
    return min(NATIVE_BTN[0], round(h * NATIVE_BTN[0] / NATIVE_BTN[1])), h


def native_caption(host):
    # effectiveWinId, unlike winId, never forces the native window into existence
    hwnd = int(host.effectiveWinId())
    return bool(hwnd and GetWindowLongW(hwnd, GWL_STYLE) & WS_CAPTION)


def resize_edges(pos, size, maximized, margin=MARGIN):
    edges = NO_EDGE
    if maximized:
        return edges
    if pos.x() < margin:
        edges |= E.LeftEdge
    elif pos.x() >= size.width() - margin:
        edges |= E.RightEdge
    if pos.y() < margin:
        edges |= E.TopEdge
    elif pos.y() >= size.height() - margin:
        edges |= E.BottomEdge
    return edges


def bevel(p, x, y, w, h, light, dark):
    p.fillRect(x, y, w, 1, light)
    p.fillRect(x, y, 1, h, light)
    p.fillRect(x, y + h - 1, w, 1, dark)
    p.fillRect(x + w - 1, y, 1, h, dark)


def bevel_face(p, w, h, down):
    face, hi, shadow, dark = (
        rdb_theme.color(k) for k in ("bar_face", "bar_hi", "bar_shadow", "bar_dark")
    )
    p.fillRect(0, 0, w, h, face)
    outer, inner = (
        ((dark, hi), (shadow, face)) if down else ((hi, dark), (face, shadow))
    )
    bevel(p, 0, 0, w, h, *outer)
    bevel(p, 1, 1, w - 2, h - 2, *inner)


def draw_db(p, x, y):
    p.save()
    p.translate(x, y)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    body = QPainterPath()
    body.setFillRule(Qt.FillRule.WindingFill)
    body.addEllipse(QRectF(1.5, 0.5, 13, 3))
    body.addRect(QRectF(1.5, 2, 13, 12))
    body.addEllipse(QRectF(1.5, 12.5, 13, 3))
    p.fillPath(body.simplified(), CREAM)
    p.setPen(QPen(ORANGE, 1))
    p.drawLine(QLineF(1.5, 2, 1.5, 14))
    p.drawLine(QLineF(14.5, 2, 14.5, 14))
    p.drawEllipse(QRectF(1.5, 0.5, 13, 3))
    for top in (4.5, 8.5, 12.5):
        p.drawArc(QRectF(1.5, top, 13, 3), 180 * 16, 180 * 16)
    p.restore()


def paint_border(host):
    if host.isMaximized() or native_caption(host) or not host.bar.frame:
        return
    p = QPainter(host)
    w, h = host.width(), host.height()
    face, hi, shadow, dark = (
        rdb_theme.color(k) for k in ("bar_face", "bar_hi", "bar_shadow", "bar_dark")
    )
    p.fillRect(0, 0, w, h, face)
    # the native classic frame: 3D light outside, highlight inside (not qDrawWinPanel's)
    bevel(p, 0, 0, w, h, face, dark)
    bevel(p, 1, 1, w - 2, h - 2, hi, shadow)


class EdgeResizer(QObject):
    def __init__(self, host):
        super().__init__(host)
        self.host, self.edges = host, NO_EDGE
        QApplication.instance().installEventFilter(self)

    def show_cursor(self, edges):
        if edges == self.edges:
            return
        if self.edges:
            QApplication.restoreOverrideCursor()
        self.edges = edges
        if edges:
            QApplication.setOverrideCursor(CURSORS[edges])

    def eventFilter(self, obj, e):
        kind = e.type()
        if kind == QEvent.Type.Leave and obj is self.host:
            self.show_cursor(NO_EDGE)
        elif (
            kind in (QEvent.Type.MouseMove, QEvent.Type.MouseButtonPress)
            and isinstance(obj, QWidget)
            and obj.window() is self.host
        ):
            edges = resize_edges(
                self.host.mapFromGlobal(e.globalPosition().toPoint()),
                self.host.size(),
                self.host.isMaximized() or native_caption(self.host),
                self.host.bar.edge,
            )
            if kind == QEvent.Type.MouseMove:
                self.show_cursor(edges)
            elif edges and e.button() == Qt.MouseButton.LeftButton:
                self.host.windowHandle().startSystemResize(edges)
                return True
        return False


class HoverButton(QPushButton):
    def __init__(self):
        super().__init__()
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def enterEvent(self, e):
        super().enterEvent(e)
        self.update()

    def leaveEvent(self, e):
        super().leaveEvent(e)
        self.update()


class Crumb(HoverButton):
    def event(self, e):
        # before QAbstractButton::event, which swallows it while disabled, as the root is on the map
        if e.type() == QEvent.Type.ContextMenu and self.is_root():
            self.window().show_db_info()
            return True
        return super().event(e)

    def set_last(self, last):
        # only the weight is pinned, so family and size keep following the application font
        f = QFont()
        if last:
            f.setBold(True)
        self.setFont(f)
        self.setEnabled(not last)
        self.updateGeometry()

    # the first crumb the row holds is the root; its text stays "Map" but is never drawn
    def is_root(self):
        return self.parentWidget().findChildren(Crumb)[0] is self

    def sizeHint(self):
        if self.is_root():
            return QSize(DB + 8, DB + 2)
        fm = self.fontMetrics()
        return QSize(fm.horizontalAdvance(self.text()) + 12, fm.height() + 4)

    def paintEvent(self, e):
        p = QPainter(self)
        if self.underMouse() and self.isEnabled():
            bevel(
                p,
                0,
                0,
                self.width(),
                self.height(),
                rdb_theme.color("bar_hi"),
                rdb_theme.color("bar_shadow"),
            )
        if self.is_root():
            draw_db(p, (self.width() - DB) // 2, (self.height() - DB) // 2)
        else:
            p.setPen(self.palette().windowText().color())
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.text())


class Chevron(QLabel):
    def __init__(self):
        super().__init__("›")
        self.setContentsMargins(2, 0, 2, 0)


class Crumbs(QWidget):
    def __init__(self):
        super().__init__()
        self.row = QHBoxLayout(self)
        self.row.setContentsMargins(0, 0, 0, 0)
        self.row.setSpacing(0)

    def minimumSizeHint(self):
        # a long path is clipped, never widens the window
        return QSize(0, super().minimumSizeHint().height())


class AppIcon(QLabel):
    # the File menu's button: a press pops the menu under the icon, as a menu bar's title does
    def __init__(self, menu):
        super().__init__()
        self.menu = menu
        self.setFixedSize(20, BAR_H)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def open_menu(self):
        at = self.mapToGlobal(QPoint(0, self.height()))
        self.menu.popup(rdb_theme.on_screen(self, self.menu.sizeHint(), at))

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.open_menu()


class CaptionButton(HoverButton):
    def __init__(self, kind):
        super().__init__()
        self.kind = kind
        self.theme = "Classic"
        self.setFixedSize(*caption_size("Classic"))

    @property
    def kind(self):
        return self._kind

    @kind.setter
    def kind(self, kind):
        self._kind = kind
        self.setToolTip(CAPTION_TIPS[kind])

    def set_theme(self, theme):
        self.theme = theme
        self.setFixedSize(*caption_size(theme))

    def glyph(self):
        if self.kind == "pin":
            return PIN_GLYPHS[self.isChecked()]
        return GLYPHS[self.kind]

    def state(self):
        if self.isDown() or self.isChecked():
            return "down"
        return "hover" if self.underMouse() else "normal"

    def glyph_color(self):
        bar = self.parentWidget()
        if self.kind == "close" and self.state() != "normal":
            return QColor("#ffffff")
        c = QColor(bar.colors()[1])
        if not bar.active():
            c.setAlpha(140)
        return c

    def paint_win11(self, p):
        bar = self.parentWidget()
        bg, text = bar.colors()
        p.fillRect(self.rect(), bg)
        state = self.state()
        if state != "normal":
            if self.kind == "close":
                c = QColor(CLOSE_RED)
                c.setAlpha(255 if state == "hover" else 178)
            else:
                c = QColor(text)
                c.setAlpha(26 if state == "hover" else 46)
            p.fillRect(self.rect(), c)
        f = QFont(bar.family)
        f.setPixelSize(max(9, round(10 * self.height() / WIN11_H)))
        p.setFont(f)
        p.setPen(self.glyph_color())
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.glyph())

    def paintEvent(self, e):
        p = QPainter(self)
        if self.theme != "Classic":
            self.paint_win11(p)
            return
        down = self.isDown() or self.isChecked()
        bevel_face(p, 16, 14, down)
        x, y = 3 + down, 2 + down

        ink_color = rdb_theme.color("bar_ink")
        face = rdb_theme.color("bar_face")

        def ink(dx, dy, w, h):
            p.fillRect(x + dx, y + dy, w, h, ink_color)

        def box(dx, dy, w, h):
            ink(dx, dy, w, 2)
            ink(dx, dy, 1, h)
            ink(dx + w - 1, dy, 1, h)
            ink(dx, dy + h - 1, w, 1)

        if self.kind == "min":
            ink(1, 7, 6, 2)
        elif self.kind == "pin":
            ink(3, 0, 3, 5)
            ink(1, 5, 7, 1)
            ink(4, 6, 1, 3)
        elif self.kind == "max":
            box(0, 0, 9, 9)
        elif self.kind == "restore":
            box(2, 0, 6, 6)
            p.fillRect(x, y + 3, 6, 6, face)
            box(0, 3, 6, 6)
        else:
            for i in range(7):
                ink(1 + i, 1 + i, 2, 1)
                ink(7 - i, 1 + i, 2, 1)


def colors_changed(msg_id, lparam):
    # WM_SETTINGCHANGE carries the name of the setting that changed; the app mode is this one
    return (
        msg_id == WM_SETTINGCHANGE
        and bool(lparam)
        and ctypes.wstring_at(lparam) == "ImmersiveColorSet"
    )


class StyleWatch(QAbstractNativeEventFilter):
    def __init__(self, bar):
        super().__init__()
        self.bar = bar
        app = QApplication.instance()
        app.installNativeEventFilter(self)
        bar.destroyed.connect(lambda: app.removeNativeEventFilter(self))

    def nativeEventFilter(self, event_type, message):
        if bytes(event_type) == b"windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message in (WM_STYLECHANGED, WM_NCCALCSIZE) and msg.hWnd == int(
                self.bar.host.effectiveWinId()
            ):
                QTimer.singleShot(0, self.bar.sync)
            elif colors_changed(msg.message, msg.lParam):
                QTimer.singleShot(0, self.bar.system_changed.emit)
        return False, 0


class TitleBar(QWidget):
    system_changed = Signal()

    def __init__(self, host, menu, crumbs):
        super().__init__(host)
        self.host = host
        self.theme, self.frame, self.edge = "Classic", FRAME, MARGIN
        self.family, self.dark, self._dwm, self._ink = None, False, None, None
        self.setFixedHeight(BAR_H)
        self.icon = AppIcon(menu)
        menu.setFont(FONT)
        self.caption = {k: CaptionButton(k) for k in ("min", "max", "close")}
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 2, 0)
        row.setSpacing(0)
        row.addWidget(self.icon)
        # imported here: rdb_editor imports this module while it is itself half loaded
        from rdb_titlebuttons import ButtonStrip

        self.strip = ButtonStrip(BAR_H)
        row.addWidget(crumbs, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addStretch(1)
        row.addWidget(self.strip, 0, Qt.AlignmentFlag.AlignVCenter)
        for k, b in self.caption.items():
            if k == "close":
                row.addSpacing(2)
            row.addWidget(b, 0, Qt.AlignmentFlag.AlignVCenter)
        self.caption["min"].clicked.connect(host.showMinimized)
        self.caption["max"].clicked.connect(self.toggle_maximized)
        self.caption["close"].clicked.connect(host.close)
        self.watch = StyleWatch(self)
        self.sync()

    def active(self):
        return self.host.isActiveWindow()

    def colors(self):
        if self.theme == "Classic":
            return rdb_theme.color("bar_face"), rdb_theme.color("bar_ink")
        on, off, text = (rdb_theme.color(k) for k in ("bar_on", "bar_off", "bar_text"))
        return (on if self.active() else off), text

    def set_theme(self, name):
        t = THEMES[name]
        if name == self.theme:
            return
        family = None if name == "Classic" else icon_family(QFontDatabase.families())
        self.theme, self.frame, self.edge, self.family = (
            name,
            t["frame"],
            t["edge"],
            family,
        )
        self.setFixedHeight(t["height"])
        self.icon.setFixedSize(20, t["height"])
        for b in self.caption.values():
            b.set_theme(name)
        row = self.layout()
        row.setContentsMargins(0, 0, t["right"], 0)
        gap = row.itemAt(row.indexOf(self.caption["close"]) - 1).spacerItem()
        gap.changeSize(t["gap"], 0, QSizePolicy.Policy.Fixed)
        row.invalidate()
        self.sync()

    def sync_look(self):
        win11 = self.theme != "Classic"
        self.dark = rdb_theme.dark()
        ink = self.colors()[1]
        if ink != self._ink:
            self._ink = ink
            pal = self.palette()
            pal.setColor(QPalette.ColorRole.WindowText, ink)
            self.setPalette(pal)
        hwnd = int(self.host.effectiveWinId())
        build = win_build()
        state = (hwnd, win11, self.dark)
        if not hwnd or build < WIN11_BUILD or state == self._dwm:
            return
        if self._dwm is None and not win11 and not self.dark:
            return
        dwm_set(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, 2 if win11 else 0)
        if build >= MICA_BUILD:
            dwm_set(hwnd, DWMWA_SYSTEMBACKDROP_TYPE, 2 if win11 else 0)
        dwm_set(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, int(state[2]))
        self._dwm = state

    def toggle_maximized(self):
        if self.host.isMaximized():
            self.host.showNormal()
        else:
            self.host.showMaximized()

    def set_tiles_side(self, side):
        # right: crumbs, stretch, strip, caption buttons; left: the strip right after the icon
        row = self.layout()
        row.removeWidget(self.strip)
        at = 1 if side == "left" else row.indexOf(self.caption["min"])
        row.insertWidget(at, self.strip, 0, Qt.AlignmentFlag.AlignVCenter)

    def open_file_menu(self):
        self.icon.open_menu()

    def showEvent(self, e):
        super().showEvent(e)
        self.sync()

    def sync(self):
        h = self.host
        native = native_caption(h)
        h.setContentsMargins(*[0 if native or h.isMaximized() else self.frame] * 4)
        # the icon stays under a system caption: it is the File menu's button
        for w in self.caption.values():
            w.setVisible(not native)
        self.icon.setPixmap(h.windowIcon().pixmap(16, 16))
        self.caption["max"].kind = "restore" if h.isMaximized() else "max"
        self.sync_look()
        for w in self.caption.values():
            w.update()
        self.update()

    def paintEvent(self, e):
        QPainter(self).fillRect(self.rect(), self.colors()[0])

    def contextMenuEvent(self, e):
        if self.childAt(e.pos()) in self.caption.values():
            e.ignore()
            return
        self.strip.menu().exec(e.globalPos())

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.host.windowHandle().startSystemMove()

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.toggle_maximized()
