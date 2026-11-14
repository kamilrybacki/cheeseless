"""The CI judge never runs PR-supplied acceptance material and never leaks hidden data."""

from __future__ import annotations

import base64
import json
import os
import stat
from pathlib import Path

import pytest

from cheeseless import bootstrap_ci
from cheeseless.adapters.outbound.runner.pytest_runner import PytestRunner
from cheeseless.bootstrap import main
from tests.ci_support import (
    LEDGER_REPO,
    CiWorld,
    export_argv,
    holdout_ids,
    judge_cli,
    make_head,
    prepare_world,
    set_judge_event,
)
from tests.requirements import needs
from tests.toy import FIXED_MAIN, HIDDEN_NORMALIZE, HIDDEN_SCORE, commit_all, git, write_files

NEEDLE = "CHEESELESS-" + "CANARY-" + "c1d2e3"  # built at run time, never a literal
BWRAP = os.access("/usr/bin/bwrap", os.X_OK)


class Boom:
    """Constructible, but any use of the sandbox or runner fails the test."""

    def __init__(self, *a, **k):
        pass

    def __getattr__(self, name):
        raise AssertionError(f"sandbox or runner used: {name}")


@pytest.fixture
def world(toy_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CiWorld:
    return prepare_world(toy_repo, tmp_path, monkeypatch)


def test_pr_touching_acceptance_fails_and_never_runs_anything(world, monkeypatch, capsys):
    head = make_head(
        world, {"acceptance/hidden/test_planted.py": f"# {NEEDLE}\ndef test_p():\n    pass\n"}
    )
    monkeypatch.setattr(bootstrap_ci, "BwrapSandbox", Boom)
    monkeypatch.setattr(bootstrap_ci, "PytestRunner", Boom)
    assert judge_cli(world, head, "--json") == 1
    out = capsys.readouterr()
    assert json.loads(out.out)["status"] == "fail"
    assert NEEDLE not in out.out + out.err


@needs(BWRAP, "bubblewrap is required")
def test_pr_conftest_canary_is_never_loaded(world, capsys):
    planted = f"import sys\nprint({NEEDLE!r}, file=sys.stderr)\nraise SystemExit({NEEDLE!r})\n"
    head = make_head(
        world,
        {
            "src/toy/__main__.py": FIXED_MAIN,
            "conftest.py": planted,
            "src/toy/conftest.py": planted,
        },
    )
    assert judge_cli(world, head, "--json") == 0
    out = capsys.readouterr()
    assert NEEDLE not in out.out + out.err


def _loud(source: str) -> str:
    """Every hidden assertion prints the needle and carries it as its failure message."""
    out = []
    for line in source.splitlines():
        if line.startswith("    assert "):
            out.append(f"    print({NEEDLE!r})")
            line = f"{line}, {NEEDLE!r}"
        out.append(line)
    return "\n".join(out) + "\n"


@pytest.fixture
def loud_world(toy_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CiWorld:
    write_files(
        toy_repo,
        {
            "acceptance/hidden/test_normalize.py": _loud(HIDDEN_NORMALIZE),
            "acceptance/hidden/test_score.py": _loud(HIDDEN_SCORE),
        },
    )
    commit_all(toy_repo, "loud hidden tests")
    return prepare_world(toy_repo, tmp_path, monkeypatch)


@needs(BWRAP, "bubblewrap is required")
def test_output_has_no_holdout_ids_or_canary(loud_world, monkeypatch, capsys):
    seen: list[str] = []

    class Recording(PytestRunner):
        def run(self, *a, **k):
            result = super().run(*a, **k)
            seen.append(result.output)
            return result

    monkeypatch.setattr(bootstrap_ci, "PytestRunner", Recording)
    head = make_head(loud_world, {"src/toy/__main__.py": "def main(argv):\n    return 1\n"})
    assert judge_cli(loud_world, head, "--json") == 1
    out = capsys.readouterr()
    # The probe is live: failing hidden tests really emitted the needle to the runner.
    assert any(NEEDLE in o for o in seen)
    text = out.out + out.err
    assert NEEDLE not in text
    ids = holdout_ids(loud_world)
    assert ids
    for test_id in ids:
        assert test_id not in text
        assert test_id.split("::")[-1] not in text
    assert set(json.loads(out.out)) == {"status", "criteria"}


def test_export_refuses_paths_inside_repo_and_git_dir(world, capsys):
    for target in (
        world.toy / "bundle.json",
        world.toy / ".git" / "bundle.json",
        world.toy / "sub" / "bundle.json",
    ):
        assert main(export_argv(world.state, target)) == 2
        assert not target.exists()
    capsys.readouterr()


def test_export_file_is_private_never_printed_and_exclusive(world, tmp_path, capsys):
    mode = stat.S_IMODE(world.bundle.stat().st_mode)
    assert mode == 0o600
    key = json.loads(world.bundle.read_bytes())["key"]
    assert key not in capsys.readouterr().out
    again = main(export_argv(world.state, world.bundle, world.root))
    assert again == 2


@pytest.mark.parametrize(
    ("repo", "root"),
    [
        ("not a repo", None),
        ("../escape", None),
        (LEDGER_REPO, "ROOT"),
        (LEDGER_REPO, "a" * 39),
    ],
)
def test_export_refuses_an_invalid_budget_ledger(world, tmp_path, repo, root, capsys):
    target = tmp_path / "bad-ledger.json"
    argv = export_argv(world.state, target, root or world.root)
    argv[argv.index("--budget-repo") + 1] = repo
    assert main(argv) == 2
    assert not target.exists()
    assert "budget ledger" in capsys.readouterr().err


def test_export_pins_the_ledger_branch_of_the_lineage(world):
    data = json.loads(world.bundle.read_bytes())
    assert data["budget"] == {
        "repo": LEDGER_REPO,
        "branch": f"cheeseless/budget/{data['lineage']}",
        "root": world.root,
    }


def test_export_refuses_symlink_into_repo(world, tmp_path):
    link = tmp_path / "link"
    link.symlink_to(world.toy)
    target = link / "b.json"
    assert main(export_argv(world.state, target)) == 2


def _bad_bundle(world: CiWorld, tmp_path: Path, mutate) -> Path:
    data = json.loads(world.bundle.read_bytes())
    mutate(data)
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(data))
    return path


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(key=base64.b64encode(b"short").decode()),
        lambda d: d["task"].update(baseline_commit="f" * 40),
        lambda d: d.update(version=9),
        lambda d: d["task"]["holdout"].clear(),
    ],
)
def test_tampered_or_short_key_bundle_is_refused(world, tmp_path, monkeypatch, mutate):
    head = make_head(world, {"src/toy/note.txt": "x"})
    bad = _bad_bundle(world, tmp_path, mutate)
    monkeypatch.setattr(bootstrap_ci, "BwrapSandbox", Boom)
    monkeypatch.setattr(bootstrap_ci, "PytestRunner", Boom)
    assert judge_cli(world, head, bundle=bad) == 2


def test_missing_bundle_is_refused(world, monkeypatch):
    head = make_head(world, {"src/toy/note.txt": "x"})
    monkeypatch.delenv(bootstrap_ci.BUNDLE_ENV, raising=False)
    assert main(["ci-judge", "--base", world.base, "--head", head]) == 2


def test_freeze_drift_exits_3(world, monkeypatch):
    from tests.toy import commit_all, git, write_files

    git(world.toy, "checkout", "-q", "main")
    write_files(world.toy, {"acceptance/hidden/test_drift.py": "def test_d():\n    pass\n"})
    drift = commit_all(world.toy, "drift")
    monkeypatch.setattr(bootstrap_ci, "BwrapSandbox", Boom)
    set_judge_event(world)
    argv = ["ci-judge", "--base", drift, "--head", drift, "--bundle-file", str(world.bundle)]
    assert main(argv) == 3


def _advance_main(world: CiWorld, files: dict[str, str]) -> str:
    git(world.toy, "checkout", "-q", "main")
    write_files(world.toy, files)
    return commit_all(world.toy, "main moves on")


def test_acceptance_touch_is_caught_after_main_moves_on(world, monkeypatch, capsys):
    # The protected diff is measured from the merge base, so a moving default tip changes nothing.
    head = make_head(
        world, {"acceptance/hidden/test_planted.py": f"# {NEEDLE}\ndef test_p():\n    pass\n"}
    )
    tip = _advance_main(world, {"README.md": "moved on\n"})
    monkeypatch.setattr(bootstrap_ci, "BwrapSandbox", Boom)
    monkeypatch.setattr(bootstrap_ci, "PytestRunner", Boom)
    assert judge_cli(world, head, "--json", base=tip) == 1
    out = capsys.readouterr()
    assert json.loads(out.out)["status"] == "fail"
    assert NEEDLE not in out.out + out.err


def test_workflow_change_identical_to_main_is_still_a_pr_touch(world, monkeypatch, capsys):
    # Regression: a diff against the default tip would see no change here.
    workflow = {".github/workflows/same.yml": f"# {NEEDLE}\non: push\n"}
    head = make_head(world, workflow)
    tip = _advance_main(world, workflow)
    monkeypatch.setattr(bootstrap_ci, "BwrapSandbox", Boom)
    monkeypatch.setattr(bootstrap_ci, "PytestRunner", Boom)
    assert judge_cli(world, head, "--json", base=tip) == 1
    out = capsys.readouterr()
    assert json.loads(out.out)["status"] == "fail"
    assert NEEDLE not in out.out + out.err
