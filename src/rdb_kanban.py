import copy
import math
import re
import time

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSizeF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QFontMetricsF,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QApplication,
    QFrame,
    QLineEdit,
    QMenu,
    QWidgetAction,
)

import rdb_color
import rdb_map
import rdb_model as m
import rdb_status
import rdb_theme
from rdb_editor import (
    TAG_GAP,
    PillDrag,
    _paint_chip,
    _paint_fold_box,
    _tag_font,
    _toward,
    paint_marker,
)
from rdb_findbar import SEL_BG, SEL_CUR, runs
from rdb_map import ALIGN_L, LINE_H, MARK, _col

MIN_W, MAX_W = 140, 640
EDGE = 4
FOLD = 13
MAX_LINES = 3


def clamp_w(w):
    return min(max(w, MIN_W), MAX_W)


def ordered_cards(stored, keys):
    # the stored order's cards that exist ("" is always one), then the rest in settings order
    every = ["", *keys]
    first = [k for k in stored if k in every]
    return first + [k for k in every if k not in first]


def board_layout(cards, heads, items, widths, z, metrics, hole=None):
    # content coordinates. Each card: its rect, title strip, and a row per heading that has an
    # item of the card under it (or under its sub-headings) and per item in it, in document order,
    # the rows inside the card's pad. hole: rows left out (a lifted item, a lifted heading's
    # groups, a lifted card), gaps {card: (id of the row the gap sits above, or None for the
    # end; height)}, cards `open` to all their headings, and card_gap (slot, width) among the
    # other cards
    hole = hole or {}
    omit_items, omit_heads = hole.get("omit_items", ()), hole.get("omit_heads", ())
    omit_cards, gaps = hole.get("omit_cards", ()), hole.get("gaps", {})
    opened, card_gap = hole.get("open", ()), hole.get("card_gap")
    gap = margin = round(metrics["gap"] * z)
    title_h, line_h, pad, head_h, inset, mark_w, cpad = (
        metrics[k]
        for k in ("title_h", "line_h", "pad", "head_h", "inset", "mark_w", "card_pad")
    )
    spans = {}
    for i, h in enumerate(heads):
        j = i + 1
        while j < len(heads) and heads[j]["level"] > h["level"]:
            j += 1
        spans[h["id"]] = {x["id"] for x in heads[i:j]}
    head_ids = {h["id"] for h in heads}
    owner, cur = {}, None
    for h in heads:
        if h["hidden"]:
            owner[h["id"]] = cur
        else:
            cur = h["id"] if h["folded"] else None
    by = {c: {} for c in cards}
    for it in items:
        if it["group"] and it["group"] not in head_ids:
            raise KeyError(it["group"])
        by[it["column"]].setdefault(it["group"], []).append(it)
    out = {
        "cards": {},
        "titles": {},
        "rows": {},
        "ends": {},
        "lines": {},
        "counts": {},
        "fold_n": {},
        "foldable": {h["id"] for h in heads if h["foldable"]},
        "z": z,
    }
    x, bottom = margin, margin
    for slot, c in enumerate(k for k in cards if k not in omit_cards):
        if card_gap is not None and card_gap[0] == slot:
            x += card_gap[1]
        w = round(clamp_w(widths.get(c, metrics["card_w"])) * z)
        rx, rw = x + cpad, w - 2 * cpad
        y, rows = margin + title_h + cpad, []
        used = {g for g, v in by[c].items() if v}

        def gap_at(rid):
            nonlocal y
            if c in gaps and gaps[c][0] == rid:
                y += gaps[c][1]

        def add_items(group):
            nonlocal y
            for it in by[c].get(group, ()):
                if it["id"] in omit_items:
                    continue
                gap_at(it["id"])
                text_w = rw - 2 * inset - (mark_w if it["mark"] else 0)
                n = min(max(metrics["lines"](it, text_w), 1), MAX_LINES)
                out["lines"][it["id"]] = n
                h = 2 * pad + n * line_h
                rows.append(("item", it["id"], QRect(rx, y, rw, h)))
                y += h

        add_items("")
        for h in heads:
            if h["hidden"] or h["id"] in omit_heads:
                continue
            if c not in opened and not spans[h["id"]] & used:
                continue
            gap_at(h["id"])
            hh = head_h[h["level"]] if isinstance(head_h, dict) else head_h
            rows.append(("head", h["id"], QRect(rx, y, rw, hh)))
            y += hh
            if h["folded"]:
                out["fold_n"][(c, h["id"])] = sum(
                    len(g)
                    for gid, g in by[c].items()
                    if gid == h["id"] or owner.get(gid) == h["id"]
                )
            else:
                add_items(h["id"])
        out["ends"][c] = y
        gap_at(None)
        height = max(y + cpad - margin, title_h + line_h + 2 * cpad)
        out["cards"][c] = QRect(x, margin, w, height)
        out["titles"][c] = QRect(x, margin, w, title_h)
        out["rows"][c] = rows
        out["counts"][c] = sum(len(g) for g in by[c].values())
        bottom = max(bottom, margin + height)
        x += w + gap
    if card_gap is not None and card_gap[0] >= len(cards) - len(omit_cards):
        x += card_gap[1]
    out["size"] = (max(x - gap, margin) + margin, bottom + margin)
    return out


def open_folds(heads, groups, shut=frozenset()):
    # display only: a folded heading with a shown item in its group (or a sub-heading's) opens,
    # unless its fold box shut it again for this search; what a fold still hides stays hidden
    spans = {}
    for i, h in enumerate(heads):
        j = i + 1
        while j < len(heads) and heads[j]["level"] > h["level"]:
            j += 1
        spans[h["id"]] = {x["id"] for x in heads[i:j]}
    out, chain = [], {}
    for h in heads:
        lvl = h["level"]
        folded = h["folded"] and (h["id"] in shut or not spans[h["id"]] & groups)
        chain = {k: v for k, v in chain.items() if k < lvl}
        out.append({**h, "folded": folded, "hidden": any(chain.values())})
        chain[lvl] = folded
    return out


def ordered_items(cards, heads, items):
    # the items as the board shows them: cards left to right, rows top to bottom
    out = []
    for c in cards:
        mine = [i for i in items if i["column"] == c]
        for group in ("", *(h["id"] for h in heads)):
            out.extend(i for i in mine if i["group"] == group)
    return out


def atom_spans(text, lines):
    # where each atom of wrap_lines' output starts and ends in the text: (start, end), a tag
    # with its '#'; None for the whitespace between atoms and for an atom cut or elided
    out, pos = [], 0
    for line in lines:
        row = []
        for t, is_tag, _ in line:
            while pos < len(text) and text[pos].isspace():
                pos += 1
            if is_tag:
                mt = m.TAG_RX.match(text, pos)
                row.append((mt.start(), mt.end()))
                pos = mt.end()
            elif text.startswith(t, pos):
                row.append((pos, pos + len(t)))
                pos += len(t)
            else:
                row.append(None)
        out.append(row)
    return out


def fold_rect(row, z):
    side = round(FOLD * z)
    return QRect(
        row.left() + round(4 * z), row.top() + (row.height() - side) // 2, side, side
    )


def hit(layout, pos):
    cards = layout["cards"]
    for c, r in cards.items():
        # outside the card the band stops where the next card starts
        nxt = min(
            (o.left() for o in cards.values() if o.left() > r.right()), default=None
        )
        out = EDGE if nxt is None else min(EDGE, nxt - (r.right() + 1) - 1)
        dx = pos.x() - (r.right() + 1)
        if r.top() <= pos.y() <= r.bottom() and -EDGE <= dx <= out:
            return ("edge", c)
    for c, r in layout["cards"].items():
        if not r.contains(pos):
            continue
        if layout["titles"][c].contains(pos):
            return ("title", c)
        for kind, rid, rr in layout["rows"][c]:
            if not rr.contains(pos):
                continue
            if (
                kind == "head"
                and rid in layout["foldable"]
                and fold_rect(rr, layout["z"]).contains(pos)
            ):
                return ("fold", rid)
            return (kind, rid)
        return ("card", c)
    return None


def card_at(layout, x):
    # the card whose column holds x, else the nearest one
    return min(
        layout["cards"],
        key=lambda c: max(
            layout["cards"][c].left() - x, x - layout["cards"][c].right() - 1, 0
        ),
    )


def _atoms(text, font, tag):
    # words and spaces in the body font, each (text, is_tag, width); with `tag` (font, pad, gap) a
    # tag is one unit drawn as the list draws it: no '#', the name in the tag font, a pad each side
    fm = QFontMetricsF(font)
    out, pos = [], 0

    def plain(chunk):
        out.extend(
            (t, False, fm.horizontalAdvance(t)) for t in re.findall(r"\s+|\S+", chunk)
        )

    if tag is not None:
        tm = QFontMetricsF(tag[0])
        for mt in m.TAG_RX.finditer(text):
            plain(text[pos : mt.start()])
            name = mt.group()[1:]
            out.append((name, True, tm.horizontalAdvance(name) + 2 * tag[1]))
            pos = mt.end()
    plain(text[pos:])
    return out


def wrap_lines(text, font, width, tag=None, limit=MAX_LINES):
    # the lines the text wraps to, each a list of atoms (text, is_tag, width): no line is wider
    # than `width`, a word longer than a line is cut and a tag longer than one is elided; of more
    # than `limit` lines the last is cut with an ellipsis. A chip leaves its gap after it, which
    # may hang past the edge, as a trailing space does
    fm = QFontMetricsF(font)
    gap = 0 if tag is None else tag[2]
    advance = lambda a: a[2] + (gap if a[1] else 0)  # noqa: E731
    lines, cur, used = [], [], 0.0

    def close():
        nonlocal cur, used
        while cur and not cur[-1][1] and cur[-1][0].isspace():
            cur.pop()
        lines.append(cur)
        cur, used = [], 0.0

    for atom in _atoms(text, font, tag):
        t, is_tag, w = atom
        if not is_tag and t.isspace():
            if cur:
                cur.append(atom)
                used += w
            continue
        if used + w > width and cur:
            close()
        if w > width:
            if is_tag:
                tm = QFontMetricsF(tag[0])
                room = max(width - 2 * tag[1], 0)
                t = tm.elidedText(t, Qt.TextElideMode.ElideRight, room)
                atom = (t, True, min(tm.horizontalAdvance(t) + 2 * tag[1], width))
            else:
                while t and fm.horizontalAdvance(t) > width:
                    n = len(t)
                    while n > 1 and fm.horizontalAdvance(t[:n]) > width:
                        n -= 1
                    cur.append((t[:n], False, fm.horizontalAdvance(t[:n])))
                    close()
                    t = t[n:]
                if not t:
                    continue
                atom = (t, False, fm.horizontalAdvance(t))
        cur.append(atom)
        used += advance(atom)
    close()
    if len(lines) <= limit:
        return lines
    # the rest of the text on one line, cut where it stops fitting
    gather, space = [], (" ", False, fm.horizontalAdvance(" "))
    for ln in lines[limit - 1 :]:
        gather += ([space] if gather else []) + ln
    ell = "…"
    ell_w = fm.horizontalAdvance(ell)
    last, used = [], 0.0
    for atom in gather:
        if used + atom[2] + ell_w > width:
            if not atom[1] and not atom[0].isspace():
                t = fm.elidedText(atom[0], Qt.TextElideMode.ElideRight, width - used)
                if t:
                    last.append((t, False, fm.horizontalAdvance(t)))
                    return [*lines[: limit - 1], last]
            break
        last.append(atom)
        used += advance(atom)
    while last and not last[-1][1] and last[-1][0].isspace():
        last.pop()
    last.append((ell, False, ell_w))
    return [*lines[: limit - 1], last]


def _key(c, kind, rid):
    if kind == "card":
        return ("card", rid)
    return ("item", rid) if kind == "item" else ("head", c, rid)


class _TitleEdit(QLineEdit):
    cancelled = Signal()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
            e.accept()
        else:
            super().keyPressEvent(e)


class KanbanBoard(QAbstractScrollArea):
    leave = Signal(str)
    widths_changed = Signal(dict)
    order_changed = Signal(list)
    statuses_edited = Signal(list)
    composed = Signal()

    def __init__(self, editor):
        super().__init__()
        self.ed, self.settings = editor, editor.settings
        self._layout = self._key = None
        self._building = self._animate = self._dropping = False
        self._raw = ([], [])
        self._cards, self._all, self._items, self._heads, self._by_id = (
            [],
            [],
            [],
            [],
            {},
        )
        # the search: the ids that show (None: all), the matched positions per id, the current id
        self._found_ids, self._found_marks, self._found_cur = None, {}, None
        # the headings a search opened over its hits, and those of them shut again for it
        self._opened, self._shut = set(), set()
        self._targets, self._shown = {}, {}
        self._widths, self._done, self._status_only = {}, True, False
        self._order = []
        self._edit = self._pending = None
        self._epoch = 0
        self._drag = self._wdrag = None
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.viewport().setMouseTracking(True)
        self.verticalScrollBar().setSingleStep(24)
        self.horizontalScrollBar().setSingleStep(24)
        for bar in (self.verticalScrollBar(), self.horizontalScrollBar()):
            bar.valueChanged.connect(self.viewport().update)
            bar.valueChanged.connect(self._place_edit)
        editor.laid_out.connect(self.refresh)
        editor.marked.connect(self.refresh)

    # data

    def set_done(self, on):
        self._done = bool(on)
        self._compose()
        self._retarget(snap=True)

    def set_widths(self, widths):
        self._widths = dict(widths)
        self._retarget(snap=True)

    def set_status_only(self, on):
        self._status_only = bool(on)
        self._compose()
        self._retarget(snap=True)

    def set_order(self, order):
        self._order = list(order)
        self._compose()
        self._retarget(snap=True)

    def _full_order(self):
        # every card in board order, the hidden ones too
        return ordered_cards(self._order, [s["key"] for s in m.STATUS_LIST])

    def _compose(self):
        items = self._raw[0]
        shown = {s["key"] for s in m.STATUS_LIST if self._done or not s["done"]}
        self._cards = [
            c
            for c in self._full_order()
            if (c in shown) or (c == "" and not self._status_only)
        ]
        self._all = [
            {**i, "column": col}
            for i in items
            if (col := m.column_of(i)) in self._cards
            and (self._done or not i["checked"])
        ]
        self._filter()
        self.composed.emit()

    def _filter(self):
        keep, heads = self._found_ids, self._raw[1]
        self._items = (
            self._all if keep is None else [i for i in self._all if i["id"] in keep]
        )
        # a search opens the folds over its hits, isolating or not, so each hit can be shown
        hits = {i["group"] for i in self._all if i["id"] in self._found_marks}
        self._heads = open_folds(heads, hits, self._shut) if hits else heads
        self._opened = {
            h["id"]
            for h, f in zip(heads, self._heads)
            if h["folded"] and not f["folded"]
        }
        self._by_id = {i["id"]: i for i in self._items}

    def find_source(self):
        # what a search runs over: the composed items in board order, and the headings
        self._sync()
        return ordered_items(self._cards, self._raw[1], self._all), self._raw[1]

    def set_find(self, shown, marks, current):
        self._found_ids = None if shown is None else frozenset(shown)
        self._found_marks, self._found_cur = marks, current
        if not marks:
            self._shut = set()
        self._filter()
        self._retarget(snap=True)

    def reveal_item(self, uid):
        self.plan()
        row = next(
            (
                rr
                for rows in self._layout["rows"].values()
                for kind, rid, rr in rows
                if (kind, rid) == ("item", uid)
            ),
            None,
        )
        # an item under a fold has no row to show
        if row is None:
            return
        for bar, lo, hi in (
            (self.horizontalScrollBar(), row.left(), row.right() + 1),
            (self.verticalScrollBar(), row.top(), row.bottom() + 1),
        ):
            if hi > bar.value() + bar.pageStep():
                bar.setValue(hi - bar.pageStep())
            if lo < bar.value():
                bar.setValue(lo)

    def _stamp(self):
        doc = self.ed.document()
        return (
            id(doc),
            doc.revision(),
            doc.characterCount(),
            self.ed.zoom,
            self._epoch,
        )

    def _sync(self):
        doc = self.ed.document()
        doc.contentsChanged.connect(self.refresh, Qt.ConnectionType.UniqueConnection)
        if self._stamp() == self._key or self._building:
            return
        # ids are written into the document while the items are read: that is no change to see
        self._building = True
        try:
            self._raw = (m.kanban_items(doc), m.kanban_heads(doc))
        finally:
            self._building = False
        self._key = self._stamp()
        # an edit that is not the board's own drop ends a drag
        if self._drag is not None and not self._dropping:
            self._drag = None
        self._compose()
        self._retarget(snap=not self._animate)

    def plan(self):
        self._sync()
        return self._layout

    def refresh(self, *_):
        if self._building:
            return
        self._key = None
        if self.isVisible():
            self._sync()
        self.viewport().update()

    def restyle(self):
        self._epoch += 1
        self.refresh()

    # geometry

    def _font(self, level=0):
        # the list page's fonts at the editor zoom: body, or H1 to H3
        f = QFont(
            m.EDITOR_FONT if not level else m.list_font(self.settings, f"h{level}")
        )
        f.setPointSizeF(f.pointSizeF() * self.ed.zoom)
        return f

    def _title_font(self):
        s, z = self.settings, self.ed.zoom
        f = QFont(s["kanban_title_font"])
        f.setPointSizeF(s["kanban_title_size"] * z)
        f.setBold(True)
        return f

    def _tag(self):
        # what wrap_lines needs of a tag: its font, the pad either side, the gap after
        s, z = self.settings, self.ed.zoom
        return _tag_font(s, z), s["tag_pad"] * z, TAG_GAP * z

    def _metrics(self):
        z, s = self.ed.zoom, self.settings
        font, tag = self._font(), self._tag()
        line = max(round(LINE_H * z), QFontMetrics(font).lineSpacing())
        # a chip taller than the line reaches past the row's pad
        pad = max(round(2 * z), 1, math.ceil((s["chip_vpad"] - 2) * z))
        extra = {1: round(m.H1_PAD * z), 2: round(s["h2_rule_px"] * z), 3: 0}
        return {
            "title_h": max(
                QFontMetrics(self._title_font()).lineSpacing() + 2 * round(3 * z),
                round(16 * z),
            ),
            "card_pad": round(s["kanban_pad"] * z),
            "card_w": s["kanban_card_w"],
            "gap": s["kanban_gap"],
            "line_h": line,
            "pad": pad,
            "head_h": {
                lvl: QFontMetrics(self._font(lvl)).lineSpacing() + 2 * pad + extra[lvl]
                for lvl in (1, 2, 3)
            },
            "inset": round(6 * z),
            "mark_w": round((MARK + 3) * z),
            "lines": lambda it, w: len(wrap_lines(it["text"], font, w, tag)),
        }

    def _layout_for(self, hole):
        return board_layout(
            self._cards,
            self._heads,
            self._items,
            self._widths,
            self.ed.zoom,
            self._metrics(),
            hole,
        )

    def _omit(self):
        d = self._drag
        if d["kind"] == "card":
            return {"omit_cards": {d["id"]}}
        if d["kind"] == "item":
            return {"omit_items": {d["id"]}, "open": {d["over"]}}
        # every card shows every heading as a place to drop the group
        return {"omit_heads": d["group"], "open": set(self._cards)}

    def _hole(self):
        d = self._drag
        if d is None or not d["live"]:
            return None
        if d["kind"] == "item" and d["over"] is not None and not d["table"]:
            # the card under the pointer shows every heading to drop into
            if d["spot"] is None or self._is_home(d["spot"]):
                return {"open": {d["over"]}}
        if d["spot"] is None or d["table"] or self._is_home(d["spot"]):
            return {"open": set(self._cards)} if d["kind"] == "head" else None
        if d["kind"] == "card":
            return {
                **self._omit(),
                "card_gap": (d["spot"], d["size"].width() + self._gap()),
            }
        key, before = d["spot"]
        h = d["size"].height()
        gaps = (
            {key: (before, h)}
            if d["kind"] == "item"
            else {c: (before, h) for c in self._cards}
        )
        return {**self._omit(), "gaps": gaps}

    def _gap(self):
        return round(self.settings["kanban_gap"] * self.ed.zoom)

    def _is_home(self, spot):
        d = self._drag
        if d["kind"] == "item":
            return spot == d["home"]
        if d["kind"] == "card":
            return spot == d["home"]
        return spot[1] == d["home"]

    def _retarget(self, snap=False):
        lay = self._layout_for(self._hole())
        targets = {}
        for c, r in lay["cards"].items():
            targets[("card", c)] = QRectF(r)
            for kind, rid, rr in lay["rows"][c]:
                targets[_key(c, kind, rid)] = QRectF(rr)
        changed = targets != self._targets
        self._layout, self._targets = lay, targets
        live = self._drag is not None and self._drag["live"]
        if snap and changed:
            self._shown = dict(targets)
        else:
            # a heading row opening under a drag grows from nothing
            self._shown = {
                k: self._shown.get(
                    k,
                    QRectF(t.x(), t.y(), t.width(), 0)
                    if live and k[0] == "head"
                    else t,
                )
                for k, t in targets.items()
            }
        w, h = lay["size"]
        vp = self.viewport().size()
        self.horizontalScrollBar().setRange(0, max(0, w - vp.width()))
        self.horizontalScrollBar().setPageStep(vp.width())
        self.verticalScrollBar().setRange(0, max(0, h - vp.height()))
        self.verticalScrollBar().setPageStep(vp.height())
        self.viewport().update()
        self._kick()
        self._place_edit()
        if self._pending in lay["cards"] and self.isVisible():
            key, self._pending = self._pending, None
            self._reveal(key)
            self._open_edit(key)

    def shown(self, key):
        return self._shown[key]

    def _kick(self):
        if not self._timer.isActive() and (
            self._moving() or (self._drag is not None and self._drag["glide"])
        ):
            self._timer.start()

    def _moving(self):
        return any(self._shown[k] != t for k, t in self._targets.items())

    def _scroll(self):
        return QPoint(
            self.horizontalScrollBar().value(), self.verticalScrollBar().value()
        )

    def to_view(self, rect):
        s = self._scroll()
        return rect.translated(-s.x(), -s.y())

    def _content(self, pos):
        return pos + self._scroll()

    # motion

    def _tick(self):
        moving = False
        for k, t in self._targets.items():
            s = self._shown[k]
            n = QRectF(
                _toward(s.x(), t.x(), PillDrag.EASE),
                _toward(s.y(), t.y(), PillDrag.EASE),
                _toward(s.width(), t.width(), PillDrag.EASE),
                _toward(s.height(), t.height(), PillDrag.EASE),
            )
            if n != s:
                self._shown[k] = n
                moving = True
        d = self._drag
        if d is not None:
            g = d["glide"]
            if g is not None:
                start, to, t0, then = g
                t = min(1.0, (time.monotonic() - t0) / PillDrag.GLIDE)
                d["at"] = start + (to() - start) * (1 - (1 - t) ** 3)
                if t >= 1.0:
                    then()
                else:
                    moving = True
            elif d["live"] and d["aimed"] != (d["pos"], self._scroll()):
                self._aim()
                moving = moving or self._moving()
        self.viewport().update()
        if not moving:
            self._timer.stop()

    def _finish(self):
        self._drag = None
        self._retarget()
        self.viewport().update()

    def _glide(self, to):
        d = self._drag
        d["glide"] = (QPointF(d["at"]), to, time.monotonic(), self._finish)
        self._timer.start()

    def _row_pos(self, key):
        return QPointF(self.to_view(self._targets[key]).topLeft())

    # drag

    def _row_key(self, d):
        return _key(d["card"], d["kind"], d["id"])

    def _begin(self, kind, card, rid, pos):
        lay = self._layout
        if kind == "card":
            rect = lay["cards"][rid]
        else:
            rect = next(rr for k, r, rr in lay["rows"][card] if (k, r) == (kind, rid))
        top = self.to_view(QRectF(self._shown[_key(card, kind, rid)])).topLeft()
        d = {
            "kind": kind,
            "card": card,
            "id": rid,
            "press": pos,
            "pos": pos,
            "grab": QPointF(pos) - top,
            "at": top,
            "size": QSizeF(rect.size()),
            "live": False,
            "spot": None,
            "glide": None,
            "aimed": None,
            "table": kind == "item" and bool(m.tables(self.ed.document())),
            "over": None,
        }
        if kind == "card":
            # what the lifted card is drawn from while the layout has no room for it
            d["lay"] = lay
            d["snap"] = {
                k: QRectF(v)
                for k, v in self._shown.items()
                if k == ("card", rid) or (k[0] == "head" and k[1] == rid)
            }
            d["snap"].update(
                {
                    ("item", r): QRectF(self._shown[("item", r)])
                    for k, r, _ in lay["rows"][rid]
                    if k == "item"
                }
            )
        self._drag = d

    def _go_live(self):
        d, doc = self._drag, self.ed.document()
        d["live"] = True
        if d["kind"] == "card":
            d["home"] = self._cards.index(d["id"])
            self._aim()
            return
        # as the card shows once the pointer is over it: every heading
        rows = self._layout_for({"open": {d["card"]}})["rows"][d["card"]]
        ids = [(k, r) for k, r, _ in rows]
        if d["kind"] == "item":
            at = ids.index(("item", d["id"]))
            d["home"] = (d["card"], ids[at + 1][1] if at + 1 < len(ids) else None)
            # rows a search opened sit in a fold the document keeps shut, yet they show: each
            # is a spot, and the item's own place always is
            d["allowed"] = set(m.kanban_spots(doc, d["id"])) | {d["home"][1]}
            if self._opened:
                d["allowed"] |= {
                    i["id"] for i in self._items if not i["sec"] and i["id"] != d["id"]
                }
        else:
            heads = self._heads
            i = next(n for n, h in enumerate(heads) if h["id"] == d["id"])
            lvl = heads[i]["level"]
            j = i + 1
            while j < len(heads) and heads[j]["level"] > lvl:
                j += 1
            d["group"] = {h["id"] for h in heads[i:j]}
            d["home"] = heads[j]["id"] if j < len(heads) else None
            d["allowed"] = set(m.kanban_spots(doc, d["id"]))
        self._aim()

    def _aim(self):
        d = self._drag
        d["aimed"] = (d["pos"], self._scroll())
        d["at"] = QPointF(d["pos"]) - d["grab"]
        at = self._content(d["pos"])
        if d["kind"] == "card":
            cx = at.x() - d["grab"].x() + d["size"].width() / 2
            d["spot"] = sum(
                1
                for k in self._cards
                if k != d["id"] and d["lay"]["cards"][k].center().x() < cx
            )
            self._retarget()
            return
        if d["table"]:
            card = card_at(self._layout, at.x())
            # checking or unchecking moves the item, which a table document refuses
            flips = m._done_status(card) != self._by_id[d["id"]]["checked"]
            moves = self.ed.check_mode == "move" and flips
            d["spot"] = None if card is None or moves else (card, None)
            self._retarget()
            return
        card = d["over"] = card_at(self._layout, at.x())
        # the rows without the lifted ones, whatever is aimed: the spots must not move with the aim
        base = self._layout_for(self._omit())
        cy = at.y() - d["grab"].y() + d["size"].height() / 2
        spots = [
            (rr.top(), rid)
            for kind, rid, rr in base["rows"][card]
            if rid in d["allowed"] and (d["kind"] == "item" or kind == "head")
        ]
        if None in d["allowed"]:
            spots.append((base["ends"][card], None))
        if not spots:
            d["spot"] = None
        else:
            _, before = min(spots, key=lambda s: abs(s[0] - cy))
            d["spot"] = (card, before)
        self._retarget()

    def _drop_card(self):
        d = self._drag
        slot, d["spot"] = d["spot"], None
        others = [k for k in self._cards if k != d["id"]]
        full = [k for k in self._full_order() if k != d["id"]]
        at = (
            full.index(others[slot])
            if slot < len(others)
            else full.index(others[-1]) + 1
            if others
            else 0
        )
        full.insert(at, d["id"])
        self._order = full + [k for k in self._order if k not in full]
        self._compose()
        self._retarget()
        self.order_changed.emit(list(self._order))
        self._glide(lambda: self._row_pos(("card", d["id"])))

    def _drop(self):
        d = self._drag
        if d["kind"] == "card":
            self._drop_card()
            return
        key, before = d["spot"]
        doc = self.ed.document()
        d["spot"] = d["over"] = None
        self._dropping = self._animate = True
        try:
            if d["table"]:
                self.ed.set_item_status(m.unit_block(doc, d["id"]), key)
            elif d["kind"] == "item":
                self.ed.kanban_drop(d["id"], key, before)
            else:
                self.ed.move_heading(d["id"], before)
            self._key = None
            self._sync()
        except BaseException:
            # the error surfaces; the board must not stay holding the row
            self._drag = None
            self._retarget(snap=True)
            raise
        finally:
            self._dropping = self._animate = False
        card = key if d["kind"] == "item" else d["card"]
        d["card"] = card
        self._glide(lambda: self._row_pos(self._row_key(d)))

    def _cancel(self):
        d = self._drag
        d["spot"] = d["over"] = None
        self._retarget()
        self._glide(lambda: self._row_pos(self._row_key(d)))

    # painting

    def paintEvent(self, e):
        self.plan()
        s, z, vp = self.settings, self.ed.zoom, self.viewport()
        p = QPainter(vp)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(vp.rect(), QColor(m.shown(s, "editor_bg")))
        d = self._drag
        live = d is not None and d["live"]
        target = (
            d["spot"][0]
            if live and d["kind"] != "card" and d["spot"] is not None
            else None
        )
        scroll = self._scroll()
        p.translate(-scroll)
        lay = self._layout
        lifted_card = d["id"] if live and d["kind"] == "card" else None
        cards = [c for c in lay["cards"] if c != lifted_card]
        for c in cards:
            self._paint_shadow(p, self._shown[("card", c)], z)
        for c in cards:
            self._paint_card(p, c, self._shown[("card", c)], z, c == target, lay)
        for c in cards:
            self._paint_rows(p, c, lay, z, d if live else None)
        p.translate(scroll)
        if live:
            self._paint_lifted(p, d, z, lay)
        p.end()

    def _paint_rows(self, p, c, lay, z, d):
        for kind, rid, _ in lay["rows"][c]:
            if d is not None and (kind, rid) == (d["kind"], d["id"]):
                if kind == "item" or c == d["card"]:
                    continue
            r = self._shown[_key(c, kind, rid)]
            if kind == "head":
                self._paint_head(p, c, rid, r, z, lay)
            else:
                self._paint_item(p, self._by_id[rid], r, z)

    def _paint_shadow(self, p, r, z):
        if not self.settings["kanban_shadow"]:
            return
        base = QColor(rdb_map.SHADOW_COL)
        rad = self.settings["kanban_radius"] * z
        p.setPen(Qt.PenStyle.NoPen)
        for grow, share in ((4, 0.2), (2.5, 0.3), (1, 0.5)):
            base.setAlpha(round(rdb_map.SHADOW_COL.alpha() * share))
            p.setBrush(base)
            g = grow * z
            p.drawRoundedRect(
                r.adjusted(-g, -g, g, g).translated(1.5 * z, 3 * z),
                rad + g,
                rad + g,
            )

    def _card_fill(self):
        return QColor(m.shown(self.settings, "kanban_card_bg"))

    def _title_look(self, c):
        # the title strip's fill and its text colour: a status card's strip is the status colour
        # when Status fill is on, every other strip the card's own fill
        s = self.settings
        if c and s["kanban_title_fill"]:
            fill = rdb_theme.shade(m.STATUS_BY_KEY[c]["color"])
            auto = rdb_theme.ink_on(fill)
        else:
            fill = self._card_fill()
            auto = rdb_theme.ink_for(fill)
        if s["kanban_title_ink"] == "Fixed":
            return fill, QColor(m.shown(s, "kanban_title_fg"))
        return fill, auto

    def _title_text(self, c, lay=None):
        name = m.STATUS_BY_KEY[c]["name"] if c else "No Status"
        if not self.settings["kanban_counts"]:
            return name
        return f"{name} {(lay or self._layout)['counts'][c]}"

    def _paint_card(self, p, c, r, z, hot, lay):
        s = self.settings
        wd = 2 if hot else 1
        rad = s["kanban_radius"] * z
        edge = r.adjusted(wd / 2, wd / 2, -wd / 2, -wd / 2)
        face = QPainterPath()
        face.addRoundedRect(edge, rad, rad)
        title_h = lay["titles"][c].height()
        strip = QRectF(r.left(), r.top(), r.width(), title_h)
        fill, ink = self._title_look(c)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._card_fill())
        p.drawPath(face)
        filled = bool(c) and s["kanban_title_fill"]
        if filled:
            top = QPainterPath()
            top.addRect(strip)
            p.setBrush(fill)
            p.drawPath(face.intersected(top))
        p.setPen(
            QPen(
                _col("card_select_border")
                if hot
                else QColor(m.shown(s, "kanban_card_border")),
                wd,
            )
        )
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(face)
        if rdb_map._cfg["card_title_rule"] and not filled:
            p.setPen(_col("card_title_rule_color"))
            y = r.top() + title_h - 0.5
            p.drawLine(QPointF(r.left() + 1, y), QPointF(r.right() - 1, y))
        x = r.left() + round(6 * z)
        if c:
            st = m.STATUS_BY_KEY[c]
            side = round(16 * z)
            rdb_status.paint_status(
                p,
                QRectF(x, r.top() + (title_h - side) / 2, side, side),
                st["icon"],
                st["color"],
                1.2 * z,
                ring=ink if filled else None,
            )
            x += side + round(4 * z)
        p.setPen(ink)
        font = self._title_font()
        p.setFont(font)
        room = r.right() - x - 6 * z
        p.drawText(
            QRectF(x, r.top(), r.right() - x, title_h),
            ALIGN_L,
            QFontMetrics(font).elidedText(
                self._title_text(c, lay), Qt.TextElideMode.ElideRight, int(room)
            ),
        )

    def _paint_head(self, p, c, rid, r, z, lay):
        s = self.settings
        head = next(h for h in self._heads if h["id"] == rid)
        lvl = head["level"]
        ink = QColor(m.shown(s, "body_fg"))
        p.save()
        p.setClipRect(r)
        if lvl == 1:
            if head["hc"]:
                fill = rdb_theme.shade(head["hc"])
                border = fill.darker(150)
                ink = rdb_theme.ink_for(fill)
            else:
                fill, border = (
                    QColor(m.shown(s, "h1_bg")),
                    QColor(m.shown(s, "h1_border")),
                )
            p.setPen(QPen(border, 1))
            p.setBrush(fill)
            p.drawRect(r.adjusted(0.5, 0.5, -0.5, -0.5))
        elif lvl == 2:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(m.shown(s, "h2_rule")))
            rule = s["h2_rule_px"] * z
            p.drawRect(QRectF(r.left(), r.bottom() - rule, r.width(), rule))
        p.setPen(ink)
        x = r.left() + round(6 * z)
        text = head["text"]
        if head["foldable"]:
            fr = fold_rect(r.toRect(), z)
            _paint_fold_box(p, QRectF(fr), ink, head["folded"])
            x = fr.right() + 1 + round(4 * z)
        if head["folded"] and (c, rid) in lay["fold_n"]:
            text = f"{text}  {lay['fold_n'][(c, rid)]}"
        font = self._font(lvl)
        p.setFont(font)
        room = r.right() - x - 6 * z
        p.drawText(
            QRectF(x, r.top(), room, r.height()),
            ALIGN_L,
            QFontMetrics(font).elidedText(text, Qt.TextElideMode.ElideRight, int(room)),
        )
        p.restore()

    def _paint_item(self, p, it, r, z):
        s = self.settings
        met = self._metrics()
        pad, line = met["pad"], met["line_h"]
        x = r.left() + met["inset"]
        if it["mark"]:
            paint_marker(p, QRectF(x, r.top() + pad, MARK * z, line), it["mark"], z)
            x += met["mark_w"]
        font = self._font()
        checked = it["checked"]
        if checked:
            font.setStrikeOut(s["strike"])
            font.setItalic(font.italic() or s["italic"])
        if checked and s["grey"]:
            ink = QColor(m.shown(s, "checked_color"))
        elif it["fg"]:
            ink = rdb_theme.shade(it["fg"])
        else:
            ink = QColor(m.shown(s, "body_fg"))
        tag = self._tag()
        tag_font, tpad, tgap = tag
        tag_ink = ink if checked else QColor(m.shown(s, "tag_fg"))
        room = r.right() - met["inset"] - x
        v = (s["chip_vpad"] - 2) * z
        lines = wrap_lines(it["text"], font, room, tag)
        spans = atom_spans(it["text"], lines)
        hits = set(self._found_marks.get(it["id"], ()))
        hit_col = SEL_CUR if it["id"] == self._found_cur else SEL_BG
        fm = QFontMetricsF(font)
        for k, atoms in enumerate(lines):
            y = r.top() + pad + k * line
            if it["bg"]:
                width = sum(a[2] + (tgap if a[1] else 0) for a in atoms)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(rdb_theme.shade(it["bg"]))
                p.drawRoundedRect(
                    QRectF(x - 2 * z, y + z, width + 4 * z, line - 2 * z), 3 * z, 3 * z
                )
            at = float(x)
            for (text, is_tag, w), span in zip(atoms, spans[k]):
                # a matched tag draws hot; matched text takes the mark behind it
                found = [i for i in range(*span) if i in hits] if span else []
                if is_tag:
                    # the list's tag: the '#' hidden, a pad either side of the name
                    _paint_chip(
                        p,
                        QRectF(at, y - v, w, line + 2 * v),
                        s,
                        False,
                        z,
                        bool(found),
                        True,
                    )
                    p.setFont(tag_font)
                    p.setPen(tag_ink)
                    p.drawText(
                        QRectF(at + tpad, y, w - 2 * tpad + 2, line), ALIGN_L, text
                    )
                    at += w + tgap
                else:
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(rdb_theme.shade(hit_col))
                    for a, n in runs([i - span[0] for i in found]):
                        left = fm.horizontalAdvance(text[:a])
                        wide = fm.horizontalAdvance(text[a : a + n])
                        p.drawRect(QRectF(at + left, y, wide, line))
                    p.setFont(font)
                    p.setPen(ink)
                    p.drawText(QRectF(at, y, w + 2, line), ALIGN_L, text)
                    at += w

    def _paint_lifted(self, p, d, z, lay):
        if d["kind"] == "card":
            # the whole card as it stood when lifted, carried by the pointer
            c = d["id"]
            home = d["snap"][("card", c)]
            p.save()
            p.translate(d["at"] - home.topLeft())
            self._paint_shadow(p, home, z)
            self._paint_card(p, c, home, z, False, d["lay"])
            for kind, rid, _ in d["lay"]["rows"][c]:
                r = d["snap"][_key(c, kind, rid)]
                if kind == "head":
                    self._paint_head(p, c, rid, r, z, d["lay"])
                else:
                    self._paint_item(p, self._by_id[rid], r, z)
            p.restore()
            return
        at, size = d["at"], d["size"]
        r = QRectF(at, size)
        self._paint_shadow(p, r, z)
        p.setBrush(self._card_fill())
        p.setPen(QPen(QColor(m.shown(self.settings, "kanban_card_border")), 1))
        rad = self.settings["kanban_radius"] * z
        p.drawRoundedRect(r, rad, rad)
        if d["kind"] == "item":
            self._paint_item(p, self._by_id[d["id"]], r, z)
        else:
            self._paint_head(p, d["card"], d["id"], r, z, lay)

    # interaction

    def _edge_cursor(self, pos):
        found = hit(self._layout, self._content(pos))
        self.viewport().setCursor(
            Qt.CursorShape.SizeHorCursor
            if found and found[0] == "edge"
            else Qt.CursorShape.ArrowCursor
        )

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        self.setFocus()
        if self._drag is not None:
            if self._drag["glide"] is None:
                return
            self._finish()
        lay, pos = self.plan(), e.position().toPoint()
        found = hit(lay, self._content(pos))
        if found is None:
            return
        kind, ident = found
        if kind == "edge":
            self._wdrag = {
                "key": ident,
                "x": self._content(pos).x(),
                "w": lay["cards"][ident].width() / self.ed.zoom,
            }
        elif kind == "title":
            self._begin("card", ident, ident, pos)
        elif kind == "fold" and ident in self._opened:
            # open for the search only: the document's fold is already shut
            self._shut.add(ident)
            self._filter()
            self._retarget(snap=True)
        elif kind == "fold":
            self.ed.set_fold(m.unit_block(self.ed.document(), ident))
        elif kind == "item":
            self._begin("item", self._card_of(ident), ident, pos)
        elif kind == "head":
            if not m.tables(self.ed.document()):
                self._begin("head", card_at(lay, self._content(pos).x()), ident, pos)

    def _card_of(self, uid):
        return self._by_id[uid]["column"]

    def mouseMoveEvent(self, e):
        pos = e.position().toPoint()
        if self._wdrag is not None:
            w = self._wdrag
            width = clamp_w(w["w"] + (self._content(pos).x() - w["x"]) / self.ed.zoom)
            self._widths[w["key"]] = round(width)
            self._retarget(snap=True)
            return
        d = self._drag
        if d is None or d["glide"] is not None:
            self._edge_cursor(pos)
            return
        d["pos"] = pos
        if not d["live"]:
            if (pos - d["press"]).manhattanLength() < QApplication.startDragDistance():
                return
            self._go_live()
        else:
            self._aim()
        self.viewport().update()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        if self._wdrag is not None:
            self._wdrag = None
            self.widths_changed.emit(dict(self._widths))
            return
        d = self._drag
        if d is None or d["glide"] is not None:
            return
        if not d["live"]:
            self._drag = None
            return
        d["pos"] = e.position().toPoint()
        spot = d["spot"] if self.viewport().rect().contains(d["pos"]) else None
        if d["table"]:
            if spot is None or spot[0] == d["card"]:
                self._cancel()
            else:
                self._drop()
        elif spot is None or self._is_home(spot):
            self._cancel()
        else:
            self._drop()

    def mouseDoubleClickEvent(self, e):
        found = hit(self.plan(), self._content(e.position().toPoint()))
        if found and found[0] == "item":
            self._drag = None
            self.leave.emit(found[1])
        elif found and found[0] == "title" and found[1]:
            self._drag = None
            self._open_edit(found[1])

    def contextMenuEvent(self, e):
        found = hit(self.plan(), self._content(e.pos()))
        if found and found[0] == "title":
            self.title_menu(found[1]).exec(e.globalPos())

    # editing the statuses: the board changes none itself, it emits the whole new list

    def _edit_status(self, key, **fields):
        new = copy.deepcopy(m.STATUS_LIST)
        next(r for r in new if r["key"] == key).update(fields)
        self.statuses_edited.emit(new)

    def _pick_color(self, key):
        c = rdb_color.get_color(self.window(), QColor(m.STATUS_BY_KEY[key]["color"]))
        if c.isValid():
            self._edit_status(key, color=c.name())

    def _add_status(self, after):
        new = copy.deepcopy(m.STATUS_LIST)
        fresh = rdb_status.new_status({r["key"] for r in new})
        new.insert(
            0
            if not after
            else next(i for i, r in enumerate(new) if r["key"] == after) + 1,
            fresh,
        )
        self._pending = fresh["key"]
        if self._order:
            # this list orders its own cards: one it does not hold would land at the end. Sent
            # first, so the refresh after the new list already finds the card in its place
            full = self._full_order()
            full.insert(full.index(after) + 1, fresh["key"])
            self.order_changed.emit(full)
        self.statuses_edited.emit(new)

    def _remove_status(self, key):
        self.statuses_edited.emit(
            [r for r in copy.deepcopy(m.STATUS_LIST) if r["key"] != key]
        )

    def _in_use(self, key):
        # the stored files, and the open document, which may hold edits not stored yet
        self.plan()
        return bool(self.ed.store.status_counts().get(key, 0)) or any(
            i["status"] == key for i in self._raw[0]
        )

    def title_menu(self, c):
        menu = QMenu(self)
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        if c:
            # rdb_settings reaches this module through its preview pane
            from rdb_settings import IconBrowser

            st = m.STATUS_BY_KEY[c]
            icon = menu.addMenu("Icon")
            browser = IconBrowser()
            browser.show_row(st)
            holder = QWidgetAction(icon)
            holder.setDefaultWidget(browser)
            icon.addAction(holder)
            browser.picked.connect(
                lambda name: (menu.close(), self._edit_status(c, icon=name))
            )
            menu.addAction("Color...").triggered.connect(lambda *_: self._pick_color(c))
            done = menu.addAction("Done")
            done.setCheckable(True)
            done.setChecked(st["done"])
            done.triggered.connect(lambda on: self._edit_status(c, done=on))
            menu.addSeparator()
        menu.addAction("Add Status").triggered.connect(lambda *_: self._add_status(c))
        if c:
            remove = menu.addAction("Remove Status")
            remove.setEnabled(not self._in_use(c))
            remove.triggered.connect(lambda *_: self._remove_status(c))
        return menu

    def _reveal(self, key):
        card, bar = self._layout["cards"][key], self.horizontalScrollBar()
        room = self.viewport().width()
        if card.right() + 1 > bar.value() + room:
            bar.setValue(card.right() + 1 - room)
        if card.left() < bar.value():
            bar.setValue(card.left())

    def _open_edit(self, key):
        self._close_edit()
        edit = _TitleEdit(m.STATUS_BY_KEY[key]["name"], self.viewport())
        edit.selectAll()
        edit.editingFinished.connect(self._commit_edit)
        edit.cancelled.connect(self._close_edit)
        self._edit, self._edit_key = edit, key
        self._place_edit()
        edit.show()
        edit.setFocus()

    def _place_edit(self):
        # over its title wherever the title now is: after a scroll, a zoom or a relayout
        edit = self._edit
        if edit is None:
            return
        if self._edit_key not in self._layout["titles"]:
            self._close_edit()
            return
        z = self.ed.zoom
        title = self.to_view(self._layout["titles"][self._edit_key])
        x = title.left() + round(6 * z) + round(16 * z) + round(4 * z)
        edit.setFont(self._title_font())
        edit.setGeometry(
            x - 2, title.top() + 1, title.right() - x - 2, title.height() - 2
        )

    def _close_edit(self):
        edit, self._edit = self._edit, None
        if edit is not None:
            edit.hide()
            edit.deleteLater()

    def _commit_edit(self):
        edit, key = self._edit, self._edit_key
        if edit is None:
            return
        name = edit.text().strip()
        self._close_edit()
        if name and name != m.STATUS_BY_KEY[key]["name"]:
            self._edit_status(key, name=name)

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        if self._drag is not None or self._wdrag is not None:
            self._drag = self._wdrag = None
            self._retarget()

    def keyPressEvent(self, e):
        d = self._drag
        if e.key() == Qt.Key.Key_Escape and d is not None:
            if d["glide"] is None:
                if d["live"]:
                    self._cancel()
                else:
                    self._drag = None
        elif e.matches(QKeySequence.StandardKey.Undo):
            self.ed.undo()
        elif e.matches(QKeySequence.StandardKey.Redo):
            self.ed.redo()
        else:
            super().keyPressEvent(e)

    def wheelEvent(self, e):
        d = e.angleDelta()
        dx, dy = d.x(), d.y()
        if e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            dx, dy = dx or dy, 0
        for bar, delta in (
            (self.horizontalScrollBar(), dx),
            (self.verticalScrollBar(), dy),
        ):
            if delta:
                bar.setValue(bar.value() - round(delta / 120 * bar.singleStep() * 3))
        e.accept()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        # the layout does not depend on the viewport: only the scroll ranges change, and a
        # scrollbar appearing under a drag must not end it
        if self._layout is None:
            self.refresh()
        else:
            self._retarget(snap=True)

    def showEvent(self, e):
        super().showEvent(e)
        self.refresh()
