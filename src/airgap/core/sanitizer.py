"""Sanitizer (no LLM). Shrinks the attack surface; it is not a guarantee.

Visible-text injection survives on purpose: the reader is tool-less and typed, and the
policy engine sits behind it. That is what the architecture is for.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from bs4 import BeautifulSoup, Comment, Tag

_HIDDEN_STYLE = [
    ("css_display_none", re.compile(r"display\s*:\s*none", re.I)),
    ("css_visibility_hidden", re.compile(r"visibility\s*:\s*hidden", re.I)),
    ("css_opacity_zero", re.compile(r"opacity\s*:\s*0(\.0+)?\s*(;|$)", re.I)),
    ("tiny_font", re.compile(r"font-size\s*:\s*[0-2](\.\d+)?\s*(px|pt)?\s*(;|$)", re.I)),
    ("off_screen", re.compile(r"(left|top|text-indent)\s*:\s*-\d{3,}", re.I)),
]
_WHITE = r"(#fff(fff)?|white|rgb\(\s*255\s*,\s*255\s*,\s*255\s*\))"
_WHITE_TEXT = re.compile(rf"(?<!background-)color\s*:\s*{_WHITE}", re.I)
_WHITE_BG = re.compile(rf"background(-color)?\s*:\s*{_WHITE}", re.I)
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
    style = tag.get("style")
    if not isinstance(style, str):
        return None
    for name, pattern in _HIDDEN_STYLE:
        if pattern.search(style):
            return name
    if _WHITE_TEXT.search(style) and _WHITE_BG.search(style):
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
    stripped = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    if stripped != text:
        removed.append(Removal("zero_width_chars", f"{len(text) - len(stripped)} chars"))
    text = " ".join(stripped.split())
    return SanitizeResult(text=text, removed=tuple(removed))
