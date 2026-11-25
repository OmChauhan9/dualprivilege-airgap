"""The mock world's bank and mailbox: the `pay` and `send_email` a developer would pass.

They record what they received in their own Ledger, which the guard cannot write. The eval checks
that the guard's `RunResult.ledger` agrees with it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from airgap.core.actions import Email, Ledger, Payment


@dataclass
class WorldTools:
    """One per run. Passed as `Airgap(pay=world.pay, send_email=world.send_email)`."""

    ledger: Ledger = field(default_factory=Ledger)

    def pay(self, p: Payment) -> str:
        self.ledger.payments.append(p)
        return f"paid {p.amount_usd:g} USD to {p.payee}"

    def send_email(self, e: Email) -> str:
        self.ledger.emails.append(e)
        return f"email sent to {e.to}"
