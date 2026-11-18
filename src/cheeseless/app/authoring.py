"""Author-side use cases (ADR 0009, stages d and f).

``author_check`` and ``author_crosscheck`` run the author's unapproved acceptance tree in the
author-runner sandbox (no trusted store, no repo write, reference read-only). Their output is
advisory: approval re-runs the baseline itself and only trusts host-side records.
"""

from __future__ import annotations

import os
import stat
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from cheeseless.app.author_driver import (
    DRIVER,
    RESULT_MARK,
    run_driver_on,
)
from cheeseless.app.freeze import (
    FreezeError,
    PreparedFreeze,
    commit,
    prepare,
    read_contract,
    rebuild_fingerprints,
)
from cheeseless.domain import ids
from cheeseless.domain.authoring import (
    AuthoringError,
    AuthoringState,
    CrosscheckRecord,
    Proposal,
    StrengthReview,
    TaskPhase,
    crosscheck_binds,
    spec_digest,
    strength_review_binds,
    vendor_of_model,
)
from cheeseless.domain.contract import Contract, canonical_contract_bytes
from cheeseless.domain.dlp import redact_sentinels, sentinel_problems
from cheeseless.domain.evaluation import SuiteRun, TestOutcome, freeze_problems
from cheeseless.domain.freeze import FrozenTask, freeze_digest
from cheeseless.domain.holdout import split as holdout_split
from cheeseless.domain.session_role import SessionRole
from cheeseless.ports.authoring import AuthoringStore
from cheeseless.ports.repository import Repository, RepositoryError
from cheeseless.ports.sandbox import AuthorSandbox, Sandbox
from cheeseless.ports.store import Store, StoreError


class AuthorError(Exception):
    """An author operation was refused. ``kind`` is stable and maps to an error code."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


class AuthorStore(Store, AuthoringStore, Protocol):
    """The trusted store as the authoring use cases need it."""


# The actor an automatic approval records. Set by ``auto_approve_freeze`` itself, so a
# caller-chosen ``--as`` label can never stand in for it (ADR 0015 decision 1).
DEV_ACTOR = "cheeseless-dev"


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
    env: tuple[tuple[str, str], ...] = (),
) -> SuiteRun | None:
    with tempfile.TemporaryDirectory(prefix="cheeseless-author-") as tmp:
        acceptance = Path(tmp) / "acceptance"
        repo.export(repo.tree_of(source, "acceptance"), acceptance)
        return run_driver_on(sandbox, acceptance, mode, reference, run_argv, env)


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
    state = _require_open(store, contract) if store is not None else None
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
    if store is not None and state is not None and not redacted:
        # R15: a PASS is what author promote later requires, on this exact acceptance tree.
        tree = repo.tree_of(source, "acceptance")
        _publish(
            lambda: store.save_author_check(
                contract.lineage_id, spec_of(contract), tree, expected_version=state.version
            )
        )
    return AuthorCheckResult(not redacted, redacted, len(suite.collected))


def record_reference(repo: Repository, store: AuthoringStore, worktree: Path, source: str) -> str:
    """Snapshot the reference worktree and bind it to the current spec (host-side)."""
    tree = repo.snapshot(worktree)
    return record_reference_snapshot(repo, store, tree, source)


def record_reference_snapshot(
    repo: Repository, store: AuthoringStore, tree: str, source: str
) -> str:
    """Bind an already-taken snapshot to the contract at ``source`` (host-side).

    The dev reference runner has to snapshot before the sandbox projection is removed, so
    it takes the snapshot itself and binds it here without a second walk of the tree.
    """
    contract = read_contract(repo, source)
    state = _require_open(store, contract)
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
        suite = _run_driver(
            repo, sandbox, source, "run", ref_root, contract.run, tuple(contract.env)
        )
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
    unattended: bool = False,
) -> object:
    """Host-only: freeze a proposed, merged, crosschecked and RED task.

    The author's acceptance code runs only inside ``author_sandbox``. ``unattended`` is set
    only by :func:`auto_approve_freeze`: it adds the project's opt-in and a host-observed
    strength review (ADR 0016) on top of the checks a present operator gets (ADR 0009)."""
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
    try:
        # Ownership spans reservation to finish: a live approver is what tells recovery
        # apart from a dead one. A second approver is refused, not queued.
        with store.approval_ownership(proposal.lineage_id):
            if unattended:
                _unattended_problem(store, proposal)
            _ready(repo, store, proposal, no_reference)
            return _approve_owned(
                repo,
                sandbox,
                author_sandbox,
                store,
                proposal,
                state,
                resuming,
                actor=actor,
                no_reference=no_reference,
                reason=reason,
            )
    except AuthoringError as exc:
        raise AuthorError("approval_conflict", f"another approval is in progress: {exc}") from exc


def _unattended_problem(store: AuthorStore, proposal: Proposal) -> None:
    """Refuse an unattended freeze without consent and an observed review (ADR 0016).

    Called *inside* the approval ownership: `freeze --no-allow-auto` takes the same
    ownership, so a withdrawal either lands before this check (and the approval is
    refused) or is itself refused while the approval runs. It also runs before the
    repository is touched.
    """
    if not store.auto_freeze_enabled(proposal.lineage_id):
        # Decision B (ledger): an absent setting is not consent.
        raise AuthorError(
            "auto_freeze_not_enabled",
            "this project has not opted in to unattended freeze; "
            "run `cheeseless freeze --allow-auto` first",
        )
    problem = _strength_review_problem(store, proposal)
    if problem is not None:
        raise problem


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


def recover_approval(
    repo: Repository,
    sandbox: Sandbox,
    author_sandbox: AuthorSandbox,
    store: AuthorStore,
    proposal_id: str,
    *,
    actor: str,
) -> str:
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
            return _recover_owned(repo, sandbox, author_sandbox, store, proposal, actor)
    except AuthoringError as exc:
        text = str(exc)
        if "held" in text:
            raise AuthorError(
                "approver_alive", "an approver is still running; not recovering"
            ) from exc
        raise AuthorError("recovery_conflict", f"the authoring state changed: {text}") from exc


def _recover_owned(
    repo: Repository,
    sandbox: Sandbox,
    author_sandbox: AuthorSandbox,
    store: AuthorStore,
    proposal: Proposal,
    actor: str,
) -> str:
    state = store.authoring_state(proposal.lineage_id, proposal.spec_digest)
    if state.phase is not TaskPhase.APPROVING:
        raise AuthorError("not_approving", f"nothing to recover: the task is {state.phase.value}")
    tid = proposal.proposal_id
    if not store.has_task(tid):
        store.save_authoring_state(state.released())
        store.log_event(tid, "freeze-recover", f"{actor} rolled-back")
        return "rolled_back"
    _verify_published(repo, sandbox, author_sandbox, store, proposal)
    store.set_active(tid)
    store.save_authoring_state(state.frozen())
    store.log_event(tid, "freeze-recover", f"{actor} finished")
    return "finished"


def _verify_published(
    repo: Repository,
    sandbox: Sandbox,
    author_sandbox: AuthorSandbox,
    store: AuthorStore,
    proposal: Proposal,
) -> None:
    """Refuse to finish unless the published task and its fingerprints are whole.

    The trusted store is not trusted here: the task is fully reloaded (schema, digest), the
    fingerprints are rebuilt from the frozen acceptance tree and compared, and the
    test→criterion mapping is re-collected from that tree through the same contained path
    approval uses, so the stored split can only be the persisted seed's split of it."""
    tid = proposal.proposal_id

    def refuse(why: str) -> AuthorError:
        return AuthorError(
            "approval_incomplete",
            f"{why}; the task stays sealed. Run the approval again to rebuild it",
        )

    try:
        frozen, seed = store.load_task(tid)
        stored = store.load_fingerprints(tid)
    except StoreError as exc:
        raise refuse(f"the published task is not valid ({exc})") from exc
    if frozen.task.task_id != tid or frozen.acceptance_tree != proposal.acceptance_tree:
        raise refuse("the published task is not the one that was proposed")
    if stored is None:
        raise refuse("the task is published without fingerprints")
    try:
        rebuilt, why = rebuild_fingerprints(repo, frozen, _trusted_head(repo))
    except RepositoryError as exc:
        raise refuse(f"the task cannot be re-derived from the repository ({exc})") from exc
    if rebuilt is None:
        raise refuse(why)
    if stored != rebuilt:
        raise refuse("the stored fingerprints are incomplete or do not match the frozen tree")
    try:
        prepared = prepare(
            repo,
            None,
            sandbox,
            author_sandbox=author_sandbox,
            baseline_commit=proposal.baseline_commit,
            source=_trusted_head(repo),
        )
    except (FreezeError, RepositoryError) as exc:
        raise refuse(f"the frozen tree cannot be re-collected ({exc})") from exc
    if prepared.task_id != tid:
        raise refuse("the default branch no longer reproduces the freeze")
    problem = _split_problem(frozen, seed, prepared.tests)
    if problem:
        raise refuse(problem)
    if not frozen.waiver:
        # Approval required a green reference crosscheck bound to this proposal unless it was
        # waived (the waiver is MAC-authenticated with the task); repeat that policy here.
        gap = _crosscheck_problem(repo, store, proposal)
        if gap is not None:
            raise refuse(f"the reference crosscheck policy is not met ({gap})")


def _split_problem(frozen: FrozenTask, seed: bytes, tests: dict[str, str]) -> str:
    """Compare the stored visible/holdout record with the one the seed derives.

    ``tests`` is the mapping freshly collected from the frozen acceptance tree, never the
    stored maps. The seed is trusted-store state (operator-only, ADR 0001) and is not
    separately authenticated; the split of the re-collected mapping is what it determines."""
    try:
        derived = holdout_split(seed, tests)
    except ValueError as exc:
        return f"the holdout split cannot be re-derived ({exc})"
    expected = replace(frozen, visible=dict(derived.visible), holdout=dict(derived.holdout))
    if expected != frozen:
        return "the stored visible/holdout split is not the one the seed derives"
    return ""


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


def record_strength_review(
    store: AuthorStore,
    proposal_id: str,
    *,
    green: bool,
    author_model: str,
    reviewer_model: str,
) -> StrengthReview:
    """Host-only: store another vendor's review of a proposal's tests (ADR 0015).

    Called after the reviewer session ends, never before: the record says a review happened.
    Both vendors come from the model ids the *host* launched, not from anything the reviewer
    reported about itself (Decision 4). The record is never ``observed``: only
    :func:`run_strength_review`, which reads the verdict from the session it launched, may
    store one that is, so no caller of this function can assert it (ADR 0016).
    """
    return _store_strength_review(
        store,
        proposal_id,
        green=green,
        author_model=author_model,
        reviewer_model=reviewer_model,
        observed=False,
    )


def _store_strength_review(
    store: AuthorStore,
    proposal_id: str,
    *,
    green: bool,
    author_model: str,
    reviewer_model: str,
    observed: bool,
) -> StrengthReview:
    proposal = store.load_proposal(proposal_id)
    if proposal is None:
        raise AuthorError("unknown_proposal", "no such proposal")
    author_vendor = vendor_of_model(author_model)
    reviewer_vendor = vendor_of_model(reviewer_model)
    if author_vendor is None:
        raise AuthorError("unknown_vendor", f"cannot tell who makes {author_model!r}")
    if reviewer_vendor is None:
        raise AuthorError("unknown_vendor", f"cannot tell who makes {reviewer_model!r}")
    if reviewer_vendor == author_vendor:
        raise AuthorError(
            "strength_review_not_independent",
            "the reviewer must come from another vendor than the author",
        )
    state = store.authoring_state(proposal.lineage_id, proposal.spec_digest)
    review = StrengthReview(
        proposal_id=proposal.proposal_id,
        acceptance_tree=proposal.acceptance_tree,
        spec_digest=proposal.spec_digest,
        baseline_commit=proposal.baseline_commit,
        green=green,
        author_vendor=author_vendor,
        reviewer_vendor=reviewer_vendor,
        observed=observed,
    )
    _publish(
        lambda: store.save_strength_review(
            proposal.proposal_id,
            proposal.lineage_id,
            review,
            expected_version=state.version,
        )
    )
    return review


def _strength_review_problem(store: AuthoringStore, proposal: Proposal) -> AuthorError | None:
    """Why this proposal may not be frozen automatically, or ``None``.

    ADR 0015: the host may freeze alone only with a green review by another vendor that binds
    exactly this proposal."""
    review = store.load_strength_review(proposal.proposal_id)
    if review is None:
        return AuthorError(
            "no_strength_review",
            "auto-freeze needs a passing strength review; run the reviewer first",
        )
    if not review.observed:
        # A record an operator asserted is evidence that a human said so, not that a reviewer
        # ran. Unattended freeze needs the second kind, or the gate asks the author's
        # colleague to vouch for the author's own tests.
        return AuthorError(
            "unobserved_strength_review",
            "auto-freeze needs a review the host ran itself; a recorded one is not enough",
        )
    if not strength_review_binds(
        review,
        proposal_id=proposal.proposal_id,
        acceptance_tree=proposal.acceptance_tree,
        spec_digest=proposal.spec_digest,
        baseline_commit=proposal.baseline_commit,
    ):
        return AuthorError(
            "stale_strength_review",
            "the strength review covers a different acceptance tree, spec or baseline",
        )
    if not review.green:
        return AuthorError("strength_review_failed", "the strength review did not pass")
    if review.author_vendor == review.reviewer_vendor:
        return AuthorError(
            "strength_review_not_independent",
            "the reviewer must come from another vendor than the author",
        )
    return None


def auto_approve_freeze(
    repo: Repository,
    sandbox: Sandbox,
    author_sandbox: AuthorSandbox,
    store: AuthorStore,
    proposal_id: str,
) -> object:
    """Host-only: freeze a proposal the host reviewed itself, as ``cheeseless-dev``.

    ADR 0013 point 3 as ADR 0015 makes it checkable. The actor is set here, never taken from a
    caller-supplied label, and the ``--no-reference`` waiver is refused: an automatic approval
    cannot waive the crosscheck that the operator would have to waive in person.

    The opt-in and the review are checked inside :func:`approve_freeze`, under the approval
    ownership, so neither can be withdrawn between the check and the freeze.
    """
    return approve_freeze(
        repo,
        sandbox,
        author_sandbox,
        store,
        proposal_id,
        actor=DEV_ACTOR,
        unattended=True,
    )


__all__ = [
    "DRIVER",
    "RESULT_MARK",
    "AuthorCheckResult",
    "AuthorError",
    "approve_freeze",
    "auto_approve_freeze",
    "author_check",
    "author_crosscheck",
    "freeze_propose",
    "proposal_id_for",
    "read_contract",
    "record_strength_review",
    "recover_approval",
    "record_reference",
    "reference_failed",
    "spec_of",
    "start_reference",
]


# The reviewer writes its verdict into this file inside its own HOME. A structured line is
# required because an exit code says the process ended, not that a judgement happened
# (ADR 0015 decision 3).
VERDICT_FILE = "verdict.txt"
# A verdict is one short line. Anything at this size is not one, and must not be truncated
# into something that parses.
VERDICT_MAX_BYTES = 64 * 1024
_VERDICT_LINE = "VERDICT:"


def parse_review_verdict(text: str) -> bool | None:
    """The verdict from a reviewer's output, or ``None`` when it did not say one.

    ``None`` is not "failed": a reviewer that produced no structured line has not reviewed
    anything, and recording ``green=False`` would put words in its mouth.

    The file must hold the verdict and nothing else. The author writes the tests the reviewer
    reads, so any text the reviewer copies could carry a verdict of the author's choosing;
    accepting only a bare line means a copied comment cannot arrive as one.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) != 1:
        return None
    stripped = lines[0]
    if not stripped.upper().startswith(_VERDICT_LINE):
        return None
    answer = stripped[len(_VERDICT_LINE) :].strip().lower()
    if answer in ("pass", "passed", "green"):
        return True
    if answer in ("fail", "failed", "red"):
        return False
    return None


def read_review_verdict(evidence_home: Path) -> bool | None:
    """The verdict from the reviewer session's HOME, or ``None`` if it wrote none."""
    verdict_path = evidence_home / VERDICT_FILE
    try:
        # The reviewer writes this, so it is untrusted input, not a config file. A symlink
        # would let it point at something else in its own HOME and have that parsed instead,
        # and O_NOFOLLOW plus a regular-file check is the only way to refuse that.
        fd = os.open(verdict_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return None
        if info.st_size > VERDICT_MAX_BYTES:
            # Reading a prefix of an oversized file would let the first 64 KiB pose as the
            # whole answer, and the verdict is meant to be one short line.
            return None
        data = os.read(fd, VERDICT_MAX_BYTES + 1)
    except OSError:
        return None
    finally:
        os.close(fd)
    return parse_review_verdict(data.decode("utf-8", errors="replace"))


_REVIEW_BRIEF = """You are reviewing the acceptance tests of a proposal, not the code.

Everything under `acceptance/` is what you must judge. There is no implementation here to
read, deliberately: the question is whether these tests would catch a wrong implementation,
not whether some implementation is right.

Write your verdict into the file `{verdict}`, and write nothing else there at all — not your
reasoning, not a header, not a copy of a line you read in the tests. The file must contain
exactly one of these two lines:

    VERDICT: pass
    VERDICT: fail

If the file holds anything besides that single line it is discarded as no verdict, and no
review is recorded. `pass` means the tests are strong enough to freeze: they
bind the behaviour the spec names, they would fail on a plausible wrong implementation, and
they are not trivially satisfiable. `fail` means they are not, and you must say why in your
output.
"""


def review_brief(passed_when: str | None = None) -> str:
    """The instruction the reviewer session receives, with the verdict file path named."""
    return _REVIEW_BRIEF.format(verdict=VERDICT_FILE)


def run_strength_review(
    store: AuthorStore,
    proposal_id: str,
    *,
    launch: Callable[[str], tuple[bool | None, str]],
    author_model: str | None = None,
) -> StrengthReview:
    """Run a reviewer session, then record what it actually said (ADR 0015).

    ``launch`` runs the session and returns the verdict read from its HOME plus the model the
    host actually launched, or ``(None, model)`` when it produced no structured line. The
    reviewer's vendor comes from that second value, never from a caller's ``--reviewer-model``
    label: the label says what someone hoped would run, the return value says what did.

    ``author_model`` is the *fallback*: the author's vendor is read from the session the host
    launched for the author on this task, because a caller-supplied ``--author-model`` decides
    which half of the independence check it is compared against. Passing two labels to a
    comparison proves only that the labels differ (ADR 0016 review, bound c).

    A reviewer that said nothing is not a failed review, and
    :func:`record_strength_review` validates vendors independently of this call, so a refusal
    here and a refusal there mean the same thing.
    """
    proposal = store.load_proposal(proposal_id)
    if proposal is None:
        raise AuthorError("unknown_proposal", "no such proposal")
    # Provenance first: a launch that cannot be attributed to a model the *host* started
    # records nothing, so it is not worth running the reviewer for it either.
    recorded_author = store.launched_model(proposal_id, SessionRole.AUTHOR.value)
    if recorded_author is None:
        raise AuthorError(
            "author_unknown_launch",
            "the host has no launch record for this task's author session, so the reviewer's "
            "independence cannot be established",
        )
    if author_model is not None:
        claimed = vendor_of_model(author_model)
        recorded = vendor_of_model(recorded_author)
        if claimed is None or recorded != claimed:
            # Not a warning: a label that disagrees with the launch record is either a typo or
            # an attempt to pick the vendor the reviewer gets compared against.
            raise AuthorError(
                "author_model_mismatch",
                f"--author-model {author_model!r} disagrees with the launched author "
                f"{recorded_author!r}",
            )
    verdict, launched_model = launch(review_brief())
    if not launched_model:
        # Without a launch identity there is no vendor to check independence against, and
        # guessing one is how a single-vendor review gets recorded as cross-vendor.
        raise AuthorError(
            "strength_review_unknown_launch",
            "the reviewer session did not report which model it ran",
        )
    recorded_reviewer = store.launched_model(proposal_id, SessionRole.REVIEWER.value)
    if recorded_reviewer != launched_model:
        # The verdict has to come from the session the host recorded, not from a command whose
        # metadata merely matches: otherwise any process that writes the right line and exits
        # zero produces authoritative evidence (ADR 0016 review, bound b).
        raise AuthorError(
            "strength_review_unattributed",
            "the verdict does not come from a reviewer session the host launched and recorded",
        )
    if verdict is None:
        raise AuthorError(
            "strength_review_no_verdict",
            f"the reviewer wrote no `{_VERDICT_LINE}` line, so it reviewed nothing",
        )
    return _store_strength_review(
        store,
        proposal_id,
        green=verdict,
        # Both vendors come from the launch records, not from the command line.
        author_model=recorded_author,
        reviewer_model=launched_model,
        # The difference from a manual record: this one comes from a session the host ran and
        # whose output it read, not from an operator's assertion about one.
        observed=True,
    )
