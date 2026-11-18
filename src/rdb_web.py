import ipaddress
import json
import re

from PySide6.QtCore import (
    QBuffer,
    QCoreApplication,
    QIODevice,
    QObject,
    Qt,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import QDesktopServices
from PySide6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineSettings,
    QWebEngineUrlRequestInterceptor,
)
from PySide6.QtNetwork import QHostInfo
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLineEdit,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


import rdb_theme

VIEW_W, VIEW_H = 1024, 1024
SNAP_W, SNAP_H = 512, 512
MAX_TAGS = 12
LOAD_TIMEOUT_MS = 30000
SETTLE_MS = 300

_JS = """(() => {
  const meta = (attr, key) => [...document.querySelectorAll(`meta[${attr}="${key}"]`)]
    .map(e => e.content);
  return JSON.stringify({
    title: document.title,
    description: meta("name", "description")[0] || meta("property", "og:description")[0] || "",
    keywords: meta("name", "keywords"),
    article: meta("property", "article:tag"),
    site: meta("property", "og:site_name")[0] || "",
    h1: [...document.querySelectorAll("h1")].map(e => e.textContent),
  });
})()"""


_profile = []


def download_requested(req):
    # a local file is the desktop's to open, never copied into the download folder
    if req.url().isLocalFile():
        req.cancel()
    else:
        req.accept()


def profile():
    if not _profile:
        # the default profile is off-the-record: a passed human check or a login never sticks
        p = QWebEngineProfile("rdb", QCoreApplication.instance())
        p.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
        )
        # the engine is plain Chromium; sites treat the extra QtWebEngine token as a bot
        p.setHttpUserAgent(re.sub(r" QtWebEngine/\S+", "", p.httpUserAgent()))
        s = p.settings()
        s.setAttribute(QWebEngineSettings.WebAttribute.PluginsEnabled, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.PdfViewerEnabled, True)
        p.downloadRequested.connect(download_requested)
        _profile.append(p)
    return _profile[0]


PRIVATE_SUFFIXES = (
    ".localhost",
    ".local",
    ".lan",
    ".internal",
    ".intranet",
    ".home.arpa",
)


NAT64 = ipaddress.ip_network("64:ff9b::/96")
# bytes 8-11 of ::a.b.c.d, ::ffff:a.b.c.d and ::ffff:0:a.b.c.d
EMBED_PREFIXES = (bytes(4), b"\0\0\xff\xff", b"\xff\xff\0\0")
BLOCKED = object()


def _public_ip(ip):
    if ip.is_multicast:
        return False
    if ip.version == 6:
        # site-local fec0::/10 and the IPv4-embedding forms, which is_global lets through:
        # compatible ::a.b.c.d, mapped ::ffff:a.b.c.d, translated ::ffff:0:a.b.c.d, NAT64
        if ip.is_site_local:
            return False
        embeds = ip.packed[:8] == bytes(8) and ip.packed[8:12] in EMBED_PREFIXES
        if embeds or ip in NAT64:
            return _public_ip(ipaddress.IPv4Address(ip.packed[12:]))
    return ip.is_global


def public_addresses(addresses):
    return bool(addresses) and all(
        _public_ip(ipaddress.ip_address(a.split("%")[0])) for a in addresses
    )


def public_host(url):
    # a browser reads a backslash as a slash, QUrl does not, so the two see different hosts
    if "\\" in url:
        return False
    # QUrl canonicalises %-escapes, full-width IDNA, and the 127.1 and 0x7f.0.0.1 forms
    host = QUrl(url).host().lower().rstrip(".")
    if not host or host == "localhost" or host.endswith(PRIVATE_SUFFIXES):
        return False
    try:
        return _public_ip(ipaddress.ip_address(host))
    except ValueError:
        pass
    # a dotless name is an intranet host; no public TLD is numeric
    last = host.rsplit(".", 1)[-1]
    return "." in host and not (last.isdigit() or last.startswith("0x"))


def _lookup(host, callback):
    def done(info):
        ok = info.error() == QHostInfo.HostInfoError.NoError
        callback([a.toString() for a in info.addresses()] if ok else [])

    QHostInfo.lookupHost(host, done)


class _PublicOnly(QWebEngineUrlRequestInterceptor):
    # every request, subresources and redirects included
    def interceptRequest(self, info):
        url = info.requestUrl()
        if url.scheme() == "data":
            return
        if url.scheme() not in ("http", "https") or not public_host(url.toString()):
            info.block(True)


_check_profile = []


def check_profile():
    if not _check_profile:
        # off the record, no script, public hosts only: claude's links are unreviewed
        p = QWebEngineProfile(QCoreApplication.instance())
        p.setHttpUserAgent(re.sub(r" QtWebEngine/\S+", "", p.httpUserAgent()))
        s = p.settings()
        s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, False)
        s.setAttribute(QWebEngineSettings.WebAttribute.PluginsEnabled, False)
        s.setAttribute(QWebEngineSettings.WebAttribute.PdfViewerEnabled, False)
        p.downloadRequested.connect(lambda req: req.cancel())
        guard = _PublicOnly(p)
        p.setUrlRequestInterceptor(guard)
        _check_profile.append((p, guard))
    return _check_profile[0][0]


def _domain(url):
    return QUrl(url).host().removeprefix("www.")


def _tags(url, info):
    found = [t for k in info["keywords"] for t in k.split(",")]
    found += info["article"]
    found.append(info["site"])
    found.append(_domain(url))
    for h in info["h1"]:
        found += re.findall(r"[^\W\d_]{3,}", h.lower())
    out, seen = [], set()
    for t in (t.strip() for t in found):
        if t and t.casefold() not in seen:
            seen.add(t.casefold())
            out.append(t)
    return out[:MAX_TAGS]


class _Fetch(QObject):
    def __init__(self, url, callback, full, prof):
        super().__init__()
        self.url, self.callback, self.full, self.done = url, callback, full, False
        self.view = QWebEngineView()
        self.view.setPage(QWebEnginePage(prof, self.view))
        self.view.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        self.view.resize(VIEW_W, VIEW_H)
        self.view.loadFinished.connect(self._loaded)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._fail)
        self.view.show()
        self.view.load(QUrl(url))
        self.timer.start(LOAD_TIMEOUT_MS)

    def _finish(self, result):
        if self.done:
            return
        self.done = True
        self.timer.stop()
        self.view.deleteLater()
        self.callback(result)

    def _fail(self):
        d = _domain(self.url)
        self._finish(
            {
                "url": self.url,
                "title": self.url,
                "description": "",
                "tags": [d] if d else [],
                "png": b"",
            }
            if self.full
            else None
        )

    def _loaded(self, ok):
        if self.done:
            return
        if not ok:
            self._fail()
        elif self.full:
            self.view.page().runJavaScript(_JS, self._collected)
        else:
            self._finish(self.view.title() or self.url)

    def _collected(self, raw):
        if self.done:
            return
        if not raw:
            self._fail()
            return
        self.info = json.loads(raw)
        # grab() right after loadFinished returns a blank frame; Chromium needs a moment to paint
        QTimer.singleShot(SETTLE_MS, self._snap)

    def _snap(self):
        if self.done:
            return
        info = self.info
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        snap = self.view.grab().scaled(
            SNAP_W,
            SNAP_H,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        snap.save(buf, "PNG")
        self._finish(
            {
                "url": self.url,
                "title": info["title"].strip() or self.url,
                "description": info["description"].strip(),
                "tags": _tags(self.url, info),
                "png": bytes(buf.data()),
            }
        )


def fetch_page(url, callback):
    return _Fetch(url, callback, True, profile())


def fetch_title(url, callback):
    return _Fetch(url, callback, False, profile())


# ponytail: resolves once before the load; a DNS-rebinding host can still flip to a LAN
# address between this lookup and Chromium's own. Closing that needs a resolver-level hook.
def check_title(url, callback):
    # the caller keeps this list, and so the fetch that starts after the lookup, alive
    held = []

    def resolved(addresses):
        if not addresses:
            callback(None)
        elif not public_addresses(addresses):
            callback(BLOCKED)
        else:
            held.append(_Fetch(url, callback, False, check_profile()))

    _lookup(QUrl(url).host(), resolved)
    return held


class WebPane(QWidget):
    closed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.view = QWebEngineView()
        page = QWebEnginePage(profile(), self.view)
        # target=_blank and window.open land in this pane, like a single-tab browser
        page.newWindowRequested.connect(lambda req: req.openIn(page))
        self.view.setPage(page)
        # the colour shown before a page paints
        rdb_theme.follow(
            self, lambda w: page.setBackgroundColor(w.palette().window().color())
        )
        self.address = QLineEdit()
        self.address.returnPressed.connect(self._go)
        style = self.style()
        self.back = self._button(
            style.standardIcon(QStyle.StandardPixmap.SP_ArrowBack),
            self.view.back,
            "Back",
        )
        self.forward = self._button(
            style.standardIcon(QStyle.StandardPixmap.SP_ArrowForward),
            self.view.forward,
            "Forward",
        )
        self.reload = self._button(
            style.standardIcon(QStyle.StandardPixmap.SP_BrowserReload),
            self.view.reload,
            "Reload",
        )
        self.external = self._button("Ext", self._open_external, "Open in Browser")
        self.close_button = self._button("x", self._close, "Close")
        strip = QHBoxLayout()
        strip.setContentsMargins(2, 1, 2, 1)
        strip.setSpacing(2)
        for b in (self.back, self.forward, self.reload):
            strip.addWidget(b)
        strip.addWidget(self.address, 1)
        for b in (self.external, self.close_button):
            strip.addWidget(b)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addLayout(strip)
        lay.addWidget(self.view, 1)
        self.view.urlChanged.connect(self._sync_history)
        self._sync_history()

    def _button(self, face, slot, tip):
        b = QToolButton()
        b.setAutoRaise(True)
        b.setToolTip(tip)
        if isinstance(face, str):
            b.setText(face)
        else:
            b.setIcon(face)
        b.clicked.connect(slot)
        return b

    def _sync_history(self, *_):
        h = self.view.history()
        self.back.setEnabled(h.canGoBack())
        self.forward.setEnabled(h.canGoForward())
        self.address.setText(self.view.url().toString())
        self.address.setCursorPosition(0)

    def _go(self):
        self.view.load(QUrl.fromUserInput(self.address.text().strip()))
        self.view.setFocus()

    def _open_external(self):
        QDesktopServices.openUrl(self.view.url())

    def _close(self):
        self.hide()
        self.closed.emit()

    def show_url(self, url):
        self.view.load(QUrl(url))
        self.show()
