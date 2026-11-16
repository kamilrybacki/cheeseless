"""Freeze use cases: prepare (validate) and commit (store) a frozen task."""

from __future__ import annotations

import secrets
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yaml

from cheeseless.domain.contract import (
    Contract,
    canonical_contract_bytes,
    parse_contract,
)
from cheeseless.domain.dlp import (
    Fingerprints,
    build_fingerprints,
    redact_sentinels,
    sentinel_problems,
)
from cheeseless.domain.evaluation import SuiteRun, criterion_by_test, freeze_problems
from cheeseless.domain.freeze import FrozenTask, HiddenBlob, freeze_digest
from cheeseless.domain.holdout import split as holdout_split
from cheeseless.domain.task import Task
from cheeseless.ports.repository import Repository
from cheeseless.ports.runner import SuiteRunner
from cheeseless.ports.sandbox import Sandbox
from cheeseless.ports.store import Store

CONTRACT_PATH = "acceptance/contract/criteria.yaml"
SPEC_PATH = "acceptance/contract/spec.md"
HOLDOUT_SEED_BYTES = 32


@dataclass(frozen=True, slots=True)
class PreparedFreeze:
    baseline_commit: str
    contract: Contract
    acceptance_tree: str
    projection_tree: str
    hidden_blobs: tuple[HiddenBlob, ...]
    tests: dict[str, str]
    task_id: str
    fingerprints: Fingerprints
    tool_version: str = "0.1.0"


@dataclass(frozen=True, slots=True)
class FreezeCommitResult:
    task_id: str
    seed: bytes


class FreezeError(Exception):
    """Raised when the freeze preconditions are not met."""

    def __init__(self, problems: list[str]) -> None:
        redacted = tuple(redact_sentinels(problem) for problem in problems)
        super().__init__("; ".join(redacted))
        self.problems = redacted


def _read_contract(repo: Repository, commit: str) -> Contract:
    raw = repo.show(commit, CONTRACT_PATH)
    data = yaml.safe_load(raw.decode("utf-8"))
    paths = {b.path for b in repo.blobs_under(repo.tree_of(commit), "acceptance/contract")}
    spec = repo.show(commit, SPEC_PATH).decode("utf-8") if SPEC_PATH in paths else None
    return parse_contract(data, spec=spec)


def _observations(run: SuiteRun) -> tuple[object, ...]:
    return (
        run.collected,
        tuple(sorted((run.outcomes or {}).items())),
        run.collection_errors,
        run.infra_cause,
    )


def _hidden_contents(
    repo: Repository, commit: str, blobs: tuple[HiddenBlob, ...]
) -> list[tuple[str, bytes]]:
    return [(blob.path, repo.show(commit, blob.path)) for blob in blobs]


def _public_contents(
    repo: Repository, commit: str, projection_tree: str
) -> list[tuple[bytes, bytes]]:
    """Agent-visible projection blobs as ``(path, content)`` pairs.

    A file name is as visible as its content, so callers scan the path bytes too.
    """
    return [
        (blob.path.encode(), repo.show(commit, blob.path))
        for blob in repo.blobs_under(projection_tree, "")
    ]


def prepare(
    repo: Repository,
    runner: SuiteRunner,
    sandbox: Sandbox,
    *,
    baseline_commit: str | None = None,
    timeout: float = 300.0,
    tool_version: str = "0.1.0",
) -> PreparedFreeze:
    if baseline_commit is None:
        baseline_commit = repo.head_commit()
    contract = _read_contract(repo, baseline_commit)
    baseline_tree = repo.tree_of(baseline_commit)
    acceptance_tree = repo.tree_of(baseline_commit, "acceptance")
    public_paths = (CONTRACT_PATH, SPEC_PATH) if contract.spec is not None else (CONTRACT_PATH,)
    projection_tree = repo.tree_without(baseline_tree, "acceptance", public_paths)
    hidden_blobs = tuple(
        HiddenBlob(path=f"acceptance/{b.path}", oid=b.oid)
        for b in repo.blobs_under(acceptance_tree, "")
        if not b.path.startswith("contract/")
    )

    hidden_contents = _hidden_contents(repo, baseline_commit, hidden_blobs)
    projection_blobs = _public_contents(repo, baseline_commit, projection_tree)
    public_contents = [content for _, content in projection_blobs]
    public_surfaces = [content for _, content in projection_blobs] + [
        path for path, _ in projection_blobs
    ]
    problems = sentinel_problems(hidden_contents, public_surfaces)
    if problems:
        raise FreezeError(problems)

    env = list(contract.env)
    with sandbox.trusted_scratch() as hidden_scratch:
        acc_root = hidden_scratch / "acceptance"
        repo.export(acceptance_tree, acc_root)
        with tempfile.TemporaryDirectory(prefix="cheeseless-freeze-candidate-") as cand_tmp:
            cand_root = Path(cand_tmp) / "candidate"
            repo.export(projection_tree, cand_root)

            with sandbox.scratch() as scratch:
                build = sandbox.build(cand_root, contract.build, env)
                if not build.ok:
                    raise FreezeError([f"build failed: {build.stderr_tail}".strip()])
                launcher = sandbox.launcher(cand_root, env, scratch)
                first = runner.run(
                    root=acc_root,
                    paths=["hidden"],
                    selected=None,
                    launcher=launcher,
                    run=contract.run,
                    timeout=timeout,
                )
                second = runner.run(
                    root=acc_root,
                    paths=["hidden"],
                    selected=None,
                    launcher=launcher,
                    run=contract.run,
                    timeout=timeout,
                )
            suite = first.suite
            problems = freeze_problems([c.id for c in contract.criteria], suite)
            if problems:
                raise FreezeError(problems)
            if _observations(first.suite) != _observations(second.suite):
                raise FreezeError(["the hidden suite is flaky: two runs disagreed"])
            tests = criterion_by_test(suite)

    if not tests:
        raise FreezeError(["the hidden suite collected no usable tests"])
    task_id = freeze_digest(baseline_commit, acceptance_tree, canonical_contract_bytes(contract))
    fingerprints = build_fingerprints(task_id, hidden_contents, public_contents)
    return PreparedFreeze(
        baseline_commit=baseline_commit,
        contract=contract,
        acceptance_tree=acceptance_tree,
        projection_tree=projection_tree,
        hidden_blobs=hidden_blobs,
        tests=dict(tests),
        task_id=task_id,
        fingerprints=fingerprints,
        tool_version=tool_version,
    )


def commit(
    prepared: PreparedFreeze,
    store: Store,
    *,
    seed: bytes | None = None,
) -> FreezeCommitResult:
    if seed is None:
        seed = secrets.token_bytes(HOLDOUT_SEED_BYTES)
    if len(seed) != HOLDOUT_SEED_BYTES:
        raise ValueError("holdout seed must be exactly 32 bytes")
    split_result = holdout_split(seed, prepared.tests)
    task_id = prepared.task_id
    expected = freeze_digest(
        prepared.baseline_commit,
        prepared.acceptance_tree,
        canonical_contract_bytes(prepared.contract),
    )
    if expected != task_id or prepared.fingerprints.task_id != task_id:
        raise ValueError("prepared freeze is inconsistent")
    task = Task(task_id, prepared.contract.lineage_id, prepared.contract.criteria)
    frozen = FrozenTask(
        task=task,
        contract=prepared.contract,
        baseline_commit=prepared.baseline_commit,
        projection_tree=prepared.projection_tree,
        acceptance_tree=prepared.acceptance_tree,
        hidden_blobs=prepared.hidden_blobs,
        visible=dict(split_result.visible),
        holdout=dict(split_result.holdout),
    )
    store.save_task(frozen, seed)
    store.save_fingerprints(prepared.fingerprints)
    store.set_active(task_id)
    store.log_event(task_id, "freeze", "committed")
    return FreezeCommitResult(task_id=task_id, seed=seed)


__all__ = [
    "PreparedFreeze",
    "FreezeCommitResult",
    "FreezeError",
    "prepare",
    "commit",
]
