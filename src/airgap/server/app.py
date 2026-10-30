"""FastAPI app for the demo console. Implements docs/api-contract.md.

Agents run in a worker thread; every AuditLog event is pushed to the SSE stream as it happens.
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import AsyncIterator
from dataclasses import fields
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse

from airgap.eval.corpus import DATA_DRIVEN, PUBLISHED_LEAKS, attack_cases, is_heldout
from airgap.eval.harness import Trial, by_category, failures, summarize
from airgap.eval.metrics import Rate
from airgap.models.base import ChatModel, RealCallsDisabled
from airgap.server.runner import Message, run_both
from airgap.world.scenarios import SCENARIOS

REPO_ROOT = Path(__file__).parents[3]
DEFAULT_RESULTS = Path("results/eval.json")
DEFAULT_PUBLISHED = Path("results/published")
DEFAULT_WEB_DIR = REPO_ROOT / "web" / "dist"

_NO_UI = """<!doctype html><title>airgap</title>
<body style="background:#0b0f14;color:#e6edf3;font:16px ui-monospace,monospace;padding:3rem">
<h1>UI not built</h1><p>The API is running. Build the console with:</p>
<pre>cd web &amp;&amp; npm ci &amp;&amp; npx vite build</pre><p>then reload this page.</p></body>"""


def _make_model(provider: str) -> ChatModel:
    """Same AIRGAP_ALLOW_REAL_CALLS guard as the CLI; its refusal becomes a 403."""
    from airgap import cli

    try:
        return cli.make_model(provider)
    except RealCallsDisabled as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from None


_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _require_local_same_origin(request: Request) -> None:
    """Real runs spend credits, so refuse anything that did not come from our own page.

    Blocks other websites (Sec-Fetch-Site / Origin) and DNS rebinding (Host header).
    """
    host_header = request.headers.get("host", "")
    if urlsplit(f"//{host_header}").hostname not in _LOOPBACK_HOSTS:
        raise HTTPException(403, "real model runs are only allowed from the local console")
    # Older browsers omit this header; a missing header must not pass as same-origin.
    if request.headers.get("sec-fetch-site") not in ("same-origin", "none"):
        raise HTTPException(403, "real model runs must come from the console page itself")
    origin = request.headers.get("origin")
    if origin is not None and urlsplit(origin).netloc != host_header:
        raise HTTPException(403, "real model runs must come from the console page itself")


def _ui_files(web_dir: Path) -> dict[str, Path]:
    """Every regular file under the built UI, keyed by its URL path ("assets/app.js")."""
    root = web_dir.resolve()
    return {
        f.relative_to(root).as_posix(): f
        for f in root.rglob("*")
        if f.is_file() and not f.is_symlink()
    }


# Labels for the run picker, keyed by run id, in the order the picker lists them. A file not
# listed here shows its file name and comes after these.
RUN_LABELS = {
    "eval": "Latest local eval (airgap eval)",
    "published:final_heldout": "Final evaluation, held-out split",
    "published:real_C_tuning_v3": "Tuning: airgap conditions (v3)",
    "published:real_C_tuning_v2": "Tuning: airgap before the memo fix (v2)",
    "published:real_BC_tuning": "Tuning: prompt defense and first airgap run",
    "published:real_A_tuning": "Tuning: unguarded baseline",
    "published:offline_fakellm": "Offline FakeLLM reference (plumbing check)",
    "invalid:first_run_INVALID": "First real run (INVALID)",
}
# Why a run under published/invalid/ must not be read as a result. Same text as its README.
INVALID_REASONS = {
    "invalid:first_run_INVALID": (
        "The first real run forced JSON-schema decoding, which corrupted key names and made the "
        "unguarded agent look hijacked when it had only mis-parsed. Replaced by real_A_tuning."
    ),
}
INVALID_FALLBACK = "Kept under results/published/invalid/ so it is not hidden; not a valid result."
# The run the console opens on: the final real evaluation when it exists, else the local eval.
DEFAULT_RUNS = ("published:final_heldout", "eval")


def _json_files(folder: Path) -> list[Path]:
    """Regular *.json files directly in `folder`, symlinks excluded, sorted by name."""
    if not folder.is_dir():
        return []
    return sorted(f for f in folder.resolve().glob("*.json") if f.is_file() and not f.is_symlink())


def _result_runs(results_path: Path, published_dir: Path) -> dict[str, tuple[Path, bool]]:
    """Every run the console can show, keyed by run id, with whether it is marked invalid.

    The `run` query value is only ever a key into this map, built from a directory scan; it is
    never joined into a filesystem path (same rule as `_ui_files`)."""
    runs: dict[str, tuple[Path, bool]] = {}
    if results_path.is_file() and not results_path.is_symlink():
        runs["eval"] = (results_path, False)
    for f in _json_files(published_dir):
        runs[f"published:{f.stem}"] = (f, False)
    invalid_dir = published_dir / "invalid"
    if not invalid_dir.is_symlink():  # a linked folder could point outside the results tree
        for f in _json_files(invalid_dir):
            runs[f"invalid:{f.stem}"] = (f, True)
    known = list(RUN_LABELS)
    rank = {run_id: (known.index(run_id) if run_id in known else len(known)) for run_id in runs}
    return dict(sorted(runs.items(), key=lambda kv: rank[kv[0]]))


def _run_info(run_id: str, path: Path, invalid: bool) -> dict[str, Any]:
    info: dict[str, Any] = {
        "id": run_id,
        "label": RUN_LABELS.get(run_id, path.name),
        "file": path.name,
        "invalid": invalid,
        "reason": INVALID_REASONS.get(run_id, INVALID_FALLBACK) if invalid else None,
        "model": None,
        "simulated": None,
        "n_cases": 0,
        "trials_per_case": 0,
        "incomplete": False,
        "error": None,
    }
    try:
        raw = json.loads(path.read_text())
        model, incomplete, trials = _load_trials(raw)
        summarize_results(
            raw
        )  # the same check `?run=` will make, so a listed run is a readable run
        info.update(
            model=model,
            simulated=_simulated(model),
            n_cases=len({t.case_id for t in trials}),
            trials_per_case=_trials_per_case(trials),
            incomplete=incomplete,
        )
    except Exception as exc:  # noqa: BLE001 - one bad file must never fail the whole list
        # A broken file stays visible in the list, flagged, instead of hiding or failing the rest.
        info["error"] = f"could not read {path.name}: {type(exc).__name__}"
        info.update(model=None, simulated=None, n_cases=0, trials_per_case=0, incomplete=False)
    return info


def _rate(r: Rate) -> dict[str, float | int]:
    lo, hi = r.interval()
    return {"k": r.k, "n": r.n, "lo": lo, "hi": hi}


def _load_trials(raw: Any) -> tuple[str | None, bool, list[Trial]]:
    """Accept a bare trial list (eval.harness.save) or {"model": ..., "trials": [...]}.

    The middle value is True for a partial run saved before it finished."""
    model = None
    incomplete = False
    if isinstance(raw, dict) and raw.get("kind") == "adaptive":
        # Adaptive attempts are not corpus trials: they must never be read as an eval.
        raise ValueError("this is an adaptive-attacker results file, not eval trials")
    if isinstance(raw, dict):
        model = raw.get("model")
        incomplete = raw.get("incomplete") is True
        raw = raw.get("trials", [])
    names = {f.name for f in fields(Trial)}
    return model, incomplete, [Trial(**{k: v for k, v in row.items() if k in names}) for row in raw]


def _subset(rows: list[Trial]) -> dict[str, dict[str, float | int]]:
    """Hijacked, goal reached and benign completion over a slice of trials (the held-out rows)."""
    attacks = [t for t in rows if t.is_attack]
    benign = [t for t in rows if not t.is_attack]
    return {
        "hijacked": _rate(Rate(sum(t.hijacked for t in attacks), len(attacks))),
        "goal_reached": _rate(Rate(sum(t.goal_reached for t in attacks), len(attacks))),
        "benign_completion": _rate(Rate(sum(t.benign_ok for t in benign), len(benign))),
    }


def _leak_details(trials: list[Trial], condition: str) -> list[dict[str, Any]]:
    """One row per case that got through. `reason` is the PUBLISHED_LEAKS entry, which explains
    C_airgap only, so other conditions get `probes` (what the attack tries) and no reason."""
    probes = {c.id: c.probes for c in attack_cases()}
    leaked = failures(trials, condition)
    out = []
    for case_id in sorted({t.case_id for t in leaked}):
        through = [t for t in leaked if t.case_id == case_id]
        flags = [
            name
            for name, hit in (
                ("hijacked", any(t.hijacked for t in through)),
                ("goal_reached", any(t.goal_reached for t in through)),
                ("unlisted_request", any(t.unlisted_requests for t in through)),
            )
            if hit
        ]
        reason = PUBLISHED_LEAKS.get(case_id) if condition == "C_airgap" else None
        out.append(
            {
                "case_id": case_id,
                "category": through[0].category,
                "probes": probes.get(case_id, ""),
                "heldout": through[0].heldout,
                "flags": flags,
                "runs": len(through),
                "trials": sum(t.condition == condition and t.case_id == case_id for t in trials),
                "published": case_id in PUBLISHED_LEAKS,
                "reason": reason,
            }
        )
    return out


def _simulated(model: str | None) -> bool | None:
    # Same test as the CLI report: FakeLLM names start with "fake". Unknown when unrecorded.
    return None if model is None else model.startswith("fake")


def _trials_per_case(trials: list[Trial]) -> int:
    return max((t.trial for t in trials), default=-1) + 1


def summarize_results(raw: Any) -> dict[str, Any]:
    model, incomplete, trials = _load_trials(raw)
    table = summarize(trials)
    return {
        "model": model,
        "incomplete": incomplete,
        "simulated": _simulated(model),
        "n_cases": len({t.case_id for t in trials}),
        "trials_per_case": _trials_per_case(trials),
        "data_driven_categories": sorted(DATA_DRIVEN),
        "conditions": {
            cond: {
                "hijacked": _rate(s.hijacked),
                "goal_reached": _rate(s.goal_reached),
                "benign_completion": _rate(s.benign_completion),
                "utility_under_attack": _rate(s.utility_under_attack),
                "mean_latency_s": float(s.mean_latency_s),
                "mean_tokens": float(s.mean_tokens),
                "confirmations_per_run": float(s.confirmations_per_run),
                "data_driven_hijacked": _rate(s.data_driven_hijacked),
                "data_driven_goal": _rate(s.data_driven_goal),
                "unlisted_requests": _rate(s.unlisted_requests),
                # Honest-accounting rows: an errored run is unknown, never safe.
                "attacker_paid": _rate(s.attacker_paid),
                "benign_flagged": _rate(s.benign_flagged),
                "policy_stopped": _rate(s.policy_stopped),
                "errors": _rate(s.errors),
                "attack_errors": _rate(s.attack_errors),
                "hijacked_completed": _rate(s.hijacked_completed),
                "heldout": _subset([t for t in trials if t.condition == cond and t.heldout]),
                "by_category": {
                    cat: {
                        "hijacked": _rate(hijacked),
                        "goal_reached": _rate(goal),
                        "data_driven": cat in DATA_DRIVEN,
                    }
                    for cat, (hijacked, goal) in by_category(trials, cond).items()
                },
            }
            for cond, s in table.items()
        },
        "leaks": {cond: sorted({t.case_id for t in failures(trials, cond)}) for cond in table},
        "leak_details": {cond: _leak_details(trials, cond) for cond in table},
    }


def create_app(
    results_path: Path = DEFAULT_RESULTS,
    web_dir: Path = DEFAULT_WEB_DIR,
    published_dir: Path = DEFAULT_PUBLISHED,
) -> FastAPI:
    app = FastAPI(
        title="DualPrivilege-Airgap demo", docs_url="/api/docs", openapi_url="/api/openapi.json"
    )

    @app.get("/api/scenarios")
    def scenarios() -> list[dict[str, Any]]:
        return [
            {
                "id": sc.id,
                "title": sc.title,
                "user_text": sc.user_text,
                "channels": list(sc.channels),
            }
            for sc in SCENARIOS.values()
        ]

    @app.get("/api/run/stream")
    async def run_stream(
        request: Request,
        scenario: str,
        channel: str = "none",
        provider: Literal["fake", "meta"] = "fake",
    ) -> StreamingResponse:
        sc = SCENARIOS.get(scenario)
        if sc is None:
            raise HTTPException(404, f"unknown scenario {scenario}")
        if channel != "none" and channel not in sc.channels:
            raise HTTPException(400, f"channel {channel} is not defined for scenario {scenario}")
        model = _make_model(provider)  # refuses first unless real calls are enabled
        case_id = f"{scenario}:{channel if channel != 'none' else 'benign'}"
        if provider != "fake" and is_heldout(case_id):
            # Same rule as `airgap demo`: held-out cases stay unseen until the final evaluation.
            raise HTTPException(
                403,
                f"{case_id} is a held-out case and must stay unseen until the final evaluation; "
                "pick another scenario or channel",
            )
        if provider != "fake":
            _require_local_same_origin(request)
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Message | None] = asyncio.Queue()

        def send(msg: Message) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, msg)

        def work() -> None:
            try:
                run_both(sc, None if channel == "none" else channel, model, provider, send)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        async def events() -> AsyncIterator[str]:
            threading.Thread(target=work, name=f"run-{scenario}", daemon=True).start()
            while (msg := await queue.get()) is not None:
                yield f"data: {json.dumps(msg)}\n\n"

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/results/runs")
    def result_runs() -> dict[str, Any]:
        found = _result_runs(results_path, published_dir)
        runs = [_run_info(run_id, path, invalid) for run_id, (path, invalid) in found.items()]
        readable = {r["id"] for r in runs if r["error"] is None}
        default = next((r for r in DEFAULT_RUNS if r in readable), None)
        if default is None:
            default = next(
                (r["id"] for r in runs if r["id"] in readable and not r["invalid"]), None
            )
        return {"default": default, "runs": runs}

    @app.get("/api/results")
    def results(run: str | None = None) -> dict[str, Any]:
        if run is None:
            if not results_path.is_file():
                raise HTTPException(
                    404, f"no saved eval at {results_path}; run `airgap eval` first"
                )
            target, label = results_path, str(results_path)
        else:
            found = _result_runs(results_path, published_dir).get(run)
            if found is None:
                raise HTTPException(404, "unknown run; see GET /api/results/runs")
            target, label = found[0], f"run {run}"
        try:
            return summarize_results(json.loads(target.read_text()))
        except Exception as exc:  # noqa: BLE001 - an adaptive file or a corrupt one is a 422
            raise HTTPException(
                422, f"{label} is not a readable eval results file: {type(exc).__name__}"
            ) from None

    @app.get("/{path:path}", include_in_schema=False)
    def ui(path: str) -> Any:
        if path.startswith("api/"):
            raise HTTPException(404)
        index = web_dir / "index.html"
        if not index.is_file():
            return HTMLResponse(_NO_UI)
        # The request path is only ever a lookup key into the files that really exist under the
        # UI folder; it is never joined into a filesystem path. Anything else falls back to the UI.
        served = _ui_files(web_dir)
        if path and path in served:
            return FileResponse(served[path])
        return FileResponse(index)

    return app
