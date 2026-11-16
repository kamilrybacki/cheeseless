"""The implementer sandbox mounts ``acceptance/`` read-only and fails closed (ledger A4, A5).

The projection carries the public contract under ``acceptance/``. The agent profile binds
it read-only on top of the writable projection, so a refused write leaves the tree
untouched. If the directory is missing, is a symlink, or the bind cannot be made, the
session does not start.
"""

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from cheeseless.adapters.outbound.sandbox import profiles
from cheeseless.ports.sandbox import SandboxError
from tests.requirements import needs
from tests.toy import CANARY

BWRAP = "/usr/bin/bwrap"
_PROBE = """
import json, os, sys
from pathlib import Path

root = Path.cwd()
acc = root / "acceptance"
results = {}


def attempt(name, action):
    try:
        action()
    except OSError as exc:
        results[name] = exc.errno
    else:
        results[name] = 0


def overwrite():
    (acc / "contract/spec.md").write_text("changed")


def append():
    with (acc / "contract/spec.md").open("a") as handle:
        handle.write("more")


def create():
    (acc / "contract/new.py").write_text("x = 1")


def delete():
    (acc / "contract/spec.md").unlink()


def rename_within():
    os.rename(acc / "contract/spec.md", acc / "contract/moved.md")


def rename_out():
    os.rename(acc / "contract/spec.md", root / "escaped.md")


def chmod():
    os.chmod(acc / "contract/spec.md", 0o600)


def mkdir():
    (acc / "extra").mkdir()


def rmdir_mount():
    os.rmdir(acc)


def rename_mount():
    os.rename(acc, root / "acceptance-old")


for fn in (
    overwrite, append, create, delete, rename_within, rename_out,
    chmod, mkdir, rmdir_mount, rename_mount,
):
    attempt(fn.__name__, fn)
(root / "src.py").write_text("writable")
(root / "src").mkdir()
(root / "src/mod.py").write_text("writable")
results["readable"] = (acc / "contract/spec.md").read_text()
print(json.dumps(results))
"""


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(oct(path.lstat().st_mode).encode())
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _projection(tmp_path: Path) -> tuple[Path, Path]:
    projection = tmp_path / "projection"
    contract = projection / "acceptance/contract"
    contract.mkdir(parents=True)
    (contract / "spec.md").write_text("public spec")
    (contract / "criteria.yaml").write_text("examples: []")
    (projection / "acceptance/notes.txt").write_text(CANARY)
    home = tmp_path / "home"
    home.mkdir()
    return projection, home


def _argv(projection: Path, home: Path, command: tuple[str, ...]) -> list[str]:
    return profiles.agent(
        bwrap=BWRAP,
        projection=projection,
        sockets=(),
        home=home,
        read_only=(),
        env=(),
        command=command,
    )


def test_argv_binds_acceptance_read_only_after_the_projection(tmp_path: Path) -> None:
    projection, home = _projection(tmp_path)
    argv = _argv(projection, home, ("true",))
    acceptance = str(projection / "acceptance")
    ro = [
        i for i in range(len(argv) - 2) if argv[i : i + 3] == ["--ro-bind", acceptance, acceptance]
    ]
    bind = ["--bind", str(projection), str(projection)]
    rw = [i for i in range(len(argv) - 2) if argv[i : i + 3] == bind]
    assert len(ro) == 1
    assert rw
    assert rw[0] < ro[0]
    assert ["--ro-bind-try", acceptance, acceptance] not in [
        argv[i : i + 3] for i in range(len(argv))
    ]


def test_missing_acceptance_fails_closed(tmp_path: Path) -> None:
    projection = tmp_path / "projection"
    projection.mkdir()
    with pytest.raises(SandboxError, match="acceptance"):
        _argv(projection, tmp_path / "home", ("true",))


def test_acceptance_that_is_a_file_fails_closed(tmp_path: Path) -> None:
    projection = tmp_path / "projection"
    projection.mkdir()
    (projection / "acceptance").write_text("not a directory")
    with pytest.raises(SandboxError, match="acceptance"):
        _argv(projection, tmp_path / "home", ("true",))


def test_symlinked_acceptance_fails_closed(tmp_path: Path) -> None:
    projection = tmp_path / "projection"
    projection.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (projection / "acceptance").symlink_to(elsewhere)
    with pytest.raises(SandboxError, match="acceptance"):
        _argv(projection, tmp_path / "home", ("true",))


@needs(os.access(BWRAP, os.X_OK), "needs bubblewrap")
def test_every_mutation_of_acceptance_is_refused_and_the_canary_is_untouched(
    tmp_path: Path,
) -> None:
    projection, home = _projection(tmp_path)
    before = _tree_digest(projection / "acceptance")
    argv = _argv(projection, home, ("/usr/bin/python3", "-c", _PROBE))
    done = subprocess.run(argv, capture_output=True, text=True, timeout=60, check=False)  # noqa: S603
    assert done.returncode == 0, done.stderr
    results = json.loads(done.stdout)
    assert results.pop("readable") == "public spec"
    refused = {
        "overwrite",
        "append",
        "create",
        "delete",
        "rename_within",
        "rename_out",
        "chmod",
        "mkdir",
        "rmdir_mount",
        "rename_mount",
    }
    assert set(results) == refused
    assert all(code != 0 for code in results.values()), results
    # The rest of the work tree stays writable.
    assert (projection / "src.py").read_text() == "writable"
    assert (projection / "src/mod.py").read_text() == "writable"
    assert _tree_digest(projection / "acceptance") == before
    assert not (projection / "escaped.md").exists()
    assert not (projection / "acceptance-old").exists()
    assert CANARY not in done.stdout + done.stderr
