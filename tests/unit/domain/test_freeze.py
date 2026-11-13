"""Frozen task invariants and the task id digest (D14, I5)."""

import pytest

from cheeseless.domain.contract import Contract
from cheeseless.domain.freeze import FrozenTask, HiddenBlob, freeze_digest
from cheeseless.domain.task import Criterion, Task

OID = "1" * 40
CRITERIA = (Criterion("alpha", "does alpha"),)
CONTRACT = Contract("toy", CRITERIA, ("python", "-m", "toy"))
TASK = Task("a" * 64, "toy", CRITERIA)


def _frozen(**changes: object) -> FrozenTask:
    fields: dict[str, object] = {
        "task": TASK,
        "contract": CONTRACT,
        "baseline_commit": OID,
        "projection_tree": OID,
        "acceptance_tree": OID,
        "hidden_blobs": (HiddenBlob("acceptance/test_a.py", OID),),
        "visible": {"acceptance/test_a.py::v": "alpha"},
        "holdout": {"acceptance/test_a.py::h": "alpha"},
    }
    fields.update(changes)
    return FrozenTask(**fields)  # type: ignore[arg-type]


def test_valid_frozen_task() -> None:
    assert _frozen().task is TASK


@pytest.mark.parametrize(
    "changes",
    [
        {"baseline_commit": "nope"},
        {"task": Task("a" * 64, "other", CRITERIA)},
        {"task": Task("a" * 64, "toy", (Criterion("beta", "b"),))},
        {"visible": {}},
        {"holdout": {"acceptance/test_a.py::h": "unknown"}},
        {"holdout": {"acceptance/test_a.py::v": "alpha"}},
    ],
)
def test_invalid_frozen_task(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        _frozen(**changes)


def test_each_criterion_needs_both_sides() -> None:
    criteria = (Criterion("alpha", "a"), Criterion("beta", "b"))
    with pytest.raises(ValueError):
        _frozen(
            task=Task("a" * 64, "toy", criteria),
            contract=Contract("toy", criteria, ("python",)),
            visible={"x::1": "alpha", "x::2": "beta"},
            holdout={"x::3": "alpha"},
        )


@pytest.mark.parametrize("path", ["", "/abs", "acceptance/../x"])
def test_hidden_blob_paths(path: str) -> None:
    with pytest.raises(ValueError):
        HiddenBlob(path, OID)


def test_freeze_digest_binds_every_input() -> None:
    base = freeze_digest(OID, "2" * 40, b"task: toy\n")
    assert len(base) == 64
    assert base == freeze_digest(OID, "2" * 40, b"task: toy\n")
    assert base != freeze_digest("3" * 40, "2" * 40, b"task: toy\n")
    assert base != freeze_digest(OID, "3" * 40, b"task: toy\n")
    assert base != freeze_digest(OID, "2" * 40, b"task: toy2\n")
    with pytest.raises(ValueError):
        freeze_digest("bad", "2" * 40, b"")
