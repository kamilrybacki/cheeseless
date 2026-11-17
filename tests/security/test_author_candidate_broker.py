"""The author-side candidate broker is a boundary the unapproved suite can reach (ADR 0010).

Exercised through the real author sandbox: the closed request schema, teardown when the
sandbox dies mid-call, and the ``--no-reference`` waiver (no broker, every call refused).
"""

import os
import shutil
import sys
import time
from pathlib import Path

import pytest

from cheeseless.adapters.outbound.sandbox.bwrap import DEFAULT_BWRAP, BwrapSandbox
from cheeseless.app.author_driver import DRIVER, parse_outcome, suite_from
from cheeseless.ports.sandbox import AUTHOR_BROKER_ENV

needs_bwrap = pytest.mark.skipif(
    not shutil.which(DEFAULT_BWRAP) and not Path(DEFAULT_BWRAP).exists(), reason="no bwrap"
)

FORBIDDEN = {
    "path": "/etc/passwd",
    "env": {"X": "1"},
    "cwd": "/",
    "profile": "author",
    "tree": "/srv/other",
    "root": "/",
    "bind": ["/", "/"],
}

RAW_CLIENT = (
    "import json, os, socket, struct\n"
    "def ask(payload):\n"
    f"    path = os.environ[{AUTHOR_BROKER_ENV!r}]\n"
    "    s = socket.socket(socket.AF_UNIX)\n"
    "    s.connect(path)\n"
    "    body = json.dumps(payload).encode()\n"
    "    s.sendall(struct.pack('>I', len(body)) + body)\n"
    "    head = b''\n"
    "    while len(head) < 4:\n"
    "        head += s.recv(4 - len(head))\n"
    "    size = struct.unpack('>I', head)[0]\n"
    "    data = b''\n"
    "    while len(data) < size:\n"
    "        data += s.recv(size - len(data))\n"
    "    return json.loads(data)\n"
)


def _author(tmp_path: Path, tree: Path | None, test: str, timeout: float = 120.0):
    acc = tmp_path / "acc"
    (acc / "hidden").mkdir(parents=True)
    (acc / "hidden" / "test_probe.py").write_text(test)
    command = (sys.executable, "-I", "-B", "-c", DRIVER, "run")
    out = BwrapSandbox().author_run(
        acc, tree, command, env=(), timeout=timeout, candidate=("/bin/sh",)
    )
    return out, parse_outcome(out)


def _outcome(out, data) -> str:
    assert data is not None, out.stderr_tail + out.stdout
    suite = suite_from(data)
    assert suite is not None
    (outcome,) = suite.outcomes.values()
    return outcome.value


@needs_bwrap
@pytest.mark.parametrize("field", sorted(FORBIDDEN))
def test_every_forbidden_request_field_is_refused(tmp_path, field):
    tree = tmp_path / "ref"
    tree.mkdir()
    test = (
        "import pytest\n"
        f"{RAW_CLIENT}"
        "@pytest.mark.criterion('C1')\n"
        "def test_refused():\n"
        f"    reply = ask({{'argv': ['-c', 'printf x'], {field!r}: {FORBIDDEN[field]!r}}})\n"
        "    assert 'unknown request field' in reply.get('error', ''), reply\n"
        "    assert 'returncode' not in reply\n"
    )
    assert _outcome(*_author(tmp_path, tree, test)) == "passed"


@needs_bwrap
def test_the_session_socket_serves_a_plain_request(tmp_path):
    tree = tmp_path / "ref"
    tree.mkdir()
    test = (
        "import os, pytest, stat\n"
        f"{RAW_CLIENT}"
        "@pytest.mark.criterion('C1')\n"
        "def test_ok():\n"
        f"    path = os.environ[{AUTHOR_BROKER_ENV!r}]\n"
        "    assert stat.S_ISSOCK(os.stat(path).st_mode)\n"
        "    assert ask({'argv': ['-c', 'printf hi']})['stdout'] == 'hi'\n"
    )
    assert _outcome(*_author(tmp_path, tree, test)) == "passed"


def _live(token: str) -> list[str]:
    found = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            cmd = (entry / "cmdline").read_bytes().decode("utf-8", "replace")
        except OSError:
            continue
        if token in cmd:
            found.append(entry.name)
    return found


@needs_bwrap
def test_killing_the_author_sandbox_mid_call_leaves_no_candidate_process(tmp_path):
    tree = tmp_path / "ref"
    tree.mkdir()
    token = f"cheeseless-sentinel-{os.getpid()}-{time.monotonic_ns()}"
    test = (
        "import pytest\n"
        "@pytest.mark.criterion('C1')\n"
        "def test_hangs(candidate):\n"
        f"    candidate.run(['-c', 'sleep 120; : {token}'])\n"
    )
    _author(tmp_path, tree, test, timeout=6.0)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and _live(token):
        time.sleep(0.2)
    assert _live(token) == []


@needs_bwrap
def test_no_reference_starts_no_broker_and_every_candidate_call_is_refused(tmp_path):
    test = (
        "import os, pytest\n"
        "@pytest.mark.criterion('C1')\n"
        "def test_refused(candidate):\n"
        f"    assert {AUTHOR_BROKER_ENV!r} not in os.environ\n"
        "    with pytest.raises(AssertionError, match='no candidate broker'):\n"
        "        candidate.run(['-c', 'printf x'])\n"
    )
    assert _outcome(*_author(tmp_path, None, test)) == "passed"
