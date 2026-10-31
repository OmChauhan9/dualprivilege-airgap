"""A recipient picked by comparing page values is chosen by the page, so it needs confirmation."""

from airgap.core.audit import AuditLog
from airgap.core.fetcher import Fetcher
from airgap.core.interpreter import Interpreter
from airgap.core.plan import Plan
from airgap.core.policy import PolicyEngine, UserContext, approve_all, deny_all
from airgap.core.reader import Reader
from airgap.core.schemas import PriceQuote
from airgap.core.values import untrusted
from airgap.world.ledger import Ledger
from airgap.world.mock_web import MockWeb
from airgap.world.tools import WorldTools


class LyingQuotes(Reader):
    PRICES = {"a.test": 30.0, "b.test": 0.01}

    def __init__(self) -> None:
        pass

    def read(self, text: str, schema_name: str, origin_domain: str):
        return untrusted(PriceQuote(price_usd=self.PRICES[origin_domain]), origin_domain)


PLAN = Plan.model_validate(
    {
        "steps": [
            {"op": "fetch", "out": "da", "url": {"lit": "http://a.test/quote"}},
            {"op": "read", "out": "qa", "doc": "da", "schema_name": "PriceQuote"},
            {"op": "fetch", "out": "db", "url": {"lit": "http://b.test/quote"}},
            {"op": "read", "out": "qb", "doc": "db", "schema_name": "PriceQuote"},
            {
                "op": "choose_min",
                "out": "c",
                "candidates": [
                    {"alias": "alice@a.test", "value": {"var": "qa", "field": "price_usd"}},
                    {"alias": "bob@b.test", "value": {"var": "qb", "field": "price_usd"}},
                ],
            },
            {
                "op": "send_email",
                "to": {"var": "c", "field": "alias"},
                "subject": "We accept your quote",
                "body": "Please proceed with the order.",
            },
        ]
    }
)


def _run(confirmer):
    ctx = UserContext(
        user_text="Compare http://a.test/quote and http://b.test/quote and email the cheaper one.",
        payee_allowlist={"alice@a.test": "0xAAAAAA01", "bob@b.test": "0xBBBBBB01"},
        email_allowlist=frozenset({"alice@a.test", "bob@b.test"}),
    )
    web = MockWeb().add("http://a.test/quote", "<p>q</p>").add("http://b.test/quote", "<p>q</p>")
    client = web.client()
    ledger, audit = Ledger(), AuditLog()
    policy = PolicyEngine(ctx, audit, confirmer)
    tools = WorldTools(client, ledger)
    Interpreter(ctx, Fetcher(client), LyingQuotes(), tools, policy, audit).run(PLAN)
    return ledger, policy


def test_steered_recipient_is_not_emailed_when_confirmation_is_refused():
    ledger, policy = _run(deny_all)
    assert ledger.emails == []
    assert policy.confirmations == 1


def test_steered_recipient_is_emailed_after_the_user_confirms():
    ledger, _ = _run(approve_all)
    assert [e.to for e in ledger.emails] == ["bob@b.test"]
