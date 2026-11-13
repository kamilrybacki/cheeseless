"""Filesystem helpers for host-session extras: home seeding and evidence capture.

Both run on the host over trees the agent controls or has just written, so every file
is opened without following symlinks, must be a regular file, and is read in chunks
under a byte cap. Directories are 0700 and files 0600 regardless of umask. Walks are
iterative and dir_fd-relative, so deep or hostile trees cannot recurse or follow a link.
"""

from __future__ import annotations

import json
import os
import stat
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from cheeseless.session_options import SessionOptionError, within

EVIDENCE_LIMIT = 50 * 1024 * 1024
EVIDENCE_ENTRIES = 100_000
EVIDENCE_DEPTH = 64
EVIDENCE_SECONDS = 60.0
_CHUNK = 1 << 16
_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
_WRITE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_IGNORED_PROJECTION = (".cheeseless-egress-", ".cheeseless-ready-")


@dataclass(frozen=True)
class EvidenceResult:
    files: int
    size: int
    truncated: bool


class _StopWalk(Exception):
    """A walk cap was hit; the reason names the cap, never a value."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@contextmanager
def _open_regular(name: str, dir_fd: int | None = None) -> Iterator[int | None]:
    """Yield a read fd for a regular file, or ``None`` for anything else."""
    try:
        fd = os.open(name, _READ_FLAGS, dir_fd=dir_fd)
    except OSError:
        yield None
        return
    try:
        yield fd if stat.S_ISREG(os.fstat(fd).st_mode) else None
    finally:
        os.close(fd)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        view = view[written:]


def _write_new_at(dir_fd: int, name: str, data: bytes) -> None:
    out = os.open(name, _WRITE_FLAGS, 0o600, dir_fd=dir_fd)
    try:
        os.fchmod(out, 0o600)
        _write_all(out, data)
    finally:
        os.close(out)


def _write_private(src_fd: int, dir_fd: int, name: str, budget: int | None) -> int:
    """Copy ``src_fd`` into a new 0600 file; return bytes written (stops at ``budget``)."""
    out = os.open(name, _WRITE_FLAGS, 0o600, dir_fd=dir_fd)
    written = 0
    try:
        os.fchmod(out, 0o600)
        while True:
            want = _CHUNK if budget is None else min(_CHUNK, budget - written + 1)
            chunk = os.read(src_fd, want)
            if not chunk:
                return written
            if budget is not None and written + len(chunk) > budget:
                return budget + 1
            _write_all(out, chunk)
            written += len(chunk)
    finally:
        os.close(out)


def _open_dir_rel(root_fd: int, rel: tuple[str, ...]) -> int:
    """Open ``rel`` below ``root_fd`` without following symlinks."""
    fd = os.open(".", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC, dir_fd=root_fd)
    try:
        for part in rel:
            child = os.open(part, _DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _ensure_dir_rel(root_fd: int, rel: tuple[str, ...]) -> int:
    """Create ``rel`` below ``root_fd`` (if needed) and return its fd."""
    fd = os.open(".", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC, dir_fd=root_fd)
    try:
        for part in rel:
            try:
                os.mkdir(part, 0o700, dir_fd=fd)
            except FileExistsError:
                pass
            child = os.open(part, _DIR_FLAGS, dir_fd=fd)
            os.fchmod(child, 0o700)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _make_exclusive_dir(parent_fd: int, name: str, what: str) -> int:
    try:
        os.mkdir(name, 0o700, dir_fd=parent_fd)
    except FileExistsError:
        raise SessionOptionError(f"{what} already exists") from None
    fd = os.open(name, _DIR_FLAGS, dir_fd=parent_fd)
    os.fchmod(fd, 0o700)
    return fd


def _walk_tree(
    root_fd: int,
    *,
    on_dir: Callable[[tuple[str, ...]], None],
    on_file: Callable[[tuple[str, ...], str, int], None],
    max_entries: int | None = None,
    max_depth: int | None = None,
    seconds: float | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> str | None:
    """Walk ``root_fd`` iteratively; return the cap that stopped it, if any."""
    maximum_entries = EVIDENCE_ENTRIES if max_entries is None else max_entries
    maximum_depth = EVIDENCE_DEPTH if max_depth is None else max_depth
    limit_seconds = EVIDENCE_SECONDS if seconds is None else seconds
    stack: list[tuple[str, ...]] = [()]
    entries = 0
    deadline = clock() + limit_seconds
    try:
        while stack:
            if clock() > deadline:
                return "time cap reached"
            rel = stack.pop()
            try:
                dir_fd = _open_dir_rel(root_fd, rel)
            except OSError:
                continue
            try:
                try:
                    names = sorted(os.listdir(dir_fd))
                except OSError:
                    continue
                on_dir(rel)
                for name in names:
                    entries += 1
                    if entries > maximum_entries:
                        return "entry cap reached"
                    try:
                        info = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
                    except OSError:
                        continue
                    if stat.S_ISREG(info.st_mode):
                        on_file(rel, name, dir_fd)
                    elif stat.S_ISDIR(info.st_mode):
                        if len(rel) + 1 > maximum_depth:
                            return "depth cap reached"
                        stack.append((*rel, name))
            finally:
                os.close(dir_fd)
    except _StopWalk as stop:
        return stop.reason
    return None


def seed_home(home: Path, entries: Sequence[tuple[str, bytes]]) -> None:
    """Write each pinned seed into ``home`` (content only, never a symlink)."""
    root_fd = os.open(home, _DIR_FLAGS)
    try:
        for rel, data in entries:
            parts = rel.split("/")
            try:
                parent = _ensure_dir_rel(root_fd, tuple(parts[:-1]))
            except OSError as exc:
                raise SessionOptionError(f"cannot create home seed directory: {rel}") from exc
            try:
                try:
                    _write_new_at(parent, parts[-1], data)
                except FileExistsError:
                    raise SessionOptionError(
                        f"home seed destination already exists: {rel}"
                    ) from None
                except OSError as exc:
                    raise SessionOptionError(f"cannot write home seed: {rel}") from exc
            finally:
                os.close(parent)
    finally:
        os.close(root_fd)


def _copy_tree_files(home: Path, out_root_fd: int, limit: int) -> tuple[EvidenceResult, str | None]:
    files = size = 0
    root_fd = os.open(home, _DIR_FLAGS)
    try:

        def on_dir(rel: tuple[str, ...]) -> None:
            fd = _ensure_dir_rel(out_root_fd, rel)
            os.close(fd)

        def on_file(rel: tuple[str, ...], name: str, dir_fd: int) -> None:
            nonlocal files, size
            with _open_regular(name, dir_fd) as src:
                if src is None:
                    return
                if os.fstat(src).st_size > limit - size:
                    raise _StopWalk("size cap reached")
                dest_fd = _open_dir_rel(out_root_fd, rel)
                try:
                    copied = _write_private(src, dest_fd, name, limit - size)
                finally:
                    os.close(dest_fd)
                if copied > limit - size:
                    raise _StopWalk("size cap reached")
                files, size = files + 1, size + copied

        reason = _walk_tree(root_fd, on_dir=on_dir, on_file=on_file)
    finally:
        os.close(root_fd)
    return EvidenceResult(files, size, reason is not None), reason


def _create_evidence_dir(dest: Path, protected: Sequence[Path]) -> int:
    if os.path.lexists(dest):
        raise SessionOptionError("evidence destination already exists")
    dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.mkdir(dest, 0o700)
    except FileExistsError:
        raise SessionOptionError("evidence destination already exists") from None
    try:
        fd = os.open(dest, _DIR_FLAGS)
    except BaseException:
        os.rmdir(dest)
        raise
    os.fchmod(fd, 0o700)
    real = Path(os.readlink(f"/proc/self/fd/{fd}"))
    if any(within(real, root) for root in protected):
        os.close(fd)
        os.rmdir(dest)
        raise SessionOptionError("evidence directory must live outside the protected locations")
    return fd


def _write_marker(dest_fd: int, reason: str) -> None:
    try:
        _write_new_at(dest_fd, "TRUNCATED", f"{reason}; evidence is incomplete\n".encode())
    except FileExistsError:
        pass


def copy_evidence(
    home: Path,
    dest: Path,
    limit: int = EVIDENCE_LIMIT,
    *,
    protected: Sequence[Path] = (),
) -> EvidenceResult:
    """Copy the regular files under ``home`` into a brand-new ``dest/home`` tree."""
    dest_fd = _create_evidence_dir(dest, protected)
    try:
        out_root_fd = _make_exclusive_dir(dest_fd, "home", "evidence directory")
        try:
            result, reason = _copy_tree_files(home, out_root_fd, limit)
        finally:
            os.close(out_root_fd)
        if reason is not None:
            _write_marker(dest_fd, reason)
        return result
    finally:
        os.close(dest_fd)


def write_projection_list(projection: Path, dest: Path) -> None:
    """Write a JSON array of the projection's sorted regular-file paths."""
    dest_fd = os.open(dest, _DIR_FLAGS)
    root_fd = os.open(projection, _DIR_FLAGS)
    found: list[str] = []
    try:

        def on_dir(rel: tuple[str, ...]) -> None:
            return None

        def on_file(rel: tuple[str, ...], name: str, dir_fd: int) -> None:
            if name.startswith(_IGNORED_PROJECTION):
                return
            found.append("/".join((*rel, name)))

        reason = _walk_tree(root_fd, on_dir=on_dir, on_file=on_file)
        data = json.dumps(sorted(found), ensure_ascii=True).encode("ascii")
        try:
            _write_new_at(dest_fd, "projection-files.json", data)
        except FileExistsError:
            raise SessionOptionError("evidence directory already holds a projection list") from None
        if reason is not None:
            _write_marker(dest_fd, reason)
    finally:
        os.close(root_fd)
        os.close(dest_fd)
