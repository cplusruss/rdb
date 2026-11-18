import copy
import json
import math
import shutil
import tempfile
from collections import namedtuple
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from PySide6.QtCore import (
    QObject,
    QPointF,
    QRectF,
    QProcess,
    QProcessEnvironment,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QPainter,
    QPalette,
    QPen,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextFormat,
    QUndoCommand,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QTextEdit,
    QToolButton,
)
from shiboken6 import isValid

import rdb_contacts
import rdb_model as m
import rdb_theme
from rdb_links import MARKS, link_entries
from rdb_map import CELL, MIN_CELLS, snap
from rdb_web import public_host

PREVIEW_CHARS = 200
PANE_LINES = 5
TIMEOUT_S = 120
WEB_TIMEOUT_S = 300
WEB_TOOLS = "WebSearch,WebFetch"
MAX_NEW = 25
ENTER = (Qt.Key.Key_Return, Qt.Key.Key_Enter)
ORANGE = "#D97757"
GREEN, RED = "#2e9e4f", "#d03a3a"
# the spark's twelve rays, each as long as this share of the radius
RAYS = (1.0, 0.7, 0.92, 0.66, 1.0, 0.74, 0.9, 0.68, 0.96, 0.72, 0.88, 0.64)
Applied = namedtuple("Applied", "title marked removed fresh")
New = namedtuple("New", "id url label")
Item = namedtuple("Item", "id text p start")

NARRATE = (
    " Before giving the structured reply, write one or two short plain sentences saying "
    "what will change. If the request needs more than that, say in those sentences that it "
    "cannot be done and give the current state unchanged."
)
REFINE = (
    " This request refines your earlier proposal: your reply replaces it, and it is the "
    "complete final state relative to the original state in the first message."
)
_NUM, _INT = {"type": "number"}, {"type": "integer"}
_STR = {"type": "string"}


def _obj(props):
    return {
        "type": "object",
        "properties": props,
        "required": list(props),
        "additionalProperties": False,
    }


def _list_of(props):
    return {"type": "array", "items": _obj(props)}


REPLY_SCHEMA = {
    "type": "object",
    "properties": {
        "cards": _list_of(
            {"id": _STR, "x": _NUM, "y": _NUM, "w": _INT, "h": _INT, "title": _STR}
        ),
        "frames": _list_of({"title": _STR, "x": _NUM, "y": _NUM, "w": _NUM, "h": _NUM}),
    },
    "required": ["cards", "frames"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = (
    "You rearrange one board of a card map. Reply with the complete new layout in the "
    "required JSON shape: every card of the board exactly once, by its id, with its new "
    "x, y, w, h and title, and the frames. Cards, frames and the read-only blocks must not "
    "overlap, except that a frame may surround the cards it groups. Do not invent or drop cards."
    + NARRATE
)

_PRIO = {"type": "string", "enum": ["", *m.PRIOS]}
_BASE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": _STR,
        "order": {
            "type": "array",
            "items": {
                "anyOf": [
                    _obj({"id": _STR, "p": _PRIO}),
                    _obj(
                        {
                            "heading": _STR,
                            "level": {"type": "integer", "enum": [1, 2, 3]},
                        }
                    ),
                ]
            },
        },
    },
    "required": ["title", "order"],
    "additionalProperties": False,
}


def _with(shape):
    out = copy.deepcopy(_BASE_SCHEMA)
    out["properties"]["order"]["items"]["anyOf"].append(_obj(shape))
    return out


DOC_SCHEMA = _with({"item": _STR, "p": _PRIO})
TIMELINE_SCHEMA = _with({"item": _STR, "p": _PRIO, "start": _STR})
LINKS_SCHEMA = _with({"new": _STR, "label": _STR})

_DOC_LEAD = (
    "You organize the open items of one todo card. Reply in the required JSON shape: "
    "the card title and order, the complete new sequence of the open items and headings. "
    "List every open item exactly once by its id with a priority p of empty, p1, p2, p3 or p4 "
    "(p1 is the most urgent). A heading you keep is listed by its id with an empty p; a heading "
    "you leave out is removed; a new heading is an entry with heading text and a level of 1, 2 "
    "or 3. "
)
_NEW_ITEM = (
    "A new item is an entry with item set to its text and p to its priority, placed anywhere "
    f"in the order, at most {MAX_NEW} per reply. Never rewrite, split, merge or drop "
    "existing items. "
)
DOC_SYSTEM_PROMPT = (
    _DOC_LEAD + _NEW_ITEM + "Start and progress are read-only." + NARRATE
)
TIMELINE_SYSTEM_PROMPT = (
    _DOC_LEAD
    + _NEW_ITEM
    + "A new item's start is a date as YYYY-MM-DD, or empty for none. Start and progress "
    "of existing items are read-only." + NARRATE
)

LINKS_SYSTEM_PROMPT = (
    _DOC_LEAD
    + "A new link is an entry with new set to its URL and label set to its title, placed "
    f"anywhere in the order, at most {MAX_NEW} per reply; never add a URL the card already has. "
    "Never rewrite, split, merge or drop existing items. You may search the web to find links; "
    "return only real URLs you have seen in search results or fetched pages, never one from "
    "memory. Say in your narration what you searched for." + NARRATE
)

CONTACTS_SCHEMA = {
    "type": "object",
    "properties": {
        "order": _list_of({"row": _INT, "tags": {"type": "array", "items": _STR}})
    },
    "required": ["order"],
    "additionalProperties": False,
}

CONTACTS_SYSTEM_PROMPT = (
    "You organize a contact list. Reply in the required JSON shape: every row exactly once "
    "by its row number, in the new order, with its complete list of tags. Every other field is "
    "read-only." + NARRATE
)


def _preview(store, c):
    if c["type"] not in m.DOC_TYPES or not store.has_project(c["id"]):
        return ""
    lines, _ = m._preview(m.html_text(store.get_project(c["id"])["html"]))
    return "\n".join(lines)[:PREVIEW_CHARS]


def board_request(store, board, prompt):
    units = (
        f"Units: card x and y are scene pixels; card w and h are cells of {CELL} pixels "
        f"(minimum {MIN_CELLS[0]} x {MIN_CELLS[1]}); frame x, y, w and h are pixels; "
        f"block x and y are pixels and w and h are cells of {CELL} pixels."
    )
    snapshot = {
        "cards": [
            {
                **{k: c[k] for k in ("id", "title", "type", "x", "y", "w", "h")},
                "preview": _preview(store, c),
            }
            for c in board["cards"]
        ],
        "frames": [
            {k: f[k] for k in ("title", "x", "y", "w", "h")} for f in board["frames"]
        ],
        "blocks": [{k: b[k] for k in ("x", "y", "w", "h")} for b in board["blocks"]],
    }
    return f"{units}\n{json.dumps(snapshot)}\n\nRequest: {prompt}"


def _get(entry, key, kinds, what):
    if not isinstance(entry, dict):
        raise ValueError(f"{what} is not an object")
    if key not in entry:
        raise ValueError(f"{what} lacks {key}")
    v = entry[key]
    if isinstance(v, bool) or not isinstance(v, kinds):
        raise ValueError(f"{what} {key} has the wrong type")
    return v


def _list(reply, key):
    v = reply.get(key) if isinstance(reply, dict) else None
    if not isinstance(v, list):
        raise ValueError(f"reply {key} is not a list")
    return v


def apply_reply(board, reply):
    new = copy.deepcopy(board)
    by_id = {c["id"]: c for c in new["cards"]}
    seen = set()
    for i, r in enumerate(_list(reply, "cards")):
        what = f"card {i + 1}"
        pid = _get(r, "id", str, what)
        if pid in seen:
            raise ValueError(f"duplicate card id {pid}")
        if pid not in by_id:
            raise ValueError(f"reply has unknown card {pid}")
        seen.add(pid)
        x, y = _get(r, "x", (int, float), what), _get(r, "y", (int, float), what)
        w, h = _get(r, "w", int, what), _get(r, "h", int, what)
        title = _get(r, "title", str, what)
        if w < MIN_CELLS[0] or h < MIN_CELLS[1]:
            raise ValueError(f"card {pid} w or h is below the minimum")
        if not title.strip():
            raise ValueError(f"card {pid} title is empty")
        by_id[pid].update(x=float(snap(x)), y=float(snap(y)), w=w, h=h, title=title)
    if missing := set(by_id) - seen:
        raise ValueError(f"reply lacks card {sorted(missing)[0]}")
    frames = []
    for i, r in enumerate(_list(reply, "frames")):
        what = f"frame {i + 1}"
        title = _get(r, "title", str, what)
        x, y = _get(r, "x", (int, float), what), _get(r, "y", (int, float), what)
        w, h = _get(r, "w", (int, float), what), _get(r, "h", (int, float), what)
        if round(w / CELL) < 1 or round(h / CELL) < 1:
            raise ValueError(f"{what} w or h is below the minimum")
        frames.append(
            {
                "title": title,
                "x": float(snap(x)),
                "y": float(snap(y)),
                "w": float(round(w / CELL) * CELL),
                "h": float(round(h / CELL) * CELL),
            }
        )
    new["frames"] = frames
    return new


def _open_units(doc):
    # the open area's headings and unchecked items; Completed is never in it
    return [u for u in m._units(doc) if not u.sec and not m.is_checked(u.blocks[0])]


def doc_basis(doc):
    # what apply_doc_reply overwrites: the open items, todo priorities, heading text and levels
    out = []
    for u in _open_units(doc):
        b = u.blocks[0]
        if m.is_heading(b):
            out.append((u.id, m.item_text(b), m.heading_level(b)))
        else:
            mark = m.marker(b)
            out.append((u.id, mark if mark in m.PRIOS else ""))
    return frozenset(out)


def doc_request(doc, title, kind, prompt):
    items = []
    for u in _open_units(doc):
        b = u.blocks[0]
        mark = m.marker(b)
        e = {
            "id": u.id,
            "text": m.item_text(b)[:PREVIEW_CHARS],
            "p": mark if mark in m.PRIOS else "",
        }
        if m.is_heading(b):
            e["level"] = m.heading_level(b)
        elif kind == "links":
            e["urls"] = list(dict.fromkeys(h for _, _, h in m.link_runs(b)))
        elif kind == "timeline":
            start = m.task_start(b)
            e["start"] = start.isoformat() if start else ""
            e["progress"] = m.get_meta(b).get("tp", 0)
        items.append(e)
    snapshot = {"title": title, "type": kind, "items": items}
    if kind == "timeline":
        snapshot["today"] = m.today()
    return (
        "The open items of one card in order; level marks a heading.\n"
        f"{json.dumps(snapshot)}\n\nRequest: {prompt}"
    )


def _url_key(url):
    p = urlsplit(url.strip())
    return p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"), p.query


def _iso(s):
    try:
        return date.fromisoformat(s).isoformat() == s
    except ValueError:
        return False


def _check_doc(doc, reply, links, timeline=False):
    title = _get(reply, "title", str, "reply")
    if not title.strip():
        raise ValueError("reply title is empty")
    units = _open_units(doc)
    todos = {u.id for u in units if m.is_todo(u.blocks[0])}
    heads = {u.id for u in units if m.is_heading(u.blocks[0])}
    seen, order, urls, items = set(), [], set(), 0
    have = {_url_key(h) for h, _ in link_entries(doc)} if links else set()
    for i, e in enumerate(_list(reply, "order")):
        what = f"entry {i + 1}"
        if not isinstance(e, dict):
            raise ValueError(f"{what} is not an object")
        if "item" in e:
            if "id" in e or "heading" in e or "new" in e:
                raise ValueError(f"{what} is only one of an id, a heading or an item")
            text = _get(e, "item", str, what).strip()
            p = _get(e, "p", str, what)
            if not text:
                raise ValueError(f"{what} item is empty")
            # a newline would make the one item several
            if not text.isprintable():
                raise ValueError(f"{what} item has control characters")
            if p not in ("", *m.PRIOS):
                raise ValueError(f"{what} p is not a priority")
            start = ""
            if timeline:
                start = _get(e, "start", str, what)
                if start and not _iso(start):
                    raise ValueError(f"{what} start is not a date")
            elif "start" in e:
                raise ValueError(f"{what} has a start")
            if items == MAX_NEW:
                raise ValueError(f"reply adds more than {MAX_NEW} items")
            items += 1
            order.append(Item(None, text, p, start))
        elif links and "new" in e:
            if "id" in e or "heading" in e:
                raise ValueError(
                    f"{what} is only one of an id, a heading or a new link"
                )
            url = _get(e, "new", str, what).strip()
            label = _get(e, "label", str, what).strip()
            # urlsplit drops embedded newlines and NULs, so check the raw string first
            if not url.isprintable() or " " in url:
                raise ValueError(f"{what} url has whitespace or control characters")
            parts = urlsplit(url)
            if parts.scheme not in ("http", "https"):
                raise ValueError(f"{what} url scheme is not http or https")
            if not parts.hostname:
                raise ValueError(f"{what} url has no host")
            if not public_host(url):
                raise ValueError(f"{what} url host is not public")
            if not label:
                raise ValueError(f"{what} label is empty")
            # insert_link would split a multi-line label into separate items
            if not label.isprintable():
                raise ValueError(f"{what} label has control characters")
            key = _url_key(url)
            if key in have:
                raise ValueError(f"{what} url is already in the card")
            if key in urls:
                raise ValueError(f"{what} url repeats an earlier entry")
            if len(urls) == MAX_NEW:
                raise ValueError(f"reply adds more than {MAX_NEW} links")
            urls.add(key)
            order.append(New(None, url, label))
        elif ("id" in e) == ("heading" in e):
            raise ValueError(f"{what} needs an id or a heading, not both")
        elif "id" in e:
            uid = _get(e, "id", str, what)
            p = _get(e, "p", str, what)
            if uid in seen:
                raise ValueError(f"duplicate item {uid}")
            if uid not in todos | heads:
                raise ValueError(f"reply has unknown item {uid}")
            seen.add(uid)
            if uid in heads and p:
                raise ValueError(f"heading {uid} has a priority")
            if p not in ("", *m.PRIOS):
                raise ValueError(f"{what} p is not a priority")
            order.append((uid, p))
        else:
            text = _get(e, "heading", str, what).strip()
            level = _get(e, "level", int, what)
            if not text:
                raise ValueError(f"{what} heading is empty")
            if level not in (1, 2, 3):
                raise ValueError(f"{what} level {level} is out of range")
            order.append((None, text, level))
    if missing := todos - seen:
        raise ValueError(f"reply lacks item {sorted(missing)[0]}")
    return title, order, seen


def new_urls(doc, reply, links=True, timeline=False):
    order = _check_doc(doc, reply, links, timeline)[1]
    return [e.url for e in order if isinstance(e, New)]


def refine_request(request, turns, prompt):
    earlier = "".join(
        f"\n\nEarlier request: {p}\nYour reply: {json.dumps(r)}" for p, r in turns
    )
    return f"{request}{earlier}\n\nNew request: {prompt}"


def _append(doc):
    at = QTextCursor(doc)
    at.movePosition(QTextCursor.MoveOperation.End)
    at.insertBlock(QTextBlockFormat(), QTextCharFormat())
    return at


def apply_doc_reply(doc, reply, links=False, timeline=False):
    title, order, keep = _check_doc(doc, reply, links, timeline)
    edit = QTextCursor(doc)
    edit.beginEditBlock()
    try:
        # an item typed since the list was opened has no id yet and went out as ~ and its block
        # number; the ids made here replace those, and nothing was added to the history before
        anon = {
            u.id: u.blocks[0].blockNumber() for u in m._units(doc) if u.id[0] == "~"
        }
        m.ensure_ids(doc)
        real = {k: m.get_meta(doc.findBlockByNumber(n))["i"] for k, n in anon.items()}
        keep = {real.get(i, i) for i in keep}
        order = [(real.get(e[0], e[0]), *e[1:]) if e[0] else e for e in order]
        before = [u.id for u in _open_units(doc)]
        # a checked item of a place-mode list stays under the unit above it
        lead, stuck, last = [], {}, None
        for u in m._units(doc):
            if u.sec:
                break
            if m.is_checked(u.blocks[0]):
                (stuck.setdefault(last, []) if last else lead).append(u.id)
            elif u.id in keep:
                last = u.id
        removed = 0
        for u in m._units(doc):
            if not u.sec and m.is_heading(u.blocks[0]) and u.id not in keep:
                m.remove_block(doc, m.unit_block(doc, u.id))
                removed += 1
        want, fresh, added = list(lead), {}, []
        for e in order:
            uid = e[0]
            if isinstance(e, New):
                # made at the end of the document like a heading
                at = _append(doc)
                m.make_todo(at.block())
                m.insert_link(at, e.label, e.url)
                m.ensure_ids(doc)
                uid = m.get_meta(at.block())["i"]
                fresh[uid] = e.url
            elif isinstance(e, Item):
                at = _append(doc)
                m.make_todo(at.block())
                at.insertText(e.text)
                if e.p:
                    m.set_marker(at.block(), e.p)
                if e.start:
                    m.set_meta(at.block(), ts=e.start, tn=1)
                    m.ensure_task_ids(doc)
                m.ensure_ids(doc)
                uid = m.get_meta(at.block())["i"]
                added.append(uid)
            elif uid is None:
                # made at the end of the document; the arrangement below moves it into place
                at = _append(doc)
                at.insertText(e[1])
                m.make_heading(at.block(), e[2])
                m.ensure_ids(doc)
                uid = m.get_meta(at.block())["i"]
            want += [uid, *stuck.get(uid, [])]
        m._arrange(doc, want)
        reprio = set()
        for uid, *rest in order:
            if uid is None:
                continue
            b = m.unit_block(doc, uid)
            now = m.marker(b)
            if m.is_todo(b) and rest[0] != (now if now in m.PRIOS else ""):
                m.set_marker(b, rest[0])
                reprio.add(uid)
                if m.is_sorted(doc):
                    m.place_item(doc, b)
        if m.is_sorted(doc):
            for uid in added:
                m.place_item(doc, m.unit_block(doc, uid))
        after = [u.id for u in _open_units(doc)]
    finally:
        edit.endEditBlock()
    m.apply_view(doc, doc.property("rdb_mode"))
    # an item is changed when the one above it is not the one that was, or its priority is
    # a new link or item above it does not count as a move
    new = {*fresh, *added}
    kept = [i for i in after if i not in new]
    was, now = dict(zip(before, [None, *before])), dict(zip(kept, [None, *kept]))
    marked = reprio | new | {i for i in kept if i not in was or was[i] != now[i]}
    return Applied(title, marked, removed, fresh)


def doc_tint(doc, ids, dead=()):
    out = []
    for b in m.iter_blocks(doc):
        uid = m.get_meta(b).get("i")
        if m.kind(b) in (m.TODO, m.HEADING) and uid in ids:
            s = QTextEdit.ExtraSelection()
            s.cursor = QTextCursor(b)
            s.format.setBackground(MARKS["dead" if uid in dead else "new"])
            s.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
            out.append(s)
    return out


def board_changes(old, new):
    # ids of the cards moved, resized or retitled, the frames that are new or changed, and the
    # number of changes (a changed frame counts once)
    was = {c["id"]: c for c in old["cards"]}
    keys = ("x", "y", "w", "h", "title")
    cards = {
        c["id"] for c in new["cards"] if any(c[k] != was[c["id"]][k] for k in keys)
    }
    gone = [f for f in old["frames"] if f not in new["frames"]]
    fresh = [f for f in new["frames"] if f not in old["frames"]]
    return cards, fresh, len(cards) + max(len(gone), len(fresh))


def contacts_request(rows, prompt):
    snapshot = {
        "rows": [
            {"row": i, **{k: r[k] for k in rdb_contacts.KEYS}}
            for i, r in enumerate(rows)
        ]
    }
    return f"Every field is read-only except tags.\n{json.dumps(snapshot)}\n\nRequest: {prompt}"


class Reorder(QUndoCommand):
    # show() puts the reply on the table without the stack; the push that accepts it then
    # finds it in place, and undo() alone takes it back with nothing left to redo
    def __init__(self, model, order, tags):
        super().__init__()
        old = list(model.rows)
        self.model, self.shown = model, False
        self.before = (old, [list(r["tags"]) for r in old])
        self.after = ([old[i] for i in order], [list(t) for t in tags])
        self.marked = {
            n
            for n, i in enumerate(order)
            if i != n or self.after[1][n] != self.before[1][i]
        }

    def _set(self, state):
        rows, tags = state
        self.model.beginResetModel()
        self.model.rows[:] = rows
        for r, t in zip(rows, tags):
            r["tags"] = list(t)
        self.model.endResetModel()
        self.model.changed.emit()

    def show(self):
        self._set(self.after)
        self.model.mark(self.marked)
        self.shown = True

    def redo(self):
        if self.shown:
            self.shown = False
        else:
            self._set(self.after)

    def undo(self):
        self.shown = False
        self._set(self.before)


def prepare_contacts_reply(model, reply):
    order, tags = [], []
    for i, e in enumerate(_list(reply, "order")):
        what = f"entry {i + 1}"
        order.append(_get(e, "row", int, what))
        t = _get(e, "tags", list, what)
        if not all(isinstance(x, str) and x.strip() for x in t):
            raise ValueError(f"{what} tags must be non-empty text")
        tags.append([x.strip() for x in t])
    if sorted(order) != list(range(len(model.rows))):
        raise ValueError("reply rows are not exactly the current rows")
    return Reorder(model, order, tags)


def _resolve(program):
    found = shutil.which(program)
    if found is None:
        return None
    p = Path(found)
    # QProcess cannot start npm's claude.cmd shim with JSON arguments; its target is a plain exe
    if p.suffix.lower() in (".cmd", ".bat"):
        exe = p.parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin"
        exe /= "claude.exe"
        return str(exe) if exe.is_file() else None
    return found


class ClaudeRun(QObject):
    finished = Signal(dict)
    failed = Signal(str)
    text = Signal(str)

    def __init__(self, parent=None, program="claude", pre_args=(), timeout_s=None):
        super().__init__(parent)
        # without a timeout of its own, a call has TIMEOUT_S and a web call WEB_TIMEOUT_S
        self.program, self.pre_args, self.timeout_s = program, list(pre_args), timeout_s
        self.request, self.live = b"", False
        self.buf, self.result, self.gap = b"", None, None
        self.proc = QProcess(self)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._timeout)
        # the user's global CLAUDE.md would put its own reply header into the narration
        env = QProcessEnvironment.systemEnvironment()
        env.insert("CLAUDE_CODE_DISABLE_CLAUDE_MDS", "1")
        self.proc.setProcessEnvironment(env)
        self.proc.started.connect(self._send)
        self.proc.readyReadStandardOutput.connect(self._read)
        self.proc.errorOccurred.connect(self._error)
        self.proc.finished.connect(self._done)

    def running(self):
        return self.proc.state() != QProcess.ProcessState.NotRunning

    def start(self, request, schema, system, web=False):
        if self.timeout_s is None:
            self.timeout_s = WEB_TIMEOUT_S if web else TIMEOUT_S
        self.timer.setInterval(round(self.timeout_s * 1000))
        self.request = request.encode("utf-8")
        self.live = True
        self.buf, self.result, self.gap = b"", None, None
        exe = _resolve(self.program)
        if exe is None:
            self._fail(f"{self.program} not found")
            return
        self.proc.setWorkingDirectory(tempfile.gettempdir())
        # stream-json, one JSON object per line, checked against claude 2.1.266:
        #   {"type": "stream_event", "event": {"type": "content_block_delta",
        #    "delta": {"type": "text_delta", "text": ...}}}  narration; the schema reply
        #    arrives as input_json_delta of the StructuredOutput tool, ignored here
        #   {"type": "stream_event", "event": {"type": "content_block_start",
        #    "content_block": {"type": "text" | "tool_use", "name": ...}}}  with the web tools
        #    each search or fetch is one more message
        #   {"type": "result", "is_error": bool, "result": str, "structured_output": dict}
        #    last line; `structured_output` is the --json-schema reply as an object
        # --allowedTools: with it WebSearch and WebFetch ran headless on 2.1.266, no prompt and
        # permission_denials empty; a search turn took about 15 s
        # --setting-sources project: the user's plugins, SessionStart hooks, model and effort
        # settings made a tiny board take 15 s (5 s without); auth does not depend on them
        self.proc.start(
            exe,
            [
                *self.pre_args,
                "-p",
                "--output-format",
                "stream-json",
                "--verbose",
                "--include-partial-messages",
                "--json-schema",
                json.dumps(schema),
                *(
                    ["--tools", WEB_TOOLS, "--allowedTools", WEB_TOOLS]
                    if web
                    else ["--tools", ""]
                ),
                "--no-session-persistence",
                "--setting-sources",
                "project",
                "--strict-mcp-config",
                "--system-prompt",
                system,
            ],
        )
        self.timer.start()

    def cancel(self):
        self.live = False
        self.timer.stop()
        self.proc.kill()
        self.proc.waitForFinished(2000)

    def _send(self):
        self.proc.write(self.request)
        self.proc.closeWriteChannel()

    def _fail(self, msg):
        if self.live:
            self.live = False
            self.timer.stop()
            self.failed.emit(msg)

    def _bad(self, msg):
        self._fail(msg)
        self.proc.kill()

    def _timeout(self):
        self._fail(f"claude timed out after {self.timeout_s} s")
        self.cancel()

    def _error(self, err):
        if err == QProcess.ProcessError.FailedToStart:
            self._fail(f"cannot start {self.program}: {self.proc.errorString()}")
        elif err == QProcess.ProcessError.Crashed:
            self._fail("claude crashed")

    def _read(self):
        self.buf += bytes(self.proc.readAllStandardOutput())
        *lines, self.buf = self.buf.split(b"\n")
        for line in lines:
            if self.live:
                self._line(line)

    def _line(self, line):
        if not line.strip():
            return
        try:
            ev = json.loads(line.decode("utf-8"))
        except ValueError:
            ev = None
        if not isinstance(ev, dict):
            self._bad("reply is not JSON")
        elif ev.get("type") == "result":
            self.result = ev
        elif ev.get("type") == "stream_event":
            event = ev.get("event")
            d = event.get("delta", {}) if isinstance(event, dict) else None
            if not isinstance(d, dict) or (
                d.get("type") == "text_delta" and not isinstance(d.get("text"), str)
            ):
                self._bad("malformed stream event")
            elif d.get("type") == "text_delta":
                self.text.emit((self.gap or "") + d["text"])
                self.gap = ""
            elif event.get("type") == "content_block_start" and self.gap == "":
                # a later message's narration (after a search) starts a new paragraph
                block = event.get("content_block")
                if isinstance(block, dict) and block.get("type") == "text":
                    self.gap = "\n\n"

    def _done(self, code, _status):
        if not self.live:
            return
        if code:
            tail = bytes(self.proc.readAllStandardError()).decode(errors="replace")
            tail = tail.strip().splitlines()
            self._fail(
                f"claude exited {code}" + (f": {tail[-1].strip()}" if tail else "")
            )
            return
        self._read()
        if self.live:
            self._line(self.buf)
        if not self.live:
            return
        env = self.result
        if env is not None and env.get("is_error"):
            self._fail(f"claude: {' '.join(str(env.get('result')).split())}")
        elif env is None or not isinstance(env.get("structured_output"), dict):
            self._fail("no structured output in reply")
        else:
            self.live = False
            self.timer.stop()
            self.finished.emit(env["structured_output"])


class Logo(QLabel):
    def __init__(self):
        super().__init__()
        self.setFixedSize(self.fontMetrics().height(), self.fontMetrics().height())

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.width() / 2
        p.setPen(
            QPen(
                QColor(ORANGE),
                r * 0.24,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
            )
        )
        for k, reach in enumerate(RAYS):
            a = math.radians(30 * k)
            dx, dy = math.cos(a), math.sin(a)
            p.drawLine(
                QPointF(r + dx * r * 0.2, r + dy * r * 0.2),
                QPointF(r + dx * r * 0.86 * reach, r + dy * r * 0.86 * reach),
            )


class Glyph(QToolButton):
    # kind: "accept" (check), "reject" (cross) or "stop" (square)
    def __init__(self, kind):
        super().__init__()
        self.kind = kind
        self.setAutoRaise(True)
        # the edit keeps the focus Enter and Esc need
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        n = self.fontMetrics().height() + 4
        self.setFixedSize(n, n)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        n = self.width()
        if self.underMouse():
            hover = self.palette().buttonText().color()
            hover.setAlpha(25)
            p.fillRect(self.rect(), hover)
        a, b, c = n * 0.28, n * 0.5, n * 0.72
        if self.kind == "stop":
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(self.palette().buttonText())
            p.drawRect(QRectF(a, a, c - a, c - a))
            return
        p.setPen(
            QPen(
                QColor(GREEN if self.kind == "accept" else RED),
                n * 0.12,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        if self.kind == "accept":
            p.drawPolyline(
                [QPointF(a, b), QPointF(n * 0.44, c), QPointF(c + 0.04 * n, a)]
            )
        else:
            p.drawLine(QPointF(a, a), QPointF(c, c))
            p.drawLine(QPointF(c, a), QPointF(a, c))


class Pane(QPlainTextEdit):
    def __init__(self):
        super().__init__()
        self.setReadOnly(True)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.hide()

    def style(self, s):
        self.setFont(m.font_of(s, "claude"))
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Text, QColor(m.shown(s, "claude_fg")))
        bg = QColor(m.shown(s, "claude_bg"))
        pal.setColor(QPalette.ColorRole.Base, bg)
        # windows11 paints a text view's viewport with Window instead of Base; Classic keeps
        # the app's Window, which shows around the viewport
        if not rdb_theme.win11():
            bg = QApplication.palette(self).color(QPalette.ColorRole.Window)
        pal.setColor(QPalette.ColorRole.Window, bg)
        self.setPalette(pal)
        pad = 2 * (self.frameWidth() + self.document().documentMargin())
        self.setFixedHeight(round(PANE_LINES * self.fontMetrics().lineSpacing() + pad))

    def begin(self):
        self.clear()
        self.show()

    def add(self, text):
        self.moveCursor(QTextCursor.MoveOperation.End)
        self.insertPlainText(text)
        self.ensureCursorVisible()

    def refine(self, prompt):
        self.add(("\n\n" if self.toPlainText() else "") + prompt + "\n")

    def reset(self, text):
        self.clear()
        self.add(text)

    def end(self):
        self.clear()
        self.hide()


class _Edit(QLineEdit):
    def __init__(self, bar):
        super().__init__()
        self.bar = bar

    def keyPressEvent(self, e):
        if e.key() in ENTER:
            if self.isReadOnly():
                pass
            elif self.text().strip():
                self.bar.submitted.emit(self.text())
            elif self.bar.reviewing:
                self.bar.accepted.emit()
        elif e.key() == Qt.Key.Key_Escape:
            self.bar.escaped.emit()
        else:
            super().keyPressEvent(e)


class PromptBar(QFrame):
    submitted = Signal(str)
    accepted = Signal()
    escaped = Signal()
    closed = Signal()

    def __init__(self):
        super().__init__()
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.logo = Logo()
        self.edit = _Edit(self)
        self.readout = QLabel()
        self.readout.setMinimumWidth(
            self.fontMetrics().horizontalAdvance("999 changes")
        )
        self.stop_btn, self.accept_btn = Glyph("stop"), Glyph("accept")
        self.reject_btn = Glyph("reject")
        for b, sig in (
            (self.stop_btn, self.escaped),
            (self.accept_btn, self.accepted),
            (self.reject_btn, self.escaped),
        ):
            b.clicked.connect(lambda _=False, s=sig: s.emit())
            b.hide()
        self.reviewing, self.back = False, None
        row = QHBoxLayout(self)
        row.setContentsMargins(4, 2, 4, 2)
        row.setSpacing(6)
        row.addWidget(self.logo)
        row.addWidget(self.edit, 1)
        row.addWidget(self.readout)
        row.addWidget(self.stop_btn)
        row.addWidget(self.accept_btn)
        row.addWidget(self.reject_btn)
        self.hide()

    def open(self):
        if self.isHidden():
            self.back = QApplication.focusWidget()
        self.show()
        self.edit.setFocus()
        self.edit.selectAll()

    def dismiss(self):
        self.hide()
        if self.back is not None and isValid(self.back) and self.back.isVisible():
            self.back.setFocus()
        self.back = None
        self.closed.emit()

    # read-only rather than disabled: a disabled edit drops the focus Esc needs
    def busy(self, on):
        self.edit.setReadOnly(on)
        self.readout.setText("0 s" if on else "")
        self.reviewing = False
        self.stop_btn.setVisible(on)
        self.accept_btn.hide()
        self.reject_btn.hide()

    def review(self, count, dead=0):
        self.edit.setReadOnly(False)
        self.readout.setText(
            f"{count} change{'s' * (count != 1)}" + (f", {dead} dead" if dead else "")
        )
        self.reviewing = True
        self.accept_btn.show()
        self.reject_btn.show()
        # focus may have left during the run; Enter and Esc here are what the lock lets through
        self.edit.setFocus()

    def say(self, text):
        self.readout.setText(text)
