"""Side effects: what the guard asked the developer's functions to do, and what came of it.

This module is the only place a developer's `pay` or `send_email` is called. It turns whatever the
function does (return, raise `Declined`, raise anything else) into one `Action` with a three-way
outcome, so no caller has to decide what an exception from a payment function means.
"""

from __future__ import annotations

import contextlib
import hashlib
import inspect
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import Literal

from airgap.core.audit import AuditLog, AuditSinkFailed


@dataclass(frozen=True)
class Payment:
    """One call to `pay`, exactly as the function receives it.

    `amount_usd` is a whole number of cents: the guard rounds it half-up before P2 checks it, so
    the amount approved is the amount charged. `Decimal(str(p.amount_usd))` is exact.
    `key` is an idempotency key: pass it to your payment provider so a retry cannot pay twice. It
    is derived from the run id, the payee, the cent amount and the memo, and how many identical
    payments the run attempted before. It is stable only for the same payment under the same
    `run_id`: a run repeated with that `run_id` sends the same key for it. Not part of equality.
    """

    payee: str
    amount_usd: float
    memo: str = ""
    key: str = field(default="", compare=False)


@dataclass(frozen=True)
class Email:
    """One call to `send_email`, exactly as the function receives it. `key` as in `Payment`,
    derived from the recipient and the subject."""

    to: str
    subject: str
    body: str
    key: str = field(default="", compare=False)


Call = Payment | Email
Kind = Literal["pay", "send_email"]

# Return normally: it happened. Raise `Declined`: it certainly did not. Raise anything else: the
# outcome is unknown. The return value is kept untouched as `Action.receipt`.
PayFn = Callable[[Payment], object]
SendEmailFn = Callable[[Email], object]


class Declined(Exception):  # noqa: N818 - a verdict the developer raises, not a fault
    """Raise from `pay` or `send_email` when you know the action did not happen.

    For example, the bank answered "insufficient funds". The guard records the call as declined,
    keeps running the plan as it does after a policy denial, and withholds the drafted reply. Any
    other exception means the outcome is unknown, and the run stops. The exception stays in
    `Action.exception`; its message is never copied into a result field, the audit log or a model.
    """


Outcome = Literal["done", "declined", "unknown"]


def kind_of(call: Call) -> Kind:
    return "pay" if isinstance(call, Payment) else "send_email"


def _describe(call: Call) -> str:
    if isinstance(call, Payment):
        return f"pay {call.amount_usd:.2f} USD to {call.payee}"
    return f"send_email to {call.to}"


@dataclass(frozen=True)
class Action:
    """One approved call to a developer function, and its outcome.

    A call the policy refused never reaches the function and appears in `RunResult.blocked`.
    """

    call: Call
    outcome: Outcome
    confirmed: bool  # the user approved it through `confirm`
    receipt: object = None  # what the function returned; never shown to a model or in a text
    exception: BaseException | None = field(default=None, compare=False)

    @property
    def summary(self) -> str:
        """Code-written, with no exception text: "pay 20.00 USD to 0x...: outcome unknown
        (TimeoutError)"."""
        if self.outcome == "unknown":
            return f"{_describe(self.call)}: outcome unknown ({type(self.exception).__name__})"
        return f"{_describe(self.call)}: {self.outcome}"


@dataclass
class Ledger:
    """What definitely happened: the calls whose function returned normally.

    An unknown-outcome call is never here; `RunResult.unsettled` lists those.
    """

    payments: list[Payment] = field(default_factory=list)
    emails: list[Email] = field(default_factory=list)

    @property
    def total_paid(self) -> float:
        return sum(p.amount_usd for p in self.payments)

    @classmethod
    def of(cls, actions: Sequence[Action]) -> Ledger:
        done = [a.call for a in actions if a.outcome == "done"]
        return cls(
            payments=[c for c in done if isinstance(c, Payment)],
            emails=[c for c in done if isinstance(c, Email)],
        )


class OutcomeUnknown(RuntimeError):
    """Raised by the interpreter after an unknown-outcome call, so nothing later runs."""


def _identity(call: Call) -> tuple[str, ...]:
    """What makes two effects the same one: the kind, the payee or recipient, the cent amount,
    and the memo or subject. Never a plan step position, so a re-planned retry matches."""
    if isinstance(call, Payment):
        return ("pay", call.payee, f"{call.amount_usd:.2f}", call.memo)
    return ("send_email", call.to.lower(), call.subject)


def effect_key(run_id: str, call: Call, occurrence: int) -> str:
    material = "\x1f".join([run_id, *_identity(call), str(occurrence)])
    return "ag-" + hashlib.sha256(material.encode()).hexdigest()[:32]


@dataclass
class Effects:
    """Per run. The single writer of the action record and the only caller of developer functions.

    A missing function is None; the policy denies that action (P0) before `call` is reached.
    """

    pay: PayFn | None
    send_email: SendEmailFn | None
    audit: AuditLog
    run_id: str
    actions: list[Action] = field(default_factory=list)
    _attempts: dict[tuple[str, ...], int] = field(default_factory=dict)

    @property
    def kinds(self) -> frozenset[Kind]:
        given: list[Kind] = []
        if self.pay is not None:
            given.append("pay")
        if self.send_email is not None:
            given.append("send_email")
        return frozenset(given)

    def keyed(self, call: Call) -> Call:
        """The call with its idempotency key. Identical calls already attempted this run count."""
        seen = self._attempts.get(_identity(call), 0)
        return replace(call, key=effect_key(self.run_id, call, seen))

    def call(self, call: Call, *, confirmed: bool) -> Action:
        """Call the matching function exactly once, record the Action, and return it.

        Never retries: a retry after an unknown outcome could pay twice. A BaseException
        (KeyboardInterrupt) is recorded as unknown first and then re-raised, even when on_event
        fails while it is recorded.
        """
        self._attempts[_identity(call)] = self._attempts.get(_identity(call), 0) + 1
        try:
            if isinstance(call, Payment):
                assert self.pay is not None, "P0 refuses a payment without a pay function"
                ret = self.pay(call)
            else:
                assert self.send_email is not None, "P0 refuses mail without a send_email function"
                ret = self.send_email(call)
        except Declined as exc:
            action = Action(call, "declined", confirmed, exception=exc)
        except Exception as exc:  # noqa: BLE001 - any other failure leaves the outcome unknown
            action = Action(call, "unknown", confirmed, exception=exc)
        except BaseException as exc:
            with contextlib.suppress(AuditSinkFailed):  # never in place of the BaseException
                self._record(Action(call, "unknown", confirmed, exception=exc))
            raise
        else:
            if inspect.isawaitable(ret):
                if inspect.iscoroutine(ret):
                    ret.close()
                action = Action(call, "unknown", confirmed, exception=TypeError("awaitable"))
            else:
                action = Action(call, "done", confirmed, receipt=ret)
        self._record(action)
        return action

    def _record(self, action: Action) -> None:
        self.actions.append(action)
        if action.outcome != "done":
            # Only off the success path: the approved `policy` event before the call is the
            # write-ahead marker, and a successful run's event stream stays as it was.
            self.audit.emit(
                "effect",
                action=kind_of(action.call),
                outcome=action.outcome,
                key=action.call.key,
                error_type=type(action.exception).__name__,
            )
