import json

import pytest

from cheeseless.adapters.outbound.git.repository import GitRepository
from cheeseless.adapters.outbound.store.file_store import FileStore, store_root
from cheeseless.app import authoring as uc
from cheeseless.app.authoring import AuthorError
from cheeseless.app.freeze import CONTRACT_PATH, FreezeError
from cheeseless.domain.authoring import MAX_REFERENCE_RETRIES, AuthoringError, TaskPhase
from cheeseless.domain.evaluation import TestOutcome
from cheeseless.ports.sandbox import AuthorOutcome
from tests.toy import TOY_FILES, make_repo
from tests.unit.app.test_freeze import _FakeRunner, _FakeSandbox, _suite

TESTS = [
    {"node_id": "t::a", "criteria": ["normalize"]},
    {"node_id": "t::b", "criteria": ["normalize"]},
    {"node_id": "t::c", "criteria": ["score"]},
    {"node_id": "t::d", "criteria": ["score"]},
]


def _line(collected=TESTS, outcomes=None, errors=(), status=0):
    body = {
        "exitstatus": status,
        "collected": collected,
        "outcomes": outcomes or {},
        "collection_errors": list(errors),
    }
    return f"noise\n{uc.RESULT_MARK}{json.dumps(body)}\n"


class FakeAuthorSandbox:
    def __init__(self, stdout="", **kw):
        self.stdout = stdout
        self.kw = kw
        self.calls = []

    def author_run(self, acceptance, reference, command, *, env, timeout=300.0):
        self.calls.append((acceptance, reference, tuple(command), tuple(env)))
        return AuthorOutcome(
            self.kw.get("exit_code", 0), self.stdout, "", self.kw.get("timed_out", False)
        )


@pytest.fixture
def world(toy_repo, tmp_path):
    repo = GitRepository.discover(toy_repo)
    store = FileStore(store_root(repo.common_dir(), tmp_path / "state"))
    return repo, store, tmp_path


def _source(repo):
    return repo.tree_of(repo.head_commit())


def test_author_check_passes_a_well_formed_tree(world):
    repo, _, _ = world
    sandbox = FakeAuthorSandbox(_line())
    result = uc.author_check(repo, sandbox, _source(repo))
    assert result.ok and result.problems == ()
    assert result.tests == 4
    acceptance, reference, command, _ = sandbox.calls[0]
    assert reference is None and "collect" in command


def test_author_check_reports_unknown_criteria_and_collection_errors(world):
    repo, _, _ = world
    bad = [{"node_id": "t::a", "criteria": ["nope"]}, {"node_id": "t::b", "criteria": []}]
    result = uc.author_check(
        repo, FakeAuthorSandbox(_line(bad, errors=["hidden/x.py"], status=2)), _source(repo)
    )
    assert not result.ok
    text = " ".join(result.problems)
    assert "nope" in text and "collection error" in text and "exactly one criterion" in text


def test_author_check_fails_closed_without_a_result_line_or_on_timeout(world):
    repo, _, _ = world
    assert not uc.author_check(repo, FakeAuthorSandbox("garbage"), _source(repo)).ok
    assert not uc.author_check(repo, FakeAuthorSandbox(_line(), timed_out=True), _source(repo)).ok
    assert not uc.author_check(
        repo, FakeAuthorSandbox("CHEESELESS-AUTHOR {not json"), _source(repo)
    ).ok


def test_author_check_refuses_a_tree_that_leaks_a_sentinel_publicly(tmp_path):
    files = dict(TOY_FILES)
    files["acceptance/hidden/test_normalize.py"] = "def test_x(candidate):\n    pass\n"
    files["acceptance/hidden/test_score.py"] = "def test_y(candidate):\n    pass\n"
    repo = GitRepository.discover(make_repo(tmp_path / "toy", files))
    result = uc.author_check(repo, FakeAuthorSandbox(_line()), _source(repo))
    assert not result.ok and any("sentinel" in p for p in result.problems)


def _record(repo, store):
    return uc.record_reference(repo, store, repo.root, _source(repo))


def test_record_reference_binds_the_snapshot_to_the_spec(world):
    repo, store, _ = world
    tree = _record(repo, store)
    contract = uc.read_contract(repo, _source(repo))
    spec, saved = store.load_reference(contract.lineage_id)
    assert saved == tree and spec == uc.spec_of(contract)


def test_crosscheck_green_and_red_are_recorded_and_do_not_need_a_budget(world):
    repo, store, _ = world
    _record(repo, store)
    passed = {t["node_id"]: "passed" for t in TESTS}
    sandbox = FakeAuthorSandbox(_line(outcomes=passed))
    rec = uc.author_crosscheck(repo, sandbox, store, _source(repo))
    assert rec.green and store.load_crosscheck(uc.proposal_id_for(repo, _source(repo))) == rec
    acceptance, reference, command, env = sandbox.calls[0]
    assert reference is not None and "run" in command

    failed = dict(passed, **{"t::c": "failed"})
    rec = uc.author_crosscheck(
        repo, FakeAuthorSandbox(_line(outcomes=failed)), store, _source(repo)
    )
    assert not rec.green and rec.red_criteria == ("score",)


def test_crosscheck_needs_a_reference_for_the_current_spec(world):
    repo, store, _ = world
    with pytest.raises(AuthorError) as caught:
        uc.author_crosscheck(repo, FakeAuthorSandbox(_line()), store, _source(repo))
    assert caught.value.kind == "no_reference"


def test_crosscheck_with_a_missing_or_failed_run_is_not_green(world):
    repo, store, _ = world
    _record(repo, store)
    rec = uc.author_crosscheck(repo, FakeAuthorSandbox("garbage"), store, _source(repo))
    assert not rec.green


def test_reference_failures_block_then_cap_retries_at_two(world):
    repo, store, _ = world
    contract = uc.read_contract(repo, _source(repo))
    state = uc.reference_failed(store, contract)
    assert state.phase is TaskPhase.REFERENCE_BLOCKED
    for _ in range(MAX_REFERENCE_RETRIES):
        uc.start_reference(store, contract)
        state = uc.reference_failed(store, contract)
    with pytest.raises(AuthoringError):
        uc.start_reference(store, contract)
    assert store.authoring_state(contract.lineage_id, uc.spec_of(contract)).retries_left == 0


def test_start_reference_when_not_blocked_is_a_noop(world):
    repo, store, _ = world
    contract = uc.read_contract(repo, _source(repo))
    assert uc.start_reference(store, contract).phase is TaskPhase.AUTHORING


def test_propose_stores_a_proposal_whose_id_is_the_task_id(world):
    repo, store, _ = world
    proposal = uc.freeze_propose(repo, store, _source(repo))
    assert store.load_proposal(proposal.proposal_id) == proposal
    contract = uc.read_contract(repo, _source(repo))
    assert (
        store.authoring_state(contract.lineage_id, uc.spec_of(contract)).phase is TaskPhase.PROPOSED
    )
    again = uc.freeze_propose(repo, store, _source(repo))
    assert again == proposal


def test_propose_is_allowed_from_reference_blocked(world):
    repo, store, _ = world
    contract = uc.read_contract(repo, _source(repo))
    uc.reference_failed(store, contract)
    uc.freeze_propose(repo, store, _source(repo))
    assert (
        store.authoring_state(contract.lineage_id, uc.spec_of(contract)).phase is TaskPhase.PROPOSED
    )


def _approve(repo, store, tmp_path, proposal, **kw):
    return uc.approve_freeze(
        repo,
        _FakeRunner([_suite(TestOutcome.FAILED)]),
        _FakeSandbox(tmp_path / "sb"),
        store,
        proposal.proposal_id,
        actor="tester",
        **kw,
    )


def _green(repo, store):
    passed = {t["node_id"]: "passed" for t in TESTS}
    return uc.author_crosscheck(
        repo, FakeAuthorSandbox(_line(outcomes=passed)), store, _source(repo)
    )


def test_approve_freezes_when_merged_crosschecked_and_red(world):
    repo, store, tmp = world
    _record(repo, store)
    _green(repo, store)
    proposal = uc.freeze_propose(repo, store, _source(repo))
    result = _approve(repo, store, tmp, proposal)
    assert result.task_id == proposal.proposal_id
    assert store.active_task_id() == result.task_id
    frozen, _ = store.load_task(result.task_id)
    assert frozen.waiver == ""
    contract = uc.read_contract(repo, _source(repo))
    assert (
        store.authoring_state(contract.lineage_id, uc.spec_of(contract)).phase is TaskPhase.FROZEN
    )


def test_approve_unknown_proposal(world):
    repo, store, tmp = world
    with pytest.raises(AuthorError) as caught:
        uc.approve_freeze(
            repo, _FakeRunner([]), _FakeSandbox(tmp / "sb"), store, "f" * 64, actor="t"
        )
    assert caught.value.kind == "unknown_proposal"


def test_approve_refuses_without_a_green_bound_crosscheck(world):
    repo, store, tmp = world
    proposal = uc.freeze_propose(repo, store, _source(repo))
    with pytest.raises(AuthorError) as caught:
        _approve(repo, store, tmp, proposal)
    assert caught.value.kind == "no_crosscheck"

    _record(repo, store)
    failed = {t["node_id"]: "failed" for t in TESTS}
    uc.author_crosscheck(repo, FakeAuthorSandbox(_line(outcomes=failed)), store, _source(repo))
    with pytest.raises(AuthorError) as caught:
        _approve(repo, store, tmp, proposal)
    assert caught.value.kind == "crosscheck_red"


def test_approve_refuses_a_crosscheck_for_a_replaced_reference(world, tmp_path):
    repo, store, tmp = world
    _record(repo, store)
    _green(repo, store)
    proposal = uc.freeze_propose(repo, store, _source(repo))
    (repo.root / "ref_extra.txt").write_text("changed reference\n")
    _record(repo, store)  # a new snapshot: the old crosscheck no longer binds
    with pytest.raises(AuthorError) as caught:
        _approve(repo, store, tmp, proposal)
    assert caught.value.kind == "crosscheck_stale"


def test_approve_refuses_when_the_tree_is_not_merged(world):
    repo, store, tmp = world
    _record(repo, store)
    _green(repo, store)
    proposal = uc.freeze_propose(repo, store, _source(repo))
    other = repo.root / CONTRACT_PATH
    other.write_text(other.read_text() + "\n# edit\n")
    import subprocess

    subprocess.run(  # noqa: S603
        ["git", "-C", str(repo.root), "commit", "-qam", "edit", "--no-verify"],  # noqa: S607
        check=True,
        env={
            "GIT_AUTHOR_NAME": "a",
            "GIT_AUTHOR_EMAIL": "a@a",
            "GIT_COMMITTER_NAME": "a",
            "GIT_COMMITTER_EMAIL": "a@a",
            "PATH": "/usr/bin:/bin",
        },
    )
    with pytest.raises(AuthorError) as caught:
        _approve(repo, store, tmp, proposal)
    assert caught.value.kind == "not_merged"


def test_approve_requires_a_red_baseline(world):
    repo, store, tmp = world
    _record(repo, store)
    _green(repo, store)
    proposal = uc.freeze_propose(repo, store, _source(repo))
    with pytest.raises(FreezeError, match="not RED"):
        uc.approve_freeze(
            repo,
            _FakeRunner([_suite(TestOutcome.PASSED)]),
            _FakeSandbox(tmp / "sb"),
            store,
            proposal.proposal_id,
            actor="t",
        )
    assert store.active_task_id() is None


def test_no_reference_needs_a_reason_and_records_the_waiver(world):
    repo, store, tmp = world
    proposal = uc.freeze_propose(repo, store, _source(repo))
    with pytest.raises(AuthorError) as caught:
        _approve(repo, store, tmp, proposal, no_reference=True, reason="  ")
    assert caught.value.kind == "reason_required"
    result = _approve(repo, store, tmp, proposal, no_reference=True, reason="no reference possible")
    frozen, _ = store.load_task(result.task_id)
    assert frozen.waiver == "no reference possible"


def test_approve_twice_is_refused(world):
    repo, store, tmp = world
    _record(repo, store)
    _green(repo, store)
    proposal = uc.freeze_propose(repo, store, _source(repo))
    _approve(repo, store, tmp, proposal)
    with pytest.raises(AuthorError) as caught:
        _approve(repo, store, tmp, proposal)
    assert caught.value.kind == "not_proposed"


def test_commit_takes_a_waiver(world):
    from cheeseless.app.freeze import commit, prepare

    repo, store, tmp = world
    prepared = prepare(repo, _FakeRunner([_suite(TestOutcome.FAILED)]), _FakeSandbox(tmp / "sb"))
    result = commit(prepared, store, waiver="why")
    assert store.load_task(result.task_id)[0].waiver == "why"


def test_driver_script_compiles():
    compile(uc.DRIVER, "driver", "exec")


@pytest.mark.slow
def test_author_check_and_crosscheck_in_the_real_author_runner(world):
    import shutil

    if shutil.which("bwrap") is None:
        pytest.skip("bwrap is not installed")
    from cheeseless.bootstrap import build_context

    repo, store, tmp = world
    ctx = build_context(repo.root)
    result = uc.author_check(repo, ctx.sandbox, _source(repo))
    assert result.ok, result.problems
    assert result.tests >= 4
