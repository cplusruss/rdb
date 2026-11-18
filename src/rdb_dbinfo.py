import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QGridLayout, QLabel

import rdb_model as m

PAIRS_PER_ROW = 2


def size_text(n):
    for limit, suffix in ((1e9, "G"), (1e6, "M"), (1e3, "k")):
        if n >= limit:
            return f"{n / limit:.3g}{suffix}B"
    return f"{n}B"


def _stamp(t):
    return datetime.datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S")


def info_rows(store):
    st = store.path.stat()
    pids = store.project_ids()
    notes = sum(1 for p in pids if m.NOTE_PID.fullmatch(p))
    counts = (
        ("Projects", len(pids) - notes),
        ("Notes", notes),
        ("Boards", len(store.board_ids())),
        ("Contacts", len(store.contact_ids())),
        ("Images", len(store.image_names())),
        ("Trash", len(store.trash_ids())),
    )
    return [
        ("Location", str(store.path.resolve())),
        ("Last saved", _stamp(st.st_mtime)),
        # st_ctime is the creation time on Windows
        ("Created", _stamp(st.st_ctime)),
        ("Size", size_text(st.st_size)),
        *((label, str(n)) for label, n in counts),
    ]


def _value(text):
    w = QLabel(text)
    w.setTextFormat(Qt.TextFormat.PlainText)
    w.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return w


class DbInfoDialog(QDialog):
    def __init__(self, parent, store):
        super().__init__(parent)
        self.setWindowTitle("Database")
        grid = QGridLayout(self)
        grid.setVerticalSpacing(0)
        (path_label, path), *rest = info_rows(store)
        grid.addWidget(QLabel(path_label), 0, 0)
        grid.addWidget(_value(path), 0, 1, 1, 2 * PAIRS_PER_ROW)
        for i, (label, value) in enumerate(rest):
            row, col = 1 + i // PAIRS_PER_ROW, 2 * (i % PAIRS_PER_ROW)
            grid.addWidget(QLabel(label), row, col)
            grid.addWidget(_value(value), row, col + 1)
        grid.setColumnStretch(2 * PAIRS_PER_ROW, 1)
