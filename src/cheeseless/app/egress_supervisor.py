"""Host-side reaction to a DLP hit (Q49, R2).

The proxy cannot cut a flow already in transit, so a hit is only advisory to the
proxy stream: this supervisor drops the response (the addon does), persists a
signed ``BLOCKED`` result, rotates the holdout without touching lineage budget or
freeze identity, tears down the whole agent process tree through a host callback,
and alerts the operator with host, rule and fingerprint id only. No content ever
enters the event log or the alert.
"""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from cheeseless.app.holdout import rotate_holdout
from cheeseless.domain.freeze import FrozenTask
from cheeseless.domain.result import Result, sign
from cheeseless.domain.status import Status
from cheeseless.ports.repository import Repository
from cheeseless.ports.store import Store

Alert = Callable[[str, str, str], None]


@dataclass(frozen=True, slots=True)
class EgressBlock:
    host: str
    rule: str
    fingerprint_id: str

    def __post_init__(self) -> None:
        for value in (self.host, self.rule, self.fingerprint_id):
            if not value or not value.isprintable() or len(value) > 128:
                raise ValueError("invalid egress block field")


def _stderr_alert(host: str, rule: str, fingerprint_id: str) -> None:
    import sys

    sys.stderr.write(
        f"cheeseless: egress blocked host={host} rule={rule} fingerprint={fingerprint_id}\n"
    )
    sys.stderr.flush()


class EgressSupervisor:
    """Idempotent, fail-closed handler for one DLP hit."""

    def __init__(
        self,
        *,
        store: Store,
        repo: Repository,
        worktree: Path,
        task_id: str,
        profile: str = "local",
        tool_version: str = "0.1.0",
        teardown: Callable[[], None] | None = None,
        alert: Alert | None = None,
    ) -> None:
        self._store = store
        self._repo = repo
        self._worktree = worktree
        self._task_id = task_id
        self._profile = profile
        self._tool_version = tool_version
        self._teardown = teardown or (lambda: None)
        self._alert = alert or _stderr_alert
        self._lock = threading.Lock()
        self._handled = False
        self._last: EgressBlock | None = None

    @property
    def handled(self) -> bool:
        return self._handled

    @property
    def last(self) -> EgressBlock | None:
        return self._last

    def block(self, block: EgressBlock) -> None:
        with self._lock:
            if self._handled:
                return
            self._handled = True
            self._last = block
        failure: Exception | None = None
        try:
            self._persist(block)
        except Exception as exc:  # noqa: BLE001 - teardown and alert must still run
            failure = exc
        try:
            rotate_holdout(self._store, self._task_id)
        except Exception as exc:  # noqa: BLE001
            failure = failure or exc
        try:
            self._store.log_event(
                self._task_id,
                "egress",
                f"dlp_blocked host={block.host} rule={block.rule} id={block.fingerprint_id}",
            )
        except Exception as exc:  # noqa: BLE001
            failure = failure or exc
        try:
            self._teardown()
        except Exception as exc:  # noqa: BLE001
            failure = failure or exc
        try:
            self._alert(block.host, block.rule, block.fingerprint_id)
        except Exception as exc:  # noqa: BLE001
            failure = failure or exc
        if failure is not None:
            raise failure

    def _persist(self, block: EgressBlock) -> None:
        frozen: FrozenTask
        try:
            frozen, _seed = self._store.load_task(self._task_id)
            baseline = frozen.baseline_commit
        except Exception:  # noqa: BLE001
            baseline = "0" * 40
        try:
            tree_hash = self._repo.snapshot(self._worktree)
        except Exception:  # noqa: BLE001
            tree_hash = baseline
        result = Result(
            task_id=self._task_id,
            tree_hash=tree_hash,
            baseline_digest=hashlib.sha256(baseline.encode()).hexdigest(),
            profile=self._profile,
            tool_version=self._tool_version,
            status=Status.BLOCKED,
            criteria=(),
        )
        signed = sign(result, self._store.hmac_key())
        self._store.save_result(signed, operator_cause="egress DLP hit")


__all__ = ["EgressBlock", "EgressSupervisor"]
