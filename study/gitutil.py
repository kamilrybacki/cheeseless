"""Plain-git helpers for building study repositories (mirrors tests/toy.py)."""

import os
import subprocess
from collections.abc import Mapping
from pathlib import Path

GIT_ENV = {
    "GIT_AUTHOR_NAME": "Study Author",
    "GIT_AUTHOR_EMAIL": "study@example.invalid",
    "GIT_COMMITTER_NAME": "Study Author",
    "GIT_COMMITTER_EMAIL": "study@example.invalid",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
}


def git(root: Path, *args: str) -> str:
    env = {**os.environ, **GIT_ENV}
    out = subprocess.run(  # noqa: S603 - fixed git argv
        ["git", *args],  # noqa: S607
        cwd=root,
        env=env,
        capture_output=True,
        check=True,
        stdin=subprocess.DEVNULL,
        timeout=30,
    )
    return out.stdout.decode().strip()


def write_files(root: Path, files: Mapping[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def commit_all(root: Path, message: str = "change") -> str:
    git(root, "add", "-A")
    git(root, "commit", "-q", "--no-verify", "-m", message)
    return git(root, "rev-parse", "HEAD")


def make_repo(root: Path, files: Mapping[str, str]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q", "--initial-branch=main")
    write_files(root, files)
    commit_all(root, "baseline")
    return root
