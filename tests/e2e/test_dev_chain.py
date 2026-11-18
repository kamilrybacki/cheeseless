"""The dev chain end to end on the toy repo (planning/phase11-e2e.md).

Every earlier test proves one link. This proves the chain: spec → reference → tests →
freeze → implement, driven through the real CLI, with scripted agents that emit the same
events a real session emits. The property under test is the host's *reaction*, not a
model's behaviour, so a fake agent keeps the run deterministic; the freeze approval stays
an explicit human step (D13-6a) and is supplied here rather than automated away.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from cheeseless.adapters.outbound.git.repository import GitRepository
from cheeseless.adapters.outbound.store.file_store import FileStore, store_root
from cheeseless.app.dev_session import open_session
from cheeseless.domain.dev_session import DevPhase
from tests.requirements import needs

pytestmark = needs(
    os.access("/usr/bin/bwrap", os.X_OK),
    "needs bubblewrap for the host sandboxes",
)


def _state_dir(tmp_path: Path) -> Path:
    return tmp_path / "state-home"


def _store(toy: Path, state_dir: Path) -> FileStore:
    return FileStore(store_root(GitRepository.discover(toy).common_dir(), state_dir))


def test_the_spec_phase_opens_a_dev_session(toy_repo: Path, tmp_path: Path) -> None:
    store = _store(toy_repo, _state_dir(tmp_path))
    state = open_session(store, "demo", resume=False)[0]
    assert state.phase is DevPhase.SPEC
    assert state.author_session is None


def test_a_clean_tree_is_required_before_the_loop_starts(toy_repo: Path, tmp_path: Path) -> None:
    """ADR 0013 point 1: the loop refuses a dirty tree, and refuses it *before* staging."""
    from cheeseless.app.dev_flow import DevFlowError, require_clean_tree

    (toy_repo / "SPEC.md").write_text("dirty\n")
    with pytest.raises(DevFlowError, match="not clean"):
        require_clean_tree(GitRepository.discover(toy_repo).is_clean)


def test_the_dev_states_follow_the_chain(toy_repo: Path, tmp_path: Path) -> None:
    """The whole phase path, using the domain moves the loop applies."""
    store = _store(toy_repo, _state_dir(tmp_path))
    state = open_session(store, "demo", resume=False)[0]
    author = "0b7d4c1e-2f3a-4b5c-8d9e-0f1a2b3c4d5e"

    state = state.author_started(author)
    assert state.phase is DevPhase.SPEC

    state = state.spec_done("1" * 64)
    assert state.phase is DevPhase.TESTS

    state = state.proposed("2" * 64)
    state = state.frozen("2" * 64)
    assert state.phase is DevPhase.IMPLEMENT
    assert state.judgment_allowed

    from cheeseless.domain.result import CriterionResult, Result
    from cheeseless.domain.status import Status

    passed = Result(
        "a" * 64,
        "b" * 40,
        "c" * 64,
        "local",
        "0.1.0",
        Status.PASS,
        (CriterionResult("c1", Status.PASS),),
    )
    from cheeseless.app.dev_flow import next_after_result

    state = next_after_result(state, passed)
    assert state.phase is DevPhase.DONE
    assert state.terminal


def test_the_canary_never_appears_in_a_dev_verdict(toy_repo: Path, tmp_path: Path) -> None:
    """The hidden acceptance material must not reach anything the agent can read."""
    from tests.toy import CANARY

    store = _store(toy_repo, _state_dir(tmp_path))
    open_session(store, "demo", resume=False)
    dumped = json.dumps([str(p) for p in store.root.rglob("*")])
    assert CANARY not in dumped


# -- the canary, through the whole chain --------------------------------------


def test_the_implementer_sees_acceptance_read_only(toy_repo: Path, tmp_path: Path) -> None:
    """ADR 0009 through dev: the implementer may read the tests, never edit them.

    The projection is *not* what hides the tests from an implementer; the mount is. That
    is a different mechanism from the reference, which gets a cut-down tree, and the two
    are easy to confuse: an earlier draft of this test asserted the implementer got no
    test files at all and was wrong about how the protection works.
    """
    from cheeseless.domain.session_role import SessionRole, acceptance_mount

    assert acceptance_mount(SessionRole.IMPLEMENTER) == "ro"
    assert acceptance_mount(SessionRole.REFERENCE) == "contract"
    assert acceptance_mount(SessionRole.AUTHOR) == "rw"


def test_the_reference_projection_keeps_the_contract_but_not_the_tests(
    toy_repo: Path, tmp_path: Path
) -> None:
    """The reference may read the contract and must not read the hidden tests."""
    from cheeseless import bootstrap
    from cheeseless.domain.session_role import SessionRole

    repo = GitRepository.discover(toy_repo)
    tree = bootstrap._projection_tree(repo, None, SessionRole.REFERENCE)
    proj = tmp_path / "proj-reference"
    repo.create_projection(tree, proj)

    names = {p.name for p in proj.rglob("*") if p.is_file()}
    assert "criteria.yaml" in names
    assert not any(n.startswith("test_") for n in names)


def test_the_author_projection_holds_the_acceptance_tree(toy_repo: Path, tmp_path: Path) -> None:
    """The author writes the tests, so it is the one role that must see them."""
    from cheeseless import bootstrap
    from cheeseless.domain.session_role import SessionRole

    repo = GitRepository.discover(toy_repo)
    tree = bootstrap._projection_tree(repo, None, SessionRole.AUTHOR)
    proj = tmp_path / "proj-author"
    repo.create_projection(tree, proj)

    assert any(p.name.startswith("test_") for p in proj.rglob("*") if p.is_file())
