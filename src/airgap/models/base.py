"""One interface for every model call, so providers are swappable."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol


class RealCallsDisabled(RuntimeError):
    """Raised when a real model is requested without AIRGAP_ALLOW_REAL_CALLS=1."""


Role = Literal["agent", "orchestrator", "reader", "attacker"]
Message = dict[str, str]


@dataclass
class Completion:
    text: str
    parsed: Any | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_s: float = 0.0
    usage_known: bool = True  # False when the provider reported no token counts


class ChatModel(Protocol):
    """`role` tells offline fakes which behavior to simulate; real providers ignore it."""

    name: str

    def complete(
        self,
        messages: list[Message],
        *,
        role: Role,
        schema: dict[str, Any] | None = None,
    ) -> Completion: ...


def approx_tokens(text: str) -> int:
    """Rough token estimate (about 4 chars per token) for offline accounting."""
    return max(1, len(text) // 4)
