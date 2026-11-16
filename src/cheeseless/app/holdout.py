"""Holdout rotation without changing freeze or budget identity (Q54, D14).

A rotation replaces the seed and the visible/holdout split only. The freeze digest,
the frozen acceptance tree and the lineage budget stay exactly as they were, so an
existing result still binds to the same task id (I7, D14).
"""

import secrets
from dataclasses import replace

from cheeseless.domain.freeze import FrozenTask
from cheeseless.domain.holdout import SEED_BYTES, split
from cheeseless.ports.store import Store


def rotate_holdout(store: Store, task_id: str, *, seed: bytes | None = None) -> FrozenTask:
    frozen, _previous = store.load_task(task_id)
    if seed is None:
        seed = secrets.token_bytes(SEED_BYTES)
    if len(seed) != SEED_BYTES:
        raise ValueError("holdout seed must be exactly 32 bytes")
    tests = {**frozen.visible, **frozen.holdout}
    if not tests:
        raise ValueError("the frozen task has no tests to split")
    result = split(seed, tests)
    rotated = replace(frozen, visible=dict(result.visible), holdout=dict(result.holdout))
    store.replace_task(rotated, seed)
    store.log_event(task_id, "holdout", "rotated")
    return rotated


__all__ = ["rotate_holdout"]
