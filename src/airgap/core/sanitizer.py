"""Sanitizer (no LLM). Shrinks the attack surface; it is not a guarantee.

Visible-text injection survives on purpose: the reader is tool-less and typed, and the
policy engine sits behind it. That is what the architecture is for.
"""

from __future__ import annotations

import math
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
_INVISIBLE_RANGES = ((0xFE00, 0xFE0F), (0xFFF0, 0xFFF8), (0xE0000, 0xE0FFF))


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
# Every CSS Values 4 length unit, sized at a 16px font and a 1000px viewport or container.
_PX_PER_UNIT = {
    "": 1.0, "px": 1.0, "pt": 4 / 3, "pc": 16.0, "in": 96.0, "cm": 96 / 2.54,
    "mm": 96 / 25.4, "q": 96 / 101.6, "%": 0.16,
    **{unit: 16.0 for unit in ("em", "rem", "ic", "ric")},
    **{unit: 8.0 for unit in ("ex", "rex", "ch", "rch")},
    **{unit: 11.2 for unit in ("cap", "rcap")},
    **{unit: 19.2 for unit in ("lh", "rlh")},
    **{
        prefix + axis: 10.0
        for prefix in ("v", "sv", "lv", "dv", "cq")
        for axis in ("w", "h", "i", "b", "min", "max")
    },
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
_FONT_PREFIX_KEYWORDS = frozenset(
    {"normal", "italic", "oblique", "small-caps", "bold", "bolder", "lighter", "ultra-condensed",
     "extra-condensed", "condensed", "semi-condensed", "semi-expanded", "expanded",
     "extra-expanded", "ultra-expanded"}
)  # fmt: skip
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
            decls.setdefault(prop, []).append(" ".join(_COMMENT.sub(" ", value).split()).lower())
    style = tag.get("style")
    if isinstance(style, str):
        # A comment separates tokens, so `700/**/1px` is two tokens, never `7001px`.
        css = _unescape(_COMMENT.sub(" ", style))
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


_COLOR_FUNCTIONS = ("rgb", "rgba", "hsl", "hsla", "hwb", "lab", "lch", "oklab", "oklch", "color")
_DEGREES_PER_UNIT = {"": 1.0, "deg": 1.0, "grad": 0.9, "rad": 180 / math.pi, "turn": 360.0}
Matrix = tuple[tuple[float, float, float], ...]
_XYZ_D65_TO_LINEAR_SRGB: Matrix = (
    (3.2409699419045226, -1.537383177570094, -0.4986107602930034),
    (-0.9692436362808796, 1.8759675015077202, 0.04155505740717559),
    (0.05563007969699366, -0.20397695888897652, 1.0569715142428786),
)
_D50_TO_D65: Matrix = (
    (0.955473421488075, -0.02309845494876471, 0.06325924320057072),
    (-0.0283697093338637, 1.0099953980813041, 0.021041441191917323),
    (0.012314014864481998, -0.020507649298898964, 1.330365926242124),
)
_D50_WHITE = (0.3457 / 0.3585, 1.0, (1.0 - 0.3457 - 0.3585) / 0.3585)
_OKLAB_TO_LMS: Matrix = (
    (1.0, 0.3963377774, 0.2158037573),
    (1.0, -0.1055613458, -0.0638541728),
    (1.0, -0.0894841775, -1.2914855480),
)
_LMS_TO_LINEAR_SRGB: Matrix = (
    (4.0767416621, -3.3077115913, 0.2309699292),
    (-1.2684380046, 2.6097574011, -0.3413193965),
    (-0.0041960863, -0.7034186147, 1.7076147010),
)


def _srgb_to_linear(v: float) -> float:
    if abs(v) <= 0.04045:
        return v / 12.92
    return math.copysign(((abs(v) + 0.055) / 1.055) ** 2.4, v)


def _linear_to_srgb(v: float) -> float:
    if abs(v) <= 0.0031308:
        return v * 12.92
    return math.copysign(1.055 * abs(v) ** (1 / 2.4) - 0.055, v)


def _rec2020_to_linear(v: float) -> float:
    a, b = 1.09929682680944, 0.018053968510807
    if abs(v) < b * 4.5:
        return v / 4.5
    return math.copysign(((abs(v) + a - 1) / a) ** (1 / 0.45), v)


def _prophoto_to_linear(v: float) -> float:
    return v / 16 if abs(v) <= 16 / 512 else math.copysign(abs(v) ** 1.8, v)


def _a98_to_linear(v: float) -> float:
    return math.copysign(abs(v) ** (563 / 256), v)


_IDENTITY: Matrix = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
# Predefined color() spaces: transfer to linear light, the matrix to XYZ, and whether that XYZ is
# D50 (a browser adapts it to D65 before converting to sRGB).
_PREDEFINED_SPACES: dict[str, tuple[Callable[[float], float], Matrix, bool]] = {
    "srgb": (_srgb_to_linear, (
        (0.41239079926595934, 0.357584339383878, 0.1804807884018343),
        (0.21263900587151027, 0.715168678767756, 0.07219231536073371),
        (0.01933081871559182, 0.11919477979462598, 0.9505321522496607),
    ), False),
    "display-p3": (_srgb_to_linear, (
        (0.4865709486482162, 0.26566769316909306, 0.1982172852343625),
        (0.2289745640697488, 0.6917385218365064, 0.079286914093745),
        (0.0, 0.04511338185890264, 1.043944368900976),
    ), False),
    "rec2020": (_rec2020_to_linear, (
        (0.6369580483012914, 0.14461690358620832, 0.1688809751641721),
        (0.2627002120112671, 0.6779980715188708, 0.05930171646986196),
        (0.0, 0.028072693049087428, 1.060985057710791),
    ), False),
    "a98-rgb": (_a98_to_linear, (
        (0.5766690429101305, 0.1855582379065463, 0.1882286462349947),
        (0.29734497525053605, 0.6273635662554661, 0.07529145849399788),
        (0.02703136138641234, 0.07068885253582723, 0.9913375368376388),
    ), False),
    "prophoto-rgb": (_prophoto_to_linear, (
        (0.7977604896723027, 0.13518583717574031, 0.0313493495815248),
        (0.2880711282292934, 0.7118432178101014, 0.00008565396060525902),
        (0.0, 0.0, 0.8251046025104601),
    ), True),
    "xyz-d65": (float, _IDENTITY, False),
    "xyz": (float, _IDENTITY, False),
    "xyz-d50": (float, _IDENTITY, True),
}  # fmt: skip
_PREDEFINED_SPACES["srgb-linear"] = (float, _PREDEFINED_SPACES["srgb"][1], False)


def _apply(m: Matrix, v: tuple[float, float, float]) -> tuple[float, float, float]:
    return (
        m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
        m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
        m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2],
    )


def _channel(token: str, scale: float) -> float:
    """A number, or a percentage of `scale`; the CSS `none` keyword is zero."""
    if token == "none":
        return 0.0
    return float(token[:-1]) / 100 * scale if token.endswith("%") else float(token)


def _hue(token: str) -> float:
    if token == "none":
        return 0.0
    m = _NUMBER.match(token)
    if not m or m.group(2) not in _DEGREES_PER_UNIT:
        raise ValueError(token)
    return float(m.group(1)) * _DEGREES_PER_UNIT[m.group(2)]


def _hsl(hue: float, sat: float, light: float) -> tuple[float, float, float]:
    def f(n: float) -> float:
        k = (n + hue / 30) % 12
        return light - sat * min(light, 1 - light) * max(-1.0, min(k - 3, 9 - k, 1.0))

    return f(0), f(8), f(4)


def _lab_to_xyz_d50(lightness: float, a: float, b: float) -> tuple[float, float, float]:
    kappa, epsilon = 24389 / 27, 216 / 24389
    f1 = (lightness + 16) / 116
    f0, f2 = a / 500 + f1, f1 - b / 200
    x = f0**3 if f0**3 > epsilon else (116 * f0 - 16) / kappa
    y = f1**3 if lightness > kappa * epsilon else lightness / kappa
    z = f2**3 if f2**3 > epsilon else (116 * f2 - 16) / kappa
    return x * _D50_WHITE[0], y, z * _D50_WHITE[2]


def _srgb(space: str, parts: list[str]) -> tuple[float, float, float] | None:
    """The color as sRGB channels in 0..1, clipped to what an sRGB screen draws."""
    if space == "rgb":
        rgb = (_channel(parts[0], 255) / 255, _channel(parts[1], 255) / 255,
               _channel(parts[2], 255) / 255)  # fmt: skip
    elif space == "hsl":
        rgb = _hsl(_hue(parts[0]), _channel(parts[1], 100) / 100, _channel(parts[2], 100) / 100)
    elif space == "hwb":
        white, black = _channel(parts[1], 100) / 100, _channel(parts[2], 100) / 100
        if white + black >= 1:
            gray = white / (white + black)
            rgb = (gray, gray, gray)
        else:
            r, g, b = _hsl(_hue(parts[0]), 1.0, 0.5)
            rgb = tuple(c * (1 - white - black) + white for c in (r, g, b))
    elif space in ("lab", "lch", "oklab", "oklch"):
        ok = space.startswith("ok")
        lightness = _channel(parts[0], 1.0 if ok else 100.0)
        if space.endswith("ch"):
            chroma = _channel(parts[1], 0.4 if ok else 150.0)
            hue = math.radians(_hue(parts[2]))
            a, b = chroma * math.cos(hue), chroma * math.sin(hue)
        else:
            a, b = (
                _channel(parts[1], 0.4 if ok else 125.0),
                _channel(parts[2], 0.4 if ok else 125.0),
            )
        if ok:
            lms = _apply(_OKLAB_TO_LMS, (lightness, a, b))
            linear = _apply(_LMS_TO_LINEAR_SRGB, (lms[0] ** 3, lms[1] ** 3, lms[2] ** 3))
        else:
            xyz = _apply(_D50_TO_D65, _lab_to_xyz_d50(lightness, a, b))
            linear = _apply(_XYZ_D65_TO_LINEAR_SRGB, xyz)
        rgb = tuple(_linear_to_srgb(c) for c in linear)
    elif space in _PREDEFINED_SPACES:
        to_linear, to_xyz, d50 = _PREDEFINED_SPACES[space]
        r, g, b = (to_linear(_channel(p, 1.0)) for p in parts)
        xyz = _apply(to_xyz, (r, g, b))
        linear = _apply(_XYZ_D65_TO_LINEAR_SRGB, _apply(_D50_TO_D65, xyz) if d50 else xyz)
        rgb = tuple(_linear_to_srgb(c) for c in linear)
    else:
        return None
    r, g, b = (min(1.0, max(0.0, c)) for c in rgb)
    return r, g, b


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
    fn = re.match(r"^([a-z]+)\((.*)\)$", color)
    if not fn or fn.group(1) not in _COLOR_FUNCTIONS:
        return None
    channels, slash, alpha_text = fn.group(2).partition("/")
    parts = [p for p in re.split(r"[\s,]+", channels.strip()) if p]
    space = parts.pop(0) if fn.group(1) == "color" and parts else fn.group(1).rstrip("a")
    if not slash and len(parts) >= 4:
        alpha_text = parts[3]
    if len(parts) < 3:
        return None
    try:
        alpha = _channel(alpha_text.strip(), 1.0) if alpha_text.strip() else 1.0
        rgb = _srgb(space, parts[:3])
    except ValueError:
        return None
    if rgb is None:
        return alpha, None
    return alpha, (rgb[0] * 255, rgb[1] * 255, rgb[2] * 255)


def _is_white(color: str) -> bool:
    parsed = _alpha_and_rgb(color)
    return bool(parsed and parsed[1] and parsed[0] > 0 and min(parsed[1]) >= 240)


def _background(decls: Declarations) -> str | None:
    for prop in ("background-color", "background"):
        for value in reversed(decls.get(prop, [])):
            for token in re.findall(
                r"#[0-9a-f]{3,8}|(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color)\([^)]*\)|[a-z]+",
                value,
            ):
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


def _font_shorthand_size(tokens: list[str]) -> float | None:
    """The size in a `font` shorthand: the first token after style, variant, weight and stretch."""
    for i, token in enumerate(tokens):
        m = _NUMBER.match(token)
        if token in _FONT_PREFIX_KEYWORDS or (m and m.group(2) in ("deg", "grad", "rad", "turn")):
            continue
        # A bare number is the weight only when a size follows it. Alone, it is read as the size.
        if (
            m
            and not m.group(2)
            and float(m.group(1)) >= 1
            and any(_px(t.split("/")[0]) is not None for t in tokens[i + 1 :])
        ):
            continue
        return _px(token.split("/")[0])
    return None


def _font_size_px(decls: Declarations) -> list[float]:
    sizes = [_px(v) for v in decls.get("font-size", [])]
    sizes += [_font_shorthand_size(v.split()) for v in decls.get("font", [])]
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
