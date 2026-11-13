"""Stratified holdout split (Q27, Q44)."""

import pytest

from cheeseless.domain.holdout import holdout_count, split

SEED = b"s" * 32
TESTS = {f"acceptance/test_a.py::test_{i}": "alpha" for i in range(6)} | {
    f"acceptance/test_b.py::test_{i}": "beta" for i in range(2)
}


@pytest.mark.parametrize(("n", "k"), [(2, 1), (3, 1), (5, 1), (6, 2), (9, 3), (30, 10)])
def test_holdout_count(n: int, k: int) -> None:
    assert holdout_count(n) == k


def test_holdout_count_needs_two_tests() -> None:
    with pytest.raises(ValueError):
        holdout_count(1)


def test_split_is_stratified_and_disjoint() -> None:
    result = split(SEED, TESTS)
    assert set(result.visible) | set(result.holdout) == set(TESTS)
    assert not set(result.visible) & set(result.holdout)
    assert sorted(result.holdout.values()) == ["alpha", "alpha", "beta"]
    assert sorted(set(result.visible.values())) == ["alpha", "beta"]


def test_split_is_deterministic_per_seed_and_varies_across_seeds() -> None:
    assert split(SEED, TESTS) == split(SEED, TESTS)
    many = {f"acceptance/t.py::test_{i}": "alpha" for i in range(30)}
    holdouts = {frozenset(split(bytes([i]) * 32, many).holdout) for i in range(5)}
    assert len(holdouts) > 1


def test_split_refuses_short_seed_and_single_test_criteria() -> None:
    with pytest.raises(ValueError):
        split(b"short", TESTS)
    with pytest.raises(ValueError):
        split(SEED, {"acceptance/t.py::test_x": "alpha"})
