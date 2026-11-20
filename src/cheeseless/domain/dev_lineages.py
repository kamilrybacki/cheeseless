"""The dev-lineages listing: which lineages exist, their stage and last change. Pure.

One compact JSON line (``docs/schemas/dev-lineages-v1.json``) for a host-side picker. It
carries only lineage ids, ``Stage`` values and UTC times; no path, digest, session id or
note can reach it, because every field is checked against a closed shape here.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from cheeseless.domain import ids
from cheeseless.domain.dev_events import Stage

SCHEMA_VERSION = 1
MAX_LINEAGES = 100
STORE_ERROR_LINE = '{"schema":1,"error":"store"}'


def _aware(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("a lineage time needs a timezone")
    return value


@dataclass(frozen=True, slots=True)
class LineageStamp:
    """A lineage that has persisted state, and when it last changed on disk."""

    lineage_id: str
    updated_at: datetime

    def __post_init__(self) -> None:
        ids.lineage_id(self.lineage_id)
        _aware(self.updated_at)


@dataclass(frozen=True, slots=True)
class LineageEntry:
    """One row of the listing."""

    lineage_id: str
    stage: Stage
    updated_at: datetime

    def __post_init__(self) -> None:
        ids.lineage_id(self.lineage_id)
        if not isinstance(self.stage, Stage):
            raise ValueError(f"invalid stage: {self.stage!r}")
        _aware(self.updated_at)


def newest_first(stamps: Iterable[LineageStamp]) -> tuple[LineageStamp, ...]:
    """Newest change first; equal times fall back to the lineage id, so order is stable."""
    by_id = sorted(stamps, key=lambda stamp: stamp.lineage_id)
    return tuple(sorted(by_id, key=lambda stamp: stamp.updated_at, reverse=True))


def rfc3339(value: datetime) -> str:
    """UTC, whole seconds: the only time format the listing uses."""
    return _aware(value).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def encode_listing(entries: Sequence[LineageEntry]) -> str:
    """The listing as one compact JSON line, in the given order, without a newline."""
    if len(entries) > MAX_LINEAGES:
        raise ValueError(f"a listing holds at most {MAX_LINEAGES} lineages")
    rows = [
        {"lineage": e.lineage_id, "stage": e.stage.value, "updated_at": rfc3339(e.updated_at)}
        for e in entries
    ]
    return json.dumps({"schema": SCHEMA_VERSION, "lineages": rows}, separators=(",", ":"))
