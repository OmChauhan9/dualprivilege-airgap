"""The airgapped agent: planner -> validated plan -> interpreter -> policy -> your functions.

`Airgap` is the one entry point. A developer hands it plain functions and the user's allowlists
and limits; `run(request)` returns what happened. Planning, fetching, sanitizing, the tool-less
reader, taint, P0-P7, confirmation, network checks and the outcome of each call stay behind it.
"""

from __future__ import annotations

import contextlib
import inspect
import math
import re
import time
import uuid
from collections.abc import Callable, Iterable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass

import httpx

from airgap.core.actions import Effects, Kind, PayFn, SendEmailFn
from airgap.core.audit import AuditLog, AuditSinkFailed, Event
from airgap.core.fetcher import Fetcher, is_hostname, registrable_domain
from airgap.core.interpreter import Interpreter
from airgap.core.orchestrator import Orchestrator, PlanRejected
from airgap.core.policy import (
    ADDRESS_RE,
    Confirm,
    PermissivePolicy,
    PolicyEngine,
    UserContext,
    deny_all,
)
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
        confirm: Confirm | None = None,
        reader: ChatModel | None = None,
        transport: httpx.BaseTransport | None = None,
        private_hosts: Iterable[str] = (),
    ) -> None:
        """
        planner           sees only the user's request, the payee aliases, the email allowlist
                          and the per-payment limit; writes the plan. Required: any object with
                          ChatModel's `complete` method.
        pay, send_email   your functions, each taking one Payment or Email. Leave one out and the
                          guard never does that action (P0). Return normally: it happened.
                          Raise `Declined`: it certainly did not. Raise anything else: the
                          outcome is unknown, and the run stops there.
        payees            alias -> address the user saved (P1). An address the user types in the
                          request also counts.
        email_recipients  the only addresses mail may go to (P3), compared ignoring ASCII case
                          only. `send_email` receives the address as you wrote it here.
        domains           registrable domains fetchable beyond those named in the request (P5),
                          compared ignoring case and one trailing dot. Each allows every host
                          under it. An entry below its registrable domain ("api.example.com")
                          is refused, and the error names the one to pass ("example.com").
        per_payment_usd   P2 limit for one payment. per_run_usd: P2 limit across one run.
        confirm           asked when the verdict is "confirm" (P4 page amount, P6 page choice).
                          Approves only by returning exactly True. Default (None): refuse.
        reader            the tool-less model that reads sanitized pages. Default: `planner`.
        transport         how pages are fetched. Default: the guard's own transport, built per
                          run, which connects only to public addresses, uses no proxy and no
                          environment settings, and is closed after the run. A transport you pass
                          is trusted code: it must send exactly one request per call and never
                          follow redirects itself, and the guard does not check the addresses it
                          connects to. The guard never closes it. Each request it gets carries
                          timeouts of what is left of the fetch's 20 s limit; that bounds each
                          socket operation, not their sum, so your transport must keep the
                          limit itself. Tests pass
                          `httpx.MockTransport`. There is no client parameter: the guard builds
                          every request, so no hook, header, cookie or credential of yours
                          reaches a page.
        private_hosts     exact host names the guard's own transport may reach at a non-public
                          address, such as an intranet page ("wiki.corp.internal"). A subdomain
                          of a listed host is not listed.

        A wrong type raises TypeError and a wrong value ValueError, each naming the argument.
        """
        confirm = deny_all if confirm is None else confirm
        _check_model("planner", planner)
        if reader is not None:
            _check_model("reader", reader)
        _check_shape("pay", pay, "pay(p: Payment)")
        _check_shape("send_email", send_email, "send_email(e: Email)")
        _check_shape("confirm", confirm, "confirm(c: Confirmation)")
        if payees is not None and not isinstance(payees, Mapping):
            raise TypeError(
                f"payees must be a mapping of alias -> address, not {type(payees).__name__}"
            )
        _check_not_one_string(
            email_recipients=email_recipients, domains=domains, private_hosts=private_hosts
        )
        problems = [
            f"{name} is an async function; pass a plain function"
            for name, fn in (("pay", pay), ("send_email", send_email), ("confirm", confirm))
            if fn is not None and _is_async(fn)
        ]
        if transport is not None and private_hosts:
            problems.append(
                "private_hosts applies only to the guard's own transport; with transport= "
                "your transport decides where it connects"
            )
        payees = dict(payees or {})
        recipients = frozenset(email_recipients)
        domains = frozenset(
            h.lower().removesuffix(".") if isinstance(h, str) else h for h in domains
        )
        private_hosts = frozenset(private_hosts)
        problems += _vet_grant(payees, recipients, domains, private_hosts)
        problems += _vet_limits(per_payment_usd, per_run_usd)
        if problems:
            raise ValueError("Airgap cannot use this configuration:\n- " + "\n- ".join(problems))
        self._planner = planner
        self._reader = reader or planner
        self._pay = pay
        self._send_email = send_email
        self._payees = payees
        self._recipients = recipients
        self._domains = domains
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

        Returns a RunResult for anything a page, the reader model, the policy or your functions
        do, and when the planner's plan is rejected (`error` "PlanRejected: ..."). Raises
        RuntimeError when called from inside a run, and TypeError for a `run_id` that is not a
        non-empty str or a planner whose `complete` returns something other than a
        `Completion`. Raises whatever the planner model's `complete`
        raises (a ConnectionError, say), BudgetExceeded for the model spend cap, and any other
        BaseException (KeyboardInterrupt); each carries the partial result as
        `exc.airgap_result`.
        """
        if run_id is not None and not (isinstance(run_id, str) and run_id):
            raise TypeError(f"run_id must be a non-empty str or None, not {run_id!r}")
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


def _check_model(name: str, model: object) -> None:
    if not callable(getattr(model, "complete", None)):
        raise TypeError(
            f"{name} must be a model with a complete(messages, *, role, schema=None) method, "
            f"got {type(model).__name__}"
        )


def _check_not_one_string(**collections: object) -> None:
    """One string where a collection belongs would be read one character at a time."""
    for name, value in collections.items():
        if isinstance(value, str | bytes):
            raise TypeError(f"{name} is one string, not a collection: write {name}=[{value!r}]")


def _is_async(fn: Callable[..., object]) -> bool:
    """An async function, or an object whose __call__ is one."""
    return inspect.iscoroutinefunction(fn) or inspect.iscoroutinefunction(type(fn).__call__)


def _check_shape(name: str, fn: Callable[..., object] | None, shape: str) -> None:
    """A function the guard calls must be callable with one positional argument."""
    if fn is None:
        return
    if not callable(fn):
        raise TypeError(f"{name} must be a function: {shape}, got {type(fn).__name__}")
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):  # a builtin with no signature: trust it
        return
    try:
        sig.bind(object())
    except TypeError:
        raise TypeError(f"{name} must be callable with one argument: {shape}, got {sig}") from None


_RECIPIENT_RE = re.compile(r"[^@\s]+@[^@\s]+")


def _vet_grant(
    payees: dict[str, str],
    recipients: frozenset[str],
    domains: frozenset[str],
    private_hosts: frozenset[str],
) -> list[str]:
    """Each problem names its argument. Addresses use the form P1 recognises in a request."""
    problems = [
        f"payees: {alias!r} -> {addr!r} needs a non-empty alias and a 0x address"
        for alias, addr in payees.items()
        if not (isinstance(alias, str) and alias.strip())
        or not (isinstance(addr, str) and ADDRESS_RE.fullmatch(addr))
    ]
    problems += [
        f"email_recipients: {r!r} is not an email address"
        for r in sorted(recipients, key=repr)
        if not (isinstance(r, str) and _RECIPIENT_RE.fullmatch(r))
    ]
    for name, hosts in (("domains", domains), ("private_hosts", private_hosts)):
        problems += [
            f"{name}: {h!r} is not a host name"
            for h in sorted(hosts, key=repr)
            if not (isinstance(h, str) and h and is_hostname(h))
        ]
    problems += [
        f"domains: {h!r} is not a registrable domain, and P5 compares registrable domains (the "
        f"last two labels). Passing {registrable_domain(h)!r} would allow every host under "
        f"{registrable_domain(h)!r}, which on shared hosting means every tenant's site"
        for h in sorted(domains, key=repr)
        if isinstance(h, str) and h and is_hostname(h) and registrable_domain(h) != h
    ]
    return problems


def _vet_limits(per_payment_usd: object, per_run_usd: object) -> list[str]:
    problems = [
        f"{name} must be a finite number greater than 0, got {v!r}"
        for name, v in (("per_payment_usd", per_payment_usd), ("per_run_usd", per_run_usd))
        if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v) or v <= 0
    ]
    if not problems and float(per_payment_usd) > float(per_run_usd):  # type: ignore[arg-type]
        problems.append(
            f"per_payment_usd ({per_payment_usd}) must not exceed per_run_usd ({per_run_usd})"
        )
    return problems


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
    plan_steps: int | None = None

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
            plan_steps=plan_steps,
        )

    try:
        plan = orchestrator.plan(request, ctx)
        plan_steps = len(plan.steps)
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
