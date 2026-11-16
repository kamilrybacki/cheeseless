"""Hardening of the session extras: pinned sources, inode checks, safe evidence.

Each test reproduces one attack from the security review. Rejections use a recorder as
the agent launcher, so a refused request provably starts no agent. Real-sandbox tests
assert the canary is absent from what the agent or the evidence can see.
"""

import json
import os
import subprocess  # noqa: S404 - the sandbox probe is a process boundary
import zlib
from pathlib import Path

import pytest

from cheeseless import bootstrap
from cheeseless.adapters.outbound.git.repository import GitRepository
from cheeseless.adapters.outbound.runner.pytest_runner import PytestRunner
from cheeseless.adapters.outbound.sandbox import profiles
from cheeseless.adapters.outbound.sandbox.bwrap import BwrapSandbox
from cheeseless.adapters.outbound.store.file_store import FileStore, store_root
from cheeseless.app.freeze import commit, prepare
from cheeseless.bootstrap import host_session
from cheeseless.session_files import copy_evidence, seed_home, write_projection_list
from cheeseless.session_options import (
    PinnedMount,
    SessionOptionError,
    prepare_extras,
)
from tests.requirements import needs
from tests.toy import CANARY, make_repo

needs_bwrap = needs(
    os.access("/usr/bin/bwrap", os.X_OK) and os.access("/usr/bin/python3", os.X_OK),
    "needs bubblewrap and /usr/bin/python3",
)


class _Env:
    def __init__(self, repo: GitRepository, state: Path) -> None:
        self.repo = repo
        self.state = state
        self.store = FileStore(store_root(repo.common_dir(), state))
        self.task_id = self.store.active_task_id()


@pytest.fixture(scope="module")
def frozen(tmp_path_factory):
    root = tmp_path_factory.mktemp("extras-hardening")
    repo = GitRepository.discover(make_repo(root / "repo"))
    store = FileStore(store_root(repo.common_dir(), root / "state"))
    commit(prepare(repo, PytestRunner(), BwrapSandbox()), store)
    return _Env(repo, root / "state")


@pytest.fixture
def no_launch(monkeypatch):
    calls: list[object] = []

    def recorder(*args, **kwargs):
        calls.append(args)
        raise AssertionError("the agent must never be launched")

    monkeypatch.setattr(bootstrap, "_launch_agent", recorder)
    return calls


def _reject(env, tmp_path, no_launch, **kwargs):
    marker = tmp_path / "agent-ran"
    with pytest.raises((SessionOptionError, RuntimeError)):
        with host_session(
            state_dir=env.state,
            cwd=env.repo.root,
            task_id=env.task_id,
            projection_dir=tmp_path / "projection",
            socket_dir=tmp_path / "sockets",
            agent_command=["/bin/sh", "-c", f"touch {marker}"],
            **kwargs,
        ):
            pass
    assert no_launch == []
    assert not marker.exists()


def _tree_text(root: Path) -> str:
    chunks = []
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            chunks.append(path.name)
            chunks.append(path.read_bytes().decode("utf-8", errors="replace"))
    return "\n".join(chunks)


def _close(mounts: tuple[PinnedMount, ...]) -> None:
    for mount in mounts:
        os.close(mount.fd)


def _hidden(env: _Env) -> Path:
    hidden = Path(env.repo.root) / "acceptance" / "hidden" / "test_score.py"
    assert CANARY in hidden.read_text(encoding="utf-8")
    return hidden


def test_seed_parent_symlink_swap_uses_pinned_content(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    source = parent / "seed"
    source.write_text("original", encoding="utf-8")
    extras = prepare_extras(
        protected=(tmp_path / "protected",),
        temp_bases=(),
        reserved_env=(),
        home_seed={"cfg/seed": source},
    )
    assert "original" not in repr(extras)
    (tmp_path / "protected").mkdir()
    evil = tmp_path / "evil"
    evil.mkdir()
    (evil / "seed").write_text(CANARY, encoding="utf-8")
    parent.rename(tmp_path / "parent-old")
    parent.symlink_to(evil)
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    seed_home(home, extras.seeds)
    assert (home / "cfg" / "seed").read_text(encoding="utf-8") == "original"
    assert CANARY not in _tree_text(home)
    _close(extras.read_only)


@needs_bwrap
def test_mount_parent_symlink_swap_agent_sees_pinned_tree(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    mounted = parent / "m"
    mounted.mkdir(parents=True)
    (mounted / "data.txt").write_text("original", encoding="utf-8")
    extras = prepare_extras(
        protected=(),
        temp_bases=(),
        reserved_env=(),
        extra_read_only=[mounted],
    )
    mount = extras.read_only[0]
    evil = tmp_path / "evil"
    (evil / "m").mkdir(parents=True)
    (evil / "m" / "data.txt").write_text(CANARY, encoding="utf-8")
    parent.rename(tmp_path / "parent-old")
    parent.symlink_to(evil)

    projection = tmp_path / "projection"
    (projection / "acceptance").mkdir(parents=True)
    home = tmp_path / "home"
    home.mkdir()
    probe = "import sys; print(open(sys.argv[1] + '/data.txt', encoding='utf-8').read(), end='')"
    acceptance_fd = profiles.open_acceptance(projection)
    argv = profiles.agent(
        bwrap="/usr/bin/bwrap",
        projection=projection,
        sockets=(),
        home=home,
        read_only=(),
        pinned_read_only=[(mount.fd, str(mounted))],
        env=(),
        command=["/usr/bin/python3", "-c", probe, str(mounted)],
        acceptance_fd=acceptance_fd,
    )
    try:
        done = subprocess.run(  # noqa: S603 - argv built from validated parts
            argv,
            pass_fds=(mount.fd, acceptance_fd),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    finally:
        os.close(acceptance_fd)
        _close(extras.read_only)
    assert done.returncode == 0, done.stderr
    assert done.stdout == "original"
    assert CANARY not in done.stdout + done.stderr


def test_seed_hard_link_to_hidden_file_is_refused(frozen, tmp_path, no_launch) -> None:
    link = tmp_path / "innocent-seed"
    os.link(_hidden(frozen), link)
    _reject(frozen, tmp_path, no_launch, home_seed={"cfg/seed": link})


def test_mounted_dir_with_hard_link_to_hidden_file_is_refused(frozen, tmp_path, no_launch) -> None:
    holder = tmp_path / "ro"
    holder.mkdir()
    os.link(_hidden(frozen), holder / "leak.py")
    _reject(frozen, tmp_path, no_launch, extra_read_only=[holder])


def test_mount_with_parent_symlinked_into_repo_is_refused(frozen, tmp_path, no_launch) -> None:
    link = tmp_path / "innocent-parent"
    link.symlink_to(frozen.repo.root)
    _reject(frozen, tmp_path, no_launch, extra_read_only=[link / "src"])


@pytest.mark.parametrize("path", ["/proc", "/dev/shm", "/sys", "/"])  # noqa: S108
def test_reserved_sandbox_destinations_are_refused(frozen, tmp_path, no_launch, path) -> None:
    _reject(frozen, tmp_path, no_launch, extra_read_only=[Path(path)])


@pytest.mark.parametrize("kind", ["directory", "symlink"])
def test_pre_existing_evidence_destination_is_refused(frozen, tmp_path, no_launch, kind) -> None:
    evidence = tmp_path / "evidence"
    if kind == "directory":
        evidence.mkdir()
    else:
        (tmp_path / "target").mkdir()
        evidence.symlink_to(tmp_path / "target")
    _reject(frozen, tmp_path, no_launch, evidence_dir=evidence)


def test_evidence_destination_with_planted_truncated_link_is_refused(tmp_path, monkeypatch):
    from cheeseless import session_files

    home = tmp_path / "home"
    home.mkdir()
    (home / "note.txt").write_bytes(b"content")
    target = tmp_path / "target"
    original = CANARY.encode() + b"\x00keep exactly\n"
    target.write_bytes(original)
    evidence = tmp_path / "evidence"
    create = session_files._create_evidence_dir

    def plant(*args):
        fd = create(*args)
        (evidence / "TRUNCATED").symlink_to(target)
        return fd

    monkeypatch.setattr(session_files, "_create_evidence_dir", plant)
    result = copy_evidence(home, evidence, limit=0)
    assert result.truncated
    assert target.read_bytes() == original
    assert CANARY not in _tree_text(evidence)


def test_new_evidence_marker_is_a_regular_file(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    for index in range(4):
        (home / f"f{index}").write_bytes(b"x" * 100)
    evidence = tmp_path / "evidence"
    result = copy_evidence(home, evidence, limit=150)
    marker = evidence / "TRUNCATED"
    assert result.truncated
    assert marker.is_file() and not marker.is_symlink()


def test_hard_linked_home_file_is_copied_without_touching_the_original(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    original = tmp_path / "original"
    original.write_text("agent-content", encoding="utf-8")
    os.link(original, home / "hard")
    evidence = tmp_path / "evidence"
    copy_evidence(home, evidence)
    copied = evidence / "home" / "hard"
    assert copied.read_text(encoding="utf-8") == "agent-content"
    assert copied.stat().st_ino != original.stat().st_ino
    copied.write_text("changed", encoding="utf-8")
    assert original.read_text(encoding="utf-8") == "agent-content"
    assert CANARY not in _tree_text(evidence)


def test_deep_evidence_tree_is_truncated_without_exception(tmp_path: Path) -> None:
    home = tmp_path / "home"
    current = home
    for _ in range(500):
        current = current / "d"
        current.mkdir(parents=True)
    (current / "deep.txt").write_text("x", encoding="utf-8")
    evidence = tmp_path / "evidence"
    result = copy_evidence(home, evidence)
    assert result.truncated
    assert "depth cap" in (evidence / "TRUNCATED").read_text(encoding="utf-8")
    assert CANARY not in _tree_text(evidence)


def test_projection_manifest_round_trips_newline_filename(tmp_path: Path) -> None:
    projection = tmp_path / "projection"
    (projection / "pkg").mkdir(parents=True)
    (projection / "pkg" / "a.py").write_text("", encoding="utf-8")
    (projection / "line\nbreak.py").write_text("", encoding="utf-8")
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    write_projection_list(projection, evidence)
    found = json.loads((evidence / "projection-files.json").read_text(encoding="utf-8"))
    assert found == ["line\nbreak.py", "pkg/a.py"]
    assert CANARY not in _tree_text(evidence)


def test_many_evidence_entries_are_truncated_without_exception(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("cheeseless.session_files.EVIDENCE_ENTRIES", 8)
    home = tmp_path / "home"
    home.mkdir()
    for index in range(40):
        (home / f"f{index}.txt").write_text("x", encoding="utf-8")
    evidence = tmp_path / "evidence"
    result = copy_evidence(home, evidence)
    assert result.truncated
    assert "entry cap" in (evidence / "TRUNCATED").read_text(encoding="utf-8")
    assert CANARY not in _tree_text(evidence)


def _loose_object(env: _Env, rel: str) -> Path:
    """Path of the git loose object that stores a hidden file's blob."""
    sha = subprocess.run(  # noqa: S603 - fixed git argv inside the test repo
        ["git", "rev-parse", f"HEAD:{rel}"],  # noqa: S607 - git from PATH in tests
        cwd=env.repo.root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    path = Path(env.repo.common_dir()) / "objects" / sha[:2] / sha[2:]
    assert path.is_file(), "the toy repo must keep the hidden blob as a loose object"
    return path


def test_mounted_dir_with_hard_link_to_git_object_is_refused(frozen, tmp_path, no_launch) -> None:
    obj = _loose_object(frozen, "acceptance/hidden/test_score.py")
    holder = tmp_path / "ro"
    holder.mkdir()
    os.link(obj, holder / "blob")
    assert CANARY.encode() in zlib.decompress((holder / "blob").read_bytes())
    _reject(frozen, tmp_path, no_launch, extra_read_only=[holder])


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_mounted_dir_with_unreadable_subdir_is_refused(frozen, tmp_path, no_launch) -> None:
    holder = tmp_path / "ro"
    locked = holder / "locked"
    locked.mkdir(parents=True)
    os.link(_hidden(frozen), locked / "leak.py")
    locked.chmod(0o000)
    try:
        with pytest.raises(SessionOptionError, match="cannot be inspected"):
            prepare_extras(
                protected=(),
                temp_bases=(),
                reserved_env=(),
                extra_read_only=[holder],
            )
        _reject(frozen, tmp_path, no_launch, extra_read_only=[holder])
    finally:
        locked.chmod(0o700)


@pytest.mark.parametrize("kind", ["socket", "fifo", "device"])
@pytest.mark.parametrize("nested", [False, True])
def test_special_mount_is_refused(frozen, tmp_path, no_launch, kind, nested):
    import socket
    import tempfile

    _hidden(frozen)
    holder = tmp_path / "holder"
    holder.mkdir()
    source = holder / "special"
    if kind == "socket":
        with tempfile.TemporaryDirectory(dir="/tmp") as short:
            with socket.socket(socket.AF_UNIX) as sock:
                sock.bind(str(Path(short) / "s"))
                os.rename(Path(short) / "s", source)
    elif kind == "fifo":
        os.mkfifo(source)
    else:
        source = Path("/dev/null")
        if nested:
            pytest.skip("device creation requires privileges")
    with pytest.raises(SessionOptionError, match="special file"):
        prepare_extras(
            protected=(),
            temp_bases=(),
            reserved_env=(),
            extra_read_only=[holder if nested else source],
        )
    _reject(frozen, tmp_path, no_launch, extra_read_only=[holder if nested else source])


def test_seed_symlink_is_refused(frozen, tmp_path, no_launch):
    source = tmp_path / "seed"
    source.symlink_to(_hidden(frozen))
    with pytest.raises(SessionOptionError, match="cannot open"):
        prepare_extras(protected=(), temp_bases=(), reserved_env=(), home_seed={"cfg": source})
    assert no_launch == []


def test_fifo_seed_is_refused(frozen, tmp_path, no_launch):
    _hidden(frozen)
    source = tmp_path / "seed"
    os.mkfifo(source)
    _reject(frozen, tmp_path, no_launch, home_seed={"cfg": source})


@pytest.mark.parametrize("failure", ["walk", "lstat", "cap", "deadline"])
def test_protected_enumeration_fails_closed(frozen, tmp_path, no_launch, monkeypatch, failure):
    _hidden(frozen)
    holder = tmp_path / "holder"
    holder.mkdir()
    if failure == "walk":

        def broken_walk(root, **kwargs):
            if "onerror" in kwargs:
                kwargs["onerror"](PermissionError("unreadable protected directory"))
            return iter(())

        monkeypatch.setattr(bootstrap.os, "walk", broken_walk)
    elif failure == "lstat":
        original = os.lstat

        def broken_stat(path, *args, **kwargs):
            if str(path).endswith("HEAD"):
                raise PermissionError("unreadable protected entry")
            return original(path, *args, **kwargs)

        monkeypatch.setattr(bootstrap.os, "lstat", broken_stat)
    elif failure == "cap":
        monkeypatch.setattr(bootstrap, "PROTECTED_WALK_ENTRIES", 0, raising=False)
    else:
        monkeypatch.setattr(bootstrap, "PROTECTED_WALK_SECONDS", -1, raising=False)
    _reject(frozen, tmp_path, no_launch, extra_read_only=[holder])


def _fresh_env(root):
    repo = GitRepository.discover(make_repo(root / "repo"))
    store = FileStore(store_root(repo.common_dir(), root / "state"))
    commit(prepare(repo, PytestRunner(), BwrapSandbox()), store)
    return _Env(repo, root / "state")


@pytest.mark.parametrize("attack", ["mount", "seed", "hardlink"])
def test_external_objects_are_protected(tmp_path, no_launch, attack):
    env = _fresh_env(tmp_path)
    obj = _loose_object(env, "acceptance/hidden/test_score.py")
    relative = obj.relative_to(env.repo.common_dir() / "objects")
    cache = tmp_path / "cache"
    (env.repo.common_dir() / "objects").rename(cache)
    (env.repo.common_dir() / "objects").symlink_to(cache, target_is_directory=True)
    assert CANARY.encode() in env.repo.show(
        env.repo.head_commit(), "acceptance/hidden/test_score.py"
    )
    obj = cache / relative
    assert CANARY.encode() in zlib.decompress(obj.read_bytes())
    if attack == "seed":
        _reject(env, tmp_path, no_launch, home_seed={"blob": obj})
    else:
        holder = cache
        if attack == "hardlink":
            holder = tmp_path / "holder"
            holder.mkdir()
            os.link(obj, holder / "blob")
        _reject(env, tmp_path, no_launch, extra_read_only=[holder])


def test_shared_clone_alternate_is_protected(tmp_path, no_launch):
    source = _fresh_env(tmp_path / "source")
    obj = _loose_object(source, "acceptance/hidden/test_score.py")
    subprocess.run(  # noqa: S603
        ["git", "clone", "--shared", str(source.repo.root), str(tmp_path / "clone")],  # noqa: S607
        check=True,
        capture_output=True,
    )
    repo = GitRepository.discover(tmp_path / "clone")
    store = FileStore(store_root(repo.common_dir(), tmp_path / "state"))
    commit(prepare(repo, PytestRunner(), BwrapSandbox()), store)
    env = _Env(repo, tmp_path / "state")
    assert CANARY.encode() in zlib.decompress(obj.read_bytes())
    holder = tmp_path / "holder"
    holder.mkdir()
    os.link(obj, holder / "blob")
    _reject(env, tmp_path, no_launch, extra_read_only=[holder])


@pytest.mark.parametrize("kind", ["directory", "file"])
def test_protected_internal_symlinks(tmp_path, no_launch, kind):
    env = _fresh_env(tmp_path)
    holder = tmp_path / "holder"
    holder.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    target = external / "hidden"
    target.write_text(CANARY)
    link = env.repo.common_dir() / "linked"
    link.symlink_to(external if kind == "directory" else target)
    os.link(target, holder / "blob")
    _reject(env, tmp_path, no_launch, extra_read_only=[holder])


@pytest.mark.parametrize("consumer", ["mount", "evidence", "projection"])
@pytest.mark.parametrize("limit", ["entries", "deadline"])
def test_listing_is_bounded(tmp_path, monkeypatch, consumer, limit):
    from cheeseless import session_files, session_options

    root = tmp_path / "tree"
    root.mkdir()
    for n in range(20):
        (root / str(n)).write_text("safe")
    # The canary remains outside the scanned tree and all produced evidence.
    (tmp_path / "hidden").write_text(CANARY)
    module = session_options if consumer == "mount" else session_files
    prefix = "MOUNT_WALK" if consumer == "mount" else "EVIDENCE"
    monkeypatch.setattr(
        module,
        prefix + ("_ENTRIES" if limit == "entries" else "_SECONDS"),
        3 if limit == "entries" else 1,
        raising=False,
    )
    now = [0.0]
    if limit == "deadline":
        monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    original = os.scandir
    consumed = []

    class Scan:
        def __init__(self, path):
            self.scan = original(path)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.scan.close()

        def __iter__(self):
            return self

        def __next__(self):
            item = next(self.scan)
            consumed.append(item.name)
            if limit == "deadline":
                now[0] = 2.0
            assert len(consumed) <= (4 if limit == "entries" else 1)
            return item

    monkeypatch.setattr(os, "scandir", Scan)
    dest = tmp_path / "evidence"
    if consumer == "mount":
        with pytest.raises(SessionOptionError, match="entries|time"):
            prepare_extras(protected=(), temp_bases=(), reserved_env=(), extra_read_only=[root])
    elif consumer == "evidence":
        assert copy_evidence(root, dest).truncated
    else:
        dest.mkdir()
        write_projection_list(root, dest)
        assert (dest / "TRUNCATED").exists()
    monkeypatch.setattr(os, "scandir", original)
    assert consumed, "the directory must be enumerated with bounded scandir"
    assert CANARY not in _tree_text(dest) if dest.exists() else True


@pytest.mark.parametrize("layout", ["environment", "environment-alternate", "recursive", "depth"])
def test_object_directory_discovery(tmp_path, monkeypatch, no_launch, layout):
    env = _fresh_env(tmp_path)
    obj = _loose_object(env, "acceptance/hidden/test_score.py")
    cache = tmp_path / "cache"
    cache.mkdir()
    blob = cache / "blob"
    blob.write_bytes(obj.read_bytes())
    assert CANARY.encode() in zlib.decompress(blob.read_bytes())
    if layout.startswith("environment"):
        key = (
            "GIT_OBJECT_DIRECTORY"
            if layout == "environment"
            else "GIT_ALTERNATE_OBJECT_DIRECTORIES"
        )
        monkeypatch.setenv(key, str(cache))
    else:
        parent = env.repo.common_dir() / "objects"
        for index in range(7 if layout == "depth" else 2):
            child = (
                cache
                if index == (6 if layout == "depth" else 1)
                else tmp_path / f"alternate{index}"
            )
            child.mkdir(exist_ok=True)
            (parent / "info").mkdir(exist_ok=True)
            (parent / "info" / "alternates").write_text(
                "\n# ignored comment\n" + os.path.relpath(child, parent) + "\n"
            )
            parent = child
    if layout != "depth":
        assert cache.resolve() in env.repo.object_dirs()
    _reject(env, tmp_path, no_launch, extra_read_only=[cache])
