"""Dispute domain rules (ADR 0009, stage g)."""

import pytest

from cheeseless.domain.dispute import (
    DEFAULT_CAP,
    NOTE_LIMIT,
    DisputeCode,
    DisputePolicy,
    check_note,
    refusal,
)
from cheeseless.domain.errors import CATALOGUE, ErrorCode


def test_policy_is_off_by_default_with_cap_three() -> None:
    policy = DisputePolicy()
    assert policy.enabled is False
    assert policy.cap == DEFAULT_CAP == 3


@pytest.mark.parametrize("cap", [0, -1, 1000, True, "3"])
def test_policy_rejects_bad_cap(cap: object) -> None:
    with pytest.raises(ValueError):
        DisputePolicy(enabled=True, cap=cap)  # type: ignore[arg-type]


def test_codes_are_a_closed_set() -> None:
    assert {c.value for c in DisputeCode} == {
        "contradicts_spec",
        "untestable",
        "ambiguous_spec",
        "other",
    }


def test_note_cap_and_control_characters() -> None:
    assert check_note("") == ""
    assert check_note("x" * NOTE_LIMIT) == "x" * NOTE_LIMIT
    for bad in ("x" * (NOTE_LIMIT + 1), "a\nb", "a\x00b", "a\x1b[31m", "a‮b", "a\x7fb"):
        with pytest.raises(ValueError):
            check_note(bad)
    with pytest.raises(ValueError):
        check_note(b"bytes")  # type: ignore[arg-type]


def test_refusal_order() -> None:
    off = DisputePolicy()
    on = DisputePolicy(enabled=True, cap=2)
    assert (
        refusal(off, lineage_count=0, holds_pass=False, blocked=False) is ErrorCode.DISPUTE_DISABLED
    )
    assert (
        refusal(on, lineage_count=2, holds_pass=True, blocked=True) is ErrorCode.DISPUTE_CAP_REACHED
    )
    assert (
        refusal(on, lineage_count=1, holds_pass=True, blocked=True) is ErrorCode.DISPUTE_NOT_ALLOWED
    )
    assert (
        refusal(on, lineage_count=1, holds_pass=False, blocked=True) is ErrorCode.SESSION_DISPUTED
    )
    assert refusal(on, lineage_count=1, holds_pass=False, blocked=False) is None


def test_new_codes_have_catalogue_entries() -> None:
    for code in (
        ErrorCode.DISPUTE_DISABLED,
        ErrorCode.DISPUTE_CAP_REACHED,
        ErrorCode.DISPUTE_INVALID,
        ErrorCode.DISPUTE_NOT_ALLOWED,
        ErrorCode.SESSION_DISPUTED,
    ):
        assert not CATALOGUE[code].retryable
