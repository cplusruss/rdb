import datetime
import math
import tempfile
from collections import ChainMap, namedtuple
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QTextBlockFormat, QTextCharFormat, QTextCursor
from PySide6.QtGui import QTextDocument
from PySide6.QtWidgets import QFrame, QGraphicsScene, QLabel, QVBoxLayout, QWidget

import rdb_map
import rdb_model as m
import rdb_timeline
from rdb_editor import Editor
from rdb_kanban import KanbanBoard
from rdb_store import Store

PREVIEW_W = 480
Sample = namedtuple(
    "Sample",
    "rows done cursor hover partial line",
    defaults=("place", None, (), (), False),
)


def row(kind, *text, **meta):
    return {"kind": kind, "text": text, **meta}


def link(label, url):
    return ("link", label, url)


class Sandbox:
    # everything a preview touches: a store and files of its own, in a folder nothing else sees,
    # and one settings dict that holds the dialog's current values
    def __init__(self, settings):
        self.settings = dict(settings)
        self.dir = tempfile.TemporaryDirectory(
            prefix="rdb_preview_", ignore_cleanup_errors=True
        )
        self.store = Store(Path(self.dir.name) / "preview.rdb", create=True)
        self.folder = self.dir.name
        self.visited = "https://visited.example"
        self.store.put_visited_links([self.visited])
        self.notes = {}

    def note(self, text):
        if text not in self.notes:
            pid = m.create_note(self.store, text=text)
            m.load_preview(self.store, pid)
            self.notes[text] = pid
        return self.notes[text]

    def close(self):
        for pid in self.notes.values():
            m.PREVIEWS.pop(pid, None)
        self.notes = {}
        self.store.close()
        self.dir.cleanup()


def build_doc(rows):
    doc = QTextDocument()
    m.prep_doc(doc)
    c = QTextCursor(doc)
    for i, r in enumerate(rows):
        if i:
            c.insertBlock(QTextBlockFormat(), QTextCharFormat())
        for part in r["text"]:
            if isinstance(part, tuple):
                m.insert_link(c, part[1], part[2])
            else:
                c.insertText(part, QTextCharFormat())
        block = c.block()
        kind = r["kind"]
        if kind == "section":
            m.set_meta(block, c="", d="", u=False, fg="", bg="", k=m.SECTION)
        elif kind == "divider":
            m.make_divider(block)
        else:
            m.set_meta(block, **m.todo_meta())
            if kind != "todo":
                m.make_heading(block, int(kind[1]))
            else:
                if r.get("checked"):
                    m.set_meta(block, d=m.today())
                if r.get("bg"):
                    m.set_meta(block, bg=r["bg"], fg=r["fg"])
                if r.get("p"):
                    m.set_marker(block, r["p"])
                if r.get("st"):
                    m.set_meta(block, st=r["st"])
                if r.get("note"):
                    m.set_meta(block, nt=r["note"], no=True)
        m.layout_block(block)
    return doc


class PreviewEditor(Editor):
    # the real editor, with every way in closed: no mouse, no focus, no wheel
    local_zoom = True

    def __init__(self, store, settings):
        super().__init__(store, settings)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setAcceptDrops(False)
        self.viewport().setAcceptDrops(False)
        for w in (self, self.viewport()):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)


class DocPart(QWidget):
    def __init__(self, box, make):
        super().__init__()
        self.box, self.make, self.doc = box, make, None
        self.ed = None
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)

    def _editor(self, sample):
        # the current-line band belongs to the pages that show it: the others read the shared
        # dict with that one key off
        settings = self.box.settings
        if not sample.line:
            settings = ChainMap({"line_hl": False}, settings)
        self.ed = PreviewEditor(self.box.store, settings)
        self.ed.setFixedWidth(PREVIEW_W - 2)
        self.layout().addWidget(self.ed)

    def refresh(self):
        sample = self.make(self.box.settings, self.box)
        if self.ed is None or sample.line != (self.ed.settings is self.box.settings):
            if self.ed is not None:
                self.ed.deleteLater()
            self._editor(sample)
        ed = self.ed
        m.set_gutter(
            self.box.settings["gutter_items"],
            self.box.settings["gutter_lpad"],
            self.box.settings["gutter_rpad"],
            self.box.settings["gutter_heads"],
        )
        doc = build_doc(sample.rows)
        ed.done = sample.done
        ed.set_document(doc)
        self.doc = doc
        ed.partial_heads = frozenset(sample.partial)
        ed._tag_hover = ed._chip_hover = None
        ed._mark_hover = ed._status_hover = -1
        for what, n in sample.hover:
            block = doc.findBlockByNumber(n)
            if what == "tag":
                ed._tag_hover = m.tag_runs(block)[0]
            elif what == "chip":
                ed._chip_hover = m.chip_runs(block)[0]
            elif what == "status":
                ed._status_hover = n
            else:
                ed._mark_hover = n
        cur = QTextCursor(doc.findBlockByNumber(sample.cursor or doc.blockCount() - 1))
        cur.movePosition(QTextCursor.MoveOperation.EndOfBlock)
        ed.setTextCursor(cur)
        ed.refresh()
        doc.setTextWidth(PREVIEW_W - 2)
        last = doc.lastBlock().blockFormat().bottomMargin()
        ed.setFixedHeight(math.ceil(doc.size().height() + last * ed.zoom) + 2)
        ed.viewport().update()


class KanbanPart(QWidget):
    # the real board on a sample list, with every way in closed
    HEIGHT = 200

    def __init__(self, box):
        super().__init__()
        self.box = box
        self.ed = PreviewEditor(box.store, box.settings)
        self.ed.hide()
        self.board = KanbanBoard(self.ed)
        self.board.setFixedSize(PREVIEW_W - 2, self.HEIGHT)
        self.board.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.board.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.board.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        for w in (self.board, self.board.viewport()):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.addWidget(self.board)

    def refresh(self):
        keys = [r["key"] for r in self.box.settings["statuses"]]
        # two items carry the first two statuses configured, whatever they are
        first, second = (keys + ["", ""])[:2]
        doc = build_doc(
            [
                row("h1", "Sample"),
                row("todo", "First item", st=first),
                row("todo", "Second item with a tag #budget"),
                row(
                    "todo",
                    "Third item that is long enough to wrap onto more lines",
                    st=second,
                ),
                row("todo", "Fourth item"),
            ]
        )
        self.ed.set_document(doc)
        self.ed.refresh()
        self.board.restyle()


class MapPart(QWidget):
    def __init__(self, box, colors):
        super().__init__()
        self.box, self.colors = box, colors
        self.scene = QGraphicsScene(0, 0, PREVIEW_W - 2, 216)
        self.view = rdb_map.MapView(self.scene)
        self.view.setFixedSize(PREVIEW_W - 2, 216)
        self.view.setFrameShape(QFrame.Shape.NoFrame)
        self.view.setInteractive(False)
        self.view.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.view.viewport().setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents
        )
        self.frame = rdb_map.FrameItem("Frame", QRectF(4, 16, 14 * rdb_map.CELL, 192))
        self.card = rdb_map.CardItem("", "Card")
        self.card.setPos(16, 32)
        if colors:
            self.other = rdb_map.CardItem("", "Card", 4, 2)
            self.other.setPos(260, 32)
            self.other.setSelected(True)
        else:
            self.other = rdb_map.TextItem("", 4, 2, "<p>Text</p>")
            self.other.setPos(260, 32)
        for item in (self.frame, self.card, self.other):
            self.scene.addItem(item)
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.addWidget(self.view)

    def refresh(self):
        s = self.box.settings
        doc = build_doc(
            [
                row("h2", "Heading"),
                row("todo", "First", p="p1"),
                row("todo", "Second", bg=s["bubble_bg"][0], fg=s["bubble_fg"][0]),
                row("todo", "Third"),
                row("todo", "Fourth"),
            ]
        )
        summary = m.card_summary(doc, m.preview_limit(s), s["card_headings"])
        self.card.set_cells(s["card_w"], s["card_h"])
        self.card.set_summary(summary)
        if self.colors:
            self.other.set_summary(summary)
        else:
            self.other.restyle()
        self.scene.update()
        self.view.viewport().update()


class TimelinePart(QWidget):
    def __init__(self, box):
        super().__init__()
        self.box = box
        self.ed = PreviewEditor(box.store, box.settings)
        self.ed.timeline = True
        self.body = rdb_timeline.TimelineBody(self.ed, box.settings)
        self.body.attach()
        self.body.gantt.day_w = 14
        self.body.setFixedWidth(PREVIEW_W - 2)
        self.body.setChildrenCollapsible(False)
        for w in (self.body, self.body.gantt.viewport()):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.addWidget(self.body)

    def refresh(self):
        day = datetime.date.fromisoformat(m.today())
        rows = [
            row("h2", "Plan"),
            row("todo", "Design", checked=True),
            row("todo", "Build"),
            row("todo", "Ship"),
        ]
        doc = build_doc(rows)
        for b, (start, days, pct, ident, after) in zip(
            list(m.iter_blocks(doc))[1:],
            (
                (-2, 3, 100, "design", ""),
                (1, 4, 40, "build", "design"),
                (5, 0, 0, "ship", "build"),
            ),
        ):
            m.set_meta(
                b,
                ts=(day + start * m.DAY).isoformat(),
                tn=days,
                tp=pct,
                ti=ident,
                ta=after,
            )
        self.ed.done = "place"
        self.ed.set_document(doc)
        self.doc = doc
        self.ed.refresh()
        self.body.setSizes([150, rdb_timeline.FIELDS_W, PREVIEW_W])
        self.body.sync()
        high = math.ceil(doc.size().height())
        self.ed.setFixedHeight(high)
        self.body.setFixedHeight(rdb_timeline.HEAD_H + high)
        self.body.gantt.refresh()
        self.body.gantt.viewport().update()
        self.body.corner.update()


class FontPart(QLabel):
    def __init__(self, box, name):
        super().__init__()
        self.box, self.name = box, name
        self.setMargin(4)

    def refresh(self):
        s = self.box.settings
        self.setFont(m.font_of(s, self.name))
        self.setText(f"{s[f'{self.name}_font']} {s[f'{self.name}_size']} pt")
        if self.name == "claude":
            self.setStyleSheet(
                f"background: {m.shown(s, 'claude_bg')}; color: {m.shown(s, 'claude_fg')};"
            )


class Pane(QFrame):
    def __init__(self, parts):
        super().__init__()
        self.setFrameShape(QFrame.Shape.Box)
        self.setFixedWidth(PREVIEW_W)
        self.parts = parts
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        for i, part in enumerate(parts):
            if i:
                rule = QFrame()
                rule.setFrameShape(QFrame.Shape.HLine)
                col.addWidget(rule)
            col.addWidget(part)

    def refresh(self):
        # the layout, the map and the chart read module settings that the window sets from
        # its own copy; a preview sets them from the dialog's
        s = self.parts[0].box.settings
        m.configure(s)
        rdb_map.configure(s)
        rdb_timeline.configure(s)
        for part in self.parts:
            part.refresh()


def items_sample(s, box):
    return Sample(
        [
            row("todo", "First", bg=s["bubble_bg"][0], fg=s["bubble_fg"][0]),
            row("todo", "Second", p="ys"),
            row("todo", "Third"),
            row("todo", "Fourth item that is long enough to wrap onto a second line"),
        ],
        cursor=2,
        line=True,
    )


def gutter_sample(s, box):
    pid = box.note("Note line\nSecond line")
    return Sample(
        [
            row("h1", "H1"),
            row("todo", "First", p="p1", bg=s["bubble_bg"][0], fg=s["bubble_fg"][0]),
            row("todo", "Second", note=pid),
            row("h2", "H2"),
            row("todo", "Third #budget", st="waiting"),
        ],
        cursor=2,
    )


def headings_sample(s, box):
    return Sample(
        [
            row("h1", "H1"),
            row("todo", "Item"),
            row("todo", "Item"),
            row("h1", "H1 selected"),
            row("todo", "Item"),
            row("h1", "H1 partial"),
            row("todo", "Item"),
            row("h2", "H2"),
            row("todo", "Item"),
            row("todo", "Item"),
            row("h3", "H3"),
            row("todo", "Item"),
        ],
        cursor=3,
        partial=(5,),
    )


def tags_sample(s, box):
    return Sample(
        [
            row("todo", "Call Alex about #budget today"),
            row("todo", "Call Alex about #budget today"),
            row("todo", "Send the #report to Sam by Friday"),
        ],
        hover=(("tag", 1),),
    )


def links_sample(s, box):
    return Sample(
        [
            row("todo", link("Project", box.folder), " notes"),
            row("todo", link("Project", box.folder), " notes"),
            row("todo", link("example.com", "https://example.com"), " site"),
            row("todo", link("visited.example", box.visited), " visited"),
        ],
        hover=(("chip", 1),),
    )


def checked_sample(s, box):
    return Sample(
        [
            row("todo", "Open item"),
            row("todo", "Checked item", checked=True),
            row("todo", "Open item"),
        ]
    )


def completed_sample(s, box):
    return Sample(
        [
            row("todo", "Open item"),
            row("section"),
            row("todo", "Done item", checked=True),
            row("todo", "Done item", checked=True),
        ],
        done="section",
    )


def divider_sample(s, box):
    return Sample([row("todo", "Above"), row("divider"), row("todo", "Below")])


def note_sample(s, box):
    pid = box.note("Note line\nSecond line\nThird line")
    return Sample([row("todo", "Item with a note", note=pid), row("todo", "Next item")])


def hover_sample(s, box):
    return Sample(
        [
            row("todo", "Marker slot"),
            row("todo", "Call Alex about #budget today"),
            row("todo", link("Project", box.folder), " notes"),
        ],
        hover=(("mark", 0), ("status", 0), ("tag", 1), ("chip", 2)),
    )


def colors_sample(s, box):
    return Sample(
        [
            row("todo", "Urgent item", p="p1"),
            row("todo", "Bubble item", bg=s["bubble_bg"][1], fg=s["bubble_fg"][1]),
            row("todo", "Checked item", checked=True),
        ]
    )


def doc_pane(*makers):
    def make(box):
        return Pane([DocPart(box, mk) for mk in makers])

    return make


def kanban_pane(box):
    return Pane([KanbanPart(box)])


def map_pane(box):
    return Pane([MapPart(box, False)])


def colors_pane(box):
    return Pane(
        [
            DocPart(box, colors_sample),
            DocPart(box, completed_sample),
            MapPart(box, True),
            FontPart(box, "claude"),
        ]
    )


def font_pane(box):
    return Pane([FontPart(box, n) for n in ("ui", "map", "claude")])


def timeline_pane(box):
    return Pane([TimelinePart(box)])


PANES = {
    "Items": doc_pane(items_sample),
    "Gutter": doc_pane(gutter_sample),
    "Kanban": kanban_pane,
    "Headings": doc_pane(headings_sample),
    "Tags": doc_pane(tags_sample),
    "Links": doc_pane(links_sample),
    "Completed": doc_pane(checked_sample, completed_sample),
    "Divider": doc_pane(divider_sample),
    "Note": doc_pane(note_sample),
    "Hover": doc_pane(hover_sample),
    "Map": map_pane,
    "Timeline": timeline_pane,
    "Fonts": font_pane,
    "Colors": colors_pane,
}
