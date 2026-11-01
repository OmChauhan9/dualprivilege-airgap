"""Fetcher (no LLM). Produces provenance that no model can spoof."""

from __future__ import annotations

import codecs
import ipaddress
import re
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

from airgap.core.values import Handle

MAX_BYTES = 1_000_000
MAX_REDIRECTS = 5
# URLs inside prose: stop before trailing sentence punctuation.
URL_RE = re.compile(r"https?://[^\s)>\"']*[^\s)>\"'.,;:!?]")
_MULTI_PART_SUFFIXES = {"co.uk", "org.uk", "com.au", "co.jp", "com.br"}


class FetchError(RuntimeError):
    pass


# Called with each redirect target before it is requested; raise FetchError to stop.
HopCheck = Callable[[str], None]


def registrable_domain(host: str) -> str:
    """Last two labels, or three for known multi-part suffixes. Offline and deterministic."""
    host = host.strip("[]")
    try:
        return str(ipaddress.ip_address(host))  # an IP address is its own site
    except ValueError:
        pass
    labels = [p for p in host.lower().strip(".").split(".") if p]
    if len(labels) <= 2:
        return ".".join(labels)
    if ".".join(labels[-2:]) in _MULTI_PART_SUFFIXES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def domain_of_url(url: str) -> str:
    return registrable_domain(urlsplit(url).hostname or "")


@dataclass(frozen=True)
class Provenance:
    requested_url: str
    final_url: str
    registrable_domain: str
    redirect_chain: tuple[str, ...]
    status: int
    content_type: str


@dataclass(frozen=True)
class FetchedDoc:
    handle: Handle
    raw: str
    provenance: Provenance


# ASCII-compatible charsets that browsers decode the same way. A browser takes the charset from a
# byte-order mark first, then the Content-Type header, then a <meta> tag in the first 1024 bytes.
# Any other charset (EBCDIC, UTF-16, UTF-7, ISO-2022) could make the markup the sanitizer parses
# differ from what a browser renders, so the page is refused rather than guessed at.
_BROWSER_CHARSETS = {"utf-8": "utf-8", "ascii": "utf-8", "iso8859-1": "cp1252", "cp1252": "cp1252"}
_UTF8_BOM = b"\xef\xbb\xbf"
_OTHER_BOMS = (b"\xff\xfe", b"\xfe\xff", b"\x00\x00\xfe\xff", b"+/v")
_META_CHARSET = re.compile(rb"""<meta[^>]*?charset\s*=\s*["']?\s*([^\s"'>;/]+)""", re.I)


def _decodable_charset(header_charset: str | None, body: bytes) -> str:
    if body.startswith(_UTF8_BOM):
        return "utf-8-sig"
    if body.startswith(_OTHER_BOMS):
        raise FetchError("unsupported charset: the page starts with a non-UTF-8 byte-order mark")
    declared = header_charset
    if not declared:
        meta = _META_CHARSET.search(body[:1024])
        declared = meta.group(1).decode("ascii", "replace") if meta else None
    if not declared:
        return "utf-8"
    try:
        name = codecs.lookup(declared).name
    except LookupError:
        name = declared
    if name not in _BROWSER_CHARSETS:
        raise FetchError(f"unsupported charset {declared!r}")
    return _BROWSER_CHARSETS[name]


class Fetcher:
    def __init__(self, client: httpx.Client) -> None:
        self.client = client
        self._n = 0

    def fetch(self, url: str, check_hop: HopCheck | None = None) -> FetchedDoc:
        """Fetch `url`, following redirects by hand so every hop is checked before it is sent."""
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise FetchError(f"unsupported url: {url}")
        visited: list[httpx.Response] = []
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            resp = self.client.get(current, follow_redirects=False)
            visited.append(resp)
            location = resp.headers.get("location")
            if not (resp.is_redirect and location):
                break
            current = urljoin(str(resp.url), location)
            if urlsplit(current).scheme not in ("http", "https"):
                raise FetchError(f"redirect to unsupported url: {current}")
            if check_hop is not None:
                check_hop(current)
        else:
            raise FetchError(f"too many redirects (more than {MAX_REDIRECTS})")
        charset = _decodable_charset(resp.charset_encoding, resp.content[:MAX_BYTES])
        body = resp.content[:MAX_BYTES].decode(charset, errors="replace")
        final_url = str(resp.url)
        prov = Provenance(
            requested_url=url,
            final_url=final_url,
            registrable_domain=domain_of_url(final_url),
            redirect_chain=tuple(str(r.url) for r in visited),
            status=resp.status_code,
            content_type=resp.headers.get("content-type", ""),
        )
        self._n += 1
        handle = Handle(id=f"doc_{self._n}", kind="document", origin=prov.registrable_domain)
        return FetchedDoc(handle=handle, raw=body, provenance=prov)
