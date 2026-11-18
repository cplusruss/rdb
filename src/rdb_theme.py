import weakref

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QGuiApplication, QPalette, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QApplication,
    QFrame,
    QGraphicsView,
    QMenu,
    QProxyStyle,
    QScrollBar,
    QStyle,
)
from shiboken6 import isValid


class _Tips(QProxyStyle):
    # every theme's style with tooltips shown at once
    def styleHint(self, hint, opt=None, widget=None, ret=None):
        if hint == QStyle.StyleHint.SH_ToolTip_WakeUpDelay:
            return 0
        return super().styleHint(hint, opt, widget, ret)

    def name(self):
        return self.baseStyle().name()


NAMES = ("Classic", "Win11", "Win11 Compact")


def accent():
    return QGuiApplication.palette().color(QPalette.ColorRole.Accent)


def _accent_alpha(a):
    def make():
        c = accent()
        c.setAlpha(a)
        return c

    return make


def _accent_light():
    return accent().lighter(170)


# Classic carries the literals the app has always drawn; Win11 follows Windows 11 light.
# Document and card colours, the map canvas and user-chosen colours are content and live elsewhere.
CLASSIC = {
    "style": "Windows",
    "scheme": Qt.ColorScheme.Unknown,
    "radius": 0,
    "grid_line": "black",
    "strip_rule": "#d0d0d0",
    "dump_edge": "#808080",
    "ring": "#0078d7",
    "pane_edge": "palette(mid)",
    "pane_active": "palette(highlight)",
    "swatch_btn_edge": "#404040",
    "swatch_edge": "#999999",
    "head_bg": "#f4f4f4",
    "head_rule": "#b8b8b8",
    "row_now": (0, 120, 215, 22),
    "hot_line": "#0078d7",
    "on_line": "#0078d7",
    "sel_color": "#000000",
    "sel_style": Qt.PenStyle.DotLine,
    "hot_fill": "#e5f1fb",
    "on_fill_quick": "#cce4f7",
    "on_fill_sort": "#c8e2fc",
    "range_bg": "#9ec5ee",
    "badge_fill": "#e6e6e6",
    "badge_edge": "#8a8a8a",
    "popup_frame": True,
    "bar_face": "#d4d0c8",
    "bar_hi": "#ffffff",
    "bar_shadow": "#808080",
    "bar_dark": "#404040",
    "bar_ink": "#000000",
    "bar_on": "#f3f3f3",
    "bar_off": "#fbfbfb",
    "bar_text": "#000000",
    "card_ink": "#1e1e1e",
    "snap_bg": "#c8c8c8",
    "snap_ink": "#555555",
}
WIN11 = {
    "style": "windows11",
    "scheme": Qt.ColorScheme.Light,
    "radius": 4,
    "grid_line": "#e5e5e5",
    "strip_rule": "#e5e5e5",
    "dump_edge": "#a0a0a0",
    "ring": accent,
    "pane_edge": "#e5e5e5",
    "pane_active": lambda: accent().name(),
    "swatch_btn_edge": "#8a8a8a",
    "swatch_edge": "#8a8a8a",
    "head_bg": "#f9f9f9",
    "head_rule": "#e5e5e5",
    "row_now": _accent_alpha(22),
    "hot_line": "#e5e5e5",
    "on_line": accent,
    "sel_color": accent,
    "sel_style": Qt.PenStyle.SolidLine,
    "hot_fill": "#f3f3f3",
    "on_fill_quick": _accent_alpha(48),
    "on_fill_sort": _accent_alpha(48),
    "range_bg": _accent_light,
    "badge_fill": "#f3f3f3",
    "badge_edge": "#d1d1d1",
    "popup_frame": False,
    "bar_face": "#d4d0c8",
    "bar_hi": "#ffffff",
    "bar_shadow": "#808080",
    "bar_dark": "#404040",
    "bar_ink": "#000000",
    "bar_on": "#f3f3f3",
    "bar_off": "#fbfbfb",
    "bar_text": "#000000",
    "card_ink": "#1e1e1e",
    "snap_bg": "#c8c8c8",
    "snap_ink": "#555555",
}
TOKENS = {"Classic": CLASSIC, "Win11": WIN11, "Win11 Compact": WIN11}


def _accent_dark():
    return accent().darker(170)


# the same keys for dark: Windows 11 dark greys, the editor's #1c1c1c at the bottom
_BAR_DARK = {
    "bar_face": "#2b2b2b",
    "bar_hi": "#5e5e5e",
    "bar_shadow": "#1a1a1a",
    "bar_dark": "#0a0a0a",
    "bar_ink": "#e6e6e6",
    "bar_on": "#202020",
    "bar_off": "#2b2b2b",
    "bar_text": "#ffffff",
    "card_ink": "#e8e8e8",
    "snap_bg": "#3a3a3a",
    "snap_ink": "#a0a0a0",
}
CLASSIC_DARK = {
    **CLASSIC,
    **_BAR_DARK,
    "scheme": Qt.ColorScheme.Dark,
    "grid_line": "#404040",
    "strip_rule": "#404040",
    "dump_edge": "#6a6a6a",
    "ring": "#4cc2ff",
    "swatch_btn_edge": "#9a9a9a",
    "swatch_edge": "#808080",
    "head_bg": "#2b2b2b",
    "head_rule": "#4a4a4a",
    "row_now": (76, 194, 255, 28),
    "hot_line": "#4cc2ff",
    "on_line": "#4cc2ff",
    "sel_color": "#dedede",
    "hot_fill": "#2a3a4a",
    "on_fill_quick": "#1f4a73",
    "on_fill_sort": "#1f4a73",
    "range_bg": "#2f5d8a",
    "badge_fill": "#333333",
    "badge_edge": "#6a6a6a",
}
WIN11_DARK = {
    **WIN11,
    **_BAR_DARK,
    "scheme": Qt.ColorScheme.Dark,
    "grid_line": "#383838",
    "strip_rule": "#383838",
    "dump_edge": "#6a6a6a",
    "pane_edge": "#3a3a3a",
    "swatch_btn_edge": "#9a9a9a",
    "swatch_edge": "#808080",
    "head_bg": "#2b2b2b",
    "head_rule": "#3a3a3a",
    "row_now": _accent_alpha(40),
    "hot_line": "#3a3a3a",
    "hot_fill": "#2f2f2f",
    "on_fill_quick": _accent_alpha(80),
    "on_fill_sort": _accent_alpha(80),
    "range_bg": _accent_dark,
    "badge_fill": "#2f2f2f",
    "badge_edge": "#4a4a4a",
}
TOKENS_DARK = {
    "Classic": CLASSIC_DARK,
    "Win11": WIN11_DARK,
    "Win11 Compact": WIN11_DARK,
}


_STYLE_PALETTE = (QAbstractButton, QScrollBar, QMenu, QGraphicsView)
_name = "Classic"
_followers = []
_dark = False
# (theme, dark) as last applied, and whether the application palette is the dark one
_applied = None
_pal_dark = False

# the OKLab lightness range a colour is mirrored into in dark mode: white lands on the editor
# background (#1c1c1c), black on the body text (#dedede)
DARK_L = (0.2264, 0.9006)


def current():
    return _name


def dark():
    return _dark


def set_dark(on):
    global _dark
    _dark = bool(on)


def _lin(v):
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def _gam(v):
    v = 12.92 * v if v <= 0.0031308 else 1.055 * max(v, 0) ** (1 / 2.4) - 0.055
    return min(1.0, max(0.0, v))


def to_lab(c):
    r, g, b = (_lin(v) for v in (c.redF(), c.greenF(), c.blueF()))
    lm = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    return (
        0.2104542553 * lm + 0.7936177850 * m - 0.0040720468 * s,
        1.9779984951 * lm - 2.4285922050 * m + 0.4505937099 * s,
        0.0259040371 * lm + 0.7827717662 * m - 0.8086757660 * s,
    )


def _rgb(L, a, b):
    lm = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3
    return (
        4.0767416621 * lm - 3.3077115913 * m + 0.2309699292 * s,
        -1.2684380046 * lm + 2.6097574011 * m - 0.3413193965 * s,
        -0.0041960863 * lm - 0.7034186147 * m + 1.7076147010 * s,
    )


def from_lab(L, a, b, alpha=255):
    # out-of-gamut colours give up chroma, never lightness, so the mirrored contrast holds
    k = 1.0
    for _ in range(40):
        lin = _rgb(L, a * k, b * k)
        if all(-1e-4 <= v <= 1 + 1e-4 for v in lin):
            break
        k *= 0.9
    c = QColor.fromRgbF(*(_gam(v) for v in lin))
    c.setAlpha(alpha)
    return c


def _valid(c):
    q = QColor(c)
    if not q.isValid():
        raise ValueError(f"not a colour: {c!r}")
    return q


def mirror(c):
    c = _valid(c)
    L, a, b = to_lab(c)
    lo, hi = DARK_L
    return from_lab(hi - L * (hi - lo), a, b, c.alpha())


def unmirror(c):
    c = _valid(c)
    L, a, b = to_lab(c)
    lo, hi = DARK_L
    return from_lab((hi - L) / (hi - lo), a, b, c.alpha())


def shade(c):
    # a colour chosen on white, as it is drawn now: itself in light mode, mirrored in dark
    return mirror(c) if _dark else QColor(c)


def unshade(c):
    return unmirror(c) if _dark else QColor(c)


def ink_on(fill, light="#ffffff"):
    # the mark ink on a fill: as written in light mode, black or white by the fill in dark
    if not _dark:
        return QColor(light)
    c = QColor(fill)
    luma = 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()
    return shade("#ffffff" if luma > 140 else "#000000")


def ink_for(bg):
    # the text colour, black or white, with the better contrast on this background
    c = QColor(bg)
    lum = 0.2126 * _lin(c.redF()) + 0.7152 * _lin(c.greenF()) + 0.0722 * _lin(c.blueF())
    return (
        QColor("#000000")
        if (lum + 0.05) / 0.05 >= 1.05 / (lum + 0.05)
        else QColor("#ffffff")
    )


def win11():
    return _name != "Classic"


def raw(key):
    v = (TOKENS_DARK if _dark else TOKENS)[_name][key]
    return v() if callable(v) else v


def color(key):
    v = raw(key)
    return QColor(*v) if isinstance(v, tuple) else QColor(v)


def text(key):
    v = raw(key)
    return v.name() if isinstance(v, QColor) else v


def set_scheme(scheme):
    QGuiApplication.styleHints().setColorScheme(scheme)


def dark_palette():
    # every role, the Disabled group included; Accent stays what the platform gives
    p = QPalette(QApplication.palette())
    R, G = QPalette.ColorRole, QPalette.ColorGroup
    for role, c in (
        (R.Window, "#202020"),
        (R.WindowText, "#dedede"),
        (R.Base, "#1c1c1c"),
        (R.AlternateBase, "#252525"),
        (R.ToolTipBase, "#2b2b2b"),
        (R.ToolTipText, "#dedede"),
        (R.PlaceholderText, "#8a8a8a"),
        (R.Text, "#dedede"),
        (R.Button, "#2b2b2b"),
        (R.ButtonText, "#dedede"),
        (R.BrightText, "#ffffff"),
        (R.Light, "#4a4a4a"),
        (R.Midlight, "#3a3a3a"),
        (R.Dark, "#141414"),
        (R.Mid, "#5a5a5a"),
        (R.Shadow, "#000000"),
        (R.Highlight, "#2f5d9e"),
        (R.HighlightedText, "#ffffff"),
        (R.Link, "#4ea1ff"),
        (R.LinkVisited, "#c58af9"),
    ):
        p.setColor(role, QColor(c))
    for role in (R.WindowText, R.Text, R.ButtonText, R.PlaceholderText):
        p.setColor(
            G.Disabled,
            role,
            QColor("#80808a" if role == R.PlaceholderText else "#7a7a7a"),
        )
    p.setColor(G.Disabled, R.Highlight, QColor("#444444"))
    p.setColor(G.Disabled, R.HighlightedText, QColor("#9a9a9a"))
    return p


def apply(name, dark=False):
    global _name, _applied, _pal_dark
    if name not in TOKENS:
        raise ValueError(f"unknown theme {name!r}")
    app = QApplication.instance()
    want = TOKENS[name]["style"]
    styled = isinstance(app.style(), _Tips)
    if (
        (name, bool(dark)) == _applied
        and styled
        and app.style().name().lower() == want.lower()
    ):
        return
    _applied = (name, bool(dark))
    leaving = app.style().name().lower() == "windows11" != want.lower()
    _name = name
    set_dark(dark)
    set_scheme(raw("scheme"))
    if not styled or app.style().name().lower() != want.lower():
        app.setStyle(_Tips(want))
    # an explicit palette survives a style change; light goes back to the platform's
    if dark:
        app.setPalette(dark_palette())
    elif _pal_dark:
        app.setPalette(QPalette())
    _pal_dark = bool(dark)
    for ref, fn in list(_followers):
        w = ref()
        if w is not None and isValid(w):
            fn(w)
    # after the followers: a style sheet change restores the palette it saved
    scrub(leaving)


def scrub(leaving):
    # A widget keeps the palette it was polished with: a style sheet restores the one it saved
    # and Qt 6.8's windows11 style leaves its own, with paint attributes, when it unpolishes.
    # Every widget is set back to the palette its parents and the application give it; the
    # windows11 style then polishes its own widgets again against the palette now in force.
    A = Qt.WidgetAttribute
    app = QApplication.instance()
    style = app.style()
    w11 = style.name().lower() == "windows11"
    widgets = QApplication.allWidgets()
    # every palette first, so that no widget is polished against a parent's stale one
    for w in widgets:
        own = isinstance(w, _STYLE_PALETTE)
        if own and w11:
            style.unpolish(w)
        if own or not w.testAttribute(A.WA_SetPalette):
            w.setPalette(QPalette())
    for w in widgets:
        if isinstance(w, _STYLE_PALETTE) and w11:
            style.polish(w)
        if not leaving:
            continue
        if isinstance(w, QScrollBar):
            w.setAttribute(A.WA_OpaquePaintEvent, True)
        if isinstance(w, (QScrollBar, QMenu)):
            w.setAttribute(A.WA_NoSystemBackground, False)
            w.setAttribute(A.WA_TranslucentBackground, False)
        if isinstance(w, QAbstractItemView):
            w.setAttribute(A.WA_Hover, False)


def follow(widget, fn, now=True):
    # fn(widget) runs now and again on every theme change, for as long as the widget lives
    _followers[:] = [f for f in _followers if f[0]() is not None]
    _followers.append((weakref.ref(widget), fn))
    if now:
        fn(widget)


def radius_text():
    r = raw("radius")
    return f" border-radius: {r}px;" if r else ""


def error(color, selector=None):
    body = f"border: 1px solid {color};{radius_text()}"
    return body if selector is None else f"{selector} {{ {body} }}"


def pane_sheet(widget):
    edge, on, r = text("pane_edge"), text("pane_active"), radius_text()
    widget.setStyleSheet(
        f"QListView, QTreeWidget {{ border: 1px solid {edge};{r} }}"
        f"QListView[active=true], QTreeWidget[active=true] {{ border: 1px solid {on};{r} }}"
        f"QTextEdit[current=false] {{ border: 1px solid {edge};{r} }}"
        f"QTextEdit[current=true] {{ border: 1px solid {on};{r} }}"
    )


def list_sheet(widget):
    widget.setStyleSheet(
        f"QListWidget, QGraphicsView {{ border: 1px solid {text('pane_edge')};{radius_text()} }}"
    )


def grid_sheet(table):
    table.setStyleSheet(
        f"QTableView {{ gridline-color: {text('grid_line')}; }} "
        "QTableView::item { padding: 0px; }"
    )


def strip_sheet(header, bg):
    header.setStyleSheet(
        f"#header {{ background: {bg}; border-bottom: 1px solid {text('strip_rule')}; }}"
    )


def edge_sheet(widget):
    widget.setStyleSheet(
        f"{type(widget).__name__} {{ border: 1px solid {text('dump_edge')}; }}"
    )


def popup_frame(frame):
    # the classic box, or a flat stroke around the Windows 11 popup
    if raw("popup_frame"):
        if frame.styleSheet():
            frame.setStyleSheet("")
        frame.setFrameShape(QFrame.Shape.Box)
    else:
        frame.setFrameShape(QFrame.Shape.NoFrame)
        edge_sheet(frame)


def ring_sheet(frame):
    frame.setStyleSheet(
        f"border: 2px solid {text('ring')};{radius_text()} background: transparent;"
    )


def selection_pen():
    return QPen(color("sel_color"), 0, raw("sel_style"))


def on_screen(owner, size, at):
    # at, moved just enough that a popup of this size stays whole on the owner window's screen
    geo = owner.window().screen().availableGeometry()
    x = max(geo.left(), min(at.x(), geo.right() - size.width() + 1))
    y = max(geo.top(), min(at.y(), geo.bottom() - size.height() + 1))
    return QPoint(x, y)
