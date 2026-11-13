"""Object records, blob hashing and symlink-safe file access for the git adapter.

Worktree files are read and written through ``openat`` with ``O_NOFOLLOW`` on every
path component, so a symlink planted anywhere on the way is refused, never followed.
"""

import errno
import hashlib
import os
import stat
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from cheeseless.adapters.outbound.git._cmd import rel_path
from cheeseless.ports.repository import RepositoryError, SnapshotRejected

REGULAR = "100644"
EXECUTABLE = "100755"
SYMLINK = "120000"
GITLINK = "160000"
DELETED = "000000"
FILE_MODES = frozenset({REGULAR, EXECUTABLE})
MAX_TOTAL_BYTES = 512 * 1024 * 1024

_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
_WRITE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
_UNSAFE_ERRNOS = frozenset({errno.ELOOP, errno.ENOTDIR, errno.ENXIO, errno.EISDIR})


@dataclass(frozen=True, slots=True)
class Entry:
    mode: str
    kind: str
    oid: str
    path: str


def decode_path(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SnapshotRejected("path is not valid UTF-8", raw.decode("utf-8", "replace")) from exc


def parse_ls_tree(raw: bytes, prefix: str = "") -> list[Entry]:
    """Records of ``git ls-tree -z``; ``prefix`` is prepended to every path."""
    entries = []
    for record in raw.split(b"\0"):
        if not record:
            continue
        meta, _, name = record.partition(b"\t")
        mode, kind, oid = meta.decode("ascii").split(" ")
        path = decode_path(name)
        entries.append(Entry(mode, kind, oid, f"{prefix}/{path}" if prefix else path))
    return entries


def blob_oid(data: bytes, object_format: str) -> str:
    header = b"blob %d\0" % len(data)
    if object_format == "sha256":
        return hashlib.sha256(header + data).hexdigest()
    return hashlib.sha1(header + data, usedforsecurity=False).hexdigest()


def parse_batch(raw: bytes, oids: Iterable[str]) -> dict[str, bytes]:
    """Contents from ``git cat-file --batch`` output, in the order the oids were asked."""
    out: dict[str, bytes] = {}
    pos = 0
    for wanted in oids:
        end = raw.index(b"\n", pos)
        header = raw[pos:end].decode("ascii").split(" ")
        if len(header) != 3 or header[0] != wanted or header[1] != "blob":
            raise RepositoryError(f"object {wanted} is missing or not a blob")
        size = int(header[2])
        out[wanted] = raw[end + 1 : end + 1 + size]
        pos = end + 1 + size + 1
    return out


def fast_import_stream(blobs: Iterable[bytes]) -> bytes:
    parts = [b"blob\ndata %d\n%s\n" % (len(data), data) for data in blobs]
    return b"".join(parts) + b"done\n"


def index_info(entries: Iterable[tuple[str, str, str]], object_format: str) -> bytes:
    """``update-index -z --index-info`` input; mode "000000" removes the path."""
    zero = "0" * (64 if object_format == "sha256" else 40)
    lines = []
    for mode, oid, path in entries:
        if mode == DELETED:
            lines.append(f"0 {zero}\t{path}\0")
        else:
            kind = "commit" if mode == GITLINK else "blob"
            lines.append(f"{mode} {kind} {oid}\t{path}\0")
    return "".join(lines).encode()


def _open_dir_chain(root: Path, parts: list[str], rel: str, *, create: bool) -> int:
    fd = os.open(root, _DIR_FLAGS)
    try:
        for part in parts:
            if create:
                try:
                    os.mkdir(part, 0o755, dir_fd=fd)
                except FileExistsError:
                    pass
            nfd = os.open(part, _DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = nfd
    except OSError as exc:
        os.close(fd)
        if exc.errno in _UNSAFE_ERRNOS:
            raise SnapshotRejected("symlink or non-directory on the path", rel) from exc
        raise SnapshotRejected(f"cannot open directory: {exc.strerror}", rel) from exc
    return fd


def read_regular(root: Path, rel: str) -> tuple[bytes, bool]:
    """Content and executable bit of a regular file, with no symlink on the way."""
    parts = rel_path(rel).split("/")
    dfd = _open_dir_chain(root, parts[:-1], rel, create=False)
    try:
        fd = os.open(parts[-1], _READ_FLAGS, dir_fd=dfd)
    except OSError as exc:
        if exc.errno in _UNSAFE_ERRNOS:
            raise SnapshotRejected("symlinks are not allowed in a snapshot", rel) from exc
        raise SnapshotRejected(f"cannot read file: {exc.strerror}", rel) from exc
    finally:
        os.close(dfd)
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise SnapshotRejected("only regular files can be snapshotted", rel)
        return handle.read(), bool(info.st_mode & stat.S_IXUSR)


def write_regular(root: Path, rel: str, data: bytes, executable: bool) -> None:
    parts = rel_path(rel).split("/")
    dfd = _open_dir_chain(root, parts[:-1], rel, create=True)
    mode = 0o755 if executable else 0o644
    try:
        fd = os.open(parts[-1], _WRITE_FLAGS, mode, dir_fd=dfd)
    except OSError as exc:
        raise RepositoryError(f"cannot create {rel}: {exc.strerror}") from exc
    finally:
        os.close(dfd)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
        os.fchmod(handle.fileno(), mode)


def write_tree_files(root: Path, entries: Iterable[Entry], contents: Mapping[str, bytes]) -> None:
    for entry in entries:
        write_regular(root, entry.path, contents[entry.oid], entry.mode == EXECUTABLE)
