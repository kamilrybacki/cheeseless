"""ci-judge end to end on the toy repository."""

import json
import os
from pathlib import Path

import pytest

from cheeseless import bootstrap_ci
from cheeseless.bootstrap import main
from cheeseless.domain.evaluation import SuiteRun
from tests.ci_support import (
    FAKE_BUDGET_TOKEN,
    LEDGER_REPO,
    CiWorld,
    FakeRunner,
    FakeSandbox,
    judge_cli,
    make_head,
    prepare_world,
    set_judge_event,
)
from tests.requirements import needs
from tests.toy import FIXED_MAIN

BWRAP = os.access("/usr/bin/bwrap", os.X_OK)
_REAL_FACTORY = bootstrap_ci.make_ledger_transport


@needs(BWRAP, "bubblewrap is required")
def test_unfixed_head_fails_and_fixed_head_passes(
    toy_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    world = prepare_world(toy_repo, tmp_path, monkeypatch)
    capsys.readouterr()
    unfixed = make_head(world, {"src/toy/note.txt": "unrelated"}, branch="unfixed")
    assert judge_cli(world, unfixed, "--json") == 1
    assert json.loads(capsys.readouterr().out)["status"] == "fail"
    fixed = make_head(world, {"src/toy/__main__.py": FIXED_MAIN}, branch="fixed")
    assert judge_cli(world, fixed, "--json") == 0
    verdict = json.loads(capsys.readouterr().out)
    assert verdict["status"] == "pass"
    assert {c["status"] for c in verdict["criteria"]} == {"pass"}


# -- budget counter wiring (P10-27) ---------------------------------------------


class Boom:
    """Constructible, but any use of the sandbox or runner fails the test."""

    def __init__(self, *a, **k):
        pass

    def __getattr__(self, name):
        raise AssertionError(f"sandbox or runner used: {name}")


@pytest.fixture
def world(toy_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CiWorld:
    world = prepare_world(toy_repo, tmp_path, monkeypatch)
    monkeypatch.setattr(bootstrap_ci, "BwrapSandbox", Boom)
    monkeypatch.setattr(bootstrap_ci, "PytestRunner", Boom)
    return world


def _judge_raw(world: CiWorld, head: str) -> int:
    """ci-judge without resetting the event env (the test arranged it)."""
    argv = ["ci-judge", "--base", world.base, "--head", head]
    return main([*argv, "--bundle-file", str(world.bundle)])


@pytest.mark.parametrize(
    ("name", "value"),
    [
        (bootstrap_ci.BUDGET_TOKEN_ENV, None),
        (bootstrap_ci.BUDGET_TOKEN_ENV, ""),
        ("GITHUB_REPOSITORY_ID", None),
        ("GITHUB_REPOSITORY_ID", "0123"),
        ("GITHUB_REPOSITORY_ID", "owner/name"),
        ("GITHUB_RUN_ID", None),
        ("GITHUB_RUN_ID", "12a"),
        ("GITHUB_RUN_ATTEMPT", None),
        ("GITHUB_RUN_ATTEMPT", "-1"),
    ],
)
def test_missing_or_invalid_judge_env_is_refused_before_any_work(world, capsys, name, value):
    head = make_head(world, {"src/toy/note.txt": "x"})
    set_judge_event(world)
    if value is None:
        world.monkeypatch.delenv(name, raising=False)
    else:
        world.monkeypatch.setenv(name, value)
    assert _judge_raw(world, head) == 2
    assert world.ledger.reads == 0
    assert world.ledger.updates_sent == 0
    err = capsys.readouterr().err
    assert FAKE_BUDGET_TOKEN not in err


def test_a_malformed_budget_token_is_refused_by_the_real_transport(world, capsys):
    head = make_head(world, {"src/toy/note.txt": "x"})
    set_judge_event(world)
    world.monkeypatch.setattr(bootstrap_ci, "make_ledger_transport", _REAL_FACTORY)
    world.monkeypatch.setenv(bootstrap_ci.BUDGET_TOKEN_ENV, "not a token\n")
    assert _judge_raw(world, head) == 2
    assert "not a token" not in capsys.readouterr().err


def test_the_budget_token_leaves_the_environment_before_the_sandbox_exists(world):
    seen: dict[str, object] = {}

    class Probe(Boom):
        def __init__(self, *a, **k):
            seen["token_in_env"] = bootstrap_ci.BUDGET_TOKEN_ENV in os.environ

    def factory(repo, token):
        seen["repo"], seen["token"] = repo, token
        seen["token_in_env_at_factory"] = bootstrap_ci.BUDGET_TOKEN_ENV in os.environ
        return world.ledger

    head = make_head(world, {"src/toy/note.txt": "x"})
    set_judge_event(world)
    world.monkeypatch.setattr(bootstrap_ci, "BwrapSandbox", Probe)
    world.monkeypatch.setattr(bootstrap_ci, "make_ledger_transport", factory)
    _judge_raw(world, head)
    assert seen["token"] == FAKE_BUDGET_TOKEN
    assert seen["repo"] == LEDGER_REPO
    assert seen["token_in_env_at_factory"] is False
    assert seen["token_in_env"] is False
    assert bootstrap_ci.BUDGET_TOKEN_ENV not in os.environ


def test_the_budget_token_is_removed_even_when_the_arguments_are_refused(world):
    set_judge_event(world)
    assert main(["ci-judge", "--base", "nope", "--head", "nope"]) == 2
    assert bootstrap_ci.BUDGET_TOKEN_ENV not in os.environ


def test_a_protected_path_touch_never_reads_the_ledger(world, capsys):
    head = make_head(world, {"acceptance/hidden/test_planted.py": "def test_p():\n    pass\n"})
    assert judge_cli(world, head) == 1
    assert world.ledger.reads == 0
    assert len(world.ledger.chain(_branch(world))) == 1


def test_a_missing_ledger_branch_blocks_without_a_charge(world, capsys):
    head = make_head(world, {"src/toy/note.txt": "x"})
    del world.ledger.refs[_branch(world)]
    # Smoke must pass to reach the reservation; stub sandbox and runner just enough.
    _green_pre_hidden(world)
    assert judge_cli(world, head, "--json") == 1
    out = capsys.readouterr()
    assert json.loads(out.out)["status"] == "blocked"
    assert "reason: infra_error" in out.err


def _branch(world: CiWorld) -> str:
    return json.loads(world.bundle.read_bytes())["budget"]["branch"]


def _green_pre_hidden(world: CiWorld, hidden: object = None) -> FakeRunner:
    """No-isolation fakes: build ok, smoke green, the hidden run scripted (never candidate code)."""
    runner = FakeRunner(hidden=hidden)
    world.monkeypatch.setattr(bootstrap_ci, "BwrapSandbox", lambda: FakeSandbox())
    world.monkeypatch.setattr(bootstrap_ci, "PytestRunner", lambda: runner)
    return runner


def test_a_reserved_run_is_charged_on_the_ledger_and_reported(world, capsys):
    head = make_head(world, {"src/toy/note.txt": "x"})
    runner = _green_pre_hidden(world, hidden=SuiteRun(outcomes={}))
    assert judge_cli(world, head, "--json") == 1
    assert runner.hidden_calls == 1
    assert len(world.ledger.chain(_branch(world))) == 2
    limit = json.loads(world.bundle.read_bytes())["ci_limit"]
    assert f"budget used 1 of {limit}" in capsys.readouterr().err


def test_a_failure_before_hidden_never_charges(world, capsys):
    head = make_head(world, {"src/toy/note.txt": "x"})
    world.monkeypatch.setattr(bootstrap_ci, "BwrapSandbox", lambda: FakeSandbox(build="fail"))
    world.monkeypatch.setattr(bootstrap_ci, "PytestRunner", lambda: FakeRunner())
    assert judge_cli(world, head) == 1
    assert world.ledger.updates_sent == 0
    assert len(world.ledger.chain(_branch(world))) == 1


def test_a_pass_that_fell_off_the_chain_is_blocked(world, capsys, monkeypatch):
    from cheeseless.adapters.outbound.ci.ledger_counter import LedgerCounter

    head = make_head(world, {"src/toy/note.txt": "x"})
    _green_pre_hidden(world, hidden=SuiteRun(outcomes={}))
    monkeypatch.setattr(LedgerCounter, "confirm_still_on_chain", lambda self: False)
    from cheeseless.app import ci_judge as app_judge
    from cheeseless.domain.status import Status

    real = app_judge.judge

    def passing(*a, **k):
        verdict = real(*a, **k)
        return app_judge.CiVerdict(Status.PASS, verdict.tree_hash, app_judge.JudgeReason.OK)

    monkeypatch.setattr(bootstrap_ci, "judge", passing)
    assert judge_cli(world, head, "--json") == 1
    out = capsys.readouterr()
    assert json.loads(out.out)["status"] == "blocked"
    assert "reason: infra_error" in out.err
