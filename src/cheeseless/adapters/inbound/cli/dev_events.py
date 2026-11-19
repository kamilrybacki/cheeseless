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
    error_event,
    event,
)
from cheeseless.ports.dev_observe import DevObservation

HEARTBEAT_SECONDS = 15.0
EXIT_OK = 0
EXIT_ERROR_LINE = 1
EXIT_NO_SUCH_LINEAGE = 3
EXIT_INTERRUPTED = 130
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

    ``--once`` prints one snapshot: exit 1 when it is a fixed error line, 3 (and no output)
    when the lineage has no state, 0 otherwise. ``--follow`` waits for the lineage to appear,
    so a viewer can be opened before the session starts; it then prints a snapshot, a change
    line whenever the view changes and a heartbeat after 15 quiet seconds. It exits 0 once
    the phase is terminal and 1 on an error line, including a lineage that vanishes after it
    was seen. ``limit`` bounds the number of polls (tests only).

    A closed pipe exits 0 and an interrupt exits 130, both silently. Any other OS or value
    error becomes the fixed error line and exit 1: no message or path is ever printed.
    """
    try:
        return _run(port, lineage, follow, interval, out, now, sleep, limit)
    except BrokenPipeError:
        return EXIT_OK
    except KeyboardInterrupt:
        return EXIT_INTERRUPTED
    except (OSError, ValueError):
        return _fixed_error(out, lineage, now)


def _run(
    port: DevObservation,
    lineage: str,
    follow: bool,
    interval: float,
    out: TextIO,
    now: Callable[[], datetime],
    sleep: Callable[[float], None],
    limit: int | None,
) -> int:
    if not follow:
        made = read_event(port, lineage, EventKind.SNAPSHOT, _stamp(now()))
        if made is None:
            return EXIT_NO_SUCH_LINEAGE
        _emit(out, made)
        return EXIT_ERROR_LINE if made.kind is EventKind.ERROR else EXIT_OK
    return _follow(port, lineage, interval, out, now, sleep, limit)


def _fixed_error(out: TextIO, lineage: str, now: Callable[[], datetime]) -> int:
    try:
        _emit(out, error_event(_stamp(now()), lineage))
    except OSError:
        pass  # the output itself is broken; the exit code is all that is left to report
    return EXIT_ERROR_LINE


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
                return EXIT_OK
            if made.kind is EventKind.ERROR:
                # An error line carries no view, so no later poll can change it: stop here.
                return EXIT_ERROR_LINE
        elif made is None and last is not None:
            # The state was seen and is gone: the store changed under us. Never keep waiting.
            _emit(out, error_event(stamp, lineage))
            return EXIT_ERROR_LINE
        elif last is not None and last.view is not None and _quiet_long_enough(last_sent, now()):
            _emit(out, event(EventKind.HEARTBEAT, stamp, last.lineage, last.view))
            last_sent = now()
        sleep(interval)
    return EXIT_OK


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
