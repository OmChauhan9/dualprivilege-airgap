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

from airgap.eval.harness import Trial, failures, summarize
from airgap.eval.metrics import Rate
from airgap.models.base import ChatModel, RealCallsDisabled
from airgap.server.runner import Message, run_both
from airgap.world.scenarios import SCENARIOS

REPO_ROOT = Path(__file__).parents[3]
DEFAULT_RESULTS = Path("results/eval.json")
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


def _rate(r: Rate) -> dict[str, float | int]:
    lo, hi = r.interval()
    return {"k": r.k, "n": r.n, "lo": lo, "hi": hi}


def _load_trials(raw: Any) -> tuple[str | None, list[Trial]]:
    """Accept a bare trial list (eval.harness.save) or {"model": ..., "trials": [...]}."""
    model = None
    if isinstance(raw, dict):
        model = raw.get("model")
        raw = raw.get("trials", [])
    names = {f.name for f in fields(Trial)}
    return model, [Trial(**{k: v for k, v in row.items() if k in names}) for row in raw]


def summarize_results(raw: Any) -> dict[str, Any]:
    model, trials = _load_trials(raw)
    table = summarize(trials)
    return {
        "model": model,
        "n_cases": len({t.case_id for t in trials}),
        "trials_per_case": max((t.trial for t in trials), default=-1) + 1,
        "conditions": {
            cond: {
                "hijacked": _rate(s.hijacked),
                "goal_reached": _rate(s.goal_reached),
                "benign_completion": _rate(s.benign_completion),
                "utility_under_attack": _rate(s.utility_under_attack),
                "mean_latency_s": float(s.mean_latency_s),
                "mean_tokens": float(s.mean_tokens),
                "confirmations_per_run": float(s.confirmations_per_run),
            }
            for cond, s in table.items()
        },
        "leaks": {cond: sorted({t.case_id for t in failures(trials, cond)}) for cond in table},
    }


def create_app(results_path: Path = DEFAULT_RESULTS, web_dir: Path = DEFAULT_WEB_DIR) -> FastAPI:
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

    @app.get("/api/results")
    def results() -> dict[str, Any]:
        if not results_path.is_file():
            raise HTTPException(404, f"no saved eval at {results_path}; run `airgap eval` first")
        return summarize_results(json.loads(results_path.read_text()))

    @app.get("/{path:path}", include_in_schema=False)
    def ui(path: str) -> Any:
        if path.startswith("api/"):
            raise HTTPException(404)
        index = web_dir / "index.html"
        if not index.is_file():
            return HTMLResponse(_NO_UI)
        root = web_dir.resolve()
        target = (root / path).resolve()
        # Containment first: a path outside the UI folder is never even probed on disk.
        if path and target.is_relative_to(root) and target.is_file():
            return FileResponse(target)
        return FileResponse(index)

    return app
