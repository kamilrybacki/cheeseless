"""Dev-mode reference attempts in the trusted store (ADR 0014).

Mixed into ``FileStore``. One record per attempt under ``dev-reference/``. Every write
shares the ``store.lock`` flock, and a state change is compare-and-set, so two racing
``reference_done`` calls cannot both win. Host bookkeeping (the lineage, the pane id and
the result tree) moves under the same lock and never changes the state machine.
"""

from dataclasses import replace
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
_LOG_DIR = "logs"


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
        "lineage_id": a.lineage_id,
        "pane_id": a.pane_id,
        "result_tree": a.result_tree,
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
        data.get("lineage_id"),
        data.get("pane_id"),
        data.get("result_tree"),
    )


class ReferenceReadMixin:
    """The read paths only; the observer builds on this and never sees a write."""

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

    def reference_attempts(self, lineage_id: str | None = None) -> tuple[ReferenceAttempt, ...]:
        wanted = None if lineage_id is None else ids.lineage_id(lineage_id)
        found: list[ReferenceAttempt] = []
        for path in _fs.list_regular(self._root / _DIR):
            if not path.name.endswith(".json"):
                continue
            attempt_id = path.name[: -len(".json")]
            attempt = self._read_reference(attempt_id)
            if attempt is not None and (wanted is None or attempt.lineage_id == wanted):
                found.append(attempt)
        return tuple(found)

    def _reference_lineage_mtimes(self) -> dict[str, int]:
        """The newest attempt-file mtime per lineage, in nanoseconds.

        A reference run changes the stage without rewriting the dev state file, so the
        listing needs this too. A corrupt attempt raises ``StoreError``.
        """
        found: dict[str, int] = {}
        for attempt in self.reference_attempts():
            lineage = attempt.lineage_id
            if lineage is None:  # a one-off reference run belongs to no lineage
                continue
            stamp = _fs.mtime_ns(self._reference_path(attempt.attempt_id))
            if stamp is not None:
                found[lineage] = max(stamp, found.get(lineage, stamp))
        return found


class ReferenceStoreMixin(ReferenceReadMixin):
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
        result_tree: str | None = None,
    ) -> ReferenceAttempt | TransitionRefused:
        attempt_id = _attempt_id(attempt_id)
        if result_tree is not None and new is not ReferenceState.DONE:
            raise ValueError("only a done attempt carries a result tree")
        if result_tree is not None:
            ids.git_oid(result_tree)
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
            if result_tree is not None:
                moved = replace(moved, result_tree=result_tree)
            _fs.ensure_private_dir(self._root / _DIR)
            _fs.write_atomic(self._reference_path(attempt_id), codec.dumps(_encode(moved)))
        return moved

    def bind_reference_attempt(
        self,
        attempt_id: str,
        *,
        owner: AttemptOwner,
        attached: bool | None = None,
        pane_id: str | None = None,
    ) -> ReferenceAttempt | None:
        """Record that the relay attached and/or which pane runs it; never changes state.

        ``None`` means the attempt is missing or belongs to another owner; nothing is written.
        """
        attempt_id = _attempt_id(attempt_id)
        if pane_id is not None:
            ids.pane_id(pane_id)
        with _fs.locked(self._root / "store.lock"):
            current = self._read_reference(attempt_id)
            if current is None or not owner.owns(current):
                return None
            fields: dict[str, Any] = {}
            if attached:
                fields["attached"] = True
            if pane_id is not None:
                fields["pane_id"] = pane_id
            if not fields:
                return current
            bound = replace(current, **fields)
            _fs.ensure_private_dir(self._root / _DIR)
            _fs.write_atomic(self._reference_path(attempt_id), codec.dumps(_encode(bound)))
        return bound

    def reference_log_path(self, attempt_id: str) -> Path:
        """A host-only log path for a headless attempt; the caller opens it 0600."""
        attempt_id = _attempt_id(attempt_id)
        _fs.ensure_private_dir(self._root / _DIR)
        _fs.ensure_private_dir(self._root / _DIR / _LOG_DIR)
        return self._root / _DIR / _LOG_DIR / f"{attempt_id}.log"
