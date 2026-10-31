"""Invisible characters outside Unicode category Cf are stripped like zero-width characters."""

import pytest

from airgap.core.sanitizer import sanitize


@pytest.mark.parametrize("code", [0x034F, 0x115F, 0x1160, 0x17B4, 0x17B5, 0x2800, 0x3164, 0xFFA0])
def test_invisible_non_cf_characters_are_stripped(code):
    res = sanitize(f"<p>a{chr(code) * 5}b</p>")
    assert res.text == "ab"
    assert any(r.channel == "zero_width_chars" for r in res.removed)
