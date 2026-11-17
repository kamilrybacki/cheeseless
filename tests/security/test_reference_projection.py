"""ADR 0009 on the real path: the reference projection holds no test, in the tree or in .git."""

import os
import subprocess
from pathlib import Path

from cheeseless import bootstrap
from cheeseless.adapters.outbound.git.repository import GitRepository
from cheeseless.adapters.outbound.sandbox import profiles
from cheeseless.domain.session_role import SessionRole, acceptance_mount
from tests.requirements import needs
from tests.toy import make_repo

pytestmark = needs(
    os.access("/usr/bin/bwrap", os.X_OK) and os.access("/usr/bin/git", os.X_OK),
    "bwrap and git are required",
)

HIDDEN = "CANARY-HIDDEN-b7e1"
CONTRACT = "CANARY-CONTRACT-3c90"
SPEC = "SPEC-TEXT-visible"
FILES = {
    "src/app.py": "print('app')\n",
    "acceptance/contract/spec.md": SPEC,
    "acceptance/contract/criteria.yaml": "CRITERIA",
    "acceptance/contract/test_smoke.py": CONTRACT,
    "acceptance/hidden/test_hidden.py": HIDDEN,
}


def _projection(tmp_path: Path) -> Path:
    root = make_repo(tmp_path / "r", FILES)
    repo = GitRepository.discover(root)
    tree = bootstrap._projection_tree(repo, None, SessionRole.REFERENCE)
    proj = tmp_path / "proj"
    repo.create_projection(tree, proj)
    return proj


def _inside(proj: Path, tmp_path: Path, script: str) -> str:
    home = tmp_path / "home"
    home.mkdir()
    pinned = profiles.open_contract_files(proj)
    try:
        argv = profiles.agent(
            bwrap="/usr/bin/bwrap",
            projection=proj,
            sockets=[],
            home=home,
            read_only=[],
            env=[],
            command=["/bin/sh", "-c", script],
            acceptance_fd=None,
            acceptance_mode=acceptance_mount(SessionRole.REFERENCE),
            pinned_read_only=pinned,
        )
        out = subprocess.run(  # noqa: S603
            argv,
            capture_output=True,
            pass_fds=tuple(fd for fd, _ in pinned),
            timeout=60,
        )
    finally:
        for fd, _ in pinned:
            os.close(fd)
    assert out.returncode == 0, out.stderr
    return out.stdout.decode(errors="replace")


def test_no_canary_is_reachable_from_a_reference_session(tmp_path):
    proj = _projection(tmp_path)
    script = (
        f"cd {proj}; git ls-files; git show HEAD:acceptance/hidden/test_hidden.py 2>/dev/null; "
        "git cat-file --batch-all-objects --batch; grep -r . . 2>/dev/null; "
        f"cat {proj}/acceptance/contract/spec.md"
    )
    out = _inside(proj, tmp_path, script)
    assert HIDDEN not in out and CONTRACT not in out
    assert "test_hidden.py" not in out  # not even listed by git ls-files
    assert SPEC in out  # the allowlisted spec stays readable
