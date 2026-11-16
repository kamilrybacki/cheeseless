"""A frozen task as the trusted store keeps it (D7, D14, I5)."""

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass

from cheeseless.domain import ids
from cheeseless.domain.contract import Contract
from cheeseless.domain.task import Task

_DIGEST_DOMAIN = b"cheeseless.v1.freeze\x00"


@dataclass(frozen=True, slots=True)
class HiddenBlob:
    path: str
    oid: str

    def __post_init__(self) -> None:
        ids.git_oid(self.oid)
        if not self.path or self.path.startswith("/") or ".." in self.path.split("/"):
            raise ValueError(f"invalid hidden path: {self.path!r}")


@dataclass(frozen=True, slots=True)
class FrozenTask:
    task: Task
    contract: Contract
    baseline_commit: str
    # Tree of the baseline with protected paths removed: what the agent starts from.
    projection_tree: str
    # Tree of acceptance/ at the baseline (hidden tests plus the public contract).
    acceptance_tree: str
    hidden_blobs: tuple[HiddenBlob, ...]
    visible: Mapping[str, str]
    holdout: Mapping[str, str]
    # Reason the reference crosscheck was waived at approval (ADR 0009). Not in the digest.
    waiver: str = ""

    def __post_init__(self) -> None:
        ids.git_oid(self.baseline_commit)
        ids.git_oid(self.projection_tree)
        ids.git_oid(self.acceptance_tree)
        if self.task.lineage_id != self.contract.lineage_id:
            raise ValueError("task and contract lineage differ")
        if self.task.criteria != self.contract.criteria:
            raise ValueError("task and contract criteria differ")
        known = set(self.task.criterion_ids)
        for selected in (self.visible, self.holdout):
            if not selected or not set(selected.values()) <= known:
                raise ValueError("visible and holdout sets must name known criteria")
        if set(self.visible) & set(self.holdout):
            raise ValueError("a test cannot be both visible and holdout")
        for cid in known:
            if cid not in self.visible.values() or cid not in self.holdout.values():
                raise ValueError(f"criterion {cid} needs visible and holdout tests")


def freeze_digest(baseline_commit: str, acceptance_tree: str, contract_bytes: bytes) -> str:
    """Task id: binds the baseline, the frozen acceptance tree and the contract."""
    h = hashlib.sha256(_DIGEST_DOMAIN)
    for part in (ids.git_oid(baseline_commit).encode(), ids.git_oid(acceptance_tree).encode()):
        h.update(len(part).to_bytes(8, "big") + part)
    h.update(len(contract_bytes).to_bytes(8, "big") + contract_bytes)
    return h.hexdigest()
