"""ADR 0009: the reference sees the spec and examples, never the hidden tests (real bwrap)."""

import os
import subprocess
import sys
from pathlib import Path

from cheeseless.adapters.outbound.sandbox import profiles
from cheeseless.domain.session_role import SessionRole, acceptance_mount
from tests.requirements import needs

pytestmark = needs(
    os.access("/usr/bin/bwrap", os.X_OK) and os.access("/usr/bin/python3", os.X_OK),
    "bwrap and python3 are required",
)

CANARY = "CANARY-HIDDEN-5d2e"

_PROBE = """
import os, sys
root = sys.argv[1]
def read(rel):
    try:
        return open(os.path.join(root, rel)).read()
    except OSError as e:
        return "ERR:" + type(e).__name__
def write(rel):
    try:
        open(os.path.join(root, rel), "w").write("x")
        return "WROTE"
    except OSError:
        return "RO"
print(read("acceptance/contract/spec.md"))
print(read("acceptance/contract/examples/ex1.md"))
print(read("acceptance/tests/test_hidden.py"))
print(write("acceptance/contract/spec.md"))
print(os.listdir(os.path.join(root, "acceptance")))
"""


def _run(tmp_path: Path) -> list[str]:
    proj = tmp_path / "proj"
    (proj / "acceptance" / "contract" / "examples").mkdir(parents=True)
    (proj / "acceptance" / "tests").mkdir()
    (proj / "acceptance" / "contract" / "spec.md").write_text("SPEC-TEXT")
    (proj / "acceptance" / "contract" / "examples" / "ex1.md").write_text("EXAMPLE-TEXT")
    (proj / "acceptance" / "tests" / "test_hidden.py").write_text(CANARY)
    home = tmp_path / "home"
    home.mkdir()
    mode = acceptance_mount(SessionRole.REFERENCE)
    fd = profiles.open_contract(proj)
    assert fd is not None
    try:
        argv = profiles.agent(
            bwrap="/usr/bin/bwrap",
            projection=proj,
            sockets=[],
            home=home,
            read_only=[],
            env=[],
            command=["/usr/bin/python3", "-I", "-c", _PROBE, str(proj)],
            acceptance_fd=fd,
            acceptance_mode=mode,
        )
        out = subprocess.run(argv, capture_output=True, text=True, pass_fds=(fd,), timeout=60)
    finally:
        os.close(fd)
    assert out.returncode == 0, out.stderr
    return out.stdout.splitlines()


def test_a_reference_session_reads_the_spec_and_examples_but_not_the_tests(tmp_path):
    lines = _run(tmp_path)
    assert lines[0] == "SPEC-TEXT"
    assert lines[1] == "EXAMPLE-TEXT"
    assert lines[2].startswith("ERR:")  # the hidden test is masked
    assert lines[3] == "RO"  # the contract is read-only
    assert CANARY not in "\n".join(lines)
    assert lines[4] == "['contract']"


def test_a_missing_contract_directory_masks_everything(tmp_path):
    proj = tmp_path / "proj"
    (proj / "acceptance").mkdir(parents=True)
    assert profiles.open_contract(proj) is None
    argv = profiles.agent(
        bwrap="bwrap",
        projection=proj,
        sockets=[],
        home=tmp_path / "h",
        read_only=[],
        env=[],
        command=["true"],
        acceptance_fd=None,
        acceptance_mode="contract",
    )
    assert "--ro-bind-fd" not in argv and "--tmpfs" in argv
