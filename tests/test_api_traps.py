"""Ways the public `Airgap` API could be misused or misread, each refused or made explicit."""

from __future__ import annotations

import json
from typing import Any

import pytest

from airgap import Airgap, Confirmation, Email, Payment
from airgap.models.base import Completion
from airgap.models.fake import FakeLLM
from airgap.world.mock_web import MockWeb

ALICE, BOB, ACME = "0x" + "aa" * 20, "0x" + "bb" * 20, "0x" + "cc" * 20
QUOTE_A, QUOTE_B, BILL = (
    "https://a.example.com/q",
    "https://b.example.org/q",
    "https://bills.test/due",
)


class Scripted:
    """The planner returns `plan`; the reader returns `reads` in order."""

    name = "scripted"

    def __init__(self, plan: dict[str, Any], reads: list[dict[str, Any]] | None = None) -> None:
        self.plan, self.reads = plan, list(reads or [])

    def complete(self, messages: Any, *, role: Any, schema: Any = None) -> Completion:
        obj = self.plan if role == "orchestrator" else self.reads.pop(0)
        return Completion(text=json.dumps(obj), parsed=obj)


def steps(*s: dict[str, Any]) -> dict[str, Any]:
    return {"steps": list(s)}


def email(to: str, body: str = "b") -> dict[str, Any]:
    return {"op": "send_email", "to": {"lit": to}, "subject": "s", "body": body}


CHOOSE_CHEAPER = (
    {"op": "fetch", "out": "da", "url": {"lit": QUOTE_A}},
    {"op": "read", "out": "qa", "doc": "da", "schema_name": "PriceQuote"},
    {"op": "fetch", "out": "db", "url": {"lit": QUOTE_B}},
    {"op": "read", "out": "qb", "doc": "db", "schema_name": "PriceQuote"},
    {
        "op": "choose_min",
        "out": "c",
        "candidates": [
            {"alias": "alice", "value": {"var": "qa", "field": "price_usd"}},
            {"alias": "bob", "value": {"var": "qb", "field": "price_usd"}},
        ],
    },
    {"op": "pay", "payee": {"var": "c", "field": "alias"}, "amount": {"lit": 25}, "memo": "dep"},
)
PAY_THE_BILL = (
    {"op": "fetch", "out": "d", "url": {"lit": BILL}},
    {"op": "read", "out": "b", "doc": "d", "schema_name": "BillingInfo"},
    {
        "op": "pay",
        "payee": {"alias": "acme"},
        "amount": {"var": "b", "field": "amount_due_usd"},
        "memo": "bill",
    },
)
PAGES = MockWeb().add(QUOTE_A, "<p>$30</p>").add(QUOTE_B, "<p>$20</p>").add(BILL, "<p>$90</p>")
QUOTES = [{"price_usd": 30}, {"price_usd": 20}]
BILL_READ = {"amount_due_usd": 90, "billing_period": "monthly"}
ASK = f"Pay a $25 deposit to whichever of {QUOTE_A} and {QUOTE_B} is cheaper, then the {BILL}"


def payer(planner: Any, paid: list[Payment], **kw: Any) -> Airgap:
    return Airgap(
        planner=planner,
        pay=lambda p: paid.append(p),
        payees={"alice": ALICE, "bob": BOB, "acme": ACME},
        transport=PAGES.transport(),
        **kw,
    )


# ---- C4: the recipient send_email receives -------------------------------------------------


def test_send_email_receives_the_allowlisted_spelling_of_the_recipient():
    sent: list[Email] = []
    g = Airgap(
        planner=Scripted(steps(email("Kim@Example.COM"))),
        send_email=sent.append,
        email_recipients={"kim@example.com"},
    )
    res = g.run("Email Kim a note")
    assert res.error is None
    assert [e.to for e in sent] == ["kim@example.com"]


def test_a_recipient_that_only_unicode_case_folding_matches_is_never_sent_to():
    sent: list[Email] = []
    kelvin = "Kim@example.com"  # KELVIN SIGN, which str.lower() folds to "k"
    g = Airgap(
        planner=Scripted(steps(email(kelvin))),
        send_email=sent.append,
        email_recipients={"kim@example.com"},
    )
    res = g.run("Email Kim a note")
    assert sent == []
    assert res.error or res.blocked


# ---- C7: the confirmation names the sites that chose the payee -----------------------------


def test_a_confirmation_for_a_payee_chosen_by_pages_names_those_pages():
    seen: list[Confirmation] = []
    paid: list[Payment] = []
    g = payer(
        Scripted(steps(*CHOOSE_CHEAPER), list(QUOTES)),
        paid,
        confirm=lambda c: bool(seen.append(c)),
    )
    g.run(ASK)
    (c,) = seen
    assert c.rules == ("P6",)
    assert c.call == Payment(BOB, 25.0, "dep")
    assert c.amount_read_from == ("example.com", "example.org")


def test_a_literal_amount_paid_to_a_page_chosen_payee_does_not_use_the_page_amount_budget():
    paid: list[Payment] = []
    g = payer(
        Scripted(steps(*CHOOSE_CHEAPER, *PAY_THE_BILL), [*QUOTES, BILL_READ]),
        paid,
        confirm=lambda c: True,
    )
    res = g.run(ASK)
    assert paid == [Payment(BOB, 25.0, "dep"), Payment(ACME, 90.0, "bill")], res.blocked


# ---- C8: collections given as one string, and domains P5 can never match --------------------


@pytest.mark.parametrize(
    "kw,argument",
    [
        ({"domains": "example.com"}, "domains"),
        ({"email_recipients": "kim@example.com"}, "email_recipients"),
        ({"payees": "acme"}, "payees"),
        ({"private_hosts": "localhost"}, "private_hosts"),
    ],
)
def test_a_single_string_where_a_collection_belongs_is_refused_naming_the_argument(kw, argument):
    with pytest.raises(TypeError, match=argument):
        Airgap(planner=FakeLLM(), **kw)


def test_a_domain_below_the_registrable_domain_is_refused_naming_the_one_to_use():
    with pytest.raises(ValueError, match=r"domains: 'api\.example\.com'.*'example\.com'"):
        Airgap(planner=FakeLLM(), domains=["api.example.com"])


@pytest.mark.parametrize("entry", ["Example.COM", "example.com."])
def test_a_domain_in_another_case_or_with_a_trailing_dot_still_allows_its_hosts(entry):
    paid: list[Payment] = []
    plan = steps(
        {"op": "fetch", "out": "d", "url": {"lit": QUOTE_A}},
        {"op": "read", "out": "q", "doc": "d", "schema_name": "PriceQuote"},
        {"op": "answer", "template": "{p}", "args": {"p": {"var": "q", "field": "price_usd"}}},
    )
    res = payer(Scripted(plan, [{"price_usd": 30}]), paid, domains=[entry]).run("What is the quote")
    assert res.error is None and res.blocked == []


# ---- C9, S6, K13: functions the guard calls are checked when it is built --------------------


def test_a_missing_planner_is_refused_naming_planner():
    with pytest.raises((TypeError, ValueError), match="planner"):
        Airgap(planner=None)  # type: ignore[arg-type]


@pytest.mark.parametrize("name", ["pay", "send_email", "confirm"])
def test_a_function_argument_that_is_not_callable_is_refused_naming_it(name):
    with pytest.raises((TypeError, ValueError), match=name):
        Airgap(planner=FakeLLM(), **{name: "yes"})


def test_an_object_with_an_async_call_method_is_refused_like_an_async_function():
    class AsyncPay:
        async def __call__(self, p: Payment) -> None:
            return None

    with pytest.raises(ValueError, match="pay is an async function"):
        Airgap(planner=FakeLLM(), pay=AsyncPay())


def test_confirm_none_refuses_every_confirmation_like_the_default():
    paid: list[Payment] = []
    res = payer(Scripted(steps(*CHOOSE_CHEAPER), list(QUOTES)), paid, confirm=None).run(ASK)
    assert paid == []
    assert res.error is None
    assert [b.split(":")[0] for b in res.blocked] == [f"pay 25 to {BOB}"]


def test_a_payment_refused_because_confirm_raised_is_listed_in_blocked():
    def confirm(c: Confirmation) -> bool:
        raise RuntimeError("dialog crashed")

    paid: list[Payment] = []
    res = payer(Scripted(steps(*CHOOSE_CHEAPER), list(QUOTES)), paid, confirm=confirm).run(ASK)
    assert paid == []
    assert res.error == "ConfirmFailed: the confirm callback raised RuntimeError"
    assert [b.split(":")[0] for b in res.blocked] == [f"pay 25 to {BOB}"]
    assert "dialog crashed" not in " ".join(res.summary)


# ---- S7: a confirm answer other than True ---------------------------------------------------


@pytest.mark.parametrize("answer", [False, "y", 1])
def test_a_refused_confirmation_says_confirmation_was_not_given(answer):
    paid: list[Payment] = []
    res = payer(Scripted(steps(*CHOOSE_CHEAPER), list(QUOTES)), paid, confirm=lambda c: answer).run(
        ASK
    )
    (line,) = res.blocked
    assert "confirmation was not given" in line
    assert repr(answer) not in line.split(":", 1)[1]


# ---- K9: the email idempotency key ----------------------------------------------------------


def test_the_email_key_covers_the_body_and_stays_the_same_for_the_same_run_id():
    def keys(body: str) -> list[str]:
        sent: list[Email] = []
        g = Airgap(
            planner=Scripted(steps(email("kim@example.com", body))),
            send_email=sent.append,
            email_recipients={"kim@example.com"},
        )
        g.run("Email Kim", run_id="msg-1")
        return [e.key for e in sent]

    assert keys("first") == keys("first")
    assert keys("first") != keys("second")


# ---- S1: an empty plan is not silent --------------------------------------------------------


def test_a_plan_with_no_steps_says_so_in_the_summary():
    res = Airgap(planner=Scripted(steps())).run("Pay my landlord the rent")
    assert res.error is None
    assert "the planner made no plan steps" in res.summary
