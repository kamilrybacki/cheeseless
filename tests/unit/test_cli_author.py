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


@pytest.mark.parametrize("command", ["failed", "retry"])
@pytest.mark.parametrize(
    "error",
    [
        FreezeError(["no contract at the source"]),
        AuthorError("frozen", "the task is frozen; author operations are closed"),
    ],
    ids=["unknown-task", "wrong-state"],
)
def test_reference_error_paths_report_refusal_not_a_traceback(
    monkeypatch, capsys, command, error
) -> None:
    def boom(*_args):
        raise error

    monkeypatch.setattr(cli, "read_contract", lambda repo, src: object())
    monkeypatch.setattr(cli, "reference_failed", boom)
    monkeypatch.setattr(cli, "start_reference", boom)
    if isinstance(error, FreezeError):
        monkeypatch.setattr(cli, "read_contract", boom)
    assert cli.main(["reference", command, "--json"], context=_ctx()) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and out["message"]
    assert cli.main(["reference", command], context=_ctx()) == 1
    assert capsys.readouterr().out.startswith("refused:")


def test_author_recover_success_and_refusal(monkeypatch, capsys) -> None:
    seen = {}

    def fake(repo, sandbox, author_sandbox, store, pid, *, actor):
        seen.update(pid=pid, actor=actor)
        return "rolled_back"

    monkeypatch.setattr(cli, "recover_approval", fake)
    code = cli.main(["author", "recover", "--task", "p1", "--as", "op", "--json"], context=_ctx())
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out == {"recovered": True, "outcome": "rolled_back", "task_id": "p1"}
    assert seen == {"pid": "p1", "actor": "op"}

    def alive(repo, sandbox, author_sandbox, store, pid, *, actor):
        raise AuthorError("approver_alive", "an approver is still running")

    monkeypatch.setattr(cli, "recover_approval", alive)
    code = cli.main(["author", "recover", "--task", "p1", "--as", "op", "--json"], context=_ctx())
    out = json.loads(capsys.readouterr().out)
    assert code == 1 and out["recovered"] is False and out["error"] == "approver_alive"


def test_author_recover_needs_actor_and_subcommand(capsys) -> None:
    assert cli.main(["author", "recover", "--task", "p1"], context=_ctx()) == 2
    assert "--as" in capsys.readouterr().err
    assert cli.main(["author"], context=_ctx()) == 2


def test_promote_role_defaults_to_implementer_and_routes_to_gate(monkeypatch, capsys) -> None:
    calls = []
    monkeypatch.setattr(
        cli,
        "promote",
        lambda *a, **k: (
            calls.append("gate") or SimpleNamespace(ref="refs/heads/gate/t", commit="c")
        ),
    )
    monkeypatch.setattr(
        cli, "promote_author", lambda *a, **k: calls.append("spec") or pytest.fail("crossed")
    )
    ctx = cli.Context(
        repo=SimpleNamespace(root="."), runner=object(), sandbox=object(), store=object()
    )
    assert cli.main(["promote", "--json"], context=ctx) == 0
    assert calls == ["gate"]
    assert json.loads(capsys.readouterr().out)["ref"] == "refs/heads/gate/t"


def test_promote_role_author_routes_to_spec_and_never_to_gate(monkeypatch, capsys) -> None:
    calls = []
    monkeypatch.setattr(cli, "promote", lambda *a, **k: pytest.fail("crossed"))
    monkeypatch.setattr(
        cli,
        "promote_author",
        lambda repo, store, *, worktree: (
            calls.append(worktree) or SimpleNamespace(ref="refs/heads/spec/l", commit="c")
        ),
    )
    ctx = cli.Context(
        repo=SimpleNamespace(root="."), runner=object(), sandbox=object(), store=object()
    )
    assert cli.main(["promote", "--role", "author", "--json"], context=ctx) == 0
    assert len(calls) == 1
    assert json.loads(capsys.readouterr().out)["ref"] == "refs/heads/spec/l"


def test_author_promote_refusal_is_structured_and_nonzero(monkeypatch, capsys) -> None:
    def refuse(*a, **k):
        raise AuthorError("stale_author_check", "the acceptance tree changed")

    monkeypatch.setattr(cli, "promote_author", refuse)
    ctx = cli.Context(
        repo=SimpleNamespace(root="."), runner=object(), sandbox=object(), store=object()
    )
    assert cli.main(["promote", "--role", "author", "--json"], context=ctx) == 1
    out = json.loads(capsys.readouterr().out)
    assert out == {"refused": "stale_author_check", "message": "the acceptance tree changed"}


def test_author_promote_path_rejection_lists_paths(monkeypatch, capsys) -> None:
    def refuse(*a, **k):
        raise cli.PromoteRejected(("src/x.py",), ())

    monkeypatch.setattr(cli, "promote_author", refuse)
    ctx = cli.Context(
        repo=SimpleNamespace(root="."), runner=object(), sandbox=object(), store=object()
    )
    assert cli.main(["promote", "--role", "author", "--json"], context=ctx) == 1
    assert json.loads(capsys.readouterr().out)["rejected"] == ["src/x.py"]


def test_author_promote_refuses_task_and_approve_flags(capsys) -> None:
    ctx = cli.Context(
        repo=SimpleNamespace(root="."), runner=object(), sandbox=object(), store=object()
    )
    assert cli.main(["promote", "--role", "author", "--approve", "src/x"], context=ctx) == 2
    assert cli.main(["promote", "--role", "author", "--task", "t"], context=ctx) == 2


@pytest.mark.parametrize("role", ["author", "implementer"])
def test_promote_refuses_an_unsnapshottable_tree_with_a_stable_json_refusal(
    monkeypatch, capsys, role
) -> None:
    def refuse(*a, **k):
        raise cli.SnapshotRejected("symlink", "acceptance/hidden/li\nnk\x1b[31m")

    monkeypatch.setattr(cli, "promote_author", refuse)
    monkeypatch.setattr(cli, "promote", refuse)
    ctx = cli.Context(
        repo=SimpleNamespace(root="."), runner=object(), sandbox=object(), store=object()
    )
    assert cli.main(["promote", "--role", role, "--json"], context=ctx) == 1
    captured = capsys.readouterr()
    out = json.loads(captured.out)
    assert out["refused"] == "snapshot_rejected" and out["reason"] == "symlink"
    assert out["path"] == "acceptance/hidden/li\nnk\x1b[31m"
    assert "\x1b" not in captured.out and "\x1b" not in captured.err


def test_promote_snapshot_refusal_text_escapes_the_path(monkeypatch, capsys) -> None:
    def refuse(*a, **k):
        raise cli.SnapshotRejected("symlink", "a\x1b[31mb")

    monkeypatch.setattr(cli, "promote_author", refuse)
    ctx = cli.Context(
        repo=SimpleNamespace(root="."), runner=object(), sandbox=object(), store=object()
    )
    assert cli.main(["promote", "--role", "author"], context=ctx) == 1
    err = capsys.readouterr().err
    assert "internal error" not in err and "\x1b" not in err and "symlink" in err


# -- the automatic approval (ADR 0015) ---------------------------------------


def test_parser_accepts_auto() -> None:
    a = cli.build_parser().parse_args(["freeze", "--approve", "p1", "--auto", "--json"])
    assert a.auto is True and a.actor is None


def test_auto_routes_to_the_host_use_case(monkeypatch, capsys) -> None:
    seen = {}

    def fake(repo, sandbox, author_sandbox, store, pid):
        seen["pid"] = pid
        return SimpleNamespace(task_id="t" * 64, seed=b"s")

    monkeypatch.setattr(cli, "auto_approve_freeze", fake)
    monkeypatch.setattr(cli, "approve_freeze", _unreachable)
    code = cli.main(["freeze", "--approve", "p1", "--auto", "--json"], context=_ctx())
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and seen == {"pid": "p1"}
    assert out["approved"] is True


def test_auto_takes_no_actor(capsys) -> None:
    """Naming an actor would let the caller choose the identity an automatic approval records."""
    code = cli.main(
        ["freeze", "--approve", "p1", "--auto", "--as", "cheeseless-dev"], context=_ctx()
    )
    assert code == 2
    assert "--auto" in capsys.readouterr().err


def test_auto_cannot_waive_the_crosscheck(capsys) -> None:
    """`--no-reference` is the operator's waiver; an automatic approval cannot take it."""
    code = cli.main(
        ["freeze", "--approve", "p1", "--auto", "--no-reference", "--reason", "r"],
        context=_ctx(),
    )
    assert code == 2
    assert "--auto" in capsys.readouterr().err


def test_auto_refuses_without_a_strength_review(monkeypatch, capsys) -> None:
    def boom(*a, **k):
        raise AuthorError("no_strength_review", "no review")

    monkeypatch.setattr(cli, "auto_approve_freeze", boom)
    code = cli.main(["freeze", "--approve", "p", "--auto", "--json"], context=_ctx())
    out = json.loads(capsys.readouterr().out)
    assert code == 1 and out["error"] == "no_strength_review"


def _unreachable(*a, **k):
    raise AssertionError("the operator path must not run for --auto")
