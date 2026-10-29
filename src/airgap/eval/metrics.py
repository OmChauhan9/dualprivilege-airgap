"""Statistics. Rates are always reported as k/n with a Wilson interval, never a bare percent."""

from __future__ import annotations

import math
from dataclasses import dataclass


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for k successes in n trials."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


@dataclass(frozen=True)
class Rate:
    k: int
    n: int

    @property
    def point(self) -> float:
        return self.k / self.n if self.n else 0.0

    def interval(self) -> tuple[float, float]:
        return wilson(self.k, self.n)

    def __str__(self) -> str:
        lo, hi = self.interval()
        return f"{self.k}/{self.n} ({self.point:.0%}, 95% CI {lo:.0%}-{hi:.0%})"
