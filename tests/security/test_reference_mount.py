"""ADR 0009: the reference sees the spec and criteria only, never any test (real bwrap)."""

import os
import subprocess
from pathlib import Path

import pytest

from cheeseless.adapters.outbound.sandbox import profiles
from cheeseless.domain.session_role import SessionRole, acceptance_mount
from tests.requirements import needs

pytestmark = needs(
    os.access("/usr/bin/bwrap", os.X_OK) and os.access("/usr/bin/python3", os.X_OK),
    "bwrap and python3 are required",
)

CANARY = "CANARY-HIDDEN-5d2e"
PUBLIC_TEST = "PUBLIC-TEST-7a11"

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
for rel in sys.argv[2:]:
    print(read(rel))
print(write("acceptance/contract/spec.md"))
print(write("acceptance/contract/criteria.yaml"))
"""

READ = (
    "acceptance/contract/spec.md",
    "acceptance/contract/criteria.yaml",
    "acceptance/contract/test_smoke.py",
    "acceptance/hidden/test_hidden.py",
    "acceptance/contract/link_to_canary.py",
    "acceptance/contract/unknown.txt",
)


def _layout(proj: Path) -> None:
    contract = proj / "acceptance" / "contract"
    (proj / "acceptance" / "hidden").mkdir(parents=True)
    contract.mkdir(parents=True)
    (contract / "spec.md").write_text("SPEC-TEXT")
    (contract / "criteria.yaml").write_text("CRITERIA-WITH-EXAMPLES")
    (contract / "test_smoke.py").write_text(PUBLIC_TEST)
    (contract / "unknown.txt").write_text("UNKNOWN-FILE")
    (proj / "acceptance" / "hidden" / "test_hidden.py").write_text(CANARY)
    (contract / "link_to_canary.py").symlink_to("../hidden/test_hidden.py")


def _argv(proj: Path, home: Path, fds: list[tuple[int, str]], command: list[str]) -> list[str]:
    return profiles.agent(
        bwrap="/usr/bin/bwrap",
        projection=proj,
        sockets=[],
        home=home,
        read_only=[],
        env=[],
        command=command,
        acceptance_fd=None,
        acceptance_mode=acceptance_mount(SessionRole.REFERENCE),
        pinned_read_only=fds,
    )


def _run(proj: Path, tmp_path: Path) -> list[str]:
    home = tmp_path / "home"
    home.mkdir()
    pinned = profiles.open_contract_files(proj)
    try:
        argv = _argv(
            proj,
            home,
            pinned,
            ["/usr/bin/python3", "-I", "-c", _PROBE, str(proj), *READ],
        )
        out = subprocess.run(  # noqa: S603
            argv,
            capture_output=True,
            text=True,
            pass_fds=tuple(fd for fd, _ in pinned),
            timeout=60,
        )
    finally:
        for fd, _ in pinned:
            os.close(fd)
    assert out.returncode == 0, out.stderr
    return out.stdout.splitlines()


def test_a_reference_session_reads_only_the_spec_and_criteria(tmp_path):
    proj = tmp_path / "proj"
    _layout(proj)
    lines = _run(proj, tmp_path)
    assert lines[0] == "SPEC-TEXT"
    assert lines[1] == "CRITERIA-WITH-EXAMPLES"
    for i, name in enumerate(READ[2:], start=2):
        assert lines[i].startswith("ERR:"), name  # public test, canary, symlink, unknown file
    joined = "\n".join(lines)
    assert CANARY not in joined and PUBLIC_TEST not in joined and "UNKNOWN-FILE" not in joined
    assert lines[len(READ)] == "RO" and lines[len(READ) + 1] == "RO"


def test_a_symlinked_spec_is_not_exposed(tmp_path):
    proj = tmp_path / "proj"
    _layout(proj)
    spec = proj / "acceptance" / "contract" / "spec.md"
    spec.unlink()
    spec.symlink_to("../hidden/test_hidden.py")
    assert [dest for _, dest in _close(profiles.open_contract_files(proj))] == [
        str(proj / "acceptance" / "contract" / "criteria.yaml")
    ]
    assert CANARY not in "\n".join(_run(proj, tmp_path))


@pytest.mark.parametrize("kind", ["fifo", "directory"])
def test_a_non_regular_spec_is_not_exposed(tmp_path, kind):
    proj = tmp_path / "proj"
    _layout(proj)
    spec = proj / "acceptance" / "contract" / "spec.md"
    spec.unlink()
    if kind == "fifo":
        os.mkfifo(spec)
    else:
        spec.mkdir()
        (spec / "inner.md").write_text(CANARY)
    assert [dest for _, dest in _close(profiles.open_contract_files(proj))] == [
        str(proj / "acceptance" / "contract" / "criteria.yaml")
    ]
    if kind == "directory":
        assert CANARY not in "\n".join(_run(proj, tmp_path))


def _close(pinned: list[tuple[int, str]]) -> list[tuple[int, str]]:
    for fd, _ in pinned:
        os.close(fd)
    return pinned


def test_missing_allowlisted_files_are_tolerated(tmp_path):
    proj = tmp_path / "proj"
    (proj / "acceptance").mkdir(parents=True)
    assert profiles.open_contract_files(proj) == []
    argv = _argv(proj, tmp_path / "h", [], ["true"])
    assert "--ro-bind-fd" not in argv and "--tmpfs" in argv
