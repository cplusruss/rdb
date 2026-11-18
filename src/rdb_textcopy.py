import os
import re
import tempfile
from pathlib import Path

from PySide6.QtGui import QTextDocument

import rdb_model as m
from rdb_contacts import FIELDS, KEYS, cell_text

WIDTH = 72
FLAGS = ("checked", "subs", "notes", "notepads", "headings", "contacts", "blocks")
BREAK = " "
# an image is its name between these until the format is known: [image: name] in Text, the bare name in Markdown
PIC_OPEN, PIC_CLOSE = "", ""
BASE = 2
SUB_BASE = 3
SUB_STEP = 4
NOTE_COL = 7
MD_STEP = 3
# a structural gap: runs of them collapse to one blank line and none shows at an end
GAP = None
_MD_SPECIAL = re.compile(r"([\\`*_\[\]<>|~#&])")
# a line that opens a block: a bullet, a number with its dot, a rule or a setext underline
_MD_LEAD = re.compile(
    r"^(\s*)(?:([-+])(?=\s|$)|(\d+)([.)])(?=\s|$)|([-=])(?=[-=]*\s*$))"
)


def options(settings):
    return {f: settings[f"text_{f}"] for f in FLAGS}


def boards_of(store, root_title, settings):
    # the boards in the order the Word export walks them; each card is
    # (pid, title, type, mode, lang), sorted by (y, x)
    out = []

    def walk(titles, mp):
        cards = sorted(mp["cards"], key=lambda c: (c["y"], c["x"]))
        blocks = sorted(mp["blocks"], key=lambda b: (b["y"], b["x"]))
        out.append(
            {
                "title": " / ".join(titles),
                "cards": [
                    (
                        c["id"],
                        c["title"],
                        c["type"],
                        m.done_mode(
                            "place" if c["type"] == "timeline" else c["done"], settings
                        ),
                        c["lang"],
                    )
                    for c in cards
                ],
                "blocks": [b for b in blocks if b["kind"] == "text"],
            }
        )
        for c in mp["cards"]:
            if c["type"] == "board":
                walk((*titles, c["title"]), m.load_board(store, c["id"]))

    walk((root_title,), m.load_map(store))
    return out


def _block_lines(block):
    out, it = [], block.begin()
    while not it.atEnd():
        fr = it.fragment()
        it += 1
        if fr.charFormat().isImageFormat():
            name = fr.charFormat().toImageFormat().name()
            out.append(PIC_OPEN + m.image_label(block, name) + PIC_CLOSE)
        else:
            out.append(m.frag_plain(fr))
    return "".join(out).replace(" ", " ").split(BREAK)


def _pictures(lines, fmt):
    if fmt == "text":
        return [
            ln.replace(PIC_OPEN, "[image: ").replace(PIC_CLOSE, "]") for ln in lines
        ]
    return [ln.replace(PIC_OPEN, "").replace(PIC_CLOSE, "") for ln in lines]


def _has_text(lines):
    return any(s.strip() for s in lines)


def _note_lines(store, nid, stamps, deps):
    deps[nid] = stamps.get(nid)
    if not store.has_project(nid):
        return []
    doc = m.open_doc(store, nid, m.NOTE_MODE, readonly=True)
    blocks = (_block_lines(b) for b in m.iter_blocks(doc))
    return [lines for lines in blocks if _has_text(lines)]


def _events(store, doc, o, stamps, deps):
    out = []
    skip, note, section, owned, inline, n = False, "", False, False, [], 0

    def flush():
        nonlocal note, inline
        if note and o["notes"] and not inline:
            inline = _note_lines(store, note, stamps, deps)
        if inline:
            out.append(("note", [ln for ls in inline for ln in ls]))
        note, inline = "", []

    for b in m.iter_blocks(doc):
        k = m.kind(b)
        if k in (m.TODO, m.HEADING, m.SECTION, m.DIVIDER):
            flush()
        lines = _block_lines(b)
        if k == m.SECTION:
            section, owned = True, False
        elif k == m.HEADING:
            skip, owned = False, False
            if o["headings"] and _has_text(lines):
                n = 0
                out.append(("heading", m.heading_level(b), " ".join(lines).strip()))
        elif k == m.TODO:
            owned = True
            checked = m.is_checked(b)
            skip = not _has_text(lines) or (checked and not o["checked"])
            if skip:
                continue
            if checked and section:
                out.append(("section",))
                section, n = False, 0
            n += 1
            note = m.note_link(b)
            urgent = b.blockFormat().boolProperty(m.PROPS["u"])
            out.append(("item", n, urgent, checked, lines))
        elif k == m.LEGACY_NOTE and owned:
            # an inline note of a file not migrated yet: read in place, the file is not rewritten
            if o["notes"] and not skip and _has_text(lines):
                inline.append(lines)
        elif k == m.DIVIDER:
            owned = False
        elif skip or not _has_text(lines):
            continue
        elif b.textList() is not None:
            if o["subs"]:
                out.append(("sub", b.textList().format().indent(), lines))
        else:
            out.append(("plain", lines))
    flush()
    return out


def _lead(mt):
    # the backslash goes before the punctuation: 1\. keeps the digits as text
    if mt.group(3):
        return mt.group(1) + mt.group(3) + "\\" + mt.group(4)
    return mt.group(1) + "\\" + (mt.group(2) or mt.group(5))


def _esc(s, lead=False):
    # lead: the text starts a line of its own, where a bullet, number or rule opens a block
    s = _MD_SPECIAL.sub(r"\\\1", s)
    return _MD_LEAD.sub(_lead, s, 1) if lead else s


def _text_heading(level, text):
    if level == 1:
        inner = max(WIDTH - 2, len(text) + 4)
        rule = "+" + "=" * inner + "+"
        return [GAP, rule, "|" + ("  " + text.upper()).ljust(inner) + "|", rule, GAP]
    return [GAP, text, ("=" if level == 2 else "-") * len(text), GAP]


def _text_event(ev):
    kind = ev[0]
    if kind == "heading":
        return _text_heading(ev[1], ev[2])
    if kind == "section":
        return [GAP, "  -- Completed --"]
    if kind == "item":
        _, n, urgent, checked, lines = ev
        prefix = (
            " " * BASE + f"{n}. [{'x' if checked else ' '}] " + ("! " if urgent else "")
        )
        pad = " " * len(prefix)
        return [prefix + lines[0]] + [pad + ln for ln in lines[1:]]
    if kind == "sub":
        pad = " " * (SUB_BASE + SUB_STEP * ev[1])
        return [pad + "- " + ev[2][0]] + [pad + "  " + ln for ln in ev[2][1:]]
    if kind == "note":
        return [" " * NOTE_COL + "> " + ln for ln in ev[1]]
    return [" " * BASE + ln for ln in ev[1]]


def _md_item_lines(prefix, pad, lines, lead):
    # lead: the first line starts a block (the task box protects an item's own)
    out = [prefix + _esc(lines[0], lead)]
    for ln in lines[1:]:
        out[-1] += "  "
        out.append(pad + _esc(ln, True))
    return out


def _md_event(ev):
    kind = ev[0]
    if kind == "heading":
        return [GAP, "#" * (2 + ev[1]) + " " + _esc(ev[2]), GAP]
    if kind == "section":
        return [GAP, "**Completed**", GAP]
    if kind == "item":
        _, n, urgent, checked, lines = ev
        head = f"{n}. "
        prefix = head + f"[{'x' if checked else ' '}] " + ("**!** " if urgent else "")
        return _md_item_lines(prefix, " " * len(head), lines, False)
    if kind == "sub":
        pad = " " * (MD_STEP * ev[1])
        return _md_item_lines(pad + "- ", pad + "  ", ev[2], True)
    if kind == "note":
        pad = " " * MD_STEP
        return _md_item_lines(pad + "> ", pad + "> ", ev[1], True)
    return _md_item_lines("", "", ev[1], True)


def _doc_body(store, pid, mode, o, stamps, deps, fmt):
    doc = m.open_doc(store, pid, mode, readonly=True)
    # folded and collapsed content is part of the copy; a hidden checked item numbers in place
    doc.setProperty("rdb_folds", [])
    m.set_collapsed(doc, False)
    m.apply_view(doc, "stay" if mode == "hide" and o["checked"] else mode)
    emit = _text_event if fmt == "text" else _md_event
    out = []
    for ev in _events(store, doc, o, stamps, deps):
        out += emit(ev)
    return [ln if ln is GAP else _pictures([ln], fmt)[0] for ln in out]


def _page_lines(store, pid):
    if not store.has_project(pid):
        return []
    doc = m.open_doc(store, pid, m.NOTE_MODE, True, True)
    lines = [ln for b in m.iter_blocks(doc) for ln in _block_lines(b)]
    while lines and not lines[-1].strip():
        lines.pop()
    return [ln.expandtabs(4).rstrip() for ln in lines]


def _page_body(store, pid, lang, fmt):
    lines = _pictures(_page_lines(store, pid), fmt)
    if not lines:
        return []
    if fmt == "text":
        return lines
    if lang:
        return [f"```{lang}", *lines, "```"]
    out = []
    for i, ln in enumerate(lines):
        # a hard break keeps a typed line on its own line
        hard = ln.strip() and i + 1 < len(lines) and lines[i + 1].strip()
        # four spaces would make a code block: the indent is kept as non-breaking spaces
        body = ln.lstrip(" ")
        indent = len(ln) - len(body)
        out.append("&nbsp;" * indent + _esc(body, not indent) + ("  " if hard else ""))
    return out


def _contact_rows(store, pid):
    rows = [
        {k: cell_text(r, k).replace("\n", " ") for k in KEYS}
        for r in store.get_contacts(pid)
    ]
    keys = [k for k, _ in FIELDS if any(r[k] for r in rows)]
    labels = dict(FIELDS)
    return keys, [[labels[k] for k in keys]] + [[r[k] for k in keys] for r in rows]


def _contacts_body(store, pid, fmt):
    keys, rows = _contact_rows(store, pid)
    if len(rows) < 2:
        return []
    if fmt == "markdown":
        cells = [[c.replace("|", "\\|") for c in r] for r in rows]
        out = ["| " + " | ".join(r) + " |" for r in cells]
        out.insert(1, "|" + "|".join(" --- " for _ in keys) + "|")
        return out
    widths = [max(len(r[i]) for r in rows) for i in range(len(keys))]

    def line(r):
        return " " * BASE + "  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip()

    return [
        line(rows[0]),
        " " * BASE + "  ".join("-" * w for w in widths),
        *map(line, rows[1:]),
    ]


def _blocks_body(blocks, fmt):
    out = []
    for b in blocks:
        doc = QTextDocument()
        doc.setHtml(b["html"])
        lines = doc.toPlainText().replace(" ", " ").replace(BREAK, "\n").split("\n")
        if not _has_text(lines):
            continue
        out += [
            GAP,
            *(lines if fmt == "text" else [_esc(ln, True) for ln in lines]),
            GAP,
        ]
    return out


def _board_header(title, fmt):
    if fmt == "markdown":
        return [GAP, "# " + _esc(title), GAP]
    bar = "#" * WIDTH
    return [GAP, bar, "#  " + title, bar, GAP]


def _card_header(title, fmt):
    if fmt == "markdown":
        return [GAP, "## " + _esc(title), GAP]
    head = f"*** {title} "
    return [GAP, head + "*" * max(3, WIDTH - len(head)), GAP]


class Copier:
    # keeps each card's rendered body keyed by the stored content, so one edit re-renders one card
    def __init__(self):
        self.cache = {}

    def _body(self, store, stamps, pid, mode, lang, o, fmt):
        key = (fmt, tuple(o[f] for f in FLAGS), pid, mode, lang)
        stamp = stamps.get(pid)
        hit = self.cache.get(key)
        if (
            hit
            and hit[0] == stamp
            and all(stamps.get(n) == s for n, s in hit[1].items())
        ):
            return hit[2]
        deps = {}
        body = _doc_body(store, pid, mode, o, stamps, deps, fmt)
        self.cache[key] = (stamp, deps, body)
        return body

    def render(self, store, boards, o, fmt):
        if fmt not in ("text", "markdown"):
            raise ValueError(f"format {fmt!r}")
        stamps = store.project_stamps()
        out, live = [], set()
        for board in boards:
            part = []
            for pid, title, kind, mode, lang in board["cards"]:
                if kind in m.PROJECT_TYPES:
                    live.add(pid)
                    body = (
                        self._body(store, stamps, pid, mode, lang, o, fmt)
                        if store.has_project(pid)
                        else []
                    )
                elif kind == "notepad" and o["notepads"]:
                    body = _page_body(store, pid, lang, fmt)
                elif kind == "contacts" and o["contacts"] and store.has_contacts(pid):
                    body = _contacts_body(store, pid, fmt)
                else:
                    continue
                part += _card_header(title, fmt) + body
            if o["blocks"]:
                part += _blocks_body(board["blocks"], fmt)
            # a board with nothing to export has no banner
            if part:
                out += _board_header(board["title"], fmt) + part
        self.cache = {k: v for k, v in self.cache.items() if k[2] in live}
        return _join(out)


def _join(lines):
    out = []
    for ln in lines:
        if ln is GAP:
            if out and out[-1] is not GAP:
                out.append(GAP)
        else:
            out.append(ln)
    while out and out[-1] is GAP:
        out.pop()
    return "".join(("" if ln is GAP else ln) + "\n" for ln in out)


def render_text(store, boards, o, copier=None):
    return (copier or Copier()).render(store, boards, o, "text")


def render_markdown(store, boards, o, copier=None):
    return (copier or Copier()).render(store, boards, o, "markdown")


def write_copy(path, text):
    # None when written or already identical, else the reason it could not be
    path = Path(path)
    data = text.replace("\n", "\r\n").encode("utf-8")
    try:
        if path.exists() and path.read_bytes() == data:
            return None
        fd, tmp = tempfile.mkstemp(
            dir=path.parent, prefix=path.name + ".", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
        except BaseException:
            os.unlink(tmp)
            raise
    except OSError as e:
        return e.strerror or str(e)
    return None
