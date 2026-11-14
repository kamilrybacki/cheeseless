"""Run counter port: the trusted CI budget (P10-6, P10-13, P10-19).

``Store`` satisfies it for the local file store; the CI judge uses a ledger in a
separate repository so a re-run or a fresh runner cannot reset the count.
"""

from typing import Protocol

from cheeseless.domain.budget import Budget, Pool
from cheeseless.ports.store import StoreError


class CounterError(StoreError):
    """The counter cannot be read or written trustworthily. Callers fail closed."""


class RunCounter(Protocol):
    def reserve_run(self, lineage_id: str, pool: Pool) -> Budget | None:
        """Charge one run and return the new budget, or ``None`` when it is exhausted."""
        ...

    def budget(self, lineage_id: str, pool: Pool) -> Budget: ...


__all__ = ["CounterError", "RunCounter"]
