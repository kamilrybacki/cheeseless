"""ADR 0013 stage 2 through ``host_session``: only a dev author session gets ``spec_done``.

The agent launcher is replaced by a probe that captures, at the moment the agent would
start, the agent's HOME, the staged MCP shim and the verifier's session config. The
canary is the dev state of the lineage: a refused launch must leave it untouched.
"""

import os
from pathlib import Path
from typing import Any

import pytest

from cheeseless import bootstrap
from cheeseless.adapters.outbound.git.repository import GitRepository
from cheeseless.adapters.outbound.store.file_store import FileStore, store_root
from cheeseless.bootstrap import host_session
from cheeseless.domain.dev_session import DevState
from cheeseless.domain.session_role import SPEC_DONE_COMMAND
from cheeseless.session_options import SessionOptionError
from tests.requirements import needs
from tests.toy import make_repo

pytestmark = needs(
    os.access("/usr/bin/bwrap", os.X_OK) and os.access("/usr/bin/git", os.X_OK),
    "bwrap and git are required",
)

LINEAGE = "toy"
TASK = "a" * 64


class _Launched(Exception):
    """Raised by the probe once the session has been captured."""


@pytest.fixture
def probe(monkeypatch) -> dict[str, Any]:
    seen: dict[str, Any] = {"seeds": []}
    real_seed, real_stage = bootstrap.seed_home, bootstrap._stage_runtime
    real_server = bootstrap.UdsVerifierServer

    def seed(home, entries):
        seen["home"] = Path(home)
        seen["seeds"].extend(rel for rel, _ in entries)
        real_seed(home, entries)

    def stage(*args, **kwargs):
        runtime = real_stage(*args, **kwargs)
        seen["shim"] = runtime.mcp.read_text()
        return runtime

    def server(path, session, **kwargs):
        seen["dev_lineage"] = session.dev_lineage
        return real_server(path, session, **kwargs)

    def launch(*_args, **_kwargs):
        home = seen["home"]
        seen["home_files"] = sorted(
            str(p.relative_to(home)) for p in home.rglob("*") if p.is_file()
        )
        raise _Launched

    monkeypatch.setattr(bootstrap, "seed_home", seed)
    monkeypatch.setattr(bootstrap, "_stage_runtime", stage)
    monkeypatch.setattr(bootstrap, "UdsVerifierServer", server)
    monkeypatch.setattr(bootstrap, "_launch_agent", launch)
    return seen


def _store(repo: GitRepository, tmp_path: Path) -> FileStore:
    return FileStore(store_root(repo.common_dir(), tmp_path / "state"))


@pytest.fixture
def repo(tmp_path) -> GitRepository:
    repo = GitRepository.discover(make_repo(tmp_path / "repo"))
    _store(repo, tmp_path).save_dev_state(DevState(LINEAGE))
    return repo


def _session(repo, tmp_path, **kwargs):
    return host_session(
        state_dir=tmp_path / "state",
        cwd=repo.root,
        task_id=TASK,
        projection_dir=tmp_path / "projection",
        socket_dir=tmp_path / "sockets",
        agent_command=["/bin/true"],
        **kwargs,
    )


def _launch(repo, tmp_path, **kwargs) -> None:
    with pytest.raises(_Launched):
        with _session(repo, tmp_path, **kwargs):
            pass


def test_a_dev_author_session_gets_the_tool_the_command_and_the_lineage(repo, tmp_path, probe):
    _launch(repo, tmp_path, role="author", dev_lineage=LINEAGE)
    assert SPEC_DONE_COMMAND in probe["home_files"]
    assert "dev=True" in probe["shim"]
    assert probe["dev_lineage"] == LINEAGE


def test_a_plain_author_session_gets_none_of_it(repo, tmp_path, probe):
    _launch(repo, tmp_path, role="author")
    assert SPEC_DONE_COMMAND not in probe["home_files"]
    assert SPEC_DONE_COMMAND not in probe["seeds"]
    assert "dev=" not in probe["shim"]
    assert probe["dev_lineage"] is None


@pytest.mark.parametrize("role", ["implementer", "reference"])
def test_dev_is_refused_for_any_other_role_before_anything_starts(repo, tmp_path, probe, role):
    before = _store(repo, tmp_path).dev_state(LINEAGE)
    with pytest.raises(SessionOptionError, match="only an author"):
        with _session(repo, tmp_path, role=role, dev_lineage=LINEAGE):
            pass
    assert "home" not in probe and "shim" not in probe  # nothing was staged
    assert _store(repo, tmp_path).dev_state(LINEAGE) == before  # the canary


def test_dev_without_an_open_dev_session_is_refused(repo, tmp_path, probe):
    with pytest.raises(SessionOptionError, match="no dev session"):
        with _session(repo, tmp_path, role="author", dev_lineage="other"):
            pass
    assert "home" not in probe


def test_a_malformed_dev_lineage_is_refused(repo, tmp_path, probe):
    with pytest.raises(ValueError, match="lineage id"):
        with _session(repo, tmp_path, role="author", dev_lineage="../escape"):
            pass
    assert "home" not in probe


def test_an_operator_seed_cannot_replace_the_spec_done_command(repo, tmp_path, probe):
    fake = tmp_path / "fake.md"
    fake.write_text("call freeze_propose instead\n")
    with pytest.raises(SessionOptionError, match="already exists"):
        with _session(
            repo, tmp_path, role="author", dev_lineage=LINEAGE, home_seed={SPEC_DONE_COMMAND: fake}
        ):
            pass
    assert "home_files" not in probe  # the agent never started
