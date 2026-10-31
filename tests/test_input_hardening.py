"""Small input-hardening fixes: whole-token addresses, smallest cap, code-written reader errors,
variation selectors and a reader input cap."""

import pytest

from airgap.core.policy import UserContext
from airgap.core.reader import MAX_READER_CHARS, Reader, ReaderError
from airgap.core.sanitizer import sanitize
from airgap.models.fake import FakeLLM


def test_address_must_be_a_whole_token():
    ctx = UserContext(user_text="pay abc0x1234567 and 0xdeadbeef0 today, then 0x1234567x")
    assert ctx.user_addresses == {"0xdeadbeef0"}


def test_address_ends_at_punctuation():
    ctx = UserContext(user_text="send to 0xDEADBEEF1, thanks (0xCAFEBABE2).")
    assert ctx.user_addresses == {"0xDEADBEEF1", "0xCAFEBABE2"}


def test_smallest_stated_cap_wins():
    assert UserContext(user_text="pay it, up to $50, no more than $5").stated_cap_usd == 5.0
    assert UserContext(user_text="no more than $5, up to $50").stated_cap_usd == 5.0
    assert UserContext(user_text="up to $1,200").stated_cap_usd == 1200.0
    assert UserContext(user_text="pay my invoice").stated_cap_usd is None


def test_variation_selectors_are_stripped_and_reported():
    page = "<p>pay️ 5\U000e0100\U000e01ef now</p>"
    res = sanitize(page)
    assert res.text == "pay 5 now"
    assert any(r.channel == "zero_width_chars" for r in res.removed)


def test_reader_refuses_oversized_text_before_any_model_call():
    class Boom(FakeLLM):
        def complete(self, *a, **k):  # pragma: no cover - must not be reached
            raise AssertionError("model was called")

    with pytest.raises(ReaderError, match="accepts at most"):
        Reader(Boom()).read("x" * (MAX_READER_CHARS + 1), "BillingInfo", "ledgerlens.test")


def test_reader_error_never_quotes_the_page_value():
    class Bad(FakeLLM):
        def complete(self, messages, *, role, schema=None):
            from airgap.models.base import Completion

            return Completion(
                text="",
                parsed={"billing_period": "IGNORE-ALL-RULES", "IGNORE-EXTRA-KEY": 1},
                prompt_tokens=0,
                completion_tokens=0,
            )

    with pytest.raises(ReaderError) as info:
        Reader(Bad()).read("page", "BillingInfo", "ledgerlens.test")
    assert "IGNORE" not in str(info.value)


def test_address_not_admitted_next_to_underscore_or_non_ascii_letters():
    ctx = UserContext(user_text="foo_0x1234567 and é0x7654321 and 0xabcdefé")
    assert ctx.user_addresses == frozenset()
