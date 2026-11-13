"""CI judge use case (P10-1..P10-7): grade a PR head against the frozen acceptance suite.

The judge runs on the default branch. The PR head is only ever exported as files; hidden
tests come from the frozen task and the default-branch tree, never from the PR tree.
"""

from __future__ import annotations

import hashlib
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from cheeseless.domain.budget import Pool
from cheeseless.domain.disclosure import RunReport, decide
from cheeseless.domain.evaluation import SuiteRun, TestOutcome, criteria_statuses, smoke_passed
from cheeseless.domain.freeze import FrozenTask
from cheeseless.domain.promote_policy import is_protected
from cheeseless.domain.result import CriterionResult, Result, sign
from cheeseless.domain.status import Status
from cheeseless.ports.repository import Repository
from cheeseless.ports.runner import SuiteRunner
from cheeseless.ports.sandbox import Sandbox
from cheeseless.ports.store import Store


class FreezeDrift(Exception):
    """The default branch's ``acceptance/`` differs from the frozen tree (fail closed)."""


class JudgeReason:
    """Closed set of operator-facing reasons. Never derived from candidate output."""

    PROTECTED_PATH = "protected_path"
    BUILD_FAILED = "build_failed"
    SMOKE_FAILED = "smoke_test_failed"
    BUDGET_EXHAUSTED = "budget_exhausted"
    INFRA_ERROR = "infra_error"
    HIDDEN_FAILED = "criteria_failed"
    OK = "ok"


@dataclass(frozen=True, slots=True)
class CiVerdict:
    """``public()`` is the only serialisable form: status plus visible criteria."""

    status: Status
    tree_hash: str
    reason: str
    visible: tuple[CriterionResult, ...] = ()
    result: Result | None = None

    def public(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "criteria": [{"id": c.criterion_id, "status": c.status.value} for c in self.visible],
        }

    @property
    def passed(self) -> bool:
        return self.status is Status.PASS


def _public_criteria(frozen: FrozenTask, suite: SuiteRun) -> tuple[CriterionResult, ...]:
    """Per-criterion status from the visible tests only (every criterion also has holdout)."""
    # The suite also reports holdout tests, so score the visible ones directly instead of
    # through ``criteria_statuses`` (which treats extra outcomes as a failure).
    outcomes = suite.outcome_map
    ok = not suite.collection_errors and not suite.infra_cause
    statuses: dict[str, Status] = {}
    for node_id, cid in frozen.visible.items():
        passed = ok and outcomes.get(node_id) is TestOutcome.PASSED
        if not passed or statuses.get(cid) is Status.FAIL:
            statuses[cid] = Status.FAIL
        else:
            statuses[cid] = Status.PASS
    return tuple(CriterionResult(cid, st) for cid, st in sorted(statuses.items()))


def _selection(frozen: FrozenTask) -> Mapping[str, str]:
    return {**frozen.visible, **frozen.holdout}


def judge(
    repo: Repository,
    store: Store,
    runner: SuiteRunner,
    sandbox: Sandbox,
    frozen: FrozenTask,
    key: bytes,
    *,
    base_commit: str,
    head_commit: str,
    tool_version: str = "0.1.0",
    timeout: float = 300.0,
) -> CiVerdict:
    # (a) Freeze drift: tests edited on the default branch after the freeze are untrusted.
    if repo.tree_of(base_commit, "acceptance") != frozen.acceptance_tree:
        raise FreezeDrift("default branch acceptance/ differs from the frozen tree")

    head_tree = repo.tree_of(head_commit)

    # (b) Protected paths fail before any run and before any budget charge. What the PR
    # changed is measured from the merge base, not the moving default-branch tip.
    fork_tree = repo.tree_of(repo.merge_base(base_commit, head_commit))
    changed = repo.diff_modes(fork_tree, head_tree)
    if any(is_protected(p) for p in changed):
        return CiVerdict(Status.FAIL, head_tree, JudgeReason.PROTECTED_PATH)

    baseline_digest = hashlib.sha256(frozen.baseline_commit.encode()).hexdigest()
    env = list(frozen.contract.env)
    selected = _selection(frozen)

    # Any failure to set up the trusted scratch or the exports fails closed.
    try:
        with sandbox.trusted_scratch() as hidden_scratch:
            acc_root = hidden_scratch / "acceptance"
            repo.export(frozen.acceptance_tree, acc_root)
            with tempfile.TemporaryDirectory(prefix="cheeseless-ci-candidate-") as tmp:
                cand_root = Path(tmp) / "candidate"
                # (c) Export only; the candidate never sees acceptance/ and nothing is checked out.
                repo.export(head_tree, cand_root, exclude=("acceptance",))
                try:
                    build = sandbox.build(cand_root, frozen.contract.build, env)
                except Exception:  # noqa: BLE001
                    return CiVerdict(Status.BLOCKED, head_tree, JudgeReason.INFRA_ERROR)
                if not build.ok:
                    return CiVerdict(Status.FAIL, head_tree, JudgeReason.BUILD_FAILED)
                try:
                    with sandbox.scratch() as scratch:
                        launcher = sandbox.launcher(cand_root, env, scratch)
                        smoke = runner.run(
                            root=acc_root,
                            paths=["contract"],
                            selected=None,
                            launcher=launcher,
                            run=frozen.contract.run,
                            timeout=timeout,
                        )
                except Exception:  # noqa: BLE001
                    return CiVerdict(Status.BLOCKED, head_tree, JudgeReason.INFRA_ERROR)
                if not smoke_passed(smoke.suite):
                    return CiVerdict(Status.FAIL, head_tree, JudgeReason.SMOKE_FAILED)

                # (d) Budget from the CI pool, charged only once a hidden run is about to happen.
                if store.reserve_run(frozen.task.lineage_id, Pool.CI) is None:
                    return CiVerdict(Status.BLOCKED, head_tree, JudgeReason.BUDGET_EXHAUSTED)

                # (e) The full suite, visible plus holdout, from the frozen acceptance tree.
                try:
                    with sandbox.scratch() as scratch:
                        launcher = sandbox.launcher(cand_root, env, scratch)
                        out = runner.run(
                            root=acc_root,
                            paths=["hidden"],
                            selected=selected,
                            launcher=launcher,
                            run=frozen.contract.run,
                            timeout=timeout,
                        )
                except Exception:  # noqa: BLE001
                    return CiVerdict(Status.BLOCKED, head_tree, JudgeReason.INFRA_ERROR)

    except Exception:  # noqa: BLE001
        return CiVerdict(Status.BLOCKED, head_tree, JudgeReason.INFRA_ERROR)

    statuses, notes = criteria_statuses(selected, out.suite)
    infra = "; ".join(p for p in (out.suite.infra_cause, notes) if p)
    verdict = decide(
        frozen.task,
        RunReport(hidden_input_read=True, criteria=statuses, infra_cause=infra),
        tree_hash=head_tree,
        baseline_digest=baseline_digest,
        profile="ci",
        tool_version=tool_version,
    )
    if not isinstance(verdict.outcome, Result):
        return CiVerdict(Status.BLOCKED, head_tree, JudgeReason.INFRA_ERROR)
    # (f) Signed and bound to the head tree hash.
    signed = sign(verdict.outcome, key)
    reason = JudgeReason.OK if signed.status is Status.PASS else JudgeReason.HIDDEN_FAILED
    return CiVerdict(signed.status, head_tree, reason, _public_criteria(frozen, out.suite), signed)


__all__ = ["CiVerdict", "FreezeDrift", "JudgeReason", "judge"]
