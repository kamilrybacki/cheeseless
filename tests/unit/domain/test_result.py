"""Result invariants, canonical encoding and HMAC binding (Q43, P11, AC 17)."""

import dataclasses

import pytest

from cheeseless.domain.result import (
    CriterionResult,
    Result,
    canonical_bytes,
    overall_status,
    sign,
    verify,
)
from cheeseless.domain.status import Status

KEY = b"k" * 32
SHA = "a" * 64
TREE = "b" * 40


def _result(**overrides: object) -> Result:
    fields: dict[str, object] = {
        "task_id": SHA,
        "tree_hash": TREE,
        "baseline_digest": "c" * 64,
        "profile": "local",
        "tool_version": "0.1.0",
        "status": Status.FAIL,
        "criteria": (
            CriterionResult("normalize", Status.PASS),
            CriterionResult("score", Status.FAIL),
        ),
    }
    fields.update(overrides)
    return Result(**fields)  # type: ignore[arg-type]


def test_sign_then_verify() -> None:
    signed = sign(_result(), KEY)
    assert signed.hmac_tag
    assert verify(signed, KEY)


def test_unsigned_or_wrong_key_does_not_verify() -> None:
    assert not verify(_result(), KEY)
    assert not verify(sign(_result(), KEY), b"x" * 32)


def test_short_key_is_rejected() -> None:
    with pytest.raises(ValueError):
        sign(_result(), b"short")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("task_id", "d" * 64),
        ("tree_hash", "e" * 40),
        ("baseline_digest", "f" * 64),
        ("profile", "ci"),
        ("tool_version", "0.1.1"),
    ],
)
def test_tampering_any_scalar_field_breaks_the_tag(field: str, value: str) -> None:
    signed = sign(_result(), KEY)
    forged = dataclasses.replace(signed, **{field: value})
    assert not verify(forged, KEY)


def test_flipping_the_verdict_breaks_the_tag() -> None:
    signed = sign(_result(status=Status.FAIL), KEY)
    forged = dataclasses.replace(
        signed,
        status=Status.PASS,
        criteria=(
            CriterionResult("normalize", Status.PASS),
            CriterionResult("score", Status.PASS),
        ),
    )
    assert not verify(forged, KEY)


@pytest.mark.parametrize(
    "criteria",
    [
        (CriterionResult("score", Status.FAIL), CriterionResult("normalize", Status.PASS)),
        (CriterionResult("score", Status.FAIL),),
        (
            CriterionResult("normalize", Status.PASS),
            CriterionResult("score", Status.FAIL),
            CriterionResult("extra", Status.FAIL),
        ),
    ],
    ids=["reordered", "dropped", "added"],
)
def test_reordering_adding_or_dropping_criteria_breaks_the_tag(
    criteria: tuple[CriterionResult, ...],
) -> None:
    signed = sign(_result(), KEY)
    assert not verify(dataclasses.replace(signed, criteria=criteria), KEY)


def test_encoding_is_length_prefixed() -> None:
    a = _result(profile="ab", tool_version="c")
    b = _result(profile="a", tool_version="bc")
    assert canonical_bytes(a) != canonical_bytes(b)


def test_encoding_ignores_the_tag_and_has_a_domain_prefix() -> None:
    plain = _result()
    assert canonical_bytes(plain) == canonical_bytes(sign(plain, KEY))
    assert canonical_bytes(plain).startswith(b"cheeseless.v1.Result")


@pytest.mark.parametrize(
    "overrides",
    [
        {"task_id": "short"},
        {"task_id": "A" * 64},
        {"tree_hash": "z" * 40},
        {"baseline_digest": ""},
        {"profile": ""},
        {"tool_version": ""},
        {"status": "PASS"},
        {"criteria": (CriterionResult("score", Status.FAIL),) * 2},
        {"status": Status.PASS},
        {"status": Status.PASS, "criteria": ()},
        {"status": Status.FAIL, "criteria": (CriterionResult("normalize", Status.PASS),)},
    ],
    ids=[
        "short-task",
        "uppercase-task",
        "non-hex-tree",
        "empty-baseline",
        "empty-profile",
        "empty-version",
        "raw-string-status",
        "duplicate-criteria",
        "pass-with-failure",
        "pass-without-criteria",
        "fail-without-failure",
    ],
)
def test_constructor_rejects_invalid_results(overrides: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        _result(**overrides)


def test_blocked_result_may_have_no_criteria() -> None:
    assert _result(status=Status.BLOCKED, criteria=()).status is Status.BLOCKED


@pytest.mark.parametrize(
    "criterion",
    [
        {"criterion_id": "", "status": Status.PASS},
        {"criterion_id": "Bad Id", "status": Status.PASS},
        {"criterion_id": "ok", "status": Status.BLOCKED},
        {"criterion_id": "ok", "status": 1},
    ],
)
def test_criterion_result_rejects_invalid_values(criterion: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        CriterionResult(**criterion)  # type: ignore[arg-type]


def test_overall_status() -> None:
    assert overall_status(()) is Status.FAIL
    assert overall_status((CriterionResult("a", Status.PASS),)) is Status.PASS
    mixed = (CriterionResult("a", Status.PASS), CriterionResult("b", Status.FAIL))
    assert overall_status(mixed) is Status.FAIL


def test_tag_must_be_bytes() -> None:
    with pytest.raises(ValueError):
        _result(hmac_tag="tag")
