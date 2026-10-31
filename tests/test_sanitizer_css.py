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
