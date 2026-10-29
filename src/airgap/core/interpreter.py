"""Interpreter: executes a validated plan. Tools run only after the policy engine approves."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from airgap.core.audit import AuditLog
from airgap.core.fetcher import FetchedDoc, Fetcher, FetchError
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
from airgap.core.policy import PermissivePolicy, PolicyEngine, UserContext
from airgap.core.reader import Reader
from airgap.core.sanitizer import SanitizeResult, sanitize
from airgap.core.values import Tainted, merge_sources, trusted
from airgap.models.budget import BudgetExceeded
from airgap.world.tools import WorldTools


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
        tools: WorldTools,
        policy: PolicyEngine | PermissivePolicy,
        audit: AuditLog,
        sanitize_input: bool = True,
    ) -> None:
        self.sanitize_input = sanitize_input
        self.ctx = ctx
        self.fetcher = fetcher
        self.reader = reader
        self.tools = tools
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

    # ---- execution ----------------------------------------------------------------------

    def run(self, plan: Plan) -> ExecResult:
        result = ExecResult()
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
                    result.answer = self._answer(step)
            except BudgetExceeded:
                raise  # the spend cap is infrastructure, never a 'safe' outcome
            except Exception as exc:  # noqa: BLE001 - anything unexpected must fail closed
                result.error = f"{type(exc).__name__}: {exc}"
                self.audit.emit("error", error=result.error)
                break
        if result.denied and result.answer is not None:
            # The reply was drafted before anything ran; it may describe actions policy denied.
            result.answer = (
                "Not completed as planned: " + "; ".join(result.denied) + ". Some actions did "
                "not run, so the drafted reply was withheld because it may describe things that "
                "did not happen."
            )
            self.audit.emit("answer", text=result.answer, untrusted_derived=False)
        return result

    def _fetch(self, step: Fetch, result: ExecResult) -> None:
        url = self.resolve(step.url)
        decision = self.policy.check_fetch(url)
        if not self.policy.authorize("fetch", {"url": url.value}, decision):
            result.blocked.append(f"fetch {url.value}: {decision.reason}")
            raise FetchError(decision.reason)

        def check_hop(next_url: str) -> None:
            hop = self.policy.check_fetch(trusted(next_url))
            if not self.policy.authorize("fetch_redirect", {"url": next_url}, hop):
                result.blocked.append(f"redirect to {next_url}: {hop.reason}")
                raise FetchError(hop.reason)

        doc = self.fetcher.fetch(str(url.value), check_hop=check_hop)
        self.policy.note_untrusted_input()
        self.tools.ledger.fetches.append(doc.provenance.final_url)
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
        amount = self.resolve(step.amount)
        memo = trusted(step.memo)
        decision = self.policy.check_pay(payee, amount, memo)
        args = {
            "payee": payee.value,
            "amount_usd": amount.value,
            "memo": step.memo,
            # What the confirmation dialog can show: where the number came from, and your cap.
            "amount_sources": sorted(amount.sources | amount.steered_by),
            "stated_cap_usd": self.ctx.stated_cap_usd,
        }
        if self.policy.authorize("pay", args, decision):
            self.tools.pay(str(payee.value), float(amount.value), step.memo)
        else:
            result.blocked.append(f"pay {amount.value} to {payee.value}: {decision.reason}")
            result.denied.append(f"pay denied by {','.join(decision.rules) or 'policy'}")

    def _email(self, step: SendEmail, result: ExecResult) -> None:
        to = self.resolve(step.to)
        decision = self.policy.check_email(to, trusted(step.subject), trusted(step.body))
        args = {"to": to.value, "subject": step.subject}
        if self.policy.authorize("send_email", args, decision):
            self.tools.send_email(str(to.value), step.subject, step.body)
        else:
            result.blocked.append(f"email to {to.value}: {decision.reason}")
            result.denied.append(f"email denied by {','.join(decision.rules) or 'policy'}")

    def _answer(self, step: Answer) -> str:
        values = {name: self.resolve(arg) for name, arg in step.args.items()}
        text = step.template.format_map({k: v.value for k, v in values.items()})
        self.audit.emit(
            "answer",
            text=text,
            untrusted_derived=any(v.is_tainted for v in values.values()),
        )
        return text
