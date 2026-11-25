"""Real models wrap JSON in prose and skip steps; the pipeline copes without weakening checks."""

import json

import pytest

from airgap.baseline.vulnerable_agent import VulnerableAgent
from airgap.core.orchestrator import Orchestrator
from airgap.core.plan import Plan, validate_plan
from airgap.core.policy import UserContext
from airgap.core.reader import Reader, ReaderError
from airgap.models.base import Completion
from airgap.models.jsonutil import extract_json_object
from airgap.world.scenarios import SCENARIOS


@pytest.mark.parametrize(
    "text,expected",
    [
        ('{"a": 1}', {"a": 1}),
        (
            'I will fetch it.{"action": "fetch", "url": "http://x.test/"}',
            {"action": "fetch", "url": "http://x.test/"},
        ),
        ('Sure!\n```json\n{"a": {"b": [1, 2]}}\n```\nDone.', {"a": {"b": [1, 2]}}),
        ('note {not json} then {"ok": true}', {"ok": True}),
        ('{"s": "a } brace in a string"}', {"s": "a } brace in a string"}),
        ('{"first": 1} {"second": 2}', {"first": 1}),
    ],
)
def test_extract_json_object_finds_the_first_object(text, expected):
    assert extract_json_object(text) == expected


@pytest.mark.parametrize("text", ["", "no json here", "[1, 2, 3]", "{broken", '"just a string"'])
def test_extract_json_object_returns_none_when_there_is_no_object(text):
    assert extract_json_object(text) is None


class Chatty:
    """Wraps every JSON answer in prose and leaves `parsed` empty, like the real model did."""

    name = "chatty"

    def __init__(self, replies):
        self.replies = list(replies)

    def complete(self, messages, *, role, schema=None):
        return Completion(text=self.replies.pop(0))


def test_unguarded_agent_runs_when_the_model_adds_prose_around_its_json():
    sc = SCENARIOS["payment"]
    model = Chatty(
        [
            'I will fetch it. {"action": "fetch", "url": "http://ledgerlens.test/portfolio"}',
            'Here you go: {"action": "finish", "text": "total is 12,345.67"}',
        ]
    )
    res = VulnerableAgent(model, sc.make_web(None)).run(sc.user_text, sc.context())
    assert res.final_text == "total is 12,345.67"
    assert [e.kind for e in res.audit.events] == ["agent_action", "agent_action"]


def test_reader_accepts_prose_around_valid_json_and_still_fails_closed_on_bad_values():
    ok = Reader(Chatty(['Result: {"unread_count": 7, "has_security_alert": false}']))
    assert ok.read("x", "InboxDigest", "maildigest.test").value.unread_count == 7
    bad = Reader(Chatty(['Result: {"unread_count": -5, "has_security_alert": false}']))
    with pytest.raises(ReaderError):
        bad.read("x", "InboxDigest", "maildigest.test")
    with pytest.raises(ReaderError):
        Reader(Chatty(["I could not find it."])).read("x", "InboxDigest", "maildigest.test")


def test_reader_rejects_extra_fields_even_when_wrapped_in_prose():
    extra = Reader(Chatty(['{"unread_count": 1, "has_security_alert": false, "wallet": "0xA"}']))
    with pytest.raises(ReaderError):
        extra.read("x", "InboxDigest", "maildigest.test")


def test_plan_with_an_answer_template_that_uses_an_undefined_placeholder_is_rejected():
    ctx = UserContext(user_text="see http://ledgerlens.test/portfolio")
    plan = Plan.model_validate(
        {
            "steps": [
                {"op": "fetch", "out": "d", "url": {"lit": "http://ledgerlens.test/portfolio"}},
                {"op": "answer", "template": "Total is {total}", "args": {}},
            ]
        }
    )
    assert any("total" in p and "placeholder" in p for p in validate_plan(plan, ctx))


def test_plan_template_may_not_reference_a_document_handle():
    """The raw page must never be rendered into an answer; only read, typed values may be."""
    ctx = UserContext(user_text="see http://ledgerlens.test/portfolio")
    plan = Plan.model_validate(
        {
            "steps": [
                {"op": "fetch", "out": "d", "url": {"lit": "http://ledgerlens.test/portfolio"}},
                {"op": "answer", "template": "{page}", "args": {"page": {"var": "d"}}},
            ]
        }
    )
    assert any("document" in p for p in validate_plan(plan, ctx))


def test_orchestrator_repairs_a_plan_with_a_bad_placeholder_using_the_validator_feedback():
    sc = SCENARIOS["payment"]
    bad = {"steps": [{"op": "answer", "template": "Total {total}", "args": {}}]}
    good = {
        "steps": [
            {"op": "fetch", "out": "d", "url": {"lit": "http://ledgerlens.test/portfolio"}},
            {"op": "read", "out": "p", "doc": "d", "schema_name": "PortfolioSummary"},
            {
                "op": "answer",
                "template": "Total {t}",
                "args": {"t": {"var": "p", "field": "total_usd"}},
            },
        ]
    }
    model = Chatty([f"Here is the plan: {json.dumps(bad)}", f"Fixed: {json.dumps(good)}"])
    plan = Orchestrator(model).plan(sc.user_text, sc.context())
    assert [s.op for s in plan.steps] == ["fetch", "read", "answer"]


# ---- field names are checked before anything runs, so repair can fire ------------------


def _plan(*steps):
    return Plan.model_validate({"steps": list(steps)})


FETCH_A = {"op": "fetch", "out": "d", "url": {"lit": "http://shop-a.test/plan"}}
READ_A = {"op": "read", "out": "q", "doc": "d", "schema_name": "PriceQuote"}
CTX = UserContext(
    user_text="see http://shop-a.test/plan",
    payee_allowlist={"shop_a": "0x5A0A000000000000000000000000000000000A01"},
)


def _answer(var, field):
    return {"op": "answer", "template": "{x}", "args": {"x": {"var": var, "field": field}}}


def test_unknown_field_of_a_read_value_is_rejected():
    problems = validate_plan(_plan(FETCH_A, READ_A, _answer("q", "cost")), CTX)
    assert any("cost" in p and "PriceQuote" in p for p in problems)


def test_known_field_of_a_read_value_is_accepted():
    assert validate_plan(_plan(FETCH_A, READ_A, _answer("q", "price_usd")), CTX) == []


def test_choice_values_expose_alias_and_price_only():
    choose = {
        "op": "choose_min",
        "out": "best",
        "candidates": [{"alias": "shop_a", "value": {"var": "q", "field": "price_usd"}}],
    }
    base = [FETCH_A, READ_A, choose]
    assert validate_plan(_plan(*base, _answer("best", "price")), CTX) == []
    assert validate_plan(_plan(*base, _answer("best", "alias")), CTX) == []
    problems = validate_plan(_plan(*base, _answer("best", "value")), CTX)
    assert any("value" in p and "alias" in p and "price" in p for p in problems)


def test_a_choice_used_without_a_field_is_rejected_before_anything_runs():
    shop_b = "0x5B0B000000000000000000000000000000000B01"
    ctx = UserContext(
        user_text=CTX.user_text, payee_allowlist={**CTX.payee_allowlist, "shop_b": shop_b}
    )
    choose = {
        "op": "choose_min",
        "out": "best",
        "candidates": [{"alias": "shop_a", "value": {"var": "q", "field": "price_usd"}}],
    }
    pay = {"op": "pay", "payee": {"alias": "shop_b"}, "amount": {"lit": 10}}
    bare = {"op": "answer", "template": "{x}", "args": {"x": {"var": "best"}}}
    problems = validate_plan(_plan(FETCH_A, READ_A, choose, pay, bare), ctx)
    assert any("best" in p and "alias" in p and "price" in p for p in problems)


def test_a_field_on_a_fetched_document_is_rejected():
    assert any("document" in p for p in validate_plan(_plan(FETCH_A, _answer("d", "text")), CTX))


def test_planner_prompt_documents_what_choose_min_produces():
    from airgap.core.orchestrator import PLAN_SYSTEM

    assert '"field": "alias"' in PLAN_SYSTEM and '"field": "price"' in PLAN_SYSTEM
    assert "No other fields exist" in PLAN_SYSTEM


# ---- an answer template the interpreter cannot render is rejected before anything runs ----

PAY_CTX = UserContext(user_text="Pay alice 10 dollars.", payee_allowlist={"alice": "0xA11CE00001"})
AMT = {"amt": {"lit": 10.5}}


def _pay_then_answer(template, args):
    return {
        "steps": [
            {"op": "pay", "payee": {"alias": "alice"}, "amount": {"lit": 10.5}, "memo": "x"},
            {"op": "answer", "template": template, "args": args},
        ]
    }


@pytest.mark.parametrize(
    "template,args",
    [
        ("Paid {amt} to {who", AMT),  # unclosed brace
        ("Paid {} to alice", AMT),  # positional field
        ("Paid {0} to alice", AMT),  # positional field
        ("Paid {amt:d} to alice", AMT),  # spec does not fit a float
        ("Paid {amt!r} to alice", AMT),  # conversion
        ("Paid {amt:{w}} to alice", {**AMT, "w": {"lit": 8}}),  # nested field in the spec
        ("Paid {amt:.2f} to alice", {"amt": {"lit": "10.5"}}),  # number spec on text
        ("Paid {amt.real} to alice", {"amt.real": {"lit": 10.5}}),  # attribute access
    ],
)
def test_an_answer_template_that_cannot_render_is_rejected_and_nothing_is_paid(template, args):
    from airgap.core.airgap import Airgap
    from airgap.models.fake import FakeLLM
    from airgap.world.mock_web import MockWeb
    from airgap.world.tools import WorldTools

    plan = _pay_then_answer(template, args)
    assert any("template" in p for p in validate_plan(Plan.model_validate(plan), PAY_CTX))
    guard = Airgap(
        planner=Chatty([json.dumps(plan)] * 2),
        reader=FakeLLM(),
        pay=WorldTools().pay,
        payees=PAY_CTX.payee_allowlist,
        transport=MockWeb().transport(),
    )
    res = guard.run(PAY_CTX.user_text)
    assert res.ledger.payments == []
    assert res.error and res.error.startswith("PlanRejected")


@pytest.mark.parametrize(
    "template,args",
    [
        ("Paid ${amt:,.2f} to alice", AMT),
        ("Paid {amt:>8} to alice, {{braces}} kept", AMT),
        ("Paid {n:d} invoices", {"n": {"lit": 3}}),
        ("Paid {who:>10}", {"who": {"alias": "alice"}}),
    ],
)
def test_answer_templates_the_interpreter_can_render_still_pass(template, args):
    assert validate_plan(Plan.model_validate(_pay_then_answer(template, args)), PAY_CTX) == []


def _answer_with_spec(spec):
    args = {"x": {"var": "q", "field": "price_usd"}}
    return {"op": "answer", "template": "{x:" + spec + "}", "args": args}


def test_a_format_spec_is_checked_against_the_read_fields_type():
    assert validate_plan(_plan(FETCH_A, READ_A, _answer_with_spec(",.2f")), CTX) == []
    problems = validate_plan(_plan(FETCH_A, READ_A, _answer_with_spec("d")), CTX)
    assert any("template" in p for p in problems)
