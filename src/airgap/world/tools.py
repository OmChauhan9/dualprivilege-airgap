"""Side-effecting tools. They only write to the ledger; there is nothing real behind them."""

from __future__ import annotations

import httpx

from airgap.world.ledger import Email, Ledger, Payment


class WorldTools:
    def __init__(self, client: httpx.Client, ledger: Ledger) -> None:
        self.client = client
        self.ledger = ledger

    def pay(self, payee: str, amount_usd: float, memo: str = "") -> str:
        self.ledger.payments.append(Payment(payee, float(amount_usd), memo))
        return f"paid {amount_usd:g} USD to {payee}"

    def send_email(self, to: str, subject: str, body: str) -> str:
        self.ledger.emails.append(Email(to, subject, body))
        return f"email sent to {to}"
