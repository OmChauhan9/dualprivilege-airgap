"""Hard spending cap: a mistake must not be able to spend your credit or your card."""

import json
import math
import multiprocessing
import threading
import time

import pytest

from airgap.models.base import Completion
from airgap.models.budget import (
    HARD_MAX_USD,
    MAX_OUTPUT_TOKENS,
    BudgetedModel,
    BudgetExceeded,
    UnknownPrice,
    cap_from_env,
    cost_usd,
    read_ledger,
)


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


# ---- hardening: shared ledger, missing usage, bad values, honest worst case ---------------------

# Big enough that the worst case covers what the stub reports, so the cap holds exactly.
BIG = [{"role": "user", "content": "x" * 30_000}]


class FitStub(Stub):
    """Reports usage no larger than the worst case for BIG, optionally slowly to force overlap."""

    def __init__(self, delay=0.0, **kw):
        super().__init__(prompt=10_000, completion=1_000, **kw)
        self.delay = delay
        self.lock = threading.Lock()

    def complete(self, messages, *, role, schema=None):
        time.sleep(self.delay)
        with self.lock:
            self.calls += 1
        return Completion(text="{}", prompt_tokens=self.prompt, completion_tokens=self.completion)


FIT_COST = cost_usd("muse-spark-1.3", 10_000, 1_000)


def _spend_until_refused(model) -> int:
    calls = 0
    while True:
        try:
            model.complete(BIG, role="agent")
        except BudgetExceeded:
            return calls
        calls += 1


def test_two_guards_on_one_ledger_share_one_cap(tmp_path):
    """The server builds a guard per request: neither may forget what the other spent."""
    ledger = tmp_path / "s.json"
    inner = Stub(prompt=100_000, completion=50_000)  # about $0.34 per call
    a = BudgetedModel(inner, cap_usd=0.50, ledger=ledger)
    b = BudgetedModel(inner, cap_usd=0.50, ledger=ledger)
    for m in (a, b, a, b):
        try:
            m.complete(MSGS, role="agent")
        except BudgetExceeded:
            pass
    per_call = cost_usd("muse-spark-1.3", 100_000, 50_000)
    assert read_ledger(ledger).spent_usd == pytest.approx(inner.calls * per_call)
    assert inner.calls == 2  # the second guard sees the first call and refuses the third


def test_threads_on_one_ledger_record_every_call_and_never_pass_the_cap(tmp_path):
    ledger, cap = tmp_path / "s.json", 0.50
    inner = FitStub(delay=0.002)
    counts: list[int] = []

    def worker():
        counts.append(_spend_until_refused(BudgetedModel(inner, cap_usd=cap, ledger=ledger)))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    state = read_ledger(ledger)
    assert sum(counts) == inner.calls > 0
    assert state.spent_usd == pytest.approx(inner.calls * FIT_COST)
    assert state.spent_usd <= cap
    assert state.reserved_usd == pytest.approx(0.0, abs=1e-9)


def _process_worker(args) -> int:
    ledger, cap = args
    return _spend_until_refused(BudgetedModel(FitStub(delay=0.001), cap_usd=cap, ledger=ledger))


def test_processes_on_one_ledger_record_every_call_and_never_pass_the_cap(tmp_path):
    ledger, cap = tmp_path / "s.json", 0.50
    with multiprocessing.get_context("spawn").Pool(3) as pool:
        counts = pool.map(_process_worker, [(ledger, cap)] * 3)
    state = read_ledger(ledger)
    assert sum(counts) > 0
    assert state.spent_usd == pytest.approx(sum(counts) * FIT_COST)
    assert state.spent_usd <= cap
    assert state.reserved_usd == pytest.approx(0.0, abs=1e-9)


def test_a_failed_call_releases_its_reservation(tmp_path):
    class Boom(Stub):
        def complete(self, messages, *, role, schema=None):
            raise ConnectionError("network down")

    ledger = tmp_path / "s.json"
    m = BudgetedModel(Boom(), cap_usd=1.0, ledger=ledger)
    with pytest.raises(ConnectionError):
        m.complete(MSGS, role="agent")
    state = read_ledger(ledger)
    assert state.spent_usd == 0.0 and state.reserved_usd == pytest.approx(0.0, abs=1e-12)


def test_an_in_flight_call_is_visible_to_other_guards_as_a_reservation(tmp_path):
    ledger = tmp_path / "s.json"
    seen: dict = {}

    class Peek(Stub):
        def complete(self, messages, *, role, schema=None):
            seen["state"] = read_ledger(ledger)
            return super().complete(messages, role=role, schema=schema)

    m = BudgetedModel(Peek(), cap_usd=1.0, ledger=ledger)
    m.complete(MSGS, role="agent")
    assert seen["state"].reserved_usd == pytest.approx(m.worst_case_usd(MSGS))
    assert read_ledger(ledger).reserved_usd == pytest.approx(0.0, abs=1e-12)


def test_missing_usage_is_charged_at_the_worst_case(tmp_path):
    class NoUsage(Stub):
        def complete(self, messages, *, role, schema=None):
            self.calls += 1
            return Completion(text="{}", usage_known=False)

    ledger = tmp_path / "s.json"
    m = BudgetedModel(NoUsage(), cap_usd=1.0, ledger=ledger)
    m.complete(MSGS, role="agent")
    assert read_ledger(ledger).spent_usd == pytest.approx(m.worst_case_usd(MSGS))
    assert m.spent_usd > 0


def test_fifty_calls_without_usage_cannot_cost_nothing(tmp_path):
    class NoUsage(Stub):
        def complete(self, messages, *, role, schema=None):
            self.calls += 1
            return Completion(text="{}", usage_known=False)

    inner = NoUsage()
    m = BudgetedModel(inner, cap_usd=0.10, ledger=tmp_path / "s.json")
    with pytest.raises(BudgetExceeded):
        for _ in range(50):
            m.complete(MSGS, role="agent")
    assert inner.calls < 50 and m.spent_usd <= 0.10


@pytest.mark.parametrize("bad", [-1, None, 1.5, "7"])
def test_nonsense_token_counts_are_charged_at_the_worst_case(tmp_path, bad):
    class Odd(Stub):
        def complete(self, messages, *, role, schema=None):
            return Completion(text="{}", prompt_tokens=bad, completion_tokens=bad)  # type: ignore[arg-type]

    m = BudgetedModel(Odd(), cap_usd=1.0, ledger=tmp_path / "s.json")
    m.complete(MSGS, role="agent")
    assert m.spent_usd == pytest.approx(m.worst_case_usd(MSGS))


def _fake_meta(usage):
    import airgap.models.meta as meta

    class Resp:
        class _C:
            class message:  # noqa: N801
                content = "{}"

        choices = [_C]
        usage = None

    Resp.usage = usage

    class Client:
        class chat:  # noqa: N801
            class completions:  # noqa: N801
                @staticmethod
                def create(**kw):
                    return Resp

    m = meta.MetaModel.__new__(meta.MetaModel)
    m.name, m.temperature = "muse-spark-1.3", 0.7
    m.__dict__["_client"] = Client
    return m


def test_meta_marks_missing_usage_as_unknown():
    comp = _fake_meta(None).complete([{"role": "user", "content": "hi"}], role="agent")
    assert comp.usage_known is False


def test_meta_marks_reported_usage_as_known():
    class Usage:
        prompt_tokens, completion_tokens = 12, 34

    comp = _fake_meta(Usage).complete([{"role": "user", "content": "hi"}], role="agent")
    assert comp.usage_known is True
    assert (comp.prompt_tokens, comp.completion_tokens) == (12, 34)


def test_meta_marks_partial_usage_as_unknown():
    class Usage:
        prompt_tokens, completion_tokens = 12, None

    comp = _fake_meta(Usage).complete([{"role": "user", "content": "hi"}], role="agent")
    assert comp.usage_known is False


def test_real_client_never_retries_behind_the_guards_back(monkeypatch):
    """Each SDK retry is another billed request, but only one would reach the ledger."""
    import sys
    import types

    import airgap.models.meta as meta

    built: dict = {}

    class FakeOpenAI:
        def __init__(self, **kw):
            built.update(kw)

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=FakeOpenAI))
    meta.MetaModel(api_key="not-a-real-key")
    assert built["max_retries"] == 0


BAD_CAPS = ["nan", "NaN", "inf", "-inf", "-1", "0", "0.0", "", "five", "1e9", str(HARD_MAX_USD + 1)]


@pytest.mark.parametrize("raw", BAD_CAPS)
def test_cap_from_env_refuses_bad_values_instead_of_defaulting(monkeypatch, raw):
    monkeypatch.setenv("AIRGAP_BUDGET_USD", raw)
    with pytest.raises(BudgetExceeded, match="AIRGAP_BUDGET_USD"):
        cap_from_env()


def test_cap_from_env_accepts_a_sane_value_and_defaults_when_unset(monkeypatch):
    monkeypatch.setenv("AIRGAP_BUDGET_USD", "12.5")
    assert cap_from_env() == 12.5
    monkeypatch.delenv("AIRGAP_BUDGET_USD")
    assert 0 < cap_from_env() <= HARD_MAX_USD


@pytest.mark.parametrize(
    "cap", [float("nan"), float("inf"), -1.0, 0.0, HARD_MAX_USD + 0.01, True, "1"]
)
def test_guard_refuses_a_bad_cap(tmp_path, cap):
    inner = Stub()
    with pytest.raises(BudgetExceeded):
        m = BudgetedModel(inner, cap_usd=cap, ledger=tmp_path / "s.json")
        m.complete(MSGS, role="agent")
    assert inner.calls == 0


@pytest.mark.parametrize(
    "content",
    [
        '{"spent_usd": NaN}',
        '{"spent_usd": Infinity}',
        '{"spent_usd": -Infinity}',
        '{"spent_usd": -5}',
        '{"spent_usd": "0.1"}',
        '{"spent_usd": true}',
        '{"spent_usd": 0.1, "reserved_usd": NaN}',
        '{"spent_usd": 0.1, "reserved_usd": -1}',
        "[]",
        "{}",
    ],
)
def test_a_poisoned_ledger_fails_closed(tmp_path, content):
    ledger = tmp_path / "s.json"
    ledger.write_text(content)
    inner = Stub()
    with pytest.raises(BudgetExceeded, match="ledger"):
        m = BudgetedModel(inner, cap_usd=1.0, ledger=ledger)
        m.complete(MSGS, role="agent")
    assert inner.calls == 0


def test_ledger_poisoned_after_construction_still_fails_closed(tmp_path):
    ledger = tmp_path / "s.json"
    inner = Stub()
    m = BudgetedModel(inner, cap_usd=1.0, ledger=ledger)
    ledger.write_text('{"spent_usd": NaN}')
    with pytest.raises(BudgetExceeded):
        m.complete(MSGS, role="agent")
    assert inner.calls == 0


def test_an_old_ledger_without_reservations_still_reads(tmp_path):
    ledger = tmp_path / "s.json"
    ledger.write_text('{"spent_usd": 0.25, "model": "muse-spark-1.3"}')
    state = read_ledger(ledger)
    assert (state.spent_usd, state.reserved_usd) == (0.25, 0.0)


def test_output_limit_sent_is_the_one_the_worst_case_assumes():
    import airgap.models.budget as budget
    import airgap.models.meta as meta

    assert meta.MAX_OUTPUT_TOKENS is budget.MAX_OUTPUT_TOKENS


@pytest.mark.parametrize("text", ["x" * 3000, "€" * 3000, "😀" * 3000, "a b\n" * 1000])
def test_worst_case_bounds_a_call_that_uses_every_byte_and_the_full_output(tmp_path, text):
    """Byte-level tokenizers emit at most one token per UTF-8 byte; max_tokens caps the output."""
    m = BudgetedModel(Stub(), cap_usd=1.0, ledger=tmp_path / "s.json")
    msgs = [{"role": "user", "content": text}]
    real_max = cost_usd(m.name, len(text.encode()), MAX_OUTPUT_TOKENS)
    assert m.worst_case_usd(msgs) >= real_max


def test_worst_case_counts_the_schema_that_is_sent_with_the_prompt(tmp_path):
    m = BudgetedModel(Stub(), cap_usd=1.0, ledger=tmp_path / "s.json")
    schema = {"type": "object", "properties": {f"f{i}": {"type": "string"} for i in range(500)}}
    with_schema = m.worst_case_usd(MSGS, schema)
    sent = len(json.dumps(schema).encode())
    assert with_schema - m.worst_case_usd(MSGS) >= cost_usd(m.name, sent, 0)


def test_the_hard_maximum_is_finite_and_modest():
    assert math.isfinite(HARD_MAX_USD) and 0 < HARD_MAX_USD <= 50


def test_show_spend_reports_reservations_and_refuses_a_poisoned_ledger(
    monkeypatch, tmp_path, capsys
):
    from airgap import cli

    ledger = tmp_path / "s.json"
    monkeypatch.setenv("AIRGAP_SPEND_LEDGER", str(ledger))
    monkeypatch.setenv("AIRGAP_BUDGET_USD", "2")
    ledger.write_text('{"spent_usd": 0.5, "reserved_usd": 0.25}')
    cli.show_spend(None)  # type: ignore[arg-type]
    out = capsys.readouterr().out
    assert "$0.5000" in out and "$0.2500" in out and "$2.00" in out
    ledger.write_text('{"spent_usd": NaN}')
    with pytest.raises(SystemExit, match="ledger"):
        cli.show_spend(None)  # type: ignore[arg-type]
