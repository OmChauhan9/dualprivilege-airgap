"""Policy engine: plain code, no LLM. This is the security boundary.

Rules
  P1  payee must be on the user's allowlist or written by the user in their own message
  P2  amount must be positive, under the per-transaction limit and the session limit
  P3  email recipients must be on the user's allowlist
  P4  tainted values may not flow to payee / recipient / subject / body / memo sinks;
      a tainted amount is allowed only with confirmation
  P5  fetches only go to domains the user named or allowlisted (including after redirects)
  P6  a choice made by tainted data (steering) requires confirmation
  R2  Rule of Two session state: [A] untrusted input seen, [B] sensitive tools, [C] state change
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from airgap.core.audit import AuditLog
from airgap.core.fetcher import URL_RE, Provenance, domain_of_url
from airgap.core.values import Tainted

Verdict = Literal["allow", "deny", "confirm"]
Confirmer = Callable[[str, dict[str, Any], "Decision"], bool]


@dataclass(frozen=True)
class Decision:
    verdict: Verdict
    rules: tuple[str, ...] = ()
    reason: str = ""


ALLOW = Decision("allow")


def deny_all(kind: str, args: dict[str, Any], decision: Decision) -> bool:
    return False


def approve_all(kind: str, args: dict[str, Any], decision: Decision) -> bool:
    return True


@dataclass
class UserContext:
    user_text: str
    payee_allowlist: dict[str, str] = field(default_factory=dict)  # alias -> address
    email_allowlist: frozenset[str] = frozenset()
    extra_domains: frozenset[str] = frozenset()
    per_tx_limit_usd: float = 100.0
    session_limit_usd: float = 200.0
    sensitive: bool = True  # Rule of Two [B]: the agent holds payment / mail authority

    @property
    def allowed_domains(self) -> frozenset[str]:
        named = {domain_of_url(u) for u in URL_RE.findall(self.user_text)}
        return frozenset(named) | self.extra_domains


class PolicyEngine:
    def __init__(self, ctx: UserContext, audit: AuditLog, confirmer: Confirmer = deny_all) -> None:
        self.ctx = ctx
        self.audit = audit
        self.confirmer = confirmer
        self.untrusted_seen = False  # Rule of Two [A]
        self.spent_usd = 0.0
        self.confirmations = 0

    def note_untrusted_input(self) -> None:
        self.untrusted_seen = True

    def rule_of_two(self, state_change: bool) -> dict[str, bool]:
        return {
            "A_untrusted_input": self.untrusted_seen,
            "B_sensitive": self.ctx.sensitive,
            "C_state_change": state_change,
        }

    # ---- checks -------------------------------------------------------------------------

    def check_fetch(self, url: Tainted) -> Decision:
        if url.is_tainted:
            return Decision("deny", ("P5", "P4"), "url derived from untrusted content")
        domain = domain_of_url(str(url.value))
        if domain not in self.ctx.allowed_domains:
            return Decision("deny", ("P5",), f"domain {domain} not named by the user")
        return ALLOW

    def check_fetch_result(self, prov: Provenance) -> Decision:
        if prov.registrable_domain not in self.ctx.allowed_domains:
            return Decision(
                "deny", ("P5",), f"redirected to unlisted domain {prov.registrable_domain}"
            )
        return ALLOW

    def check_pay(self, payee: Tainted, amount: Tainted, memo: Tainted) -> Decision:
        rules: list[str] = []
        addr = str(payee.value)
        if payee.is_tainted:
            return Decision("deny", ("P4",), "payee derived from untrusted content")
        if memo.is_tainted:
            return Decision("deny", ("P4",), "memo derived from untrusted content")
        if addr not in self.ctx.payee_allowlist.values() and addr not in self.ctx.user_text:
            return Decision("deny", ("P1",), f"payee {addr} is not on the allowlist")
        try:
            amt = float(amount.value)
        except (TypeError, ValueError):
            return Decision("deny", ("P2",), "amount is not a number")
        if amt <= 0 or amt > self.ctx.per_tx_limit_usd:
            return Decision("deny", ("P2",), f"amount {amt:g} outside per-transaction limit")
        if self.spent_usd + amt > self.ctx.session_limit_usd:
            return Decision("deny", ("P2",), "session spend limit exceeded")
        if amount.is_tainted:
            rules.append("P4")
        if payee.is_steered or amount.is_steered:
            rules.append("P6")
        if rules and self.untrusted_seen:
            return Decision("confirm", tuple(rules), "value chosen or supplied by untrusted data")
        return ALLOW

    def check_email(self, to: Tainted, subject: Tainted, body: Tainted) -> Decision:
        if to.is_tainted or subject.is_tainted or body.is_tainted:
            return Decision("deny", ("P4",), "email field derived from untrusted content")
        if str(to.value).lower() not in {e.lower() for e in self.ctx.email_allowlist}:
            return Decision("deny", ("P3",), f"recipient {to.value} is not on the allowlist")
        return ALLOW

    # ---- enforcement --------------------------------------------------------------------

    def authorize(self, kind: str, args: dict[str, Any], decision: Decision) -> bool:
        state_change = kind in ("pay", "send_email")
        approved = decision.verdict == "allow"
        if decision.verdict == "confirm":
            self.confirmations += 1
            approved = self.confirmer(kind, args, decision)
        self.audit.emit(
            "policy",
            action=kind,
            args=args,
            verdict=decision.verdict,
            rules=list(decision.rules),
            reason=decision.reason,
            approved=approved,
            rule_of_two=self.rule_of_two(state_change),
        )
        if approved and kind == "pay":
            self.spent_usd += float(args["amount_usd"])
        return approved
