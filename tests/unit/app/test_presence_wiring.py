"""The five attended decisions each ask for presence in the use case (ADR 0017).

A refusing port leaves state exactly as it was; an approving port is asked the exact
decision and the audit records ``presence: tty-challenge``. The decisions ADR 0017 exempts
(unattended freeze, rollback, withdrawing consent) never ask.
"""

from __future__ import annotations

import json

import pytest

from cheeseless.adapters.outbound.git.repository import GitRepository
from cheeseless.adapters.outbound.runner.pytest_runner import PytestRunner
from cheeseless.adapters.outbound.sandbox.bwrap import BwrapSandbox
from cheeseless.adapters.outbound.store.file_store import FileStore, store_root
from cheeseless.app import authoring as uc
from cheeseless.app.authoring import AuthorError
from cheeseless.app.dispute import configure_disputes, gate_dispute, resolve_dispute
from cheeseless.app.freeze import commit, prepare
from cheeseless.app.gate_run import gate_run
from cheeseless.app.promote import PromoteRejected, promote
from cheeseless.domain.authoring import TaskPhase
from cheeseless.domain.dispute import DisputeStatus
from cheeseless.domain.presence import PresenceOperation, PresenceRefused
from cheeseless.ports.store import StoreError
from tests.factories import frozen_task
from tests.presence_fakes import Approving, Refusing
from tests.toy import FIXED_MAIN
from tests.unit.app.test_authoring import _green, _HookedStore, _record, _red_sandbox, _source
from tests.unit.app.test_freeze import _FakeSandbox

NOTE = "presence: tty-challenge"
SENSITIVE = "requirements.txt"


@pytest.fixture
def world(toy_repo, tmp_path):
    repo = GitRepository.discover(toy_repo)
    return repo, FileStore(store_root(repo.common_dir(), tmp_path / "state")), tmp_path


@pytest.fixture
def hooked(toy_repo, tmp_path):
    repo = GitRepository.discover(toy_repo)
    return repo, _HookedStore(store_root(repo.common_dir(), tmp_path / "state")), tmp_path


def _events(store) -> list[dict]:
    path = store.root / "events.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _state(repo, store):
    contract = uc.read_contract(repo, _source(repo))
    return store.authoring_state(contract.lineage_id, uc.spec_of(contract))


def _ready_proposal(repo, store):
    _record(repo, store)
    _green(repo, store)
    return uc.freeze_propose(repo, store, _source(repo))


def _approve(repo, store, tmp, proposal, presence, **kw):
    return uc.approve_freeze(
        repo,
        _FakeSandbox(tmp / "sb"),
        _red_sandbox(),
        store,
        proposal.proposal_id,
        actor="tester",
        presence=presence,
        **kw,
    )


# --- 1. attended freeze approval -------------------------------------------------------


def test_a_refused_presence_leaves_the_proposal_unfrozen(world):
    repo, store, tmp = world
    proposal = _ready_proposal(repo, store)
    before = _state(repo, store)
    proof = Refusing()
    with pytest.raises(AuthorError) as caught:
        _approve(repo, store, tmp, proposal, proof)
    assert caught.value.kind == "presence_refused"
    assert len(proof.asked) == 1
    assert _state(repo, store) == before
    assert not store.has_task(proposal.proposal_id)
    assert store.active_task_id() is None
    assert not any(e["kind"] == "freeze-approve" for e in _events(store))


def test_no_presence_port_refuses_an_attended_approval(world):
    repo, store, tmp = world
    proposal = _ready_proposal(repo, store)
    with pytest.raises(AuthorError) as caught:
        _approve(repo, store, tmp, proposal, None)
    assert caught.value.kind == "presence_refused"
    assert _state(repo, store).phase is TaskPhase.PROPOSED


def test_an_approval_asks_the_exact_decision_and_records_the_signal(world):
    repo, store, tmp = world
    proposal = _ready_proposal(repo, store)
    proof = Approving()
    result = _approve(repo, store, tmp, proposal, proof)
    assert result.task_id == proposal.proposal_id
    (asked,) = proof.asked
    assert asked.operation is PresenceOperation.FREEZE_APPROVE
    assert asked.lineage_id == _state(repo, store).lineage_id
    assert asked.subject == proposal.proposal_id
    assert (asked.actor, asked.outcome, asked.waiver) == ("tester", "freeze", "")
    (event,) = [e for e in _events(store) if e["kind"] == "freeze-approve"]
    assert NOTE in event["cause"] and "tester" in event["cause"]


def test_a_waived_approval_shows_the_waiver_in_the_request(world):
    repo, store, tmp = world
    proposal = uc.freeze_propose(repo, store, _source(repo))
    proof = Approving()
    try:
        _approve(repo, store, tmp, proposal, proof, no_reference=True, reason="no ref yet")
    except AuthorError as exc:  # pragma: no cover - diagnostic only
        pytest.fail(f"waived approval refused: {exc.kind} {exc}")
    (asked,) = proof.asked
    assert asked.outcome == "freeze no-reference"
    assert asked.waiver == "no ref yet"


def test_a_state_change_while_the_operator_answers_is_refused(world):
    repo, store, tmp = world
    proposal = _ready_proposal(repo, store)

    def move_on():
        store.save_authoring_state(_state(repo, store).approving())

    with pytest.raises(AuthorError) as caught:
        _approve(repo, store, tmp, proposal, Approving(before_answer=move_on))
    assert caught.value.kind == "approval_conflict"
    assert not store.has_task(proposal.proposal_id)


def test_presence_is_asked_only_after_the_readiness_checks(world):
    repo, store, tmp = world
    _record(repo, store)
    proposal = uc.freeze_propose(repo, store, _source(repo))
    proof = Approving()
    with pytest.raises(AuthorError):
        _approve(repo, store, tmp, proposal, proof)
    assert proof.asked == []


def test_the_unattended_route_never_asks_for_presence(world, monkeypatch):
    repo, store, tmp = world
    proposal = _ready_proposal(repo, store)
    seen = []
    original = uc.approve_freeze

    def spy(*args, **kwargs):
        seen.append(kwargs.get("presence", "missing"))
        return original(*args, **kwargs)

    monkeypatch.setattr(uc, "approve_freeze", spy)
    try:
        uc.auto_approve_freeze(
            repo, _FakeSandbox(tmp / "sb"), _red_sandbox(), store, proposal.proposal_id
        )
    except AuthorError:
        pass  # not opted in; what matters is that it never carried a presence port
    assert seen == [None]


# --- 2. author recover, finish branch --------------------------------------------------


def _strand(repo, store, tmp, *, publish):
    proposal = _ready_proposal(repo, store)
    if publish:
        store.fail_set_active = True
        with pytest.raises(AuthorError):
            _approve(repo, store, tmp, proposal, Approving())
    else:
        store.save_authoring_state(_state(repo, store).approving())
    return proposal


def _recover(repo, store, tmp, proposal, presence):
    return uc.recover_approval(
        repo,
        _FakeSandbox(tmp / "sb"),
        _red_sandbox(),
        store,
        proposal.proposal_id,
        actor="op",
        presence=presence,
    )


def test_a_refused_presence_does_not_finish_a_published_approval(hooked):
    repo, store, tmp = hooked
    proposal = _strand(repo, store, tmp, publish=True)
    before = _state(repo, store)
    with pytest.raises(AuthorError) as caught:
        _recover(repo, store, tmp, proposal, Refusing())
    assert caught.value.kind == "presence_refused"
    assert _state(repo, store) == before
    assert store.active_task_id() is None


def test_finishing_a_recovery_asks_and_records_presence(hooked):
    repo, store, tmp = hooked
    proposal = _strand(repo, store, tmp, publish=True)
    proof = Approving()
    assert _recover(repo, store, tmp, proposal, proof) == "finished"
    (asked,) = proof.asked
    assert asked.operation is PresenceOperation.FREEZE_RECOVER
    assert (asked.subject, asked.actor, asked.outcome) == (proposal.proposal_id, "op", "finish")
    (event,) = [e for e in _events(store) if "finished" in e["cause"]]
    assert NOTE in event["cause"]


def test_a_recovery_whose_state_moved_while_answering_is_refused(hooked):
    repo, store, tmp = hooked
    proposal = _strand(repo, store, tmp, publish=True)

    def roll_back():
        store.save_authoring_state(_state(repo, store).released())

    with pytest.raises(AuthorError):
        _recover(repo, store, tmp, proposal, Approving(before_answer=roll_back))
    assert store.active_task_id() is None


def test_a_rollback_needs_no_presence(hooked):
    repo, store, tmp = hooked
    proposal = _strand(repo, store, tmp, publish=False)
    assert _recover(repo, store, tmp, proposal, None) == "rolled_back"
    assert _state(repo, store).phase is TaskPhase.PROPOSED


# --- 3. auto-freeze opt-in -------------------------------------------------------------


def test_a_refused_presence_does_not_enable_auto_freeze(world):
    repo, store, _ = world
    lineage = _state(repo, store).lineage_id
    with pytest.raises(AuthorError) as caught:
        uc.configure_auto_freeze(repo, store, enabled=True, presence=Refusing())
    assert caught.value.kind == "presence_refused"
    assert not store.auto_freeze_enabled(lineage)


def test_enabling_auto_freeze_asks_and_records_presence(world):
    repo, store, _ = world
    lineage = _state(repo, store).lineage_id
    proof = Approving()
    assert uc.configure_auto_freeze(repo, store, enabled=True, presence=proof) == lineage
    assert store.auto_freeze_enabled(lineage)
    (asked,) = proof.asked
    assert asked.operation is PresenceOperation.AUTO_FREEZE_ENABLE
    assert (asked.lineage_id, asked.outcome) == (lineage, "allow")
    assert any(e["kind"] == "auto-freeze-enable" and NOTE in e["cause"] for e in _events(store))


def test_withdrawing_auto_freeze_needs_no_presence(world):
    repo, store, _ = world
    lineage = _state(repo, store).lineage_id
    uc.configure_auto_freeze(repo, store, enabled=True, presence=Approving())
    assert uc.configure_auto_freeze(repo, store, enabled=False, presence=None) == lineage
    assert not store.auto_freeze_enabled(lineage)


# --- 4. dispute resolution -------------------------------------------------------------


@pytest.fixture
def disputed(tmp_path):
    store = FileStore(tmp_path / "state")
    task = frozen_task()
    store.save_task(task, bytes(range(32)))
    configure_disputes(store, enabled=True, cap=3)
    gate_dispute(
        store, task_id=task.task.task_id, criterion_id="normalize", code="untestable", note="why"
    )
    (record,) = store.disputes()
    return store, record


def test_a_refused_presence_leaves_the_dispute_open(disputed):
    store, record = disputed
    with pytest.raises(PresenceRefused):
        resolve_dispute(store, record.dispute_id, upheld=True, actor="op", presence=Refusing())
    assert store.disputes()[0].status is DisputeStatus.OPEN


def test_resolving_a_dispute_asks_and_records_presence(disputed):
    store, record = disputed
    proof = Approving()
    done = resolve_dispute(store, record.dispute_id, upheld=False, actor="op", presence=proof)
    assert done.status is DisputeStatus.REJECTED
    (asked,) = proof.asked
    assert asked.operation is PresenceOperation.DISPUTE_RESOLVE
    assert (asked.lineage_id, asked.subject) == (record.lineage_id, record.dispute_id)
    assert (asked.actor, asked.outcome) == ("op", "rejected")
    assert any(e["kind"] == "dispute-presence" and NOTE in e["cause"] for e in _events(store))


def test_a_resolved_or_unknown_dispute_is_refused_before_asking(disputed):
    store, record = disputed
    resolve_dispute(store, record.dispute_id, upheld=True, actor="op", presence=Approving())
    proof = Approving()
    with pytest.raises(StoreError):
        resolve_dispute(store, record.dispute_id, upheld=False, actor="op", presence=proof)
    with pytest.raises(StoreError):
        resolve_dispute(store, "nope", upheld=False, actor="op", presence=proof)
    assert proof.asked == []


def test_a_dispute_resolved_while_the_operator_answers_is_not_resolved_twice(disputed):
    store, record = disputed

    def elsewhere():
        store.resolve_dispute(record.dispute_id, DisputeStatus.UPHELD, "other")

    with pytest.raises(StoreError):
        resolve_dispute(
            store,
            record.dispute_id,
            upheld=False,
            actor="op",
            presence=Approving(before_answer=elsewhere),
        )
    assert store.disputes()[0].status is DisputeStatus.UPHELD


# --- 5. promote with sensitive-path approvals ------------------------------------------


def _evaluate(toy_repo, tmp_path, *, touch_sensitive):
    repo = GitRepository.discover(toy_repo)
    runner, sandbox = PytestRunner(), BwrapSandbox()
    store = FileStore(store_root(repo.common_dir(), tmp_path / "state"))
    prepared = prepare(repo, runner, sandbox)
    commit(prepared, store)
    wt = tmp_path / "wt"
    repo.create_projection(prepared.projection_tree, wt)
    (wt / "src" / "toy" / "__main__.py").write_text(FIXED_MAIN)
    if touch_sensitive:
        (wt / SENSITIVE).write_text("# pinned\n")
    gate_run(repo, store, runner, sandbox, worktree=wt)
    return repo, store, wt


@pytest.fixture
def evaluated(toy_repo, tmp_path):
    return _evaluate(toy_repo, tmp_path, touch_sensitive=True)


def test_an_unapproved_sensitive_path_is_rejected_without_asking(evaluated):
    repo, store, wt = evaluated
    proof = Approving()
    with pytest.raises(PromoteRejected):
        promote(repo, store, worktree=wt, presence=proof)
    assert proof.asked == []


def test_a_refused_presence_does_not_promote_a_sensitive_approval(evaluated):
    repo, store, wt = evaluated
    with pytest.raises(AuthorError) as caught:
        promote(repo, store, worktree=wt, approved=(SENSITIVE,), presence=Refusing())
    assert caught.value.kind == "presence_refused"
    assert not any(e["kind"] == "promote" for e in _events(store))


def test_a_sensitive_approval_asks_and_records_presence(evaluated):
    repo, store, wt = evaluated
    proof = Approving()
    out = promote(repo, store, worktree=wt, approved=(SENSITIVE,), presence=proof)
    assert out.commit
    (asked,) = proof.asked
    assert asked.operation is PresenceOperation.PROMOTE_APPROVE
    assert asked.outcome == f"approve {SENSITIVE}"
    (event,) = [e for e in _events(store) if e["kind"] == "promote"]
    assert NOTE in event["cause"]


def test_a_promote_without_sensitive_approvals_needs_no_presence(toy_repo, tmp_path):
    repo, store, wt = _evaluate(toy_repo, tmp_path, touch_sensitive=False)
    out = promote(repo, store, worktree=wt, presence=None)
    assert out.commit


def test_promote_without_a_port_refuses_a_sensitive_approval(evaluated):
    repo, store, wt = evaluated
    with pytest.raises(AuthorError) as caught:
        promote(repo, store, worktree=wt, approved=(SENSITIVE,), presence=None)
    assert caught.value.kind == "presence_refused"


def test_a_candidate_changed_while_the_operator_answers_is_not_promoted(evaluated):
    repo, store, wt = evaluated

    def edit():
        (wt / SENSITIVE).write_text("# swapped\n")

    with pytest.raises((ValueError, AuthorError)):
        promote(
            repo,
            store,
            worktree=wt,
            approved=(SENSITIVE,),
            presence=Approving(before_answer=edit),
        )
    assert not any(e["kind"] == "promote" for e in _events(store))
