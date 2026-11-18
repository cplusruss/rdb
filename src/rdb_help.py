from PySide6.QtCore import QRect
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

import rdb_keys
import rdb_theme

PREF = "shortcuts/rect"
WIDE = 760
GLYPHS = {
    "Left": "←",
    "Up": "↑",
    "Right": "→",
    "Down": "↓",
    "Arrows": "←↑↓→",
    "PgDown": "PgDn",
}


def compact(chord):
    return chord.lower().replace("+", "").replace(" ", "")


def _label(text, name):
    w = QLabel(text)
    w.setObjectName(name)
    return w


class Row(QWidget):
    def __init__(self, label, chords):
        super().__init__()
        self.name = label.lower()
        self.keys = " ".join(compact(c) for c in chords)
        box = QHBoxLayout(self)
        box.setContentsMargins(0, 3, 0, 3)
        box.setSpacing(3)
        box.addWidget(_label(label, "action"), 1)
        for i, chord in enumerate(chords):
            if i:
                box.addWidget(_label("/", "sep"))
            for g, group in enumerate(chord.split(" ")):
                if g:
                    box.addSpacing(5)
                bits = group.split("+")
                for j, key in enumerate(bits):
                    if j:
                        box.addWidget(_label("+", "sep"))
                    box.addWidget(_label(GLYPHS.get(key, key), "cap"))


class Card(QFrame):
    def __init__(self, section):
        super().__init__()
        self.setObjectName("card")
        self.title = section.title.lower()
        col = QVBoxLayout(self)
        col.setContentsMargins(12, 10, 12, 8)
        col.setSpacing(0)
        col.addWidget(_label(section.title, "title"))
        self.rows = [Row(label, chords) for label, chords in section.rows]
        for r in self.rows:
            col.addWidget(r)

    def narrow(self, q):
        # a title that matches keeps the whole section
        hit = q in self.title
        n = 0
        for r in self.rows:
            on = hit or q in r.name or compact(q) in r.keys
            r.setVisible(on)
            n += on
        return n


class ShortcutGuide(QDialog):
    def __init__(self, parent, prefs):
        super().__init__(parent)
        self.prefs = prefs
        self.setWindowTitle("Keyboard Shortcuts")
        self.resize(900, 700)
        self.setMinimumSize(420, 300)
        self.restore()
        self.filter = QLineEdit()
        self.filter.setClearButtonEnabled(True)
        self.filter.setPlaceholderText("Filter")
        self.filter.textChanged.connect(self.narrow)
        self.cards = [Card(s) for s in rdb_keys.SECTIONS]
        body = QWidget()
        self.cols = QHBoxLayout(body)
        self.cols.setContentsMargins(0, 0, 4, 0)
        self.cols.setSpacing(12)
        self.columns = [QVBoxLayout(), QVBoxLayout()]
        for c in self.columns:
            c.setSpacing(12)
            self.cols.addLayout(c, 1)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setWidget(body)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 8, 12)
        lay.setSpacing(10)
        lay.addWidget(self.filter)
        lay.addWidget(self.scroll, 1)
        self.shown = {id(c): True for c in self.cards}
        self.count = 0
        rdb_theme.follow(self, ShortcutGuide._sheet)
        self.place()

    def _sheet(self):
        win = rdb_theme.win11()
        edge = rdb_theme.text("pane_edge")
        cap = "palette(window)" if win else "palette(base)"
        card = f"border: 1px solid {edge};" + (
            " background: palette(base); border-radius: 8px;" if win else ""
        )
        self.setStyleSheet(
            f"QFrame#card {{ {card} }}"
            f"QLabel#title {{ font-weight: bold; font-size: 10pt;"
            f" color: {rdb_theme.text('pane_active')}; padding: 0 0 5px 0; }}"
            f"QLabel#cap {{ background: {cap}; border: 1px solid {edge};"
            f" border-bottom: 2px solid palette(mid); border-radius: 4px;"
            f" padding: 1px 6px; font-weight: bold; }}"
            "QLabel#sep { color: palette(mid); }"
        )

    def restore(self):
        rect = self.prefs.value(PREF)
        if isinstance(rect, QRect) and any(
            s.availableGeometry().intersects(rect) for s in QApplication.screens()
        ):
            self.setGeometry(rect)

    def hideEvent(self, e):
        if not e.spontaneous():
            self.prefs.setValue(PREF, self.geometry())
        super().hideEvent(e)

    def showEvent(self, e):
        super().showEvent(e)
        self.filter.setFocus()

    def narrow(self, text):
        q = text.strip().lower()
        for c in self.cards:
            self.shown[id(c)] = c.narrow(q) > 0
            c.setVisible(self.shown[id(c)])
        self.place()
        self.scroll.verticalScrollBar().setValue(0)

    def place(self):
        n = 2 if self.width() >= WIDE else 1
        self.count = n
        for col in self.columns:
            while col.count():
                col.takeAt(0)
        live = [c for c in self.cards if self.shown[id(c)]]
        # flow: fill the first column to about half of the visible rows, then the next
        weight = [len(c.rows) + 3 for c in live]
        half, run, at = sum(weight) / n, 0, 0
        for c, w in zip(live, weight):
            if at < n - 1 and run >= half:
                at, run = at + 1, 0
            self.columns[at].addWidget(c)
            run += w
        for c in self.cards:
            if not self.shown[id(c)]:
                self.columns[0].addWidget(c)
        for i, col in enumerate(self.columns):
            col.addStretch(1)
            self.cols.setStretch(i, 1 if i < n else 0)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if (2 if self.width() >= WIDE else 1) != self.count:
            self.place()
