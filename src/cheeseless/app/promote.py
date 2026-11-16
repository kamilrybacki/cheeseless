"""promote use case: apply an evaluated tree to gate/<task> with path filtering."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cheeseless.domain.promote_policy import decide_promote
from cheeseless.domain.result import verify
from cheeseless.ports.repository import Repository
from cheeseless.ports.store import Store


@dataclass(frozen=True, slots=True)
class PromoteOutcome:
    ref: str
    commit: str


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

    changes = repo.diff_modes(frozen.projection_tree, candidate_tree)
    decision = decide_promote(changes, approved=approved)
    if not decision.ok:
        raise PromoteRejected(decision.rejected, decision.needs_approval)

    commit = repo.promote(frozen.baseline_commit, frozen.projection_tree, candidate_tree, ref)
    store.log_event(task_id, "promote", commit)
    return PromoteOutcome(ref=ref, commit=commit)


__all__ = ["PromoteOutcome", "PromoteRejected", "promote"]
