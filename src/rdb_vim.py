import base64
import json
import re
import struct
from collections import namedtuple
from contextlib import contextmanager
from types import SimpleNamespace

from PySide6.QtCore import QEvent, QMimeData, QPoint, Qt
from PySide6.QtGui import (
    QKeyEvent,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextDocumentFragment,
)
from PySide6.QtWidgets import QApplication

from rdb_model import (
    body_html,
    apply_view,
    attach_images,
    chip_span,
    ensure_ids,
    ensure_task_ids,
    fold_heading,
    fragment_html,
    fragment_plain,
    get_meta,
    image_names,
    is_heading,
    is_section,
    item_blocks,
    iter_blocks,
    layout_block,
    make_todo,
    mirror_range,
    numbered,
    plain_meta,
    remove_item,
    set_meta,
    todo_meta,
    unit_of,
    NO_HOME,
    HEADING,
    TODO,
)

ITEMS_MIME = "application/x-rdb-items"
MO = QTextCursor.MoveOperation
KEEP = QTextCursor.MoveMode.KeepAnchor
CTRL = Qt.KeyboardModifier.ControlModifier
ALT = Qt.KeyboardModifier.AltModifier
SHIFT = Qt.KeyboardModifier.ShiftModifier
K = Qt.Key
INF = 1 << 30
NEED = object()

# count / op / ocount / name / arg; name "_" is the doubled operator (whole lines)
Cmd = namedtuple("Cmd", "count op ocount name arg")
# kind: "e" exclusive, "i" inclusive, "l" linewise, "c" charwise with a final exclusive end
M = namedtuple("M", "pos kind want scroll", defaults=(None, None))
R = namedtuple("R", "s e line")

SPECIAL = {
    K.Key_Return: "<CR>",
    K.Key_Enter: "<CR>",
    K.Key_Backspace: "<BS>",
    K.Key_Delete: "<Del>",
}
ARROWS = {
    K.Key_Left: "h",
    K.Key_Right: "l",
    K.Key_Up: "k",
    K.Key_Down: "j",
    K.Key_Home: "0",
    K.Key_End: "$",
    K.Key_PageUp: "<C-b>",
    K.Key_PageDown: "<C-f>",
}
VIM_CTRL = {"<C-d>", "<C-f>", "<C-b>", "<C-r>"}
NOOP = {"<C-d>", "<C-f>", "<C-b>"}
OPS = {"d", "c", "y", ">", "<", "g~", "gu", "gU"}
CHANGE_OPS = OPS - {"y"}
ARG_MOT = set("fFtT'")
ARG_CMD = set("rm")
CMDS = set("xXsSDCYpPJ~iaIAoOR") | {
    "u",
    "<C-r>",
    ".",
    "gi",
    "gv",
    "v",
    "V",
    "r",
    "m",
    "ZZ",
    "ZQ",
    "za",
    "zo",
    "zc",
}
CHANGE_CMDS = set("xXsSDCpPJ~iaIAoOR") | {"gi", "r"}
VCMDS = set("dcy><~uUJpPxXsSDCRYroOvV") | {"g~", "gu", "gU", "m"}
ALIAS = {
    "x": ("d", "l"),
    "X": ("d", "h"),
    "D": ("d", "$"),
    "C": ("c", "$"),
    "s": ("c", "l"),
    "S": ("c", "_"),
    "Y": ("y", "_"),
}
# visual command -> (operator, force linewise)
VOP = {
    "d": ("d", False),
    "x": ("d", False),
    "X": ("d", True),
    "D": ("d", True),
    "c": ("c", False),
    "s": ("c", False),
    "S": ("c", True),
    "R": ("c", True),
    "C": ("c", True),
    "y": ("y", False),
    "Y": ("y", True),
    ">": (">", False),
    "<": ("<", False),
    "~": ("g~", False),
    "u": ("gu", False),
    "U": ("gU", False),
    "g~": ("g~", False),
    "gu": ("gu", False),
    "gU": ("gU", False),
}
PAIR_OBJ = "()[]{}<>bBt" + chr(34) + chr(39) + chr(96)
OBJ_CH = set("wWsp\"'`()b[]{}B<>t")
BLOCKS = {
    "(": "()",
    ")": "()",
    "b": "()",
    "[": "[]",
    "]": "[]",
    "{": "{}",
    "}": "{}",
    "B": "{}",
    "<": "<>",
    ">": "<>",
}
PAIRS = {"(": ")", "[": "]", "{": "}"}
LABELS = {
    "normal": "NORMAL",
    "visual": "VISUAL",
    "vline": "V-LINE",
    "replace": "REPLACE",
}
BLANK = " \t"
WS = " \t\n"
CASE = {
    "g~": lambda c: c.swapcase(),
    "gu": lambda c: c.lower(),
    "gU": lambda c: c.upper(),
}


def token(e):
    key, mods, chars = e.key(), e.modifiers(), e.text()
    ctrl, alt = bool(mods & CTRL), bool(mods & ALT)
    if ctrl and alt:
        return chars if chars and chars.isprintable() else None
    if alt:
        return None
    if ctrl:
        if key in (K.Key_Return, K.Key_Enter) and not mods & SHIFT:
            return "<CR>"
        # Ctrl+B and Ctrl+U are the editor's bold and underline
        if mods & SHIFT or not 0x41 <= key <= 0x5A or key in (K.Key_B, K.Key_U):
            return None
        return f"<C-{chr(key).lower()}>"
    if key in SPECIAL:
        return SPECIAL[key]
    if key in ARROWS:
        return ARROWS[key]
    if 0x41 <= key <= 0x5A and len(chars) == 1 and chars.isascii() and chars.isalpha():
        # Caps Lock flips the typed case; a command means what it would with it off
        return chr(key) if mods & SHIFT else chr(key).lower()
    return chars if chars and chars.isprintable() else None


# Qt positions count UTF-16 units: an astral character (an emoji) is two units here as well
def text(b):
    t = b.text()
    if max(t, default=" ") < "\U00010000":
        return t
    d = t.encode("utf-16-le")
    return "".join(map(chr, struct.unpack(f"<{len(d) // 2}H", d)))


def astral(u):
    return u.encode("utf-16-le", "surrogatepass").decode("utf-16-le")


def high(t, i):
    return i < len(t) and "\ud800" <= t[i] <= "\udbff"


def ok(b):
    return b.isValid() and b.isVisible() and not is_section(b)


def indent(t):
    return len(t) - len(t.lstrip(BLANK))


def fnb(t):
    return min(indent(t), max(len(t) - 1, 0))


def wordc(c):
    return c.isalnum() or c == "_"


def combine(a, b):
    return None if a is None and b is None else (a or 1) * (b or 1)


class Cur:
    def __init__(self, eng, p):
        self.eng = eng
        self.b = eng.blk(p)
        self.col = p - self.b.position()

    @property
    def pos(self):
        return self.b.position() + self.col

    def ch(self):
        t = text(self.b)
        return t[self.col] if self.col < len(t) else ""

    def chip(self):
        return chip_span(self.eng.doc, self.pos)

    def empty(self):
        return not text(self.b)

    def last_line(self):
        return not self.eng.nxt(self.b).isValid()

    # inc/dec follow vim: 0 moved, 1 crossed a line, 2 moved onto the line end, -1 at the buffer edge
    def inc(self):
        if self.col < len(text(self.b)):
            chip = self.chip()
            self.col = chip[1] - self.b.position() if chip else self.col + 1
            return 0 if self.col < len(text(self.b)) else 2
        nb = self.eng.nxt(self.b)
        if not nb.isValid():
            return -1
        self.b, self.col = nb, 0
        return 1

    def dec(self):
        if self.col > 0:
            self.col -= 1
            chip = self.chip()
            if chip:
                self.col = chip[0] - self.b.position()
            return 0
        pb = self.eng.prv(self.b)
        if not pb.isValid():
            return -1
        self.b, self.col = pb, len(text(pb))
        return 1

    def incl(self):
        r = self.inc()
        if r >= 1 and self.col:
            r = self.inc()
        return r

    def decl(self):
        r = self.dec()
        if r == 1 and self.col:
            r = self.dec()
        return r

    def cls(self, big):
        c = self.ch()
        if c == "\u00a0" and self.chip():
            return 1 if big else 2
        if c in ("", " ", "\t", "\u00a0"):
            return 0
        return 1 if big or not wordc(c) else 2

    def skip(self, cl, big, fwd):
        while self.cls(big) == cl:
            if (self.inc() if fwd else self.dec()) == -1:
                return True
        return False

    def back_in_line(self, big):
        sc = self.cls(big)
        while self.col > 0:
            self.dec()
            if self.cls(big) != sc:
                self.inc()
                break

    def fwd_word(self, n, big, eol):
        while n > 0:
            n -= 1
            sc = self.cls(big)
            last = self.last_line()
            i = self.inc()
            if i == -1 or (i >= 1 and last):
                return False
            if i >= 1 and eol and n == 0:
                return True
            if sc != 0:
                while self.cls(big) == sc:
                    i = self.inc()
                    if i == -1 or (i >= 1 and eol and n == 0):
                        return True
            while self.cls(big) == 0:
                if self.col == 0 and self.empty():
                    break
                i = self.inc()
                if i == -1 or (i >= 1 and last):
                    return True
        return True

    def bck_word(self, n, big, stop):
        while n > 0:
            n -= 1
            sc = self.cls(big)
            if self.dec() == -1:
                return False
            done = False
            if not stop or sc == self.cls(big) or sc == 0:
                while self.cls(big) == 0:
                    if self.col == 0 and self.empty():
                        done = True
                        break
                    if self.dec() == -1:
                        return True
                if not done and self.skip(self.cls(big), big, False):
                    return True
            if not done:
                self.inc()
            stop = False
        return True

    def end_word(self, n, big, stop, empty):
        while n > 0:
            n -= 1
            sc = self.cls(big)
            if self.inc() == -1:
                return False
            done = False
            if self.cls(big) == sc and sc != 0:
                if self.skip(sc, big, True):
                    return False
            elif not stop or sc == 0:
                while self.cls(big) == 0:
                    if empty and self.col == 0 and self.empty():
                        done = True
                        break
                    if self.inc() == -1:
                        return False
                if not done and self.skip(self.cls(big), big, True):
                    return False
            if not done:
                self.dec()
            stop = False
        return True

    def bckend_word(self, n, big, eol):
        while n > 0:
            n -= 1
            sc = self.cls(big)
            i = self.dec()
            if i == -1:
                return False
            if eol and i == 1:
                return True
            if sc != 0:
                while self.cls(big) == sc:
                    i = self.dec()
                    if i == -1 or (eol and i == 1):
                        return True
            while self.cls(big) == 0:
                if self.col == 0 and self.empty():
                    break
                i = self.dec()
                if i == -1 or (eol and i == 1):
                    return True
        return True

    def in_indent(self, extra):
        return indent(text(self.b)) >= self.col + extra


class VimEngine:
    def __init__(self, ed):
        self.ed = ed
        w = lambda big, fn: lambda p, c, a, o: fn(p, c, o, big)  # noqa: E731
        self.mot = {
            "h": self.m_h,
            "l": self.m_l,
            "j": lambda p, c, a, o: self.vert(p, c or 1, 1),
            "k": lambda p, c, a, o: self.vert(p, c or 1, -1),
            "w": w(False, self.m_w),
            "W": w(True, self.m_w),
            "e": w(False, self.m_e),
            "E": w(True, self.m_e),
            "b": w(False, self.m_b),
            "B": w(True, self.m_b),
            "ge": w(False, self.m_ge),
            "gE": w(True, self.m_ge),
            "0": lambda p, c, a, o: M(self.blk(p).position(), "e"),
            "^": self.m_caret,
            "$": self.m_dollar,
            "g_": self.m_g_,
            "gg": lambda p, c, a, o: self.m_go(c, True),
            "G": lambda p, c, a, o: self.m_go(c, False),
            "f": lambda p, c, a, o: self.m_find(p, c, a, "f", False),
            "F": lambda p, c, a, o: self.m_find(p, c, a, "F", False),
            "t": lambda p, c, a, o: self.m_find(p, c, a, "t", False),
            "T": lambda p, c, a, o: self.m_find(p, c, a, "T", False),
            ";": lambda p, c, a, o: self.m_again(p, c, False),
            ",": lambda p, c, a, o: self.m_again(p, c, True),
            "%": self.m_pct,
            "{": lambda p, c, a, o: self.m_par(p, c, False),
            "}": lambda p, c, a, o: self.m_par(p, c, True),
            "H": self.m_H,
            "M": self.m_M,
            "L": self.m_L,
            "'": lambda p, c, a, o: self.m_mark(a),
            "n": lambda p, c, a, o: self.m_search(p, c, False),
            "N": lambda p, c, a, o: self.m_search(p, c, True),
            "*": lambda p, c, a, o: self.m_star(p, c, True),
            "#": lambda p, c, a, o: self.m_star(p, c, False),
            "<C-d>": lambda p, c, a, o: self.page(p, c, 1, False),
            "<C-f>": lambda p, c, a, o: self.page(p, c, 1, True),
            "<C-b>": lambda p, c, a, o: self.page(p, c, -1, True),
        }
        self.ops = {
            "d": self.op_d,
            "c": self.op_c,
            "y": self.op_y,
            ">": lambda r, v: self.op_shift(r, 1),
            "<": lambda r, v: self.op_shift(r, -1),
            "g~": lambda r, v: self.op_case(r, "g~"),
            "gu": lambda r, v: self.op_case(r, "gu"),
            "gU": lambda r, v: self.op_case(r, "gU"),
        }
        self.cmds = {
            "p": lambda c: self.put(False, c.count or 1),
            "P": lambda c: self.put(True, c.count or 1),
            "J": lambda c: self.join(self.blk(self.cur()), max(c.count or 2, 2)),
            "u": self.c_undo,
            "<C-r>": self.c_redo,
            ".": self.c_dot,
            "~": self.c_tilde,
            "r": self.c_replace,
            "m": self.c_mark,
            "v": lambda c: self.enter_visual("visual"),
            "V": lambda c: self.enter_visual("vline"),
            "gv": self.c_gv,
            "ZZ": lambda c: self.ed.leave.emit(),
            "ZQ": lambda c: self.ed.leave.emit(),
            "za": lambda c: self.c_fold(None),
            "zo": lambda c: self.c_fold(False),
            "zc": lambda c: self.c_fold(True),
        }
        for k in ("i", "a", "I", "A", "o", "O", "R", "gi"):
            self.cmds[k] = self.c_insert
        for k in ALIAS:
            self.cmds[k] = self.c_alias
        self.reset()

    @property
    def doc(self):
        return self.ed.document()

    @property
    def vis(self):
        return self.ed.mode in ("visual", "vline")

    def reset(self):
        self.buf = []
        self.prompt = None
        self.pcmd = None
        self.want = None
        self.find = None
        self.search = None
        self.marks = {}
        self.last = None
        self.gi = None
        self.gv = None
        self.anchor = self.vpos = 0
        self.shown = None
        self.scroll = None
        self.after = 0
        self.vspec = None
        self.replaying = False
        self.ins = None
        self.join_next = False
        self.cmd = None

    def cancel(self):
        self.buf = []
        self.prompt = None

    def label(self):
        m = self.ed.mode
        if m == "insert":
            return ""
        if self.prompt is not None:
            return self.pcmd.name + self.prompt
        tag = LABELS[m]
        return f"{tag}  {''.join(self.buf)}" if self.buf else tag

    # buffer geometry: only visible, non-section blocks are lines

    def blk(self, p):
        return self.doc.findBlock(p)

    def nxt(self, b):
        b = b.next()
        while b.isValid() and not ok(b):
            b = b.next()
        return b

    def prv(self, b):
        b = b.previous()
        while b.isValid() and not ok(b):
            b = b.previous()
        return b

    def lines(self):
        return [b for b in iter_blocks(self.doc) if ok(b)]

    def cur(self):
        return self.vpos if self.vis else self.ed.textCursor().position()

    def snap(self, p):
        b = self.blk(p)
        col = p - b.position()
        p = p - 1 if col and high(text(b), col - 1) else p
        chip = chip_span(self.doc, p)
        return chip[0] if chip else p

    def nextc(self, p):
        chip = chip_span(self.doc, p)
        if chip:
            return chip[1]
        b = self.blk(p)
        return p + (2 if high(text(b), p - b.position()) else 1)

    def norm(self, p):
        b = self.blk(p)
        col = min(p - b.position(), max(len(text(b)) - 1, 0))
        return self.snap(b.position() + col)

    def setcur(self, p, raw=False):
        if self.vis:
            self.vpos = self.snap(p)
            self.render()
            return
        c = QTextCursor(self.doc)
        c.setPosition(self.snap(p) if raw else self.norm(p))
        self.ed.setTextCursor(c)

    def set_line(self, b):
        self.setcur(b.position() + fnb(text(b)))

    def maxcol(self, b):
        n = len(text(b))
        return n if self.vis else max(n - 1, 0)

    def at_col(self, b, w):
        return b.position() + min(w, self.maxcol(b))

    def sanitize(self):
        ed = self.ed
        c = ed.textCursor()
        if self.vis:
            if (c.anchor(), c.position()) != self.shown:
                self.leave_visual(c.position())
            return
        if c.hasSelection():
            c.clearSelection()
            ed.setTextCursor(c)
        b = c.block()
        if not ok(b):
            ed._leave(b)
            c = ed.textCursor()
        if c.position() != self.norm(c.position()):
            self.setcur(c.position())

    # keys

    def key(self, e):
        tok = token(e)
        if tok is None or (tok.startswith("<C-") and tok not in VIM_CTRL):
            return False
        self.sanitize()
        if not ok(self.blk(self.cur())) and tok not in ("o", "O"):
            # every line is hidden: only opening a new item can do anything
            self.buf = []
            self.prompt = None
        elif self.prompt is not None:
            self.prompt_key(tok)
        else:
            self.buf.append(tok)
            cmd = self.parse(self.buf)
            if cmd is not NEED:
                self.buf = []
                if cmd is not None:
                    self.exec(cmd)
        self.ed.status_changed.emit()
        return True

    def prompt_key(self, tok):
        if tok == "<CR>":
            cmd, line, self.prompt = self.pcmd, self.prompt, None
            if cmd.name == ":":
                # ex line: :N goes to gutter number N, the quit commands leave the editor, everything
                # else (:w included) is ignored
                if line.isdigit():
                    b = self.ex_line(max(int(line), 1))
                    if b is not None:
                        self.set_line(b)
                elif line in ("q", "q!", "wq", "wq!", "x", "x!"):
                    self.ed.leave.emit()
                return
            pat = line or (self.search[0] if self.search else "")
            if pat:
                self.search = (pat, cmd.name == "/", False)
                self.exec(cmd._replace(name="n"))
        elif tok == "<BS>":
            self.prompt = self.prompt[:-1] if self.prompt else None
        elif len(tok) == 1:
            self.prompt += tok

    def ex_line(self, n):
        if self.ed.note:
            lines = self.lines()
            return lines[min(n, len(lines)) - 1]
        # list numbers restart at every heading: N in the caret's section first, else the first N
        here, sec, mine, hits = self.blk(self.cur()).blockNumber(), 0, 0, []
        for b, k in numbered(self.doc, self.ed.timeline):
            if k == 0:
                sec += 1
            if b.blockNumber() <= here:
                mine = sec
            if k == n and ok(b):
                hits.append((sec, b))
        return next((b for s, b in hits if s == mine), hits[0][1] if hits else None)

    def parse(self, t):
        i = 0

        def num():
            nonlocal i
            s = i
            while i < len(t) and t[i].isdigit() and (i > s or t[i] != "0"):
                i += 1
            return int("".join(t[s:i])) if i > s else None

        def key():
            nonlocal i
            if i >= len(t):
                return None
            k = t[i]
            i += 1
            if k in ("g", "Z", "z"):
                if i >= len(t):
                    return None
                k += t[i]
                i += 1
            return k

        n1 = num()
        k = key()
        if k is None:
            return NEED
        if k in OPS and not self.vis:
            n2 = num()
            k2 = key()
            if k2 is None:
                return NEED
            if k2 == k or (k[0] == "g" and k2 == k[1]):
                return Cmd(n1, k, n2, "_", None)
            return self.build(n1, k, n2, k2, t, i)
        return self.build(n1, None, None, k, t, i)

    def build(self, n1, op, n2, k, t, i):
        if k in ("i", "a") and (op or self.vis):
            if i >= len(t):
                return NEED
            return Cmd(n1, op, n2, k + t[i], None) if t[i] in OBJ_CH else None
        if k in ARG_MOT or (op is None and k in ARG_CMD):
            if i >= len(t):
                return NEED
            return Cmd(n1, op, n2, k, t[i])
        if k in ("/", "?") or (k in self.mot and not (op and k in NOOP)):
            return Cmd(n1, op, n2, k, None)
        if k == ":" and op is None and not self.vis:
            return Cmd(n1, None, None, k, None)
        if op is None and k in (VCMDS if self.vis else CMDS):
            return Cmd(n1, None, None, k, None)
        return None

    def escape(self):
        if self.prompt is not None or self.buf:
            self.cancel()
        elif self.vis:
            self.leave_visual(self.vpos)
        elif self.ed.mode in ("insert", "replace"):
            self.end_insert()
        self.ed.status_changed.emit()

    # one command = one undo step

    def exec(self, cmd):
        if cmd.name in ("u", "<C-r>", ".", "ZZ", "ZQ") and not self.vis:
            return self.dispatch(cmd)
        doc, was_vis = self.doc, self.vis
        c = QTextCursor(doc)
        c.beginEditBlock()
        steps = doc.availableUndoSteps()
        self.cmd, self.vspec = cmd, None
        try:
            done = self.dispatch(cmd)
        finally:
            c.endEditBlock()
        if self.ins is not None and self.ins.fresh:
            self.ins.fresh = False
            self.join_next = doc.availableUndoSteps() != steps
        elif done is not False and not self.replaying:
            if was_vis:
                if self.vspec:
                    self.last = (cmd, [], self.vspec)
            elif cmd.op in CHANGE_OPS or (cmd.op is None and cmd.name in CHANGE_CMDS):
                self.last = (cmd, [], None)

    def dispatch(self, cmd):
        if self.vis:
            return self.vdispatch(cmd)
        if cmd.op:
            return self.run_op(cmd)
        if cmd.name in ("/", "?", ":"):
            return self.begin_prompt(cmd)
        if cmd.name in self.mot:
            return self.run_motion(cmd)
        return self.cmds[cmd.name](cmd)

    def begin_prompt(self, cmd):
        self.prompt, self.pcmd = "", cmd

    def run_motion(self, cmd):
        t = self.mot[cmd.name](self.cur(), cmd.count, cmd.arg, None)
        if t is None:
            return False
        self.want = t.want
        self.setcur(t.pos)
        if t.scroll is not None:
            self.ed.verticalScrollBar().setValue(t.scroll)

    def run_op(self, cmd):
        op, name = cmd.op, cmd.name
        n = combine(cmd.count, cmd.ocount)
        p = self.cur()
        if name in ("/", "?"):
            return self.begin_prompt(cmd)
        if name == "_":
            r = self.lines_range(p, n or 1)
        elif len(name) == 2 and name[0] in "ia":
            t = self.obj(name, p, n or 1)
            r = None if t is None else self.mkrange(*t)
        else:
            t = self.mot[name](p, n, cmd.arg, op)
            r = None if t is None else self.mkrange(p, t.pos, t.kind)
        if r is None:
            return False
        return self.apply(op, r, False)

    def apply(self, op, r, vis):
        if not r.line and not self.span_ok(r.s, r.e):
            return False
        return self.ops[op](r, vis)

    def span_ok(self, s, e):
        b, end = self.blk(s), self.blk(e).blockNumber()
        while b.isValid() and b.blockNumber() <= end:
            if not ok(b):
                return False
            b = b.next()
        return True

    def lines_range(self, p, n):
        b = t = self.blk(p)
        for _ in range(n - 1):
            nb = self.nxt(t)
            if not nb.isValid():
                if t == b:
                    return None
                break
            t = nb
        return R(p, t.position(), True)

    def mkrange(self, a, b, kind):
        s, e = self.snap(min(a, b)), max(a, b)
        if kind == "l":
            return R(s, e, True)
        if kind == "i":
            eb = self.blk(e)
            return R(
                s, self.nextc(e) if e - eb.position() < len(text(eb)) else e, False
            )
        e = self.snap(e)
        sb, eb = self.blk(s), self.blk(e)
        # exclusive motion ending in column 0 of a later line stops at the end of the previous line,
        # and becomes linewise when it starts at or before the first non-blank
        if kind == "e" and e == eb.position() and eb != sb:
            pb = self.prv(eb)
            if indent(text(sb)) >= s - sb.position():
                return R(s, pb.position(), True)
            return R(s, pb.position() + len(text(pb)), False)
        return R(s, e, False)

    # registers: the unnamed register is the system clipboard

    def reg_chars(self, s, e):
        tmp = QTextDocument()
        tc = QTextCursor(tmp)
        b = self.blk(s)
        while b.isValid() and b.position() <= e:
            end = b.position() + len(text(b))
            lo, hi = max(s, b.position()), min(e, end)
            it = b.begin()
            while not it.atEnd():
                fr = it.fragment()
                a = fr.position()
                x, y = max(a, lo), min(a + fr.length(), hi)
                if x < y:
                    tc.insertText(astral(text(fr)[x - a : y - a]), fr.charFormat())
                it += 1
            if e > end:
                tc.insertBlock()
            b = b.next()
        frag = QTextDocumentFragment(tmp)
        md = QMimeData()
        md.setHtml(fragment_html(frag))
        md.setText(fragment_plain(frag))
        QApplication.clipboard().setMimeData(md)

    def runs(self, blocks):
        runs = []
        for b in blocks:
            if runs and runs[-1][-1].next() == b:
                runs[-1].append(b)
            else:
                runs.append([b])
        return runs

    def reg_lines(self, blocks, move):
        tmp = QTextDocument()
        tc = QTextCursor(tmp)
        for i, run in enumerate(self.runs(blocks)):
            c = QTextCursor(run[0])
            last = run[-1]
            c.setPosition(last.position() + last.length() - 1, KEEP)
            if i:
                tc.insertBlock()
            start = tmp.blockCount()
            tc.insertFragment(c.selection())
            # a fragment that starts in a list arrives with an empty block in front of it
            if tmp.blockCount() - start == len(run):
                c = QTextCursor(tmp.findBlockByNumber(start - 1))
                c.movePosition(MO.NextBlock, KEEP)
                c.removeSelectedText()
        if tmp.blockCount() != len(blocks):
            raise RuntimeError(
                f"{tmp.blockCount()} register blocks for {len(blocks)} lines"
            )
        html = body_html(tmp)
        md = QMimeData()
        md.setHtml(html)
        md.setText(tmp.toPlainText())
        images = {
            name: base64.b64encode(self.ed.store.get_image(name)).decode()
            for name in image_names(tmp)
        }
        payload = {
            "html": html,
            "meta": [get_meta(b) for b in blocks],
            "images": images,
            "markup": {n: self.ed.store.get_markup(n) for n in images},
            "move": move,
        }
        md.setData(ITEMS_MIME, json.dumps(payload).encode())
        QApplication.clipboard().setMimeData(md)

    def put(self, before, n):
        md = QApplication.clipboard().mimeData()
        if md is None or not (md.hasText() or md.hasHtml()):
            return False
        if md.hasFormat(ITEMS_MIME):
            return self.put_lines(md, before, n)
        p = self.cur()
        b = self.blk(p)
        pos = p if before or not text(b) else p + 1
        c = QTextCursor(self.doc)
        c.setPosition(pos)
        self.ed.setTextCursor(c)
        for _ in range(n):
            # a put keeps yanked blank lines, which a paste drops
            self.ed.insertFromMimeData(md, keep_blanks=True)
        end = self.ed.textCursor().position()
        self.setcur(pos if "\n" in md.text() else end - 1)

    def put_lines(self, md, before, n):
        ed, doc = self.ed, self.doc
        payload = json.loads(bytes(md.data(ITEMS_MIME)).decode())
        meta = payload["meta"]
        tmp = QTextDocument()
        tmp.setHtml(payload["html"])
        ed._adopt_images(tmp, payload["images"], meta, payload["markup"])
        if tmp.blockCount() != len(meta):
            raise ValueError(
                f"{tmp.blockCount()} register blocks, {len(meta)} meta rows"
            )
        anchor = self.blk(self.cur())
        if not before and meta[0]["k"] in (TODO, HEADING):
            # an item goes below the whole item under the caret
            anchor = unit_of(anchor)[-1]
        top = None
        for i in range(n):
            count = doc.blockCount()
            if before and i == 0:
                # a block inserted at a block start keeps the old format on the block above
                c = QTextCursor(anchor)
                c.insertBlock(c.blockFormat(), QTextCharFormat())
                c = QTextCursor(c.block().previous())
                start = c.blockNumber()
            else:
                c = QTextCursor(anchor)
                c.movePosition(MO.EndOfBlock)
                c.insertBlock(QTextBlockFormat(), QTextCharFormat())
                start = anchor.blockNumber() + 1
            c.insertFragment(QTextDocumentFragment(tmp))
            # a fragment that starts in a list arrives with an empty block in front of it
            if doc.blockCount() - count == len(meta) + 1:
                c = QTextCursor(doc.findBlockByNumber(start))
                c.movePosition(MO.NextBlock, KEEP)
                c.removeSelectedText()
            if doc.blockCount() != count + len(meta):
                raise RuntimeError(
                    f"pasted {doc.blockCount() - count} blocks, expected {len(meta)}"
                )
            for j, mt in enumerate(ed.paste_metas(payload)):
                nb = doc.findBlockByNumber(start + j)
                set_meta(nb, **mt)
                layout_block(nb)
            tail = doc.findBlockByNumber(start + len(meta) - 1)
            mirror_range(
                doc,
                doc.findBlockByNumber(start).position(),
                tail.position() + tail.length(),
            )
            # a pasted heading folds at once, with no id still to add in an undo step of its own
            ensure_ids(doc, (HEADING,))
            anchor = doc.findBlockByNumber(start + len(meta) - 1)
            top = start if top is None else top
        if image_names(tmp):
            attach_images(doc, ed.store)
            ed.fit_images(force=True)
        if ed.timeline:
            ensure_task_ids(doc)
        self.set_line(doc.findBlockByNumber(top))
        ed._home(top, anchor.blockNumber(), before)

    # deleting lines

    def del_blocks(self, blocks):
        for run in reversed(self.runs(blocks)):
            remove_item(self.doc, run)

    def ensure_line(self):
        if any(ok(b) for b in iter_blocks(self.doc)):
            return
        c = QTextCursor(self.doc.begin())
        c.insertBlock(c.blockFormat(), QTextCharFormat())
        new = c.block().previous()
        make_todo(new)
        # a note page keeps plain lines
        set_meta(new, **(plain_meta() if self.ed.note else todo_meta()), **NO_HOME)
        layout_block(new)

    def group(self, b):
        # a folded heading goes with the blocks its fold hides
        return item_blocks(b) if is_heading(b) else [b]

    def blocks_in(self, r):
        b, end = self.blk(r.s), self.blk(r.e).blockNumber()
        out = []
        while b.isValid() and b.blockNumber() <= end:
            if ok(b):
                out += self.group(b)
            b = b.next()
        return out

    def del_lines(self, r):
        blocks = self.blocks_in(r)
        if not blocks:
            return False
        self.reg_lines(blocks, True)
        first = blocks[0].blockNumber()
        gone = {b.blockNumber() for b in blocks}
        self.marks = {
            k: c for k, c in self.marks.items() if c.blockNumber() not in gone
        }
        # a lone Completed header must not outlive the last item
        if all(b.blockNumber() in gone or is_section(b) for b in iter_blocks(self.doc)):
            blocks = list(iter_blocks(self.doc))
        self.del_blocks(blocks)
        self.ensure_line()
        # what the lines below belong to may have changed: a fold now hides them
        apply_view(self.doc, self.ed.check_mode)
        b = self.doc.findBlockByNumber(min(first, self.doc.blockCount() - 1))
        land = b
        while land.isValid() and not ok(land):
            land = land.next()
        if not land.isValid():
            land = self.prv(b)
        self.set_line(land)

    # operators

    def op_y(self, r, vis):
        if r.line:
            self.reg_lines(self.blocks_in(r), False)
        elif r.s == r.e:
            return False
        else:
            self.reg_chars(r.s, r.e)
        self.setcur(r.s)

    def op_d(self, r, vis):
        sb, eb = self.blk(r.s), self.blk(r.e)
        # a multi-line charwise delete from the indent to the end of a line is linewise
        if not vis and not r.line and sb != eb:
            if (
                not text(eb)[r.e - eb.position() :].strip(BLANK)
                and indent(text(sb)) >= r.s - sb.position()
            ):
                r = R(r.s, r.e, True)
        if r.line:
            return self.del_lines(r)
        if r.s == r.e:
            return False
        self.reg_chars(r.s, r.e)
        self.cut(r.s, r.e)
        self.setcur(r.s)

    def cut(self, s, e):
        c = QTextCursor(self.doc)
        c.setPosition(s)
        c.setPosition(e, KEEP)
        c.removeSelectedText()

    def op_c(self, r, vis):
        if r.line:
            blocks = self.blocks_in(r)
            # a folded first line keeps its group under it
            blocks = blocks[:1] + blocks[len(self.group(blocks[0])) :]
            self.reg_lines(blocks, True)
            n = blocks[0].blockNumber()
            if len(blocks) > 1:
                self.del_blocks(blocks[1:])
            # an empty first line does not survive the removal below it as the same block
            first = self.doc.findBlockByNumber(n)
            c = QTextCursor(first)
            c.movePosition(MO.EndOfBlock, KEEP)
            c.removeSelectedText()
            pos = first.position()
        else:
            if r.s != r.e:
                self.reg_chars(r.s, r.e)
                self.cut(r.s, r.e)
            pos = r.s
        self.setcur(pos, raw=True)
        self.begin_insert("c", 1)

    def op_shift(self, r, step, times=1):
        blocks = self.blocks_in(r)
        for b in blocks:
            for _ in range(times):
                self.ed.setTextCursor(QTextCursor(b))
                self.ed.indent(step)
        self.set_line(blocks[0])

    def map_chars(self, s, e, fn):
        edits = []
        b = self.blk(s)
        while b.isValid() and b.position() <= e:
            it = b.begin()
            while not it.atEnd():
                fr = it.fragment()
                a = fr.position()
                x, y = max(a, s), min(a + fr.length(), e)
                if x < y:
                    old = text(fr)[x - a : y - a]
                    new = "".join(n if len(n := fn(ch)) == 1 else ch for ch in old)
                    if new != old:
                        edits.append((x, y, new, fr.charFormat()))
                it += 1
            b = b.next()
        for x, y, new, fmt in edits:
            c = QTextCursor(self.doc)
            c.setPosition(x)
            c.setPosition(y, KEEP)
            c.insertText(astral(new), fmt)

    def map_range(self, r, fn):
        if r.line:
            for b in self.blocks_in(r):
                self.map_chars(b.position(), b.position() + len(text(b)), fn)
        else:
            self.map_chars(r.s, r.e, fn)

    def op_case(self, r, op):
        self.map_range(r, CASE[op])
        self.setcur(self.blk(r.s).position() if r.line else r.s)

    # inserting

    def c_insert(self, cmd):
        ed, k, n = self.ed, cmd.name, cmd.count or 1
        p = self.cur()
        b = self.blk(p)
        if k == "o":
            ed.open_below()
        elif k == "O":
            ed.open_above()
        else:
            self.setcur(
                {
                    "i": p,
                    "a": p + (1 if text(b) else 0),
                    "I": b.position() + indent(text(b)),
                    "A": b.position() + len(text(b)),
                    "R": p,
                    "gi": self.gi.position() if self.gi is not None else p,
                }[k],
                raw=True,
            )
        self.begin_insert(k, n)

    def begin_insert(self, kind, count):
        self.ed._set_mode("replace" if kind == "R" else "insert")
        self.ins = SimpleNamespace(
            cmd=self.cmd, kind=kind, count=count, keys=[], fresh=True, vspec=self.vspec
        )
        self.after = self.ed.textCursor().position()

    @contextmanager
    def typing(self, e):
        ed, ins = self.ed, self.ins
        if (
            not ed.settings["vim"]
            or ed.mode not in ("insert", "replace")
            or e.key() == K.Key_Escape
        ):
            yield
            return
        if e.modifiers() & (CTRL | ALT):
            self.join_next = False
            yield
            return
        doc = self.doc
        # a cursor move by anything but typing (arrows, mouse) ends the insert session
        if ins is not None and (
            e.key() in ARROWS or self.ed.textCursor().position() != self.after
        ):
            self.join_next = False
            ins.keys.clear()
        if ins is not None and ins.cmd is not None and not self.replaying:
            ins.keys.append((e.key(), e.modifiers(), e.text()))
        steps = doc.availableUndoSteps()
        c = QTextCursor(doc)
        # typed text of one insert joins the edit block of the command that started it
        if ins is not None:
            (c.joinPreviousEditBlock if self.join_next else c.beginEditBlock)()
        try:
            yield
        finally:
            if ins is not None:
                c.endEditBlock()
        if ins is not None and doc.availableUndoSteps() != steps:
            self.join_next = True
        self.after = self.ed.textCursor().position()

    def replay_keys(self, keys):
        prev, self.replaying = self.replaying, True
        try:
            for key, mods, chars in keys:
                self.ed.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, key, mods, chars))
        finally:
            self.replaying = prev

    def end_insert(self):
        ed, ins = self.ed, self.ins
        self.ins = None
        if ins is not None:
            if ins.count > 1:
                c = QTextCursor(self.doc)
                if self.join_next:
                    c.joinPreviousEditBlock()
                else:
                    c.beginEditBlock()
                try:
                    for _ in range(ins.count - 1):
                        if ins.kind in ("o", "O"):
                            ed.new_todo()
                        self.replay_keys(ins.keys)
                finally:
                    c.endEditBlock()
            if ins.cmd is not None and not self.replaying:
                self.last = (ins.cmd, ins.keys, ins.vspec)
        self.join_next = False
        c = ed.textCursor()
        self.gi = QTextCursor(c)
        if c.positionInBlock() > 0:
            c.setPosition(self.snap(c.position() - 1))
        ed._set_mode("normal")
        ed.setTextCursor(c)

    def c_dot(self, cmd):
        if self.last is None:
            return False
        c0, keys, vspec = self.last
        if cmd.count:
            c0 = c0._replace(count=cmd.count, ocount=None)
        c = QTextCursor(self.doc)
        c.beginEditBlock()
        self.replaying = True
        try:
            self.cmd, self.vspec = c0, None
            if vspec:
                self.vreselect(vspec)
            self.dispatch(c0)
            if self.ins is not None:
                self.ins.fresh = False
                self.ins.keys = list(keys)
                self.replay_keys(keys)
                self.end_insert()
        finally:
            self.replaying = False
            c.endEditBlock()

    # simple commands

    def c_alias(self, cmd):
        op, mo = ALIAS[cmd.name]
        return self.run_op(Cmd(cmd.count, op, None, mo, None))

    def c_undo(self, cmd):
        self.rewind(self.ed.undo, cmd.count or 1)

    def c_redo(self, cmd):
        self.rewind(self.ed.redo, cmd.count or 1)

    # the cursor goes to the start of what changed, not to the end Qt leaves it at
    def rewind(self, step, n):
        top = []
        note = lambda pos, removed, added: top.append(pos)  # noqa: E731
        self.doc.contentsChange.connect(note)
        try:
            for _ in range(n):
                step()
        finally:
            self.doc.contentsChange.disconnect(note)
        if top:
            p = min(top)
            # a change that starts on a line break belongs to the line after it
            if self.doc.characterAt(p) == chr(0x2029):
                p += 1
            self.setcur(p)
        self.sanitize()

    def c_tilde(self, cmd):
        p = self.cur()
        b = self.blk(p)
        n = min(cmd.count or 1, len(text(b)) - (p - b.position()))
        if n <= 0:
            return False
        self.map_chars(p, p + n, CASE["g~"])
        self.setcur(p + n)

    def c_replace(self, cmd):
        p, ch = self.cur(), cmd.arg
        b = self.blk(p)
        n = cmd.count or 1
        if len(text(b)) - (p - b.position()) < n:
            return False
        c = QTextCursor(self.doc)
        c.setPosition(p + 1)
        fmt = c.charFormat()
        c.setPosition(p)
        c.setPosition(p + n, KEEP)
        c.insertText(ch * n, fmt)
        self.setcur(p + n - 1)

    def c_mark(self, cmd):
        if not cmd.arg.islower():
            return False
        c = QTextCursor(self.doc)
        c.setPosition(self.cur())
        self.marks[cmd.arg] = c

    def join(self, b, n):
        avail = 0
        x = b
        while avail < n - 1 and ok(x.next()):
            x = x.next()
            avail += 1
        if not avail:
            return False
        c = QTextCursor(self.doc)
        for _ in range(avail):
            nb = b.next()
            nt = text(nb)
            lead = indent(nt)
            body = nt[lead:]
            t = text(b)
            space = body and not body.startswith(")") and t and t[-1] not in WS
            c.setPosition(b.position() + len(t))
            joint = c.position()
            c.setPosition(nb.position() + lead, KEEP)
            c.insertText(" " if space else "")
        self.setcur(joint)

    # visual mode

    def enter_visual(self, mode):
        self.anchor = self.vpos = self.cur()
        self.ed._set_mode(mode)
        self.render()

    def render(self):
        ed, a, p = self.ed, self.anchor, self.vpos
        c = QTextCursor(self.doc)
        end = self.doc.characterCount() - 1
        if ed.mode == "vline":
            ba, bp = self.blk(a), self.blk(p)
            if a <= p:
                c.setPosition(ba.position())
                c.setPosition(bp.position() + len(text(bp)), KEEP)
            else:
                c.setPosition(ba.position() + len(text(ba)))
                c.setPosition(bp.position(), KEEP)
        elif p >= a:
            c.setPosition(a)
            c.setPosition(min(self.nextc(p), end), KEEP)
        else:
            c.setPosition(min(self.nextc(a), end))
            c.setPosition(p, KEEP)
        ed.setTextCursor(c)
        cc = ed.textCursor()
        self.shown = (cc.anchor(), cc.position())

    def snapshot(self):
        a, p = QTextCursor(self.doc), QTextCursor(self.doc)
        a.setPosition(self.anchor)
        p.setPosition(self.vpos)
        self.gv = (self.ed.mode, a, p)

    def leave_visual(self, pos):
        self.snapshot()
        self.ed._set_mode("normal")
        self.shown = None
        self.setcur(pos)

    def c_fold(self, closed):
        head = fold_heading(self.blk(self.cur()))
        if head is None:
            return False
        self.ed.set_fold(head, closed)

    def c_gv(self, cmd):
        if self.gv is None:
            return False
        mode, a, p = self.gv
        self.anchor, self.vpos = a.position(), p.position()
        self.ed._set_mode(mode)
        self.render()

    def vrange(self, force_line):
        lo, hi = sorted((self.anchor, self.vpos))
        if force_line or self.ed.mode == "vline":
            return R(lo, hi, True)
        b = self.blk(hi)
        # a selection ending on the line end takes the line break with it
        if hi - b.position() < len(text(b)) or ok(b.next()):
            return R(lo, self.nextc(hi), False)
        return R(lo, hi, False)

    # the shape of a selection, so that . can reselect the same amount of text from the cursor
    def vspec_now(self):
        lo, hi = sorted((self.anchor, self.vpos))
        n = len(self.blocks_in(R(lo, hi, True)))
        if self.ed.mode == "vline":
            return ("vline", n, None)
        return (
            "visual",
            n,
            hi - lo + 1 if n == 1 else hi - self.blk(hi).position() + 1,
        )

    def vreselect(self, spec):
        mode, n, cols = spec
        p = self.cur()
        b = last = self.blk(p)
        for _ in range(n - 1):
            if not self.nxt(last).isValid():
                break
            last = self.nxt(last)
        if mode == "vline":
            end = last.position() + min(p - b.position(), len(text(last)))
        elif n == 1:
            end = min(p + cols - 1, b.position() + len(text(b)))
        else:
            end = last.position() + min(cols - 1, len(text(last)))
        self.anchor, self.vpos = p, end
        self.ed._set_mode(mode)
        self.render()

    def vdispatch(self, cmd):
        name, n = cmd.name, cmd.count
        if name in ("/", "?"):
            return self.begin_prompt(cmd)
        if name in self.mot:
            return self.run_motion(cmd)
        if len(name) == 2 and name[0] in "ia":
            return self.vobj(name, n or 1)
        if name in ("o", "O"):
            self.anchor, self.vpos = self.vpos, self.anchor
            return self.render()
        if name in ("v", "V"):
            want = "visual" if name == "v" else "vline"
            if self.ed.mode == want:
                return self.leave_visual(self.vpos)
            self.ed._set_mode(want)
            return self.render()
        if name == "m":
            return self.c_mark(cmd)
        if name not in VOP or VOP[name][0] != "y":
            self.vspec = self.vspec_now()
        if name in ("p", "P"):
            return self.vput(n or 1, name == "P")
        lo = min(self.anchor, self.vpos)
        if name == "J":
            r = self.vrange(True)
            blocks = self.blocks_in(r)
            self.leave_visual(lo)
            return self.join(blocks[0], max(len(blocks), 2))
        if name == "r":
            r = self.vrange(False)
            self.leave_visual(lo)
            self.map_range(r, lambda c: cmd.arg)
            return self.setcur(self.blk(r.s).position() if r.line else lo)
        op, force = VOP[name]
        r = self.vrange(force)
        self.leave_visual(lo)
        if op in (">", "<"):
            return self.op_shift(r, 1 if op == ">" else -1, n or 1)
        done = self.apply(op, r, True)
        # after a linewise yank or case change the cursor rests at the start of the line
        if r.line and op in ("y", "g~", "gu", "gU"):
            self.setcur(self.blk(r.s).position())
        return done

    def copy_mime(self, md):
        out = QMimeData()
        for fmt in md.formats():
            out.setData(fmt, md.data(fmt))
        return out

    # p puts the unnamed register over the selection and leaves the replaced text in it; P leaves it alone
    def vput(self, n, keep):
        clip = QApplication.clipboard()
        if clip.mimeData() is None:
            return False
        src = self.copy_mime(clip.mimeData())
        lo = min(self.anchor, self.vpos)
        r = self.vrange(False)
        self.leave_visual(lo)
        first = self.blocks_in(r)[0].blockNumber() if r.line else 0
        if r.line:
            self.del_lines(r)
        else:
            self.op_d(r, True)
        gone = self.copy_mime(clip.mimeData())
        clip.setMimeData(self.copy_mime(src))
        if r.line:
            # deleted at the end of the buffer: put below the line that is left
            self.put(self.blk(self.cur()).blockNumber() >= first, n)
        else:
            b = self.blk(self.cur())
            self.put(not (text(b) and r.s - b.position() >= len(text(b))), n)
        clip.setMimeData(src if keep else gone)

    def vobj(self, name, n):
        lo, hi = sorted((self.anchor, self.vpos))

        def within(t):
            end = t[1] - 1 if t[2] in "ec" and t[1] > t[0] else t[1]
            return min(t[0], t[1]) >= lo and max(t[0], end) <= hi

        t = self.obj(name, self.vpos, n)
        # a pair object that would not grow the selection takes the enclosing pair
        while (
            t is not None and lo != hi and name[1] in PAIR_OBJ and within(t) and n < 8
        ):
            n += 1
            t = self.obj(name, self.vpos, n)
        if name[1] == "s" and lo != hi:
            t = self.obj(name, min(hi + 1, self.doc.characterCount() - 1), n)
            t = None if t is None else (self.anchor, t[1], t[2])
        if t is None:
            return False
        a, b, kind = t
        if kind == "l":
            self.ed._set_mode("vline")
        elif self.ed.mode == "vline":
            self.ed._set_mode("visual")
        if kind in ("e", "c"):
            b = b - 1 if b > a else a
        self.anchor, self.vpos = a, b
        self.render()

    # motions

    def vert(self, p, n, d):
        b = t = self.blk(p)
        w = self.want if self.want is not None else p - b.position()
        step = self.nxt if d > 0 else self.prv
        for _ in range(n):
            nb = step(t)
            if not nb.isValid():
                break
            t = nb
        if t == b:
            return None
        return M(self.at_col(t, w), "l", w)

    def m_h(self, p, c, a, op):
        cur = Cur(self, p)
        if cur.col == 0:
            return None
        for _ in range(c or 1):
            if cur.col:
                cur.dec()
        return M(cur.pos, "e")

    def m_l(self, p, c, a, op):
        b = self.blk(p)
        col = p - b.position()
        t = text(b)
        lim = len(t) if (op or self.vis) else max(len(t) - 1, 0)
        if col >= lim:
            return None
        for _ in range(c or 1):
            chip = chip_span(self.doc, b.position() + col)
            col = min(
                chip[1] - b.position() if chip else col + (2 if high(t, col) else 1),
                lim,
            )
        return M(b.position() + col, "e")

    def m_w(self, p, c, op, big):
        cur = Cur(self, p)
        kind = "e"
        # cw acts like ce on a non-blank
        if op == "c" and cur.ch() and cur.cls(big):
            cur.end_word(c or 1, big, True, False)
            kind = "i"
        else:
            cur.fwd_word(c or 1, big, bool(op))
            # do not rest on the line end unless we did not move
            if cur.pos > p and cur.col > 0 and cur.ch() == "" and not self.vis:
                cur.col -= 1
                kind = "i"
        return M(cur.pos, kind)

    def m_e(self, p, c, op, big):
        cur = Cur(self, p)
        good = cur.end_word(c or 1, big, False, False)
        if not good and not op:
            return None
        if cur.pos > p and cur.col > 0 and cur.ch() == "" and not self.vis:
            cur.col -= 1
        return M(cur.pos, "i")

    def m_b(self, p, c, op, big):
        cur = Cur(self, p)
        return (
            M(cur.pos, "e")
            if cur.bck_word(c or 1, big, False) and cur.pos != p
            else None
        )

    def m_ge(self, p, c, op, big):
        cur = Cur(self, p)
        if not cur.bckend_word(c or 1, big, False):
            return None
        return M(cur.pos, "i")

    def m_caret(self, p, c, a, op):
        b = self.blk(p)
        return M(b.position() + fnb(text(b)), "e")

    def down(self, b, n):
        for _ in range(n):
            nb = self.nxt(b)
            if not nb.isValid():
                return None
            b = nb
        return b

    def m_dollar(self, p, c, a, op):
        t = self.down(self.blk(p), (c or 1) - 1)
        if t is None:
            return None
        n = len(text(t))
        return M(t.position() + (n if self.vis else max(n - 1, 0)), "i", INF)

    def m_g_(self, p, c, a, op):
        t = self.down(self.blk(p), (c or 1) - 1)
        if t is None:
            return None
        return M(t.position() + max(len(text(t).rstrip(BLANK)) - 1, 0), "i")

    def m_go(self, c, top):
        lines = self.lines()
        b = (
            (lines[0] if top else lines[-1])
            if c is None
            else lines[min(c, len(lines)) - 1]
        )
        return M(b.position() + fnb(text(b)), "l")

    def m_find(self, p, c, ch, cmd, again):
        if not again:
            self.find = (cmd, ch)
        n = c or 1
        fwd, till = cmd in "ft", cmd in "tT"
        b = self.blk(p)
        t = text(b)
        i = p - b.position()
        # ; and , after t/T must move on even when the target is adjacent
        stop = not (again and n == 1 and till)
        for _ in range(n):
            while True:
                i += 1 if fwd else -1
                if i >= len(t) or i < 0:
                    return None
                if t[i] == ch and stop:
                    break
                stop = True
        if till:
            i -= 1 if fwd else -1
        return M(b.position() + i, "i" if fwd else "e")

    def m_again(self, p, c, rev):
        if self.find is None:
            return None
        cmd, ch = self.find
        return self.m_find(p, c, ch, cmd.swapcase() if rev else cmd, True)

    def scan(self, cur, want, nest, fwd):
        depth = 0
        while (cur.inc() if fwd else cur.dec()) != -1:
            c = cur.ch()
            if c == nest:
                depth += 1
            elif c == want:
                if depth == 0:
                    return True
                depth -= 1
        return False

    def m_pct(self, p, c, a, op):
        if c:
            lines = self.lines()
            b = lines[min((c * len(lines) + 99) // 100, len(lines)) - 1]
            return M(b.position() + fnb(text(b)), "l")
        cur = Cur(self, p)
        t = text(cur.b)
        allp = "".join(PAIRS) + "".join(PAIRS.values())
        while cur.col < len(t) and t[cur.col] not in allp:
            cur.col += 1
        if cur.col == len(t):
            return None
        ch = t[cur.col]
        close = {v: k for k, v in PAIRS.items()}
        if ch in PAIRS:
            found = self.scan(cur, PAIRS[ch], ch, True)
        else:
            found = self.scan(cur, close[ch], ch, False)
        return M(cur.pos, "i") if found else None

    def m_par(self, p, c, fwd):
        lines = self.lines()
        curr = lines.index(self.blk(p))
        d = 1 if fwd else -1
        n = c or 1
        for k in range(n):
            skipped, first = False, True
            while True:
                empty = not text(lines[curr])
                if not empty:
                    skipped = True
                if not first and skipped and empty:
                    break
                first = False
                nx = curr + d
                if nx < 0 or nx >= len(lines):
                    if n - k - 1:
                        return None
                    break
                curr = nx
        b = lines[curr]
        if fwd and curr == len(lines) - 1 and text(b):
            return M(b.position() + len(text(b)) - 1, "i")
        return M(b.position(), "e")

    def vlines(self):
        ed = self.ed
        vh = ed.viewport().height()
        b = ed.cursorForPosition(QPoint(0, 0)).block()
        out = []
        while b.isValid():
            if ok(b):
                g = ed._geom(b)
                if g is None or g.bottom > vh:
                    break
                if g.top >= 0:
                    out.append(b)
            b = b.next()
        return out or [self.blk(self.cur())]

    def m_H(self, p, c, a, op):
        v = self.vlines()
        b = v[min((c or 1) - 1, len(v) - 1)]
        return M(b.position() + fnb(text(b)), "l")

    def m_L(self, p, c, a, op):
        v = self.vlines()
        b = v[max(len(v) - (c or 1), 0)]
        return M(b.position() + fnb(text(b)), "l")

    def m_M(self, p, c, a, op):
        v = self.vlines()
        b = v[(len(v) - 1) // 2]
        return M(b.position() + fnb(text(b)), "l")

    def page(self, p, c, d, full):
        nvis = len(self.vlines())
        if full:
            n = max(nvis - 2, 1) * (c or 1)
        else:
            if c:
                self.scroll = c
            n = self.scroll or max(nvis // 2, 1)
        b = t = self.blk(p)
        step = self.nxt if d > 0 else self.prv
        for _ in range(n):
            nb = step(t)
            if not nb.isValid():
                break
            t = nb
        if t == b:
            return None
        g0, g1 = self.ed._geom(b), self.ed._geom(t)
        sb = self.ed.verticalScrollBar()
        scroll = sb.value() + round(g1.top - g0.top) if g0 and g1 else None
        return M(t.position() + fnb(text(t)), "l", None, scroll)

    def m_mark(self, ch):
        cs = self.marks.get(ch)
        if cs is None or not ok(cs.block()):
            return None
        b = cs.block()
        return M(b.position() + fnb(text(b)), "l")

    def find_text(self, p, pat, fwd, whole, n):
        rx = re.compile(rf"\b{re.escape(pat)}\b" if whole else re.escape(pat), re.I)
        lines = self.lines()
        i0 = lines.index(self.blk(p))
        col = p - lines[i0].position()
        pos = p
        for _ in range(n):
            found = None
            for k in range(len(lines) + 1):
                idx = (i0 + k if fwd else i0 - k) % len(lines)
                b = lines[idx]
                ms = [m.start() for m in rx.finditer(text(b))]
                if k == 0:
                    ms = [s for s in ms if (s > col if fwd else s < col)]
                elif k == len(lines):
                    ms = [s for s in ms if (s <= col if fwd else s >= col)]
                if ms:
                    found = (idx, ms[0] if fwd else ms[-1])
                    break
            if found is None:
                return None
            i0, col = found
            pos = lines[i0].position() + col
        return pos

    def m_search(self, p, c, rev):
        if self.search is None:
            return None
        pat, fwd, whole = self.search
        pos = self.find_text(p, pat, fwd != rev, whole, c or 1)
        return None if pos is None else M(pos, "e")

    def m_star(self, p, c, fwd):
        b = self.blk(p)
        col = p - b.position()
        hit = next((m for m in re.finditer(r"\w+", text(b)) if m.end() > col), None)
        if hit is None:
            return None
        self.search = (hit.group(), fwd, True)
        pos = self.find_text(b.position() + hit.start(), hit.group(), fwd, True, c or 1)
        return None if pos is None else M(pos, "e")

    # text objects: (a, b, kind), see M for kinds

    def obj(self, name, p, n):
        inner, ch = name[0] == "i", name[1]
        if ch in "wW":
            return self.o_word(p, n, inner, ch == "W")
        if ch == "s":
            return self.o_sent(p, n, inner)
        if ch == "p":
            return self.o_par(p, n, inner)
        if ch in "\"'`":
            return self.o_quote(p, n, inner, ch)
        if ch == "t":
            return self.o_tag(p, n, inner)
        return self.o_block(p, n, inner, *BLOCKS[ch])

    def o_word(self, p, n, inner, big):
        incl = not inner
        cur = Cur(self, p)
        vis = self.vis and self.anchor != p
        include_white = False
        inclusive = True
        if not vis:
            cur.back_in_line(big)
            start = cur.pos
            if (cur.cls(big) == 0) == incl:
                if not cur.end_word(1, big, True, True):
                    return None
            else:
                cur.fwd_word(1, big, True)
                if cur.col == 0:
                    cur.decl()
                else:
                    cur.col -= 1
                include_white = incl
            n -= 1
        else:
            start = self.anchor
        while n > 0:
            inclusive = True
            if vis and cur.pos < self.anchor:
                if cur.decl() == -1:
                    return None
                if incl != (cur.cls(big) != 0):
                    if not cur.bck_word(1, big, True):
                        return None
                else:
                    if not cur.bckend_word(1, big, True):
                        return None
                    cur.incl()
            else:
                if cur.incl() == -1:
                    return None
                if incl != (cur.cls(big) == 0):
                    if not cur.fwd_word(1, big, True) and n > 1:
                        return None
                    if cur.col == 0:
                        inclusive = False
                    else:
                        cur.col -= 1
                elif not cur.end_word(1, big, True, True):
                    return None
            n -= 1
        if include_white and (cur.cls(big) != 0 or (cur.col == 0 and not inclusive)):
            # no white after the word: take the white before it, but not indent
            s = Cur(self, start)
            if s.col > 0:
                s.col -= 1
                s.back_in_line(big)
                if s.cls(big) == 0 and s.col > 0:
                    start = s.pos
        return (start, cur.pos, "i" if inclusive else "e")

    def o_sent(self, p, n, inner):
        b = self.blk(p)
        if not text(b):
            return None
        run = [b]
        while (x := self.prv(run[0])).isValid() and x.next() == run[0] and text(x):
            run.insert(0, x)
        while (x := self.nxt(run[-1])).isValid() and run[-1].next() == x and text(x):
            run.append(x)
        joined = "\n".join(text(x) for x in run)
        base = run[0].position()
        idx = min(p - base, len(joined) - 1)
        segs, i = [], 0
        while i < len(joined):
            j = i
            if joined[i] in WS:
                while j < len(joined) and joined[j] in WS:
                    j += 1
                segs.append((i, j, True))
            else:
                j = len(joined)
                for m in re.finditer(r"[.!?]+[)\]\"']*(?=[ \t\n]|$)", joined[i:]):
                    j = i + m.end()
                    break
                segs.append((i, j, False))
            i = j
        k = next(x for x, s in enumerate(segs) if s[0] <= idx < s[1])
        if inner:
            if k + n > len(segs):
                return None
            return (base + segs[k][0], base + segs[k + n - 1][1], "c")
        j = k
        if segs[k][2]:
            j = k + 2 * n
            if j > len(segs):
                return None
            return (base + segs[k][0], base + segs[j - 1][1], "c")
        for _ in range(n):
            if j >= len(segs):
                return None
            j += 2 if j + 1 < len(segs) and segs[j + 1][2] else 1
        start = segs[k][0]
        if not segs[j - 1][2] and k > 0 and segs[k - 1][2]:
            start = segs[k - 1][0]
        return (base + start, base + segs[j - 1][1], "c")

    def o_par(self, p, n, inner):
        lines = self.lines()
        last = len(lines) - 1
        cl = lines.index(self.blk(p))
        white = lambda i: not text(lines[i]).strip(BLANK)  # noqa: E731
        ps = lambda i: not text(lines[i])  # noqa: E731
        incl = not inner
        start = cl
        if self.vis and lines.index(self.blk(self.anchor)) != cl:
            d = 1 if start >= lines.index(self.blk(self.anchor)) else -1
            edge = last if d > 0 else 0
            for _ in range(n):
                if start == edge:
                    return None
                prev_w = None
                for _t in range(2):
                    start += d
                    sw = white(start)
                    if prev_w == sw:
                        start -= d
                        break
                    while start != edge and not (
                        sw != white(start + d)
                        or (not sw and ps(start + (1 if d > 0 else 0)))
                    ):
                        start += d
                    if not incl or start == edge:
                        break
                    prev_w = sw
            return (self.anchor, lines[start].position(), "l")
        white_front = white(start)
        while start > 0:
            if white_front:
                if not white(start - 1):
                    break
            elif white(start - 1) or ps(start):
                break
            start -= 1
        end = start
        while end <= last and white(end):
            end += 1
        end -= 1
        i = n - (1 if not incl and white_front else 0)
        do_white = False
        while i > 0:
            i -= 1
            if end == last:
                return None
            if not incl:
                do_white = white(end + 1)
            if incl or not do_white:
                end += 1
                while end < last and not white(end + 1) and not ps(end + 1):
                    end += 1
            if i == 0 and white_front and incl:
                break
            if incl or do_white:
                while end < last and white(end + 1):
                    end += 1
        if not white_front and not white(end) and incl:
            while start > 0 and white(start - 1):
                start -= 1
        return (lines[start].position(), lines[end].position(), "l")

    def o_quote(self, p, n, inner, q):
        b = self.blk(p)
        t = text(b)
        col = p - b.position()
        if col >= len(t):
            return None

        def nextq(i, esc):
            while i < len(t):
                if esc and t[i] == "\\":
                    i += 1
                    if i >= len(t):
                        return -1
                elif t[i] == q:
                    return i
                i += 1
            return -1

        def prevq(i):
            while i > 0:
                i -= 1
                k = 0
                while i - k > 0 and t[i - k - 1] == "\\":
                    k += 1
                if k & 1:
                    i -= k
                elif t[i] == q:
                    break
            return i

        if t[col] == q:
            s = 0
            while True:
                s = nextq(s, False)
                if s < 0 or s > col:
                    return None
                e = nextq(s + 1, True)
                if e < 0:
                    return None
                if s <= col <= e:
                    break
                s = e + 1
        else:
            s = prevq(col)
            if t[s] != q:
                s = nextq(s, False)
                if s < 0:
                    return None
            e = nextq(s + 1, True)
            if e < 0:
                return None
        if not inner:
            if e + 1 < len(t) and t[e + 1] in BLANK:
                while e + 1 < len(t) and t[e + 1] in BLANK:
                    e += 1
            else:
                while s > 0 and t[s - 1] in BLANK:
                    s -= 1
        if inner and n < 2:
            s += 1
        end = e + 1 if (not inner or n > 1) else e
        return (b.position() + s, b.position() + end, "c")

    def o_block(self, p, n, inner, what, other):
        incl = not inner
        cur = Cur(self, p)
        lo, hi = p, p
        vis = self.vis and self.anchor != p
        if vis:
            lo, hi = sorted((self.anchor, p))
            cur = Cur(self, lo)
        else:
            if what == "{":
                while cur.in_indent(1):
                    if cur.inc() != 0:
                        break
            if cur.ch() == what:
                cur.col += 1
        start = None
        for _ in range(n):
            if not self.scan(cur, what, other, False):
                return None
            start = cur.pos
        end = Cur(self, start)
        if not self.scan(end, other, what, True):
            return None
        sol = False
        s = Cur(self, start)
        while not incl:
            s = Cur(self, start)
            s.incl()
            sol = end.col == 0
            end.decl()
            while end.in_indent(1):
                sol = True
                if end.decl() != 0:
                    break
            if vis and s.pos >= lo and end.pos <= hi and s.pos != end.pos:
                cur = Cur(self, lo)
                cur.decl()
                if not self.scan(cur, what, other, False):
                    return None
                start = cur.pos
                end = Cur(self, start)
                if not self.scan(end, other, what, True):
                    return None
            else:
                break
        if incl:
            return (start, end.pos, "i")
        if vis:
            return (s.pos, end.pos + (1 if sol and end.ch() else 0), "i")
        if sol:
            end.incl()
            return (s.pos, end.pos, "e")
        if s.pos <= end.pos:
            return (s.pos, end.pos, "i")
        return (s.pos, s.pos, "e")

    def o_tag(self, p, n, inner):
        b = self.blk(p)
        t = text(b)
        col = p - b.position()
        stack, pairs = [], []
        for m in re.finditer(r"<(/?)([A-Za-z][^\s/>]*)[^>]*?(/?)>", t):
            if m.group(3):
                continue
            if not m.group(1):
                stack.append(m)
                continue
            for k in range(len(stack) - 1, -1, -1):
                if stack[k].group(2) == m.group(2):
                    pairs.append((stack[k], m))
                    del stack[k:]
                    break
        hits = [(o, c) for o, c in pairs if o.start() <= col < c.end()]
        hits.sort(key=lambda oc: oc[1].end() - oc[0].start())
        if len(hits) < n:
            return None
        o, c = hits[n - 1]
        s, e = (o.end(), c.start()) if inner else (o.start(), c.end())
        return (b.position() + s, b.position() + e, "c")
