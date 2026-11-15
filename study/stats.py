"""Stdlib-only statistics for the study (see docs/study/protocol.md section 6)."""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass

Z95 = 1.959963984540054


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float] | None:
    """Wilson score interval; ``None`` when there is no denominator."""
    if n <= 0:
        return None
    if not 0 <= k <= n:
        raise ValueError("k must be within 0..n")
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def rule_of_three(n: int) -> float | None:
    """Approximate 95% upper bound on the rate when 0 events were seen in n trials."""
    return None if n <= 0 else min(1.0, 3.0 / n)


@dataclass(frozen=True)
class Rate:
    k: int
    n: int

    @property
    def point(self) -> float | None:
        return None if self.n == 0 else self.k / self.n

    @property
    def ci(self) -> tuple[float, float] | None:
        return wilson(self.k, self.n)

    @property
    def zero_event_bound(self) -> float | None:
        return rule_of_three(self.n) if self.k == 0 else None


@dataclass(frozen=True)
class Cell:
    """Counts for one cluster (task x agent): arm A (naive) versus arm B (cheeseless)."""

    key: str
    a: Rate
    b: Rate


def paired_differences(cells: Sequence[Cell]) -> dict[str, float | None]:
    """Per-cell B - A rate difference (``None`` where either arm has no runs)."""
    out: dict[str, float | None] = {}
    for c in cells:
        pa, pb = c.a.point, c.b.point
        out[c.key] = None if pa is None or pb is None else pb - pa
    return out


def pooled_difference(cells: Sequence[Cell]) -> float | None:
    ka = sum(c.a.k for c in cells)
    na = sum(c.a.n for c in cells)
    kb = sum(c.b.k for c in cells)
    nb = sum(c.b.n for c in cells)
    if na == 0 or nb == 0:
        return None
    return kb / nb - ka / na


def cluster_bootstrap(
    cells: Sequence[Cell], seed: int, reps: int = 2000
) -> tuple[float, float, int] | None:
    """Seeded percentile bootstrap of the pooled B - A difference.

    The resampling unit is the *task*: each draw takes whole tasks (with replacement) and keeps
    every agent cell of a drawn task together. Cell keys are ``task/agent``. Returns
    ``(low, high, n_clusters)``; the cluster count is the number of distinct tasks and must be
    reported next to the interval (few clusters make it descriptive, not inferential).
    """
    by_task: dict[str, list[Cell]] = {}
    for c in cells:
        if c.a.n and c.b.n:
            by_task.setdefault(c.key.split("/", 1)[0], []).append(c)
    tasks = sorted(by_task)
    if len(tasks) < 2:
        return None
    rng = random.Random(seed)  # noqa: S311 - seeded, non-crypto
    draws = []
    for _ in range(reps):
        sample = [c for t in (tasks[rng.randrange(len(tasks))] for _ in tasks) for c in by_task[t]]
        d = pooled_difference(sample)
        if d is not None:
            draws.append(d)
    draws.sort()
    lo = draws[int(0.025 * (len(draws) - 1))]
    hi = draws[int(math.ceil(0.975 * (len(draws) - 1)))]
    return lo, hi, len(tasks)


def shuffled_order(items: Sequence[str], seed: int) -> list[str]:
    """Deterministic, seed-recorded randomisation of run order."""
    out = list(items)
    random.Random(seed).shuffle(out)  # noqa: S311 - seeded, non-crypto
    return out
