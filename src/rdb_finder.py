import re
from collections import namedtuple
from functools import lru_cache
from itertools import groupby

from PySide6.QtCore import (
    QAbstractListModel,
    QEvent,
    QModelIndex,
    QPoint,
    QPointF,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QPainter,
    QPen,
    QPolygonF,
    QTextCursor,
    QTextDocument,
    QTextFormat,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractSlider,
    QApplication,
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionToolButton,
    QStylePainter,
    QTextEdit,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

import rdb_model as m
import rdb_theme
from rdb_contacts import FIELDS, cell_text, count_text, search_terms
from rdb_editor import MARK_NAMES, Editor, ItemHighlighter, paint_marker

LIMIT = 300
CHAR, CONSEC, WORD, PREFIX, FULL = 16, 12, 10, 20, 20
GAP_OPEN, GAP_EXT, GAP_MAX = 5, 2, 20
HIER = 30
# a small lift for the items marked P1..P3
PRIO_BOOST = {"p1": 6, "p2": 4, "p3": 2}
LEVEL = {
    "board": 60,
    "card": 60,
    "h1": 45,
    "h2": 30,
    "h3": 15,
    "item": 0,
    "done": 0,
    "text": 0,
    "note": 0,
    "contact": 0,
}
HEADS = frozenset(("board", "card", "h1", "h2", "h3"))
ITEMS = frozenset(("item", "done", "note", "contact"))
BLOCKS = frozenset(("h1", "h2", "h3", "item", "done"))
SEP = frozenset(r" _-./\:>|()[]{},;#@")
SUBSTRING_TRIES = 4
FULL_WIDTH = QTextFormat.Property.FullWidthSelection
MARK_BG, FLASH_BG = "#ffff00", "#ffe680"
FLASH_MS, PANE_DELAY, MAX_SELECTIONS, MAX_SNIPPETS = 1000, 20, 300, 20
BADGE_W = 28
BADGE_FONT = QFont("Tahoma", 8, QFont.Weight.Bold)
# the 9x11 sheet with its top right corner folded over 3 px that the editor draws for a note
NOTE_EDGE = (
    (0, 0, 6, 1),
    (0, 10, 9, 1),
    (0, 0, 1, 11),
    (8, 3, 1, 8),
    (6, 1, 1, 1),
    (7, 2, 1, 1),
    (5, 1, 1, 3),
    (6, 3, 2, 1),
)
LINE_SEP = chr(0x2028)
MOVES = {
    Qt.Key.Key_Up: -1,
    Qt.Key.Key_Down: 1,
    Qt.Key.Key_Home: "home",
    Qt.Key.Key_End: "end",
}
# the pane the movement keys drive shows a highlight-coloured frame
PANES = ("results", "items", "tree")
CTRL_SHIFT = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier

# kind: board card | block (heading or item) | text | note | contact (block: its row); board_path:
# board ids from the map
Target = namedtuple("Target", "kind board_path pid block")
# hay: more strings a query matches besides the text (a contact's company, email, numbers, tags)
Entry = namedtuple(
    "Entry",
    "kind text path trail target board_path pid block tags urgent n dates card order prio hay since",
    defaults=("", (), ""),
)
Hit = namedtuple("Hit", "entry score marks")
Query = namedtuple("Query", "terms tags owners urgent heads done")


def parse_doc(doc):
    numbers = {b.blockNumber(): n for b, n in m.numbered(doc)}
    recs, chain = [], ()
    for b in m.iter_blocks(doc):
        if m.is_section(b):
            chain = ()
        elif m.is_heading(b):
            lvl, text = m.heading_level(b), m.item_text(b)
            if lvl not in (1, 2, 3):
                raise ValueError(f"heading level {lvl!r} in block {b.blockNumber()}")
            if not text:
                continue
            chain = tuple(c for c in chain if c[0] < lvl)
            recs.append(
                (f"h{lvl}", text, b.blockNumber(), chain, None, False, ("", ""), "", "")
            )
            chain += ((lvl, text, b.blockNumber()),)
        elif m.is_todo(b):
            text = m.item_text(b)
            if not text:
                continue
            meta, done = m.get_meta(b), m.is_checked(b)
            recs.append(
                (
                    "done" if done else "item",
                    text,
                    b.blockNumber(),
                    chain,
                    numbers.get(b.blockNumber()),
                    bool(meta["u"]) and not done,
                    (meta["c"], meta["d"]),
                    m.mark_of(meta),
                    # the day it became P1: when it was never recorded, the day it was made
                    meta.get("ud") or meta["c"],
                )
            )
            if nt := m.note_link(b):
                # the text is the note document's pid and the item's excerpt; build_index reads the note
                recs.append(
                    (
                        "note",
                        (nt, m.excerpt(text)),
                        b.blockNumber(),
                        chain,
                        None,
                        False,
                        ("", ""),
                        "",
                        "",
                    )
                )
    return recs


def build_index(store, settings, cache, maps, live=None):
    stamps = store.project_stamps()
    entries, kept = [], {}

    def add(kind, text, path, trail, target, board_path, pid, **kw):
        fields = {"block": None, "tags": (), "urgent": False, "n": None}
        fields |= {"dates": ("", ""), "card": None, **kw}
        entries.append(
            Entry(
                kind,
                text,
                path,
                trail,
                target,
                board_path,
                pid,
                order=len(entries),
                **fields,
            )
        )

    def records(pid, mode):
        if live is not None and live[0] == pid:
            return parse_doc(live[1])
        key = (stamps[pid], mode)
        hit = cache.get(pid)
        if hit is None or hit[0] != key:
            hit = (key, parse_doc(m.open_doc(store, pid, mode)))
        kept[pid] = hit
        return hit[1]

    def note_body(nt):
        key = stamps[nt]
        hit = cache.get(nt)
        if hit is None or hit[0] != key:
            hit = (key, " ".join(m.html_text(store.get_project(nt)["html"]).split()))
        kept[nt] = hit
        return hit[1]

    by_path = {path: (titles, mp) for path, titles, mp in maps}
    queue = [()]
    # board depth first, then reading order on the board; a card is followed by its headings and items
    for path in queue:
        titles, mp = by_path[path]
        trail = tuple(
            Target("board", path[: i + 1], None, None) for i in range(len(path))
        )
        placed = [(c["y"], c["x"], 0, i, c) for i, c in enumerate(mp["cards"])] + [
            (b["y"], b["x"], 1, i, b)
            for i, b in enumerate(mp["blocks"])
            if b["kind"] == "text"
        ]
        for _, _, is_text, _, el in sorted(placed, key=lambda t: t[:4]):
            if is_text:
                bid = el["id"]
                hit = cache.get(bid)
                if hit is None or hit[0] != el["html"]:
                    hit = (el["html"], " ".join(m.html_text(el["html"]).split()))
                kept[bid] = hit
                if hit[1]:
                    own = Target("text", path, bid, None)
                    add("text", hit[1], titles, trail, own, path, bid)
                continue
            c = el
            pid, tags = c["id"], tuple(c["tags"])
            if c["type"] == "board":
                own = Target("board", (*path, pid), None, None)
                add(
                    "board",
                    c["title"],
                    titles,
                    trail,
                    own,
                    path,
                    pid,
                    tags=tags,
                    card=c,
                )
                queue.append((*path, pid))
                continue
            own = Target("card", path, pid, None)
            add("card", c["title"], titles, trail, own, path, pid, tags=tags, card=c)
            if c["type"] == "contacts":
                for row, r in enumerate(
                    store.get_contacts(pid) if store.has_contacts(pid) else ()
                ):
                    label = (
                        r["name"]
                        or r["company"]
                        or r["email"]
                        or r["phone"]
                        or r["mobile"]
                    )
                    if label:
                        add(
                            "contact",
                            label,
                            (*titles, c["title"]),
                            (*trail, own),
                            Target("contact", path, pid, row),
                            path,
                            pid,
                            tags=(*tags, *r["tags"]),
                            hay=search_terms(r),
                            card=c,
                        )
                continue
            if c["type"] == "notepad":
                # the text page is one entry, found by its body; its face is that body
                if pid in stamps and (body := note_body(pid)):
                    add(
                        "note",
                        body,
                        (*titles, c["title"]),
                        (*trail, own),
                        own,
                        path,
                        pid,
                        tags=tags,
                        card=c,
                    )
                continue
            if c["type"] not in m.PROJECT_TYPES or (
                pid not in stamps and (live is None or live[0] != pid)
            ):
                continue
            base = (*titles, c["title"])
            base_trail = (*trail, own)
            mode = m.done_mode(c["done"], settings)
            for kind, text, block, chain, n, urgent, dates, prio, since in records(
                pid, mode
            ):
                p = base + tuple(h[1] for h in chain)
                tr = base_trail + tuple(Target("block", path, pid, h[2]) for h in chain)
                if kind == "note":
                    # path: list > item excerpt > Note; an empty note has nothing to find
                    nt, item = text
                    if nt in stamps and (body := note_body(nt)):
                        own = Target("note", path, nt, None)
                        item_at = Target("block", path, pid, block)
                        add(
                            "note",
                            body,
                            (*p, item, "Note"),
                            (*tr, item_at, own),
                            own,
                            path,
                            nt,
                            tags=tags,
                            card=c,
                        )
                    continue
                add(
                    kind,
                    text,
                    p,
                    tr,
                    Target("block", path, pid, block),
                    path,
                    pid,
                    block=block,
                    tags=tags,
                    urgent=urgent,
                    n=n,
                    dates=dates,
                    prio=prio,
                    since=since,
                    card=c,
                )
    cache.clear()
    cache.update(kept)
    return entries


def today_entries(entries, day):
    return [e for e in entries if e.kind in ("item", "done") and day in e.dates]


def card_entries(entries, exclude):
    return [
        e
        for e in entries
        if e.kind == "card" and e.card["type"] in m.PROJECT_TYPES and e.pid != exclude
    ]


def parse_query(q):
    terms, tags, owners = [], [], []
    urgent = heads = done = False
    for tok in q.lower().split():
        rest = tok[1:]
        if tok[0] == "#":
            tags.append(rest)
            continue
        if tok[0] == "@":
            if rest:
                owners.append(rest)
            continue
        if tok[0] == "!":
            urgent = True
        elif tok[:2] == "h:":
            heads, rest = True, tok[2:]
        elif tok[:2] == "x:":
            done, rest = True, tok[2:]
        else:
            rest = tok
        if rest:
            terms.append(rest)
    return Query(tuple(terms), tuple(tags), tuple(owners), urgent, heads, done)


def blank(q):
    return not (q.terms or q.tags or q.owners or q.urgent or q.heads or q.done)


def _extends(old, new):
    return all(any(n.startswith(o) for n in new) for o in old)


def narrows(old, new):
    # every entry that satisfies `new` also satisfied `old`, so the old survivors are enough;
    # a blank query dropped the items, so only another blank query may reuse it
    if blank(old):
        return blank(new)
    return (
        _extends(old.terms, new.terms)
        and _extends(old.tags, new.tags)
        and _extends(old.owners, new.owners)
        and old.urgent <= new.urgent
        and old.heads <= new.heads
        and old.done <= new.done
    )


def match(t, s):
    # best (score, positions) of term t as unbroken text in s, both lowercase, or None
    n = len(t)
    at = s.find(t)
    if at < 0:
        return None
    best = None
    for _ in range(SUBSTRING_TRIES):
        sc = CHAR * n + CONSEC * (n - 1)
        if at == 0 or s[at - 1] in SEP:
            sc += WORD
        if at == 0:
            sc += PREFIX
        if n == len(s):
            sc += FULL
        if best is None or sc > best[0]:
            best = (sc, at)
        at = s.find(t, at + 1)
        if at < 0:
            break
    return best[0], tuple(range(best[1], best[1] + n))


def fuzzy_match(t, s):
    # match, else t as a subsequence of s; for tag names, which are short
    if (r := match(t, s)) is not None:
        return r
    n = len(t)
    j = -1
    for ch in t:
        j = s.find(ch, j + 1)
        if j < 0:
            return None
    pos = [0] * n
    end = j + 1
    for k in range(n - 1, -1, -1):
        end = s.rfind(t[k], 0, end)
        pos[k] = end
    score, prev = CHAR * n, None
    for p in pos:
        if p == 0 or s[p - 1] in SEP:
            score += WORD
        if prev is not None:
            gap = p - prev - 1
            score += (
                CONSEC if gap == 0 else -min(GAP_MAX, GAP_OPEN + GAP_EXT * (gap - 1))
            )
        prev = p
    if pos[0] == 0:
        score += PREFIX
    return score, tuple(pos)


def term_match(t, low, segs):
    # (score, text positions, matched in the path) of the best place term t matches, or None
    r = match(t, low)
    if r is not None:
        return r[0], r[1], False
    path = None
    for s in segs:
        p = match(t, s)
        if p is not None and (path is None or p[0] > path[0]):
            path = p
    return None if path is None else (path[0] // 2, (), True)


def whole_word(t):
    return re.compile(rf"(?<!\w){re.escape(t)}(?!\w)")


def whole_match(rx, s):
    # best (score, positions) of the whole-word occurrences of a compiled term in s, or None
    best = None
    for _, mo in zip(range(SUBSTRING_TRIES), rx.finditer(s)):
        at, n = mo.start(), mo.end() - mo.start()
        sc = (
            CHAR * n
            + CONSEC * (n - 1)
            + WORD
            + PREFIX * (at == 0)
            + FULL * (n == len(s))
        )
        if best is None or sc > best[0]:
            best = (sc, at, n)
    return None if best is None else (best[0], tuple(range(best[1], best[1] + best[2])))


def whole_term_match(rx, low, segs):
    # term_match for whole words: the text, else the best path segment, which marks nothing
    if (r := whole_match(rx, low)) is not None:
        return r[0], r[1], False
    path = max((p[0] for s in segs if (p := whole_match(rx, s))), default=None)
    return None if path is None else (path // 2, (), True)


class Ranker:
    def __init__(self, entries):
        # path segments repeat across thousands of entries
        lower = lru_cache(maxsize=None)(str.lower)
        self.rows = [
            (
                e,
                lower(e.text),
                tuple(lower(s) for s in e.path) + tuple(lower(h) for h in e.hay),
                tuple(
                    lower(s)
                    for s, t in zip(e.path, e.trail)
                    if t.kind in ("board", "card")
                )
                + ((lower(e.text),) if e.kind in ("board", "card") else ()),
            )
            for e in entries
        ]
        # an index with structure lists only that until a query asks for items (Today has none)
        self.browse = any(e.kind not in ITEMS for e in entries)
        self.last, self.count, self.whole = None, len(entries), False
        self.completed, self.marks = True, frozenset()
        self.by_pid = {}
        for r in self.rows:
            if r[0].block is not None:
                self.by_pid.setdefault(r[0].pid, []).append(r)

    def set_whole(self, flag):
        self.whole, self.last = flag, None

    def set_completed(self, flag):
        self.completed, self.last = flag, None

    def set_marks(self, marks):
        self.marks, self.last = frozenset(marks), None

    def _rxs(self, query):
        return tuple(whole_word(t) for t in query.terms) if self.whole else None

    def _score(self, query, row, rxs):
        e, low, segs, owners = row
        if (
            (query.urgent and not e.urgent)
            or (e.kind == "done" and not (self.completed or query.done))
            or (query.heads and e.kind not in HEADS)
            or (query.done and e.kind != "done")
            or (self.marks and e.prio not in self.marks)
            or not all(
                (
                    any(t.startswith(g) for t in e.tags)
                    or any(t.startswith("#" + g) for t in m.TAG_RX.findall(low))
                )
                if g
                else e.tags
                for g in query.tags
            )
            or not all(any(match(o, s) for s in owners) for o in query.owners)
        ):
            return None
        boost = 0 if e.kind == "done" else PRIO_BOOST.get(e.prio, 0)
        score, parts, via = LEVEL[e.kind] + boost, [], False
        for i, t in enumerate(query.terms):
            r = (
                term_match(t, low, segs)
                if rxs is None
                else whole_term_match(rxs[i], low, segs)
            )
            if r is None:
                return None
            score += r[0]
            parts.append(r[1])
            via = via or r[2]
        return score + HIER * via, parts

    @staticmethod
    def _order(e, score):
        return e.kind in ITEMS, e.kind == "done", -score, len(e.path), e.order

    def rank(self, q, limit=LIMIT):
        # (structure hits, item hits): two lists for the two panes, each best first and capped apart
        query = parse_query(q)
        rows, rxs = self.rows, self._rxs(query)
        # whole words do not narrow: "fo" and "foo" match different entries; with completed
        # items left out, x: brings in items the last survivors never held
        if (
            not self.whole
            and self.last is not None
            and narrows(self.last[0], query)
            and (self.completed or self.last[0].done == query.done)
        ):
            rows = self.last[1]
        if blank(query) and self.browse and not self.marks:
            keep = [r for r in rows if r[0].kind not in ITEMS]
            self.last, self.count = (query, keep), len(keep)
            return [Hit(r[0], 0, ()) for r in keep[:limit]], []
        keep, out = [], []
        for row in rows:
            got = self._score(query, row, rxs)
            if got is not None:
                keep.append(row)
                out.append((self._order(row[0], got[0]), row[0], got))
        self.last, self.count = (query, keep), len(keep)
        out.sort(key=lambda t: t[0])
        lists = ([], [])
        for _, e, (score, parts) in out:
            side = lists[e.kind in ITEMS]
            if len(side) < limit:
                side.append(Hit(e, score, marks_of(parts)))
        return lists

    def snippets(self, q, pid):
        # the headings and items of one document that match in their own text (or pass the filters)
        query = parse_query(q)
        if blank(query):
            return []
        out, rxs = [], self._rxs(query)
        for row in self.by_pid.get(pid, ()):
            got = self._score(query, row, rxs)
            if got is None:
                continue
            marks = marks_of(got[1])
            if marks or not query.terms:
                out.append((self._order(row[0], got[0]), Hit(row[0], got[0], marks)))
        out.sort(key=lambda t: t[0])
        return [h for _, h in out]


def marks_of(parts):
    return tuple(sorted({i for p in parts for i in p}))


def term_selections(doc, terms, whole=False):
    out = []
    flags = (
        QTextDocument.FindFlag.FindWholeWords if whole else QTextDocument.FindFlag(0)
    )
    for t in terms:
        c = QTextCursor(doc)
        while len(out) < MAX_SELECTIONS:
            c = doc.find(t, c, flags)
            if c.isNull():
                break
            out.append(_selection(c, MARK_BG))
    return out


def _selection(cursor, colour, full=False):
    s = QTextEdit.ExtraSelection()
    s.cursor = cursor
    s.format.setBackground(rdb_theme.shade(colour))
    if full:
        s.format.setProperty(FULL_WIDTH, True)
    return s


def flash(editor, number):
    block = editor.document().findBlockByNumber(number)
    if not block.isValid():
        raise ValueError(f"no block {number}")
    editor.setExtraSelections([_selection(QTextCursor(block), FLASH_BG, True)])
    timer = editor.findChild(QTimer, "flash")
    if timer is None:
        timer = QTimer(editor)
        timer.setObjectName("flash")
        timer.setSingleShot(True)
        timer.timeout.connect(lambda: editor.setExtraSelections([]))
    timer.start(FLASH_MS)


class HitModel(QAbstractListModel):
    def __init__(self):
        super().__init__()
        self.hits = []

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.hits)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and index.isValid():
            return self.hits[index.row()].entry.text
        return None

    def set_hits(self, hits):
        self.beginResetModel()
        self.hits = hits
        self.endResetModel()


class RowDelegate(QStyledItemDelegate):
    def __init__(self, settings):
        super().__init__()
        self.settings = settings

    def sizeHint(self, option, index):
        return QSize(0, 2 * QFontMetrics(option.font).height() + 6)

    def paint(self, p, option, index):
        hit = index.model().hits[index.row()]
        e, s, r, pal = hit.entry, self.settings, option.rect, option.palette
        sel = bool(option.state & QStyle.StateFlag.State_Selected)
        # the list that is not driving keeps its current row marked, only fainter
        faint = (
            sel
            and option.widget is not None
            and option.widget.property("active") is False
        )
        sel = sel and not faint
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(r, pal.highlight() if sel else pal.base())
        if faint:
            tint = QColor(pal.highlight().color())
            tint.setAlpha(60)
            p.fillRect(r, tint)
        fg = pal.highlightedText().color() if sel else pal.text().color()
        if e.kind == "done" and not sel:
            fg = QColor(m.shown(s, "checked_color"))
        dim = QColor(fg)
        dim.setAlpha(150)
        if e.urgent:
            p.fillRect(
                QRectF(r.left(), r.top(), 3, r.height()),
                QColor(m.shown(s, "urgent_color")),
            )
        badge = QRectF(r.left() + 6, r.center().y() - 8, BADGE_W, 16)
        self.paint_badge(p, badge, e, fg, sel)
        font = option.font
        fm = QFontMetrics(font)
        left, right = badge.right() + 8, r.right() - 6
        self.paint_marked(
            p,
            QPointF(left, r.top() + 3 + fm.ascent()),
            right,
            e.text,
            hit.marks,
            font,
            fg,
        )
        y2 = r.top() + 3 + fm.height() + fm.ascent()
        p.setFont(font)
        p.setPen(dim)
        path = fm.elidedText(
            " > ".join(("Map", *e.path)),
            Qt.TextElideMode.ElideMiddle,
            int(right - left),
        )
        p.drawText(QPointF(left, y2), path)
        p.restore()

    def paint_marked(self, p, at, right, text, marks, font, colour):
        ms, x, fm = set(marks), at.x(), QFontMetrics(font)
        p.setFont(font)
        i = 0
        while i < len(text) and x < right:
            on, j = i in ms, i
            while j < len(text) and (j in ms) == on:
                j += 1
            run = fm.elidedText(text[i:j], Qt.TextElideMode.ElideRight, int(right - x))
            w = fm.horizontalAdvance(run)
            fill = rdb_theme.shade(MARK_BG)
            if on:
                p.fillRect(QRectF(x, at.y() - fm.ascent(), w, fm.height()), fill)
            # the marker colour is the same whatever the row colour, so the marked text has its own ink
            p.setPen(rdb_theme.ink_for(fill) if on else colour)
            p.drawText(QPointF(x, at.y()), run)
            x += w
            if run != text[i:j]:
                break
            i = j

    def paint_badge(self, p, r, e, fg, sel):
        s, k = self.settings, e.kind
        box = r.adjusted(0.5, 0.5, -0.5, -0.5)
        if k in ("h1", "h2", "h3"):
            fill, edge = (
                (QColor(m.shown(s, "h1_bg")), QColor(m.shown(s, "h1_border")))
                if k == "h1"
                else (rdb_theme.color("badge_fill"), rdb_theme.color("badge_edge"))
            )
            p.setPen(QPen(edge, 1))
            p.setBrush(fill)
            p.drawRoundedRect(box, 3, 3)
            p.setFont(BADGE_FONT)
            p.setPen(rdb_theme.ink_for(fill))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, k.upper())
            return
        ink = fg if sel else QColor(m.shown(s, "number_color"))
        p.setPen(QPen(ink, 1.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        c = r.center()
        if k in ("item", "done"):
            if e.n:
                p.setFont(BADGE_FONT)
                p.drawText(r, Qt.AlignmentFlag.AlignCenter, str(e.n))
            else:
                pen = QPen(ink, 1.8)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                p.setPen(pen)
                p.drawPolyline(
                    QPolygonF(
                        [
                            QPointF(c.x() - 5, c.y()),
                            QPointF(c.x() - 2, c.y() + 3.5),
                            QPointF(c.x() + 4, c.y() - 4),
                        ]
                    )
                )
        elif k == "card":
            p.drawRoundedRect(QRectF(c.x() - 7, c.y() - 5, 14, 10), 2, 2)
            p.drawLine(QPointF(c.x() - 4, c.y() - 2), QPointF(c.x() + 4, c.y() - 2))
        elif k == "board":
            for dx in (-6, 0):
                for dy in (-5, 0):
                    p.drawRect(QRectF(c.x() + dx, c.y() + dy, 5, 4.5))
        elif k == "contact":
            p.drawEllipse(QPointF(c.x(), c.y() - 3), 3, 3)
            p.drawArc(QRectF(c.x() - 6, c.y() + 2, 12, 10), 0, 180 * 16)
        elif k == "note":
            x, y = int(c.x()) - 4, int(c.y()) - 5
            p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            for rx, ry, w, h in NOTE_EDGE:
                p.fillRect(x + rx, y + ry, w, h, ink)
        else:
            for dy, half in ((-4, 7), (0, 7), (4, 4)):
                p.drawLine(
                    QPointF(c.x() - 7, c.y() + dy),
                    QPointF(c.x() - 7 + 2 * half, c.y() + dy),
                )


def beside(block, step):
    n = block.previous() if step < 0 else block.next()
    while n.isValid() and not n.isVisible():
        n = n.previous() if step < 0 else n.next()
    return n if n.isValid() else block


def reveal(doc, numbers):
    for n in numbers:
        b = doc.findBlockByNumber(n)
        if not b.isValid():
            raise ValueError(f"no block {n}")
        if not b.isVisible():
            # a done item inside a collapsed or hidden Completed section, or a block in a folded heading
            m.open_to(doc, b)
            m.apply_view(doc, "stay")


class PreviewEditor(Editor):
    local_zoom = True

    def __init__(self, store, settings):
        super().__init__(store, settings)
        self.setReadOnly(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def keyPressEvent(self, e):
        e.ignore()

    def mousePressEvent(self, e):
        if (
            e.button() == Qt.MouseButton.MiddleButton
            and e.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            super().mousePressEvent(e)
        e.accept()

    def mouseDoubleClickEvent(self, e):
        e.accept()

    def mouseReleaseEvent(self, e):
        e.accept()

    def contextMenuEvent(self, e):
        e.accept()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.document().setProperty("laid", self.viewport().width())

    def adopt(self, doc):
        # every editor on a document shares its layout, which wraps at the width of the last one to size it
        width = self.viewport().width()
        if self.document() is not doc:
            self.setDocument(doc)
        else:
            self._apply_zoom()
            if doc.property("laid") != width:
                self.setLineWrapColumnOrWidth(0)
        doc.setProperty("laid", width)

    def show_doc(self, doc, links):
        self.links = links
        self.adopt(doc)
        self.verticalScrollBar().setValue(0)


class Snippet(PreviewEditor):
    picked = Signal(object)
    opened = Signal(object)

    def __init__(self, store, settings):
        super().__init__(store, settings)
        self.hit = None
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
        self.setProperty("current", False)

    def wheelEvent(self, e):
        e.ignore()

    def mouseMoveEvent(self, e):
        e.accept()

    def mousePressEvent(self, e):
        e.accept()
        self.picked.emit(self)

    def mouseDoubleClickEvent(self, e):
        e.accept()
        self.opened.emit(self)

    def set_current(self, flag):
        if self.property("current") != flag:
            self.setProperty("current", flag)
            self.style().unpolish(self)
            self.style().polish(self)

    def mark(self, doc):
        block = doc.findBlockByNumber(self.hit.entry.block)
        raw = block.text().replace(LINE_SEP, " ")
        at = block.position() + len(raw) - len(raw.lstrip())
        sels = []
        for _, run in groupby(enumerate(self.hit.marks), lambda t: t[1] - t[0]):
            run = [i for _, i in run]
            c = QTextCursor(doc)
            c.setPosition(at + run[0])
            c.setPosition(at + run[-1] + 1, QTextCursor.MoveMode.KeepAnchor)
            sels.append(_selection(c, MARK_BG))
        self.setExtraSelections(sels)


class SnippetPane(QScrollArea):
    picked = Signal(object)
    opened = Signal(object)

    def __init__(self, store, settings):
        super().__init__()
        self.store, self.settings = store, settings
        self.pool, self.shown, self.args = [], [], None
        self.setWidget(QWidget())
        self.more = QLabel(self.widget())
        self.more.setContentsMargins(6, 2, 6, 2)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        # a constant scrollbar keeps the snippets one width whatever they add up to
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def fill(self, doc, hits, more, links, current):
        self.args = (doc, links)
        self.more.setText(f"+{more}" if more else "")
        self.more.setHidden(not more)
        while len(self.pool) < len(hits):
            sn = Snippet(self.store, self.settings)
            sn.setParent(self.widget())
            sn.picked.connect(self.picked)
            sn.opened.connect(self.opened)
            self.pool.append(sn)
        for sn in self.pool[len(hits) :]:
            sn.hide()
        self.shown = self.pool[: len(hits)]
        for sn, hit in zip(self.shown, hits):
            sn.hit = hit
            sn.set_current(hit.entry is current)
        self.place()
        self.verticalScrollBar().setValue(0)

    def set_current(self, sn):
        for other in self.shown:
            other.set_current(other is sn)

    def place(self):
        doc, links = self.args
        width = self.viewport().width()
        # widths first: sizing an editor lays the shared document out again
        for sn in self.shown:
            sn.links = links
            sn.setGeometry(0, 0, width, 100)
            sn.show()
        for sn in self.shown:
            sn.adopt(doc)
        lay = doc.documentLayout()
        # the scroll range is only current once the whole layout is done
        lay.documentSize()
        y = 0
        for sn in self.shown:
            block = doc.findBlockByNumber(sn.hit.entry.block)
            top = lay.blockBoundingRect(beside(block, -1)).top()
            bottom = lay.blockBoundingRect(beside(block, 1)).bottom()
            frame = sn.height() - sn.viewport().height()
            sn.setGeometry(0, y, width, round(bottom - top) + frame)
            sn.verticalScrollBar().setValue(round(top))
            sn.mark(doc)
            y += sn.height()
        if not self.more.isHidden():
            self.more.setGeometry(0, y, width, self.more.sizeHint().height())
            y += self.more.height()
        self.widget().resize(width, y)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.shown and self.isVisible() and e.oldSize().width() != e.size().width():
            self.place()


def pref_bool(key, raw):
    # an INI file hands a stored bool back as "true" or "false"
    if isinstance(raw, bool):
        return raw
    if raw in ("true", "false"):
        return raw == "true"
    raise ValueError(f"{key}: {raw!r} is not a bool")


def stepped(i, step, last):
    if step == "home":
        return 0
    return last if step == "end" else min(max(i + step, 0), last)


class MarkToggle(QToolButton):
    def __init__(self, mark, k=1):
        super().__init__()
        self.mark, self.k = mark, k
        self.setToolTip(MARK_NAMES[m.MARKS.index(mark)])
        self.setCheckable(True)
        self.setAutoRaise(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.set_k(k)

    def set_k(self, k):
        self.k = k
        self.setFixedSize(round(22 * k), round(20 * k))
        self.update()

    def paintEvent(self, _ev):
        p = QStylePainter(self)
        opt = QStyleOptionToolButton()
        self.initStyleOption(opt)
        p.drawComplexControl(QStyle.ComplexControl.CC_ToolButton, opt)
        paint_marker(p, QRectF(self.rect()), self.mark, self.k)
        if self.isChecked():
            p.setPen(QPen(self.palette().text().color(), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(self.rect().adjusted(0, 0, -1, -1))


def entry_tags(entries):
    # each tag as first written, one per case-insensitive name
    found = {}
    for e in entries:
        for t in (*e.tags, *(t[1:] for t in m.TAG_RX.findall(e.text))):
            found.setdefault(t.lower(), t)
    return sorted(found.values(), key=str.lower)


def list_name(query, marks):
    names = dict(zip(m.MARKS[1:], MARK_NAMES[1:]))
    return " ".join(
        filter(None, [query, *(names[k] for k in m.MARKS[1:] if k in marks)])
    )


class Finder(QDialog):
    def __init__(self, parent, entries, title, store, settings, prefs, query=""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.store, self.settings, self.prefs = store, settings, prefs
        self.ranker, self.total = Ranker(entries), len(entries)
        self.docs, self.nodes, self.targets = {}, [], []
        self.choice = self.target = self.listed = None
        # pane: what the movement keys drive; source: the list whose current row drives the tree and
        # preview; prefer: the list last chosen, which drives whenever it has hits
        self.pane = self.source = self.prefer = "results"
        self.filling = self.forwarding = self.loading = False
        self.tags = None
        self.edit = QLineEdit()
        self.count = QLabel()
        self.count.setContentsMargins(6, 0, 6, 0)
        self.whole = QCheckBox("Whole word")
        self.completed = QCheckBox("Completed")
        self.make = QPushButton("New List")
        self.make.setEnabled(False)
        self.toggles = {k: MarkToggle(k) for k in (*m.PRIOS, *m.STARS)}
        self.model, self.item_model = HitModel(), HitModel()
        self.delegate = RowDelegate(settings)
        self.results, self.items = QListView(), QListView()
        self.lists = {
            "results": (self.results, self.model),
            "items": (self.items, self.item_model),
        }
        for view, model in self.lists.values():
            view.setModel(model)
            view.setItemDelegate(self.delegate)
            view.setUniformItemSizes(True)
            view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(12)
        self.tree.setUniformRowHeights(True)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.preview = PreviewEditor(store, settings)
        self.face = QTextEdit()
        self.face.setReadOnly(True)
        self.face.setFont(m.list_font(settings, "body"))
        self.snips = SnippetPane(store, settings)
        self.stack = QStackedWidget()
        for w in (self.preview, self.face, self.snips):
            self.stack.addWidget(w)
        for w in (
            self.results,
            self.items,
            self.tree,
            self.preview,
            self.face,
            self.whole,
            self.completed,
            self.make,
        ):
            w.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        rdb_theme.follow(self, rdb_theme.pane_sheet)
        self.vsplit = QSplitter(Qt.Orientation.Vertical)
        self.vsplit.addWidget(self.tree)
        self.vsplit.addWidget(self.stack)
        self.lsplit = QSplitter(Qt.Orientation.Vertical)
        self.lsplit.addWidget(self.results)
        self.lsplit.addWidget(self.items)
        self.hsplit = QSplitter(Qt.Orientation.Horizontal)
        self.hsplit.addWidget(self.lsplit)
        self.hsplit.addWidget(self.vsplit)
        for sp in (self.hsplit, self.vsplit, self.lsplit):
            sp.setChildrenCollapsible(False)
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(0)
        top.addStretch(1)
        for b in self.toggles.values():
            top.addWidget(b)
        top.addWidget(self.count)
        top.addWidget(self.whole)
        top.addWidget(self.completed)
        top.addWidget(self.make)
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        col.addWidget(self.edit)
        col.addLayout(top)
        self.tagpop = QListWidget(self)
        self.tagpop.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.tagpop.hide()
        col.addWidget(self.hsplit, 1)
        # after the layout owns them: setVisible(True) on a parentless widget shows it as a window
        # only the Go finder starts lists; Today and Move To are the same dialog
        self.make.setVisible(title == "Go")
        # an index without structure (Today) or without items (Move To) needs one list only
        self.results.setHidden(not self.ranker.browse)
        self.items.setHidden(not any(e.kind in ITEMS for e in entries))
        for b in self.toggles.values():
            # Move To lists cards only, which carry no marker
            b.setVisible(not self.items.isHidden())
        if parent is None:
            self.resize(900, 600)
        else:
            self.resize(parent.width() * 4 // 5, parent.height() * 4 // 5)
            self.move(parent.mapToGlobal(parent.rect().center()) - self.rect().center())
        w, h = self.width(), self.height()
        sizes = {
            "hsplit": [w * 2 // 5, w * 3 // 5],
            "vsplit": [h * 3 // 10, h * 7 // 10],
            "lsplit": [h // 2, h // 2],
        }
        # one list pane alone would save a split that squeezes the other dialogs
        self.splits = [
            (name, sp)
            for name, sp in (
                ("hsplit", self.hsplit),
                ("vsplit", self.vsplit),
                ("lsplit", self.lsplit),
            )
            if name != "lsplit"
            or not (self.results.isHidden() or self.items.isHidden())
        ]
        for name, sp in self.splits:
            state = prefs.value(f"finder/{name}")
            sp.setSizes(sizes[name])
            if state is not None:
                sp.restoreState(state)
        for key, box in (
            ("finder/whole", self.whole),
            ("finder/completed", self.completed),
        ):
            if (raw := prefs.value(key)) is not None:
                box.setChecked(pref_bool(key, raw))
        self.ranker.set_whole(self.whole.isChecked())
        self.ranker.set_completed(self.completed.isChecked())
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(PANE_DELAY)
        self.timer.timeout.connect(self.update_panes)
        self.edit.textChanged.connect(self.refilter)
        self.edit.textChanged.connect(self.update_tagpop)
        self.edit.cursorPositionChanged.connect(self.update_tagpop)
        self.tagpop.itemClicked.connect(lambda _i: self.complete_tag())
        self.whole.toggled.connect(self.toggle_whole)
        self.completed.toggled.connect(self.toggle_completed)
        self.make.clicked.connect(self.new_list)
        for b in self.toggles.values():
            b.toggled.connect(self.toggle_marks)
        self.edit.installEventFilter(self)
        for name, (view, _model) in self.lists.items():
            view.selectionModel().currentChanged.connect(
                lambda _cur, _prev, name=name: self.row_changed(name)
            )
            # a click on the row already current changes no selection, but it picks the list
            view.clicked.connect(lambda _i, name=name: self.clicked_row(name))
            view.doubleClicked.connect(lambda _i: self.open_target())
        self.tree.currentItemChanged.connect(self.node_changed)
        self.tree.itemDoubleClicked.connect(lambda _n, _c: self.open_target())
        self.snips.picked.connect(self.pick_snippet)
        self.snips.opened.connect(self.open_snippet)
        self.set_pane("results")
        self.edit.setText(query)
        self.refilter(query)

    def tag_names(self):
        if self.tags is None:
            self.tags = sorted(
                {t.lower() for t in entry_tags(r[0] for r in self.ranker.rows)}
            )
        return self.tags

    def tag_token(self):
        text, at = self.edit.text(), self.edit.cursorPosition()
        a = text.rfind(" ", 0, at) + 1
        b = text.find(" ", at)
        b = len(text) if b < 0 else b
        return (a, b) if text[a : a + 1] == "#" else None

    def update_tagpop(self, *_):
        tok = self.tag_token()
        names = (
            m.fuzzy_tags(
                self.tag_names(), self.edit.text()[tok[0] + 1 : tok[1]].lower()
            )
            if tok
            else []
        )
        if not names:
            self.tagpop.hide()
            return
        self.tagpop.clear()
        self.tagpop.addItems(names)
        self.tagpop.setCurrentRow(0)
        row = self.tagpop.sizeHintForRow(0)
        h = min(len(names), 8) * row + 2 * self.tagpop.frameWidth()
        at = self.edit.mapTo(self, QPoint(0, self.edit.height()))
        self.tagpop.setGeometry(at.x(), at.y(), self.edit.width(), h)
        self.tagpop.show()
        self.tagpop.raise_()

    def complete_tag(self):
        item, tok = self.tagpop.currentItem(), self.tag_token()
        if item is None or tok is None:
            return
        text = self.edit.text()
        tail = text[tok[1] :]
        self.tagpop.hide()
        self.edit.setText(f"{text[: tok[0]]}#{item.text()} {tail.removeprefix(' ')}")
        self.edit.setCursorPosition(tok[0] + len(item.text()) + 2)

    def toggle_whole(self, flag):
        self.ranker.set_whole(flag)
        self.refilter(self.edit.text())

    def toggle_completed(self, flag):
        self.ranker.set_completed(flag)
        self.refilter(self.edit.text())

    def selected_marks(self):
        return frozenset(k for k, b in self.toggles.items() if b.isChecked())

    def toggle_marks(self, _flag):
        self.ranker.set_marks(self.selected_marks())
        self.refilter(self.edit.text())

    def refilter(self, q):
        heads, items = self.ranker.rank(q)
        self.loading = True
        for (view, model), hits in zip(self.lists.values(), (heads, items)):
            model.set_hits(hits)
            if hits:
                view.setCurrentIndex(model.index(0))
        self.loading = False
        self.count.setText(f"{self.ranker.count} / {self.total}")
        self.make.setEnabled(
            bool(q.strip() or self.ranker.marks)
            and any(h.entry.kind == "item" for h in items)
        )
        self.source = next(
            (n for n in (self.prefer, "results", "items") if self.lists[n][1].hits),
            self.source,
        )
        if self.pane != "tree" and self.pane != self.source:
            self.set_pane(self.source)
        self.retarget()

    def current_hit(self):
        view, model = self.lists[self.source]
        row = view.currentIndex().row()
        return model.hits[row] if row >= 0 else None

    def retarget(self):
        hit = self.current_hit()
        self.target = None if hit is None else hit.entry.target
        self.timer.start()

    def row_changed(self, name):
        if self.loading:
            return
        self.source = self.prefer = name
        if self.pane != name:
            self.set_pane(name)
        self.retarget()

    def clicked_row(self, name):
        if self.isVisible():
            self.row_changed(name)

    def pick_snippet(self, sn):
        self.snips.set_current(sn)
        self.fill_tree(sn.hit.entry)

    def open_snippet(self, sn):
        self.pick_snippet(sn)
        self.open_target()

    def node_target(self, item):
        return self.targets[item.data(0, Qt.ItemDataRole.UserRole)]

    def node_changed(self, item, _prev):
        if item is not None and not self.filling:
            self.target = self.node_target(item)

    def add_node(self, parent, label, target):
        item = QTreeWidgetItem(parent, [label])
        item.setData(0, Qt.ItemDataRole.UserRole, len(self.targets))
        self.targets.append(target)
        self.nodes.append(item)
        return item

    def update_panes(self):
        self.timer.stop()
        hit = self.current_hit()
        self.fill_tree(None if hit is None else hit.entry)
        if hit is None:
            self.show_face("")
        else:
            self.fill_preview(hit)

    def fill_tree(self, e):
        self.filling = True
        self.tree.clear()
        self.nodes, self.targets = [], []
        if e is None:
            self.filling = False
            return
        node = self.add_node(self.tree, "Map", Target("board", (), None, None))
        for label, tr in zip(e.path, e.trail):
            node = self.add_node(node, label, tr)
        own = self.add_node(node, e.text, e.target)
        font = own.font(0)
        font.setBold(True)
        own.setFont(0, font)
        self.tree.expandAll()
        self.filling = False
        self.tree.setCurrentItem(own)

    def doc_of(self, e):
        doc = self.docs.get(e.pid)
        if doc is None:
            doc = self.docs[e.pid] = m.open_doc(
                self.store, e.pid, m.done_mode(e.card["done"], self.settings)
            )
            # the dialog owns the clone, so it outlives every editor that shows it
            doc.setParent(self)
            # the highlighter styles the document on the next event pass, and heading sizes move the
            # layout the snippets are measured on
            QApplication.sendPostedEvents(
                ItemHighlighter(doc, self.settings), QEvent.Type.MetaCall
            )
        return doc

    def raise_pane(self, w):
        # the stack lays a page out on its next layout pass, and the panes measure themselves now
        self.stack.setCurrentWidget(w)
        w.setGeometry(self.stack.contentsRect())

    def fill_preview(self, hit):
        e = hit.entry
        query = self.edit.text()
        project = e.card is not None and e.card["type"] in m.PROJECT_TYPES
        own_row = e.kind in BLOCKS
        if not own_row and not (
            e.kind == "card" and project and self.store.has_project(e.pid)
        ):
            self.show_face(self.face_text(e), parse_query(query).terms)
            return
        doc = self.doc_of(e)
        links = e.card["type"] == "links"
        found = self.ranker.snippets(query, e.pid)
        if own_row:
            found = [hit] + [h for h in found if h.entry is not e]
        shown = found[:MAX_SNIPPETS]
        if not shown:
            self.raise_pane(self.preview)
            self.preview.show_doc(doc, links)
            return
        reveal(doc, [h.entry.block for h in shown])
        self.raise_pane(self.snips)
        self.snips.fill(
            doc, shown, len(found) - len(shown), links, e if own_row else None
        )

    def show_face(self, text, terms=()):
        self.face.setPlainText(text)
        self.face.setExtraSelections(
            term_selections(self.face.document(), terms, self.whole.isChecked())
        )
        self.stack.setCurrentWidget(self.face)

    def face_text(self, e):
        if e.kind == "contact":
            r = self.store.get_contacts(e.pid)[e.target.block]
            lines = [(label, cell_text(r, k)) for k, label in FIELDS]
            return "\n".join(f"{label}  {v}" for label, v in lines if v)
        if e.kind == "board":
            s = m.board_summary(self.store, e.pid, self.settings)
            rows = [
                f"{c['title']}  {c['open']} open"
                + (f"  {c['urgent']} urgent" if c["urgent"] else "")
                for c in s["cards"]
            ]
            return "\n".join([m.state_text(s), *rows])
        if e.kind == "text":
            mp = (
                m.load_board(self.store, e.board_path[-1])
                if e.board_path
                else m.load_map(self.store)
            )
            return m.html_text(
                next(b["html"] for b in mp["blocks"] if b["id"] == e.pid)
            )
        if e.kind == "note":
            return m.html_text(self.store.get_project(e.pid)["html"])
        if e.card["type"] == "notepad":
            if not self.store.has_project(e.pid):
                return ""
            return m.html_text(self.store.get_project(e.pid)["html"])
        if e.card["type"] == "page":
            return e.card["url"]
        if e.card["type"] == "contacts":
            n = (
                len(self.store.get_contacts(e.pid))
                if self.store.has_contacts(e.pid)
                else 0
            )
            return count_text(n)
        return m.state_text(
            {"open": 0, "urgent": 0, "done": 0}, e.card["type"] == "links"
        )

    def set_pane(self, pane):
        self.pane = pane
        for name, w in zip(PANES, (self.results, self.items, self.tree)):
            w.setProperty("active", name == pane)
            w.style().unpolish(w)
            w.style().polish(w)

    def cycle(self, step):
        i = PANES.index(self.pane)
        for _ in PANES:
            i = (i + step) % len(PANES)
            name = PANES[i]
            # an empty pane is passed over
            if self.nodes if name == "tree" else self.lists[name][1].hits:
                break
        else:
            return
        self.set_pane(name)
        if name in self.lists:
            self.source = self.prefer = name
            self.update_panes()

    def eventFilter(self, obj, ev):
        if obj is self.edit and ev.type() == QEvent.Type.KeyPress and self.route(ev):
            return True
        return super().eventFilter(obj, ev)

    def route(self, ev):
        k, mods = ev.key(), ev.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
        if self.tagpop.isVisible() and mods == Qt.KeyboardModifier.NoModifier:
            if k == Qt.Key.Key_Escape:
                self.tagpop.hide()
                return True
            if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Tab):
                self.complete_tag()
                return True
            if k in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                row = self.tagpop.currentRow() + (1 if k == Qt.Key.Key_Down else -1)
                self.tagpop.setCurrentRow(min(max(row, 0), self.tagpop.count() - 1))
                return True
        if k in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            if self.timer.isActive():
                self.update_panes()
            self.cycle(1 if k == Qt.Key.Key_Tab else -1)
        elif k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.open_target()
        elif k == Qt.Key.Key_W and mods == Qt.KeyboardModifier.AltModifier:
            self.whole.toggle()
        elif k == Qt.Key.Key_C and mods == Qt.KeyboardModifier.AltModifier:
            self.completed.toggle()
        elif k == Qt.Key.Key_N and mods == CTRL_SHIFT:
            self.new_list()
        elif mods != Qt.KeyboardModifier.NoModifier:
            return False
        elif k in MOVES:
            if self.timer.isActive():
                self.update_panes()
            self.move(MOVES[k])
        elif k in (Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
            if self.timer.isActive():
                self.update_panes()
            act = (
                QAbstractSlider.SliderAction.SliderPageStepSub
                if k == Qt.Key.Key_PageUp
                else QAbstractSlider.SliderAction.SliderPageStepAdd
            )
            self.stack.currentWidget().verticalScrollBar().triggerAction(act)
        else:
            return False
        return True

    def move(self, step):
        if self.pane in self.lists:
            view, model = self.lists[self.pane]
            last = model.rowCount() - 1
            if last >= 0:
                view.setCurrentIndex(
                    model.index(stepped(view.currentIndex().row(), step, last))
                )
        elif self.nodes:
            i = stepped(
                self.nodes.index(self.tree.currentItem()), step, len(self.nodes) - 1
            )
            self.tree.setCurrentItem(self.nodes[i])

    def new_list(self):
        if self.make.isVisibleTo(self) and self.make.isEnabled():
            q = self.edit.text().strip()
            # every match, not only the rows the panes show
            _, hits = self.ranker.rank(q, limit=len(self.ranker.rows))
            self.listed = (
                list_name(q, self.ranker.marks),
                [h.entry for h in hits if h.entry.kind == "item"],
            )
            self.accept()

    def open_target(self):
        if self.target is not None:
            self.choice = self.target
            self.accept()

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key.Key_Escape or self.forwarding:
            super().keyPressEvent(ev)
            return
        # whatever the dialog receives is typing for the query line
        self.forwarding = True
        try:
            QApplication.sendEvent(self.edit, ev)
        finally:
            self.forwarding = False

    def showEvent(self, ev):
        super().showEvent(ev)
        self.edit.setFocus()
        self.timer.start()

    def done(self, result):
        self.timer.stop()
        for name, sp in self.splits:
            self.prefs.setValue(f"finder/{name}", sp.saveState())
        self.prefs.setValue("finder/whole", self.whole.isChecked())
        self.prefs.setValue("finder/completed", self.completed.isChecked())
        super().done(result)
