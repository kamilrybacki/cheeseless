"""`strength-review --run` must record only what the reviewer actually said (ADR 0015)."""

from pathlib import Path

import pytest

from cheeseless.app.authoring import AuthorError
from cheeseless.domain.authoring import Proposal


def _store_with_proposal(
    tmp_path: Path,
    *,
    author_model: str = "claude-sonnet-5-5",
    reviewer_model: str | None = None,
):  # type: ignore[no-untyped-def]
    """A store holding a proposal, plus the launch records a real host would have written.

    Both vendors are taken from `sessions.json`, which only `register_session` writes, so a
    test has to record the launches it is pretending happened. `reviewer_model` left as
    `None` means the host never launched a reviewer for this task.
    """
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
    store.register_session(proposal.proposal_id, "author", author_model)
    if reviewer_model is not None:
        store.register_session(proposal.proposal_id, "reviewer", reviewer_model)
    return store, proposal


def test_a_reviewer_that_says_nothing_records_nothing(tmp_path: Path) -> None:
    """The point of decision 3: exit 0 is not a verdict."""
    store, _ = _store_with_proposal(tmp_path, reviewer_model="gpt-6.2-sol")
    from cheeseless.app.authoring import run_strength_review

    with pytest.raises(AuthorError) as caught:
        run_strength_review(
            store,
            "a" * 64,
            launch=lambda brief: (None, "gpt-6.2-sol"),
            author_model="claude-sonnet-5-5",
        )
    assert caught.value.kind == "strength_review_no_verdict"


def test_the_brief_names_the_verdict_file() -> None:
    from cheeseless.app.authoring import VERDICT_FILE, review_brief

    assert VERDICT_FILE in review_brief()
    assert "VERDICT: pass" in review_brief()


def test_a_run_still_refuses_a_same_vendor_reviewer(tmp_path: Path) -> None:
    """Vendors come from the launch config, so a --run cannot fake independence."""
    store, _ = _store_with_proposal(tmp_path, reviewer_model="claude-opus-4-5")
    from cheeseless.app.authoring import run_strength_review

    with pytest.raises(AuthorError) as caught:
        run_strength_review(
            store,
            "a" * 64,
            # The launch really ran a Claude reviewer, whatever anyone declared.
            launch=lambda brief: (True, "claude-opus-4-5"),
            author_model="claude-sonnet-5-5",
        )
    assert caught.value.kind == "strength_review_not_independent"


def test_a_run_records_the_verdict_the_reviewer_wrote(tmp_path: Path) -> None:
    store, _ = _store_with_proposal(tmp_path, reviewer_model="gpt-6.2-sol")
    from cheeseless.app.authoring import run_strength_review

    review = run_strength_review(
        store,
        "a" * 64,
        launch=lambda brief: (False, "gpt-6.2-sol"),
        author_model="claude-sonnet-5-5",
    )
    assert review.green is False
    assert review.reviewer_vendor != review.author_vendor
    assert review.reviewer_vendor == "openai", "the vendor comes from the launch"


def test_a_run_for_an_unknown_proposal_launches_nothing(tmp_path: Path) -> None:
    """A bad task id must not start a sandbox: the check comes before the launch."""
    store, _ = _store_with_proposal(tmp_path, reviewer_model="gpt-6.2-sol")
    from cheeseless.app.authoring import run_strength_review

    launched = []
    with pytest.raises(AuthorError):
        run_strength_review(
            store,
            "f" * 64,
            launch=lambda brief: (launched.append(brief) or True, "gpt-6.2-sol"),  # type: ignore[func-returns-value]
            author_model="claude-sonnet-5-5",
        )
    assert launched == []


def test_a_launch_that_reports_no_model_records_nothing(tmp_path: Path) -> None:
    """Without a launch identity there is no vendor to check independence against.

    Guessing one is how a single-vendor review gets written down as cross-vendor, so this
    refuses instead of falling back on whatever the operator declared.
    """
    store, _ = _store_with_proposal(tmp_path, reviewer_model="gpt-6.2-sol")
    from cheeseless.app.authoring import run_strength_review

    with pytest.raises(AuthorError) as caught:
        run_strength_review(
            store,
            "a" * 64,
            launch=lambda brief: (True, ""),
            author_model="claude-sonnet-5-5",
        )
    assert caught.value.kind == "strength_review_unknown_launch"


def test_the_declared_reviewer_model_cannot_stand_in_for_the_launched_one(tmp_path: Path) -> None:
    """The whole point: a label is not a launch.

    `--reviewer-model gpt-6.2-sol` with a reviewer session that really ran a Claude model is
    a same-vendor review, and it must be refused even though the label says otherwise.
    """
    store, _ = _store_with_proposal(tmp_path, reviewer_model="claude-opus-4-5")
    from cheeseless.app.authoring import run_strength_review

    with pytest.raises(AuthorError) as caught:
        run_strength_review(
            store,
            "a" * 64,
            launch=lambda brief: (True, "claude-opus-4-5"),
            author_model="claude-sonnet-5-5",
        )
    assert caught.value.kind == "strength_review_not_independent"


def test_no_author_launch_record_means_no_review(tmp_path: Path) -> None:
    """ADR 0016 bound (c): the author side of the comparison comes from the launch too.

    `--author-model` is what the caller typed. Without a launch record the host cannot say
    which vendor the author *was*, so a reviewer cannot be shown to be independent of it.
    """
    store, _ = _store_with_proposal(tmp_path, reviewer_model="gpt-6.2-sol")
    # Rewind both records to "no author session was ever launched": the append-only log and
    # the live table it falls back to.
    (store._root / "launched-sessions.json").unlink()
    (store._root / "sessions.json").unlink()
    from cheeseless.app.authoring import run_strength_review

    launched: list[str] = []
    with pytest.raises(AuthorError) as caught:
        run_strength_review(
            store,
            "a" * 64,
            launch=lambda brief: (launched.append(brief) or True, "gpt-6.2-sol"),  # type: ignore[func-returns-value]
            author_model="claude-sonnet-5-5",
        )
    assert caught.value.kind == "author_unknown_launch"
    assert launched == [], "an unattributable review is not worth running a reviewer for"


def test_a_declared_author_labels_that_contradicts_the_launch_is_refused(tmp_path: Path) -> None:
    """A caller must not pick which vendor the reviewer is compared against.

    Here the label claims an OpenAI author, which would make an OpenAI reviewer look
    independent. The launch record says the author was Claude. The label loses.
    """
    store, _ = _store_with_proposal(
        tmp_path, author_model="claude-sonnet-5-5", reviewer_model="gpt-6.2-sol"
    )
    from cheeseless.app.authoring import run_strength_review

    with pytest.raises(AuthorError) as caught:
        run_strength_review(
            store,
            "a" * 64,
            launch=lambda brief: (True, "gpt-6.2-sol"),
            author_model="gpt-6.2-sol",
        )
    assert caught.value.kind == "author_model_mismatch"


def test_a_verdict_from_an_unrecorded_process_is_refused(tmp_path: Path) -> None:
    """ADR 0016 bound (b): observed means the host's reviewer session, not any process.

    A launcher that reports a model the host never recorded for this task is producing
    evidence about a process the gate knows nothing about.
    """
    store, _ = _store_with_proposal(tmp_path)  # no reviewer launch recorded
    from cheeseless.app.authoring import run_strength_review

    with pytest.raises(AuthorError) as caught:
        run_strength_review(
            store,
            "a" * 64,
            launch=lambda brief: (True, "gpt-6.2-sol"),
        )
    assert caught.value.kind == "strength_review_unattributed"


def test_the_author_launch_record_outlives_the_session(tmp_path: Path) -> None:
    """`end_session` forgets the live entry; provenance has to survive that.

    Reviews are recorded after the session ends, so a store that only kept live sessions
    would lose the author's vendor exactly when it is needed.
    """
    store, _ = _store_with_proposal(tmp_path, reviewer_model="gpt-6.2-sol")
    for session_id in list(store._read_json("sessions.json")):
        store.end_session(session_id)
    assert store.launched_model("a" * 64, "author") == "claude-sonnet-5-5"
    assert store.launched_model("a" * 64, "reviewer") == "gpt-6.2-sol"
