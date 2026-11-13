"""status use case: return the latest verified result for a task."""

from __future__ import annotations

from pathlib import Path

from cheeseless.domain.result import Result, verify
from cheeseless.ports.repository import Repository
from cheeseless.ports.store import Store


def status(
    store: Store,
    *,
    task_id: str | None = None,
    repo: Repository | None = None,
    worktree: str | Path | None = None,
) -> Result | None:
    if task_id is None:
        task_id = store.active_task_id()
    if task_id is None:
        return None
    result = store.latest_result(task_id)
    if result is None:
        return None
    key = store.hmac_key()
    if not verify(result, key):
        raise ValueError("stored result failed HMAC verification")
    if result.task_id != task_id:
        raise ValueError("stored result is bound to another task")
    if repo is not None and worktree is not None:
        current = repo.snapshot(Path(worktree))
        if current != result.tree_hash:
            # Stale result: still return it, caller can inspect tree_hash.
            pass
    return result


__all__ = ["status"]