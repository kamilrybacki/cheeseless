"""Run budget (D7, D14, Q54)."""

import pytest

from cheeseless.domain.budget import DEFAULT_LIMITS, Budget, Pool
from cheeseless.domain.errors import BudgetState


def test_default_pools() -> None:
    assert DEFAULT_LIMITS == {Pool.LOCAL: 30, Pool.CI: 5}


def test_reserve_until_exhausted() -> None:
    budget = Budget(limit=2)
    budget = budget.reserve().reserve()
    assert budget.exhausted
    assert budget.state() == BudgetState(remaining=0, limit=2)
    with pytest.raises(ValueError):
        budget.reserve()


def test_grant_adds_runs_without_resetting_use() -> None:
    budget = Budget(limit=1, used=1).grant(3)
    assert budget == Budget(limit=4, used=1)
    assert budget.remaining == 3
    with pytest.raises(ValueError):
        budget.grant(0)


def test_negative_counts_are_refused() -> None:
    with pytest.raises(ValueError):
        Budget(limit=-1)
    with pytest.raises(ValueError):
        Budget(limit=1, used=-1)
