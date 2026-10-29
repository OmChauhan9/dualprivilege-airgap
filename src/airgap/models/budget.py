"""Hard spending cap around any ChatModel.

Prices are the published per-million-token rates for Standard mode. A model with no entry here
is refused rather than guessed, which also keeps `-contributor` models out. The running total is
persisted so the cap spans runs, and an unreadable ledger fails closed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from airgap.models.base import ChatModel, Completion, Message, Role

# (input USD per 1M tokens, output USD per 1M tokens), Standard mode.
PRICES: dict[str, tuple[float, float]] = {
    "muse-spark-1.3": (1.25, 4.25),
    "muse-spark-1.2": (1.25, 4.25),
    "muse-spark-1.1": (1.25, 4.25),
}
DEFAULT_CAP_USD = 5.0
DEFAULT_LEDGER = Path(".spend.json")
# Worst-case sizing for the pre-call check: ~3 characters per token, and the output limit
# that MetaModel sends as max_tokens.
CHARS_PER_TOKEN = 3
MAX_OUTPUT_TOKENS = 2048


class BudgetExceeded(RuntimeError):
    """The next call could push total spend past the cap, or the ledger is unreadable."""


class UnknownPrice(ValueError):
    """No published price for this model, so its cost cannot be bounded."""


def cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    if model not in PRICES:
        raise UnknownPrice(f"no known price for model {model!r}")
    price_in, price_out = PRICES[model]
    return (prompt_tokens * price_in + completion_tokens * price_out) / 1_000_000


class BudgetedModel:
    def __init__(
        self,
        inner: ChatModel,
        cap_usd: float = DEFAULT_CAP_USD,
        ledger: Path = DEFAULT_LEDGER,
    ) -> None:
        if inner.name not in PRICES:
            raise UnknownPrice(f"no known price for model {inner.name!r}")
        self.inner = inner
        self.name = inner.name
        self.cap_usd = cap_usd
        self.ledger = ledger
        self.calls = 0
        self.spent_usd = self._load()

    def _load(self) -> float:
        if not self.ledger.exists():
            return 0.0
        try:
            return float(json.loads(self.ledger.read_text())["spent_usd"])
        except (ValueError, KeyError, TypeError) as exc:
            raise BudgetExceeded(f"spend ledger {self.ledger} is unreadable: {exc}") from exc

    def _save(self) -> None:
        self.ledger.write_text(json.dumps({"spent_usd": self.spent_usd, "model": self.name}))

    def worst_case_usd(self, messages: list[Message]) -> float:
        chars = sum(len(m["content"]) for m in messages)
        return cost_usd(self.name, chars // CHARS_PER_TOKEN + 1, MAX_OUTPUT_TOKENS)

    def complete(
        self,
        messages: list[Message],
        *,
        role: Role,
        schema: dict[str, Any] | None = None,
    ) -> Completion:
        worst = self.worst_case_usd(messages)
        if self.spent_usd + worst > self.cap_usd:
            raise BudgetExceeded(
                f"refusing call: spent ${self.spent_usd:.4f} + worst case ${worst:.4f} would pass "
                f"the ${self.cap_usd:.2f} cap (raise AIRGAP_BUDGET_USD deliberately to continue)"
            )
        comp = self.inner.complete(messages, role=role, schema=schema)
        self.calls += 1
        self.spent_usd += cost_usd(self.name, comp.prompt_tokens, comp.completion_tokens)
        self._save()
        return comp


def cap_from_env() -> float:
    return float(os.environ.get("AIRGAP_BUDGET_USD", DEFAULT_CAP_USD))
