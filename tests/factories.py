"""Shared builders for domain values used across test layers."""

from cheeseless.domain.contract import Contract, canonical_contract_bytes
from cheeseless.domain.freeze import FrozenTask, HiddenBlob, freeze_digest
from cheeseless.domain.task import Criterion, Task

BASELINE = "1" * 40
PROJECTION = "2" * 40
ACCEPTANCE = "3" * 40
CRITERIA = (Criterion("normalize", "CLI normalises input"), Criterion("score", "score() ranks"))


def contract(lineage: str = "toy") -> Contract:
    return Contract(
        lineage_id=lineage,
        criteria=CRITERIA,
        run=("python3", "-m", "toy"),
        env=(("PYTHONPATH", "src"),),
    )


def frozen_task(lineage: str = "toy", acceptance: str = ACCEPTANCE) -> FrozenTask:
    c = contract(lineage)
    task_id = freeze_digest(BASELINE, acceptance, canonical_contract_bytes(c))
    return FrozenTask(
        task=Task(task_id, lineage, CRITERIA),
        contract=c,
        baseline_commit=BASELINE,
        projection_tree=PROJECTION,
        acceptance_tree=acceptance,
        hidden_blobs=(HiddenBlob("acceptance/hidden/test_a.py", "4" * 40),),
        visible={"t::a": "normalize", "t::c": "score"},
        holdout={"t::b": "normalize", "t::d": "score"},
    )
