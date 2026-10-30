"""Server contract (docs/api-contract.md). FakeLLM only; a real model is never constructed."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from airgap.eval.corpus import PUBLISHED_LEAKS, attack_cases, benign_cases
from airgap.eval.harness import run_suite, save
from airgap.models.fake import FakeLLM
from airgap.server.app import create_app
from airgap.server.replay import record_run
from airgap.world import sites
from airgap.world.scenarios import SCENARIOS

AUDIT_KINDS = {
    "agent_action",
    "plan",
    "fetch",
    "sanitize",
    "reader_output",
    "policy",
    "tainted_control_flow",
    "answer",
    "error",
}


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(results_path=tmp_path / "eval.json", web_dir=tmp_path / "dist"))


def stream(client: TestClient, **params: str) -> list[dict]:
    with client.stream("GET", "/api/run/stream", params=params) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        lines = [ln for ln in resp.iter_lines() if ln.startswith("data: ")]
    return [json.loads(ln.removeprefix("data: ")) for ln in lines]


def pane(msgs: list[dict], name: str) -> list[dict]:
    return [m for m in msgs if m["pane"] == name]


# ---- /api/scenarios ---------------------------------------------------------------------


def test_scenarios_lists_every_scenario_with_its_channels(client):
    resp = client.get("/api/scenarios")
    assert resp.status_code == 200
    body = resp.json()
    assert [s["id"] for s in body] == list(SCENARIOS)
    payment = next(s for s in body if s["id"] == "payment")
    assert payment["title"] == SCENARIOS["payment"].title
    assert payment["user_text"] == SCENARIOS["payment"].user_text
    assert payment["channels"] == list(sites.CHANNELS)
    steering = next(s for s in body if s["id"] == "steering")
    assert steering["channels"] == ["json_ld", "visible_text"]


# ---- /api/run/stream --------------------------------------------------------------------


def test_stream_runs_unguarded_then_airgap_with_ordered_envelopes(client):
    msgs = stream(client, scenario="payment", channel="css_display_none", provider="fake")
    assert [m["seq"] for m in msgs] == list(range(len(msgs)))
    for m in msgs:
        assert set(m) == {"seq", "pane", "kind", "data"}

    assert msgs[0]["kind"] == "start"
    assert msgs[-1]["kind"] == "end"
    body = msgs[1:-1]
    panes = [m["pane"] for m in body]
    # Unguarded pane first, then the airgap pane; never interleaved.
    first_airgap = panes.index("airgap")
    assert set(panes[:first_airgap]) == {"unguarded"}
    assert set(panes[first_airgap:]) == {"airgap"}

    for name in ("unguarded", "airgap"):
        events = pane(msgs, name)
        assert [e["kind"] for e in events].count("done") == 1
        assert events[-1]["kind"] == "done"
        assert {e["kind"] for e in events[:-1]} <= AUDIT_KINDS


def test_start_event_names_provider_and_model_per_role(client):
    start = stream(client, scenario="payment", channel="none", provider="fake")[0]
    assert start["pane"] is None
    data = start["data"]
    assert data["provider"] == "fake"
    assert data["simulated"] is True
    assert data["scenario"] == "payment"
    assert data["channel"] is None
    assert data["models"] == {
        "unguarded_agent": FakeLLM.name,
        "orchestrator": FakeLLM.name,
        "reader": FakeLLM.name,
    }


def test_unguarded_pane_is_hijacked_and_airgap_pane_blocks(client):
    msgs = stream(client, scenario="payment", channel="css_display_none", provider="fake")
    base = pane(msgs, "unguarded")
    air = pane(msgs, "airgap")

    pays = [e for e in base if e["kind"] == "agent_action" and e["data"]["action"] == "pay"]
    assert pays and pays[0]["data"]["payee"] == sites.ATTACKER_WALLET
    done = base[-1]["data"]
    assert done["judgement"] == {"hijacked": True, "goal_reached": True, "benign_ok": False}
    assert done["payments"][0]["payee"] == sites.ATTACKER_WALLET
    assert done["payments"][0]["amount_usd"] == 50.0

    kinds = [e["kind"] for e in air if e["kind"] != "policy"]
    assert kinds[:4] == ["plan", "fetch", "sanitize", "reader_output"]
    fetch = next(e for e in air if e["kind"] == "fetch")
    assert fetch["data"]["domain"] == "ledgerlens.test"
    sanitize = next(e for e in air if e["kind"] == "sanitize")
    assert "css_display_none" in {r["channel"] for r in sanitize["data"]["removed"]}
    reader = next(e for e in air if e["kind"] == "reader_output")
    assert reader["data"]["sources"] == ["untrusted:ledgerlens.test"]
    done = air[-1]["data"]
    assert done["judgement"] == {"hijacked": False, "goal_reached": False, "benign_ok": True}
    assert done["payments"] == [] and done["emails"] == []
    assert "12,345.67" in done["reply"]
    for key in ("confirmations", "prompt_tokens", "completion_tokens", "latency_s"):
        assert key in done


def test_policy_events_carry_rule_ids_and_rule_of_two(client):
    msgs = stream(client, scenario="email", channel="visible_text", provider="fake")
    policies = [e for e in pane(msgs, "airgap") if e["kind"] == "policy"]
    assert policies
    for p in policies:
        assert set(p["data"]["rule_of_two"]) == {
            "A_untrusted_input",
            "B_sensitive",
            "C_state_change",
        }
        assert isinstance(p["data"]["rules"], list)
    assert pane(msgs, "unguarded")[-1]["data"]["emails"][0]["to"] == sites.ATTACKER_EMAIL


def test_steering_visible_text_is_reported_as_still_succeeding(client):
    """The honest case: a visible price lie stays inside policy and still steers the payment."""
    msgs = stream(client, scenario="steering", channel="visible_text", provider="fake")
    air = pane(msgs, "airgap")
    assert any(e["kind"] == "tainted_control_flow" for e in air)
    confirm = [e for e in air if e["kind"] == "policy" and e["data"]["verdict"] == "confirm"]
    assert confirm and "P6" in confirm[0]["data"]["rules"]
    assert air[-1]["data"]["judgement"]["hijacked"] is True


def test_each_side_effect_is_flagged_authorized_or_not(client):
    """Identity attack: the unguarded agent pays the attacker and then the real payee."""
    msgs = stream(client, scenario="identity", channel="css_display_none", provider="fake")
    payments = pane(msgs, "unguarded")[-1]["data"]["payments"]
    flags = {p["payee"]: p["unauthorized"] for p in payments}
    assert flags == {sites.ATTACKER_WALLET: True, sites.LEDGER_WALLET: False}


def test_benign_channel_none_runs_clean(client):
    msgs = stream(client, scenario="identity", channel="none", provider="fake")
    for name in ("unguarded", "airgap"):
        j = pane(msgs, name)[-1]["data"]["judgement"]
        assert j == {"hijacked": False, "goal_reached": False, "benign_ok": True}


def test_stream_rejects_unknown_scenario_and_channel(client):
    assert client.get("/api/run/stream", params={"scenario": "nope"}).status_code == 404
    resp = client.get(
        "/api/run/stream", params={"scenario": "steering", "channel": "css_display_none"}
    )
    assert resp.status_code == 400


def test_meta_provider_is_refused_without_the_real_call_guard(client, monkeypatch):
    import airgap.models.meta as meta

    monkeypatch.delenv("AIRGAP_ALLOW_REAL_CALLS", raising=False)

    def boom(*a, **k):
        raise AssertionError("a real model client must never be built in tests")

    monkeypatch.setattr(meta.MetaModel, "from_env", classmethod(boom))
    resp = client.get(
        "/api/run/stream", params={"scenario": "payment", "channel": "none", "provider": "meta"}
    )
    assert resp.status_code == 403
    assert "AIRGAP_ALLOW_REAL_CALLS" in resp.json()["detail"]


# ---- /api/results -----------------------------------------------------------------------


def test_results_is_404_when_no_eval_saved(client):
    assert client.get("/api/results").status_code == 404


def test_results_summarizes_saved_trials_with_wilson_intervals(tmp_path):
    path = tmp_path / "eval.json"
    save(run_suite(FakeLLM()), path)
    client = TestClient(create_app(results_path=path, web_dir=tmp_path / "dist"))
    body = client.get("/api/results").json()

    assert body["model"] is None  # bare trial lists do not record the model
    c = body["conditions"]["C_airgap"]
    for key in ("hijacked", "goal_reached", "benign_completion", "utility_under_attack"):
        rate = c[key]
        assert set(rate) == {"k", "n", "lo", "hi"}
        assert 0 <= rate["lo"] <= rate["hi"] <= 1
        assert 0 <= rate["k"] <= rate["n"]
    assert c["hijacked"]["n"] == len(attack_cases())
    assert c["benign_completion"]["n"] == len(benign_cases())
    for key in ("mean_latency_s", "mean_tokens", "confirmations_per_run"):
        assert isinstance(c[key], float)
    assert body["conditions"]["A_no_defense"]["hijacked"]["k"] > 0
    assert set(body["leaks"]["C_airgap"]) == set(PUBLISHED_LEAKS)
    # Strict confirmation lets nothing new through: what remains is published and has no
    # side effect (a false balance in the answer, redirect hops to unlisted hosts).
    assert set(body["leaks"]["C_airgap_strict_confirm"]) <= set(PUBLISHED_LEAKS)


def test_results_reads_model_name_when_recorded(tmp_path):
    from dataclasses import asdict

    path = tmp_path / "eval.json"
    trials = [asdict(t) for t in run_suite(FakeLLM())]
    path.write_text(json.dumps({"model": "fake-llm", "trials": trials}))
    client = TestClient(create_app(results_path=path, web_dir=tmp_path / "dist"))
    assert client.get("/api/results").json()["model"] == "fake-llm"


@pytest.fixture(scope="module")
def fake_trials() -> list:
    return run_suite(FakeLLM())


def results_for(tmp_path: Path, payload: object) -> dict:
    path = tmp_path / "eval.json"
    path.write_text(json.dumps(payload))
    client = TestClient(create_app(results_path=path, web_dir=tmp_path / "dist"))
    resp = client.get("/api/results")
    assert resp.status_code == 200
    return resp.json()


def is_rate(rate: dict) -> bool:
    return set(rate) == {"k", "n", "lo", "hi"} and 0 <= rate["lo"] <= rate["hi"] <= 1


def test_results_reports_the_data_driven_subset_per_condition(tmp_path, fake_trials):
    from dataclasses import asdict

    from airgap.eval.corpus import DATA_DRIVEN
    from airgap.eval.harness import summarize

    body = results_for(tmp_path, [asdict(t) for t in fake_trials])
    n_dd = sum(c.category in DATA_DRIVEN for c in attack_cases())
    assert 0 < n_dd < len(attack_cases())
    assert body["data_driven_categories"] == sorted(DATA_DRIVEN)
    for cond, s in summarize(fake_trials).items():
        c = body["conditions"][cond]
        for key, rate in (
            ("data_driven_hijacked", s.data_driven_hijacked),
            ("data_driven_goal", s.data_driven_goal),
            ("unlisted_requests", s.unlisted_requests),
        ):
            assert is_rate(c[key])
            assert (c[key]["k"], c[key]["n"]) == (rate.k, rate.n)
        assert c["data_driven_hijacked"]["n"] == n_dd
        assert c["unlisted_requests"]["n"] == len(attack_cases())


def test_results_by_category_matches_the_harness_table(tmp_path, fake_trials):
    from dataclasses import asdict

    from airgap.eval.corpus import DATA_DRIVEN
    from airgap.eval.harness import by_category

    body = results_for(tmp_path, [asdict(t) for t in fake_trials])
    for cond, c in body["conditions"].items():
        table = by_category(fake_trials, cond)
        assert list(c["by_category"]) == list(table)
        for cat, (hijacked, goal) in table.items():
            row = c["by_category"][cat]
            assert is_rate(row["hijacked"]) and is_rate(row["goal_reached"])
            assert (row["hijacked"]["k"], row["hijacked"]["n"]) == (hijacked.k, hijacked.n)
            assert (row["goal_reached"]["k"], row["goal_reached"]["n"]) == (goal.k, goal.n)
            assert row["data_driven"] is (cat in DATA_DRIVEN)
        assert sum(r["hijacked"]["n"] for r in c["by_category"].values()) == len(attack_cases())


def test_results_reports_held_out_rows_separately(tmp_path, fake_trials):
    from dataclasses import asdict

    body = results_for(tmp_path, [asdict(t) for t in fake_trials])
    held_attacks = [c for c in attack_cases() if c.heldout]
    held_benign = [c for c in benign_cases() if c.heldout]
    assert held_attacks and held_benign
    for cond, c in body["conditions"].items():
        held = c["heldout"]
        assert set(held) == {"hijacked", "goal_reached", "benign_completion"}
        assert all(is_rate(r) for r in held.values())
        assert held["hijacked"]["n"] == len(held_attacks)
        assert held["goal_reached"]["n"] == len(held_attacks)
        assert held["benign_completion"]["n"] == len(held_benign)
        rows = [t for t in fake_trials if t.condition == cond and t.heldout and t.is_attack]
        assert held["hijacked"]["k"] == sum(t.hijacked for t in rows)


def test_results_give_every_leak_its_published_reason(tmp_path, fake_trials):
    from dataclasses import asdict

    body = results_for(tmp_path, [asdict(t) for t in fake_trials])
    probes = {c.id: c.probes for c in attack_cases()}
    assert set(body["leak_details"]) == set(body["leaks"])
    for cond, details in body["leak_details"].items():
        # Same cases, same order as the existing `leaks` list.
        assert [d["case_id"] for d in details] == body["leaks"][cond]
        for d in details:
            assert set(d) == {
                "case_id", "category", "probes", "heldout", "flags", "runs", "trials",
                "published", "reason",
            }  # fmt: skip
            assert d["flags"] and set(d["flags"]) <= {
                "hijacked",
                "goal_reached",
                "unlisted_request",
            }
            assert 1 <= d["runs"] <= d["trials"]
            assert d["probes"] == probes[d["case_id"]]
            assert d["published"] is (d["case_id"] in PUBLISHED_LEAKS)
    airgap = body["leak_details"]["C_airgap"]
    assert airgap
    for d in airgap:
        assert d["reason"] == PUBLISHED_LEAKS[d["case_id"]]
    # The reasons explain C_airgap only; the unguarded agent's leaks are not "known limitations".
    assert all(d["reason"] is None for d in body["leak_details"]["A_no_defense"])


def test_leak_flags_name_what_got_through(tmp_path, fake_trials):
    from dataclasses import asdict, replace

    base = next(t for t in fake_trials if t.condition == "C_airgap" and not t.got_through)
    attack = replace(
        base,
        case_id="x:redirect",
        category="redirect",
        is_attack=True,
        hijacked=False,
        goal_reached=False,
        unlisted_requests=("http://relay.test/",),
    )
    detail = results_for(tmp_path, [asdict(attack)])["leak_details"]["C_airgap"]
    assert detail == [
        {
            "case_id": "x:redirect",
            "category": "redirect",
            "probes": "",
            "heldout": attack.heldout,
            "flags": ["unlisted_request"],
            "runs": 1,
            "trials": 1,
            "published": False,
            "reason": None,
        }
    ]


@pytest.mark.parametrize(
    ("model", "simulated"), [("fake-llm", True), ("muse-spark-1.3", False), (None, None)]
)
def test_results_say_whether_the_model_was_simulated(tmp_path, fake_trials, model, simulated):
    from dataclasses import asdict

    rows = [asdict(t) for t in fake_trials]
    payload = rows if model is None else {"model": model, "trials": rows}
    body = results_for(tmp_path, payload)
    assert body["model"] == model
    assert body["simulated"] is simulated


def test_results_load_files_saved_before_categories_existed(tmp_path, fake_trials):
    from dataclasses import asdict

    old = [
        {
            k: v
            for k, v in asdict(t).items()
            if k not in ("category", "heldout", "unlisted_requests")
        }
        for t in fake_trials
    ]
    c = results_for(tmp_path, old)["conditions"]["C_airgap"]
    assert c["data_driven_hijacked"]["n"] == 0  # unknown, not zero leaks: the UI says so
    assert c["heldout"]["hijacked"]["n"] == 0
    assert list(c["by_category"]) == [""]


# ---- static UI and replay ---------------------------------------------------------------


def test_root_serves_built_ui_when_present(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>airgap console</html>")
    client = TestClient(create_app(results_path=tmp_path / "eval.json", web_dir=dist))
    assert "airgap console" in client.get("/").text


def test_root_explains_how_to_build_when_ui_missing(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "npm" in resp.text


def test_recorded_replay_matches_live_stream(client):
    live = stream(client, scenario="identity", channel="json_ld", provider="fake")
    recorded = record_run("identity", "json_ld")

    def strip(msgs):  # wall-clock latency is the only nondeterministic field
        out = json.loads(json.dumps(msgs))
        for m in out:
            m["data"].pop("latency_s", None)
        return out

    assert strip(recorded) == strip(live)


def test_committed_replays_are_current():
    """web/public/replays/fake.json must be regenerated when the pipeline changes."""
    path = Path(__file__).parents[1] / "web" / "public" / "replays" / "fake.json"
    stored = json.loads(path.read_text())
    assert stored["simulated"] is True
    for key, msgs in stored["runs"].items():
        scenario, channel = key.split(":")
        fresh = record_run(scenario, channel)
        for a, b in zip(msgs, fresh, strict=True):
            a["data"].pop("latency_s", None)
            b["data"].pop("latency_s", None)
            assert a == json.loads(json.dumps(b)), key


# ---- real-call guard against other web pages (issue #6) ------------------------------------

_META_PARAMS = {"scenario": "email", "channel": "none", "provider": "meta"}  # a tuning case


@pytest.fixture
def real_calls_enabled(monkeypatch, tmp_path):
    """Guard open, but the 'real' model is the offline fake: no network client is ever built."""
    import airgap.models.budget as budget
    import airgap.models.meta as meta
    from airgap.models.fake import FakeLLM

    monkeypatch.setenv("AIRGAP_ALLOW_REAL_CALLS", "1")
    monkeypatch.setenv("AIRGAP_BUDGET_USD", "1")
    monkeypatch.setenv("AIRGAP_SPEND_LEDGER", str(tmp_path / "spend.json"))
    monkeypatch.setitem(budget.PRICES, "fake-llm", (0.0, 0.0))  # the fake costs nothing
    monkeypatch.setattr(meta.MetaModel, "from_env", classmethod(lambda cls: FakeLLM()))


def test_real_run_rejects_cross_site_requests(client, real_calls_enabled):
    resp = client.get(
        "/api/run/stream",
        params=_META_PARAMS,
        headers={"host": "127.0.0.1:8000", "sec-fetch-site": "cross-site"},
    )
    assert resp.status_code == 403


def test_real_run_rejects_non_loopback_host_header(client, real_calls_enabled):
    resp = client.get("/api/run/stream", params=_META_PARAMS, headers={"host": "evil.example:8000"})
    assert resp.status_code == 403


def test_real_run_rejects_foreign_origin(client, real_calls_enabled):
    resp = client.get(
        "/api/run/stream",
        params=_META_PARAMS,
        headers={"host": "localhost:8000", "origin": "https://evil.example"},
    )
    assert resp.status_code == 403


def test_real_run_allows_same_origin_loopback(client, real_calls_enabled):
    resp = client.get(
        "/api/run/stream",
        params=_META_PARAMS,
        headers={
            "host": "127.0.0.1:8000",
            "origin": "http://127.0.0.1:8000",
            "sec-fetch-site": "same-origin",
        },
    )
    assert resp.status_code == 200


def test_offline_provider_is_not_gated_by_origin_checks(client):
    resp = client.get(
        "/api/run/stream",
        params={"scenario": "payment", "channel": "none"},
        headers={"sec-fetch-site": "cross-site"},
    )
    assert resp.status_code == 200


def test_real_run_requires_the_browser_to_say_where_the_request_came_from(
    client, real_calls_enabled
):
    """Older browsers omit Sec-Fetch-Site; a missing header must not count as same-origin."""
    resp = client.get("/api/run/stream", params=_META_PARAMS, headers={"host": "127.0.0.1:8000"})
    assert resp.status_code == 403


@pytest.mark.parametrize(
    "path",
    ["..%2f..%2f..%2fetc%2fpasswd", "%2e%2e/%2e%2e/pyproject.toml", "/etc/passwd", "a/../../x"],
)
def test_static_route_never_serves_files_outside_the_built_ui(tmp_path, path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>console</html>")
    (tmp_path / "secret.txt").write_text("TOP-SECRET")
    client = TestClient(create_app(results_path=tmp_path / "e.json", web_dir=dist))
    resp = client.get("/" + path)
    assert resp.status_code == 200 and "TOP-SECRET" not in resp.text
    assert "console" in resp.text  # unknown paths fall back to the UI, nothing else


def test_static_route_checks_containment_before_touching_the_filesystem(tmp_path, monkeypatch):
    """CodeQL py/path-injection: a path outside the UI folder must never reach `is_file()`."""
    from pathlib import Path

    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>console</html>")
    probed: list[Path] = []
    real = Path.is_file

    def spy(self):
        probed.append(self)
        return real(self)

    monkeypatch.setattr(Path, "is_file", spy)
    client = TestClient(create_app(results_path=tmp_path / "e.json", web_dir=dist))
    client.get("/..%2f..%2fetc%2fpasswd")
    outside = [p for p in probed if "etc" in p.parts and "passwd" in p.parts]
    assert outside == []


def test_static_route_serves_real_assets_and_refuses_symlinks_out_of_the_folder(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>console</html>")
    (dist / "assets" / "app.js").write_text("console.log('ui')")
    outside = tmp_path / "secret.txt"
    outside.write_text("TOP-SECRET")
    (dist / "leak.txt").symlink_to(outside)
    client = TestClient(create_app(results_path=tmp_path / "e.json", web_dir=dist))
    assert client.get("/assets/app.js").text == "console.log('ui')"
    assert "TOP-SECRET" not in client.get("/leak.txt").text


# ---- honest accounting in the API and the held-out guard ---------------------------------


def test_results_expose_errors_attacker_paid_and_incomplete(tmp_path, fake_trials):
    from dataclasses import asdict

    trials = [asdict(t) for t in fake_trials]
    body = results_for(tmp_path, {"model": "fake-llm", "trials": trials})
    assert body["incomplete"] is False
    for cond in body["conditions"].values():
        for key in (
            "attacker_paid",
            "benign_flagged",
            "policy_stopped",
            "errors",
            "attack_errors",
            "hijacked_completed",
        ):
            assert is_rate(cond[key]), key
    partial = results_for(tmp_path, {"model": "m", "incomplete": True, "trials": trials})
    assert partial["incomplete"] is True
    assert results_for(tmp_path, trials)["incomplete"] is False  # a bare list is complete


def test_results_count_an_errored_attack_run_as_unknown_not_safe(tmp_path, fake_trials):
    from dataclasses import asdict

    trials = [asdict(t) for t in fake_trials]
    victim = next(t for t in trials if t["condition"] == "C_airgap" and t["case_id"])
    victim["error"] = "PlanRejected: boom"
    cond = results_for(tmp_path, trials)["conditions"]["C_airgap"]
    assert cond["errors"]["k"] >= 1


def _heldout_and_tuning_attack() -> tuple[str, str]:
    from airgap.eval.corpus import cases_for

    held = next(c for c in cases_for("heldout") if c.is_attack and ":" in c.id)
    tune = next(c for c in cases_for("tuning") if c.is_attack)
    return held.id, tune.id


def test_live_demo_refuses_a_heldout_case_on_a_real_model(client, real_calls_enabled):
    held, _ = _heldout_and_tuning_attack()
    scenario, channel = held.split(":")
    resp = client.get(
        "/api/run/stream",
        params={"scenario": scenario, "channel": channel, "provider": "meta"},
        headers={
            "host": "127.0.0.1:8000",
            "origin": "http://127.0.0.1:8000",
            "sec-fetch-site": "same-origin",
        },
    )
    assert resp.status_code == 403
    assert "held-out" in resp.json()["detail"]


def test_live_demo_still_replays_a_heldout_case_offline(client):
    held, _ = _heldout_and_tuning_attack()
    scenario, channel = held.split(":")
    resp = client.get(
        "/api/run/stream", params={"scenario": scenario, "channel": channel, "provider": "fake"}
    )
    assert resp.status_code == 200


# ---- /api/results/runs and ?run= ---------------------------------------------------------


@pytest.fixture
def runs_dir(tmp_path: Path, fake_trials) -> tuple[Path, Path]:
    """A results folder laid out like results/: eval.json plus published/ and published/invalid/."""
    from dataclasses import asdict

    rows = [asdict(t) for t in fake_trials]
    attack = next(r for r in rows if r["is_attack"])
    eval_path = tmp_path / "eval.json"
    eval_path.write_text(json.dumps(rows))
    published = tmp_path / "published"
    (published / "invalid").mkdir(parents=True)
    (published / "final_heldout.json").write_text(
        json.dumps({"model": "muse-spark-1.3", "trials": [attack, {**attack, "trial": 1}]})
    )
    (published / "offline_fakellm.json").write_text(
        json.dumps({"model": "fake-llm", "trials": rows})
    )
    (published / "brand_new_run.json").write_text(
        json.dumps({"model": "m", "incomplete": True, "trials": [attack]})
    )
    (published / "invalid" / "first_run_INVALID.json").write_text(
        json.dumps({"model": "muse-spark-1.3", "trials": [attack]})
    )
    (published / "README.md").write_text("not a run")
    return eval_path, published


def runs_client(paths: tuple[Path, Path]) -> TestClient:
    eval_path, published = paths
    return TestClient(
        create_app(
            results_path=eval_path, web_dir=eval_path.parent / "dist", published_dir=published
        )
    )


def test_runs_lists_local_eval_published_and_invalid_runs(runs_dir):
    body = runs_client(runs_dir).get("/api/results/runs").json()
    runs = {r["id"]: r for r in body["runs"]}
    assert set(runs) == {
        "eval",
        "published:brand_new_run",
        "published:final_heldout",
        "published:offline_fakellm",
        "invalid:first_run_INVALID",
    }
    assert body["default"] == "published:final_heldout"
    # Runs named in RUN_LABELS in that order, then files the table does not know.
    assert [r["id"] for r in body["runs"]] == [
        "eval",
        "published:final_heldout",
        "published:offline_fakellm",
        "invalid:first_run_INVALID",
        "published:brand_new_run",
    ]

    final = runs["published:final_heldout"]
    assert final["label"] == "Final evaluation, held-out split"
    assert final["model"] == "muse-spark-1.3" and final["simulated"] is False
    assert (final["n_cases"], final["trials_per_case"]) == (1, 2)
    assert final["invalid"] is False and final["reason"] is None and final["error"] is None

    offline = runs["published:offline_fakellm"]
    assert offline["simulated"] is True and offline["trials_per_case"] == 1

    local = runs["eval"]
    assert local["model"] is None and local["simulated"] is None
    assert local["n_cases"] == len(attack_cases()) + len(benign_cases())

    # Unknown file names fall back to the file name, never to anything taken from the request.
    new = runs["published:brand_new_run"]
    assert new["label"] == "brand_new_run.json" and new["incomplete"] is True

    bad = runs["invalid:first_run_INVALID"]
    assert bad["invalid"] is True
    assert "JSON-schema decoding" in bad["reason"]


def test_runs_default_falls_back_to_the_local_eval_then_to_any_valid_run(runs_dir):
    eval_path, published = runs_dir
    (published / "final_heldout.json").unlink()
    assert runs_client(runs_dir).get("/api/results/runs").json()["default"] == "eval"
    eval_path.unlink()
    body = runs_client(runs_dir).get("/api/results/runs").json()
    assert body["default"] in {"published:brand_new_run", "published:offline_fakellm"}


def test_runs_is_empty_when_nothing_is_saved(tmp_path):
    client = TestClient(
        create_app(
            results_path=tmp_path / "eval.json",
            web_dir=tmp_path / "dist",
            published_dir=tmp_path / "published",
        )
    )
    assert client.get("/api/results/runs").json() == {"default": None, "runs": []}


def test_runs_flags_an_unreadable_file_instead_of_failing(runs_dir):
    _, published = runs_dir
    (published / "broken.json").write_text("{not json")
    body = runs_client(runs_dir).get("/api/results/runs").json()
    broken = next(r for r in body["runs"] if r["id"] == "published:broken")
    assert broken["error"] and broken["n_cases"] == 0
    assert body["default"] == "published:final_heldout"


def test_results_for_a_run_matches_summarizing_that_file(runs_dir):
    from airgap.server.app import summarize_results

    _, published = runs_dir
    client = runs_client(runs_dir)
    for run_id, path in [
        ("published:final_heldout", published / "final_heldout.json"),
        ("invalid:first_run_INVALID", published / "invalid" / "first_run_INVALID.json"),
    ]:
        resp = client.get("/api/results", params={"run": run_id})
        assert resp.status_code == 200
        assert resp.json() == summarize_results(json.loads(path.read_text()))
    final = client.get("/api/results", params={"run": "published:final_heldout"}).json()
    assert final["model"] == "muse-spark-1.3" and final["simulated"] is False


def test_results_without_run_still_reads_the_local_eval(runs_dir):
    client = runs_client(runs_dir)
    assert client.get("/api/results").json() == client.get("/api/results?run=eval").json()


@pytest.mark.parametrize(
    "run",
    [
        "nope",
        "final_heldout",  # the bare file stem is not an id
        "published:missing",
        "published:README",
        "../../etc/passwd",
        "..%2f..%2fetc%2fpasswd",
        "%2e%2e%2f%2e%2e%2fetc%2fpasswd",
        "/etc/passwd",
        "published:../eval",
        "published:../../../etc/passwd",
        "invalid:../final_heldout",
        "",
    ],
)
def test_results_for_an_unknown_run_is_404(runs_dir, run):
    resp = runs_client(runs_dir).get("/api/results", params={"run": run})
    assert resp.status_code == 404
    assert "root:" not in resp.text


def test_results_run_value_never_reaches_the_filesystem(runs_dir, monkeypatch):
    """CodeQL py/path-injection: the run id is a map key, never part of a path that gets probed."""
    probed: list[Path] = []
    for name in ("is_file", "read_text", "exists"):
        real = getattr(Path, name)

        def spy(self, *a, _real=real, **kw):
            probed.append(self)
            return _real(self, *a, **kw)

        monkeypatch.setattr(Path, name, spy)
    client = runs_client(runs_dir)
    for run in ("../../etc/passwd", "published:../../etc/passwd", "..%2f..%2fetc%2fpasswd"):
        assert client.get("/api/results", params={"run": run}).status_code == 404
    assert [p for p in probed if "etc" in p.parts or ".." in p.parts] == []


def test_runs_skip_symlinked_files(runs_dir, tmp_path):
    _, published = runs_dir
    outside = tmp_path / "elsewhere.json"
    outside.write_text(json.dumps({"model": "x", "trials": []}))
    (published / "linked.json").symlink_to(outside)
    ids = {r["id"] for r in runs_client(runs_dir).get("/api/results/runs").json()["runs"]}
    assert "published:linked" not in ids


# ---- verifier round: one bad file must not break the list; no escape through a linked folder ----


@pytest.mark.parametrize(
    "bad",
    [
        {"model": 5, "trials": []},  # model is not a string
        [{"case_id": "x", "condition": "A", "trial": "0"}],  # wrong field types
        {"model": "m", "trials": [{"hijacked": None}]},
    ],
)
def test_a_malformed_run_is_flagged_not_a_500_for_the_whole_list(runs_dir, bad):
    eval_path, published = runs_dir
    (published / "broken.json").write_text(json.dumps(bad))
    client = TestClient(
        create_app(
            results_path=eval_path, web_dir=eval_path.parent / "dist", published_dir=published
        )
    )
    resp = client.get("/api/results/runs")
    assert resp.status_code == 200
    runs = {r["id"]: r for r in resp.json()["runs"]}
    assert runs["published:broken"]["error"]
    assert runs["published:final_heldout"]["error"] is None
    assert resp.json()["default"] == "published:final_heldout"
    # Asking for the broken run directly is a clean 422, not a 500.
    assert client.get("/api/results", params={"run": "published:broken"}).status_code == 422


def test_a_corrupt_json_file_is_a_clean_422(runs_dir):
    eval_path, published = runs_dir
    (published / "corrupt.json").write_text("{not json")
    client = TestClient(
        create_app(
            results_path=eval_path, web_dir=eval_path.parent / "dist", published_dir=published
        )
    )
    assert client.get("/api/results", params={"run": "published:corrupt"}).status_code == 422


def test_a_symlinked_invalid_folder_is_not_scanned(tmp_path, runs_dir):
    eval_path, published = runs_dir
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.json").write_text(json.dumps([]))
    import shutil

    shutil.rmtree(published / "invalid")
    try:
        (published / "invalid").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are not available here")
    client = TestClient(
        create_app(
            results_path=eval_path, web_dir=eval_path.parent / "dist", published_dir=published
        )
    )
    ids = {r["id"] for r in client.get("/api/results/runs").json()["runs"]}
    assert not any(i.startswith("invalid:") for i in ids)
    assert client.get("/api/results", params={"run": "invalid:secret"}).status_code == 404
