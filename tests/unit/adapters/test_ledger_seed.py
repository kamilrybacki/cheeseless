"""Seeding a CI budget ledger branch: idempotent, race-safe, never over a foreign chain."""

from __future__ import annotations

import pytest

from cheeseless.adapters.outbound.ci.github_ledger import TransportError
from cheeseless.adapters.outbound.ci.ledger_seed import SeedError, SeedResult, seed_ledger
from cheeseless.domain.budget import Pool
from cheeseless.domain.budget_ledger import (
    LedgerIdentity,
    branch_for,
    derive_key,
    run_record,
    seed_record,
)
from tests.ci_support import LEDGER_IDENT, LEDGER_STORE_KEY, FakeLedgerTransport, ledger_counter

BRANCH = branch_for(LEDGER_IDENT.lineage)


def _seed(ledger: FakeLedgerTransport, **kwargs: object) -> SeedResult:
    args = {"store_key": LEDGER_STORE_KEY, "identity": LEDGER_IDENT, "branch": BRANCH}
    args.update(kwargs)
    return seed_ledger(ledger, **args)  # type: ignore[arg-type]


def test_seed_creates_a_branch_the_counter_accepts() -> None:
    ledger = FakeLedgerTransport()
    result = _seed(ledger)
    assert result.created
    assert ledger.refs[BRANCH] == result.root
    assert ledger.commits[result.root].parents == ()
    counter = ledger_counter(ledger, result.root)
    assert counter.reserve_run(LEDGER_IDENT.lineage, Pool.CI) is not None
    assert counter.observed == 1


def test_re_running_returns_the_existing_root_without_writing() -> None:
    ledger = FakeLedgerTransport()
    first = _seed(ledger)
    commits = len(ledger.commits)
    again = _seed(ledger)
    assert again == SeedResult(root=first.root, created=False)
    assert len(ledger.commits) == commits
    assert ledger.creates_sent == 1


def test_a_branch_that_already_has_runs_is_refused() -> None:
    ledger = FakeLedgerTransport()
    root = ledger.seed(LEDGER_STORE_KEY, LEDGER_IDENT)
    assert ledger_counter(ledger, root).reserve_run(LEDGER_IDENT.lineage, Pool.CI) is not None
    with pytest.raises(SeedError, match="already has runs"):
        _seed(ledger)


@pytest.mark.parametrize(
    ("key", "ident"),
    [
        (b"\x11" * 32, LEDGER_IDENT),
        (LEDGER_STORE_KEY, LedgerIdentity(repo_id="987654321", lineage=LEDGER_IDENT.lineage)),
    ],
)
def test_a_foreign_seed_is_refused(key: bytes, ident: LedgerIdentity) -> None:
    ledger = FakeLedgerTransport()
    ledger.seed(key, ident)
    with pytest.raises(SeedError, match="not a seed"):
        _seed(ledger)


def test_a_non_ledger_branch_is_refused() -> None:
    ledger = FakeLedgerTransport()
    ledger.refs[BRANCH] = ledger.add_commit((), b"hello")
    with pytest.raises(SeedError, match="not a seed"):
        _seed(ledger)


def test_a_rewritten_seed_message_is_caught_before_the_branch_exists() -> None:
    ledger = FakeLedgerTransport()
    ledger.rewrite_message = lambda message: message + b"\n"
    with pytest.raises(SeedError, match="changed the seed"):
        _seed(ledger)
    assert BRANCH not in ledger.refs
    assert ledger.creates_sent == 0


def test_a_lost_create_response_is_recovered() -> None:
    ledger = FakeLedgerTransport()
    ledger.create_script = ["lost"]
    result = _seed(ledger)
    assert result.created
    assert ledger.refs[BRANCH] == result.root


def test_a_dropped_create_fails() -> None:
    ledger = FakeLedgerTransport()
    ledger.create_script = ["drop"]
    with pytest.raises(TransportError):
        _seed(ledger)
    assert BRANCH not in ledger.refs


def test_a_ruleset_refusal_is_explained() -> None:
    ledger = FakeLedgerTransport()
    ledger.create_script = ["ruleset"]
    with pytest.raises(SeedError, match="ruleset"):
        _seed(ledger)
    assert BRANCH not in ledger.refs


def test_losing_the_race_to_another_valid_seed_adopts_its_root() -> None:
    ledger = FakeLedgerTransport()
    won: list[str] = []
    ledger.create_script = ["race"]
    ledger.racers = [lambda: won.append(ledger.seed(LEDGER_STORE_KEY, LEDGER_IDENT))]
    result = _seed(ledger)
    assert result == SeedResult(root=won[0], created=False)
    assert ledger.refs[BRANCH] == won[0]


def test_losing_the_race_to_a_foreign_writer_is_refused() -> None:
    ledger = FakeLedgerTransport()
    ledger.create_script = ["race"]
    ledger.racers = [lambda: ledger.seed(b"\x11" * 32, LEDGER_IDENT)]
    with pytest.raises(SeedError, match="not a seed"):
        _seed(ledger)


def test_losing_the_race_to_a_seed_that_already_ran_is_refused() -> None:
    ledger = FakeLedgerTransport()
    key = derive_key(LEDGER_STORE_KEY)

    def racer() -> None:
        root = ledger.seed(LEDGER_STORE_KEY, LEDGER_IDENT)
        record = run_record(key, LEDGER_IDENT, seq=1, parent=root, run_key="1/1", claim="c" * 32)
        ledger.append(BRANCH, record)

    ledger.create_script = ["race"]
    ledger.racers = [racer]
    with pytest.raises(SeedError, match="already has runs"):
        _seed(ledger)


def test_a_branch_for_another_lineage_is_refused_before_any_request() -> None:
    ledger = FakeLedgerTransport()
    with pytest.raises(ValueError):
        _seed(ledger, branch=branch_for("other-task"))
    assert ledger.reads == 0
    assert ledger.commits == {}


def test_the_seed_record_is_the_codec_record() -> None:
    ledger = FakeLedgerTransport()
    root = _seed(ledger).root
    expected = seed_record(derive_key(LEDGER_STORE_KEY), LEDGER_IDENT)
    assert ledger.commits[root].message == expected
