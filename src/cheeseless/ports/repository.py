"""Git port. Everything runs with hooks, filters and user config disabled (ADR 0002)."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from cheeseless.domain.freeze import HiddenBlob


class RepositoryError(Exception):
    """Git failed or the repository is in a state the gate refuses."""


class SnapshotRejected(RepositoryError):
    """The worktree holds something that cannot be snapshotted (symlink, gitlink …)."""

    def __init__(self, reason: str, path: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.path = path


@dataclass(frozen=True, slots=True)
class PromoteOutcome:
    ref: str
    commit: str


class Repository(Protocol):
    @property
    def root(self) -> Path: ...

    def head_commit(self) -> str: ...

    def default_branch_commit(self) -> str:
        """Commit of the trusted default branch; ``RepositoryError`` if it cannot be resolved."""
        ...

    def tree_of(self, commit: str, path: str = "") -> str:
        """Tree oid of ``path`` in ``commit`` ("" means the root tree)."""
        ...

    def dirty_paths(self, prefix: str) -> list[str]:
        """Tracked or untracked changes under ``prefix`` relative to HEAD."""
        ...

    def is_clean(self) -> bool:
        """True when the whole worktree matches HEAD: no change, no untracked file."""
        ...

    def blobs_under(self, tree: str, prefix: str) -> tuple[HiddenBlob, ...]: ...

    def show(self, commit: str, path: str) -> bytes: ...

    def tree_without(self, tree: str, prefix: str, keep: str | Sequence[str]) -> str:
        """``tree`` minus everything under ``prefix`` except ``keep``."""
        ...

    def replace_subtree(self, tree: str, prefix: str, source_tree: str, keep: str) -> str:
        """``tree`` minus everything under ``prefix``, plus ``source_tree``'s ``keep`` entries."""
        ...

    def snapshot(self, worktree: Path) -> str:
        """Write the worktree (tracked and untracked, not ignored) as a tree."""
        ...

    def export(
        self, tree: str, dest: Path, *, exclude: Sequence[str] = (), only: Sequence[str] = ()
    ) -> None:
        """Write regular files of ``tree`` into the empty dir ``dest``."""
        ...

    def create_projection(self, tree: str, dest: Path) -> None:
        """A fresh repo at ``dest`` whose single commit has exactly ``tree``."""
        ...

    def diff_modes(self, old_tree: str, new_tree: str) -> Mapping[str, str]:
        """Changed path → new mode ("000000" for deletions)."""
        ...

    def merge_base(self, a: str, b: str) -> str:
        """The best common ancestor commit of ``a`` and ``b`` (error if none)."""
        ...

    def promote(self, baseline: str, base_tree: str, candidate_tree: str, ref: str) -> str:
        """Commit baseline + (candidate − base) on ``ref``; return the commit."""
        ...

    def sparse_hide(self, keep: Sequence[str], hide: Sequence[str]) -> None: ...

    def stray_hidden(self, worktree: Path, hidden: Sequence[HiddenBlob]) -> list[str]:
        """Hidden paths or contents found in ``worktree`` (D11)."""
        ...
