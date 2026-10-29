"""Structured audit log. Feeds the CLI trace and, later, the UI event stream."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Event:
    kind: str
    data: dict[str, Any]
    ts: float = field(default_factory=time.time)


class AuditLog:
    def __init__(self, sink: Callable[[Event], None] | None = None) -> None:
        self.events: list[Event] = []
        self._sink = sink

    def emit(self, kind: str, **data: Any) -> Event:
        ev = Event(kind, data)
        self.events.append(ev)
        if self._sink:
            self._sink(ev)
        return ev

    def of_kind(self, kind: str) -> list[Event]:
        return [e for e in self.events if e.kind == kind]
