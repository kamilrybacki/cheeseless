"""``ci-budget-init``: the operator seeds the CI budget ledger from the local store (P10-29).

The command needs the store key, so it runs on the operator's machine. It must keep the
ledger token out of the environment and out of every output, refuse anything but a fresh
seed of this repository and lineage, and print the root that ``ci-export`` pins.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import pytest

from cheeseless import bootstrap_ci
from cheeseless.adapters.outbound.ci.github_ledger import TransportError
from cheeseless.bootstrap import main
from cheeseless.domain.budget_ledger import LedgerIdentity, branch_for, derive_key, run_record
from cheeseless.domain.evaluation import SuiteRun, TestOutcome
from tests.ci_support import (
    FAKE_BUDGET_TOKEN,
    JUDGED_REPO_ID,
    LEDGER_REPO,
    CiWorld,
    FakeLedgerTransport,
    FakeRunner,
    FakeSandbox,
    export_argv,
    judge_cli,
    make_head,
)
from tests.toy import freeze_without_approval, git


@pytest.fixture
def frozen(toy_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A frozen toy repository; returns the state directory."""
    state = tmp_path / "state"
    monkeypatch.chdir(toy_repo)
    assert freeze_without_approval(str(state)) == 0
    return state


@pytest.fixture
def ledger(monkeypatch: pytest.MonkeyPatch) -> FakeLedgerTransport:
    fake = FakeLedgerTransport()
    monkeypatch.setattr(bootstrap_ci, "make_ledger_seeder", lambda repo, token: fake)
    monkeypatch.setenv(bootstrap_ci.BUDGET_TOKEN_ENV, FAKE_BUDGET_TOKEN)
    return fake


def _init(state: Path, *, repo: str = LEDGER_REPO, repo_id: str = JUDGED_REPO_ID) -> int:
    """Run ``ci-budget-init``. The command pops the token, so re-runs must set it again."""
    argv = ["--state-dir", str(state), "ci-budget-init", "--budget-repo", repo]
    return main([*argv, "--repo-id", repo_id])


def _probe(state: Path, tmp_path: Path) -> dict:
    probe = tmp_path / "probe.json"
    assert main(export_argv(state, probe)) == 0
    data = json.loads(probe.read_bytes())
    probe.unlink()
    return data


def test_init_prints_the_root_that_export_pins_and_the_judge_accepts(
    frozen: Path,
    ledger: FakeLedgerTransport,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert _init(frozen) == 0
    root = capsys.readouterr().out.strip()
    branch = branch_for(_probe(frozen, tmp_path)["lineage"])
    assert ledger.refs == {branch: root}

    bundle = tmp_path / "bundle.json"
    assert main(export_argv(frozen, bundle, root)) == 0
    task = json.loads(bundle.read_bytes())["task"]
    ids = [*task["visible"], *task["holdout"]]
    runner = FakeRunner(hidden=SuiteRun(outcomes=dict.fromkeys(ids, TestOutcome.PASSED)))
    monkeypatch.setattr(bootstrap_ci, "BwrapSandbox", FakeSandbox)
    monkeypatch.setattr(bootstrap_ci, "PytestRunner", lambda: runner)
    toy = Path.cwd()
    world = CiWorld(toy, frozen, bundle, git(toy, "rev-parse", "HEAD"), ledger, root, monkeypatch)
    head = make_head(world, {"src/toy/extra.py": "X = 1\n"})
    assert judge_cli(world, head) == 0
    assert runner.hidden_calls == 1
    assert len(ledger.chain(branch)) == 2


def test_init_is_idempotent(
    frozen: Path,
    ledger: FakeLedgerTransport,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert _init(frozen) == 0
    first = capsys.readouterr()
    monkeypatch.setenv(bootstrap_ci.BUDGET_TOKEN_ENV, FAKE_BUDGET_TOKEN)
    assert _init(frozen) == 0
    again = capsys.readouterr()
    assert again.out == first.out
    assert "already seeded" in again.err
    assert ledger.creates_sent == 1


def test_the_token_leaves_the_environment_and_never_reaches_output(
    frozen: Path, ledger: FakeLedgerTransport, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: list[str | None] = []
    real_write_root = ledger.write_root

    def spy(**kwargs: object) -> str:
        seen.append(os.environ.get(bootstrap_ci.BUDGET_TOKEN_ENV))
        return real_write_root(**kwargs)  # type: ignore[arg-type]

    ledger.write_root = spy  # type: ignore[method-assign]
    assert _init(frozen) == 0
    assert seen == [None]
    assert bootstrap_ci.BUDGET_TOKEN_ENV not in os.environ
    out = capsys.readouterr()
    assert FAKE_BUDGET_TOKEN not in out.out + out.err


def test_the_seeder_gets_the_ledger_repo_and_the_token(
    frozen: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, str]] = []
    fake = FakeLedgerTransport()

    def factory(repo: str, token: str) -> FakeLedgerTransport:
        calls.append((repo, token))
        return fake

    monkeypatch.setattr(bootstrap_ci, "make_ledger_seeder", factory)
    monkeypatch.setenv(bootstrap_ci.BUDGET_TOKEN_ENV, FAKE_BUDGET_TOKEN)
    assert _init(frozen) == 0
    assert calls == [(LEDGER_REPO, FAKE_BUDGET_TOKEN)]


def test_no_token_is_a_usage_error(
    frozen: Path,
    ledger: FakeLedgerTransport,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv(bootstrap_ci.BUDGET_TOKEN_ENV)
    assert _init(frozen) == 2
    assert bootstrap_ci.BUDGET_TOKEN_ENV in capsys.readouterr().err
    assert ledger.commits == {}


def test_no_frozen_task_is_a_usage_error(
    toy_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ledger: FakeLedgerTransport,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(toy_repo)
    assert _init(tmp_path / "state") == 2
    assert "no frozen task" in capsys.readouterr().err
    assert ledger.commits == {}


@pytest.mark.parametrize(
    ("repo", "repo_id"),
    [
        (LEDGER_REPO, "0123"),
        (LEDGER_REPO, "not-a-number"),
        ("../../etc", JUDGED_REPO_ID),
        ("owner", JUDGED_REPO_ID),
    ],
)
def test_bad_identifiers_are_refused_before_any_request(
    frozen: Path,
    ledger: FakeLedgerTransport,
    capsys: pytest.CaptureFixture[str],
    repo: str,
    repo_id: str,
) -> None:
    assert _init(frozen, repo=repo, repo_id=repo_id) == 2
    err = capsys.readouterr().err
    assert "ci-budget-init refused" in err
    assert FAKE_BUDGET_TOKEN not in err
    assert ledger.reads == 0
    assert ledger.commits == {}


def test_a_ruleset_refusal_is_a_usage_error(
    frozen: Path, ledger: FakeLedgerTransport, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger.create_script = ["ruleset"]
    assert _init(frozen) == 2
    out = capsys.readouterr()
    assert out.out == ""
    assert "ruleset" in out.err
    assert ledger.refs == {}


def test_a_ledger_that_already_has_runs_is_refused(
    frozen: Path,
    ledger: FakeLedgerTransport,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert _init(frozen) == 0
    root = capsys.readouterr().out.strip()
    data = _probe(frozen, tmp_path)
    capsys.readouterr()
    ident = LedgerIdentity(repo_id=JUDGED_REPO_ID, lineage=data["lineage"])
    key = derive_key(base64.b64decode(data["key"]))
    record = run_record(key, ident, seq=1, parent=root, run_key="1/1", claim="c" * 32)
    ledger.append(branch_for(ident.lineage), record)
    monkeypatch.setenv(bootstrap_ci.BUDGET_TOKEN_ENV, FAKE_BUDGET_TOKEN)
    assert _init(frozen) == 2
    out = capsys.readouterr()
    assert out.out == ""
    assert "already has runs" in out.err


def test_an_unreachable_ledger_is_a_usage_error(
    frozen: Path, ledger: FakeLedgerTransport, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger.create_script = ["drop"]
    assert _init(frozen) == 2
    out = capsys.readouterr()
    assert out.out == ""
    assert "cannot reach the ledger" in out.err
    assert FAKE_BUDGET_TOKEN not in out.err


def test_init_never_runs_the_judge_or_the_export(
    frozen: Path, ledger: FakeLedgerTransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(args: object) -> int:
        raise AssertionError("wrong command")

    monkeypatch.setattr(bootstrap_ci, "ci_judge", boom)
    monkeypatch.setattr(bootstrap_ci, "ci_export", boom)
    assert _init(frozen) == 0


def test_the_real_seeder_can_seed_but_the_judge_transport_cannot() -> None:
    seeder = bootstrap_ci.make_ledger_seeder(LEDGER_REPO, FAKE_BUDGET_TOKEN)
    judge = bootstrap_ci.make_ledger_transport(LEDGER_REPO, FAKE_BUDGET_TOKEN)
    assert callable(seeder.create_ref) and callable(seeder.write_root)
    assert not hasattr(judge, "create_ref") and not hasattr(judge, "write_root")


def test_outside_a_repository_is_a_usage_error_without_a_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ledger: FakeLedgerTransport,
    capsys: pytest.CaptureFixture[str],
) -> None:
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    monkeypatch.chdir(outside)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    assert _init(tmp_path / "state") == 2
    out = capsys.readouterr()
    assert out.out == ""
    assert "ci-budget-init failed" in out.err
    assert "Traceback" not in out.err
    assert FAKE_BUDGET_TOKEN not in out.err
    assert ledger.commits == {}


def test_a_transport_refusal_reason_reaches_the_operator(
    frozen: Path,
    ledger: FakeLedgerTransport,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def empty(**kwargs: object) -> str:
        raise TransportError("ledger blob write failed: the ledger repository is empty")

    monkeypatch.setattr(ledger, "write_root", empty)
    assert _init(frozen) == 2
    out = capsys.readouterr()
    assert out.out == ""
    assert "repository is empty" in out.err
    assert FAKE_BUDGET_TOKEN not in out.err
