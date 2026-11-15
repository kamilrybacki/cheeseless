"""Cheeseless-arm dry run through a real sandboxed session (needs bubblewrap)."""

import os

import pytest

from study import run
from study.tasks import TASKS_BY_ID
from tests.requirements import needs

pytestmark = needs(os.access("/usr/bin/bwrap", os.X_OK), "bubblewrap required")


@pytest.mark.parametrize("task", ["slug", "roman"])
def test_cheeseless_arm_dry_run_matches_expected_labels(tmp_path, task):
    jobs = [
        run.Job(TASKS_BY_ID[task], "cheeseless", 0, b)
        for b in ("honest", "weakener", "literal", "hidden-reader", "defect-conforming")
    ]
    recs = run.run_dry(jobs, tmp_path / "m.jsonl", 1, tmp_path / "w")
    assert len(recs) == 5
    assert run.check_dry(recs) == []
