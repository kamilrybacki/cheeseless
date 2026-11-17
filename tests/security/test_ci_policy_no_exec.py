"""The path policy that decides the spec/ gate runs no PR code and reveals no hidden content (S18).

`ci-branch --check-payload --no-author-check` is what the unprivileged `policy` job runs. Its
result must come from tree comparison only: acceptance content carrying an executable marker is
never run, a spec PR that reaches outside acceptance/ is a failure, and the payload carries no
canary or sentinel text.
"""

import json
import subprocess
from pathlib import Path

import pytest

from cheeseless.bootstrap import main
from tests.toy import CANARY, SENTINEL


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


def _policy(
    repo: Path, monkeypatch, capsys, branch: str, head: str, *, author_check: bool = False
) -> tuple[int, str]:
    monkeypatch.chdir(repo)
    base = _git(repo, "rev-parse", "HEAD")
    argv = ["ci-branch", "--branch", branch, "--base", base, "--head", head, "--check-payload"]
    code = main(argv if author_check else [*argv, "--no-author-check"])
    return code, capsys.readouterr().out


class _Spies:
    """Records every construction of the sandbox and every call of the author check."""

    def __init__(self) -> None:
        self.sandboxes = 0
        self.checks = 0

    def install(self, monkeypatch) -> None:
        spies = self

        class SpySandbox:
            def __init__(self, *args: object, **kwargs: object) -> None:
                spies.sandboxes += 1

        def spy_author_check(*args: object, **kwargs: object) -> None:
            spies.checks += 1
            raise RuntimeError("spy: the author check must not run here")

        monkeypatch.setattr("cheeseless.bootstrap_ci.BwrapSandbox", SpySandbox)
        monkeypatch.setattr("cheeseless.bootstrap_ci.author_check", spy_author_check)


def test_the_policy_path_never_builds_a_sandbox_or_calls_the_author_check(
    toy_repo, monkeypatch, capsys
):
    """S23: with --no-author-check nothing that can execute acceptance content is even built.

    A marker-file test cannot prove this: bwrap would block the write. The spies can."""
    spies = _Spies()
    spies.install(monkeypatch)
    head = _commit(toy_repo, {"acceptance/hidden/test_x.py": "def test_x(candidate):\n    pass\n"})
    code, out = _policy(toy_repo, monkeypatch, capsys, "spec/L1", head)
    assert code == 0 and json.loads(out)["conclusion"] == "success"
    assert (spies.sandboxes, spies.checks) == (0, 0)


def test_control_without_the_flag_the_spies_do_fire(toy_repo, monkeypatch, capsys):
    """Control: the spies are wired to the real call sites, so a zero above is evidence."""
    spies = _Spies()
    spies.install(monkeypatch)
    head = _commit(toy_repo, {"acceptance/hidden/test_x.py": "def test_x(candidate):\n    pass\n"})
    _policy(toy_repo, monkeypatch, capsys, "spec/L1", head, author_check=True)
    assert spies.sandboxes == 1 and spies.checks == 1


def test_the_policy_path_never_executes_acceptance_content(toy_repo, monkeypatch, capsys):
    marker = toy_repo / "pwned-marker"
    exploit = (
        "import pathlib\n"
        f"pathlib.Path({str(marker)!r}).write_text('ran')\n"
        "def test_x(candidate):\n    pass\n"
    )
    head = _commit(
        toy_repo, {"acceptance/hidden/test_x.py": exploit, "acceptance/conftest.py": exploit}
    )
    code, out = _policy(toy_repo, monkeypatch, capsys, "spec/L1", head)
    assert code == 0 and json.loads(out)["conclusion"] == "success"
    assert not marker.exists()


@pytest.mark.parametrize("path", ["src/toy/evil.py", ".github/workflows/x.yml", "pyproject.toml"])
def test_a_spec_pr_reaching_outside_acceptance_is_a_failure(toy_repo, monkeypatch, capsys, path):
    head = _commit(toy_repo, {"acceptance/a.txt": "x", path: "x"})
    code, out = _policy(toy_repo, monkeypatch, capsys, "spec/L1", head)
    assert code == 0 and json.loads(out)["conclusion"] == "failure"


def test_a_gate_pr_touching_acceptance_is_a_failure(toy_repo, monkeypatch, capsys):
    head = _commit(toy_repo, {"acceptance/hidden/t.py": "x", "src/toy/n.txt": "x"})
    code, out = _policy(toy_repo, monkeypatch, capsys, "gate/t1", head)
    assert code == 0 and json.loads(out)["conclusion"] == "failure"


@pytest.mark.parametrize("branch", ["spec/L1", "gate/t1", "feature/x"])
def test_the_policy_payload_carries_no_canary_or_sentinel_content(
    toy_repo, monkeypatch, capsys, branch
):
    leaky = f"# {CANARY} {SENTINEL}\n"
    head = _commit(toy_repo, {"acceptance/hidden/t.txt": leaky, "src/toy/n.txt": leaky})
    _, out = _policy(toy_repo, monkeypatch, capsys, branch, head)
    assert CANARY not in out and SENTINEL not in out
