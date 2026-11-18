"""Launcher provenance on the real CLI launch path, not a hand-written `register_session`.

`test_launcher_config.py` checks the catalog and `resolve_launch` and simulates the host by
calling the store directly, so it could not notice a CLI path that records a typed label or an
unconfigured model (gpt-6.1-terra review). These tests drive `cheeseless session`, `dev`,
`dev-reference` and `strength-review --run` through `bootstrap.main` with a real operator
config under `$XDG_CONFIG_HOME` and a real sandboxed agent, then read the model from the
host's own launch record. Each refusal has a canary: the same setup without the fault launches.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from cheeseless.adapters.outbound.git.repository import GitRepository
from cheeseless.adapters.outbound.store.file_store import FileStore, store_root
from cheeseless.app.authoring import freeze_propose, spec_of
from cheeseless.app.freeze import read_contract
from cheeseless.bootstrap import build_context, main
from cheeseless.domain.dev_session import DevState
from tests.requirements import needs
from tests.toy import freeze_without_approval

needs_bwrap = needs(
    os.access("/usr/bin/bwrap", os.X_OK) and os.access("/usr/bin/python3", os.X_OK),
    "needs bubblewrap and /usr/bin/python3",
)
pytestmark = needs_bwrap

TID = "a" * 64
AUTHOR_MODEL = "claude-sonnet-5-5"
REVIEWER_MODEL = "gpt-6.2-sol"
# The reviewer's only job here is to say something parseable: the launch record is the subject.
REVIEWER_COMMAND = ["/bin/sh", "-c", 'echo "VERDICT: PASS" > "$HOME/verdict.txt"']

CONFIG = f"""
[launchers.author]
command = ["/bin/true"]
model = "{AUTHOR_MODEL}"

[launchers.reviewer]
command = {json.dumps(REVIEWER_COMMAND)}
model = "{REVIEWER_MODEL}"
"""


@pytest.fixture
def operator(tmp_path: Path, toy_repo: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The operator's private launcher config, outside the repo; returns the state dir."""
    xdg = tmp_path / "xdg"
    (xdg / "cheeseless").mkdir(parents=True)
    xdg.chmod(0o700)
    (xdg / "cheeseless").chmod(0o700)
    config = xdg / "cheeseless" / "launchers.toml"
    config.write_text(CONFIG)
    config.chmod(0o600)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.chdir(toy_repo)
    return tmp_path / "state"


def _store(toy_repo: Path, state: Path) -> FileStore:
    return FileStore(store_root(GitRepository.discover(toy_repo).common_dir(), state))


def _launches(toy_repo: Path, state: Path) -> list[dict[str, Any]]:
    path = _store(toy_repo, state)._root / "launched-sessions.json"  # noqa: SLF001
    if not path.exists():
        return []
    return list(json.loads(path.read_text())["sessions"])


def _models(toy_repo: Path, state: Path, role: str) -> list[str]:
    """Every model the host recorded for ``role``, read from the append-only launch log."""
    return [s["model"] for s in _launches(toy_repo, state) if s["role"] == role]


def _session(state: Path, task: str, *extra: str) -> int:
    return main(["session", "--state-dir", str(state), "--task", task, "--role", "author", *extra])


# -- session ------------------------------------------------------------------


def test_session_with_a_launcher_records_the_configured_model(
    operator: Path, toy_repo: Path
) -> None:
    assert _session(operator, TID, "--launcher", "author") == 0
    assert _store(toy_repo, operator).launched_model(TID, "author") == AUTHOR_MODEL


def test_session_without_a_launcher_records_no_model(operator: Path, toy_repo: Path) -> None:
    assert _session(operator, TID, "--", "/bin/true") == 0
    assert _models(toy_repo, operator, "author") == [""]
    assert _store(toy_repo, operator).launched_model(TID, "author") is None


def test_session_refuses_a_command_that_contradicts_the_launcher(
    operator: Path, toy_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _session(operator, TID, "--launcher", "author", "--", "/bin/false") == 2
    assert "session:" in capsys.readouterr().err
    assert _launches(toy_repo, operator) == []


def test_canary_session_accepts_the_launchers_own_command(operator: Path, toy_repo: Path) -> None:
    assert _session(operator, TID, "--launcher", "author", "--", "/bin/true") == 0
    assert _store(toy_repo, operator).launched_model(TID, "author") == AUTHOR_MODEL


# -- dev ----------------------------------------------------------------------


def _dev(state: Path, *extra: str) -> int:
    # `dev` launches into the active task, so give it one the way the other freeze tests do.
    assert freeze_without_approval(str(state)) == 0
    return main(["dev", "--lineage", "demo", "--state-dir", str(state), *extra])


def test_dev_with_a_launcher_records_the_configured_model(
    operator: Path, toy_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "t")
    _dev(operator, "--launcher", "author")
    assert _models(toy_repo, operator, "author") == [AUTHOR_MODEL]


def test_dev_without_a_launcher_records_no_model(
    operator: Path, toy_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "t")
    _dev(operator, "--", "/bin/true")
    assert _models(toy_repo, operator, "author") == [""]


def test_dev_refuses_a_command_that_contradicts_the_launcher(
    operator: Path, toy_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "t")
    assert _dev(operator, "--launcher", "author", "--", "/bin/false") == 1
    assert _launches(toy_repo, operator) == []


def test_canary_dev_accepts_the_launchers_own_command(
    operator: Path, toy_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "t")
    _dev(operator, "--launcher", "author", "--", "/bin/true")
    assert _models(toy_repo, operator, "author") == [AUTHOR_MODEL]


# -- dev-reference ------------------------------------------------------------


def _reference(toy_repo: Path, state: Path, *extra: str) -> int:
    """A headless reference attempt in a lineage whose spec is done, as the host test sets up."""
    repo = GitRepository.discover(toy_repo)
    contract = read_contract(repo, repo.head_commit())
    digest = spec_of(contract)
    _store(toy_repo, state).save_dev_state(DevState(contract.lineage_id).spec_done(digest))
    argv = ["dev-reference", "--state-dir", str(state), "--lineage", contract.lineage_id]
    return main([*argv, "--spec-digest", digest, "--task", TID, "--headless", *extra])


def test_reference_with_a_launcher_records_the_configured_model(
    operator: Path, toy_repo: Path
) -> None:
    assert _reference(toy_repo, operator, "--launcher", "author") == 0
    assert [s["model"] for s in _launches(toy_repo, operator)] == [AUTHOR_MODEL]


def test_reference_without_a_launcher_records_no_model(operator: Path, toy_repo: Path) -> None:
    assert _reference(toy_repo, operator, "--", "/bin/true") == 0
    assert [s["model"] for s in _launches(toy_repo, operator)] == [""]


def test_reference_refuses_a_command_that_contradicts_the_launcher(
    operator: Path, toy_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _reference(toy_repo, operator, "--launcher", "author", "--", "/bin/false") == 2
    assert "dev-reference:" in capsys.readouterr().err
    assert _launches(toy_repo, operator) == []


def test_canary_reference_accepts_the_launchers_own_command(operator: Path, toy_repo: Path) -> None:
    assert _reference(toy_repo, operator, "--launcher", "author", "--", "/bin/true") == 0
    assert [s["model"] for s in _launches(toy_repo, operator)] == [AUTHOR_MODEL]


# -- strength-review --run ----------------------------------------------------


@pytest.fixture
def proposed(operator: Path, toy_repo: Path) -> str:
    """A stored proposal whose author the host launched through the operator's launcher."""
    ctx = build_context(operator)
    proposal = freeze_propose(ctx.repo, ctx.store, ctx.repo.head_commit())  # type: ignore[arg-type]
    assert _session(operator, proposal.proposal_id, "--launcher", "author") == 0
    return proposal.proposal_id


def _review(state: Path, task: str, *extra: str) -> int:
    return main(["--state-dir", str(state), "strength-review", "--task", task, "--run", *extra])


def _reviewers(toy_repo: Path, state: Path) -> list[str]:
    return _models(toy_repo, state, "reviewer")


def test_review_with_a_launcher_records_the_configured_model(
    operator: Path, toy_repo: Path, proposed: str
) -> None:
    assert _review(operator, proposed, "--launcher", "reviewer") == 0
    assert _reviewers(toy_repo, operator) == [REVIEWER_MODEL]


def test_review_without_a_launcher_records_no_model_despite_a_typed_one(
    operator: Path, toy_repo: Path, proposed: str
) -> None:
    rc = _review(operator, proposed, "--reviewer-model", REVIEWER_MODEL, "--", *REVIEWER_COMMAND)
    # The typed label never reaches the record, so the review cannot count as cross-vendor.
    assert rc == 1
    assert _reviewers(toy_repo, operator) == [""]


@pytest.mark.parametrize(
    "fault",
    [
        ["--reviewer-model", "claude-opus-5-5"],
        ["--", "/bin/sh", "-c", 'echo "VERDICT: PASS" > "$HOME/verdict.txt"; true'],
    ],
    ids=["label", "command"],
)
def test_review_refuses_a_label_or_command_that_contradicts_the_launcher(
    operator: Path, toy_repo: Path, proposed: str, fault: list[str]
) -> None:
    assert _review(operator, proposed, "--launcher", "reviewer", *fault) == 2
    assert _reviewers(toy_repo, operator) == []


@pytest.mark.parametrize(
    "agreeing",
    [["--reviewer-model", REVIEWER_MODEL], ["--", *REVIEWER_COMMAND]],
    ids=["label", "command"],
)
def test_canary_review_accepts_a_label_or_command_that_agrees(
    operator: Path, toy_repo: Path, proposed: str, agreeing: list[str]
) -> None:
    assert _review(operator, proposed, "--launcher", "reviewer", *agreeing) == 0
    assert _reviewers(toy_repo, operator) == [REVIEWER_MODEL]
