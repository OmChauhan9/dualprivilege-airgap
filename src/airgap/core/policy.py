"""Policy engine: plain code, no LLM. This is the security boundary.

Rules
  P1  payee must be on the user's allowlist or written by the user in their own message
  P2  amount must be positive, under the per-transaction limit and the session limit; a cap the
      user states in their own request bounds every payment and the task total; with no cap, the
      amounts read from pages share the per-transaction limit as a task budget
  P3  email recipients must be on the user's allowlist
  P4  tainted values may not flow to payee / recipient / subject / body / memo sinks;
      a tainted amount is allowed only with confirmation
  P5  fetches only go to domains the user named or allowlisted; every redirect hop is checked
      before it is requested, not just the final domain
  P6  a choice made by tainted data (steering) requires confirmation
  P7  only HTTP 2xx responses are read as content; error pages never reach the reader
  R2  Rule of Two session state: [A] untrusted input seen, [B] sensitive tools, [C] state change
"""

from __future__ import annotations

import math
import re
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


_CAP_RE = re.compile(
    r"\b(?:up to|at most|no more than|not more than|max(?:imum)?(?: of)?)\s+\$\s?"
    r"(\d[\d,]*(?:\.\d+)?)",
    re.I,
)


# Whole tokens only: a hex run glued to other word characters ("abc0x1234567") is not an address.
# Anything a reader would take for a link, in any letter case, with or without a scheme. Wider
# than URL_RE on purpose: it only removes text before addresses are extracted, so it can only
# shrink the set of addresses the user is taken to have written.
_LINK_RE = re.compile(r"(?:\b[a-z][a-z0-9+.-]*://|\bwww\.)\S+", re.I)
_ADDRESS_RE = re.compile(r"(?<!\w)0x[0-9a-fA-F]{6,}(?!\w)")


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
    def user_addresses(self) -> frozenset[str]:
        """Whole addresses the user wrote in their own message (exact match, not substrings).

        An address inside a URL the user pasted does not count: a lure link can carry a wallet the
        user never meant to name.
        """
        return frozenset(_ADDRESS_RE.findall(_LINK_RE.sub(" ", self.user_text)))

    @property
    def stated_cap_usd(self) -> float | None:
        """A spending cap the user wrote in their own request. Never read from fetched content.

        If the request states several, the smallest wins, so a second phrase cannot widen the first.
        """
        found = [float(c.replace(",", "")) for c in _CAP_RE.findall(self.user_text)]
        return min(found) if found else None

    @property
    def allowed_domains(self) -> frozenset[str]:
        named = {domain_of_url(u) for u in URL_RE.findall(self.user_text)}
        return frozenset(named) | self.extra_domains


class PermissivePolicy:
    """Ablation only: logs decisions but allows everything. Never used outside evaluation."""

    def __init__(self, ctx: UserContext, audit: AuditLog, confirmer: Confirmer = deny_all) -> None:
        self.inner = PolicyEngine(ctx, audit, confirmer)
        self.ctx = ctx

    @property
    def confirmations(self) -> int:
        return self.inner.confirmations

    def note_untrusted_input(self) -> None:
        self.inner.note_untrusted_input()

    def check_fetch(self, url: Tainted) -> Decision:
        return ALLOW

    def check_fetch_result(self, prov: Provenance) -> Decision:
        if not 200 <= prov.status < 300:
            return Decision("deny", ("P7",), f"HTTP {prov.status} response is not read as content")
        return ALLOW

    def check_pay(self, payee: Tainted, amount: Tainted, memo: Tainted) -> Decision:
        return ALLOW

    def check_email(self, to: Tainted, subject: Tainted, body: Tainted) -> Decision:
        return ALLOW

    def authorize(self, kind: str, args: dict[str, Any], decision: Decision) -> bool:
        return self.inner.authorize(kind, args, decision)


class PolicyEngine:
    def __init__(self, ctx: UserContext, audit: AuditLog, confirmer: Confirmer = deny_all) -> None:
        self.ctx = ctx
        self.audit = audit
        self.confirmer = confirmer
        self.untrusted_seen = False  # Rule of Two [A]
        self.spent_usd = 0.0
        self.tainted_spent_usd = 0.0  # approved amounts that were read from pages
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
        if not 200 <= prov.status < 300:
            return Decision("deny", ("P7",), f"HTTP {prov.status} response is not read as content")
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
        if addr not in self.ctx.payee_allowlist.values() and addr not in self.ctx.user_addresses:
            return Decision("deny", ("P1",), f"payee {addr} is not on the allowlist")
        try:
            amt = float(amount.value)
        except (TypeError, ValueError):
            return Decision("deny", ("P2",), "amount is not a number")
        if not math.isfinite(amt):
            return Decision("deny", ("P2",), "amount is not a finite number")
        if amt <= 0 or amt > self.ctx.per_tx_limit_usd:
            return Decision("deny", ("P2",), f"amount {amt:g} outside per-transaction limit")
        if self.spent_usd + amt > self.ctx.session_limit_usd:
            return Decision("deny", ("P2",), "session spend limit exceeded")
        cap = self.ctx.stated_cap_usd
        if cap is not None and self.spent_usd + amt > cap:
            return Decision(
                "deny",
                ("P2",),
                f"total {self.spent_usd + amt:g} would exceed your stated cap {cap:g}",
            )
        page_supplied = amount.is_tainted or amount.is_steered
        if cap is None and page_supplied:
            budget = self.ctx.per_tx_limit_usd
            if self.tainted_spent_usd + amt > budget:
                return Decision(
                    "deny",
                    ("P2",),
                    f"amounts read from pages would total {self.tainted_spent_usd + amt:g}, over "
                    f"the task budget {budget:g}; state a cap in your request to raise it",
                )
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
        if to.is_steered and self.untrusted_seen:
            return Decision("confirm", ("P6",), "recipient chosen by untrusted data")
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
            if args.get("amount_sources"):
                self.tainted_spent_usd += float(args["amount_usd"])
        return approved
