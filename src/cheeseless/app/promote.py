"""promote use case: apply an evaluated tree to gate/<task> with path filtering."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cheeseless.app.authoring import AuthorError, _require_open, read_contract, spec_of
from cheeseless.domain.authoring import AuthoringError
from cheeseless.domain.promote_policy import decide_author_promote, decide_promote
from cheeseless.domain.result import verify
from cheeseless.ports.authoring import AuthoringStore
from cheeseless.ports.repository import Repository
from cheeseless.ports.store import Store


@dataclass(frozen=True, slots=True)
class PromoteOutcome:
    ref: str
    commit: str


GATE_REF = "refs/heads/gate/"


class PromoteRejected(Exception):
    """Raised when the promote path filter rejects or requires approval."""

    def __init__(self, rejected: tuple[str, ...], needs_approval: tuple[str, ...]) -> None:
        super().__init__("promote rejected")
        self.rejected = rejected
        self.needs_approval = needs_approval


def promote(
    repo: Repository,
    store: Store,
    *,
    task_id: str | None = None,
    worktree: str | Path,
    ref: str | None = None,
    approved: Any = (),
) -> PromoteOutcome:
    if task_id is None:
        task_id = store.active_task_id()
    if task_id is None:
        raise ValueError("no active task")
    frozen, _seed = store.load_task(task_id)
    if store.session_disputed(task_id):
        raise ValueError("the session ended in a dispute and cannot be promoted")
    result = store.latest_result(task_id)
    if result is None:
        raise ValueError("no result for task")
    key = store.hmac_key()
    if not verify(result, key):
        raise ValueError("stored result failed HMAC verification")
    if result.task_id != task_id:
        raise ValueError("result task mismatch")
    if result.status.value != "pass":
        raise ValueError("only a PASS result may be promoted")

    candidate_tree = repo.snapshot(Path(worktree))
    if candidate_tree != result.tree_hash:
        raise ValueError("candidate tree does not match the evaluated tree")

    if ref is None:
        ref = f"refs/heads/gate/{task_id}"
    if not ref.startswith(GATE_REF):
        raise ValueError(f"implementer promote only writes under {GATE_REF}: {ref}")

    changes = repo.diff_modes(frozen.projection_tree, candidate_tree)
    decision = decide_promote(changes, approved=approved)
    if not decision.ok:
        raise PromoteRejected(decision.rejected, decision.needs_approval)

    commit = repo.promote(frozen.baseline_commit, frozen.projection_tree, candidate_tree, ref)
    store.log_event(task_id, "promote", commit)
    return PromoteOutcome(ref=ref, commit=commit)


def promote_author(
    repo: Repository, store: AuthoringStore, *, worktree: str | Path
) -> PromoteOutcome:
    """Publish the author's acceptance/ change onto spec/<lineage> (ledger Q3; ADR 0009, R15, R17).

    Requires an author_check PASS recorded on the same acceptance tree and spec, refuses once
    approval has begun, and carries only acceptance/ paths relative to the default branch.
    """
    tree = repo.snapshot(Path(worktree))
    contract = read_contract(repo, tree)
    state = _require_open(store, contract)
    saved = store.load_author_check(contract.lineage_id)
    if saved is None:
        raise AuthorError("no_author_check", "no author_check PASS is recorded for this task")
    if saved != (spec_of(contract), repo.tree_of(tree, "acceptance")):
        raise AuthorError(
            "stale_author_check", "the acceptance tree changed since the last author_check PASS"
        )
    baseline = repo.default_branch_commit()
    base_tree = repo.tree_of(baseline)
    decision = decide_author_promote(repo.diff_modes(base_tree, tree))
    if not decision.ok:
        raise PromoteRejected(decision.rejected, decision.needs_approval)
    ref = f"refs/heads/spec/{contract.lineage_id}"
    # Publication happens under the lock every lifecycle transition takes, at the version we
    # checked: approval cannot begin between the state check and the ref update (R17).
    with _guard(store, contract.lineage_id, state.version):
        commit = repo.promote(baseline, base_tree, tree, ref)
    return PromoteOutcome(ref=ref, commit=commit)


@contextmanager
def _guard(store: AuthoringStore, lineage_id: str, version: int) -> Iterator[None]:
    try:
        with store.hold_open(lineage_id, version):
            yield
    except AuthoringError as exc:
        raise AuthorError(
            "approval_conflict", "the task changed while promoting; nothing was published"
        ) from exc


__all__ = ["PromoteOutcome", "PromoteRejected", "promote", "promote_author"]
