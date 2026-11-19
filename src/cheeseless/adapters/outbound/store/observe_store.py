"""Read-only view of the trusted store for the dev-events observer.

It never creates, locks, repairs or writes anything: a missing store reads as empty and
a corrupt one raises ``StoreError``. It builds on the read-only mixins, so no write method exists on it at runtime.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cheeseless.adapters.outbound.store import _fs, codec
from cheeseless.adapters.outbound.store.dev_store import DevReadMixin
from cheeseless.adapters.outbound.store.reference_store import ReferenceReadMixin
from cheeseless.domain.dispute import DisputeCode, DisputeRecord, DisputeStatus
from cheeseless.ports.store import StoreError


class ObserveStore(DevReadMixin, ReferenceReadMixin):
    """Satisfies the ``DevObservation`` port. Constructing it touches nothing on disk."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def disputes(self) -> tuple[DisputeRecord, ...]:
        raw = self._read_disputes().get("records", [])
        if not isinstance(raw, list):
            raise StoreError("dispute records are invalid")
        try:
            return tuple(_dispute(row) for row in raw)
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise StoreError("dispute records are invalid") from exc

    def _read_disputes(self) -> dict[str, Any]:
        raw = _fs.read_bytes(self._root / "disputes.json")
        if raw is None:
            return {}
        data = codec.loads(raw, "disputes")
        if not isinstance(data, dict):
            raise StoreError("dispute records are invalid")
        return data


def _dispute(row: dict[str, Any]) -> DisputeRecord:
    return DisputeRecord(
        dispute_id=str(row["dispute_id"]),
        task_id=str(row["task_id"]),
        lineage_id=str(row["lineage_id"]),
        criterion_id=str(row["criterion_id"]),
        code=DisputeCode(row["code"]),
        note=str(row["note"]),
        status=DisputeStatus(row["status"]),
        resolved_by=str(row.get("resolved_by", "")),
    )
