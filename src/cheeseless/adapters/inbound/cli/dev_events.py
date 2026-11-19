"""Host-only ``dev-events`` output: NDJSON snapshots, changes and heartbeats (S3).

Read-only by construction: the only port it touches is ``DevObservation``. Nothing here is
exposed on MCP or UDS. Viewers get the same lines from ``--once`` or ``--follow``.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import TextIO

from cheeseless.app.dev_observe import read_event
from cheeseless.domain.dev_events import (
    DevEvent,
    EventKind,
    Stage,
    encode,
    event,
)
from cheeseless.ports.dev_observe import DevObservation

HEARTBEAT_SECONDS = 15.0
TERMINAL_STAGES = frozenset({Stage.DONE, Stage.ABORTED})


def run_dev_events(
    port: DevObservation,
    lineage: str,
    *,
    follow: bool,
    interval: float,
    out: TextIO,
    now: Callable[[], datetime],
    sleep: Callable[[float], None],
    limit: int | None = None,
) -> int:
    """Print the lineage's dev-events and return the exit code.

    ``--once`` prints one snapshot: exit 1 when it is a fixed error line, 0 otherwise (an
    unknown lineage prints nothing). ``--follow`` prints a snapshot, then a change line
    whenever the view changes and a heartbeat after 15 quiet seconds; it exits 0 once the
    phase is terminal. ``limit`` bounds the number of polls (tests only).
    """
    if not follow:
        made = read_event(port, lineage, EventKind.SNAPSHOT, _stamp(now()))
        if made is None:
            return 0
        _emit(out, made)
        return 1 if made.kind is EventKind.ERROR else 0
    return _follow(port, lineage, interval, out, now, sleep, limit)


def _follow(
    port: DevObservation,
    lineage: str,
    interval: float,
    out: TextIO,
    now: Callable[[], datetime],
    sleep: Callable[[float], None],
    limit: int | None,
) -> int:
    last: DevEvent | None = None
    last_sent = now()
    polls = 0
    while limit is None or polls < limit:
        polls += 1
        stamp = _stamp(now())
        kind = EventKind.CHANGE if last is not None else EventKind.SNAPSHOT
        made = read_event(port, lineage, kind, stamp)
        if made is not None and _changed(made, last):
            made = _as_end_if_terminal(made, stamp)
            _emit(out, made)
            last, last_sent = made, now()
            if made.kind is EventKind.END:
                return 0
            if made.kind is EventKind.ERROR:
                # An error line carries no view, so no later poll can change it: stop here.
                return 1
        elif last is not None and last.view is not None and _quiet_long_enough(last_sent, now()):
            _emit(out, event(EventKind.HEARTBEAT, stamp, last.lineage, last.view))
            last_sent = now()
        sleep(interval)
    return 0


def _changed(made: DevEvent, last: DevEvent | None) -> bool:
    # Compare the view only: the poll kind (change) never equals the first line's kind
    # (snapshot), so comparing kinds would report every poll as a change.
    if last is None:
        return True
    return made.view != last.view


def _as_end_if_terminal(made: DevEvent, stamp: str) -> DevEvent:
    if made.view is not None and made.view.stage in TERMINAL_STAGES:
        return event(EventKind.END, stamp, made.lineage, made.view)
    return made


def _quiet_long_enough(since: datetime, current: datetime) -> bool:
    return (current - since).total_seconds() >= HEARTBEAT_SECONDS


def _stamp(current: datetime) -> str:
    return current.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _emit(out: TextIO, made: DevEvent) -> None:
    out.write(encode(made) + "\n")
    out.flush()
