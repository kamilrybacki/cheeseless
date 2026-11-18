"""``launchers.toml``: host-owned declarations of which command runs which model.

The file lives outside the repository, in the operator's config directory. It is refused
unless it is a regular file owned by the current user that nobody else can write, in a
directory that is likewise ours and not writable by others; otherwise another account could
change which model cheeseless believes it launched.
"""

import os
import stat
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from cheeseless.domain.launcher import Launcher, LauncherError, check_launcher_name


def default_config_path(env: Mapping[str, str] | None = None) -> Path:
    source = os.environ if env is None else env
    base = source.get("XDG_CONFIG_HOME")
    root = Path(base) if base and Path(base).is_absolute() else Path.home() / ".config"
    return root / "cheeseless" / "launchers.toml"


def _check_owner_and_mode(st: os.stat_result, what: str, path: Path) -> None:
    if st.st_uid != os.geteuid():
        raise LauncherError(f"{what} is not owned by the current user: {path}")
    if stat.S_IMODE(st.st_mode) & 0o022:
        raise LauncherError(f"{what} is writable by group or others: {path}")


def _read_trusted(path: Path) -> bytes:
    try:
        parent = path.parent.stat()
    except OSError as exc:
        raise LauncherError(f"launcher config directory is unreadable: {path.parent}") from exc
    _check_owner_and_mode(parent, "launcher config directory", path.parent)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError as exc:
        raise LauncherError(f"no launcher config at {path}") from exc
    except OSError as exc:
        raise LauncherError(f"launcher config cannot be opened (symlink?): {path}") from exc
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise LauncherError(f"launcher config is not a regular file: {path}")
        _check_owner_and_mode(st, "launcher config", path)
        with os.fdopen(fd, "rb", closefd=False) as handle:
            return handle.read()
    finally:
        os.close(fd)


def _parse(raw: bytes, path: Path) -> dict[str, Launcher]:
    try:
        doc: dict[str, Any] = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise LauncherError(f"launcher config is not valid TOML: {path}") from exc
    table = doc.get("launchers", {})
    if not isinstance(table, dict):
        raise LauncherError("`launchers` must be a table")
    found: dict[str, Launcher] = {}
    for name, entry in table.items():
        check_launcher_name(name)
        if not isinstance(entry, dict):
            raise LauncherError(f"launcher {name!r} must be a table")
        command = entry.get("command")
        if not isinstance(command, list) or not all(isinstance(a, str) for a in command):
            raise LauncherError(f"launcher {name!r}: command must be a list of strings")
        model = entry.get("model")
        if not isinstance(model, str):
            raise LauncherError(f"launcher {name!r}: model must be a string")
        found[name] = Launcher(name=name, command=tuple(command), model=model)
    return found


class TomlLauncherCatalog:
    """Reads and validates the file on every lookup, so an edit takes effect at once."""

    def __init__(self, path: Path | None = None, forbidden: tuple[Path, ...] = ()) -> None:
        self._path = path if path is not None else default_config_path()
        self._forbidden = forbidden

    @property
    def path(self) -> Path:
        return self._path

    def get(self, name: str) -> Launcher:
        check_launcher_name(name)
        resolved = self._path.resolve()
        for root in self._forbidden:
            if resolved.is_relative_to(root.resolve()):
                raise LauncherError(f"launcher config must live outside the repository: {root}")
        launchers = _parse(_read_trusted(self._path), self._path)
        try:
            return launchers[name]
        except KeyError:
            raise LauncherError(f"no launcher named {name!r} in {self._path}") from None
