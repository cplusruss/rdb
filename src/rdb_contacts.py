import csv
import io
import json
import quopri
import re
from pathlib import Path

from PySide6.QtCore import (
    QAbstractTableModel,
    QEvent,
    QModelIndex,
    QSortFilterProxyModel,
    Qt,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QFont,
    QPalette,
    QUndoCommand,
    QUndoStack,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

import rdb_model as m
import rdb_theme
from rdb_model import EMAIL

FIELDS = (
    ("name", "Name"),
    ("company", "Company"),
    ("title", "Title"),
    ("phone", "Phone"),
    ("mobile", "Mobile"),
    ("email", "Email"),
    ("address", "Address"),
    ("website", "Website"),
    ("notes", "Notes"),
    ("tags", "Tags"),
)
KEYS = tuple(k for k, _ in FIELDS)
COL = {k: i for i, k in enumerate(KEYS)}
JOIN = ", "
WIDTHS = (130, 100, 90, 90, 90, 160, 170, 110, 170, 90)
UNDO_LIMIT = 200
FONT = QFont("Tahoma", 8)
BOLD = QFont("Tahoma", 8, QFont.Weight.Bold)
LINK_FONT = QFont("Tahoma", 8)
LINK_FONT.setUnderline(True)
LINK_INK = "#0645ad"
MARK_BG = QColor(217, 119, 87, 80)
CTRL = Qt.KeyboardModifier.ControlModifier
SHIFT = Qt.KeyboardModifier.ShiftModifier
ENTER = (Qt.Key.Key_Return, Qt.Key.Key_Enter)
WIDTHS_KEY = "contacts/widths"


def blank_row():
    return {k: [] if k == "tags" else "" for k in KEYS}


def count_text(n):
    return f"{n} contacts"


def cell_text(row, key):
    return JOIN.join(row[key]) if key == "tags" else row[key]


def digits(text):
    return "".join(ch for ch in text if ch.isdigit())


def search_terms(row):
    # what the finder matches besides the name
    parts = (
        row["company"],
        row["email"],
        digits(row["phone"]),
        digits(row["mobile"]),
        *row["tags"],
    )
    return tuple(p for p in parts if p)


def mailto(text):
    return "mailto:" + text.split(JOIN)[0].strip()


def web_url(text):
    url = text.split(JOIN)[0].strip()
    return url if "://" in url else "https://" + url


def summary(rows, limit):
    lines = []
    for r in sorted(rows, key=lambda r: r["name"].lower()):
        text = "  ".join(t for t in (r["name"], r["phone"] or r["mobile"]) if t)
        if text:
            lines.append(
                {
                    "text": text,
                    "bg": "",
                    "fg": "",
                    "urgent": False,
                    "block": None,
                    "n": None,
                }
            )
    return {"count": len(rows), "preview": lines[:limit]}


PHONE = re.compile(r"\+?[\d(][\d ().\-]*\d")


def guess_contact(text):
    email = EMAIL.search(text)
    email = email.group() if email else ""
    text = text.replace(email, "", 1) if email else text
    phone = next((r for r in PHONE.findall(text) if len(digits(r)) >= 7), "")
    text = text.replace(phone, "", 1) if phone else text
    lines = (ln.strip(",;|- 	") for ln in text.splitlines())
    return {
        "name": next((ln for ln in lines if ln), ""),
        "company": "",
        "phone": phone,
        "mobile": "",
        "email": email,
    }


def _key(row):
    return json.dumps(row, sort_keys=True)


def unseen(have, new):
    seen, out = {_key(r) for r in have}, []
    for r in new:
        if _key(r) not in seen:
            seen.add(_key(r))
            out.append(r)
    return out


def _decode(data):
    if not isinstance(data, bytes):
        return data.removeprefix("\ufeff")
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252")


def _append(row, key, value, sep=JOIN):
    if value:
        row[key] = sep.join(p for p in (row[key], value) if p)


def _empty(row):
    return not any(row.values())


# vCard

FOLD = 75


def _esc(s):
    return (
        s.replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace(";", "\\;")
        .replace(",", "\\,")
    )


def _esc_min(s):
    return s.replace("\\", "\\\\").replace("\n", "\\n")


def _fold(line):
    out, rest = [line[:FOLD]], line[FOLD:]
    while rest:
        out.append(" " + rest[: FOLD - 1])
        rest = rest[FOLD - 1 :]
    return "\r\n".join(out)


def _card_lines(r):
    name = r["name"]
    given, _, family = name.rpartition(" ")
    if not given:
        given, family = "", name
    lines = [
        "BEGIN:VCARD",
        "VERSION:3.0",
        f"FN:{_esc(name)}",
        f"N:{_esc(family)};{_esc(given)};;;",
    ]
    if r["company"]:
        lines.append(f"ORG:{_esc(r['company'])}")
    if r["title"]:
        lines.append(f"TITLE:{_esc(r['title'])}")
    for key, kind, prop in (
        ("phone", "VOICE", "TEL"),
        ("mobile", "CELL", "TEL"),
        ("email", "INTERNET", "EMAIL"),
    ):
        lines += [f"{prop};TYPE={kind}:{_esc_min(v)}" for v in r[key].split(JOIN) if v]
    if r["address"]:
        lines.append(f"ADR;TYPE=WORK:;;{_esc(r['address'])};;;;")
    lines += [f"URL:{_esc_min(v)}" for v in r["website"].split(JOIN) if v]
    if r["notes"]:
        lines.append(f"NOTE:{_esc(r['notes'])}")
    if r["tags"]:
        lines.append("CATEGORIES:" + ",".join(_esc(t) for t in r["tags"]))
    lines.append("END:VCARD")
    return "".join(_fold(line) + "\r\n" for line in lines)


def format_vcards(rows):
    return "".join(_card_lines(r) for r in rows)


def write_vcards(path, rows):
    Path(path).write_bytes(format_vcards(rows).encode("utf-8"))


def _logical(text):
    # unfolding, and the quoted-printable soft line break of vCard 2.1
    out, soft = [], False
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if soft:
            out[-1] = out[-1][:-1] + raw
        elif raw[:1] in (" ", "\t") and out:
            out[-1] += raw[1:]
        else:
            out.append(raw)
        soft = (
            out[-1].endswith("=")
            and "QUOTED-PRINTABLE" in out[-1].split(":", 1)[0].upper()
        )
    return [line for line in out if line.strip()]


def _split(s, sep):
    out, cur, i = [], [], 0
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s):
            cur.append(s[i : i + 2])
            i += 2
            continue
        if ch == sep:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        i += 1
    out.append("".join(cur))
    return out


def _split_quoted(s, sep):
    out, cur, quoted = [], [], False
    for ch in s:
        quoted ^= ch == '"'
        if ch == sep and not quoted:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    return out


def _unescape(s):
    s = re.sub(r"\\(.)", lambda mo: "\n" if mo.group(1) in "nN" else mo.group(1), s)
    return s.replace("\r\n", "\n").replace("\r", "\n")


def _prop(line):
    quoted = False
    for i, ch in enumerate(line):
        quoted ^= ch == '"'
        if ch == ":" and not quoted:
            head, value = line[:i], line[i + 1 :]
            break
    else:
        raise ValueError(f"vCard line without ':': {line[:40]!r}")
    name, *params = _split_quoted(head, ";")
    types, qp, charset = set(), False, "utf-8"
    for p in params:
        k, eq, v = p.partition("=")
        k, v = k.upper(), v.strip('"')
        if not eq and k in ("QUOTED-PRINTABLE", "BASE64", "8BIT", "7BIT"):
            qp = qp or k == "QUOTED-PRINTABLE"
        elif not eq or k == "TYPE":
            types.update(t.lower() for t in (v if eq else k).split(","))
        elif k == "ENCODING":
            qp = qp or v.upper() == "QUOTED-PRINTABLE"
        elif k == "CHARSET":
            charset = v
    return name.rsplit(".", 1)[-1].upper(), types, value, qp, charset


def _text(value, qp, charset):
    if qp:
        value = quopri.decodestring(value.encode("utf-8")).decode(charset)
    return _unescape(value)


def _card(props):
    row, fn, n = blank_row(), "", None
    for name, types, value, qp, charset in props:
        if name in ("N", "ADR", "ORG"):
            parts = [_text(p, qp, charset) for p in _split(value, ";")]
        else:
            text = _text(value, qp, charset)
        if name == "FN":
            fn = text
        elif name == "N":
            n = parts
        elif name == "ORG":
            _append(row, "company", JOIN.join(p for p in parts if p))
        elif name == "TITLE":
            row["title"] = text
        elif name == "TEL":
            _append(
                row,
                "mobile" if types & {"cell", "mobile"} else "phone",
                text.removeprefix("tel:"),
            )
        elif name == "EMAIL":
            _append(row, "email", text)
        elif name == "ADR":
            _append(row, "address", JOIN.join(p for p in parts if p), "; ")
        elif name == "URL":
            _append(row, "website", text)
        elif name == "NOTE":
            _append(row, "notes", text, "\n")
        elif name == "CATEGORIES":
            for t in (_text(p, qp, charset).strip() for p in _split(value, ",")):
                if t and t not in row["tags"]:
                    row["tags"].append(t)
    if not fn and n:
        # N is family;given;additional;prefix;suffix
        fn = " ".join(p for p in (n[1:2] + n[2:3] + n[:1]) if p)
    row["name"] = fn
    return row


def parse_vcards(data):
    rows, cur = [], None
    for line in _logical(_decode(data)):
        if cur is None and ":" not in line:
            continue
        name, types, value, qp, charset = _prop(line)
        if name == "BEGIN" and value.upper() == "VCARD":
            cur = []
        elif name == "END" and value.upper() == "VCARD" and cur is not None:
            row = _card(cur)
            if not _empty(row):
                rows.append(row)
            cur = None
        elif cur is not None:
            cur.append((name, types, value, qp, charset))
    if cur is not None:
        raise ValueError("vCard without END:VCARD")
    if not rows:
        raise ValueError("no vCards found")
    return rows


def read_vcards(path):
    return parse_vcards(Path(path).read_bytes())


# CSV

CSV_NAMES = {
    "name": ("name", "full name", "display name"),
    "company": (
        "company",
        "organization",
        "organization name",
        "organization 1 - name",
    ),
    "title": ("title", "job title", "organization title", "organization 1 - title"),
    "phone": (
        "phone",
        "business phone",
        "home phone",
        "primary phone",
        "phone 1 - value",
    ),
    "mobile": ("mobile", "mobile phone", "cell phone"),
    "email": ("email", "e-mail", "e-mail address", "e-mail 1 - value", "email address"),
    "address": (
        "address",
        "address 1 - formatted",
        "business street",
        "business city",
        "business state",
        "business postal code",
    ),
    "website": ("website", "web page", "website 1 - value"),
    "notes": ("notes",),
    "tags": ("tags", "categories", "labels"),
}
CSV_KEY = {h: k for k, names in CSV_NAMES.items() for h in names}
FIRST, LAST = ("first name", "given name"), ("last name", "family name")
TAG_SPLIT = re.compile(r"\s*(?:,|;|:::)\s*")


def format_csv(rows):
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\r\n")
    w.writerow([label for _, label in FIELDS])
    for r in rows:
        w.writerow([cell_text(r, k) for k in KEYS])
    return out.getvalue()


def write_csv(path, rows):
    Path(path).write_bytes(b"\xef\xbb\xbf" + format_csv(rows).encode("utf-8"))


def parse_csv(data):
    table = list(csv.reader(io.StringIO(_decode(data), newline="")))
    if not table:
        raise ValueError("empty CSV file")
    head = [h.strip().lower() for h in table[0]]
    cols = [(i, CSV_KEY[h]) for i, h in enumerate(head) if h in CSV_KEY]
    first = [i for i, h in enumerate(head) if h in FIRST]
    last = [i for i, h in enumerate(head) if h in LAST]
    if not (cols or first or last):
        raise ValueError("no contact columns in the CSV file")
    rows = []
    for rec in table[1:]:

        def at(i):
            return rec[i].strip().replace("\r\n", "\n") if i < len(rec) else ""

        row = blank_row()
        for i, key in cols:
            v = at(i)
            if key == "tags":
                row["tags"] += [
                    t for t in TAG_SPLIT.split(v) if t and t not in row["tags"]
                ]
            elif v and v not in row[key].split(JOIN):
                _append(row, key, v)
        if not row["name"]:
            row["name"] = " ".join(p for p in (at(i) for i in first + last) if p)
        if not _empty(row):
            rows.append(row)
    return rows


def read_csv(path):
    return parse_csv(Path(path).read_bytes())


READERS = {".vcf": read_vcards, ".csv": read_csv}


def dropped_files(mime):
    paths = [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]
    return [p for p in paths if Path(p).suffix.lower() in READERS]


def read_files(parent, paths):
    try:
        return [r for p in paths for r in READERS[Path(p).suffix.lower()](p)]
    except (OSError, ValueError) as e:
        QMessageBox.warning(parent, "Import", str(e))
        return None


# table


class Edit(QUndoCommand):
    def __init__(self, model, row, key, old, new):
        super().__init__()
        self.model, self.row, self.key, self.old, self.new = model, row, key, old, new

    def redo(self):
        self.model.put(self.row, self.key, self.new)

    def undo(self):
        self.model.put(self.row, self.key, self.old)


class Rows(QUndoCommand):
    # items: (row number, row dict) ascending; inserted, or removed when insert is false
    def __init__(self, model, items, insert):
        super().__init__()
        self.model, self.items, self.insert = model, items, insert

    def redo(self):
        (self.model.insert if self.insert else self.model.remove)(self.items)

    def undo(self):
        (self.model.remove if self.insert else self.model.insert)(self.items)


def _cell(key, text):
    if key == "tags":
        return [t for t in (p.strip() for p in re.split(r"[,;]", text)) if t]
    return text.strip()


class Model(QAbstractTableModel):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.rows = []
        # rows tinted while a proposed change is shown; display only
        self.marked = set()
        self.stack = QUndoStack(self)
        self.stack.setUndoLimit(UNDO_LIMIT)

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(FIELDS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if (
            role == Qt.ItemDataRole.DisplayRole
            and orientation == Qt.Orientation.Horizontal
        ):
            return FIELDS[section][1]
        return None

    def flags(self, index):
        return (
            Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemIsEditable
        )

    def text(self, row, col):
        return cell_text(self.rows[row], KEYS[col]).replace("\n", " ")

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            return self.text(index.row(), index.column())
        if role == Qt.ItemDataRole.BackgroundRole and index.row() in self.marked:
            return MARK_BG
        if KEYS[index.column()] == "email" and self.text(index.row(), index.column()):
            if role == Qt.ItemDataRole.FontRole:
                return LINK_FONT
            if role == Qt.ItemDataRole.ForegroundRole:
                return QColor(rdb_theme.shade(LINK_INK))
        return None

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if role != Qt.ItemDataRole.EditRole:
            return False
        row, key = index.row(), KEYS[index.column()]
        old, new = self.rows[row][key], _cell(key, str(value))
        # a line edit cannot hold a line break, so an untouched note commits as it was
        if new == old or (key != "tags" and old.replace("\n", " ") == new):
            return False
        self.stack.push(Edit(self, row, key, old, new))
        return True

    def mark(self, rows):
        self.marked = set(rows)
        if self.rows:
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(len(self.rows) - 1, len(FIELDS) - 1),
                [Qt.ItemDataRole.BackgroundRole],
            )

    def put(self, row, key, value):
        self.rows[row][key] = value
        ix = self.index(row, COL[key])
        self.dataChanged.emit(ix, ix)
        self.changed.emit()

    def insert(self, items):
        for i, row in items:
            self.beginInsertRows(QModelIndex(), i, i)
            self.rows.insert(i, row)
            self.endInsertRows()
        self.changed.emit()

    def remove(self, items):
        for i, _ in reversed(items):
            self.beginRemoveRows(QModelIndex(), i, i)
            del self.rows[i]
            self.endRemoveRows()
        self.changed.emit()

    def load(self, rows):
        self.beginResetModel()
        self.rows = rows
        self.stack.clear()
        self.endResetModel()

    def add(self, new):
        start = len(self.rows)
        self.stack.push(Rows(self, list(enumerate(new, start)), True))

    def drop(self, numbers):
        self.stack.push(Rows(self, [(i, self.rows[i]) for i in sorted(numbers)], False))


class Proxy(QSortFilterProxyModel):
    def __init__(self):
        super().__init__()
        # rdb_finder imports the editor, which imports rdb_links, and the finder imports this module
        from rdb_finder import match

        self.match = match
        self.terms = {}
        self.setSortCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

    def set_filter(self, col, text):
        self.terms[col] = text.lower().split()
        self.invalidateFilter()

    def filterAcceptsRow(self, row, parent):
        src = self.sourceModel()
        return all(
            self.match(t, src.text(row, col).lower())
            for col, ts in self.terms.items()
            for t in ts
        )


class Table(QTableView):
    def __init__(self, page):
        super().__init__()
        self.page = page
        self.setAcceptDrops(True)

    def keyPressEvent(self, e):
        k, mods, page = (
            e.key(),
            e.modifiers() & ~Qt.KeyboardModifier.KeypadModifier,
            self.page,
        )
        if k in ENTER and not mods and self.currentIndex().isValid():
            self.edit(self.currentIndex())
        elif k == Qt.Key.Key_Delete and not mods:
            page.delete_selected()
        elif k == Qt.Key.Key_N and mods == CTRL:
            page.add_row()
        elif k == Qt.Key.Key_F and mods == CTRL:
            page.focus_filter()
        elif k == Qt.Key.Key_Z and mods == CTRL:
            page.model.stack.undo()
        elif (
            k == Qt.Key.Key_Y
            and mods == CTRL
            or k == Qt.Key.Key_Z
            and mods == CTRL | SHIFT
        ):
            page.model.stack.redo()
        elif k == Qt.Key.Key_Escape:
            page.escape()
        else:
            super().keyPressEvent(e)

    def mousePressEvent(self, e):
        index = self.indexAt(e.position().toPoint())
        if (
            e.button() == Qt.MouseButton.LeftButton
            and e.modifiers() == CTRL
            and index.isValid()
            and self.page.follow(index)
        ):
            e.accept()
            return
        super().mousePressEvent(e)

    def contextMenuEvent(self, e):
        menu = self.page.context_menu(self.indexAt(e.pos()))
        menu.exec(e.globalPos())
        menu.deleteLater()

    def dragEnterEvent(self, e):
        if dropped_files(e.mimeData()):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        self.dragEnterEvent(e)

    def dropEvent(self, e):
        paths = dropped_files(e.mimeData())
        if paths:
            e.acceptProposedAction()
            self.page.import_files(paths)
        else:
            e.ignore()


class ContactsPage(QWidget):
    changed = Signal()
    leave = Signal()
    web = Signal(str)

    def __init__(self, settings, prefs):
        super().__init__()
        self.settings, self.prefs = settings, prefs
        self._bg = self._fg = ""
        self.dirty = self.restoring = False
        self.model, self.proxy = Model(), Proxy()
        self.proxy.setSourceModel(self.model)
        self.header = QFrame()
        self.header.setObjectName("header")
        self.title, self.state = QLabel(), QLabel()
        self.title.setFont(BOLD)
        self.state.setFont(FONT)
        row = QHBoxLayout(self.header)
        row.setContentsMargins(8, 3, 8, 3)
        row.addWidget(self.title)
        row.addStretch(1)
        row.addWidget(self.state)
        self.table = t = Table(self)
        t.setModel(self.proxy)
        t.setFont(FONT)
        rdb_theme.follow(t, rdb_theme.grid_sheet)
        t.setShowGrid(True)
        t.setGridStyle(Qt.PenStyle.SolidLine)
        t.setAlternatingRowColors(False)
        t.setWordWrap(False)
        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        t.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        t.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        t.setTabKeyNavigation(True)
        vh = t.verticalHeader()
        vh.hide()
        vh.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        vh.setMinimumSectionSize(1)
        vh.setDefaultSectionSize(t.fontMetrics().height() + 2)
        self.head = t.horizontalHeader()
        self.head.setFont(FONT)
        self.head.setHighlightSections(False)
        self.head.setStretchLastSection(True)
        t.setSortingEnabled(True)
        self.head.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        self.bar = QWidget()
        self.filters = [QLineEdit(self.bar) for _ in FIELDS]
        for i, f in enumerate(self.filters):
            f.setFont(FONT)
            f.textChanged.connect(lambda text, c=i: self.proxy.set_filter(c, text))
            f.installEventFilter(self)
        self.bar.setFixedHeight(self.filters[0].sizeHint().height())
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        col.addWidget(self.header)
        col.addWidget(self.bar)
        col.addWidget(t, 1)
        self._restore_widths()
        self.head.sectionResized.connect(self._resized)
        t.horizontalScrollBar().valueChanged.connect(self._place)
        self.model.changed.connect(self._edited)
        for sig in (
            self.model.rowsInserted,
            self.model.rowsRemoved,
            self.model.modelReset,
        ):
            sig.connect(self._count)
        self._count()
        rdb_theme.follow(self, ContactsPage._style)

    def _restore_widths(self):
        raw = self.prefs.value(WIDTHS_KEY)
        if raw is None:
            widths = WIDTHS
        else:
            widths = [int(x) for x in ([raw] if isinstance(raw, str) else raw)]
        if len(widths) != len(FIELDS):
            raise ValueError(
                f"{WIDTHS_KEY}: {len(widths)} widths for {len(FIELDS)} columns"
            )
        self.restoring = True
        for i, w in enumerate(widths):
            self.head.resizeSection(i, w)
        self.restoring = False

    def _resized(self, *_):
        if not self.restoring:
            self.prefs.setValue(
                WIDTHS_KEY, [self.head.sectionSize(i) for i in range(len(FIELDS))]
            )
        self._place()

    def _place(self, *_):
        off = self.table.frameWidth()
        for i, f in enumerate(self.filters):
            f.setGeometry(
                off + self.head.sectionViewportPosition(i),
                0,
                self.head.sectionSize(i),
                self.bar.height(),
            )

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place()

    def showEvent(self, e):
        super().showEvent(e)
        self._place()

    def _edited(self):
        self.dirty = True
        self.changed.emit()

    def _count(self, *_):
        self.state.setText(count_text(self.model.rowCount()))

    def _style(self):
        bg = (
            rdb_theme.shade(self._bg).name()
            if self._bg
            else m.shown(self.settings, "card_bg")
        )
        rdb_theme.strip_sheet(self.header, bg)
        ink = rdb_theme.shade(self._fg) if self._fg else rdb_theme.color("card_ink")
        for label in (self.title, self.state):
            pal = label.palette()
            pal.setColor(QPalette.ColorRole.WindowText, ink)
            label.setPalette(pal)

    def refresh(self):
        self._style()
        self.table.viewport().update()

    def set_colors(self, bg, fg):
        self._bg, self._fg = bg, fg
        self._style()

    def set_title(self, text):
        self.title.setText(text)

    def load(self, rows, row=None):
        for f in self.filters:
            f.setText("")
        self.model.load(rows)
        self.dirty = False
        if row is not None and row < len(rows):
            self.select_row(row)

    def select_row(self, row):
        ix = self.proxy.mapFromSource(self.model.index(row, 0))
        self.table.setCurrentIndex(ix)
        self.table.selectRow(ix.row())
        self.table.scrollTo(ix)

    def current_row(self):
        ix = self.table.currentIndex()
        return self.proxy.mapToSource(ix).row() if ix.isValid() else 0

    def focus_filter(self):
        self.filters[0].setFocus()
        self.filters[0].selectAll()

    def escape(self):
        if not self.settings["vim"]:
            self.leave.emit()

    def eventFilter(self, obj, e):
        if obj in self.filters and e.type() == QEvent.Type.KeyPress:
            if e.key() == Qt.Key.Key_Down:
                self.table.setFocus()
                return True
            if e.key() == Qt.Key.Key_Escape:
                if obj.text():
                    obj.clear()
                else:
                    self.escape()
                return True
        return super().eventFilter(obj, e)

    def add_row(self):
        for f in self.filters:
            f.setText("")
        self.model.add([blank_row()])
        ix = self.proxy.mapFromSource(self.model.index(self.model.rowCount() - 1, 0))
        self.table.setCurrentIndex(ix)
        self.table.selectRow(ix.row())
        self.table.scrollTo(ix)
        self.table.edit(ix)

    def delete_selected(self):
        rows = {
            self.proxy.mapToSource(i).row()
            for i in self.table.selectionModel().selectedRows()
        }
        if not rows:
            return
        if self.settings["confirm_delete"]:
            yes, no = QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No
            if (
                QMessageBox.warning(
                    self, "Delete", "Delete selected contacts?", yes | no, no
                )
                != yes
            ):
                return
        self.model.drop(rows)

    def follow(self, index):
        key = KEYS[index.column()]
        text = self.model.rows[self.proxy.mapToSource(index).row()][key]
        if not text or key not in ("email", "website"):
            return False
        QDesktopServices.openUrl(
            QUrl(mailto(text) if key == "email" else web_url(text))
        )
        return True

    def context_menu(self, index):
        menu = QMenu(self)
        if index.isValid():
            key = KEYS[index.column()]
            text = self.model.rows[self.proxy.mapToSource(index).row()][key]
            if text and key == "email":
                menu.addAction(
                    "Email", lambda: QDesktopServices.openUrl(QUrl(mailto(text)))
                )
            if text and key in ("phone", "mobile"):
                menu.addAction(
                    "Copy Number", lambda: QApplication.clipboard().setText(text)
                )
            if text and key == "website":
                menu.addAction("Website", lambda: self.web.emit(web_url(text)))
            if menu.actions():
                menu.addSeparator()
        menu.addAction("Import vCard...", lambda: self.pick_import("vCard (*.vcf)"))
        menu.addAction(
            "Export vCard...",
            lambda: self.pick_export(".vcf", "vCard (*.vcf)", write_vcards),
        )
        menu.addAction("Import CSV...", lambda: self.pick_import("CSV (*.csv)"))
        menu.addAction(
            "Export CSV...", lambda: self.pick_export(".csv", "CSV (*.csv)", write_csv)
        )
        return menu

    def pick_import(self, kind):
        path, _ = QFileDialog.getOpenFileName(self, "Import", "", kind)
        if path:
            self.import_files([path])

    def pick_export(self, suffix, kind, write):
        name = re.sub(r'[\\/:*?"<>|]', "_", self.title.text() or "contacts") + suffix
        path, _ = QFileDialog.getSaveFileName(self, "Export", name, kind)
        if not path:
            return
        try:
            write(path, self.model.rows)
        except OSError as e:
            QMessageBox.warning(self, "Export", str(e))

    def import_files(self, paths):
        new = read_files(self, paths)
        if new is None:
            return 0
        rows = unseen(self.model.rows, new)
        if rows:
            self.model.add(rows)
        return len(rows)


FORM = ("name", "company", "phone", "mobile", "email")


class ContactPopup(QFrame):
    added = Signal(dict)

    def __init__(self, parent, prefill=None):
        super().__init__(parent, Qt.WindowType.Popup)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        rdb_theme.follow(self, rdb_theme.popup_frame)
        grid = QGridLayout(self)
        grid.setContentsMargins(6, 6, 6, 6)
        grid.setSpacing(4)
        labels = dict(FIELDS)
        self._edits = {}
        for i, k in enumerate(FORM):
            grid.addWidget(QLabel(labels[k]), i, 0)
            edit = self._edits[k] = QLineEdit("" if prefill is None else prefill[k])
            edit.returnPressed.connect(self._add)
            grid.addWidget(edit, i, 1)
        buttons = QHBoxLayout()
        for text, fn in (("Add", self._add), ("Cancel", self.close)):
            b = QPushButton(text)
            b.setAutoDefault(False)
            b.clicked.connect(fn)
            buttons.addWidget(b)
        grid.addLayout(buttons, len(FORM), 0, 1, 2)
        self._edits["name"].setFocus()

    def _add(self):
        row = blank_row()
        row.update({k: e.text().strip() for k, e in self._edits.items()})
        if not _empty(row):
            self.added.emit(row)
        self.close()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(e)
