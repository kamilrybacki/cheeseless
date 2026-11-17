"""Dev-mode reference attempts in the trusted store (ADR 0014).

Mixed into ``FileStore``. One record per attempt under ``dev-reference/``. Every write
shares the ``store.lock`` flock, and a state change is compare-and-set, so two racing
``reference_done`` calls cannot both win.
"""

from pathlib import Path
from typing import Any

from cheeseless.adapters.outbound.store import _fs, codec
from cheeseless.domain import ids
from cheeseless.domain.reference_attempt import (
    AttemptOwner,
    ReferenceAttempt,
    ReferenceMode,
    ReferenceState,
    TransitionRefused,
    transition,
)
from cheeseless.ports.store import StoreError

_DIR = "dev-reference"


def _attempt_id(value: str) -> str:
    try:
        return ids.attempt_id(value)
    except ValueError as exc:
        raise StoreError(f"invalid attempt id: {exc}") from exc


def _encode(a: ReferenceAttempt) -> dict[str, Any]:
    return {
        "attempt_id": a.attempt_id,
        "session_id": a.session_id,
        "generation": a.generation,
        "spec_digest": a.spec_digest,
        "mode": a.mode.value,
        "attached": a.attached,
        "state": a.state.value,
    }


def _decode(attempt_id: str, data: dict[str, Any]) -> ReferenceAttempt:
    if data["attempt_id"] != attempt_id:
        raise ValueError("the record belongs to another attempt")
    return ReferenceAttempt(
        attempt_id,
        data["session_id"],
        data["generation"],
        data["spec_digest"],
        ReferenceMode(data["mode"]),
        data["attached"],
        ReferenceState(data["state"]),
    )


class ReferenceStoreMixin:
    _root: Path

    def _reference_path(self, attempt_id: str) -> Path:
        return self._root / _DIR / f"{attempt_id}.json"

    def _read_reference(self, attempt_id: str) -> ReferenceAttempt | None:
        raw = _fs.read_bytes(self._reference_path(attempt_id))
        if raw is None:
            return None
        try:
            return _decode(attempt_id, codec.loads(raw, f"{attempt_id}.json"))
        except (KeyError, ValueError, TypeError) as exc:
            raise StoreError(f"corrupt reference attempt {attempt_id}: {exc}") from exc

    def reference_attempt(self, attempt_id: str) -> ReferenceAttempt | None:
        return self._read_reference(_attempt_id(attempt_id))

    def save_reference_attempt(self, attempt: ReferenceAttempt) -> ReferenceAttempt:
        attempt_id = _attempt_id(attempt.attempt_id)
        with _fs.locked(self._root / "store.lock"):
            if self._read_reference(attempt_id) is not None:
                raise StoreError(f"reference attempt {attempt_id} already exists")
            _fs.ensure_private_dir(self._root / _DIR)
            _fs.write_atomic(self._reference_path(attempt_id), codec.dumps(_encode(attempt)))
        return attempt

    def transition_reference_attempt(
        self,
        attempt_id: str,
        expected: ReferenceState,
        new: ReferenceState,
        *,
        owner: AttemptOwner | None = None,
    ) -> ReferenceAttempt | TransitionRefused:
        attempt_id = _attempt_id(attempt_id)
        with _fs.locked(self._root / "store.lock"):
            current = self._read_reference(attempt_id)
            if (
                current is None
                or current.state is not expected
                or (owner is not None and not owner.owns(current))
            ):
                return TransitionRefused(
                    attempt_id, expected, None if current is None else current.state
                )
            moved = transition(current, new)
            _fs.ensure_private_dir(self._root / _DIR)
            _fs.write_atomic(self._reference_path(attempt_id), codec.dumps(_encode(moved)))
        return moved
