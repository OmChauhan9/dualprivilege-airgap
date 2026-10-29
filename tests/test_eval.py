import pytest

from airgap.eval.corpus import PUBLISHED_LEAKS
from airgap.eval.harness import failures, run_suite, summarize
from airgap.eval.metrics import Rate, wilson
from airgap.models.fake import FakeLLM


def test_wilson_zero_of_forty_has_nonzero_upper_bound():
    lo, hi = wilson(0, 40)
    assert lo == 0.0
    assert hi == pytest.approx(0.0877, abs=0.001)


def test_rate_string_always_carries_n_and_interval():
    assert "0/40" in str(Rate(0, 40)) and "95% CI" in str(Rate(0, 40))


@pytest.fixture(scope="module")
def trials():
    return run_suite(FakeLLM())


INJECTION = {"hidden_text", "poisoned_metadata", "visible_text"}


def test_suite_covers_all_conditions_and_scores_from_the_ledger(trials):
    table = summarize(trials)
    assert {"A_no_defense", "C_airgap", "C_no_policy", "C_no_sanitizer"} <= set(table)
    injected = [t for t in trials if t.condition == "A_no_defense" and t.category in INJECTION]
    assert injected and all(t.hijacked for t in injected)  # the gate: every injection lands
    airgap = [t for t in trials if t.condition == "C_airgap" and t.category in INJECTION]
    assert not any(t.hijacked for t in airgap)


def test_every_leak_is_published_and_every_published_leak_still_leaks(trials):
    """A new hole cannot appear silently, and a fixed one must be taken off the list."""
    assert {t.case_id for t in failures(trials, "C_airgap")} == set(PUBLISHED_LEAKS)
    assert "steering:visible_text" in PUBLISHED_LEAKS


def test_strict_confirmation_trades_utility_for_safety(trials):
    table = summarize(trials)
    assert table["C_airgap_strict_confirm"].hijacked.k == 0
    strict = table["C_airgap_strict_confirm"].benign_completion
    plausible = table["C_airgap"].benign_completion
    assert strict.k < plausible.k


def test_held_out_results_are_reported_separately(trials):
    from airgap.eval.harness import report

    text = report(trials, "fake-llm")
    held = [t for t in trials if t.condition == "C_airgap" and t.heldout and t.is_attack]
    assert held
    assert f"held-out attacks only ({len(held)}): hijacked" in text


@pytest.fixture(scope="module")
def text(trials):
    from airgap.eval.harness import report

    return report(trials, "fake-llm")


def test_report_breaks_down_hijacked_and_goal_by_category_with_k_of_n(trials, text):
    from airgap.eval.harness import by_category

    cats = by_category(trials, "C_airgap")
    assert {"hidden_text", "in_policy_amount", "split_payment", "redirect"} <= set(cats)
    for name, (hijacked, goal) in cats.items():
        assert hijacked.n == goal.n > 0
        assert f"{name} " in text and str(hijacked) in text


def test_report_lists_every_attack_that_got_through_for_every_condition(trials, text):
    for cond in summarize(trials):
        assert f"Got through {cond} (" in text
        for t in failures(trials, cond):
            assert t.case_id in text
    # Published leaks carry their reason and tracking link next to the case.
    assert "issues/2" in text and "known limitation" in text


def test_report_never_prints_a_bare_percent(text):
    import re

    for line in text.splitlines():
        for pct in re.finditer(r"\(\d+%, 95% CI", line):
            assert re.search(r"\d+/\d+ $", line[: pct.start()]), line


def test_zero_of_n_shows_its_upper_bound(trials, text):
    zero = summarize(trials)["C_airgap"].hijacked
    assert "0/" in str(Rate(0, zero.n)) and "-" in str(Rate(0, zero.n)).split("CI")[1]
    assert "unlisted-domain requests" in text


def test_offline_report_carries_the_loud_plumbing_note(text):
    assert "plumbing" in text and "not evidence about real models" in text
    assert "ablation rows" in text.lower() and "not informative" in text.lower()


def test_results_saved_before_new_trial_fields_still_load(trials):
    from dataclasses import asdict, fields

    from airgap.eval.harness import Trial

    old = {k: v for k, v in asdict(trials[0]).items() if k not in ("category", "heldout")}
    names = {f.name for f in fields(Trial)}
    assert Trial(**{k: v for k, v in old.items() if k in names}).category == ""


def test_saved_results_record_the_model_and_round_trip_through_the_server(tmp_path):
    import json

    from fastapi.testclient import TestClient

    from airgap.eval.harness import save
    from airgap.server.app import create_app

    path = tmp_path / "eval.json"
    save(run_suite(FakeLLM()), path, model_name="fake-llm")
    raw = json.loads(path.read_text())
    assert raw["model"] == "fake-llm" and raw["trials"]
    client = TestClient(create_app(results_path=path, web_dir=tmp_path / "dist"))
    assert client.get("/api/results").json()["model"] == "fake-llm"


def test_run_suite_can_be_limited_to_chosen_conditions():
    trials = run_suite(FakeLLM(), only=("A_no_defense",))
    assert {t.condition for t in trials} == {"A_no_defense"}


def test_run_suite_rejects_an_unknown_condition_name():
    with pytest.raises(ValueError, match="unknown condition"):
        run_suite(FakeLLM(), only=("A_typo",))


def test_report_still_renders_when_only_one_condition_ran():
    from airgap.eval.harness import report

    text = report(run_suite(FakeLLM(), only=("A_no_defense",)), "fake-llm")
    assert "A_no_defense" in text
