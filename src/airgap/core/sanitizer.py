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
# encode hidden bits between visible words. Variation selectors (including the Mongolian ones), the
# combining grapheme joiner, the Khmer inherent vowels, the Hangul and Khitan fillers, the braille
# blank, and code points Unicode reserves as default-ignorable.
_INVISIBLE = frozenset(
    {0x034F, 0x115F, 0x1160, 0x17B4, 0x17B5, 0x180B, 0x180C, 0x180D, 0x180F, 0x2065, 0x2800,
     0x3164, 0xFFA0, 0x16FE4}
)  # fmt: skip
_INVISIBLE_RANGES = ((0xFE00, 0xFE0F), (0xFFF0, 0xFFF8), (0xE0080, 0xE0FFF))


def _is_invisible(ch: str) -> bool:
    code = ord(ch)
    return code in _INVISIBLE or any(lo <= code <= hi for lo, hi in _INVISIBLE_RANGES)


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
    "bgcolor": "background-color", "text": "color", "fill": "fill",
}  # fmt: skip
_SVG_TEXT = ("text", "tspan", "textpath")
# Near-white named colors (every channel at least 240), as a browser draws them.
_NEAR_WHITE = {
    "white": (255, 255, 255), "snow": (255, 250, 250), "ghostwhite": (248, 248, 255),
    "whitesmoke": (245, 245, 245), "ivory": (255, 255, 240), "floralwhite": (255, 250, 240),
    "mintcream": (245, 255, 250), "azure": (240, 255, 255), "seashell": (255, 245, 238),
    "honeydew": (240, 255, 240), "aliceblue": (240, 248, 255), "lavenderblush": (255, 240, 245),
}  # fmt: skip
_OFFSET_PROPS = ("left", "top", "right", "bottom", "text-indent", "margin-left", "margin-top")
_SHORTHAND_OFFSETS = ("margin", "inset", "translate")
_FAR_OFF_SCREEN_PX = -1000.0
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
    fn = re.match(r"^(calc|min|max|clamp)\((.*)\)$", token)
    if fn:
        args = [_px(a) for a in fn.group(2).split(",")]
        known = [a for a in args if a is not None]
        if len(known) != len(args):
            return None
        if fn.group(1) == "calc":
            return known[0] if len(known) == 1 else None
        if fn.group(1) == "min":
            return min(known)
        if fn.group(1) == "max":
            return max(known)
        return max(known[0], min(known[1], known[2])) if len(known) == 3 else None
    m = _NUMBER.match(token)
    if not m or m.group(2) not in _PX_PER_UNIT:
        return None
    return float(m.group(1)) * _PX_PER_UNIT[m.group(2)]


def _alpha_and_rgb(color: str) -> tuple[float, tuple[float, float, float] | None] | None:
    """(alpha, rgb) for the color forms pages use to hide text; None when it is not parseable."""
    color = color.strip()
    if color in ("transparent", "none"):
        return 0.0, None
    if color in _NEAR_WHITE:
        r, g, b = _NEAR_WHITE[color]
        return 1.0, (float(r), float(g), float(b))
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
    other = re.match(r"^(?:hwb|lab|lch|oklab|oklch|color)\((.*)\)$", color)
    if other:
        _, slash, alpha_text = other.group(1).partition("/")
        if not slash:
            return 1.0, None
        a = alpha_text.strip()
        try:
            return (float(a[:-1]) / 100 if a.endswith("%") else float(a)), None
        except ValueError:
            return None
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
    return bool(parsed and parsed[1] and parsed[0] > 0 and min(parsed[1]) >= 240)


def _background(decls: Declarations) -> str | None:
    for prop in ("background-color", "background"):
        for value in reversed(decls.get(prop, [])):
            for token in re.findall(r"#[0-9a-f]{3,8}|(?:rgba?|hsla?)\([^)]*\)|[a-z]+", value):
                parsed = _alpha_and_rgb(token)
                if parsed is not None and parsed[0] > 0:  # a transparent layer shows the one below
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


def _zoomed_out(decls: Declarations) -> bool:
    for value in decls.get("zoom", []):
        try:
            factor = float(value[:-1]) / 100 if value.endswith("%") else float(value)
        except ValueError:
            continue
        if factor <= 0.3:
            return True
    return False


def _any(decls: Declarations, prop: str, test: Callable[[str], bool]) -> bool:
    return any(test(v) for v in decls.get(prop, []))


def _opacity_zero(value: str) -> bool:
    calc = re.match(r"^calc\((.*)\)$", value)
    if calc:
        value = calc.group(1).strip()
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
        poly = re.match(r"^polygon\((.*)\)$", value)
        if poly and len({p.strip() for p in poly.group(1).split(",")}) == 1:
            return True  # every vertex is the same point
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
    for prop in _SHORTHAND_OFFSETS:
        for value in decls.get(prop, []):
            for token in value.split():
                offset = _px(token)
                if offset is not None and offset <= _FAR_OFF_SCREEN_PX:
                    return True
    for value in decls.get("letter-spacing", []):
        spacing = _px(value)
        if spacing is not None and spacing <= _OFF_SCREEN_PX:
            return True
    for value in decls.get("transform", []):
        for arg in re.findall(r"translate[xy3d]*\(([^)]*)\)", value):
            for token in re.split(r"[\s,]+", arg.strip()):
                offset = _px(token)
                if offset is not None and offset <= _FAR_OFF_SCREEN_PX:
                    return True
        if re.search(r"scale[xy]?\(\s*0(\.0*)?%?\s*[,)]", value):
            return True
        matrix = re.search(r"matrix\(([^)]*)\)", value)
        if matrix:
            parts = [p.strip() for p in matrix.group(1).split(",")]
            if len(parts) == 6 and all(_px(p) == 0 for p in parts[:4]):
                return True
    for value in decls.get("scale", []):
        if value.split() and all(_px(t.rstrip("%")) == 0 for t in value.split()):
            return True
    return False


def _transparent_text(decls: Declarations) -> bool:
    for prop in ("color", "-webkit-text-fill-color", "fill-opacity"):
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
    (
        "css_visibility_hidden",
        lambda d: _any(d, "content-visibility", lambda v: v == "hidden"),
    ),
    ("css_opacity_zero", lambda d: _any(d, "opacity", _opacity_zero)),
    (
        "css_opacity_zero",
        lambda d: _any(
            d,
            "filter",
            lambda v: any(_opacity_zero(a) for a in re.findall(r"opacity\(([^)]*)\)", v)),
        ),
    ),
    ("tiny_font", lambda d: any(px < _TINY_FONT_PX for px in _font_size_px(d))),
    ("tiny_font", _zoomed_out),
    ("off_screen", _off_screen),
    ("transparent_text", _transparent_text),
    ("clipped", _clipped),
    ("collapsed", _collapsed),
)


# Elements a browser never paints as text: legacy fallbacks, option lists, ruby fallback
# parentheses, SVG descriptions and definitions, MathML annotations.
_UNRENDERED = ("noembed", "noframes", "datalist", "rp", "desc", "defs", "annotation")
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
    if tag.name == "dialog" and not tag.has_attr("open"):
        return "unrendered_element"
    decls = _declarations(tag)
    if tag.name == "svg" and any(
        (size := _px(str(tag.get(dim, "")))) is not None and size <= 0
        for dim in ("width", "height")
    ):
        return "collapsed"
    if tag.name in _SVG_TEXT and _any(
        decls, "fill", lambda v: (_alpha_and_rgb(v) or (1.0,))[0] <= 0.05
    ):
        return "transparent_text"
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
    for tag in soup.find_all(["style", "noscript", "template", "iframe", *_UNRENDERED]):
        removed.append(Removal(tag.name, _snip(tag.get_text())))
        tag.decompose()
    for tag in soup.find_all(
        ["video", "audio", "canvas"]
    ):  # fallback text shows only if unsupported
        if tag.get_text(strip=True):
            removed.append(Removal(f"{tag.name}_fallback", _snip(tag.get_text())))
        tag.clear()
    for tag in soup.find_all("details"):
        if tag.has_attr("open"):
            continue
        for child in [c for c in tag.contents if not (isinstance(c, Tag) and c.name == "summary")]:
            text = child.get_text() if isinstance(child, Tag) else str(child)
            if text.strip():
                removed.append(Removal("closed_details", _snip(text)))
            child.extract()
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
