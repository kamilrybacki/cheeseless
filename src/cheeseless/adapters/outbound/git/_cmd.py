"""Hardened git invocation (ADR 0002).

Git runs with a minimal environment, no system or global config, no hooks, no
fsmonitor, no attribute files and no network. ``--attr-source`` points at the empty
tree, so no ``.gitattributes`` in a worktree can request a filter or a conversion.
Command-line ``-c`` settings override anything in the repository's own config. Only
plumbing commands are used, and every path argument is validated first.
"""

import os
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from cheeseless.domain import ids
from cheeseless.ports.repository import RepositoryError

EMPTY_TREE = {
    "sha1": "4b825dc642cb6eb9a060e54bf8d69288fbee4904",
    "sha256": "6ef19b41225c5369f1c104d45d8d85efa9b057b53b14b4b9b939dd74decc5321",
}
TIMEOUT_SECONDS = 300
_STDERR_TAIL = 400

_CONFIG = (
    "core.hooksPath=/dev/null",
    "core.fsmonitor=false",
    "core.untrackedCache=false",
    "core.attributesFile=/dev/null",
    "core.excludesFile=/dev/null",
    "core.autocrlf=false",
    "core.safecrlf=false",
    "core.symlinks=true",
    "core.protectNTFS=true",
    "core.protectHFS=true",
    "core.splitIndex=false",
    "core.pager=cat",
    "core.sshCommand=false",
    "diff.external=",
    "protocol.allow=never",
    "submodule.recurse=false",
    "gc.auto=0",
    "maintenance.auto=false",
    "credential.helper=",
)

_ENV = {
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_ATTR_NOSYSTEM": "1",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_NO_REPLACE_OBJECTS": "1",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_LITERAL_PATHSPECS": "1",
    "GIT_PROTOCOL_FROM_USER": "0",
    "GIT_PAGER": "cat",
    "LC_ALL": "C",
}

IDENTITY = {
    "GIT_AUTHOR_NAME": "cheeseless",
    "GIT_AUTHOR_EMAIL": "cheeseless@localhost.invalid",
    "GIT_AUTHOR_DATE": "@0 +0000",
    "GIT_COMMITTER_NAME": "cheeseless",
    "GIT_COMMITTER_EMAIL": "cheeseless@localhost.invalid",
    "GIT_COMMITTER_DATE": "@0 +0000",
}


def base_env() -> dict[str, str]:
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), **_ENV}


def oid(value: str) -> str:
    try:
        return ids.git_oid(value)
    except ValueError as exc:
        raise RepositoryError(str(exc)) from exc


def rel_path(path: str) -> str:
    """A repository-relative path that cannot be an option or escape the tree."""
    parts = path.split("/")
    if (
        not path
        or path.startswith(("/", "-"))
        or "\0" in path
        or any(p in ("", ".", "..") or p.lower() == ".git" for p in parts)
    ):
        raise RepositoryError(f"invalid repository path: {path!r}")
    return path


def under(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


def _tail(data: bytes) -> str:
    return data.decode("utf-8", errors="replace").strip()[-_STDERR_TAIL:]


def _exec(argv: Sequence[str], *, cwd: Path, env: Mapping[str, str], input: bytes | None) -> bytes:
    try:
        out = subprocess.run(  # noqa: S603 - fixed git argv, validated arguments
            list(argv),
            cwd=cwd,
            env=dict(env),
            input=input,
            capture_output=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RepositoryError(f"git {argv[1]} could not run: {exc}") from exc
    if out.returncode != 0:
        raise RepositoryError(f"git {argv[1]} failed: {_tail(out.stderr)}")
    return out.stdout


@dataclass(frozen=True, slots=True)
class Git:
    """One repository: an explicit git dir and, optionally, an explicit work tree."""

    git_dir: Path
    work_tree: Path | None = None
    object_format: str = "sha1"

    def run(
        self,
        *args: str,
        input: bytes | None = None,
        index: Path | None = None,
        extra_env: Mapping[str, str] | None = None,
    ) -> bytes:
        argv = ["git", f"--attr-source={EMPTY_TREE[self.object_format]}"]
        for item in _CONFIG:
            argv += ["-c", item]
        env = {**base_env(), "GIT_DIR": str(self.git_dir), **(extra_env or {})}
        if self.work_tree is not None:
            env["GIT_WORK_TREE"] = str(self.work_tree)
        if index is not None:
            env["GIT_INDEX_FILE"] = str(index)
        cwd = self.work_tree if self.work_tree is not None else self.git_dir
        return _exec([*argv, *args], cwd=cwd, env=env, input=input)

    def text(self, *args: str, index: Path | None = None) -> str:
        return self.run(*args, index=index).decode().strip()


def init(dest: Path, template: Path, object_format: str = "sha1") -> Git:
    """A brand-new repository at ``dest`` with no hooks or sample files."""
    argv = [
        "git",
        "-c",
        "init.defaultBranch=main",
        "init",
        "-q",
        f"--template={template}",
        f"--object-format={object_format}",
        str(dest),
    ]
    _exec(argv, cwd=dest.parent, env=base_env(), input=None)
    return Git(dest / ".git", dest, object_format)


@dataclass(frozen=True, slots=True)
class Location:
    top: Path
    git_dir: Path
    common_dir: Path
    object_format: str


def discover(cwd: Path) -> Location:
    """Toplevel, git dir, common dir and object format of the repository at ``cwd``."""
    argv = [
        "git",
        "rev-parse",
        "--show-toplevel",
        "--absolute-git-dir",
        "--git-common-dir",
        "--show-object-format",
    ]
    try:
        out = _exec(argv, cwd=cwd, env=base_env(), input=None)
    except RepositoryError as exc:
        raise RepositoryError(f"not inside a git repository: {cwd}") from exc
    lines = out.decode().splitlines()
    if len(lines) != 4 or lines[3] not in EMPTY_TREE:
        raise RepositoryError(f"unexpected git rev-parse output in {cwd}")
    common = Path(lines[2])
    if not common.is_absolute():
        common = cwd / common
    return Location(Path(lines[0]).resolve(), Path(lines[1]), common.resolve(), lines[3])
