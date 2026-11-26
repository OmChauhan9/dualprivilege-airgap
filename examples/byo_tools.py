"""Bring your own pay and send_email: the airgap in front of your functions, offline.

    uv run python examples/byo_tools.py

It uses the offline FakeLLM and an in-process web page, so it makes no network request and no
model call. For a real agent, pass your model as `planner` and drop `transport=`: the guard then
fetches pages itself, from public addresses only.
"""

from __future__ import annotations

from airgap import Airgap, Confirmation, Declined, Email, Payment
from airgap.models.fake import FakeLLM
from airgap.world.mock_web import MockWeb

LANDLORD = "0x1ED6E4000000000000000000000000000000AA01"
DUE = "https://rent.example.test/due"
REQUEST = f"Pay my landlord the invoice at {DUE}, up to $500"

sent: list[Payment] = []


def pay(p: Payment) -> str:
    """Your payment call. Return: it happened. Raise Declined: it certainly did not."""
    if p.amount_usd > 1000:
        raise Declined("over the account's daily limit")
    sent.append(p)
    return f"tx-{len(sent)}"  # kept as Action.receipt, never shown to a model


def flaky_pay(p: Payment) -> str:
    """A payment call that times out: the money may or may not have moved."""
    raise TimeoutError("bank gateway timed out")


def send_email(e: Email) -> str:
    return "msg-1"


def confirm(c: Confirmation) -> bool:
    """Asked only when a page supplied the amount (P4) or chose the payee (P6)."""
    assert isinstance(c.call, Payment)
    print(
        f"confirm? pay {c.call.amount_usd:.2f} USD to {c.call.payee} "
        f"(amount read from {', '.join(c.amount_read_from)}; your cap {c.stated_cap_usd})"
    )
    return c.call.amount_usd <= 50  # must be exactly True to approve


def guard(pay_fn) -> Airgap:
    web = MockWeb().add(DUE, "<p>Amount due: $45.00</p><p>Billing period: monthly</p>")
    return Airgap(
        planner=FakeLLM(),
        pay=pay_fn,
        send_email=send_email,
        payees={"landlord": LANDLORD},
        email_recipients={"me@example.test"},
        per_payment_usd=500,
        per_run_usd=1000,
        confirm=confirm,
        transport=web.transport(),  # tests and examples only: the in-process page
    )


def main() -> None:
    ok = guard(pay).run(REQUEST, run_id="example-1")
    print("reply:", ok.final_text)
    print("paid:", [(p.payee, p.amount_usd, p.key) for p in ok.ledger.payments])

    unknown = guard(flaky_pay).run(REQUEST, run_id="example-2")
    for action in unknown.unsettled:
        print("unsettled:", action.summary, "key", action.call.key)
    print("error:", unknown.error)
    print("reply:", unknown.final_text)


if __name__ == "__main__":
    main()
