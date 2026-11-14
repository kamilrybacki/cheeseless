"""Trusted-store audit sink for allowed egress requests (P11-11).

The proxy runs on its own thread, so every append is serialised. The event's only text
is validated, bounded metadata; the sink never receives request or response content.
"""

from __future__ import annotations

import json
import threading

from cheeseless.domain.audit import AuditEvent
from cheeseless.ports.store import Store

AUDIT_KIND = "egress_allow"


class StoreAuditSink:
    def __init__(self, store: Store, task_id: str, *, lock: threading.Lock | None = None) -> None:
        self._store = store
        self._task_id = task_id
        self._lock = lock or threading.Lock()

    def record(self, event: AuditEvent) -> None:
        line = json.dumps(event.to_record(), sort_keys=True, separators=(",", ":"))
        with self._lock:
            self._store.log_event(self._task_id, AUDIT_KIND, line)


__all__ = ["AUDIT_KIND", "StoreAuditSink"]
