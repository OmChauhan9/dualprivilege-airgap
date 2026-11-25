"""Fetcher (no LLM). Produces provenance that no model can spoof."""

from __future__ import annotations

import codecs
import ipaddress
import re
from collections.abc import Callable, Iterator
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


_HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", re.I)


def is_hostname(host: str) -> bool:
    """A well-formed DNS name or IP address. Python's URL parsing accepts any text as a host
    (spaces included), so text a page chose may name a host in an error only if this holds."""
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        pass
    labels = host.removesuffix(".").split(".")
    return len(host) <= 253 and all(_HOST_LABEL.fullmatch(label) for label in labels)


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


# A browser takes the charset from a byte-order mark first, then the Content-Type header, then a
# <meta> tag in the first 1024 bytes, and may switch to a later <meta> or guess when there is none.
# Only UTF-8 and windows-1252 are decoded, and only when a browser is sure to pick the same one.
# Any other charset (EBCDIC, UTF-16, UTF-7, ISO-2022, Shift_JIS) could make the markup the
# sanitizer parses differ from what a browser renders, so the page is refused rather than guessed.
# The labels are the WHATWG Encoding Standard's, not Python's codec aliases.
_WHATWG_LABELS: dict[str, str] = {
    label: encoding
    for encoding, labels in (
        ("utf-8", "unicode-1-1-utf-8 unicode11utf8 unicode20utf8 utf-8 utf8 x-unicode20utf8"),
        (
            "windows-1252",
            "ansi_x3.4-1968 ascii cp1252 cp819 csisolatin1 ibm819 iso-8859-1 iso-ir-100 iso8859-1"
            " iso88591 iso_8859-1 iso_8859-1:1987 l1 latin1 us-ascii windows-1252 x-cp1252",
        ),
    )
    for label in labels.split()
}
# In a <meta>, UTF-16 means UTF-8 and x-user-defined means windows-1252 (HTML prescan rule).
_WHATWG_META_LABELS = (
    _WHATWG_LABELS
    | {
        label: "utf-8"
        for label in "csunicode iso-10646-ucs-2 ucs-2 unicode unicodefeff utf-16 utf-16le"
        " unicodefffe utf-16be".split()
    }
    | {"x-user-defined": "windows-1252"}
)
# Python's cp1252 leaves five bytes undefined; WHATWG maps them to the C1 control of that number.
_WINDOWS_1252_HIGH = str.maketrans(
    {chr(b): bytes([b]).decode("cp1252", "replace") for b in range(0x80, 0xA0)}
    | {chr(b): chr(b) for b in (0x81, 0x8D, 0x8F, 0x90, 0x9D)}
)
_UTF8_BOM = b"\xef\xbb\xbf"
_OTHER_BOMS = (b"\xff\xfe", b"\xfe\xff", b"\x00\x00\xfe\xff", b"+/v")
_HTML_COMMENT = re.compile(rb"<!--.*?(?:-->|$)", re.S)
_DECLARATION = rb"""charset[\t\n\f\r ]*=[\t\n\f\r ]*(?:"([^"]*)"|'([^']*)'|([^\t\n\f\r >"']*))"""
_ANY_DECLARATION = re.compile(_DECLARATION, re.I)
_META_DECLARATION = re.compile(rb"<meta[\t\n\f\r /][^>]*?" + _DECLARATION, re.I)
# Bytes a browser's guess could read as something other than ASCII.
_NOT_PLAIN_ASCII = re.compile(rb"[\x00\x1b\x80-\xff]")
_TOKEN = r"[!#$%&'*+.^_`|~0-9A-Za-z-]+"
_MIME_PARAM = rf"[\t ]*;[\t ]*({_TOKEN})=((?:(?![;,\"\\])[\x21-\x7e])+|\"[^\"\\]*\")"
_SIMPLE_MIME = re.compile(rf"[\t ]*{_TOKEN}/{_TOKEN}(?:{_MIME_PARAM})*[\t ]*;?[\t ]*")


def _unsupported(label: str) -> FetchError:
    try:
        name = codecs.lookup(label).name
    except (LookupError, ValueError):
        name = None
    if name is None or name in ("utf-8", "cp1252", "iso8859-1", "ascii"):
        # Unknown to browsers, even where Python has an alias for it.
        return FetchError("unsupported charset: the page declares an unknown one")
    return FetchError(f"unsupported charset {name!r}")


def _encoding(label: str, labels: dict[str, str] = _WHATWG_LABELS) -> str:
    encoding = labels.get(label.strip("\t\n\f\r ").lower())
    if encoding is None:
        raise _unsupported(label)
    return encoding


def _header_label(content_type: str | None) -> str | None:
    if content_type is None or "charset" not in content_type.lower():
        return None
    # Browsers parse Content-Type by the Fetch Standard, which differs from httpx on odd headers
    # (repeated types, a space before "=", */*). Only a plain one is read.
    if not _SIMPLE_MIME.fullmatch(content_type) or content_type.split(";")[0].strip() == "*/*":
        raise FetchError("unsupported charset: the Content-Type header is not a plain one")
    for name, value in re.findall(_MIME_PARAM, content_type):
        if name.lower() == "charset":
            return value.strip('"')
    return None


def _labels(matches: Iterator[re.Match[bytes]]) -> list[str]:
    found = (b"".join(m.groups(b"")).decode("latin-1") for m in matches)
    return [label for label in found if label.strip("\t\n\f\r ")]  # browsers skip empty labels


def _decodable_charset(content_type: str | None, body: bytes) -> str:
    if body.startswith(_UTF8_BOM):
        return "utf-8-sig"
    if body.startswith(_OTHER_BOMS):
        raise FetchError("unsupported charset: the page starts with a non-UTF-8 byte-order mark")
    header = _header_label(content_type)
    if header is not None:
        return _encoding(header)
    # A browser may act on any <meta> it meets while parsing, so every declaration must agree,
    # and it surely sees only one in the first 1024 bytes outside a comment.
    found = _labels(_ANY_DECLARATION.finditer(body))
    declared = {_encoding(label, _WHATWG_META_LABELS) for label in found}
    if len(declared) > 1:
        raise FetchError("unsupported charset: the page declares conflicting ones")
    early = _labels(_META_DECLARATION.finditer(_HTML_COMMENT.sub(b"", body[:1024])))
    if declared and early:
        return declared.pop()
    if _NOT_PLAIN_ASCII.search(body):
        raise FetchError("unsupported charset: the page declares none a browser is sure to use")
    return "utf-8"


def _decode(body: bytes, charset: str) -> str:
    if charset == "windows-1252":
        return body.decode("latin-1").translate(_WINDOWS_1252_HIGH)
    return body.decode(charset, errors="replace")


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
            try:
                resp = self.client.get(current, follow_redirects=False)
            except httpx.RemoteProtocolError as exc:
                # httpx quotes what the server sent, such as a malformed Location header.
                raise FetchError("the server sent a malformed response") from exc
            visited.append(resp)
            location = resp.headers.get("location")
            if not (resp.is_redirect and location):
                break
            current = urljoin(str(resp.url), location)
            if urlsplit(current).scheme not in ("http", "https"):
                raise FetchError("redirect to a url that is not http or https")
            if check_hop is not None:
                check_hop(current)
        else:
            raise FetchError(f"too many redirects (more than {MAX_REDIRECTS})")
        content = resp.content[:MAX_BYTES]
        body = _decode(content, _decodable_charset(resp.headers.get("content-type"), content))
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
