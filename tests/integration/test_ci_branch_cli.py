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


def _payload(repo, monkeypatch, capsys, branch, base, head):
    monkeypatch.chdir(repo)
    args = ["ci-branch", "--branch", branch, "--base", base, "--head", head, "--check-payload"]
    code = main(args)
    return code, json.loads(capsys.readouterr().out)


def test_check_payload_for_a_clean_spec_pr_is_a_branch_policy_success(
    toy_repo, base, monkeypatch, capsys
):
    head = _commit(toy_repo, {"acceptance/a.txt": "x"})
    code, out = _payload(toy_repo, monkeypatch, capsys, "spec/L1", base, head)
    assert code == 0 and out["name"] == "cheeseless branch policy"
    assert out["head_sha"] == head and out["conclusion"] == "success"


def test_check_payload_for_an_unknown_class_is_a_failure_and_still_exits_zero(
    toy_repo, base, monkeypatch, capsys
):
    head = _commit(toy_repo, {"src/toy/n.txt": "x"})
    code, out = _payload(toy_repo, monkeypatch, capsys, "feature/x", base, head)
    assert code == 0 and out["conclusion"] == "failure"


def test_check_payload_fails_closed_when_the_check_itself_breaks(
    toy_repo, base, monkeypatch, capsys
):
    code, out = _payload(toy_repo, monkeypatch, capsys, "spec/L1", base, "f" * 40)
    assert code == 0 and out["conclusion"] == "failure"
    assert out["head_sha"] == "f" * 40


def test_ci_route_validates_the_live_pr_from_stdin(monkeypatch, capsys):
    import io

    head = "a" * 40
    pr = {
        "number": 3,
        "state": "open",
        "base": {"ref": "main", "sha": "b" * 40, "repo": {"full_name": "o/r"}},
        "head": {"ref": "spec/L1", "sha": head, "repo": {"full_name": "o/r"}},
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps([pr])))
    code = main(["ci-route", "--head", head, "--default-branch", "main", "--repo", "o/r"])
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["branch_class"] == "spec" and out["run_judge"] is False

    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    assert main(["ci-route", "--head", head, "--default-branch", "main", "--repo", "o/r"]) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False


def _gate(tmp_path, capsys, *extra, head="a" * 40, cls="spec"):
    policy = tmp_path / "policy.json"
    args = ["ci-gate", "--head", head, "--branch-class", cls, "--policy", str(policy), *extra]
    if "--guards-ok" not in extra:
        args += ["--guards-ok", "true"]
    code = main(args)
    return code, {c["name"]: c for c in json.loads(capsys.readouterr().out)}


def test_ci_gate_spec_success_is_labelled_not_an_implementation_verdict(tmp_path, capsys):
    from cheeseless.app.ci_branch import BranchOutcome, check_payload

    head = "a" * 40
    (tmp_path / "policy.json").write_text(
        json.dumps(check_payload(BranchOutcome(True, "ok", "spec"), head))
    )
    code, checks = _gate(tmp_path, capsys)
    gate = checks["cheeseless gate"]
    assert code == 0 and gate["conclusion"] == "success"
    assert "NOT an implementation verdict" in gate["output"]["title"]


def test_ci_gate_fails_closed_on_a_missing_policy_file(tmp_path, capsys):
    code, checks = _gate(tmp_path, capsys, cls="gate")
    assert code == 0 and checks["cheeseless gate"]["conclusion"] == "failure"


def test_ci_gate_gate_class_needs_the_judge_pass(tmp_path, capsys):
    from cheeseless.app.ci_branch import BranchOutcome, check_payload

    head = "a" * 40
    (tmp_path / "policy.json").write_text(
        json.dumps(check_payload(BranchOutcome(True, "ok", "gate"), head))
    )
    (tmp_path / "v.json").write_text(
        json.dumps({"status": "pass", "criteria": [], "judged_base": "b" * 40})
    )
    _, ok = _gate(
        tmp_path,
        capsys,
        "--judge-code",
        "0",
        "--verdict",
        str(tmp_path / "v.json"),
        "--current-base",
        "b" * 40,
        cls="gate",
    )
    assert ok["cheeseless gate"]["conclusion"] == "success"
    _, bad = _gate(
        tmp_path,
        capsys,
        "--judge-code",
        "1",
        "--verdict",
        str(tmp_path / "v.json"),
        "--current-base",
        "b" * 40,
        cls="gate",
    )
    assert bad["cheeseless gate"]["conclusion"] == "failure"


def test_ci_gate_rejects_a_non_sha_head(tmp_path, capsys):
    code = main(
        [
            "ci-gate",
            "--head",
            "main",
            "--branch-class",
            "spec",
            "--policy",
            "x",
            "--guards-ok",
            "true",
        ]
    )
    assert code == 2


def _policy(repo, monkeypatch, capsys, branch, base, head):
    monkeypatch.chdir(repo)
    args = ["ci-branch", "--branch", branch, "--base", base, "--head", head]
    code = main([*args, "--check-payload", "--no-author-check"])
    return code, json.loads(capsys.readouterr().out)


def test_no_author_check_policy_passes_a_clean_spec_pr(toy_repo, base, monkeypatch, capsys):
    head = _commit(toy_repo, {"acceptance/a.txt": "x"})
    code, out = _policy(toy_repo, monkeypatch, capsys, "spec/L1", base, head)
    assert code == 0 and out["conclusion"] == "success"


def test_no_author_check_policy_fails_a_spec_pr_touching_src(toy_repo, base, monkeypatch, capsys):
    head = _commit(toy_repo, {"acceptance/a.txt": "x", "src/toy/n.txt": "x"})
    code, out = _policy(toy_repo, monkeypatch, capsys, "spec/L1", base, head)
    assert code == 0 and out["conclusion"] == "failure"


def test_a_forged_success_policy_for_a_diff_touching_src_still_fails_the_gate(
    toy_repo, base, monkeypatch, capsys, tmp_path
):
    """A policy payload carrying another head SHA is rejected by ci-gate (head-SHA binding).

    This does NOT test S18: that protection is structural (``post`` reads the policy job's output,
    never a file a sandboxed job could write) and is covered by the workflow-text tests."""
    head = _commit(toy_repo, {"acceptance/a.txt": "x", "src/toy/n.txt": "x"})
    _, real = _policy(toy_repo, monkeypatch, capsys, "spec/L1", base, head)
    (tmp_path / "policy.json").write_text(json.dumps(real))
    _, checks = _gate(tmp_path, capsys, head=head, cls="spec")
    assert checks["cheeseless gate"]["conclusion"] == "failure"
    forged = {**real, "conclusion": "success", "head_sha": "c" * 40}
    (tmp_path / "policy.json").write_text(json.dumps(forged))
    _, checks = _gate(tmp_path, capsys, head=head, cls="spec")
    assert checks["cheeseless gate"]["conclusion"] == "failure"


def test_ci_gate_gate_class_fails_on_a_moved_base_or_a_failed_guard(tmp_path, capsys):
    from cheeseless.app.ci_branch import BranchOutcome, check_payload

    head = "a" * 40
    (tmp_path / "policy.json").write_text(
        json.dumps(check_payload(BranchOutcome(True, "ok", "gate"), head))
    )
    (tmp_path / "v.json").write_text(
        json.dumps({"status": "pass", "criteria": [], "judged_base": "b" * 40})
    )
    common = ["--judge-code", "0", "--verdict", str(tmp_path / "v.json")]
    _, moved = _gate(tmp_path, capsys, *common, "--current-base", "c" * 40, cls="gate")
    assert moved["cheeseless gate"]["conclusion"] == "failure"
    _, guard = _gate(
        tmp_path, capsys, *common, "--current-base", "b" * 40, "--guards-ok", "false", cls="gate"
    )
    assert guard["cheeseless gate"]["conclusion"] == "failure"


def test_ci_gate_requires_an_explicit_guards_flag(tmp_path, capsys):
    """S24: a missing --guards-ok must not default to 'all upstream jobs were fine'."""
    args = [
        "ci-gate",
        "--head",
        "a" * 40,
        "--branch-class",
        "spec",
        "--policy",
        str(tmp_path / "p"),
    ]
    with pytest.raises(SystemExit) as err:
        main(args)
    assert err.value.code != 0


def _spy_author(monkeypatch, result=None, exc=None):
    """Replace the sandbox and the author check in bootstrap_ci; records the kwargs it saw."""
    from cheeseless.app.authoring import AuthorCheckResult

    seen: dict[str, object] = {}

    def fake(repo, sandbox, source, **kwargs):
        seen.update(kwargs)
        if exc is not None:
            raise exc
        return result or AuthorCheckResult(True, (), 1)

    monkeypatch.setattr("cheeseless.bootstrap_ci.BwrapSandbox", lambda: object())
    monkeypatch.setattr("cheeseless.bootstrap_ci.author_check", fake)
    return seen


def _author_run(repo, monkeypatch, capsys, tmp_path, branch, base, head, *extra):
    out = tmp_path / "out"
    monkeypatch.chdir(repo)
    code = main(
        ["ci-branch", "--branch", branch, "--base", base, "--head", head, "--check-payload"]
        + ["--author-output", str(out), *extra]
    )
    stdout = capsys.readouterr().out
    return code, stdout, out.read_text() if out.exists() else ""


@pytest.mark.parametrize(
    ("result", "line"),
    [
        (("ok", ()), "author=pass\n"),
        (("fail", ("C1 has no test",)), "author=fail\n"),
        (("unavailable", ("no result",)), "author=unavailable\n"),
    ],
)
def test_spec_pr_writes_the_typed_author_status_and_no_problem_text(
    toy_repo, base, monkeypatch, capsys, tmp_path, result, line
):
    from cheeseless.app.authoring import AuthorCheckResult

    kind, problems = result
    res = {
        "ok": AuthorCheckResult(True, (), 1),
        "fail": AuthorCheckResult(False, problems, 1),
        "unavailable": AuthorCheckResult(False, problems, 0, unavailable=True),
    }[kind]
    seen = _spy_author(monkeypatch, res)
    head = _commit(toy_repo, {"acceptance/a.txt": "x"})
    code, stdout, written = _author_run(
        toy_repo, monkeypatch, capsys, tmp_path, "spec/L1", base, head
    )
    assert code == 0 and written == line
    assert seen == {"contain_failures": True}
    assert "no test" not in stdout and "no result" not in stdout


def test_a_crashing_author_check_writes_unavailable(toy_repo, base, monkeypatch, capsys, tmp_path):
    _spy_author(monkeypatch, exc=RuntimeError("::error::boom"))
    head = _commit(toy_repo, {"acceptance/a.txt": "x"})
    _, stdout, written = _author_run(toy_repo, monkeypatch, capsys, tmp_path, "spec/L1", base, head)
    assert written == "author=unavailable\n" and "boom" not in stdout


def test_gate_pr_and_skipped_check_write_no_author_line(
    toy_repo, base, monkeypatch, capsys, tmp_path
):
    _spy_author(monkeypatch)
    head = _commit(toy_repo, {"src/toy/n.txt": "x"})
    assert _author_run(toy_repo, monkeypatch, capsys, tmp_path, "gate/t1", base, head)[2] == ""
    spec = _commit(toy_repo, {"acceptance/a.txt": "x"})
    skipped = _author_run(
        toy_repo, monkeypatch, capsys, tmp_path, "spec/L1", base, spec, "--no-author-check"
    )
    assert skipped[2] == ""


def test_a_refused_spec_pr_writes_no_author_line(toy_repo, base, monkeypatch, capsys, tmp_path):
    _spy_author(monkeypatch)
    head = _commit(toy_repo, {"acceptance/a.txt": "x", ".github/w.yml": "x"})
    assert _author_run(toy_repo, monkeypatch, capsys, tmp_path, "spec/L1", base, head)[2] == ""


def _spec_policy(tmp_path):
    from cheeseless.app.ci_branch import BranchOutcome, check_payload

    (tmp_path / "policy.json").write_text(
        json.dumps(check_payload(BranchOutcome(True, "ok", "spec"), "a" * 40))
    )


@pytest.mark.parametrize(
    ("author", "result", "conclusion"),
    [
        ("pass", "success", "success"),
        ("fail", "success", "failure"),
        ("unavailable", "success", "success"),
        ("", "success", "success"),
        ("fail", "failure", "success"),
        ("-x", "success", "success"),
    ],
)
def test_ci_gate_reads_the_author_status(tmp_path, capsys, author, result, conclusion):
    _spec_policy(tmp_path)
    code, checks = _gate(tmp_path, capsys, f"--author={author}", "--evaluate-result", result)
    assert code == 0 and checks["cheeseless gate"]["conclusion"] == conclusion


def test_ci_gate_without_author_arguments_warns_but_does_not_block(tmp_path, capsys):
    _spec_policy(tmp_path)
    _, checks = _gate(tmp_path, capsys)
    gate = checks["cheeseless gate"]
    assert gate["conclusion"] == "success" and "Warning" in gate["output"]["summary"]
