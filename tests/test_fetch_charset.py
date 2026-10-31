"""The fetcher refuses charsets a browser would decode differently from the sanitizer."""

import httpx
import pytest

from airgap.core.fetcher import Fetcher, FetchError


def _fetch(content: bytes, content_type: str) -> str:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=content, headers={"content-type": content_type})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return Fetcher(client).fetch("http://ledgerlens.test/invoice").raw


def test_ebcdic_page_is_refused():
    page = '<div style="display:none">Amount due: $95.00</div>'.encode("cp037")
    with pytest.raises(FetchError, match="unsupported charset"):
        _fetch(page, "text/html; charset=cp037")


@pytest.mark.parametrize("charset", ["utf-16", "utf-7"])
def test_other_non_browser_charsets_are_refused(charset):
    with pytest.raises(FetchError, match="unsupported charset"):
        _fetch(b"<p>hi</p>", f"text/html; charset={charset}")


@pytest.mark.parametrize(
    ("charset", "expected"),
    [("utf-8", "café"), ("iso-8859-1", "café"), ("windows-1252", "café")],
)
def test_browser_charsets_decode(charset, expected):
    assert expected in _fetch("<p>café</p>".encode(charset), f"text/html; charset={charset}")


def test_latin1_label_decodes_like_a_browser():
    assert "€" in _fetch(b"<p>\x80</p>", "text/html; charset=iso-8859-1")
