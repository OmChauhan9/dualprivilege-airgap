"""Fetcher (no LLM). Produces provenance that no model can spoof."""

from __future__ import annotations

import codecs
import ipaddress
import re
import time
import zlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

from airgap.core.values import Handle

MAX_BYTES = 1_000_000
MAX_REDIRECTS = 5
# Wall clock for one fetch: every hop, its connect, headers and body. Each request's timeouts are
# what is left of it, so no transport setting can extend it. Read at call time, so a test can
# lower it.
FETCH_DEADLINE_S = 20.0
# Every header a site receives. Built by the guard; nothing comes from the caller's code.
REQUEST_HEADERS = (
    ("user-agent", "airgap/0.1"),
    ("accept", "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5"),
    ("accept-encoding", "identity"),
)
_TOO_SLOW = "the page took too long"
# URLs inside prose: stop before trailing sentence punctuation.
URL_RE = re.compile(r"https?://[^\s)>\"']*[^\s)>\"'.,;:!?]")
_MULTI_PART_SUFFIXES = {"co.uk", "org.uk", "com.au", "co.jp", "com.br"}


class FetchError(RuntimeError):
    pass


class AddressRefused(FetchError):
    """A host resolved to a non-public address; raised by the guard's transport at connect time.
    A redirect hop's host can be one the page chose, so `host` names it only when it is a
    well-formed host name."""

    def __init__(self, host: str) -> None:
        self.host = host if is_hostname(host) else "a host that is not a valid name"
        super().__init__(f"{self.host} resolves to a non-public address")


# Called with (the URL that redirected, the redirect target) before the target is requested;
# raise FetchError to stop.
HopCheck = Callable[[str, str], None]


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
_ANY_DECLARATION = re.compile(
    rb"""charset[\t\n\f\r ]*=[\t\n\f\r ]*(?:"([^"]*)"|'([^']*)'|([^\t\n\f\r >"']*))""", re.I
)
_SPACE = b"\t\n\f\r "
# Chrome reads the content of these as text when it looks for a <meta>; the prescan reads markup.
_TEXT_ELEMENTS = {
    b"iframe",
    b"noembed",
    b"noframes",
    b"noscript",
    b"plaintext",
    b"script",
    b"style",
    b"textarea",
    b"title",
    b"xmp",
}
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


def _lower(b: int) -> int:
    return b + 0x20 if 0x41 <= b <= 0x5A else b


def _get_attribute(data: bytes, i: int) -> tuple[bytes | None, bytes, int]:
    """HTML Standard "get an attribute" (prescan). A None name means the tag ended at `i`."""
    while data[i] in b"\t\n\f\r /":
        i += 1
    if data[i] == 0x3E:
        return None, b"", i
    name, value = bytearray(), bytearray()
    while True:
        b = data[i]
        if b == 0x3D and name:
            i += 1
            break
        if b in _SPACE:
            while data[i] in _SPACE:
                i += 1
            if data[i] != 0x3D:
                return bytes(name), b"", i
            i += 1
            break
        if b in b"/>":
            return bytes(name), b"", i
        name.append(_lower(b))
        i += 1
    while data[i] in _SPACE:
        i += 1
    quote = data[i]
    if quote in b"\"'":
        while data[i := i + 1] != quote:
            value.append(_lower(data[i]))
        return bytes(name), bytes(value), i + 1
    if quote == 0x3E:
        return bytes(name), b"", i
    while data[i] not in _SPACE and data[i] != 0x3E:
        value.append(_lower(data[i]))
        i += 1
    return bytes(name), bytes(value), i


def _content_charset(content: bytes) -> str | None:
    """HTML Standard "extract a character encoding from a meta element"; returns the label."""
    i = 0
    while (i := content.find(b"charset", i)) >= 0:
        i += 7
        while i < len(content) and content[i] in _SPACE:
            i += 1
        if i < len(content) and content[i] == 0x3D:
            i += 1
            while i < len(content) and content[i] in _SPACE:
                i += 1
            if i == len(content):
                return None
            if content[i] in b"\"'":
                end = content.find(content[i : i + 1], i + 1)
                return None if end < 0 else content[i + 1 : end].decode("latin-1")
            end = i
            while end < len(content) and content[end] not in b"\t\n\f\r ;":
                end += 1
            return content[i:end].decode("latin-1")
    return None


def _meta_charset(data: bytes, i: int) -> tuple[str | None, int]:
    """The <meta> branch of the prescan, from just after "<meta ": the label it yields, if any."""
    seen: set[bytes] = set()
    got_pragma, need_pragma, charset = False, None, None
    while True:
        name, value, i = _get_attribute(data, i)
        if name is None:
            break
        if name in seen:
            continue
        seen.add(name)
        if name == b"http-equiv":
            got_pragma = got_pragma or value == b"content-type"
        elif name == b"content":
            label = _content_charset(value)
            if label and charset is None:
                charset, need_pragma = label, True
        elif name == b"charset":
            charset, need_pragma = value.decode("latin-1"), False
    if need_pragma is None or (need_pragma and not got_pragma):
        return None, i
    return charset, i


def _prescan(data: bytes) -> str | None:
    """HTML Standard "prescan a byte stream to determine its encoding" over `data`.

    Returns the encoding of the first <meta> a browser would use, or None when there is none or
    Chrome may not see it. A label outside the supported table is refused: a browser either uses
    that charset or skips to the next <meta>, and only the full WHATWG table tells which.
    """
    if data.startswith((b"<\x00?\x00x\x00", b"\x00<\x00?\x00x")):
        raise FetchError("unsupported charset: the page starts with a UTF-16 XML declaration")
    after_text_element = False
    i = 0
    try:
        while i < len(data):
            if data.startswith(b"<!--", i):
                i = data.index(b"-->", i + 2) + 2  # the dashes of "<!--" may close it: "<!-->"
            elif data[i : i + 5].lower() == b"<meta" and data[i + 5] in b"\t\n\f\r /":
                label, i = _meta_charset(data, i + 5)
                if label is not None and label.strip("\t\n\f\r "):
                    encoding = _encoding(label, _WHATWG_META_LABELS)
                    return None if after_text_element else encoding
            elif data[i] == 0x3C and (
                data[i + 1 : i + 2].isalpha()
                or (data[i + 1 : i + 2] == b"/" and data[i + 2 : i + 3].isalpha())
            ):
                end = i + 1
                while data[end] not in _SPACE and data[end] != 0x3E:
                    end += 1
                after_text_element |= data[i + 1 : end].lower() in _TEXT_ELEMENTS
                i = end
                while (attribute := _get_attribute(data, i))[0] is not None:
                    i = attribute[2]
                i = attribute[2]
            elif data[i] == 0x3C and data[i + 1 : i + 2] in (b"!", b"/", b"?"):
                i = data.index(b">", i + 1)
            i += 1
    except (IndexError, ValueError):  # the 1024 bytes end inside a tag or comment
        return None
    return None


def _decodable_charset(content_type: str | None, body: bytes) -> str:
    if body.startswith(_UTF8_BOM):
        return "utf-8-sig"
    if body.startswith(_OTHER_BOMS):
        raise FetchError("unsupported charset: the page starts with a non-UTF-8 byte-order mark")
    header = _header_label(content_type)
    if header is not None:
        return _encoding(header)
    # A browser may act on any <meta> it meets while parsing, so every declaration must agree
    # with the one the prescan of the first 1024 bytes finds.
    found = _labels(_ANY_DECLARATION.finditer(body))
    declared = {_encoding(label, _WHATWG_META_LABELS) for label in found}
    early = _prescan(body[:1024])
    if len(declared | {early} - {None}) > 1:
        raise FetchError("unsupported charset: the page declares conflicting ones")
    if early is not None:
        return early
    if _NOT_PLAIN_ASCII.search(body):
        raise FetchError("unsupported charset: the page declares none a browser is sure to use")
    return "utf-8"


def _decode(body: bytes, charset: str) -> str:
    if charset == "windows-1252":
        return body.decode("latin-1").translate(_WINDOWS_1252_HIGH)
    return body.decode(charset, errors="replace")


def _unambiguous(url: str) -> None:
    """P5 reads the host with urlsplit; the transport connects to httpx's reading of it. Refuse an
    address the two read differently, so the host P5 checked is the host that is reached."""
    try:
        same = httpx.URL(url).host == (urlsplit(url).hostname or "")
    except (httpx.InvalidURL, ValueError) as exc:
        raise FetchError("the address is not a valid url") from exc
    if not same:
        raise FetchError("the address is ambiguous")


def _read_body(resp: httpx.Response, deadline: float) -> bytes:
    """At most MAX_BYTES of decoded body, pulling at most one chunk past the cap. gzip and deflate
    are inflated with a bounded decompressor; any other Content-Encoding is refused."""
    encoding = resp.headers.get("content-encoding", "identity").strip().lower()
    if encoding in ("", "identity"):
        inflater = None
    elif encoding in ("gzip", "x-gzip"):
        inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
    elif encoding == "deflate":
        inflater = zlib.decompressobj()
    else:
        raise FetchError("the site sent a page in an unsupported encoding")
    stream = resp.stream
    assert isinstance(stream, httpx.SyncByteStream)
    out = bytearray()
    for chunk in stream:
        if inflater is not None:
            try:
                chunk = inflater.decompress(chunk, MAX_BYTES + 1 - len(out))
            except zlib.error as exc:
                raise FetchError("the site sent a corrupt compressed page") from exc
        out += chunk
        if len(out) > MAX_BYTES:
            break
        if time.monotonic() > deadline:
            raise FetchError(_TOO_SLOW)
    return bytes(out[:MAX_BYTES])


class Fetcher:
    """Per run. Sends requests it builds itself straight to a transport, never through a client.

    Without a client there are no event hooks, no auth, no default headers, no cookie jar and no
    environment proxy to leak a credential or rewrite a checked URL. The transport is trusted code:
    it must send exactly one request per call and never follow redirects itself. The Fetcher never
    closes it.

    The guard never downloads the body of a non-2xx page, since P7 refuses it next. Only the
    undefended baseline sets `read_error_pages`, so it sees error pages as a naive agent would.
    """

    def __init__(self, transport: httpx.BaseTransport, read_error_pages: bool = False) -> None:
        self.transport = transport
        self.read_error_pages = read_error_pages
        self._n = 0

    def _send(self, url: str, deadline: float) -> httpx.Response:
        """One request. Every failure becomes a code-written FetchError; the original exception,
        whose text may quote a Location header or the body, stays chained for a debugger."""
        left = deadline - time.monotonic()
        if left <= 0:
            raise FetchError(_TOO_SLOW)
        timeout = dict.fromkeys(("connect", "read", "write", "pool"), left)
        request = httpx.Request(
            "GET", url, headers=REQUEST_HEADERS, extensions={"timeout": timeout}
        )
        try:
            resp = self.transport.handle_request(request)
        except FetchError:
            raise  # AddressRefused from the guard's transport
        except httpx.RemoteProtocolError as exc:
            raise FetchError("the server sent a malformed response") from exc
        except httpx.TimeoutException as exc:
            raise FetchError(_TOO_SLOW) from exc
        except Exception as exc:  # noqa: BLE001 - any transport failure is a failed fetch
            raise FetchError("the site could not be fetched") from exc
        resp.request = request
        return resp

    def fetch(self, url: str, check_hop: HopCheck | None = None) -> FetchedDoc:
        """Fetch `url`, following redirects by hand so every hop is checked before it is sent."""
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise FetchError(f"unsupported url: {url}")
        _unambiguous(url)
        deadline = time.monotonic() + FETCH_DEADLINE_S
        visited: list[httpx.Response] = []
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            resp = self._send(current, deadline)
            visited.append(resp)
            location = resp.headers.get("location")
            if not (resp.is_redirect and location):
                break
            resp.close()  # a redirect's body is never read
            try:
                prev, current = current, urljoin(str(resp.url), location)
                scheme = urlsplit(current).scheme
            except ValueError as exc:  # a Location header no URL parser accepts
                raise FetchError("the server sent a malformed response") from exc
            if scheme not in ("http", "https"):
                raise FetchError("redirect to a url that is not http or https")
            _unambiguous(current)  # before P5 reads the host, so P5 never quotes a malformed one
            if check_hop is not None:
                check_hop(prev, current)
        else:
            raise FetchError(f"too many redirects (more than {MAX_REDIRECTS})")
        try:
            readable = resp.is_success or self.read_error_pages
            content = _read_body(resp, deadline) if readable else b""
        except FetchError:
            raise
        except httpx.RemoteProtocolError as exc:
            raise FetchError("the server sent a malformed response") from exc
        except httpx.TimeoutException as exc:
            raise FetchError(_TOO_SLOW) from exc
        except Exception as exc:  # noqa: BLE001 - any failure while reading is a failed fetch
            raise FetchError("the site could not be fetched") from exc
        finally:
            resp.close()
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
