import json
import os
import subprocess  # noqa: S404 - the probe runs inside the real sandbox
import time
from pathlib import Path

import pytest

from cheeseless.adapters.outbound.git.repository import GitRepository
from cheeseless.adapters.outbound.runner.pytest_runner import PytestRunner
from cheeseless.adapters.outbound.sandbox.bwrap import BwrapSandbox
from cheeseless.adapters.outbound.store.file_store import FileStore, store_root
from cheeseless.app.freeze import commit, prepare
from cheeseless.bootstrap import host_session
from tests.requirements import needs
from tests.toy import CANARY, make_repo

pytestmark = needs(
    os.access("/usr/bin/bwrap", os.X_OK) and os.access("/usr/bin/python3", os.X_OK),
    "needs bubblewrap and /usr/bin/python3",
)

PROBE = r"""
import json, os, socket, sys
proj, repo_root, store_root, sock, canary = sys.argv[1:6]
out = {}
out["repo_visible"] = os.path.exists(repo_root)
out["store_visible"] = os.path.exists(store_root)
out["hidden_visible"] = os.path.exists(os.path.join(repo_root, "acceptance", "hidden"))
found = []
prune = {"/proc", "/sys", "/dev", "/usr", "/lib", "/bin", "/sbin", "/etc"}
for dirpath, dirs, files in os.walk("/"):
    if dirpath in prune:
        dirs[:] = []
        continue
    for name in files:
        path = os.path.join(dirpath, name)
        if name == "test_score.py":
            found.append(path)
            continue
        try:
            with open(path, "rb") as handle:
                if canary.encode() in handle.read(1 << 20):
                    found.append(path)
        except OSError:
            pass
out["canaries"] = found

def post(path):
    body = b"{}"
    request = (
        "POST " + path + " HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\n"
        "Content-Length: " + str(len(body)) + "\r\nConnection: close\r\n\r\n"
    ).encode("ascii") + body
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(60)
    client.connect(sock)
    client.sendall(request)
    data = b""
    while True:
        chunk = client.recv(65536)
        if not chunk:
            break
        data += chunk
    client.close()
    return data.split(b"\r\n", 1)[0].decode("iso-8859-1")

out["run"] = post("/run")
out["status"] = post("/status")
out["cwd"] = os.getcwd()
runtime = os.environ.get("CHEESELESS_RUNTIME", "")
out["runtime_outside"] = (
    bool(runtime)
    and not runtime.startswith(repo_root)
    and not runtime.startswith(store_root)
)
try:
    with open(os.path.join(runtime, "writable-probe"), "w", encoding="ascii") as handle:
        handle.write("x")
    out["runtime_writable"] = True
except OSError:
    out["runtime_writable"] = False
mounts = open("/proc/self/mountinfo", encoding="utf-8", errors="replace").read().splitlines()
out["repo_mounts"] = [line for line in mounts if repo_root in line or store_root in line]
out["socket_mounts"] = [line for line in mounts if ".sock" in line]
with open(os.path.join(proj, "probe.json"), "w", encoding="utf-8") as handle:
    json.dump(out, handle)
print("PROBE-DONE")
"""


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    root = tmp_path_factory.mktemp("session-fixture")
    repo = GitRepository.discover(make_repo(root / "toy"))
    runner = PytestRunner()
    sandbox = BwrapSandbox()
    store = FileStore(store_root(repo.common_dir(), root / "state"))
    prepared = prepare(repo, runner, sandbox)
    commit(prepared, store)
    return type(
        "SessionEnv",
        (),
        {
            "repo": repo,
            "store": store,
            "state": root / "state",
            "task_id": store.active_task_id(),
        },
    )()


def _wait(path: Path, timeout: float = 120.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.1)
    raise AssertionError(f"probe did not finish: {path}")


def test_host_session_isolates_and_authenticates(env, tmp_path):
    projection = tmp_path / "projection"
    socket_dir = tmp_path / "sockets"
    probe = projection / "probe.json"
    command = [
        "/usr/bin/python3",
        "-c",
        PROBE,
        str(projection),
        str(env.repo.root),
        str(store_root(env.repo.common_dir(), env.state)),
        str(socket_dir / "verifier.sock"),
        CANARY,
    ]
    with host_session(
        state_dir=env.state,
        cwd=env.repo.root,
        task_id=env.task_id,
        projection_dir=projection,
        socket_dir=socket_dir,
        agent_command=command,
    ):
        _wait(probe)
    data = json.loads(probe.read_text())

    assert data["run"].startswith("HTTP/1.1 200"), data
    assert data["status"].startswith("HTTP/1.1 200"), data
    assert data["repo_visible"] is False
    assert data["store_visible"] is False
    assert data["hidden_visible"] is False
    assert data["canaries"] == []
    assert data["repo_mounts"] == []
    assert len(data["socket_mounts"]) == 2, data["socket_mounts"]
    assert any("verifier.sock" in line for line in data["socket_mounts"])
    assert any("egress.sock" in line for line in data["socket_mounts"])
    assert data["cwd"] == str(projection)
    assert data["runtime_outside"] is True
    assert data["runtime_writable"] is False

    host_hidden = env.repo.root / "acceptance" / "hidden" / "test_score.py"
    assert host_hidden.is_file()
    assert CANARY in host_hidden.read_text(encoding="utf-8")


def test_host_session_rejects_stray_hidden_before_launch(env, tmp_path, monkeypatch):
    projection = tmp_path / "projection"
    socket_dir = tmp_path / "sockets"
    marker = tmp_path / "agent-ran"
    monkeypatch.setattr(
        GitRepository,
        "stray_hidden",
        lambda self, worktree, hidden: ["acceptance/hidden/test_score.py"],
    )
    with pytest.raises(RuntimeError, match="stray hidden material"):
        with host_session(
            state_dir=env.state,
            cwd=env.repo.root,
            task_id=env.task_id,
            projection_dir=projection,
            socket_dir=socket_dir,
            agent_command=["/bin/sh", "-c", f"touch {marker}"],
        ):
            pass
    assert not marker.exists()


def test_host_session_rejects_projection_inside_repo(env, tmp_path):
    with pytest.raises(RuntimeError, match="outside the trusted repository"):
        with host_session(
            state_dir=env.state,
            cwd=env.repo.root,
            task_id=env.task_id,
            projection_dir=env.repo.root / "projection",
            socket_dir=tmp_path / "sockets",
        ):
            pass


def test_host_session_negative_control_finds_canary_on_host(env):
    store_path = store_root(env.repo.common_dir(), env.state)
    found = subprocess.run(  # noqa: S603 - fixed test command
        ["/usr/bin/grep", "-rl", CANARY, str(env.repo.root), str(store_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert found.returncode == 0
    assert found.stdout.strip()


def test_host_session_default_projection_lifecycle(env):
    with host_session(
        state_dir=env.state,
        cwd=env.repo.root,
        task_id=env.task_id,
    ) as info:
        assert info["projection_dir"].is_dir()
        assert info["socket_path"].is_socket()
        assert info["namespace_fd"] >= 0
        projection = info["projection_dir"]
        sockdir = info["socket_path"].parent
        home = info["home_dir"]
    assert not projection.exists()
    assert not sockdir.exists()
    assert not home.exists()
