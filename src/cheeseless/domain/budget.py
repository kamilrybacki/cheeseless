"""Run budget (D7, D14, Q54, I4): separate local and CI pools, keyed by lineage."""

import enum
from dataclasses import dataclass

from cheeseless.domain.errors import BudgetState


class Pool(enum.Enum):
    LOCAL = "local"
    CI = "ci"


DEFAULT_LIMITS = {Pool.LOCAL: 30, Pool.CI: 5}


@dataclass(frozen=True, slots=True)
class Budget:
    limit: int
    used: int = 0

    def __post_init__(self) -> None:
        if self.limit < 0 or self.used < 0:
            raise ValueError("budget counts must be non-negative")

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    @property
    def exhausted(self) -> bool:
        return self.remaining == 0

    def reserve(self) -> "Budget":
        if self.exhausted:
            raise ValueError("run budget exhausted")
        return Budget(self.limit, self.used + 1)

    def grant(self, runs: int) -> "Budget":
        if runs <= 0:
            raise ValueError("a grant must add at least one run")
        return Budget(self.limit + runs, self.used)

    def state(self) -> BudgetState:
        return BudgetState(remaining=self.remaining, limit=self.limit)
