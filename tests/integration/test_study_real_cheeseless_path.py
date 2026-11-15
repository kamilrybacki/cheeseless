import json
import os
import stat

from study import manifest, run
from study.fake_agent import solution_main
from study.tasks import TASKS_BY_ID
from tests.requirements import needs

pytestmark = needs(os.access("/usr/bin/bwrap", os.X_OK), "bubblewrap required")


def test_honest_agent_that_skips_gate_run_is_false_reject(tmp_path, monkeypatch):
    ro = tmp_path / "ro"
    ro.mkdir()
    (ro / "sol.py").write_text(solution_main(TASKS_BY_ID["slug"]))
    wrapper = ro / "agent.sh"
    wrapper.write_text(f"#!/bin/sh\ncp {ro}/sol.py src/slugtool/__main__.py\nexit 0\n")
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
    for k, v in {
        "CHEESELESS_STUDY_AGENT": "1",
        "CHEESELESS_STUDY_AGENT_CMD": json.dumps([str(wrapper)]),
        "CHEESELESS_STUDY_AGENT_RO": json.dumps([str(wrapper.parent)]),
        "CHEESELESS_STUDY_AGENT_ID": json.dumps({"vendor": "t", "model": "m", "version": "1"}),
    }.items():
        monkeypatch.setenv(k, v)
    mpath = tmp_path / "m.jsonl"
    argv = [
        "--manifest", str(mpath), "--workdir", str(tmp_path / "w"),
        "--evidence-dir", str(tmp_path / "e"), "--arms", "cheeseless", "--tasks", "slug",
        "--reps", "1", "--yes",
    ]  # fmt: skip
    assert run.main(argv, out=lambda _s: None) == 0
    rec = list(manifest.read_all(mpath))[0]
    assert rec["status"] == "valid"
    assert rec["oracle_verdict"] == "pass" and rec["judge_verdict"] == "reject"
    assert rec["labels"]["false_reject"] and not rec["labels"]["success"]
