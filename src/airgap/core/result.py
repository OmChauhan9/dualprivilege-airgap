"""Shared result type for the baseline and the airgapped pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field

from airgap.core.audit import AuditLog
from airgap.world.ledger import Ledger


@dataclass
class RunResult:
    final_text: str
    ledger: Ledger
    audit: AuditLog
    blocked: list[str] = field(default_factory=list)
    confirmations: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_s: float = 0.0
    error: str | None = None
