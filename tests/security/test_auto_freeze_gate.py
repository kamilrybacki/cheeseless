"""Auto-freeze is only reachable through a green, bound, independently reviewed proposal.

ADR 0015 decision 1: the actor is set by the host, never taken from a caller-supplied label.
These tests prove that an agent which can call ``auto_approve_freeze`` still cannot freeze its
own weak tests, because the check consults the store rather than the caller's word.
"""

from __future__ import annotations

import inspect

import pytest

from cheeseless.app import authoring as authoring_app
from cheeseless.app.authoring import AuthorError, auto_approve_freeze
from cheeseless.domain.authoring import Proposal, StrengthReview

A = "a" * 40
B = "b" * 40
PID = "a1" * 32
DIGEST = "d" * 64


class _Store:
    """The little of the authoring store these tests exercise."""

    def __init__(self, proposal: Proposal | None, review: StrengthReview | None) -> None:
        self._proposal = proposal
        self._review = review

    def load_proposal(self, proposal_id: str) -> Proposal | None:
        return self._proposal

    def load_strength_review(self, proposal_id: str) -> StrengthReview | None:
        return self._review


def _proposal() -> Proposal:
    return Proposal(
        proposal_id=PID,
        lineage_id="a2" * 16,
        acceptance_tree=A,
        spec_digest=DIGEST,
        baseline_commit=B,
    )


def _review(**kw: object) -> StrengthReview:
    fields: dict[str, object] = {
        "proposal_id": PID,
        "acceptance_tree": A,
        "spec_digest": DIGEST,
        "baseline_commit": B,
        "green": True,
        "author_vendor": "openai",
        "reviewer_vendor": "anthropic",
    }
    fields.update(kw)
    return StrengthReview(**fields)  # type: ignore[arg-type]


def _auto(store: _Store) -> object:
    # repo/sandbox are never reached: every refusal happens before the freeze is attempted.
    return auto_approve_freeze(None, None, None, store, PID)  # type: ignore[arg-type]


def test_auto_freeze_refuses_without_any_strength_review() -> None:
    with pytest.raises(AuthorError) as exc:
        _auto(_Store(_proposal(), None))
    assert exc.value.kind == "no_strength_review"


def test_auto_freeze_refuses_a_review_of_a_different_acceptance_tree() -> None:
    with pytest.raises(AuthorError) as exc:
        _auto(_Store(_proposal(), _review(acceptance_tree="c" * 40)))
    assert exc.value.kind == "stale_strength_review"


def test_auto_freeze_refuses_a_review_of_a_different_spec() -> None:
    with pytest.raises(AuthorError) as exc:
        _auto(_Store(_proposal(), _review(spec_digest="e" * 64)))
    assert exc.value.kind == "stale_strength_review"


def test_auto_freeze_refuses_a_review_of_a_different_baseline() -> None:
    with pytest.raises(AuthorError) as exc:
        _auto(_Store(_proposal(), _review(baseline_commit="c" * 40)))
    assert exc.value.kind == "stale_strength_review"


def test_auto_freeze_refuses_a_review_of_another_proposal() -> None:
    with pytest.raises(AuthorError) as exc:
        _auto(_Store(_proposal(), _review(proposal_id="f" * 64)))
    assert exc.value.kind == "stale_strength_review"


def test_auto_freeze_refuses_a_red_review() -> None:
    with pytest.raises(AuthorError) as exc:
        _auto(_Store(_proposal(), _review(green=False)))
    assert exc.value.kind == "strength_review_failed"


def test_auto_freeze_takes_no_actor_and_no_waiver() -> None:
    """A parameter for either would let the caller choose the mode, which ADR 0015 forbids."""
    params = inspect.signature(auto_approve_freeze).parameters
    assert "actor" not in params
    assert "no_reference" not in params
    assert "reason" not in params


def test_auto_freeze_sets_the_dev_actor_itself() -> None:
    """The actor must be the module constant, not anything derived from a caller's string."""
    source = inspect.getsource(auto_approve_freeze)
    assert "actor=DEV_ACTOR" in source


def test_the_automatic_actor_is_the_name_audit_reads() -> None:
    assert authoring_app.DEV_ACTOR == "cheeseless-dev"


def test_the_operator_path_still_takes_the_actor_from_the_caller() -> None:
    """``approve_freeze`` keeps its ``--as`` because a human may name themselves.

    The two paths stay distinct: only the automatic one sets the actor.
    """
    assert "actor" in inspect.signature(authoring_app.approve_freeze).parameters


def test_a_green_review_from_the_author_vendor_cannot_even_be_stored() -> None:
    with pytest.raises(ValueError, match="vendor"):
        _review(reviewer_vendor="openai")


def test_the_refusal_never_reaches_the_freeze() -> None:
    """The refusals happen before approval, so a refused auto-freeze leaves no task behind."""
    source = inspect.getsource(authoring_app.auto_approve_freeze)
    problem_check = source.index("_strength_review_problem")
    approve_call = source.index("return approve_freeze")
    assert problem_check < approve_call
