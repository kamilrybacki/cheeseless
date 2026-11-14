"""The trusted CI budget counter (P10-27): spec section 7, security tests 1-6.

Every test drives the real ``ci-judge`` CLI against the toy repository and an in-memory
ledger. The sandbox and runner are fakes that never execute candidate code; they only
record whether, and with what environment, the hidden run would have happened.
"""

from __future__ import annotations

import base64
import dataclasses
import json
import os
from collections.abc import Callable
from pathlib import Path

import pytest

from cheeseless import bootstrap_ci
from cheeseless.adapters.outbound.ci.ledger_counter import ALERT_PREFIX
from cheeseless.bootstrap import main
from cheeseless.domain.budget import Budget, Pool
from cheeseless.domain.budget_ledger import (
    RECORD_FILE,
    LedgerIdentity,
    derive_key,
    run_record,
    seed_record,
)
from cheeseless.domain.evaluation import SuiteRun, TestOutcome
from tests.ci_support import (
    CLAIM_A,
    CLAIM_B,
    FAKE_BUDGET_TOKEN,
    JUDGED_REPO_ID,
    LEDGER_IDENT,
    LEDGER_STORE_KEY,
    CiWorld,
    FakeLedgerTransport,
    FakeRunner,
    FakeSandbox,
    export_argv,
    judge_cli,
    ledger_counter,
    make_head,
    prepare_world,
    set_judge_event,
)
from tests.toy import git

OTHER_CLAIM = "c" * 32
WRONG_KEY = derive_key(b"\x11" * 32)  # obviously fake


@pytest.fixture
def world(toy_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CiWorld:
    return prepare_world(toy_repo, tmp_path, monkeypatch)


# -- helpers -------------------------------------------------------------------


def _bundle(world: CiWorld) -> dict:
    return json.loads(world.bundle.read_bytes())


def _key(world: CiWorld) -> bytes:
    return derive_key(base64.b64decode(_bundle(world)["key"]))


def _ident(world: CiWorld) -> LedgerIdentity:
    return LedgerIdentity(repo_id=JUDGED_REPO_ID, lineage=_bundle(world)["lineage"])


def _branch(world: CiWorld) -> str:
    return _bundle(world)["budget"]["branch"]


def _limit(world: CiWorld) -> int:
    return _bundle(world)["ci_limit"]


def _tip(world: CiWorld) -> str:
    return world.ledger.refs[_branch(world)]


def _record(
    world: CiWorld,
    *,
    seq: int,
    parent: str,
    rkey: str,
    claim: str = OTHER_CLAIM,
    key: bytes | None = None,
    ident: LedgerIdentity | None = None,
) -> bytes:
    return run_record(
        key or _key(world),
        ident or _ident(world),
        seq=seq,
        parent=parent,
        run_key=rkey,
        claim=claim,
    )


def _charge_others(world: CiWorld, count: int) -> None:
    """Valid charges by other judges, as if ``count`` earlier runs reached hidden."""
    for _ in range(count):
        seq = len(world.ledger.chain(_branch(world)))
        rec = _record(world, seq=seq, parent=_tip(world), rkey=f"{9000 + seq}/1")
        world.ledger.append(_branch(world), rec)


def _passing_suite(world: CiWorld) -> SuiteRun:
    task = _bundle(world)["task"]
    ids = [*task["visible"], *task["holdout"]]
    return SuiteRun(outcomes=dict.fromkeys(ids, TestOutcome.PASSED))


class HookedRunner(FakeRunner):
    """``on_hidden`` runs at the moment the hidden suite would run."""

    def __init__(self, hidden: object, on_hidden: Callable[[], object] | None = None) -> None:
        super().__init__(hidden=hidden)
        self.on_hidden = on_hidden

    def run(self, root, paths, **kwargs):
        if list(paths) == ["hidden"] and self.on_hidden is not None:
            self.on_hidden()
        return super().run(root, paths, **kwargs)


def _fakes(world: CiWorld, runner: FakeRunner, sandbox: FakeSandbox | None = None) -> FakeRunner:
    world.monkeypatch.setattr(bootstrap_ci, "BwrapSandbox", lambda: sandbox or FakeSandbox())
    world.monkeypatch.setattr(bootstrap_ci, "PytestRunner", lambda: runner)
    return runner


def _judge(world: CiWorld, head: str, capsys, **event) -> tuple[int, dict, str]:
    """One judge run with the given event ids; returns (exit, public verdict, stderr)."""
    set_judge_event(world, **event)
    argv = ["ci-judge", "--base", world.base, "--head", head, "--bundle-file", str(world.bundle)]
    code = main([*argv, "--json"])
    out = capsys.readouterr()
    return code, json.loads(out.out) if out.out.strip() else {}, out.err


def _blocked(code: int, verdict: dict, err: str, reason: str) -> None:
    assert code == 1
    assert verdict["status"] == "blocked"
    assert f"reason: {reason}" in err


# -- 1. the sixth run is refused ---------------------------------------------------


def test_the_run_after_the_limit_never_reaches_hidden(world, capsys):
    head = make_head(world, {"src/toy/note.txt": "x"})
    limit = _limit(world)
    for n in range(1, limit + 1):
        runner = _fakes(world, FakeRunner(hidden=SuiteRun(outcomes={})))
        code, verdict, err = _judge(world, head, capsys)
        assert (code, verdict["status"]) == (1, "fail")
        assert runner.hidden_calls == 1
        assert f"budget used {n} of {limit}" in err
    assert len(world.ledger.chain(_branch(world))) == limit + 1
    sent = world.ledger.updates_sent
    runner = _fakes(world, FakeRunner(hidden=_passing_suite(world)))
    code, verdict, err = _judge(world, head, capsys)
    _blocked(code, verdict, err, "budget_exhausted")
    assert runner.hidden_calls == 0
    assert world.ledger.updates_sent == sent
    assert len(world.ledger.chain(_branch(world))) == limit + 1


# -- 2. a tampered or substituted ledger blocks ---------------------------------


def _replace_root(world: CiWorld, **changes) -> None:
    root = world.ledger.commits[world.root]
    world.ledger.commits[world.root] = dataclasses.replace(root, **changes)


def _append(world: CiWorld, message: bytes, files: dict | None = None) -> None:
    world.ledger.append(_branch(world), message, files)


def _first_run(world: CiWorld, **fields) -> bytes:
    """A valid seq 1 record on the root, with ``fields`` overwritten (MAC not recomputed)."""
    obj = json.loads(_record(world, seq=1, parent=world.root, rkey="9001/1"))
    for name, value in fields.items():
        if value is None:
            obj.pop(name)
        else:
            obj[name] = value
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


def _other_root(world: CiWorld, ident: LedgerIdentity) -> None:
    """A complete, validly MAC'd ledger for another identity, served under our branch."""
    root = world.ledger.add_commit((), seed_record(_key(world), ident))
    world.ledger.refs[_branch(world)] = root


def _merge(world: CiWorld) -> None:
    side = world.ledger.add_commit((), seed_record(_key(world), _ident(world)))
    rec = _record(world, seq=1, parent=world.root, rkey="9001/1")
    merge = world.ledger.add_commit((world.root, side), rec)
    world.ledger.refs[_branch(world)] = merge


def _duplicate_run_key(world: CiWorld) -> None:
    first = world.ledger.append(
        _branch(world), _record(world, seq=1, parent=world.root, rkey="9001/1")
    )
    _append(world, _record(world, seq=2, parent=first, rkey="9001/1"))


def _copied_record(world: CiWorld) -> None:
    """A valid record moved onto another parent: its signed parent no longer matches."""
    first = _record(world, seq=1, parent=world.root, rkey="9001/1")
    world.ledger.append(_branch(world), _record(world, seq=1, parent=world.root, rkey="9002/1"))
    sibling = world.ledger.add_commit((_tip(world),), first)
    world.ledger.refs[_branch(world)] = sibling


def _overlong(world: CiWorld) -> None:
    _charge_others(world, _limit(world) + 1)


def _message_differs(world: CiWorld) -> None:
    real = _record(world, seq=1, parent=world.root, rkey="9001/1")
    other = _record(world, seq=1, parent=world.root, rkey="9002/1")
    _append(world, real, {RECORD_FILE: other})


TAMPERING: dict[str, Callable[[CiWorld], object]] = {
    "missing branch": lambda w: w.ledger.refs.pop(_branch(w)),
    "wrong root": lambda w: w.ledger.seed(base64.b64decode(_bundle(w)["key"]), _ident(w)),
    "seed with a parent": lambda w: _replace_root(w, parents=("f" * 40,)),
    "bad seed MAC": lambda w: _replace_root(
        w,
        message=seed_record(WRONG_KEY, _ident(w)),
        files={RECORD_FILE: seed_record(WRONG_KEY, _ident(w))},
    ),
    "bad record MAC": lambda w: _append(
        w, _record(w, seq=1, parent=w.root, rkey="9001/1", key=WRONG_KEY)
    ),
    "record copied onto another parent": _copied_record,
    "record for another repo": lambda w: _append(
        w,
        _record(
            w,
            seq=1,
            parent=w.root,
            rkey="9001/1",
            ident=LedgerIdentity("987654321", _ident(w).lineage),
        ),
    ),
    "record for another lineage": lambda w: _append(
        w,
        _record(
            w, seq=1, parent=w.root, rkey="9001/1", ident=LedgerIdentity(JUDGED_REPO_ID, "other")
        ),
    ),
    "record for another pool": lambda w: _append(w, _first_run(w, pool="local")),
    "non-contiguous seq": lambda w: _append(w, _record(w, seq=2, parent=w.root, rkey="9001/1")),
    "merge commit": _merge,
    "duplicate run key": _duplicate_run_key,
    "chain longer than the limit": _overlong,
    "unsigned appended commit": lambda w: _append(w, b"manual budget reset\n"),
    "unknown version": lambda w: _append(w, _first_run(w, v=2)),
    "unknown kind": lambda w: _append(w, _first_run(w, kind="refund")),
    "seed at a non-root": lambda w: _append(w, seed_record(_key(w), _ident(w))),
    "run record at the root": lambda w: _replace_root(
        w,
        message=_record(w, seq=1, parent=w.root, rkey="9001/1"),
        files={RECORD_FILE: _record(w, seq=1, parent=w.root, rkey="9001/1")},
    ),
    "extra field": lambda w: _append(w, _first_run(w, note="refund")),
    "missing field": lambda w: _append(w, _first_run(w, claim=None)),
    "non-canonical JSON": lambda w: _append(
        w, json.dumps(json.loads(_first_run(w)), indent=1).encode()
    ),
    "extra file in the tree": lambda w: _append(
        w, _first_run(w), {RECORD_FILE: _first_run(w), "reset.txt": b"0"}
    ),
    "message differs from the blob": _message_differs,
    "valid ledger of another lineage": lambda w: _other_root(
        w, LedgerIdentity(JUDGED_REPO_ID, "other-lineage")
    ),
    "valid ledger of another repo": lambda w: _other_root(
        w, LedgerIdentity("987654321", _ident(w).lineage)
    ),
}


@pytest.mark.parametrize("tamper", list(TAMPERING), ids=list(TAMPERING))
def test_a_tampered_ledger_blocks_before_hidden(world, capsys, tamper):
    head = make_head(world, {"src/toy/note.txt": "x"})
    TAMPERING[tamper](world)
    runner = _fakes(world, FakeRunner(hidden=_passing_suite(world)))
    code, verdict, err = _judge(world, head, capsys)
    _blocked(code, verdict, err, "infra_error")
    assert runner.hidden_calls == 0


def test_another_judged_repository_cannot_use_the_ledger(world, capsys):
    # The seed MAC binds the judged repository id: a fork reusing the bundle is refused.
    head = make_head(world, {"src/toy/note.txt": "x"})
    runner = _fakes(world, FakeRunner(hidden=_passing_suite(world)))
    set_judge_event(world)
    world.monkeypatch.setenv("GITHUB_REPOSITORY_ID", "987654321")
    argv = ["ci-judge", "--base", world.base, "--head", head, "--bundle-file", str(world.bundle)]
    assert main([*argv, "--json"]) == 1
    out = capsys.readouterr()
    assert json.loads(out.out)["status"] == "blocked"
    assert "reason: infra_error" in out.err
    assert runner.hidden_calls == 0
    assert world.ledger.updates_sent == 0


def test_a_bundle_pinning_another_branch_is_refused(world, tmp_path, capsys):
    # The bundle is MAC'd and its branch must be the lineage's own: no ledger read at all.
    data = _bundle(world)
    data["budget"]["branch"] = "cheeseless/budget/other-lineage"
    bad = tmp_path / "branch.json"
    bad.write_text(json.dumps(data))
    head = make_head(world, {"src/toy/note.txt": "x"})
    runner = _fakes(world, FakeRunner(hidden=_passing_suite(world)))
    assert judge_cli(world, head, bundle=bad) == 2
    assert runner.hidden_calls == 0
    assert world.ledger.reads == 0


def test_a_seed_only_ledger_is_valid_and_the_first_charge_is_one(world, capsys):
    head = make_head(world, {"src/toy/note.txt": "x"})
    runner = _fakes(world, FakeRunner(hidden=_passing_suite(world)))
    code, verdict, err = _judge(world, head, capsys)
    assert (code, verdict["status"]) == (0, "pass")
    assert runner.hidden_calls == 1
    assert f"budget used 1 of {_limit(world)}" in err


def test_a_refreeze_of_the_same_lineage_keeps_counting(world, tmp_path, capsys):
    head = make_head(world, {"src/toy/note.txt": "x"})
    _fakes(world, FakeRunner(hidden=SuiteRun(outcomes={})))
    assert _judge(world, head, capsys)[0] == 1
    lineage = _bundle(world)["lineage"]
    git(world.toy, "checkout", "-q", "main")
    assert main(["--state-dir", str(world.state), "freeze", "--yes"]) == 0
    again = tmp_path / "secrets" / "refrozen.json"
    assert main(export_argv(world.state, again, world.root)) == 0
    assert json.loads(again.read_bytes())["lineage"] == lineage
    world = dataclasses.replace(world, bundle=again)
    head = make_head(world, {"src/toy/note.txt": "y"}, branch="pr2")
    runner = _fakes(world, FakeRunner(hidden=_passing_suite(world)))
    capsys.readouterr()  # drop the freeze and export output
    code, verdict, err = _judge(world, head, capsys)
    assert (code, verdict["status"]) == (0, "pass")
    assert runner.hidden_calls == 1
    assert f"budget used 2 of {_limit(world)}" in err


# -- 3. two judges race for the last slot ------------------------------------------


def test_on_an_atomic_ledger_two_racers_get_one_slot():
    ledger = FakeLedgerTransport()
    root = ledger.seed(LEDGER_STORE_KEY, LEDGER_IDENT)
    for n in range(1, 5):
        ledger_counter(ledger, root, run_key=f"{n}/1").reserve_run(LEDGER_IDENT.lineage, Pool.CI)
    first = ledger_counter(ledger, root, run_key="2001/1", claim=CLAIM_A)
    second = ledger_counter(ledger, root, run_key="2002/1", claim=CLAIM_B)
    results: dict[str, object] = {}
    ledger.update_script = ["race"]
    ledger.racers = [
        lambda: results.update(second=second.reserve_run(LEDGER_IDENT.lineage, Pool.CI))
    ]
    results["first"] = first.reserve_run(LEDGER_IDENT.lineage, Pool.CI)
    assert results["second"] == Budget(5, 5)
    assert results["first"] is None
    assert len(ledger.chain(next(iter(ledger.refs)))) == 6


def _force_overwrite(world: CiWorld) -> None:
    """A non-atomic store: another judge's sibling charge replaces ours without a fast-forward."""
    ours = world.ledger.commits[_tip(world)]
    parent = ours.parents[0]
    seq = json.loads(ours.message)["seq"]
    rec = _record(world, seq=seq, parent=parent, rkey="9999/1")
    world.ledger.refs[_branch(world)] = world.ledger.add_commit((parent,), rec)


def test_an_overwrite_before_the_post_hidden_read_withholds_the_pass(world, capsys):
    head = make_head(world, {"src/toy/note.txt": "x"})
    _charge_others(world, 4)
    runner = _fakes(world, HookedRunner(_passing_suite(world), lambda: _force_overwrite(world)))
    code, verdict, err = _judge(world, head, capsys)
    assert runner.hidden_calls == 1
    _blocked(code, verdict, err, "infra_error")
    assert ALERT_PREFIX in err


def test_an_overwrite_during_a_failing_hidden_run_blocks_and_alerts(world, capsys):
    # A lost charge is an incident whatever the verdict: a FAIL must not hide it.
    head = make_head(world, {"src/toy/note.txt": "x"})
    _charge_others(world, 4)
    failing = SuiteRun(outcomes={})
    runner = _fakes(world, HookedRunner(failing, lambda: _force_overwrite(world)))
    code, verdict, err = _judge(world, head, capsys)
    assert runner.hidden_calls == 1
    _blocked(code, verdict, err, "infra_error")
    assert ALERT_PREFIX in err


def test_a_failing_run_whose_charge_stays_keeps_its_fail(world, capsys):
    head = make_head(world, {"src/toy/note.txt": "x"})
    _fakes(world, FakeRunner(hidden=SuiteRun(outcomes={})))
    code, verdict, err = _judge(world, head, capsys)
    assert (code, verdict["status"]) == (1, "fail")
    assert ALERT_PREFIX not in err


def test_a_rewind_to_a_valid_prefix_is_not_detected(world, capsys):
    # The documented limit (docs/ci/README.md): replaying a valid prefix of the chain is
    # indistinguishable from a shorter history. Only the ruleset against force pushes and
    # deletions stops it; the validator cannot.
    head = make_head(world, {"src/toy/note.txt": "x"})
    seed = _tip(world)
    _charge_others(world, _limit(world))
    world.ledger.refs[_branch(world)] = seed
    _fakes(world, FakeRunner(hidden=_passing_suite(world)))
    code, verdict, err = _judge(world, head, capsys)
    assert (code, verdict["status"]) == (0, "pass")
    assert f"budget used 1 of {_limit(world)}" in err


def test_an_overwrite_after_the_post_hidden_read_loses_the_charge(world, capsys):
    # The documented limit (spec section 5): a store that is not compare-and-swap can drop a
    # charge after the judge last looked. The required ruleset forbids force pushes.
    head = make_head(world, {"src/toy/note.txt": "x"})
    _charge_others(world, 4)
    _fakes(world, FakeRunner(hidden=_passing_suite(world)))
    code, verdict, _ = _judge(world, head, capsys)
    assert (code, verdict["status"]) == (0, "pass")
    ours = _tip(world)
    _force_overwrite(world)
    assert ours not in {c.sha for c in world.ledger.chain(_branch(world))}
    assert len(world.ledger.chain(_branch(world))) == 6


# -- 4. an ambiguous reservation is kept and blocks ---------------------------------


def test_a_lost_update_followed_by_failed_reads_keeps_the_charge(world, capsys):
    head = make_head(world, {"src/toy/note.txt": "x"})
    ledger = world.ledger
    real_update = ledger.update_ref

    def lost_then_dark(branch, sha):
        ledger.update_script = ["lost"]
        try:
            return real_update(branch, sha)
        finally:
            ledger.fail_reads = 1000

    world.monkeypatch.setattr(ledger, "update_ref", lost_then_dark)
    runner = _fakes(world, FakeRunner(hidden=_passing_suite(world)))
    code, verdict, err = _judge(world, head, capsys)
    _blocked(code, verdict, err, "infra_error")
    assert runner.hidden_calls == 0
    ledger.fail_reads = 0
    chain = ledger.chain(_branch(world))
    assert len(chain) == 2  # the ambiguous charge stays: no refund path exists
    assert json.loads(chain[0].message)["run_key"].endswith("/1")


# -- 5. one run key, two judges -------------------------------------------------


def test_a_second_judge_with_the_same_run_key_never_reaches_hidden(world, capsys):
    head = make_head(world, {"src/toy/note.txt": "x"})
    first = _fakes(world, FakeRunner(hidden=SuiteRun(outcomes={})))
    assert _judge(world, head, capsys, run_id="4242")[0] == 1
    assert first.hidden_calls == 1
    second = _fakes(world, FakeRunner(hidden=_passing_suite(world)))
    code, verdict, err = _judge(world, head, capsys, run_id="4242")
    _blocked(code, verdict, err, "infra_error")
    assert second.hidden_calls == 0
    assert f"{ALERT_PREFIX} lineage {_ident(world).lineage}" in err
    assert len(world.ledger.chain(_branch(world))) == 2


def test_a_lost_response_within_one_judge_is_readmitted_once(world, capsys):
    head = make_head(world, {"src/toy/note.txt": "x"})
    world.ledger.update_script = ["lost"]
    runner = _fakes(world, FakeRunner(hidden=_passing_suite(world)))
    code, verdict, err = _judge(world, head, capsys, run_id="4343")
    assert (code, verdict["status"]) == (0, "pass")
    assert runner.hidden_calls == 1
    assert ALERT_PREFIX not in err
    assert len(world.ledger.chain(_branch(world))) == 2


def test_a_rerun_attempt_is_a_new_charge(world, capsys):
    head = make_head(world, {"src/toy/note.txt": "x"})
    for attempt in ("1", "2"):
        runner = _fakes(world, FakeRunner(hidden=SuiteRun(outcomes={})))
        assert _judge(world, head, capsys, run_id="4444", attempt=attempt)[0] == 1
        assert runner.hidden_calls == 1
    keys = [json.loads(c.message).get("run_key") for c in world.ledger.chain(_branch(world))]
    assert keys[:2] == ["4444/2", "4444/1"]


# -- 6. the budget token never reaches the candidate --------------------------------


class RecordingSandbox(FakeSandbox):
    def __init__(self) -> None:
        super().__init__()
        self.seen: list[str] = []

    def _look(self, env) -> None:
        self.seen.append(repr(list(env)))
        self.seen.append(repr(dict(os.environ)))

    def build(self, workdir, steps, env):
        self._look(env)
        return super().build(workdir, steps, env)

    def launcher(self, workdir, env, scratch):
        self._look(env)
        return super().launcher(workdir, env, scratch)


def test_the_budget_token_never_reaches_the_candidate_build_or_run(world, capsys):
    head = make_head(world, {"src/toy/note.txt": "x"})
    sandbox = RecordingSandbox()
    runner = _fakes(world, FakeRunner(hidden=_passing_suite(world)), sandbox)
    code, verdict, err = _judge(world, head, capsys)
    assert (code, verdict["status"]) == (0, "pass")
    assert runner.hidden_calls == 1
    # build, smoke launcher, hidden launcher: each looked at twice (argument env, process env).
    assert len(sandbox.seen) == 6
    for seen in sandbox.seen:
        assert FAKE_BUDGET_TOKEN not in seen
        assert bootstrap_ci.BUDGET_TOKEN_ENV not in seen
    assert FAKE_BUDGET_TOKEN not in err


def test_the_real_sandbox_scrubs_the_environment(tmp_path, monkeypatch):
    """The real bwrap argv clears the env and sets only the base and contract env."""
    from cheeseless.adapters.outbound.sandbox import profiles

    monkeypatch.setenv(bootstrap_ci.BUDGET_TOKEN_ENV, FAKE_BUDGET_TOKEN)
    env = [("TOY_MODE", "ci")]
    common = {"bwrap": "/usr/bin/bwrap", "inside": "/w/abcd", "env": env, "seccomp_fd": 7}
    argvs = [
        profiles.candidate(
            workdir=tmp_path / "w",
            upper=tmp_path / "up",
            work=tmp_path / "wk",
            command=["python", "-m", "toy"],
            **common,
        ),
        profiles.build(workdir=tmp_path / "w", command=["make"], **common),
    ]
    for argv in argvs:
        assert "--clearenv" in argv
        cleared = argv.index("--clearenv")
        assert all(i > cleared for i, a in enumerate(argv) if a == "--setenv")
        set_names = {argv[i + 1] for i, a in enumerate(argv) if a == "--setenv"}
        assert bootstrap_ci.BUDGET_TOKEN_ENV not in set_names
        assert set_names == {*profiles.BASE_ENV, "TOY_MODE"}
        assert not any(FAKE_BUDGET_TOKEN in a for a in argv)
