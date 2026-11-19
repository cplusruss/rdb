import copy
import ctypes
import datetime
import getpass
import hashlib
import math
import os
import sqlite3
import sys
import tempfile
import time
import traceback
from contextlib import contextmanager
from pathlib import Path

from PySide6.QtCore import (
    QBuffer,
    QByteArray,
    QEvent,
    QFileSystemWatcher,
    QIODevice,
    QLockFile,
    QPointF,
    QProcess,
    QProcessEnvironment,
    QRectF,
    Qt,
    QTimer,
    QUrl,
)
from PySide6.QtGui import (
    QAction,
    QDesktopServices,
    QColor,
    QFont,
    QGuiApplication,
    QImage,
    QImageWriter,
    QKeyEvent,
    QKeySequence,
    QPen,
    QShortcut,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
)
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QGraphicsRectItem,
    QGraphicsScene,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

import rdb_bar
import rdb_contacts
import rdb_model as m
from rdb_model import append_link
import rdb_hotkey
import rdb_map
import rdb_paths
import rdb_rdoc
import rdb_theme
import rdb_update
import rdb_version
import rdb_timeline
import rdb_tip
import tabkeys
from rdb_claude import (
    CONTACTS_SCHEMA,
    CONTACTS_SYSTEM_PROMPT,
    DOC_SCHEMA,
    DOC_SYSTEM_PROMPT,
    ENTER,
    LINKS_SCHEMA,
    LINKS_SYSTEM_PROMPT,
    ORANGE,
    REFINE,
    REPLY_SCHEMA,
    SYSTEM_PROMPT,
    TIMELINE_SCHEMA,
    TIMELINE_SYSTEM_PROMPT,
    ClaudeRun,
    Pane,
    PromptBar,
    apply_doc_reply,
    apply_reply,
    board_changes,
    board_request,
    contacts_request,
    doc_basis,
    doc_request,
    doc_tint,
    new_urls,
    prepare_contacts_reply,
    refine_request,
)
from rdb_bar import (
    SYNC_EVENTS,
    Chevron,
    Crumb,
    Crumbs,
    EdgeResizer,
    TitleBar,
    paint_border,
)
from rdb_dump import DumpBox
from rdb_editor import Editor, EditorPage, set_zoom, zoom_hub
from rdb_dbinfo import DbInfoDialog
from rdb_findbar import EditorFinder, FindBar, KanbanFinder, MapFinder
from rdb_export import export_docx
from rdb_textcopy import Copier, boards_of, options, write_copy
from rdb_finder import (
    Finder,
    Target,
    build_index,
    card_entries,
    entry_tags,
    flash,
    today_entries,
)
from rdb_help import ShortcutGuide
from rdb_imgpack import PackPass, size_text
from rdb_insert import InsertDialog
from rdb_links import link_entries, snap_name
from rdb_map import (
    Block,
    CardItem,
    FrameItem,
    ImageItem,
    MapView,
    TextItem,
    TitleEditor,
    caption_cells,
    caption_of,
    image_cells,
)
from rdb_quick import (
    QuickAdd,
    add_item,
    quick_counts,
    quick_default,
    quick_entries,
    quick_loc,
)
from rdb_settings import SettingsDialog, TagManager
from rdb_store import Store, drop_from_sets, rename_in_sets
from rdb_titlebuttons import TitleTab, load_tiles
from rdb_triage import PickCard, TriagePage, item_view, open_items
from rdb_undo import MapEdit, MapStack, snapshot, undoable
from rdb_urgent import Banner, arrange, due, item_key
from rdb_urgent_page import PID as URGENT_PID, UrgentPage
from rdb_web import BLOCKED, check_title, fetch_page, fetch_title

UNDO_LIMIT = 200
TITLE_FETCHES = 3


def instance_name():
    return os.environ.get("RDB_INSTANCE") or f"rdb-{getpass.getuser()}"


INSTANCE = instance_name()
ASFW_ANY = -1
CTRL_SHIFT = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
RECENT_MAX = 8
COPY_GAP_S = 3
ZOOM_SAVE_MS = 400
IMAGE_NOTE = "Image"
# a snip still being written is looked at again this often, up to this many times
SNIP_RETRY_MS, SNIP_TRIES = 300, 20
HISTORY_KEYS = {
    Qt.Key.Key_Left: lambda w: w.step(-1),
    Qt.Key.Key_Right: lambda w: w.step(1),
}


class FileBusy(Exception):
    pass


# what a failed File > Open / New / Save As can raise; shown to the user, current file kept
LOAD_ERRORS = (FileBusy, ValueError, KeyError, sqlite3.DatabaseError)


def lock_for(path):
    key = hashlib.sha1(str(Path(path).resolve()).lower().encode()).hexdigest()[:16]
    lock = QLockFile(str(Path(tempfile.gettempdir()) / f"rdb-{key}.lock"))
    # age-based staleness would let a second window steal a live lock after 30 s; a dead pid still frees it
    lock.setStaleLockTime(0)
    return lock


def app_settings():
    return rdb_paths.settings()


def map_at(store, path):
    return m.load_board(store, path[-1]) if path else m.load_map(store)


def put_map_at(store, path, mp):
    if path:
        m.save_board(store, path[-1], mp)
    else:
        m.save_map(store, mp)


def drop_offset(target, held):
    # scan the board's content area row by row for the first spot where the group's bounding
    # box keeps one cell clear of every card and block; else the first row under the content
    cell = rdb_map.CELL
    gx, gy = min(d["x"] for d in held), min(d["y"] for d in held)
    gw = max(d["x"] + d["w"] * cell for d in held) - gx
    gh = max(d["y"] + d["h"] * cell for d in held) - gy
    have = [
        (d["x"], d["y"], d["w"] * cell, d["h"] * cell)
        for d in target["cards"] + target["blocks"]
    ]
    if not have:
        return -gx, -gy
    x0, y0 = min(r[0] for r in have), min(r[1] for r in have)
    x_last = max(x0, max(r[0] + r[2] for r in have) - gw)
    y = y0
    while True:
        x = x0
        while x <= x_last:
            if not any(
                x < rx + rw + cell
                and rx < x + gw + cell
                and y < ry + rh + cell
                and ry < y + gh + cell
                for rx, ry, rw, rh in have
            ):
                return x - gx, y - gy
            x += cell
        y += cell


def duplicate_shift(target, held, own):
    # one cell right and down, then on along the diagonal until no copied item overlaps a card
    # or block other than the originals
    cell = rdb_map.CELL
    have = [
        (d["x"], d["y"], d["w"] * cell, d["h"] * cell)
        for d in target["cards"] + target["blocks"]
        if d["id"] not in own
    ]
    k = 1
    while any(
        d["x"] + k * cell < rx + rw
        and rx < d["x"] + k * cell + d["w"] * cell
        and d["y"] + k * cell < ry + rh
        and ry < d["y"] + k * cell + d["h"] * cell
        for d in held
        for rx, ry, rw, rh in have
    ):
        k += 1
    return k * cell, k * cell


def path_titles(store, path):
    titles, mp = [], m.load_map(store)
    for pid in path:
        titles.append({c["id"]: c for c in mp["cards"]}[pid]["title"])
        mp = m.load_board(store, pid)
    return titles


def card_face(store, c, settings, doc=None):
    if c["type"] == "board":
        return m.board_summary(store, c["id"], settings)
    if c["type"] == "page":
        return QImage.fromData(store.get_image(c["snap"])) if c["snap"] else None
    if c["type"] == "contacts":
        # doc: the rows of the page that is open on this card
        if doc is None and not store.has_contacts(c["id"]):
            return None
        rows = store.get_contacts(c["id"]) if doc is None else doc
        return rdb_contacts.summary(rows, m.preview_limit(settings))
    if doc is None and store.has_project(c["id"]):
        doc = m.open_doc(store, c["id"], m.done_mode(c["done"], settings))
    return None if doc is None else face_summary(c["type"], doc, settings)


def ask_tag_name(parent, current):
    # the new name for a tag, None when cancelled; an invalid one is refused and asked again
    name = current
    while True:
        name, ok = QInputDialog.getText(parent, "Rename tag", "Tag", text=name)
        if not ok:
            return None
        name = name.strip().removeprefix("#")
        if m.tag_name_ok(name):
            return name
        QMessageBox.warning(parent, "Rename tag", f"#{name}: not a valid tag")


def face_summary(kind, doc, settings):
    if kind == "notepad":
        return m.notepad_summary(doc, m.preview_limit(settings))
    if kind == "timeline":
        return m.timeline_summary(doc)
    return m.card_summary(doc, m.preview_limit(settings), settings["card_headings"])


def show_face(card, face):
    if face is None:
        return
    if card.type == "page":
        card.set_snapshot(face)
    else:
        card.set_summary(face)


def image_bytes(img, fmt="PNG", quality=-1):
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    if not img.save(buf, fmt, quality):
        raise OSError(f"cannot encode image as {fmt}")
    return bytes(ba)


def png_bytes(img):
    return image_bytes(img)


def snip_encode(img, s):
    # scaled down so its longest edge is at most Max (0: no limit), then encoded as Format at
    # Quality; png has none. Returns the bytes and the image they hold
    mx = s["snip_max"]
    if mx and max(img.width(), img.height()) > mx:
        img = img.scaled(
            mx,
            mx,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    fmt = s["snip_format"]
    return image_bytes(img, fmt, -1 if fmt == "png" else s["snip_quality"]), img


def dump_into(ed, item, s):
    # one undo step; a link or an image rides in the item just added, extra is a line under it
    doc = ed.document()
    keep = QTextCursor(ed.textCursor())
    keep.setKeepPositionOnInsert(True)
    edit = QTextCursor(doc)
    edit.beginEditBlock()
    try:
        add_item(ed, None, item.text, "", False, "", 1)
        c = QTextCursor(m.group_tail(doc))
        c.movePosition(QTextCursor.MoveOperation.EndOfBlock)
        if item.url:
            m.insert_link(c, item.url, item.url)
        if item.image is not None:
            data, img = snip_encode(item.image, s)
            name = ed._new_image_name("." + s["snip_format"])
            ed.store.put_image(name, data)
            ed._forget_image(name)
            doc.addResource(QTextDocument.ResourceType.ImageResource, QUrl(name), img)
            c.insertImage(name)
            if item.extra.strip():
                m.set_image_label(c.block(), name, item.extra.strip())
        if item.extra:
            ed.setTextCursor(c)
            ed.new_todo(False)
            ed.textCursor().insertText(item.extra)
    finally:
        edit.endEditBlock()
    ed.setTextCursor(keep)
    ed.fit_images()


def snip_spot(target):
    # the left column, one empty cell under everything else on the board
    cell = rdb_map.CELL
    below = [
        (d["x"], d["y"] + d["h"] * cell) for d in target["cards"] + target["blocks"]
    ]
    below += [(f["x"], f["y"] + f["h"]) for f in target["frames"]]
    if not below:
        return 0.0, 0.0
    return min(x for x, _ in below), max(y for _, y in below) + cell


def claim_snip(path):
    # the PNG moves under imported\ in its folder, a name clash taking a numeric suffix (a
    # leftover sidecar there counts, so the pair keeps one name); returns where it went
    done = path.parent / "imported"
    done.mkdir(exist_ok=True)
    dest, n = done / path.name, 1
    while dest.exists() or dest.with_suffix(".txt").exists():
        n += 1
        dest = done / f"{path.stem}_{n}{path.suffix}"
    path.rename(dest)
    return dest


def can_write(fmt):
    have = {bytes(f).lower() for f in QImageWriter.supportedImageFormats()}
    return fmt.encode() in have


INBOX_MAX_W = 16


def item_doc(store, unit):
    # one text block's document: the lines of an item (struck when it is checked), then the
    # plain lines of its note document; also the names of the images of the item and its note
    doc = QTextDocument()
    c = QTextCursor(doc)
    struck = m.is_checked(unit[0])
    names = []
    for n, b in enumerate(unit):
        if n:
            c.insertBlock(QTextBlockFormat(), QTextCharFormat())
        it = b.begin()
        while not it.atEnd():
            f = it.fragment().charFormat()
            if f.isImageFormat():
                names.append(f.toImageFormat().name())
            else:
                f.setFontStrikeOut(f.fontStrikeOut() or struck)
                c.insertText(it.fragment().text(), f)
            it += 1
    nt = m.note_link(unit[0])
    if nt and store.has_project(nt):
        note = QTextDocument()
        note.setHtml(store.get_project(nt)["html"])
        names += m.image_order(note)
        text = note.toPlainText().replace("\ufffc", "")
        for line in text.rstrip().split("\n") if text.strip() else []:
            c.insertBlock(QTextBlockFormat(), QTextCharFormat())
            c.insertText(line)
    c.select(QTextCursor.SelectionType.Document)
    m.char_normalize(c)
    return doc, list(dict.fromkeys(names))


def text_cells(html):
    # whole cells that hold the html on one line, up to INBOX_MAX_W wide, and then its height
    t = TextItem("", rdb_map.TEXT_CELLS[0], 1, html)
    t.doc.setTextWidth(-1)
    pad_w, pad_h = (2 * p for p in rdb_map.TEXT_PAD)
    w = math.ceil((t.doc.idealWidth() + pad_w) / rdb_map.CELL - 1e-9)
    w = min(INBOX_MAX_W, max(rdb_map.TEXT_CELLS[0], w))
    t.set_cells(w, 1)
    t.restyle()
    h = math.ceil((t.doc.size().height() + pad_h) / rdb_map.CELL - 1e-9)
    return w, max(h, rdb_map.TEXT_MIN[1])


def inbox_blocks(store):
    # the old Inbox list as blocks, open and checked items in document order, in one column
    # from the top left with one empty cell between them: an item's text block, then an image
    # block (reusing the stored blob) for each of its images
    doc = m.open_doc(store, "inbox", "stay")
    taken = (
        m.card_ids(store)
        | m.block_ids(store)
        | set(store.project_ids())
        | set(store.trash_ids())
        | set(store.board_ids())
    )
    lines, blocks, i = list(m.iter_blocks(doc)), [], 0
    y = 0.0

    def put(kind, w, h, **fields):
        nonlocal y
        bid = m.make_id(kind, taken)
        taken.add(bid)
        blocks.append(
            {"id": bid, "kind": kind, "x": 0.0, "y": y, "w": w, "h": h, **fields}
        )
        y += (h + 1) * rdb_map.CELL

    while i < len(lines):
        first = lines[i]
        unit = m.item_blocks(first) if m.is_todo(first) else [first]
        i += len(unit)
        if m.is_section(first):
            continue
        tmp, names = item_doc(store, unit)
        if tmp.toPlainText().strip():
            html = m.body_html(tmp)
            put("text", *text_cells(html), html=html, bg="")
        for name in names:
            img = QImage.fromData(store.get_image(name))
            if img.isNull():
                raise ValueError(f"{store.path}: image {name!r} does not decode")
            put(
                "image",
                *rdb_map.image_cells(img.width(), img.height()),
                img=name,
                iw=img.width(),
                ih=img.height(),
                note="",
            )
    return blocks


def ensure_inbox(store):
    # the Inbox is a board; a list-Inbox from an older build becomes one, once. Its board and
    # map are written before its project goes to the trash, so an interrupted pass loses nothing
    # and the next one finishes the trashing
    mp = m.load_map(store)
    card = next(c for c in mp["cards"] if c["id"] == "inbox")
    if card["type"] == "list":
        if "inbox" in store.board_ids():
            raise ValueError(f"{store.path}: list Inbox, but a board 'inbox' exists")
        card["type"] = "board"
        blocks = inbox_blocks(store) if store.has_project("inbox") else []
        m.save_board(store, "inbox", {**m.new_board(), "blocks": blocks})
    elif "inbox" not in store.board_ids():
        m.save_board(store, "inbox", m.new_board())
    m.save_map(store, mp)
    if store.has_project("inbox"):
        view = store.get_view()
        if view and view["pid"] == "inbox":
            store.put_view({**view, "pid": "", "block": 0})
        m.trash_project(store, "inbox")


def board_data(store, path, settings):
    titles = path_titles(store, path)
    mp = map_at(store, path)
    return mp, {c["id"]: card_face(store, c, settings) for c in mp["cards"]}, titles


class Pasteboard(Editor):
    local_zoom = True

    # never shown: a paste aims at a block that may be hidden, and the caret must stay on it
    def _keep_visible(self):
        pass


def resolve_mode(mode):
    # Light, Dark, or System: the Windows app mode
    if mode == "System":
        return rdb_bar.apps_use_dark()
    if mode not in ("Light", "Dark"):
        raise ValueError(f"unknown mode {mode!r}")
    return mode == "Dark"


class Toast(QLabel):
    # a passing message over the bottom edge of the window, taking no layout space; with a
    # timeout it goes by itself, without one it stays until clicked or replaced
    def __init__(self, win):
        super().__init__(win)
        self.setStyleSheet(
            "background:palette(text);color:palette(base);border-radius:4px;padding:4px 10px"
        )
        self.timer = QTimer(self, singleShot=True, timeout=self.hide)
        self.hide()

    def show_message(self, text, ms=0):
        self.setText(text)
        self.adjustSize()
        self.place()
        self.show()
        self.raise_()
        if ms:
            self.timer.start(ms)
        else:
            self.timer.stop()

    def place(self):
        win = self.parentWidget()
        self.move((win.width() - self.width()) // 2, win.height() - self.height() - 12)

    def currentMessage(self):
        return self.text() if self.isVisible() else ""

    def mousePressEvent(self, e):
        self.timer.stop()
        self.hide()


class MainWindow(QMainWindow):
    def __init__(self, path, snips=False):
        super().__init__()
        self.toast = Toast(self)
        # snips: whether to watch the snip folder while a file is open; the app does, a test
        # must ask
        self.snips = snips
        self.snip_watch = QFileSystemWatcher(self)
        self.snip_watch.directoryChanged.connect(lambda _: self.scan_snips())
        self.snip_tries = {}
        self.snip_timer = QTimer(self)
        self.snip_timer.setSingleShot(True)
        self.snip_timer.timeout.connect(self.scan_snips)
        # the Settings dialog previews its edits in self.settings; this holds what is saved
        self.dialog_before = None
        self.store, self.pid, self.doc, self.saved_collapsed = None, "", None, False
        # the running claude call, the kind of view it was asked about (map, doc or contacts)
        # with what the reply must still fit, and the (accept, reject) pair of a reply shown
        # but not yet accepted; only the latter locks the window
        self.claude_run, self.claude_kind, self.claude_t0 = None, None, 0.0
        self.claude_basis, self.claude_pending = None, None
        # the first request with its schema and system prompt, the earlier (prompt, reply)
        # pairs, the state they were written for (a board dict, a document copy), the last
        # prompt and the readout of the shown reply; claude_refining holds the pane's text
        # from before a refinement began, None otherwise
        self.claude_request, self.claude_task, self.claude_turns = None, None, []
        self.claude_orig, self.claude_prompt = None, ""
        self.claude_shown, self.claude_refining = (0, 0), None
        # a links card's call may search the web: its reply's new urls are checked before the
        # preview (claude_check, None otherwise), and the ones that failed are tinted red
        self.claude_links, self.claude_check, self.claude_dead = False, None, set()
        # a timeline card's reply may carry a start date for a new item
        self.claude_timeline = False
        # async writers that came in while a reply is shown, replayed once the window is free
        self.claude_deferred = []
        self.claude_tick = QTimer(self)
        self.claude_tick.timeout.connect(self.claude_elapsed)
        # the list a shown note page belongs to; pid is the note document then
        self.owner = ""
        # (card pid, scroll) of the list page a note page was opened from
        self.note_return = None
        self.dump_box = None
        self.dump_key = None
        # the urgent page shown while pid is URGENT_PID
        self.urgent_page = None
        QApplication.instance().setAttribute(
            Qt.ApplicationAttribute.AA_DontShowIconsInMenus
        )
        self.lock = None
        self.settings = dict(m.DEFAULT_SETTINGS)
        self.history, self.hpos = [((), "")], 0
        # (history entry the move was made from, target pid), newest first
        self.recent_moves = []
        self.board_path, self.board_views = [], {}
        self.card_clipboard, self.fetches, self.snaps = None, {}, {}
        # a snapshot of copied cards (m.copy_bundle); at most one of it and card_clipboard is set
        self.copy_buffer = None
        self.undo_stack = MapStack(self)
        self.undo_stack.setUndoLimit(UNDO_LIMIT)
        # rec: the snapshot from when the edit being recorded began; run: bumped by each
        # selection change, which ends a run of key nudges
        self.rec, self.run = None, 0
        self.finder_cache = {}
        self.tag_cache = None
        # the banner's clock; a test replaces it
        self.clock = datetime.datetime.now
        # true while the cut or copied cards are newer than whatever the system clipboard holds
        self.cut_newer = False
        self.title_queue, self.titles_active = [], 0
        self.scene = QGraphicsScene()
        self.scene.setSceneRect(-50000, -50000, 100000, 100000)
        self.cards = {}
        self.view = MapView(self.scene)
        self.crumbs = Crumbs()
        self.crumb_btns, self.crumb_seps, self.crumb_titles = [], [], []
        self.update_job = None
        self.instance_server = None
        self.global_zoom = app_settings().value("editor_zoom", 1.0, type=float)
        set_zoom(self.global_zoom)
        self.zoom_applying = False
        # a card's zoom lands in the map once a burst of wheel steps ends
        self.zoom_timer = QTimer(self, singleShot=True, interval=ZOOM_SAVE_MS)
        self.zoom_timer.timeout.connect(self.save_layout)
        zoom_hub().changed.connect(self.save_zoom)
        self.map_page = QWidget()
        col = QVBoxLayout(self.map_page)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        col.addWidget(self.view, 1)
        self.mapfind = MapFinder(self.view, self.find_texts)
        self.mapbar = FindBar(self.mapfind, self.view, "find/pin_map")
        col.addWidget(self.mapbar)
        self.claudeout = Pane()
        self.claudeout.style(self.settings)
        self.claudebar = PromptBar()
        self.claudebar.closed.connect(self.claudeout.end)
        self.claudebar.submitted.connect(self.ask_claude)
        self.claudebar.accepted.connect(self.claude_accept)
        self.claudebar.escaped.connect(self.claude_escape)
        self.page = EditorPage(None, self.settings)
        self.editor = self.page.editor
        self.editfind = EditorFinder(self.editor)
        self.editor.tag_source = self.project_tags
        self.editorbar = FindBar(self.editfind, self.editor, "find/pin_editor")
        self.editorbar.tag_sets = self.tag_sets
        self.page.layout().addWidget(self.editorbar)
        self.kanbanbar = FindBar(
            KanbanFinder(self.page.board), self.page.board, "find/pin_kanban"
        )
        self.kanbanbar.tag_sets = self.tag_sets
        self.page.layout().addWidget(self.kanbanbar)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.map_page)
        self.stack.addWidget(self.page)
        self.contacts = rdb_contacts.ContactsPage(self.settings, app_settings())
        self.stack.addWidget(self.contacts)
        self.triage = TriagePage(self.settings)
        self.stack.addWidget(self.triage)
        self.left = QWidget()
        left = QVBoxLayout(self.left)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(0)
        self.urgent = Banner()
        self.urgent.picked.connect(self.pick_urgent)
        self.urgent.add_requested.connect(lambda: self.quick_add(True))
        self.urgent.list_requested.connect(self.open_urgent)
        self.urgent.reordered.connect(self.save_urgent_order)
        left.addWidget(self.urgent)
        left.addWidget(self.stack, 1)
        left.addWidget(self.claudeout)
        left.addWidget(self.claudebar)
        self.setCentralWidget(self.left)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.save_current)
        self.copier = Copier()
        self.copy_clock = time.monotonic
        self.copy_last = None
        self.copy_timer = QTimer(self)
        self.copy_timer.setSingleShot(True)
        self.copy_timer.timeout.connect(self.write_text_copy)
        # edits kick urgent_soon, which folds a burst into one refresh; the minute timer
        # catches the end of day and midnight
        self.urgent_soon = QTimer(self)
        self.urgent_soon.setSingleShot(True)
        self.urgent_soon.setInterval(50)
        self.urgent_soon.timeout.connect(self.refresh_urgent)
        self.urgent_timer = QTimer(self)
        self.urgent_timer.setInterval(60000)
        self.urgent_timer.timeout.connect(self.refresh_urgent)
        self.urgent_timer.start()
        self.view.enter.connect(self.enter)
        self.view.enter_row.connect(self.enter)
        self.view.created.connect(self.card_created)
        self.view.renamed.connect(self.item_renamed)
        self.view.remove.connect(self.confirm_delete)
        self.view.changed.connect(self.layout_changed)
        self.view.check_item.connect(self.check_from_card)
        self.view.mark_item.connect(self.mark_from_card)
        self.view.contacts_added.connect(self.add_contacts)
        self.view.insert_request.connect(self.insert_card)
        self.view.styled.connect(self.card_styled)
        self.view.quick_add.connect(self.quick_add)
        self.view.undo_request.connect(self.undo_stack.undo)
        self.view.redo_request.connect(self.undo_stack.redo)
        self.scene.selectionChanged.connect(self.end_run)
        self.view.move_into.connect(self.move_into)
        self.view.cut_cards.connect(self.cut_cards)
        self.view.copy_cards.connect(self.copy_cards)
        self.view.duplicate_cards.connect(self.duplicate_cards)
        self.view.copy_dropped.connect(self.copy_dropped)
        self.view.paste_cards.connect(self.paste_request)
        self.view.url_dropped.connect(self.url_dropped)
        self.view.images_dropped.connect(self.images_dropped)
        self.view.block_added.connect(self.text_added)
        self.view.image_opened.connect(self.open_image_note)
        self.view.snip_chosen.connect(self.toggle_snips)
        QApplication.clipboard().dataChanged.connect(self.system_clip_changed)
        self.view.link_clicked.connect(self.link_row_clicked)
        self.view.open_external.connect(lambda url: QDesktopServices.openUrl(QUrl(url)))
        self.view.view_toggled.connect(self.card_view)
        self.view.clip_ready = lambda: (
            self.card_clipboard is not None or self.copy_buffer is not None
        )
        self.view.note_text = self.note_text
        self.editor.leave.connect(self.leave_editor)
        self.editor.notice.connect(self.toast.show_message)
        self.editor.note_requested.connect(self.open_note)
        self.editor.link_opened.connect(self.open_link)
        self.editor.move_requested.connect(self.quick_move)
        self.editor.marked.connect(self.urgent_soon.start)
        self.triage.leave.connect(self.go_map)
        self.triage.move.connect(self.triage_move)
        self.triage.date.connect(self.triage_date)
        self.triage.urgent.connect(self.triage_urgent)
        self.triage.delete.connect(self.triage_delete)
        self.triage.undo.connect(self.undo_stack.undo)
        self.triage.redo.connect(self.undo_stack.redo)
        self.page.grid.leave.connect(self.go_map)
        self.page.grid.opened.connect(self.open_link)
        self.page.grid.edited.connect(self.edit_links)
        self.page.view_changed.connect(self.view_changed)
        self.page.kanban_changed.connect(self.kanban_changed)
        self.page.kanban_done_changed.connect(self.kanban_done_changed)
        self.page.widths_changed.connect(self.kanban_widths_changed)
        self.page.kanban_status_only_changed.connect(self.kanban_status_only_changed)
        self.page.order_changed.connect(self.kanban_order_changed)
        self.page.statuses_edited.connect(self.statuses_edited)
        self.page.done_changed.connect(self.done_changed)
        self.page.fields_toggled.connect(self.fields_toggled)
        self.page.wrap_toggled.connect(self.wrap_toggled)
        self.page.lang_changed.connect(self.lang_changed)
        self.page.sort_changed.connect(self.sort_changed)
        self.editor.sort_changing.connect(self.save_current)
        self.contacts.changed.connect(self.timer.start)
        self.contacts.leave.connect(self.go_map)
        self.contacts.web.connect(lambda url: QDesktopServices.openUrl(QUrl(url)))
        self.editor.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.editor.customContextMenuRequested.connect(self.show_editor_menu)
        self.shortcut_guide = None
        for seq, fn in (
            ("Ctrl+P", self.find),
            ("Ctrl+Space", self.find),
            ("Ctrl+T", lambda: self.find(True)),
            # anywhere, a list included, where a plain Q is text
            ("Ctrl+Q", self.quick_add),
            ("Ctrl+Shift+Q", lambda: self.quick_add(True)),
            ("Alt+Left", lambda: self.step(-1)),
            ("Alt+Right", lambda: self.step(1)),
            ("Ctrl+H", lambda: self.go_depth(0)),
            ("Ctrl+Shift+Space", self.claudebar.open),
            ("Ctrl+Shift+T", self.open_triage),
        ):
            QShortcut(QKeySequence(seq), self).activated.connect(fn)
        file_menu = self.file_menu = QMenu("File", self)
        for label, fn, key in (
            ("New...", self.new_file, "Ctrl+Shift+N"),
            ("Open...", self.open_file, "Ctrl+O"),
            ("Save As...", self.save_as, "Ctrl+Shift+S"),
        ):
            act = QAction(label, self)
            act.setShortcut(QKeySequence(key))
            act.triggered.connect(fn)
            file_menu.addAction(act)
            # the menu is in no widget, so a shortcut needs the window to hold its action
            self.addAction(act)
            if label == "Open...":
                self.recent_menu = file_menu.addMenu("Recent")
                self.recent_menu.aboutToShow.connect(self.fill_recent)
        file_menu.addSeparator()
        export_menu = file_menu.addMenu("Export")
        export_menu.addAction("Word (.docx)...", self.export_word)
        export_menu.addAction("Text (.txt)...", lambda: self.export_text("text"))
        export_menu.addAction("Markdown (.md)...", lambda: self.export_text("markdown"))
        settings_action = QAction("Settings...", self)
        settings_action.setShortcut(QKeySequence("Ctrl+,"))
        settings_action.triggered.connect(self.edit_settings)
        file_menu.addAction(settings_action)
        self.addAction(settings_action)
        file_menu.addAction("Check for Updates...", self.check_updates)
        help_menu = file_menu.addMenu("Help")
        shortcuts_action = QAction("Keyboard Shortcuts...", self)
        shortcuts_action.setShortcut(QKeySequence("F1"))
        shortcuts_action.triggered.connect(self.show_shortcuts)
        help_menu.addAction(shortcuts_action)
        self.addAction(shortcuts_action)
        file_menu.addSeparator()
        file_menu.addAction("Exit", self.close)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint)
        self.bar = TitleBar(self, file_menu, self.crumbs)
        self.bar.system_changed.connect(self.system_mode_changed)
        QGuiApplication.styleHints().colorSchemeChanged.connect(self.scheme_changed)
        self.bar.strip.attach(self, IMAGE_NOTE)
        QShortcut(QKeySequence("Alt+F"), self).activated.connect(
            self.bar.open_file_menu
        )
        self.setMenuWidget(self.bar)
        EdgeResizer(self)
        QApplication.instance().installEventFilter(self)
        self.resize(1100, 750)
        self.restore_window()
        self.pending_view = None
        self.load_file(path)

    def show_shortcuts(self):
        if self.shortcut_guide is None:
            self.shortcut_guide = ShortcutGuide(self, app_settings())
        self.shortcut_guide.show()
        self.shortcut_guide.raise_()
        self.shortcut_guide.activateWindow()

    def save_window(self):
        prefs = app_settings()
        # normalGeometry is the rect under a maximized window; geometry() otherwise
        rect = self.normalGeometry() if self.isMaximized() else self.geometry()
        prefs.setValue("window/rect", rect)
        prefs.setValue("window/max", self.isMaximized())

    def restore_window(self):
        # not restoreGeometry: before the native window exists it allows for a titlebar a
        # frameless window does not have, so a full-height window came back shorter and lower
        prefs = app_settings()
        rect = prefs.value("window/rect")
        if rect is None or not any(
            s.availableGeometry().intersects(rect) for s in QApplication.screens()
        ):
            return
        self.setGeometry(rect)
        if prefs.value("window/max", False, bool):
            self.setWindowState(self.windowState() | Qt.WindowState.WindowMaximized)

    def load_file(self, path):
        self.claude_abort()
        path = Path(path)
        if self.store is not None and path.resolve() == self.store.path.resolve():
            return
        lock = lock_for(path)
        if not lock.tryLock(0):
            raise FileBusy(f"{path} is open in another rdb window")
        # load and check everything from the new file before letting go of the current one
        try:
            store = Store(path, create=not path.exists())
            try:
                settings = m.load_settings(store)
                rdb_map.configure(settings)
                ensure_inbox(store)
                tiles = load_tiles(store)
                view = store.get_view()
                # views saved before boards existed have no board_path
                bpath = view.get("board_path", []) if view else []
                mp, faces, titles = board_data(store, bpath, settings)
            except Exception:
                store.close()
                raise
        except Exception:
            lock.unlock()
            raise
        if self.store is not None:
            self.pack_pass.stop()
            self.save_current()
            self.restore_clipboard()
            self.save_layout()
            self.flush_text_copy()
            self.save_view()
            self.store.on_write = None
            self.store.close()
            self.lock.unlock()
        self.store, self.lock = store, lock
        store.on_write = self.queue_text_copy
        self.undo_stack.clear()
        self.editor.store = self.store
        self.settings.clear()
        self.settings.update(settings)
        rdb_theme.set_dark(resolve_mode(self.settings["mode"]))
        m.configure(self.settings)
        rdb_map.configure(self.settings)
        rdb_timeline.configure(self.settings)
        # self.doc stays referenced: the editor still shows it until the next project replaces it
        self.leave_page()
        self.pid, self.owner, self.history, self.hpos = "", "", [(tuple(bpath), "")], 0
        self.bar.strip.load(tiles)
        m.PREVIEWS.clear()
        self.recent_moves = []
        self.board_views = {}
        self.stack.setCurrentWidget(self.map_page)
        self.fill_scene(bpath, titles, mp, faces)
        prefs = app_settings()
        prefs.setValue("last_file", str(path))
        done = [p for p in prefs.value("recent_files", [], type=list) if p != str(path)]
        # the open file heads the list and is not shown: it plus RECENT_MAX others
        prefs.setValue("recent_files", [str(path), *done][: RECENT_MAX + 1])
        self.fill_recent()
        self.apply_settings()
        self.setWindowTitle(self.title())
        self.restore_view(view)
        self.sync_snips()
        self.pack_pass = PackPass(
            store,
            lambda: self.doc is None or not self.doc.isModified(),
            self.images_packed,
            self,
        )

    def images_packed(self, saved):
        if self.pack_pass.error:
            self.toast.show_message(f"Images: vacuum failed: {self.pack_pass.error}")
        elif saved:
            self.toast.show_message(f"Images: {size_text(saved)} saved")

    def fill_scene(self, path, titles, mp, faces):
        self.mapbar.suspend()
        self.scene.clear()
        self.cards, self.snaps = {}, {}
        self.board_path = list(path)
        for c in mp["cards"]:
            show_face(self.add_card(c), faces[c["id"]])
        for f in mp["frames"]:
            self.scene.addItem(
                FrameItem(f["title"], QRectF(f["x"], f["y"], f["w"], f["h"]))
            )
        for b in mp["blocks"]:
            self.add_block(b)
        self.view.cursor_cell = None
        self.crumb_titles = list(titles)
        self.update_crumbs()
        self.show_snip()
        self.resume_finds()

    def claude_target(self):
        # what the bar would act on from here; None on a note or text page, whose reply
        # would drop every heading it leaves out
        page = self.stack.currentWidget()
        if page is self.map_page:
            return "map"
        if self.owner or self.on_urgent or page is self.triage:
            return None
        if page is self.page and self.cards[self.pid].type == "notepad":
            return None
        return "contacts" if page is self.contacts else "doc"

    def ask_claude(self, prompt):
        if self.claude_pending is not None:
            # a refinement: the same task, asked again with everything said so far
            request = refine_request(self.claude_request, self.claude_turns, prompt)
            task = self.claude_task[0], self.claude_task[1] + REFINE
            self.claude_refining = self.claudeout.toPlainText()
            self.claudeout.refine(prompt)
        else:
            kind = self.claude_target()
            if kind is None:
                self.claudebar.say("no card here")
                return
            self.claude_links = kind == "doc" and self.cards[self.pid].type == "links"
            self.claude_timeline = (
                kind == "doc" and self.cards[self.pid].type == "timeline"
            )
            self.claude_orig = None
            if kind == "map":
                self.view.commit_text()
                self.claude_orig = self.layout_dict()
                request = board_request(self.store, self.claude_orig, prompt)
                task = REPLY_SCHEMA, SYSTEM_PROMPT
            elif kind == "doc":
                card = self.cards[self.pid]
                self.claude_orig = self.doc.clone()
                request = doc_request(self.doc, card.title, card.type, prompt)
                task = (
                    (LINKS_SCHEMA, LINKS_SYSTEM_PROMPT)
                    if self.claude_links
                    else (TIMELINE_SCHEMA, TIMELINE_SYSTEM_PROMPT)
                    if self.claude_timeline
                    else (DOC_SCHEMA, DOC_SYSTEM_PROMPT)
                )
            else:
                request = contacts_request(self.contacts.model.rows, prompt)
                task = CONTACTS_SCHEMA, CONTACTS_SYSTEM_PROMPT
            self.claude_kind, self.claude_basis = kind, self.claude_state(kind)
            self.claude_request, self.claude_task, self.claude_turns = request, task, []
            self.claudeout.begin()
        self.claude_prompt = prompt
        self.claude_t0 = time.monotonic()
        self.claude_run = run = ClaudeRun(self)
        run.finished.connect(self.claude_done)
        run.failed.connect(self.claude_failed)
        run.text.connect(self.claudeout.add)
        self.claudebar.busy(True)
        self.claude_tick.start(1000)
        run.start(request, *task, web=self.claude_links)

    def claude_state(self, kind):
        # what a reply was written for, and would overwrite: the cards and frames, the open
        # items, or the rows
        if kind == "map":
            board = self.layout_dict()
            cards = ("id", "title", "x", "y", "w", "h")
            frames = ("title", "x", "y", "w", "h")
            return (
                frozenset(tuple(c[k] for k in cards) for c in board["cards"]),
                sorted(tuple(f[k] for k in frames) for f in board["frames"]),
            )
        if kind == "doc":
            return doc_basis(self.doc), self.cards[self.pid].title
        # in order: a reply names rows by position
        return tuple(id(r) for r in self.contacts.model.rows)

    def claude_elapsed(self):
        self.claudebar.say(f"{int(time.monotonic() - self.claude_t0)} s")

    def end_claude(self):
        self.claude_tick.stop()
        if self.claude_run is not None:
            self.claude_run.deleteLater()
            self.claude_run = None
        self.claudebar.busy(False)

    def cancel_claude(self):
        run = self.claude_run
        self.end_claude()
        run.cancel()
        self.claude_over(cancelled=True)

    def cancel_check(self):
        self.claude_check = None
        self.claudebar.busy(False)
        self.claude_over(cancelled=True)

    def claude_over(self, msg="", cancelled=False):
        # a call or a link check ended without a reply; a refinement goes back to the reply
        # still shown, with the pane as it was
        if self.claude_refining is not None:
            self.claudeout.reset(self.claude_refining)
            self.claude_refining = None
            self.claudebar.review(*self.claude_shown)
        elif cancelled:
            self.claudeout.end()
        if msg:
            self.claudebar.say(msg)

    def claude_defers(self, fn, *args):
        if self.claude_pending is None:
            return False
        self.claude_deferred.append((fn, args))
        return True

    def claude_flush(self):
        queue, self.claude_deferred = self.claude_deferred, []
        for fn, args in queue:
            fn(*args)

    def claude_abort(self):
        # whatever claude is doing ends: a running call or a link check is cancelled, a shown
        # reply rejected
        if self.claude_run is not None:
            self.cancel_claude()
        elif self.claude_check is not None:
            self.cancel_check()
        if self.claude_pending is not None:
            self.claude_reject()

    def claude_escape(self):
        # one step: a running call or check ends (a refinement leaves its reply shown), then
        # a shown reply is rejected, then the bar closes
        if self.claude_run is not None:
            self.cancel_claude()
        elif self.claude_check is not None:
            self.cancel_check()
        elif self.claude_pending is not None:
            self.claude_reject()
        else:
            self.claudebar.dismiss()

    def claude_failed(self, msg):
        self.end_claude()
        self.claude_over(msg)

    def claude_fits(self):
        if QApplication.activeModalWidget() is not None:
            raise ValueError("dialog open")
        # during a refinement the screen shows the previous reply, not the original state
        if (
            self.claude_refining is None
            and self.claude_state(self.claude_kind) != self.claude_basis
        ):
            raise ValueError("changed while claude worked")

    def claude_dry(self, reply):
        # a refinement's reply checked against the original state, touching nothing; returns
        # the new links it names
        if self.claude_kind == "map":
            apply_reply(self.claude_orig, reply)
        elif self.claude_kind == "doc":
            return new_urls(
                self.claude_orig, reply, self.claude_links, self.claude_timeline
            )
        else:
            prepare_contacts_reply(self.contacts.model, reply)
        return []

    def claude_done(self, reply):
        self.end_claude()
        try:
            self.claude_fits()
            if self.claude_refining is not None:
                urls = self.claude_dry(reply)
            else:
                urls = (
                    new_urls(self.doc, reply, timeline=self.claude_timeline)
                    if self.claude_links
                    else []
                )
            if urls:
                self.check_links(reply, urls)
            else:
                self.claude_show(reply, set())
        except ValueError as e:
            self.claude_over(str(e))

    def claude_show(self, reply, dead):
        if self.claude_refining is not None:
            # the shown reply goes exactly and the new one is made from the original state;
            # the deferred writers stay deferred until the final Accept or Reject
            _, reject = self.claude_pending
            self.claude_pending, self.claude_refining = None, None
            reject()
        elif self.claude_kind == "map":
            # the first call ran unlocked: refinements are checked against what this preview
            # is made from, not the state at submit
            self.claude_orig = self.layout_dict()
        elif self.claude_kind == "doc":
            self.claude_orig = self.doc.clone()
        self.claude_dead = dead
        count, accept, reject = getattr(self, f"preview_{self.claude_kind}")(reply)
        self.claude_pending = accept, reject
        self.claude_turns.append((self.claude_prompt, reply))
        self.claude_shown = count, len(dead)
        self.claudebar.edit.clear()
        self.claudebar.review(*self.claude_shown)

    def check_links(self, reply, urls):
        self.claude_check = {
            "reply": reply,
            "queue": list(urls),
            "active": 0,
            "done": 0,
            "total": len(urls),
            "dead": set(),
        }
        self.claudebar.busy(True)
        self.pump_check()

    def pump_check(self):
        chk = self.claude_check
        self.claudebar.say(f"checking {chk['done']}/{chk['total']}")
        while chk["active"] < TITLE_FETCHES and chk["queue"]:
            url = chk["queue"].pop(0)
            chk["active"] += 1
            self.fetch(
                check_title,
                url,
                lambda title, c=chk, u=url: self.link_checked(c, u, title),
            )

    def link_checked(self, chk, url, title):
        if self.claude_check is not chk:
            return
        if title is BLOCKED:
            # later answers for this check fail the identity test above and are dropped
            self.claude_check = None
            self.claudebar.busy(False)
            self.claude_over("url host is not public")
            return
        chk["active"] -= 1
        chk["done"] += 1
        if title is None:
            chk["dead"].add(url)
        if chk["done"] < chk["total"]:
            self.pump_check()
            return
        self.claude_check = None
        self.claudebar.busy(False)
        try:
            self.claude_fits()
            self.claude_show(chk["reply"], chk["dead"])
        except ValueError as e:
            self.claude_over(str(e))

    def claude_accept(self):
        accept, _ = self.claude_pending
        self.claude_pending = None
        self.claudebar.busy(False)
        accept()
        self.claudebar.dismiss()
        self.claude_flush()

    def claude_reject(self):
        _, reject = self.claude_pending
        self.claude_pending = None
        self.claudebar.busy(False)
        reject()
        self.claudeout.end()
        self.claudebar.edit.setText(self.claude_turns[0][0])
        self.claudebar.edit.setFocus()
        self.claude_flush()

    def claude_blocks(self, obj, e):
        # while a reply is shown, the bar and the caption buttons are the only live input:
        # Enter and Esc and typing in the bar, and the mouse on the bar and the caption
        if not (isinstance(obj, QWidget) and obj.window() is self):
            return False
        kind = e.type()
        in_bar = obj is self.claudebar or self.claudebar.isAncestorOf(obj)
        out = self.claudeout
        in_out = obj is out or out.isAncestorOf(obj)
        if in_out and kind in (
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseButtonRelease,
            QEvent.Type.MouseButtonDblClick,
            QEvent.Type.MouseMove,
            QEvent.Type.Wheel,
            QEvent.Type.ContextMenu,
        ):
            return False
        if kind in (
            QEvent.Type.KeyPress,
            QEvent.Type.KeyRelease,
            QEvent.Type.ShortcutOverride,
        ):
            plain = not e.modifiers() & (CTRL_SHIFT | Qt.KeyboardModifier.AltModifier)
            edit = self.claudebar.edit
            if in_bar and plain and e.key() in (*ENTER, Qt.Key.Key_Escape):
                return False
            # Ctrl+C copies the pane's selection unless the bar's own text is selected
            if kind == QEvent.Type.KeyPress and e.matches(
                QKeySequence.StandardKey.Copy
            ):
                (edit if not in_out and edit.hasSelectedText() else out).copy()
                return True
            # the edit takes text for a refinement, but no shortcut and no Tab out of it
            typing = not e.modifiers() & (
                Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier
            )
            if (
                obj is edit
                and not edit.isReadOnly()
                and typing
                and e.key() not in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab)
            ):
                return False
            if kind == QEvent.Type.ShortcutOverride:
                e.accept()
            return True
        if kind in (
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseButtonRelease,
            QEvent.Type.MouseButtonDblClick,
            QEvent.Type.Wheel,
            QEvent.Type.ContextMenu,
            QEvent.Type.DragEnter,
            QEvent.Type.DragMove,
            QEvent.Type.Drop,
        ):
            return not (
                in_bar
                or obj is self.bar
                or any(obj is b for b in self.bar.caption.values())
            )
        return False

    def preview_map(self, reply):
        self.view.commit_text()
        old = self.layout_dict()
        board = apply_reply(old, reply)
        faces = {
            c["id"]: card_face(self.store, c, self.settings) for c in board["cards"]
        }
        self.fill_scene(
            self.board_path, path_titles(self.store, self.board_path), board, faces
        )
        cards, frames, count = board_changes(old, board)
        rects = [self.cards[pid].sceneBoundingRect() for pid in cards]
        rects += [QRectF(f["x"], f["y"], f["w"], f["h"]) for f in frames]
        for r in rects:
            outline = QGraphicsRectItem(r.adjusted(-3, -3, 3, 3))
            outline.setPen(QPen(QColor(ORANGE), 3))
            outline.setZValue(1e6)
            outline.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self.scene.addItem(outline)

        def accept():
            with self.recorded():
                put_map_at(self.store, self.board_path, board)
                self.reload_board()

        # the store still holds the old layout, and the reload shows it again
        return count, accept, self.reload_board

    def preview_doc(self, reply):
        doc, card = self.doc, self.cards[self.pid]
        self.close_finds()
        steps = doc.availableUndoSteps()
        got = apply_doc_reply(doc, reply, self.claude_links, self.claude_timeline)
        # the edit queued a save, which waits for the reply to be accepted
        self.timer.stop()
        old = card.title
        if got.title != old:
            card.set_title(got.title)
            self.page.set_title(got.title)
        dead = {uid for uid, url in got.fresh.items() if url in self.claude_dead}
        self.editor.setExtraSelections(doc_tint(doc, got.marked, dead))
        self.page.grid.marks = {
            url: "dead" if url in self.claude_dead else "new"
            for url in got.fresh.values()
        }
        # a new link's page is fetched once it is accepted
        if self.page.cards_shown:
            self.fill_grid()

        def accept():
            self.editor.setExtraSelections([])
            self.page.grid.marks = {}
            if self.page.cards_shown:
                self.fill_grid()
            if got.title != old:
                with self.recorded():
                    self.item_renamed(card, got.title)
            self.resume_finds()
            self.timer.start()

        def reject():
            self.editor.setExtraSelections([])
            self.page.grid.marks = {}
            if doc.availableUndoSteps() > steps:
                self.editor.undo()
            doc.clearUndoRedoStacks(QTextDocument.Stacks.RedoStack)
            card.set_title(old)
            self.page.set_title(old)
            if self.page.cards_shown:
                self.fill_grid()
            self.resume_finds()
            self.timer.start()

        count = len(got.marked) + got.removed + (got.title != old)
        return count, accept, reject

    def preview_contacts(self, reply):
        page = self.contacts
        cmd = prepare_contacts_reply(page.model, reply)
        dirty = page.dirty
        cmd.show()
        # the change queued a save, which waits for the reply to be accepted
        self.timer.stop()

        def accept():
            page.model.mark(())
            page.model.stack.push(cmd)
            self.timer.start()

        def reject():
            cmd.undo()
            page.model.mark(())
            page.dirty = dirty
            if not dirty:
                self.timer.stop()

        return len(cmd.marked), accept, reject

    def update_crumbs(self):
        head = self.owner or self.pid
        labels = [
            "Map",
            *self.crumb_titles,
            *(
                ["Urgent"]
                if self.on_urgent
                else [self.cards[head].title if head in self.cards else IMAGE_NOTE]
                if self.pid
                else []
            ),
            *([self.page.context.text()] if self.owner in self.cards else []),
        ]
        self.bar.strip.sync()
        # widgets are kept and hidden: a click handler may be what triggers the rebuild
        while len(self.crumb_btns) < len(labels):
            depth = len(self.crumb_btns)
            b = Crumb()
            b.clicked.connect(lambda _=False, at=depth: self.go_depth(at))
            self.crumb_btns.append(b)
            self.crumb_seps.append(Chevron())
            self.crumbs.row.insertWidget(2 * depth, self.crumb_seps[-1])
            self.crumbs.row.insertWidget(2 * depth + 1, b)
        for i, (b, sep) in enumerate(zip(self.crumb_btns, self.crumb_seps)):
            b.setVisible(i < len(labels))
            sep.setVisible(0 < i < len(labels))
            if i < len(labels):
                b.setText(labels[i])
                b.set_last(i == len(labels) - 1)

    def load_board(self, path):
        self.claude_abort()
        self.leave_page()
        self.save_current()
        self.save_layout()
        self.page_zoom("")
        self.board_views[tuple(self.board_path)] = self.map_view_state()
        mp, faces, titles = board_data(self.store, path, self.settings)
        self.pid, self.owner = "", ""
        self.sync_urgent_hide()
        self.show_map()
        self.fill_scene(path, titles, mp, faces)
        self.setWindowTitle(self.title())
        self.view.setFocus()
        self.pending_view = self.board_views.get(tuple(path))
        if self.pending_view is not None:
            self.apply_pending_view()
        else:
            self.view.resetTransform()
            boxes = list(self.cards.values())
            self.view.centerOn(rdb_map.union(boxes).center() if boxes else QPointF())

    def go_depth(self, depth):
        if depth < len(self.board_path):
            self.load_board(self.board_path[:depth])
            self.push("")
        elif self.owner in self.cards and depth == len(self.board_path) + 1:
            self.open_project(self.owner)
        elif self.pid:
            self.go_map()

    def up(self):
        left = self.board_path[-1]
        self.load_board(self.board_path[:-1])
        self.select_card(left)
        self.push("")

    def map_view_state(self):
        # a map that is hidden, or was never shown, has its view pending
        return self.pending_view or self.view.view_state()

    def show_map(self):
        self.stack.setCurrentWidget(self.map_page)

    def save_view(self):
        if self.in_contacts():
            block = self.contacts.current_row()
        else:
            block = self.editor.textCursor().blockNumber() if self.pid else 0
        state = self.map_view_state()
        zoom = {k: state[k] for k in ("zoom", "cx", "cy")}
        self.store.put_view(
            {
                **zoom,
                "pid": "" if self.on_urgent else self.pid,
                "block": block,
                "board_path": self.board_path,
            }
        )

    def restore_view(self, v):
        if v is None:
            return
        # centring needs the map's real viewport size, which it only has once on screen
        self.pending_view = v
        self.apply_pending_view()
        if v["pid"] in self.cards or self.note_owner(v["pid"]) is not None:
            self.open_project(v["pid"], v["block"])

    def apply_pending_view(self):
        if self.pending_view is not None and self.view.isVisible():
            self.view.set_view_state(self.pending_view)
            self.pending_view = None

    def showEvent(self, e):
        super().showEvent(e)
        self.apply_pending_view()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.toast.place()

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() in SYNC_EVENTS:
            self.bar.sync()

    def paintEvent(self, e):
        super().paintEvent(e)
        paint_border(self)

    def check_from_card(self, pid, block):
        # card checkboxes are only reachable on the map, where every project is saved
        mode = self.mode_of(pid)
        doc = m.open_doc(self.store, pid, mode)
        b = doc.findBlockByNumber(block)
        if not m.is_todo(b):
            raise ValueError(f"{pid}: block {block} is not an item")
        with self.recorded(rows=(pid,)):
            m.toggle_check(doc, b, mode)
            m.save_project(self.store, pid, doc)
        self.cards[pid].set_summary(self.summary(doc, pid))

    def add_contacts(self, pid, rows):
        old = self.store.get_contacts(pid)
        new = rdb_contacts.unseen(old, rows)
        if not new:
            return
        with self.recorded(contacts=(pid,)):
            self.store.put_contacts(pid, old + new)
        self.cards[pid].set_summary(
            rdb_contacts.summary(old + new, m.preview_limit(self.settings))
        )

    def mark_from_card(self, pid, block, mark):
        doc = m.open_doc(self.store, pid, self.mode_of(pid))
        b = doc.findBlockByNumber(block)
        with self.recorded(rows=(pid,)):
            m.set_marker(b, mark)
            if m.is_sorted(doc):
                m.place_item(doc, b)
            m.save_project(self.store, pid, doc)
        self.cards[pid].set_summary(self.summary(doc, pid))

    def title(self, project=""):
        name = f"rdb - {self.store.path.stem}"
        return f"{name} - {project}" if project else name

    def _pick_new_path(self, caption):
        path, _ = QFileDialog.getSaveFileName(
            self,
            caption,
            str(self.store.path.parent),
            "rdb (*.rdb)",
            options=QFileDialog.Option.DontConfirmOverwrite,
        )
        if not path:
            return None
        path = Path(path).with_suffix(".rdb")
        if path.exists():
            QMessageBox.warning(self, caption, f"{path} exists")
            return None
        return path

    def fill_recent(self):
        menu = self.recent_menu
        menu.clear()
        here = str(self.store.path)
        paths = [
            p for p in app_settings().value("recent_files", [], type=list) if p != here
        ]
        for i, p in enumerate(paths, 1):
            act = menu.addAction(f"&{i} {p}")
            act.setData(p)
            act.setEnabled(Path(p).is_file())
            act.triggered.connect(lambda _=False, p=p: self.switch_file(p, "Open"))
        menu.menuAction().setEnabled(bool(paths))

    def switch_file(self, path, caption):
        try:
            self.load_file(path)
        except LOAD_ERRORS as e:
            QMessageBox.warning(self, caption, str(e))

    def new_file(self):
        path = self._pick_new_path("New")
        if path is not None:
            self.switch_file(path, "New")

    def open_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open", str(self.store.path.parent), "rdb (*.rdb)"
        )
        if path:
            self.switch_file(path, "Open")

    def save_as(self):
        path = self._pick_new_path("Save As")
        if path is None:
            return
        self.save_current()
        self.restore_clipboard()
        self.save_layout()
        self.store.backup_to(path)
        self.switch_file(path, "Save As")

    def eventFilter(self, obj, e):
        kind = e.type()
        out = self.claudeout
        # a click in the pane took the focus; Enter and Esc still go to the bar
        if (
            kind == QEvent.Type.KeyPress
            and isinstance(obj, QWidget)
            and (obj is out or out.isAncestorOf(obj))
            and e.key() in (*ENTER, Qt.Key.Key_Escape)
            and not e.modifiers() & (CTRL_SHIFT | Qt.KeyboardModifier.AltModifier)
        ):
            edit = self.claudebar.edit
            edit.setFocus()
            QApplication.sendEvent(
                edit, QKeyEvent(kind, e.key(), e.modifiers(), e.text())
            )
            return True
        if self.claude_pending is not None and self.claude_blocks(obj, e):
            return True
        if (
            kind == QEvent.Type.MouseButtonPress
            and e.button() in (Qt.MouseButton.BackButton, Qt.MouseButton.ForwardButton)
            and isinstance(obj, QWidget)
            and obj.window() is self
            and QApplication.activeModalWidget() is None
        ):
            self.step(-1 if e.button() == Qt.MouseButton.BackButton else 1)
            return True
        if (
            kind in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress)
            and e.modifiers() == Qt.KeyboardModifier.ControlModifier
            and e.key() in HISTORY_KEYS
            and self.settings["ctrl_arrows"] == "History"
            and isinstance(obj, QWidget)
            and obj.window() is self
            # leaving the map would delete a card title editor mid-edit and leave the view dead
            and not (obj is self.view and isinstance(self.view._ed, TitleEditor))
            # in a timeline they size the bar of the current row
            and not (obj is self.editor and self.editor.timeline)
        ):
            # Ctrl+Left/Right are word keys in text fields; here they navigate. A held key steps once
            if kind == QEvent.Type.ShortcutOverride:
                e.accept()
            elif not e.isAutoRepeat():
                HISTORY_KEYS[e.key()](self)
            return True
        if (
            kind == QEvent.Type.KeyPress
            and e.key() == Qt.Key.Key_F
            and e.modifiers() == Qt.KeyboardModifier.ControlModifier
            and isinstance(obj, QWidget)
            and obj.window() is self
            and QApplication.activeModalWidget() is None
            # a card title or a text block being typed keeps its focus
            and self.view._ed is None
        ):
            if not e.isAutoRepeat():
                self.open_find()
            return True
        if (
            kind == QEvent.Type.KeyPress
            and e.key() == Qt.Key.Key_F3
            and e.modifiers()
            in (Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.ShiftModifier)
            and isinstance(obj, QWidget)
            and obj.window() is self
            and self.current_findbar().isVisible()
        ):
            back = e.modifiers() == Qt.KeyboardModifier.ShiftModifier
            self.current_findbar().step(-1 if back else 1)
            return True
        if (
            kind == QEvent.Type.KeyPress
            and obj is self.editor
            and e.text() == "/"
            and not e.modifiers()
            & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)
            and self.editor.mode == "normal"
            and not (self.editor.vim.buf or self.editor.vim.prompt is not None)
        ):
            if not e.isAutoRepeat():
                self.open_find()
            return True
        if (
            kind == QEvent.Type.KeyPress
            and e.key() == Qt.Key.Key_Backspace
            and e.modifiers() == Qt.KeyboardModifier.NoModifier
            and (
                (obj is self.view and self.view._ed is None)
                or obj is self.page.grid
                # vim normal mode, unless a count, operator or search line is pending
                or (
                    obj is self.editor
                    and self.editor.mode == "normal"
                    and not (self.editor.vim.buf or self.editor.vim.prompt is not None)
                )
            )
        ):
            if not e.isAutoRepeat():
                self.step(-1)
            return True
        if (
            kind == QEvent.Type.KeyPress
            and obj is self.view
            and e.key() == Qt.Key.Key_Escape
            and self.view._ed is None
            and self.board_path
            # Esc during a drag only cancels the drag
            and not self.view.cancel_drag()
        ):
            self.up()
            return True
        return super().eventFilter(obj, e)

    def current_findbar(self):
        if not self.pid:
            return self.mapbar
        return self.kanbanbar if self.on_board() else self.editorbar

    def on_board(self):
        return self.page.body.currentWidget() is self.page.board

    def open_find(self):
        if self.in_contacts():
            self.contacts.focus_filter()
        elif self.pid and self.page.cards_shown:
            self.page.grid.focus_filter()
        else:
            self.current_findbar().open()

    def show_db_info(self):
        DbInfoDialog(self, self.store).exec()

    def find_texts(self):
        # the texts of the items, headings and notes of each list on this board, from the finder index
        board, out = tuple(self.board_path), {}
        for e in self.finder_entries():
            if e.board_path == board and e.kind not in ("card", "board", "text"):
                out.setdefault(e.card["id"], []).extend((e.text, *e.hay))
        return out

    def close_finds(self):
        self.mapbar.suspend()
        self.editorbar.suspend()
        self.kanbanbar.suspend()

    def resume_finds(self):
        on_page = self.stack.currentWidget() is (
            self.page if self.pid else self.map_page
        )
        # the cards grid hides the editor the bar would search
        if on_page and not (self.pid and self.page.cards_shown):
            self.current_findbar().resume()

    def zoom_card(self):
        # the card whose page is shown and keeps its own zoom; None when the page is not a card's
        if not self.settings["card_zoom"] or self.on_urgent:
            return None
        return self.cards.get(self.owner or self.pid)

    def save_zoom(self, z):
        if self.zoom_applying:
            return
        card = self.zoom_card()
        if card is None:
            self.global_zoom = z
            app_settings().setValue("editor_zoom", z)
        else:
            card.zoom = z
            self.zoom_timer.start()

    def page_zoom(self, owner):
        # owner: the card whose page is about to show, "" for a page that is no card's
        if self.zoom_timer.isActive():
            self.zoom_timer.stop()
            self.save_layout()
        card = self.cards.get(owner) if self.settings["card_zoom"] else None
        self.zoom_applying = True
        try:
            set_zoom(card.zoom if card is not None and card.zoom else self.global_zoom)
        finally:
            self.zoom_applying = False

    def link_row_clicked(self, pid, block, external):
        # link rows are only openable on the map, where every project is saved
        doc = m.open_doc(self.store, pid, self.mode_of(pid))
        b = doc.findBlockByNumber(block)
        if not b.isValid():
            raise ValueError(f"{pid}: no block {block}")
        for _, _, href in m.link_runs(b) or m.detected_links(b):
            self.open_link(href, external)
            return

    def open_link(self, href, external):
        # a path link opens through the desktop; external means its folder.
        # An rdoc spot opens in rdoc, which is started when it is not running
        self.editor.mark_visited(href)
        if m.is_path_link(href):
            if (spot := rdb_rdoc.parse_href(href)) and not external:
                if not rdb_rdoc.goto(*spot):
                    rdb_rdoc.launch(
                        rdb_paths.resolve(self.settings["rdoc_path"]), *spot
                    )
            else:
                QDesktopServices.openUrl(m.path_open_url(href, external))
        else:
            QDesktopServices.openUrl(QUrl(href))

    @property
    def on_urgent(self):
        return self.pid == URGENT_PID

    def in_contacts(self):
        return (
            bool(self.pid)
            and not self.owner
            and not self.on_urgent
            and self.cards[self.pid].type == "contacts"
        )

    def in_links(self):
        return (
            bool(self.pid)
            and not self.owner
            and not self.on_urgent
            and self.cards[self.pid].type == "links"
        )

    def sync_urgent_hide(self):
        text_page = (
            bool(self.pid)
            and not self.owner
            and not self.on_urgent
            and self.cards[self.pid].type == "notepad"
        )
        self.urgent.suppress(text_page and self.settings["notepad_hide_urgent"])

    def leave_page(self):
        self.close_finds()

    def show_editor_menu(self, pos):
        menu = self.editor.note_menu(pos)
        if menu is None:
            menu = self.editor_menu(pos)
        if not menu.isEmpty():
            menu.exec(self.editor.viewport().mapToGlobal(pos))
        menu.deleteLater()

    def editor_menu(self, pos):
        menu = QMenu(self.editor)
        at = self.editor.cursorForPosition(pos).block()
        if m.is_section(at) and not self.on_urgent and not self.editor.isReadOnly():
            act = menu.addAction("Delete all completed")
            act.setEnabled(bool(m.done_items(self.doc)))
            act.triggered.connect(self.delete_completed)
            menu.addSeparator()
        if (head := self.editor.clicked_h1(pos)) is not None:
            menu.addAction("Rename").triggered.connect(
                lambda: self.editor.rename_heading(head)
            )
            menu.addSeparator()
        if not self.editor.isReadOnly() and self.editor.href_at(pos):
            menu.addAction("Edit Link...").triggered.connect(
                lambda: self.editor.edit_link_at(pos)
            )
            menu.addSeparator()
        todos = [] if self.owner else self.move_sources(pos)
        # a Note card's find bar has no tag controls to clear a tag filter with
        tag = (
            None
            if self.on_urgent or self.editor.text_page
            else self.editor.tag_text_at(pos)
        )
        if tag:
            menu.addAction("Filter").triggered.connect(
                lambda: self.editorbar.filter_tag(tag)
            )
            menu.addAction("Find everywhere").triggered.connect(
                lambda: self.find(query=tag)
            )
            menu.addAction("Rename tag...").triggered.connect(
                lambda: self.rename_tag(tag)
            )
            menu.addSeparator()
        fmt = self.editor.format_menu(menu, pos)
        if fmt is not None:
            menu.addMenu(fmt)
        if self.in_links():
            urls = self.link_targets(pos)
            act = menu.addAction("Auto-Create Link Descriptions")
            act.setEnabled(bool(urls))
            act.triggered.connect(lambda: self.describe_links(urls))
        if todos:
            self.add_move_menu(menu, todos)
        return menu

    def delete_completed(self):
        # one undo step; the Completed header goes with its last item
        m.remove_items(self.doc, m.done_items(self.doc))
        self.save_current()

    def rename_tag(self, tag):
        new = ask_tag_name(self, tag[1:])
        if new is None or new == tag[1:]:
            return
        self.rename_tag_everywhere(tag[1:], new)

    def edit_tags(self, edit):
        # edit(live) changes every project and card in the store, the open document in place
        self.save_current()
        self.save_layout()
        live = self.live_doc_for_tags()
        changed = edit(live)
        if live is not None and live[0] in changed:
            self.doc.setModified(False)
        mp = (
            m.load_board(self.store, self.board_path[-1])
            if self.board_path
            else m.load_map(self.store)
        )
        for c in mp["cards"]:
            self.cards[c["id"]].tags = list(c["tags"])
            self.cards[c["id"]].update()
        for pid in changed:
            self.refresh_cards_of(pid)
        if self.on_urgent and changed:
            # the page is a copy of the cards' items: made again from what is stored, or a later
            # edit on it writes the old tag back
            bar = self.editor.verticalScrollBar()
            scroll = bar.value()
            self.open_urgent()
            bar.setValue(scroll)

    def rename_tag_everywhere(self, old, new):
        self.edit_tags(lambda live: m.rename_tag_all(self.store, live, old, new))
        final = dict((n.lower(), n) for n, _ in self.tag_rows())
        self.store.put_tag_sets(
            rename_in_sets(self.store.get_tag_sets(), old, final.get(new.lower(), new))
        )

    def delete_tags_everywhere(self, names):
        self.edit_tags(lambda live: m.delete_tags_all(self.store, live, names))
        self.store.put_tag_sets(drop_from_sets(self.store.get_tag_sets(), names))

    def tag_sets(self):
        return self.store.get_tag_sets()

    def tag_manager(self):
        return TagManager(
            self.tag_rows,
            self.rename_tag_everywhere,
            self.delete_tags_everywhere,
            (self.store.get_tag_sets, self.store.put_tag_sets),
        )

    def tag_rows(self):
        self.save_layout()
        return m.tag_table(self.store, self.live_doc_for_tags())

    def live_doc_for_tags(self):
        return None if self.in_contacts() else self.live_doc()

    def move_sources(self, pos):
        return self.sources_at(self.editor.cursorForPosition(pos))

    def sources_at(self, at):
        # the todos the menu acts on: those a selection touches when the click is inside it,
        # else the one clicked; hidden blocks are not part of what the user sees selected
        c = self.editor.textCursor()
        blocks = [at.block()]
        if c.hasSelection() and c.selectionStart() <= at.position() <= c.selectionEnd():
            blocks, b = [], self.doc.findBlock(c.selectionStart())
            while b.isValid() and b.position() < c.selectionEnd():
                blocks.append(b)
                b = b.next()
        todos = {}
        for b in blocks:
            t = m.owner_todo(b)
            if t is not None and b.isVisible():
                todos[t.blockNumber()] = t
        return list(todos.values())

    def move_choices(self):
        found = {}
        for path, titles, mp in self.maps():
            board = titles[-1] if titles else "Map"
            for c in mp["cards"]:
                if c["type"] in m.PROJECT_TYPES and c["id"] != self.pid:
                    label = (
                        c["title"]
                        if list(path) == self.board_path
                        else f"{c['title']} ({board})"
                    )
                    found[c["id"]] = (label, Target("card", path, c["id"], None))
        # a move target counts as opened at the history entry it was made from
        order = []
        for key in reversed(self.history):
            order += [pid for at, pid in self.recent_moves if at == key]
            order.append(key[1])
        recent = [found[p] for p in dict.fromkeys(order) if p in found][:5]
        return recent, len(found) > len(recent)

    def add_move_menu(self, menu, todos):
        recent, more = self.move_choices()
        sub = menu.addMenu("Move To")
        sub.menuAction().setEnabled(bool(recent or more))
        for label, target in recent:
            act = sub.addAction(label.replace("&", "&&"))
            act.triggered.connect(lambda _=False, t=target: self.move_items(todos, t))
        if more:
            if recent:
                sub.addSeparator()
            act = sub.addAction("More Cards...")
            act.triggered.connect(lambda _=False: self.move_picked(todos))

    def pick_card(self):
        entries = card_entries(self.finder_entries(), self.pid)
        dlg = Finder(
            self, entries, "Move To", self.store, self.settings, app_settings()
        )
        picked = dlg.choice if dlg.exec() else None
        dlg.deleteLater()
        return picked

    def quick_move(self, block):
        # like the QuickMenu's formatting: the selection if any, else the item it opened on
        c = self.editor.textCursor()
        self.move_picked(self.sources_at(c if c.hasSelection() else QTextCursor(block)))

    def move_picked(self, todos):
        target = self.pick_card()
        if target is not None:
            self.move_items(todos, target)

    def move_items(self, todos, target):
        if self.on_urgent:
            self.move_urgent(todos, target)
            return
        if target.pid == self.pid:
            raise ValueError(f"{target.pid}: items cannot move into their own list")
        cards = map_at(self.store, list(target.board_path))["cards"]
        card = {c["id"]: c for c in cards}[target.pid]
        if card["type"] not in m.PROJECT_TYPES:
            raise ValueError(f"{target.pid}: a {card['type']} card holds no items")
        items = [m.item_blocks(t) for t in todos]
        mimes = [self.editor.item_mime(blocks, True) for blocks in items]
        # the cut items go through the editor's own paste into the target list, which stays
        # closed: it is saved, and only then are the items taken from this one
        # this list still holds the notes of the items until they are taken from it
        self.paste_into(target.pid, self.mode_of(card), mimes, (self.pid,))
        for pid in (target.pid, *target.board_path):
            if pid in self.cards:
                self.refresh_card(self.cards[pid])
        self.recent_moves = [r for r in self.recent_moves if r[1] != target.pid]
        self.recent_moves.insert(0, (self.history[self.hpos], target.pid))
        m.remove_items(self.doc, items)
        self.save_current()

    def move_urgent(self, todos, target):
        # the page is written first, so every row names a stored item; each card gives up its
        # items as a cut, the target takes them, and the page is rebuilt from the store
        page = self.urgent_page
        self.save_current()
        cards = map_at(self.store, list(target.board_path))["cards"]
        card = {c["id"]: c for c in cards}[target.pid]
        if card["type"] not in m.PROJECT_TYPES:
            raise ValueError(f"{target.pid}: a {card['type']} card holds no items")
        moves = {}
        for t in todos:
            pid, sid = page.items[m.get_meta(t)["i"]]
            if pid != target.pid:
                moves.setdefault(pid, []).append(sid)
        for pid, sids in moves.items():
            doc = m.open_doc(self.store, pid, page.mode(pid))
            items = [m.item_blocks(m.unit_block(doc, sid)) for sid in sids]
            mimes = [self.editor.item_mime(blocks, True) for blocks in items]
            self.paste_into(target.pid, self.mode_of(card), mimes, (pid,))
            m.remove_items(doc, items)
            m.normalize(doc, page.mode(pid))
            m.save_project(self.store, pid, doc)
            for p in (pid, *page.boards[pid]):
                if p in self.cards:
                    self.refresh_card(self.cards[p])
        for pid in (target.pid, *target.board_path):
            if pid in self.cards:
                self.refresh_card(self.cards[pid])
        self.recent_moves = [r for r in self.recent_moves if r[1] != target.pid]
        self.recent_moves.insert(0, (self.history[self.hpos], target.pid))
        bar = self.editor.verticalScrollBar()
        y = bar.value()
        self.open_urgent()
        if self.urgent_page is page:
            # nothing is urgent any more: the page has nothing to show
            self.go_map()
        else:
            bar.setValue(y)
        self.urgent_soon.start()

    def quick_add(self, holding=False):
        hold = self.ensure_holding() if holding else None
        full = self.finder_entries()
        entries = quick_entries(full)
        default = quick_default(entries, self.store)
        if holding:
            default = next(e for e in entries if e.kind == "card" and e.pid == hold)
        dlg = QuickAdd(self, entries, default, quick_counts(full), urgent=holding)
        if holding:
            dlg.text.setFocus()
        item = dlg.item if dlg.exec() else None
        dlg.deleteLater()
        if item is not None:
            # the holding card stays out of the remembered location unless it was picked
            self.add_quick_item(
                item,
                remember=not holding or quick_loc(item.entry) != quick_loc(default),
            )

    def is_list_card(self, pid):
        return any(
            c["id"] == pid and c["type"] == "list"
            for mp in m.all_maps(self.store)
            for c in mp["cards"]
        )

    def ensure_holding(self):
        pid = self.store.get_holding_card()
        return pid if self.is_list_card(pid) else self.make_holding()

    def ensure_dump(self):
        pid = self.store.get_dump_card()
        return pid if self.is_list_card(pid) else self.make_dump()

    @undoable
    def make_holding(self):
        return self.make_inbox_list("Uncategorized", self.store.put_holding_card)

    @undoable
    def make_dump(self):
        return self.make_inbox_list("Dump", self.store.put_dump_card)

    def make_inbox_list(self, title, remember):
        # the stored id needs no undo of its own: once the card is gone it names nothing
        shown = self.board_path == ["inbox"]
        if shown:
            self.view.commit_text()
        target = self.layout_dict() if shown else m.load_board(self.store, "inbox")
        pid = m.make_id(title, self.taken_ids())
        card = m.new_card(pid, title, *snip_spot(target))
        # its project is made here, so undoing the card takes the items added to it as well
        mode = m.done_mode(card["done"], self.settings)
        m.save_project(self.store, pid, m.open_doc(self.store, pid, mode))
        remember(pid)
        if shown:
            # the card joins the scene; a rebuild would delete a title editor mid-edit
            show_face(self.add_card(card), card_face(self.store, card, self.settings))
            self.save_layout()
        else:
            target["cards"].append(card)
            m.save_board(self.store, "inbox", target)
        return pid

    def append_to(self, pid, card, fn):
        # fn(editor) adds to the end of a card's document, which is the open editor's when
        # the card is open and a throwaway pasteboard's otherwise; the latter is one undo step
        if pid == self.pid:
            fn(self.editor)
            self.save_current()
        else:
            ed = Pasteboard(self.store, self.settings)
            ed.pid, ed.done, ed.sort = pid, card["done"], card["sort"]
            ed.timeline = card["type"] == "timeline"
            doc = m.open_doc(self.store, pid, m.done_mode(ed.done, self.settings))
            ed.set_document(doc)
            doc.setParent(ed)
            # a card never opened has no project yet; the command then records it as made
            rows = (pid,) if self.store.has_project(pid) else ()
            with self.recorded(rows=rows):
                fn(ed)
                m.save_project(self.store, pid, doc)
            ed.deleteLater()
        if self.stack.currentWidget() is self.triage:
            self.reload_triage()

    def refresh_cards_of(self, pid):
        for p in (pid, *self.locate(pid)):
            if p in self.cards:
                self.refresh_card(self.cards[p])

    def add_quick_item(self, item, remember=True):
        e = item.entry
        args = (None if e.kind == "card" else e.block, *item[1:])
        self.append_to(e.pid, e.card, lambda ed: add_item(ed, *args))
        for pid in (e.pid, *e.board_path):
            if pid in self.cards:
                self.refresh_card(self.cards[pid])
        if remember:
            self.store.put_quick_add(quick_loc(e))
        self.toast.show_message(f"+1 {e.card['title']}", 4000)

    def file_dump(self, item):
        pid = self.ensure_dump()
        card = next(
            c for mp in m.all_maps(self.store) for c in mp["cards"] if c["id"] == pid
        )
        self.append_to(pid, card, lambda ed: dump_into(ed, item, self.settings))
        self.refresh_cards_of(pid)

    def dump_card(self):
        pid = self.store.get_dump_card()
        return next(
            (
                c
                for mp in m.all_maps(self.store)
                for c in mp["cards"]
                if c["id"] == pid and c["type"] == "list"
            ),
            None,
        )

    def triage_doc(self):
        card = self.dump_card()
        return m.open_doc(self.store, card["id"], self.mode_of(card))

    def reload_triage(self, at=None):
        views = []
        if self.dump_card() is not None:
            # a block dies with its document
            doc = self.triage_doc()
            views = [item_view(self.store, b) for b in open_items(doc)]
        self.triage.load(views, at)

    def open_triage(self):
        self.claude_abort()
        self.close_finds()
        self.save_current()
        if not self.pid:
            self.pending_view = self.map_view_state()
        self.leave_page()
        self.page_zoom("")
        self.pid, self.owner = "", ""
        self.sync_urgent_hide()
        self.reload_triage(0)
        self.stack.setCurrentWidget(self.triage)
        self.triage.setFocus()
        self.setWindowTitle(self.title("Triage"))
        self.update_crumbs()

    def pick_location(self):
        full = self.finder_entries()
        dump = self.store.get_dump_card()
        entries = [
            e
            for e in quick_entries(full)
            if e.kind in ("board", "card") and e.pid != dump
        ]
        dlg = PickCard(
            self, entries, quick_default(entries, self.store), quick_counts(full)
        )
        entry = dlg.target if dlg.exec() else None
        dlg.deleteLater()
        return entry

    def triage_edit(self, n, fn, extra=()):
        # fn(doc, item blocks) changes the dump list's document; one undo step
        pid = self.dump_card()["id"]
        doc = self.triage_doc()
        blocks = m.item_blocks(open_items(doc)[n])
        rows = (pid, *(p for p in extra if self.store.has_project(p)))
        with self.recorded(rows=rows):
            fn(doc, blocks)
            m.normalize(doc, self.mode_of(self.dump_card()))
            m.save_project(self.store, pid, doc)
        for p in (pid, *extra):
            self.refresh_cards_of(p)
        self.reload_triage()

    def triage_urgent(self):
        def mark(doc, blocks):
            m.set_marker(blocks[0], "p1")
            if m.is_sorted(doc):
                m.place_item(doc, blocks[0])

        self.triage_edit(self.triage.acted, mark)

    def triage_date(self, start, end):
        self.triage_edit(
            self.triage.acted,
            lambda doc, blocks: rdb_timeline._set_dates(doc, blocks[0], start, end),
        )

    def triage_delete(self):
        # like the editor's delete: the item stays on the clipboard
        def cut(doc, blocks):
            QApplication.clipboard().setMimeData(self.editor.item_mime(blocks, True))
            m.remove_items(doc, [blocks])

        self.triage_edit(self.triage.acted, cut)

    def triage_move(self):
        entry = self.pick_location()
        if entry is not None:
            self.triage_file(self.triage.acted, entry)

    def triage_file(self, n, entry):
        card = entry.card
        if card["type"] not in m.PROJECT_TYPES:
            raise ValueError(f"{entry.pid}: a {card['type']} card holds no items")
        dump = self.dump_card()["id"]

        def move(doc, blocks):
            mime = self.editor.item_mime(blocks, True)
            self.paste_into(entry.pid, self.mode_of(card), [mime], (dump,))
            m.remove_items(doc, [blocks])

        self.triage_edit(n, move, (entry.pid, *entry.board_path))

    def install_dump_key(self):
        self.dump_key = rdb_hotkey.DumpKey(self, self.open_dump, self.dump_key_taken)
        self.dump_key.set_key(self.settings["dump_key"])

    def dump_key_taken(self, text):
        self.toast.show_message(f"{text} taken")

    def open_dump(self, title):
        if self.dump_box is None:
            box = DumpBox(self.file_dump, title)
            box.finished.connect(lambda _: self.drop_dump(box))
            self.dump_box = box
        self.dump_box.title = title
        self.dump_box.popup()

    def drop_dump(self, box):
        if self.dump_box is box:
            self.dump_box = None

    def paste_into(self, pid, mode, mimes, ignore):
        doc = m.open_doc(self.store, pid, mode)
        doc.setUndoRedoEnabled(False)
        ed = Pasteboard(self.store, self.settings)
        # plain appends: the target's own mode sorts the checked items once, below
        ed.pid, ed.done = pid, "place"
        ed.set_document(doc)
        doc.setParent(ed)
        sec = m.section_block(doc)
        tail = sec.previous() if sec is not None else doc.lastBlock()
        if not tail.isValid():
            # Completed opens the list: an empty todo above it takes the paste
            QTextCursor(sec).insertBlock(sec.blockFormat(), QTextCharFormat())
            tail = doc.begin()
            m.set_meta(tail, **m.todo_meta(), **m.NEW_ITEM)
        # a lone empty todo at the end of the open area is the list's blank line, not an item
        blank = m.owner_todo(tail)
        if blank is not None and (
            len(m.item_blocks(blank)) > 1 or m.item_text(blank) or m.is_checked(blank)
        ):
            blank = None
        ed.setTextCursor(QTextCursor(tail))
        for md in mimes:
            ed._paste_items(md, ignore)
        if blank is not None:
            m.remove_item(doc, [blank])
        m.normalize(doc, mode)
        m.save_project(self.store, pid, doc)
        ed.deleteLater()

    @undoable
    def list_from_results(self, query, entries):
        self.save_layout()
        card = CardItem(m.make_id(query, self.taken_ids()), query)
        dx, dy = drop_offset(
            map_at(self.store, self.board_path),
            [{"x": 0, "y": 0, "w": card.w, "h": card.h}],
        )
        card.setPos(dx, dy)
        self.scene.clearSelection()
        self.scene.addItem(card)
        card.setSelected(True)
        self.cards[card.pid] = card
        docs = {}
        mimes = []
        for e in entries:
            doc = docs.get(e.pid) or docs.setdefault(
                e.pid, m.open_doc(self.store, e.pid, self.mode_of(e.card))
            )
            todo = doc.findBlockByNumber(e.block)
            if not m.is_todo(todo) or m.is_checked(todo):
                raise ValueError(f"{e.pid}: block {e.block} is no open todo")
            mimes.append(self.editor.item_mime(m.item_blocks(todo), False))
        self.paste_into(card.pid, self.mode_of(card.pid), mimes, ())
        self.save_layout()
        self.refresh_card(card)
        self.open_project(card.pid)

    def link_targets(self, pos):
        c = self.editor.textCursor()
        if c.hasSelection():
            return m.url_links(self.doc, c.selectionStart(), c.selectionEnd())
        # the caret index nearest the click is on either side of the character hit
        p = self.editor.cursorForPosition(pos).position()
        href = self.editor.anchorAt(pos)
        return [u for u in m.url_links(self.doc, p - 1, p + 1) if u == href]

    def mode_of(self, card):
        # the model mode of a card dict, or of the card with that pid on this board
        if isinstance(card, str) and (m.is_note_pid(card) or card == URGENT_PID):
            return m.NOTE_MODE
        done = card["done"] if isinstance(card, dict) else self.cards[card].done
        return m.done_mode(done, self.settings)

    def summary(self, doc, pid):
        return face_summary(self.cards[pid].type, doc, self.settings)

    def add_card(self, c):
        card = CardItem(c["id"], c["title"], c["w"], c["h"])
        card.type = c["type"]
        card.bg, card.fg, card.url, card.view = c["bg"], c["fg"], c["url"], c["view"]
        card.kanban = c["kanban"]
        card.kanban_done = c["kanban_done"]
        card.kanban_w = dict(c["kanban_w"])
        card.kanban_status_only = c["kanban_status_only"]
        card.kanban_order = list(c["kanban_order"])
        card.done = "place" if c["type"] == "timeline" else c["done"]
        card.sort = c["sort"]
        card.lang = c["lang"]
        card.zoom = c["zoom"]
        card.scope = c["scope"]
        card.tags = list(c["tags"])
        card.setPos(c["x"], c["y"])
        self.scene.addItem(card)
        self.cards[c["id"]] = card
        if c["snap"]:
            self.snaps[c["id"]] = c["snap"]
        return card

    def card_dict(self, c):
        return {
            "id": c.pid,
            "title": c.title,
            "x": c.x(),
            "y": c.y(),
            "w": c.w,
            "h": c.h,
            "type": c.type,
            "bg": c.bg,
            "fg": c.fg,
            "tags": list(c.tags),
            "url": c.url,
            "snap": self.snaps.get(c.pid, ""),
            "view": c.view,
            "kanban": c.kanban,
            "kanban_done": c.kanban_done,
            "kanban_w": dict(c.kanban_w),
            "kanban_status_only": c.kanban_status_only,
            "kanban_order": list(c.kanban_order),
            "done": c.done,
            "sort": c.sort,
            "lang": c.lang,
            "zoom": c.zoom,
            "scope": c.scope,
        }

    def refresh_card(self, card):
        doc = None
        if card.pid == self.pid:
            doc = self.contacts.model.rows if card.type == "contacts" else self.doc
        show_face(card, card_face(self.store, self.card_dict(card), self.settings, doc))

    def remove_card(self, card):
        self.scene.removeItem(card)
        del self.cards[card.pid]
        self.snaps.pop(card.pid, None)

    def select_card(self, pid):
        if pid in self.cards:
            card = self.cards[pid]
            self.scene.clearSelection()
            card.setSelected(True)
            # a card in view stays put: scrolling it clear of the edge would move the map
            shown = self.view.mapFromScene(card.sceneBoundingRect()).boundingRect()
            if not self.view.viewport().rect().intersects(shown):
                self.view.ensureVisible(card)

    def enter(self, pid, block=None):
        card = self.cards[pid]
        if card.type == "board":
            self.load_board([*self.board_path, pid])
            self.push("")
        elif card.type == "page":
            QDesktopServices.openUrl(QUrl(card.url))
        else:
            self.open_project(pid, block)

    def note_owner(self, pid, among=None):
        # the list whose item, or the image block whose note, links this note document, among
        # the cards and blocks of this board; a document in the trash has no page even while a
        # stored list still names it
        if not (m.is_note_pid(pid) and self.store.has_project(pid)):
            return None
        held = [p for p, _ in self.store.note_holders(pid)]
        held += [
            b["id"]
            for _, _, mp in self.maps()
            for b in mp["blocks"]
            if b["kind"] == "image" and b["note"] == pid
        ]
        if among is None:
            among = {*self.cards, *(b.bid for b in self.block_items())}
        return next((p for p in held if p in among), None)

    def note_text(self, nt):
        return m.html_text(self.store.get_project(nt)["html"])

    def note_caption(self, nt):
        # a lost note document shows as missing, like a lost blob: raising here would stop
        # fill_scene halfway and the next save would drop every block after this one
        if nt and not self.store.has_project(nt):
            return f"missing {nt}"
        return caption_of(self.note_text(nt)) if nt else ""

    def note_excerpt(self, owner, nt):
        doc = (
            self.doc
            if owner == self.pid
            else m.open_doc(self.store, owner, self.mode_of(owner))
        )
        item = next(b for b in m.iter_blocks(doc) if m.note_link(b) == nt)
        return m.excerpt(m.item_text(item))

    def seek_note(self, nt):
        # back from a note page: the caret goes to the item that holds the note
        item = next((b for b in m.iter_blocks(self.doc) if m.note_link(b) == nt), None)
        if item is None:
            return
        if not item.isVisible():
            m.show_item(self.doc, item)
            m.apply_view(self.doc, self.mode_of(self.pid))
        self.editor.setTextCursor(QTextCursor(item))
        self.editor.ensureCursorVisible()

    def leave_editor(self):
        if self.owner in self.cards:
            self.open_project(self.owner)
        else:
            self.go_map()

    def open_project(self, pid, block=None, record=True):
        # pid is a list card, or a note document, which opens as the page of the item that links it
        # (the list is saved first: the link is found in what is stored)
        self.claude_abort()
        self.close_finds()
        self.save_current()
        note = m.is_note_pid(pid)
        owner = self.note_owner(pid) if note else pid
        if owner is None:
            raise KeyError(f"{self.store.path}: no item links note {pid!r}")
        image = note and owner not in self.cards
        card = None if image else self.cards[owner]
        if card is not None and card.type == "board":
            raise ValueError(f"{owner}: a board has no page")
        if card is not None and card.type == "contacts":
            self.open_contacts(card, block, record)
            return
        left = self.pid if self.owner else ""
        left_owner = self.owner
        back = self.note_return
        self.note_return = None
        if note and not self.owner and self.pid == owner:
            self.note_return = (owner, self.editor.verticalScrollBar().value())
        if not self.pid:
            # the map is about to be hidden, and it can be resized before it returns
            self.pending_view = self.map_view_state()
        if note or card.type != "links":
            self.leave_page()
        text_page = card is not None and card.type == "notepad"
        fresh = text_page and not self.store.has_project(pid)
        doc = m.open_doc(
            self.store, pid, m.NOTE_MODE if note else self.mode_of(pid), text_page
        )
        if text_page:
            m.plain_page(doc, fresh)
        context = ""
        if note:
            context = IMAGE_NOTE if image else self.note_excerpt(owner, pid)
        self.page_zoom(owner)
        self.editor.pid = pid
        self.editor.pinned = False
        self.editor.lang = card.lang if text_page else ""
        self.page.set_done("place" if note else card.done)
        self.page.set_sort("" if note else card.sort)
        bg, fg = ("", "") if image else (card.bg, card.fg)
        self.page.set_colors(bg, fg)
        # swap the editor first: dropping the old ref deletes the QTextDocument the editor still shows
        self.editor.set_document(doc)
        self.doc, self.pid, self.saved_collapsed = doc, pid, m.is_collapsed(doc)
        self.owner = owner if note else ""
        self.sync_urgent_hide()
        self.editorbar.set_plain(text_page)
        doc.contentsChanged.connect(self.timer.start)
        if block is not None:
            self.editor.setTextCursor(QTextCursor(doc.findBlockByNumber(block)))
            self.editor.ensureCursorVisible()
        elif left and pid == left_owner:
            self.seek_note(left)
            if back is not None and back[0] == pid:
                self.editor.verticalScrollBar().setValue(back[1])
        self.stack.setCurrentWidget(self.page)
        self.editor.setFocus()
        if note:
            self.page.set_note(context)
            self.page.set_kanban(False)
            self.setWindowTitle(
                self.title(f"{IMAGE_NOTE if image else card.title} - Note")
            )
            self.page.set_state("")
        else:
            self.page.set_links(
                card.type == "links", card.view, card.type == "timeline"
            )
            lst = card.type == "list"
            self.page.set_kanban_done(card.kanban_done if lst else True)
            self.page.set_kanban_widths(card.kanban_w if lst else {})
            self.page.set_kanban_status_only(card.kanban_status_only if lst else False)
            self.page.set_kanban_order(card.kanban_order if lst else [])
            self.page.set_kanban(card.kanban and lst)
            if self.page.cards_shown:
                self.fill_grid()
            self.setWindowTitle(self.title(card.title))
            self.page.set_title(card.title)
            if text_page:
                self.page.set_notepad(card.lang)
                self.page.set_state("")
            else:
                self.page.set_state(
                    m.state_text(self.summary(doc, pid), self.in_links())
                )
        self.update_crumbs()
        self.resume_finds()
        if record:
            self.push(pid)

    def open_urgent(self):
        # the banner's items as one editable page; the card on show is saved first, and the
        # page is made from what is stored
        self.claude_abort()
        self.close_finds()
        self.save_current()
        entries = self.urgent_entries(None)
        if not entries:
            self.resume_finds()
            return
        page = UrgentPage(self.store, self.settings, entries)
        if not self.pid:
            self.pending_view = self.map_view_state()
        self.leave_page()
        self.page_zoom("")
        self.editor.pid = URGENT_PID
        self.editor.pinned = True
        self.page.set_done("place")
        self.page.set_sort("")
        urgent = m.shown(self.settings, "urgent_color")
        self.page.set_colors(urgent, rdb_theme.ink_for(urgent).name(), drawn=True)
        self.page.set_links(False, "list")
        self.page.set_kanban(False)
        self.page.kanban_btn.hide()
        self.page.eye.hide()
        self.page.sort_btn.hide()
        self.editor.set_document(page.doc)
        page.doc.setModified(False)
        self.doc, self.pid, self.saved_collapsed = page.doc, URGENT_PID, False
        self.owner, self.urgent_page = "", page
        self.sync_urgent_hide()
        self.editorbar.set_plain(False)
        page.doc.contentsChanged.connect(self.timer.start)
        self.stack.setCurrentWidget(self.page)
        self.editor.setFocus()
        self.page.set_title("Urgent")
        self.page.set_state("")
        self.setWindowTitle(self.title("Urgent"))
        self.update_crumbs()
        self.resume_finds()

    def open_note(self, nt):
        if self.on_urgent:
            # the note opens on its source card's board, with that card as its owner
            self.save_current()
            owner = self.urgent_page.owner_of(nt)
            path = next(
                p
                for p, _, mp in self.maps()
                if any(c["id"] == owner for c in mp["cards"])
            )
            if list(path) != self.board_path:
                self.load_board(list(path))
                self.push("")
        self.open_project(nt)

    def open_contacts(self, card, row, record):
        self.claude_abort()
        if not self.pid:
            self.pending_view = self.map_view_state()
        self.leave_page()
        self.page_zoom(card.pid)
        self.contacts.set_colors(card.bg, card.fg)
        self.contacts.set_title(card.title)
        self.contacts.load(self.store.get_contacts(card.pid), row)
        self.pid, self.owner = card.pid, ""
        self.sync_urgent_hide()
        self.stack.setCurrentWidget(self.contacts)
        self.contacts.table.setFocus()
        self.setWindowTitle(self.title(card.title))
        self.update_crumbs()
        if record:
            self.push(card.pid)

    def go_map(self, record=True):
        self.claude_abort()
        left = self.owner or self.pid
        if left:
            self.leave_page()
        self.save_current()
        self.page_zoom("")
        self.pid, self.owner = "", ""
        self.sync_urgent_hide()
        self.show_map()
        self.apply_pending_view()
        self.view.setFocus()
        self.setWindowTitle(self.title())
        self.update_crumbs()
        self.select_card(left)
        self.resume_finds()
        if record:
            self.push("")

    def push(self, pid):
        key = (tuple(self.board_path), pid)
        if self.history[self.hpos] == key:
            return
        del self.history[self.hpos + 1 :]
        self.history.append(key)
        self.hpos += 1

    def step(self, d):
        # entries of cards and notes deleted or moved away since are gone before the position moves
        self.prune_history()
        # the urgent page is no history entry: Back shows the view it was opened from
        i = self.hpos + (0 if self.on_urgent and d < 0 else d)
        if not 0 <= i < len(self.history):
            return
        self.view.commit_text()
        self.hpos = i
        path, pid = self.history[i]
        if list(path) != self.board_path:
            self.load_board(list(path))
        if pid:
            self.open_project(pid, record=False)
        else:
            self.go_map(record=False)

    def save_current(self):
        self.timer.stop()
        if not self.pid:
            return
        if self.on_urgent:
            self.save_urgent()
            return
        if self.in_contacts():
            if self.contacts.dirty:
                rows = self.contacts.model.rows
                self.store.put_contacts(self.pid, rows)
                self.contacts.dirty = False
                self.cards[self.pid].set_summary(
                    rdb_contacts.summary(rows, m.preview_limit(self.settings))
                )
            return
        if self.doc.isModified() or m.is_collapsed(self.doc) != self.saved_collapsed:
            # an open item under Completed is a bug in an edit path; reopening would move it,
            # so the stored and the shown document would number their blocks differently
            mode = self.mode_of(self.pid)
            if m.has_stray(self.doc, mode):
                m.normalize(self.doc, mode)
            m.save_project(self.store, self.pid, self.doc)
            self.doc.setModified(False)
            self.saved_collapsed = m.is_collapsed(self.doc)
            self.urgent_soon.start()
            if not self.owner:
                summary = self.summary(self.doc, self.pid)
                self.cards[self.pid].set_summary(summary)
                if self.cards[self.pid].type != "notepad":
                    self.page.set_state(m.state_text(summary, self.in_links()))
            elif self.owner not in self.cards:
                item = next(b for b in self.block_items() if b.bid == self.owner)
                item.caption = self.note_caption(self.pid)
                item.update()

    def confirm_urgent_delete(self, items, cards):
        yes, no = QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No
        ans = QMessageBox.warning(
            self, "Delete", f"Delete {items} items from {cards} cards?", yes | no, no
        )
        return ans == yes

    def save_urgent(self):
        if not self.doc.isModified():
            return
        written, lost = self.urgent_page.save(self.confirm_urgent_delete)
        # the save added ids to new rows, which kicked the timer again
        self.timer.stop()
        self.doc.setModified(False)
        for pid, doc in written.items():
            if pid in self.cards:
                self.cards[pid].set_summary(self.summary(doc, pid))
            # a card on a board below shows on this board through the board card that holds it
            for board in self.urgent_page.boards[pid]:
                if board in self.cards:
                    self.refresh_card(self.cards[board])
        if written:
            self.urgent_soon.start()
        if lost:
            self.toast.show_message(f"Not saved: {', '.join(lost)} changed", 6000)

    def layout_dict(self):
        cards = [self.card_dict(c) for c in self.cards.values()]
        frames = [
            {
                "title": f.title,
                "x": f.x(),
                "y": f.y(),
                "w": f.rect.width(),
                "h": f.rect.height(),
            }
            for f in self.scene.items()
            if isinstance(f, FrameItem)
        ]
        blocks = [self.block_dict(b) for b in self.block_items() if b.bid]
        return {"cards": cards, "frames": frames, "blocks": blocks}

    def block_items(self):
        return [
            i
            for i in self.scene.items(Qt.SortOrder.AscendingOrder)
            if isinstance(i, Block)
        ]

    def block_dict(self, b):
        d = {"id": b.bid, "kind": b.kind, "x": b.x(), "y": b.y(), "w": b.w, "h": b.h}
        if b.kind == "image":
            return {**d, "img": b.name, "iw": b.iw, "ih": b.ih, "note": b.note}
        return {**d, "html": b.html(), "bg": b.bg}

    def add_block(self, d):
        if d["kind"] == "image":
            has = self.store.has_image(d["img"])
            img = QImage.fromData(self.store.get_image(d["img"]) if has else b"")
            # a lost or corrupt blob shows as a broken block: the file still opens,
            # and the entry survives saves until the user deletes it
            b = ImageItem(
                d["id"], d["img"], None if img.isNull() else img, d["w"], d["h"]
            )
            if b.image is None:
                b.iw, b.ih = d["iw"], d["ih"]
            b.note = d["note"]
            b.caption = self.note_caption(b.note)
        elif d["kind"] == "text":
            b = TextItem(d["id"], d["w"], d["h"], d["html"], d["bg"])
        else:
            raise ValueError(f"{self.store.path}: block {d['id']} kind {d['kind']!r}")
        b.setPos(d["x"], d["y"])
        self.scene.addItem(b)
        return b

    def select_block(self, bid):
        b = next(i for i in self.block_items() if i.bid == bid)
        self.scene.clearSelection()
        b.setSelected(True)
        self.view.ensureVisible(b)

    def save_layout(self):
        # every leaving path saves the layout first, so text being edited lands in it
        self.view.commit_text()
        put_map_at(self.store, self.board_path, self.layout_dict())
        self.show_snip()

    def queue_text_copy(self):
        # at most one write per COPY_GAP_S: a save inside the gap leaves one pending for its end
        if not self.settings["text_copy"] or self.copy_timer.isActive():
            return
        wait = (
            0
            if self.copy_last is None
            else COPY_GAP_S - (self.copy_clock() - self.copy_last)
        )
        if wait > 0:
            self.copy_timer.start(math.ceil(wait * 1000))
        else:
            self.write_text_copy()

    def write_text_copy(self):
        self.copy_timer.stop()
        s = self.settings
        if not s["text_copy"]:
            return
        fmt, ext = (
            ("markdown", ".md") if s["text_format"] == "Markdown" else ("text", ".txt")
        )
        # the copy is an optional sidecar: nothing it raises may stop the save or the close
        # that called it, and a failed write leaves copy_last alone so the next save retries
        try:
            boards = boards_of(self.store, self.store.path.stem, s)
            text = self.copier.render(self.store, boards, options(s), fmt)
            err = write_copy(self.store.path.with_suffix(ext), text)
        except Exception as e:
            traceback.print_exc()
            self.toast.show_message(f"Text copy: {type(e).__name__}: {e}", 6000)
            return
        if err:
            self.toast.show_message(f"Text copy: {err}", 6000)
            return
        self.copy_last = self.copy_clock()

    def flush_text_copy(self):
        if self.copy_timer.isActive():
            self.write_text_copy()

    def show_snip(self):
        target = m.snip_target(self.store)
        for card in self.cards.values():
            card.snip = card.pid == target
            card.update()

    @undoable
    def toggle_snips(self, pid):
        self.store.put_snip_board("inbox" if pid == m.snip_target(self.store) else pid)
        self.show_snip()

    def sync_snips(self):
        self.snip_watch.removePaths(self.snip_watch.directories())
        self.snip_timer.stop()
        self.snip_tries.clear()
        if not self.snips:
            return
        folder = Path(rdb_paths.resolve(self.settings["snip_folder"]))
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self.toast.show_message(f"Snip folder: {e}")
            return
        self.snip_watch.addPath(str(folder))
        self.scan_snips()

    def snip_settings(self):
        return self.dialog_before or self.settings

    def scan_snips(self):
        s = self.snip_settings()
        folder, fmt = Path(rdb_paths.resolve(s["snip_folder"])), s["snip_format"]
        if not (self.snips and folder.is_dir()):
            return
        if self.claude_pending is not None:
            self.snip_timer.start(SNIP_RETRY_MS)
            return
        if not can_write(fmt):
            self.toast.show_message(f"Snip format {fmt} is not available")
            return
        retry = False
        for f in sorted(folder.glob("*.png")):
            img = QImage(str(f))
            if not img.isNull() and self.import_snip(f, img):
                self.snip_tries.pop(f.name, None)
                continue
            # still being written or held by its writer, or no image at all: polled until it
            # completes, however late
            retry = True
            n = self.snip_tries[f.name] = self.snip_tries.get(f.name, 0) + 1
            if n == SNIP_TRIES:
                self.toast.show_message(f"{f.name}: not imported")
        if retry and not self.snip_timer.isActive():
            self.snip_timer.start(SNIP_RETRY_MS)

    @undoable
    def import_snip(self, path, img):
        # False: its writer still holds the file; True: imported, or another window took it
        s = self.snip_settings()
        data, img = snip_encode(img, s)
        pid = m.snip_target(self.store)
        shown = self.board_path_of(pid) == self.board_path
        # the PNG is claimed before anything is stored: a failure later loses no picture. Its
        # sidecar (the note typed at the snip, complete before the PNG appeared) is read first,
        # so another window that took the files first is the same FileNotFoundError, and moves
        # last, so a failure there loses neither the picture nor the note
        side = path.with_suffix(".txt")
        has_side = side.exists()
        try:
            text = side.read_text("utf-8-sig").strip() if has_side else ""
            dest = claim_snip(path)
        except FileNotFoundError:
            return True
        except PermissionError:
            return False
        note = m.create_note(self.store, text=text) if text else ""
        if shown:
            self.save_layout()
        target = m.load_board(self.store, pid)
        bid = m.make_id("image", self.taken_ids())
        name = f"img/blk_{bid}.{s['snip_format']}"
        self.store.put_image(name, data)
        x, y = snip_spot(target)
        w, h = image_cells(img.width(), img.height())
        if caption_of(text):
            h = caption_cells(img.width(), img.height(), w, h)
        target["blocks"].append(
            {
                "id": bid,
                "kind": "image",
                "x": x,
                "y": y,
                "w": w,
                "h": h,
                "img": name,
                "iw": img.width(),
                "ih": img.height(),
                "note": note,
            }
        )
        m.save_board(self.store, pid, target)
        if shown:
            self.reload_board()
        if has_side:
            side.rename(dest.with_suffix(".txt"))
        return True

    def snap(self, rows=(), contacts=()):
        return snapshot(
            self.store,
            self.card_clipboard,
            self.copy_buffer,
            self.board_path,
            rows,
            contacts,
        )

    @contextmanager
    def recorded(self, rows=(), contacts=(), nudge=None):
        # the store still holds the state before the edit when the scope opens (the edits
        # that ride on a scene change only save at the end), so the scope is one command
        if self.rec is not None:
            yield
            return
        self.rec = before = self.snap(rows, contacts)
        try:
            yield
        finally:
            self.rec = None
            self.urgent_soon.start()
            cmd = MapEdit(self, before, self.snap(rows, contacts), nudge)
            if not cmd.empty():
                self.undo_stack.push(cmd)

    def keep_blobs(self, names):
        for n in names:
            if self.store.has_image(n):
                self.rec["blobs"].setdefault(n, self.store.get_image(n))

    def end_run(self):
        self.run += 1

    def layout_changed(self):
        nudge = (self.run, time.monotonic()) if self.view.nudging else None
        with self.recorded(nudge=nudge):
            self.save_layout()

    @undoable
    def card_styled(self, _cards):
        self.save_layout()

    def board_path_of(self, pid):
        if not pid:
            return []
        for path, _, _ in self.maps():
            if path and path[-1] == pid:
                return list(path)
        raise KeyError(f"{self.store.path}: no board {pid!r}")

    def reload_board(self):
        mp, faces, titles = board_data(self.store, self.board_path, self.settings)
        self.fill_scene(self.board_path, titles, mp, faces)

    def apply_edit(self, cmd, undo):
        self.save_current()
        if self.zoom_timer.isActive():
            self.zoom_timer.stop()
            self.save_layout()
        path = self.board_path_of(cmd.board)
        if path != self.board_path or (self.pid and cmd.maps):
            self.load_board(path)
            self.push("")
        cmd.write(self.store, undo)
        if cmd.clip:
            self.card_clipboard = copy.deepcopy(cmd.clip[0 if undo else 1])
        if cmd.copy:
            self.copy_buffer = cmd.copy[0 if undo else 1]
        self.reload_board()
        if self.pid in cmd.rows:
            self.open_project(self.pid, record=False)
        if self.stack.currentWidget() is self.triage:
            self.reload_triage(self.triage.acted)
        self.prune_history()
        self.urgent_soon.start()

    def amend(self, pid, fields):
        for i in range(self.undo_stack.count()):
            self.undo_stack.command(i).amend(pid, fields)

    def taken_ids(self):
        clip = self.card_clipboard or {"cards": [], "blocks": []}
        # a trashed project comes back on undo, so its id stays taken
        return (
            m.card_ids(self.store)
            | m.block_ids(self.store)
            | set(self.store.project_ids())
            | set(self.store.trash_ids())
            | set(self.store.board_ids())
            | set(self.cards)
            | {b.bid for b in self.block_items()}
            | {d["id"] for d in clip["cards"] + clip["blocks"]}
        )

    def insert_card(self, pos):
        dlg = InsertDialog(self, self.settings)
        if dlg.exec() and dlg.choice:
            if dlg.choice == "text":
                self.view.new_text_at(pos)
            else:
                self.view.create_card_at(dlg.choice, pos)

    @undoable
    def text_added(self, item):
        item.bid = m.make_id("text", self.taken_ids())
        self.save_layout()

    @undoable
    def paste_image(self, img, pos):
        bid = m.make_id("image", self.taken_ids())
        name = f"img/blk_{bid}.png"
        self.store.put_image(name, png_bytes(img))
        b = ImageItem(bid, name, img, *image_cells(img.width(), img.height()))
        b.setPos(pos)
        self.scene.clearSelection()
        self.scene.addItem(b)
        b.setSelected(True)
        self.view.ensureVisible(b)
        self.save_layout()

    @undoable
    def images_dropped(self, paths, at):
        # a file that does not decode is skipped; the rest of a multi-file drop still lands
        for i, path in enumerate(paths):
            img = QImage(path)
            if not img.isNull():
                step = i * rdb_map.CELL
                self.paste_image(img, at + QPointF(step, step))

    @undoable
    def make_image_note(self, item):
        item.note = m.create_note(self.store)
        self.save_layout()

    def open_image_note(self, item):
        if not item.note:
            self.make_image_note(item)
        self.open_project(item.note)

    def system_clip_changed(self):
        self.cut_newer = False

    def paste_request(self, pos):
        img = QApplication.clipboard().image()
        newer = self.cut_newer or img.isNull()
        if self.card_clipboard is not None and newer:
            self.paste_cards(pos)
        elif self.copy_buffer is not None and newer:
            self.paste_copy(pos)
        elif not img.isNull():
            self.paste_image(img, self.view.place())

    def fetch(self, start, url, done):
        key = object()
        self.fetches[key] = None

        def finished(result):
            self.fetches.pop(key, None)
            done(result)

        handle = start(url, finished)
        # a callback that already ran leaves nothing to keep alive
        if key in self.fetches:
            self.fetches[key] = handle

    @undoable
    def card_created(self, card, text):
        page = card.type == "page"
        name = QUrl(text).host().removeprefix("www.") if page else text
        card.pid = m.make_id(name or text, self.taken_ids())
        self.cards[card.pid] = card
        if page:
            card.url = text
        if card.type == "board":
            m.save_board(self.store, card.pid, m.new_board())
        elif card.type == "contacts":
            self.store.put_contacts(card.pid, [])
        self.save_layout()
        if page:
            store, pid = self.store, card.pid
            self.fetch(fetch_page, text, lambda r: self.page_fetched(store, pid, r))
        elif card.type != "board":
            self.open_project(card.pid)

    def page_fetched(self, store, pid, r):
        if self.claude_defers(self.page_fetched, store, pid, r):
            return
        if store is not self.store:
            return
        path = self.locate(pid)
        if path is None:
            return
        fields = {"title": r["title"], "tags": list(r["tags"]), "snap": ""}
        self.amend(pid, fields)
        if path == self.board_path:
            card = self.cards[pid]
            card.set_title(r["title"])
            card.tags = list(r["tags"])
        # only this card is written: the layout save would end a text block being typed
        mp = map_at(store, path)
        {d["id"]: d for d in mp["cards"]}[pid].update(fields)
        put_map_at(store, path, mp)

    def url_dropped(self, url, pos, target):
        if target is not None and target.type == "links":
            self.add_link(target.pid, url)
            return
        card = CardItem("", url, self.settings["card_w"], self.settings["card_w"])
        card.type = "page"
        card.setPos(pos)
        self.scene.addItem(card)
        self.card_created(card, url)

    def edit_project(self, pid, edit):
        live = pid == self.pid
        doc = self.doc if live else m.open_doc(self.store, pid, self.mode_of(pid))
        edit(doc)
        # the open document is saved by the autosave timer
        if not live:
            m.save_project(self.store, pid, doc)
            if pid in self.cards:
                self.refresh_card(self.cards[pid])

    def add_link(self, pid, url):
        store = self.store
        self.edit_project(pid, lambda doc: append_link(doc, url, self.mode_of(pid)))
        self.fetch(
            fetch_title, url, lambda title: self.link_titled(store, pid, url, title)
        )

    def link_titled(self, store, pid, url, title):
        if self.claude_defers(self.link_titled, store, pid, url, title):
            return
        # the open project may not be stored yet; any other one must still be, or it was deleted
        live = pid == self.pid
        if (
            store is self.store
            and title not in (None, url)
            and (live or store.has_project(pid))
        ):
            self.edit_project(pid, lambda doc: m.relabel_links(doc, url, title))
            if live and self.page.cards_shown:
                self.fill_grid()

    def describe_links(self, urls):
        self.title_queue += [(self.store, self.pid, u) for u in urls]
        self.pump_titles()

    def pump_titles(self):
        while self.titles_active < TITLE_FETCHES and self.title_queue:
            store, pid, url = self.title_queue.pop(0)
            self.titles_active += 1
            self.fetch(
                fetch_title,
                url,
                lambda title, s=store, p=pid, u=url: self.title_done(s, p, u, title),
            )

    def title_done(self, store, pid, url, title):
        self.titles_active -= 1
        self.link_titled(store, pid, url, title)
        self.pump_titles()

    @undoable
    def view_changed(self, view):
        self.cards[self.pid].view = view
        self.save_layout()
        if view == "cards":
            self.close_finds()
            self.fill_grid()
        else:
            self.resume_finds()

    @undoable
    def kanban_changed(self, on):
        self.cards[self.pid].kanban = on
        self.save_layout()
        self.page.set_kanban(on)
        self.close_finds()
        self.resume_finds()

    @undoable
    def kanban_done_changed(self, on):
        self.cards[self.pid].kanban_done = on
        self.save_layout()
        self.page.set_kanban_done(on)

    @undoable
    def kanban_widths_changed(self, widths):
        self.cards[self.pid].kanban_w = dict(widths)
        self.save_layout()
        self.page.set_kanban_widths(widths)

    @undoable
    def kanban_status_only_changed(self, on):
        self.cards[self.pid].kanban_status_only = on
        self.save_layout()
        self.page.set_kanban_status_only(on)

    @undoable
    def kanban_order_changed(self, order):
        self.cards[self.pid].kanban_order = list(order)
        self.save_layout()
        self.page.set_kanban_order(order)

    @undoable
    def card_view(self, card, view):
        card.view = view
        self.save_layout()

    @undoable
    def lang_changed(self, lang):
        self.cards[self.pid].lang = lang
        self.editor.set_lang(lang)
        self.save_layout()

    @undoable
    def done_changed(self, done):
        self.cards[self.pid].done = done
        self.save_layout()

    def sort_changed(self, sort):
        # the list's stored copy is part of the step: undoing the field alone leaves the stored
        # list sorted (and its home numbers) under a card that says unsorted
        rows = (self.pid,) if self.store.has_project(self.pid) else ()
        with self.recorded(rows=rows):
            self.cards[self.pid].sort = sort
            self.save_current()
            self.save_layout()

    def fill_grid(self):
        entries = link_entries(self.doc)
        have = self.store.image_names()
        # snapshots stored by earlier versions still show
        snaps = {
            u: self.store.get_image(snap_name(u))
            for u, _ in entries
            if snap_name(u) in have
        }
        self.page.grid.fill(entries, snaps)

    def edit_links(self, edit):
        edit(self.doc)
        self.save_current()
        self.fill_grid()

    def maps(self):
        def walk(path, titles, mp):
            yield path, titles, mp
            for c in mp["cards"]:
                if c["type"] == "board":
                    yield from walk(
                        (*path, c["id"]),
                        (*titles, c["title"]),
                        m.load_board(self.store, c["id"]),
                    )

        yield from walk((), (), m.load_map(self.store))

    def locate(self, pid, rows="cards"):
        for path, _, mp in self.maps():
            if any(c["id"] == pid for c in mp[rows]):
                return list(path)
        return None

    def prune_history(self):
        ids = {
            p: {c["id"] for c in mp["cards"]} | {b["id"] for b in mp["blocks"]}
            for p, _, mp in self.maps()
        }
        kept, pos = [], 0
        for i, k in enumerate(self.history):
            path, pid = k
            if path not in ids or (
                pid and pid not in ids[path] and self.note_owner(pid, ids[path]) is None
            ):
                continue
            if kept and kept[-1] == k:
                continue
            kept.append(k)
            if i <= self.hpos:
                pos = len(kept) - 1
        self.history, self.hpos = kept or [(tuple(self.board_path), "")], pos
        self.bar.strip.sync()

    def restore_clipboard(self):
        clip, self.card_clipboard = self.card_clipboard, None
        if clip is None:
            return
        if clip["path"] == self.board_path:
            for d in clip["cards"]:
                self.refresh_card(self.add_card(d))
            for d in clip["blocks"]:
                self.add_block(d)
            return
        mp = map_at(self.store, clip["path"])
        mp["cards"] += clip["cards"]
        mp["blocks"] += clip["blocks"]
        put_map_at(self.store, clip["path"], mp)

    @undoable
    def cut_cards(self, items):
        cards = [c for c in items if isinstance(c, CardItem) and c.pid != "inbox"]
        blocks = [b for b in items if isinstance(b, Block)]
        if not cards and not blocks:
            return
        self.restore_clipboard()
        self.copy_buffer = None
        held = [self.card_dict(c) for c in cards]
        held_blocks = [self.block_dict(b) for b in blocks]
        for c in cards:
            self.remove_card(c)
        for b in blocks:
            self.scene.removeItem(b)
        self.card_clipboard = {
            "path": list(self.board_path),
            "cards": held,
            "blocks": held_blocks,
        }
        self.cut_newer = True
        self.save_layout()
        self.prune_history()

    @undoable
    def paste_cards(self, pos):
        clip, self.card_clipboard = self.card_clipboard, None
        if clip is None:
            return
        held = clip["cards"] + clip["blocks"]
        dx = pos.x() - min(d["x"] for d in held)
        dy = pos.y() - min(d["y"] for d in held)
        self.scene.clearSelection()
        for d in clip["cards"]:
            card = self.add_card({**d, "x": d["x"] + dx, "y": d["y"] + dy})
            self.refresh_card(card)
            card.setSelected(True)
        for d in clip["blocks"]:
            self.add_block({**d, "x": d["x"] + dx, "y": d["y"] + dy}).setSelected(True)
        self.save_layout()

    def card_titles(self):
        clip = self.card_clipboard or {"cards": []}
        return {c["title"] for mp in m.all_maps(self.store) for c in mp["cards"]} | {
            c["title"] for c in clip["cards"]
        }

    @staticmethod
    def copyable(items):
        return (
            [c for c in items if isinstance(c, CardItem) and c.pid != "inbox"],
            [b for b in items if isinstance(b, Block)],
        )

    def bundle_of(self, items):
        cards, blocks = self.copyable(items)
        return m.copy_bundle(
            self.store,
            [self.card_dict(c) for c in cards],
            [self.block_dict(b) for b in blocks],
        )

    @undoable
    def copy_cards(self, items):
        if not any(self.copyable(items)):
            return
        # a cut still pending goes back to its origin first, as at close; the copy then
        # sees it (a board's own map regains the cards cut from inside it)
        if self.card_clipboard is not None:
            self.restore_clipboard()
            self.save_layout()
        self.copy_buffer = self.bundle_of(items)
        self.cut_newer = True

    def add_copies(self, bundle, place, board=None):
        # place(held) is the shift of the new entries, which start at their source positions;
        # a board takes them in its first blank area instead
        cards, blocks = m.paste_bundle(
            self.store, bundle, self.taken_ids(), self.card_titles()
        )
        held = cards + blocks
        target = None if board is None else m.load_board(self.store, board.pid)
        dx, dy = place(held) if board is None else drop_offset(target, held)
        for d in held:
            d["x"] += dx
            d["y"] += dy
        if board is not None:
            target["cards"] += cards
            target["blocks"] += blocks
            m.save_board(self.store, board.pid, target)
            self.refresh_card(board)
            return
        self.scene.clearSelection()
        for d in cards:
            card = self.add_card(d)
            self.refresh_card(card)
            card.setSelected(True)
        for d in blocks:
            self.add_block(d).setSelected(True)
        self.save_layout()

    @undoable
    def paste_copy(self, pos):
        self.add_copies(
            self.copy_buffer,
            lambda held: (
                pos.x() - min(d["x"] for d in held),
                pos.y() - min(d["y"] for d in held),
            ),
        )

    @undoable
    def duplicate_cards(self, items):
        bundle = self.bundle_of(items)
        if not (bundle["cards"] or bundle["blocks"]):
            return
        own = {e["card"]["id"] for e in bundle["cards"]}
        own |= {e["block"]["id"] for e in bundle["blocks"]}
        self.add_copies(
            bundle, lambda held: duplicate_shift(self.layout_dict(), held, own)
        )

    @undoable
    def copy_dropped(self, items, delta, board):
        bundle = self.bundle_of(items)
        if bundle["cards"] or bundle["blocks"]:
            self.add_copies(bundle, lambda held: (delta.x(), delta.y()), board)

    @undoable
    def move_into(self, items, board):
        cards = [c for c in items if isinstance(c, CardItem) and c.pid != "inbox"]
        blocks = [b for b in items if isinstance(b, Block)]
        if not cards and not blocks:
            return
        held = [self.card_dict(c) for c in cards]
        held_blocks = [self.block_dict(b) for b in blocks]
        for c in cards:
            self.remove_card(c)
        for b in blocks:
            self.scene.removeItem(b)
        target = m.load_board(self.store, board.pid)
        dx, dy = drop_offset(target, held + held_blocks)
        target["cards"] += [{**d, "x": d["x"] + dx, "y": d["y"] + dy} for d in held]
        target["blocks"] += [
            {**d, "x": d["x"] + dx, "y": d["y"] + dy} for d in held_blocks
        ]
        m.save_board(self.store, board.pid, target)
        self.save_layout()
        self.refresh_card(board)
        self.prune_history()

    @undoable
    def item_renamed(self, item, title):
        self.save_layout()
        if isinstance(item, CardItem) and item.pid == self.pid:
            self.setWindowTitle(self.title(title))
            self.page.set_title(title)
            self.update_crumbs()

    def confirm_delete(self):
        sel = [
            i for i in self.scene.selectedItems() if getattr(i, "pid", "") != "inbox"
        ]
        if not sel:
            return
        # text and image blocks go without asking (undo brings them back)
        if not self.settings["confirm_delete"] or all(
            isinstance(i, Block) for i in sel
        ):
            self.delete_selected()
            return
        yes, no = QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No
        ans = QMessageBox.warning(
            self, "Delete", "Delete selected blocks?", yes | no, no
        )
        if ans == yes:
            self.delete_selected()

    @undoable
    def delete_selected(self):
        blobs = []
        for item in self.scene.selectedItems():
            if isinstance(item, CardItem):
                if item.pid == "inbox":
                    continue
                if item.type == "board":
                    self.trash_board(item.pid)
                elif item.type == "contacts":
                    if self.store.has_contacts(item.pid):
                        self.store.delete_contacts(item.pid)
                elif self.store.has_project(item.pid):
                    m.trash_project(self.store, item.pid)
                self.remove_card(item)
            else:
                if isinstance(item, ImageItem):
                    blobs.append(item.name)
                    if self.store.has_project(item.note):
                        m.trash_project(self.store, item.note)
                self.scene.removeItem(item)
        self.save_layout()
        # blobs go only after the map no longer names them
        self.keep_blobs(blobs)
        for name in blobs:
            self.store.delete_image(name)
        self.prune_history()

    def trash_board(self, pid):
        clip = self.card_clipboard
        # cards cut from inside the board are part of it, so they go home first
        if clip and set(clip["path"]) & m.board_contents(self.store, pid)[1]:
            self.restore_clipboard()
        for board in m.board_contents(self.store, pid)[1]:
            self.keep_blobs(
                b["img"]
                for b in m.load_board(self.store, board)["blocks"]
                if b["kind"] == "image"
            )
        m.delete_board(self.store, pid)

    def live_doc(self):
        return (self.pid, self.doc) if self.pid and not self.on_urgent else None

    def finder_entries(self, today_only=False):
        self.save_current()
        self.save_layout()
        entries = build_index(
            self.store, self.settings, self.finder_cache, self.maps(), self.live_doc()
        )
        return today_entries(entries, m.today()) if today_only else entries

    def project_tags(self):
        stamps = self.store.project_stamps()
        if self.tag_cache is None or self.tag_cache[0] != stamps:
            entries = build_index(
                self.store, self.settings, self.finder_cache, self.maps()
            )
            self.tag_cache = (stamps, entry_tags(entries))
        found = {t.lower(): t for t in self.tag_cache[1]}
        live = self.live_doc()
        if live is not None:
            for b in m.iter_blocks(live[1]):
                for a, e in m.tag_runs(b):
                    t = m.run_text(b, a, e)[1:]
                    found.setdefault(t.lower(), t)
        return sorted(found.values(), key=str.lower)

    def find(self, today_only=False, query=""):
        dlg = Finder(
            self,
            self.finder_entries(today_only),
            "Today" if today_only else "Go",
            self.store,
            self.settings,
            app_settings(),
            query,
        )
        picked = dlg.choice if dlg.exec() else None
        listed = dlg.listed
        # the dialog is a child of the window and holds the preview clones
        dlg.deleteLater()
        if listed:
            self.list_from_results(*listed)
        elif picked:
            self.goto(picked)

    def goto(self, t):
        path = list(t.board_path)
        if t.kind == "board":
            if (
                self.pid
                or path != self.board_path
                or self.stack.currentWidget() is not self.map_page
            ):
                self.load_board(path)
                self.push("")
            return
        if path != self.board_path:
            self.load_board(path)
        elif t.kind == "text" and self.pid:
            self.go_map()
        if t.kind == "text":
            self.select_block(t.pid)
            return
        if t.kind == "note":
            self.open_project(t.pid)
            return
        if t.kind == "contact":
            self.open_project(t.pid, t.block)
            return
        self.enter(t.pid, t.block)
        if t.block is not None:
            b = self.doc.findBlockByNumber(t.block)
            # a done item can sit in the collapsed Completed section or be hidden by the Hide mode
            if not b.isVisible():
                m.show_item(self.doc, b)
                m.apply_view(self.doc, self.mode_of(self.pid))
                self.editor.setTextCursor(QTextCursor(b))
                self.editor.ensureCursorVisible()
            flash(self.editor, t.block)

    def pick_urgent(self, e):
        # the bubble was made before any unsaved edit; saving renumbers blocks, so the item is
        # found again by its text in what is stored
        self.save_current()
        key = item_key(e)
        before = [ln.entry for ln in self.urgent.links]
        n = sum(item_key(x) == key for x in before[: before.index(e)])
        same = [x for x in self.urgent_entries(None) if item_key(x) == key]
        self.refresh_urgent()
        if same:
            self.goto(same[min(n, len(same) - 1)].target)

    def scheme_changed(self, _scheme):
        self.system_mode_changed()

    def system_mode_changed(self):
        # the Windows app mode flipped; only the System mode cares
        if (
            self.settings["mode"] == "System"
            and resolve_mode("System") != rdb_theme.dark()
        ):
            self.apply_settings()

    def apply_settings(self):
        s = self.settings
        dark = resolve_mode(s["mode"])
        flipped = dark != rdb_theme.dark()
        if flipped:
            # an open map text edit holds colours drawn for the old mode
            self.view.commit_text()
            self.save_current()
        rdb_theme.set_dark(dark)
        m.configure(s)
        rdb_tip.configure(s)
        rdb_map.configure(s)
        rdb_timeline.configure(s)
        rdb_theme.apply(s["theme"], dark)
        QApplication.instance().setFont(m.font_of(s, "ui"))
        # a widget under a style sheet keeps the application font it was polished with
        self.urgent.setStyleSheet(self.urgent.styleSheet())
        self.sync_urgent_hide()
        self.page_zoom("" if self.on_urgent else self.owner or self.pid)
        self.claudeout.style(s)
        self.bar.set_theme(s["theme"])
        self.bar.sync()
        self.bar.set_tiles_side(s["tiles_side"])
        self.timer.setInterval(s["autosave_ms"])
        self.queue_text_copy()
        self.editorbar.configure(s)
        self.kanbanbar.configure(s)
        self.mapbar.configure(s)
        bars = (
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
            if s["scrollbars"]
            else Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        for view in (self.editor, self.page.grid, self.contacts.table):
            view.setVerticalScrollBarPolicy(bars)
            view.setHorizontalScrollBarPolicy(bars)
        if self.pid and not self.in_contacts():
            self.doc.setDefaultFont(
                m.note_font(m.EDITOR_FONT) if m.is_text_doc(self.doc) else m.EDITOR_FONT
            )
            m.normalize(self.doc, self.mode_of(self.pid))
        for b in self.block_items():
            if isinstance(b, TextItem):
                b.restyle()
        for card in self.cards.values():
            self.refresh_card(card)
        self.page.refresh()
        self.page.board.restyle()
        self.contacts.refresh()
        self.page.grid.viewport().update()
        self.scene.update()
        self.view.viewport().update()
        if flipped:
            self.reopen_page()
        self.urgent_soon.start()
        if self.dump_key is not None:
            self.dump_key.set_key(s["dump_key"])

    def statuses_edited(self, items):
        m.check_setting("statuses", items)
        self.settings["statuses"] = items
        m.save_settings(self.store, self.settings)
        self.apply_settings()

    def reopen_page(self):
        # the document on show was made for the other mode
        if self.on_urgent:
            self.open_urgent()
        elif self.pid and not self.in_contacts():
            cur = self.editor.textCursor()
            at = (cur.blockNumber(), cur.positionInBlock())
            scroll = self.editor.verticalScrollBar().value()
            self.open_project(self.pid, record=False)
            b = self.doc.findBlockByNumber(min(at[0], self.doc.blockCount() - 1))
            c = QTextCursor(b)
            c.setPosition(b.position() + min(at[1], max(b.length() - 1, 0)))
            self.editor.setTextCursor(c)
            self.editor.verticalScrollBar().setValue(scroll)

    def refresh_urgent(self):
        # ponytail: the whole finder index per refresh, ~7 ms warm at 2.4k items but ~0.3 s at
        # 30k; a walk that keeps only urgent items if files grow that large
        items = self.urgent_entries(self.live_doc())
        self.urgent.show_items(
            items, m.shown(self.settings, "urgent_color"), self.settings["urgent_count"]
        )

    def urgent_entries(self, live):
        entries = build_index(
            self.store, self.settings, self.finder_cache, self.maps(), live
        )
        items = due(
            entries,
            self.clock(),
            self.settings["urgent_eod"],
            self.settings["urgent_at_once"],
        )
        order = app_settings().value(self.urgent_order_key(), [], list)
        return arrange(items, order)

    def urgent_order_key(self):
        # per file; a hash because a path's slashes would nest ini groups
        path = str(self.store.path.resolve()).lower()
        return "urgent_order/" + hashlib.md5(path.encode()).hexdigest()

    def save_urgent_order(self, keys):
        app_settings().setValue(self.urgent_order_key(), keys)

    def fields_toggled(self, on):
        self.settings["tl_fields"] = on
        m.save_settings(self.store, self.settings)
        self.page.tl.sync()

    def wrap_toggled(self, on):
        self.settings["notepad_wrap"] = on
        m.save_settings(self.store, self.settings)
        self.editor.apply_wrap()

    def edit_settings(self):
        before = dict(self.settings)
        tiles = TitleTab(self.bar.strip)
        dlg = SettingsDialog(
            self,
            self.settings,
            app_settings(),
            [("Title Bar", tiles), ("Tags", self.tag_manager())],
        )

        def preview():
            self.settings.update(dlg.values())
            self.apply_settings()

        dlg.changed.connect(preview)
        self.save_current()
        self.dialog_before = before
        try:
            accepted = dlg.exec()
        finally:
            self.dialog_before = None
        if accepted:
            self.settings.update(dlg.values())
            m.save_settings(self.store, self.settings)
        else:
            self.settings.update(before)
            tiles.revert()
        tiles.release()
        dlg.deleteLater()
        self.apply_settings()
        self.sync_snips()

    def export_word(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export", str(self.store.path.with_suffix(".docx")), "Word (*.docx)"
        )
        if not path:
            return
        self.save_current()
        self.save_layout()
        projects = [
            (c["id"], c["title"], self.mode_of(c))
            for _, _, mp in self.maps()
            for c in sorted(mp["cards"], key=lambda c: (c["y"], c["x"]))
            if c["type"] in (*m.DOC_TYPES, "contacts")
        ]
        n = export_docx(self.store, projects, path)
        self.toast.show_message(f"{n} projects exported", 4000)

    def export_text(self, fmt):
        ext, kind = (".md", "Markdown") if fmt == "markdown" else (".txt", "Text")
        path, _ = QFileDialog.getSaveFileName(
            self, "Export", str(self.store.path.with_suffix(ext)), f"{kind} (*{ext})"
        )
        if not path:
            return
        self.save_current()
        self.save_layout()
        s = self.settings
        boards = boards_of(self.store, self.store.path.stem, s)
        text = self.copier.render(self.store, boards, options(s), fmt)
        err = write_copy(Path(path), text)
        if err:
            QMessageBox.warning(self, "Export", f"{path}: {err}")
            return
        self.toast.show_message(f"Exported {Path(path).name}", 4000)

    def check_updates(self):
        if self.update_job is not None and self.update_job.isRunning():
            return
        job = self.update_job = rdb_update.Job(
            lambda progress, cancelled: rdb_update.fetch_latest(), self
        )
        job.done.connect(self.update_found)
        job.failed.connect(
            lambda why: QMessageBox.warning(self, "rdb", f"Update check failed: {why}")
        )
        job.start()

    def update_found(self, release):
        ver, url, size, sha = release
        installed = rdb_version.VERSION
        if ver <= rdb_update.parse_version(installed):
            QMessageBox.information(self, "rdb", f"rdb {installed} is up to date")
            return
        latest = ".".join(map(str, ver))
        text = f"rdb {latest} available (installed {installed})"
        if not rdb_paths.FROZEN:
            QMessageBox.information(self, "rdb", text)
            return
        box = QMessageBox(QMessageBox.Icon.Question, "rdb", text, parent=self)
        update = box.addButton("Update", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is not update:
            return
        exe = rdb_paths.exe_path()
        dest = exe + ".download"
        job = self.update_job = rdb_update.Job(
            lambda progress, cancelled: rdb_update.download(
                url, dest, size, sha, progress, cancelled
            ),
            self,
        )
        dlg = QProgressDialog("", "Cancel", 0, size, self)
        dlg.setWindowTitle(f"Downloading rdb {latest}")
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)

        def progress(done, total):
            dlg.setValue(done)
            dlg.setLabelText(f"{done // 1024} of {total // 1024} KB")

        def stop():
            job.stop = True

        def failed(why):
            dlg.close()
            QMessageBox.warning(self, "rdb", f"Update failed: {why}")

        def downloaded(_):
            # read before close(): closing a QProgressDialog emits canceled, which sets stop
            cancelled = job.stop
            dlg.close()
            try:
                if not cancelled:
                    rdb_update.swap(exe, dest)
            except OSError as e:
                failed(str(e))
                cancelled = True
            if cancelled:
                if os.path.exists(dest):
                    os.remove(dest)
                return
            self.restart_new_exe()

        dlg.canceled.connect(stop)
        job.progress.connect(progress)
        job.failed.connect(failed)
        job.done.connect(downloaded)
        job.start()

    def restart_new_exe(self):
        path = str(self.store.path)
        if not self.close():
            return
        # the new process must find no server to hand its file to
        self.instance_server.close()
        proc = QProcess()
        proc.setProgram(rdb_paths.exe_path())
        proc.setArguments([path])
        # a onefile child that inherits this process's extraction state would reuse the folder about to be deleted
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYINSTALLER_RESET_ENVIRONMENT", "1")
        proc.setProcessEnvironment(env)
        if not proc.startDetached():
            QMessageBox.warning(None, "rdb", f"Update failed: {proc.errorString()}")
        QApplication.quit()

    def closeEvent(self, e):
        if self.update_job is not None:
            self.update_job.stop = True
            # unbounded: destroying a running QThread aborts; a stalled read ends at rdb_update.TIMEOUT
            self.update_job.wait()
        self.claude_abort()
        zoom_hub().changed.disconnect(self.save_zoom)
        if self.dump_key is not None:
            self.dump_key.release()
        if self.dump_box is not None:
            self.dump_box.close()
        # whatever a step raises, the file is released
        try:
            self.save_current()
            self.restore_clipboard()
            self.save_layout()
            self.flush_text_copy()
            self.save_view()
            self.save_window()
            self.pack_pass.stop()
            self.editor.close_image_windows()
            self.fetches.clear()
            self.urgent_soon.stop()
            self.urgent_timer.stop()
        finally:
            self.store.on_write = None
            self.store.close()
            self.lock.unlock()
        super().closeEvent(e)


def raise_running(name):
    s = QLocalSocket()
    s.connectToServer(name)
    if not s.waitForConnected(300):
        return False
    # Windows only lets the running instance take the foreground if this launch allows it
    ctypes.windll.user32.AllowSetForegroundWindow(ASFW_ANY)
    s.write(b"raise")
    s.waitForBytesWritten(300)
    s.disconnectFromServer()
    return True


def serve_raise(window, name):
    server = QLocalServer(window)
    if not server.listen(name):
        raise RuntimeError(f"single instance: {server.errorString()}")
    server.newConnection.connect(lambda: bring_front(window, server))
    return server


def bring_front(window, server):
    while (c := server.nextPendingConnection()) is not None:
        c.deleteLater()
    window.setWindowState(window.windowState() & ~Qt.WindowState.WindowMinimized)
    window.show()
    window.raise_()
    window.activateWindow()


def open_window(path):
    # a file that cannot open leads to a picker, not an exit; only cancelling quits
    while True:
        try:
            return MainWindow(path, snips=True)
        except LOAD_ERRORS as e:
            QMessageBox.warning(None, "rdb", f"{path}\n{e}")
        path, _ = QFileDialog.getSaveFileName(
            None,
            "Open or create rdb file",
            rdb_paths.app_dir(),
            "rdb (*.rdb)",
            options=QFileDialog.Option.DontConfirmOverwrite,
        )
        if not path:
            sys.exit(1)
        path = Path(path).with_suffix(".rdb")


def main():
    QGuiApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    app = QApplication(sys.argv)
    # <ICON_INJECT_START>
    app.processEvents()
    try:
        import base64
        import ctypes

        from PySide6.QtGui import QIcon, QPixmap

        icon_pngs = (
            (
                "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAACXBIWXMAAA7EAAAOxAGVKw4b"
                "AAAC2klEQVQ4jX2SS2yUZRiFn+/y/9O5tDMTOnQsA9oLaiQVo0QXLhrDZeOiaQgmboEujHGh"
                "RINhYdSFiQkhNLrxbsQFiRhYiBBJSgzRiKGERogtMkPj2Drt9DbMrf/l+1gg2lLiWZ68ec6b"
                "nCP4R4W3aLGLPC2k6pHKfUQp7QamMWWD4HokpNg5zCXuIwFQ/mDXM83Za9+oaDLlZHpjOp2T"
                "qi2LX5rAn7u5bL3qvGlWRtXsjX0dRymtAuQP8KATbf9jw0tntNDu/UIAmD35On5x7NyGd6d3"
                "rvS1MDzktGZq058MxoUblU6mV+jUJqHbsngzEwTlvLFeLTQ2MMard9wL1gDGCpvde1zXf/se"
                "b2Ycr3g5bJoQFU/LSO4Jqdd1SZXIUD7xmlkDEIY+gqaSboLEk3vu+urew+rYKUKv2rX2A8VV"
                "f+Gv2K3R44SVEjqdQ6dzqPg6/HKBa9NVSg1BM/8TDXdHmTeP9BM6kvd7RwBE4VX6Ix2PnYw9"
                "vD2FFAQLRYKFIiIS45gZ4LTYji8iCCEJfS+si9itzjbV/LW47Jeqja1aWboAks8OrXrt93nJ"
                "mQtRzu3vXmmrmmdSH11cZM/jSd44OzMsrWDOK40nl6eu0iz8TLDwJ1hLqW7xgrV1vnyqxIHT"
                "Mwgg9G2fNlBpyfRUKr98ljTLVYLFIsFikfn1L2JiB7kx5zOSr7FvW4ovRpf4cnSJt3e0syml"
                "/6vRSm0zg4dXJeXzNeSVBCP5GkPf/s3ZiRrfjVfZuTnOoefauTBZvwOwkpuYwLdhgFD6X4B0"
                "EwDs3Zbix0KDry4vIQQce6ETJVfU2H2YyfKHG5+f+njghHTjaae9J6bTG2Ut3Eou2o8U8Onu"
                "LA+0aga3JFifuDORqJa0OHJO3CVdf4WII3hKCLqFE3sU5UT64+f3vzfQk8q2rt5VVEsO/VBu"
                "XJxsDgn+TwfH0h2t0eEwtH0r7RZXlit1+3nlnc1f3wbt/yVCyo3PGwAAAABJRU5ErkJggg=="
            ),
            (
                "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAACXBIWXMAAA7EAAAOxAGVKw4b"
                "AAAFm0lEQVRYhZ2WW2xUVRSGv71nzgwz09ILnalF7ECQctNWihGpQFSQcIkSEiFBIwQxkWio"
                "+GCIiTF90UKikYB4iS+aeIWIGKJoogQoEImh0IIQilgKvUDvzK1zZuac7cPplJZOO9P+L+ec"
                "nbX+9a+9zlp7C8aAi1U48oJM7Mkm8FAVsbH4jgSRavH6doqEYLFSLAEWAD4QBaDcg1wjoDqB"
                "duCMEJxQipqpu2kbt4CmSuYrm9iFUksHDOxOZcsqENKRhXB6kNoEzHgUpYcxYyGMUKdSCf0u"
                "jxB/CkPt8O/hbMYCFIim7ewFXhf2CaZn7irpmlaBVjgTLfcBkHJkBtMk3nuT+O0r9DWeJvzP"
                "r6ZKRCWwz7+bbQJUWgFNlSxVkj+ck0vxbfgc6fBkIj61Hj1E+/db0VvrESbL/Hv4czR72S/j"
                "XetTgRpVcIbo5xjgHRn2fvMfgCV66wWa9zyNZ+4qXNMWovlmouUVpy9Bzw3i7ckSHEElogzi"
                "zUCAxBQK3DOeJtpSS6juIKG6g1YSdic2zySkM/kTujDjfdZPqIcwwl2ohD5AKN25uKZVELl6"
                "FCUxMxKQxITpFXif302so4Fw3c/0NZ0h0XMTI3ibRKBtaHmEQAgJNgearwSXfwGesjU4vCUE"
                "z+0ncvVoutjDBSTh8JbgWPYWefesm4kIZqATObEAaXench0z7AASihRghDpGNZZ2NzK/OC1p"
                "kkdCUTpbCWCCHyDWdikteSZI8iR50wpAcgkg2niK3ppPSARaxxU4EWil98Q+oo2nkuxpMxIA"
                "jW+yVSg+1XwlxNsbQAi0/Gk4fCVohTOxZXmRTg/SkYVwuFCxPmsMR8PUdZicazOIBLowwl2g"
                "FLbsQozgbWpl2Xc/2taeGBRNRyTO8f7cOhBqmID8Fe/g8M4gfPEXojfPEu+8NqLybpHPm9pO"
                "jsvF49ms3xD2jVTP6BjWBc4p83BOmQeAEekh1nYRI9yNGe3FjNzBjIcR9ixeaVrP8Z4prC/N"
                "ZpHfjWZLebAOQdxQnGyKsL8+uAIV/wrU6pRtmITNnYdr+vAMz3fAH+c11pdm88OG+zNOG2Bb"
                "RR5KtXDgQnAlOxpKkzP2cQC9pT4jknPtltsif2az4HJ7jPK91/HvukZ7yGDR1H4/qcqtHVD0"
                "Aug3z2LqIaQza1RC3bCemWx7UDdZ+3UzVzqsC9RXtXfIdvbnrUi+cQUg0dtC896ldP1aReTy"
                "78S7roOZdpyPCKVg66FbA8FdmuDZ2UOTs3ZAoFCQs3AL+q1LaQ+jQOAJ4OW0Ar74u5dvzwcG"
                "vvetuY9ZXgfH/ovcI6AftpwiCp98AyPciX6jlmjzWfSWC5jhbuK9zaiY5ZiwFYN21++Dmm4+"
                "rOlm8/wc3lvuRQg43xal8vDtAZtN5Tlsnp8zTGTKLrB5CnDPXo579vIh68qMo/QweQ05MOiw"
                "+7Cmm1vBBNXHulDA209OYt03regJ6/Sc43Oyb01hyl0a5aYxHEJqSFcuQtiGrG8omzjwvvNY"
                "FzlVDfzbZdXdrUkOvDgZjyN1qDEJGAm7VnhZPSt153y2tpA5PueIvhJAGlwFCNf/jKmHxixA"
                "swn2vzCZCr9ryPqWR3N5ad7wug8TULyHowg+tu6ET5njaUO3Jjm8cQpzC61sy4qc7H0udd0H"
                "ww4gQPER25oq+dJM6NWhuoPPDGpDZfNMEoPbMBp8DHiVuDH0Bp3vtvHXa35ON/WxsNiFS0s9"
                "qGLGwK1ZT2nR+Bb3yRiLlWSJUiwQ4EOIApTyAFySs1jl+GlcZwHA+m9bOHAhCEo+kn6WDsLF"
                "Khy5HWTXywdDqzyHDwEr1j2czaKpbhwZjOWYoTh5PWIFRx1h5+zVYxIwBG9f9VpHqlg5dmd1"
                "BKFtonpGx/gFWESCHQ2lSFWOYuReS0KgY4padpXUJ29E/wN3uES2CS9j1QAAAABJRU5ErkJg"
                "gg=="
            ),
            (
                "iVBORw0KGgoAAAANSUhEUgAAADAAAAAwCAYAAABXAvmHAAAACXBIWXMAAA7EAAAOxAGVKw4b"
                "AAAIv0lEQVRogdWae3BU9RXHP7+7d3ezr4RsXkgS8gACFkXGxjEtIFJpq3aY0dLayoytgk07"
                "1Y7MaDvyaIURHx21PqbT6bRap+MUxDqhgrQUoTrKVNSMAvIKqIQQZMnLPHY3e+/dvb/+sY/s"
                "JhuSvUmc6XcmM2fP7/7O75z7+51zfufcwP85RC4PNzdi97u43KZQZUoqBJQD+QjcSNwSDEUS"
                "loKwkHRIQbsUtMdsnJj9JB1fugFHN+Hw9XId8B0TFglYADgtrtUONCN4w1TYXfsUZy3KyUBW"
                "A86upVZK7kWwGiiYjIWyrPxfafJc9yBN9X/CsC4mDc2N2EvcrJewEVDTx9TCShylc3GU1qH6"
                "q1CcPhSnB8XpQzg9KHYP0jSQWghTG8DUQ5hakFioC6PjNHpHC3rHKaQeHq7DIVPhztrfcXhC"
                "BsiHUFr72CbgtviIwFW7GO/CleRVX4vi8FiRnwlpYvS0ETq2m+CRJmIDncmRQUVw3cynabZs"
                "QOt9rETwKkDezGsoWrEFNf+yiSs9GswYwaO76NmzBRnTkfBxzTMsyFWMkqIEDyZJ/40bplZ5"
                "AMWGd8EtuOqWJZbnyta15LxoygApmZuke/Y8QrT/80nRc1SYMYKHdzB46s0US9qRuYpJOaoQ"
                "bAfuBoi0fcD5P9yEq3ZRwgcaJtEHziZ8YEe6D1jGkAEQyzBfSgY/PcDgpwfiD06riEehsjpU"
                "fzWK04vi8KLkeRNRyIs0dUwthNQGMLUQph4kFurG6DiFfrEFvfN0tig0OQako+TWJ+l79y/o"
                "gROQ2NVobzvR3nbCp/ZPysKK04N3wa3EtAFCR16zLCerAc7Kq7ls3rcwo2GCza8QOv4voj2t"
                "mMag5YWEsKF4/Dhn1uOrX0Ve+VUA9Ox52LJMGMWAJBTVTX7DneQ33AmAqUfQWg+gB1rQe9uI"
                "9QUwjTAYGjKqIxQbwu4AWx42jx+1sBy1sIq8mgYcRbMmpKglA4ZDceThqluOq275lChjBUNh"
                "VOBIcU3zS1NAmkPXIDWKLdf5KQMEXJukY5HeCSs2Xujnj6Row0ZRrvOVNFpLEmawa2JajRdS"
                "ZgQGm0owVxHpmTh1P+/auZ7Q0deRMS37rEmA0f0ZXa9vJNp3IcUzY+ScJNIT2cVkIouFe+ja"
                "tR5l3+N4rliBq7oBe+lcVF8ZiJyKuBRkVMPoPI0WOE7o2G60cx9ZkjMcKW3OruWPEn4K8awb"
                "7W0f8bCSVzBUD+QlMrHTi3B6UZweZMxAakHMSBBTDxLVw7zdVcjb3X46IwrIYVcdIVDy8jEH"
                "+wDYZbvppQGRP3IXJBrIFmJmE09cERjTgIp796FfPMHAh9uJnHkvI1KMFwFRxi/tj/KO8vWc"
                "514CQRD38fjcF0FIGC0PKAqu2UtxzV6KNAaJtL5HpP0j9MBx9MBJzEjfmMrf7NxBD4WTqTyA"
                "F+QLrDs5i8fYMLoBaRB2F6451+Oac32KZ+ohYgMdxAYuYuohpBFBGhFQVFBdrPnwa/R0xpVf"
                "Uz+N1fUFVBfaUay5DwOaycFzg2zc20VbrwGSdaw7tofH5r+TUyZOQnF4UIpqsBfVjBjbf06w"
                "v1NNKf/8yunWtE7DdB/MKXbQUOniymfPoEWlQIpHgSXpiawkScuo9fD51rmh17y6fmINjYNt"
                "gzzyZjcH2+K5Yk6xgyXV7sSoWERjsz21A1IwL1kPmXrI8qKdg0MGVBfaLct56aM+fvz3C8hE"
                "i2THHeXc8hUfNUMyBcVOf3oiG0jSWrulDscIWD3zHwc0GpsCGVH3z+/HA4eSfncwhEj9VCSf"
                "Jumef2+h+5+b0C4cHRm7pxgDmsn3t54nEs1ct8affTfTj1BqB5CS4OEmgoebcJTOJa+6AUdZ"
                "HfbSudiLahE2S74/JqSEn/0jQEunnsEv86psXJb9npdVE8/8mwmfeANpGomOWktqTNhU1IJy"
                "RFpnTnF6EE4vRHUi528ErCWv55t72XqoP4MnBGz94Qym+7K/tKzcwhseoHD5rwgdeY3wJ2/F"
                "fULGawQZi2L0jN6XNezzyXarP9trsO1QP3OKHXx3vm/ElerwBY1f7Lw4Yt6mG4r5xiz3CP4l"
                "DQCwuf3kN9xFfsNdmJE+tPMfxzPxxZNE+z8n1hcgFu4ZVXA6Wr8wuOrZM/Rr8ZewamE+L902"
                "I+Xk/Ylzrw0798tne9iwrPiSssd1mJW8AlyzFuOatTiDL2NRpB5GRhOZ2KbiPVgKxzPnv3p0"
                "IKU8wNZD/fhdNp5bUQZAY1OA012Z5/4yn8rffjADm8IlMSFvFDYV4coH8od46sjzM7NgZAT5"
                "/btfMN2n4nfZ2H4k89wrArbdPoNS79gV5tSEk2H43pU+VhzysutEZsG1cW/2ztzD3yxhac3o"
                "5z4dY2zQ5EAR8PLtM1hU5Rrz2RvrPDx4/fhL45QBJkSStDQmv5R02xV2/qiC+WWjf6Eqz1cz"
                "nHs8GLrMSd5J0t27fzMl3Wm/28aeuyqpzOITNgW2ryqn2JNbZyVlQNU0dgjYDkPd6Y5Xfk74"
                "1P4JXe6Go6JAZe+aSorcmYo++u2ScR2x4Rgq6jdjNjdyR4mL41Lwa6RUrXWn3Yz1XXBeiYP/"
                "/GQm97wWoK03yt3XFPDAkpxbQnG9szHPrqVWwj3AmjG1GYbN6npeVO8A4NT9tcwpdowxIzes"
                "evlzth1OhF1npDBrFKp6hs+qn+H+4DRKhclyJE8LeJ+05tdoqJVnUvTBc9a72dkQM+GD9lSs"
                "6WDzwr5L5oErNqED+xN/NDdiL/MwzzSpMhUqhKRCCnxCxr/UC4Gx2HwXh9RX6cJh37i3i4ZK"
                "16TsQsyEh/Z18kl3MmPLv4KQFkuOMfDgydUgXwBwqoIl1W5qCu2ZxUgO6I+YfNAeSVOekzhD"
                "X2VzfXhqDEAK1p3cgmQdOf4/xjjQgjRX8tv5x2DKMrGQPHb5BoS5FOQByP3rYxZ0gHwCZ+jq"
                "pPIw+W8nOxqb7RQ7/RgWG6tuI8LmhX3Jblw6/gfh51nKrEMrDAAAAABJRU5ErkJggg=="
            ),
        )
        app_icon = QIcon()
        for data in icon_pngs:
            pm = QPixmap()
            pm.loadFromData(base64.b64decode(data))
            app_icon.addPixmap(pm)
        app.setWindowIcon(app_icon)
        # own taskbar identity under plain python; a Pylauncher EXE hosts the app in its
        # own process, where an explicit ID would break grouping with an Explorer pin
        if not hasattr(sys, "_pylauncher_exe"):
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("rdb.todo")
    except Exception as e:
        print(f"Icon setup failed: {e}")
    # <ICON_INJECT_END>
    app.setStyle("Windows")
    app.setFont(QFont("Tahoma", 8))
    tabkeys.install(app)
    rdb_paths.ensure_ini()
    if rdb_paths.FROZEN:
        try:
            rdb_update.cleanup(rdb_paths.exe_path())
        except OSError as e:
            print(f"Update cleanup failed: {e}")
    last = app_settings().value("last_file", "")
    if len(sys.argv) > 1:
        path = sys.argv[1]
    elif last and Path(last).exists():
        path = last
    else:
        path = rdb_paths.default_file()
    if raise_running(INSTANCE):
        sys.exit(0)
    window = open_window(path)
    window.instance_server = serve_raise(window, INSTANCE)
    # <ICON_INJECT_START>
    try:
        if not app.windowIcon().isNull():
            window.setWindowIcon(app.windowIcon())
    except Exception:
        pass
    # <ICON_INJECT_END>
    window.show()
    window.install_dump_key()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
