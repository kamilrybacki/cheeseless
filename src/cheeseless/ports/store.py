"""Trusted store port (Q38, Q55, D6, D7, D14).

The store holds everything the agent must never see or change: the HMAC key, the
holdout seed, frozen tasks, the run budget and signed results.
"""

from typing import Protocol

from cheeseless.domain.budget import Budget, Pool
from cheeseless.domain.dispute import DisputeCode, DisputePolicy, DisputeRecord, DisputeStatus
from cheeseless.domain.dlp import Fingerprints
from cheeseless.domain.errors import ErrorCode
from cheeseless.domain.freeze import FrozenTask
from cheeseless.domain.result import Result


class StoreError(Exception):
    """The store is missing, corrupt or not private. Callers fail closed."""


class SessionDisputedError(StoreError):
    """The task's session ended in a dispute; no result may be published for it."""


class DisputeRefused(Exception):  # noqa: N818 - a refusal, not a fault
    """A dispute was refused for a catalogued reason. Nothing was recorded."""

    def __init__(self, code: ErrorCode) -> None:
        super().__init__(code.value)
        self.code = code


class Store(Protocol):
    def hmac_key(self) -> bytes:
        """Return the key, creating it on first use."""
        ...

    def save_task(self, frozen: FrozenTask, seed: bytes) -> None:
        """Persist the immutable task and seed exactly once; ``StoreError`` if it exists."""
        ...

    def replace_task(self, frozen: FrozenTask, seed: bytes) -> None:
        """Replace an existing task's seed and split (holdout rotation only)."""
        ...

    def has_task(self, task_id: str) -> bool: ...

    def load_task(self, task_id: str) -> tuple[FrozenTask, bytes]:
        """Raise ``StoreError`` if the task is unknown or unreadable."""
        ...

    def active_task_id(self) -> str | None: ...

    def set_active(self, task_id: str) -> None: ...

    def reserve_run(self, lineage_id: str, pool: Pool) -> Budget | None:
        """Atomically charge one run. ``None`` when the budget is exhausted."""
        ...

    def budget(self, lineage_id: str, pool: Pool) -> Budget: ...

    def grant(self, lineage_id: str, pool: Pool, runs: int) -> Budget: ...

    def save_result(self, result: Result, operator_cause: str) -> None: ...

    def latest_result(self, task_id: str) -> Result | None: ...

    def record_attempt(self, task_id: str, now: float, window: float) -> int:
        """Record a pre-hidden attempt; return attempts inside ``window`` seconds."""
        ...

    def log_event(self, subject: str, kind: str, cause: str) -> None:
        """Append an operator-only audit event."""
        ...

    def save_fingerprints(self, fingerprints: Fingerprints) -> None:
        """Store the digest-only DLP fingerprint set for a frozen task (Q48)."""
        ...

    def load_fingerprints(self, task_id: str) -> Fingerprints | None:
        """Return the fingerprint set, or ``None`` when freeze did not create one."""
        ...

    def session_role(self, task_id: str) -> str:
        """Role bound by the host at launch (``implementer`` if never set). Host-written."""
        ...

    def set_session_role(self, task_id: str, role: str) -> None: ...

    def session_generation(self, task_id: str) -> int:
        """Host-owned counter, bumped when a rejected dispute reopens the task."""
        ...

    def dispute_policy(self) -> DisputePolicy:
        """Trusted dispute policy; disabled when the host never set one."""
        ...

    def set_dispute_policy(self, policy: DisputePolicy) -> None: ...

    def session_disputed(self, task_id: str) -> bool:
        """True while an open or upheld dispute keeps the task closed."""
        ...

    def file_dispute(
        self, task_id: str, lineage_id: str, criterion_id: str, code: DisputeCode, note: str
    ) -> None:
        """Atomically check policy, cap and session state, then record the dispute.

        Raises ``DisputeRefused`` and records nothing when it must be refused.
        """
        ...

    def disputes(self) -> tuple[DisputeRecord, ...]: ...

    def resolve_dispute(self, dispute_id: str, outcome: DisputeStatus, actor: str) -> DisputeRecord:
        """Record the outcome once. Raises ``StoreError`` on a second resolution."""
        ...
