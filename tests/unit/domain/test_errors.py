"""Error catalogue and staged disclosure (ADR 0008, Q50-Q52, D10)."""

import pytest

from cheeseless.domain.errors import (
    CATALOGUE,
    PRE_HIDDEN_STAGE_CODES,
    STDERR_TAIL_LIMIT,
    BudgetState,
    ErrorCode,
    ErrorDetails,
    gate_error,
    tail,
)


def test_every_code_has_a_catalogue_entry_with_text() -> None:
    assert set(CATALOGUE) == set(ErrorCode)
    for entry in CATALOGUE.values():
        assert entry.reason and entry.hint


def test_gate_error_uses_trusted_templates_and_docs_anchor() -> None:
    err = gate_error(ErrorCode.BUILD_FAILED, stderr_tail="boom")
    assert err.reason == CATALOGUE[ErrorCode.BUILD_FAILED].reason
    assert err.docs == "docs/errors.md#build_failed"
    assert err.pre_hidden_stderr_tail == "boom"
    assert err.retryable


@pytest.mark.parametrize("code", sorted(set(ErrorCode) - PRE_HIDDEN_STAGE_CODES, key=str))
def test_stderr_tail_is_refused_outside_pre_hidden_stages(code: ErrorCode) -> None:
    with pytest.raises(ValueError):
        gate_error(code, stderr_tail="leak")


def test_stderr_tail_is_truncated_to_its_end() -> None:
    err = gate_error(ErrorCode.STARTUP_CRASH, stderr_tail="x" * 10 + "y" * STDERR_TAIL_LIMIT)
    assert err.pre_hidden_stderr_tail == "y" * STDERR_TAIL_LIMIT


def test_tail_never_splits_a_multibyte_character() -> None:
    text = "é" * STDERR_TAIL_LIMIT
    cut = tail(text)
    assert len(cut.encode()) <= STDERR_TAIL_LIMIT
    assert set(cut) == {"é"}


def test_budget_state_keeps_unknown_distinct_from_zero() -> None:
    assert BudgetState().remaining is None
    assert BudgetState(remaining=0, limit=30).remaining == 0
    with pytest.raises(ValueError):
        BudgetState(remaining=-1)
    with pytest.raises(ValueError):
        BudgetState(remaining=31, limit=30)


@pytest.mark.parametrize(
    "details",
    [
        {"criterion_id": "Bad Id"},
        {"agent_written_path": "/abs/path"},
        {"agent_written_path": "../escape"},
        {"agent_written_path": "a\nb"},
        {"host": "has space"},
    ],
)
def test_error_details_are_whitelisted_shapes(details: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        ErrorDetails(**details)


def test_details_accept_valid_values() -> None:
    d = ErrorDetails(
        criterion_id="score",
        agent_written_path="src/toy/cli.py",
        budget=BudgetState(remaining=3, limit=30),
        host="pypi.org",
    )
    assert gate_error(ErrorCode.RUN_BUDGET_EXHAUSTED, details=d).details == d


def test_code_must_be_an_error_code() -> None:
    with pytest.raises(ValueError):
        gate_error(9)  # type: ignore[arg-type]


def test_domain_codes_mirror_the_proto_enum() -> None:
    from cheeseless.adapters.inbound.proto import error_pb2

    wire = {v.name.removeprefix("ERROR_CODE_") for v in error_pb2.ErrorCode.DESCRIPTOR.values}
    assert wire - {"UNSPECIFIED"} == {c.name for c in ErrorCode}


def test_description_only_on_criterion_failed() -> None:
    with pytest.raises(ValueError):
        gate_error(ErrorCode.BUILD_FAILED, public_description="x")
