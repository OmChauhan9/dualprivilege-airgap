"""Findings from the independent verifier: each test failed before its fix."""

import math

import pytest

from airgap.core.airgap import AirgapAgent
from airgap.core.audit import AuditLog
from airgap.core.fetcher import Fetcher, registrable_domain
from airgap.core.interpreter import Interpreter
from airgap.core.plan import Plan, validate_plan
from airgap.core.policy import PolicyEngine, UserContext, approve_all
from airgap.core.reader import Reader
from airgap.core.sanitizer import sanitize
from airgap.core.values import trusted
from airgap.models.fake import FakeLLM
from airgap.world.ledger import Ledger
from airgap.world.scenarios import CATALOG
from airgap.world.tools import WorldTools

ADDR_40 = "0x" + "1" * 40


# ---- HIGH 1: the reply must not claim an action that policy denied ---------------------------


@pytest.mark.parametrize(
    "scenario,attack",
    [("identity", "amount_over_limit"), ("identity", "amount_huge"), ("steering", "price_zero")],
)
def test_reply_does_not_claim_a_payment_that_policy_denied(scenario, attack):
    sc = CATALOG[scenario]
    res = AirgapAgent(FakeLLM(), FakeLLM(), sc.make_web(attack), approve_all).run(
        sc.user_text, sc.context()
    )
    assert not res.ledger.payments
    assert "paid" not in res.final_text.lower()
    assert "did not run" in res.final_text and "P2" in res.final_text


def test_denied_action_status_line_never_repeats_page_controlled_text():
    sc = CATALOG["identity"]
    res = AirgapAgent(FakeLLM(), FakeLLM(), sc.make_web("amount_huge"), approve_all).run(
        sc.user_text, sc.context()
    )
    assert "999999" not in res.final_text  # the page's number is not echoed in the status line


def test_honest_runs_keep_their_normal_reply():
    sc = CATALOG["identity"]
    res = AirgapAgent(FakeLLM(), FakeLLM(), sc.make_web(None), sc.plausible_confirmer()).run(
        sc.user_text, sc.context()
    )
    assert "did not run" not in res.final_text and res.ledger.payments


# ---- HIGH 2: P1 matches whole addresses, not substrings of the user's text --------------------


def ctx(text: str) -> UserContext:
    return UserContext(user_text=text, payee_allowlist={})


def engine(text: str) -> PolicyEngine:
    return PolicyEngine(ctx(text), AuditLog(), approve_all)


@pytest.mark.parametrize("payee", ["", "Send", "to", ADDR_40[:-1], ADDR_40 + "2", ADDR_40.upper()])
def test_p1_rejects_fragments_and_near_misses_of_the_users_text(payee):
    d = engine(f"Send 5 USD to {ADDR_40} please").check_pay(trusted(payee), trusted(5), trusted(""))
    assert d.verdict == "deny" and "P1" in d.rules


def test_p1_accepts_the_whole_address_the_user_wrote():
    d = engine(f"Send 5 USD to {ADDR_40}.").check_pay(trusted(ADDR_40), trusted(5), trusted(""))
    assert d.verdict == "allow"


def test_validate_plan_rejects_a_truncated_user_address():
    plan = Plan.model_validate(
        {"steps": [{"op": "pay", "payee": {"lit": ADDR_40[:-1]}, "amount": {"lit": 5}}]}
    )
    assert any("not allowlisted" in p for p in validate_plan(plan, ctx(f"Send 5 USD to {ADDR_40}")))


# ---- MEDIUM 3: non-finite amounts ------------------------------------------------------------


@pytest.mark.parametrize("amount", [float("nan"), float("inf"), -float("inf"), "nan", "inf"])
def test_non_finite_amounts_are_denied_and_never_poison_the_running_total(amount):
    e = engine(f"Send 5 USD to {ADDR_40}")
    d = e.check_pay(trusted(ADDR_40), trusted(amount), trusted(""))
    assert d.verdict == "deny" and "P2" in d.rules
    assert math.isfinite(e.spent_usd)


# ---- LOW 6: odd plans fail closed instead of crashing ----------------------------------------


def make_interpreter(text: str) -> Interpreter:
    from airgap.world.mock_web import MockWeb

    c = UserContext(user_text=text)
    web = MockWeb().add("http://ledgerlens.test/p", "<html><body>Total value: $5.00</body></html>")
    client = web.client()
    audit = AuditLog()
    return Interpreter(
        c,
        Fetcher(client),
        Reader(FakeLLM()),
        WorldTools(client, Ledger()),
        PolicyEngine(c, audit, approve_all),
        audit,
    )


@pytest.mark.parametrize(
    "steps",
    [
        [{"op": "answer", "template": "{0}", "args": {}}],
        [{"op": "answer", "template": "{a.b.c}", "args": {"a": {"lit": "x"}}}],
        [
            {"op": "fetch", "out": "d", "url": {"lit": "http://ledgerlens.test/p"}},
            {"op": "read", "out": "p", "doc": "d", "schema_name": "PortfolioSummary"},
            {"op": "answer", "template": "{x}", "args": {"x": {"var": "p", "field": "nope"}}},
        ],
    ],
)
def test_interpreter_turns_unexpected_exceptions_into_a_recorded_error(steps):
    res = make_interpreter("see http://ledgerlens.test/p").run(
        Plan.model_validate({"steps": steps})
    )
    assert res.error and res.answer is None


# ---- LOW 7: IP addresses and domain parsing --------------------------------------------------


def test_ip_addresses_are_their_own_site():
    assert registrable_domain("192.168.0.1") == "192.168.0.1"
    assert registrable_domain("192.168.0.1") != registrable_domain("10.0.0.1")
    assert registrable_domain("[::1]") != registrable_domain("[::2]")


# ---- LOW 8: more hidden-text tricks ----------------------------------------------------------


@pytest.mark.parametrize(
    "style",
    [
        "color:transparent",
        "font-size:0.1em",
        "font-size:0%",
        "clip:rect(0,0,0,0)",
        "height:0;overflow:hidden",
        "position:absolute;left:-99px",
        "position:absolute;top:-500px",
    ],
)
def test_sanitizer_strips_more_hiding_tricks(style):
    html = f'<html><body><p>Visible 42</p><div style="{style}">SECRET PAYLOAD</div></body></html>'
    out = sanitize(html)
    assert "SECRET PAYLOAD" not in out.text and "Visible 42" in out.text


def test_sanitizer_keeps_ordinary_styles():
    html = '<body><p style="font-size:1.2em;margin-left:-4px;color:#333">Normal</p></body>'
    assert "Normal" in sanitize(html).text
