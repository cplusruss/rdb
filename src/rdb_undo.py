import copy
import functools

from PySide6.QtGui import QUndoCommand, QUndoStack

import rdb_model as m

# nudges on one selection closer together than this are one run
RUN_S = 1.0
NUDGE = 1


def _bare(mp):
    # card zoom is no part of undo: a map differs only by what is not zoom
    if mp is None:
        return None
    return {
        **mp,
        "cards": [{k: v for k, v in c.items() if k != "zoom"} for c in mp["cards"]],
    }


def snapshot(store, clip, copied, path, rows=(), contacts=()):
    maps = {"": m.load_map(store)}
    maps.update({b: m.load_board(store, b) for b in store.board_ids()})
    return {
        "path": list(path),
        "maps": maps,
        "clip": copy.deepcopy(clip),
        # a copy buffer is never changed in place, only replaced
        "copy": copied,
        "projects": set(store.project_ids()),
        "contacts": set(store.contact_ids()),
        "images": store.image_names(),
        "snip": store.get_snip_board(),
        # bytes of images an edit is about to delete, filled in by that edit
        "blobs": {},
        "rows": {p: store.get_project(p) for p in rows},
        "crows": {p: store.get_contacts(p) for p in contacts},
    }


def undoable(fn):
    @functools.wraps(fn)
    def run(self, *a, **k):
        with self.recorded():
            return fn(self, *a, **k)

    return run


class MapEdit(QUndoCommand):
    def __init__(self, win, before, after, nudge=None):
        super().__init__()
        self.win, self.fresh, self.nudge = win, True, nudge
        self.board = before["path"][-1] if before["path"] else ""
        old, new = before["maps"], after["maps"]
        self.maps = {
            k: (old.get(k), new.get(k))
            for k in old.keys() | new.keys()
            if _bare(old.get(k)) != _bare(new.get(k))
        }
        clip = (before["clip"], after["clip"])
        self.clip = clip if clip[0] != clip[1] else None
        # a copy alone is no edit: it rides on whatever else the command changed
        self.copy = (
            (before["copy"], after["copy"])
            if before["copy"] is not after["copy"]
            else None
        )
        self.rows = {
            p: (row, after["rows"][p])
            for p, row in before["rows"].items()
            if row != after["rows"][p]
        }
        self.crows = {
            p: (row, after["crows"][p])
            for p, row in before["crows"].items()
            if row != after["crows"][p]
        }
        self.snip = (
            (before["snip"], after["snip"]) if before["snip"] != after["snip"] else None
        )
        self.trashed = sorted(before["projects"] - after["projects"])
        self.made = sorted(after["projects"] - before["projects"])
        self.c_trashed = sorted(before["contacts"] - after["contacts"])
        self.c_made = sorted(after["contacts"] - before["contacts"])
        gone = before["images"] - after["images"]
        lost = gone - before["blobs"].keys()
        if lost:
            raise KeyError(f"deleted images not kept: {sorted(lost)}")
        self.gone = {n: before["blobs"][n] for n in gone}
        self.new = {
            n: win.store.get_image(n) for n in after["images"] - before["images"]
        }

    def empty(self):
        return not (
            self.maps
            or self.clip
            or self.snip
            or self.rows
            or self.crows
            or self.trashed
            or self.made
            or self.c_trashed
            or self.c_made
            or self.gone
            or self.new
        )

    def id(self):
        return NUDGE if self.nudge else -1

    def mergeWith(self, other):
        if not (
            self.nudge
            and other.nudge
            and other.board == self.board
            and other.nudge[0] == self.nudge[0]
            and other.nudge[1] - self.nudge[1] < RUN_S
        ):
            return False
        for pid, (_, after) in other.maps.items():
            self.maps[pid] = (self.maps[pid][0], after)
        self.nudge = other.nudge
        return True

    def redo(self):
        if self.fresh:
            self.fresh = False
        else:
            self.win.apply_edit(self, False)

    def undo(self):
        self.win.apply_edit(self, True)

    def check(self, store, undo):
        # a project is live or in the trash; one in neither is lost, and nothing is written for it
        for pid in self.trashed + self.made:
            if not (store.has_project(pid) or store.in_trash(pid)):
                raise KeyError(
                    f"{store.path}: project {pid!r} is gone, cannot "
                    f"{'undo' if undo else 'redo'}"
                )

    @staticmethod
    def _project(store, pid, live):
        # a note that its other holder already restored (or trashed) is left as it is
        if live and not store.has_project(pid):
            store.restore_project(pid)
        elif not live and store.has_project(pid):
            store.delete_project(pid)

    def write(self, store, undo):
        self.check(store, undo)
        i = 0 if undo else 1
        # the zoom a card has now stays; a card the step brings back has its snapshot zoom
        now = {
            c["id"]: c["zoom"]
            for mp in [
                m.load_map(store),
                *(m.load_board(store, b) for b in store.board_ids()),
            ]
            for c in mp["cards"]
        }
        if self.snip:
            store.put_snip_board(self.snip[i])
        for pid, pair in self.maps.items():
            mp = pair[i]
            if mp is not None:
                mp = {
                    **mp,
                    "cards": [
                        {**c, "zoom": now.get(c["id"], c["zoom"])} for c in mp["cards"]
                    ],
                }
            if mp is None:
                store.delete_board(pid)
            elif pid:
                m.save_board(store, pid, mp)
            else:
                m.save_map(store, mp)
        for pid, pair in self.rows.items():
            # a project edited since is left alone: the old copy would erase that work
            if store.get_project(pid) != pair[1 - i]:
                self.win.toast.show_message(f"Not restored: {pid} was edited", 4000)
                continue
            row = pair[i]
            store.put_project(pid, row["html"], row["meta"], row["collapsed"])
        for pid, pair in self.crows.items():
            if store.get_contacts(pid) != pair[1 - i]:
                self.win.toast.show_message(f"Not restored: {pid} was edited", 4000)
                continue
            store.put_contacts(pid, pair[i])
        for pid in self.trashed:
            self._project(store, pid, undo)
        for pid in self.made:
            self._project(store, pid, not undo)
        for pid in self.c_trashed:
            if undo:
                store.restore_contacts(pid)
            else:
                store.delete_contacts(pid)
        for pid in self.c_made:
            if undo:
                store.delete_contacts(pid)
            else:
                store.restore_contacts(pid)
        for name, data in self.gone.items():
            if undo:
                store.put_image(name, data)
            else:
                store.delete_image(name)
        for name, data in self.new.items():
            if undo:
                store.delete_image(name)
            else:
                store.put_image(name, data)

    def amend(self, pid, fields):
        # a page card's fetched title, tags and snapshot arrive after its creation was recorded
        held = [mp for pair in self.maps.values() for mp in pair if mp]
        held += [c for c in self.clip or () if c]
        for pool in held:
            for card in pool["cards"]:
                if card["id"] == pid:
                    card.update(fields)


class MapStack(QUndoStack):
    # a command that cannot be applied fails before the index moves
    def undo(self):
        if self.canUndo():
            cmd = self.command(self.index() - 1)
            cmd.check(cmd.win.store, True)
        super().undo()

    def redo(self):
        if self.canRedo():
            cmd = self.command(self.index())
            cmd.check(cmd.win.store, False)
        super().redo()
