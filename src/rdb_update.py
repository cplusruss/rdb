import hashlib
import json
import os
import re
import time
import urllib.request

from PySide6.QtCore import QThread, Signal

API_LATEST = "https://api.github.com/repos/cplusruss/rdb/releases/latest"
ASSET = "rdb.exe"
TIMEOUT = 30
CHUNK = 1 << 16

_VERSION = re.compile(r"v?(\d+)\.(\d+)\.(\d+)", re.ASCII)
_DIGEST = re.compile(r"sha256:([0-9a-f]{64})")


class Cancelled(Exception):
    pass


def parse_version(s):
    m = _VERSION.fullmatch(s)
    if m is None:
        raise ValueError(f"bad version {s!r}")
    return tuple(int(g) for g in m.groups())


def latest_release(data):
    ver = parse_version(data.get("tag_name", ""))
    found = [a for a in data.get("assets", []) if a.get("name") == ASSET]
    if len(found) != 1:
        raise ValueError(f"release has {len(found)} assets named {ASSET}")
    a = found[0]
    url, size, digest = (a.get(k) for k in ("browser_download_url", "size", "digest"))
    if not isinstance(url, str) or not url:
        raise ValueError(f"{ASSET} has no download url")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise ValueError(f"{ASSET} size {size!r}")
    m = _DIGEST.fullmatch(digest) if isinstance(digest, str) else None
    if m is None:
        raise ValueError(f"{ASSET} digest {digest!r} is not sha256")
    return ver, url, size, m.group(1)


def _open(url):
    req = urllib.request.Request(url, headers={"User-Agent": "rdb-updater"})
    return urllib.request.urlopen(req, timeout=TIMEOUT)


def fetch_latest():
    with _open(API_LATEST) as r:
        return latest_release(json.load(r))


def download(url, dest, size, sha256, progress=None, cancelled=None):
    h = hashlib.sha256()
    done = 0
    try:
        with _open(url) as r, open(dest, "wb") as f:
            while chunk := r.read(CHUNK):
                if cancelled is not None and cancelled():
                    raise Cancelled
                f.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if progress is not None:
                    progress(done, size)
        if done != size:
            raise ValueError(f"downloaded {done} bytes, release says {size}")
        if h.hexdigest() != sha256:
            raise ValueError("sha256 mismatch")
    except BaseException:
        if os.path.exists(dest):
            os.remove(dest)
        raise


def swap(exe, new):
    old = exe + ".old"
    if not os.path.isfile(new):
        raise FileNotFoundError(new)
    if os.path.exists(old):
        os.remove(old)
    os.rename(exe, old)
    try:
        os.rename(new, exe)
    except OSError:
        os.rename(old, exe)
        raise


def cleanup(exe):
    old = exe + ".old"
    # a restarted instance can start while its parent still holds the file
    for _ in range(10):
        if not os.path.exists(old):
            return
        try:
            os.remove(old)
            return
        except PermissionError:
            time.sleep(0.1)
    os.remove(old)


class Job(QThread):
    progress = Signal(int, int)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self.fn = fn
        self.stop = False

    def run(self):
        try:
            result = self.fn(self.progress.emit, lambda: self.stop)
        except Cancelled:
            return
        except Exception as e:
            self.failed.emit(str(e))
            return
        self.done.emit(result)
