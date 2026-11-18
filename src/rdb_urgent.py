from PySide6.QtCore import QPoint, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QTextLayout, QTextOption
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QWidget,
)

import rdb_theme

PAD, GAP = 8, 8
# bubble: width, corner radius, text inset left (past the accent bar) and right, and top/bottom
BUBBLE_W, RADIUS, TEXT_L, TEXT_R, TEXT_Y = 230, 7, 14, 9, 5
PILL_H = 22


def due(entries, now, eod, at_once):
    # open urgent items made urgent before today, or today once the clock reaches the end of
    # day; one with no recorded day is due at once, and so is every one when at_once
    day, late = now.date().isoformat(), now.strftime("%H:%M") >= eod
    return [
        e
        for e in entries
        if e.kind == "item"
        and e.urgent
        and (at_once or not e.since or e.since < day or (late and e.since == day))
    ]


def item_key(e):
    # text, not block number: block numbers shift with every edit above the item
    return f"{e.pid}\t{e.text}"


def arrange(entries, order):
    # the dragged order first; items never placed keep index order at the end
    rank = {k: i for i, k in enumerate(order)}
    return sorted(entries, key=lambda e: rank.get(item_key(e), len(rank)))


def two_lines(text, font, fm, width):
    # the text word-wrapped to width, cut to two lines with the second elided
    lay = QTextLayout(text, font)
    # a word longer than the bubble breaks rather than running out of it
    opt = QTextOption()
    opt.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    lay.setTextOption(opt)
    lay.beginLayout()
    spans = []
    while (ln := lay.createLine()).isValid():
        ln.setLineWidth(width)
        spans.append((ln.textStart(), ln.textLength()))
    lay.endLayout()
    lines = [text[a : a + n].strip() for a, n in spans]
    if len(lines) > 2:
        rest = text[spans[1][0] :].strip()
        lines = [lines[0], fm.elidedText(rest, Qt.TextElideMode.ElideRight, width)]
    return lines


class Link(QWidget):
    clicked = Signal(object)
    dragged = Signal(object, int)
    dropped = Signal()

    def __init__(self, entry, color):
        super().__init__()
        self.entry, self.color = entry, QColor(color)
        self.press, self.dragging = None, False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        fm = self.fontMetrics()
        self.lines = two_lines(entry.text, self.font(), fm, BUBBLE_W - TEXT_L - TEXT_R)
        # as wide as its longest line, never wider than BUBBLE_W; one height for all
        text_w = max(fm.horizontalAdvance(ln) for ln in self.lines)
        self.setFixedSize(
            min(BUBBLE_W, text_w + TEXT_L + TEXT_R + 1),
            2 * fm.lineSpacing() + 2 * TEXT_Y,
        )

    def enterEvent(self, e):
        self.update()

    def leaveEvent(self, e):
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.press = e.position().toPoint()

    def mouseMoveEvent(self, e):
        if self.press is None:
            return
        far = (e.position().toPoint() - self.press).manhattanLength()
        if not self.dragging and far >= QApplication.startDragDistance():
            self.dragging = True
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.update()
        if self.dragging:
            self.dragged.emit(self, round(e.globalPosition().x()))

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton or self.press is None:
            return
        self.press = None
        if self.dragging:
            self.dragging = False
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.update()
            self.dropped.emit()
        else:
            self.clicked.emit(self.entry)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c, r = self.color, QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        edge, tint = QColor(c), QColor(c)
        edge.setAlpha(110)
        tint.setAlpha(80 if self.dragging else 46 if self.underMouse() else 20)
        p.setPen(QPen(edge, 1))
        p.setBrush(self.palette().base())
        p.drawRoundedRect(r, RADIUS, RADIUS)
        p.setBrush(tint)
        p.drawRoundedRect(r, RADIUS, RADIUS)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawRoundedRect(QRectF(5, 6, 3, r.height() - 11), 1.5, 1.5)
        fm = self.fontMetrics()
        # a single line sits in the middle
        y = (self.height() - len(self.lines) * fm.lineSpacing()) / 2 + fm.ascent()
        p.setPen(c.lighter(190) if rdb_theme.dark() else c.darker(175))
        for line in self.lines:
            p.drawText(TEXT_L, round(y), line)
            y += fm.lineSpacing()


class Strip(QScrollArea):
    # the item texts in one row; the wheel and the thumbwheel both scroll it sideways
    def __init__(self):
        super().__init__()
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.inner = QWidget()
        self.row = QHBoxLayout(self.inner)
        self.row.setContentsMargins(0, 0, 0, 0)
        self.row.setSpacing(GAP)
        self.row.addStretch(1)
        self.setWidget(self.inner)

    def wheelEvent(self, e):
        d = e.angleDelta()
        bar = self.horizontalScrollBar()
        bar.setValue(bar.value() - (d.x() or d.y()))
        e.accept()


class Pill(QLabel):
    clicked = Signal()
    right_clicked = Signal()

    def __init__(self):
        super().__init__()
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        elif e.button() == Qt.MouseButton.RightButton:
            self.right_clicked.emit()


class Banner(QFrame):
    picked = Signal(object)
    add_requested = Signal()
    list_requested = Signal()
    reordered = Signal(list)

    def __init__(self):
        super().__init__()
        self.setObjectName("urgent")
        row = QHBoxLayout(self)
        row.setContentsMargins(PAD, 5, PAD, 5)
        row.setSpacing(GAP)
        self.count = Pill()
        self.count.clicked.connect(self.add_requested)
        self.count.right_clicked.connect(self.list_requested)
        self.count.setObjectName("count")
        self.count.setFixedHeight(PILL_H)
        self.count.setMinimumWidth(PILL_H)
        self.count.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(self.count, 0, Qt.AlignmentFlag.AlignVCenter)
        self.strip = Strip()
        row.addWidget(self.strip, 1)
        self.links, self.color, self.suppressed = [], None, False
        self.hide()

    def minimumSizeHint(self):
        # a long list scrolls, never widens the window
        return QSize(0, super().minimumSizeHint().height())

    def suppress(self, on):
        self.suppressed = on
        self.setVisible(bool(self.links) and not on)

    def show_items(self, entries, color, count=True):
        c = QColor(color)
        # every autosave refreshes; rebuilding unchanged bubbles flickered and reset the scroll
        if c != self.color:
            rgb = f"{c.red()},{c.green()},{c.blue()}"
            self.setStyleSheet(
                f"QFrame#urgent{{background:rgba({rgb},18);border-bottom:1px solid rgba({rgb},90)}}"
                f"QLabel#count{{color:{rdb_theme.ink_for(c).name()};background:{c.name()};"
                f"border-radius:{PILL_H // 2}px;padding:0 7px;font-weight:bold}}"
                "QScrollArea, QScrollArea > QWidget > QWidget{background:transparent}"
            )
        same = c == self.color and [item_key(ln.entry) for ln in self.links] == [
            item_key(e) for e in entries
        ]
        self.color = c
        if same:
            for link, e in zip(self.links, entries):
                link.entry = e
        elif not any(ln.dragging for ln in self.links):
            bar = self.strip.horizontalScrollBar()
            keep = bar.value()
            for link in self.links:
                self.strip.row.removeWidget(link)
                link.deleteLater()
            self.links = [Link(e, c) for e in entries]
            for i, link in enumerate(self.links):
                link.clicked.connect(self.picked)
                link.dragged.connect(self.drag)
                link.dropped.connect(self.drop)
                self.strip.row.insertWidget(i, link)
            self.strip.inner.adjustSize()
            bar.setValue(keep)
        self.strip.setFixedHeight(
            self.links[0].height() if self.links else self.count.sizeHint().height()
        )
        self.count.setText(str(len(entries)))
        self.count.setVisible(count)
        self.setVisible(bool(entries) and not self.suppressed)

    def drag(self, link, gx):
        # the held bubble takes the slot past every other bubble whose middle the cursor passed
        x = self.strip.inner.mapFromGlobal(QPoint(gx, 0)).x()
        others = [ln for ln in self.links if ln is not link]
        i = sum(ln.geometry().center().x() < x for ln in others)
        if self.links.index(link) == i:
            return
        self.links = [*others[:i], link, *others[i:]]
        self.strip.row.removeWidget(link)
        self.strip.row.insertWidget(i, link)
        self.strip.ensureWidgetVisible(link, 0, 0)

    def drop(self):
        self.reordered.emit([item_key(ln.entry) for ln in self.links])
