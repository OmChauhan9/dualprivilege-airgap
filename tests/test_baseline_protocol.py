"""The unguarded baseline must be a fair opponent: not crippled by its own tool protocol."""

import json

from airgap.baseline.vulnerable_agent import VulnerableAgent
from airgap.models.base import Completion
from airgap.world import sites
from airgap.world.scenarios import SCENARIOS


class Scripted:
    """Replies with prose around JSON and records whether a schema was forced on the model."""

    name = "scripted"

    def __init__(self, replies):
        self.replies = list(replies)
        self.schemas = []

    def complete(self, messages, *, role, schema=None):
        self.schemas.append(schema)
        return Completion(text=self.replies.pop(0))


def run(replies, scenario="identity", attack=None):
    sc = SCENARIOS[scenario]
    model = Scripted(replies)
    res = VulnerableAgent(model, sc.make_web(attack)).run(sc.user_text, sc.context())
    return model, res


FETCH = 'Let me look. {"action": "fetch", "url": "http://ledgerlens.test/billing"}'


def test_no_schema_is_forced_on_the_baseline_model():
    model, _ = run([FETCH, '{"action": "finish", "text": "done"}'])
    assert model.schemas == [None, None]


def test_a_saved_payee_name_resolves_to_its_allowlisted_address():
    pay = 'Paying now. {"action": "pay", "payee": "ledgerlens", "amount_usd": 20, "memo": "m"}'
    _, res = run([FETCH, pay, '{"action": "finish", "text": "paid"}'])
    assert [(p.payee, p.amount_usd) for p in res.ledger.payments] == [(sites.LEDGER_WALLET, 20.0)]


def test_an_address_the_model_supplies_is_used_verbatim_not_rewritten():
    pay = json.dumps({"action": "pay", "payee": sites.ATTACKER_WALLET, "amount_usd": 20})
    _, res = run([FETCH, pay, '{"action": "finish", "text": "paid"}'])
    assert [p.payee for p in res.ledger.payments] == [sites.ATTACKER_WALLET]


def test_a_payment_with_no_valid_amount_is_rejected_like_a_real_tool_would():
    bad = [
        '{"action": "pay", "payee": "ledgerlens"}',
        '{"action": "pay", "payee": "ledgerlens", "amount_usd": "lots"}',
        '{"action": "pay", "payee": "ledgerlens", "amount_usd": -5}',
        '{"action": "pay", "payee": "ledgerlens", "amount_usd": 0}',
    ]
    _, res = run([FETCH, *bad, '{"action": "finish", "text": "gave up"}'])
    assert res.ledger.payments == []
    assert res.final_text == "gave up"


def test_the_error_is_shown_to_the_model_so_it_can_retry():
    pay_bad = '{"action": "pay", "payee": "ledgerlens"}'
    pay_ok = '{"action": "pay", "payee": "ledgerlens", "amount_usd": 20}'
    model, res = run([FETCH, pay_bad, pay_ok, '{"action": "finish", "text": "ok"}'])
    assert [p.amount_usd for p in res.ledger.payments] == [20.0]
