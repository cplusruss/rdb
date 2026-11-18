import getpass
import json
import os
import sys
from pathlib import Path, PureWindowsPath

from PySide6.QtCore import QProcess, QUrl
from PySide6.QtNetwork import QLocalSocket

import rdb_paths
from rdb_model import path_local

CONNECT_MS = 200
REPLY_MS = 3000
SPOT_KEYS = ("path", "page", "page_name", "cells", "label")
ENCODED = QUrl.ComponentFormattingOption.FullyEncoded


def pipe_name():
    # rdoc's single-instance pipe (rdoc app\core\instance.py)
    return os.environ.get("RDOC_INSTANCE") or f"rdoc-{getpass.getuser()}"


def _json(raw):
    try:
        json.loads(raw.decode("utf-8"))
    except ValueError:
        return False
    return True


def _ask(message, complete=bool):
    # the reply bytes, or None when nothing is listening: the one quiet case. A Windows pipe
    # does not report the server hanging up, so the reply ends when complete(reply) says so
    sock = QLocalSocket()
    sock.connectToServer(pipe_name())
    if not sock.waitForConnected(CONNECT_MS):
        return None
    sock.write(message)
    sock.waitForBytesWritten(REPLY_MS)
    reply = b""
    while not complete(reply) and sock.waitForReadyRead(REPLY_MS):
        reply += bytes(sock.readAll())
    reply += bytes(sock.readAll())
    connected = sock.state() == QLocalSocket.LocalSocketState.ConnectedState
    sock.abort()
    if not reply and connected:
        raise TimeoutError("rdoc did not answer")
    return reply


def spot():
    raw = _ask(json.dumps({"cmd": "spot"}).encode("utf-8"), _json)
    if raw is None:
        return None
    reply = json.loads(raw.decode("utf-8"))
    if not isinstance(reply, dict) or (reply and not set(SPOT_KEYS) <= set(reply)):
        raise ValueError(f"rdoc spot reply: {raw!r}")
    if reply and not (
        isinstance(reply["cells"], list)
        and all(isinstance(reply[k], str) for k in SPOT_KEYS if k != "cells")
    ):
        raise ValueError(f"rdoc spot reply: {raw!r}")
    return reply


def goto(path, page, cells):
    message = {"cmd": "goto", "path": path, "page": page, "cells": cells}
    raw = _ask(json.dumps(message).encode("utf-8"))
    if raw is None:
        return False
    if raw != b"ok":
        raise ValueError(f"rdoc goto reply: {raw!r}")
    return True


def _enc(item):
    return bytes(QUrl.toPercentEncoding(item)).decode("ascii")


def spot_href(reply):
    if not reply:
        return ""
    url = QUrl.fromLocalFile(reply["path"])
    frag = f"page={_enc(reply['page'])}"
    if reply["cells"]:
        frag += "&cells=" + ",".join(_enc(c) for c in reply["cells"])
    url.setFragment(frag, QUrl.ParsingMode.StrictMode)
    return url.toString(ENCODED)


def spot_label(reply):
    if not reply:
        return ""
    label = f"{PureWindowsPath(reply['path']).stem} / {reply['page_name']}"
    return f"{label} / {reply['label']}" if reply["label"] else label


RDOC_EXTS = (".rdoc", ".drawio", ".dio", ".xml")


def parse_href(href):
    # (path, page, cells) of a file link whose fragment names a page, else None
    url = QUrl(href)
    if not url.isLocalFile() or not url.hasFragment():
        return None
    items = {}
    for part in url.fragment(ENCODED).split("&"):
        key, _, value = part.partition("=")
        items[key] = value
    if "page" not in items or Path(url.toLocalFile()).suffix.lower() not in RDOC_EXTS:
        return None
    dec = QUrl.fromPercentEncoding
    cells = [dec(c.encode("ascii")) for c in items.get("cells", "").split(",") if c]
    return path_local(href), dec(items["page"].encode("ascii")), cells


def launch_command(rdoc_path, path, page, cells):
    if not Path(rdoc_path).is_file():
        raise FileNotFoundError(rdoc_path)
    if not Path(path).is_file():
        raise FileNotFoundError(path)
    args = [path, "--page", page] + (["--cells", ",".join(cells)] if cells else [])
    ext = Path(rdoc_path).suffix.lower()
    if ext == ".py":
        if rdb_paths.FROZEN:
            raise ValueError(f"rdoc_path {rdoc_path!r} is a script: set it to rdoc.exe")
        return os.path.join(os.path.dirname(sys.executable), "pythonw.exe"), [
            rdoc_path,
            *args,
        ]
    if ext == ".exe":
        return rdoc_path, args
    raise ValueError(f"rdoc_path {rdoc_path!r} is neither a .py nor an .exe")


def start_detached(program, args):
    return QProcess.startDetached(program, args)


def launch(rdoc_path, path, page, cells):
    program, args = launch_command(rdoc_path, path, page, cells)
    if not start_detached(program, args):
        raise OSError(f"could not start {program}")
