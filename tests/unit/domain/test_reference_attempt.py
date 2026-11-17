"""ADR 0014: the reference attempt record and its state machine (pure, no I/O)."""

import dataclasses
import itertools

import pytest

from cheeseless.domain.reference_attempt import (
    ReferenceAttempt,
    ReferenceMode,
    ReferenceState,
    ReferenceTransitionError,
    transition,
)

S = ReferenceState
DIGEST = "a" * 64
SESSION = "0123456789abcdef0123456789abcdef"


def attempt(state: S = S.RUNNING, **over: object) -> ReferenceAttempt:
    fields: dict[str, object] = {
        "attempt_id": "att-1",
        "session_id": SESSION,
        "generation": 0,
        "spec_digest": DIGEST,
        "mode": ReferenceMode.TUI,
        "attached": False,
        "state": state,
    }
    return ReferenceAttempt(**{**fields, **over})  # type: ignore[arg-type]


_ALLOWED = {
    (S.RUNNING, S.STOPPING),
    (S.STOPPING, S.DONE),
    (S.RUNNING, S.FAILED),
    (S.STOPPING, S.FAILED),
    (S.RUNNING, S.CANCELLED),
    (S.STOPPING, S.CANCELLED),
}


@pytest.mark.parametrize(("old", "new"), list(itertools.product(S, S)))
def test_only_the_adr_transitions_are_allowed(old, new):
    if (old, new) in _ALLOWED:
        assert transition(attempt(old), new).state is new
    else:
        with pytest.raises(ReferenceTransitionError):
            transition(attempt(old), new)


def test_transition_returns_a_new_object_and_never_mutates():
    before = attempt()
    after = transition(before, S.STOPPING)
    assert before.state is S.RUNNING and after is not before
    assert dataclasses.replace(after, state=S.RUNNING) == before


def test_the_record_is_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        attempt().state = S.DONE  # type: ignore[misc]


@pytest.mark.parametrize(
    "bad",
    [
        {"attempt_id": "../x"},
        {"attempt_id": ""},
        {"attempt_id": "A/b"},
        {"session_id": "not-a-session"},
        {"spec_digest": "short"},
        {"generation": -1},
        {"generation": True},
        {"attached": 1},
    ],
)
def test_invalid_fields_are_refused(bad):
    with pytest.raises(ValueError):
        attempt(**bad)
