"""Composition root: wires adapters to ports; may import every layer."""

from __future__ import annotations

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
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

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
from cheeseless.adapters.outbound.egress.bridge import (
    BRIDGE_HOST,
    BRIDGE_PORT,
    EGRESS_PORT_ENV,
    EGRESS_READY_ENV,
    EGRESS_SOCKET_ENV,
)
from cheeseless.adapters.outbound.egress.proxy import EgressProxy, ProxyConfig
from cheeseless.adapters.outbound.git.repository import GitRepository
from cheeseless.adapters.outbound.runner.pytest_runner import PytestRunner
from cheeseless.adapters.outbound.sandbox import profiles, seccomp
from cheeseless.adapters.outbound.sandbox.bwrap import BwrapSandbox
from cheeseless.adapters.outbound.sandbox.launch import force_remove
from cheeseless.adapters.outbound.store.file_store import FileStore, store_root
from cheeseless.app.audit import StoreAuditSink
from cheeseless.app.dlp import scanner_for
from cheeseless.app.egress_supervisor import EgressBlock, EgressSupervisor
from cheeseless.domain.budget import Pool
from cheeseless.domain.egress_policy import EgressPolicy, PolicyError
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
    return Context(repo=repo, runner=PytestRunner(), sandbox=BwrapSandbox(), store=store)


def _pool(profile: str) -> Pool:
    return Pool.CI if profile == "ci" else Pool.LOCAL


def _private_dir(prefix: str) -> Path:
    path = Path(tempfile.mkdtemp(prefix=prefix))
    os.chmod(path, stat.S_IRWXU)
    return path


def _forbidden_roots(repo: Any) -> tuple[Path, ...]:
    return (Path(repo.root), Path(repo.common_dir()))


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
    timeout: float = SESSION_START_TIMEOUT,
) -> tuple[subprocess.Popen[Any], int]:
    """Start bwrap with ``--info-fd`` and pin the sandbox PID namespace.

    Reading the info pipe is bounded: a child that never reports readiness, or a
    pipe that stays open, cannot hang the host. Any failure tears the child down.
    """
    read_fd, write_fd = os.pipe()
    proc: subprocess.Popen[Any] | None = None
    try:
        argv = build_argv(write_fd)
        proc = subprocess.Popen(  # noqa: S603 - argv built from validated parts
            argv,
            pass_fds=(*pass_fds, write_fd),
            close_fds=True,
            stdin=subprocess.PIPE if stdio else None,
            stdout=subprocess.PIPE if stdio else None,
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


def _mcp_shim_source(python: str, site: Path, socket_path: Path, task_id: str) -> str:
    """The staged MCP stdio shim; socket and task are quoted literals, never env.

    A client that filters ``CHEESELESS_SOCKET``/``CHEESELESS_TASK`` out of the MCP
    server environment, or that sets conflicting values, still reaches the one verifier
    UDS this session created for the one frozen task. Nothing mutable (the projection or
    the environment) can redirect it.
    """
    return (
        f"#!{python}\n"
        "import sys\n"
        f"sys.path.insert(0, {str(site)!r})\n"
        "from cheeseless.bootstrap import serve_mcp\n"
        f"raise SystemExit(serve_mcp({str(socket_path)!r}, task_id={task_id!r}))\n"
    )


def _stage_runtime(socket_path: Path, task_id: str) -> TrustedRuntime:
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
        _mcp_shim_source(python, site, socket_path, task_id),
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
    )
    server = UdsVerifierServer(socket_path, session, socket_dir_owned=dir_owned)
    try:
        server.serve_forever()
    finally:
        session.close_namespace_fds()
    return 0


def serve_mcp(
    uds_socket: str | Path | None,
    *,
    task_id: str | None = None,
    timeout: float = 10.0,
) -> int:
    """MCP stdio server. It needs only the verifier socket and task, never a store."""
    if uds_socket is None:
        raise ValueError("uds_socket is required")
    client = UdsVerifierClient(uds_socket, task_id=task_id, timeout=timeout)
    run_stdio(build_mcp_server(client))
    return 0


def _run_session_command(args: Any, agent: list[str] | None) -> int:
    """Map ``session`` options to a host session; usage and validation errors exit 2."""
    try:
        if not agent:
            raise SessionOptionError("an agent command is required after --")
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
            projection_dir=args.projection,
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
    if args.command is None:
        parser.print_help()
        return 2
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
        )
    if args.command == "serve-mcp":
        return serve_mcp(
            uds_socket=getattr(args, "uds_socket", None),
            task_id=getattr(args, "task", None),
            timeout=getattr(args, "timeout", 10.0),
        )
    if args.command == "session":
        return _run_session_command(args, agent)
    if args.command in ("ci-export", "ci-judge", "ci-budget-init"):
        from cheeseless.bootstrap_ci import run as run_ci  # noqa: PLC0415 - avoids a cycle

        return run_ci(args)
    ctx = build_context(getattr(args, "state_dir", None))
    return dispatch(args, ctx)


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
    egress_policy: EgressPolicy | None = None,
    egress_upstream_trusted_ca: Path | None = None,
    egress_insecure_upstream: bool = False,
    extra_read_only: Sequence[Path] | None = None,
    home_seed: Mapping[str, Path] | None = None,
    extra_env: Mapping[str, str] | None = None,
    evidence_dir: Path | None = None,
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
    """
    ctx = build_context(state_dir, cwd)
    if task_id is None:
        task_id = ctx.store.active_task_id()
    if task_id is None:
        raise ValueError("no active task")
    frozen, _ = ctx.store.load_task(task_id)
    protected = _protected_roots(ctx, state_dir, projection_dir, socket_dir)
    hidden_inodes: frozenset[tuple[int, int]] = frozenset()
    if extra_read_only or home_seed:
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
    ns_fd: int | None = None
    seccomp_fd: int | None = None
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
        if fingerprints is None:
            raise RuntimeError("the frozen task has no DLP fingerprints; re-freeze it")
        policy = egress_policy or EgressPolicy(allowed_hosts=frozenset())
        identity_markers = {
            marker
            for marker in (frozen.contract.lineage_id, Path(ctx.repo.root).name.lower())
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
        seed_home(home_dir, extras.seeds)
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
        runtime = _stage_runtime(socket_path, task_id)

        ctx.repo.create_projection(frozen.projection_tree, projection_dir)
        strays = ctx.repo.stray_hidden(projection_dir, frozen.hidden_blobs)
        if strays:
            raise RuntimeError(f"stray hidden material in the projection: {strays}")

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
        command = _bridge_command(python, ready_path, egress_ready, agent_args)

        seccomp_fd = seccomp.program_fd()
        env = _merge_env(agent_env, egress_ready_env, extras.env)

        pinned_read_only = tuple((mount.fd, str(mount.path)) for mount in extras.read_only)

        def build_argv(info_fd: int) -> list[str]:
            return profiles.agent(
                bwrap=bwrap,
                projection=projection_dir,
                sockets=[socket_path, egress_socket],
                home=home_dir,
                read_only=[runtime.root, python_root, proxy.ca_cert_path],
                pinned_read_only=pinned_read_only,
                env=env,
                command=command,
                seccomp_fd=seccomp_fd,
                info_fd=info_fd,
            )

        mount_fds = tuple(mount.fd for mount in extras.read_only)
        proc, ns_fd = _launch_agent(build_argv, (*mount_fds, seccomp_fd), stdio=agent_stdio)
        holder["proc"] = proc
        os.close(seccomp_fd)
        seccomp_fd = None
        session.expected_pidns_fd = ns_fd
        _wait_for_bridge(egress_ready, proc)
        ready_path.write_text("ready\n", encoding="ascii")

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
        if server is not None:
            server.shutdown()
        if thread is not None:
            thread.join(timeout=SESSION_START_TIMEOUT)
        if session is not None:
            session.close_namespace_fds()
        ns_fd = None
        if seccomp_fd is not None:
            try:
                os.close(seccomp_fd)
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
