"""Hard spending cap: a mistake must not be able to spend your credit or your card."""

import json

import pytest

from airgap.models.base import Completion
from airgap.models.budget import BudgetedModel, BudgetExceeded, UnknownPrice, cost_usd


class Stub:
    """Reports fixed token usage per call, like a real provider would."""

    def __init__(self, name="muse-spark-1.3", prompt=1000, completion=500):
        self.name = name
        self.prompt, self.completion = prompt, completion
        self.calls = 0

    def complete(self, messages, *, role, schema=None):
        self.calls += 1
        return Completion(text="{}", prompt_tokens=self.prompt, completion_tokens=self.completion)


MSGS = [{"role": "user", "content": "x" * 300}]


def test_cost_uses_published_per_million_prices():
    assert cost_usd("muse-spark-1.3", 1_000_000, 0) == pytest.approx(1.25)
    assert cost_usd("muse-spark-1.3", 0, 1_000_000) == pytest.approx(4.25)


def test_unknown_model_is_refused_not_guessed(tmp_path):
    with pytest.raises(UnknownPrice):
        BudgetedModel(Stub(name="mystery-model"), cap_usd=1.0, ledger=tmp_path / "s.json")


def test_contributor_models_have_no_price_entry_and_are_refused(tmp_path):
    with pytest.raises(UnknownPrice):
        BudgetedModel(
            Stub(name="muse-spark-1.3-contributor"), cap_usd=1.0, ledger=tmp_path / "s.json"
        )


def test_actual_usage_is_recorded_and_persisted(tmp_path):
    ledger = tmp_path / "s.json"
    m = BudgetedModel(Stub(), cap_usd=1.0, ledger=ledger)
    m.complete(MSGS, role="agent")
    expected = cost_usd("muse-spark-1.3", 1000, 500)
    assert m.spent_usd == pytest.approx(expected)
    assert json.loads(ledger.read_text())["spent_usd"] == pytest.approx(expected)


def test_cap_spans_runs_through_the_ledger_file(tmp_path):
    ledger = tmp_path / "s.json"
    BudgetedModel(Stub(), cap_usd=1.0, ledger=ledger).complete(MSGS, role="agent")
    again = BudgetedModel(Stub(), cap_usd=1.0, ledger=ledger)
    assert again.spent_usd > 0


def test_call_is_blocked_before_it_is_made_when_worst_case_would_pass_the_cap(tmp_path):
    inner = Stub()
    m = BudgetedModel(inner, cap_usd=0.0001, ledger=tmp_path / "s.json")
    with pytest.raises(BudgetExceeded):
        m.complete(MSGS, role="agent")
    assert inner.calls == 0


def test_calls_stop_once_the_running_total_reaches_the_cap(tmp_path):
    inner = Stub(prompt=100_000, completion=50_000)  # about $0.34 per call
    m = BudgetedModel(inner, cap_usd=0.50, ledger=tmp_path / "s.json")
    m.complete(MSGS, role="agent")
    with pytest.raises(BudgetExceeded):
        for _ in range(5):
            m.complete(MSGS, role="agent")
    assert m.spent_usd <= 0.50 + 0.35  # at most one call's overshoot, never unbounded
    assert inner.calls <= 3


def test_a_corrupt_ledger_fails_closed(tmp_path):
    ledger = tmp_path / "s.json"
    ledger.write_text("not json")
    with pytest.raises(BudgetExceeded):
        BudgetedModel(Stub(), cap_usd=1.0, ledger=ledger)


def test_make_model_wraps_the_real_client_in_the_budget_guard(monkeypatch, tmp_path):
    """The CLI and server can never hand out an unbudgeted real model."""
    import airgap.models.meta as meta
    from airgap import cli

    monkeypatch.setenv("AIRGAP_ALLOW_REAL_CALLS", "1")
    monkeypatch.setenv("AIRGAP_BUDGET_USD", "2.5")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AIRGAP_SPEND_LEDGER", str(tmp_path / "spend.json"))
    monkeypatch.setattr(meta.MetaModel, "from_env", classmethod(lambda cls: Stub()))
    model = cli.make_model("meta")
    assert isinstance(model, BudgetedModel) and model.cap_usd == 2.5


def test_real_client_always_sends_an_output_limit(monkeypatch):
    import airgap.models.meta as meta
    from airgap.models.budget import MAX_OUTPUT_TOKENS

    sent: dict = {}

    class Resp:
        class _C:
            class message:  # noqa: N801
                content = "{}"

        choices = [_C]
        usage = None

    class Client:
        class chat:  # noqa: N801
            class completions:  # noqa: N801
                @staticmethod
                def create(**kw):
                    sent.update(kw)
                    return Resp

    m = meta.MetaModel.__new__(meta.MetaModel)
    m.name, m.temperature = "muse-spark-1.3", 0.7
    m.__dict__["_client"] = Client
    m.complete([{"role": "user", "content": "hi"}], role="agent")
    assert sent["max_tokens"] == MAX_OUTPUT_TOKENS


def test_default_ledger_lives_outside_the_working_directory(monkeypatch, tmp_path):
    """A worktree or checkout can be deleted; the record of what was spent must survive it."""
    from pathlib import Path

    from airgap.models.budget import default_ledger

    monkeypatch.delenv("AIRGAP_SPEND_LEDGER", raising=False)
    assert default_ledger() == Path.home() / ".airgap" / "spend.json"
    monkeypatch.setenv("AIRGAP_SPEND_LEDGER", str(tmp_path / "x.json"))
    assert default_ledger() == tmp_path / "x.json"


def test_ledger_directory_is_created_on_first_write(tmp_path):
    ledger = tmp_path / "nested" / "dir" / "spend.json"
    BudgetedModel(Stub(), cap_usd=1.0, ledger=ledger).complete(MSGS, role="agent")
    assert ledger.exists()
