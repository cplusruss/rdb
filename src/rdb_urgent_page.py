import secrets

from PySide6.QtGui import (
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextDocumentFragment,
)

import rdb_model as m

# the pid the window holds while the page is shown; no card can have it
PID = "_urgent"
LEVEL = 2
# what a card keeps of an item when the page's copy of it is written back
KEPT = ("i", "a", "hs", "ti", "ta")
# an id no item has, for the new copy of an item while the old one is still in the document
TEMP = "~"
MO = QTextCursor.MoveOperation


def fragment(blocks):
    c = QTextCursor(blocks[0])
    last = blocks[-1]
    c.setPosition(last.position() + last.length() - 1, QTextCursor.MoveMode.KeepAnchor)
    return QTextDocumentFragment(c)


def signature(blocks):
    return fragment(blocks).toHtml(), [m.get_meta(b) for b in blocks]


def token(taken):
    while (i := secrets.token_hex(3)) in taken:
        pass
    taken.add(i)
    return i


def insert_unit(doc, before, frag, metas):
    # the fragment goes in before `before` (the end of the document when None), the way each
    # block's meta is set in the same edit
    old = [m.get_meta(b) for b in m.iter_blocks(doc)]
    at = doc.blockCount() if before is None else before.blockNumber()
    above = before.previous() if before is not None else doc.lastBlock()
    c = QTextCursor(doc)
    c.beginEditBlock()
    try:
        if above.isValid():
            c.setPosition(above.position())
            c.movePosition(MO.EndOfBlock)
            c.insertBlock(QTextBlockFormat(), QTextCharFormat())
        else:
            # at the very top the split would leave the old first block's format on the new one
            c.setPosition(0)
            c.insertBlock(QTextBlockFormat(), QTextCharFormat())
            c.movePosition(MO.PreviousBlock)
        c.insertFragment(frag)
    finally:
        c.endEditBlock()
    if doc.blockCount() != len(old) + len(metas):
        raise RuntimeError(
            f"inserted {doc.blockCount() - len(old)} blocks, expected {len(metas)}"
        )
    m._reapply(doc, old[:at] + metas + old[at:])


def unit_metas(blocks, head):
    metas = [{**m.NEW_ITEM, **m.get_meta(b)} for b in blocks]
    metas[0] = {**metas[0], **head}
    return metas


def replace_unit(doc, sid, blocks, mode):
    old = m.item_blocks(m.unit_block(doc, sid))
    kept = m.get_meta(old[0])
    keep = {k: kept.get(k, m.NEW_ITEM[k]) for k in KEPT}
    insert_unit(
        doc, old[0], fragment(blocks), unit_metas(blocks, {**keep, "i": TEMP + sid})
    )
    # the new unit goes home before the old one leaves: the last item under Completed takes the
    # header with it, and an unchecked copy left there would lose its place
    m.normalize(doc, mode)
    m.remove_item(doc, m.item_blocks(m.unit_block(doc, sid)))
    m.set_meta(m.unit_block(doc, TEMP + sid), i=sid)


class UrgentPage:
    # one document that lists the banner's items under a heading per source card. ids: page
    # item id -> (card, id in that card), kept per save so an item written once is updated next
    def __init__(self, store, settings, entries):
        self.store, self.settings = store, settings
        self.cards, self.heads, self.items, self.seen, self.boards = {}, {}, {}, {}, {}
        groups = {}
        for e in entries:
            groups.setdefault(e.pid, []).append(e)
            self.cards[e.pid] = e.card
            self.boards[e.pid] = e.board_path
        # items above the first heading belong to the card the page opened with first
        self.first = next(iter(groups))
        self.doc = doc = QTextDocument()
        m.prep_doc(doc)
        taken = set()
        c = QTextCursor(doc)
        for n, (pid, es) in enumerate(groups.items()):
            if n:
                c.insertBlock(QTextBlockFormat(), QTextCharFormat())
            m.make_heading(c.block(), LEVEL)
            hid = token(taken)
            m.set_meta(c.block(), i=hid)
            self.heads[hid] = pid
            e0 = es[0]
            c.insertText(" > ".join(e0.path[: len(e0.board_path) + 1]))
            card = open_source(store, pid, self.mode(pid))
            for e in es:
                todo = card.findBlockByNumber(e.block)
                if not m.is_todo(todo) or m.item_text(todo) != e.text:
                    raise ValueError(f"{pid}: block {e.block} is not {e.text!r}")
                sid = m.get_meta(todo)["i"]
                pgid = token(taken)
                self.items[pgid] = (pid, sid)
                blocks = m.item_blocks(todo)
                first = doc.blockCount()
                c.insertBlock(QTextBlockFormat(), QTextCharFormat())
                c.insertFragment(fragment(blocks))
                for j, b in enumerate(blocks):
                    blank = {"a": "", "hs": 0, "ti": "", "ta": ""}
                    mt = {**m.NEW_ITEM, **m.get_meta(b), **blank}
                    page_b = doc.findBlockByNumber(first + j)
                    m.set_meta(page_b, **{**mt, "i": pgid if j == 0 else ""})
                    m.layout_block(page_b)
                c.movePosition(MO.End)
        m.normalize(doc, m.NOTE_MODE)
        m.attach_images(doc, store)
        doc.clearUndoRedoStacks()
        doc.setModified(False)
        self.seen = {
            u.id: signature(u.blocks) for us in self.groups().values() for u in us
        }
        self.snapshot()

    def snapshot(self):
        # the page as it stands: what a refused bulk delete puts back
        units = m._units(self.doc)
        self.order = [u.id for u in units]
        self.stash = {
            u.id: (fragment(u.blocks), [m.get_meta(b) for b in u.blocks]) for u in units
        }

    def restore(self):
        # every unit missing from the page goes back below the nearest one before it that is
        # still there (the top of the page when none is); a page emptied to its one blank row
        # loses that row
        doc = self.doc
        present = {u.id for u in m._units(doc)}
        blank = m._units(doc)[0].id if doc.blockCount() == 1 else None
        for n, uid in enumerate(self.order):
            if uid in present:
                continue
            above = next((x for x in reversed(self.order[:n]) if x in present), None)
            if above is None:
                before = doc.begin()
            else:
                nxt = m.item_blocks(m.unit_block(doc, above))[-1].next()
                before = nxt if nxt.isValid() else None
            frag, metas = self.stash[uid]
            insert_unit(doc, before, frag, metas)
            present.add(uid)
        if blank is not None:
            m.remove_item(doc, m.item_blocks(m.unit_block(doc, blank)))

    def mode(self, pid):
        return m.done_mode(self.cards[pid]["done"], self.settings)

    def groups(self):
        # the todo units of the page per card: an item belongs to the nearest heading above it
        # that names a card
        out = {pid: [] for pid in self.cards}
        pid = self.first
        for u in m._units(self.doc):
            if m.is_heading(u.blocks[0]):
                pid = self.heads.get(u.id, pid)
            else:
                out[pid].append(u)
        return out

    def owner_of(self, nt):
        # the card whose item on the page links this note document
        for us in self.groups().values():
            for u in us:
                if m.note_link(u.blocks[0]) == nt:
                    return self.items[u.id][0]
        raise KeyError(f"no item on the page links note {nt!r}")

    def save(self, ask):
        # writes what the page changed back to its cards; returns the documents written by
        # card, and the titles of cards that no longer hold an item the page edited. Rows
        # deleted from two or more cards are put back unless ask(items, cards) says yes
        m.ensure_ids(self.doc)
        groups = self.groups()
        here = {u.id for us in groups.values() for u in us}
        deleted = {g: pc for g, pc in self.items.items() if g not in here}
        cards = {p for p, _ in deleted.values()}
        if len(cards) > 1 and not ask(len(deleted), len(cards)):
            self.restore()
            m.ensure_ids(self.doc)
            groups = self.groups()
        sigs = {u.id: signature(u.blocks) for us in groups.values() for u in us}
        mapped, now = self.items, dict(self.items)
        written, lost = {}, []
        for pid, units in groups.items():
            here = {u.id for u in units}
            gone = [g for g, (p, _) in mapped.items() if p == pid and g not in here]
            edited = [
                u
                for u in units
                if mapped.get(u.id, ("",))[0] == pid
                and sigs[u.id] != self.seen.get(u.id)
            ]
            added = [u for u in units if mapped.get(u.id, ("",))[0] != pid]
            if not (gone or edited or added):
                continue
            doc, missing = self.write(pid, units, gone, edited, added, now)
            if missing:
                lost.append(self.cards[pid]["title"])
            if doc is not None:
                written[pid] = doc
        self.items, self.seen = now, sigs
        self.snapshot()
        return written, lost

    def write(self, pid, units, gone, edited, added, now):
        mapped = self.items
        if not self.store.has_project(pid):
            return None, True
        mode = self.mode(pid)
        doc = m.open_doc(self.store, pid, mode)
        have = {m.get_meta(b).get("i") for b in m.iter_blocks(doc) if m.is_todo(b)}
        cut = [
            m.item_blocks(m.unit_block(doc, mapped[g][1]))
            for g in gone
            if mapped[g][1] in have
        ]
        for g in gone:
            # a row moved to another card was mapped there already
            if now.get(g) == mapped[g]:
                del now[g]
        if cut:
            m.remove_items(doc, sorted(cut, key=lambda bl: bl[0].blockNumber()))
        missing, placed = False, []
        for u in edited:
            sid = mapped[u.id][1]
            if sid not in have:
                missing = True
                continue
            was = m.marker(m.unit_block(doc, sid))
            replace_unit(doc, sid, u.blocks, mode)
            if m.mark_of(m.get_meta(u.blocks[0])) != was:
                placed.append(sid)
        if added:
            self.insert_added(doc, pid, units, added, have, placed, now)
        m.normalize(doc, mode)
        if m.is_sorted(doc):
            for sid in placed:
                m.place_item(doc, m.unit_block(doc, sid))
        m.attach_images(doc, self.store)
        m.save_project(self.store, pid, doc)
        return doc, missing

    def insert_added(self, doc, pid, units, added, have, placed, now):
        # after the card's item for the nearest mapped row above; before the card's first mapped
        # item when there is none; at the end of the open items when the card has none left
        mapped = self.items
        taken = {m.get_meta(b).get("i") for b in m.iter_blocks(doc)}
        first = next(
            (
                mapped[u.id][1]
                for u in units
                if mapped.get(u.id, ("",))[0] == pid and mapped[u.id][1] in have
            ),
            None,
        )
        last = None
        for u in units:
            if mapped.get(u.id, ("",))[0] == pid:
                if mapped[u.id][1] in have:
                    last = mapped[u.id][1]
                continue
            if last is not None:
                nxt = m.item_blocks(m.unit_block(doc, last))[-1].next()
                before = nxt if nxt.isValid() else None
            elif first is not None:
                before = m.unit_block(doc, first)
            else:
                before = m.section_block(doc)
            sid = token(taken)
            insert_unit(
                doc,
                before,
                fragment(u.blocks),
                unit_metas(u.blocks, {**blanks(), "i": sid}),
            )
            now[u.id] = (pid, sid)
            have.add(sid)
            placed.append(sid)
            last = sid


def blanks():
    return {k: m.NEW_ITEM[k] for k in KEPT}


def open_source(store, pid, mode):
    # an item with no id stored is named afresh at every open, so a card whose ids were never
    # saved is saved with the ones it has now before the page counts on them
    doc = m.open_doc(store, pid, mode)
    stored = {r.get("i") for r in store.get_project(pid)["meta"]}
    if any(
        m.get_meta(b).get("i") not in stored for b in m.iter_blocks(doc) if m.is_todo(b)
    ):
        m.save_project(store, pid, doc)
    return doc
