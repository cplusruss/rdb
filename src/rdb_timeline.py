import ctypes
import ctypes.wintypes as wt
import datetime
import math
import os
import re
import subprocess
import tempfile
from collections import namedtuple
from contextlib import contextmanager

from PySide6.QtCore import (
    QDate,
    QEvent,
    QMarginsF,
    QPointF,
    QRect,
    QRectF,
    QSizeF,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QImage,
    QPageSize,
    QPainter,
    QPainterPath,
    QPainterPathStroker,
    QPdfWriter,
    QPen,
    QPolygonF,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QApplication,
    QCalendarWidget,
    QFrame,
    QLineEdit,
    QMenu,
    QSplitter,
    QSplitterHandle,
    QTableView,
    QVBoxLayout,
    QWidget,
)

import rdb_model as m
import rdb_paths
import rdb_theme

DAY_W = 22
# the time zoom: a day's width in px, and the factor of one wheel notch
DAY_W_MIN, DAY_W_MAX, DAY_W_STEP = 1, 120, 1.25
# the narrowest day that still carries its date and weekday
DAY_LABEL = 16
CF_ENHMETAFILE = 14
HEAD_ROW = 16
HEAD_H = 3 * HEAD_ROW
BAR_H = 12
EDGE = 4
HANDLE_R = 4
HANDLE_GAP = 7
LEAD_DAYS, TAIL_DAYS = 3, 7
START, DAYS, PCT, AFTER = range(4)
COLUMNS = (("Start", 64), ("Days", 38), ("%", 34), ("After", 54))
FIELDS_W = sum(w for _, w in COLUMNS)
# the task pane's opening width; only the chart takes up a window resize
LEFT_W = 360
MAX_DAYS = 3650
LINK_INK = QColor("#555555")
FONT = QFont("Tahoma", 8)
FONT_BOLD = QFont("Tahoma", 8, QFont.Weight.Bold)
FONT_BAR = QFont("Tahoma", 7)
FONT_LABEL = QFont("Tahoma", 7, QFont.Weight.Bold)
ALIGN_C = Qt.AlignmentFlag.AlignCenter
ALIGN_L = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
ALIGN_R = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
CTRL = Qt.KeyboardModifier.ControlModifier
ALT = Qt.KeyboardModifier.AltModifier
SHIFT = Qt.KeyboardModifier.ShiftModifier
# (modifiers, key) -> (days of start, days of duration)
NUDGE_KEYS = {
    (CTRL | ALT, Qt.Key.Key_Left): (-1, 0),
    (CTRL | ALT, Qt.Key.Key_Right): (1, 0),
    (CTRL | ALT | SHIFT, Qt.Key.Key_Left): (0, -1),
    (CTRL | ALT | SHIFT, Qt.Key.Key_Right): (0, 1),
    (CTRL, Qt.Key.Key_Left): (0, -1),
    (CTRL, Qt.Key.Key_Right): (0, 1),
    (SHIFT, Qt.Key.Key_Left): (-1, 0),
    (SHIFT, Qt.Key.Key_Right): (1, 0),
}
DAY = m.DAY
DATE_FMT = m.DEFAULT_SETTINGS["tl_date_fmt"]
# how each format shows a day; the numeric ones read a typed M/D or D/M in the same order
SHOWN = {
    "M/D": lambda d: f"{d.month}/{d.day}",
    "D/M": lambda d: f"{d.day}/{d.month}",
    "MM-DD": lambda d: f"{d.month:02d}-{d.day:02d}",
    "MMM D": lambda d: f"{m.MONTHS[d.month - 1]} {d.day}",
    "D MMM": lambda d: f"{d.day} {m.MONTHS[d.month - 1]}",
    "YYYY-MM-DD": datetime.date.isoformat,
}
DAY_FIRST = ("D/M", "D MMM")
MONTH_NAMES = tuple(
    "january february march april may june july august september october november december".split()
)
_Y = r"(\d{4}|\d{2})"
_SEP = re.compile(r"\s*(?:\.\.|[–—-]|(?<![a-z])to(?![a-z]))\s*")
_ISO = re.compile(r"(\d{4})([-/.]?)(\d{1,2})\2(\d{1,2})")
_NUM = re.compile(r"(\d{1,2})([/.])(\d{1,2})(?:(?:\2|\s+)" + _Y + ")?")
_MD = re.compile(r"(\d{1,2})-(\d{1,2})(?:\s+" + _Y + ")?")
_NAME_D = re.compile(r"([a-z]{3,})\s*(\d{1,2})(?:(?:,\s*|\s+)" + _Y + ")?")
_D_NAME = re.compile(r"(\d{1,2})\s*([a-z]{3,})(?:(?:,\s*|\s*)" + _Y + ")?")
# a month name joined to its day (and year) by one repeated - . or /: 01-OCT-26, oct.1.2026
_NAME_SEP = re.compile(r"([a-z]{3,})([./-])(\d{1,2})(?:\2" + _Y + ")?")
_D_NAME_SEP = re.compile(r"(\d{1,2})([./-])([a-z]{3,})(?:\2" + _Y + ")?")
# days from today: +3, -2, +3d, +2w
_REL = re.compile(r"([+-])(\d{1,3})([dw]?)")
REL_WORDS = {"today": 0, "tod": 0, "tomorrow": 1, "tmw": 1, "tmrw": 1, "yesterday": -1}
WEEKDAY_NAMES = tuple(
    "monday tuesday wednesday thursday friday saturday sunday".split()
)
_BARE = re.compile(r"(\d{1,2})(?:\s+" + _Y + ")?")
WEEKDAYS = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
# top / bottom / centre of a block's row in the destination's coordinates; info is its
# timeline row (see rdb_model.timeline_rows), None for a block that is not an item
Item = namedtuple("Item", "block top bottom cy info")


def _tone(light, dark):
    return QColor(dark if rdb_theme.dark() else light)


def _c(s, key):
    return QColor(m.shown(s, key))


@contextmanager
def light():
    # exports are documents: drawn in the light colours whatever the window shows
    was = rdb_theme.dark()
    rdb_theme.set_dark(False)
    _bind_tones()
    try:
        yield
    finally:
        rdb_theme.set_dark(was)
        _bind_tones()


def _bind_tones():
    global LINK_INK
    LINK_INK = _tone("#555555", "#a0a0a0")


def configure(settings):
    global HEAD_H, DATE_FMT
    _bind_tones()
    HEAD_H = (3 if settings["tl_weekdays"] else 2) * HEAD_ROW
    DATE_FMT = settings["tl_date_fmt"]


def _edit(doc, fn):
    c = QTextCursor(doc)
    c.beginEditBlock()
    try:
        fn()
    finally:
        c.endEditBlock()


def dated(doc):
    return {
        m.task_id(b): b
        for b in m.iter_blocks(doc)
        if m.task_start(b) is not None and m.task_id(b)
    }


def push(doc):
    # a dependent never starts before its predecessors end; it is pushed later, never pulled earlier
    tasks = dated(doc)
    for _ in range(len(tasks) + 1):
        moved = False
        for b in tasks.values():
            ends = [m.task_end(tasks[i]) for i in m.task_after(b) if i in tasks]
            if ends and m.task_start(b) < max(ends):
                m.set_meta(b, ts=max(ends).isoformat())
                moved = True
        if not moved:
            return
    raise RuntimeError("dependency cycle")


def _finish(doc):
    m.ensure_task_ids(doc)
    push(doc)


def set_task(doc, block, **fields):
    if "ts" in fields:
        if not fields["ts"]:
            fields = {**fields, "tn": 0, "tp": 0}
        elif m.task_start(block) is None:
            fields = {"tn": 1, **fields}

    def run():
        m.set_meta(block, **fields)
        _finish(doc)

    _edit(doc, run)


def nudge(doc, blocks, dstart, ddays):
    blocks = [b for b in blocks if m.task_start(b) is not None]

    def run():
        for b in blocks:
            m.set_meta(
                b,
                ts=(m.task_start(b) + dstart * DAY).isoformat(),
                tn=max(0, m.task_days(b) + ddays),
            )
        _finish(doc)

    if blocks:
        _edit(doc, run)


def _cyclic(tasks, after):
    # after: task id -> predecessor ids
    state = {}

    def visit(i):
        if state.get(i) == 1:
            return True
        if state.get(i) == 2:
            return False
        state[i] = 1
        # ponytail: recursion depth is the longest dependency chain; iterate if lists chain past ~900
        if any(visit(p) for p in after[i] if p in tasks):
            return True
        state[i] = 2
        return False

    return any(visit(i) for i in tasks)


def _graph(doc, block=None, preds=None):
    tasks = dated(doc)
    after = {i: m.task_after(b) for i, b in tasks.items()}
    if block is not None:
        after[m.task_id(block)] = preds
    return tasks, after


def link(doc, pred, dep):
    # pred ends before dep starts; False when that would make a cycle
    pi, di = m.task_id(pred), m.task_id(dep)
    if not (pi and di):
        raise ValueError("a linked task needs a start")
    if pi == di:
        return False
    have = m.task_after(dep)
    if pi in have:
        return True
    tasks, after = _graph(doc, dep, [*have, pi])
    if _cyclic(tasks, after):
        return False

    def run():
        m.set_meta(dep, ta=",".join(after[di]))
        _finish(doc)

    _edit(doc, run)
    return True


def unlink(doc, pred_id, dep):
    keep = [i for i in m.task_after(dep) if i != pred_id]
    _edit(doc, lambda: m.set_meta(dep, ta=",".join(keep)))


def task_numbers(doc):
    # the gutter number of every task, which is how the After column names them
    return {b.blockNumber(): n for b, n in m.numbered(doc, True) if m.is_todo(b)}


def after_text(doc, block):
    nums, byid = task_numbers(doc), {m.task_id(b): b for b in m.iter_blocks(doc)}
    return ",".join(
        str(n)
        for n in sorted(
            nums[byid[i].blockNumber()]
            for i in m.task_after(block)
            if i in byid and byid[i].blockNumber() in nums
        )
    )


def _month(name):
    return next(
        (i for i, full in enumerate(MONTH_NAMES, 1) if full.startswith(name)), None
    )


def _year(text):
    return None if text is None else int(text) + (2000 if len(text) == 2 else 0)


def _relative(text, today):
    # a word, an offset or a weekday name (the next one, today included), else None
    if text in REL_WORDS:
        return today + REL_WORDS[text] * DAY
    if x := _REL.fullmatch(text):
        n = int(x[2]) * (7 if x[3] == "w" else 1)
        return today + (n if x[1] == "+" else -n) * DAY
    if len(text) >= 3 and text.isalpha():
        wd = next((i for i, n in enumerate(WEEKDAY_NAMES) if n.startswith(text)), None)
        if wd is not None:
            return today + ((wd - today.weekday()) % 7) * DAY
    return None


def _side(text, fmt, today, bare=False):
    # (year or None, month or None, day) of one end of a range, None when it is no known shape
    if (day := _relative(text, today)) is not None:
        return day.year, day.month, day.day
    if x := _ISO.fullmatch(text):
        return int(x[1]), int(x[3]), int(x[4])
    if x := _NUM.fullmatch(text):
        a, b = int(x[1]), int(x[3])
        return _year(x[4]), *((b, a) if fmt in DAY_FIRST else (a, b))
    if fmt == "MM-DD" and (x := _MD.fullmatch(text)):
        return _year(x[3]), int(x[1]), int(x[2])
    if x := _NAME_D.fullmatch(text):
        return (_year(x[3]), mo, int(x[2])) if (mo := _month(x[1])) else None
    if x := _D_NAME.fullmatch(text):
        return (_year(x[3]), mo, int(x[1])) if (mo := _month(x[2])) else None
    if x := _NAME_SEP.fullmatch(text):
        return (_year(x[4]), mo, int(x[3])) if (mo := _month(x[1])) else None
    if x := _D_NAME_SEP.fullmatch(text):
        return (_year(x[4]), mo, int(x[1])) if (mo := _month(x[3])) else None
    if bare and (x := _BARE.fullmatch(text)):
        return _year(x[2]), None, int(x[1])
    return None


def parse_dates(text, fmt, today):
    # (start, end or None) of a date or a range; a shape it does not know is refused, never guessed
    if fmt not in SHOWN:
        raise ValueError(f"date format {fmt!r}")
    text = text.strip().lower()
    if one := _side(text, fmt, today):
        return datetime.date(one[0] or today.year, one[1], one[2]), None
    for cut in _SEP.finditer(text):
        a = _side(text[: cut.start()], fmt, today)
        b = _side(text[cut.end() :], fmt, today, True)
        if a and b:
            (y1, m1, d1), (y2, m2, d2) = a, b
            start = datetime.date(y1 or y2 or today.year, m1, d1)
            end = datetime.date(y2 or y1 or today.year, m2 or m1, d2)
            if end < start:
                # a stated year is never second-guessed; without one the end is next year's
                if y1 or y2:
                    raise ValueError(f"{text!r} ends before it starts")
                end = end.replace(year=end.year + 1)
            return start, end
    raise ValueError(f"{text!r} is not a date or a range")


def _whole(text, lo, hi):
    if not re.fullmatch(r"\d+", text.strip()) or not lo <= int(text) <= hi:
        raise ValueError(f"{text!r} is not {lo} to {hi}")
    return int(text)


def _typed(text):
    return parse_dates(text, DATE_FMT, datetime.date.fromisoformat(m.today()))


def _span(start, end):
    n = (end - start).days + 1
    if not 1 <= n <= MAX_DAYS:
        raise ValueError(f"{n} days")
    return n


def _set_dates(doc, block, start, end):
    fields = {"ts": start.isoformat()}
    if end is not None:
        fields["tn"] = _span(start, end)
    set_task(doc, block, **fields)


def set_field(doc, block, col, text, ranged=False):
    # one typed cell of a task row; ValueError leaves the document alone. ranged: the bar's
    # days box, where a range sets the start as well
    text = text.strip()
    if not m.is_todo(block):
        raise ValueError("only a task has fields")
    if col == START:
        if text:
            _set_dates(doc, block, *_typed(text))
        else:
            set_task(doc, block, ts="")
        return
    if m.task_start(block) is None:
        raise ValueError("no start")
    if col == DAYS:
        # a signed number here is a bad count, never a day relative to today
        if re.fullmatch(r"[+-]?\d+", text):
            set_task(doc, block, tn=_whole(text, 0, MAX_DAYS))
        else:
            start, end = _typed(text)
            if end is None:
                set_task(doc, block, tn=_span(m.task_start(block), start))
            elif ranged:
                _set_dates(doc, block, start, end)
            else:
                raise ValueError(f"{text!r} is a range")
    elif col == PCT:
        set_task(doc, block, tp=_whole(text, 0, 100))
    elif col == AFTER:
        nums = {n: b for b, n in m.numbered(doc, True) if m.is_todo(b)}
        preds = []
        for part in filter(None, (t.strip() for t in text.split(","))):
            b = nums.get(_whole(part, 1, len(nums) or 1))
            if b is None or b == block or m.task_start(b) is None or not m.task_id(b):
                raise ValueError(f"{part!r} is not a task with a start")
            preds.append(m.task_id(b))
        tasks, after = _graph(doc, block, preds)
        if _cyclic(tasks, after):
            raise ValueError("dependency cycle")
        _edit(doc, lambda: (m.set_meta(block, ta=",".join(preds)), _finish(doc)))
    else:
        raise ValueError(f"column {col}")


def start_text(day):
    return SHOWN[DATE_FMT](day)


def cell_text(doc, block, col):
    if not m.is_todo(block) or m.task_start(block) is None:
        return ""
    if col == START:
        return start_text(m.task_start(block))
    if col == DAYS:
        return str(m.task_days(block))
    if col == PCT:
        return str(m.get_meta(block).get("tp", 0))
    return after_text(doc, block)


def luma_ink(base):
    return QColor("#ffffff") if base.lightness() < 140 else QColor("#202020")


def day_bg(s, day):
    if s["tl_today_fill"] and day.isoformat() == m.today():
        return _c(s, "tl_today_bg")
    return _c(s, "tl_weekend_bg" if day.weekday() >= 5 else "tl_day_bg")


def day_line(s, day):
    c = _c(s, "tl_grid_color")
    return c.darker(115) if day is not None and day.weekday() == 0 else c


class GanttView(QAbstractScrollArea):
    # a bar's days to edit in place: its block and the box over the bar
    days_requested = Signal(object, QRect)

    def __init__(self, editor, settings):
        super().__init__()
        self.ed, self.settings = editor, settings
        self._origin, self._doc = None, None
        self._drag = None
        self._hover = -1
        self.day_w = DAY_W
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setFrameShape(QFrame.Shape.NoFrame)
        # the keys stay with the editor beside it (vim, moves, nudges)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.viewport().setMouseTracking(True)
        self.horizontalScrollBar().valueChanged.connect(self.viewport().update)

    # geometry

    @property
    def _z(self):
        return self.ed.zoom

    def _bar_h(self):
        return BAR_H * self._z

    def frame(self, at_least=0):
        # first shown day and the number of days the chart spans
        first = last = None
        for b in m.iter_blocks(self.ed.document()):
            start = m.task_start(b)
            if start is None:
                continue
            end = start + max(m.task_days(b), 1) * DAY
            first = start if first is None else min(first, start)
            last = end if last is None else max(last, end)
        today = datetime.date.fromisoformat(m.today())
        # today is always in range, with room after it to scroll it to the first column
        first = today if first is None else min(first, today)
        last = today + DAY if last is None else max(last, today + DAY)
        origin = first - LEAD_DAYS * DAY
        days = (last + TAIL_DAYS * DAY - origin).days
        room = (today - origin).days + math.ceil(at_least / self.day_w)
        return origin, max(days, room)

    def go_today(self):
        self.refresh()
        origin, _ = self.frame(self.viewport().width())
        today = datetime.date.fromisoformat(m.today())
        self.horizontalScrollBar().setValue((today - origin).days * self.day_w)

    def refresh(self):
        doc = self.ed.document()
        origin, days = self.frame(self.viewport().width())
        bar = self.horizontalScrollBar()
        if doc is not self._doc:
            self._doc, self._origin = doc, None
            bar.setValue(0)
        elif self._origin is not None and origin != self._origin:
            # an earlier first day moves every bar right; the view keeps the date it shows
            bar.setValue(bar.value() + (self._origin - origin).days * self.day_w)
        self._origin = origin
        bar.setRange(0, max(0, days * self.day_w - self.viewport().width()))
        bar.setPageStep(self.viewport().width())
        self.viewport().update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.refresh()

    def zoom_time(self, up, x=None):
        # wider or narrower days, the date under x (default the middle) staying put
        x = self.viewport().width() / 2 if x is None else x
        bar = self.horizontalScrollBar()
        at = (bar.value() + x) / self.day_w
        w = self.day_w * DAY_W_STEP if up else self.day_w / DAY_W_STEP
        # a step always moves at least a pixel, so the narrow end still zooms
        w = round(w) if round(w) != self.day_w else self.day_w + (1 if up else -1)
        self.day_w = min(max(w, DAY_W_MIN), DAY_W_MAX)
        self.refresh()
        bar.setValue(round(at * self.day_w - x))
        self.viewport().update()

    def day_rect(self, day):
        # the column of a day, in viewport coordinates
        origin, _ = self.frame(self.viewport().width())
        x = (day - origin).days * self.day_w - self.horizontalScrollBar().value()
        return QRect(x, 0, self.day_w, self.viewport().height())

    def items(self, y0=None, scroll=None, drag=True):
        y0 = HEAD_H if y0 is None else y0
        scroll = self.ed.verticalScrollBar().value() if scroll is None else scroll
        doc = self.ed.document()
        info = {b.blockNumber(): r for b, r in m.timeline_rows(doc)}
        out = []
        for r in self.ed.row_geoms():
            i = info.get(r.block.blockNumber())
            if drag and self._drag is not None and i is not None:
                i = self._dragged(r.block, i)
            out.append(
                Item(
                    r.block,
                    y0 + r.top - scroll,
                    y0 + r.bottom - scroll,
                    y0 + r.cy - scroll,
                    i,
                )
            )
        return out

    def _dragged(self, block, info):
        kind, b, delta = self._drag["kind"], self._drag["block"], self._drag["delta"]
        if b.blockNumber() != block.blockNumber() or info["start"] is None:
            return info
        if kind == "move":
            return {**info, "start": info["start"] + delta * DAY}
        if kind == "resize":
            return {**info, "days": max(0, info["days"] + delta)}
        return info

    def bar_rect(self, block, items=None, origin=None, xoff=None):
        for it in self.items() if items is None else items:
            if it.block.blockNumber() == block.blockNumber():
                return self._bar(it, origin, xoff)
        return None

    def _bar(self, it, origin=None, xoff=None):
        i = it.info
        if i is None or i["start"] is None or i["level"]:
            return None
        origin = self.frame(self.viewport().width())[0] if origin is None else origin
        xoff = -self.horizontalScrollBar().value() if xoff is None else xoff
        h = self._bar_h()
        return QRectF(
            xoff + (i["start"] - origin).days * self.day_w,
            it.cy - h / 2,
            max(i["days"], 1) * self.day_w,
            h,
        )

    def _links(self, items, origin, xoff):
        # (predecessor block, dependent block, path) of every arrow
        by_id = {
            m.task_id(it.block): it for it in items if it.info and not it.info["level"]
        }
        out = []
        for it in items:
            if not it.info or it.info["start"] is None or it.info["level"]:
                continue
            dep = self._bar(it, origin, xoff)
            for i in m.task_after(it.block):
                pit = by_id.get(i)
                pred = None if pit is None else self._bar(pit, origin, xoff)
                if pred is None:
                    continue
                out.append((pit.block, it.block, _elbow(pred, dep)))
        return out

    # painting

    def paintEvent(self, e):
        p = QPainter(self.viewport())
        origin, days = self.frame(self.viewport().width())
        p.fillRect(self.viewport().rect(), _c(self.settings, "editor_bg"))
        self.paint_chart(
            p,
            self.viewport().width(),
            self.viewport().height(),
            -self.horizontalScrollBar().value(),
            self.items(),
            origin,
            days,
        )
        p.end()

    def paint_chart(self, p, w, h, xoff, items, origin, days, here=None):
        s = self.settings
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        first = max(0, (-xoff) // self.day_w)
        last = min(days, (w - xoff) // self.day_w + 1)
        today = datetime.date.fromisoformat(m.today())
        p.save()
        p.setClipRect(0, HEAD_H, w, max(h - HEAD_H, 0))
        for n in range(first, last):
            x = xoff + n * self.day_w
            p.fillRect(
                x,
                HEAD_H,
                self.day_w,
                h - HEAD_H,
                day_bg(self.settings, origin + n * DAY),
            )
        if here is None:
            here = self.ed.textCursor().block().blockNumber()
        for it in items:
            if it.bottom < HEAD_H or it.top > h:
                continue
            if it.info and it.info["level"] == 1:
                tint = _c(s, "h1_bg")
                tint.setAlpha(140)
                p.fillRect(QRectF(0, it.top, w, it.bottom - it.top), tint)
            if it.block.blockNumber() == here:
                p.fillRect(
                    QRectF(0, it.top, w, it.bottom - it.top), rdb_theme.color("row_now")
                )
        if s["tl_grid"]:
            for n in range(first, last):
                day = origin + n * DAY
                if not self._ruled(day):
                    continue
                p.fillRect(
                    xoff + n * self.day_w,
                    HEAD_H,
                    1,
                    h - HEAD_H,
                    day_line(self.settings, day),
                )
            for it in items:
                p.fillRect(0, int(it.bottom) - 1, w, 1, day_line(self.settings, None))
        for y, rh in self.h2_rules(items):
            p.fillRect(QRectF(0, y, w, rh), _c(s, "h2_rule"))
        if s["tl_today_line"]:
            tx = xoff + (today - origin).days * self.day_w + self.day_w // 2
            p.fillRect(tx, HEAD_H, 1, h - HEAD_H, _c(s, "card_select_border"))
        self._paint_links(p, items, origin, xoff)
        for it in items:
            if it.bottom < HEAD_H or it.top > h or it.info is None:
                continue
            if it.info["level"]:
                self._paint_bracket(p, it, origin, xoff)
            else:
                self._paint_bar(p, it, origin, xoff)
        p.restore()
        self._paint_head(p, w, xoff, first, last, origin, today)

    def _ruled(self, day):
        # a line per day while days are wide enough to tell apart, else a line per week
        return self.day_w >= 6 or day.weekday() == 0

    def _paint_head(self, p, w, xoff, first, last, origin, today):
        p.fillRect(0, 0, w, HEAD_H, rdb_theme.color("head_bg"))
        num_y = HEAD_H - HEAD_ROW
        for n in range(first, last):
            day = origin + n * DAY
            x = xoff + n * self.day_w
            p.fillRect(
                x, HEAD_ROW, self.day_w, HEAD_H - HEAD_ROW, day_bg(self.settings, day)
            )
            if self.settings["tl_grid"] and self._ruled(day):
                p.fillRect(
                    x, HEAD_ROW, 1, HEAD_H - HEAD_ROW, day_line(self.settings, day)
                )
            p.setPen(_tone("#404040", "#c8c8c8"))
            p.setFont(FONT)
            if self.day_w >= DAY_LABEL:
                p.drawText(QRect(x, num_y, self.day_w, HEAD_ROW), ALIGN_C, str(day.day))
            if self.settings["tl_weekdays"] and self.day_w >= DAY_LABEL:
                p.setFont(FONT_BAR)
                p.drawText(
                    QRect(x, HEAD_ROW, self.day_w, HEAD_ROW),
                    ALIGN_C,
                    WEEKDAYS[day.weekday()],
                )
        if self.day_w < DAY_LABEL <= 7 * self.day_w:
            # too narrow for every day: the Mondays carry the date, the week as room, drawn
            # once every day's background is down
            p.setPen(_tone("#404040", "#c8c8c8"))
            p.setFont(FONT)
            for n in range(first, last):
                day = origin + n * DAY
                if day.weekday() == 0:
                    p.drawText(
                        QRect(
                            xoff + n * self.day_w + 2,
                            num_y,
                            7 * self.day_w - 2,
                            HEAD_ROW,
                        ),
                        ALIGN_L,
                        str(day.day),
                    )
        p.setPen(rdb_theme.color("head_rule"))
        p.drawLine(0, HEAD_ROW, w, HEAD_ROW)
        p.drawLine(0, HEAD_H - 1, w, HEAD_H - 1)
        p.setFont(FONT)
        fm = QFontMetrics(FONT)
        month = (origin + first * DAY).replace(day=1)
        while True:
            nxt = (month + 31 * DAY).replace(day=1)
            left = xoff + (month - origin).days * self.day_w
            right = xoff + (nxt - origin).days * self.day_w
            p.setPen(rdb_theme.color("head_rule"))
            p.drawLine(left, 0, left, HEAD_H)
            room = right - max(left, 0) - 6
            if room > 0:
                p.setPen(_tone("#202020", "#e8e8e8"))
                p.drawText(
                    QRect(max(left, 0) + 4, 0, room, HEAD_ROW),
                    ALIGN_L,
                    fm.elidedText(
                        f"{m.MONTHS[month.month - 1]} {month.year}",
                        Qt.TextElideMode.ElideRight,
                        room,
                    ),
                )
            if right >= w:
                break
            month = nxt
        if self.settings["tl_today_line"]:
            tx = xoff + (today - origin).days * self.day_w + self.day_w // 2
            p.fillRect(tx, 0, 1, HEAD_H, _c(self.settings, "card_select_border"))

    def _base(self, info):
        return (
            rdb_theme.shade(info["bar"])
            if info["bar"]
            else _c(self.settings, "frame_color")
        )

    def _paint_bar(self, p, it, origin, xoff):
        r = self._bar(it, origin, xoff)
        if r is None:
            return
        i, base = it.info, self._base(it.info)
        p.save()
        p.setOpacity(0.4 if i["done"] else 1.0)
        p.setPen(QPen(base.darker(150), 1))
        if i["days"] == 0:
            c = QPointF(r.left() + self.day_w / 2, r.center().y())
            d = r.height() / 2 + 1
            p.setBrush(base)
            p.drawPolygon(
                QPolygonF(
                    [
                        QPointF(c.x(), c.y() - d),
                        QPointF(c.x() + d, c.y()),
                        QPointF(c.x(), c.y() + d),
                        QPointF(c.x() - d, c.y()),
                    ]
                )
            )
            p.restore()
            return
        body = r.adjusted(0.5, 0.5, -0.5, -0.5)
        p.setBrush(base)
        p.drawRoundedRect(body, 3, 3)
        if i["pct"]:
            clip = QPainterPath()
            clip.addRoundedRect(body, 3, 3)
            p.save()
            p.setClipPath(clip, Qt.ClipOperation.IntersectClip)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(base.darker(145))
            p.drawRect(
                QRectF(r.left(), r.top(), r.width() * i["pct"] / 100, r.height())
            )
            p.restore()
        p.setFont(FONT_LABEL)
        label = f"{i['days']}d"
        if QFontMetrics(FONT_LABEL).horizontalAdvance(label) + 4 <= r.width():
            p.setPen(
                rdb_theme.shade(i["fg"])
                if i["fg"]
                else luma_ink(base.darker(145) if i["pct"] >= 50 else base)
            )
            p.drawText(r, ALIGN_C, label)
        else:
            p.setPen(
                rdb_theme.shade(i["fg"]) if i["fg"] else _tone("#404040", "#c8c8c8")
            )
            p.drawText(QRectF(r.right() + 4, r.top(), 40, r.height()), ALIGN_L, label)
        if self._hover == it.block.blockNumber():
            p.setPen(QPen(base.darker(150), 1))
            p.setBrush(_tone("#ffffff", "#2b2b2b"))
            p.drawEllipse(self._handle(r), HANDLE_R, HANDLE_R)
        p.restore()

    @staticmethod
    def _handle(r):
        return QPointF(r.right() + HANDLE_GAP, r.center().y())

    def _paint_bracket(self, p, it, origin, xoff):
        i = it.info
        if i["start"] is None:
            return
        x = xoff + (i["start"] - origin).days * self.day_w
        w = max(i["days"], 1) * self.day_w
        ink = (
            _tone("#2a2a2a", "#e0e0e0")
            if i["level"] == 1
            else _tone("#5a5a5a", "#a8a8a8")
        )
        t = 3 if i["level"] == 1 else 2
        c = it.cy
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(ink)
        p.drawPolygon(
            QPolygonF(
                [
                    QPointF(x, c - 5),
                    QPointF(x + w, c - 5),
                    QPointF(x + w, c + 4),
                    QPointF(x + w - 4, c - 5 + t),
                    QPointF(x + 4, c - 5 + t),
                    QPointF(x, c + 4),
                ]
            )
        )

    def _paint_links(self, p, items, origin, xoff):
        p.save()
        p.setPen(QPen(LINK_INK, 1.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        for _, _, path in self._links(items, origin, xoff):
            p.drawPath(path)
            end = path.currentPosition()
            p.save()
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(LINK_INK)
            p.drawPolygon(
                QPolygonF(
                    [
                        end,
                        QPointF(end.x() - 5, end.y() - 3),
                        QPointF(end.x() - 5, end.y() + 3),
                    ]
                )
            )
            p.restore()
        p.restore()

    # mouse

    def h2_rules(self, items):
        # (top, height) of the rule under each H2, where the editor draws it, for the rows beside
        s, z, out = self.settings, self._z, []
        for it in items:
            if it.info and it.info["level"] == 2:
                g = self.ed._geom(it.block)
                out.append((round(g.bottom) + z + it.cy - g.cy, s["h2_rule_px"] * z))
        return out

    def mouseDoubleClickEvent(self, e):
        pos = e.position()
        if pos.y() < HEAD_H:
            return
        origin = self.frame(self.viewport().width())[0]
        xoff = -self.horizontalScrollBar().value()
        _, it = self._hit(pos)
        if it is not None and not it.info["level"]:
            r = self._bar(it, origin, xoff).toRect()
            self.days_requested.emit(
                it.block,
                QRect(r.left(), r.top() - 3, max(r.width(), 40), r.height() + 6),
            )
            return
        row = next(
            (
                i
                for i in self.items()
                if i.top <= pos.y() < i.bottom and i.info and not i.info["level"]
            ),
            None,
        )
        if row is None:
            return
        day = origin + int((pos.x() - xoff) // self.day_w) * DAY
        set_task(self.ed.document(), row.block, ts=day.isoformat())
        self.ed.setTextCursor(QTextCursor(row.block))
        self.ed.setFocus()

    def _hit(self, pos):
        items = self.items()
        origin = self.frame(self.viewport().width())[0]
        xoff = -self.horizontalScrollBar().value()
        for it in items:
            r = self._bar(it, origin, xoff)
            if r is None:
                continue
            if (pos - self._handle(r)).manhattanLength() <= 2 * HANDLE_R:
                return "link", it
            if r.adjusted(-2, 0, 2, 0).contains(pos):
                return ("resize" if pos.x() >= r.right() - EDGE else "move"), it
        return None, None

    def mouseMoveEvent(self, e):
        pos = e.position()
        if self._drag is not None:
            d = self._drag
            d["pos"] = pos
            d["moved"] = d["moved"] or abs(pos.x() - d["x0"]) > 2
            if d["kind"] != "link":
                d["delta"] = round((pos.x() - d["x0"]) / self.day_w)
            self.viewport().update()
            return
        kind, it = self._hit(pos)
        n = it.block.blockNumber() if it is not None else -1
        self.viewport().setCursor(
            {
                "link": Qt.CursorShape.CrossCursor,
                "resize": Qt.CursorShape.SizeHorCursor,
                "move": Qt.CursorShape.SizeAllCursor,
            }.get(kind, Qt.CursorShape.ArrowCursor)
        )
        if n != self._hover:
            self._hover = n
            self.viewport().update()

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        kind, it = self._hit(e.position())
        if it is None:
            return
        self._drag = {
            "kind": kind,
            "block": it.block,
            "x0": e.position().x(),
            "pos": e.position(),
            "delta": 0,
            "moved": False,
        }

    def mouseReleaseEvent(self, e):
        d, self._drag = self._drag, None
        if d is None or e.button() != Qt.MouseButton.LeftButton:
            return
        doc, block = self.ed.document(), d["block"]
        if d["kind"] == "link":
            kind, it = self._hit(e.position())
            if it is not None and it.block.blockNumber() != block.blockNumber():
                link(doc, block, it.block)
        elif d["delta"]:
            if d["kind"] == "move":
                start = m.task_start(block) + d["delta"] * DAY
                set_task(doc, block, ts=start.isoformat())
            else:
                set_task(doc, block, tn=max(0, m.task_days(block) + d["delta"]))
        if not d["moved"]:
            self.ed.setTextCursor(QTextCursor(block))
            self.ed.setFocus()
        self.viewport().update()

    def arrow_at(self, pos):
        items = self.items()
        origin = self.frame(self.viewport().width())[0]
        xoff = -self.horizontalScrollBar().value()
        stroke = QPainterPathStroker()
        stroke.setWidth(6)
        for pred, dep, path in self._links(items, origin, xoff):
            if stroke.createStroke(path).contains(pos):
                return pred, dep
        return None

    def link_menu(self, pos):
        hit = self.arrow_at(QPointF(pos))
        if hit is None:
            return None
        pred, dep = hit
        menu = QMenu(self)
        menu.addAction("Remove link").triggered.connect(
            lambda: unlink(self.ed.document(), m.task_id(pred), dep)
        )
        return menu

    def contextMenuEvent(self, e):
        menu = self.link_menu(e.pos())
        if menu is not None:
            menu.exec(e.globalPos())
            menu.deleteLater()

    def wheelEvent(self, e):
        if e.modifiers() & CTRL:
            if dy := e.angleDelta().y():
                self.zoom_time(dy > 0, e.position().x())
            e.accept()
        elif e.angleDelta().x() or e.modifiers() & SHIFT:
            super().wheelEvent(e)
        else:
            self.ed.wheelEvent(e)

    def leaveEvent(self, e):
        super().leaveEvent(e)
        if self._hover != -1 and self._drag is None:
            self._hover = -1
            self.viewport().update()


def _elbow(a, b):
    # from the right edge of the predecessor to the left edge of the dependent
    x1, y1, x2, y2 = a.right(), a.center().y(), b.left(), b.center().y()
    path = QPainterPath(QPointF(x1, y1))
    if x2 >= x1 + 8:
        for pt in ((x1 + 6, y1), (x1 + 6, y2), (x2, y2)):
            path.lineTo(*pt)
    else:
        mid = (y1 + y2) / 2
        for pt in ((x1 + 6, y1), (x1 + 6, mid), (x2 - 6, mid), (x2 - 6, y2), (x2, y2)):
            path.lineTo(*pt)
    return path


class DatePopup(QFrame):
    # a month calendar under a field. A click or Enter picks the start; Shift+click or Shift+Enter
    # on a later date picks the end of the range that begins at the start it opened with.
    picked = Signal(object, object)

    def __init__(self, parent, settings):
        super().__init__(parent, Qt.WindowType.Popup)
        self.settings, self._start, self._shift = settings, None, False
        self.cal = QCalendarWidget(self)
        self.cal.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
        self.cal.setVerticalHeaderFormat(
            QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader
        )
        weekend = QTextCharFormat()
        weekend.setBackground(QBrush(_c(settings, "tl_weekend_bg")))
        for wd in (Qt.DayOfWeek.Saturday, Qt.DayOfWeek.Sunday):
            self.cal.setWeekdayTextFormat(wd, weekend)
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.addWidget(self.cal)
        self.view = self.cal.findChild(QTableView)
        for w in (self.view, self.view.viewport()):
            w.installEventFilter(self)
        self.cal.clicked.connect(self._pick)
        self.cal.activated.connect(self._pick)
        self.cal.selectionChanged.connect(self._mark)

    def popup_at(self, pos, start):
        # pos: where the top left goes, in screen coordinates
        self._start, self._shift = start, False
        day = start or datetime.date.fromisoformat(m.today())
        self.cal.setSelectedDate(QDate(day.year, day.month, day.day))
        self._mark()
        self.move(pos)
        self.show()
        self.view.setFocus()

    def eventFilter(self, obj, e):
        if e.type() in (
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseButtonRelease,
            QEvent.Type.KeyPress,
            QEvent.Type.KeyRelease,
        ):
            self._shift = bool(e.modifiers() & SHIFT)
        if e.type() == QEvent.Type.KeyPress and e.key() == Qt.Key.Key_Escape:
            self.hide()
            return True
        return False

    def _mark(self):
        cal, today = self.cal, datetime.date.fromisoformat(m.today())
        cal.setDateTextFormat(QDate(), QTextCharFormat())
        now = QTextCharFormat()
        now.setFontWeight(QFont.Weight.Bold)
        now.setBackground(QBrush(_c(self.settings, "tl_today_bg")))
        cal.setDateTextFormat(QDate(today.year, today.month, today.day), now)
        if self._start is None:
            return
        end = cal.selectedDate().toPython() if self._shift else self._start
        lo, hi = sorted((self._start, end))
        for i in range((hi - lo).days + 1):
            d = lo + i * DAY
            fmt = QTextCharFormat()
            fmt.setBackground(QBrush(rdb_theme.color("range_bg")))
            if d == today:
                fmt.setFontWeight(QFont.Weight.Bold)
            cal.setDateTextFormat(QDate(d.year, d.month, d.day), fmt)

    def _pick(self, qdate):
        if not self.isVisible():
            return
        day = qdate.toPython()
        if self._shift:
            if self._start is None or day < self._start:
                return
            start, end = self._start, day
        else:
            start, end = day, None
        self.hide()
        self.picked.emit(start, end)


class CellEdit(QLineEdit):
    cancelled = Signal()
    calendar_requested = Signal()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
            e.accept()
            return
        if e.key() == Qt.Key.Key_F4 or (
            e.key() == Qt.Key.Key_Down and e.modifiers() & ALT
        ):
            self.calendar_requested.emit()
            e.accept()
            return
        super().keyPressEvent(e)

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        # the calendar popup takes the focus while the edit stays open
        if e.reason() != Qt.FocusReason.PopupFocusReason:
            self.cancelled.emit()


class FieldColumns(QWidget):
    def __init__(self, gantt, editor, settings):
        super().__init__()
        self.gantt, self.ed, self.settings = gantt, editor, settings
        self.col = START
        self._at = None
        # the bar days box also takes a range
        self._bar = False
        self.edit = CellEdit(self)
        self.edit.hide()
        self.edit.returnPressed.connect(self.commit)
        self.edit.cancelled.connect(self.cancel)
        self.edit.calendar_requested.connect(self.calendar_from_edit)
        self.cal = DatePopup(self, settings)
        self.cal.picked.connect(self.dates_picked)
        self._cal_row = None
        # a click on a Start cell waits out a double click, which edits instead
        self._click = QTimer(self)
        self._click.setSingleShot(True)
        self._click.timeout.connect(self.calendar_from_click)
        self.setFixedWidth(FIELDS_W)
        # F2 reaches edit_current through the editor, which keeps the focus
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def col_x(self, col):
        return sum(w for _, w in COLUMNS[:col])

    def items(self):
        return self.gantt.items()

    def cell_rect(self, it, col):
        return QRect(self.col_x(col), int(it.cy) - 10, COLUMNS[col][1], 20)

    def cell_at(self, pos):
        for it in self.items():
            if not (it.info and not it.info["level"]):
                continue
            for col in range(len(COLUMNS)):
                if self.cell_rect(it, col).contains(pos):
                    return it.block, col
        return None

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), _c(self.settings, "editor_bg"))
        self.paint_cols(p, self.width(), self.height(), self.items())
        p.end()

    def paint_cols(self, p, w, h, items, here=None):
        s = self.settings
        doc = self.ed.document()
        if here is None:
            here = self.ed.textCursor().block().blockNumber()
        p.setFont(FONT)
        for it in items:
            if it.bottom < HEAD_H or it.top > h:
                continue
            if self.settings["tl_grid"]:
                p.fillRect(0, int(it.bottom) - 1, w, 1, _c(s, "tl_grid_color"))
            if it.info and it.info["level"] == 1:
                tint = _c(s, "h1_bg")
                tint.setAlpha(140)
                p.fillRect(QRectF(0, it.top, w, it.bottom - it.top), tint)
            if it.block.blockNumber() == here:
                p.fillRect(
                    QRectF(0, it.top, w, it.bottom - it.top), rdb_theme.color("row_now")
                )
            if not (it.info and not it.info["level"]):
                continue
            p.setPen(_tone("#303030", "#d0d0d0"))
            for col in range(len(COLUMNS)):
                p.drawText(
                    self.cell_rect(it, col).adjusted(2, 0, -4, 0),
                    ALIGN_R if col != AFTER else ALIGN_L,
                    cell_text(doc, it.block, col),
                )
        p.setPen(_c(s, "tl_grid_color"))
        for col in range(1, len(COLUMNS)):
            p.drawLine(self.col_x(col), HEAD_H, self.col_x(col), h)
        for y, rh in self.gantt.h2_rules(items):
            p.fillRect(QRectF(0, y, w, rh), _c(s, "h2_rule"))
        p.fillRect(0, 0, w, HEAD_H, rdb_theme.color("head_bg"))
        p.setPen(rdb_theme.color("head_rule"))
        p.drawLine(0, HEAD_H - 1, w, HEAD_H - 1)
        p.drawLine(w - 1, 0, w - 1, h)
        # the H2 rule runs on over the column border
        for y, rh in self.gantt.h2_rules(items):
            p.fillRect(QRectF(w - 1, y, 1, rh), _c(s, "h2_rule"))
        p.setFont(FONT_BOLD)
        p.setPen(_tone("#303030", "#d0d0d0"))
        for col, (name, cw) in enumerate(COLUMNS):
            p.drawText(
                QRect(self.col_x(col), HEAD_H - HEAD_ROW, cw, HEAD_ROW), ALIGN_C, name
            )

    def mousePressEvent(self, e):
        hit = self.cell_at(e.position().toPoint())
        self._click.stop()
        if hit is not None:
            self.col = hit[1]
            self.ed.setTextCursor(QTextCursor(hit[0]))
            if hit[1] == START:
                self._cal_row = hit[0].blockNumber()
                self._click.start(QApplication.doubleClickInterval())
        self.cancel()
        self.update()

    def mouseDoubleClickEvent(self, e):
        hit = self.cell_at(e.position().toPoint())
        if hit is not None:
            self.edit_cell(*hit)

    def calendar_from_click(self):
        block = self.ed.document().findBlockByNumber(self._cal_row)
        it = next(i for i in self.items() if i.block.blockNumber() == self._cal_row)
        self.open_calendar(
            block, self.mapToGlobal(self.cell_rect(it, START).bottomLeft())
        )

    def calendar_from_edit(self):
        if self._at is not None and self._at[1] == START:
            self._cal_row = self._at[0]
            self.open_calendar(
                self.ed.document().findBlockByNumber(self._cal_row),
                self.edit.mapToGlobal(self.edit.rect().bottomLeft()),
            )

    def open_calendar(self, block, pos):
        self.cal.popup_at(pos, m.task_start(block))

    def dates_picked(self, start, end):
        doc = self.ed.document()
        _set_dates(doc, doc.findBlockByNumber(self._cal_row), start, end)
        self.cancel()
        self.ed.setFocus()

    def edit_current(self):
        block = self.ed.textCursor().block()
        if self.settings["tl_fields"] and m.is_todo(block):
            self.edit_cell(block, self.col)

    def edit_cell(self, block, col, host=None, rect=None):
        # host / rect: the box sits over a bar in the chart instead of in its column
        if rect is None:
            it = next(
                i for i in self.items() if i.block.blockNumber() == block.blockNumber()
            )
            rect = self.cell_rect(it, col)
        self._click.stop()
        self.cal.hide()
        self._at = (block.blockNumber(), col)
        self._bar = host is not None
        self.col = col
        self.edit.setParent(host or self)
        self.edit.setStyleSheet("")
        self.edit.setGeometry(rect)
        self.edit.setText(cell_text(self.ed.document(), block, col))
        self.edit.show()
        self.edit.setFocus()
        self.edit.selectAll()

    def commit(self):
        doc = self.ed.document()
        n, col = self._at
        try:
            set_field(doc, doc.findBlockByNumber(n), col, self.edit.text(), self._bar)
        except ValueError:
            self.edit.setStyleSheet(rdb_theme.error(_tone("#d42020", "#ff6b6b").name()))
            return False
        self.cancel()
        return True

    def cancel(self):
        self._at = None
        # focus goes back only from the open field; one lost to another window stays there
        typing = self.edit.hasFocus()
        if not self.edit.isHidden():
            self.edit.blockSignals(True)
            self.edit.hide()
            self.edit.blockSignals(False)
        if typing:
            self.ed.setFocus()
        self.update()


class Corner(QFrame):
    def __init__(self):
        super().__init__()
        self.setFixedHeight(HEAD_H)
        rdb_theme.follow(self, Corner._sheet)

    def _sheet(self):
        self.setStyleSheet(
            f"background: {rdb_theme.text('head_bg')}; "
            f"border-bottom: 1px solid {rdb_theme.text('head_rule')};"
        )


def prefs():
    # the same ini as rdb.app_settings; importing rdb here would be circular
    return rdb_paths.settings()


class _Handle(QSplitterHandle):
    # the H2 rules cross the gap between the panes
    def paintEvent(self, e):
        super().paintEvent(e)
        body = self.splitter()
        p = QPainter(self)
        for y, rh in body.gantt.h2_rules(body.gantt.items()):
            p.fillRect(QRectF(0, y, self.width(), rh), _c(body.settings, "h2_rule"))
        p.end()


class TimelineBody(QSplitter):
    def __init__(self, editor, settings):
        super().__init__(Qt.Orientation.Horizontal)
        self.ed, self.settings = editor, settings
        self.left = QWidget()
        col = QVBoxLayout(self.left)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        self.corner = Corner()
        col.addWidget(self.corner)
        self.gantt = GanttView(editor, settings)
        self.fields = FieldColumns(self.gantt, editor, settings)
        self.gantt.days_requested.connect(
            lambda block, rect: self.fields.edit_cell(
                block, DAYS, self.gantt.viewport(), rect
            )
        )
        for w in (self.left, self.fields, self.gantt):
            self.addWidget(w)
        self.setChildrenCollapsible(False)
        self.setStretchFactor(0, 0)
        self.setStretchFactor(1, 0)
        self.setStretchFactor(2, 1)
        left = prefs().value("timeline/left_w", LEFT_W, int)
        self.setSizes([left, FIELDS_W, 800])
        self.splitterMoved.connect(
            lambda: prefs().setValue("timeline/left_w", self.sizes()[0])
        )
        editor.laid_out.connect(self.sync)
        editor.verticalScrollBar().valueChanged.connect(self.sync)
        editor.verticalScrollBar().rangeChanged.connect(self.sync)
        editor.cursorPositionChanged.connect(self.sync)
        editor.field_requested.connect(self.fields.edit_current)
        editor.today_requested.connect(self.gantt.go_today)
        editor.time_zoom.connect(self.gantt.zoom_time)

    def attach(self):
        self.left.layout().addWidget(self.ed)
        self.sync()

    def createHandle(self):
        return _Handle(self.orientation(), self)

    def holds_editor(self):
        return self.ed.parentWidget() is self.left

    def sync(self, *_):
        self.corner.setFixedHeight(HEAD_H)
        self.fields.setVisible(self.settings["tl_fields"])
        if not self.ed.timeline:
            return
        self.gantt.refresh()
        self.fields.update()
        for i in range(1, self.count()):
            self.handle(i).update()

    def fields_w(self):
        return FIELDS_W if self.settings["tl_fields"] else 0

    def export_size(self):
        doc = self.ed.document()
        _, days = self.gantt.frame()
        return (
            self.ed.viewport().width() + self.fields_w() + days * self.gantt.day_w,
            HEAD_H + math.ceil(doc.size().height()),
        )

    def _paint(self, p, h, chart_w, xoff, items, origin, days):
        text_w = self.ed.viewport().width()
        self._paint_text(p, text_w, h, items)
        p.translate(text_w, 0)
        if self.fields_w():
            self.fields.paint_cols(p, FIELDS_W, h, items, -1)
        p.translate(self.fields_w(), 0)
        self.gantt.paint_chart(p, chart_w, h, xoff, items, origin, days, -1)

    def render_image(self):
        with light():
            return self._render_image()

    def _render_image(self):
        w, h = self.export_size()
        img = QImage(w, h, QImage.Format.Format_ARGB32)
        img.fill(QColor("#ffffff"))
        origin, days = self.gantt.frame()
        p = QPainter(img)
        self._paint(
            p,
            h,
            days * self.gantt.day_w,
            0,
            self.gantt.items(HEAD_H, 0, False),
            origin,
            days,
        )
        p.end()
        return img

    def _paint_text(self, p, w, h, items):
        s = self.settings
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(0, 0, w, HEAD_H, rdb_theme.color("head_bg"))
        p.setPen(rdb_theme.color("head_rule"))
        p.drawLine(0, HEAD_H - 1, w, HEAD_H - 1)
        nums = task_numbers(self.ed.document())
        p.save()
        # rows scrolled up stay under the header
        p.setClipRect(QRectF(0, HEAD_H, w, max(h - HEAD_H, 0)))
        for it in items:
            if it.info is None or it.bottom < HEAD_H or it.top > h:
                continue
            lvl = it.info["level"]
            level = f"h{lvl}" if lvl else "body"
            font = m.list_font(s, level)
            font.setStrikeOut(it.info["done"] and s["strike"])
            p.setFont(font)
            p.setPen(_c(s, "checked_color" if it.info["done"] else f"{level}_fg"))
            x = 4
            if not lvl:
                p.drawText(
                    QRectF(4, it.cy - 10, 24, 20),
                    ALIGN_R,
                    str(nums[it.block.blockNumber()]),
                )
                x = m.GUTTER
            text = QFontMetrics(font).elidedText(
                m.item_text(it.block), Qt.TextElideMode.ElideRight, w - x - 4
            )
            p.drawText(QRectF(x, it.cy - 10, w - x - 4, 20), ALIGN_L, text)
        p.restore()

    def save_png(self, path):
        if not self.render_image().save(path, "PNG"):
            raise OSError(f"cannot write {path}")

    def visible_size(self):
        vp = self.gantt.viewport()
        # cropped below the last row, so a short list leaves no empty grid
        rows = self.gantt.items(drag=False)
        bottom = math.ceil(max(it.bottom for it in rows)) + 1 if rows else HEAD_H
        w = self.ed.viewport().width() + self.fields_w() + vp.width()
        return w, min(vp.height(), bottom)

    def write_pdf(self, path):
        with light():
            self._write_pdf(path)

    def _write_pdf(self, path):
        # what is on screen: the text, fields and chart columns at their current scroll;
        # PDF because Qt's SVG writer drops clipping
        w, h = self.visible_size()
        vw = self.gantt.viewport().width()
        pdf = QPdfWriter(path)
        pdf.setResolution(96)
        pdf.setPageSize(QPageSize(QSizeF(w * 0.75, h * 0.75), QPageSize.Unit.Point))
        pdf.setPageMargins(QMarginsF(0, 0, 0, 0))
        origin, days = self.gantt.frame(vw)
        p = QPainter(pdf)
        p.fillRect(0, 0, w, h, QColor("#ffffff"))
        xoff = -self.gantt.horizontalScrollBar().value()
        self._paint(p, h, vw, xoff, self.gantt.items(drag=False), origin, days)
        p.end()

    def emf_bytes(self, inkscape):
        with tempfile.TemporaryDirectory() as tmp:
            pdf, svg, emf = (os.path.join(tmp, f"t.{x}") for x in ("pdf", "svg", "emf"))
            # a private profile whose EMF export keeps text as text, not outlines
            with open(os.path.join(tmp, "preferences.xml"), "w") as f:
                f.write(INK_PREFS)
            env = {**os.environ, "INKSCAPE_PROFILE_DIR": tmp}
            self.write_pdf(pdf)

            def run(*args):
                subprocess.run(
                    [inkscape, *args],
                    check=True,
                    capture_output=True,
                    env=env,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )

            run(
                pdf,
                "--pdf-font-strategy=keep",
                "--export-type=svg",
                f"--export-filename={svg}",
            )
            with open(svg, encoding="utf-8") as f:
                text = GLYPH_DX.sub("", QT_FONT.sub(_font_css, f.read()))
            with open(svg, "w", encoding="utf-8") as f:
                f.write(text)
            run(svg, "--export-type=emf", f"--export-filename={emf}")
            with open(emf, "rb") as f:
                return f.read()

    def copy_emf(self, inkscape):
        # the top-level window's handle: winId() on this child would make it native
        put_emf(int(self.window().winId()), self.emf_bytes(inkscape))


INK_PREFS = (
    '<?xml version="1.0"?>\n<inkscape version="1"><group id="extensions" '
    'org.inkscape.output.emf.textToPath="false" /></inkscape>\n'
)
# Qt's PDF writer names a font family-style-weight, which Inkscape keeps as the family
QT_FONT = re.compile(r"font-family:([^;\"]+?)-(\d)-(\d+)")
# per-glyph kerning offsets, which would export every letter as its own text record
GLYPH_DX = re.compile(r'\s+dx="[^"]*"')


def _font_css(mo):
    italic = ";font-style:italic" if mo[2] != "0" else ""
    return f"font-family:{mo[1]};font-weight:{mo[3]}{italic}"


def put_emf(hwnd, data):
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    gdi32.SetEnhMetaFileBits.restype = wt.HANDLE
    gdi32.SetEnhMetaFileBits.argtypes = [wt.UINT, ctypes.c_char_p]
    gdi32.DeleteEnhMetaFile.argtypes = [wt.HANDLE]
    user32.OpenClipboard.argtypes = [wt.HWND]
    user32.SetClipboardData.restype = wt.HANDLE
    user32.SetClipboardData.argtypes = [wt.UINT, wt.HANDLE]
    h = gdi32.SetEnhMetaFileBits(len(data), data)
    if not h:
        raise OSError("not an EMF")
    if not user32.OpenClipboard(hwnd):
        gdi32.DeleteEnhMetaFile(h)
        raise OSError("clipboard busy")
    try:
        user32.EmptyClipboard()
        # the clipboard owns the handle once it takes it
        if not user32.SetClipboardData(CF_ENHMETAFILE, h):
            gdi32.DeleteEnhMetaFile(h)
            raise OSError("clipboard refused the EMF")
    finally:
        user32.CloseClipboard()
