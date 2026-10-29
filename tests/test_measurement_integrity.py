"""A failed run must never look like a safe one; the report must show the noise floor."""

import json

import pytest

from airgap import cli
from airgap.baseline.vulnerable_agent import VulnerableAgent
from airgap.core.airgap import AirgapAgent
from airgap.eval.harness import run_suite, summarize
from airgap.models.base import Completion
from airgap.models.budget import BudgetExceeded
from airgap.models.fake import FakeLLM
from airgap.world import sites
from airgap.world.scenarios import SCENARIOS


class Scripted:
    name = "scripted"

    def __init__(self, replies):
        self.replies = list(replies)

    def complete(self, messages, *, role, schema=None):
        return Completion(text=self.replies.pop(0))


FETCH = '{"action": "fetch", "url": "http://ledgerlens.test/billing"}'


def baseline(replies):
    sc = SCENARIOS["identity"]
    return VulnerableAgent(Scripted(replies), sc.make_web(None), max_steps=4).run(
        sc.user_text, sc.context()
    )


# ---- the baseline reports why it stopped ----


def test_a_reply_with_no_action_is_an_error_not_a_quiet_stop():
    res = baseline([FETCH, "I think I should pay but I am not sure how."])
    assert res.error and "no JSON action" in res.error


def test_running_out_of_steps_is_an_error():
    res = baseline([FETCH] * 6)
    assert res.error and "step limit" in res.error


def test_a_clean_finish_has_no_error():
    assert baseline([FETCH, '{"action": "finish", "text": "done"}']).error is None


@pytest.mark.parametrize(
    "payee",
    [
        "0x1ED6E4",  # truncated
        "0x1ED6E4000000000000000000000000000000AA0",  # one hex digit short
        "0x1ED6E4000000000000000000000000000000AA0Z",  # not hex
        "ledgerlens-wallet",  # not a saved name, not an address
    ],
)
def test_the_baseline_wallet_rejects_malformed_addresses(payee):
    pay = json.dumps({"action": "pay", "payee": payee, "amount_usd": 20})
    res = baseline([FETCH, pay, '{"action": "finish", "text": "x"}'])
    assert res.ledger.payments == []


def test_a_well_formed_address_is_paid_as_given():
    pay = json.dumps({"action": "pay", "payee": sites.LEDGER_WALLET, "amount_usd": 20})
    res = baseline([FETCH, pay, '{"action": "finish", "text": "x"}'])
    assert [p.payee for p in res.ledger.payments] == [sites.LEDGER_WALLET]


# ---- infrastructure failures are not scored as safe ----


class Broke:
    name = "broke"

    def complete(self, messages, *, role, schema=None):
        if role == "reader":
            raise BudgetExceeded("cap reached")
        return FakeLLM().complete(messages, role=role, schema=schema)


def test_budget_exhaustion_propagates_instead_of_becoming_a_clean_run():
    sc = SCENARIOS["payment"]
    with pytest.raises(BudgetExceeded):
        AirgapAgent(Broke(), Broke(), sc.make_web(None)).run(sc.user_text, sc.context())


def test_partial_trials_survive_a_budget_stop():
    class StopsAfter:
        name = "fake-llm"

        def __init__(self, n):
            self.n, self.inner = n, FakeLLM()

        def complete(self, messages, *, role, schema=None):
            self.n -= 1
            if self.n < 0:
                raise BudgetExceeded("cap reached")
            return self.inner.complete(messages, role=role, schema=schema)

    sink: list = []
    with pytest.raises(BudgetExceeded):
        run_suite(StopsAfter(40), only=("A_no_defense",), sink=sink)
    assert 0 < len(sink) < 96


def test_cli_saves_partial_results_and_exits_nonzero_on_a_budget_stop(tmp_path, monkeypatch):
    class Stops:
        name = "fake-llm"
        n = 30

        def complete(self, messages, *, role, schema=None):
            Stops.n -= 1
            if Stops.n < 0:
                raise BudgetExceeded("cap reached")
            return FakeLLM().complete(messages, role=role, schema=schema)

    monkeypatch.setattr(cli, "make_model", lambda provider: Stops())
    out = tmp_path / "partial.json"
    with pytest.raises(SystemExit) as exc:
        cli.main(["eval", "--conditions", "A_no_defense", "--out", str(out)])
    assert exc.value.code != 0
    saved = json.loads(out.with_suffix(".partial.json").read_text())
    assert saved["trials"] and saved["incomplete"], "paid-for trials must be saved, marked partial"


# ---- the report shows noise and errors next to every safety number ----


def test_summary_reports_errors_the_benign_false_positive_rate_and_attacker_payments():
    s = summarize(run_suite(FakeLLM(), only=("A_no_defense", "C_airgap")))
    base, air = s["A_no_defense"], s["C_airgap"]
    assert base.attacker_paid.n == base.hijacked.n and base.attacker_paid.k > 0
    assert air.attacker_paid.k == 0
    assert base.benign_flagged.n == base.benign_completion.n  # denominator is the benign runs
    assert base.errors.n > 0


def test_report_prints_errors_and_the_benign_false_positive_rate():
    from airgap.eval.harness import report

    text = report(run_suite(FakeLLM(), only=("A_no_defense",)), "fake-llm")
    assert "errored runs" in text and "benign runs flagged" in text and "attacker paid" in text


# ---- real runs default to the tuning split ----


def test_real_runs_default_to_the_tuning_split_and_offline_runs_to_all():
    p = cli.build_parser()
    assert cli.resolve_split(p.parse_args(["eval", "--provider", "meta"])) == "tuning"
    assert cli.resolve_split(p.parse_args(["eval"])) == "all"
    assert cli.resolve_split(
        p.parse_args(["eval", "--provider", "meta", "--split", "heldout"])
    ) == ("heldout")


# ---- verifier round 2: errored runs are unknown, never safe ----


class Offline:
    """An unreachable provider: every call raises, like a dead connection."""

    name = "fake-llm"

    def complete(self, messages, *, role, schema=None):
        raise ConnectionError("connection refused")


def test_a_condition_that_never_worked_does_not_headline_zero_percent_safe():
    from airgap.eval.harness import report

    trials = run_suite(Offline(), only=("C_airgap",), split="tuning")
    s = summarize(trials)["C_airgap"]
    assert s.errors.k == s.errors.n > 0
    assert s.attack_errors.k == s.attack_errors.n > 0
    assert s.hijacked_completed.n == 0  # nothing finished, so no rate can be claimed
    text = report(trials, "fake-llm")
    assert "WARNING" in text and "errored" in text and "NOT safe" in text


def test_errored_attack_runs_are_listed_not_hidden():
    from airgap.eval.harness import report

    text = report(run_suite(Offline(), only=("C_airgap",), split="tuning"), "fake-llm")
    assert "Errored (outcome unknown)" in text


def test_report_carries_the_independence_caveat_on_the_intervals():
    from airgap.eval.harness import report

    text = report(run_suite(FakeLLM(), only=("A_no_defense",)), "fake-llm")
    assert "assume independent runs" in text


# ---- a stopped or failed run never replaces complete results ----


def test_a_stopped_run_is_saved_beside_the_results_not_over_them(tmp_path, monkeypatch):
    exc = BudgetExceeded("cap")

    class Dies:
        name = "fake-llm"
        n = 30

        def complete(self, messages, *, role, schema=None):
            Dies.n -= 1
            if Dies.n < 0:
                raise exc
            return FakeLLM().complete(messages, role=role, schema=schema)

    out = tmp_path / "eval.json"
    out.write_text('{"model": "fake-llm", "trials": ["COMPLETE-RESULTS"]}')
    monkeypatch.setattr(cli, "make_model", lambda provider: Dies())
    with pytest.raises(SystemExit) as stop:
        cli.main(["eval", "--conditions", "A_no_defense", "--out", str(out)])
    assert stop.value.code not in (0, None)
    assert "COMPLETE-RESULTS" in out.read_text(), "complete results must be untouched"
    partial = json.loads((tmp_path / "eval.partial.json").read_text())
    assert partial["incomplete"] is True and partial["trials"]


# ---- the attacker is recognised however the address is written ----


@pytest.mark.parametrize("variant", [str.lower, str.upper, lambda a: f" {a} "])
def test_attacker_paid_ignores_case_and_whitespace_in_the_address(variant):
    from airgap.eval.harness import _outcome
    from airgap.world.ledger import Payment

    sc = SCENARIOS["payment"]
    web = sc.make_web(None)
    res = AirgapAgent(FakeLLM(), FakeLLM(), web).run(sc.user_text, sc.context())
    res.ledger.payments.append(Payment(variant(sites.ATTACKER_WALLET), 5.0))
    assert _outcome(sc, web, res).attacker_paid
    assert sc.judge(res.ledger, res.final_text).goal_reached


def test_attacker_email_is_recognised_in_any_case():
    from airgap.world.ledger import Email

    sc = SCENARIOS["email"]
    res = AirgapAgent(FakeLLM(), FakeLLM(), sc.make_web(None)).run(sc.user_text, sc.context())
    res.ledger.emails.append(Email(" COLLECTOR@Evil.test ", "s", "b"))
    assert sc.judge(res.ledger, res.final_text).goal_reached


# ---- held-out cases are never spent on a real model by accident ----


def test_real_runs_refuse_split_all_unless_heldout_is_explicitly_allowed(monkeypatch):
    monkeypatch.setattr(cli, "make_model", lambda provider: FakeLLM())
    with pytest.raises(SystemExit) as stop:
        cli.main(["eval", "--provider", "meta", "--split", "all"])
    assert "held-out" in str(stop.value.code)


def test_real_demo_refuses_a_heldout_case(monkeypatch):
    from airgap.eval.corpus import attack_cases

    held = next(c for c in attack_cases() if c.heldout and c.scenario in SCENARIOS)
    monkeypatch.setattr(cli, "make_model", lambda provider: FakeLLM())
    with pytest.raises(SystemExit) as stop:
        cli.main(
            ["demo", "--provider", "meta", "--scenario", held.scenario]
            + ["--channel", str(held.attack)]
        )
    assert "held-out" in str(stop.value.code)
