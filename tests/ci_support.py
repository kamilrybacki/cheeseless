"""Helpers that drive the ci-export / ci-judge CLI against the toy repository."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from cheeseless.bootstrap import main
from tests.toy import commit_all, git, write_files


@dataclass(frozen=True)
class CiWorld:
    toy: Path
    state: Path
    bundle: Path
    base: str


def prepare_world(toy: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CiWorld:
    state = tmp_path / "state"
    monkeypatch.chdir(toy)
    assert main(["--state-dir", str(state), "freeze", "--yes"]) == 0
    bundle = tmp_path / "secrets" / "bundle.json"
    bundle.parent.mkdir(mode=0o700)
    assert main(["--state-dir", str(state), "ci-export", "--out", str(bundle)]) == 0
    return CiWorld(toy, state, bundle, git(toy, "rev-parse", "HEAD"))


def make_head(world: CiWorld, files: dict[str, str], branch: str = "pr") -> str:
    git(world.toy, "checkout", "-q", "-b", branch, world.base)
    write_files(world.toy, files)
    return commit_all(world.toy, "pr")


def judge_cli(
    world: CiWorld,
    head: str,
    *extra: str,
    bundle: Path | None = None,
    base: str | None = None,
) -> int:
    argv = ["ci-judge", "--base", base or world.base, "--head", head, "--bundle-file"]
    argv.append(str(bundle or world.bundle))
    return main([*argv, *extra])


def holdout_ids(world: CiWorld) -> list[str]:
    data = json.loads(world.bundle.read_bytes())
    return list(data["task"]["holdout"])
