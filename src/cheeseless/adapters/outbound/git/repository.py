"""``Repository`` port over hardened git plumbing (ADR 0002)."""

import os
import stat
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path

from cheeseless.adapters.outbound.git import objects as obj
from cheeseless.adapters.outbound.git._cmd import (
    IDENTITY,
    Git,
    discover,
    init,
    oid,
    rel_path,
    under,
)
from cheeseless.domain.freeze import HiddenBlob
from cheeseless.ports.repository import RepositoryError, SnapshotRejected

GATE_REF_PREFIX = "refs/heads/gate/"
_PROJECTION_MESSAGE = b"cheeseless projection\n"


@contextmanager
def _scratch() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="cheeseless-git-") as tmp:
        yield Path(tmp)


class GitRepository:
    """The trusted repository. Every git call goes through :class:`Git`."""

    def __init__(self, path: Path) -> None:
        where = discover(path)
        self._root = where.top
        self._common = where.common_dir
        self._format = where.object_format
        self._git = Git(where.git_dir, where.top, where.object_format)

    @classmethod
    def discover(cls, cwd: Path) -> "GitRepository":
        return cls(cwd)

    @property
    def root(self) -> Path:
        return self._root

    def common_dir(self) -> Path:
        return self._common

    def object_dirs(self) -> tuple[Path, ...]:
        """Effective object stores, including bounded recursive alternates.

        Include the default store too: our hardened Git environment intentionally
        ignores caller Git variables, but those paths must still stay protected.
        """
        raw = self._git.text("rev-parse", "--git-path", "objects")
        default = Path(raw) if Path(raw).is_absolute() else self.root / raw
        roots = [default]
        if value := os.environ.get("GIT_OBJECT_DIRECTORY"):
            effective = (
                self._git.run(
                    "rev-parse",
                    "--git-path",
                    "objects",
                    extra_env={"GIT_OBJECT_DIRECTORY": value},
                )
                .decode()
                .strip()
            )
            roots.append(Path(effective))
        roots.extend(
            Path(p)
            for p in os.environ.get("GIT_ALTERNATE_OBJECT_DIRECTORIES", "").split(os.pathsep)
            if p
        )
        found: set[Path] = set()

        def visit(path: Path, depth: int) -> None:
            real = path.resolve(strict=True)
            if real in found:
                return
            if depth > 5:
                raise RuntimeError("git object alternates exceed depth limit")
            if not real.is_dir():
                raise RuntimeError("git object storage is not a directory")
            found.add(real)
            alternates = real / "info" / "alternates"
            try:
                lines = alternates.read_text().splitlines()
            except FileNotFoundError:
                return
            for line in lines:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                child = Path(line)
                visit(child if child.is_absolute() else real / child, depth + 1)

        try:
            for root in roots:
                visit(root if root.is_absolute() else self.root / root, 0)
        except OSError as exc:
            raise RuntimeError("git object storage cannot be inspected") from exc
        return tuple(sorted(found))

    # -- reading ---------------------------------------------------------------------

    def _resolve(self, spec: str) -> str | None:
        try:
            return self._git.text("rev-parse", "--verify", "--quiet", spec)
        except RepositoryError:
            return None

    def _kind(self, object_id: str) -> str:
        return self._git.text("cat-file", "-t", object_id)

    def head_commit(self) -> str:
        found = self._resolve("HEAD^{commit}")
        if found is None:
            raise RepositoryError("the repository has no commit at HEAD")
        return oid(found)

    def default_branch_commit(self) -> str:
        """The remote default branch (``origin/HEAD``) when set, else local ``main``; fail closed."""
        for spec in ("refs/remotes/origin/HEAD^{commit}", "refs/heads/main^{commit}"):
            found = self._resolve(spec)
            if found is not None:
                return oid(found)
        raise RepositoryError("the default branch cannot be resolved (no origin/HEAD or main)")

    def tree_of(self, commit: str, path: str = "") -> str:
        spec = f"{oid(commit)}:{rel_path(path)}" if path else f"{oid(commit)}^{{tree}}"
        found = self._resolve(spec)
        if found is None or self._kind(found) != "tree":
            raise RepositoryError(f"no tree at {path or '/'} in {commit}")
        return oid(found)

    def _entries(self, tree: str, prefix: str = "") -> list[obj.Entry]:
        if not prefix:
            return obj.parse_ls_tree(self._git.run("ls-tree", "-r", "-z", oid(tree)))
        sub = self._resolve(f"{oid(tree)}:{rel_path(prefix)}")
        if sub is None:
            return []
        if self._kind(sub) != "tree":
            raise RepositoryError(f"{prefix} is not a directory")
        return obj.parse_ls_tree(self._git.run("ls-tree", "-r", "-z", sub), prefix)

    def blobs_under(self, tree: str, prefix: str) -> tuple[HiddenBlob, ...]:
        blobs = []
        for entry in self._entries(tree, prefix):
            if entry.mode not in obj.FILE_MODES:
                raise SnapshotRejected("only regular files are allowed here", entry.path)
            blobs.append(HiddenBlob(entry.path, entry.oid))
        return tuple(sorted(blobs, key=lambda b: b.path))

    def show(self, commit: str, path: str) -> bytes:
        return self._git.run("cat-file", "blob", f"{oid(commit)}:{rel_path(path)}")

    def _contents(self, oids: Sequence[str]) -> dict[str, bytes]:
        unique = list(dict.fromkeys(oids))
        if not unique:
            return {}
        raw = self._git.run("cat-file", "--batch", input="".join(f"{o}\n" for o in unique).encode())
        return obj.parse_batch(raw, unique)

    def dirty_paths(self, prefix: str) -> list[str]:
        raw = self._git.run(
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            "--no-renames",
            "--",
            rel_path(prefix),
        )
        paths = {obj.decode_path(rec[3:]) for rec in raw.split(b"\0") if len(rec) > 3}
        return sorted(p for p in paths if under(p, prefix))

    def merge_base(self, a: str, b: str) -> str:
        found = self._git.text("merge-base", oid(a), oid(b))
        return oid(found)

    def diff_modes(self, old_tree: str, new_tree: str) -> Mapping[str, str]:
        return {path: mode for mode, _oid, path in self._changes(old_tree, new_tree)}

    def _changes(self, old_tree: str, new_tree: str) -> list[tuple[str, str, str]]:
        raw = self._git.run(
            "diff-tree", "-r", "-z", "--raw", "--no-renames", oid(old_tree), oid(new_tree)
        )
        fields = raw.split(b"\0")
        changes = []
        for meta, path in zip(fields[0::2], fields[1::2], strict=False):
            if not meta:
                continue
            _old_mode, new_mode, _old_oid, new_oid, _status = meta.decode("ascii")[1:].split(" ")
            changes.append((new_mode, new_oid, obj.decode_path(path)))
        return changes

    # -- writing trees and refs ------------------------------------------------------

    def _write_tree(self, entries: Sequence[tuple[str, str, str]], *, base: str = "") -> str:
        with _scratch() as tmp:
            index = tmp / "index"
            if base:
                self._git.run("read-tree", base, index=index)
            info = obj.index_info(entries, self._format)
            if info:
                self._git.run("update-index", "-z", "--index-info", input=info, index=index)
            return oid(self._git.text("write-tree", index=index))

    def tree_without(self, tree: str, prefix: str, keep: str | Sequence[str]) -> str:
        rel_path(prefix)
        keep_paths = (keep,) if isinstance(keep, str) else tuple(keep)
        for path in keep_paths:
            rel_path(path)
        kept = [
            (e.mode, e.oid, e.path)
            for e in self._entries(tree)
            if not under(e.path, prefix) or any(under(e.path, path) for path in keep_paths)
        ]
        return self._write_tree(kept)

    def replace_subtree(self, tree: str, prefix: str, source_tree: str, keep: str) -> str:
        rel_path(prefix)
        rel_path(keep)
        kept = [(e.mode, e.oid, e.path) for e in self._entries(tree) if not under(e.path, prefix)]
        kept += [(e.mode, e.oid, e.path) for e in self._entries(source_tree) if under(e.path, keep)]
        return self._write_tree(kept)

    def snapshot(self, worktree: Path) -> str:
        listed = self._untracked_listing(worktree)
        entries: list[tuple[str, str, str]] = []
        blobs: list[bytes] = []
        total = 0
        for path in listed:
            data, executable = obj.read_regular(worktree, path)
            total += len(data)
            if total > obj.MAX_TOTAL_BYTES:
                raise SnapshotRejected("the worktree is too large to snapshot", path)
            mode = obj.EXECUTABLE if executable else obj.REGULAR
            entries.append((mode, obj.blob_oid(data, self._format), path))
            blobs.append(data)
        if blobs:
            self._git.run("fast-import", "--quiet", input=obj.fast_import_stream(blobs))
        return self._write_tree(entries)

    def _untracked_listing(self, worktree: Path) -> list[str]:
        """Every non-ignored file of ``worktree``, listed against an empty index."""
        lister = Git(self._git.git_dir, worktree.resolve(), self._format)
        with _scratch() as tmp:
            raw = lister.run(
                "ls-files", "-z", "--others", "--exclude-standard", index=tmp / "index"
            )
        paths = []
        for record in raw.split(b"\0"):
            if not record:
                continue
            path = obj.decode_path(record)
            if path.endswith("/"):
                raise SnapshotRejected("nested repositories are not allowed", path.rstrip("/"))
            paths.append(path)
        return paths

    def promote(self, baseline: str, base_tree: str, candidate_tree: str, ref: str) -> str:
        if not ref.startswith(GATE_REF_PREFIX):
            raise RepositoryError(f"promote only writes under {GATE_REF_PREFIX}: {ref}")
        self._git.run("check-ref-format", ref)
        changes = self._changes(base_tree, candidate_tree)
        for mode, _oid, path in changes:
            if mode not in obj.FILE_MODES and mode != obj.DELETED:
                raise SnapshotRejected(f"cannot promote a file of mode {mode}", path)
        parent = oid(baseline)
        tree = self._write_tree(changes, base=f"{parent}^{{tree}}")
        commit = self._commit(self._git, tree, parent)
        self._git.run("update-ref", ref, commit)
        return commit

    @staticmethod
    def _commit(git: Git, tree: str, parent: str = "") -> str:
        args = ["commit-tree", tree] + (["-p", parent] if parent else [])
        message = b"cheeseless promote\n" if parent else _PROJECTION_MESSAGE
        return oid(git.run(*args, input=message, extra_env=IDENTITY).decode().strip())

    # -- worktrees -------------------------------------------------------------------

    def export(
        self,
        tree: str,
        dest: Path,
        *,
        exclude: Sequence[str] = (),
        only: Sequence[str] = (),
    ) -> None:
        if dest.is_symlink() or (dest.exists() and (not dest.is_dir() or any(dest.iterdir()))):
            raise RepositoryError(f"export target must be an empty directory: {dest}")
        dest.mkdir(parents=True, exist_ok=True)
        entries = [
            e
            for e in self._entries(tree)
            if not any(under(e.path, x) for x in exclude)
            and (not only or any(under(e.path, o) for o in only))
        ]
        self._write_files(dest, entries)

    def _write_files(self, dest: Path, entries: Sequence[obj.Entry]) -> dict[str, bytes]:
        for entry in entries:
            if entry.mode not in obj.FILE_MODES:
                raise SnapshotRejected("only regular files can be exported", entry.path)
        contents = self._contents([e.oid for e in entries])
        obj.write_tree_files(dest, entries, contents)
        return contents

    def create_projection(self, tree: str, dest: Path) -> None:
        if dest.exists() or dest.is_symlink():
            raise RepositoryError(f"projection target already exists: {dest}")
        entries = self._entries(tree)
        dest.mkdir(parents=True)
        with _scratch() as template:
            proj = init(dest, template, self._format)
        contents = self._write_files(dest, entries)
        if contents:
            proj.run("fast-import", "--quiet", input=obj.fast_import_stream(contents.values()))
        info = obj.index_info([(e.mode, e.oid, e.path) for e in entries], self._format)
        if info:
            proj.run("update-index", "-z", "--index-info", input=info)
        written = oid(proj.text("write-tree"))
        if written != oid(tree):
            raise RepositoryError("the projection tree does not match the requested tree")
        proj.run("update-ref", "refs/heads/main", self._commit(proj, written))
        proj.run("update-index", "-q", "--refresh")

    def sparse_hide(self, keep: Sequence[str], hide: Sequence[str]) -> None:
        patterns = ["/*"]
        patterns += [f"!/{rel_path(h)}/" for h in hide]
        patterns += [f"/{rel_path(k)}/" for k in keep]
        self._git.run(
            "sparse-checkout",
            "set",
            "--no-cone",
            "--stdin",
            input="".join(f"{p}\n" for p in patterns).encode(),
        )

    def stray_hidden(self, worktree: Path, hidden: Sequence[HiddenBlob]) -> list[str]:
        """Hidden paths present in ``worktree`` and files whose content is a hidden blob."""
        found = {b.path for b in hidden if os.path.lexists(worktree / b.path)}
        sizes = self._blob_sizes([b.oid for b in hidden])
        wanted = set(sizes.values())
        targets = set(sizes)
        for rel, full in _walk_files(worktree):
            info = os.lstat(full)
            if not stat.S_ISREG(info.st_mode) or info.st_size not in wanted:
                continue
            try:
                data, _ = obj.read_regular(worktree, rel)
            except (SnapshotRejected, RepositoryError):
                found.add(rel)
                continue
            if obj.blob_oid(data, self._format) in targets:
                found.add(rel)
        return sorted(found)

    def _blob_sizes(self, oids: Sequence[str]) -> dict[str, int]:
        unique = list(dict.fromkeys(oids))
        if not unique:
            return {}
        raw = self._git.run(
            "cat-file", "--batch-check", input="".join(f"{o}\n" for o in unique).encode()
        )
        sizes = {}
        for line in raw.decode("ascii").splitlines():
            parts = line.split(" ")
            if len(parts) == 3 and parts[1] == "blob":
                sizes[parts[0]] = int(parts[2])
        return sizes


def _walk_files(root: Path) -> Iterator[tuple[str, str]]:
    for dirpath, dirnames, filenames in os.walk(root):
        if Path(dirpath) == root and ".git" in dirnames:
            dirnames.remove(".git")
        for name in filenames:
            full = os.path.join(dirpath, name)
            yield os.path.relpath(full, root).replace(os.sep, "/"), full
