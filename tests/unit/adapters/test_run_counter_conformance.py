"""RunCounter conformance (ci-budget-counter section 7): FileStore and the ledger counter.

Each ``next()`` is the counter one judge run sees, so the ledger harness mints a fresh run key
per call while the file store is simply shared.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

import pytest

from cheeseless.adapters.outbound.store.file_store import FileStore
from cheeseless.domain.budget import DEFAULT_LIMITS, Budget, Pool
from cheeseless.ports.run_counter import RunCounter
from tests.ci_support import LEDGER_IDENT, LEDGER_STORE_KEY, FakeLedgerTransport, ledger_counter

LINEAGE = LEDGER_IDENT.lineage
LIMIT = DEFAULT_LIMITS[Pool.CI]


class Harness(Protocol):
    def next(self) -> RunCounter: ...


class FileHarness:
    def __init__(self, root: Path) -> None:
        self._store = FileStore(root)

    def next(self) -> RunCounter:
        return self._store


class LedgerHarness:
    def __init__(self) -> None:
        self.transport = FakeLedgerTransport()
        self._root = self.transport.seed(LEDGER_STORE_KEY, LEDGER_IDENT)
        self._run = 1000

    def next(self) -> RunCounter:
        self._run += 1
        return ledger_counter(self.transport, self._root, run_key=f"{self._run}/1", limit=LIMIT)


@pytest.fixture(params=["file", "ledger"])
def harness(request: pytest.FixtureRequest, tmp_path: Path) -> Harness:
    if request.param == "file":
        return FileHarness(tmp_path / "store")
    return LedgerHarness()


def test_a_fresh_lineage_has_the_full_ci_budget(harness: Harness) -> None:
    assert harness.next().budget(LINEAGE, Pool.CI) == Budget(LIMIT, 0)


def test_reserve_charges_exactly_one_run(harness: Harness) -> None:
    assert harness.next().reserve_run(LINEAGE, Pool.CI) == Budget(LIMIT, 1)
    assert harness.next().budget(LINEAGE, Pool.CI) == Budget(LIMIT, 1)


def test_reserve_refuses_once_exhausted_and_charges_nothing(harness: Harness) -> None:
    used = [harness.next().reserve_run(LINEAGE, Pool.CI) for _ in range(LIMIT)]
    assert used == [Budget(LIMIT, n) for n in range(1, LIMIT + 1)]
    assert harness.next().reserve_run(LINEAGE, Pool.CI) is None
    assert harness.next().budget(LINEAGE, Pool.CI) == Budget(LIMIT, LIMIT)
