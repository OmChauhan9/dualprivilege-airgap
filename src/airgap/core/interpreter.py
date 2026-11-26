"""Interpreter: executes a validated plan. The developer's functions run only after the policy
engine approves."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from pydantic import BaseModel

from airgap.core.actions import Action, Effects, Email, OutcomeUnknown, Payment, kind_of
from airgap.core.audit import AuditLog, AuditSinkFailed
from airgap.core.fetcher import AddressRefused, FetchedDoc, Fetcher, FetchError
from airgap.core.plan import (
    Alias,
    Answer,
    Arg,
    ChooseMin,
    Fetch,
    Lit,
    Pay,
    Plan,
    Read,
    SendEmail,
)
from airgap.core.policy import ConfirmFailed, Decision, Policy, UserContext
from airgap.core.reader import Reader
from airgap.core.sanitizer import SanitizeResult, sanitize
from airgap.core.values import Tainted, merge_sources, trusted
from airgap.models.budget import BudgetExceeded

FROM_A_PAGE = "<from a page>"


def _shown(value: Tainted) -> Any:
    """How a payee, recipient or URL appears in `blocked` and in audit args: page text never."""
    return FROM_A_PAGE if value.is_tainted and isinstance(value.value, str) else value.value


def _cents(amount: Tainted) -> Tainted:
    """The amount rounded half-up to whole cents, so the amount approved is the amount charged.
    A value already in whole cents, or not a finite number, is returned unchanged for P2."""
    v = amount.value
    if isinstance(v, bool) or not isinstance(v, int | float | str):
        return amount
    try:
        exact = float(v)
        cents = float(Decimal(str(v).strip()).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    except (ValueError, ArithmeticError):
        return amount
    if not math.isfinite(exact) or exact == cents:
        return amount
    return Tainted(cents, amount.sources, amount.steered_by)


@dataclass
class Choice:
    alias: str
    price: Tainted
    steered_by: frozenset[str]


@dataclass
class ExecResult:
    answer: str | None = None
    blocked: list[str] = field(default_factory=list)
    # Code-written summaries ("pay denied by P2"): safe to show, they never repeat page text.
    denied: list[str] = field(default_factory=list)
    error: str | None = None


class Interpreter:
    def __init__(
        self,
        ctx: UserContext,
        fetcher: Fetcher,
        reader: Reader,
        effects: Effects,
        policy: Policy,
        audit: AuditLog,
        sanitize_input: bool = True,
    ) -> None:
        self.sanitize_input = sanitize_input
        self.ctx = ctx
        self.fetcher = fetcher
        self.reader = reader
        self.effects = effects
        self.policy = policy
        self.audit = audit
        self.env: dict[str, Any] = {}

    # ---- argument resolution ------------------------------------------------------------

    def resolve(self, arg: Arg) -> Tainted:
        if isinstance(arg, Lit):
            return trusted(arg.lit)
        if isinstance(arg, Alias):
            return trusted(self.ctx.payee_allowlist[arg.alias])
        val = self.env[arg.var]
        if isinstance(val, Choice):
            if arg.field == "alias":
                return Tainted(val.alias, steered_by=val.steered_by)
            if arg.field == "price":
                return Tainted(val.price.value, val.price.sources, val.steered_by)
            raise KeyError(f"choice has no field {arg.field}")
        if isinstance(val, Tainted) and arg.field and isinstance(val.value, BaseModel):
            return Tainted(getattr(val.value, arg.field), val.sources)
        if isinstance(val, Tainted):
            return val
        raise KeyError(f"{arg.var} is not a value")

    def _payee_address(self, payee: Tainted) -> Tainted:
        """A choice label (alias) becomes an allowlisted address, keeping its steering flag."""
        label = str(payee.value)
        if label in self.ctx.payee_allowlist:
            return Tainted(self.ctx.payee_allowlist[label], payee.sources, payee.steered_by)
        return payee

    def _recipient(self, to: Tainted) -> Tainted:
        """An allowlisted recipient in the allowlist's own spelling, keeping its labels, so
        `send_email` receives the address P3 matched and never another spelling of it."""
        listed = self.ctx.listed_recipient(str(to.value))
        return to if listed is None else Tainted(listed, to.sources, to.steered_by)

    # ---- execution ----------------------------------------------------------------------

    def run(self, plan: Plan) -> ExecResult:
        result = ExecResult()
        answer_untrusted = False
        for step in plan.steps:
            try:
                if isinstance(step, Fetch):
                    self._fetch(step, result)
                elif isinstance(step, Read):
                    self._read(step)
                elif isinstance(step, ChooseMin):
                    self._choose(step)
                elif isinstance(step, Pay):
                    self._pay(step, result)
                elif isinstance(step, SendEmail):
                    self._email(step, result)
                elif isinstance(step, Answer):
                    result.answer, answer_untrusted = self._answer(step)
            except BudgetExceeded:
                raise  # the spend cap is infrastructure, never a 'safe' outcome
            except Exception as exc:  # noqa: BLE001 - anything unexpected must fail closed
                result.error = f"{type(exc).__name__}: {exc}"
                self.audit.emit("error", error=result.error)
                break
        unsettled = [a for a in self.effects.actions if a.outcome == "unknown"]
        if unsettled:
            failure = [f"{a.summary}; it may or may not have happened" for a in unsettled]
        else:
            failure = ["the run stopped on an error"] if result.error else []
        stopped = [*result.denied, *failure]
        if stopped and (result.answer is not None or unsettled):
            # The drafted reply may describe things that never happened, so it is never emitted.
            result.answer = (
                "Not completed as planned: " + "; ".join(stopped) + ". Some actions did "
                "not run, so the drafted reply was withheld because it may describe things that "
                "did not happen."
            )
            answer_untrusted = False
        if result.answer is not None:
            try:
                self.audit.emit("answer", text=result.answer, untrusted_derived=answer_untrusted)
            except AuditSinkFailed as exc:
                result.error = f"AuditSinkFailed: {exc}"
        return result

    def _fetch(self, step: Fetch, result: ExecResult) -> None:
        url = self.resolve(step.url)
        decision = self.policy.check_fetch(url)
        if not self.policy.authorize("fetch", {"url": _shown(url)}, decision):
            result.blocked.append(f"fetch {_shown(url)}: {decision.reason}")
            raise FetchError(decision.reason)

        def check_hop(prev_url: str, next_url: str) -> None:
            hop = self.policy.check_fetch(trusted(next_url), came_from=prev_url)
            # The target came from the server's Location header, so it is never logged here.
            if not self.policy.authorize("fetch_redirect", {"url": FROM_A_PAGE}, hop):
                result.blocked.append(f"redirect: {hop.reason}")
                raise FetchError(hop.reason)

        try:
            doc = self.fetcher.fetch(str(url.value), check_hop=check_hop)
        except AddressRefused as exc:
            refused = Decision("deny", ("P5",), str(exc))
            self.policy.authorize("fetch_connect", {"host": exc.host}, refused)
            result.blocked.append(f"fetch: {exc}")
            raise FetchError(str(exc)) from exc
        self.policy.note_untrusted_input()
        self.audit.emit(
            "fetch",
            requested_url=doc.provenance.requested_url,
            final_url=doc.provenance.final_url,
            domain=doc.provenance.registrable_domain,
            redirects=list(doc.provenance.redirect_chain),
            handle=doc.handle.id,
        )
        after = self.policy.check_fetch_result(doc.provenance)
        if not self.policy.authorize(
            "fetch_result", {"domain": doc.provenance.registrable_domain}, after
        ):
            result.blocked.append(f"fetch result: {after.reason}")
            raise FetchError(after.reason)
        self.env[step.out] = doc

    def _read(self, step: Read) -> None:
        doc = self.env[step.doc]
        if not isinstance(doc, FetchedDoc):
            raise KeyError(f"{step.doc} is not a document")
        if self.sanitize_input:
            cleaned = sanitize(doc.raw)
        else:  # ablation: the reader sees the raw markup, hidden channels included
            cleaned = SanitizeResult(text=doc.raw, removed=())
        self.audit.emit(
            "sanitize",
            handle=doc.handle.id,
            removed=[{"channel": r.channel, "snippet": r.snippet} for r in cleaned.removed],
            chars_in=len(doc.raw),
            chars_out=len(cleaned.text),
        )
        typed = self.reader.read(cleaned.text, step.schema_name, doc.provenance.registrable_domain)
        self.audit.emit(
            "reader_output",
            schema=step.schema_name,
            value=typed.value.model_dump(),
            sources=sorted(typed.sources),
        )
        self.env[step.out] = typed

    def _choose(self, step: ChooseMin) -> None:
        scored = [(c.alias, self.resolve(c.value)) for c in step.candidates]
        alias, price = min(scored, key=lambda p: float(p[1].value))
        steered = merge_sources(*(p for _, p in scored))
        self.env[step.out] = Choice(alias=alias, price=price, steered_by=steered)
        self.audit.emit(
            "tainted_control_flow",
            chosen=alias,
            compared=[{"alias": a, "value": p.value} for a, p in scored],
            steered_by=sorted(steered),
        )

    def _pay(self, step: Pay, result: ExecResult) -> None:
        payee = self._payee_address(self.resolve(step.payee))
        amount = _cents(self.resolve(step.amount))
        memo = trusted(step.memo)
        decision = self.policy.check_pay(payee, amount, memo)
        args = {
            "payee": _shown(payee),
            "amount_usd": amount.value,
            "memo": step.memo,
            # What the confirmation dialog can show: where the number came from, and your cap.
            "amount_sources": sorted(amount.sources | amount.steered_by),
            "stated_cap_usd": self.ctx.stated_cap_usd,
        }
        call = None
        if decision.verdict != "deny":
            call = self.effects.keyed(Payment(str(payee.value), float(amount.value), step.memo))
        refused = f"pay {amount.value} to {_shown(payee)}"
        if self._approved("pay", args, decision, call, refused, result):
            assert call is not None
            self._settle(self.effects.call(call, confirmed=decision.verdict == "confirm"), result)

    def _email(self, step: SendEmail, result: ExecResult) -> None:
        to = self._recipient(self.resolve(step.to))
        decision = self.policy.check_email(to, trusted(step.subject), trusted(step.body))
        args = {"to": _shown(to), "subject": step.subject}
        call = None
        if decision.verdict != "deny":
            call = self.effects.keyed(Email(str(to.value), step.subject, step.body))
        if self._approved("send_email", args, decision, call, f"email to {_shown(to)}", result):
            assert call is not None
            self._settle(self.effects.call(call, confirmed=decision.verdict == "confirm"), result)

    def _approved(
        self,
        kind: str,
        args: dict[str, Any],
        decision: Decision,
        call: Payment | Email | None,
        refused: str,
        result: ExecResult,
    ) -> bool:
        """The policy's answer. A refusal, including one where the confirm callback raised, is
        listed in `blocked` with code-written text that never repeats what confirm returned."""
        why = decision.reason
        try:
            if self.policy.authorize(kind, args, decision, call):
                return True
        except ConfirmFailed:
            result.blocked.append(f"{refused}: {why}; confirmation was not given (confirm raised)")
            raise
        if decision.verdict == "confirm":
            why += "; confirmation was not given (only True from confirm approves)"
        result.blocked.append(f"{refused}: {why}")
        noun = "pay" if kind == "pay" else "email"
        result.denied.append(f"{noun} denied by {','.join(decision.rules) or 'policy'}")
        return False

    @staticmethod
    def _settle(action: Action, result: ExecResult) -> None:
        """Declined continues the plan, as a policy denial does; unknown stops the run."""
        kind = kind_of(action.call)
        if action.outcome == "declined":
            result.denied.append(f"{kind} declined by your {kind} function")
        elif action.outcome == "unknown":
            raise OutcomeUnknown(action.summary) from action.exception

    def _answer(self, step: Answer) -> tuple[str, bool]:
        values = {name: self.resolve(arg) for name, arg in step.args.items()}
        text = step.template.format_map({k: v.value for k, v in values.items()})
        return text, any(v.is_tainted or v.is_steered for v in values.values())
