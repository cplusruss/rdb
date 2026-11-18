import bisect
import json
import re
import xml.etree.ElementTree as ET

from pygments.lexers import (
    CLexer,
    CppLexer,
    IniLexer,
    JsonLexer,
    PythonLexer,
    SystemVerilogLexer,
    VerilogLexer,
    VhdlLexer,
    XmlLexer,
    find_lexer_class,
    guess_lexer,
)
from pygments.token import Comment, Keyword, Name, Number, String
from pygments.util import ClassNotFound
from PySide6.QtCore import QTimer
from PySide6.QtGui import (
    QColor,
    QFont,
    QTextBlockUserData,
    QTextCharFormat,
    QTextFormat,
)
from rdb_editor import ItemHighlighter, doc_zoom
import rdb_theme
from rdb_model import shown, tag_runs

LEXERS = {
    "Python": PythonLexer,
    "C": CLexer,
    "C++": CppLexer,
    "VHDL": VhdlLexer,
    "Verilog": VerilogLexer,
    "SystemVerilog": SystemVerilogLexer,
    "JSON": JsonLexer,
    "INI": IniLexer,
    "XML": XmlLexer,
}
LANGS = tuple(LEXERS)
PLAIN = "Plain"
GUESS_CHARS = 8000
# the lowest analyse_text score of a Pygments guess that is taken over Plain
GUESS_MIN = 0.5

LIGHT = {
    "keyword": ("#0000cc", True, False),
    "string": ("#a31515", False, False),
    "comment": ("#008000", False, True),
    "number": ("#098658", False, False),
    "type": ("#267f99", False, False),
    "preproc": ("#af00db", False, False),
    "tag": ("#800000", False, False),
    "attr": ("#e50000", False, False),
}
DARK = {
    "keyword": ("#569cd6", True, False),
    "string": ("#ce9178", False, False),
    "comment": ("#6a9955", False, True),
    "number": ("#b5cea8", False, False),
    "type": ("#4ec9b0", False, False),
    "preproc": ("#c586c0", False, False),
    "tag": ("#d7ba7d", False, False),
    "attr": ("#9cdcfe", False, False),
}


def _kind(tok):
    if tok in Comment.Preproc or tok in Comment.PreprocFile:
        return "preproc"
    if tok in Comment:
        return "comment"
    if tok in Keyword.Type or tok in Name.Builtin or tok in Name.Class:
        return "type"
    if tok in Keyword:
        return "keyword"
    if tok in String:
        return "string"
    if tok in Number:
        return "number"
    if tok in Name.Tag:
        return "tag"
    if tok in Name.Attribute:
        return "attr"
    return None


def _score(text, rules):
    return sum(w for rx, w in rules if re.search(rx, text, re.M))


PY_RULES = (
    (r"^\s*def \w+\(.*\)\s*(->.*)?:\s*$", 2),
    (r"^\s*class \w+.*:\s*$", 2),
    (r"^(import [\w., ]+|from [\w.]+ import .+)$", 2),
    (r"^if __name__ ==", 2),
    (r"^\s*(elif .*|else|try|except.*|finally|with .*|for .* in .*|while .*):\s*$", 1),
    (r"\bself\.\w+", 1),
    (r"^\s*(print|len|range)\(", 1),
)
C_RULES = (
    (r"^\s*#\s*include\s*[<\"]", 3),
    (r"^\s*#\s*(define|ifdef|ifndef|endif|pragma)\b", 2),
    (
        r"^\s*(static\s+)?(unsigned\s+|const\s+)?(int|void|char|float|double|long|uint\d+_t)\s+\**\w+\s*\([^)]*\)\s*\{?\s*$",
        2,
    ),
    (r"\b(printf|malloc|sizeof)\s*\(", 1),
    (r";\s*$", 1),
)
CPP_RULES = (
    (r"^\s*#\s*include\s*<\w+>", 3),
    (r"\bstd::", 3),
    (r"^\s*using\s+namespace\b", 3),
    (r"^\s*namespace\s+\w+", 3),
    (r"^\s*template\s*<", 3),
    (r"^\s*(class|struct)\s+\w+.*\{?\s*$", 1),
    (r"^\s*(public|private|protected)\s*:", 3),
    (r"\b(nullptr|constexpr|cout|cin)\b", 2),
    (r"\w::\w", 1),
)
SV_RULES = (
    r"\b(always_ff|always_comb|always_latch|endinterface|endpackage|endclass)\b",
    r"^\s*(interface|package|class|program)\s+\w+",
    r"\b(logic|bit|int|byte)\b\s*(\[|\w)",
    r"\bimport\s+\w+::",
    r"\.\*",
)


def _xml(text):
    if text.startswith("<?xml"):
        return True
    if not (text.startswith("<") and text.endswith(">")):
        return False
    try:
        ET.fromstring(f"<r>{text}</r>")
    except ET.ParseError:
        return False
    return True


def _json(text):
    if text[:1] not in "{[":
        return False
    try:
        json.loads(text)
    except ValueError:
        return False
    return True


def _ini(text):
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not any(re.fullmatch(r"\[[^\[\]]+\]", ln) for ln in lines):
        return False
    shape = r"\[[^\[\]]+\]|[;#].*|[^=:\s][^=:]*[=:].*"
    ok = sum(bool(re.fullmatch(shape, ln)) for ln in lines)
    return ok == len(lines) and any("=" in ln for ln in lines)


def _guess(text):
    try:
        lex = guess_lexer(text[:GUESS_CHARS])
    except ClassNotFound:
        return ""
    return lex.name if lex.analyse_text(text[:GUESS_CHARS]) >= GUESS_MIN else ""


def detect(text):
    # a tuned signature first; a Pygments guess only when none fits and it is confident
    text = text.strip()
    if len(text.split()) < 2 and not _json(text):
        return ""
    if _xml(text):
        return "XML"
    if _json(text):
        return "JSON"
    if re.search(
        r"^\s*(entity\s+\w+\s+is|architecture\s+\w+\s+of\s+\w+\s+is|library\s+ieee\s*;)"
        r"|\bend\s+(entity|architecture)\b",
        text,
        re.M | re.I,
    ):
        return "VHDL"
    if re.search(r"^\s*module\s+\w+", text, re.M) and re.search(r"\bendmodule\b", text):
        return (
            "SystemVerilog"
            if any(re.search(r, text, re.M) for r in SV_RULES)
            else "Verilog"
        )
    if _ini(text):
        return "INI"
    c, cpp, py = _score(text, C_RULES), _score(text, CPP_RULES), _score(text, PY_RULES)
    if cpp >= 3:
        return "C++"
    if c >= 3 and c > py:
        return "C"
    if py >= 2:
        return "Python"
    return _guess(text)


def lexer_for(name):
    if name in LEXERS:
        return LEXERS[name]()
    return find_lexer_class(name)()


def _line_spans(text, lexer):
    # {line: [(start, length, kind)]} of the tokens that take a colour
    starts = [0]
    starts.extend(i + 1 for i, ch in enumerate(text) if ch == "\n")
    out = {}
    for at, tok, val in lexer.get_tokens_unprocessed(text):
        kind = _kind(tok)
        if kind is None:
            continue
        while val:
            n = bisect.bisect_right(starts, at) - 1
            head, nl, rest = val.partition("\n")
            if head:
                out.setdefault(n, []).append((at - starts[n], len(head), kind))
            at += len(head) + len(nl)
            val = rest
    return out


def _u16(line, i):
    # a Qt column of the str index i
    return len(line[:i].encode("utf-16-le")) // 2


class Spans(QTextBlockUserData):
    def __init__(self, text, spans):
        super().__init__()
        self.text, self.spans = text, spans

    def fit(self, text):
        # the spans of the text they were lexed from, moved to the edited text of the same block
        if text == self.text:
            return self.spans
        old = self.text
        p = 0
        while p < min(len(old), len(text)) and old[p] == text[p]:
            p += 1
        s = 0
        while s < min(len(old), len(text)) - p and old[-1 - s] == text[-1 - s]:
            s += 1
        oend, nend = len(old) - s, len(text) - s
        delta = nend - oend

        def at(x):
            if x <= p:
                return x
            return x + delta if x >= oend else min(x, nend)

        out = []
        for a, n, kind in self.spans:
            a2, e2 = at(a), at(a + n)
            if e2 > a2:
                out.append((a2, e2 - a2, kind))
        return out


class SyntaxHighlighter(ItemHighlighter):
    def __init__(self, doc, settings, caret, visited, lang=""):
        super().__init__(doc, settings, caret, visited)
        self.lang = lang
        self.resolved = ""
        self._fresh = None
        self._applied = None
        self._look = {}
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(150)
        self.timer.timeout.connect(self.flush)
        doc.contentsChange.connect(self._edited)
        self.restyle()

    def _edited(self, *_):
        self.timer.start()

    def restyle(self):
        dark = QColor(shown(self.settings, "editor_bg")).lightness() < 128
        self._look = {}
        for kind, (col, bold, italic) in (DARK if dark else LIGHT).items():
            f = QTextCharFormat()
            f.setForeground(QColor(col))
            if bold:
                f.setFontWeight(QFont.Weight.Bold)
            f.setFontItalic(italic)
            self._look[kind] = f

    def flush(self):
        # a full rehighlight takes seconds on a long note: after the first one, only the blocks
        # whose token spans changed are redone
        self.timer.stop()
        doc = self.document()
        if doc is None:
            return
        self.restyle()
        text = doc.toPlainText()
        self.resolved = (
            detect(text) if not self.lang else "" if self.lang == PLAIN else self.lang
        )
        spans = _line_spans(text, lexer_for(self.resolved)) if self.resolved else {}
        sig = (
            self.resolved,
            repr(list(self.settings.items())),
            doc_zoom(doc),
            rdb_theme.dark(),
        )
        modified = doc.isModified()
        doc.blockSignals(True)
        try:
            if sig != self._applied:
                self._applied = sig
                self._fresh = spans
                self.rehighlight()
            else:
                self._redo(spans)
            doc.setModified(modified)
        finally:
            doc.blockSignals(False)
            self._fresh = None

    def _redo(self, spans):
        block = self.document().begin()
        while block.isValid():
            new = spans.get(block.blockNumber(), [])
            data = block.userData()
            if data is None or data.text != block.text() or data.spans != new:
                block.setUserData(Spans(block.text(), new))
                self.rehighlightBlock(block)
            block = block.next()

    def _item(self, text):
        super()._item(text)
        block = self.currentBlock()
        if self._fresh is not None:
            spans = self._fresh.get(block.blockNumber(), [])
            self.setCurrentBlockUserData(Spans(text, spans))
        else:
            data = block.userData()
            spans = data.fit(text) if data is not None else []
        if not spans:
            return
        base = block.position()
        free = []
        it = block.begin()
        while not it.atEnd():
            frag = it.fragment()
            lo = frag.position() - base
            fc = frag.charFormat()
            # links and chips keep the list look; so does a colour the user set
            if not fc.isAnchor() and not fc.hasProperty(
                QTextFormat.Property.ForegroundBrush
            ):
                free.append((lo, lo + frag.length()))
            it += 1
        for a, b in tag_runs(block):
            free = [
                piece
                for lo, hi in free
                for piece in ((lo, min(hi, a - base)), (max(lo, b - base), hi))
                if piece[0] < piece[1]
            ]
        wide = len(text) != len(text.encode("utf-16-le")) // 2
        for a, n, kind in spans:
            if wide:
                a, n = _u16(text, a), _u16(text, a + n) - _u16(text, a)
            for lo, hi in free:
                s, e = max(a, lo), min(a + n, hi)
                if s < e:
                    f = QTextCharFormat(self.format(s))
                    f.merge(self._look[kind])
                    self.setFormat(s, e - s, f)
