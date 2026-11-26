"""Shared result type for the baseline and the airgapped pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field

from airgap.core.actions import Action, Ledger
from airgap.core.audit import AuditLog


@dataclass
class RunResult:
    """What one run did.

    `actions` is the single record of side effects, written by the guard after each function call
    returns or raises. `ledger` and `unsettled` are derived from it, so they cannot disagree.

      unsettled   calls whose outcome is unknown: check with your provider before retrying
      ledger      what definitely happened
      blocked     what the policy refused, code-written; a value from a page reads "<from a page>"
      error       why the run stopped early, code-written
      final_text  the reply; withheld and replaced by a code-written notice when anything was
                  denied, declined, unknown or errored
      plan_steps  how many steps the validated plan had; None when no plan was made
      summary     one code-written line per action, refusal and stop, for logs and UIs, and
                  "the planner made no plan steps" when the plan was empty
    """

    final_text: str
    audit: AuditLog
    actions: tuple[Action, ...] = ()
    blocked: list[str] = field(default_factory=list)
    confirmations: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_s: float = 0.0
    error: str | None = None
    plan_steps: int | None = None

    @property
    def ledger(self) -> Ledger:
        """The calls whose function returned normally."""
        return Ledger.of(self.actions)

    @property
    def unsettled(self) -> tuple[Action, ...]:
        """Calls whose function raised something other than `Declined`: they may or may not have
        happened. Never in `ledger`."""
        return tuple(a for a in self.actions if a.outcome == "unknown")

    @property
    def summary(self) -> tuple[str, ...]:
        return (
            *(a.summary for a in self.actions),
            *self.blocked,
            *(["the planner made no plan steps"] if self.plan_steps == 0 else []),
            *([self.error] if self.error else []),
        )
