"""Trusted store on the local file system (Q38, Q55, D7, D14).

Lives outside the repository and worktree, under ``$XDG_STATE_HOME/cheeseless``.
Every directory is 0700, every file 0600, nothing follows symlinks, and every
read re-validates. Budget and attempt updates hold an exclusive ``flock``.
"""

import hashlib
import os
import secrets
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from cheeseless.adapters.outbound.store import _fs, codec
from cheeseless.domain import ids
from cheeseless.domain.budget import DEFAULT_LIMITS, Budget, Pool
from cheeseless.domain.dlp import Fingerprints
from cheeseless.domain.freeze import FrozenTask
from cheeseless.domain.holdout import SEED_BYTES
from cheeseless.domain.result import MIN_KEY_BYTES, Result
from cheeseless.ports.store import StoreError

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


class FileStore:
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
        _fs.write_atomic(self._task_path(frozen.task.task_id), codec.encode_task(frozen, seed))

    def load_task(self, task_id: str) -> tuple[FrozenTask, bytes]:
        raw = _fs.read_bytes(self._task_path(task_id))
        if raw is None:
            raise StoreError(f"unknown task {task_id}")
        return codec.decode_task(raw, task_id)

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
        _fs.append_line(results / f"{task_id}.jsonl", line)
        _fs.write_atomic(results / f"{task_id}.json", codec.encode_result(result))

    def latest_result(self, task_id: str) -> Result | None:
        raw = _fs.read_bytes(self._root / "results" / f"{_task_id(task_id)}.json")
        return None if raw is None else codec.decode_result(raw)

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
