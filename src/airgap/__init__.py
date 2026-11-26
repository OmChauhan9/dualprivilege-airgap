"""Airgap: a payment and email guard for LLM agents.

    from airgap import Airgap
    guard = Airgap(planner=model, pay=pay, send_email=send_email,
                   payees={"landlord": "0x..."}, email_recipients={"me@example.com"})
    result = guard.run("Pay my landlord the invoice at https://rent.example.com/due")

`model`, `pay` and `send_email` are yours, and the URL is a placeholder. To try it offline, pass
`airgap.models.fake.FakeLLM()` as the planner and an in-process page as `transport=`, as
examples/byo_tools.py does. FakeLLM is a script: it plans only a few request wordings, such as
paying "the invoice at" a URL, and makes no plan for others.

Everything not listed in `__all__` is internal and may change without notice.
"""

from airgap.core.actions import Action, Declined, Email, Ledger, Payment
from airgap.core.airgap import Airgap
from airgap.core.audit import AuditLog, Event
from airgap.core.policy import Confirmation
from airgap.core.result import RunResult
from airgap.models.base import ChatModel, Completion

__all__ = [
    "Airgap",
    "Declined",
    "Confirmation",
    "Payment",
    "Email",
    "RunResult",
    "Action",
    "Ledger",
    "AuditLog",
    "Event",
    "ChatModel",
    "Completion",
]
