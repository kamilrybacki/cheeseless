"""The judge workflow template maps verdicts to check conclusions fail-closed."""

import shutil
import subprocess
from pathlib import Path

import pytest

from tests.requirements import needs

WORKFLOW = Path(__file__).resolve().parents[2] / "docs" / "ci" / "judge.yml"
JQ = shutil.which("jq") is not None


def _conclusion_snippet() -> str:
    lines = WORKFLOW.read_text().splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.strip().startswith("status=$(jq"))
    end = next(i for i in range(start, len(lines)) if lines[i].strip() == "fi")
    body = "\n".join(ln.strip() for ln in lines[start : end + 1])
    return body.replace("${{ steps.judge.outputs.code }}", "$CODE") + '\necho "$conclusion"\n'


def _conclude(tmp_path: Path, code: str, verdict: str | None) -> str:
    if verdict is not None:
        (tmp_path / "verdict.json").write_text(verdict)
    out = subprocess.run(  # noqa: S603 - fixed argv, test-owned script
        ["bash", "-c", _conclusion_snippet()],  # noqa: S607
        cwd=tmp_path,
        env={"CODE": code, "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


def test_template_has_no_neutral_conclusion() -> None:
    assert "neutral" not in WORKFLOW.read_text()


@needs(JQ, "needs jq")
@pytest.mark.parametrize(
    ("code", "verdict", "expected"),
    [
        ("0", '{"status": "pass", "criteria": []}', "success"),
        ("0", '{"status": "fail", "criteria": []}', "failure"),
        ("0", "not json", "failure"),
        ("0", None, "failure"),
        ("1", '{"status": "pass", "criteria": []}', "failure"),
        ("1", '{"status": "blocked", "criteria": []}', "failure"),
        ("2", None, "failure"),
        ("3", None, "failure"),
        ("", None, "failure"),
    ],
)
def test_only_exit_zero_with_pass_status_succeeds(
    tmp_path: Path, code: str, verdict: str | None, expected: str
) -> None:
    assert _conclude(tmp_path, code, verdict) == expected
