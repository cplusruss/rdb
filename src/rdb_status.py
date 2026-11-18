import math
import secrets

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QIcon,
    QPainter,
    QPen,
    QPixmap,
    QPolygonF,
)

import rdb_theme


def _gear():
    pts = []
    for i in range(8):
        a = 2 * math.pi * i / 8
        for r, d in ((4.6, -0.32), (6.8, -0.2), (6.8, 0.2), (4.6, 0.32)):
            pts.append((8 + r * math.cos(a + d), 8 + r * math.sin(a + d)))
    return [("f", pts), ("c", ("d", 8, 8, 2))]


def _star():
    pts = []
    for i in range(10):
        a = math.pi * (i / 5 - 0.5)
        d = 8 if i % 2 == 0 else 3.6
        pts.append((8 + d * math.cos(a), 8.6 + d * math.sin(a)))
    return [("f", pts)]


def _chip():
    out = [("F", 4, 4, 8, 8, 1)]
    for t in (5.6, 8, 10.4):
        out += [
            ("l", t, 1.8, t, 4, 1.5),
            ("l", t, 12, t, 14.2, 1.5),
            ("l", 1.8, t, 4, t, 1.5),
            ("l", 12, t, 14.2, t, 1.5),
        ]
    return out


# the digits: a stroke of 2.6 units (about 1.2 px) so five pixels of height stay open
_SIX = [
    (10.8, 3.6),
    (8.2, 2.6),
    (5.6, 4),
    (4.6, 7.4),
    (4.6, 10.4),
    (5.8, 12.8),
    (8, 13.6),
    (10.4, 12.6),
    (11.4, 10.2),
    (10.4, 8),
    (8, 7.2),
    (5.8, 8),
    (4.6, 9.6),
]
DIGITS = {
    "n1": [("p", [(5.4, 5.2), (8.6, 2.4), (8.6, 13.6)], 2.6)],
    "n2": [
        (
            "p",
            [
                (4.4, 5.4),
                (5.4, 3.4),
                (8, 2.6),
                (10.6, 3.4),
                (11.4, 5.6),
                (10.4, 7.8),
                (4.4, 13.6),
                (11.8, 13.6),
            ],
            2.6,
        )
    ],
    "n3": [
        ("p", [(4.4, 3.2), (11, 3.2), (7.2, 7.4)], 2.6),
        (
            "p",
            [
                (7.2, 7.4),
                (10.4, 7.8),
                (11.6, 10.2),
                (10.8, 12.6),
                (8, 13.6),
                (5.2, 12.8),
                (4.2, 11.6),
            ],
            2.6,
        ),
    ],
    "n4": [("p", [(10.2, 13.6), (10.2, 2.6), (4, 9.6), (12.2, 9.6)], 2.6)],
    "n5": [
        (
            "p",
            [
                (11, 2.8),
                (5.6, 2.8),
                (5, 7.2),
                (8, 6.8),
                (10.6, 7.8),
                (11.6, 10.2),
                (10.6, 12.6),
                (8, 13.6),
                (5.2, 12.8),
                (4.2, 11.6),
            ],
            2.6,
        )
    ],
    "n6": [("p", _SIX, 2.6)],
    "n7": [("p", [(4.2, 3), (11.8, 3), (7.2, 13.6)], 2.6)],
    "n8": [("e", 8, 5.2, 2.5, 2.6), ("e", 8, 10.7, 3.1, 2.6)],
    "n9": [("p", [(16 - x, 16 - y) for x, y in _SIX], 2.6)],
}

# glyphs in a 16-unit box, bold like the marker superstars: solid shapes where they can be, and a
# stroke of W units otherwise. Ops: l line (x1 y1 x2 y2 [w]), p polyline (pts [w]), f filled
# polygon, d filled circle (cx cy r), D filled ellipse (cx cy rx ry), e ring (cx cy r [w]),
# F filled rect (x y w h [radius]), a arc (x y w h start span [w], degrees as Qt), c cut: the op
# inside it drawn in the disc colour
GLYPHS = {
    "pencil": [("f", [(3, 13), (3.6, 10.2), (10.6, 3.2), (12.8, 5.4), (5.8, 12.4)])],
    "ruler": [
        ("f", [(2, 10.5), (10.5, 2), (14, 5.5), (5.5, 14)]),
        ("c", ("l", 4.2, 10, 5.6, 11.4, 1.3)),
        ("c", ("l", 6.4, 7.8, 7.8, 9.2, 1.3)),
        ("c", ("l", 8.6, 5.6, 10, 7, 1.3)),
    ],
    "bulb": [("d", 8, 6.4, 4.6), ("F", 5.8, 11, 4.4, 1.6), ("F", 6.4, 13, 3.2, 1.5)],
    "hourglass": [
        ("F", 3.5, 1.8, 9, 2),
        ("F", 3.5, 12.2, 9, 2),
        ("f", [(4.5, 3.6), (11.5, 3.6), (8, 8)]),
        ("f", [(4.5, 12.4), (11.5, 12.4), (8, 8)]),
    ],
    "clock": [("e", 8, 8, 6.6, 1.4), ("p", [(8, 4.2), (8, 8.4), (11, 10.2)], 2.6)],
    "pause": [("F", 4, 3, 3, 10), ("F", 9, 3, 3, 10)],
    "play": [("f", [(5, 2.8), (13.5, 8), (5, 13.2)])],
    "stop": [("F", 3.5, 3.5, 9, 9, 1)],
    "truck": [
        ("F", 1.4, 3.4, 8.8, 7.6),
        ("f", [(10.8, 5.8), (12.8, 5.8), (14.6, 8.4), (14.6, 11), (10.8, 11)]),
        ("d", 4.6, 12.4, 2),
        ("d", 11.8, 12.4, 2),
    ],
    "box": [
        ("f", [(2, 4.8), (8, 2), (14, 4.8), (14, 11.2), (8, 14), (2, 11.2)]),
        ("c", ("p", [(2.6, 5.1), (8, 7.6), (13.4, 5.1)], 1.4)),
        ("c", ("l", 8, 7.6, 8, 13.4, 1.4)),
    ],
    "check": [("p", [(3.2, 8.4), (6.6, 11.6), (12.8, 4.6)])],
    "checks": [
        ("p", [(1.4, 8.4), (4.2, 11.2), (9.4, 5)], 2.6),
        ("l", 6.8, 11.2, 14.4, 4.2, 2.6),
    ],
    "cross": [("l", 4, 4, 12, 12), ("l", 12, 4, 4, 12)],
    "gear": _gear(),
    "wrench": [
        ("l", 3.4, 12.6, 9, 7, 3.4),
        ("d", 10.6, 5.4, 3.8),
        ("c", ("f", [(10.6, 5.6), (11.6, 0.6), (15.4, 4.4)])),
    ],
    "hammer": [
        ("l", 3, 13, 9, 7, 2.6),
        ("f", [(8.4, 1.9), (14.1, 7.6), (11.6, 10.1), (5.9, 4.4)]),
    ],
    "flask": [
        ("F", 5.6, 1.8, 4.8, 1.8),
        (
            "f",
            [(6.6, 3.4), (9.4, 3.4), (9.4, 6.8), (13.8, 14), (2.2, 14), (6.6, 6.8)],
        ),
    ],
    "search": [("e", 6.8, 6.8, 3.8, 2.6), ("l", 9.8, 9.8, 13.6, 13.6, 3.4)],
    "eye": [
        (
            "f",
            [
                (1.2, 8),
                (4, 4.8),
                (8, 3.6),
                (12, 4.8),
                (14.8, 8),
                (12, 11.2),
                (8, 12.4),
                (4, 11.2),
            ],
        ),
        ("c", ("d", 8, 8, 2.8)),
        ("d", 8, 8, 1.3),
    ],
    "chat": [
        (
            "f",
            [
                (2, 2.6),
                (14, 2.6),
                (14, 10.6),
                (7.6, 10.6),
                (4, 14),
                (4.6, 10.6),
                (2, 10.6),
            ],
        )
    ],
    "mail": [
        ("F", 1.6, 3.6, 12.8, 8.8, 1),
        ("c", ("p", [(2.2, 4.4), (8, 9), (13.8, 4.4)], 1.4)),
    ],
    "phone": [
        ("F", 4.2, 1.6, 7.6, 12.8, 1.6),
        ("c", ("F", 5.8, 3.2, 4.4, 7.6)),
        ("c", ("d", 8, 12.6, 0.9)),
    ],
    "cart": [
        ("p", [(1.2, 3), (3.6, 3), (5.2, 10)], 2.2),
        ("f", [(4.2, 4.6), (14.6, 4.6), (12.8, 10.2), (5.4, 10.2)]),
        ("d", 6, 12.9, 1.6),
        ("d", 11.8, 12.9, 1.6),
    ],
    "dollar": [
        (
            "p",
            [
                (11.4, 4.8),
                (10, 3.5),
                (8, 3.1),
                (6, 3.5),
                (4.8, 5.2),
                (5.6, 7),
                (8, 7.9),
                (10.4, 8.9),
                (11.2, 10.8),
                (10, 12.5),
                (8, 12.9),
                (6, 12.5),
                (4.6, 11.2),
            ],
            2.6,
        ),
        ("l", 8, 1.2, 8, 14.8, 2),
    ],
    "doc": [
        ("f", [(3.2, 1.6), (9.6, 1.6), (12.8, 4.8), (12.8, 14.4), (3.2, 14.4)]),
        ("c", ("f", [(9.2, 1.2), (9.2, 5.2), (13.2, 5.2)])),
        ("c", ("l", 5.4, 8, 10.6, 8, 1.3)),
        ("c", ("l", 5.4, 11, 10.6, 11, 1.3)),
    ],
    "clipboard": [
        ("F", 2.8, 3, 10.4, 11.6, 1.2),
        ("c", ("F", 5, 1.4, 6, 3.8, 1)),
        ("F", 6, 1.4, 4, 2.8, 0.8),
        ("c", ("l", 5.2, 8.4, 10.8, 8.4, 1.3)),
        ("c", ("l", 5.2, 11.4, 10.8, 11.4, 1.3)),
    ],
    "flag": [
        ("l", 3.6, 2, 3.6, 14.4, 2.4),
        ("f", [(4.6, 2.2), (13.6, 2.2), (11, 5.6), (13.6, 9), (4.6, 9)]),
    ],
    "ban": [("e", 8, 8, 5.2, 2.4), ("l", 4.4, 11.6, 11.6, 4.4, 2.4)],
    "lock": [
        ("F", 3, 7.2, 10, 7.2, 1.2),
        ("a", 4.8, 2.2, 6.4, 6.4, 0, 180, 2.2),
        ("l", 4.8, 5.4, 4.8, 7.6, 2.2),
        ("l", 11.2, 5.4, 11.2, 7.6, 2.2),
        ("c", ("d", 8, 10.4, 1.2)),
    ],
    "question": [
        ("a", 4.6, 1.8, 6.8, 6.8, 180, -220),
        ("p", [(10.6, 7.4), (8, 9.1), (8, 10.2)]),
        ("d", 8, 13.4, 1.7),
    ],
    "alert": [("l", 8, 2.8, 8, 9.4, 3.2), ("d", 8, 13, 1.8)],
    "calendar": [
        ("F", 2.2, 3.2, 11.6, 11.2, 1.2),
        ("c", ("l", 2.2, 6.6, 13.8, 6.6, 1.3)),
        *(("c", ("d", x, y, 1)) for y in (9.4, 12) for x in (5.4, 8, 10.6)),
        ("F", 4.4, 1.4, 2, 3.2, 0.6),
        ("F", 9.6, 1.4, 2, 3.2, 0.6),
    ],
    "person": [
        ("d", 8, 4.8, 3.1),
        (
            "f",
            [
                (2.4, 14.4),
                (2.9, 11.4),
                (5, 9.4),
                (8, 8.8),
                (11, 9.4),
                (13.1, 11.4),
                (13.6, 14.4),
            ],
        ),
    ],
    "people": [
        ("d", 11.2, 4.6, 2.4),
        (
            "f",
            [(9.4, 8.4), (11.2, 8), (13.4, 8.6), (14.8, 10.6), (15, 12.8), (11, 12.8)],
        ),
        ("c", ("d", 5.8, 5.4, 3.4)),
        (
            "c",
            (
                "f",
                [
                    (0.4, 14.6),
                    (0.6, 10.4),
                    (3, 8.2),
                    (8.6, 8.2),
                    (11, 10.4),
                    (11.2, 14.6),
                ],
            ),
        ),
        ("d", 5.8, 5.4, 2.6),
        (
            "f",
            [
                (1.2, 14),
                (1.6, 11),
                (3.4, 9.4),
                (5.8, 9),
                (8.2, 9.4),
                (10, 11),
                (10.4, 14),
            ],
        ),
    ],
    "rocket": [
        ("f", [(14, 2), (12.8, 7.2), (7.6, 12.4), (3.6, 8.4), (8.8, 3.2)]),
        ("f", [(5.6, 6.8), (2.2, 6.8), (1.4, 9.8), (4.4, 9.2)]),
        ("f", [(9.2, 10.4), (9.2, 13.8), (6.2, 14.6), (6.8, 11.6)]),
        ("c", ("d", 10, 6, 1.4)),
    ],
    "bug": [
        ("D", 8, 9.4, 4, 4.8),
        ("d", 8, 3.8, 2.3),
        ("c", ("l", 8, 6.4, 8, 13.6, 1.1)),
        ("l", 2.8, 6.6, 4.6, 7.6, 1.5),
        ("l", 13.2, 6.6, 11.4, 7.6, 1.5),
        ("l", 2.4, 10, 4.2, 10, 1.5),
        ("l", 13.6, 10, 11.8, 10, 1.5),
        ("l", 2.8, 13.6, 4.6, 12.4, 1.5),
        ("l", 13.2, 13.6, 11.4, 12.4, 1.5),
    ],
    "chip": _chip(),
    "plane": [
        (
            "f",
            [
                (8, 1.4),
                (9.2, 2.8),
                (9.2, 6.4),
                (14.8, 9.6),
                (14.8, 11.2),
                (9.2, 9.6),
                (9, 12.4),
                (10.8, 13.8),
                (10.8, 14.8),
                (8, 14),
                (5.2, 14.8),
                (5.2, 13.8),
                (7, 12.4),
                (6.8, 9.6),
                (1.2, 11.2),
                (1.2, 9.6),
                (6.8, 6.4),
                (6.8, 2.8),
            ],
        )
    ],
    "house": [
        (
            "f",
            [
                (8, 1.6),
                (14.6, 7.6),
                (12.6, 7.6),
                (12.6, 14.2),
                (3.4, 14.2),
                (3.4, 7.6),
                (1.4, 7.6),
            ],
        ),
        ("c", ("F", 6.6, 9.6, 2.8, 4.6)),
    ],
    "archive": [
        ("F", 1.6, 2.4, 12.8, 3.6, 0.8),
        ("F", 2.6, 7, 10.8, 7.4, 0.8),
        ("c", ("l", 6.2, 9.4, 9.8, 9.4, 1.6)),
    ],
    "up": [("l", 8, 13.8, 8, 6, 3), ("f", [(8, 1.6), (13.4, 7.6), (2.6, 7.6)])],
    "down": [("l", 8, 2.2, 8, 10, 3), ("f", [(8, 14.4), (13.4, 8.4), (2.6, 8.4)])],
    "back": [
        ("l", 5, 5.8, 9.8, 5.8, 2.6),
        ("a", 6.2, 5.8, 7.2, 7.2, 90, -180, 2.6),
        ("l", 9.8, 13, 6, 13, 2.6),
        ("f", [(1.4, 5.8), (5.8, 2), (5.8, 9.6)]),
    ],
    "thumb": [
        ("F", 1.6, 7.2, 3, 7, 0.6),
        (
            "f",
            [
                (5.6, 7.2),
                (8.4, 1.8),
                (9.8, 2.2),
                (10.4, 4.6),
                (9.6, 6.8),
                (13.4, 6.8),
                (14.4, 8),
                (13.2, 13.6),
                (12, 14.2),
                (5.6, 14.2),
            ],
        ),
    ],
    "star": _star(),
    "heart": [
        ("d", 5.4, 5.8, 3.3),
        ("d", 10.6, 5.8, 3.3),
        ("f", [(2.2, 6.8), (8, 14), (13.8, 6.8), (8, 5.6)]),
    ],
    "bolt": [
        ("f", [(10, 1.2), (3, 9.2), (7.4, 9.2), (6, 14.8), (13, 6.6), (8.6, 6.6)])
    ],
    "pin": [
        ("d", 8, 6.2, 4.8),
        ("f", [(3.9, 8.6), (8, 14.8), (12.1, 8.6)]),
        ("c", ("d", 8, 6.2, 1.8)),
    ],
    "link": [
        ("e", 5.4, 10.6, 2.9, 2.1),
        ("e", 10.6, 5.4, 2.9, 2.1),
        ("l", 6.8, 9.2, 9.2, 6.8, 2.4),
    ],
    "cloud": [
        ("d", 5.2, 9.6, 3.4),
        ("d", 8.8, 6.8, 4.2),
        ("d", 12, 10, 3),
        ("F", 5.2, 9.8, 6.8, 3.2),
    ],
    **DIGITS,
}
ICON_KEYS = tuple(GLYPHS)

# one glyph unit in pixels at k=1: the 16-unit box is 8 px inside the 11 px disc. A stroke of W
# units is 1.6 px, the weight of the marker glyphs
UNIT = 0.46
W = 3.5


def _poly(pts):
    return QPolygonF([QPointF(x, y) for x, y in pts])


def _pen(color, w):
    pen = QPen(color, w)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def _op(p, op, a, color):
    if op in _ARGS:
        p.setPen(_pen(color, a[-1] if len(a) > _ARGS[op] else W))
        p.setBrush(Qt.BrushStyle.NoBrush)
    else:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
    if op == "l":
        p.drawLine(QPointF(a[0], a[1]), QPointF(a[2], a[3]))
    elif op == "p":
        p.drawPolyline(_poly(a[0]))
    elif op == "e":
        p.drawEllipse(QPointF(a[0], a[1]), a[2], a[2])
    elif op == "a":
        p.drawArc(QRectF(a[0], a[1], a[2], a[3]), round(a[4] * 16), round(a[5] * 16))
    elif op == "f":
        p.drawPolygon(_poly(a[0]))
    elif op == "d":
        p.drawEllipse(QPointF(a[0], a[1]), a[2], a[2])
    elif op == "D":
        p.drawEllipse(QPointF(a[0], a[1]), a[2], a[3])
    elif op == "F":
        r = a[4] if len(a) > 4 else 0
        p.drawRoundedRect(QRectF(*a[:4]), r, r)
    else:
        raise ValueError(f"glyph op {op!r}")


# the argument count of each stroked op before its optional width
_ARGS = {"l": 4, "p": 1, "e": 3, "a": 6}


def _glyph(p, icon, ink, fill):
    for op, *a in GLYPHS[icon]:
        if op == "c":
            _op(p, a[0][0], a[0][1:], fill)
        else:
            _op(p, op, a, ink)


def new_status(taken):
    # a status as the table and the board add it, with a key none of `taken` has
    while (key := secrets.token_hex(3)) in taken:
        pass
    return {
        "key": key,
        "name": "Status",
        "icon": "check",
        "color": "#5f6368",
        "done": False,
    }


def paint_status(
    p, rect, icon, color, k, faint=False, hot=False, missing=False, ring=None
):
    # a status centred in rect, 11 px at 100%: a disc in the status colour with a drawn glyph,
    # as the marker superstars; an empty slot draws a placeholder ring when faint, darker when
    # hot; a key nobody configures draws a red ring; ring: a 1 px outline in that colour round the
    # disc, for a disc on a fill of its own colour
    cx, cy = rect.center().x(), rect.center().y()
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    if missing:
        p.setPen(QPen(rdb_theme.shade("#d93025"), max(1.4 * k, 1)))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(cx, cy), 4.8 * k, 4.8 * k)
    elif icon:
        fill = rdb_theme.shade(color)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(fill)
        p.drawEllipse(QPointF(cx, cy), 5.5 * k, 5.5 * k)
        if ring is not None:
            p.setPen(QPen(ring, 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QPointF(cx, cy), 5.5 * k - 0.5, 5.5 * k - 0.5)
        p.translate(cx, cy)
        p.scale(UNIT * k, UNIT * k)
        p.translate(-8, -8)
        _glyph(p, icon, rdb_theme.ink_on(fill), fill)
    elif faint:
        # the same greys as the marker placeholder
        grey = ("#e0e0e0", "#8c8c8c") if rdb_theme.dark() else ("#404040", "#808080")
        p.setPen(QPen(QColor(grey[not hot]), max(k, 1)))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(cx, cy), 5 * k, 5 * k)
    p.restore()


def status_icon(icon, color, size):
    # dpr-aware, for menus and the settings table
    dpr = 2
    pm = QPixmap(size * dpr, size * dpr)
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    paint_status(p, QRectF(0, 0, size, size), icon, color, size / 11)
    p.end()
    return QIcon(pm)
