"""Validation and parsing for the opt-in extras of a host session.

Extra read-only mounts, home seeds and extra environment variables widen what the
sandboxed agent can see, so each one is validated against the locations that protect
hidden tests before anything is launched. Every check fails closed. Messages name the
offending key or path, never a value: environment values are usually credentials.
"""

from __future__ import annotations

import json
import os
import re
import stat
import time
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cheeseless.domain.egress_policy import EgressPolicy

POLICY_FILE_LIMIT = 1 << 20
ENV_VALUE_LIMIT = 1 << 15
SEED_FILE_LIMIT = 1 << 20
MOUNT_WALK_ENTRIES = 200_000
MOUNT_WALK_DEPTH = 64
MOUNT_WALK_SECONDS = 60.0

type Inode = tuple[int, int]

_ENV_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")
_RESERVED_NAMES = frozenset(
    {
        "PATH",
        "HOME",
        "TMPDIR",
        "LANG",
        "NODE_EXTRA_CA_CERTS",
        "SSL_CERT_FILE",
        "CODEX_CA_CERTIFICATE",
    }
)
_RESERVED_PREFIXES = ("CHEESELESS_", "LD_", "TMUX")
_PROXY_NAMES = frozenset({"HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY"})


class SessionOptionError(RuntimeError):
    """An opt-in session option was refused. Nothing has been launched."""


def within(path: Path | str, root: Path | str) -> bool:
    """True when ``path`` is ``root`` or lives below it, after resolving symlinks."""
    real_path = os.path.realpath(path)
    real_root = os.path.realpath(root)
    try:
        return os.path.commonpath((real_path, real_root)) == real_root
    except ValueError:
        return False


def ensure_outside_all(path: Path, protected: Sequence[Path], what: str) -> None:
    """Refuse ``path`` when it is, or lives inside, any protected root."""
    for root in protected:
        if within(path, root):
            raise SessionOptionError(f"{what} must live outside the protected locations")


def _check_reserved(
    real: Path,
    raw: Path,
    reserved_mounts: Sequence[str],
    sandbox_tmp: str | None,
) -> None:
    for reserved in reserved_mounts:
        if within(real, reserved) or within(reserved, real):
            raise SessionOptionError(
                f"extra read-only path overlaps a reserved sandbox mount: {raw}"
            )
    if sandbox_tmp is not None and within(sandbox_tmp, real):
        raise SessionOptionError(
            f"extra read-only path contains the sandbox temporary directory: {raw}"
        )


def _walk_pinned_tree(
    root_fd: int,
    hidden_inodes: frozenset[Inode],
    raw: Path,
) -> None:
    """Reject a pinned tree that contains a hard link to hidden material.

    Iterative and dir_fd-relative so a deep or hostile tree cannot recurse, follow a
    symlink or run without bound. Any cap overflow fails closed.
    """
    stack: list[tuple[str, ...]] = [()]
    entries = 0
    deadline = time.monotonic() + MOUNT_WALK_SECONDS
    while stack:
        rel = stack.pop()
        try:
            dir_fd = _open_dir_rel(root_fd, rel)
        except OSError as exc:
            raise SessionOptionError(f"extra read-only path cannot be inspected: {raw}") from exc
        try:
            try:
                names = []
                with os.scandir(dir_fd) as scan:
                    for entry in scan:
                        if time.monotonic() > deadline:
                            raise SessionOptionError(
                                f"extra read-only path time cap reached: {raw}"
                            )
                        names.append(entry.name)
                        if len(names) > MOUNT_WALK_ENTRIES - entries:
                            raise SessionOptionError(
                                f"extra read-only path has too many entries: {raw}"
                            )
                names.sort()
            except OSError as exc:
                raise SessionOptionError(
                    f"extra read-only path cannot be inspected: {raw}"
                ) from exc
            for name in names:
                if time.monotonic() > deadline:
                    raise SessionOptionError(f"extra read-only path time cap reached: {raw}")
                entries += 1
                if entries > MOUNT_WALK_ENTRIES:
                    raise SessionOptionError(f"extra read-only path has too many entries: {raw}")
                try:
                    info = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
                except OSError as exc:
                    raise SessionOptionError(
                        f"extra read-only path cannot be inspected: {raw}"
                    ) from exc
                if stat.S_ISREG(info.st_mode):
                    if (info.st_dev, info.st_ino) in hidden_inodes:
                        raise SessionOptionError(
                            f"extra read-only path contains hidden material: {raw}"
                        )
                elif stat.S_ISDIR(info.st_mode):
                    if len(rel) + 1 > MOUNT_WALK_DEPTH:
                        raise SessionOptionError(f"extra read-only path is too deep: {raw}")
                    stack.append((*rel, name))
                elif not stat.S_ISLNK(info.st_mode):
                    raise SessionOptionError(f"extra read-only path contains a special file: {raw}")
        finally:
            os.close(dir_fd)


def _open_dir_rel(root_fd: int, rel: tuple[str, ...]) -> int:
    """Open ``rel`` below ``root_fd`` without following symlinks."""
    fd = os.open(".", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC, dir_fd=root_fd)
    try:
        for part in rel:
            child = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=fd,
            )
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _check_pinned_inodes(
    fd: int,
    raw: Path,
    hidden_inodes: frozenset[Inode],
) -> None:
    info = os.fstat(fd)
    if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)):
        raise SessionOptionError(f"extra read-only path is a special file: {raw}")
    if stat.S_ISREG(info.st_mode):
        if (info.st_dev, info.st_ino) in hidden_inodes:
            raise SessionOptionError(f"extra read-only path is hidden material: {raw}")
    elif stat.S_ISDIR(info.st_mode):
        _walk_pinned_tree(fd, hidden_inodes, raw)


def check_extra_read_only(
    paths: Iterable[Path],
    protected: Sequence[Path],
    temp_bases: Sequence[Path] = (),
    hidden_inodes: frozenset[Inode] = frozenset(),
    reserved_mounts: Sequence[str] = (),
    sandbox_tmp: str | None = None,
) -> tuple[PinnedMount, ...]:
    """Pin extra read-only mounts; refuse overlaps with protected roots.

    Each source is opened once and kept open, so a path swapped after validation cannot
    change what is bound. A path is refused when it lies inside a protected root *or*
    contains one: mounting an ancestor would expose everything below it. ``temp_bases``
    are directories in which the session will create private directories later; they
    only need the ancestor check, because they do not exist yet.
    """
    pinned: list[PinnedMount] = []
    try:
        for raw in paths:
            try:
                fd = os.open(raw, os.O_PATH | os.O_CLOEXEC)
            except OSError as exc:
                raise SessionOptionError(f"extra read-only path does not exist: {raw}") from exc
            try:
                real = Path(os.readlink(f"/proc/self/fd/{fd}"))
                if any(within(real, root) or within(root, real) for root in protected):
                    raise SessionOptionError(
                        f"extra read-only path overlaps a protected location: {raw}"
                    )
                if any(within(base, real) for base in temp_bases):
                    raise SessionOptionError(
                        f"extra read-only path contains a session directory: {raw}"
                    )
                _check_reserved(real, raw, reserved_mounts, sandbox_tmp)
                _check_pinned_inodes(fd, raw, hidden_inodes)
            except BaseException:
                os.close(fd)
                raise
            pinned.append(PinnedMount(fd=fd, path=real))
    except BaseException:
        for mount in pinned:
            try:
                os.close(mount.fd)
            except OSError:
                pass
        raise
    return tuple(pinned)


@dataclass(frozen=True)
class PinnedMount:
    """An extra read-only source held open between validation and launch."""

    fd: int
    path: Path


def _check_seed_rel(rel: object) -> str:
    if not isinstance(rel, str) or not rel or "\0" in rel:
        raise SessionOptionError("home seed destination must be a non-empty relative path")
    if rel.startswith("/") or any(part in ("", ".", "..") for part in rel.split("/")):
        raise SessionOptionError(f"home seed destination is not a clean relative path: {rel!r}")
    return rel


def _read_seed(rel: str, source: Path, protected: Sequence[Path]) -> tuple[bytes, Inode]:
    try:
        fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError as exc:
        raise SessionOptionError(f"cannot open home seed source: {rel}") from exc
    try:
        real = Path(os.readlink(f"/proc/self/fd/{fd}"))
        if any(within(real, root) for root in protected):
            raise SessionOptionError(f"home seed source is inside a protected location: {rel}")
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise SessionOptionError(f"home seed source is not a regular file: {rel}")
        if info.st_nlink != 1:
            raise SessionOptionError(f"home seed source has multiple links: {rel}")
        if info.st_size > SEED_FILE_LIMIT:
            raise SessionOptionError(f"home seed source is too large: {rel}")
        chunks: list[bytes] = []
        remaining = SEED_FILE_LIMIT + 1
        while remaining > 0:
            chunk = os.read(fd, min(1 << 16, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b"".join(chunks)
        if len(content) > SEED_FILE_LIMIT:
            raise SessionOptionError(f"home seed source is too large: {rel}")
        return content, (info.st_dev, info.st_ino)
    except OSError as exc:
        raise SessionOptionError(f"cannot read home seed source: {rel}") from exc
    finally:
        os.close(fd)


def check_home_seed(
    seed: Mapping[str, Path] | None,
    protected: Sequence[Path],
    hidden_inodes: frozenset[Inode] = frozenset(),
) -> tuple[tuple[str, bytes], ...]:
    """Validate home seeds; return sorted ``(relative destination, pinned content)``.

    The content is read while the source is open, so replacing the path afterwards
    cannot change what lands in HOME. The content is never placed in a message or repr.
    """
    entries: list[tuple[str, bytes]] = []
    for rel, source in (seed or {}).items():
        clean = _check_seed_rel(rel)
        content, inode = _read_seed(clean, source, protected)
        if inode in hidden_inodes:
            raise SessionOptionError(f"home seed source is hidden material: {clean}")
        entries.append((clean, content))
    entries.sort()
    names = [rel for rel, _ in entries]
    for outer in names:
        if any(inner != outer and inner.startswith(outer + "/") for inner in names):
            raise SessionOptionError(f"home seed destinations conflict: {outer!r}")
    return tuple(entries)


def _env_reserved(key: str, reserved: frozenset[str]) -> bool:
    upper = key.upper()
    return (
        key in reserved
        or upper in _RESERVED_NAMES
        or upper in _PROXY_NAMES
        or upper.startswith(_RESERVED_PREFIXES)
    )


def check_extra_env(
    extra: Mapping[str, str] | None, reserved: Iterable[str]
) -> tuple[tuple[str, str], ...]:
    """Validate additive environment variables; collisions raise, never override."""
    taken = frozenset(reserved)
    pairs: list[tuple[str, str]] = []
    for key, value in (extra or {}).items():
        if not isinstance(key, str) or _ENV_KEY.fullmatch(key) is None:
            raise SessionOptionError(f"invalid environment variable name: {key!r}")
        if _env_reserved(key, taken):
            raise SessionOptionError(f"environment variable {key} is set by the session")
        if not isinstance(value, str) or "\0" in value or len(value) > ENV_VALUE_LIMIT:
            raise SessionOptionError(f"invalid value for environment variable {key}")
        pairs.append((key, value))
    return tuple(sorted(pairs))


@dataclass(frozen=True)
class SessionExtras:
    """The validated, immutable form of a session's opt-in extras."""

    read_only: tuple[PinnedMount, ...] = ()
    seeds: tuple[tuple[str, bytes], ...] = field(default=(), repr=False)
    env: tuple[tuple[str, str], ...] = ()


def prepare_extras(
    *,
    protected: Sequence[Path],
    temp_bases: Sequence[Path],
    reserved_env: Iterable[str],
    extra_read_only: Iterable[Path] | None = None,
    home_seed: Mapping[str, Path] | None = None,
    extra_env: Mapping[str, str] | None = None,
    hidden_inodes: frozenset[Inode] = frozenset(),
    reserved_mounts: Sequence[str] = (),
    sandbox_tmp: str | None = None,
) -> SessionExtras:
    """Validate every opt-in extra at once, before anything is launched."""
    read_only = check_extra_read_only(
        extra_read_only or (),
        protected,
        temp_bases,
        hidden_inodes=hidden_inodes,
        reserved_mounts=reserved_mounts,
        sandbox_tmp=sandbox_tmp,
    )
    try:
        seeds = check_home_seed(home_seed, protected, hidden_inodes)
        env = check_extra_env(extra_env, reserved_env)
    except BaseException:
        for mount in read_only:
            try:
                os.close(mount.fd)
            except OSError:
                pass
        raise
    return SessionExtras(read_only=read_only, seeds=seeds, env=env)


def parse_key_value(raw: str) -> tuple[str, str]:
    """Split ``KEY=VALUE`` at the first ``=``; the value may contain more of them."""
    key, sep, value = raw.partition("=")
    if not sep or not key:
        raise SessionOptionError("expected KEY=VALUE")
    return key, value


def parse_seed_spec(raw: str) -> tuple[str, Path]:
    """Split ``REL=SRC`` for ``--home-seed``."""
    rel, sep, source = raw.partition("=")
    if not sep or not rel or not source:
        raise SessionOptionError("expected REL=SRC for a home seed")
    return rel, Path(source)


def load_egress_policy(path: Path) -> EgressPolicy:
    """Read a TOML or JSON egress-policy file; the domain does the validating."""
    suffix = path.suffix.lower()
    if suffix not in (".toml", ".json"):
        raise SessionOptionError("egress policy file must have a .toml or .json extension")
    try:
        with path.open("rb") as handle:
            raw = handle.read(POLICY_FILE_LIMIT + 1)
        if len(raw) > POLICY_FILE_LIMIT:
            raise SessionOptionError("egress policy file is too large")
        data: Any = tomllib.loads(raw.decode()) if suffix == ".toml" else json.loads(raw)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise SessionOptionError(f"cannot read egress policy file: {type(exc).__name__}") from exc
    if not isinstance(data, Mapping):
        raise SessionOptionError("egress policy file must hold a table")
    return EgressPolicy.from_config(data)
