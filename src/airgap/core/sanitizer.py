"""Sanitizer (no LLM). Shrinks the attack surface; it is not a guarantee.

Visible-text injection survives on purpose: the reader is tool-less and typed, and the
policy engine sits behind it. That is what the architecture is for.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass

from bs4 import BeautifulSoup, Comment, Tag

# Invisible characters outside category Cf: they render as nothing (or as blank filler) and can
# encode hidden bits between visible words. Variation selectors, the combining grapheme joiner,
# the Khmer inherent vowels, the Hangul fillers and the braille blank.
_INVISIBLE = frozenset({0x034F, 0x115F, 0x1160, 0x17B4, 0x17B5, 0x2800, 0x3164, 0xFFA0})


def _is_invisible(ch: str) -> bool:
    code = ord(ch)
    return code in _INVISIBLE or 0xFE00 <= code <= 0xFE0F or 0xE0100 <= code <= 0xE01EF


# Inline CSS is parsed into declarations before any rule runs, so comments, escapes, `!important`,
# declaration order and unit spelling cannot hide a hiding trick from the rules below.
Declarations = dict[str, list[str]]

_COMMENT = re.compile(r"/\*.*?(\*/|$)", re.S)
_ESCAPE = re.compile(r"\\(?:([0-9a-fA-F]{1,6})\s?|(.))", re.S)
_IMPORTANT = re.compile(r"!\s*important", re.I)
_NUMBER = re.compile(r"^([+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?)([a-z%]*)$")
_PX_PER_UNIT = {
    "": 1.0, "px": 1.0, "pt": 4 / 3, "pc": 16.0, "in": 96.0, "cm": 96 / 2.54,
    "mm": 96 / 25.4, "q": 96 / 101.6, "em": 16.0, "rem": 16.0, "ex": 8.0, "ch": 8.0,
    "%": 0.16, "vw": 10.0, "vh": 10.0, "vmin": 10.0, "vmax": 10.0,
}  # fmt: skip
# Presentation attributes (SVG, legacy HTML) that a browser applies like inline CSS.
_PRESENTATION = {
    "display": "display", "visibility": "visibility", "opacity": "opacity",
    "font-size": "font-size", "fill-opacity": "fill-opacity", "color": "color",
    "bgcolor": "background-color",
}  # fmt: skip
_OFFSET_PROPS = ("left", "top", "right", "bottom", "text-indent", "margin-left", "margin-top")
_TINY_FONT_PX = 5.0
_OFF_SCREEN_PX = -10.0


def _unescape(css: str) -> str:
    def repl(m: re.Match[str]) -> str:
        if m.group(1):
            code = int(m.group(1), 16)
            return chr(code) if 0 < code <= 0x10FFFF else ""
        return m.group(2)

    return _ESCAPE.sub(repl, css)


def _declarations(tag: Tag) -> Declarations:
    decls: Declarations = {}
    for attr, prop in _PRESENTATION.items():
        value = tag.get(attr)
        if isinstance(value, str):
            decls.setdefault(prop, []).append(value.strip().lower())
    style = tag.get("style")
    if isinstance(style, str):
        css = _unescape(_COMMENT.sub("", style))
        for part in css.split(";"):
            prop, sep, value = part.partition(":")
            if sep:
                value = " ".join(_IMPORTANT.sub("", value).split()).lower()
                decls.setdefault(prop.strip().lower(), []).append(value)
    return decls


def _px(token: str) -> float | None:
    token = token.strip()
    if token.startswith("calc(") and token.endswith(")"):
        token = token[5:-1]
    m = _NUMBER.match(token)
    if not m or m.group(2) not in _PX_PER_UNIT:
        return None
    return float(m.group(1)) * _PX_PER_UNIT[m.group(2)]


def _alpha_and_rgb(color: str) -> tuple[float, tuple[float, float, float] | None] | None:
    """(alpha, rgb) for the color forms pages use to hide text; None when it is not parseable."""
    color = color.strip()
    if color == "transparent":
        return 0.0, None
    if color == "white":
        return 1.0, (255.0, 255.0, 255.0)
    if color.startswith("#") and len(color) in (4, 5, 7, 9):
        hexes = color[1:]
        if len(hexes) in (3, 4):
            hexes = "".join(ch * 2 for ch in hexes)
        try:
            channels = [int(hexes[i : i + 2], 16) for i in range(0, len(hexes), 2)]
        except ValueError:
            return None
        alpha = channels[3] / 255 if len(channels) == 4 else 1.0
        return alpha, (float(channels[0]), float(channels[1]), float(channels[2]))
    m = re.match(r"^(rgba?|hsla?)\((.*)\)$", color)
    if not m:
        return None
    parts = [p for p in re.split(r"[\s,/]+", m.group(2).strip()) if p]
    if len(parts) < 3:
        return None
    alpha = 1.0
    if len(parts) >= 4:
        a = parts[3]
        try:
            alpha = float(a[:-1]) / 100 if a.endswith("%") else float(a)
        except ValueError:
            return None
    if m.group(1).startswith("hsl"):
        try:
            lightness = float(parts[2].rstrip("%"))
        except ValueError:
            return None
        return alpha, (255.0, 255.0, 255.0) if lightness >= 100 else (0.0, 0.0, 0.0)
    try:
        rgb = [float(p[:-1]) * 2.55 if p.endswith("%") else float(p) for p in parts[:3]]
    except ValueError:
        return None
    return alpha, (rgb[0], rgb[1], rgb[2])


def _is_white(color: str) -> bool:
    parsed = _alpha_and_rgb(color)
    return bool(parsed and parsed[1] and parsed[0] > 0 and min(parsed[1]) >= 250)


def _background(decls: Declarations) -> str | None:
    for prop in ("background-color", "background"):
        for value in reversed(decls.get(prop, [])):
            for token in re.findall(r"#[0-9a-f]{3,8}|(?:rgba?|hsla?)\([^)]*\)|[a-z]+", value):
                if _alpha_and_rgb(token) is not None:
                    return token
    return None


def _effective_background(tag: Tag) -> str:
    """The nearest inline background on the tag or an ancestor; a page with none renders white."""
    for node in (tag, *tag.parents):
        if isinstance(node, Tag):
            bg = _background(_declarations(node))
            if bg is not None:
                return bg
    return "white"


def _font_size_px(decls: Declarations) -> list[float]:
    sizes = [_px(v) for v in decls.get("font-size", [])]
    for value in decls.get("font", []):
        for token in value.split():
            size = _px(token.split("/")[0])
            if size is not None:
                sizes.append(size)
                break
    return [s for s in sizes if s is not None]


def _any(decls: Declarations, prop: str, test: Callable[[str], bool]) -> bool:
    return any(test(v) for v in decls.get(prop, []))


def _opacity_zero(value: str) -> bool:
    try:
        number = float(value[:-1]) / 100 if value.endswith("%") else float(value)
    except ValueError:
        return False
    return number <= 0.05


def _clipped(decls: Declarations) -> bool:
    for value in decls.get("clip", []):
        m = re.match(r"^rect\((.*)\)$", value)
        if m:
            sides = [_px(p) for p in re.split(r"[\s,]+", m.group(1).strip()) if p]
            known = [x for x in sides if x is not None]
            if len(known) == 4 == len(sides):
                top, right, bottom, left = known
                if bottom <= top or right <= left:
                    return True
    for value in decls.get("clip-path", []):
        m = re.match(r"^inset\(\s*([\d.]+)%", value)
        if (m and float(m.group(1)) >= 50) or re.match(r"^circle\(\s*0", value):
            return True
    return False


def _collapsed(decls: Declarations) -> bool:
    clips = any(v in ("hidden", "clip") for p in ("overflow", "overflow-x", "overflow-y")
                for v in decls.get(p, []))  # fmt: skip
    if not clips:
        return False
    sizes = [
        _px(v) for p in ("height", "max-height", "width", "max-width") for v in decls.get(p, [])
    ]
    return any(s is not None and s <= 1 for s in sizes)


def _off_screen(decls: Declarations) -> bool:
    for prop in _OFFSET_PROPS:
        for value in decls.get(prop, []):
            offset = _px(value) if not value.endswith("%") else _px(value[:-1])
            if offset is not None and offset * (10 if value.endswith("%") else 1) <= _OFF_SCREEN_PX:
                return True
    for value in decls.get("transform", []):
        for arg in re.findall(r"translate[xy3d]*\(([^)]*)\)", value):
            for token in re.split(r"[\s,]+", arg.strip()):
                offset = _px(token)
                if offset is not None and offset <= -1000:
                    return True
        if re.search(r"scale[xy]?\(\s*0(\.0*)?\s*[,)]", value):
            return True
    return False


def _transparent_text(decls: Declarations) -> bool:
    for prop in ("color", "fill-opacity"):
        for value in decls.get(prop, []):
            if prop == "fill-opacity":
                if _opacity_zero(value):
                    return True
                continue
            parsed = _alpha_and_rgb(value)
            if parsed is not None and parsed[0] <= 0.05:
                return True
    return False


_HIDING_RULES: tuple[tuple[str, Callable[[Declarations], bool]], ...] = (
    ("css_display_none", lambda d: _any(d, "display", lambda v: v == "none")),
    (
        "css_visibility_hidden",
        lambda d: _any(d, "visibility", lambda v: v in ("hidden", "collapse")),
    ),
    ("css_opacity_zero", lambda d: _any(d, "opacity", _opacity_zero)),
    ("tiny_font", lambda d: any(px < _TINY_FONT_PX for px in _font_size_px(d))),
    ("off_screen", _off_screen),
    ("transparent_text", _transparent_text),
    ("clipped", _clipped),
    ("collapsed", _collapsed),
)


_SNIPPET = 160


@dataclass(frozen=True)
class Removal:
    channel: str
    snippet: str


@dataclass(frozen=True)
class SanitizeResult:
    text: str
    removed: tuple[Removal, ...]


def _snip(s: str) -> str:
    s = " ".join(s.split())
    return s[:_SNIPPET] + ("..." if len(s) > _SNIPPET else "")


def _hidden_channel(tag: Tag) -> str | None:
    if tag.has_attr("hidden"):
        return "hidden_attr"
    decls = _declarations(tag)
    if not decls:
        return None
    for name, rule in _HIDING_RULES:
        if rule(decls):
            return name
    colors = decls.get("color", [])
    if colors and _is_white(colors[-1]) and _is_white(_effective_background(tag)):
        return "white_on_white"
    return None


def sanitize(html: str) -> SanitizeResult:
    soup = BeautifulSoup(html, "lxml")
    removed: list[Removal] = []

    for tag in soup.find_all("script"):
        kind = "json_ld" if "ld+json" in str(tag.get("type", "")) else "script"
        removed.append(Removal(kind, _snip(tag.get_text())))
        tag.decompose()
    for tag in soup.find_all(["style", "noscript", "template", "iframe"]):
        removed.append(Removal(tag.name, _snip(tag.get_text())))
        tag.decompose()
    for tag in soup.find_all("meta"):
        key = tag.get("property") or tag.get("name") or "meta"
        removed.append(Removal("meta:" + str(key), _snip(str(tag.get("content", "")))))
        tag.decompose()
    for node in soup.find_all(string=lambda s: isinstance(s, Comment)):
        removed.append(Removal("html_comment", _snip(str(node))))
        node.extract()
    for tag in soup.find_all("img"):
        if tag.get("alt"):
            removed.append(Removal("alt_text", _snip(str(tag.get("alt")))))
        tag.decompose()
    for tag in list(soup.find_all(True)):
        if tag.decomposed:
            continue
        channel = _hidden_channel(tag)
        if channel:
            removed.append(Removal(channel, _snip(tag.get_text())))
            tag.decompose()

    body = soup.body or soup
    for title in soup.find_all("title"):
        title.decompose()
    text = body.get_text(" ")
    text = unicodedata.normalize("NFKC", text)
    stripped = "".join(
        ch for ch in text if unicodedata.category(ch) != "Cf" and not _is_invisible(ch)
    )
    if stripped != text:
        removed.append(Removal("zero_width_chars", f"{len(text) - len(stripped)} chars"))
    text = " ".join(stripped.split())
    return SanitizeResult(text=text, removed=tuple(removed))
