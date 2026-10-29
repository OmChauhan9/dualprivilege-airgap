"""Hard spending cap around any ChatModel.

Prices are the published per-million-token rates for Standard mode. A model with no entry here
is refused rather than guessed, which also keeps `-contributor` models out. The running total is
persisted so the cap spans runs, and anything unreadable or out of range fails closed.

Several guards (threads, server requests, processes) may share one ledger. Every call RESERVES its
worst case under an inter-process file lock before it is made, then SETTLES to the actual cost
afterwards, re-reading the ledger each time so no guard can overwrite what another recorded. A
call that dies between the two leaves its reservation in place: it may have been billed.
"""

from __future__ import annotations

import fcntl
import json
import math
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
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
# Deliberate friction: no cap above this is accepted, so a typo such as 5000 cannot open the
# guard. Raising it takes a code change, not an environment variable.
HARD_MAX_USD = 50.0
# Worst-case sizing for the pre-call check. Byte-level tokenizers emit at most one token per
# UTF-8 byte; each message adds a few tokens of chat framing. MetaModel sends MAX_OUTPUT_TOKENS
# as max_tokens, so output can never be longer.
FRAMING_TOKENS_PER_MESSAGE = 16
FRAMING_TOKENS_PER_CALL = 64
MAX_OUTPUT_TOKENS = 2048


def default_ledger() -> Path:
    """Where the running total lives: outside any checkout, so deleting one cannot reset it."""
    override = os.environ.get("AIRGAP_SPEND_LEDGER")
    return Path(override) if override else Path.home() / ".airgap" / "spend.json"


class BudgetExceeded(RuntimeError):
    """The next call could push total spend past the cap, or the cap or ledger is invalid."""


class UnknownPrice(ValueError):
    """No published price for this model, so its cost cannot be bounded."""


def cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    if model not in PRICES:
        raise UnknownPrice(f"no known price for model {model!r}")
    price_in, price_out = PRICES[model]
    return (prompt_tokens * price_in + completion_tokens * price_out) / 1_000_000


def _finite_number(value: object) -> float | None:
    """The value as a float if it is a real, finite number (not a bool or a string), else None."""
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return None
    return float(value)


def check_cap(cap: object, source: str = "cap_usd") -> float:
    """A cap must be a finite number in (0, HARD_MAX_USD]; anything else refuses all calls."""
    value = _finite_number(cap)
    if value is None or not 0 < value <= HARD_MAX_USD:
        raise BudgetExceeded(
            f"refusing calls: {source}={cap!r} is not a number of dollars in (0, {HARD_MAX_USD:g}]"
        )
    return value


def cap_from_env() -> float:
    raw = os.environ.get("AIRGAP_BUDGET_USD")
    if raw is None:
        return DEFAULT_CAP_USD
    try:
        value = float(raw)
    except ValueError:
        value = raw
    return check_cap(value, source="AIRGAP_BUDGET_USD")


@dataclass(frozen=True)
class LedgerState:
    spent_usd: float  # settled: actual cost of finished calls
    reserved_usd: float = 0.0  # worst case of calls in flight (or that died mid-call)

    @property
    def committed_usd(self) -> float:
        return self.spent_usd + self.reserved_usd


def read_ledger(path: Path) -> LedgerState:
    """Read and validate a ledger; a missing file is zero, anything malformed fails closed."""
    if not path.exists():
        return LedgerState(0.0)
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise BudgetExceeded(f"spend ledger {path} is unreadable: {exc}") from exc
    if not isinstance(data, dict) or "spent_usd" not in data:
        raise BudgetExceeded(f"spend ledger {path} is unreadable: no spent_usd")
    values = {key: data.get(key, 0.0) for key in ("spent_usd", "reserved_usd")}
    for key, value in values.items():
        number = _finite_number(value)
        if number is None or number < 0:
            raise BudgetExceeded(f"spend ledger {path} is invalid: {key}={value!r}")
    return LedgerState(float(values["spent_usd"]), float(values["reserved_usd"]))


class BudgetedModel:
    def __init__(
        self,
        inner: ChatModel,
        cap_usd: float = DEFAULT_CAP_USD,
        ledger: Path | None = None,
    ) -> None:
        if inner.name not in PRICES:
            raise UnknownPrice(f"no known price for model {inner.name!r}")
        self.inner = inner
        self.name = inner.name
        self.cap_usd = check_cap(cap_usd)
        self.ledger = ledger if ledger is not None else default_ledger()
        self.calls = 0
        with self._locked():
            self.spent_usd = read_ledger(self.ledger).spent_usd

    @contextmanager
    def _locked(self) -> Iterator[None]:
        """Exclusive across threads and processes: flock on a sidecar file next to the ledger."""
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        lock = self.ledger.with_name(self.ledger.name + ".lock")
        with open(lock, "a") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    def _write(self, state: LedgerState) -> None:
        """Atomic replace, so a reader without the lock never sees half a file."""
        tmp = self.ledger.with_name(self.ledger.name + ".tmp")
        tmp.write_text(
            json.dumps(
                {
                    "spent_usd": state.spent_usd,
                    "reserved_usd": state.reserved_usd,
                    "model": self.name,
                }
            )
        )
        os.replace(tmp, self.ledger)
        self.spent_usd = state.spent_usd

    def _settle(self, reserved: float, actual: float) -> None:
        with self._locked():
            state = read_ledger(self.ledger)
            left = state.reserved_usd - reserved
            self._write(LedgerState(state.spent_usd + actual, left if left > 1e-12 else 0.0))

    def worst_case_usd(
        self, messages: list[Message], schema: dict[str, Any] | None = None
    ) -> float:
        sent = sum(len(m.get("role", "").encode()) + len(m["content"].encode()) for m in messages)
        if schema is not None:
            sent += len(json.dumps(schema).encode())  # sent as response_format, billed as input
        framing = FRAMING_TOKENS_PER_CALL + FRAMING_TOKENS_PER_MESSAGE * len(messages)
        return cost_usd(self.name, sent + framing, MAX_OUTPUT_TOKENS)

    def _actual_usd(self, comp: Completion, worst: float) -> float:
        tokens = (comp.prompt_tokens, comp.completion_tokens)
        if not comp.usage_known or not all(isinstance(t, int) and t >= 0 for t in tokens):
            return worst  # nothing trustworthy reported: assume the most it could have cost
        return cost_usd(self.name, *tokens)

    def complete(
        self,
        messages: list[Message],
        *,
        role: Role,
        schema: dict[str, Any] | None = None,
    ) -> Completion:
        worst = self.worst_case_usd(messages, schema)
        with self._locked():
            state = read_ledger(self.ledger)
            if state.committed_usd + worst > self.cap_usd:
                raise BudgetExceeded(
                    f"refusing call: spent ${state.spent_usd:.4f} + in flight "
                    f"${state.reserved_usd:.4f} + worst case ${worst:.4f} would pass the "
                    f"${self.cap_usd:.2f} cap (raise AIRGAP_BUDGET_USD deliberately to continue)"
                )
            self._write(LedgerState(state.spent_usd, state.reserved_usd + worst))
        try:
            comp = self.inner.complete(messages, role=role, schema=schema)
        except Exception:
            self._settle(worst, 0.0)
            raise
        self.calls += 1
        self._settle(worst, self._actual_usd(comp, worst))
        return comp
