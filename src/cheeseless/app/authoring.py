"""Author-side use cases (ADR 0009, stages d and f).

``author_check`` and ``author_crosscheck`` run the author's unapproved acceptance tree in the
author-runner sandbox (no trusted store, no repo write, reference read-only). Their output is
advisory: approval re-runs the baseline itself and only trusts host-side records.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from cheeseless.app.author_driver import (
    DRIVER,
    RESULT_MARK,
    run_driver_on,
)
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
from cheeseless.domain.evaluation import SuiteRun, TestOutcome, freeze_problems
from cheeseless.domain.freeze import freeze_digest
from cheeseless.ports.authoring import AuthoringStore
from cheeseless.ports.repository import Repository, RepositoryError
from cheeseless.ports.sandbox import AuthorSandbox, Sandbox
from cheeseless.ports.store import Store


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


def _require_open(store: AuthoringStore, contract: Contract) -> AuthoringState:
    """The authoring state, or a refusal when the task is frozen or being frozen."""
    state = store.authoring_state(contract.lineage_id, spec_of(contract))
    if state.sealed:
        raise AuthorError("frozen", "the task is frozen; author operations are closed")
    return state


def _publish(write: Callable[[], None]) -> None:
    """Run a version-conditional write; losing the race to approval is a refusal."""
    try:
        write()
    except AuthoringError as exc:
        raise AuthorError(
            "approval_conflict", "the task changed while this was running; nothing was recorded"
        ) from exc


def _baseline(repo: Repository, baseline_commit: str | None) -> str:
    return baseline_commit or repo.head_commit()


def proposal_id_for(repo: Repository, source: str, baseline_commit: str | None = None) -> str:
    contract = read_contract(repo, source)
    return freeze_digest(
        _baseline(repo, baseline_commit),
        repo.tree_of(source, "acceptance"),
        canonical_contract_bytes(contract),
    )


def _run_driver(
    repo: Repository,
    sandbox: AuthorSandbox,
    source: str,
    mode: str,
    reference: Path | None,
    run_argv: tuple[str, ...],
) -> SuiteRun | None:
    with tempfile.TemporaryDirectory(prefix="cheeseless-author-") as tmp:
        acceptance = Path(tmp) / "acceptance"
        repo.export(repo.tree_of(source, "acceptance"), acceptance)
        return run_driver_on(sandbox, acceptance, mode, reference, run_argv)


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


def author_check(
    repo: Repository,
    sandbox: AuthorSandbox,
    source: str,
    *,
    store: AuthoringStore | None = None,
) -> AuthorCheckResult:
    """Collect the author's tree and check it against the contract (stage d, advisory)."""
    try:
        contract = read_contract(repo, source)
    except (FreezeError, ValueError, OSError) as exc:
        return AuthorCheckResult(False, (redact_sentinels(f"contract: {exc}"),), 0)
    if store is not None:
        _require_open(store, contract)
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
    state = _require_open(store, contract)
    tree = repo.snapshot(worktree)
    _publish(
        lambda: store.save_reference(
            contract.lineage_id, spec_of(contract), tree, expected_version=state.version
        )
    )
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
    state = _require_open(store, contract)
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
    pid = proposal_id_for(repo, source, baseline)
    _publish(
        lambda: store.save_crosscheck(
            pid, contract.lineage_id, record, expected_version=state.version
        )
    )
    return record


def reference_failed(store: AuthoringStore, contract: Contract) -> AuthoringState:
    """A reference session failed: the task is blocked and the author can see it."""
    spec = spec_of(contract)
    state = _require_open(store, contract).reference_failed(spec)
    store.save_authoring_state(state)
    return state


def start_reference(store: AuthoringStore, contract: Contract) -> AuthoringState:
    """Start a reference retry; refused (``AuthoringError``) after two retries."""
    state = _require_open(store, contract)
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
    state = _require_open(store, contract)
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
    sandbox: Sandbox,
    author_sandbox: AuthorSandbox,
    store: AuthorStore,
    proposal_id: str,
    *,
    actor: str,
    no_reference: bool = False,
    reason: str = "",
) -> object:
    """Host-only: freeze a proposed, merged, crosschecked and RED task.

    The author's acceptance code runs only inside ``author_sandbox``."""
    reason = reason.strip()
    if no_reference and not reason:
        raise AuthorError("reason_required", "--no-reference needs a --reason")
    proposal = store.load_proposal(proposal_id)
    if proposal is None:
        raise AuthorError("unknown_proposal", "no such proposal")
    state = store.authoring_state(proposal.lineage_id, proposal.spec_digest)
    # An approval that persisted the task and then failed leaves the state APPROVING with
    # the task on disk: a retry resumes it (the seed is never regenerated).
    resuming = state.phase is TaskPhase.APPROVING and store.has_task(proposal.proposal_id)
    if state.phase is not TaskPhase.PROPOSED and not resuming:
        raise AuthorError("not_proposed", f"the task is {state.phase.value}")
    _ready(repo, store, proposal, no_reference)
    try:
        # Ownership spans reservation to finish: a live approver is what tells recovery
        # apart from a dead one. A second approver is refused, not queued.
        with store.approval_ownership(proposal.lineage_id):
            return _approve_owned(
                repo, sandbox, author_sandbox, store, proposal, state, resuming,
                actor=actor, no_reference=no_reference, reason=reason,
            )
    except AuthoringError as exc:
        raise AuthorError("approval_conflict", f"another approval is in progress: {exc}") from exc


def _approve_owned(
    repo: Repository,
    sandbox: Sandbox,
    author_sandbox: AuthorSandbox,
    store: AuthorStore,
    proposal: Proposal,
    state: AuthoringState,
    resuming: bool,
    *,
    actor: str,
    no_reference: bool,
    reason: str,
) -> object:
    if resuming:
        reserved = state
    else:
        # Reserve before the expensive, side-effecting part: one approver at a time, and
        # only from the exact state version this approver validated.
        try:
            reserved = store.save_authoring_state(state.approving())
        except AuthoringError as exc:
            raise AuthorError(
                "approval_conflict", f"another approval won the reservation: {exc}"
            ) from exc
    try:
        # The reservation closed the author surface, so what is checked now is what is frozen.
        _ready(repo, store, proposal, no_reference)
        prepared: PreparedFreeze = prepare(
            repo,
            None,
            sandbox,
            author_sandbox=author_sandbox,
            baseline_commit=proposal.baseline_commit,
            source=_trusted_head(repo),
        )
        if prepared.task_id != proposal.proposal_id:
            raise AuthorError("not_merged", "the default branch no longer reproduces the freeze")
        result = commit(prepared, store, waiver=reason if no_reference else "")
    except BaseException as exc:
        if store.has_task(proposal.proposal_id):
            # The immutable task is already published: releasing would let a retry race a
            # half-finished freeze. Stay sealed; re-running the approval finishes it.
            if isinstance(exc, Exception):
                raise AuthorError(
                    "approval_incomplete",
                    f"the freeze was committed but not finished ({exc}); "
                    "run the approval again to complete it",
                ) from exc
            raise
        store.save_authoring_state(reserved.released())
        raise
    store.save_authoring_state(reserved.frozen())
    store.log_event(
        result.task_id, "freeze-approve", f"{actor}{' no-reference' if no_reference else ''}"
    )
    return result


def recover_approval(store: AuthorStore, proposal_id: str, *, actor: str) -> str:
    """Host-only: clear an APPROVING reservation whose approver died. Returns the outcome.

    Refused while the approver is alive (it holds the ownership lock). Never reachable
    through the UDS or MCP surfaces. Published task: finish it to FROZEN; otherwise roll
    back to PROPOSED. Every state change goes through the lifecycle compare-and-set."""
    proposal = store.load_proposal(proposal_id)
    if proposal is None:
        raise AuthorError("unknown_proposal", "no such proposal")
    try:
        owner = store.approval_ownership(proposal.lineage_id)
        with owner:
            return _recover_owned(store, proposal, actor)
    except AuthoringError as exc:
        text = str(exc)
        if "held" in text:
            raise AuthorError("approver_alive", "an approver is still running; not recovering") from exc
        raise AuthorError("recovery_conflict", f"the authoring state changed: {text}") from exc


def _recover_owned(store: AuthorStore, proposal: Proposal, actor: str) -> str:
    state = store.authoring_state(proposal.lineage_id, proposal.spec_digest)
    if state.phase is not TaskPhase.APPROVING:
        raise AuthorError("not_approving", f"nothing to recover: the task is {state.phase.value}")
    tid = proposal.proposal_id
    if not store.has_task(tid):
        store.save_authoring_state(state.released())
        store.log_event(tid, "freeze-recover", f"{actor} rolled-back")
        return "rolled_back"
    if store.load_fingerprints(tid) is None:
        # Published but the DLP fingerprints are missing: finishing here would freeze
        # without them. The approval itself rebuilds them, so that is the only way out.
        raise AuthorError(
            "approval_incomplete",
            "the task is published without fingerprints; run the approval again to finish it",
        )
    store.set_active(tid)
    store.save_authoring_state(state.frozen())
    store.log_event(tid, "freeze-recover", f"{actor} finished")
    return "finished"


def _trusted_head(repo: Repository) -> str:
    """The default branch, not whichever checkout the host happens to have open."""
    try:
        return repo.default_branch_commit()
    except RepositoryError as exc:
        raise AuthorError("not_merged", str(exc)) from exc


def _ready(repo: Repository, store: AuthoringStore, proposal: Proposal, no_reference: bool) -> None:
    head = _trusted_head(repo)
    if repo.tree_of(head, "acceptance") != proposal.acceptance_tree:
        raise AuthorError(
            "not_merged", "the proposed acceptance tree is not merged into the default branch"
        )
    if not no_reference:
        problem = _crosscheck_problem(repo, store, proposal)
        if problem is not None:
            raise problem


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
    "recover_approval",
    "record_reference",
    "reference_failed",
    "spec_of",
    "start_reference",
]
