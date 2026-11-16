"""Author-role operations as the agent-reachable surface sees them.

Each function snapshots the author's worktree, runs the use case and returns either its
result or a catalogued ``GateError``. Raw exception text never leaves: it may name
hidden or unapproved content.
"""

from __future__ import annotations

from pathlib import Path

from cheeseless.app.authoring import (
    AuthorCheckResult,
    AuthorError,
    AuthorStore,
    author_check,
    author_crosscheck,
    freeze_propose,
    spec_of,
)
from cheeseless.app.freeze import read_contract
from cheeseless.domain.authoring import CrosscheckRecord, Proposal, TaskPhase
from cheeseless.domain.errors import ErrorCode, GateError, gate_error
from cheeseless.ports.repository import Repository
from cheeseless.ports.sandbox import AuthorSandbox


def _blocked(repo: Repository, store: AuthorStore, source: str) -> bool:
    try:
        contract = read_contract(repo, source)
        state = store.authoring_state(contract.lineage_id, spec_of(contract))
    except Exception:  # noqa: BLE001
        return False
    return state.phase is TaskPhase.REFERENCE_BLOCKED


def gate_author_check(
    repo: Repository, sandbox: AuthorSandbox, store: AuthorStore, worktree: Path
) -> AuthorCheckResult | GateError:
    try:
        return author_check(repo, sandbox, repo.snapshot(worktree), store=store)
    except AuthorError:
        return gate_error(ErrorCode.AUTHOR_REFUSED)
    except Exception:  # noqa: BLE001
        return gate_error(ErrorCode.INFRA_ERROR)


def gate_author_crosscheck(
    repo: Repository, sandbox: AuthorSandbox, store: AuthorStore, worktree: Path
) -> CrosscheckRecord | GateError:
    source = ""
    try:
        source = repo.snapshot(worktree)
        return author_crosscheck(repo, sandbox, store, source)
    except AuthorError as exc:
        if exc.kind == "no_reference" and _blocked(repo, store, source):
            return gate_error(ErrorCode.REFERENCE_BLOCKED)
        return gate_error(ErrorCode.AUTHOR_REFUSED)
    except Exception:  # noqa: BLE001
        return gate_error(ErrorCode.INFRA_ERROR)


def gate_freeze_propose(
    repo: Repository, store: AuthorStore, worktree: Path
) -> Proposal | GateError:
    try:
        return freeze_propose(repo, store, repo.snapshot(worktree))
    except AuthorError:
        return gate_error(ErrorCode.AUTHOR_REFUSED)
    except Exception:  # noqa: BLE001
        return gate_error(ErrorCode.INFRA_ERROR)


__all__ = ["gate_author_check", "gate_author_crosscheck", "gate_freeze_propose"]
