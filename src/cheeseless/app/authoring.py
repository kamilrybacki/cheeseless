"""Author-side use cases (ADR 0009, stages d and f).

``author_check`` and ``author_crosscheck`` run the author's unapproved acceptance tree in the
author-runner sandbox (no trusted store, no repo write, reference read-only). Their output is
advisory: approval re-runs the baseline itself and only trusts host-side records.
"""

from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from cheeseless.app.freeze import FreezeError, PreparedFreeze, commit, prepare, read_contract
from cheeseless.domain import ids
from cheeseless.domain.authoring import (
    AuthoringError,
    AuthoringState,
    CrosscheckRecord,
    Proposal,
    TaskPhase,
    crosscheck_binds,
    spec_digest,
)
from cheeseless.domain.contract import Contract, canonical_contract_bytes
from cheeseless.domain.dlp import redact_sentinels, sentinel_problems
from cheeseless.domain.evaluation import CollectedTest, SuiteRun, TestOutcome, freeze_problems
from cheeseless.domain.freeze import freeze_digest
from cheeseless.ports.authoring import AuthoringStore
from cheeseless.ports.repository import Repository
from cheeseless.ports.runner import SuiteRunner
from cheeseless.ports.sandbox import AuthorOutcome, AuthorSandbox, Sandbox
from cheeseless.ports.store import Store

RESULT_MARK = "CHEESELESS-AUTHOR "
CHECK_TIMEOUT = 300.0

# Runs inside the author-runner sandbox as ``python -I -B -c DRIVER collect|run <argv-json>``.
# It collects and (in run mode) executes the hidden suite against the mounted reference and
# prints one result line. It never imports the host package.
DRIVER = r"""
import json, os, subprocess, sys, types
import pytest

MARK = "CHEESELESS-AUTHOR "
mode = sys.argv[1]
argv = json.loads(sys.argv[2]) if len(sys.argv) > 2 else []


class Rec:
    def __init__(self):
        self.collected, self.outcomes, self.errors = [], {}, []

    def pytest_configure(self, config):
        config.addinivalue_line("markers", "criterion(id): the acceptance criterion tested")

    def pytest_collection_modifyitems(self, items):
        for item in items:
            crit = []
            for m in item.iter_markers("criterion"):
                ok = len(m.args) == 1 and not m.kwargs and isinstance(m.args[0], str)
                crit.append(m.args[0] if ok else "<invalid criterion marker>")
            self.collected.append({"node_id": item.nodeid, "criteria": crit})

    def pytest_collectreport(self, report):
        if report.failed:
            self.errors.append(report.nodeid or "<collection>")

    def pytest_runtest_logreport(self, report):
        nid, x = report.nodeid, hasattr(report, "wasxfail")
        if report.when == "teardown":
            if report.failed:
                self.outcomes[nid] = "error"
        elif report.when == "setup":
            if report.failed:
                self.outcomes[nid] = "error"
            elif report.skipped:
                self.outcomes[nid] = "xfailed" if x else "skipped"
        elif report.failed:
            self.outcomes[nid] = "failed"
        elif report.skipped:
            self.outcomes[nid] = "xfailed" if x else "skipped"
        else:
            self.outcomes[nid] = "xpassed" if x else "passed"


class Runner:
    @pytest.fixture
    def candidate(self):
        ref = os.environ["CHEESELESS_AUTHOR_REFERENCE"]

        class C:
            def run(self, args=(), stdin=None, timeout=None):
                data = stdin.encode() if isinstance(stdin, str) else stdin
                try:
                    p = subprocess.run([*argv, *args], input=data, capture_output=True,
                                       cwd=ref, timeout=timeout or 30.0)
                except subprocess.TimeoutExpired:
                    raise AssertionError("candidate call timed out")
                dec = lambda b: b.decode("utf-8", "replace")
                return types.SimpleNamespace(returncode=p.returncode,
                                             stdout=dec(p.stdout), stderr=dec(p.stderr))
        return C()


rec = Rec()
args = ["hidden", "-p", "no:cacheprovider", "--import-mode=importlib", "-c", os.devnull,
        "--rootdir", os.getcwd(), "-q"]
if mode == "collect":
    args.append("--collect-only")
code = pytest.main(args, plugins=[rec] + ([Runner()] if mode == "run" else []))
print(MARK + json.dumps({"exitstatus": int(code), "collected": rec.collected,
                         "outcomes": rec.outcomes, "collection_errors": rec.errors}))
"""


class AuthorError(Exception):
    """An author operation was refused. ``kind`` is stable and maps to an error code."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


class AuthorStore(Store, AuthoringStore, Protocol):
    """The trusted store as the authoring use cases need it."""


@dataclass(frozen=True, slots=True)
class AuthorCheckResult:
    ok: bool
    problems: tuple[str, ...]
    tests: int


def spec_of(contract: Contract) -> str:
    return spec_digest(canonical_contract_bytes(contract))


def _baseline(repo: Repository, baseline_commit: str | None) -> str:
    return baseline_commit or repo.head_commit()


def proposal_id_for(repo: Repository, source: str, baseline_commit: str | None = None) -> str:
    contract = read_contract(repo, source)
    return freeze_digest(
        _baseline(repo, baseline_commit),
        repo.tree_of(source, "acceptance"),
        canonical_contract_bytes(contract),
    )


def _parse(outcome: AuthorOutcome) -> dict[str, object] | None:
    if outcome.timed_out:
        return None
    for line in reversed(outcome.stdout.splitlines()):
        if line.startswith(RESULT_MARK):
            try:
                data = json.loads(line[len(RESULT_MARK) :])
            except ValueError:
                return None
            return data if isinstance(data, dict) else None
    return None


def _suite(data: dict[str, object]) -> SuiteRun | None:
    try:
        collected = tuple(
            CollectedTest(str(t["node_id"]), tuple(str(c) for c in t["criteria"]))
            for t in data["collected"]  # type: ignore[attr-defined]
        )
        raw = data["outcomes"]
        outcomes = {str(k): TestOutcome(str(v)) for k, v in raw.items()}  # type: ignore[attr-defined]
        errors = tuple(str(e) for e in data["collection_errors"])  # type: ignore[attr-defined]
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    return SuiteRun(collected=collected, outcomes=outcomes, collection_errors=errors)


def _run_driver(
    repo: Repository,
    sandbox: AuthorSandbox,
    source: str,
    mode: str,
    reference: Path | None,
    run_argv: tuple[str, ...],
) -> SuiteRun | None:
    command = (sys.executable, "-I", "-B", "-c", DRIVER, mode, json.dumps(list(run_argv)))
    with tempfile.TemporaryDirectory(prefix="cheeseless-author-") as tmp:
        acceptance = Path(tmp) / "acceptance"
        repo.export(repo.tree_of(source, "acceptance"), acceptance)
        outcome = sandbox.author_run(acceptance, reference, command, env=(), timeout=CHECK_TIMEOUT)
    data = _parse(outcome)
    return None if data is None else _suite(data)


def _hidden_and_public(
    repo: Repository, source: str
) -> tuple[list[tuple[str, bytes]], list[bytes]]:
    tree = repo.tree_of(source, "acceptance")
    hidden = [
        (
            f"acceptance/{b.path}",
            repo.show(source, f"acceptance/{b.path}"),
        )
        for b in repo.blobs_under(tree, "")
        if not b.path.startswith("contract/")
    ]
    projection = repo.tree_without(repo.tree_of(source), "acceptance", "acceptance/contract")
    public: list[bytes] = []
    for blob in repo.blobs_under(projection, ""):
        public.append(repo.show(source, blob.path))
        public.append(blob.path.encode())
    return hidden, public


def author_check(repo: Repository, sandbox: AuthorSandbox, source: str) -> AuthorCheckResult:
    """Collect the author's tree and check it against the contract (stage d, advisory)."""
    try:
        contract = read_contract(repo, source)
    except (FreezeError, ValueError, OSError) as exc:
        return AuthorCheckResult(False, (redact_sentinels(f"contract: {exc}"),), 0)
    hidden, public = _hidden_and_public(repo, source)
    problems = list(sentinel_problems(hidden, public))
    suite = _run_driver(repo, sandbox, source, "collect", None, ())
    if suite is None:
        problems.append("the author runner returned no usable result")
        return AuthorCheckResult(False, tuple(redact_sentinels(p) for p in problems), 0)
    problems += freeze_problems([c.id for c in contract.criteria], suite)
    # The RED-on-baseline and flakiness checks belong to approval; collection is all we run.
    problems = [p for p in problems if "not RED" not in p]
    redacted = tuple(redact_sentinels(p) for p in problems)
    return AuthorCheckResult(not redacted, redacted, len(suite.collected))


def record_reference(repo: Repository, store: AuthoringStore, worktree: Path, source: str) -> str:
    """Snapshot the reference worktree and bind it to the current spec (host-side)."""
    contract = read_contract(repo, source)
    tree = repo.snapshot(worktree)
    store.save_reference(contract.lineage_id, spec_of(contract), tree)
    return tree


def author_crosscheck(
    repo: Repository,
    sandbox: AuthorSandbox,
    store: AuthorStore,
    source: str,
    *,
    baseline_commit: str | None = None,
    reference_worktree: Path | None = None,
) -> CrosscheckRecord:
    """Run the author's tests against the reference snapshot and record the result.

    It does not charge the attempt budget: the reference is not the candidate.
    """
    contract = read_contract(repo, source)
    spec = spec_of(contract)
    saved = store.load_reference(contract.lineage_id)
    if saved is None or saved[0] != spec:
        raise AuthorError("no_reference", "no reference snapshot is bound to the current spec")
    reference_tree = saved[1]
    acceptance_tree = repo.tree_of(source, "acceptance")
    baseline = _baseline(repo, baseline_commit)
    with tempfile.TemporaryDirectory(prefix="cheeseless-ref-") as tmp:
        ref_root = Path(tmp) / "reference"
        repo.export(reference_tree, ref_root)
        suite = _run_driver(repo, sandbox, source, "run", ref_root, contract.run)
    valid = {c.id for c in contract.criteria}
    green = False
    red: list[str] = []
    if suite is not None:
        outcomes = suite.outcome_map
        failing = [t for t in suite.collected if outcomes.get(t.node_id) is not TestOutcome.PASSED]
        green = bool(suite.collected) and not failing and not suite.collection_errors
        red = sorted({c for t in failing for c in t.criteria if c in valid})
        if not green and not red:
            red = []
    record = CrosscheckRecord(
        acceptance_tree=acceptance_tree,
        spec_digest=spec,
        baseline_commit=baseline,
        reference_tree=reference_tree,
        green=green,
        red_criteria=tuple(red) if not green else (),
    )
    store.save_crosscheck(proposal_id_for(repo, source, baseline), record)
    return record


def reference_failed(store: AuthoringStore, contract: Contract) -> AuthoringState:
    """A reference session failed: the task is blocked and the author can see it."""
    spec = spec_of(contract)
    state = store.authoring_state(contract.lineage_id, spec).reference_failed(spec)
    store.save_authoring_state(state)
    return state


def start_reference(store: AuthoringStore, contract: Contract) -> AuthoringState:
    """Start a reference retry; refused (``AuthoringError``) after two retries."""
    state = store.authoring_state(contract.lineage_id, spec_of(contract))
    if state.phase is not TaskPhase.REFERENCE_BLOCKED:
        return state
    state = state.retry_started()
    store.save_authoring_state(state)
    return state


def freeze_propose(
    repo: Repository, store: AuthoringStore, source: str, *, baseline_commit: str | None = None
) -> Proposal:
    contract = read_contract(repo, source)
    spec = spec_of(contract)
    state = store.authoring_state(contract.lineage_id, spec)
    if state.closed:
        raise AuthorError("frozen", "the task is frozen; author operations are closed")
    if state.phase is TaskPhase.PROPOSED:
        state = state.stale()
    baseline = _baseline(repo, baseline_commit)
    proposal = Proposal(
        proposal_id=proposal_id_for(repo, source, baseline),
        lineage_id=contract.lineage_id,
        baseline_commit=ids.git_oid(baseline),
        acceptance_tree=repo.tree_of(source, "acceptance"),
        spec_digest=spec,
    )
    store.save_proposal(proposal)
    store.save_authoring_state(state.proposed())
    return proposal


def _crosscheck_problem(
    repo: Repository, store: AuthoringStore, proposal: Proposal
) -> AuthorError | None:
    record = store.load_crosscheck(proposal.proposal_id)
    saved = store.load_reference(proposal.lineage_id)
    if record is None:
        return AuthorError("no_crosscheck", "no crosscheck is recorded for this proposal")
    bound = saved is not None and crosscheck_binds(
        record,
        acceptance_tree=proposal.acceptance_tree,
        spec_digest=proposal.spec_digest,
        baseline_commit=proposal.baseline_commit,
        reference_tree=saved[1],
    )
    if not bound or saved is None or saved[0] != proposal.spec_digest:
        return AuthorError("crosscheck_stale", "the crosscheck no longer matches this proposal")
    if not record.green:
        names = ", ".join(record.red_criteria) or "the run"
        return AuthorError("crosscheck_red", f"the reference failed the author's tests: {names}")
    return None


def approve_freeze(
    repo: Repository,
    runner: SuiteRunner,
    sandbox: Sandbox,
    store: AuthorStore,
    proposal_id: str,
    *,
    actor: str,
    no_reference: bool = False,
    reason: str = "",
) -> object:
    """Host-only: freeze a proposed, merged, crosschecked and RED task."""
    reason = reason.strip()
    if no_reference and not reason:
        raise AuthorError("reason_required", "--no-reference needs a --reason")
    proposal = store.load_proposal(proposal_id)
    if proposal is None:
        raise AuthorError("unknown_proposal", "no such proposal")
    contract_state = store.authoring_state(proposal.lineage_id, proposal.spec_digest)
    if contract_state.phase is not TaskPhase.PROPOSED:
        raise AuthorError("not_proposed", f"the task is {contract_state.phase.value}")
    head = repo.head_commit()
    if repo.tree_of(head, "acceptance") != proposal.acceptance_tree:
        raise AuthorError("not_merged", "the proposed acceptance tree is not merged into HEAD")
    if not no_reference:
        problem = _crosscheck_problem(repo, store, proposal)
        if problem is not None:
            raise problem
    prepared: PreparedFreeze = prepare(
        repo, runner, sandbox, baseline_commit=proposal.baseline_commit, source=head
    )
    if prepared.task_id != proposal.proposal_id:
        raise AuthorError("not_merged", "HEAD no longer reproduces the proposed freeze")
    result = commit(prepared, store, waiver=reason if no_reference else "")
    try:
        store.save_authoring_state(contract_state.frozen())
    except AuthoringError as exc:  # pragma: no cover - guarded above
        raise AuthorError("not_proposed", str(exc)) from exc
    store.log_event(
        result.task_id, "freeze-approve", f"{actor}{' no-reference' if no_reference else ''}"
    )
    return result


__all__ = [
    "DRIVER",
    "RESULT_MARK",
    "AuthorCheckResult",
    "AuthorError",
    "approve_freeze",
    "author_check",
    "author_crosscheck",
    "freeze_propose",
    "proposal_id_for",
    "read_contract",
    "record_reference",
    "reference_failed",
    "spec_of",
    "start_reference",
]
