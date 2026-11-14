"""Ledger run counter: the trusted cross-run CI budget (P10-6, P10-13, P10-19).

One commit per charged run on ``cheeseless/budget/<lineage>`` in a separate ledger
repository, appended by compare-and-swap (non-force ref update). Every read validates
the whole chain down to the pinned root; anything unexpected fails closed with
``CounterError``. A charge is never refunded: once a record may be durable it stays.
"""

from __future__ import annotations

from cheeseless.adapters.outbound.ci.github_ledger import (
    LedgerMalformed,
    LedgerTransport,
    TransportError,
    UpdateResult,
)
from cheeseless.domain.budget import Budget, Pool
from cheeseless.domain.budget_ledger import (
    RECORD_FILE,
    ChainState,
    LedgerCommit,
    LedgerIdentity,
    LedgerInvalid,
    check_branch,
    derive_key,
    run_record,
    validate_chain,
)
from cheeseless.domain.ids import git_oid
from cheeseless.ports.run_counter import CounterError

ALERT_PREFIX = "cheeseless-budget-alert:"
DEFAULT_ATTEMPTS = 3


class LedgerCounter:
    """``RunCounter`` for one lineage and one CI run (``run_key`` and ``claim`` are fixed)."""

    def __init__(
        self,
        transport: LedgerTransport,
        *,
        store_key: bytes,
        identity: LedgerIdentity,
        branch: str,
        root: str,
        limit: int,
        run_key: str,
        claim: str,
        attempts: int = DEFAULT_ATTEMPTS,
    ) -> None:
        check_branch(branch, identity.lineage)
        git_oid(root)
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise ValueError("invalid budget limit")
        key = derive_key(store_key)
        # Validates run_key and claim once, so a bad configuration never reaches the ledger.
        run_record(key, identity, seq=1, parent=root, run_key=run_key, claim=claim)
        if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 1:
            raise ValueError("invalid attempt count")
        self._transport = transport
        self._key = key
        self._identity = identity
        self._branch = branch
        self._root = root
        self._limit = limit
        self._run_key = run_key
        self._claim = claim
        self._attempts = attempts
        self._reserved: str | None = None
        self.alerts: list[str] = []
        self.observed: int | None = None

    def __repr__(self) -> str:
        return (
            f"LedgerCounter(lineage={self._identity.lineage!r}, "
            f"branch={self._branch!r}, limit={self._limit})"
        )

    # -- RunCounter -------------------------------------------------------------

    def budget(self, lineage_id: str, pool: Pool) -> Budget:
        self._check_scope(lineage_id, pool)
        return Budget(self._limit, self._read_with_retries().count)

    def reserve_run(self, lineage_id: str, pool: Pool) -> Budget | None:
        self._check_scope(lineage_id, pool)
        for _ in range(self._attempts):
            try:
                state = self._attempt_read()
                mine = self._mine(state)
                if mine is not None:
                    return mine
                if state.count >= self._limit:
                    self.observed = state.count
                    return None
                sha = self._write(state)
                result = self._transport.update_ref(self._branch, sha)
            except TransportError:
                # Ambiguous: the update may have landed. The next read finds our run_key.
                continue
            if result is UpdateResult.REJECTED:
                continue
            return self._after_update(sha)
        raise CounterError("the CI budget ledger could not be updated")

    # -- after hidden execution -------------------------------------------------

    @property
    def charged(self) -> bool:
        """True once this judge's charge was confirmed on the chain."""
        return self._reserved is not None

    def confirm_still_on_chain(self) -> bool:
        """True only if our charge is still on a valid chain. Any doubt returns False."""
        if self._reserved is None:
            return False
        try:
            state = self._read_with_retries()
        except CounterError:
            return False
        self.observed = state.count
        if state.contains(self._reserved):
            return True
        self._alert(f"charge {self._reserved} is no longer on the chain (tip {state.tip})")
        return False

    # -- internals --------------------------------------------------------------

    def _check_scope(self, lineage_id: str, pool: Pool) -> None:
        if lineage_id != self._identity.lineage or pool is not Pool.CI:
            raise CounterError("this counter only counts the pinned lineage's CI pool")

    def _alert(self, detail: str) -> None:
        self.alerts.append(f"{ALERT_PREFIX} lineage {self._identity.lineage}: {detail}")

    def _mine(self, state: ChainState) -> Budget | None:
        entry = state.find(self._run_key)
        if entry is None:
            return None
        if entry.claim != self._claim:
            self._alert(f"run key {self._run_key} is held by another judge (record {entry.sha})")
            raise CounterError("the run key was charged by another judge")
        self._reserved = entry.sha
        self.observed = state.count
        return Budget(self._limit, entry.seq)

    def _write(self, state: ChainState) -> str:
        record = run_record(
            self._key,
            self._identity,
            seq=state.count + 1,
            parent=state.tip,
            run_key=self._run_key,
            claim=self._claim,
        )
        return self._transport.write_commit(
            parent=state.tip, message=record, files={RECORD_FILE: record}
        )

    def _after_update(self, sha: str) -> Budget:
        """The ref moved to ``sha``. Require it on the chain before trusting the charge."""
        state = self._read_with_retries()
        mine = self._mine(state)
        if mine is None or self._reserved != sha:
            self._alert(f"updated to {sha} but the chain tip is {state.tip}")
            self._reserved = None
            raise CounterError("the CI budget charge is not on the ledger chain")
        return mine

    def _read_with_retries(self) -> ChainState:
        for _ in range(self._attempts):
            try:
                return self._attempt_read()
            except TransportError:
                continue
        raise CounterError("the CI budget ledger could not be read")

    def _attempt_read(self) -> ChainState:
        """One read. A plain ``TransportError`` is retryable; any defect is ``CounterError``."""
        try:
            commits = self._walk()
            return validate_chain(
                commits,
                key=self._key,
                identity=self._identity,
                root=self._root,
                limit=self._limit,
            )
        except (LedgerMalformed, LedgerInvalid):
            raise CounterError("the CI budget ledger is not trustworthy") from None

    def _walk(self) -> list[LedgerCommit]:
        tip = self._transport.get_ref(self._branch)
        if tip is None:
            raise CounterError("the CI budget ledger branch does not exist")
        commits: list[LedgerCommit] = []
        sha = tip
        # A valid chain is at most limit + 1 commits (seed plus runs); read one more to tell.
        for _ in range(self._limit + 2):
            commit = self._transport.get_commit(sha)
            if commit.sha != sha:
                raise CounterError("the CI budget ledger served a commit under another id")
            commits.append(commit)
            if sha == self._root or len(commit.parents) != 1:
                return commits
            sha = commit.parents[0]
        raise CounterError("the CI budget ledger is longer than the limit allows")


__all__ = ["ALERT_PREFIX", "LedgerCounter"]
