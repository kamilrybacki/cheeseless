"""Seed a CI budget ledger branch: the operator's one-time ``ci-budget-init`` step.

The seed is a parentless commit whose message and only file are the canonical seed record
(see ``domain.budget_ledger``). Seeding is idempotent: an existing valid seed is returned,
never replaced. The new commit is read back and validated *before* the branch exists, so a
server that rewrites commit messages is caught while nothing is published yet.
"""

from __future__ import annotations

from dataclasses import dataclass

from cheeseless.adapters.outbound.ci.github_ledger import (
    LedgerSeeder,
    TransportError,
    UpdateResult,
)
from cheeseless.domain.budget_ledger import (
    RECORD_FILE,
    LedgerIdentity,
    LedgerInvalid,
    check_branch,
    derive_key,
    seed_record,
    validate_chain,
)


class SeedError(Exception):
    """Seeding was refused. The message is safe to print (no key, no token)."""


@dataclass(frozen=True)
class SeedResult:
    root: str
    created: bool


def _check_tip(transport: LedgerSeeder, tip: str, key: bytes, identity: LedgerIdentity) -> None:
    """``tip`` must be a fresh seed of this lineage under this key, or ``SeedError``."""
    commit = transport.get_commit(tip)
    if commit.parents:
        raise SeedError("the ledger branch already has runs; keep the root it was pinned with")
    try:
        validate_chain([commit], key=key, identity=identity, root=tip, limit=0)
    except LedgerInvalid:
        raise SeedError(
            "the ledger branch exists but is not a seed for this repository, task lineage "
            "and store key"
        ) from None


def _create(transport: LedgerSeeder, branch: str, sha: str) -> UpdateResult | None:
    """``None`` when the answer was lost; the caller re-reads the branch to find out."""
    try:
        return transport.create_ref(branch, sha)
    except TransportError:
        return None


def seed_ledger(
    transport: LedgerSeeder, *, store_key: bytes, identity: LedgerIdentity, branch: str
) -> SeedResult:
    """Create the ledger branch, or return the valid seed already there.

    Raises ``SeedError`` on refusal, ``ValueError`` for a branch of another lineage and
    ``TransportError`` when the ledger cannot be reached.
    """
    check_branch(branch, identity.lineage)
    key = derive_key(store_key)
    tip = transport.get_ref(branch)
    if tip is not None:
        _check_tip(transport, tip, key, identity)
        return SeedResult(root=tip, created=False)
    record = seed_record(key, identity)
    sha = transport.write_root(message=record, files={RECORD_FILE: record})
    try:
        _check_tip(transport, sha, key, identity)
    except SeedError:
        raise SeedError(
            "the ledger server changed the seed commit; no branch was created"
        ) from None
    result = _create(transport, branch, sha)
    tip = transport.get_ref(branch)
    if tip == sha:
        return SeedResult(root=sha, created=True)
    if tip is None:
        if result is UpdateResult.REJECTED:
            raise SeedError(
                "the ledger branch creation was rejected: seed before enabling the "
                "update-restricting ruleset, or check the token's permissions"
            )
        raise TransportError("ledger ref create failed")
    _check_tip(transport, tip, key, identity)
    return SeedResult(root=tip, created=False)


__all__ = ["SeedError", "SeedResult", "seed_ledger"]
