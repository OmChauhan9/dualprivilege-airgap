"""The airgapped agent: planner -> validated plan -> interpreter -> policy -> your functions.

`Airgap` is the one entry point. A developer hands it plain functions and the user's allowlists
and limits; `run(request)` returns what happened. Planning, fetching, sanitizing, the tool-less
reader, taint, P0-P7, confirmation, network checks and the outcome of each call stay behind it.
"""

from __future__ import annotations

import contextlib
import inspect
import math
import time
import uuid
from collections.abc import Callable, Iterable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass

import httpx

from airgap.core.actions import Effects, Kind, PayFn, SendEmailFn
from airgap.core.audit import AuditLog, AuditSinkFailed, Event
from airgap.core.fetcher import Fetcher
from airgap.core.interpreter import Interpreter
from airgap.core.orchestrator import Orchestrator, PlanRejected
from airgap.core.policy import Confirm, PermissivePolicy, PolicyEngine, UserContext, deny_all
from airgap.core.reader import Reader
from airgap.core.result import RunResult
from airgap.core.transport import PublicTransport
from airgap.models.base import ChatModel

# Set while a run is in progress in this context. A pay, send_email, confirm or on_event that
# calls `run` again is refused, so nesting cannot multiply the per-run limits.
_IN_RUN: ContextVar[bool] = ContextVar("airgap_in_run", default=False)


@dataclass(frozen=True)
class _Ablation:
    """Evaluation only. The production path always switches nothing off."""

    sanitize_input: bool = True
    enforce_policy: bool = True


class Airgap:
    """A payment and email guard around a planner model and a reader model.

    Immutable after construction. One instance serves many runs and threads: each run builds its
    own policy (spend), fetcher, action record and audit log. Limits are per run.
    """

    def __init__(
        self,
        *,
        planner: ChatModel,
        pay: PayFn | None = None,
        send_email: SendEmailFn | None = None,
        payees: Mapping[str, str] | None = None,
        email_recipients: Iterable[str] = (),
        domains: Iterable[str] = (),
        per_payment_usd: float = 100.0,
        per_run_usd: float = 200.0,
        confirm: Confirm = deny_all,
        reader: ChatModel | None = None,
        transport: httpx.BaseTransport | None = None,
        private_hosts: Iterable[str] = (),
    ) -> None:
        """
        planner           sees only the user's request and the allowlist names; writes the plan.
        pay, send_email   your functions, each taking one Payment or Email. Leave one out and the
                          guard never does that action (P0). Return normally: it happened.
                          Raise `Declined`: it certainly did not. Raise anything else: the
                          outcome is unknown, and the run stops there.
        payees            alias -> address the user saved (P1). An address the user types in the
                          request also counts.
        email_recipients  the only addresses mail may go to (P3), compared case-insensitively.
        domains           registrable domains fetchable beyond those named in the request (P5).
        per_payment_usd   P2 limit for one payment. per_run_usd: P2 limit across one run.
        confirm           asked when the verdict is "confirm" (P4 page amount, P6 page choice).
                          Approves only by returning exactly True. Default: refuse.
        reader            the tool-less model that reads sanitized pages. Default: `planner`.
        transport         how pages are fetched. Default: the guard's own transport, built per
                          run, which connects only to public addresses, uses no proxy and no
                          environment settings, and is closed after the run. A transport you pass
                          is trusted code: it must send exactly one request per call and never
                          follow redirects itself, and the guard does not check the addresses it
                          connects to. The guard never closes it. Tests pass
                          `httpx.MockTransport`. There is no client parameter: the guard builds
                          every request, so no hook, header, cookie or credential of yours
                          reaches a page.
        private_hosts     exact host names the guard's own transport may reach at a non-public
                          address, such as an intranet page ("wiki.corp.internal"). A subdomain
                          of a listed host is not listed.
        """
        _check_shape("pay", pay, "pay(p: Payment)")
        _check_shape("send_email", send_email, "send_email(e: Email)")
        _check_shape("confirm", confirm, "confirm(c: Confirmation)")
        problems = [
            f"{name} is an async function; pass a plain function"
            for name, fn in (("pay", pay), ("send_email", send_email), ("confirm", confirm))
            if fn is not None and inspect.iscoroutinefunction(fn)
        ]
        if transport is not None and private_hosts:
            problems.append(
                "private_hosts applies only to the guard's own transport; with transport= "
                "your transport decides where it connects"
            )
        problems += [
            f"{name} must be a positive finite number"
            for name, v in (("per_payment_usd", per_payment_usd), ("per_run_usd", per_run_usd))
            if not (isinstance(v, int | float) and math.isfinite(v) and v > 0)
        ]
        recipients = frozenset(email_recipients)
        problems += [f"email recipient {r!r} has no @" for r in sorted(recipients) if "@" not in r]
        if problems:
            raise ValueError("Airgap cannot use this configuration:\n- " + "\n- ".join(problems))
        self._planner = planner
        self._reader = reader or planner
        self._pay = pay
        self._send_email = send_email
        self._payees = dict(payees or {})
        self._recipients = recipients
        self._domains = frozenset(domains)
        self._per_payment = float(per_payment_usd)
        self._per_run = float(per_run_usd)
        self._confirm = confirm
        self._transport = transport
        self._private_hosts = frozenset(h.lower().rstrip(".") for h in private_hosts)

    def run(
        self,
        request: str,
        *,
        run_id: str | None = None,
        on_event: Callable[[Event], None] | None = None,
    ) -> RunResult:
        """Carry out one user request. `request` is the user's own words: the only text the planner
        sees, and the only source of typed addresses (P1), the stated cap (P2) and named URLs (P5).

        `run_id` seeds each call's idempotency key (`Payment.key`). Pass a stable id, such as the
        message id, and a repeated run sends the same key for the same payment or email. Default:
        a new random id.

        Limits are per run. An approved amount stays counted against `per_run_usd` even when your
        function declines it or its outcome is unknown. A callback (pay, send_email, confirm,
        on_event) that calls `run` again on the same thread or asyncio task is refused; one that
        starts another thread and calls the guard there is not detected, and gets fresh limits,
        so a callback must never call the guard from another thread.

        `on_event` receives every audit event as it happens. The approved `policy` event comes
        just before your function runs. If `on_event` raises, the run stops there and the result
        is still returned, with error "AuditSinkFailed: on_event raised OSError". `Event.untrusted`
        names the fields that may hold page text: never give those to a model with tools and never
        render them as HTML.

        Returns for anything a page, a model, the policy or your functions do. Raises RuntimeError
        when called from inside a run, BudgetExceeded for the model spend cap, and any
        BaseException, which then carries the partial result as `exc.airgap_result`.
        """
        return _execute(self, request, run_id, on_event, _Ablation())

    def _context(self, request: str) -> UserContext:
        """The policy's view of one run. `user_text` is set from `request` here and nowhere else."""
        tools: set[Kind] = set()
        if self._pay is not None:
            tools.add("pay")
        if self._send_email is not None:
            tools.add("send_email")
        return UserContext(
            user_text=request,
            payee_allowlist=dict(self._payees),
            email_allowlist=self._recipients,
            extra_domains=self._domains,
            per_tx_limit_usd=self._per_payment,
            session_limit_usd=self._per_run,
            tools=frozenset(tools),
        )


def _check_shape(name: str, fn: Callable[..., object] | None, shape: str) -> None:
    """A function the guard calls must take exactly one positional argument."""
    if fn is None:
        return
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):  # a builtin with no signature: trust it
        return
    try:
        sig.bind(None)
        takes_one = True
    except TypeError:
        takes_one = False
    try:
        sig.bind(None, None)
        takes_two = True
    except TypeError:
        takes_two = False
    if not takes_one or takes_two:
        raise TypeError(f"{name} must take exactly one positional argument: {shape}, got {sig}")


def run_ablated(
    guard: Airgap, request: str, *, sanitize_input: bool = True, enforce_policy: bool = True
) -> RunResult:
    """Evaluation only: run with the sanitizer or the policy switched off (C_no_sanitizer,
    C_no_policy). tests/test_public_surface.py confines it to `airgap.eval`."""
    return _execute(guard, request, None, None, _Ablation(sanitize_input, enforce_policy))


def _execute(
    guard: Airgap,
    request: str,
    run_id: str | None,
    on_event: Callable[[Event], None] | None,
    ablation: _Ablation,
) -> RunResult:
    if _IN_RUN.get():
        raise RuntimeError("Airgap.run was called from inside a run")
    token = _IN_RUN.set(True)
    audit = AuditLog(on_event)
    ctx = guard._context(request)
    effects = Effects(guard._pay, guard._send_email, audit, run_id or uuid.uuid4().hex)
    orchestrator, reader = Orchestrator(guard._planner), Reader(guard._reader)
    policy = (PolicyEngine if ablation.enforce_policy else PermissivePolicy)(
        ctx, audit, guard._confirm
    )
    owned = guard._transport is None
    transport = guard._transport or PublicTransport(guard._private_hosts)
    start = time.perf_counter()
    blocked: list[str] = []
    final, error = "", None

    def result() -> RunResult:
        return RunResult(
            final_text=final,
            audit=audit,
            actions=tuple(effects.actions),
            blocked=blocked,
            confirmations=policy.confirmations,
            prompt_tokens=orchestrator.prompt_tokens + reader.prompt_tokens,
            completion_tokens=orchestrator.completion_tokens + reader.completion_tokens,
            latency_s=time.perf_counter() - start,
            error=error,
        )

    try:
        plan = orchestrator.plan(request, ctx)
        audit.emit("plan", steps=[s.model_dump() for s in plan.steps])
        interp = Interpreter(
            ctx,
            Fetcher(transport),
            reader,
            effects,
            policy,
            audit,
            sanitize_input=ablation.sanitize_input,
        )
        outcome = interp.run(plan)
        final, blocked, error = outcome.answer or "", outcome.blocked, outcome.error
    except PlanRejected as exc:
        error = f"PlanRejected: {exc}"
        with contextlib.suppress(AuditSinkFailed):
            audit.emit("error", error=error)
    except AuditSinkFailed as exc:
        error = f"AuditSinkFailed: {exc}"
    except BaseException as exc:
        exc.airgap_result = result()  # type: ignore[attr-defined]
        raise
    finally:
        if owned:
            transport.close()
        _IN_RUN.reset(token)
    return result()
