"""Author-side lifecycle rules (ADR 0009, stages d and f).

A task is authored before it is frozen. ``AuthoringState`` is the pure, immutable state
machine for that period; ``Proposal`` and ``CrosscheckRecord`` are the host-side records
that approval checks. Nothing here touches the store or the repository, and nothing may
carry hidden-test content: only digests, tree ids and public criterion ids.
"""

from __future__ import annotations

import enum
import hashlib
from dataclasses import dataclass, replace

from cheeseless.domain import ids

# A reference session may be retried at most twice after its first failure (ledger R12).
MAX_REFERENCE_RETRIES = 2


class AuthoringError(Exception):
    """A transition the current phase does not allow."""


class TaskPhase(enum.Enum):
    AUTHORING = "authoring"
    REFERENCE_BLOCKED = "reference_blocked"
    PROPOSED = "proposed"
    # An approval holds the reservation: exactly one approver may freeze a proposal.
    APPROVING = "approving"
    FROZEN = "frozen"


def spec_digest(contract_bytes: bytes) -> str:
    """Digest of the contract bytes only: what the reference session was given."""
    return hashlib.sha256(contract_bytes).hexdigest()


@dataclass(frozen=True, slots=True)
class AuthoringState:
    lineage_id: str
    spec_digest: str
    phase: TaskPhase = TaskPhase.AUTHORING
    # Reference failures under ``spec_digest``; a spec edit resets it.
    reference_failures: int = 0
    # Retries already started after the first failure.
    retries_used: int = 0
    # Bumped by the store on every save; a save of a stale version is refused.
    version: int = 0

    def __post_init__(self) -> None:
        ids.lineage_id(self.lineage_id)
        ids.sha256_hex(self.spec_digest, "spec digest")
        if self.reference_failures < 0 or self.retries_used < 0 or self.version < 0:
            raise ValueError("counts cannot be negative")

    @property
    def closed(self) -> bool:
        return self.phase is TaskPhase.FROZEN

    @property
    def sealed(self) -> bool:
        """Frozen, or being frozen: author operations are refused in both."""
        return self.phase in (TaskPhase.FROZEN, TaskPhase.APPROVING)

    @property
    def retries_left(self) -> int:
        if self.reference_failures == 0:
            return MAX_REFERENCE_RETRIES
        return max(0, MAX_REFERENCE_RETRIES - self.retries_used)

    def _open(self) -> None:
        if self.sealed:
            raise AuthoringError("the task is frozen; author operations are closed")

    def reference_failed(self, current_spec: str) -> AuthoringState:
        self._open()
        if self.phase is TaskPhase.PROPOSED:
            raise AuthoringError("a proposed task has no reference session in flight")
        same = current_spec == self.spec_digest
        return replace(
            self,
            spec_digest=ids.sha256_hex(current_spec, "spec digest"),
            phase=TaskPhase.REFERENCE_BLOCKED,
            reference_failures=(self.reference_failures if same else 0) + 1,
            retries_used=self.retries_used if same else 0,
        )

    def retry_started(self) -> AuthoringState:
        if self.phase is not TaskPhase.REFERENCE_BLOCKED:
            raise AuthoringError("only a blocked reference can be retried")
        if self.retries_left <= 0:
            raise AuthoringError("the reference retry limit is used up; edit the spec")
        return replace(self, retries_used=self.retries_used + 1)

    def spec_edited(self, new_spec: str) -> AuthoringState:
        self._open()
        if new_spec == self.spec_digest:
            return self
        return replace(
            self,
            spec_digest=ids.sha256_hex(new_spec, "spec digest"),
            phase=TaskPhase.AUTHORING,
            reference_failures=0,
            retries_used=0,
        )

    def proposed(self) -> AuthoringState:
        if self.phase not in (TaskPhase.AUTHORING, TaskPhase.REFERENCE_BLOCKED):
            raise AuthoringError(f"cannot propose from {self.phase.value}")
        return replace(self, phase=TaskPhase.PROPOSED)

    def stale(self) -> AuthoringState:
        if self.phase is not TaskPhase.PROPOSED:
            raise AuthoringError(f"only a proposed task can go stale, not {self.phase.value}")
        return replace(self, phase=TaskPhase.AUTHORING)

    def approving(self) -> AuthoringState:
        if self.phase is not TaskPhase.PROPOSED:
            raise AuthoringError(f"cannot approve from {self.phase.value}")
        return replace(self, phase=TaskPhase.APPROVING)

    def released(self) -> AuthoringState:
        if self.phase is not TaskPhase.APPROVING:
            raise AuthoringError(f"nothing to release in {self.phase.value}")
        return replace(self, phase=TaskPhase.PROPOSED)

    def frozen(self) -> AuthoringState:
        if self.phase is not TaskPhase.APPROVING:
            raise AuthoringError(f"cannot freeze from {self.phase.value}")
        return replace(self, phase=TaskPhase.FROZEN)


@dataclass(frozen=True, slots=True)
class Proposal:
    """What ``freeze_propose`` stores. ``proposal_id`` equals the future task id."""

    proposal_id: str
    lineage_id: str
    baseline_commit: str
    acceptance_tree: str
    spec_digest: str

    def __post_init__(self) -> None:
        ids.sha256_hex(self.proposal_id, "proposal id")
        ids.lineage_id(self.lineage_id)
        ids.git_oid(self.baseline_commit)
        ids.git_oid(self.acceptance_tree)
        ids.sha256_hex(self.spec_digest, "spec digest")


@dataclass(frozen=True, slots=True)
class CrosscheckRecord:
    """Host evidence that the reference snapshot did (or did not) pass the author's tests."""

    acceptance_tree: str
    spec_digest: str
    baseline_commit: str
    reference_tree: str
    green: bool
    # Public criterion ids that failed against the reference: possible spec contradictions.
    red_criteria: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        ids.git_oid(self.acceptance_tree)
        ids.sha256_hex(self.spec_digest, "spec digest")
        ids.git_oid(self.baseline_commit)
        ids.git_oid(self.reference_tree)
        for cid in self.red_criteria:
            ids.criterion_id(cid)
        if self.green and self.red_criteria:
            raise ValueError("a green crosscheck cannot list red criteria")


# Model family -> who makes it. Independence is decided here, from the family the host
# launched, never from a string the reviewer reported (ADR 0015 decision 4).
_VENDORS: tuple[tuple[str, str], ...] = (
    ("gpt", "openai"),
    ("o1", "openai"),
    ("o3", "openai"),
    ("o4", "openai"),
    ("claude", "anthropic"),
    ("gemini", "google"),
    ("deepseek", "deepseek"),
    ("llama", "meta"),
    ("qwen", "alibaba"),
    ("mistral", "mistral"),
)


def vendor_of_model(model: str) -> str | None:
    """The vendor that makes ``model``, or ``None`` if it is not a recognised family.

    ``None`` is not a fallback to "unknown": callers must refuse, never guess.
    """
    name = model.rsplit("/", 1)[-1].lower()
    for prefix, vendor in _VENDORS:
        if name.startswith(prefix):
            return vendor
    return None


@dataclass(frozen=True, slots=True)
class StrengthReview:
    """Host-written evidence that another vendor reviewed a proposal's tests (ADR 0015).

    Only the host writes this, and only after a reviewer session it launched itself. There is
    deliberately no free-text field: a place to argue is a place to hide an argument.
    """

    proposal_id: str
    acceptance_tree: str
    spec_digest: str
    baseline_commit: str
    green: bool
    author_vendor: str
    reviewer_vendor: str
    # Whether the host watched a reviewer session produce this, or an operator asserted it.
    # A record cannot tell you whether judgment happened; it can tell you who said so.
    observed: bool = False

    def __post_init__(self) -> None:
        ids.sha256_hex(self.proposal_id, "proposal id")
        ids.git_oid(self.acceptance_tree)
        ids.sha256_hex(self.spec_digest, "spec digest")
        ids.git_oid(self.baseline_commit)
        for field, value in (
            ("author", self.author_vendor),
            ("reviewer", self.reviewer_vendor),
        ):
            if not value:
                raise ValueError(f"the {field} vendor cannot be empty")
        # A green review by the author's own vendor is not a review; it is the author agreeing
        # with itself. Refused at construction so it can never reach the store.
        if self.green and self.author_vendor == self.reviewer_vendor:
            raise ValueError("a green review needs a reviewer from another vendor")


def strength_review_binds(
    record: StrengthReview,
    *,
    proposal_id: str,
    acceptance_tree: str,
    spec_digest: str,
    baseline_commit: str,
) -> bool:
    """Whether ``record`` reviewed exactly the proposal now being approved."""
    return (
        record.proposal_id == proposal_id
        and record.acceptance_tree == acceptance_tree
        and record.spec_digest == spec_digest
        and record.baseline_commit == baseline_commit
    )


def crosscheck_binds(
    record: CrosscheckRecord,
    *,
    acceptance_tree: str,
    spec_digest: str,
    baseline_commit: str,
    reference_tree: str,
) -> bool:
    return (
        record.acceptance_tree == acceptance_tree
        and record.spec_digest == spec_digest
        and record.baseline_commit == baseline_commit
        and record.reference_tree == reference_tree
    )


__all__ = [
    "MAX_REFERENCE_RETRIES",
    "AuthoringError",
    "AuthoringState",
    "CrosscheckRecord",
    "Proposal",
    "StrengthReview",
    "TaskPhase",
    "crosscheck_binds",
    "spec_digest",
    "strength_review_binds",
    "vendor_of_model",
]
