"""Composition root: wires adapters to ports; may import every layer."""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import select
import shutil
import signal
import stat
import subprocess  # noqa: S404 - the sandbox is a process boundary by design
import sys
import sysconfig
import tempfile
import threading
import time as _time
import types
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast

import cheeseless
from cheeseless.adapters.inbound.cli.main import (
    Context,
    build_parser,
    dispatch,
    split_agent_argv,
)
from cheeseless.adapters.inbound.mcp.server import build_mcp_server, run_stdio
from cheeseless.adapters.inbound.uds.client import UdsVerifierClient
from cheeseless.adapters.inbound.uds.server import SessionConfig, UdsVerifierServer
from cheeseless.adapters.outbound.config.launchers import TomlLauncherCatalog
from cheeseless.adapters.outbound.egress.bridge import (
    BRIDGE_HOST,
    BRIDGE_PORT,
    EGRESS_PORT_ENV,
    EGRESS_READY_ENV,
    EGRESS_SOCKET_ENV,
)
from cheeseless.adapters.outbound.egress.proxy import EgressProxy, ProxyConfig
from cheeseless.adapters.outbound.git.repository import GitRepository
from cheeseless.adapters.outbound.pane.relay import PaneRelay
from cheeseless.adapters.outbound.presence.tty import TtyPresence
from cheeseless.adapters.outbound.runner.pytest_runner import PytestRunner
from cheeseless.adapters.outbound.sandbox import profiles, pty_launch, seccomp
from cheeseless.adapters.outbound.sandbox.bwrap import BwrapSandbox
from cheeseless.adapters.outbound.sandbox.launch import force_remove
from cheeseless.adapters.outbound.store.file_store import FileStore, store_root
from cheeseless.app.audit import StoreAuditSink
from cheeseless.app.authoring import read_review_verdict
from cheeseless.app.dlp import scanner_for
from cheeseless.app.egress_supervisor import EgressBlock, EgressSupervisor
from cheeseless.app.launching import resolve_launch
from cheeseless.app.reference_view import reference_tree, reviewer_tree_of
from cheeseless.domain import ids
from cheeseless.domain.budget import Pool
from cheeseless.domain.dlp import Fingerprints
from cheeseless.domain.egress_policy import EgressPolicy, PolicyError
from cheeseless.domain.reference_attempt import (
    AttemptOwner,
    NewReferenceAttempt,
    ReferenceAttempt,
    ReferenceMode,
    ReferenceState,
)
from cheeseless.domain.session_role import (
    SessionRole,
    acceptance_mount,
    dev_home_entries,
    parse_role,
)
from cheeseless.ports.dev_session import DevSessionStore
from cheeseless.ports.reference_attempt import ReferenceAttemptStore
from cheeseless.ports.store import Store, StoreError
from cheeseless.session_files import copy_evidence, seed_home, write_projection_list
from cheeseless.session_options import (
    SessionExtras,
    SessionOptionError,
    check_extra_env,
    ensure_outside_all,
    load_egress_policy,
    parse_key_value,
    parse_seed_spec,
    prepare_extras,
)
from cheeseless.session_options import within as _within

SESSION_START_TIMEOUT = 10.0
MAX_INFO_BYTES = 1 << 20
_DEFAULT_AGENT = ("sleep", "infinity")
BRIDGE_BOOT = (
    "import os,sys;"
    "sys.path.insert(0, os.environ['CHEESELESS_RUNTIME']);"
    "from cheeseless.adapters.outbound.egress.bridge import client_main;"
    "raise SystemExit(client_main())"
)


def _state_home(state_dir: Path | None) -> Path:
    if state_dir is not None:
        return state_dir
    env = os.environ.get("XDG_STATE_HOME")
    if env:
        return Path(env)
    return Path.home() / ".local" / "state"


def build_context(state_dir: Path | None = None, cwd: Path | None = None) -> Context:
    repo = GitRepository.discover(cwd or Path.cwd())
    store = FileStore(store_root(repo.common_dir(), _state_home(state_dir)))
    sandbox = BwrapSandbox(protected=[store.root, repo.root])
    return Context(
        repo=repo,
        runner=PytestRunner(),
        sandbox=sandbox,
        store=store,
        author_sandbox=sandbox,
        reviewer_launch=_reviewer_launch_for(state_dir),
        launchers=_launcher_catalog(repo),
        presence=TtyPresence(),
    )


def _launcher_catalog(repo: Any) -> TomlLauncherCatalog:
    """The operator's launcher config; never read from inside the repository."""
    return TomlLauncherCatalog(forbidden=_forbidden_roots(repo))


def resolve_cli_launch(
    launcher_name: str | None,
    agent: Sequence[str] | None,
    *,
    label: str | None = None,
    cwd: Path | None = None,
) -> tuple[list[str], str]:
    """``(command, model)`` for a CLI launch path (``session``, ``dev``, ``dev-reference``).

    ``model`` is the operator-declared one for ``--launcher``, else empty: a launch nobody
    configured records no model and so cannot satisfy the unattended-freeze vendor check.
    Raises ``LauncherError`` (a ``ValueError``) on any refusal.
    """
    launcher = None
    if launcher_name is not None:
        launcher = _launcher_catalog(GitRepository.discover(cwd or Path.cwd())).get(launcher_name)
    return resolve_launch(launcher, agent or [], label)


def _reviewer_launch_for(
    state_dir: Path | None,
) -> Callable[[Sequence[str], str, str], Callable[[str], tuple[bool | None, str]]]:
    """The launch seam `strength-review --run` uses (ADR 0015).

    The reviewer's model session is started by the host, not by the CLI: an inbound adapter
    cannot import the bootstrap, and the sandbox setup belongs here anyway.

    The returned launch reports the model it launched as well as the verdict, because the
    reviewer's vendor has to come from the launch and not from a flag someone typed.
    """

    def make(
        command: Sequence[str], task_id: str, reviewer_model: str
    ) -> Callable[[str], tuple[bool | None, str]]:
        def launch(brief: str) -> tuple[bool | None, str]:
            with host_session(
                state_dir=state_dir,
                agent_command=list(command),
                role=SessionRole.REVIEWER,
                brief_file=brief,
                task_id=task_id,
                agent_model=reviewer_model,
            ) as info:
                # Teardown removes HOME, so the verdict has to be read while the session is
                # still open - and only once the agent has finished, or a fast reviewer's file
                # would be read as absent or half-written.
                proc: subprocess.Popen[Any] = info["agent_proc"]
                try:
                    code = proc.wait()
                except KeyboardInterrupt:
                    code = 1
                verdict = read_review_verdict(Path(info["home_dir"]))
                # Read from the binding the host wrote at launch, not from the caller's flag.
                launched = info["binding"].model
            if code != 0:
                # A reviewer that crashed recorded nothing; a refusal would hide the crash.
                return None, launched
            return verdict, launched

        return launch

    return make


def _pool(profile: str) -> Pool:
    return Pool.CI if profile == "ci" else Pool.LOCAL


def _private_dir(prefix: str) -> Path:
    path = Path(tempfile.mkdtemp(prefix=prefix))
    os.chmod(path, stat.S_IRWXU)
    return path


def _forbidden_roots(repo: Any) -> tuple[Path, ...]:
    return (Path(repo.root), Path(repo.common_dir()))


@dataclass(frozen=True, slots=True)
class _ReviewSubject:
    """What a reviewer projection needs from a pre-freeze proposal.

    `host_session` reads `contract` and `projection_tree` before it reaches the reviewer
    branch, so a subject without them would fail deep in setup with an `AttributeError`
    rather than a refusal. They are named here rather than discovered later.
    """

    acceptance_tree: str
    # Which lineage is being reviewed, for the DLP identity markers. A proposal knows it.
    contract: Any
    # Nothing is public before a freeze: the tests are not hidden from anyone yet.
    projection_tree: str


def _load_proposal_for_review(store: Any, task_id: str) -> Any:
    """The stored proposal a reviewer must be shown, or a refusal (ADR 0015 decision 2).

    A reviewer's verdict binds an acceptance tree. Reviewing ``HEAD`` instead would bind the
    verdict to a tree nobody proposed, so the fallback is only allowed when no proposal was
    made at all — a corrupt or unreadable proposal is a failure, not an invitation.
    """
    proposal = store.load_proposal(task_id)
    if proposal is None:
        # A verdict must bind an acceptance tree, and without a proposal there is none to
        # bind. Falling back to HEAD here would silently restore the hole this function
        # exists to close, so a reviewer refuses instead.
        raise SessionOptionError("a reviewer needs a stored proposal to bind its verdict to")
    if proposal.proposal_id != task_id:
        raise RuntimeError("the stored proposal does not match the requested task")
    return _ReviewSubject(
        acceptance_tree=proposal.acceptance_tree,
        contract=types.SimpleNamespace(lineage_id=proposal.lineage_id),
        projection_tree=proposal.acceptance_tree,
    )


def _refuse_wrong_reviewer_tree(repo: Any, projection: Path, tree: str) -> None:
    """The projection must be exactly ``tree``: no candidate code, no extra blob.

    ``stray_hidden`` cannot be used here because a reviewer is *given* the hidden tests.
    Comparing the files actually written against the tree that was selected catches both a
    stray hidden blob and a stray implementation blob.
    """
    expected = {b.path for b in repo.blobs_under(tree, "")}
    actual = {
        str(path.relative_to(projection))
        for path in projection.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(projection).parts
    }
    if actual != expected:
        extra = sorted(actual - expected)[:5]
        missing = sorted(expected - actual)[:5]
        raise RuntimeError(
            f"the reviewer projection is not the selected tree (extra={extra}, missing={missing})"
        )


def _refuse_overlap(a: Path, b: Path) -> None:
    """Refuse two mounts where one contains the other: the second bind shadows the first."""
    if _within(a, b) or _within(b, a):
        raise SessionOptionError(f"{a} and {b} overlap; one bind would shadow the other")


def _ensure_outside(path: Path, repo: Any, what: str) -> None:
    for root in _forbidden_roots(repo):
        if _within(path, root):
            raise RuntimeError(f"{what} must live outside the trusted repository")


_RESERVED_ENV = (*profiles.BASE_ENV, EGRESS_SOCKET_ENV, EGRESS_PORT_ENV, EGRESS_READY_ENV)


def _protected_roots(
    ctx: Context, state_dir: Path | None, *explicit: Path | None
) -> tuple[Path, ...]:
    """Locations that protect hidden tests and must never be mounted, seeded or written."""
    repo: Any = ctx.repo
    return (
        *_forbidden_roots(repo),
        *repo.object_dirs(),
        store_root(repo.common_dir(), _state_home(state_dir)),
        _state_home(state_dir),
        *(path for path in explicit if path is not None),
    )


PROTECTED_WALK_ENTRIES = 2_000_000
PROTECTED_WALK_SECONDS = 60.0


def _regular_inodes(root: Path, found: set[tuple[int, int]]) -> None:
    deadline = _time.monotonic() + PROTECTED_WALK_SECONDS
    entries = 0

    def onerror(error: OSError) -> None:
        raise error

    try:
        for dirpath, dirs, names in os.walk(root, followlinks=False, onerror=onerror):
            for name in (*dirs, *names):
                entries += 1
                if entries > PROTECTED_WALK_ENTRIES:
                    raise SessionOptionError("protected inode scan entry cap reached")
                if _time.monotonic() > deadline:
                    raise SessionOptionError("protected inode scan time cap reached")
                info = os.lstat(os.path.join(dirpath, name))
                if stat.S_ISLNK(info.st_mode):
                    info = os.stat(os.path.join(dirpath, name))
                    if stat.S_ISDIR(info.st_mode):
                        raise SessionOptionError(
                            "protected directory symlinks are an unsupported layout"
                        )
                if stat.S_ISREG(info.st_mode):
                    found.add((info.st_dev, info.st_ino))
    except OSError as exc:
        raise SessionOptionError("protected inode scan cannot be inspected") from exc


def _hidden_inodes(ctx: Context, frozen: Any, state_dir: Path | None) -> frozenset[tuple[int, int]]:
    """``(st_dev, st_ino)`` of every hidden file, git object and store file.

    A hard link outside the repository carries the same inode, so checking the inode is
    the only path-independent way to keep hidden material out of a mount or a seed. Git
    objects are included because a loose object is only zlib-compressed hidden content.
    """
    repo: Any = ctx.repo
    found: set[tuple[int, int]] = set()
    for blob in frozen.hidden_blobs:
        try:
            info = os.lstat(Path(repo.root) / blob.path)
        except OSError:
            continue
        if stat.S_ISREG(info.st_mode):
            found.add((info.st_dev, info.st_ino))
    for objects in repo.object_dirs():
        _regular_inodes(objects, found)
    _regular_inodes(Path(repo.common_dir()), found)
    _regular_inodes(store_root(repo.common_dir(), _state_home(state_dir)), found)
    return frozenset(found)


def _validate_extras(
    protected: tuple[Path, ...],
    hidden_inodes: frozenset[tuple[int, int]],
    extra_read_only: Sequence[Path] | None,
    home_seed: Mapping[str, Path] | None,
    extra_env: Mapping[str, str] | None,
) -> SessionExtras:
    """Validate the opt-in mounts, seeds and env before any server or agent starts."""
    return prepare_extras(
        protected=protected,
        temp_bases=(Path(tempfile.gettempdir()),),
        reserved_env=_RESERVED_ENV,
        extra_read_only=extra_read_only,
        home_seed=home_seed,
        extra_env=extra_env,
        hidden_inodes=hidden_inodes,
        reserved_mounts=profiles.RESERVED_MOUNTS,
        sandbox_tmp=profiles.SANDBOX_TMP,
    )


def _merge_env(*groups: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
    """Concatenate env groups; a duplicate key is a bug, never an override."""
    merged = tuple(pair for group in groups for pair in group)
    keys = [key for key, _ in merged]
    if len(keys) != len(set(keys)):
        raise RuntimeError("duplicate environment variable in the agent environment")
    return merged


def _wait_for_socket(path: Path, thread: threading.Thread) -> None:
    deadline = _time.monotonic() + SESSION_START_TIMEOUT
    while _time.monotonic() < deadline:
        if not thread.is_alive():
            raise RuntimeError("verifier server exited before creating its socket")
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            _time.sleep(0.05)
            continue
        if not stat.S_ISSOCK(st.st_mode):
            raise RuntimeError("verifier socket path is not a socket")
        if st.st_uid != os.geteuid():
            raise RuntimeError("verifier socket is owned by another user")
        return
    raise RuntimeError("verifier socket did not appear")


def _read_info(read_fd: int, deadline: float) -> bytes:
    buf = bytearray()
    while True:
        remaining = deadline - _time.monotonic()
        if remaining <= 0:
            raise RuntimeError("bubblewrap startup timed out")
        ready, _, _ = select.select([read_fd], [], [], remaining)
        if not ready:
            raise RuntimeError("bubblewrap startup timed out")
        chunk = os.read(read_fd, 65536)
        if not chunk:
            return bytes(buf)
        buf.extend(chunk)
        if len(buf) > MAX_INFO_BYTES:
            raise RuntimeError("bubblewrap reported oversized startup data")


def _launch_agent(
    build_argv: Callable[[int], list[str]],
    pass_fds: tuple[int, ...],
    *,
    stdio: bool = False,
    pty_slave: int | None = None,
    log_fd: int | None = None,
    closed: bool = False,
    timeout: float = SESSION_START_TIMEOUT,
) -> tuple[subprocess.Popen[Any], int]:
    """Start bwrap with ``--info-fd`` and pin the sandbox PID namespace.

    Reading the info pipe is bounded: a child that never reports readiness, or a
    pipe that stays open, cannot hang the host. Any failure tears the child down.

    ``pty_slave`` is the opt-in pty mode (ADR 0014): the slave of a pty pair dedicated to
    this sandbox becomes the child's stdin, stdout and stderr. The caller keeps the master
    and closes the slave after this returns. ``--new-session`` stays in the profile, so the
    child cannot use ``TIOCSTI`` against the host terminal; it only ever sees this pty.
    ``log_fd`` (headless reference) sends stdin to ``/dev/null`` and both output streams to
    the host's log file, so nothing is inherited from or flushed to the operator terminal.
    ``pty_slave``, ``stdio`` and ``log_fd`` are mutually exclusive.
    """
    if sum(option is not None for option in (pty_slave, log_fd)) + int(stdio) + int(closed) > 1:
        raise ValueError("pty_slave, log_fd, closed and stdio are mutually exclusive")
    if pty_slave is not None:
        stdin = stdout = stderr = pty_slave
    elif closed:
        # Every stream detached from the host: `None` would *inherit* the operator's
        # descriptors, which is how project text reaches a session that mounts nothing.
        stdin, stdout, stderr = subprocess.DEVNULL, subprocess.DEVNULL, subprocess.DEVNULL
    elif log_fd is not None:
        stdin, stdout, stderr = subprocess.DEVNULL, log_fd, log_fd
    else:
        if stdio:
            stdin, stdout, stderr = subprocess.PIPE, subprocess.PIPE, None
        else:
            stdin, stdout, stderr = None, None, None
    read_fd, write_fd = os.pipe()
    proc: subprocess.Popen[Any] | None = None
    try:
        argv = build_argv(write_fd)
        proc = subprocess.Popen(  # noqa: S603 - argv built from validated parts
            argv,
            pass_fds=(*pass_fds, write_fd),
            close_fds=True,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            text=stdio,
        )
    except BaseException:
        os.close(read_fd)
        os.close(write_fd)
        raise
    os.close(write_fd)
    try:
        raw = _read_info(read_fd, _time.monotonic() + timeout)
    except BaseException:
        _terminate(proc)
        raise
    finally:
        try:
            os.close(read_fd)
        except OSError:
            pass
    if proc.poll() is not None:
        _terminate(proc)
        raise RuntimeError("bubblewrap exited before reporting its namespaces")
    try:
        info = json.loads(raw)
        child_pid = int(info["child-pid"])
        pidns_inode = int(info["pid-namespace"])
    except (ValueError, KeyError, TypeError) as exc:
        _terminate(proc)
        raise RuntimeError("bubblewrap did not report a PID namespace") from exc
    try:
        ns_fd = os.open(f"/proc/{child_pid}/ns/pid", os.O_RDONLY)
    except OSError as exc:
        _terminate(proc)
        raise RuntimeError("cannot pin the sandbox PID namespace") from exc
    if os.fstat(ns_fd).st_ino != pidns_inode:
        os.close(ns_fd)
        _terminate(proc)
        raise RuntimeError("sandbox PID namespace changed while starting")
    host_inode = os.stat("/proc/self/ns/pid").st_ino
    if os.fstat(ns_fd).st_ino == host_inode:
        os.close(ns_fd)
        _terminate(proc)
        raise RuntimeError("sandbox is in the host PID namespace")
    return proc, ns_fd


def _terminate(proc: subprocess.Popen[Any]) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, 15)
    except OSError:
        proc.terminate()
    try:
        proc.wait(timeout=5)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(proc.pid, 9)
    except OSError:
        proc.kill()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


@dataclass(frozen=True, slots=True)
class TrustedRuntime:
    root: Path
    site: Path
    mcp: Path


def _link_or_copy(source: str, destination: str) -> str:
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)
    return destination


def _disputes_enabled(store: Store) -> bool:
    """The host's dispute switch, read from the trusted store; any error means off."""
    try:
        return store.dispute_policy().enabled
    except Exception:  # noqa: BLE001 - fail closed: an unreadable policy hides the tool
        return False


# The only host variables a reviewer may see. A denylist answers "what did we think of?"
# and silently passes whatever is added later; the reviewer reads untrusted text and its
# verdict decides the freeze, so the list of things it may inherit should be explicit and
# short. Everything else is dropped; ``BASE_ENV`` is supplied by the sandbox builder itself.
_REVIEWER_ENV_ALLOWED = frozenset({"CHEESELESS_RUNTIME", "CHEESELESS_MCP"})


def _reviewer_env(env: Sequence[tuple[str, str]]) -> list[tuple[str, str]]:
    """``env`` reduced to the variables a reviewer needs to start its model session.

    It keeps no socket path, no bridge port, no readiness marker, no proxy variable and no
    CA path: every one of those is a route off the tests it was launched to judge (ADR 0015).
    """
    return [(k, v) for k, v in env if k in _REVIEWER_ENV_ALLOWED]


def _mcp_shim_source(
    python: str,
    site: Path,
    socket_path: Path,
    task_id: str,
    *,
    disputes: bool = False,
    role: SessionRole = SessionRole.IMPLEMENTER,
    dev: bool = False,
) -> str:
    """The staged MCP stdio shim; socket and task are quoted literals, never env.

    A client that filters ``CHEESELESS_SOCKET``/``CHEESELESS_TASK`` out of the MCP
    server environment, or that sets conflicting values, still reaches the one verifier
    UDS this session created for the one frozen task. Nothing mutable (the projection or
    the environment) can redirect it. The dispute switch is a literal too, so the
    agent cannot make ``gate_dispute`` appear; the verifier still enforces the policy.
    The role is baked the same way, but only for non-implementer roles: the implementer's
    shim stays byte-identical to the pre-role one. ``dev`` adds the one dev-only tool of
    the role (ADR 0013 ``spec_done`` for an author, ADR 0014 ``reference_done`` for a TUI
    reference); every other shim ignores it and stays byte-identical.
    """
    role_arg = "" if role is SessionRole.IMPLEMENTER else f", role={role.value!r}"
    if dev and role in (SessionRole.AUTHOR, SessionRole.REFERENCE):
        role_arg += ", dev=True"
    return (
        f"#!{python}\n"
        "import sys\n"
        f"sys.path.insert(0, {str(site)!r})\n"
        "from cheeseless.bootstrap import serve_mcp\n"
        f"raise SystemExit(serve_mcp({str(socket_path)!r}, task_id={task_id!r}, "
        f"disputes={bool(disputes)!r}{role_arg}))\n"
    )


def _stage_runtime(
    socket_path: Path,
    task_id: str,
    *,
    disputes: bool = False,
    role: SessionRole = SessionRole.IMPLEMENTER,
    dev: bool = False,
) -> TrustedRuntime:
    """Stage the agent client runtime outside the repo; the sandbox mounts it read-only.

    The sandbox mounts only this staging directory, never the repository, the venv
    path inside the repository or the trusted store. The MCP shim carries the session's
    socket and task id as literals (P11-11).
    """
    root = _private_dir("cheeseless-runtime-")
    site = root / "site"
    site.mkdir(mode=0o700)
    purelib = Path(sysconfig.get_paths()["purelib"])
    if purelib.is_dir():
        for dirpath, dirnames, filenames in os.walk(purelib):
            dirnames[:] = [d for d in dirnames if d != "__pycache__" and d != "cheeseless"]
            relative = Path(dirpath).relative_to(purelib)
            target = site / relative
            target.mkdir(mode=0o700, parents=True, exist_ok=True)
            for name in filenames:
                if (
                    name.endswith(".pth")
                    or name.startswith("__editable__")
                    or name == "direct_url.json"
                ):
                    continue
                source = Path(dirpath) / name
                if source.is_symlink() or not source.is_file():
                    continue
                _link_or_copy(str(source), str(target / name))
    package = Path(cheeseless.__file__).parent
    shutil.copytree(
        package,
        site / "cheeseless",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    bin_dir = root / "bin"
    bin_dir.mkdir(mode=0o700)
    mcp = bin_dir / "cheeseless-mcp"
    python = os.path.realpath(sys.executable)
    mcp.write_text(
        _mcp_shim_source(python, site, socket_path, task_id, disputes=disputes, role=role, dev=dev),
        encoding="utf-8",
    )
    os.chmod(mcp, 0o500)
    return TrustedRuntime(root=root, site=site, mcp=mcp)


def _bridge_command(
    python: str, ready_path: Path, egress_ready: Path, command: list[str]
) -> list[str]:
    script = (
        '"$1" -I -c "$2" & '
        'while [ ! -e "$3" ] || [ ! -e "$4" ]; do sleep 0.05; done; '
        'rm -f -- "$3" "$4"; shift 4; exec "$@"'
    )
    return [
        "/bin/sh",
        "-c",
        script,
        "sh",
        python,
        BRIDGE_BOOT,
        str(ready_path),
        str(egress_ready),
        *command,
    ]


def _wait_for_bridge(egress_ready: Path, proc: subprocess.Popen[Any]) -> None:
    deadline = _time.monotonic() + SESSION_START_TIMEOUT
    while _time.monotonic() < deadline:
        if egress_ready.exists():
            return
        if proc.poll() is not None:
            _terminate(proc)
            raise RuntimeError("the in-sandbox egress bridge exited before readiness")
        _time.sleep(0.02)
    _terminate(proc)
    raise RuntimeError("the in-sandbox egress bridge did not become ready")


def _ready_command(ready_path: Path, command: list[str]) -> list[str]:
    script = 'while [ ! -e "$1" ]; do sleep 0.05; done; rm -f -- "$1"; shift; exec "$@"'
    return ["/bin/sh", "-c", script, "sh", str(ready_path), *command]


def serve_uds(
    socket_path: str | Path | None = None,
    *,
    state_dir: Path | None = None,
    cwd: Path | None = None,
    task_id: str | None = None,
    worktree: Path | None = None,
    profile: str = "local",
    timeout: float = 300.0,
    tool_version: str = "0.1.0",
    expected_pidns_inode: int | None = None,
    expected_pidns_fd: int | None = None,
    namespace_fd: int | None = None,
    role: str = "implementer",
) -> int:
    ctx = build_context(state_dir, cwd)
    if task_id is None:
        task_id = ctx.store.active_task_id()
    if worktree is None:
        worktree = ctx.repo.root
    dir_owned = False
    if socket_path is None:
        directory = _private_dir("cheeseless-uds-")
        socket_path = directory / "verifier.sock"
        dir_owned = True
    session_id, binding = (
        (None, None) if task_id is None else ctx.store.register_session(task_id, role)
    )
    session = SessionConfig(
        task_id=task_id,
        worktree=worktree,
        profile=profile,
        pool=_pool(profile),
        timeout=timeout,
        tool_version=tool_version,
        repo=ctx.repo,
        store=ctx.store,
        runner=ctx.runner,
        sandbox=ctx.sandbox,
        dlp=scanner_for(ctx.store, task_id),
        expected_pidns_inode=expected_pidns_inode,
        expected_pidns_fd=expected_pidns_fd,
        namespace_fd=namespace_fd,
        role=role,
        session_id=session_id,
        generation=None if binding is None else binding.generation,
        author_sandbox=ctx.sandbox if role == "author" else None,  # type: ignore[arg-type]
    )
    server = UdsVerifierServer(socket_path, session, socket_dir_owned=dir_owned)
    try:
        server.serve_forever()
    finally:
        session.close_namespace_fds()
        if session_id is not None:
            _end_session_reported(ctx.store, session_id, task_id)
    return 0


def serve_mcp(
    uds_socket: str | Path | None,
    *,
    task_id: str | None = None,
    timeout: float = 10.0,
    disputes: bool = False,
    author: bool = False,
    role: str = "implementer",
    dev: bool = False,
) -> int:
    """MCP stdio server. It needs only the verifier socket and task, never a store."""
    if uds_socket is None:
        raise ValueError("uds_socket is required")
    bound = parse_role(role)
    client = UdsVerifierClient(uds_socket, task_id=task_id, timeout=timeout, role=bound.value)
    run_stdio(build_mcp_server(client, disputes=disputes, author=author, role=bound, dev=dev))
    return 0


PRE_FREEZE_TASK = "0" * 64
"""Internal stand-in task id for an author or reference session before any freeze."""


def _absolute_projection(path: Path | None) -> Path | None:
    """Anchor a relative ``--projection`` at the cwd (S48).

    Lexical only: symlinks are not resolved, so the host's own refusal of a symlinked
    projection path still sees the link the operator named."""
    if path is None or path.is_absolute():
        return path
    return Path.cwd() / path


def _run_session_command(args: Any, agent: list[str] | None) -> int:
    """Map ``session`` options to a host session; usage and validation errors exit 2."""
    try:
        agent, agent_model = resolve_cli_launch(getattr(args, "launcher", None), agent)
        if not agent:
            raise SessionOptionError("an agent command is required after --, or --launcher NAME")
        env = dict(check_extra_env(dict(parse_key_value(i) for i in args.env), _RESERVED_ENV))
        seeds = dict(parse_seed_spec(item) for item in args.home_seed)
        policy = load_egress_policy(args.egress_policy) if args.egress_policy else None
        return launch_host_session(
            state_dir=args.state_dir,
            task_id=args.task,
            profile=args.profile,
            timeout=args.timeout,
            agent_command=agent,
            agent_stdio=False,
            egress_policy=policy,
            extra_read_only=tuple(args.ro),
            home_seed=seeds,
            extra_env=env,
            evidence_dir=args.evidence_dir,
            projection_dir=_absolute_projection(args.projection),
            role=args.role,
            agent_model=agent_model,
        )
    except (SessionOptionError, PolicyError) as exc:
        print(f"session: {exc}", file=sys.stderr)
        return 2
    except (ValueError, RuntimeError) as exc:
        # Startup refusals (no active task, stray hidden material, sandbox checks)
        # are operator errors, not crashes; teardown already ran in host_session.
        print(f"session: {exc}", file=sys.stderr)
        return 2


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    head, agent = split_agent_argv(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(head)
    if args.command == "strength-review":
        # `main` split the argv at `--`, so the reviewer command is in `agent`, not in the
        # args the parser produced. Dropping it made `--run -- <agent>` report the command as
        # missing: the documented form could never work.
        args.reviewer_command = list(agent or [])
    if args.command is None:
        parser.print_help()
        return 2
    if args.command == "pane-relay":
        from cheeseless.adapters.inbound.cli.pane_client import run_pane_client  # noqa: PLC0415

        return run_pane_client(args.socket)
    if args.command == "dev-reference":
        from cheeseless.bootstrap_reference import run_reference_command  # noqa: PLC0415

        return run_reference_command(args, agent)
    if args.command == "dev":
        from cheeseless.bootstrap_dev import run_dev_command  # noqa: PLC0415

        return run_dev_command(args, agent)
    if args.command == "serve-uds":
        return serve_uds(
            socket_path=getattr(args, "socket", None),
            state_dir=getattr(args, "state_dir", None),
            task_id=getattr(args, "task", None),
            worktree=getattr(args, "worktree", None),
            profile=getattr(args, "profile", "local"),
            timeout=getattr(args, "timeout", 300.0),
            tool_version=getattr(args, "tool_version", "0.1.0"),
            expected_pidns_fd=getattr(args, "pidns_fd", None),
            role=getattr(args, "role", "implementer"),
        )
    if args.command == "serve-mcp":
        return serve_mcp(
            uds_socket=getattr(args, "uds_socket", None),
            task_id=getattr(args, "task", None),
            timeout=getattr(args, "timeout", 10.0),
            disputes=bool(getattr(args, "disputes", False)),
            author=bool(getattr(args, "author", False)),
            role=getattr(args, "role", "implementer"),
        )
    if args.command == "session":
        return _run_session_command(args, agent)
    if args.command in (
        "ci-export",
        "ci-bundle-check",
        "ci-judge",
        "ci-budget-init",
        "ci-branch",
        "ci-route",
        "ci-gate",
    ):
        from cheeseless.bootstrap_ci import run as run_ci  # noqa: PLC0415 - avoids a cycle

        return run_ci(args)
    ctx = build_context(getattr(args, "state_dir", None))
    return dispatch(args, ctx)


def _end_session_reported(store: Any, session_id: str, task_id: str | None) -> None:
    """Teardown must not raise, but a failed ``end_session`` is reported, never swallowed.

    A stale record stays unreachable (the id is random), so this is non-fatal; the operator
    still learns that the state directory could not be cleaned.
    """
    try:
        store.end_session(session_id)
    except Exception as exc:  # noqa: BLE001 - teardown is best effort, but loud
        print(
            f"session teardown: end_session failed for session {session_id} "
            f"(task {task_id}): {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )


def _projection_tree(repo: Any, frozen: Any, role: SessionRole) -> str:
    """The tree a session's projection repo is built from.

    A reference session never sees a test, so its tree is cut down to the allowlist before the
    projection's ``.git`` is written, before or after a freeze. A reviewer (ADR 0015) sees every
    test and nothing else: the candidate's code must not reach a session whose verdict decides
    whether the suite freezes.
    """
    tree: str = frozen.projection_tree if frozen is not None else repo.tree_of(repo.head_commit())
    if role is SessionRole.REFERENCE:
        return reference_tree(repo, tree)
    if role is SessionRole.REVIEWER:
        # ADR 0015 decision 2: the verdict names an acceptance tree, so the reviewer must be
        # shown *that* tree. `projection_tree` is the wrong one on both sides of a freeze:
        # before it, otherwise; after it, it is the public projection with the hidden tests
        # taken out, so the reviewer would judge a suite that is not the frozen one.
        if frozen is None:
            # Both a frozen task and a stored proposal answer "which tree must this verdict be
            # about", and `host_session` refuses a reviewer without one. Reaching here would
            # mean a caller bypassed that refusal, so this fails rather than guessing a tree.
            raise SessionOptionError("a reviewer needs a proposal or a frozen task to review")
        return reviewer_tree_of(repo, frozen.acceptance_tree)
    return tree


def _close_pins(pins: list[tuple[int, str]]) -> None:
    """Close and forget every pinned fd (idempotent: the list is emptied)."""
    while pins:
        fd, _ = pins.pop()
        with contextlib.suppress(OSError):
            os.close(fd)


def _session_argv(
    bound: SessionRole,
    *,
    bwrap: str,
    projection: Path,
    home: Path,
    sockets: Sequence[Path],
    read_only: Sequence[Path],
    acceptance_fd: int | None,
    acceptance_mode: str,
    pinned_read_only: Sequence[tuple[int, str]],
    env: Sequence[tuple[str, str]],
    command: Sequence[str],
    seccomp_fd: int | None,
    info_fd: int,
) -> list[str]:
    """Choose the argv for one session. The role decides which guarantees apply.

    Extracted so the choice itself is testable: it used to live inside `host_session`, where
    nothing could check that a reviewer really got the reviewer profile rather than the
    agent one, which is writable and networked.
    """
    if bound is SessionRole.REVIEWER:
        # ADR 0015: no sockets and no egress. It judges the tests it was given; a channel to
        # the host or the network would let something else judge for it.
        return profiles.reviewer(
            bwrap=bwrap,
            projection=projection,
            home=home,
            read_only=read_only,
            acceptance_fd=acceptance_fd,
            pinned_read_only=pinned_read_only,
            env=_reviewer_env(env),
            command=command,
            seccomp_fd=seccomp_fd,
            info_fd=info_fd,
        )
    return profiles.agent(
        bwrap=bwrap,
        projection=projection,
        sockets=sockets,
        home=home,
        read_only=read_only,
        acceptance_fd=acceptance_fd,
        acceptance_mode=acceptance_mode,
        pinned_read_only=pinned_read_only,
        env=env,
        command=command,
        seccomp_fd=seccomp_fd,
        info_fd=info_fd,
    )


@contextmanager
def host_session(
    *,
    state_dir: Path | None = None,
    cwd: Path | None = None,
    task_id: str | None = None,
    projection_dir: Path | None = None,
    socket_dir: Path | None = None,
    profile: str = "local",
    timeout: float = 300.0,
    tool_version: str = "0.1.0",
    agent_command: list[str] | None = None,
    agent_stdio: bool = False,
    brief_file: str | None = None,
    egress_policy: EgressPolicy | None = None,
    egress_upstream_trusted_ca: Path | None = None,
    egress_insecure_upstream: bool = False,
    extra_read_only: Sequence[Path] | None = None,
    home_seed: Mapping[str, Path] | None = None,
    extra_env: Mapping[str, str] | None = None,
    evidence_dir: Path | None = None,
    role: SessionRole | str = SessionRole.IMPLEMENTER,
    dev_lineage: str | None = None,
    new_reference: NewReferenceAttempt | None = None,
    on_reference_stop: Callable[[str], None] | None = None,
    agent_pty: tuple[int, int] | None = None,
    log_file: Path | None = None,
    agent_model: str = "",
) -> Iterator[dict[str, Any]]:
    """Run one isolated agent session with authenticated verifier and DLP egress.

    The projection is the only writable project tree inside the sandbox; the original
    repository, its git directory and the trusted store are never mounted. A staged,
    read-only client runtime is mounted outside the projection. The sandbox PID
    namespace is pinned before the agent may connect and kept open until teardown.
    Egress is an empty netns plus one UDS, exposed to the agent only as loopback TCP.

    ``extra_read_only``, ``home_seed`` and ``extra_env`` are opt-in and validated against
    the protected locations (and their ancestors) before anything starts; ``extra_env``
    is additive and can never override a variable the session sets.

    ``role`` is bound by the host at launch and recorded in the trusted store; the agent
    cannot choose or change it. An implementer needs a FROZEN task and keeps the read-only
    acceptance mount. An author or reference session works on the pre-freeze spec tree
    (HEAD): the author's acceptance tree stays writable, the reference's is hidden.

    ``dev_lineage`` marks an author session of an open ``cheeseless dev`` session
    (ADR 0013): only then does the agent get the ``spec_done`` tool and command.

    ``new_reference`` (ADR 0014) binds a reference sandbox to one stored attempt. A TUI
    attempt needs ``agent_pty`` (rows, cols): the child gets the pty slave and the host
    starts the relay on the master, exposed as ``info["relay"]``. A headless attempt needs
    ``log_file`` instead; the relay and the ``reference_done`` tool do not exist for it.
    ``on_reference_stop`` is the host seam the verifier calls once after ``running`` became
    ``stopping``.
    """
    bound = parse_role(role) if isinstance(role, str) else role
    ctx = build_context(state_dir, cwd)
    if dev_lineage is not None:
        dev_lineage = ids.lineage_id(dev_lineage)
        if bound is not SessionRole.AUTHOR:
            raise SessionOptionError("only an author session can belong to a dev session")
        if cast(DevSessionStore, ctx.store).dev_state(dev_lineage) is None:
            raise SessionOptionError(f"no dev session is open for {dev_lineage}")
    if new_reference is not None:
        if bound is not SessionRole.REFERENCE:
            raise SessionOptionError("only a reference session can run a reference attempt")
        if dev_lineage is not None:
            raise SessionOptionError("a dev author session cannot be a reference attempt")
        tui = new_reference.mode is ReferenceMode.TUI
        if tui != (agent_pty is not None):
            raise SessionOptionError(
                "a TUI attempt needs a pty and a headless one must not have one"
            )
        if not tui and log_file is None:
            raise SessionOptionError("a headless attempt needs a host log file")
        if tui and log_file is not None:
            raise SessionOptionError("a TUI attempt writes no host log file")
        if agent_stdio:
            raise SessionOptionError("a reference attempt cannot use stdio")
    elif agent_pty is not None or log_file is not None:
        raise SessionOptionError("a pty or log file is only for a reference attempt")
    reference_tui = new_reference is not None and new_reference.mode is ReferenceMode.TUI
    dev = dev_lineage is not None or reference_tui
    if task_id is None:
        task_id = ctx.store.active_task_id()
    if task_id is None and bound in (SessionRole.AUTHOR, SessionRole.REFERENCE):
        # Pre-freeze roles work on HEAD and have no task yet (S49); the implementer and the
        # reviewer still need a real one and fail closed below.
        task_id = PRE_FREEZE_TASK
    if task_id is None:
        raise ValueError("no active task")
    if bound is SessionRole.IMPLEMENTER:
        frozen: Any = ctx.store.load_task(task_id)[0]
    elif bound is SessionRole.REVIEWER:
        # ADR 0015 decision 2: the verdict names an acceptance tree, so a reviewer must be
        # shown the stored proposal's snapshot. Falling back on any StoreError would let a
        # corrupt record quietly downgrade the session to "review HEAD instead", which is the
        # one thing the record exists to prevent.
        frozen = _load_proposal_for_review(ctx.store, task_id)
    else:
        try:
            frozen = ctx.store.load_task(task_id)[0]
        except StoreError:
            frozen = None  # pre-freeze: there are no hidden blobs to withhold yet
    protected = _protected_roots(ctx, state_dir, projection_dir, socket_dir)
    hidden_inodes: frozenset[tuple[int, int]] = frozenset()
    if (extra_read_only or home_seed) and frozen is not None:
        hidden_inodes = _hidden_inodes(ctx, frozen, state_dir)
    extras = _validate_extras(protected, hidden_inodes, extra_read_only, home_seed, extra_env)

    projection_owned = projection_dir is None
    dir_owned = socket_dir is None
    home_dir: Path | None = None
    socket_dir_path: Path | None = None
    egress_dir: Path | None = None
    runtime: TrustedRuntime | None = None
    server: UdsVerifierServer | None = None
    session: SessionConfig | None = None
    proxy: EgressProxy | None = None
    supervisor: EgressSupervisor | None = None
    thread: threading.Thread | None = None
    proc: subprocess.Popen[Any] | None = None
    relay: PaneRelay | None = None
    reference_attempt: ReferenceAttempt | None = None
    pty_master: int | None = None
    pty_slave: int | None = None
    log_fd: int | None = None
    ns_fd: int | None = None
    seccomp_fd: int | None = None
    acceptance_fd: int | None = None
    contract_pins: list[tuple[int, str]] = []
    ready_path: Path | None = None
    egress_ready: Path | None = None
    blocked = threading.Event()
    holder: dict[str, subprocess.Popen[Any] | None] = {"proc": None}
    try:
        if evidence_dir is not None:
            ensure_outside_all(evidence_dir, protected, "evidence directory")
            if os.path.lexists(evidence_dir):
                raise SessionOptionError("evidence directory must not already exist")
        sandbox = ctx.sandbox
        if not isinstance(sandbox, BwrapSandbox):
            raise RuntimeError("host sessions require the bubblewrap sandbox")
        bwrap = sandbox.binary()
        fingerprints = ctx.store.load_fingerprints(task_id)
        if fingerprints is None and bound is SessionRole.IMPLEMENTER:
            raise RuntimeError("the frozen task has no DLP fingerprints; re-freeze it")
        if fingerprints is None:
            # Pre-freeze there is nothing hidden to fingerprint; the egress policy still applies.
            fingerprints = Fingerprints(task_id=task_id, k=8, kgrams=frozenset(), literals=())
        policy = egress_policy or EgressPolicy(allowed_hosts=frozenset())
        identity_markers = {
            marker
            for marker in (
                frozen.contract.lineage_id if frozen is not None else "",
                Path(ctx.repo.root).name.lower(),
            )
            if marker
        }
        policy = replace(
            policy,
            denied_markers=tuple(sorted(set(policy.denied_markers) | identity_markers)),
        )

        if projection_dir is None:
            projection_dir = Path(tempfile.mkdtemp(prefix="cheeseless-projection-"))
            os.rmdir(projection_dir)
        else:
            if os.path.lexists(projection_dir):
                if projection_dir.is_symlink() or not projection_dir.is_dir():
                    raise RuntimeError("projection path must be a directory or absent")
                if any(projection_dir.iterdir()):
                    raise RuntimeError("projection path must be empty")
                os.rmdir(projection_dir)
        _ensure_outside(projection_dir, ctx.repo, "projection")

        home_dir = _private_dir("cheeseless-home-")
        if bound is SessionRole.REVIEWER and (extras.seeds or extras.read_only):
            # ADR 0015: a home seed or an extra read-only mount is an unfiltered route for
            # candidate content into the reviewer's view. Refusing is the only honest answer;
            # filtering would leave the question of what was missed.
            raise SessionOptionError("a reviewer session accepts no home seeds or extra mounts")
        if brief_file is not None:
            # The reviewer's instruction comes from the host, and it is written into the
            # reviewer's own HOME rather than passed in argv: argv is visible to the process
            # it describes and can carry the author's text.
            if bound is not SessionRole.REVIEWER:
                raise SessionOptionError("only a reviewer session takes a brief")
            (home_dir / "BRIEF.md").write_text(brief_file)
        if bound is SessionRole.REVIEWER and agent_stdio:
            # Inherited stdio is a stream of content that is not in the snapshot the verdict
            # binds. A review whose evidence came from outside its tree is not evidence.
            raise SessionOptionError("a reviewer session cannot inherit the operator's stdio")
        if bound is SessionRole.REVIEWER:
            # A writable bind laid over a read-only one shadows it. The builder cannot see
            # whether HOME is an ancestor of the projection, so the caller that picks both
            # paths is the place to refuse the overlap rather than a comment in the builder.
            _refuse_overlap(home_dir, projection_dir)
        seed_home(home_dir, dev_home_entries(bound, dev=dev))
        seed_home(home_dir, extras.seeds)  # an operator seed can never replace a dev entry
        if socket_dir is None:
            socket_dir_path = _private_dir("cheeseless-uds-")
        else:
            socket_dir_path = socket_dir
        _ensure_outside(socket_dir_path, ctx.repo, "socket directory")
        if _within(socket_dir_path, projection_dir):
            raise RuntimeError("socket directory must live outside the projection")
        socket_path = socket_dir_path / "verifier.sock"

        egress_dir = _private_dir("cheeseless-egress-")
        _ensure_outside(egress_dir, ctx.repo, "egress directory")
        if _within(egress_dir, projection_dir) or _within(egress_dir, socket_dir_path):
            raise RuntimeError("egress directory must be separate from the session paths")
        egress_socket = egress_dir / "egress.sock"
        runtime = _stage_runtime(
            socket_path, task_id, disputes=_disputes_enabled(ctx.store), role=bound, dev=dev
        )

        tree = _projection_tree(ctx.repo, frozen, bound)
        ctx.repo.create_projection(tree, projection_dir)
        # ADR 0015: the reviewer is the one session *meant* to hold the hidden tests, so the
        # leak check that protects every other projection would abort it before it launched.
        # It gets the exact-tree check instead, which catches a stray blob just as firmly.
        if bound is SessionRole.REVIEWER:
            _refuse_wrong_reviewer_tree(ctx.repo, projection_dir, tree)
        else:
            strays = (
                ctx.repo.stray_hidden(projection_dir, frozen.hidden_blobs)
                if frozen is not None
                else []
            )
            if strays:
                raise RuntimeError(f"stray hidden material in the projection: {strays}")

        # The model is part of what the host launched, so it is recorded with the session
        # rather than being re-declared later by whoever records a verdict.
        session_id, binding = ctx.store.register_session(task_id, bound.value, agent_model)
        session = SessionConfig(
            task_id=task_id,
            worktree=projection_dir,
            profile=profile,
            pool=_pool(profile),
            timeout=timeout,
            tool_version=tool_version,
            repo=ctx.repo,
            store=ctx.store,
            runner=ctx.runner,
            sandbox=sandbox,
            dlp=scanner_for(ctx.store, task_id),
            role=bound.value,
            author_sandbox=sandbox if bound is SessionRole.AUTHOR else None,
            session_id=session_id,
            generation=binding.generation,
            dev_lineage=dev_lineage,
            dev_reference=None if new_reference is None else new_reference.attempt_id,
            on_reference_stop=None if new_reference is None else on_reference_stop,
        )
        if new_reference is not None:
            reference_attempt = ReferenceAttempt(
                new_reference.attempt_id,
                session_id,
                binding.generation,
                new_reference.spec_digest,
                new_reference.mode,
                False,
                ReferenceState.RUNNING,
                lineage_id=new_reference.lineage_id,
            )
            cast(ReferenceAttemptStore, ctx.store).save_reference_attempt(reference_attempt)
        saved_attempt = reference_attempt

        def on_attach() -> None:
            """Record that the pane client reached the relay; evidence only."""
            attempt = saved_attempt
            if attempt is None:
                return
            owner = AttemptOwner(attempt.session_id, attempt.generation, attempt.mode)
            cast(ReferenceAttemptStore, ctx.store).bind_reference_attempt(
                attempt.attempt_id, owner=owner, attached=True
            )

        server = UdsVerifierServer(socket_path, session, socket_dir_owned=dir_owned)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        _wait_for_socket(socket_path, thread)

        def teardown() -> None:
            held = holder["proc"]
            if held is not None:
                _terminate(held)
            blocked.set()

        supervisor = EgressSupervisor(
            store=ctx.store,
            repo=ctx.repo,
            worktree=projection_dir,
            task_id=task_id,
            profile=profile,
            tool_version=tool_version,
            teardown=teardown,
        )

        def on_hit(host: str, rule: str, fingerprint_id: str) -> None:
            try:
                supervisor.block(EgressBlock(host, rule, fingerprint_id))
            except Exception:  # noqa: BLE001 - never let a proxy thread die open
                blocked.set()

        proxy = EgressProxy(
            ProxyConfig(
                policy=policy,
                confdir=egress_dir / "ca",
                socket_path=egress_socket,
                upstream_trusted_ca=egress_upstream_trusted_ca,
                insecure_upstream=egress_insecure_upstream,
            ),
            fingerprints,
            on_hit=on_hit,
            on_audit=StoreAuditSink(ctx.store, task_id).record,
        )
        proxy.start()
        ca_cert = str(proxy.ca_cert_path)
        proxy_url = f"http://{BRIDGE_HOST}:{BRIDGE_PORT}"

        agent_env = (
            ("CHEESELESS_SOCKET", str(socket_path)),
            ("CHEESELESS_TASK", task_id),
            ("CHEESELESS_RUNTIME", str(runtime.site)),
            ("CHEESELESS_MCP", str(runtime.mcp)),
            (EGRESS_SOCKET_ENV, str(egress_socket)),
            (EGRESS_PORT_ENV, str(BRIDGE_PORT)),
            ("HTTPS_PROXY", proxy_url),
            ("HTTP_PROXY", proxy_url),
            ("ALL_PROXY", proxy_url),
            ("NO_PROXY", ""),
            ("https_proxy", proxy_url),
            ("http_proxy", proxy_url),
            ("all_proxy", proxy_url),
            ("no_proxy", ""),
            ("CODEX_CA_CERTIFICATE", ca_cert),
            ("NODE_EXTRA_CA_CERTS", ca_cert),
            ("SSL_CERT_FILE", ca_cert),
        )

        egress_ready = projection_dir / f".cheeseless-egress-{secrets.token_hex(8)}"
        ready_path = projection_dir / f".cheeseless-ready-{secrets.token_hex(8)}"
        egress_ready_env = ((EGRESS_READY_ENV, str(egress_ready)),)
        python = os.path.realpath(sys.executable)
        python_root = Path(python).parent.parent
        if any(_within(python_root, root) for root in _forbidden_roots(ctx.repo)):
            raise RuntimeError("the client interpreter must live outside the repository")
        agent_args = list(agent_command) if agent_command is not None else list(_DEFAULT_AGENT)
        if bound is SessionRole.REVIEWER:
            # ADR 0015: no egress bridge, so no wrapper and no readiness handshake. The
            # reviewer is launched exactly as given; waiting for a bridge that was never
            # created would hang the session until the timeout.
            command = agent_args
        else:
            command = _bridge_command(python, ready_path, egress_ready, agent_args)
        launch_command = command
        if new_reference is not None and new_reference.mode is ReferenceMode.TUI:
            # --new-session leaves the child with no controlling tty, so a resize on the
            # master would not reach it (D14-3b-2). The trusted wrapper restores that.
            launch_command = pty_launch.with_controlling_tty(python, command)
        if log_file is not None:
            _ensure_outside(log_file, ctx.repo, "reference log")
            if _within(log_file, projection_dir) or _within(projection_dir, log_file):
                raise SessionOptionError("the reference log must live outside the projection")
            log_fd = os.open(
                log_file,
                os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
            )
            if not stat.S_ISREG(os.fstat(log_fd).st_mode):
                raise SessionOptionError("the reference log must be a regular file")

        seccomp_fd = seccomp.program_fd()
        mount_mode = acceptance_mount(bound)
        if mount_mode == "ro":
            acceptance_fd = profiles.open_acceptance(projection_dir)
        elif mount_mode == "contract":
            contract_pins = profiles.open_contract_files(projection_dir)
        pinned_acceptance = acceptance_fd
        env = _merge_env(agent_env, egress_ready_env, extras.env)

        pinned_read_only = (
            *((mount.fd, str(mount.path)) for mount in extras.read_only),
            *((fd, dest) for fd, dest in contract_pins),
        )

        def build_argv(info_fd: int) -> list[str]:
            return _session_argv(
                bound,
                bwrap=bwrap,
                projection=projection_dir,
                home=home_dir,
                sockets=[socket_path, egress_socket],
                read_only=[runtime.root, python_root, proxy.ca_cert_path],
                acceptance_fd=pinned_acceptance,
                acceptance_mode=mount_mode,
                pinned_read_only=pinned_read_only,
                env=env,
                command=launch_command,
                seccomp_fd=seccomp_fd,
                info_fd=info_fd,
            )

        mount_fds = (*(mount.fd for mount in extras.read_only), *(fd for fd, _ in contract_pins))
        if agent_pty is not None:
            pty_master, pty_slave = pty_launch.open_pty(*agent_pty)
        proc, ns_fd = _launch_agent(
            build_argv,
            (*mount_fds, seccomp_fd, *(() if acceptance_fd is None else (acceptance_fd,))),
            stdio=agent_stdio,
            pty_slave=pty_slave,
            log_fd=log_fd,
            # A reviewer gets no host descriptor at all (ADR 0015): its evidence is the tree
            # it was given, and inherited stdin is evidence from outside it.
            closed=bound is SessionRole.REVIEWER,
        )
        if pty_slave is not None:
            os.close(pty_slave)
            pty_slave = None
        if log_fd is not None:
            os.close(log_fd)
            log_fd = None
        holder["proc"] = proc
        os.close(seccomp_fd)
        seccomp_fd = None
        if acceptance_fd is not None:
            os.close(acceptance_fd)
            acceptance_fd = None
        _close_pins(contract_pins)
        session.expected_pidns_fd = ns_fd
        if bound is not SessionRole.REVIEWER:
            _wait_for_bridge(egress_ready, proc)
            ready_path.write_text("ready\n", encoding="ascii")
        if pty_master is not None:
            relay = PaneRelay(pty_master, on_attach=on_attach)
            relay.start()
            pty_master = None  # the relay owns the master now

        yield {
            "socket_path": socket_path,
            "egress_socket_path": egress_socket,
            "egress_ca_cert": proxy.ca_cert_path,
            "projection_dir": projection_dir,
            "home_dir": home_dir,
            "runtime_dir": runtime.site,
            "namespace_fd": ns_fd,
            "agent_proc": proc,
            "server": server,
            "blocked": blocked,
            "protected_roots": protected,
            "relay": relay,
            "reference_attempt": reference_attempt,
            # What the host recorded for this session, including the model it launched.
            "binding": binding,
        }
    finally:
        for path in (ready_path, egress_ready):
            if path is not None:
                try:
                    path.unlink()
                except OSError:
                    pass
        if proxy is not None:
            proxy.stop()
        if proc is not None:
            _terminate(proc)
        holder["proc"] = None
        if relay is not None:
            relay.close()
            relay = None
        for leftover_fd in (pty_slave, pty_master, log_fd):
            if leftover_fd is not None:
                try:
                    os.close(leftover_fd)
                except OSError:
                    pass
        if server is not None:
            server.shutdown()
        if thread is not None:
            thread.join(timeout=SESSION_START_TIMEOUT)
        if session is not None:
            session.close_namespace_fds()
            if session.session_id is not None:
                _end_session_reported(ctx.store, session.session_id, task_id)
        ns_fd = None
        _close_pins(contract_pins)
        for leftover in (seccomp_fd, acceptance_fd):
            if leftover is not None:
                try:
                    os.close(leftover)
                except OSError:
                    pass
        if home_dir is not None:
            force_remove(home_dir)
        if egress_dir is not None:
            force_remove(egress_dir)
        if runtime is not None:
            force_remove(runtime.root)
        if dir_owned and socket_dir_path is not None:
            force_remove(socket_dir_path)
        if projection_owned and projection_dir is not None:
            force_remove(projection_dir)
        for mount in extras.read_only:
            try:
                os.close(mount.fd)
            except OSError:
                pass


def _capture_evidence(info: Mapping[str, Any], dest: Path) -> None:
    """Copy the sandbox HOME and list the projection while both still exist."""
    try:
        copy_evidence(
            Path(info["home_dir"]),
            dest,
            protected=info["protected_roots"],
        )
        write_projection_list(Path(info["projection_dir"]), dest)
    except (OSError, SessionOptionError) as exc:
        print(f"evidence capture failed: {type(exc).__name__}", file=sys.stderr)


TERMINATING_SIGNALS = (signal.SIGTERM, signal.SIGHUP)


class _Terminated(BaseException):
    """Raised from a signal handler so ``host_session`` teardown still runs."""

    def __init__(self, signum: int) -> None:
        super().__init__(signum)
        self.signum = signum


@contextmanager
def _signals_raise(signums: Sequence[int] = TERMINATING_SIGNALS) -> Iterator[None]:
    """Turn SIGTERM/SIGHUP into ``_Terminated`` once; later signals are ignored.

    The default disposition kills the host without unwinding, which would leave
    the throwaway HOME (seeded credentials), the egress CA key and the sockets
    behind. Only the main thread may install handlers.
    """
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    fired = False

    def handler(signum: int, _frame: object) -> None:
        nonlocal fired
        if fired:
            return
        fired = True
        raise _Terminated(signum)

    previous = {signum: signal.signal(signum, handler) for signum in signums}
    try:
        yield
    finally:
        for signum, old in previous.items():
            signal.signal(signum, old)


def launch_host_session(*args: Any, evidence_dir: Path | None = None, **kwargs: Any) -> int:
    """Run a host session until the agent exits or the operator interrupts."""
    try:
        with _signals_raise():
            return _run_host_session(*args, evidence_dir=evidence_dir, **kwargs)
    except _Terminated as exc:
        print(f"session terminated by signal {exc.signum}; cleaned up", file=sys.stderr)
        return 128 + exc.signum


def _run_host_session(*args: Any, evidence_dir: Path | None, **kwargs: Any) -> int:
    blocked = threading.Event()
    code = 0
    with host_session(*args, evidence_dir=evidence_dir, **kwargs) as info:
        proc: subprocess.Popen[Any] = info["agent_proc"]
        blocked = info["blocked"]
        try:
            code = proc.wait()
        except KeyboardInterrupt:
            code = 0
        if evidence_dir is not None:
            _capture_evidence(info, evidence_dir)
    if blocked.is_set():
        return 1
    return 0 if code == 0 else (code if code > 0 else 1)


serve_host_session = launch_host_session
serve_session = launch_host_session
launch_session = launch_host_session
run_session = launch_host_session

__all__ = [
    "Context",
    "build_context",
    "host_session",
    "launch_host_session",
    "main",
    "serve_mcp",
    "serve_uds",
]
