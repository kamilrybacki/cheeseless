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

    def __init__(
        self,
        proposal: Proposal | None,
        review: StrengthReview | None,
        *,
        opted_in: bool = True,
    ) -> None:
        self._proposal = proposal
        self._review = review
        self._opted_in = opted_in

    def auto_freeze_enabled(self, lineage_id: str) -> bool:
        return self._opted_in

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
        # These fixtures stand for a review the host ran; `observed=False` has its own test.
        "observed": True,
    }
    fields.update(kw)
    return StrengthReview(**fields)  # type: ignore[arg-type]


def _auto(store: _Store) -> object:
    # repo/sandbox are never reached: every refusal happens before the freeze is attempted.
    return auto_approve_freeze(None, None, None, store, PID)  # type: ignore[arg-type]


def test_auto_freeze_refuses_a_review_an_operator_merely_recorded() -> None:
    """The whole point of observing the session: an operator's word is not a review.

    Without this, `cheeseless strength-review --passed` would be enough to auto-freeze,
    and the gate would be asking someone to vouch for tests they may have written.
    """
    with pytest.raises(AuthorError) as exc:
        _auto(_Store(_proposal(), _review(observed=False)))
    assert exc.value.kind == "unobserved_strength_review"


def test_auto_freeze_refuses_a_project_that_never_opted_in() -> None:
    """Decision B: unattended freeze is opt-in per project, and an absent record is not consent.

    A repo that has never written the setting must not get an unattended freeze, even with a
    perfect observed review - otherwise the opt-in is a formality, not a gate.
    """
    with pytest.raises(AuthorError) as exc:
        _auto(_Store(_proposal(), _review(), opted_in=False))
    assert exc.value.kind == "auto_freeze_not_enabled"


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


# -- writing the record (ADR 0015 decision 4) --------------------------------
#
# Vendors come from the models the host launched. A reviewer that could name its own
# vendor would be attesting to its own independence.

from cheeseless.app.authoring import record_strength_review  # noqa: E402


class _WritingStore(_Store):
    def __init__(self, proposal: Proposal) -> None:
        super().__init__(proposal, None)
        self.saved: StrengthReview | None = None

    def load_proposal(self, proposal_id: str) -> Proposal | None:
        # Only the stored proposal exists; any other id is the unknown case.
        return self._proposal if self._proposal.proposal_id == proposal_id else None

    def authoring_state(self, lineage_id: str, spec_digest: str) -> object:
        return type("S", (), {"version": 3})()

    def save_strength_review(self, pid, lineage, record, *, expected_version):
        assert expected_version == 3
        self.saved = record


def test_the_host_derives_the_vendors_from_the_models_it_launched() -> None:
    store = _WritingStore(_proposal())
    review = record_strength_review(
        store, PID, green=True, author_model="gpt-6.2-sol", reviewer_model="claude-opus-5"
    )
    assert (review.author_vendor, review.reviewer_vendor) == ("openai", "anthropic")
    assert review.green and store.saved == review


def test_a_review_by_the_author_model_family_is_refused() -> None:
    with pytest.raises(AuthorError) as exc:
        record_strength_review(
            _WritingStore(_proposal()),
            PID,
            green=True,
            author_model="gpt-6.2-sol",
            reviewer_model="gpt-6.2-astra",
        )
    assert exc.value.kind == "strength_review_not_independent"


@pytest.mark.parametrize("model", ["totally-unknown", "internal-ft-v1"])
def test_an_unrecognised_model_is_refused_rather_than_assumed_independent(model: str) -> None:
    """Guessing a vendor here would make the independence check meaningless."""
    with pytest.raises(AuthorError) as exc:
        record_strength_review(
            _WritingStore(_proposal()),
            PID,
            green=True,
            author_model="gpt-6.2-sol",
            reviewer_model=model,
        )
    assert exc.value.kind == "unknown_vendor"


def test_a_red_review_is_recordable() -> None:
    """A failed review is a result, not an error; it must be storable to be honest."""
    review = record_strength_review(
        _WritingStore(_proposal()),
        PID,
        green=False,
        author_model="gpt-6.2-sol",
        reviewer_model="claude-opus-5",
    )
    assert not review.green


def test_an_unknown_proposal_is_refused() -> None:
    with pytest.raises(AuthorError) as exc:
        record_strength_review(
            _WritingStore(_proposal()),
            "f" * 64,
            green=True,
            author_model="gpt-6.2-sol",
            reviewer_model="claude-opus-5",
        )
    assert exc.value.kind == "unknown_proposal"


def test_recording_a_review_never_freezes_anything() -> None:
    """Writing evidence and acting on it stay separate (Decision 1)."""
    source = inspect.getsource(record_strength_review)
    assert "approve_freeze" not in source
    assert "auto_approve_freeze" not in source


def test_the_optin_is_written_by_the_host_and_withdrawable() -> None:
    """Decision B: the flag exists, is per lineage, and can be taken back."""
    from cheeseless.adapters.inbound.cli import main as cli

    parser = cli.build_parser()
    args = parser.parse_args(["freeze", "--allow-auto"])
    assert args.allow_auto is True
    assert args.no_allow_auto is False
    args = parser.parse_args(["freeze", "--no-allow-auto"])
    assert args.no_allow_auto is True

    # The use case reads the setting before it looks at any review, and the store is asked
    # for the proposal's own lineage rather than a caller-supplied one.
    source = inspect.getsource(authoring_app.auto_approve_freeze)
    assert "auto_freeze_enabled" in source
    assert "auto_freeze_not_enabled" in source
