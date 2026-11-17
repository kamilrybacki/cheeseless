"""Author-side and normal-side candidate calls agree on the probed observations (ADR 0010).

The same probe runs through the normal launcher and through the author runner's host broker.
Both reach the candidate through one launcher code path, so every observation must agree:
nothing a candidate leaves behind (files, abstract sockets, permission or content changes) is
visible to the next call, and the suite's own /tmp output is untouched.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from cheeseless.adapters.outbound.sandbox.bwrap import DEFAULT_BWRAP, BwrapSandbox
from cheeseless.app.author_driver import DRIVER, parse_outcome, suite_from

needs_bwrap = pytest.mark.skipif(
    not shutil.which(DEFAULT_BWRAP) and not Path(DEFAULT_BWRAP).exists(), reason="no bwrap"
)

SH = "/bin/sh"
PY = "/usr/bin/python3"
CANARY = "CHEESELESS-CANARY-4f9c1d"


def _marker(path: str) -> str:
    return f"if [ -e {path} ]; then printf warm; else (: > {path}) 2>/dev/null; printf cold; fi"


# Places a candidate may keep state between calls.
MARKERS = {
    "cwd": "marker",
    "tmp": "/tmp/cheeseless-marker",  # noqa: S108
    "home": "$HOME/marker",
    "dev": "/dev/cheeseless-marker",
    "w": "/w/cheeseless-marker",
    "run": "/run/cheeseless-marker",
    "var-tmp": "/var/tmp/cheeseless-marker",  # noqa: S108
    "root": "/cheeseless-marker",
}
NEW_DIR = (
    "if mkdir /cheeseless-dir 2>/dev/null; then (: > /cheeseless-dir/m) 2>/dev/null; printf cold; "
    "else printf warm; fi"
)

ABSTRACT = (
    "import os, socket, time\n"
    "s = socket.socket(socket.AF_UNIX)\n"
    "try:\n"
    "    s.connect('\\0cheeseless-abstract')\n"
    "    print('warm', end='')\n"
    "except OSError:\n"
    "    print('cold', end='')\n"
    "    if os.fork() == 0:\n"
    "        os.setsid()\n"
    "        listener = socket.socket(socket.AF_UNIX)\n"
    "        listener.bind('\\0cheeseless-abstract')\n"
    "        listener.listen(1)\n"
    "        time.sleep(30)\n"
)

# Same size, same mtime: a snapshot of metadata alone would miss this.
MUTATE = "stat -c %a d; cat f; chmod 777 d; printf B > f; touch -d '2001-01-01 00:00:00' f"


def _normal(tree: Path, prefix: list[str], args: list[str], calls: int = 2) -> list[str]:
    sandbox = BwrapSandbox()
    with sandbox.scratch() as scratch:
        launcher = sandbox.launcher(tree, (), scratch)
        runs = [
            subprocess.run(  # noqa: S603
                [*launcher, *prefix, *args], capture_output=True, text=True, timeout=60
            )
            for _ in range(calls)
        ]
    return [r.stdout for r in runs]


def _tree(tmp_path: Path) -> Path:
    tree = tmp_path / "ref"
    (tree / "d").mkdir(parents=True)
    (tree / "d" / "keep").write_text("x")
    (tree / "d").chmod(0o755)
    (tree / "f").write_text("A")
    return tree


def _author(tmp_path: Path, tree: Path | None, test: str, candidate=(SH,), files=None) -> str:
    acc = tmp_path / "acc"
    (acc / "hidden").mkdir(parents=True)
    (acc / "hidden" / "test_probe.py").write_text(test)
    for name, text in (files or {}).items():
        (acc / "hidden" / name).write_text(text)
    command = (sys.executable, "-I", "-B", "-c", DRIVER, "run")
    out = BwrapSandbox().author_run(acc, tree, command, env=(), timeout=120.0, candidate=candidate)
    data = parse_outcome(out)
    assert data is not None, out.stderr_tail + out.stdout
    suite = suite_from(data)
    assert suite is not None
    (outcome,) = suite.outcomes.values()
    return outcome.value


def _probe_test(expected: list[str], args: list[str]) -> str:
    return (
        "import pytest\n"
        "@pytest.mark.criterion('C1')\n"
        "def test_probe(candidate):\n"
        f"    got = [candidate.run({args!r}).stdout for _ in range(2)]\n"
        f"    assert got == {expected!r}, got\n"
    )


def _differential(tmp_path: Path, args: list[str], expected: list[str] | None = None) -> None:
    tree = _tree(tmp_path)
    normal = _normal(tree, [SH], args)
    if expected is not None:
        assert normal == expected
    assert _author(tmp_path, tree, _probe_test(normal, args)) == "passed"


@needs_bwrap
@pytest.mark.parametrize("place", sorted(MARKERS))
def test_markers_do_not_persist_between_calls_on_either_path(tmp_path, place):
    _differential(tmp_path, ["-c", _marker(MARKERS[place])], ["cold", "cold"])


@needs_bwrap
def test_a_new_top_level_directory_does_not_persist(tmp_path):
    _differential(tmp_path, ["-c", NEW_DIR], ["cold", "cold"])


@needs_bwrap
def test_a_background_abstract_socket_does_not_survive_the_call(tmp_path):
    tree = _tree(tmp_path)
    args = ["-c", ABSTRACT]
    assert _normal(tree, [PY], args) == ["cold", "cold"]
    test = _probe_test(["cold", "cold"], args)
    assert _author(tmp_path, tree, test, candidate=(PY,)) == "passed"


@needs_bwrap
def test_permission_and_same_mtime_content_changes_do_not_leak(tmp_path):
    tree = _tree(tmp_path)
    args = ["-c", MUTATE]
    normal = _normal(tree, [SH], args)
    assert normal[0] == normal[1] != ""
    assert _author(tmp_path, tree, _probe_test(normal, args)) == "passed"


@needs_bwrap
def test_suite_tmp_output_survives_and_the_candidate_never_sees_it(tmp_path):
    tree = _tree(tmp_path)
    probe = "test -e /tmp/suite-output && printf seen || printf absent"
    test = (
        "import pytest, pathlib\n"
        "@pytest.mark.criterion('C1')\n"
        "def test_suite_tmp(candidate):\n"
        "    out = pathlib.Path('/tmp/suite-output')\n"  # noqa: S108
        "    out.write_text('kept')\n"
        f"    assert candidate.run(['-c', {probe!r}]).stdout == 'absent'\n"
        "    assert out.read_text() == 'kept'\n"
    )
    assert _author(tmp_path, tree, test) == "passed"


@needs_bwrap
def test_the_candidate_cannot_see_acceptance_content_canary(tmp_path):
    tree = _tree(tmp_path)
    roots = "/w /tmp /home /mnt /opt /srv /etc /var /run /usr/local"
    probe = (
        f"grep -rsl {CANARY} {roots} 2>/dev/null; "
        f"find {roots} -name 'canary-*' 2>/dev/null; printf done"
    )
    assert _normal(tree, [SH], ["-c", probe], calls=1) == ["done"]
    test = _probe_test(["done", "done"], ["-c", probe])
    files = {"canary-file.txt": CANARY}
    assert _author(tmp_path, tree, test, files=files) == "passed"


@needs_bwrap
def test_the_per_call_deadline_covers_bwrap_startup(tmp_path):
    tree = _tree(tmp_path)
    test = (
        "import pytest\n"
        "@pytest.mark.criterion('C1')\n"
        "def test_deadline(candidate):\n"
        "    with pytest.raises(AssertionError, match='timed out'):\n"
        "        candidate.run(['-c', 'printf ok'], timeout=0.001)\n"
        "    assert candidate.run(['-c', 'printf ok'], timeout=60).stdout == 'ok'\n"
    )
    assert _author(tmp_path, tree, test) == "passed"


STDIN_PROBE = (
    "if [ -c /proc/self/fd/0 ]; then printf 'null|'; else printf 'data|'; fi; "
    "od -An -tx1 | tr -d ' \\n'"
)


@needs_bwrap
@pytest.mark.parametrize("data", [b"\xff", b"\x00\xfe\x80", b"", None])
def test_stdin_bytes_reach_the_candidate_unchanged_on_either_path(tmp_path, data):
    tree = _tree(tmp_path)
    sandbox = BwrapSandbox()
    with sandbox.scratch() as scratch:
        launcher = sandbox.launcher(tree, (), scratch)
        run = subprocess.run(  # noqa: S603
            [*launcher, SH, "-c", STDIN_PROBE],
            input=data,
            stdin=subprocess.DEVNULL if data is None else None,
            capture_output=True,
            timeout=60,
        )
    expected = run.stdout.decode()
    test = (
        "import pytest\n"
        "@pytest.mark.criterion('C1')\n"
        "def test_stdin(candidate):\n"
        f"    got = candidate.run(['-c', {STDIN_PROBE!r}], stdin={data!r}).stdout\n"
        f"    assert got == {expected!r}, got\n"
    )
    assert _author(tmp_path, tree, test) == "passed"
