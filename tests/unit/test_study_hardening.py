import os

import pytest

from study import manifest, real, report, run
from study.agent_config import AgentId
from study.prompt import template_hash
from study.tasks import TASKS_BY_ID
from tests.unit.test_study_run_report import _rec


def test_missing_bwrap_refuses(monkeypatch, tmp_path):
    monkeypatch.setattr(real.shutil, "which", lambda _: None)
    with pytest.raises(real.IsolationError):
        real.sandbox_argv(tmp_path, tmp_path / "home", ["true"])


def test_real_record_unknown_and_usage_unavailable(tmp_path):
    (tmp_path / "usage.json").write_text('{"tokens": 999, "cost_usd": 123}')
    rec = run._record(
        run.Job(TASKS_BY_ID["slug"], "naive", 0),
        real.infra_result("naive", "probe"),
        mode="real",
        seed=1,
        agent=AgentId("v", "m", "1"),
        attempt=1,
        canary=False,
    )
    assert rec["tokens"] is None and rec["usage_provenance"] == "unavailable"
    assert rec["session_policy"] == "fresh_per_run"
    assert rec["observability"] == "diff_only"
    for flag in ("read_hidden", "history_read", "net_exfil"):
        assert rec["flags"][flag] == "unknown"
    from study.usage import read_usage

    assert read_usage(tmp_path) is None


def test_pilot_rejects_explicit_held_out(tmp_path):
    for task in ("rpn", "dedupe"):
        assert run.main(["--dry-run", "--phase", "pilot", "--tasks", task]) == 2
    assert template_hash() == "406fecfcdaa8b805"


def test_report_phase_and_unknown():
    a = {**_rec("naive", "roman", False), "phase": "pilot"}
    text = report.render([a])
    for flag in ("read_hidden", "history_read", "net_exfil"):
        assert f"| naive | {flag} | unknown |" in text
    assert "not measured" in text
    assert "run-level descriptive intervals that ignore within-task correlation" in text
    assert text.index("Primary comparison") < text.index("## Rates")
    b = {**_rec("cheeseless", "roman", False), "phase": "main"}
    with pytest.raises(ValueError, match="phase"):
        report.render([a, b])


def test_manifest_tail_requires_explicit_repair(tmp_path):
    path = tmp_path / "m.jsonl"
    manifest.append(path, {"run_id": "a", "phase": "pilot"})
    with path.open("ab") as fh:
        fh.write(b'{"run_id":')
    with pytest.raises(manifest.CorruptManifest, match="trailing"):
        list(manifest.read_all(path))
    with pytest.raises(manifest.CorruptManifest):
        manifest.validate_resume(path, "pilot")
    manifest.validate_resume(path, "pilot", repair_tail=True)
    assert len(list(manifest.read_all(path))) == 1
    with pytest.raises(ValueError, match="phase"):
        manifest.validate_resume(path, "main")


def test_append_single_write_and_fsync(tmp_path, monkeypatch):
    writes, syncs = [], []
    write, sync = os.write, os.fsync
    monkeypatch.setattr(
        manifest.os, "write", lambda fd, data: (writes.append(data), write(fd, data))[1]
    )
    monkeypatch.setattr(manifest.os, "fsync", lambda fd: (syncs.append(fd), sync(fd))[1])
    manifest.append(tmp_path / "m", {"run_id": "a"})
    assert len(writes) == len(syncs) == 1
    assert writes[0].endswith(b"\n")


def test_real_requires_session_and_bwrap(tmp_path, monkeypatch):
    from tests.unit.test_study_run_report import _env

    for k, v in _env(tmp_path, "#!/bin/sh\nexit 0\n").items():
        monkeypatch.setenv(k, v)
    args = [
        "--phase",
        "pilot",
        "--manifest",
        str(tmp_path / "m"),
        "--workdir",
        str(tmp_path / "w"),
        "--evidence-dir",
        str(tmp_path / "e"),
        "--yes",
    ]
    monkeypatch.delenv("CHEESELESS_STUDY_FRESH_SESSION")
    lines = []
    assert run.main(args, out=lines.append) == 2
    assert "FRESH_SESSION" in lines[-1]
    monkeypatch.setenv("CHEESELESS_STUDY_FRESH_SESSION", "1")
    monkeypatch.setattr(run, "bwrap_available", lambda: False)
    assert run.main(args, out=lines.append) == 2
    assert "bubblewrap unavailable" in lines[-1]


def test_failed_self_check_refuses_without_manifest(tmp_path, monkeypatch):
    from tests.unit.test_study_run_report import _env

    for k, v in _env(tmp_path, "#!/bin/sh\nexit 0\n").items():
        monkeypatch.setenv(k, v)

    def fail(*a, **kw):
        raise real.IsolationError("failed probe")

    monkeypatch.setattr(run, "isolation_check", fail)
    assert (
        run.main(
            [
                "--phase",
                "pilot",
                "--manifest",
                str(tmp_path / "m"),
                "--workdir",
                str(tmp_path / "w"),
                "--evidence-dir",
                str(tmp_path / "e"),
                "--yes",
            ]
        )
        == 2
    )
    assert not (tmp_path / "m").exists()


def test_evidence_must_be_outside_study_checkout(tmp_path):
    from study.agent_config import load_study_agent
    from tests.unit.test_study_run_report import _env

    agent = load_study_agent(_env(tmp_path, "#!/bin/sh\nexit 0\n"))
    args = run._parser().parse_args(
        [
            "--phase",
            "pilot",
            "--manifest",
            str(tmp_path / "m"),
            "--workdir",
            str(tmp_path / "w"),
            "--evidence-dir",
            str(run.Path(__file__).resolve().parents[2] / "evidence"),
        ]
    )
    with pytest.raises(ValueError, match="outside"):
        run._validate_paths(args, agent)


def test_usage_directory_cannot_overlap_work(tmp_path):
    from study.agent_config import load_study_agent
    from tests.unit.test_study_run_report import _env

    agent = load_study_agent(_env(tmp_path, "#!/bin/sh\nexit 0\n"))
    args = run._parser().parse_args(
        [
            "--phase",
            "pilot",
            "--manifest",
            str(tmp_path / "m"),
            "--workdir",
            str(tmp_path / "w"),
            "--evidence-dir",
            str(tmp_path / "e"),
            "--usage-file-dir",
            str(tmp_path / "w" / "u"),
        ]
    )
    with pytest.raises(ValueError, match="overlap"):
        run._validate_paths(args, agent)


def test_probe_execution_error_is_fail_closed(tmp_path, monkeypatch):
    def unavailable(*a, **kw):
        raise OSError("probe failed")

    monkeypatch.setattr(real.subprocess, "run", unavailable)
    with pytest.raises(real.IsolationError):
        real.isolation_check(tmp_path, tmp_path, ())


def test_resume_requires_real_session_policy(tmp_path):
    path = tmp_path / "m"
    manifest.append(path, {"phase": "pilot", "mode": "real", "run_id": "a"})
    with pytest.raises(ValueError, match="session_policy"):
        manifest.validate_resume(path, "pilot")


def test_bootstrap_counts_distinct_ordered_resamples():
    from study.stats import Cell, Rate, cluster_bootstrap

    result = cluster_bootstrap([Cell(f"{i}/a", Rate(1, 2), Rate(0, 2)) for i in range(3)], seed=1)
    assert result[2:] == (3, 27)


def test_infra_record_marks_writes_unobserved(tmp_path):
    rec = run._record(
        run.Job(TASKS_BY_ID["slug"], "naive", 0),
        real.infra_result("naive", "probe"),
        mode="real",
        seed=1,
        agent=AgentId("v", "m", "1"),
        attempt=1,
        canary=False,
    )
    assert rec["writes_observed"] is False
