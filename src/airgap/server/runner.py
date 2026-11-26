"""Runs one scenario through both agents and emits the stream envelopes in docs/api-contract.md.

Shared by the live SSE endpoint and the replay recorder, so a replay is byte-for-byte what the
live stream sends (apart from wall-clock latency).
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from typing import Any, Literal

from airgap.baseline.vulnerable_agent import VulnerableAgent
from airgap.core.actions import Email, Ledger, Payment
from airgap.core.audit import AuditLog, Event
from airgap.core.result import RunResult
from airgap.models.base import ChatModel, Completion, Role
from airgap.models.fake import FakeLLM
from airgap.world.scenarios import Scenario
from airgap.world.tools import WorldTools

Pane = Literal["unguarded", "airgap"]
Message = dict[str, Any]


class RunStopped(RuntimeError):
    """The run was told to stop, so no further model call is made."""


class _Stoppable:
    """Refuses every model call once `stop` is set; a call already in flight may finish."""

    def __init__(self, model: ChatModel, stop: threading.Event) -> None:
        self.name = model.name
        self._model = model
        self._stop = stop

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        role: Role,
        schema: dict[str, Any] | None = None,
    ) -> Completion:
        if self._stop.is_set():
            raise RunStopped("the client disconnected, so no further model call was made")
        return self._model.complete(messages, role=role, schema=schema)


def jsonable(data: Any) -> Any:
    """Audit payloads are plain data already; this only guards against stray non-JSON types."""
    return json.loads(json.dumps(data, default=str))


def _unauthorized(sc: Scenario, payment: Payment | None = None, email: Email | None = None) -> bool:
    """Judge one side effect on its own, so the UI can flag exactly the unauthorized ones."""
    alone = Ledger(payments=[payment] if payment else [], emails=[email] if email else [])
    return sc.judge(alone, "").hijacked


def done_data(res: RunResult, sc: Scenario) -> dict[str, Any]:
    j = sc.judge(res.ledger, res.final_text)
    return {
        "reply": res.final_text,
        "judgement": {
            "hijacked": j.hijacked,
            "goal_reached": j.goal_reached,
            "benign_ok": j.benign_ok,
        },
        "payments": [
            {
                "payee": p.payee,
                "amount_usd": p.amount_usd,
                "memo": p.memo,
                "unauthorized": _unauthorized(sc, payment=p),
            }
            for p in res.ledger.payments
        ],
        "emails": [
            {"to": e.to, "subject": e.subject, "unauthorized": _unauthorized(sc, email=e)}
            for e in res.ledger.emails
        ],
        "blocked": list(res.blocked),
        "error": res.error,
        "confirmations": res.confirmations,
        "prompt_tokens": res.prompt_tokens,
        "completion_tokens": res.completion_tokens,
        "latency_s": res.latency_s,
    }


def run_both(
    sc: Scenario,
    channel: str | None,
    model: ChatModel,
    provider: str,
    send: Callable[[Message], None],
    stop: threading.Event | None = None,
) -> None:
    """Unguarded agent, then the airgapped agent, on the same page. `send` gets each envelope.

    Once `stop` is set, every later model call raises RunStopped, which ends the run.
    """
    seq = 0

    def emit(pane: Pane | None, kind: str, data: dict[str, Any]) -> None:
        nonlocal seq
        send({"seq": seq, "pane": pane, "kind": kind, "data": jsonable(data)})
        seq += 1

    def sink_for(pane: Pane) -> Callable[[Event], None]:
        return lambda ev: emit(pane, ev.kind, ev.data)

    emit(
        None,
        "start",
        {
            "scenario": sc.id,
            "title": sc.title,
            "user_text": sc.user_text,
            "channel": channel,
            "provider": provider,
            "simulated": isinstance(model, FakeLLM),
            "models": {
                "unguarded_agent": model.name,
                "orchestrator": model.name,
                "reader": model.name,
            },
        },
    )
    if stop is not None:  # wrapped only now: the start event reads the real model's type
        model = _Stoppable(model, stop)
    try:
        base = VulnerableAgent(model, sc.make_web(channel)).run(
            sc.user_text, sc.context(), AuditLog(sink_for("unguarded"))
        )
        emit("unguarded", "done", done_data(base, sc))
        air = sc.guard(model, sc.make_web(channel), WorldTools()).run(
            sc.user_text, on_event=sink_for("airgap")
        )
        emit("airgap", "done", done_data(air, sc))
    except Exception as exc:  # surface crashes to the UI instead of a silently dead stream
        emit(None, "error", {"error": f"{type(exc).__name__}: {exc}"})
    finally:
        emit(None, "end", {})
