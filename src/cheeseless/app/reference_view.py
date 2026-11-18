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


def reviewer_tree_of(repo: Repository, acceptance_tree: str) -> str:
    """A projection tree holding ``acceptance_tree`` at ``acceptance/`` and nothing else.

    The record names an acceptance tree; this is how the reviewer is shown exactly that tree,
    including hidden tests, without needing a repository tree that happens to contain it.
    Fails closed on an empty tree for the same reason as ``reviewer_tree``.
    """
    if not repo.blobs_under(acceptance_tree, ""):
        raise ValueError("the acceptance tree to review is empty")
    return repo.tree_from_acceptance(acceptance_tree)


def reviewer_tree(repo: Repository, tree: str) -> str:
    """``tree`` reduced to ``acceptance/``: the tests to judge, and nothing else.

    ADR 0015: the reviewer's verdict is about the tests. Showing it the candidate's code
    would let it judge the implementation instead, and would hand an untrusted agent's code
    to a model whose verdict decides whether the suite freezes.

    Fails closed when ``acceptance/`` is not a directory: an empty tree would let the reviewer
    judge a suite it was never shown and still record a verdict.
    """
    if not any(b.path.startswith(f"{ACCEPTANCE}/") for b in repo.blobs_under(tree, "")):
        raise ValueError("the tree holds no acceptance tests to review")
    return repo.tree_keeping(tree, ACCEPTANCE)
