"""A strength review is evidence the host recorded, and approval may not invent one (ADR 0015)."""

from __future__ import annotations

import dataclasses

import pytest

from cheeseless.domain.authoring import (
    StrengthReview,
    strength_review_binds,
    vendor_of_model,
)

A = "a" * 40
B = "b" * 40
C = "c" * 40
DIGEST = "d" * 64
PID = "a1" * 32


def _review(**kw: object) -> StrengthReview:
    fields: dict[str, object] = {
        "proposal_id": PID,
        "acceptance_tree": A,
        "spec_digest": DIGEST,
        "baseline_commit": B,
        "green": True,
        "author_vendor": "vendor-a",
        "reviewer_vendor": "vendor-b",
    }
    fields.update(kw)
    return StrengthReview(**fields)  # type: ignore[arg-type]


def test_a_green_review_by_another_vendor_is_the_only_thing_that_binds() -> None:
    review = _review()
    assert review.green
    assert strength_review_binds(
        review, proposal_id=PID, acceptance_tree=A, spec_digest=DIGEST, baseline_commit=B
    )


def test_a_stale_acceptance_tree_does_not_bind() -> None:
    assert not strength_review_binds(
        _review(), proposal_id=PID, acceptance_tree=C, spec_digest=DIGEST, baseline_commit=B
    )


def test_a_stale_spec_digest_does_not_bind() -> None:
    assert not strength_review_binds(
        _review(),
        proposal_id=PID,
        acceptance_tree=A,
        spec_digest="e" * 64,
        baseline_commit=B,
    )


def test_a_stale_baseline_does_not_bind() -> None:
    assert not strength_review_binds(
        _review(), proposal_id=PID, acceptance_tree=A, spec_digest=DIGEST, baseline_commit=C
    )


def test_a_review_of_another_proposal_does_not_bind() -> None:
    assert not strength_review_binds(
        _review(), proposal_id="f" * 64, acceptance_tree=A, spec_digest=DIGEST, baseline_commit=B
    )


def test_a_self_reviewed_proposal_cannot_carry_green() -> None:
    """The whole point of the gate: one vendor cannot clear its own tests (ADR 0015)."""
    with pytest.raises(ValueError, match="vendor"):
        _review(reviewer_vendor="vendor-a")


def test_a_red_review_by_another_vendor_is_representable() -> None:
    """A failing review is a normal outcome and must be storable, not an error."""
    review = _review(green=False)
    assert not review.green
    assert strength_review_binds(
        review, proposal_id=PID, acceptance_tree=A, spec_digest=DIGEST, baseline_commit=B
    )


def test_an_unknown_vendor_cannot_be_recorded() -> None:
    with pytest.raises(ValueError, match="vendor"):
        _review(reviewer_vendor="")


@pytest.mark.parametrize(
    "vendor,expected",
    [
        ("gpt-6.2-sol", "openai"),
        ("claude-opus-5", "anthropic"),
        ("gemini-flash", "google"),
        ("deepseek-v4-pro", "deepseek"),
    ],
)
def test_the_vendor_comes_from_the_model_family(vendor: str, expected: str) -> None:
    """Independence is decided by who makes the model, not by a prefix someone chose."""
    assert vendor_of_model(vendor) == expected


def test_an_unrecognised_model_has_no_vendor_rather_than_a_guessed_one() -> None:
    assert vendor_of_model("some-unknown-model") is None


def test_the_record_carries_no_free_text_a_reviewer_could_steer() -> None:
    """Everything here is a digest, an id, a vendor name or a boolean.

    A "reason" field would be the obvious place to hide an argument, so no field may hold
    prose the reviewer wrote.
    """
    fields = {f.name for f in dataclasses.fields(StrengthReview)}
    assert fields == {
        "proposal_id",
        "acceptance_tree",
        "spec_digest",
        "baseline_commit",
        "green",
        "author_vendor",
        "reviewer_vendor",
        # A boolean, not prose: it records who said this, not what they argued.
        "observed",
    }
