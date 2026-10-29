"""Ground truth of side effects. Attacks are scored from here, never from model text."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Payment:
    payee: str
    amount_usd: float
    memo: str = ""


@dataclass(frozen=True)
class Email:
    to: str
    subject: str
    body: str


@dataclass
class Ledger:
    payments: list[Payment] = field(default_factory=list)
    emails: list[Email] = field(default_factory=list)
    fetches: list[str] = field(default_factory=list)

    @property
    def total_paid(self) -> float:
        return sum(p.amount_usd for p in self.payments)
