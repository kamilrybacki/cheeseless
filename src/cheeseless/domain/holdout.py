"""Stratified holdout split (Q27, Q44).

Within each criterion, tests are ranked by HMAC(seed, node id) and a fixed count goes
to the holdout, never fewer than one on either side. The seed is drawn after the
operator approves the suite, so the author cannot steer the split.
"""

import hashlib
import hmac
from collections.abc import Mapping
from dataclasses import dataclass

from cheeseless.domain import ids

SEED_BYTES = 32
MIN_TESTS_PER_CRITERION = 2
HOLDOUT_FRACTION_DENOMINATOR = 3


@dataclass(frozen=True, slots=True)
class Split:
    """Node id → criterion id, for the visible (local) and holdout (CI) sets."""

    visible: Mapping[str, str]
    holdout: Mapping[str, str]


def _rank(seed: bytes, node_id: str) -> bytes:
    return hmac.new(seed, b"cheeseless.holdout\x00" + node_id.encode(), hashlib.sha256).digest()


def holdout_count(n: int) -> int:
    """About a third, but at least one test on each side."""
    if n < MIN_TESTS_PER_CRITERION:
        raise ValueError("a criterion needs at least two tests for a holdout split")
    return min(n - 1, max(1, n // HOLDOUT_FRACTION_DENOMINATOR))


def split(seed: bytes, tests: Mapping[str, str]) -> Split:
    """``tests`` maps node id → criterion id."""
    if not isinstance(seed, bytes) or len(seed) < SEED_BYTES:
        raise ValueError("the holdout seed must be at least 32 bytes")
    by_criterion: dict[str, list[str]] = {}
    for node_id, cid in tests.items():
        ids.criterion_id(cid)
        by_criterion.setdefault(cid, []).append(node_id)
    visible: dict[str, str] = {}
    holdout: dict[str, str] = {}
    for cid, nodes in sorted(by_criterion.items()):
        ranked = sorted(nodes, key=lambda node: _rank(seed, node))
        k = holdout_count(len(ranked))
        holdout.update((node, cid) for node in ranked[:k])
        visible.update((node, cid) for node in ranked[k:])
    return Split(visible=visible, holdout=holdout)
