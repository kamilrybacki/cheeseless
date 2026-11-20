"""``cheeseless dev`` phase state in the trusted store (ADR 0013).

Mixed into ``FileStore``. One record per lineage under ``dev/``, created lazily. Saves
share the ``store.lock`` flock with every other store transition and are compare-and-set
on ``version``, so two hosts driving one lineage cannot both advance it.
"""

from dataclasses import replace
from pathlib import Path
from typing import Any

from cheeseless.adapters.outbound.store import _fs, codec
from cheeseless.domain import ids
from cheeseless.domain.dev_session import DevPhase, DevSessionError, DevState
from cheeseless.ports.store import StoreError

_DIR = "dev"


def _lineage(value: str) -> str:
    try:
        return ids.lineage_id(value)
    except ValueError as exc:
        raise StoreError(f"invalid lineage id: {exc}") from exc


def _state_lineage(name: str) -> str | None:
    if not (name.startswith("state-") and name.endswith(".json")):
        return None
    try:
        return ids.lineage_id(name[len("state-") : -len(".json")])
    except ValueError:
        return None


def _encode(state: DevState) -> dict[str, Any]:
    return {
        "lineage_id": state.lineage_id,
        "phase": state.phase.value,
        "spec_digest": state.spec_digest,
        "proposal_id": state.proposal_id,
        "author_session": state.author_session,
        "implementer_session": state.implementer_session,
        "holdout_failures": state.holdout_failures,
        "version": state.version,
    }


def _optional_str(data: dict[str, Any], key: str) -> str | None:
    value = data[key]
    if value is not None and not isinstance(value, str):
        raise TypeError(f"{key} must be a string")
    return value


def _decode(lineage: str, data: dict[str, Any]) -> DevState:
    if data["lineage_id"] != lineage:
        raise ValueError("the record belongs to another lineage")
    for key in ("holdout_failures", "version"):
        if type(data[key]) is not int:
            raise TypeError(f"{key} must be an integer")
    return DevState(
        lineage,
        DevPhase(data["phase"]),
        _optional_str(data, "spec_digest"),
        _optional_str(data, "proposal_id"),
        _optional_str(data, "author_session"),
        _optional_str(data, "implementer_session"),
        data["holdout_failures"],
        data["version"],
    )


class DevReadMixin:
    """The read paths only; the observer builds on this and never sees a write."""

    _root: Path

    def _dev_path(self, lineage: str) -> Path:
        return self._root / _DIR / f"state-{lineage}.json"

    def _read_dev(self, lineage: str) -> DevState | None:
        name = f"state-{lineage}.json"
        raw = _fs.read_bytes(self._dev_path(lineage))
        if raw is None:
            return None
        try:
            return _decode(lineage, codec.loads(raw, name))
        except (KeyError, ValueError, TypeError) as exc:
            raise StoreError(f"corrupt dev session state for {lineage}: {exc}") from exc

    def dev_state(self, lineage_id: str) -> DevState | None:
        return self._read_dev(_lineage(lineage_id))

    def _dev_lineage_mtimes(self) -> dict[str, int]:
        """Each lineage with a state file, and that file's mtime in nanoseconds.

        Names that are not ``state-<lineage id>.json`` (temp files, strays) are skipped. A
        file that vanishes while listing is skipped too.
        """
        found: dict[str, int] = {}
        for path in _fs.list_regular(self._root / _DIR):
            lineage = _state_lineage(path.name)
            if lineage is None:
                continue
            stamp = _fs.mtime_ns(path)
            if stamp is not None:
                found[lineage] = stamp
        return found


class DevStoreMixin(DevReadMixin):
    def save_dev_state(self, state: DevState) -> DevState:
        lineage = _lineage(state.lineage_id)
        _fs.ensure_private_dir(self._root / _DIR)
        with _fs.locked(self._root / "store.lock"):
            stored = self._read_dev(lineage)
            if (0 if stored is None else stored.version) != state.version:
                raise DevSessionError("the dev session changed since it was read")
            saved = replace(state, version=state.version + 1)
            _fs.write_atomic(self._dev_path(lineage), codec.dumps(_encode(saved)))
        return saved
