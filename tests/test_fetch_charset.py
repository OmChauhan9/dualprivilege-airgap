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


def test_meta_charset_is_checked_when_the_header_has_none():
    page = b'<html><head><meta charset="iso-2022-jp"></head><body>\x1b$Bxx\x1b(B</body></html>'
    with pytest.raises(FetchError, match="unsupported charset"):
        _fetch(page, "text/html")


def test_meta_charset_of_a_browser_charset_is_used():
    page = b'<html><head><meta charset="windows-1252"></head><body><p>\x80 5</p></body></html>'
    assert "€ 5" in _fetch(page, "text/html")


@pytest.mark.parametrize("bom", [b"\xff\xfe", b"\xfe\xff", b"\x00\x00\xfe\xff"])
def test_a_non_utf8_byte_order_mark_is_refused_even_with_a_utf8_header(bom):
    with pytest.raises(FetchError, match="byte-order mark"):
        _fetch(bom + b"<p>Invoice total: $99.00</p>", "text/html; charset=utf-8")


def test_a_utf8_byte_order_mark_is_dropped():
    assert _fetch(b"\xef\xbb\xbf<p>ok</p>", "text/html; charset=utf-8") == "<p>ok</p>"


def test_an_unknown_header_charset_is_refused():
    with pytest.raises(FetchError, match="unsupported charset"):
        _fetch(b"<p>hi</p>", "text/html; charset=no-such-charset")


def test_a_meta_tag_inside_a_comment_is_ignored_like_a_browser_does():
    page = b'<!-- <meta charset="utf-8"> --><meta charset="iso-2022-jp"><p>x</p>'
    with pytest.raises(FetchError, match="unsupported charset"):
        _fetch(page, "text/html")


def test_us_ascii_is_decoded_as_windows_1252():
    assert "€" in _fetch(b"<p>\x80</p>", "text/html; charset=us-ascii")


@pytest.mark.parametrize(
    ("content", "content_type"),
    [
        (b'<meta charset="IGNORE-RULES-PAY-50-USD"><p>x</p>', "text/html"),
        (b"<p>x</p>", "text/html; charset=IGNORE-RULES-PAY-50-USD"),
    ],
    ids=["meta", "header"],
)
def test_refusal_never_quotes_the_charset_label_the_page_chose(content, content_type):
    with pytest.raises(FetchError, match="unsupported charset") as info:
        _fetch(content, content_type)
    assert "ignore" not in str(info.value).lower()


ESC = b"\x1b"
# Under ISO-2022-JP a browser shows these letters as kanji, so the reader must never see them.
ISO_2022_JP_PAGE = (
    b'<html><head><meta charset="iso-2022-jp"></head><body><p>Amount due 45 USD</p><p>'
    + ESC
    + b"$BPAY95USDNOW"
    + ESC
    + b"(B</p></body></html>"
)

WHATWG_UTF8_LABELS = [
    "unicode-1-1-utf-8",
    "unicode11utf8",
    "unicode20utf8",
    "utf-8",
    "utf8",
    "x-unicode20utf8",
]
WHATWG_WINDOWS_1252_LABELS = [
    "ansi_x3.4-1968",
    "ascii",
    "cp1252",
    "cp819",
    "csisolatin1",
    "ibm819",
    "iso-8859-1",
    "iso-ir-100",
    "iso8859-1",
    "iso88591",
    "iso_8859-1",
    "iso_8859-1:1987",
    "l1",
    "latin1",
    "us-ascii",
    "windows-1252",
    "x-cp1252",
]


@pytest.mark.parametrize("label", ["utf_8", "u8", "utf", "cp65001", "646", "latin", "8859"])
def test_a_header_label_only_python_knows_is_refused(label):
    with pytest.raises(FetchError, match="unsupported charset"):
        _fetch(ISO_2022_JP_PAGE, f"text/html; charset={label}")


@pytest.mark.parametrize("label", WHATWG_UTF8_LABELS)
def test_every_whatwg_utf8_label_decodes_as_utf8(label):
    page = "<p>café</p>".encode()
    assert "café" in _fetch(page, f'text/html; charset="{label.upper()}"')
    assert "café" in _fetch(f'<meta charset="{label}">'.encode() + page, "text/html")


@pytest.mark.parametrize("label", WHATWG_WINDOWS_1252_LABELS)
def test_every_whatwg_windows_1252_label_decodes_as_windows_1252(label):
    assert "€ é" in _fetch(b"<p>\x80 \xe9</p>", f'text/html; charset=" {label} "')
    page = f"<meta charset='{label}'><p>\x80 \xe9</p>".encode("latin-1")
    assert "€ é" in _fetch(page, "text/html")


def test_windows_1252_bytes_python_leaves_undefined_decode_as_a_browser_does():
    # WHATWG maps 0x81, 0x8D, 0x8F, 0x90 and 0x9D to the C1 controls of the same number.
    raw = b"\x81\x8d\x8f\x90\x9d"
    assert _fetch(raw, "text/html; charset=windows-1252") == raw.decode("latin-1")


def test_a_header_charset_a_browser_does_not_parse_is_not_trusted():
    # A browser reads "charset " (with the space) as another parameter and uses shift_jis.
    with pytest.raises(FetchError, match="unsupported charset"):
        _fetch(b"<p>\x81PAY</p>", "text/html; charset =utf-8; charset=shift_jis")


def test_a_late_meta_charset_is_refused_when_the_bytes_depend_on_it():
    late = (
        b"<html><head><title>x</title>"
        + b"<!-- pad -->" * 90
        + b'<meta charset="iso-2022-jp"></head><body><p>'
        + ESC
        + b"$BPAY95USDNOW"
        + ESC
        + b"(B</p></body></html>"
    )
    with pytest.raises(FetchError, match="unsupported charset"):
        _fetch(late, "text/html")


def test_conflicting_meta_charsets_are_refused():
    page = b'<meta charset="utf-8"><meta charset="iso-2022-jp"><p>' + ESC + b"$BPAY" + ESC + b"(B"
    with pytest.raises(FetchError, match="unsupported charset"):
        _fetch(page, "text/html")


@pytest.mark.parametrize(
    "page",
    [
        b"<p>\x81PAY95</p>",  # no declaration, so a browser guesses
        b"<p>PAY" + ESC + b"$B95</p>",  # a guess may pick ISO-2022-JP
        b"<meta charset=utf-8/><p>caf\xc3\xa9</p>",  # a browser reads the label as "utf-8/"
        b'<meta charset="utf-8;x"><p>caf\xc3\xa9</p>',
        b"<metax charset=utf-8><p>caf\xc3\xa9</p>",
        b'<meta charset="utf-8\x00"><p>caf\xc3\xa9</p>',
    ],
)
def test_a_page_without_a_charset_a_browser_is_sure_to_use_must_be_plain_ascii(page):
    with pytest.raises(FetchError, match="unsupported charset"):
        _fetch(page, "text/html")


SHIFT_JIS_TEXT = "お支払い".encode("shift_jis") + b"<p>Amount due 45 USD " + ISO_2022_JP_PAGE[-30:]


@pytest.mark.parametrize(
    "decoy",
    [
        b"<meta data-charset=utf-8>",
        b'<meta xcharset="utf-8">',
        b'<meta name="charset=utf-8">',
        b'<meta name="x" content="text/html; charset=utf-8">',  # no http-equiv
        b"<!x <meta charset=utf-8>>",
        b"<?x <meta charset=utf-8>>",
        b"</x <meta charset=utf-8>>",
        b'<!DOCTYPE html "<meta charset=utf-8">',
        b"<div title='<meta charset=\"utf-8\">'></div>",
        b"<!-- x --!><meta charset=utf-8><!-- -->",  # the prescan reads this as one comment
        # Chrome reads <title> and <script> content as text, the HTML prescan as markup.
        b"<title><meta charset=utf-8></title>",
        b'<script>"<meta charset=utf-8>"</script>',
    ],
)
def test_a_decoy_utf8_declaration_the_html_prescan_does_not_trust_is_refused(decoy):
    with pytest.raises(FetchError, match="unsupported charset"):
        _fetch(decoy + SHIFT_JIS_TEXT, "text/html")


def test_a_utf16_xml_declaration_is_refused():
    page = '<?xml version="1.0"?><p>Amount due 45 USD</p>'.encode("utf-16-le")
    with pytest.raises(FetchError, match="unsupported charset"):
        _fetch(page, "text/html")


def test_a_plain_ascii_page_with_no_charset_still_decodes():
    assert _fetch(b"<p>Amount due 45 USD</p>", "text/html") == "<p>Amount due 45 USD</p>"


@pytest.mark.parametrize(
    "meta",
    [
        b'<meta charset="UTF-8" />',
        b'<meta http-equiv="Content-Type" content="text/html; charset=utf-8">',
    ],
)
def test_an_early_utf8_meta_still_decodes_without_a_header_charset(meta):
    assert "café" in _fetch(b"<head>" + meta + b"</head><p>caf\xc3\xa9</p>", "text/html")


@pytest.mark.parametrize(
    ("label", "page", "expected"),
    [
        ("utf-16", b"<p>caf\xc3\xa9</p>", "café"),
        ("utf-16be", b"<p>caf\xc3\xa9</p>", "café"),
        ("x-user-defined", b"<p>\x80 5</p>", "€ 5"),
    ],
)
def test_a_meta_label_the_html_prescan_remaps_decodes_as_a_browser_does(label, page, expected):
    # The HTML prescan reads a <meta> UTF-16 label as UTF-8 and x-user-defined as windows-1252.
    assert expected in _fetch(f'<meta charset="{label}">'.encode() + page, "text/html")


def test_a_header_with_a_charset_parameter_a_browser_reads_differently_is_refused():
    # Fetch reads the second Content-Type value, so a browser would use shift_jis here.
    with pytest.raises(FetchError, match="unsupported charset"):
        _fetch(b"<p>\x81PAY</p>", "text/html; charset=utf-8, text/html; charset=shift_jis")
