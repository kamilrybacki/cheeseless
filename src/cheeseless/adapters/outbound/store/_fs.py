"""Private-file primitives for the trusted store: no symlinks, no wider modes."""

import errno
import fcntl
import os
import secrets
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from cheeseless.ports.store import StoreError

_DIR_MODE = 0o700
_FILE_MODE = 0o600
_NOFOLLOW = os.O_NOFOLLOW | os.O_CLOEXEC


def ensure_private_dir(path: Path) -> None:
    """Create ``path`` 0700, or check that an existing one is ours and private."""
    try:
        path.mkdir(mode=_DIR_MODE, parents=False)
    except FileExistsError:
        pass
    except OSError as exc:
        raise StoreError(f"cannot create store directory {path}: {exc}") from exc
    st = path.lstat()
    if not stat.S_ISDIR(st.st_mode):
        raise StoreError(f"store path is not a directory: {path}")
    if st.st_uid != os.geteuid():
        raise StoreError(f"store directory is owned by another user: {path}")
    if stat.S_IMODE(st.st_mode) & 0o077:
        raise StoreError(f"store directory is readable by others: {path}")


def _open(path: Path, flags: int, mode: int = _FILE_MODE) -> int:
    try:
        fd = os.open(path, flags | _NOFOLLOW, mode)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise StoreError(f"store file is a symlink: {path}") from exc
        raise
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise StoreError(f"store file is not a regular file: {path}")
    return fd


def read_bytes(path: Path) -> bytes | None:
    """File contents, or ``None`` if it does not exist."""
    try:
        fd = _open(path, os.O_RDONLY)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise StoreError(f"cannot read {path}: {exc}") from exc
    with os.fdopen(fd, "rb") as handle:
        return handle.read()


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view) :]
    os.fsync(fd)


def write_atomic(path: Path, data: bytes) -> None:
    """Replace ``path`` with ``data`` via a fresh 0600 temporary file."""
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        fd = _open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        try:
            _write_all(fd, data)
        finally:
            os.close(fd)
        os.replace(tmp, path)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise StoreError(f"cannot write {path}: {exc}") from exc


def create_exclusive(path: Path, data: bytes) -> bool:
    """Create ``path`` with ``data``. ``False`` if it already exists."""
    try:
        fd = _open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError:
        return False
    except OSError as exc:
        raise StoreError(f"cannot create {path}: {exc}") from exc
    try:
        _write_all(fd, data)
    finally:
        os.close(fd)
    return True


def open_append(path: Path) -> int:
    """Open (or create) a private append-only regular file; the caller owns the fd."""
    try:
        return _open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT)
    except OSError as exc:
        raise StoreError(f"cannot open {path}: {exc}") from exc


def list_regular(directory: Path) -> tuple[Path, ...]:
    """Regular files directly under ``directory``, sorted; a symlink fails closed."""
    try:
        entries = list(os.scandir(directory))
    except FileNotFoundError:
        return ()
    except OSError as exc:
        raise StoreError(f"cannot list {directory}: {exc}") from exc
    files: list[Path] = []
    for entry in entries:
        if entry.is_symlink():
            raise StoreError(f"store entry is a symlink: {entry.path}")
        if entry.is_file(follow_symlinks=False):
            files.append(Path(entry.path))
    return tuple(sorted(files, key=lambda path: path.name))


def append_line(path: Path, line: bytes) -> None:
    try:
        fd = _open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT)
    except OSError as exc:
        raise StoreError(f"cannot append to {path}: {exc}") from exc
    try:
        _write_all(fd, line.rstrip(b"\n") + b"\n")
    finally:
        os.close(fd)


@contextmanager
def locked(path: Path) -> Iterator[None]:
    """Exclusive advisory lock shared by every process using this store."""
    try:
        fd = _open(path, os.O_RDWR | os.O_CREAT)
    except OSError as exc:
        raise StoreError(f"cannot open lock {path}: {exc}") from exc
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


@contextmanager
def try_locked(path: Path) -> Iterator[bool]:
    """Non-blocking exclusive advisory lock: yields whether it was acquired.

    The lock lives as long as the descriptor, so a process that dies releases it."""
    try:
        fd = _open(path, os.O_RDWR | os.O_CREAT)
    except OSError as exc:
        raise StoreError(f"cannot open lock {path}: {exc}") from exc
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True
    finally:
        os.close(fd)
