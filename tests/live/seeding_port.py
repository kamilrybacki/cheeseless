"""Port for ``ci-budget-init`` style seeding (check 13b), with the real adapter."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Protocol

from cheeseless.adapters.outbound.ci.github_ledger import TransportError, UrllibLedgerSeeder
from cheeseless.adapters.outbound.ci.ledger_seed import SeedError, seed_ledger
from cheeseless.domain.budget_ledger import LedgerIdentity, branch_for
from tests.live.github_client import Token


@dataclass(frozen=True)
class SeedOutcome:
    ok: bool
    detail: str
    root: str = ""


class SeedPort(Protocol):
    def seed(self, repo: str, token: Token, *, repo_id: str, lineage: str) -> SeedOutcome: ...


class CheeselessSeeder:
    """Drives the product's own ``seed_ledger`` against a real repository."""

    def seed(self, repo: str, token: Token, *, repo_id: str, lineage: str) -> SeedOutcome:
        try:
            result = seed_ledger(
                UrllibLedgerSeeder(repo, token.reveal()),
                store_key=secrets.token_bytes(32),
                identity=LedgerIdentity(repo_id=repo_id, lineage=lineage),
                branch=branch_for(lineage),
            )
        except (SeedError, ValueError) as exc:
            return SeedOutcome(False, f"seed refused: {exc}")
        except TransportError as exc:
            return SeedOutcome(False, f"seed transport failure: {exc}")
        return SeedOutcome(True, "seeded" if result.created else "already seeded", result.root)
