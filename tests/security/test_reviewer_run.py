"""`strength-review --run` must record only what the reviewer actually said (ADR 0015)."""

from pathlib import Path

import pytest

from cheeseless.app.authoring import AuthorError
from cheeseless.domain.authoring import Proposal


def _store_with_proposal(tmp_path: Path):  # type: ignore[no-untyped-def]
    from cheeseless.adapters.outbound.store.file_store import FileStore

    store = FileStore(tmp_path / "state")
    proposal = Proposal(
        proposal_id="a" * 64,
        lineage_id="b" * 64,
        baseline_commit="c" * 40,
        acceptance_tree="d" * 40,
        spec_digest="e" * 64,
    )
    store.save_proposal(proposal)
    return store, proposal


def test_a_reviewer_that_says_nothing_records_nothing(tmp_path: Path) -> None:
    """The point of decision 3: exit 0 is not a verdict."""
    store, _ = _store_with_proposal(tmp_path)
    from cheeseless.app.authoring import run_strength_review

    with pytest.raises(AuthorError) as caught:
        run_strength_review(
            store,
            "a" * 64,
            launch=lambda brief: None,
            author_model="claude-sonnet-5-5",
            reviewer_model="gpt-6.2-sol",
        )
    assert caught.value.kind == "strength_review_no_verdict"


def test_the_brief_names_the_verdict_file() -> None:
    from cheeseless.app.authoring import VERDICT_FILE, review_brief

    assert VERDICT_FILE in review_brief()
    assert "VERDICT: pass" in review_brief()


def test_a_run_still_refuses_a_same_vendor_reviewer(tmp_path: Path) -> None:
    """Vendors come from the launch config, so a --run cannot fake independence."""
    store, _ = _store_with_proposal(tmp_path)
    from cheeseless.app.authoring import run_strength_review

    with pytest.raises(AuthorError) as caught:
        run_strength_review(
            store,
            "a" * 64,
            launch=lambda brief: True,
            author_model="claude-sonnet-5-5",
            reviewer_model="claude-opus-4-5",
        )
    assert caught.value.kind == "strength_review_not_independent"


def test_a_run_records_the_verdict_the_reviewer_wrote(tmp_path: Path) -> None:
    store, _ = _store_with_proposal(tmp_path)
    from cheeseless.app.authoring import run_strength_review

    review = run_strength_review(
        store,
        "a" * 64,
        launch=lambda brief: False,
        author_model="claude-sonnet-5-5",
        reviewer_model="gpt-6.2-sol",
    )
    assert review.green is False
    assert review.reviewer_vendor != review.author_vendor


def test_a_run_for_an_unknown_proposal_launches_nothing(tmp_path: Path) -> None:
    """A bad task id must not start a sandbox: the check comes before the launch."""
    store, _ = _store_with_proposal(tmp_path)
    from cheeseless.app.authoring import run_strength_review

    launched = []
    with pytest.raises(AuthorError):
        run_strength_review(
            store,
            "f" * 64,
            launch=lambda brief: launched.append(brief) or True,  # type: ignore[func-returns-value]
            author_model="claude-sonnet-5-5",
            reviewer_model="gpt-6.2-sol",
        )
    assert launched == []
