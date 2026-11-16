"""Host CLI: freeze --approve and the reference commands (ADR 0009)."""

import json
from types import SimpleNamespace

import pytest

from cheeseless.adapters.inbound.cli import main as cli
from cheeseless.app.authoring import AuthorError
from cheeseless.app.freeze import FreezeError
from cheeseless.domain.authoring import AuthoringState, TaskPhase


def _ctx():
    repo = SimpleNamespace(head_commit=lambda: "a" * 40)
    return cli.Context(
        repo=repo, runner=object(), sandbox=object(), store=object(), author_sandbox=object()
    )


def test_parser_accepts_approve_and_reference() -> None:
    p = cli.build_parser()
    a = p.parse_args(
        ["freeze", "--approve", "x", "--as", "me", "--no-reference", "--reason", "r", "--json"]
    )
    assert (a.approve, a.actor, a.no_reference, a.reason, a.json) == ("x", "me", True, "r", True)
    r = p.parse_args(["reference", "record", "--worktree", "worktree-dir", "--json"])
    assert r.reference_command == "record" and r.json


def test_approve_success_json(monkeypatch, capsys) -> None:
    seen = {}

    def fake(repo, sandbox, author_sandbox, store, pid, **kw):
        seen.update(pid=pid, **kw)
        return SimpleNamespace(task_id="t" * 64, seed=b"s")

    monkeypatch.setattr(cli, "approve_freeze", fake)
    code = cli.main(
        [
            "freeze",
            "--approve",
            "p1",
            "--as",
            "alice",
            "--no-reference",
            "--reason",
            "why",
            "--json",
        ],
        context=_ctx(),
    )
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["approved"] is True and out["task_id"] == "t" * 64
    assert "seed" not in out
    assert seen == {"pid": "p1", "actor": "alice", "no_reference": True, "reason": "why"}


def test_approve_needs_actor(capsys) -> None:
    assert cli.main(["freeze", "--approve", "p1"], context=_ctx()) == 2
    assert "--as" in capsys.readouterr().err


@pytest.mark.parametrize(
    "exc,kind",
    [(AuthorError("not_merged", "tree differs"), "not_merged"), (FreezeError(["bad"]), "freeze")],
)
def test_approve_refusals(monkeypatch, capsys, exc, kind) -> None:
    def boom(*a, **k):
        raise exc

    monkeypatch.setattr(cli, "approve_freeze", boom)
    code = cli.main(["freeze", "--approve", "p", "--as", "a", "--json"], context=_ctx())
    out = json.loads(capsys.readouterr().out)
    assert code == 1 and out["approved"] is False and out["error"] == kind


def _state(phase=TaskPhase.REFERENCE_BLOCKED):
    return AuthoringState("l" * 8 if False else "lineage-a", "a" * 64, phase, 1, 0)


def test_reference_failed_and_retry(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "read_contract", lambda repo, src: object())
    monkeypatch.setattr(cli, "reference_failed", lambda store, c: _state())
    assert cli.main(["reference", "failed", "--json"], context=_ctx()) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["phase"] == "reference_blocked" and out["retries_left"] == 2

    from cheeseless.domain.authoring import AuthoringError

    def deny(store, c):
        raise AuthoringError("retry limit")

    monkeypatch.setattr(cli, "start_reference", deny)
    assert cli.main(["reference", "retry", "--json"], context=_ctx()) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_reference_record(monkeypatch, capsys, tmp_path) -> None:
    monkeypatch.setattr(cli, "record_reference", lambda repo, store, wt, src: "f" * 40)
    code = cli.main(["reference", "record", "--worktree", str(tmp_path), "--json"], context=_ctx())
    assert code == 0 and json.loads(capsys.readouterr().out) == {"reference_tree": "f" * 40}


def test_reference_without_subcommand(capsys) -> None:
    assert cli.main(["reference"], context=_ctx()) == 2
