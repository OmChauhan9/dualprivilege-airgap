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
