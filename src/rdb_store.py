import datetime
import hashlib
import json
import sqlite3
from pathlib import Path


FORMAT = "rdb1"
TABLES = """
CREATE TABLE info(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE projects(pid TEXT PRIMARY KEY, html TEXT NOT NULL, meta TEXT NOT NULL,
                      collapsed INTEGER NOT NULL);
CREATE TABLE images(name TEXT PRIMARY KEY, data BLOB NOT NULL);
CREATE TABLE trash(pid TEXT NOT NULL, deleted TEXT NOT NULL, html TEXT NOT NULL,
                   meta TEXT NOT NULL, collapsed INTEGER NOT NULL);
"""


class Store:
    def __init__(self, path, create=False):
        self.path = Path(path)
        # called after every write of content (projects, boards, the map, contacts)
        self.on_write = None
        # called after every put_image, so the pack pass picks the new image up
        self.image_put = None
        if create and self.path.exists():
            raise FileExistsError(f"{self.path} exists")
        if not create and not self.path.exists():
            raise FileNotFoundError(f"{self.path} not found")
        self.db = sqlite3.connect(str(self.path))
        if create:
            with self.db:
                self.db.executescript(TABLES)
                self.db.execute("INSERT INTO info VALUES('format', ?)", (FORMAT,))
        try:
            row = self.db.execute(
                "SELECT value FROM info WHERE key='format'"
            ).fetchone()
        except sqlite3.DatabaseError as e:
            self.db.close()
            raise ValueError(f"{self.path} is not an rdb file") from e
        if row != (FORMAT,):
            self.db.close()
            raise ValueError(f"{self.path} is not an rdb file")
        # files from before boards or contacts existed gain the tables on first open
        with self.db:
            for table in (
                "boards(pid TEXT PRIMARY KEY, map TEXT NOT NULL)",
                "contacts(pid TEXT PRIMARY KEY, rows TEXT NOT NULL)",
                "contacts_trash(pid TEXT NOT NULL, deleted TEXT NOT NULL, rows TEXT NOT NULL)",
                "images_packed(name TEXT PRIMARY KEY)",
                "markup(name TEXT PRIMARY KEY, strokes TEXT NOT NULL)",
            ):
                self.db.execute(f"CREATE TABLE IF NOT EXISTS {table}")

    def _wrote(self):
        if self.on_write:
            self.on_write()

    def close(self):
        self.db.close()

    def _get_info(self, key):
        row = self.db.execute("SELECT value FROM info WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def _put_info(self, key, value):
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO info VALUES(?, ?)", (key, json.dumps(value))
            )

    def get_map(self):
        return self._get_info("map")

    def put_map(self, m):
        self._put_info("map", m)
        self._wrote()

    def get_snip_board(self):
        return self._get_info("snip_board")

    def put_snip_board(self, pid):
        self._put_info("snip_board", pid)

    def get_holding_card(self):
        return self._get_info("holding_card")

    def put_holding_card(self, pid):
        self._put_info("holding_card", pid)

    def get_dump_card(self):
        return self._get_info("dump_card")

    def put_dump_card(self, pid):
        self._put_info("dump_card", pid)

    def get_quick_add(self):
        return self._get_info("quick_add")

    def put_quick_add(self, loc):
        self._put_info("quick_add", loc)

    def get_title_tiles(self):
        tiles = self._get_info("title_tiles")
        return [] if tiles is None else tiles

    def put_title_tiles(self, entries):
        self._put_info("title_tiles", entries)

    def get_view(self):
        return self._get_info("view")

    def put_view(self, v):
        self._put_info("view", v)

    def get_settings(self):
        return self._get_info("settings") or {}

    def put_settings(self, s):
        self._put_info("settings", s)

    def get_tag_sets(self):
        sets = self._get_info("tag_sets")
        return [] if sets is None else sets

    def put_tag_sets(self, sets):
        self._put_info("tag_sets", sets)

    def get_visited_links(self):
        links = self._get_info("visited_links")
        return [] if links is None else links

    def put_visited_links(self, links):
        self._put_info("visited_links", links)

    def project_ids(self):
        return [r[0] for r in self.db.execute("SELECT pid FROM projects ORDER BY pid")]

    def has_project(self, pid):
        return (
            self.db.execute("SELECT 1 FROM projects WHERE pid=?", (pid,)).fetchone()
            is not None
        )

    def get_project(self, pid):
        row = self.db.execute(
            "SELECT html, meta, collapsed FROM projects WHERE pid=?", (pid,)
        ).fetchone()
        if row is None:
            raise KeyError(f"{self.path}: no project {pid!r}")
        return {"html": row[0], "meta": json.loads(row[1]), "collapsed": bool(row[2])}

    def project_stamps(self):
        rows = self.db.execute("SELECT pid, html, meta, collapsed FROM projects")
        return {
            pid: hashlib.sha1((html + meta).encode()).digest() + bytes([collapsed])
            for pid, html, meta, collapsed in rows
        }

    def put_project(self, pid, html, meta, collapsed):
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO projects VALUES(?, ?, ?, ?)",
                (pid, html, json.dumps(meta), int(collapsed)),
            )
        self._wrote()

    def delete_project(self, pid):
        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        with self.db:
            self.db.execute(
                "INSERT INTO trash SELECT pid, ?, html, meta, collapsed FROM projects WHERE pid=?",
                (stamp, pid),
            )
            self.db.execute("DELETE FROM projects WHERE pid=?", (pid,))
        self._wrote()

    def restore_project(self, pid):
        with self.db:
            row = self.db.execute(
                "SELECT rowid FROM trash WHERE pid=? ORDER BY rowid DESC", (pid,)
            ).fetchone()
            if row is None:
                raise KeyError(f"{self.path}: no trashed project {pid!r}")
            self.db.execute(
                "INSERT INTO projects SELECT pid, html, meta, collapsed FROM trash WHERE rowid=?",
                row,
            )
            self.db.execute("DELETE FROM trash WHERE rowid=?", row)
        self._wrote()

    def in_trash(self, pid):
        return pid in self.trash_ids()

    def note_holders(self, note):
        # (project pid, block number) of every item that links this note document
        needle = f'"nt": "{note}"'
        out = []
        rows = self.db.execute(
            "SELECT pid, meta FROM projects WHERE instr(meta, ?) > 0", (needle,)
        )
        for pid, meta in rows:
            out += [
                (pid, i)
                for i, row in enumerate(json.loads(meta))
                if row.get("nt") == note
            ]
        return out

    def status_counts(self):
        # {status key: items carrying it} over every stored project, the trashed ones too:
        # a restore brings their items back
        out = {}
        for (meta,) in self.db.execute(
            "SELECT meta FROM projects WHERE instr(meta, ?) > 0"
            " UNION ALL SELECT meta FROM trash WHERE instr(meta, ?) > 0",
            ('"st"', '"st"'),
        ):
            for row in json.loads(meta):
                if row.get("st"):
                    out[row["st"]] = out.get(row["st"], 0) + 1
        return out

    def board_ids(self):
        return [r[0] for r in self.db.execute("SELECT pid FROM boards ORDER BY pid")]

    def get_board(self, pid):
        row = self.db.execute("SELECT map FROM boards WHERE pid=?", (pid,)).fetchone()
        if row is None:
            raise KeyError(f"{self.path}: no board {pid!r}")
        return json.loads(row[0])

    def put_board(self, pid, m):
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO boards VALUES(?, ?)", (pid, json.dumps(m))
            )
        self._wrote()

    def delete_board(self, pid):
        with self.db:
            self.db.execute("DELETE FROM boards WHERE pid=?", (pid,))
        self._wrote()

    def trash_ids(self):
        rows = self.db.execute(
            "SELECT pid, deleted FROM trash UNION ALL SELECT pid, deleted FROM contacts_trash"
            " ORDER BY deleted"
        )
        return [r[0] for r in rows]

    def contact_ids(self):
        return [r[0] for r in self.db.execute("SELECT pid FROM contacts ORDER BY pid")]

    def has_contacts(self, pid):
        return (
            self.db.execute("SELECT 1 FROM contacts WHERE pid=?", (pid,)).fetchone()
            is not None
        )

    def get_contacts(self, pid):
        row = self.db.execute(
            "SELECT rows FROM contacts WHERE pid=?", (pid,)
        ).fetchone()
        if row is None:
            raise KeyError(f"{self.path}: no contacts {pid!r}")
        return json.loads(row[0])

    def put_contacts(self, pid, rows):
        # a contacts card's pid is its card id, which no project or board shares
        if self.has_project(pid) or pid in self.board_ids():
            raise ValueError(f"{self.path}: {pid!r} is a project or board id")
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO contacts VALUES(?, ?)", (pid, json.dumps(rows))
            )
        self._wrote()

    def delete_contacts(self, pid):
        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        with self.db:
            if not self.has_contacts(pid):
                raise KeyError(f"{self.path}: no contacts {pid!r}")
            self.db.execute(
                "INSERT INTO contacts_trash SELECT pid, ?, rows FROM contacts WHERE pid=?",
                (stamp, pid),
            )
            self.db.execute("DELETE FROM contacts WHERE pid=?", (pid,))
        self._wrote()

    def restore_contacts(self, pid):
        with self.db:
            row = self.db.execute(
                "SELECT rowid FROM contacts_trash WHERE pid=? ORDER BY rowid DESC",
                (pid,),
            ).fetchone()
            if row is None:
                raise KeyError(f"{self.path}: no trashed contacts {pid!r}")
            self.db.execute(
                "INSERT INTO contacts SELECT pid, rows FROM contacts_trash WHERE rowid=?",
                row,
            )
            self.db.execute("DELETE FROM contacts_trash WHERE rowid=?", row)
        self._wrote()

    def image_names(self):
        return {r[0] for r in self.db.execute("SELECT name FROM images")}

    def get_image(self, name):
        row = self.db.execute(
            "SELECT data FROM images WHERE name=?", (name,)
        ).fetchone()
        if row is None:
            raise KeyError(f"{self.path}: no image {name!r}")
        return bytes(row[0])

    def put_image(self, name, data):
        # stored as given: packing is the PackPass's job, off the GUI thread
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO images VALUES(?, ?)",
                (name, sqlite3.Binary(data)),
            )
            self.db.execute("DELETE FROM images_packed WHERE name=?", (name,))
        if self.image_put is not None:
            self.image_put()

    def _store_image(self, name, data):
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO images VALUES(?, ?)",
                (name, sqlite3.Binary(data)),
            )
            self.db.execute("INSERT OR IGNORE INTO images_packed VALUES(?)", (name,))

    def unpacked_images(self):
        q = "SELECT name FROM images WHERE name NOT IN (SELECT name FROM images_packed)"
        return sorted(r[0] for r in self.db.execute(q))

    def replace_packed(self, name, old, new):
        # the packed bytes go in only while the stored ones are still the bytes that were packed
        if self.get_image(name) == old:
            self._store_image(name, new)
            return True
        return False

    def vacuum(self):
        self.db.execute("VACUUM")

    def has_image(self, name):
        q = "SELECT 1 FROM images WHERE name=?"
        return self.db.execute(q, (name,)).fetchone() is not None

    def delete_image(self, name):
        with self.db:
            self.db.execute("DELETE FROM images WHERE name=?", (name,))
            self.db.execute("DELETE FROM markup WHERE name=?", (name,))

    def get_markup(self, name):
        # an image without a row has no markup
        row = self.db.execute(
            "SELECT strokes FROM markup WHERE name=?", (name,)
        ).fetchone()
        return json.loads(row[0]) if row else []

    def put_markup(self, name, strokes):
        with self.db:
            if strokes:
                self.db.execute(
                    "INSERT OR REPLACE INTO markup VALUES(?, ?)",
                    (name, json.dumps(strokes)),
                )
            else:
                self.db.execute("DELETE FROM markup WHERE name=?", (name,))

    def backup_to(self, path):
        if Path(path).exists():
            raise FileExistsError(f"{path} exists")
        dest = sqlite3.connect(str(path))
        with dest:
            self.db.backup(dest)
        dest.close()


def rename_in_sets(sets, old, new):
    # old becomes new inside every set; a set that then holds the name twice (any case) keeps the first
    out = []
    for s in sets:
        seen, tags = set(), []
        for t in s["tags"]:
            t = new if t.lower() == old.lower() else t
            if t.lower() not in seen:
                seen.add(t.lower())
                tags.append(t)
        out.append({"name": s["name"], "tags": tags})
    return out


def drop_from_sets(sets, names):
    # the names leave every set; a set left empty goes
    gone = {n.lower() for n in names}
    kept = [
        {"name": s["name"], "tags": [t for t in s["tags"] if t.lower() not in gone]}
        for s in sets
    ]
    return [s for s in kept if s["tags"]]
