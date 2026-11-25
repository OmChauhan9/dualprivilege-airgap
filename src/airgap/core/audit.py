"""Structured audit log. Feeds the CLI trace, the UI event stream and the developer's on_event."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

# The one list of event fields that can carry page-derived text. `Event.untrusted` is derived from
# it; tests/test_public_surface.py fails when the guard emits a kind missing here.
UNTRUSTED_FIELDS: dict[str, frozenset[str]] = {
    "plan": frozenset(),
    "fetch": frozenset({"final_url", "redirects"}),
    "sanitize": frozenset({"removed"}),
    "reader_output": frozenset({"value"}),
    "tainted_control_flow": frozenset({"compared"}),
    "policy": frozenset(),  # a tainted payee, recipient or URL is written as "<from a page>"
    "effect": frozenset(),
    "answer": frozenset({"text"}),  # always listed; `untrusted_derived` says if it really is
    "error": frozenset({"error"}),  # a reader or plan error may quote a value
}


class AuditSinkFailed(RuntimeError):
    """The developer's on_event raised. The run stops where it happened; the cause is chained."""


@dataclass
class Event:
    kind: str
    data: dict[str, Any]
    ts: float = field(default_factory=time.time)

    @property
    def untrusted(self) -> frozenset[str]:
        """Keys of `data` that may hold page text. Never give them to a model with tools and
        never render them as HTML."""
        return UNTRUSTED_FIELDS.get(self.kind, frozenset(self.data))


class AuditLog:
    def __init__(self, sink: Callable[[Event], None] | None = None) -> None:
        self.events: list[Event] = []
        self._sink = sink

    def emit(self, kind: str, /, **data: Any) -> Event:
        """Record the event, then hand it to the sink. The first sink failure raises
        AuditSinkFailed; the sink is not called again, and later events are still recorded."""
        ev = Event(kind, data)
        self.events.append(ev)
        if self._sink:
            try:
                self._sink(ev)
            except Exception as exc:
                self._sink = None
                raise AuditSinkFailed(f"on_event raised {type(exc).__name__}") from exc
        return ev

    def of_kind(self, kind: str) -> list[Event]:
        return [e for e in self.events if e.kind == kind]
