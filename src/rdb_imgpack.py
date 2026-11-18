import io
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor

from PIL import Image, ImageChops, PngImagePlugin
from PySide6.QtCore import QObject, QTimer

PNG_SIG = b"\x89PNG\r\n\x1a\n"
# metadata chunks (an ICC profile) beyond Pillow's default 1 MB limit are what packing drops
PngImagePlugin.MAX_TEXT_CHUNK = 64 << 20
MODES = ("1", "L", "LA", "P", "RGB", "RGBA")


def _encode(im):
    out = io.BytesIO()
    im.save(out, "PNG", optimize=True, compress_level=9)
    return out.getvalue()


def _grey(rgba):
    r, g, b, _ = rgba.split()
    return ImageChops.difference(r, g).getbbox() is None and (
        ImageChops.difference(g, b).getbbox() is None
    )


def _palette(rgba, colors):
    # exact: one entry per distinct RGBA value
    keys = {}
    pal, alpha = bytearray(), bytearray()
    for _, (r, g, b, a) in colors:
        keys[int.from_bytes(bytes((r, g, b, a)), sys.byteorder)] = len(keys)
        pal += bytes((r, g, b))
        alpha.append(a)
    index = bytes(map(keys.__getitem__, memoryview(rgba.tobytes()).cast("I")))
    out = Image.frombytes("P", rgba.size, index)
    out.putpalette(bytes(pal))
    if min(alpha) < 255:
        out.info["transparency"] = bytes(alpha)
    return out


def _candidates(rgba):
    # the same pixels in the leaner modes the image qualifies for
    solid = rgba.getchannel("A").getextrema() == (255, 255)
    yield rgba.convert("RGB") if solid else rgba
    if (colors := rgba.getcolors(256)) is not None:
        yield _palette(rgba, colors)
    if _grey(rgba):
        yield rgba.convert("L") if solid else rgba.convert("LA")


def pack(data):
    # the smallest PNG with exactly the same RGBA pixels; anything else comes back as given.
    # A PNG that cannot be decoded raises
    if not data.startswith(PNG_SIG):
        return data
    if len(data) > 24 and data[24] > 8:
        # IHDR bit depth: Pillow decodes 16-bit channels as 8-bit, which would degrade them
        return data
    im = Image.open(io.BytesIO(data))
    if im.format != "PNG" or im.mode not in MODES or getattr(im, "n_frames", 1) > 1:
        return data
    im.load()
    rgba = im.convert("RGBA")
    ref = rgba.tobytes()
    best = data
    for cand in _candidates(rgba):
        out = _encode(cand)
        if len(out) >= len(best):
            continue
        back = Image.open(io.BytesIO(out))
        if back.convert("RGBA").tobytes() == ref:
            best = out
    return best


VACUUM_BYTES = 1 << 20


def size_text(n):
    return f"{n / (1 << 20):.1f} MB" if n >= 1 << 20 else f"{max(n, 1) / 1024:.0f} KB"


class PackPass(QObject):
    # packs the images of a store that no write has packed yet, one at a time: Pillow runs in a
    # worker thread on bytes only, and this thread reads and writes the store. A saving of
    # VACUUM_BYTES or more is vacuumed once idle() says no edit is waiting to be saved. A put_image
    # re-triggers the pass. A failed vacuum ends the pass and leaves its message in .error
    def __init__(self, store, idle, done, parent=None):
        super().__init__(parent)
        self.store, self.idle, self.done = store, idle, done
        self.saved = 0
        self.failed = []
        self.error = None
        self._names = []
        self._job = None
        self._pool = None
        self._closed = False
        self._timer = QTimer(self)
        self._timer.setInterval(20)
        self._timer.timeout.connect(self._tick)
        store.image_put = self.kick
        self.kick()

    def kick(self):
        if self._closed:
            return
        self._names = self.store.unpacked_images()
        if self._pool is None:
            self._pool = ThreadPoolExecutor(max_workers=1)
        self._timer.start()

    def _halt(self):
        self._timer.stop()
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)
            self._pool = None

    def stop(self):
        self._closed = True
        if self.store.image_put == self.kick:
            self.store.image_put = None
        self._halt()

    def _tick(self):
        if self._job is not None:
            name, old, fut = self._job
            if not fut.done():
                return
            self._job = None
            try:
                new = fut.result()
            except (OSError, ValueError, Image.DecompressionBombError):
                # a PNG Pillow cannot read stays as stored, and is not tried again
                new = old
                self.failed.append(name)
            try:
                if self.store.replace_packed(name, old, new):
                    self.saved += len(old) - len(new)
            except KeyError:
                pass
        while self._names:
            name = self._names.pop(0)
            try:
                old = self.store.get_image(name)
            except KeyError:
                continue
            self._job = (name, old, self._pool.submit(pack, old))
            return
        if self.saved >= VACUUM_BYTES:
            if not self.idle():
                return
            try:
                self.store.vacuum()
            except sqlite3.OperationalError as e:
                self.error = str(e)
        saved, self.saved = self.saved, 0
        self._halt()
        self.done(saved)
