"""ci-judge end to end on the toy repository."""

import json
import os
from pathlib import Path

import pytest

from tests.ci_support import judge_cli, make_head, prepare_world
from tests.requirements import needs
from tests.toy import FIXED_MAIN

BWRAP = os.access("/usr/bin/bwrap", os.X_OK)


@needs(BWRAP, "bubblewrap is required")
def test_unfixed_head_fails_and_fixed_head_passes(
    toy_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    world = prepare_world(toy_repo, tmp_path, monkeypatch)
    capsys.readouterr()
    unfixed = make_head(world, {"src/toy/note.txt": "unrelated"}, branch="unfixed")
    assert judge_cli(world, unfixed, "--json") == 1
    assert json.loads(capsys.readouterr().out)["status"] == "fail"
    fixed = make_head(world, {"src/toy/__main__.py": FIXED_MAIN}, branch="fixed")
    assert judge_cli(world, fixed, "--json") == 0
    verdict = json.loads(capsys.readouterr().out)
    assert verdict["status"] == "pass"
    assert {c["status"] for c in verdict["criteria"]} == {"pass"}
