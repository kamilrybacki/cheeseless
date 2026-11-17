"""ci-branch end to end on the toy repository (no secrets, no budget)."""

import json
import subprocess
from pathlib import Path

import pytest

from cheeseless.bootstrap import main


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(  # noqa: S603 - fixed argv, test-owned repo
        ["git", "-C", str(repo), *args],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
    )
    return out.stdout.strip()


def _commit(repo: Path, files: dict[str, str]) -> str:
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "--detach", base)
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        _git(repo, "add", "-f", name)
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "c")
    head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", base)
    return head


def _run(repo, monkeypatch, capsys, branch, base, head):
    monkeypatch.chdir(repo)
    code = main(["ci-branch", "--branch", branch, "--base", base, "--head", head, "--json"])
    return code, json.loads(capsys.readouterr().out)


@pytest.fixture
def base(toy_repo: Path) -> str:
    return _git(toy_repo, "rev-parse", "HEAD")


def test_gate_pr_with_source_only_passes(toy_repo, base, monkeypatch, capsys):
    head = _commit(toy_repo, {"src/toy/n.txt": "x"})
    code, out = _run(toy_repo, monkeypatch, capsys, "gate/t1", base, head)
    assert code == 0 and out["ok"] and out["branch_class"] == "gate"


def test_gate_pr_touching_acceptance_fails(toy_repo, base, monkeypatch, capsys):
    head = _commit(toy_repo, {"acceptance/hidden/t.py": "x"})
    code, out = _run(toy_repo, monkeypatch, capsys, "gate/t1", base, head)
    assert code == 1 and out["reason"] == "acceptance_in_gate_pr"


def test_spec_pr_outside_acceptance_fails(toy_repo, base, monkeypatch, capsys):
    head = _commit(toy_repo, {"acceptance/a.txt": "x", ".github/w.yml": "x"})
    code, out = _run(toy_repo, monkeypatch, capsys, "spec/l1", base, head)
    assert code == 1 and out["reason"] == "outside_author_paths"
    assert out["rejected"] == [".github/w.yml"]


def test_unknown_branch_class_fails(toy_repo, base, monkeypatch, capsys):
    head = _commit(toy_repo, {"src/toy/n.txt": "x"})
    code, out = _run(toy_repo, monkeypatch, capsys, "feature/x", base, head)
    assert code == 1 and out["reason"] == "unknown_branch_class"


def test_non_sha_arguments_are_a_usage_error(toy_repo, monkeypatch, capsys):
    monkeypatch.chdir(toy_repo)
    code = main(["ci-branch", "--branch", "gate/t", "--base", "main", "--head", "HEAD"])
    assert code == 2
    assert "40-character" in capsys.readouterr().err
