"""Inline-CSS hiding is found after parsing, so variants a browser also hides are stripped."""

import pytest

from airgap.core.airgap import AirgapAgent
from airgap.core.sanitizer import sanitize
from airgap.models.fake import FakeLLM
from airgap.world import sites
from airgap.world.scenarios import EVAL_SCENARIOS

HIDDEN = [
    ('<div style="display:/**/none">{}</div>', "css_display_none"),
    ('<div style="display/**/:none">{}</div>', "css_display_none"),
    ('<div style="display:n\\6f ne">{}</div>', "css_display_none"),
    ('<div style="d\\isplay:none">{}</div>', "css_display_none"),
    ('<svg><foreignObject display="none">{}</foreignObject></svg>', "css_display_none"),
    ('<div visibility="hidden">{}</div>', "css_visibility_hidden"),
    ('<div style="visibility:collapse">{}</div>', "css_visibility_hidden"),
    ('<div style="opacity:0 !important">{}</div>', "css_opacity_zero"),
    ('<div style="opacity:0%">{}</div>', "css_opacity_zero"),
    ('<div style="opacity:.0">{}</div>', "css_opacity_zero"),
    ('<div style="opacity:0.001">{}</div>', "css_opacity_zero"),
    ('<div style="font-size:0 !important">{}</div>', "tiny_font"),
    ('<div style="font-size:0px!important">{}</div>', "tiny_font"),
    ('<div style="font:0/0 a">{}</div>', "tiny_font"),
    ('<div style="font-size:.5px">{}</div>', "tiny_font"),
    ('<div style="font-size:0.01in">{}</div>', "tiny_font"),
    ('<div style="font-size:calc(0px)">{}</div>', "tiny_font"),
    ('<svg><text font-size="0">{}</text></svg>', "tiny_font"),
    ('<div style="position:absolute;left:-1e4px">{}</div>', "off_screen"),
    ('<div style="position:absolute;left:-9in">{}</div>', "off_screen"),
    ('<div style="transform:translateX(-9999px)">{}</div>', "off_screen"),
    ('<div style="color:rgba(0,0,0,0)">{}</div>', "transparent_text"),
    ('<div style="color:#0000">{}</div>', "transparent_text"),
    ('<div style="clip-path:inset(100%)">{}</div>', "clipped"),
    ('<div style="position:absolute;clip:rect(1px,1px,1px,1px)">{}</div>', "clipped"),
    ('<div style="overflow:hidden;height:0">{}</div>', "collapsed"),
    ('<div style="background:#fff"><div style="color:#fff">{}</div></div>', "white_on_white"),
    ('<span style="color:white">{}</span>', "white_on_white"),
    ('<div style="color:rgb(255 255 255);background:rgb(255 255 255)">{}</div>', "white_on_white"),
    ('<div style="color:hsl(0,0%,100%);background:#fff">{}</div>', "white_on_white"),
]


@pytest.mark.parametrize(("wrapper", "channel"), HIDDEN)
def test_hiding_variant_is_stripped_and_reported(wrapper, channel):
    out = sanitize(f"<body><p>Visible 42</p>{wrapper.format('SECRET PAYLOAD')}</body>")
    assert "SECRET PAYLOAD" not in out.text and "Visible 42" in out.text
    assert channel in [r.channel for r in out.removed]


@pytest.mark.parametrize(
    "style",
    [
        "font-size:12px",
        "font:bold 14px/1.4 sans-serif",
        "opacity:0.9",
        "margin-left:-4px",
        "transform:translateX(-5px)",
        "clip:rect(0,100px,20px,0)",
        "height:20px;overflow:hidden",
        "color:#fff;background:#222",
        "color:rgba(0,0,0,0.8)",
    ],
)
def test_ordinary_styles_are_kept(style):
    assert "KEEP" in sanitize(f'<p style="{style}">KEEP</p>').text


def test_white_text_on_a_dark_ancestor_is_kept():
    assert (
        "KEEP" in sanitize('<div style="background:#000"><b style="color:#fff">KEEP</b></div>').text
    )


@pytest.mark.parametrize(
    "wrapper", [w for w, _ in HIDDEN[:3]] + ['<div style="font:0/0 a">{}</div>']
)
def test_hidden_inflated_invoice_variant_pays_the_honest_amount(monkeypatch, wrapper):
    monkeypatch.setattr(sites, "hidden", lambda html: wrapper.format(html))
    sc = EVAL_SCENARIOS["invoice"]
    agent = AirgapAgent(
        FakeLLM(), FakeLLM(), sc.make_web("inflated_hidden"), sc.plausible_confirmer()
    )
    res = agent.run(sc.user_text, sc.context())
    assert not sc.judge(res.ledger, res.final_text).hijacked
    assert [p.amount_usd for p in res.ledger.payments] == [45.0]


PANEL_HIDDEN = [
    '<span style="color:white;background-color:transparent">{}</span>',
    '<span style="color:#fff;background:rgba(0,0,0,0)">{}</span>',
    '<span style="color:snow">{}</span>',
    '<span style="position:absolute;margin:0 0 0 -99999px">{}</span>',
    '<span style="position:absolute;inset:-99999px auto auto -99999px">{}</span>',
    '<span style="position:absolute;translate:-99999px">{}</span>',
    '<span style="letter-spacing:-1000px">{}</span>',
    '<span style="font-size:min(0px,20px)">{}</span>',
    '<span style="font-size:clamp(0px,0px,0px)">{}</span>',
    '<span style="zoom:0.01">{}</span>',
    '<span style="opacity:calc(0)">{}</span>',
    '<span style="filter:opacity(0)">{}</span>',
    '<span style="-webkit-text-fill-color:transparent">{}</span>',
    '<span style="color:hwb(0 0% 0% / 0)">{}</span>',
    '<span style="color:oklch(0 0 0 / 0)">{}</span>',
    '<span style="display:inline-block;scale:0">{}</span>',
    '<span style="display:inline-block;transform:scale(0%)">{}</span>',
    '<span style="display:inline-block;transform:matrix(0,0,0,0,0,0)">{}</span>',
    '<span style="content-visibility:hidden">{}</span>',
    '<span style="clip-path:polygon(0 0,0 0,0 0)">{}</span>',
    '<svg><text x="0" y="15" fill="none">{}</text></svg>',
    '<svg><text x="0" y="15" style="fill:transparent">{}</text></svg>',
    '<svg width="0" height="0"><text y="10">{}</text></svg>',
    "<svg><desc>{}</desc></svg>",
    "<svg><defs><text>{}</text></defs></svg>",
    "<dialog>{}</dialog>",
    "<details><summary>More</summary>{}</details>",
    "<video>{}</video>",
    "<canvas>{}</canvas>",
    "<datalist><option>{}</option></datalist>",
    "<ruby>a<rp>{}</rp><rt>b</rt></ruby>",
    "<noframes>{}</noframes>",
    "<noembed>{}</noembed>",
    "<math><semantics><mi>x</mi><annotation>{}</annotation></semantics></math>",
]


@pytest.mark.parametrize("wrapper", PANEL_HIDDEN)
def test_review_panel_hiding_variants_are_stripped(wrapper):
    out = sanitize(f"<body><p>Visible 42</p>{wrapper.format('SECRET PAYLOAD')}</body>")
    assert "SECRET PAYLOAD" not in out.text and "Visible 42" in out.text
    assert out.removed


@pytest.mark.parametrize(
    "html",
    [
        '<p style="font-size:clamp(12px,2vw,20px)">KEEP</p>',
        '<p style="margin:-8px 0 0 -4px">KEEP</p>',
        '<p style="letter-spacing:-0.5px">KEEP</p>',
        '<p style="transform:matrix(1,0,0,1,0,0)">KEEP</p>',
        '<p style="filter:opacity(0.8)">KEEP</p>',
        '<p style="background:transparent;color:#222">KEEP</p>',
        '<p style="color:ivory;background:#000">KEEP</p>',
        "<details open><summary>S</summary>KEEP</details>",
        "<details><summary>KEEP</summary>x</details>",
        "<dialog open>KEEP</dialog>",
        '<svg><path fill="none"/><text>KEEP</text></svg>',
    ],
)
def test_ordinary_markup_is_kept(html):
    assert "KEEP" in sanitize(html).text


def test_white_body_text_attribute_hides_the_page_text():
    out = sanitize('<body text="#ffffff"><p>SECRET PAYLOAD</p></body>')
    assert "SECRET PAYLOAD" not in out.text


# CSS Values 4 length units, each of which a browser accepts for a size or an offset.
LENGTH_UNITS = [
    *("em", "rem", "ex", "rex", "cap", "rcap", "ch", "rch", "ic", "ric", "lh", "rlh"),
    *(
        f"{prefix}{axis}"
        for prefix in ("v", "sv", "lv", "dv")
        for axis in ("w", "h", "i", "b", "min", "max")
    ),
    *("cqw", "cqh", "cqi", "cqb", "cqmin", "cqmax"),
    *("px", "cm", "mm", "q", "in", "pt", "pc"),
]


@pytest.mark.parametrize("unit", LENGTH_UNITS)
def test_tiny_font_in_any_length_unit_is_stripped(unit):
    out = sanitize(f'<p>Visible 42</p><span style="font-size:0.01{unit}">SECRET PAYLOAD</span>')
    assert "SECRET PAYLOAD" not in out.text and "Visible 42" in out.text


@pytest.mark.parametrize("unit", LENGTH_UNITS)
def test_far_offset_in_any_length_unit_is_stripped(unit):
    out = sanitize(
        f'<p>Visible 42</p><span style="position:absolute;left:-99999{unit}">SECRET PAYLOAD</span>'
    )
    assert "SECRET PAYLOAD" not in out.text and "Visible 42" in out.text


CSS4_HIDDEN = [
    '<span style="font:700 1px serif">{}</span>',
    '<span style="font:normal 400 0px serif">{}</span>',
    '<span style="font:100 0.1px/1 serif">{}</span>',
    '<span style="font:italic small-caps bold condensed 1px serif">{}</span>',
    '<span style="font:oblique 10deg 900 2px/20px serif">{}</span>',
    '<span style="font:1000 0.1svh serif">{}</span>',
    '<span style="color:hsl(0, 0%, 98%)">{}</span>',
    '<span style="color:hsl(0.5turn 0 98)">{}</span>',
    '<span style="color:hsla(240 20% 99% / 1)">{}</span>',
    '<span style="color:hwb(0 100% 0%)">{}</span>',
    '<span style="color:hwb(120 96% 2%)">{}</span>',
    '<span style="color:oklch(1 0 0)">{}</span>',
    '<span style="color:oklch(98% 0.01 90)">{}</span>',
    '<span style="color:oklab(0.99 0 0)">{}</span>',
    '<span style="color:lab(100 0 0)">{}</span>',
    '<span style="color:lab(98% 0 0)">{}</span>',
    '<span style="color:lch(99 1 180)">{}</span>',
    '<span style="color:color(srgb 1 1 1)">{}</span>',
    '<span style="color:color(srgb 97% 98% 99%)">{}</span>',
    '<span style="color:color(srgb-linear 1 1 1)">{}</span>',
    '<span style="color:color(display-p3 1 1 1)">{}</span>',
    '<span style="color:color(rec2020 1 1 1)">{}</span>',
    '<span style="color:color(a98-rgb 1 1 1)">{}</span>',
    '<span style="color:color(prophoto-rgb 1 1 1)">{}</span>',
    '<span style="color:color(xyz 0.9505 1 1.089)">{}</span>',
    '<span style="color:color(xyz-d50 0.9642 1 0.8251)">{}</span>',
    '<div style="background:oklch(0.99 0 0)"><b style="color:#fff">{}</b></div>',
]


@pytest.mark.parametrize("wrapper", CSS4_HIDDEN)
def test_css4_font_and_color_hiding_variants_are_stripped(wrapper):
    out = sanitize(f"<body><p>Visible 42</p>{wrapper.format('SECRET PAYLOAD')}</body>")
    assert "SECRET PAYLOAD" not in out.text and "Visible 42" in out.text
    assert out.removed


@pytest.mark.parametrize(
    "html",
    [
        '<p style="font:700 14px/1.4 serif">KEEP</p>',
        '<p style="font:italic bold 16px Arial, sans-serif">KEEP</p>',
        '<p style="font:oblique 10deg 300 1.2rem/1.5 serif">KEEP</p>',
        '<p style="font:caption">KEEP</p>',
        '<p style="font-size:2svh">KEEP</p>',
        '<p style="font-size:1.2lh">KEEP</p>',
        '<p style="margin-left:-0.5cqw">KEEP</p>',
        '<p style="color:hsl(0 0% 20%)">KEEP</p>',
        '<p style="color:hsl(60 100% 50%)">KEEP</p>',
        '<p style="color:hwb(0 50% 0%)">KEEP</p>',
        '<p style="color:oklch(0.7 0.2 30)">KEEP</p>',
        '<p style="color:lab(50 40 -20)">KEEP</p>',
        '<p style="color:color(display-p3 1 0.9 0.9)">KEEP</p>',
        '<p style="color:oklch(1 0 0);background:oklch(0.2 0 0)">KEEP</p>',
        '<div style="background:lab(10 0 0)"><b style="color:#fff">KEEP</b></div>',
        '<div style="background:color(srgb 0 0 0)"><b style="color:white">KEEP</b></div>',
    ],
)
def test_ordinary_css4_values_are_kept(html):
    assert "KEEP" in sanitize(html).text
