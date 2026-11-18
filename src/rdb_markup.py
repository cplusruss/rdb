from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen

# kind -> (colour, width as a multiple of the base pen, alpha)
PENS = {
    "red": ("#e02020", 1.0, 255),
    "green": ("#1fa83a", 1.0, 255),
    "blue": ("#2050e0", 1.0, 255),
    "yellow": ("#ffe000", 3.5, 110),
}


def pen_width(img_w, img_h, kind):
    # image pixels: a stroke keeps its look whatever size the picture is drawn at
    return round(max(2.0, max(img_w, img_h) / 250) * PENS[kind][1], 2)


def paint_strokes(p, strokes):
    # p already maps image pixels to the target
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    for s in strokes:
        colour, _, alpha = PENS[s["k"]]
        c = QColor(colour)
        c.setAlpha(alpha)
        pen = QPen(c, s["w"])
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        pts = [QPointF(x, y) for x, y in s["p"]]
        if len(pts) == 1:
            p.drawPoint(pts[0])
            continue
        path = QPainterPath(pts[0])
        for pt in pts[1:]:
            path.lineTo(pt)
        p.drawPath(path)


def marked(img, strokes):
    # a copy of the picture with the strokes on it; the picture itself when there are none
    if not strokes:
        return img
    out = img.convertToFormat(QImage.Format.Format_ARGB32)
    out.setDevicePixelRatio(img.devicePixelRatio())
    p = QPainter(out)
    p.scale(1 / img.devicePixelRatio(), 1 / img.devicePixelRatio())
    paint_strokes(p, strokes)
    p.end()
    return out


def marked_bytes(store, name):
    # the stored bytes, or a PNG of the picture with its markup when it has any
    strokes = store.get_markup(name)
    data = store.get_image(name)
    if not strokes:
        return data
    img = QImage.fromData(data)
    if img.isNull():
        raise ValueError(f"image {name} cannot be decoded")
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    if not marked(img, strokes).save(buf, "PNG"):
        raise OSError(f"cannot encode image {name}")
    return bytes(ba)
