"""Task and criterion invariants (Q27, Q44, I5)."""

import pytest

from cheeseless.domain.task import Criterion, Task

SHA = "a" * 64


def _task(**overrides: object) -> Task:
    fields: dict[str, object] = {
        "task_id": SHA,
        "lineage_id": "toy-normalize",
        "criteria": (Criterion("normalize", "CLI normalises input"),),
    }
    fields.update(overrides)
    return Task(**fields)  # type: ignore[arg-type]


def test_valid_task() -> None:
    task = _task()
    assert task.criterion("normalize").description == "CLI normalises input"


def test_unknown_criterion_lookup_raises() -> None:
    with pytest.raises(KeyError):
        _task().criterion("missing")


@pytest.mark.parametrize(
    "overrides",
    [
        {"task_id": "abc"},
        {"lineage_id": ""},
        {"lineage_id": "has space"},
        {"criteria": ()},
        {"criteria": (Criterion("a", "x"), Criterion("a", "y"))},
    ],
)
def test_task_rejects_invalid_values(overrides: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        _task(**overrides)


@pytest.mark.parametrize(
    ("cid", "description"),
    [("", "x"), ("UPPER", "x"), ("a" * 65, "x"), ("ok", ""), ("ok", "x" * 501)],
)
def test_criterion_rejects_invalid_values(cid: str, description: str) -> None:
    with pytest.raises(ValueError):
        Criterion(cid, description)
