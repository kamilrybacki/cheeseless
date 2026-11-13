"""Opt-in session extras (read-only mounts, HOME seeds, env) cannot expose hidden material.

Rejection tests never reach the sandbox: they patch the agent launcher with a recorder and
assert it is never called. The positive test runs a real bubblewrap session.
"""

import json
import os
import stat
import time
from pathlib import Path

import pytest

from cheeseless import bootstrap
from cheeseless.adapters.outbound.git.repository import GitRepository
from cheeseless.adapters.outbound.runner.pytest_runner import PytestRunner
from cheeseless.adapters.outbound.sandbox.bwrap import BwrapSandbox
from cheeseless.adapters.outbound.store.file_store import FileStore, store_root
from cheeseless.app.freeze import commit, prepare
from cheeseless.bootstrap import host_session, launch_host_session
from cheeseless.session_options import SessionOptionError
from tests.requirements import needs
from tests.toy import CANARY, make_repo

needs_bwrap = needs(
    os.access("/usr/bin/bwrap", os.X_OK) and os.access("/usr/bin/python3", os.X_OK),
    "needs bubblewrap and /usr/bin/python3",
)

TOOL = "print('ro-tool-ran')\n"

PROBE = r"""
import json, os, stat, subprocess, sys
proj, tool, seed_rel, canary = sys.argv[1:5]
out = {}
ran = subprocess.run(["/usr/bin/python3", tool], capture_output=True, text=True, check=False)
out["tool_stdout"] = ran.stdout.strip()
try:
    open(os.path.join(os.path.dirname(tool), "writable-probe"), "w").close()
    out["ro_writable"] = True
except OSError:
    out["ro_writable"] = False
seed = os.path.join(os.environ["HOME"], seed_rel)
info = os.lstat(seed)
out["seed_mode"] = stat.S_IMODE(info.st_mode)
out["seed_is_symlink"] = stat.S_ISLNK(info.st_mode)
out["seed_dir_mode"] = stat.S_IMODE(os.lstat(os.path.dirname(seed)).st_mode)
out["seed_text"] = open(seed, encoding="utf-8").read()
out["extra_flag"] = os.environ.get("EXTRA_FLAG", "")
out["https_proxy"] = os.environ.get("HTTPS_PROXY", "")
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
with open(os.path.join(proj, "probe.json"), "w", encoding="utf-8") as handle:
    json.dump(out, handle)
print("PROBE-DONE")
"""


class _Env:
    def __init__(self, repo: GitRepository, state: Path, task_id: str | None = None) -> None:
        self.repo = repo
        self.state = state
        self.store_dir = store_root(repo.common_dir(), state)
        self.task_id = task_id


@pytest.fixture
def plain(tmp_path):
    """A frozen toy repo: hidden blobs and the store are known to the validator."""
    repo = GitRepository.discover(make_repo(tmp_path / "repo"))
    store = FileStore(store_root(repo.common_dir(), tmp_path / "state"))
    commit(prepare(repo, PytestRunner(), BwrapSandbox()), store)
    return _Env(repo, tmp_path / "state", store.active_task_id())


@pytest.fixture
def no_launch(monkeypatch):
    calls: list[object] = []

    def recorder(*args, **kwargs):
        calls.append(args)
        raise AssertionError("the agent must never be launched")

    monkeypatch.setattr(bootstrap, "_launch_agent", recorder)
    return calls


def _reject(env, tmp_path, no_launch, **kwargs):
    with pytest.raises((SessionOptionError, RuntimeError)):
        with host_session(
            state_dir=env.state,
            cwd=env.repo.root,
            task_id=env.task_id,
            projection_dir=tmp_path / "projection",
            socket_dir=tmp_path / "sockets",
            agent_command=["/bin/true"],
            **kwargs,
        ):
            pass
    assert no_launch == []


def test_extra_read_only_rejects_repo_root(plain, tmp_path, no_launch):
    _reject(plain, tmp_path, no_launch, extra_read_only=[plain.repo.root])


def test_extra_read_only_rejects_repo_subdir(plain, tmp_path, no_launch):
    hidden = Path(plain.repo.root) / "acceptance" / "hidden"
    _reject(plain, tmp_path, no_launch, extra_read_only=[hidden])


def test_extra_read_only_rejects_ancestor_of_repo(plain, tmp_path, no_launch):
    _reject(plain, tmp_path, no_launch, extra_read_only=[tmp_path])


def test_extra_read_only_rejects_store_dir(plain, tmp_path, no_launch):
    _reject(plain, tmp_path, no_launch, extra_read_only=[plain.store_dir])


def test_extra_read_only_rejects_symlink_into_repo(plain, tmp_path, no_launch):
    link = tmp_path / "innocent"
    link.symlink_to(plain.repo.root)
    _reject(plain, tmp_path, no_launch, extra_read_only=[link])


def test_extra_read_only_rejects_missing_path(plain, tmp_path, no_launch):
    _reject(plain, tmp_path, no_launch, extra_read_only=[tmp_path / "ro" / "missing"])


def test_home_seed_rejects_source_inside_repo(plain, tmp_path, no_launch):
    hidden = Path(plain.repo.root) / "acceptance" / "hidden" / "test_score.py"
    assert CANARY in hidden.read_text(encoding="utf-8")
    _reject(plain, tmp_path, no_launch, home_seed={".config/leak.py": hidden})


def test_home_seed_rejects_source_in_store(plain, tmp_path, no_launch):
    secret = plain.store_dir / "state.json"
    secret.write_text(CANARY, encoding="utf-8")
    _reject(plain, tmp_path, no_launch, home_seed={".config/leak": secret})


@pytest.mark.parametrize("rel", ["../escape", "/etc/passwd", "a/../../b", ""])
def test_home_seed_rejects_bad_relative_paths(plain, tmp_path, no_launch, rel):
    source = tmp_path / "seed-source"
    source.write_text("ok", encoding="utf-8")
    _reject(plain, tmp_path, no_launch, home_seed={rel: source})


@pytest.mark.parametrize(
    "key",
    ["HTTPS_PROXY", "https_proxy", "CHEESELESS_SOCKET", "HOME", "PATH", "NODE_EXTRA_CA_CERTS"],
)
def test_extra_env_cannot_override_session_variables(plain, tmp_path, no_launch, key):
    _reject(plain, tmp_path, no_launch, extra_env={key: "attacker"})


@pytest.mark.parametrize("env", [{"A=B": "x"}, {"A": "x\x00y"}, {"A\x00": "x"}])
def test_extra_env_rejects_malformed_entries(plain, tmp_path, no_launch, env):
    _reject(plain, tmp_path, no_launch, extra_env=env)


def test_evidence_dir_inside_repo_is_rejected(plain, tmp_path, no_launch):
    _reject(plain, tmp_path, no_launch, evidence_dir=Path(plain.repo.root) / "evidence")


def test_evidence_dir_inside_store_is_rejected(plain, tmp_path, no_launch):
    _reject(plain, tmp_path, no_launch, evidence_dir=plain.store_dir / "evidence")


def test_evidence_dir_inside_projection_is_rejected(plain, tmp_path, no_launch):
    _reject(plain, tmp_path, no_launch, evidence_dir=tmp_path / "projection" / "evidence")


@pytest.fixture(scope="module")
def frozen(tmp_path_factory):
    root = tmp_path_factory.mktemp("mounts-fixture")
    repo = GitRepository.discover(make_repo(root / "repo"))
    store = FileStore(store_root(repo.common_dir(), root / "state"))
    commit(prepare(repo, PytestRunner(), BwrapSandbox()), store)
    return type(
        "Frozen", (), {"repo": repo, "state": root / "state", "task_id": store.active_task_id()}
    )()


def _wait(path: Path, timeout: float = 120.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.1)
    raise AssertionError(f"probe did not finish: {path}")


def _tree_text(root: Path) -> str:
    chunks = []
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            chunks.append(path.name)
            chunks.append(path.read_bytes().decode("utf-8", errors="replace"))
    return "\n".join(chunks)


@needs_bwrap
def test_allowed_extras_work_and_leak_nothing(frozen, tmp_path, capfd):
    ro_dir = tmp_path / "ro"
    ro_dir.mkdir()
    tool = ro_dir / "tool.py"
    tool.write_text(TOOL, encoding="utf-8")
    seed_source = tmp_path / "seed-source.json"
    seed_source.write_text('{"theme": "dark"}', encoding="utf-8")
    seed_source.chmod(0o644)
    projection = tmp_path / "projection"
    command = [
        "/usr/bin/python3",
        "-c",
        PROBE,
        str(projection),
        str(tool),
        ".agent/settings.json",
        CANARY,
    ]
    with host_session(
        state_dir=frozen.state,
        cwd=frozen.repo.root,
        task_id=frozen.task_id,
        projection_dir=projection,
        socket_dir=tmp_path / "sockets",
        agent_command=command,
        extra_read_only=[ro_dir],
        home_seed={".agent/settings.json": seed_source},
        extra_env={"EXTRA_FLAG": "on"},
    ) as info:
        _wait(projection / "probe.json")
        data = json.loads((projection / "probe.json").read_text())
        home_text = _tree_text(Path(info["home_dir"]))
        projection_text = _tree_text(projection)

    assert data["tool_stdout"] == "ro-tool-ran"
    assert data["ro_writable"] is False
    assert data["seed_mode"] == 0o600
    assert data["seed_dir_mode"] == 0o700
    assert data["seed_is_symlink"] is False
    assert data["seed_text"] == '{"theme": "dark"}'
    assert data["extra_flag"] == "on"
    assert data["https_proxy"].startswith("http://127.0.0.1:")
    assert data["canaries"] == []
    assert CANARY not in home_text
    assert CANARY not in projection_text
    assert CANARY not in capfd.readouterr().out
    assert stat.S_IMODE(seed_source.stat().st_mode) == 0o644
    assert (Path(frozen.repo.root) / "acceptance" / "hidden" / "test_score.py").is_file()


NOTE_WRITER = (
    "import os; open(os.path.join(os.environ['HOME'], 'note.txt'), 'w').write('agent-was-here')"
)


@needs_bwrap
def test_evidence_is_captured_before_teardown_and_leaks_nothing(frozen, tmp_path):
    seed_source = tmp_path / "seed-source.json"
    seed_source.write_text('{"theme": "dark"}', encoding="utf-8")
    evidence = tmp_path / "evidence"
    code = launch_host_session(
        state_dir=frozen.state,
        cwd=frozen.repo.root,
        task_id=frozen.task_id,
        projection_dir=tmp_path / "projection",
        socket_dir=tmp_path / "sockets",
        agent_command=["/usr/bin/python3", "-c", NOTE_WRITER],
        home_seed={".agent/settings.json": seed_source},
        evidence_dir=evidence,
    )

    assert code == 0
    assert (evidence / "home" / "note.txt").read_text(encoding="utf-8") == "agent-was-here"
    assert (evidence / "home" / ".agent" / "settings.json").read_text() == '{"theme": "dark"}'
    assert (evidence / "projection-files.json").is_file()
    assert CANARY not in _tree_text(evidence)
    assert not list(evidence.rglob("test_score.py"))


@needs_bwrap
def test_canary_scan_has_a_positive_control(frozen, tmp_path, capfd):
    """Prove the in-sandbox canary scan finds a known marker, so an empty list means something."""
    control = tmp_path / "control"
    control.mkdir()
    marker = control / "marker.txt"
    marker.write_text(CANARY, encoding="utf-8")
    tool = control / "tool.py"
    tool.write_text(TOOL, encoding="utf-8")
    seed_source = tmp_path / "seed-source.json"
    seed_source.write_text('{"theme": "dark"}', encoding="utf-8")
    projection = tmp_path / "projection"
    command = [
        "/usr/bin/python3",
        "-c",
        PROBE,
        str(projection),
        str(tool),
        ".agent/settings.json",
        CANARY,
    ]
    with host_session(
        state_dir=frozen.state,
        cwd=frozen.repo.root,
        task_id=frozen.task_id,
        projection_dir=projection,
        socket_dir=tmp_path / "sockets",
        agent_command=command,
        extra_read_only=[control],
        home_seed={".agent/settings.json": seed_source},
    ) as info:
        _wait(projection / "probe.json")
        data = json.loads((projection / "probe.json").read_text())

    assert data["tool_stdout"] == "ro-tool-ran"
    assert str(marker) in data["canaries"]
    assert CANARY in _tree_text(control)
    assert info["home_dir"] is not None
    assert CANARY not in capfd.readouterr().out
