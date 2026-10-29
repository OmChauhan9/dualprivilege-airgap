"""Labeled values. Anything derived from fetched content carries its untrusted sources."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Tainted:
    """A value plus the untrusted origins it depends on. Empty `sources` means trusted.

    `steered_by` records values that were *chosen* by tainted data (control flow) even though
    the chosen value itself came from a trusted set such as the payee allowlist.
    """

    value: Any
    sources: frozenset[str] = frozenset()
    steered_by: frozenset[str] = frozenset()

    @property
    def is_tainted(self) -> bool:
        return bool(self.sources)

    @property
    def is_steered(self) -> bool:
        return bool(self.steered_by)


def trusted(value: Any) -> Tainted:
    return Tainted(value)


def untrusted(value: Any, domain: str) -> Tainted:
    return Tainted(value, frozenset({f"untrusted:{domain}"}))


def merge_sources(*values: Tainted) -> frozenset[str]:
    out: set[str] = set()
    for v in values:
        out |= v.sources | v.steered_by
    return frozenset(out)


@dataclass(frozen=True)
class Handle:
    """Opaque reference the orchestrator may pass around but never read through."""

    id: str
    kind: str
    origin: str | None = None
