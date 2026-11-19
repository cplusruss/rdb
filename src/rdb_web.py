import ipaddress
import re
import socket
import threading
import urllib.parse
import urllib.request
from html.parser import HTMLParser

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtNetwork import QHostInfo

MAX_TAGS = 12
LOAD_TIMEOUT_MS = 30000
MAX_BYTES = 2 * 1024 * 1024
# a script-free fetch shows as a bot to sites that gate on the agent string
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

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


def _squash(text):
    return " ".join(text.split())


class _Head(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title = self.og_title = self.site = ""
        self.description, self.og_description = [], []
        self.keywords, self.article, self.h1 = [], [], []
        self._in_title, self._h1_depth = False, 0
        self._text = []

    def handle_starttag(self, tag, attrs):
        if tag == "title" and not self.title:
            self._in_title, self._text = True, []
        elif tag == "h1":
            if not self._h1_depth:
                self._text = []
            self._h1_depth += 1
        elif tag == "meta":
            a = {k.lower(): (v or "") for k, v in attrs}
            name, prop, content = (
                a.get("name", "").lower(),
                a.get("property", "").lower(),
                a.get("content", ""),
            )
            if name == "description":
                self.description.append(content)
            elif name == "keywords":
                self.keywords.append(content)
            elif prop == "og:description":
                self.og_description.append(content)
            elif prop == "article:tag":
                self.article.append(content)
            elif prop == "og:site_name" and not self.site:
                self.site = content
            elif prop == "og:title" and not self.og_title:
                self.og_title = content

    def handle_endtag(self, tag):
        if tag == "title" and self._in_title:
            self._in_title = False
            self.title = _squash("".join(self._text))
        elif tag == "h1" and self._h1_depth:
            self._h1_depth -= 1
            if not self._h1_depth:
                self.h1.append("".join(self._text))

    def handle_data(self, data):
        if self._in_title or self._h1_depth:
            self._text.append(data)


class _PublicRedirects(urllib.request.HTTPRedirectHandler):
    # redirects are requests too: a public page must not bounce the fetch onto this network
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not public_host(newurl):
            raise OSError(f"redirect to a non-public host: {newurl}")
        # a public-looking name can still resolve to this network; this runs on the worker
        host = urllib.parse.urlsplit(newurl).hostname
        found = {a[4][0] for a in socket.getaddrinfo(host, None)}
        if not public_addresses(found):
            raise OSError(f"redirect to a non-public address: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _download(url, public_only):
    if url.split(":", 1)[0].lower() not in ("http", "https"):
        raise ValueError(f"not an http url: {url}")
    if public_only and not public_host(url):
        raise OSError(f"not a public host: {url}")
    handlers = [_PublicRedirects] if public_only else []
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with opener.open(req, timeout=LOAD_TIMEOUT_MS / 1000) as r:
        data = r.read(MAX_BYTES)
        charset = r.headers.get_content_charset()
    try:
        return data.decode(charset or "utf-8", errors="replace")
    except LookupError:
        return data.decode("utf-8", errors="replace")


def _parse(url, text):
    head = _Head()
    head.feed(text)
    head.close()
    return {
        "url": url,
        "title": head.title or _squash(head.og_title) or url,
        "description": next(
            (
                d.strip()
                for d in head.description[:1] + head.og_description[:1]
                if d.strip()
            ),
            "",
        ),
        "tags": _tags(
            url,
            {
                "keywords": head.keywords,
                "article": head.article,
                "site": head.site,
                "h1": head.h1,
            },
        ),
        "png": b"",
    }


class _Fetch(QObject):
    done = Signal(object)

    def __init__(self, url, callback, full, public_only):
        super().__init__()
        self.url, self.callback, self.full = url, callback, full
        # emitted from the worker, delivered on this object's (the GUI) thread
        self.done.connect(self._finish)
        threading.Thread(target=self._work, args=(public_only,), daemon=True).start()

    def _work(self, public_only):
        try:
            page = _parse(self.url, _download(self.url, public_only))
        # any failure, whatever its type, must still reach the callback, or its caller stays busy
        except Exception:
            d = _domain(self.url)
            self.done.emit(
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
            return
        self.done.emit(page if self.full else page["title"])

    def _finish(self, result):
        self.callback(result)


def fetch_page(url, callback):
    return _Fetch(url, callback, True, False)


def fetch_title(url, callback):
    return _Fetch(url, callback, False, False)


# ponytail: resolves once before the load; a DNS-rebinding host can still flip to a LAN
# address between this lookup and urllib's own. Closing that needs a resolver-level hook.
def check_title(url, callback):
    # the caller keeps this list, and so the fetch that starts after the lookup, alive
    held = []

    def resolved(addresses):
        if not addresses:
            callback(None)
        elif not public_addresses(addresses):
            callback(BLOCKED)
        else:
            held.append(_Fetch(url, callback, False, True))

    _lookup(QUrl(url).host(), resolved)
    return held
