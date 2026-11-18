"""Verifier UDS server: only ``run``, ``status`` and ``dispute``, host-configured session."""

from __future__ import annotations

import errno
import fcntl
import os
import socket
import socketserver
import stat
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cheeseless.app.author_gate import (
    gate_author_check,
    gate_author_crosscheck,
    gate_freeze_propose,
    gate_spec_done,
)
from cheeseless.app.dispute import gate_dispute
from cheeseless.app.gate_run import gate_run
from cheeseless.app.reference_gate import gate_reference_done
from cheeseless.app.status import status as app_status
from cheeseless.domain.budget import Pool
from cheeseless.domain.disclosure import AgentView, Verdict
from cheeseless.domain.dlp import DlpScanner
from cheeseless.domain.errors import ErrorCode, gate_error
from cheeseless.domain.reference_attempt import AttemptOwner, ReferenceMode, ReferenceState
from cheeseless.domain.task import Task
from cheeseless.ports.repository import Repository
from cheeseless.ports.runner import SuiteRunner
from cheeseless.ports.sandbox import AuthorSandbox, Sandbox
from cheeseless.ports.store import Store

from .mapping import (
    MappingError,
    agent_view_to_run_response,
    agent_view_to_status_response,
    author_outcome_to_response,
    dispute_outcome_to_response,
    dispute_request_fields,
    parse_author_request,
    parse_dispute_request,
    parse_run_request,
    parse_status_request,
    run_request_to_task_id,
    serialize_author_response,
    serialize_dispute_response,
    serialize_run_response,
    serialize_status_response,
    status_request_to_task_id,
)

SO_PEERPIDFD = getattr(socket, "SO_PEERPIDFD", 77)
PIDFD_GET_PID_NAMESPACE = 0xFF05
MAX_BODY_BYTES = 65536
MAX_HEADER_BYTES = 16384
MAX_LINE_BYTES = 8192
MAX_REQUEST_BYTES = MAX_HEADER_BYTES + MAX_BODY_BYTES
MAX_CONCURRENT = 8
REQUEST_QUEUE_SIZE = 16
READ_TIMEOUT = 10.0
HOST_PIDNS: int | None = None
HOST_PIDNS_LOCK = threading.Lock()
_REASONS = {
    200: "OK",
    400: "Bad Request",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    408: "Request Timeout",
    413: "Payload Too Large",
    429: "Too Many Requests",
    431: "Request Header Fields Too Large",
}


def _host_pidns_inode() -> int:
    global HOST_PIDNS
    if HOST_PIDNS is None:
        with HOST_PIDNS_LOCK:
            if HOST_PIDNS is None:
                HOST_PIDNS = os.stat("/proc/self/ns/pid").st_ino
    return HOST_PIDNS


def _peer_pidns_inode(conn: socket.socket) -> int | None:
    """Inode of the peer's PID namespace, or ``None`` if the kernel cannot tell us."""
    try:
        raw = conn.getsockopt(socket.SOL_SOCKET, SO_PEERPIDFD, 4)
    except OSError:
        return None
    if len(raw) != 4:
        return None
    pidfd = int.from_bytes(raw, sys.byteorder)
    if pidfd <= 0:
        return None
    nsfd: int | None = None
    try:
        nsfd = fcntl.ioctl(pidfd, PIDFD_GET_PID_NAMESPACE, 0)
        if nsfd < 0:
            return None
        return os.fstat(nsfd).st_ino
    except (OSError, ValueError):
        return None
    finally:
        if nsfd is not None and nsfd >= 0:
            try:
                os.close(nsfd)
            except OSError:
                pass
        try:
            os.close(pidfd)
        except OSError:
            pass


def _paths(slug: str, rpc: str) -> tuple[str, str, str]:
    return (f"/{slug}", f"/cheeseless.v1.Verifier/{rpc}", f"/{rpc}")


ROLE_HEADER = "x-cheeseless-role"
_AUTHOR_ROUTES = {
    path: kind
    for kind, slug, rpc in (
        ("check", "author-check", "AuthorCheck"),
        ("crosscheck", "author-crosscheck", "AuthorCrosscheck"),
        ("propose", "freeze-propose", "FreezePropose"),
        # dev author sessions only (ADR 0013): refused unless ``dev_lineage`` is set
        ("spec_done", "spec-done", "SpecDone"),
    )
    for path in _paths(slug, rpc)
}
_DEV_ONLY_KINDS = frozenset({"spec_done"})
_AUTHOR_KINDS = frozenset(_AUTHOR_ROUTES.values())
# dev TUI reference sessions only (ADR 0014): admitted per attempt, see ``_reference_ok``
_REFERENCE_ROUTES = {path: "reference_done" for path in _paths("reference-done", "ReferenceDone")}
_REFERENCE_KINDS = frozenset(_REFERENCE_ROUTES.values())
_IMPLEMENTER_ROUTES = {
    path: kind
    for kind, slug, rpc in (
        ("run", "run", "Run"),
        ("status", "status", "Status"),
        ("dispute", "dispute", "Dispute"),
    )
    for path in _paths(slug, rpc)
}


@dataclass
class SessionConfig:
    task_id: str | None
    worktree: Path
    profile: str
    pool: Pool
    timeout: float
    tool_version: str
    repo: Repository
    store: Store
    runner: SuiteRunner
    sandbox: Sandbox
    dlp: DlpScanner | None = None
    expected_pidns_inode: int | None = None
    expected_pidns_fd: int | None = None
    namespace_fd: int | None = None
    pidns_fd: int | None = None
    # ``implementer`` (run, status, dispute) or ``author`` (check, crosscheck, propose).
    role: str = "implementer"
    # Host-recorded identity of this session (store.register_session). Both are required for
    # any role-checked route: a config without them fails closed.
    session_id: str | None = None
    # Store generation at launch; a later rejected dispute makes this session stale.
    generation: int | None = None
    author_sandbox: AuthorSandbox | None = None
    # Set only by a host-started ``cheeseless dev`` author session: the lineage whose dev
    # state ``spec_done`` may move. ``None`` means the dev-only routes do not exist.
    dev_lineage: str | None = None
    # Set only for a dev TUI reference session: the attempt ``reference_done`` may stop.
    dev_reference: str | None = None
    # Host seam (a later slice): called once, with the attempt id, after running -> stopping.
    on_reference_stop: Callable[[str], None] | None = None

    def namespace_fds(self) -> tuple[int, ...]:
        return tuple(
            fd
            for fd in (self.expected_pidns_fd, self.namespace_fd, self.pidns_fd)
            if fd is not None
        )

    def pinned_inode(self) -> int | None:
        """The one namespace inode this session trusts, or ``None`` (fail closed).

        Acceptance requires a lifetime-held namespace FD. A bare inode is only ever
        an additional consistency check: without an FD it can never authorise a peer.
        """
        fds = self.namespace_fds()
        if not fds:
            return None
        inodes: set[int] = set()
        for fd in fds:
            try:
                inodes.add(os.fstat(fd).st_ino)
            except OSError:
                return None
        if self.expected_pidns_inode is not None:
            if not isinstance(self.expected_pidns_inode, int):
                return None
            inodes.add(self.expected_pidns_inode)
        if len(inodes) != 1:
            return None
        inode = inodes.pop()
        if inode <= 0:
            return None
        return inode

    def close_namespace_fds(self) -> None:
        for name in ("expected_pidns_fd", "namespace_fd", "pidns_fd"):
            fd = getattr(self, name)
            if fd is None:
                continue
            setattr(self, name, None)
            try:
                os.close(fd)
            except OSError:
                pass


class _VerifierUnixServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    block_on_close = False
    request_queue_size = REQUEST_QUEUE_SIZE

    def server_bind(self) -> None:
        # Bypass UnixStreamServer.server_bind: it unlinks an arbitrary path.
        # The caller has already verified and removed only a stale, owned socket.
        socketserver.TCPServer.server_bind(self)
        self.server_address = self.socket.getsockname()


class UdsVerifierServer:
    def __init__(
        self, socket_path: str | Path, session: SessionConfig, *, socket_dir_owned: bool = False
    ) -> None:
        self.socket_path = Path(socket_path)
        self.session = session
        self._httpd: _VerifierUnixServer | None = None
        self._run_lock = threading.Lock()
        self._semaphore = threading.BoundedSemaphore(MAX_CONCURRENT)
        self._started = False
        self._socket_owned = False
        self._dir_created = socket_dir_owned

    # -- socket lifecycle ------------------------------------------------------------

    def _prepare(self) -> None:
        if self._started:
            raise RuntimeError("server already started")
        path = self.socket_path
        if not path.is_absolute():
            raise RuntimeError("socket path must be absolute")
        directory = path.parent
        try:
            dst = os.lstat(directory)
        except FileNotFoundError:
            try:
                os.mkdir(directory, 0o700)
            except OSError as exc:
                raise RuntimeError(f"cannot create socket directory: {exc}") from exc
            self._dir_created = True
            os.chmod(directory, 0o700)
            dst = os.lstat(directory)
        self._check_dir(dst)
        self._check_socket_path()
        self._started = True

    @staticmethod
    def _check_dir(st: os.stat_result) -> None:
        if not stat.S_ISDIR(st.st_mode):
            raise RuntimeError("socket directory path is not a directory")
        if st.st_uid != os.geteuid():
            raise RuntimeError("socket directory owned by another user")
        if stat.S_IMODE(st.st_mode) != 0o700:
            raise RuntimeError("socket directory must be owned and mode 0700")

    def _check_socket_path(self) -> None:
        try:
            st = os.lstat(self.socket_path)
        except FileNotFoundError:
            return
        if stat.S_ISLNK(st.st_mode):
            raise RuntimeError("socket path is a symlink")
        if not stat.S_ISSOCK(st.st_mode):
            raise RuntimeError("socket path is not a socket")
        if st.st_uid != os.geteuid():
            raise RuntimeError("socket path owned by another user")
        if not self._is_stale_socket():
            raise RuntimeError("socket path is a live socket")
        try:
            os.unlink(self.socket_path)
        except OSError as exc:
            raise RuntimeError(f"cannot remove stale socket: {exc}") from exc
        self._socket_owned = False

    def _is_stale_socket(self) -> bool:
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        probe.settimeout(0.2)
        try:
            probe.connect(str(self.socket_path))
        except OSError as exc:
            return exc.errno in (errno.ECONNREFUSED, errno.ENOENT)
        else:
            return False
        finally:
            probe.close()

    def _bind(self) -> _VerifierUnixServer:
        httpd = _VerifierUnixServer(
            str(self.socket_path), self._handler_class(), bind_and_activate=False
        )
        try:
            httpd.server_bind()
            self._socket_owned = True
            os.chmod(self.socket_path, 0o600)
            st = os.lstat(self.socket_path)
            if not stat.S_ISSOCK(st.st_mode) or st.st_uid != os.geteuid():
                raise RuntimeError("socket is not owned by the verifier")
            if stat.S_IMODE(st.st_mode) & 0o077:
                raise RuntimeError("socket is not private")
            httpd.server_activate()
        except Exception:
            httpd.server_close()
            raise
        return httpd

    def serve_forever(self) -> None:
        httpd: _VerifierUnixServer | None = None
        try:
            self._prepare()
            httpd = self._bind()
            self._httpd = httpd
            httpd.serve_forever(poll_interval=0.1)
        finally:
            self._httpd = None
            if httpd is not None:
                httpd.server_close()
            self._cleanup_socket()
            self.session.close_namespace_fds()

    def _cleanup_socket(self) -> None:
        if self._socket_owned:
            try:
                st = os.lstat(self.socket_path)
                if stat.S_ISSOCK(st.st_mode) and st.st_uid == os.geteuid():
                    os.unlink(self.socket_path)
            except OSError:
                pass
            self._socket_owned = False
        if self._dir_created:
            directory = self.socket_path.parent
            try:
                st = os.lstat(directory)
                if (
                    stat.S_ISDIR(st.st_mode)
                    and st.st_uid == os.geteuid()
                    and stat.S_IMODE(st.st_mode) == 0o700
                ):
                    os.rmdir(directory)
            except OSError:
                pass
            self._dir_created = False

    def shutdown(self) -> None:
        httpd = self._httpd
        if httpd is not None:
            httpd.shutdown()

    # -- authentication --------------------------------------------------------------

    def _authenticate(self, conn: socket.socket) -> bool:
        inode = _peer_pidns_inode(conn)
        if inode is None:
            return False
        try:
            host_inode = _host_pidns_inode()
        except OSError:
            return False
        # The host PID namespace is never a session sandbox.
        if inode == host_inode:
            return False
        expected = self.session.pinned_inode()
        if expected is None:
            return False
        if expected == host_inode:
            return False
        return inode == expected

    # -- request handling ------------------------------------------------------------

    def _handle_one(self, conn: socket.socket) -> None:
        try:
            conn.settimeout(READ_TIMEOUT)
            if not self._semaphore.acquire(timeout=READ_TIMEOUT):
                return
            try:
                if not self._authenticate(conn):
                    return
                rfile = conn.makefile("rb")
                wfile = conn.makefile("wb")
                try:
                    self._process_request(rfile, wfile)
                finally:
                    _quiet_close(rfile)
                    _quiet_close(wfile)
            finally:
                self._semaphore.release()
        finally:
            _quiet_close(conn)

    def _process_request(self, rfile: Any, wfile: Any) -> None:
        header_bytes = 0
        try:
            request_line = _readline(rfile)
            if not request_line:
                return
            header_bytes += len(request_line)
            if len(request_line) >= MAX_LINE_BYTES or header_bytes > MAX_HEADER_BYTES:
                self._write_error(wfile, 431, "request line too large")
                return
            parts = request_line.decode("iso-8859-1").rstrip("\r\n").split(" ")
            if len(parts) != 3:
                self._write_error(wfile, 400, "bad request")
                return
            method, path, version = parts
            if version not in ("HTTP/1.0", "HTTP/1.1"):
                self._write_error(wfile, 400, "bad protocol")
                return
            headers: dict[str, str] = {}
            while True:
                line = _readline(rfile)
                if not line:
                    self._write_error(wfile, 400, "truncated headers")
                    return
                if line in (b"\r\n", b"\n"):
                    break
                header_bytes += len(line)
                if len(line) >= MAX_LINE_BYTES or header_bytes > MAX_HEADER_BYTES:
                    self._write_error(wfile, 431, "headers too large")
                    return
                key, sep, value = line.decode("iso-8859-1").partition(":")
                if not sep or not key.strip():
                    self._write_error(wfile, 400, "bad header")
                    return
                name = key.strip().lower()
                if name in headers:
                    self._write_error(wfile, 400, "duplicate header")
                    return
                headers[name] = value.strip()
            if "transfer-encoding" in headers:
                self._write_error(wfile, 400, "transfer encoding not allowed")
                return
            if method != "POST":
                self._write_error(wfile, 405, "method not allowed")
                return
            try:
                content_length = int(headers.get("content-length", "0"))
            except ValueError:
                self._write_error(wfile, 400, "bad content-length")
                return
            if content_length < 0 or content_length > MAX_BODY_BYTES:
                self._write_error(wfile, 413, "payload too large")
                return
            if header_bytes + content_length > MAX_REQUEST_BYTES:
                self._write_error(wfile, 413, "request too large")
                return
            body = _read_exact(rfile, content_length)
            if body is None:
                self._write_error(wfile, 400, "truncated body")
                return
            self._route(wfile, path, body, claimed_role=headers.get(ROLE_HEADER, "implementer"))
        except TimeoutError:
            self._write_error(wfile, 408, "request timeout")
        except (OSError, ValueError):
            try:
                self._write_error(wfile, 400, "bad request")
            except OSError:
                pass

    def _role_ok(self, claimed: str, kind: str) -> bool:
        """The claim, the server's bound role and this session's own record must all agree.

        A reference session has only ``reference_done``, and only for a running TUI attempt
        bound to it (``_reference_ok``); every other kind is refused for it. The record is per
        session (not per task), so a second session on the same task cannot change this one's
        role; its generation must still equal the task's, so a rejected dispute stales it.
        Missing task id, session id, generation or record, and any store failure, refuse
        (fail closed).
        """
        s = self.session
        if s.role == "reviewer":
            # ADR 0015: a reviewer has no RPC at all. The sandbox gives it no socket, so this
            # is unreachable by design — refused anyway so the guarantee does not rest on the
            # mount staying correct.
            return False
        if (kind in _REFERENCE_KINDS) != (s.role == "reference") or claimed != s.role:
            return False
        if s.task_id is None or s.session_id is None or s.generation is None:
            return False
        try:
            record = s.store.session_record(s.session_id)
            return (
                record is not None
                and record.task_id == s.task_id
                and record.role.value == s.role
                and record.generation == s.generation
                and s.store.session_generation(s.task_id) == s.generation
                and (kind not in _REFERENCE_KINDS or self._reference_ok())
            )
        except Exception:  # noqa: BLE001 - an unreadable record must refuse
            return False

    def _reference_ok(self) -> bool:
        """The bound attempt exists, is this session's, is a running TUI one (read-only)."""
        s = self.session
        if s.dev_reference is None:
            return False
        attempt = s.store.reference_attempt(s.dev_reference)  # type: ignore[attr-defined]
        return (
            attempt is not None
            and attempt.session_id == s.session_id
            and attempt.generation == s.generation
            and attempt.mode is ReferenceMode.TUI
            and attempt.state is ReferenceState.RUNNING
        )

    def _route(self, wfile: Any, path: str, body: bytes, claimed_role: str = "implementer") -> None:
        path = path.split("?", 1)[0]
        author_kind = _AUTHOR_ROUTES.get(path)
        implementer_kind = _IMPLEMENTER_ROUTES.get(path)
        reference_kind = _REFERENCE_ROUTES.get(path)
        known = author_kind or implementer_kind or reference_kind
        if (
            implementer_kind == "status"
            and self.session.task_id is None
            and self.session.role == "implementer"
        ):
            self._no_task_status(wfile)
        elif known is not None and not self._role_ok(claimed_role, known):
            self._role_refused(wfile, known)
        elif reference_kind is not None:
            self._reference(wfile, body, reference_kind)
        elif author_kind is not None:
            if self.session.role != "author" or (
                author_kind in _DEV_ONLY_KINDS and self.session.dev_lineage is None
            ):
                self._role_refused(wfile, author_kind)
            else:
                self._author(wfile, body, author_kind)
        elif implementer_kind is not None:
            if self.session.role == "author":
                self._role_refused(wfile, implementer_kind)
            else:
                getattr(self, "_" + implementer_kind)(wfile, body)
        else:
            self._write_error(wfile, 404, "not found")

    def _no_task_status(self, wfile: Any) -> None:
        """No task is frozen, so there is no session to bind: answer read-only, touch no store.

        This is the pre-stage-(c) answer (an infra error: nothing to report), kept so
        `serve-uds` without a frozen task stays informative instead of refusing as a role error.
        """
        view = AgentView(gate_error(ErrorCode.INFRA_ERROR), ())
        self._write_response(
            wfile, 200, serialize_status_response(agent_view_to_status_response(view))
        )

    def _role_refused(self, wfile: Any, kind: str) -> None:
        err = gate_error(ErrorCode.ROLE_NOT_ALLOWED)
        if kind in _AUTHOR_KINDS | _REFERENCE_KINDS:
            data = serialize_author_response(author_outcome_to_response(kind, err))
        elif kind == "run":
            data = serialize_run_response(agent_view_to_run_response(AgentView(err, ())))
        elif kind == "status":
            data = serialize_status_response(agent_view_to_status_response(AgentView(err, ())))
        else:
            data = serialize_dispute_response(dispute_outcome_to_response(err))
        self._write_response(wfile, 200, data)

    def _author(self, wfile: Any, body: bytes, kind: str) -> None:
        try:
            task_id = parse_author_request(kind, body)
        except (MappingError, UnicodeDecodeError, ValueError):
            self._write_error(wfile, 400, "bad request")
            return
        if not self._task_matches(task_id, wfile):
            return
        if not self._run_lock.acquire(blocking=False):
            self._write_error(wfile, 429, "run in progress")
            return
        try:
            outcome = self._author_outcome(kind)
            self._write_response(
                wfile, 200, serialize_author_response(author_outcome_to_response(kind, outcome))
            )
        finally:
            self._run_lock.release()

    def _reference(self, wfile: Any, body: bytes, kind: str) -> None:
        try:
            task_id = parse_author_request(kind, body)
        except (MappingError, UnicodeDecodeError, ValueError):
            self._write_error(wfile, 400, "bad request")
            return
        if not self._task_matches(task_id, wfile):
            return
        if not self._run_lock.acquire(blocking=False):
            self._write_error(wfile, 429, "run in progress")
            return
        try:
            s = self.session
            outcome: object
            if s.dev_reference is None or s.session_id is None or s.generation is None:
                outcome = gate_error(ErrorCode.ROLE_NOT_ALLOWED)  # unreachable: _role_ok refused
            else:
                outcome = gate_reference_done(
                    s.store,  # type: ignore[arg-type]
                    s.dev_reference,
                    AttemptOwner(s.session_id, s.generation, ReferenceMode.TUI),
                    s.on_reference_stop,
                )
            self._write_response(
                wfile, 200, serialize_author_response(author_outcome_to_response(kind, outcome))
            )
        finally:
            self._run_lock.release()

    def _author_outcome(self, kind: str) -> object:
        s = self.session
        sandbox = s.author_sandbox
        store: Any = s.store
        if kind == "propose":
            return gate_freeze_propose(s.repo, store, s.worktree)
        if kind == "spec_done":
            if s.dev_lineage is None:  # unreachable: _route refuses first; fail closed anyway
                return gate_error(ErrorCode.ROLE_NOT_ALLOWED)
            return gate_spec_done(s.repo, store, s.worktree, s.dev_lineage)
        if sandbox is None:
            return gate_error(ErrorCode.INFRA_ERROR)
        if kind == "check":
            return gate_author_check(s.repo, sandbox, store, s.worktree)
        return gate_author_crosscheck(s.repo, sandbox, store, s.worktree)

    def _dispute(self, wfile: Any, body: bytes) -> None:
        try:
            req = parse_dispute_request(body)
        except (MappingError, UnicodeDecodeError, ValueError):
            self._write_error(wfile, 400, "bad request")
            return
        task_id, criterion_id, code, note = dispute_request_fields(req)
        if not self._task_matches(task_id, wfile):
            return
        configured = self.session.task_id
        if configured is None:
            self._write_error(wfile, 400, "bad request")
            return
        if not self._run_lock.acquire(blocking=False):
            self._write_error(wfile, 429, "run in progress")
            return
        try:
            try:
                outcome = gate_dispute(
                    self.session.store,
                    task_id=configured,
                    criterion_id=criterion_id,
                    code=code,
                    note=note,
                )
            except Exception:
                outcome = gate_error(ErrorCode.INFRA_ERROR)
            data = serialize_dispute_response(dispute_outcome_to_response(outcome))
            self._write_response(wfile, 200, data)
        finally:
            self._run_lock.release()

    def _run(self, wfile: Any, body: bytes) -> None:
        try:
            req = parse_run_request(body)
        except (MappingError, UnicodeDecodeError, ValueError):
            self._write_error(wfile, 400, "bad request")
            return
        if not self._task_matches(run_request_to_task_id(req), wfile):
            return
        if not self._run_lock.acquire(blocking=False):
            self._write_error(wfile, 429, "run in progress")
            return
        try:
            try:
                verdict = gate_run(
                    self.session.repo,
                    self.session.store,
                    self.session.runner,
                    self.session.sandbox,
                    worktree=self.session.worktree,
                    task_id=self.session.task_id,
                    profile=self.session.profile,
                    tool_version=self.session.tool_version,
                    timeout=self.session.timeout,
                    pool=self.session.pool,
                    scanner=self.session.dlp,
                )
                agent_view = self._agent_view(verdict)
            except Exception:
                agent_view = AgentView(gate_error(ErrorCode.INFRA_ERROR), ())
            data = serialize_run_response(agent_view_to_run_response(agent_view))
            self._write_response(wfile, 200, data)
        finally:
            self._run_lock.release()

    def _status(self, wfile: Any, body: bytes) -> None:
        try:
            req = parse_status_request(body)
        except (MappingError, UnicodeDecodeError, ValueError):
            self._write_error(wfile, 400, "bad request")
            return
        if not self._task_matches(status_request_to_task_id(req), wfile):
            return
        if not self._run_lock.acquire(blocking=False):
            self._write_error(wfile, 429, "run in progress")
            return
        try:
            try:
                disputed = self._session_disputed()
            except Exception:
                # Fail closed: an unreadable dispute ledger must not look like "no dispute".
                agent_view = AgentView(gate_error(ErrorCode.INFRA_ERROR), ())
                data = serialize_status_response(agent_view_to_status_response(agent_view))
                self._write_response(wfile, 200, data)
                return
            if disputed:
                agent_view = AgentView(gate_error(ErrorCode.SESSION_DISPUTED), ())
                data = serialize_status_response(agent_view_to_status_response(agent_view))
                self._write_response(wfile, 200, data)
                return
            try:
                result = app_status(
                    self.session.store,
                    task_id=self.session.task_id,
                    repo=self.session.repo,
                    worktree=self.session.worktree,
                )
            except Exception:
                result = None
            if result is None:
                agent_view = AgentView(gate_error(ErrorCode.INFRA_ERROR), ())
            else:
                agent_view = self._agent_view(Verdict(result, charged=True))
            data = serialize_status_response(agent_view_to_status_response(agent_view))
            self._write_response(wfile, 200, data)
        finally:
            self._run_lock.release()

    def _session_disputed(self) -> bool:
        task_id = self.session.task_id
        if task_id is None:
            return False
        return self.session.store.session_disputed(task_id)

    def _task_matches(self, client_task_id: str, wfile: Any) -> bool:
        configured = self.session.task_id
        if client_task_id and configured and client_task_id != configured:
            self._write_error(wfile, 403, "task mismatch")
            return False
        return True

    def _agent_view(self, verdict: Verdict) -> AgentView:
        task = self._load_task()
        if task is not None:
            return verdict.agent_view(task)
        return AgentView(verdict.outcome, ())

    def _write_response(self, wfile: Any, status_code: int, body: bytes) -> None:
        reason = _REASONS.get(status_code, "OK" if status_code < 400 else "Error")
        wfile.write(f"HTTP/1.1 {status_code} {reason}\r\n".encode("ascii"))
        wfile.write(b"Content-Type: application/json\r\n")
        wfile.write(f"Content-Length: {len(body)}\r\n".encode("ascii"))
        wfile.write(b"Connection: close\r\n\r\n")
        wfile.write(body)
        wfile.flush()

    def _write_error(self, wfile: Any, status_code: int, message: str) -> None:
        body = ('{"error":"' + message + '"}').encode("ascii")
        self._write_response(wfile, status_code, body)

    def _handler_class(self) -> type[socketserver.BaseRequestHandler]:
        outer = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                outer._handle_one(self.request)

        return Handler

    def _load_task(self) -> Task | None:
        if self.session.task_id is None:
            return None
        try:
            frozen, _ = self.session.store.load_task(self.session.task_id)
            return frozen.task
        except Exception:
            return None


def _readline(rfile: Any) -> bytes:
    line: bytes = rfile.readline(MAX_LINE_BYTES)
    return line


def _read_exact(rfile: Any, length: int) -> bytes | None:
    chunks: list[bytes] = []
    remaining = length
    while remaining > 0:
        chunk = rfile.read(remaining)
        if not chunk:
            return None
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _quiet_close(obj: Any) -> None:
    try:
        obj.close()
    except OSError:
        pass


__all__ = ["SessionConfig", "UdsVerifierServer"]
