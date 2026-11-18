"""The `freeze` command has no route that commits a suite without the approval gate (ADR 0016).

A hidden flag is not an unreachable one: any agent that can run the CLI can pass it. So the
CLI must not carry a test-only shortcut at all, and a bare `freeze --yes` must stop at the
same gate as `freeze --approve <id> --auto`: no proposal, no opt-in, no observed review or no
crosscheck each leave nothing frozen. An operator approving with `--as` is not bound by the
unattended gates and keeps ADR 0009's. Tests that need a frozen task as a given use
`tests/toy.py`, which calls the app layer directly.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cheeseless.adapters.inbound.cli.main import build_parser
from cheeseless.adapters.outbound.git.repository import GitRepository
from cheeseless.adapters.outbound.store.file_store import FileStore, store_root
from cheeseless.app.authoring import freeze_propose
from cheeseless.bootstrap import build_context, main
from cheeseless.domain.authoring import Proposal, StrengthReview
from tests.toy import freeze_without_approval

# Every option `freeze` accepts. A new one must be reviewed against the gate before it is
# added here: this list notices a shortcut slipping back in.
_REVIEWED_FREEZE_OPTIONS = frozenset(
    {
        "-h",
        "--help",
        "--allow-auto",
        "--approve",
        "--as",
        "--auto",
        "--baseline",
        "--json",
        "--no-allow-auto",
        "--no-reference",
        "--reason",
        "--yes",
    }
)


def _freeze_options() -> set[str]:
    parser = build_parser()
    sub = next(a for a in parser._actions if a.dest == "command")  # noqa: SLF001
    freeze = sub.choices["freeze"]  # type: ignore[index]
    return {opt for action in freeze._actions for opt in action.option_strings}  # noqa: SLF001


def test_freeze_takes_only_reviewed_options() -> None:
    assert _freeze_options() == _REVIEWED_FREEZE_OPTIONS


def test_the_old_test_shortcut_is_rejected_by_the_parser() -> None:
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["freeze", "--internal-test-freeze"])
    assert exc.value.code == 2


def _active_task(repo_root: Path, state: Path) -> str | None:
    repo = GitRepository.discover(repo_root)
    return FileStore(store_root(repo.common_dir(), state)).active_task_id()


def test_freeze_yes_without_a_proposal_freezes_nothing(
    toy_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    monkeypatch.chdir(toy_repo)
    rc = main(["--state-dir", str(state), "freeze", "--yes"])
    assert rc == 1
    assert _active_task(toy_repo, state) is None


def test_canary_the_same_tree_does_freeze_through_the_app_layer(
    toy_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Without this, the refusal above would also pass if freezing the toy were simply broken.
    state = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    monkeypatch.chdir(toy_repo)
    assert freeze_without_approval(str(state)) == 0
    assert _active_task(toy_repo, state) is not None


def _proposed_and_opted_in(toy_repo: Path, state: Path) -> Proposal:
    ctx = build_context(state)
    proposal = freeze_propose(ctx.repo, ctx.store, ctx.repo.head_commit())  # type: ignore[arg-type]
    ctx.store.set_auto_freeze(proposal.lineage_id, True)  # type: ignore[attr-defined]
    return proposal


def _freeze_yes(state: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, dict[str, object]]:
    capsys.readouterr()
    rc = main(["--state-dir", str(state), "freeze", "--yes", "--json"])
    return rc, json.loads(capsys.readouterr().out)


def test_freeze_yes_with_optin_but_no_review_freezes_nothing(
    toy_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The opt-in is consent to an unattended freeze, not the freeze itself: without a
    # host-observed review the bare route must stop exactly where `--approve --auto` stops.
    state = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    monkeypatch.chdir(toy_repo)
    _proposed_and_opted_in(toy_repo, state)
    rc, out = _freeze_yes(state, capsys)
    assert rc == 1
    assert out["error"] == "no_strength_review"
    assert _active_task(toy_repo, state) is None


def test_canary_a_stored_review_moves_the_refusal_to_the_next_gate(
    toy_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Proves the refusal above is the review check and not an earlier failure: once an
    # observed green review exists, the same command gets past it and is stopped by the
    # crosscheck, which `freeze --yes` must also honour.
    state = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    monkeypatch.chdir(toy_repo)
    proposal = _proposed_and_opted_in(toy_repo, state)
    store = build_context(state).store
    version = store.authoring_state(proposal.lineage_id, proposal.spec_digest).version  # type: ignore[attr-defined]
    review = StrengthReview(
        proposal_id=proposal.proposal_id,
        acceptance_tree=proposal.acceptance_tree,
        spec_digest=proposal.spec_digest,
        baseline_commit=proposal.baseline_commit,
        green=True,
        author_vendor="openai",
        reviewer_vendor="anthropic",
        observed=True,
    )
    store.save_strength_review(  # type: ignore[attr-defined]
        proposal.proposal_id, proposal.lineage_id, review, expected_version=version
    )
    rc, out = _freeze_yes(state, capsys)
    assert rc == 1
    assert out["error"] == "no_crosscheck"
    assert _active_task(toy_repo, state) is None


def test_an_operator_approval_needs_no_optin_and_no_review(
    toy_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The unattended gates replace a present human, so they must not bind one: a project that
    # never opted in is still freezable by an operator, held to ADR 0009's crosscheck.
    state = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    monkeypatch.chdir(toy_repo)
    ctx = build_context(state)
    proposal = freeze_propose(ctx.repo, ctx.store, ctx.repo.head_commit())  # type: ignore[arg-type]
    capsys.readouterr()
    argv = ["--state-dir", str(state), "freeze", "--approve", proposal.proposal_id]
    rc = main([*argv, "--as", "operator", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert out["error"] == "no_crosscheck"
    # Canary: the same proposal, unattended, stops at the opt-in instead.
    rc = main([*argv, "--auto", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert out["error"] == "auto_freeze_not_enabled"
    assert _active_task(toy_repo, state) is None
