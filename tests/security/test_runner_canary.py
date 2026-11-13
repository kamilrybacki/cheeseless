"""The runner keeps the candidate tree out of pytest and the hidden suite out of reach.

The acceptance root and the candidate workdir are separate trees, as the app builds
them. The workdir carries files that would hijack pytest or Python if pytest ever looked
at it (``conftest.py``, ``pytest.ini``, ``sitecustomize.py``); the candidate itself
hunts the whole visible filesystem for the hidden suite's canary. Neither may succeed.
"""

import os
import sys
from pathlib import Path

import pytest

from cheeseless.adapters.outbound.runner.pytest_runner import PytestRunner
from cheeseless.adapters.outbound.sandbox.bwrap import DEFAULT_BWRAP, BwrapSandbox
from cheeseless.domain.evaluation import TestOutcome
from tests.requirements import needs
from tests.toy import CANARY, HIDDEN_NORMALIZE, write_files

pytestmark = needs(
    os.access(DEFAULT_BWRAP, os.X_OK) and os.access("/usr/bin/python3", os.X_OK),
    "needs bubblewrap and /usr/bin/python3",
)

# The canary is split so the candidate's own source does not contain it.
HUNTER = """\
import os, sys

# Built at run time: a constant expression would be folded into the .pyc and found.
NEEDLE = "-".join(["CHEESELESS", "CANARY", "{tail}"]).encode()
# Pseudo filesystems and the read-only host system (identical for every launch) are
# pruned by their real path, so the walk also stops under /proc/1/root.
SKIP = {{"/proc", "/sys", "/dev", "/usr", "/lib", "/lib32", "/lib64", "/bin", "/sbin"}}


def hunt():
    hits = []
    for top in ("/", "/proc/1/root", "/proc/self/cwd/.."):
        for dirpath, dirs, files in os.walk(top):
            if os.path.realpath(dirpath) in SKIP:
                dirs[:] = []
                continue
            for name in files:
                path = os.path.join(dirpath, name)
                try:
                    with open(path, "rb") as fh:
                        if NEEDLE in fh.read(1 << 20):
                            hits.append(path)
                except OSError:
                    pass
    for key, value in os.environ.items():
        if NEEDLE.decode() in value:
            hits.append("env:" + key)
    return hits


def main(argv):
    if argv[:1] == ["--version"]:
        print("toy 0.1.0")
        return 0
    for hit in hunt():
        print("FOUND " + hit)
    text = sys.stdin.read()
    sys.stdout.write("".join(" ".join(l.split()).lower() + "\\n" for l in text.splitlines()))
    return 0


raise SystemExit(main(sys.argv[1:]))
"""


def _hostile(work: Path, marker: Path) -> None:
    touch = f"open({str(marker)!r}, 'a').write(__file__ + '\\n')\n"
    write_files(
        work,
        {
            "src/toy/__init__.py": "",
            "src/toy/__main__.py": HUNTER.format(tail=CANARY.rsplit("-", 1)[1]),
            "conftest.py": touch,
            "acceptance/conftest.py": touch,
            "sitecustomize.py": touch,
            "src/sitecustomize.py": touch,
            "pytest.ini": "[pytest]\naddopts = -p conftest --collect-only\n",
            "pyproject.toml": "[tool.pytest.ini_options]\naddopts = '--collect-only'\n",
        },
    )


def test_hostile_workdir_and_canary_hunt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHEESELESS_HOST_SECRET", CANARY)
    monkeypatch.chdir(tmp_path)
    marker = tmp_path / "pwned.log"
    work = tmp_path / "work"
    _hostile(work, marker)
    root = tmp_path / "acceptance-root"
    write_files(root, {"hidden/test_normalize.py": HIDDEN_NORMALIZE})
    assert CANARY in (root / "hidden" / "test_normalize.py").read_text()

    sandbox = BwrapSandbox()
    with sandbox.scratch() as scratch:
        launcher = sandbox.launcher(work, (("PYTHONPATH", "src"),), scratch)
        out = PytestRunner().run(
            root,
            ["hidden"],
            selected=None,
            launcher=launcher,
            run=["python3", "-m", "toy"],
            timeout=300.0,
        )
        assert list(scratch.iterdir()) == []

    assert not marker.exists(), marker.read_text()
    assert "FOUND" not in out.output
    assert out.suite.infra_cause == ""
    assert set(out.suite.outcome_map.values()) == {TestOutcome.PASSED}, out.output
    assert len(out.suite.outcome_map) == 3
    assert sorted(p.name for p in work.iterdir()) == [
        "acceptance",
        "conftest.py",
        "pyproject.toml",
        "pytest.ini",
        "sitecustomize.py",
        "src",
    ]


def test_runner_never_imports_from_its_cwd(tmp_path: Path) -> None:
    """pytest runs with ``-I``: a planted module next to the root is never imported."""
    marker = tmp_path / "planted.log"
    root = tmp_path / "root"
    write_files(
        root,
        {
            "hidden/test_ok.py": "def test_ok():\n    import json\n    assert json.dumps(1)\n",
            "json.py": f"open({str(marker)!r}, 'w').write('x')\n",
            "pytest.py": f"open({str(marker)!r}, 'w').write('x')\n",
        },
    )
    out = PytestRunner().run(
        root, ["hidden"], selected=None, launcher=(), run=[sys.executable], timeout=60.0
    )
    assert out.suite.outcome_map == {"hidden/test_ok.py::test_ok": TestOutcome.PASSED}
    assert not marker.exists()


def test_the_hunter_finds_a_visible_canary(tmp_path: Path) -> None:
    """Positive control: the same hunter, given a leaked canary, reports it."""
    work = tmp_path / "work"
    _hostile(work, tmp_path / "pwned.log")
    write_files(work, {"leak/notes.txt": f"oops {CANARY}\n"})
    root = tmp_path / "acceptance-root"
    write_files(root, {"hidden/test_normalize.py": HIDDEN_NORMALIZE})
    sandbox = BwrapSandbox()
    with sandbox.scratch() as scratch:
        out = PytestRunner().run(
            root,
            ["hidden"],
            selected=None,
            launcher=sandbox.launcher(work, (("PYTHONPATH", "src"),), scratch),
            run=["python3", "-m", "toy"],
            timeout=300.0,
        )
    assert "FOUND /w/" in out.output
    assert "leak/notes.txt" in out.output
