"""Policy engine: plain code, no LLM. This is the security boundary.

Rules
  P0  an action runs only if the developer gave the guard a function for it (pay, send_email)
  P1  payee must be on the user's allowlist or written by the user in their own message
  P2  amount must be positive and at most the per-transaction limit and the session limit; a cap the
      user states in their own request bounds every payment and the task total; with no cap, the
      amounts read from pages share the per-transaction limit as a task budget
  P3  email recipients must be on the user's allowlist
  P4  tainted values may not flow to payee / recipient / subject / body / memo sinks;
      a tainted amount is allowed only with confirmation
  P5  fetches only go to domains the user named or allowlisted; every redirect hop is checked
      before it is requested, not just the final domain; a request keeps the scheme's default
      port unless the user named that exact origin, and a hop never goes from https to http;
      with the guard's own transport, every connection goes to a public address only
  P6  a choice made by tainted data (steering) requires confirmation
  P7  only HTTP 2xx responses are read as content; error pages never reach the reader
  R2  Rule of Two session state: [A] untrusted input seen, [B] sensitive tools, [C] state change
"""

from __future__ import annotations

import ipaddress
import math
import re
import string
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urlsplit

from airgap.core.actions import Call, Kind
from airgap.core.audit import AuditLog
from airgap.core.fetcher import URL_RE, Provenance, domain_of_url, is_hostname
from airgap.core.values import Tainted

Verdict = Literal["allow", "deny", "confirm"]
IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
_DEFAULT_PORTS = {"http": 80, "https": 443}


def is_public_address(addr: IPAddress) -> bool:
    """P5, connection rule: a socket may only go to a globally routable unicast address.

    Refuses loopback, private, link-local (cloud metadata), shared (CGNAT), unique-local,
    multicast, unspecified and reserved ranges, also when wrapped in an IPv6 form.
    """
    if isinstance(addr, ipaddress.IPv6Address):
        embedded = addr.ipv4_mapped or addr.sixtofour or (addr.teredo[1] if addr.teredo else None)
        if embedded is not None:
            return is_public_address(embedded)
    return addr.is_global and not addr.is_multicast


def _origin(url: str) -> tuple[str, str, int] | None:
    """(scheme, host, port) with the default port filled in; None for an invalid port."""
    parts = urlsplit(url)
    try:
        port = parts.port
    except ValueError:
        return None
    if port is None:
        port = _DEFAULT_PORTS.get(parts.scheme, 0)
    return parts.scheme, parts.hostname or "", port


@dataclass(frozen=True)
class Confirmation:
    """What the user is asked to approve, built by code from the exact call that would run.

    Every field is the user's own data, the policy-checked call, or code-written text; page text
    never appears, so it is safe to show. Approve by returning exactly True.
    """

    call: Call  # the Payment or Email your function receives if you approve
    rules: tuple[str, ...]  # ("P4",) an amount read from a page, ("P6",) a choice made by page data
    reason: str
    # The sites the amount was read from and the sites whose values chose the payee or recipient.
    amount_read_from: tuple[str, ...] = ()
    stated_cap_usd: float | None = None  # the cap the user wrote in the request, if any


Confirm = Callable[[Confirmation], object]


class ConfirmFailed(RuntimeError):
    """The confirm callback raised: counted as a refusal, and the run stops."""


@dataclass(frozen=True)
class Decision:
    verdict: Verdict
    rules: tuple[str, ...] = ()
    reason: str = ""
    read_from: frozenset[str] = frozenset()  # a confirm verdict: the sources behind it


ALLOW = Decision("allow")


def deny_all(c: Confirmation) -> bool:
    """The default `confirm`: refuse everything that needs a human."""
    return False


def approve_all(c: Confirmation) -> bool:
    """Evaluation only (C_airgap_approve_all)."""
    return True


_CAP_RE = re.compile(
    r"\b(?:up to|at most|no more than|not more than|max(?:imum)?(?: of)?)\s+\$\s?"
    r"(\d[\d,]*(?:\.\d+)?)",
    re.I,
)


# An address counts only as a word of its own: split on plain spaces, tabs and line breaks, with
# nothing around it but quotes, brackets, sentence punctuation or a dash joining a plain word
# ("0x...—thanks"). Glued to a link character (`/ ? = & @ : # %`), an ASCII hyphen (a host
# character), another word or an unusual space such as U+00A0, it is part of something else, often
# a pasted link that carries a wallet the user never meant to name. A rule on the word itself, not
# a link pattern, so no spelling of a link (`my_shop.test/`, `user:0x...@host`, `data:`) needs to be
# listed.
# The address form P1 recognises. `Airgap(payees=...)` must use it too.
ADDRESS_RE = re.compile(r"0x[0-9a-fA-F]{6,}")
_ASCII_LOWER = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)
_WORD_SEP_RE = re.compile(r"[ \t\r\n]+")
_ADDRESS_WORD_RE = re.compile(
    r"(?:[(\[{<\"'`*\u201c\u2018\u00ab\u300c\u300e\uff08\u2013\u2014]|[^\W_]+[\u2013\u2014])*"
    "(" + ADDRESS_RE.pattern + ")"
    r"(?:[)\]}>\"'`*\u201d\u2019\u00bb\u300d\u300f\uff09.,;:!?\u2013\u2014\u2026"
    r"\u3002\uff0c\u3001\uff1b\uff1a\uff01\uff1f]|[\u2013\u2014][^\W_]+)*"
)


@dataclass
class UserContext:
    user_text: str
    payee_allowlist: dict[str, str] = field(default_factory=dict)  # alias -> address
    email_allowlist: frozenset[str] = frozenset()
    extra_domains: frozenset[str] = frozenset()
    per_tx_limit_usd: float = 100.0
    session_limit_usd: float = 200.0
    tools: frozenset[Kind] = frozenset({"pay", "send_email"})  # P0: the functions given

    def listed_recipient(self, address: str) -> str | None:
        """The allowlist entry `address` names, compared ignoring ASCII case only, or None.

        Unicode case folding is not used: `str.lower()` folds the Kelvin sign to "k", so a
        look-alike would match an entry while naming a different mailbox.
        """
        key = address.translate(_ASCII_LOWER)
        return min(
            (e for e in self.email_allowlist if e.translate(_ASCII_LOWER) == key), default=None
        )

    @property
    def sensitive(self) -> bool:
        """Rule of Two [B]: the agent holds payment or mail authority."""
        return bool(self.tools)

    @property
    def user_addresses(self) -> frozenset[str]:
        """Whole addresses the user wrote in their own message (exact match, not substrings).

        An address inside a URL the user pasted does not count: a lure link can carry a wallet the
        user never meant to name.
        """
        words = (_ADDRESS_WORD_RE.fullmatch(w) for w in _WORD_SEP_RE.split(self.user_text))
        return frozenset(m.group(1) for m in words if m)

    @property
    def stated_cap_usd(self) -> float | None:
        """A spending cap the user wrote in their own request. Never read from fetched content.

        If the request states several, the smallest wins, so a second phrase cannot widen the first.
        """
        found = [float(c.replace(",", "")) for c in _CAP_RE.findall(self.user_text)]
        return min(found) if found else None

    @property
    def named_origins(self) -> frozenset[tuple[str, str, int]]:
        """(scheme, host, port) of every URL the user wrote: only these may use another port."""
        return frozenset(o for u in URL_RE.findall(self.user_text) if (o := _origin(u)))

    @property
    def allowed_domains(self) -> frozenset[str]:
        named = {domain_of_url(u) for u in URL_RE.findall(self.user_text)}
        return frozenset(named) | self.extra_domains


class PermissivePolicy:
    """Ablation only: logs decisions but allows everything. Never used outside evaluation."""

    def __init__(self, ctx: UserContext, audit: AuditLog, confirmer: Confirm = deny_all) -> None:
        self.inner = PolicyEngine(ctx, audit, confirmer)
        self.ctx = ctx

    @property
    def confirmations(self) -> int:
        return self.inner.confirmations

    def note_untrusted_input(self) -> None:
        self.inner.note_untrusted_input()

    def check_fetch(self, url: Tainted, came_from: str | None = None) -> Decision:
        return ALLOW

    def check_fetch_result(self, prov: Provenance) -> Decision:
        if not 200 <= prov.status < 300:
            return Decision("deny", ("P7",), f"HTTP {prov.status} response is not read as content")
        return ALLOW

    def check_pay(self, payee: Tainted, amount: Tainted, memo: Tainted) -> Decision:
        return ALLOW

    def check_email(self, to: Tainted, subject: Tainted, body: Tainted) -> Decision:
        return ALLOW

    def authorize(
        self, kind: str, args: dict[str, Any], decision: Decision, call: Call | None = None
    ) -> bool:
        return self.inner.authorize(kind, args, decision, call)


class PolicyEngine:
    def __init__(self, ctx: UserContext, audit: AuditLog, confirmer: Confirm = deny_all) -> None:
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

    def check_fetch(self, url: Tainted, came_from: str | None = None) -> Decision:
        """`came_from` is the URL that redirected here, None for the first request."""
        if url.is_tainted:
            return Decision("deny", ("P5", "P4"), "url derived from untrusted content")
        domain = domain_of_url(str(url.value))
        if domain not in self.ctx.allowed_domains:
            if not is_hostname(domain):
                return Decision("deny", ("P5",), "the address has no valid domain the user named")
            return Decision("deny", ("P5",), f"domain {domain} not named by the user")
        origin = _origin(str(url.value))
        if origin is None:
            return Decision("deny", ("P5",), "the address has an invalid port")
        scheme, host, port = origin
        if port != _DEFAULT_PORTS.get(scheme) and origin not in self.ctx.named_origins:
            return Decision("deny", ("P5",), f"port {port} on {host} was not named by the user")
        if came_from is not None and urlsplit(came_from).scheme == "https" and scheme == "http":
            return Decision("deny", ("P5",), "a redirect from https to http")
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
        if "pay" not in self.ctx.tools:
            return Decision("deny", ("P0",), "no pay function was given to the guard")
        rules: list[str] = []
        addr = str(payee.value)
        if payee.is_tainted:
            return Decision("deny", ("P4",), "payee derived from untrusted content")
        if memo.is_tainted:
            return Decision("deny", ("P4",), "memo derived from untrusted content")
        if addr not in self.ctx.payee_allowlist.values() and addr not in self.ctx.user_addresses:
            return Decision("deny", ("P1",), f"payee {addr} is not on the allowlist")
        if isinstance(amount.value, bool):  # float(True) is 1.0; a read bool field can get here
            return Decision("deny", ("P2",), "amount is not a number")
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
            read_from = amount.sources | amount.steered_by | payee.steered_by
            return Decision(
                "confirm", tuple(rules), "value chosen or supplied by untrusted data", read_from
            )
        return ALLOW

    def check_email(self, to: Tainted, subject: Tainted, body: Tainted) -> Decision:
        if "send_email" not in self.ctx.tools:
            return Decision("deny", ("P0",), "no send_email function was given to the guard")
        if to.is_tainted or subject.is_tainted or body.is_tainted:
            return Decision("deny", ("P4",), "email field derived from untrusted content")
        if self.ctx.listed_recipient(str(to.value)) is None:
            return Decision("deny", ("P3",), f"recipient {to.value} is not on the allowlist")
        if to.is_steered and self.untrusted_seen:
            return Decision("confirm", ("P6",), "recipient chosen by untrusted data", to.steered_by)
        return ALLOW

    # ---- enforcement --------------------------------------------------------------------

    def authorize(
        self, kind: str, args: dict[str, Any], decision: Decision, call: Call | None = None
    ) -> bool:
        """`call` is the exact Payment or Email that runs if approved; a confirm verdict without
        one is refused. The callback receives that frozen call, so it cannot change what runs, and
        approves only by returning exactly True. A callback that raises is a refusal: the event
        is still written, then ConfirmFailed stops the run."""
        state_change = kind in ("pay", "send_email")
        approved = decision.verdict == "allow"
        failure: Exception | None = None
        if decision.verdict == "confirm":
            self.confirmations += 1
            if call is not None:
                try:
                    approved = self.confirmer(self._confirmation(call, args, decision)) is True
                except Exception as exc:  # noqa: BLE001 - a crashed dialog is a refusal
                    failure = exc
        extra = {"confirm_error": type(failure).__name__} if failure else {}
        self.audit.emit(
            "policy",
            action=kind,
            args=args,
            verdict=decision.verdict,
            rules=list(decision.rules),
            reason=decision.reason,
            approved=approved,
            rule_of_two=self.rule_of_two(state_change),
            **extra,
        )
        if failure is not None:
            raise ConfirmFailed(
                f"the confirm callback raised {type(failure).__name__}"
            ) from failure
        if approved and kind == "pay":
            self.spent_usd += float(args["amount_usd"])
            if args.get("amount_sources"):
                self.tainted_spent_usd += float(args["amount_usd"])
        return approved

    @staticmethod
    def _confirmation(call: Call, args: dict[str, Any], decision: Decision) -> Confirmation:
        return Confirmation(
            call,
            decision.rules,
            decision.reason,
            amount_read_from=tuple(
                sorted(s.removeprefix("untrusted:") for s in decision.read_from)
            ),
            stated_cap_usd=args.get("stated_cap_usd"),
        )


# The interpreter takes either; only the eval builds the permissive one.
Policy = PolicyEngine | PermissivePolicy
