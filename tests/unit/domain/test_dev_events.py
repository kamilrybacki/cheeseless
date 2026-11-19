"""The dev-event projection: derivation, closed enums and the wire encoding (S1)."""

import json
from pathlib import Path

import pytest

from cheeseless.domain.dev_events import (
    SCHEMA_VERSION,
    Actor,
    Awaiting,
    DevEvent,
    DevView,
    EventKind,
    Hint,
    ReferenceView,
    Stage,
    derive,
    encode,
    error_event,
    event,
)
from cheeseless.domain.dev_session import DevPhase, DevState
from cheeseless.domain.reference_attempt import ReferenceAttempt, ReferenceMode, ReferenceState
from tests.schema_check import violations

LINEAGE = "demo"
DIGEST = "a" * 64
OTHER_DIGEST = "b" * 64
PROPOSAL = "c" * 64
SESSION = "11111111-2222-3333-4444-555555555555"
OBSERVED = "2026-11-19T10:00:00Z"
SCHEMA_PATH = Path(__file__).resolve().parents[3] / "docs" / "schemas" / "dev-event-v1.json"


def _state(**fields: object) -> DevState:
    return DevState(lineage_id=LINEAGE, **fields)  # type: ignore[arg-type]


def _attempt(
    state: ReferenceState = ReferenceState.RUNNING,
    mode: ReferenceMode = ReferenceMode.TUI,
    digest: str = DIGEST,
) -> ReferenceAttempt:
    return ReferenceAttempt(
        attempt_id="ref-1",
        session_id="0" * 32,
        generation=1,
        spec_digest=digest,
        mode=mode,
        attached=False,
        state=state,
        lineage_id=LINEAGE,
    )


def _tests_phase(**fields: object) -> DevState:
    return _state(phase=DevPhase.TESTS, spec_digest=DIGEST, **fields)


# -- derivation ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("state", "stage", "actor", "awaiting", "hint"),
    [
        (_state(), Stage.SPEC, Actor.TEST_WRITER, Awaiting.SPEC_DONE, Hint.RUN_DEV_RESUME),
        (
            _state(
                phase=DevPhase.IMPLEMENT,
                spec_digest=DIGEST,
                proposal_id=PROPOSAL,
                implementer_session=SESSION,
            ),
            Stage.CODE,
            Actor.CODER,
            Awaiting.NONE,
            Hint.NONE,
        ),
        (
            _state(phase=DevPhase.BLOCKED, spec_digest=DIGEST, proposal_id=PROPOSAL),
            Stage.BLOCKED,
            Actor.OPERATOR,
            Awaiting.RUN_GRANT,
            Hint.GRANT_RUNS_OR_ABORT,
        ),
        (
            _state(phase=DevPhase.DISPUTED, spec_digest=DIGEST, proposal_id=PROPOSAL),
            Stage.DISPUTE,
            Actor.OPERATOR,
            Awaiting.DISPUTE_RESOLUTION,
            Hint.RUN_DISPUTE_RESOLVE,
        ),
        (
            _state(phase=DevPhase.DONE, spec_digest=DIGEST, proposal_id=PROPOSAL),
            Stage.DONE,
            Actor.NONE,
            Awaiting.NONE,
            Hint.NONE,
        ),
        (_state(phase=DevPhase.ABORTED), Stage.ABORTED, Actor.NONE, Awaiting.NONE, Hint.NONE),
    ],
    ids=["spec", "implement", "blocked", "disputed", "done", "aborted"],
)
def test_phase_maps_to_one_stage(
    state: DevState, stage: Stage, actor: Actor, awaiting: Awaiting, hint: Hint
) -> None:
    view = derive(state, None, open_disputes=0)

    assert (view.stage, view.actor, view.awaiting, view.hint) == (stage, actor, awaiting, hint)


def test_tests_phase_without_reference_waits_for_the_reference() -> None:
    view = derive(_tests_phase(), None, open_disputes=0)

    assert view.stage is Stage.TESTS
    assert view.actor is Actor.TEST_WRITER
    assert view.awaiting is Awaiting.REFERENCE_DONE
    assert view.reference is ReferenceView.NONE


def test_tests_phase_with_done_reference_has_nothing_to_await() -> None:
    view = derive(_tests_phase(), _attempt(ReferenceState.DONE), open_disputes=0)

    assert view.stage is Stage.TESTS
    assert view.awaiting is Awaiting.NONE
    assert view.reference is ReferenceView.DONE


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (ReferenceMode.TUI, ReferenceView.RUNNING_TUI),
        (ReferenceMode.HEADLESS, ReferenceView.RUNNING_HEADLESS),
    ],
)
@pytest.mark.parametrize("state", [ReferenceState.RUNNING, ReferenceState.STOPPING])
def test_running_reference_is_its_own_stage(
    mode: ReferenceMode, expected: ReferenceView, state: ReferenceState
) -> None:
    view = derive(_tests_phase(), _attempt(state, mode), open_disputes=0)

    assert view.stage is Stage.REFERENCE
    assert view.actor is Actor.REFERENCE_CODER
    assert view.reference is expected


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (ReferenceState.FAILED, ReferenceView.FAILED),
        (ReferenceState.CANCELLED, ReferenceView.CANCELLED),
    ],
)
def test_failed_or_cancelled_reference_keeps_the_tests_stage_waiting(
    state: ReferenceState, expected: ReferenceView
) -> None:
    view = derive(_tests_phase(), _attempt(state), open_disputes=0)

    assert view.stage is Stage.TESTS
    assert view.awaiting is Awaiting.REFERENCE_DONE
    assert view.reference is expected


def test_proposal_in_tests_phase_is_the_lock_stage() -> None:
    view = derive(_tests_phase(proposal_id=PROPOSAL), _attempt(ReferenceState.DONE), 0)

    assert view.stage is Stage.LOCK
    assert view.actor is Actor.OPERATOR
    assert view.awaiting is Awaiting.LOCK_APPROVAL
    assert view.hint is Hint.RUN_FREEZE_APPROVE


def test_reference_of_another_spec_is_ignored() -> None:
    view = derive(_tests_phase(), _attempt(ReferenceState.DONE, digest=OTHER_DIGEST), 0)

    assert view.reference is ReferenceView.NONE
    assert view.awaiting is Awaiting.REFERENCE_DONE


def test_reference_is_not_shown_outside_the_tests_phase() -> None:
    state = _state(
        phase=DevPhase.IMPLEMENT,
        spec_digest=DIGEST,
        proposal_id=PROPOSAL,
        implementer_session=SESSION,
    )

    view = derive(state, _attempt(ReferenceState.DONE), open_disputes=0)

    assert view.reference is ReferenceView.NONE
    assert view.stage is Stage.CODE


def test_holdout_limit_asks_for_refreeze_or_abort() -> None:
    state = _state(
        phase=DevPhase.IMPLEMENT,
        spec_digest=DIGEST,
        proposal_id=PROPOSAL,
        implementer_session=SESSION,
        holdout_failures=3,
    )

    view = derive(state, None, open_disputes=0)

    assert view.stage is Stage.CODE
    assert view.awaiting is Awaiting.REFREEZE_OR_ABORT
    assert view.hint is Hint.REFREEZE_OR_ABORT
    assert view.holdout_failures == 3


def test_counts_and_version_are_carried_through() -> None:
    state = _state(
        phase=DevPhase.DISPUTED,
        spec_digest=DIGEST,
        proposal_id=PROPOSAL,
        holdout_failures=2,
        version=7,
    )

    view = derive(state, None, open_disputes=2)

    assert view == DevView(
        stage=Stage.DISPUTE,
        actor=Actor.OPERATOR,
        awaiting=Awaiting.DISPUTE_RESOLUTION,
        reference=ReferenceView.NONE,
        holdout_failures=2,
        open_disputes=2,
        state_version=7,
        hint=Hint.RUN_DISPUTE_RESOLVE,
    )


def test_every_phase_derives_without_error() -> None:
    for phase in DevPhase:
        spec = None if phase in (DevPhase.SPEC, DevPhase.ABORTED) else DIGEST
        frozen = (DevPhase.SPEC, DevPhase.TESTS, DevPhase.ABORTED)
        proposal = None if phase in frozen else PROPOSAL
        state = _state(phase=phase, spec_digest=spec, proposal_id=proposal)

        for attempt in (None, *(_attempt(s) for s in ReferenceState)):
            assert isinstance(derive(state, attempt, 0), DevView)


def test_the_view_has_no_free_text_field() -> None:
    names = set(DevView.__dataclass_fields__)

    assert names == {
        "stage",
        "actor",
        "awaiting",
        "reference",
        "holdout_failures",
        "open_disputes",
        "state_version",
        "hint",
    }


# -- encoding ------------------------------------------------------------------


def test_encode_emits_one_closed_line_per_event() -> None:
    view = derive(_tests_phase(version=4), _attempt(ReferenceState.RUNNING), open_disputes=0)

    line = encode(event(EventKind.SNAPSHOT, OBSERVED, LINEAGE, view))

    assert "\n" not in line
    assert json.loads(line) == {
        "v": SCHEMA_VERSION,
        "kind": "snapshot",
        "observed_at": OBSERVED,
        "lineage": LINEAGE,
        "stage": "reference",
        "actor": "reference_coder",
        "awaiting": "none",
        "reference": "running_tui",
        "holdout_failures": 0,
        "open_disputes": 0,
        "state_version": 4,
        "hint": "none",
    }


def test_error_line_carries_no_state() -> None:
    line = encode(error_event(OBSERVED, LINEAGE))

    assert json.loads(line) == {
        "v": SCHEMA_VERSION,
        "kind": "error",
        "observed_at": OBSERVED,
        "lineage": LINEAGE,
    }


def test_observation_time_must_be_rfc3339_utc() -> None:
    view = derive(_state(), None, 0)

    for bad in ("2026-11-19 10:00:00", "2026-11-19T10:00:00+02:00", "yesterday", ""):
        with pytest.raises(ValueError, match="observed_at"):
            event(EventKind.CHANGE, bad, LINEAGE, view)


def test_lineage_is_validated() -> None:
    with pytest.raises(ValueError, match="lineage"):
        error_event(OBSERVED, "Not A Lineage/../x")


def test_event_kinds_are_closed() -> None:
    assert [kind.value for kind in EventKind] == [
        "snapshot",
        "change",
        "heartbeat",
        "end",
        "error",
    ]


def test_event_is_a_frozen_value() -> None:
    made = event(EventKind.END, OBSERVED, LINEAGE, derive(_state(), None, 0))

    assert isinstance(made, DevEvent)
    with pytest.raises(AttributeError):
        made.kind = EventKind.CHANGE  # type: ignore[misc]


# -- schema --------------------------------------------------------------------


def _schema() -> dict[str, object]:
    loaded: dict[str, object] = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return loaded


def _enum_of(schema: dict[str, object], field: str) -> list[str]:
    properties = schema["properties"]
    assert isinstance(properties, dict)
    members = properties[field]["enum"]  # type: ignore[index]
    assert isinstance(members, list)
    return members


def test_schema_enums_match_the_domain() -> None:
    schema = _schema()

    assert _enum_of(schema, "stage") == [m.value for m in Stage]
    assert _enum_of(schema, "actor") == [m.value for m in Actor]
    assert _enum_of(schema, "awaiting") == [m.value for m in Awaiting]
    assert _enum_of(schema, "reference") == [m.value for m in ReferenceView]
    assert _enum_of(schema, "hint") == [m.value for m in Hint]
    assert _enum_of(schema, "kind") == [m.value for m in EventKind]


def test_schema_is_closed_and_versioned() -> None:
    schema = _schema()

    assert schema["additionalProperties"] is False
    assert schema["properties"]["v"] == {"const": SCHEMA_VERSION}  # type: ignore[index]


@pytest.mark.parametrize("state", [None, _tests_phase(version=2)])
def test_every_encoded_line_validates(state: DevState | None) -> None:
    schema = _schema()
    view = derive(state or _state(), _attempt(), 1)
    lines = [
        encode(event(EventKind.SNAPSHOT, OBSERVED, LINEAGE, view)),
        encode(event(EventKind.HEARTBEAT, OBSERVED, LINEAGE, view)),
        encode(event(EventKind.END, OBSERVED, LINEAGE, view)),
        encode(error_event(OBSERVED, LINEAGE)),
    ]

    for line in lines:
        assert violations(schema, json.loads(line)) == []


def test_schema_refuses_a_free_text_field() -> None:
    line = json.loads(encode(error_event(OBSERVED, LINEAGE)))
    line["note"] = "CANARY-NOTE-x"

    assert violations(_schema(), line) != []


def test_schema_refuses_an_out_of_range_holdout_count() -> None:
    view = derive(
        _state(
            phase=DevPhase.IMPLEMENT,
            spec_digest=DIGEST,
            proposal_id=PROPOSAL,
            implementer_session=SESSION,
        ),
        None,
        0,
    )
    line = json.loads(encode(event(EventKind.CHANGE, OBSERVED, LINEAGE, view)))
    line["holdout_failures"] = 4

    assert violations(_schema(), line) != []


# -- schema bounds -------------------------------------------------------------


def _view(**fields: int) -> DevView:
    base = {"holdout_failures": 0, "open_disputes": 0, "state_version": 0}
    return DevView(
        stage=Stage.SPEC,
        actor=Actor.NONE,
        awaiting=Awaiting.NONE,
        reference=ReferenceView.NONE,
        hint=Hint.NONE,
        **{**base, **fields},
    )


@pytest.mark.parametrize(
    "fields",
    [
        {"holdout_failures": 99},
        {"holdout_failures": -1},
        {"open_disputes": -1},
        {"state_version": -1},
    ],
)
def test_a_view_outside_the_schema_bounds_is_rejected(fields: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        _view(**fields)


def test_derive_rejects_a_state_that_escaped_validation() -> None:
    state = _state()
    object.__setattr__(state, "holdout_failures", 99)

    with pytest.raises(ValueError):
        derive(state, None, 0)


def test_derive_rejects_a_negative_dispute_count() -> None:
    with pytest.raises(ValueError):
        derive(_state(), None, -1)


def test_the_largest_valid_view_still_encodes() -> None:
    line = encode(event(EventKind.SNAPSHOT, OBSERVED, LINEAGE, _view(holdout_failures=3)))

    assert json.loads(line)["holdout_failures"] == 3
