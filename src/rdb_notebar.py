from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QTextCharFormat,
    QTextCursor,
    QTextFormat,
    QTextListFormat,
)
from PySide6.QtWidgets import (
    QComboBox,
    QFontComboBox,
    QFrame,
    QHBoxLayout,
)

import rdb_color
import rdb_findbar
import rdb_model as m
import rdb_syntax

SIZES = ("8", "9", "10", "10.5", "11", "12", "14", "16", "18", "20", "24", "28", "36")


class NoteBar(QFrame):
    # rdoc's Document page toolbar, cut to what a text page takes
    wrap_toggled = Signal(bool)
    lang_changed = Signal(str)

    def __init__(self, editor):
        super().__init__()
        self.ed = editor
        self._syncing = False
        self.font_box = QFontComboBox()
        self.font_box.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.size_box = QComboBox()
        self.size_box.setEditable(True)
        self.size_box.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.size_box.addItems(SIZES)
        self.size_box.setFixedWidth(54)
        self.bold = rdb_findbar.glyph_button("docBold", self)
        self.italic = rdb_findbar.glyph_button("docItalic", self)
        self.underline = rdb_findbar.glyph_button("docUnderline", self)
        self.color = rdb_findbar.glyph_button("docColor", self, checkable=False)
        self.bullets = rdb_findbar.glyph_button("docBullets", self)
        self.numbers = rdb_findbar.glyph_button("docNumbers", self)
        self.lang_box = QComboBox()
        self.lang_box.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.lang_box.addItems(("Auto", rdb_syntax.PLAIN, *rdb_syntax.LANGS))
        self.wrap = rdb_findbar.glyph_button("wrap", self)
        row = QHBoxLayout(self)
        row.setContentsMargins(2, 1, 2, 1)
        row.setSpacing(2)
        for w in (
            self.font_box,
            self.size_box,
            self.bold,
            self.italic,
            self.underline,
            self.color,
            self.bullets,
            self.numbers,
        ):
            row.addWidget(w)
        row.addStretch(1)
        row.addWidget(self.lang_box)
        row.addWidget(self.wrap)
        self.font_box.activated.connect(
            lambda _i: self.set_family(self.font_box.currentText())
        )
        self.size_box.activated.connect(lambda _i: self._size())
        self.size_box.lineEdit().returnPressed.connect(self._size)
        self.bold.clicked.connect(self.toggle_bold)
        self.italic.clicked.connect(lambda: self._toggle("italic"))
        self.underline.clicked.connect(lambda: self._toggle("underline"))
        self.color.clicked.connect(self._pick_color)
        self.bullets.clicked.connect(lambda: self.toggle_list(False))
        self.numbers.clicked.connect(lambda: self.toggle_list(True))
        self.wrap.clicked.connect(self.wrap_toggled)
        self.lang_box.activated.connect(self._lang)
        editor.cursorPositionChanged.connect(self.sync)
        editor.currentCharFormatChanged.connect(self.sync)
        self.hide()

    def _merge(self, fmt):
        self.ed.mergeCurrentCharFormat(fmt)
        self.ed.setFocus()

    def _toggle(self, what):
        f = QTextCharFormat()
        if what == "italic":
            f.setFontItalic(not self.ed.currentCharFormat().fontItalic())
        else:
            f.setFontUnderline(not self.ed.currentCharFormat().fontUnderline())
        self._merge(f)

    def toggle_bold(self):
        f = QTextCharFormat()
        f.setFontWeight(
            QFont.Weight.Normal
            if self.ed.currentCharFormat().fontWeight() >= QFont.Weight.Bold
            else QFont.Weight.Bold
        )
        self._merge(f)

    def set_family(self, family):
        f = QTextCharFormat()
        f.setFontFamilies([family])
        self._merge(f)

    def set_size(self, pt):
        f = QTextCharFormat()
        f.setFontPointSize(pt)
        self._merge(f)

    def set_color(self, color):
        f = QTextCharFormat()
        m.set_char_colour(f, "fg", color)
        self._merge(f)

    def _size(self):
        # Return in the box fires both signals
        pt = float(self.size_box.currentText())
        if self.ed.currentCharFormat().fontPointSize() != pt:
            self.set_size(pt)

    def _pick_color(self):
        cur = self.ed.currentCharFormat()
        now = QColor(cur.stringProperty(m.ORIG_FG) or cur.foreground().color())
        c = rdb_color.get_color(self.window(), now)
        if c.isValid():
            self.set_color(c)

    def _blocks(self):
        c = self.ed.textCursor()
        doc = self.ed.document()
        first = doc.findBlock(c.selectionStart())
        last = doc.findBlock(c.selectionEnd())
        out = []
        while first.isValid():
            out.append(first)
            if first == last:
                break
            first = first.next()
        return out

    def toggle_list(self, numbered):
        blocks = self._blocks()

        def kind(b):
            lst = b.textList()
            return None if lst is None else lst.format().style() not in m.BULLETS

        c = self.ed.textCursor()
        c.beginEditBlock()
        if all(kind(b) is numbered for b in blocks):
            for b in blocks:
                b.textList().remove(b)
                bf = b.blockFormat()
                bf.setIndent(m.gutter_steps(b))
                QTextCursor(b).setBlockFormat(bf)
        else:
            style = QTextListFormat.Style.ListDecimal if numbered else m.bullet_for(1)
            for b in blocks:
                if kind(b) is not numbered:
                    m.set_list_level(QTextCursor(b), 1, style)
        c.endEditBlock()
        self.ed.setFocus()
        self.sync()

    def sync(self, *_):
        if self._syncing:
            return
        self._syncing = True
        try:
            ed = self.ed
            cf = ed.currentCharFormat()
            base = ed.document().defaultFont()
            # fontFamilies() of a format that sets none crashes PySide
            named = cf.hasProperty(QTextFormat.Property.FontFamilies)
            self.font_box.setCurrentText(cf.font().family() if named else base.family())
            self.size_box.setCurrentText(f"{cf.fontPointSize() or base.pointSizeF():g}")
            self.bold.setChecked(cf.fontWeight() >= QFont.Weight.Bold)
            self.italic.setChecked(cf.fontItalic())
            self.underline.setChecked(cf.fontUnderline())
            lst = ed.textCursor().block().textList()
            numbered = lst is not None and lst.format().style() not in m.BULLETS
            self.bullets.setChecked(lst is not None and not numbered)
            self.numbers.setChecked(numbered)
        finally:
            self._syncing = False

    def _lang(self, i):
        self.lang_changed.emit("" if i == 0 else self.lang_box.itemText(i))
        self.ed.setFocus()

    def sync_lang(self, lang):
        self.lang_box.setCurrentText(lang or "Auto")

    def sync_wrap(self, on):
        self.wrap.setChecked(on)
