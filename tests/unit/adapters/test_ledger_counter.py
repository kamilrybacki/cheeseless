"""Ledger run counter: idempotency, CAS retries, faults and fail-closed reads (section 3, 7)."""

from __future__ import annotations

import pytest

from cheeseless.adapters.outbound.ci.github_ledger import TransportError, UpdateResult
from cheeseless.adapters.outbound.ci.ledger_counter import ALERT_PREFIX, LedgerCounter
from cheeseless.domain.budget import Budget, Pool
from cheeseless.domain.budget_ledger import (
    LedgerCommit,
    branch_for,
    derive_key,
    run_record,
    validate_chain,
)
from cheeseless.ports.run_counter import CounterError
from cheeseless.ports.store import StoreError
from tests.ci_support import (
    CLAIM_A,
    CLAIM_B,
    LEDGER_IDENT,
    LEDGER_STORE_KEY,
    FakeLedgerTransport,
    ledger_counter,
)

LINEAGE = LEDGER_IDENT.lineage
BRANCH = branch_for(LINEAGE)
KEY = derive_key(LEDGER_STORE_KEY)


@pytest.fixture
def t() -> FakeLedgerTransport:
    return FakeLedgerTransport()


@pytest.fixture
def root(t: FakeLedgerTransport) -> str:
    return t.seed(LEDGER_STORE_KEY, LEDGER_IDENT)


def _runs(t: FakeLedgerTransport, root: str, limit: int = 5) -> list[str]:
    chain = validate_chain(t.chain(BRANCH), key=KEY, identity=LEDGER_IDENT, root=root, limit=limit)
    return [r.run_key for r in chain.runs]


def _fill(t: FakeLedgerTransport, root: str, n: int) -> None:
    for i in range(n):
        assert ledger_counter(t, root, run_key=f"{900 + i}/1").reserve_run(LINEAGE, Pool.CI)


def test_counter_error_is_a_store_error() -> None:
    assert issubclass(CounterError, StoreError)


# -- idempotency ------------------------------------------------------------------


def test_lost_response_is_found_and_reused_not_charged_twice(
    t: FakeLedgerTransport, root: str
) -> None:
    t.update_script = ["lost"]
    assert ledger_counter(t, root).reserve_run(LINEAGE, Pool.CI) == Budget(5, 1)
    assert t.updates_sent == 1
    assert _runs(t, root) == ["1001/1"]


def test_a_second_reserve_in_one_process_reuses_the_record(
    t: FakeLedgerTransport, root: str
) -> None:
    counter = ledger_counter(t, root)
    assert counter.reserve_run(LINEAGE, Pool.CI) == Budget(5, 1)
    assert counter.reserve_run(LINEAGE, Pool.CI) == Budget(5, 1)
    assert t.updates_sent == 1


def test_another_process_holding_the_run_key_is_refused_with_an_alert(
    t: FakeLedgerTransport, root: str
) -> None:
    ledger_counter(t, root, claim=CLAIM_A).reserve_run(LINEAGE, Pool.CI)
    intruder = ledger_counter(t, root, claim=CLAIM_B)
    with pytest.raises(CounterError):
        intruder.reserve_run(LINEAGE, Pool.CI)
    assert t.updates_sent == 1
    assert intruder.alerts and intruder.alerts[0].startswith(ALERT_PREFIX)
    assert CLAIM_A not in intruder.alerts[0] and CLAIM_B not in intruder.alerts[0]


def test_ambiguous_write_that_was_not_applied_is_retried(t: FakeLedgerTransport, root: str) -> None:
    t.update_script = ["drop"]
    assert ledger_counter(t, root).reserve_run(LINEAGE, Pool.CI) == Budget(5, 1)
    assert t.updates_sent == 2
    assert _runs(t, root) == ["1001/1"]


def test_durable_but_unobserved_charge_is_kept_never_refunded(
    t: FakeLedgerTransport, root: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = t.update_ref

    def lost_then_outage(branch: str, sha: str) -> UpdateResult:
        real(branch, sha)
        t.fail_reads = 10_000
        raise TransportError("ledger update timed out")

    monkeypatch.setattr(t, "update_ref", lost_then_outage)
    with pytest.raises(CounterError):
        ledger_counter(t, root).reserve_run(LINEAGE, Pool.CI)
    t.fail_reads = 0
    assert _runs(t, root) == ["1001/1"]


# -- compare-and-swap ------------------------------------------------------------


def test_a_lost_race_rebuilds_on_the_new_tip(t: FakeLedgerTransport, root: str) -> None:
    other = ledger_counter(t, root, run_key="2002/1", claim=CLAIM_B)
    t.update_script = ["race"]
    t.racers = [lambda: other.reserve_run(LINEAGE, Pool.CI)]
    assert ledger_counter(t, root).reserve_run(LINEAGE, Pool.CI) == Budget(5, 2)
    assert _runs(t, root) == ["2002/1", "1001/1"]


def test_a_race_into_exhaustion_refuses_without_writing(t: FakeLedgerTransport, root: str) -> None:
    _fill(t, root, 4)
    other = ledger_counter(t, root, run_key="2002/1", claim=CLAIM_B)
    t.update_script = ["race"]
    t.racers = [lambda: other.reserve_run(LINEAGE, Pool.CI)]
    assert ledger_counter(t, root).reserve_run(LINEAGE, Pool.CI) is None
    assert len(_runs(t, root)) == 5
    assert "1001/1" not in _runs(t, root)


def test_cas_attempts_run_out_and_raise(t: FakeLedgerTransport, root: str) -> None:
    t.update_script = ["drop", "drop", "drop", "ok"]
    with pytest.raises(CounterError):
        ledger_counter(t, root).reserve_run(LINEAGE, Pool.CI)
    assert t.updates_sent == 3
    assert _runs(t, root) == []


def test_exhausted_budget_writes_nothing(t: FakeLedgerTransport, root: str) -> None:
    _fill(t, root, 5)
    sent = t.updates_sent
    assert ledger_counter(t, root).reserve_run(LINEAGE, Pool.CI) is None
    assert t.updates_sent == sent


def test_an_update_reported_ok_but_absent_from_the_chain_fails_closed(
    t: FakeLedgerTransport, root: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(t, "update_ref", lambda branch, sha: UpdateResult.UPDATED)
    counter = ledger_counter(t, root)
    with pytest.raises(CounterError):
        counter.reserve_run(LINEAGE, Pool.CI)
    assert counter.alerts


# -- transport faults -------------------------------------------------------------


def test_transient_read_errors_are_retried(t: FakeLedgerTransport, root: str) -> None:
    t.fail_reads = 2
    assert ledger_counter(t, root).reserve_run(LINEAGE, Pool.CI) == Budget(5, 1)


def test_persistent_read_errors_raise_before_any_update_is_sent(
    t: FakeLedgerTransport, root: str
) -> None:
    t.fail_reads = 10_000
    with pytest.raises(CounterError):
        ledger_counter(t, root).reserve_run(LINEAGE, Pool.CI)
    assert t.updates_sent == 0


def test_a_failed_object_write_is_retried(t: FakeLedgerTransport, root: str) -> None:
    t.fail_writes = 1
    assert ledger_counter(t, root).reserve_run(LINEAGE, Pool.CI) == Budget(5, 1)


def test_budget_read_errors_raise(t: FakeLedgerTransport, root: str) -> None:
    t.fail_reads = 10_000
    with pytest.raises(CounterError):
        ledger_counter(t, root).budget(LINEAGE, Pool.CI)


# -- fail-closed validation -------------------------------------------------------


def test_a_tampered_ledger_is_never_written_to(t: FakeLedgerTransport, root: str) -> None:
    t.append(BRANCH, b'{"v":1}')
    with pytest.raises(CounterError):
        ledger_counter(t, root).reserve_run(LINEAGE, Pool.CI)
    assert t.updates_sent == 0


def test_a_missing_branch_fails_closed(t: FakeLedgerTransport, root: str) -> None:
    del t.refs[BRANCH]
    with pytest.raises(CounterError):
        ledger_counter(t, root).reserve_run(LINEAGE, Pool.CI)
    with pytest.raises(CounterError):
        ledger_counter(t, root).budget(LINEAGE, Pool.CI)


def test_a_commit_served_under_another_sha_fails_closed(t: FakeLedgerTransport, root: str) -> None:
    t.commits["f" * 40] = t.commits[root]
    t.refs[BRANCH] = "f" * 40
    with pytest.raises(CounterError):
        ledger_counter(t, root).budget(LINEAGE, Pool.CI)


def test_reads_are_bounded_by_the_limit(t: FakeLedgerTransport, root: str) -> None:
    _fill(t, root, 5)
    for i in range(20):
        t.append(BRANCH, b"junk %d" % i)
    t.reads = 0
    with pytest.raises(CounterError):
        ledger_counter(t, root).budget(LINEAGE, Pool.CI)
    assert t.reads <= 5 + 3


def test_a_limit_lowered_below_the_used_count_fails_closed(
    t: FakeLedgerTransport, root: str
) -> None:
    _fill(t, root, 3)
    with pytest.raises(CounterError):
        ledger_counter(t, root, limit=2).reserve_run(LINEAGE, Pool.CI)


@pytest.mark.parametrize(("lineage", "pool"), [("other-task", Pool.CI), (LINEAGE, Pool.LOCAL)])
def test_only_the_pinned_lineage_and_ci_pool_are_counted(
    t: FakeLedgerTransport, root: str, lineage: str, pool: Pool
) -> None:
    counter = ledger_counter(t, root)
    with pytest.raises(CounterError):
        counter.reserve_run(lineage, pool)
    with pytest.raises(CounterError):
        counter.budget(lineage, pool)


@pytest.mark.parametrize(
    "overrides",
    [
        {"branch": "cheeseless/budget/other-task"},
        {"root": "not-a-sha"},
        {"run_key": "01/1"},
        {"claim": "short"},
        {"limit": -1},
        {"store_key": b"short"},
    ],
)
def test_bad_configuration_is_refused(t: FakeLedgerTransport, root: str, overrides: dict) -> None:
    args: dict[str, object] = {
        "store_key": LEDGER_STORE_KEY,
        "identity": LEDGER_IDENT,
        "branch": BRANCH,
        "root": root,
        "limit": 5,
        "run_key": "1001/1",
        "claim": CLAIM_A,
        **overrides,
    }
    with pytest.raises(ValueError):
        LedgerCounter(t, **args)  # type: ignore[arg-type]


# -- after hidden execution ------------------------------------------------------


def test_confirm_sees_our_commit_and_logs_the_count(t: FakeLedgerTransport, root: str) -> None:
    counter = ledger_counter(t, root)
    counter.reserve_run(LINEAGE, Pool.CI)
    _fill(t, root, 1)
    assert counter.confirm_still_on_chain() is True
    assert counter.observed == 2


def test_confirm_without_a_reservation_fails_closed(t: FakeLedgerTransport, root: str) -> None:
    assert ledger_counter(t, root).confirm_still_on_chain() is False


def test_confirm_detects_an_overwritten_charge(t: FakeLedgerTransport, root: str) -> None:
    counter = ledger_counter(t, root)
    counter.reserve_run(LINEAGE, Pool.CI)
    ours = t.refs[BRANCH]
    # A non-atomic backend lets another judge's record replace ours (P1 violation).
    other = run_record(KEY, LEDGER_IDENT, seq=1, parent=root, run_key="2002/1", claim=CLAIM_B)
    t.refs[BRANCH] = t.add_commit((root,), other)
    assert counter.confirm_still_on_chain() is False
    alert = counter.alerts[-1]
    assert alert.startswith(ALERT_PREFIX)
    assert LINEAGE in alert and ours in alert and t.refs[BRANCH] in alert


def test_confirm_fails_closed_when_the_ledger_is_unreadable(
    t: FakeLedgerTransport, root: str
) -> None:
    counter = ledger_counter(t, root)
    counter.reserve_run(LINEAGE, Pool.CI)
    t.fail_reads = 10_000
    assert counter.confirm_still_on_chain() is False


def test_repr_never_shows_the_key_or_claim(t: FakeLedgerTransport, root: str) -> None:
    text = repr(ledger_counter(t, root))
    assert CLAIM_A not in text and LEDGER_STORE_KEY.hex() not in text
    assert isinstance(t.commits[root], LedgerCommit)
