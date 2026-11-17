"""The ``cheeseless dev`` CLI: parser wiring and the preconditions it refuses on.

These never launch a sandbox: every case stops before a session is staged, which is the
property ADR 0013 wants (a refusal leaves nothing behind).
"""

from pathlib import Path

import pytest

from cheeseless.adapters.inbound.cli.main import build_parser, split_agent_argv


def test_the_dev_parser_takes_a_lineage_and_an_agent() -> None:
    head, agent = split_agent_argv(
        ["dev", "--lineage", "demo", "--", "claude", "--model", "m"]
    )
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
