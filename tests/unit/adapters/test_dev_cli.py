"""The ``cheeseless dev`` CLI: parser wiring and the preconditions it refuses on.

These never launch a sandbox: every case stops before a session is staged, which is the
property ADR 0013 wants (a refusal leaves nothing behind).
"""

from pathlib import Path

import pytest

from cheeseless.adapters.inbound.cli.main import build_parser, split_agent_argv


def test_the_dev_parser_takes_a_lineage_and_an_agent() -> None:
    head, agent = split_agent_argv(["dev", "--lineage", "demo", "--", "claude", "--model", "m"])
    args = build_parser().parse_args(head)
    assert args.command == "dev"
    assert args.lineage == "demo"
    assert agent == ["claude", "--model", "m"]


def test_dev_defaults_to_a_tui_reference() -> None:
    args = build_parser().parse_args(["dev", "--lineage", "demo"])
    assert args.headless_reference is False
    assert args.resume is False
    assert args.reference_timeout == 1800.0


def test_dev_can_ask_for_the_headless_fallback() -> None:
    args = build_parser().parse_args(["dev", "--lineage", "demo", "--headless-reference"])
    assert args.headless_reference is True


def test_a_dirty_tree_refuses_before_anything_is_staged(
    toy_repo: Path, tmp_path, monkeypatch
) -> None:
    from cheeseless.bootstrap_dev import run_dev_command

    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "t")
    monkeypatch.chdir(toy_repo)
    (toy_repo / "untracked.txt").write_text("x")
    args = build_parser().parse_args(["dev", "--lineage", "demo", "--state-dir", str(tmp_path)])
    assert run_dev_command(args, ["/bin/true"]) == 1


def test_a_missing_agent_is_refused(toy_repo: Path, tmp_path, monkeypatch) -> None:
    from cheeseless.bootstrap_dev import run_dev_command

    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "t")
    monkeypatch.chdir(toy_repo)
    args = build_parser().parse_args(["dev", "--lineage", "demo", "--state-dir", str(tmp_path)])
    assert run_dev_command(args, None) == 1


def test_a_missing_token_is_refused_for_the_author(toy_repo: Path, tmp_path, monkeypatch) -> None:
    from cheeseless.bootstrap_dev import run_dev_command

    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.chdir(toy_repo)
    args = build_parser().parse_args(["dev", "--lineage", "demo", "--state-dir", str(tmp_path)])
    assert run_dev_command(args, ["/bin/true"]) == 1


def test_an_unknown_lineage_cannot_resume(toy_repo: Path, tmp_path, monkeypatch) -> None:
    from cheeseless.bootstrap_dev import run_dev_command

    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "t")
    monkeypatch.chdir(toy_repo)
    args = build_parser().parse_args(
        ["dev", "--lineage", "demo", "--resume", "--state-dir", str(tmp_path)]
    )
    assert run_dev_command(args, ["/bin/true"]) == 1


@pytest.mark.parametrize("flag", ["--json"])
def test_json_output_is_one_object(flag, toy_repo: Path, tmp_path, monkeypatch, capsys) -> None:
    """A refusal goes to stderr, so ``--json`` never prints a half object on stdout."""
    from cheeseless.bootstrap_dev import run_dev_command

    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.chdir(toy_repo)
    args = build_parser().parse_args(
        ["dev", "--lineage", "demo", flag, "--state-dir", str(tmp_path)]
    )
    assert run_dev_command(args, ["/bin/true"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "dev:" in captured.err


# -- the tests phase ----------------------------------------------------------


def _in_tests_phase(toy_repo: Path, state_dir: Path) -> None:
    """Record a tests-phase dev session in the same store the CLI will resolve."""
    from cheeseless.adapters.outbound.git.repository import GitRepository
    from cheeseless.adapters.outbound.store.file_store import FileStore, store_root
    from cheeseless.app.dev_session import open_session

    repo = GitRepository.discover(toy_repo)
    store = FileStore(store_root(repo.common_dir(), state_dir))
    state = open_session(store, "demo", resume=False)[0]
    author = "0b7d4c1e-2f3a-4b5c-8d9e-0f1a2b3c4d5e"
    store.save_dev_state(state.author_started(author).spec_done("1" * 64))


def test_the_tests_phase_refuses_without_a_done_reference(
    toy_repo: Path, tmp_path, monkeypatch, capsys
) -> None:
    from cheeseless.bootstrap_dev import run_dev_command

    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "t")
    monkeypatch.chdir(toy_repo)
    state_dir = tmp_path / "state"
    _in_tests_phase(toy_repo, state_dir)
    args = build_parser().parse_args(
        ["dev", "--lineage", "demo", "--resume", "--state-dir", str(state_dir)]
    )
    assert run_dev_command(args, ["/bin/true"]) == 1
    captured = capsys.readouterr()
    assert "reference is stale" in captured.err or "no reference" in captured.err


def test_the_tests_phase_points_at_the_reference_command(
    toy_repo: Path, tmp_path, monkeypatch, capsys
) -> None:
    from cheeseless.bootstrap_dev import run_dev_command

    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "t")
    monkeypatch.chdir(toy_repo)
    state_dir = tmp_path / "state"
    _in_tests_phase(toy_repo, state_dir)
    args = build_parser().parse_args(
        ["dev", "--lineage", "demo", "--resume", "--state-dir", str(state_dir)]
    )
    run_dev_command(args, ["/bin/true"])
    assert "dev-reference" in capsys.readouterr().err


def test_the_tests_phase_never_approves_a_freeze(
    toy_repo: Path, tmp_path, monkeypatch, capsys
) -> None:
    """ADR 0013: the host may approve as cheeseless-dev only after a strength review."""
    from cheeseless.bootstrap_dev import run_dev_command

    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "t")
    monkeypatch.chdir(toy_repo)
    state_dir = tmp_path / "state"
    _in_tests_phase(toy_repo, state_dir)
    args = build_parser().parse_args(
        ["dev", "--lineage", "demo", "--resume", "--state-dir", str(state_dir)]
    )
    run_dev_command(args, ["/bin/true"])
    output = capsys.readouterr()
    assert "cheeseless-dev" not in output.out + output.err


def test_the_implement_report_names_the_re_freeze() -> None:
    """The count that forces a re-freeze must be visible, not just the phase name."""
    from cheeseless.bootstrap_dev import _implement_report
    from cheeseless.domain.dev_session import MAX_HOLDOUT_FAILURES, DevPhase, DevState

    state = DevState(
        lineage_id="demo",
        phase=DevPhase.IMPLEMENT,
        spec_digest="1" * 64,
        proposal_id="2" * 64,
        author_session="0b7d4c1e-2f3a-4b5c-8d9e-0f1a2b3c4d5e",
        holdout_failures=MAX_HOLDOUT_FAILURES,
        version=1,
    )

    class _Store:
        def dev_state(self, lineage: str) -> DevState:
            return state

    report = _implement_report(_Store(), "demo")
    assert "re-freeze" in report
    assert str(MAX_HOLDOUT_FAILURES) in report
