"""The ``cheeseless dev`` phase state machine (ADR 0013)."""

import pytest

from cheeseless.domain.dev_session import (
    MAX_HOLDOUT_FAILURES,
    DevPhase,
    DevSessionError,
    DevState,
    ResumeTarget,
    resume_target,
)

SPEC = "1" * 64
SPEC2 = "2" * 64
PID = "d" * 64
AUTHOR = "0b7d4c1e-2f3a-4b5c-8d9e-0f1a2b3c4d5e"
IMPL = "9a8b7c6d-5e4f-4a3b-9c2d-1e0f9a8b7c6d"


def _implementing() -> DevState:
    return (
        DevState("demo")
        .author_started(AUTHOR)
        .spec_done(SPEC)
        .proposed(PID)
        .frozen(PID)
        .implementer_started(IMPL)
    )


def test_a_new_session_starts_in_the_spec_phase() -> None:
    state = DevState("demo")
    assert state.phase is DevPhase.SPEC
    assert state.spec_digest is None and state.proposal_id is None
    assert state.author_session is None and state.implementer_session is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"lineage_id": "Not Valid"},
        {"lineage_id": "demo", "spec_digest": "xyz"},
        {"lineage_id": "demo", "author_session": "not-a-uuid"},
        {"lineage_id": "demo", "holdout_failures": -1},
        {"lineage_id": "demo", "version": -1},
        {"lineage_id": "demo", "phase": DevPhase.TESTS},
        {"lineage_id": "demo", "phase": DevPhase.IMPLEMENT, "spec_digest": SPEC},
        {"lineage_id": "demo", "implementer_session": IMPL},
        {"lineage_id": "demo", "holdout_failures": MAX_HOLDOUT_FAILURES + 1},
    ],
)
def test_invalid_states_are_refused(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        DevState(**kwargs)  # type: ignore[arg-type]


def test_spec_done_moves_to_tests_and_binds_the_digest() -> None:
    state = DevState("demo").author_started(AUTHOR).spec_done(SPEC)
    assert state.phase is DevPhase.TESTS and state.spec_digest == SPEC
    assert state.author_session == AUTHOR


def test_spec_done_is_only_allowed_in_the_spec_phase() -> None:
    with pytest.raises(DevSessionError):
        DevState("demo").spec_done(SPEC).spec_done(SPEC)


def test_a_spec_change_in_the_tests_phase_drops_the_proposal() -> None:
    state = DevState("demo").spec_done(SPEC).proposed(PID).spec_changed(SPEC2)
    assert state.spec_digest == SPEC2 and state.proposal_id is None
    assert state.phase is DevPhase.TESTS


def test_an_unchanged_spec_keeps_the_proposal() -> None:
    state = DevState("demo").spec_done(SPEC).proposed(PID)
    assert state.spec_changed(SPEC) == state


def test_freezing_needs_the_recorded_proposal() -> None:
    tests = DevState("demo").spec_done(SPEC)
    with pytest.raises(DevSessionError):
        tests.frozen(PID)
    with pytest.raises(DevSessionError):
        tests.proposed(PID).frozen("e" * 64)
    frozen = tests.proposed(PID).frozen(PID)
    assert frozen.phase is DevPhase.IMPLEMENT and frozen.holdout_failures == 0


def test_the_author_session_cannot_be_set_after_the_freeze() -> None:
    with pytest.raises(DevSessionError):
        _implementing().author_started(AUTHOR)


def test_the_implementer_session_is_only_set_while_implementing() -> None:
    with pytest.raises(DevSessionError):
        DevState("demo").spec_done(SPEC).implementer_started(IMPL)


def test_three_holdout_fails_require_a_refreeze() -> None:
    state = _implementing()
    for _ in range(MAX_HOLDOUT_FAILURES - 1):
        state = state.holdout_failed()
        assert state.judgment_allowed
    state = state.holdout_failed()
    assert state.needs_refreeze and not state.judgment_allowed
    with pytest.raises(DevSessionError):
        state.holdout_failed()


def test_a_refreeze_returns_to_the_author_with_a_clean_slate() -> None:
    state = _implementing()
    for _ in range(MAX_HOLDOUT_FAILURES):
        state = state.holdout_failed()
    again = state.refreeze()
    assert again.phase is DevPhase.TESTS
    assert again.proposal_id is None and again.holdout_failures == 0
    assert again.implementer_session is None
    assert again.author_session == AUTHOR and again.spec_digest == SPEC


def test_a_refreeze_is_refused_before_three_fails() -> None:
    with pytest.raises(DevSessionError):
        _implementing().holdout_failed().refreeze()


def test_a_pass_ends_the_session() -> None:
    done = _implementing().passed()
    assert done.phase is DevPhase.DONE and done.terminal
    with pytest.raises(DevSessionError):
        done.aborted()


def test_a_pass_after_three_fails_is_refused() -> None:
    state = _implementing()
    for _ in range(MAX_HOLDOUT_FAILURES):
        state = state.holdout_failed()
    with pytest.raises(DevSessionError):
        state.passed()


def test_an_empty_pool_blocks_until_runs_are_granted() -> None:
    blocked = _implementing().pool_empty()
    assert blocked.phase is DevPhase.BLOCKED
    resumed = blocked.runs_granted()
    assert resumed.phase is DevPhase.IMPLEMENT and resumed.implementer_session is None
    with pytest.raises(DevSessionError):
        DevState("demo").runs_granted()


def test_a_rejected_dispute_starts_a_fresh_implementer() -> None:
    disputed = _implementing().holdout_failed().disputed()
    assert disputed.phase is DevPhase.DISPUTED
    rejected = disputed.dispute_rejected()
    assert rejected.phase is DevPhase.IMPLEMENT
    assert rejected.implementer_session is None and rejected.holdout_failures == 1


def test_an_upheld_dispute_returns_to_the_author_for_a_refreeze() -> None:
    upheld = _implementing().holdout_failed().disputed().dispute_upheld()
    assert upheld.phase is DevPhase.TESTS
    assert upheld.proposal_id is None and upheld.holdout_failures == 0
    assert upheld.implementer_session is None


def test_dispute_rulings_need_a_dispute() -> None:
    with pytest.raises(DevSessionError):
        _implementing().dispute_rejected()
    with pytest.raises(DevSessionError):
        _implementing().dispute_upheld()
    with pytest.raises(DevSessionError):
        DevState("demo").disputed()


def test_abort_is_allowed_from_any_open_phase() -> None:
    assert DevState("demo").aborted().phase is DevPhase.ABORTED
    assert _implementing().pool_empty().aborted().terminal


def test_resume_in_an_author_phase_returns_the_author_session() -> None:
    assert resume_target(DevState("demo")) == ResumeTarget("author", None)
    spec = DevState("demo").author_started(AUTHOR)
    assert resume_target(spec) == ResumeTarget("author", AUTHOR)
    assert resume_target(spec.spec_done(SPEC)) == ResumeTarget("author", AUTHOR)


def test_resume_in_the_implement_phase_always_starts_fresh() -> None:
    assert resume_target(_implementing()) == ResumeTarget("implementer", None)


@pytest.mark.parametrize(
    "state",
    [
        _implementing().pool_empty(),
        _implementing().disputed(),
        _implementing().passed(),
        DevState("demo").aborted(),
        _implementing().holdout_failed().holdout_failed().holdout_failed(),
    ],
    ids=["blocked", "disputed", "done", "aborted", "needs-refreeze"],
)
def test_resume_is_refused_where_the_user_must_decide(state: DevState) -> None:
    with pytest.raises(DevSessionError):
        resume_target(state)
