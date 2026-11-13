"""Load the digest-only DLP scanner from the trusted store (Q48).

A missing, stale or corrupt fingerprint file must never make the gate expose
unscanned text: callers omit the tail instead (I16).
"""

from cheeseless.domain.dlp import DlpScanner
from cheeseless.ports.store import Store, StoreError


def scanner_for(store: Store, task_id: str | None) -> DlpScanner | None:
    if task_id is None:
        return None
    try:
        fingerprints = store.load_fingerprints(task_id)
    except (StoreError, ValueError, OSError):
        return None
    if fingerprints is None:
        return None
    try:
        return DlpScanner(fingerprints)
    except ValueError:
        return None


__all__ = ["scanner_for"]
