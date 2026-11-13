"""Candidates run with no view of the hidden tests and no way out (ADR 0003, D12).

Every probe runs through the real launcher, the same argv prefix the runner uses. The
hidden tests and a host socket sit next to the workdir on the host; the candidate must
not reach either, must not keep its writes, must not see the host environment and
must get EPERM from the calls that read or steer another process.
"""

import json
import os
import socket
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from cheeseless.adapters.outbound.sandbox import launch
from cheeseless.adapters.outbound.sandbox.bwrap import DEFAULT_BWRAP, BwrapSandbox
from tests.requirements import needs
from tests.toy import CANARY

pytestmark = needs(
    os.access(DEFAULT_BWRAP, os.X_OK) and os.access("/usr/bin/python3", os.X_OK),
    "needs bubblewrap and /usr/bin/python3",
)

PROBE = r"""
import ctypes, json, os, socket, sys
hidden, sock, parent = sys.argv[1:4]
out = {}
def attempt(name, fn):
    try:
        out[name] = ["ok", repr(fn())[:200]]
    except BaseException as exc:
        out[name] = ["err", type(exc).__name__]
attempt("read_hidden", lambda: open(hidden).read())
attempt("list_parent", lambda: os.listdir(parent))
def uds():
    s = socket.socket(socket.AF_UNIX)
    s.connect(sock)
    return "connected"
attempt("uds", uds)
def tcp():
    socket.create_connection(("1.1.1.1", 53), timeout=2)
    return "connected"
attempt("tcp", tcp)
out["links"] = sorted(n.split(":")[0].strip() for n in open("/proc/net/dev").readlines()[2:])
libc = ctypes.CDLL(None, use_errno=True)
for name, nr in (("ptrace", 101), ("process_vm_readv", 310), ("pidfd_getfd", 438)):
    ctypes.set_errno(0)
    rc = libc.syscall(nr, 0, 0, 0, 0, 0, 0)
    out[name] = [rc, ctypes.get_errno()]
out["env"] = dict(os.environ)
out["pids"] = sorted(int(p) for p in os.listdir("/proc") if p.isdigit())
out["home"] = os.path.exists("/home")
found = []
for root in ("/", ):
    for dirpath, dirs, files in os.walk(root):
        if dirpath in ("/proc", "/sys", "/dev"):
            dirs[:] = []
            continue
        found += [os.path.join(dirpath, f) for f in files if f == "test_score.py"]
out["found"] = found
with open("written.txt", "w") as fh:
    fh.write("persist?")
out["wrote"] = os.path.exists("written.txt")
print(json.dumps(out))
"""


@pytest.fixture
def host(tmp_path: Path) -> Iterator[dict[str, Path]]:
    layout = {
        "work": tmp_path / "work",
        "scratch": tmp_path / "scratch",
        "hidden": tmp_path / "hidden" / "test_score.py",
        "sock": tmp_path / "host.sock",
    }
    layout["work"].mkdir()
    layout["scratch"].mkdir()
    layout["hidden"].parent.mkdir()
    layout["hidden"].write_text(f"# {CANARY}\ndef test_x(): pass\n")
    (layout["work"] / "main.py").write_text("print('candidate')\n")
    server = socket.socket(socket.AF_UNIX)
    server.bind(str(layout["sock"]))
    server.listen(1)
    try:
        yield layout
    finally:
        server.close()


def _probe(host: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> tuple[dict[str, Any], str]:
    monkeypatch.setenv("CHEESELESS_HOST_SECRET", CANARY)
    prefix = BwrapSandbox().launcher(host["work"], (("PYTHONPATH", "src"),), host["scratch"])
    args = [str(host["hidden"]), str(host["sock"]), str(host["work"].parent)]
    done = subprocess.run(  # noqa: S603
        [*prefix, "python3", "-c", PROBE, *args],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout), done.stdout + done.stderr


def test_candidate_cannot_reach_hidden_tests_or_the_host(
    host: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    out, raw = _probe(host, monkeypatch)
    assert CANARY not in raw
    assert out["read_hidden"][0] == "err"
    assert out["list_parent"][0] == "err"
    assert out["found"] == []
    assert out["uds"][0] == "err"
    assert out["tcp"][0] == "err"
    assert out["links"] == ["lo"]
    assert out["home"] is False


def test_candidate_gets_eperm_for_process_access(
    host: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    out, _ = _probe(host, monkeypatch)
    for name in ("ptrace", "process_vm_readv", "pidfd_getfd"):
        assert out[name] == [-1, 1], name  # EPERM


def test_candidate_env_is_scrubbed_and_pids_are_private(
    host: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    out, _ = _probe(host, monkeypatch)
    assert set(out["env"]) <= {"PATH", "HOME", "TMPDIR", "LANG", "PYTHONPATH", "PWD", "LC_CTYPE"}
    assert out["env"]["PYTHONPATH"] == "src"
    assert max(out["pids"]) < 10


def test_candidate_writes_do_not_persist(
    host: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    out, _ = _probe(host, monkeypatch)
    assert out["wrote"] is True
    assert sorted(p.name for p in host["work"].iterdir()) == ["main.py"]
    assert list(host["scratch"].iterdir()) == []


def test_build_steps_persist_but_see_no_network_or_hidden_tests(host: dict[str, Path]) -> None:
    script = (
        f"test ! -e {host['hidden']} && test ! -e {host['sock']} "
        "&& test \"$(tail -n +3 /proc/net/dev | grep -vc 'lo:')\" = 0 "
        "&& echo built > built.txt"
    )
    outcome = BwrapSandbox().build(host["work"], [("sh", "-c", script)], ())
    assert outcome.ok, outcome.stderr_tail
    assert (host["work"] / "built.txt").read_text() == "built\n"


def test_failing_build_step_reports_its_exit_code(host: dict[str, Path]) -> None:
    outcome = BwrapSandbox().build(host["work"], [("sh", "-c", "echo oops >&2; exit 3")], ())
    assert (outcome.ok, outcome.exit_code, outcome.stderr_tail) == (False, 3, "oops\n")


def test_launcher_exit_status_and_signals(host: dict[str, Path]) -> None:
    prefix = BwrapSandbox().launcher(host["work"], (), host["scratch"])
    exited = subprocess.run([*prefix, "sh", "-c", "exit 7"], check=False)  # noqa: S603
    assert exited.returncode == 7
    killed = subprocess.run([*prefix, "sh", "-c", "kill -KILL $$"], check=False)  # noqa: S603
    assert killed.returncode == 128 + 9
    assert list(host["scratch"].iterdir()) == []


def test_launch_main_in_process(host: dict[str, Path], capfd: pytest.CaptureFixture[str]) -> None:
    spec = json.dumps(
        {
            "bwrap": DEFAULT_BWRAP,
            "workdir": str(host["work"]),
            "scratch": str(host["scratch"]),
            "env": [],
        }
    )
    assert launch.main([spec, "--", "python3", "main.py"]) == 0
    assert capfd.readouterr().out == "candidate\n"
    assert list(host["scratch"].iterdir()) == []


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["{}", "--"],
        ["not json", "--", "x"],
        ['{"bwrap": "/b", "workdir": "rel", "scratch": "/", "env": []}', "--", "x"],
        ['{"bwrap": "/b", "workdir": "/", "scratch": "/", "env": [["PATH", "/x"]]}', "--", "x"],
        ['{"bwrap": 1, "workdir": "/", "scratch": "/", "env": []}', "--", "x"],
    ],
)
def test_launch_rejects_bad_specs(argv: list[str], capfd: pytest.CaptureFixture[str]) -> None:
    assert launch.main(argv) == launch.LAUNCH_FAILED
    assert "cheeseless sandbox:" in capfd.readouterr().err


def test_launch_module_runs_isolated() -> None:
    done = subprocess.run(  # noqa: S603
        [sys.executable, "-I", "-m", launch.__name__], capture_output=True, text=True, check=False
    )
    assert done.returncode == launch.LAUNCH_FAILED
