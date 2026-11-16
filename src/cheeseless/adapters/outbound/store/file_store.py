"""Trusted store on the local file system (Q38, Q55, D7, D14).

Lives outside the repository and worktree, under ``$XDG_STATE_HOME/cheeseless``.
Every directory is 0700, every file 0600, nothing follows symlinks, and every
read re-validates. Budget and attempt updates hold an exclusive ``flock``.
"""

import hashlib
import hmac
import os
import secrets
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from cheeseless.adapters.outbound.store import _fs, codec
from cheeseless.adapters.outbound.store.authoring_store import AuthoringStoreMixin
from cheeseless.domain import ids
from cheeseless.domain.budget import DEFAULT_LIMITS, Budget, Pool
from cheeseless.domain.dispute import (
    DisputeCode,
    DisputePolicy,
    DisputeRecord,
    DisputeStatus,
    refusal,
)
from cheeseless.domain.dlp import Fingerprints
from cheeseless.domain.freeze import FrozenTask
from cheeseless.domain.holdout import SEED_BYTES
from cheeseless.domain.result import MIN_KEY_BYTES, Result
from cheeseless.domain.status import Status
from cheeseless.ports.store import DisputeRefused, SessionDisputedError, StoreError

KEY_BYTES = MIN_KEY_BYTES


def store_root(common_dir: Path, state_home: Path) -> Path:
    """One store per repository, keyed by its resolved git common directory."""
    digest = hashlib.sha256(os.fsencode(os.path.realpath(common_dir))).hexdigest()[:16]
    return state_home / "cheeseless" / digest


def _task_id(value: str) -> str:
    try:
        return ids.sha256_hex(value, "task id")
    except ValueError as exc:
        raise StoreError(f"invalid task id: {exc}") from exc


class FileStore(AuthoringStoreMixin):
    def __init__(self, root: Path, clock: Callable[[], float] = time.time) -> None:
        self._root = root
        self._clock = clock
        root.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        _fs.ensure_private_dir(root)
        for sub in ("tasks", "results", "fingerprints"):
            _fs.ensure_private_dir(root / sub)

    @property
    def root(self) -> Path:
        return self._root

    # -- key -----------------------------------------------------------------

    def hmac_key(self) -> bytes:
        path = self._root / "hmac.key"
        _fs.create_exclusive(path, secrets.token_bytes(KEY_BYTES))
        key = _fs.read_bytes(path)
        if key is None or len(key) != KEY_BYTES:
            raise StoreError("HMAC key is missing or has the wrong length")
        return key

    # -- tasks ---------------------------------------------------------------

    def _task_path(self, task_id: str) -> Path:
        return self._root / "tasks" / f"{_task_id(task_id)}.json"

    def save_task(self, frozen: FrozenTask, seed: bytes) -> None:
        if len(seed) != SEED_BYTES:
            raise StoreError(f"holdout seed must be {SEED_BYTES} bytes")
        raw = codec.encode_task(frozen, seed)
        if frozen.waiver:
            data = codec.loads(raw, "task")
            data["waiver_mac"] = self._waiver_mac(frozen.task.task_id, frozen.waiver)
            raw = codec.dumps(data)
        _fs.write_atomic(self._task_path(frozen.task.task_id), raw)

    def _waiver_mac(self, task_id: str, waiver: str) -> str:
        """The waiver sits outside the freeze digest, so the host key authenticates it."""
        message = b"cheeseless-waiver-v1\0" + task_id.encode() + b"\0" + waiver.encode()
        return hmac.new(self.hmac_key(), message, hashlib.sha256).hexdigest()

    def load_task(self, task_id: str) -> tuple[FrozenTask, bytes]:
        raw = _fs.read_bytes(self._task_path(task_id))
        if raw is None:
            raise StoreError(f"unknown task {task_id}")
        frozen, seed = codec.decode_task(raw, task_id)
        if frozen.waiver:
            claimed = codec.loads(raw, "task").get("waiver_mac")
            expected = self._waiver_mac(task_id, frozen.waiver)
            if not isinstance(claimed, str) or not hmac.compare_digest(claimed, expected):
                raise StoreError(f"task {task_id}: waiver is not authentic")
        return frozen, seed

    def active_task_id(self) -> str | None:
        raw = _fs.read_bytes(self._root / "active")
        if raw is None:
            return None
        return _task_id(raw.decode("ascii", errors="replace").strip())

    def set_active(self, task_id: str) -> None:
        if _fs.read_bytes(self._task_path(task_id)) is None:
            raise StoreError(f"unknown task {task_id}")
        _fs.write_atomic(self._root / "active", task_id.encode() + b"\n")

    # -- budget --------------------------------------------------------------

    def _read_json(self, name: str) -> dict[str, Any]:
        raw = _fs.read_bytes(self._root / name)
        return {} if raw is None else codec.loads(raw, name)

    @staticmethod
    def _budget_from(data: dict[str, Any], lineage_id: str, pool: Pool) -> Budget:
        entry = data.get(lineage_id, {}).get(pool.value)
        if entry is None:
            return Budget(DEFAULT_LIMITS[pool])
        try:
            return Budget(int(entry["limit"]), int(entry["used"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise StoreError(f"budget entry for {lineage_id}/{pool.value} is invalid") from exc

    def _update_budget(
        self, lineage_id: str, pool: Pool, change: Callable[[Budget], Budget | None]
    ) -> Budget | None:
        ids.lineage_id(lineage_id)
        with _fs.locked(self._root / "store.lock"):
            data = self._read_json("budget.json")
            new = change(self._budget_from(data, lineage_id, pool))
            if new is None:
                return None
            pools = {**data.get(lineage_id, {}), pool.value: {"limit": new.limit, "used": new.used}}
            _fs.write_atomic(self._root / "budget.json", codec.dumps({**data, lineage_id: pools}))
            return new

    def reserve_run(self, lineage_id: str, pool: Pool) -> Budget | None:
        return self._update_budget(lineage_id, pool, lambda b: None if b.exhausted else b.reserve())

    def budget(self, lineage_id: str, pool: Pool) -> Budget:
        ids.lineage_id(lineage_id)
        return self._budget_from(self._read_json("budget.json"), lineage_id, pool)

    def grant(self, lineage_id: str, pool: Pool, runs: int) -> Budget:
        granted = self._update_budget(lineage_id, pool, lambda b: b.grant(runs))
        if granted is None:  # pragma: no cover - grant never declines
            raise StoreError("grant failed")
        return granted

    # -- results -------------------------------------------------------------

    def save_result(self, result: Result, operator_cause: str) -> None:
        task_id = _task_id(result.task_id)
        results = self._root / "results"
        line = codec.encode_result(result, operator_cause=operator_cause, time=self._clock())
        with _fs.locked(self._root / "store.lock"):
            # A signed BLOCKED result (egress DLP hit) is security evidence: it is kept
            # even after a dispute, so a dispute cannot hide a leak attempt.
            if result.status is not Status.BLOCKED and self._blocked(task_id, self._records()):
                raise SessionDisputedError("the session ended in a dispute")
            _fs.append_line(results / f"{task_id}.jsonl", line)
            _fs.write_atomic(results / f"{task_id}.json", codec.encode_result(result))

    def latest_result(self, task_id: str) -> Result | None:
        raw = _fs.read_bytes(self._root / "results" / f"{_task_id(task_id)}.json")
        return None if raw is None else codec.decode_result(raw)

    # -- disputes (ADR 0009, stage g) --------------------------------------------
    # Files are created lazily so the layout is unchanged while disputes are unused.

    def dispute_policy(self) -> DisputePolicy:
        raw = _fs.read_bytes(self._root / "dispute-policy.json")
        if raw is None:
            return DisputePolicy()
        data = codec.loads(raw, "dispute-policy.json")
        try:
            return DisputePolicy(enabled=data["enabled"], cap=data["cap"])
        except (KeyError, ValueError) as exc:
            raise StoreError(f"dispute policy is invalid: {exc}") from exc

    def set_dispute_policy(self, policy: DisputePolicy) -> None:
        body = codec.dumps({"enabled": policy.enabled, "cap": policy.cap})
        with _fs.locked(self._root / "store.lock"):
            _fs.write_atomic(self._root / "dispute-policy.json", body)

    def _records(self) -> tuple[DisputeRecord, ...]:
        raw = self._read_json("disputes.json").get("records", [])
        if not isinstance(raw, list):
            raise StoreError("dispute records are invalid")
        try:
            return tuple(
                DisputeRecord(
                    dispute_id=str(r["dispute_id"]),
                    task_id=str(r["task_id"]),
                    lineage_id=str(r["lineage_id"]),
                    criterion_id=str(r["criterion_id"]),
                    code=DisputeCode(r["code"]),
                    note=str(r["note"]),
                    status=DisputeStatus(r["status"]),
                    resolved_by=str(r.get("resolved_by", "")),
                )
                for r in raw
            )
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise StoreError("dispute records are invalid") from exc

    def _write_records(self, records: tuple[DisputeRecord, ...]) -> None:
        rows = [
            {
                "dispute_id": r.dispute_id,
                "task_id": r.task_id,
                "lineage_id": r.lineage_id,
                "criterion_id": r.criterion_id,
                "code": r.code.value,
                "note": r.note,
                "status": r.status.value,
                "resolved_by": r.resolved_by,
            }
            for r in records
        ]
        _fs.write_atomic(self._root / "disputes.json", codec.dumps({"records": rows}))

    @staticmethod
    def _blocked(task_id: str, records: tuple[DisputeRecord, ...]) -> bool:
        return any(r.task_id == task_id and r.blocks_session for r in records)

    def session_disputed(self, task_id: str) -> bool:
        return self._blocked(_task_id(task_id), self._records())

    def disputes(self) -> tuple[DisputeRecord, ...]:
        return self._records()

    def file_dispute(
        self, task_id: str, lineage_id: str, criterion_id: str, code: DisputeCode, note: str
    ) -> None:
        task_id = _task_id(task_id)
        ids.lineage_id(lineage_id)
        self.load_task(task_id)
        with _fs.locked(self._root / "store.lock"):
            records = self._records()
            latest = self.latest_result(task_id)
            reason = refusal(
                self.dispute_policy(),
                lineage_count=sum(1 for r in records if r.lineage_id == lineage_id),
                holds_pass=latest is not None and latest.status is Status.PASS,
                blocked=self._blocked(task_id, records),
            )
            if reason is not None:
                raise DisputeRefused(reason)
            record = DisputeRecord(
                f"d-{len(records) + 1}", task_id, lineage_id, criterion_id, code, note
            )
            self._write_records((*records, record))
            self.log_event(task_id, "dispute", record.dispute_id)

    def resolve_dispute(self, dispute_id: str, outcome: DisputeStatus, actor: str) -> DisputeRecord:
        if outcome is DisputeStatus.OPEN:
            raise StoreError("a dispute can only be resolved as upheld or rejected")
        if not actor.strip():
            raise StoreError("a resolution needs an actor")
        with _fs.locked(self._root / "store.lock"):
            records = self._records()
            match = [r for r in records if r.dispute_id == dispute_id]
            if not match:
                raise StoreError(f"unknown dispute: {dispute_id}")
            if match[0].status is not DisputeStatus.OPEN:
                raise StoreError(f"dispute {dispute_id} is already resolved")
            done = DisputeRecord(
                match[0].dispute_id,
                match[0].task_id,
                match[0].lineage_id,
                match[0].criterion_id,
                match[0].code,
                match[0].note,
                outcome,
                actor.strip(),
            )
            self._write_records(tuple(done if r is match[0] else r for r in records))
            self.log_event(
                done.task_id, f"dispute-{outcome.value}", f"{dispute_id} by {done.resolved_by}"
            )
            return done

    # -- DLP fingerprints --------------------------------------------------------

    def save_fingerprints(self, fingerprints: Fingerprints) -> None:
        task_id = _task_id(fingerprints.task_id)
        _fs.write_atomic(
            self._root / "fingerprints" / f"{task_id}.json", codec.encode_fingerprints(fingerprints)
        )

    def load_fingerprints(self, task_id: str) -> Fingerprints | None:
        task_id = _task_id(task_id)
        raw = _fs.read_bytes(self._root / "fingerprints" / f"{task_id}.json")
        if raw is None:
            return None
        fingerprints = codec.decode_fingerprints(raw)
        if fingerprints.task_id != task_id:
            raise StoreError("fingerprint file names another task")
        return fingerprints

    # -- attempts and audit ----------------------------------------------------

    def record_attempt(self, task_id: str, now: float, window: float) -> int:
        task_id = _task_id(task_id)
        with _fs.locked(self._root / "store.lock"):
            data = self._read_json("attempts.json")
            previous = data.get(task_id, [])
            if not isinstance(previous, list):
                raise StoreError("attempt log is invalid")
            recent = [float(t) for t in previous if now - float(t) < window] + [now]
            _fs.write_atomic(self._root / "attempts.json", codec.dumps({**data, task_id: recent}))
            return len(recent)

    def log_event(self, subject: str, kind: str, cause: str) -> None:
        event = {"time": self._clock(), "subject": subject, "kind": kind, "cause": cause}
        _fs.append_line(self._root / "events.jsonl", codec.dumps(event))
