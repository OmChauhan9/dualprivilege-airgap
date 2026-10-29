"""Stated cap and task budget for issues #2 (in-limit amount) and #3 (task total)."""

import pytest

from airgap.core.airgap import AirgapAgent
from airgap.core.audit import AuditLog
from airgap.core.policy import PolicyEngine, UserContext, approve_all
from airgap.core.values import trusted, untrusted
from airgap.models.fake import FakeLLM
from airgap.world import sites
from airgap.world.scenarios import EVAL_SCENARIOS

WALLET = sites.HOSTHARBOR_WALLET


def ctx(text: str, **kw) -> UserContext:
    return UserContext(user_text=text, payee_allowlist={"hh": WALLET}, **kw)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Pay it, up to $60.", 60.0),
        ("pay at most $1,250.50 total", 1250.5),
        ("no more than $45", 45.0),
        ("max $20 please", 20.0),
        ("Pay invoice 12.", None),
        ("It costs about 50 dollars", None),
    ],
)
def test_cap_is_parsed_only_from_the_users_own_words(text, expected):
    assert ctx(text).stated_cap_usd == expected


def pay(engine: PolicyEngine, amount, tainted=True):
    value = untrusted(amount, "hostharbor.test") if tainted else trusted(amount)
    decision = engine.check_pay(trusted(WALLET), value, trusted(""))
    args = {
        "payee": WALLET,
        "amount_usd": amount,
        "amount_sources": sorted(value.sources),
    }
    return decision, engine.authorize("pay", args, decision)


def test_amount_over_the_stated_cap_is_denied_even_though_it_is_under_the_limit():
    engine = PolicyEngine(ctx("pay up to $60"), AuditLog(), approve_all)
    engine.note_untrusted_input()
    decision, approved = pay(engine, 95.0)
    assert decision.verdict == "deny" and "P2" in decision.rules and not approved


def test_amount_at_the_per_transaction_limit_is_denied_under_a_lower_cap():
    engine = PolicyEngine(ctx("pay up to $60"), AuditLog(), approve_all)
    engine.note_untrusted_input()
    assert pay(engine, 100.0)[0].verdict == "deny"


def test_stated_cap_bounds_the_running_total_not_just_each_payment():
    engine = PolicyEngine(ctx("pay both, up to $80 in total"), AuditLog(), approve_all)
    engine.note_untrusted_input()
    first, ok1 = pay(engine, 45.0)
    second, ok2 = pay(engine, 40.0)
    assert first.verdict == "confirm" and ok1
    assert second.verdict == "deny" and not ok2


def test_without_a_cap_page_supplied_amounts_share_the_per_transaction_limit_as_budget():
    engine = PolicyEngine(ctx("pay both invoices"), AuditLog(), approve_all)
    engine.note_untrusted_input()
    assert pay(engine, 90.0)[1]
    second, approved = pay(engine, 90.0)
    assert second.verdict == "deny" and not approved  # 180 would be allowed by the session limit


def test_user_stated_amounts_are_not_counted_against_the_page_budget():
    engine = PolicyEngine(ctx("pay 90 now and 90 later"), AuditLog(), approve_all)
    engine.note_untrusted_input()
    assert pay(engine, 90.0, tainted=False)[1]
    assert pay(engine, 90.0, tainted=False)[1]


def run(sid: str, attack: str | None, confirmer=None):
    sc = EVAL_SCENARIOS[sid]
    agent = AirgapAgent(FakeLLM(), FakeLLM(), sc.make_web(attack), confirmer or approve_all)
    res = agent.run(sc.user_text, sc.context())
    return res, sc.judge(res.ledger, res.final_text)


@pytest.mark.parametrize("attack", ["inflated_visible", "amount_at_limit"])
def test_invoice_with_a_stated_cap_stops_in_limit_inflation(attack):
    res, j = run("invoice-cap", attack)
    assert not j.hijacked and not res.ledger.payments


def test_hidden_inflation_is_stripped_by_the_sanitizer_so_the_honest_amount_is_paid():
    res, j = run("invoice-cap", "inflated_hidden")
    assert j.benign_ok and not j.hijacked


def test_two_invoices_with_a_stated_cap_stop_the_inflated_pair():
    res, j = run("invoices-cap", "inflated_pair")
    assert not j.hijacked and not res.ledger.payments


@pytest.mark.parametrize("sid", ["invoice-cap", "invoices-cap"])
def test_stated_cap_tasks_still_complete_when_the_page_is_honest(sid):
    res, j = run(sid, None)
    assert j.benign_ok and not j.hijacked


def test_without_a_cap_the_inflated_pair_is_bounded_to_one_payment_but_not_closed():
    res, j = run("invoices", "inflated_pair")
    assert [p.amount_usd for p in res.ledger.payments] == [90.0]  # was two payments, $180
    assert j.hijacked  # one inflated payment still gets through: published, stating a cap stops it


def test_confirmation_dialog_shows_where_the_amount_came_from_and_the_cap():
    res, _ = run("invoice-cap", None)
    ev = next(e.data for e in res.audit.events if e.kind == "policy" and e.data["action"] == "pay")
    assert ev["args"]["amount_sources"] == ["untrusted:hostharbor.test"]
    assert ev["args"]["stated_cap_usd"] == 60.0
