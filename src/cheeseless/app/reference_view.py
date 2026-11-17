"""What a reference session's projection contains (ADR 0009: never any test)."""

from __future__ import annotations

from cheeseless.domain.session_role import REFERENCE_ALLOWLIST
from cheeseless.ports.repository import Repository

ACCEPTANCE = "acceptance"


def reference_tree(repo: Repository, tree: str) -> str:
    """``tree`` with ``acceptance/`` cut down to the reference allowlist.

    The projection repo is built from this tree, so neither its worktree nor its ``.git`` holds
    a hidden or contract test blob; the sandbox mask is defence in depth, not the only barrier.
    """
    keep = tuple(f"{ACCEPTANCE}/{rel}" for rel in REFERENCE_ALLOWLIST)
    narrowed = repo.tree_without(tree, ACCEPTANCE, keep)
    # ``tree_without`` keeps whole subtrees: a directory named ``spec.md`` would carry its
    # children along. Keep only the allowlisted paths that are themselves files.
    exact = tuple(b.path for b in repo.blobs_under(narrowed, ACCEPTANCE) if b.path in keep)
    return repo.tree_without(narrowed, ACCEPTANCE, exact)
