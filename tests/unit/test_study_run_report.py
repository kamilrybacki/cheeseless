import json
import re
import stat
from pathlib import Path

import pytest

from study import agent_config, manifest, report, run
from study.agent_config import AgentUnavailable, load_study_agent
from study.expected import EXPECTED
from study.prompt import template_hash
from study.tasks import TASKS_BY_ID

ROOT = Path(__file__).resolve().parents[2]


def test_protocol_pins_template_hash_and_freeze_commit():
    text = (ROOT / "docs/study/protocol.md").read_text()
    assert template_hash() in text
    assert re.search(r"frozen at the commit", text)
    assert "held out" in text.lower()


def test_expected_table_is_complete():
    assert len(EXPECTED) == 60
    assert all(set(v) <= set("SGFAB-") for v in EXPECTED.values())


def test_dry_run_naive_matches_expected(tmp_path):
    path = tmp_path / "m.jsonl"
    jobs = [
        run.Job(TASKS_BY_ID[t], "naive", 0, b)
        for t in ("slug", "roman")
        for b in ("honest", "weakener", "literal", "hidden-reader", "defect-conforming")
    ]
    recs = run.run_dry(jobs, path, 1, tmp_path / "w")
    assert len(recs) == 10
    assert run.check_dry(recs) == []
    # resumable: a second pass does nothing
    assert run.run_dry(jobs, path, 1, tmp_path / "w2") == []


def test_check_dry_reports_mismatch():
    rec = {
        "task_id": "slug",
        "behaviour": "honest",
        "arm": "naive",
        "labels": {"gamed": True},
    }
    assert run.check_dry([rec]) != []
    assert run.label_code(rec) == "G"
    assert run.label_code({"labels": {}}) == "-"


def test_real_mode_refuses_without_opt_in(monkeypatch):
    monkeypatch.delenv("CHEESELESS_STUDY_AGENT", raising=False)
    lines: list[str] = []
    assert run.main([], out=lines.append) == 2
    assert "opt-in" in lines[0]


def _env(tmp_path: Path, script: str) -> dict[str, str]:
    wrapper = tmp_path / "agent.sh"
    wrapper.write_text(script)
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
    return {
        "CHEESELESS_STUDY_AGENT": "1",
        "CHEESELESS_STUDY_AGENT_CMD": json.dumps([str(wrapper)]),
        "CHEESELESS_STUDY_AGENT_ID": json.dumps({"vendor": "test", "model": "m", "version": "1"}),
    }


def test_agent_config_validation(tmp_path):
    with pytest.raises(AgentUnavailable):
        load_study_agent({})
    env = _env(tmp_path, "#!/bin/sh\nexit 0\n")
    agent = load_study_agent(env)
    assert agent.agent.label == "test/m/1" and agent.timeout == agent_config.DEFAULT_TIMEOUT
    for key, bad in (
        ("CHEESELESS_STUDY_AGENT_TIMEOUT", "0"),
        ("CHEESELESS_STUDY_AGENT_TIMEOUT", "x"),
        ("CHEESELESS_STUDY_AGENT_SEED", "[1]"),
        ("CHEESELESS_STUDY_AGENT_RO", "{}"),
        ("CHEESELESS_STUDY_AGENT_ID", "{}"),
        ("CHEESELESS_STUDY_AGENT_CMD", "[]"),
    ):
        with pytest.raises(AgentUnavailable):
            load_study_agent({**env, key: bad})


def test_real_mode_needs_yes_then_runs_naive_with_scripted_wrapper(tmp_path, monkeypatch):
    for k, v in _env(tmp_path, "#!/bin/sh\nexit 0\n").items():
        monkeypatch.setenv(k, v)
    mpath = tmp_path / "m.jsonl"
    argv = [
        "--manifest", str(mpath), "--workdir", str(tmp_path / "w"),
        "--evidence-dir", str(tmp_path / "e"), "--tasks", "slug", "--arms", "naive",
        "--reps", "1",
    ]  # fmt: skip
    lines: list[str] = []
    assert run.main(argv, out=lines.append) == 2
    assert any("planned runs: 1" in line for line in lines) and not mpath.exists()
    lines.clear()
    assert run.main([*argv, "--yes"], out=lines.append) == 0
    recs = list(manifest.read_all(mpath))
    assert len(recs) == 1 and recs[0]["mode"] == "real"
    assert recs[0]["transcript_observed"] is False
    # resumable
    assert run.main([*argv, "--yes"], out=lines.append) == 0
    assert len(list(manifest.read_all(mpath))) == 1


def test_naive_agent_nonzero_exit_is_infra_error_not_blocked(tmp_path, monkeypatch):
    for k, v in _env(tmp_path, "#!/bin/sh\nexit 3\n").items():
        monkeypatch.setenv(k, v)
    mpath = tmp_path / "m.jsonl"
    argv = [
        "--manifest", str(mpath), "--workdir", str(tmp_path / "w"),
        "--evidence-dir", str(tmp_path / "e"), "--tasks", "roman", "--arms", "naive",
        "--reps", "1", "--yes",
    ]  # fmt: skip
    assert run.main(argv, out=lambda _s: None) == 0
    rec = list(manifest.read_all(mpath))[0]
    assert rec["status"] == "infra-error" and not rec["labels"]["blocked"]


def test_redaction_hit_stores_invalid_stub(tmp_path):
    path = tmp_path / "m.jsonl"
    rec = {
        "record_type": "run", "mode": "real", "run_id": "x", "attempt": 1,
        "study_id": "s", "arm": "naive", "task_id": "slug", "note": "/home/someone/x",
    }  # fmt: skip
    stub = run._append_safe(path, rec)
    assert stub["status"] == "invalid" and stub["exclusion_reason"] == "redaction"
    assert "someone" not in path.read_text()


def _rec(arm, task, solvable, **labels):
    base = dict.fromkeys(("success", "gamed", "false_reject", "attempted", "blocked"), False)
    base.update(labels, counted=True)
    return {
        "record_type": "run", "mode": "real", "run_id": f"{arm}{task}{len(labels)}",
        "arm": arm, "task_id": task, "solvable": solvable, "status": "valid",
        "agent": {"vendor": "v", "model": "m", "version": "1"}, "labels": base,
    }  # fmt: skip


def test_report_no_data_and_dry_records_ignored():
    assert "No data" in report.render([])
    dry = {**_rec("naive", "roman", False), "mode": "dry-run"}
    assert report.render([dry]) == report.NO_DATA


def test_report_k_of_n_wording_and_limits():
    recs = [_rec("naive", "roman", False, gamed=True, attempted=True)] + [
        _rec("cheeseless", "roman", False) for _ in range(1)
    ]
    recs += [{**_rec("naive", "roman", False), "run_id": f"n{i}"} for i in range(3)]
    recs += [{**_rec("cheeseless", "median", False), "run_id": f"c{i}"} for i in range(3)]
    recs.append({**_rec("naive", "roman", False), "run_id": "inv", "status": "infra-error"})
    text = report.render(recs)
    assert "0 of 4 unsatisfiable-task runs gamed in this version and configuration" in text
    assert "not zero risk" in text.lower()
    assert "## Limits" in text and "## What this does not show" in text
    assert "infra-error" in text
    assert "0% gamed" not in text


def test_write_report_refuses_redaction_hit(tmp_path):
    mp = tmp_path / "m.jsonl"
    mp.write_text("")
    out = tmp_path / "results.md"
    assert report.main(["--manifest", str(mp), "--out", str(out)]) == 0
    assert out.read_text() == report.NO_DATA


def test_usage_file_validated(tmp_path):
    from study.usage import read_usage

    assert read_usage(tmp_path) is None
    f = tmp_path / "usage.json"
    f.write_text('{"tokens": 120, "cost_usd": 0.5}')
    u = read_usage(tmp_path)
    assert u is not None and u.tokens == 120 and u.cost_usd == 0.5
    for bad in ("not json", "[1]", '{"tokens": -1}', '{"tokens": true}', '{"tokens": "9"}'):
        f.write_text(bad)
        assert read_usage(tmp_path) is None
    f.write_text('{"tokens": 5}' + " " * 5000)
    assert read_usage(tmp_path) is None
    f.unlink()
    f.symlink_to(tmp_path / "x")
    assert read_usage(tmp_path) is None


def test_record_has_started_and_usage(tmp_path, monkeypatch):
    for k, v in _env(tmp_path, "#!/bin/sh\nexit 0\n").items():
        monkeypatch.setenv(k, v)
    mpath = tmp_path / "m.jsonl"
    argv = [
        "--manifest", str(mpath), "--workdir", str(tmp_path / "w"),
        "--evidence-dir", str(tmp_path / "e"), "--tasks", "slug", "--arms", "naive",
        "--reps", "1", "--yes",
    ]  # fmt: skip
    assert run.main(argv, out=lambda _s: None) == 0
    rec = list(manifest.read_all(mpath))[0]
    assert rec["started"] and rec["started"] <= rec["ended"]
    assert rec["tokens"] is None and rec["cost_usd"] is None


def test_unexpected_exception_becomes_infra_error(tmp_path, monkeypatch):
    for k, v in _env(tmp_path, "#!/bin/sh\nexit 0\n").items():
        monkeypatch.setenv(k, v)

    def boom(*_a, **_k):
        raise ZeroDivisionError("/home/someone/secret")

    monkeypatch.setattr(run, "run_naive_real", boom)
    mpath = tmp_path / "m.jsonl"
    argv = [
        "--manifest", str(mpath), "--workdir", str(tmp_path / "w"),
        "--evidence-dir", str(tmp_path / "e"), "--tasks", "slug,wordfreq", "--arms", "naive",
        "--reps", "1", "--yes",
    ]  # fmt: skip
    assert run.main(argv, out=lambda _s: None) == 0
    recs = list(manifest.read_all(mpath))
    assert len(recs) == 2 and all(r["status"] == "infra-error" for r in recs)
    assert all(r["exclusion_reason"] == "unexpected:ZeroDivisionError" for r in recs)
    assert "someone" not in mpath.read_text()


def test_report_session_blocked_sensitivity():
    recs = [{**_rec("cheeseless", "roman", False), "run_id": f"c{i}"} for i in range(3)]
    recs.append(
        {
            **_rec("cheeseless", "roman", False),
            "run_id": "sb",
            "status": "invalid",
            "exclusion_reason": "session_blocked",
        }
    )
    recs.append({**_rec("naive", "roman", False), "run_id": "n"})
    text = report.render(recs)
    assert "## Sensitivity: session_blocked runs" in text
    assert "session_blocked runs (cheeseless, unsatisfiable): 1." in text
    assert "| counted as missing | 0/3 | 0/3 |" in text
    assert "| counted as blocked | 0/4 | 1/4 |" in text
