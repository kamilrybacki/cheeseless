"""Applying a gate result to a dev phase (ADR 0013 stage 5, slice 3).

The verifier already decides pass or fail; this only says which domain move that verdict
means for the dev session, and when the loop must stop asking for more attempts.
"""

import pytest

from cheeseless.app.dev_flow import DevFlowError, next_after_result
from cheeseless.domain.dev_session import DevPhase, DevState
from cheeseless.domain.result import CriterionResult, Result
from cheeseless.domain.status import Status

TASK = "a" * 64
TREE = "b" * 40
BASELINE = "c" * 64
DIGEST = "d" * 64


def _result(status: Status) -> Result:
    return Result(
        TASK,
        TREE,
        BASELINE,
        "local",
        "0.1.0",
        status,
        (CriterionResult("c1", status),),
    )


def _implementing() -> DevState:
    return DevState(
        lineage_id="demo",
        phase=DevPhase.IMPLEMENT,
        spec_digest=DIGEST,
        proposal_id="e" * 64,
        author_session="0b7d4c1e-2f3a-4b5c-8d9e-0f1a2b3c4d5e",
        implementer_session=None,
        holdout_failures=0,
        version=1,
    )


def test_a_pass_moves_the_phase_to_done() -> None:
    state = next_after_result(_implementing(), _result(Status.PASS))
    assert state.phase is DevPhase.DONE


def test_a_fail_counts_a_holdout_failure() -> None:
    state = next_after_result(_implementing(), _result(Status.FAIL))
    assert state.phase is DevPhase.IMPLEMENT
    assert state.holdout_failures == 1


def test_a_fail_at_the_limit_stops_the_loop() -> None:
    """The limit is recorded, then every further verdict is refused (judgment_allowed).

    The domain deliberately lets the final FAIL be stored: the author needs to see the
    count that forced the re-freeze. What it forbids is another judgment afterwards.
    """
    from cheeseless.domain.dev_session import MAX_HOLDOUT_FAILURES

    state = _implementing()
    for _ in range(MAX_HOLDOUT_FAILURES):
        state = next_after_result(state, _result(Status.FAIL))
    assert state.holdout_failures == MAX_HOLDOUT_FAILURES
    assert not state.judgment_allowed
    with pytest.raises(DevFlowError, match="too often"):
        next_after_result(state, _result(Status.FAIL))
    with pytest.raises(DevFlowError, match="too often"):
        next_after_result(state, _result(Status.PASS))


def test_a_result_outside_the_implement_phase_is_refused() -> None:
    state = _implementing()
    from dataclasses import replace

    with pytest.raises(DevFlowError, match="implement"):
        next_after_result(replace(state, phase=DevPhase.SPEC), _result(Status.PASS))


def test_the_run_never_charges_the_pool_itself() -> None:
    """Budget accounting belongs to gate_run; this function only reads a verdict."""
    import inspect

    from cheeseless.app import dev_flow

    body = inspect.getsource(dev_flow.next_after_result)
    for forbidden in ("record_attempt", "Pool", "save_"):
        assert forbidden not in body
