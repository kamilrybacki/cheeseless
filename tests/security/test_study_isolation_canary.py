import os
import subprocess

import pytest

from study import real
from study.tasks import CANARY


@pytest.mark.skipif(not os.access("/usr/bin/bwrap", os.X_OK), reason="bubblewrap required")
def test_study_probe_hides_oracle_scorer_manifest_evidence_and_canary(tmp_path):
    work, home = tmp_path / "work", tmp_path / "home"
    work.mkdir()
    home.mkdir()
    protected = []
    for name in ("oracle", "scorer", "manifest", "evidence"):
        path = tmp_path / name
        path.write_text(CANARY)
        protected.append(path)
    real.isolation_check(work, home, tuple(protected))
    command = [
        "/bin/sh",
        "-c",
        'cat "$1" 2>/dev/null; touch /work/ok; echo probe > /outside',
        "probe",
        str(protected[0]),
    ]
    result = subprocess.run(real.sandbox_argv(work, home, command), capture_output=True)  # noqa: S603 - fixed sandbox probe
    assert result.returncode != 0
    assert CANARY.encode() not in result.stdout + result.stderr
    assert (work / "ok").exists()
    assert all(p.read_text() == CANARY for p in protected)


@pytest.mark.skipif(not os.access("/usr/bin/bwrap", os.X_OK), reason="bubblewrap required")
def test_candidate_cannot_read_host_canary_during_oracle_grading(tmp_path):
    from study import suite
    from study.tasks import TASKS_BY_ID

    secret = tmp_path / "oracle-secret"
    secret.write_text(CANARY)
    root = tmp_path / "candidate"
    pkg = root / "src" / "slugtool"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "__main__.py").write_text(
        f"from pathlib import Path\nprint(Path({str(secret)!r}).read_text())"
    )
    verdict = suite.run_suite(
        TASKS_BY_ID["slug"],
        root,
        {
            "test_probe.py": (
                "def test_probe(candidate):\n"
                '    assert "CHEESELESS-CANARY" not in '
                'candidate.run(["slug"]).stdout\n'
            ),
        },
    )
    assert verdict == suite.PASS


@pytest.mark.skipif(not os.access("/usr/bin/bwrap", os.X_OK), reason="bubblewrap required")
def test_editable_tests_cannot_write_parent_manifest(tmp_path):
    from study import suite
    from study.tasks import TASKS_BY_ID

    manifest = tmp_path / "manifest"
    manifest.write_text(CANARY)
    work = tmp_path / "work"
    acceptance = work / "acceptance"
    acceptance.mkdir(parents=True)
    (acceptance / "test_probe.py").write_text(
        "from pathlib import Path\ndef test_probe():\n"
        f'    Path({str(manifest)!r}).write_text("corrupt")\n'
    )
    suite.in_tree_verdict(TASKS_BY_ID["slug"], work)
    assert manifest.read_text() == CANARY


@pytest.mark.skipif(not os.access("/usr/bin/bwrap", os.X_OK), reason="bubblewrap required")
def test_isolated_in_tree_grading_positive_control(tmp_path):
    from study import suite
    from study.gitutil import make_repo
    from study.tasks import TASKS_BY_ID, baseline_files, solution_main

    task = TASKS_BY_ID["slug"]
    files = {**baseline_files(task), "acceptance/conftest.py": suite.conftest_text(task.package)}
    files[f"src/{task.package}/__main__.py"] = solution_main(task)
    make_repo(tmp_path / "candidate", files)
    assert suite.in_tree_verdict(task, tmp_path / "candidate") == suite.PASS


@pytest.mark.skipif(not os.access("/usr/bin/bwrap", os.X_OK), reason="bubblewrap required")
def test_naive_can_run_prompt_pytest_command_inside_sandbox(tmp_path):
    from study.agent_config import AgentId, StudyAgent
    from study.tasks import TASKS_BY_ID, solution_main

    solution = tmp_path / "solution.py"
    solution.write_text(solution_main(TASKS_BY_ID["slug"]))
    command = (
        "/bin/sh",
        "-c",
        "cp /agent-home/solution.py src/slugtool/__main__.py; python3 -m pytest acceptance",
    )
    agent = StudyAgent(
        AgentId("scripted", "probe", "1"), command, command, {"solution.py": solution}, (), 30
    )
    result = real.run_naive_real(TASKS_BY_ID["slug"], tmp_path / "run", agent)
    assert result.status == "valid" and result.judge_in_tree == "accept"
