"""The host-only ``dev-events`` command (S3): NDJSON out, read-only, bounded follow."""

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from io import StringIO

import pytest

from cheeseless.adapters.inbound.cli.dev_events import run_dev_events
from cheeseless.adapters.inbound.cli.main import build_parser
from cheeseless.domain.dev_session import DevPhase, DevState
from cheeseless.domain.dispute import DisputeRecord
from cheeseless.domain.reference_attempt import ReferenceAttempt
from cheeseless.ports.store import StoreError

LINEAGE = "demo"
DIGEST = "a" * 64
START = datetime(2026, 11, 19, 10, 0, 0, tzinfo=UTC)


@dataclass
class Clock:
    """A fake clock: ``sleep`` advances it, so heartbeats are deterministic."""

    now_value: datetime = START
    slept: list[float] = field(default_factory=list)

    def now(self) -> datetime:
        return self.now_value

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now_value += timedelta(seconds=seconds)


@dataclass
class ScriptedObservation:
    """Returns one state per poll; the last one repeats. Read methods only."""

    states: list[DevState | None]
    error: Exception | None = None
    polls: int = 0

    def dev_state(self, lineage_id: str) -> DevState | None:
        self.polls += 1
        if self.error is not None:
            raise self.error
        index = min(self.polls - 1, len(self.states) - 1)
        return self.states[index]

    def reference_attempts(self, lineage_id: str | None = None) -> tuple[ReferenceAttempt, ...]:
        return ()

    def disputes(self) -> tuple[DisputeRecord, ...]:
        return ()


PROPOSAL = "c" * 64


def _state(phase: DevPhase, version: int = 1) -> DevState:
    # Frozen phases (tests, implement, done) need a proposal id; SPEC must not have one.
    proposal = None if phase is DevPhase.SPEC else PROPOSAL
    return DevState(
        lineage_id=LINEAGE,
        phase=phase,
        spec_digest=DIGEST,
        proposal_id=proposal,
        version=version,
    )


def _lines(text: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in text.splitlines()]


def _run(
    observation: ScriptedObservation,
    *,
    follow: bool = False,
    limit: int | None = None,
    clock: Clock | None = None,
) -> tuple[int, str, Clock]:
    active = clock or Clock()
    out = StringIO()
    code = run_dev_events(
        observation,
        LINEAGE,
        follow=follow,
        interval=1.0,
        out=out,
        now=active.now,
        sleep=active.sleep,
        limit=limit,
    )
    return code, out.getvalue(), active


def test_once_prints_one_snapshot_line_and_exits_zero() -> None:
    code, text, _ = _run(ScriptedObservation([_state(DevPhase.TESTS)]))

    lines = _lines(text)
    assert code == 0
    assert len(lines) == 1
    assert lines[0]["kind"] == "snapshot"
    assert lines[0]["v"] == 1
    assert lines[0]["lineage"] == LINEAGE
    assert lines[0]["observed_at"] == "2026-11-19T10:00:00Z"
    assert lines[0]["stage"] == "lock"  # a frozen proposal in the tests phase is the lock stage


def test_once_for_an_unknown_lineage_prints_nothing_and_exits_zero() -> None:
    code, text, _ = _run(ScriptedObservation([None]))

    assert code == 0
    assert text == ""


def test_once_on_a_corrupt_store_prints_the_fixed_error_line_and_exits_one() -> None:
    code, text, _ = _run(ScriptedObservation([], error=StoreError("/home/x/secret")))

    lines = _lines(text)
    assert code == 1
    assert lines == [
        {"v": 1, "kind": "error", "observed_at": "2026-11-19T10:00:00Z", "lineage": LINEAGE}
    ]
    assert "secret" not in text


def test_follow_emits_only_one_change_line_per_observed_change_and_ends_on_done() -> None:
    observation = ScriptedObservation(
        [
            _state(DevPhase.TESTS, version=1),
            _state(DevPhase.TESTS, version=1),  # same view: no line
            _state(DevPhase.IMPLEMENT, version=3),
            _state(DevPhase.DONE, version=4),
        ]
    )

    code, text, _ = _run(observation, follow=True, limit=20)

    lines = _lines(text)
    assert code == 0
    assert [line["kind"] for line in lines] == ["snapshot", "change", "end"]
    assert lines[1]["stage"] == "code"
    assert lines[-1]["stage"] == "done"
    assert observation.polls == 4


def test_follow_on_an_already_terminal_lineage_ends_at_once() -> None:
    code, text, _ = _run(ScriptedObservation([_state(DevPhase.ABORTED)]), follow=True, limit=5)

    lines = _lines(text)
    assert code == 0
    assert [line["kind"] for line in lines] == ["end"]
    assert lines[0]["stage"] == "aborted"


def test_follow_on_a_corrupt_store_prints_one_error_line_and_exits_one() -> None:
    # An error line has no view, so it never changes between polls: follow must stop on it
    # rather than wait forever for a change that cannot come.
    observation = ScriptedObservation([], error=StoreError("/home/x/secret"))

    code, text, _ = _run(observation, follow=True, limit=5)

    assert code == 1
    assert [line["kind"] for line in _lines(text)] == ["error"]
    assert observation.polls == 1
    assert "secret" not in text


def test_follow_writes_a_heartbeat_after_fifteen_quiet_seconds() -> None:
    observation = ScriptedObservation([_state(DevPhase.TESTS)])

    _, text, _ = _run(observation, follow=True, limit=17)

    kinds = [line["kind"] for line in _lines(text)]
    assert kinds == ["snapshot", "heartbeat"]


def test_follow_sleeps_for_the_interval_between_polls() -> None:
    observation = ScriptedObservation([_state(DevPhase.TESTS)])

    _, _, clock = _run(observation, follow=True, limit=3)

    assert clock.slept == [1.0, 1.0, 1.0]


def test_follow_waits_while_the_lineage_has_no_state_yet() -> None:
    observation = ScriptedObservation([None, None, _state(DevPhase.SPEC)])

    code, text, _ = _run(observation, follow=True, limit=10)

    lines = _lines(text)
    assert code == 0
    assert [line["kind"] for line in lines] == ["snapshot"]
    assert observation.polls == 10  # SPEC is not terminal, so the bound ends the loop


def test_parser_accepts_the_documented_flags() -> None:
    args = build_parser().parse_args(
        [
            "dev-events",
            "--lineage",
            "demo",
            "--follow",
            "--interval",
            "2.5",
            "--state-dir",
            "state-dir",  # a relative path; the parser only records it, nothing is created
        ]
    )

    assert args.command == "dev-events"
    assert args.lineage == "demo"
    assert args.follow is True
    assert args.interval == 2.5


@pytest.mark.parametrize(
    "argv",
    [
        ["dev-events", "--lineage", "demo", "--as", "author"],
        ["dev-events", "--lineage", "demo", "--bogus"],
        ["dev-events"],
        ["dev-events", "--lineage", "demo", "--once", "--follow"],
    ],
)
def test_unknown_write_or_incomplete_flags_are_rejected_with_exit_two(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as info:
        build_parser().parse_args(argv)

    assert info.value.code == 2
