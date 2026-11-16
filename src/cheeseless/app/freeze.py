"""Freeze use cases: prepare (validate) and commit (store) a frozen task."""

from __future__ import annotations

import secrets
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yaml

from cheeseless.app.author_driver import run_driver_on
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
from cheeseless.ports.sandbox import AuthorSandbox, Sandbox
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


def read_contract(repo: Repository, commit: str) -> Contract:
    raw = repo.show(commit, CONTRACT_PATH)
    data = yaml.safe_load(raw.decode("utf-8"))
    paths = {b.path for b in repo.blobs_under(repo.tree_of(commit), "acceptance/contract")}
    spec = None
    if SPEC_PATH in paths:
        try:
            spec = repo.show(commit, SPEC_PATH).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise FreezeError([f"{SPEC_PATH} must be UTF-8 text"]) from exc
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


def _projection_contents(
    repo: Repository, baseline: str, source: str, projection_tree: str
) -> list[tuple[bytes, bytes]]:
    """Projection blobs; baseline paths are read from the baseline, the contract from source."""
    out = []
    for blob in repo.blobs_under(projection_tree, ""):
        origin = source if blob.path.startswith("acceptance/contract/") else baseline
        out.append((blob.path.encode(), repo.show(origin, blob.path)))
    return out


def _contained_run(
    author_sandbox: AuthorSandbox, acc_root: Path, cand_root: Path, contract: Contract
) -> SuiteRun:
    suite = run_driver_on(
        author_sandbox, acc_root, "run", cand_root, contract.run, tuple(contract.env)
    )
    if suite is None:
        raise FreezeError(["the contained author runner returned no usable result"])
    return suite


def _host_run(
    runner: SuiteRunner,
    acc_root: Path,
    launcher: tuple[str, ...],
    contract: Contract,
    timeout: float,
) -> SuiteRun:
    return runner.run(
        root=acc_root,
        paths=["hidden"],
        selected=None,
        launcher=launcher,
        run=contract.run,
        timeout=timeout,
    ).suite


def prepare(
    repo: Repository,
    runner: SuiteRunner | None,
    sandbox: Sandbox,
    *,
    author_sandbox: AuthorSandbox | None = None,
    baseline_commit: str | None = None,
    source: str | None = None,
    timeout: float = 300.0,
    tool_version: str = "0.1.0",
) -> PreparedFreeze:
    """Validate a freeze. ``source`` (a commit or tree oid) is where the files are read
    from; it defaults to the baseline commit. The digest always uses ``baseline_commit``.

    With ``author_sandbox`` the suite is executed by the contained author runner (read-only
    acceptance, private /tmp, no network, no store or home) instead of ``runner``. Approval
    of an author-proposed tree must use it: that code is not trusted yet."""
    if baseline_commit is None:
        baseline_commit = repo.head_commit()
    if source is None:
        source = baseline_commit
    contract = read_contract(repo, source)
    acceptance_tree = repo.tree_of(source, "acceptance")
    # The candidate is the baseline code, never ``source``: the digest names the baseline.
    # Only the public contract (spec.md and public tests) is taken from ``source``.
    projection_tree = repo.replace_subtree(
        repo.tree_of(baseline_commit), "acceptance", repo.tree_of(source), "acceptance/contract"
    )
    hidden_blobs = tuple(
        HiddenBlob(path=f"acceptance/{b.path}", oid=b.oid)
        for b in repo.blobs_under(acceptance_tree, "")
        if not b.path.startswith("contract/")
    )

    hidden_contents = _hidden_contents(repo, source, hidden_blobs)
    projection_blobs = _projection_contents(repo, baseline_commit, source, projection_tree)
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
                if author_sandbox is not None:
                    first = _contained_run(author_sandbox, acc_root, cand_root, contract)
                    second = _contained_run(author_sandbox, acc_root, cand_root, contract)
                else:
                    if runner is None:
                        raise ValueError("prepare needs a runner or an author sandbox")
                    launcher = sandbox.launcher(cand_root, env, scratch)
                    first = _host_run(runner, acc_root, launcher, contract, timeout)
                    second = _host_run(runner, acc_root, launcher, contract, timeout)
            suite = first
            problems = freeze_problems([c.id for c in contract.criteria], suite)
            if problems:
                raise FreezeError(problems)
            if _observations(first) != _observations(second):
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
    waiver: str = "",
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
        waiver=waiver,
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
    "read_contract",
]
