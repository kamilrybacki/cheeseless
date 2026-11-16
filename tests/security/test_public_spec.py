"""Only the public contract reaches the implementer; it cannot be promoted."""

from pathlib import Path

import pytest

from cheeseless.adapters.outbound.git.repository import GitRepository
from cheeseless.adapters.outbound.store.file_store import FileStore
from cheeseless.app.freeze import FreezeError, commit, prepare
from cheeseless.app.promote import PromoteRejected, promote
from cheeseless.domain.evaluation import TestOutcome
from cheeseless.domain.result import CriterionResult, Result, sign
from cheeseless.domain.status import Status
from cheeseless.ports.repository import SnapshotRejected
from tests.security.test_projection_canary import _all_objects, _worktree_bytes
from tests.toy import CANARY, CRITERIA_YAML, commit_all, write_files
from tests.unit.app.test_freeze import _FakeRunner, _FakeSandbox, _suite


def test_public_spec_projection_and_store_roundtrip(toy_repo: Path, tmp_path: Path) -> None:
    yaml = CRITERIA_YAML.replace(
        "  - id: score",
        "    examples:\n      - input: HELLO\n        expected_output: hello\n  - id: score",
    )
    write_files(
        toy_repo,
        {
            "acceptance/contract/criteria.yaml": yaml,
            "acceptance/contract/spec.md": "# Normalize text\n",
            "acceptance/notes.txt": (
                "private acceptance note CHEESELESS-SENTINEL-notes0001 "
                "CHEESELESS-SENTINEL-notes0002"
            ),
        },
    )
    commit_all(toy_repo)
    repo = GitRepository(toy_repo)
    prepared = prepare(repo, _FakeRunner([_suite(TestOutcome.FAILED)]), _FakeSandbox(tmp_path))
    dest = tmp_path / "projection"
    repo.create_projection(prepared.projection_tree, dest)
    assert (dest / "acceptance/contract/spec.md").read_text() == "# Normalize text\n"
    assert "expected_output: hello" in (dest / "acceptance/contract/criteria.yaml").read_text()
    paths = {b.path for b in repo.blobs_under(prepared.projection_tree, "acceptance")}
    assert {"acceptance/contract/spec.md", "acceptance/contract/criteria.yaml"} <= paths
    assert all(path.startswith("acceptance/contract/") for path in paths)
    assert CANARY.encode() in _all_objects(toy_repo)
    for contents in (_all_objects(dest), _worktree_bytes(dest)):
        assert CANARY.encode() not in contents
        assert b"private acceptance note" not in contents
    store = FileStore(tmp_path / "store")
    commit(prepared, store)
    frozen, _ = store.load_task(prepared.task_id)
    assert frozen.contract == prepared.contract
    assert frozen.contract.spec == "# Normalize text\n"
    assert frozen.contract.criteria[0].examples[0].expected_output == "hello"
    (dest / "acceptance/contract/spec.md").write_text("changed spec")
    changes = repo.diff_modes(prepared.projection_tree, repo.snapshot(dest))
    assert "acceptance/contract/spec.md" in changes
    # Even a signed PASS cannot authorize a spec edit.
    result = sign(
        Result(
            task_id=prepared.task_id,
            tree_hash=repo.snapshot(dest),
            baseline_digest=prepared.task_id,
            profile="test",
            tool_version="test",
            status=Status.PASS,
            criteria=tuple(CriterionResult(c.id, Status.PASS) for c in frozen.contract.criteria),
        ),
        store.hmac_key(),
    )
    store.save_result(result, "test")
    with pytest.raises(PromoteRejected) as caught:
        promote(repo, store, worktree=dest)
    assert caught.value.rejected == ("acceptance/contract/spec.md",)


def test_spec_named_directory_is_not_read_as_a_spec(toy_repo: Path, tmp_path: Path) -> None:
    write_files(toy_repo, {"acceptance/contract/spec.md/notes.txt": "not a spec"})
    commit_all(toy_repo)
    repo = GitRepository(toy_repo)
    prepared = prepare(repo, _FakeRunner([_suite(TestOutcome.FAILED)]), _FakeSandbox(tmp_path))
    assert prepared.contract.spec is None


def test_public_contract_tests_stay_projected_and_hidden_tests_do_not(
    toy_repo: Path, tmp_path: Path
) -> None:
    write_files(
        toy_repo,
        {
            "acceptance/contract/spec.md": "public spec",
            "acceptance/contract/test_smoke.py": "def test_smoke():\n    pass\n",
        },
    )
    commit_all(toy_repo)
    repo = GitRepository(toy_repo)
    prepared = prepare(repo, _FakeRunner([_suite(TestOutcome.FAILED)]), _FakeSandbox(tmp_path))
    dest = tmp_path / "projection"
    repo.create_projection(prepared.projection_tree, dest)
    assert (dest / "acceptance/contract/test_smoke.py").is_file()
    assert (dest / "acceptance/contract/spec.md").read_text() == "public spec"
    assert CANARY.encode() not in _all_objects(dest)
    assert CANARY.encode() not in _worktree_bytes(dest)


def test_symlinked_spec_is_refused_at_freeze(toy_repo: Path, tmp_path: Path) -> None:
    write_files(toy_repo, {"acceptance/contract/target.txt": CANARY})
    (toy_repo / "acceptance/contract/spec.md").symlink_to("target.txt")
    commit_all(toy_repo)
    repo = GitRepository(toy_repo)
    with pytest.raises((SnapshotRejected, FreezeError)):
        prepare(repo, _FakeRunner([_suite(TestOutcome.FAILED)]), _FakeSandbox(tmp_path))


def test_non_utf8_spec_is_a_freeze_error(toy_repo: Path, tmp_path: Path) -> None:
    (toy_repo / "acceptance/contract/spec.md").write_bytes(b"\xff\xfe broken")
    commit_all(toy_repo)
    repo = GitRepository(toy_repo)
    with pytest.raises(FreezeError, match="UTF-8"):
        prepare(repo, _FakeRunner([_suite(TestOutcome.FAILED)]), _FakeSandbox(tmp_path))
