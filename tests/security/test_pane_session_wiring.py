"""ADR 0019 through ``host_session``: which roles may run on a pty, and how it is relayed.

The author (test-writer) pane is interactive; the implementer (coder) and the reference
panes are view-only. A role relay lives at a deterministic path inside the state dir, so a
pane client can find it without being told. Nothing GUI-side enters the sandbox: the relay
is host-owned, exactly as in ADR 0014.
"""

import os
import tempfile
from pathlib import Path

import pytest

from cheeseless import bootstrap, pane_layout
from cheeseless.adapters.outbound.git.repository import GitRepository
from cheeseless.adapters.outbound.runner.pytest_runner import PytestRunner
from cheeseless.adapters.outbound.sandbox.bwrap import BwrapSandbox
from cheeseless.adapters.outbound.store.file_store import FileStore, store_root
from cheeseless.app.freeze import commit, prepare
from cheeseless.bootstrap import host_session
from cheeseless.domain.dev_session import DevState
from cheeseless.domain.reference_attempt import NewReferenceAttempt, ReferenceMode
from cheeseless.session_options import SessionOptionError
from tests.requirements import needs
from tests.toy import make_repo

PYTHON = "/usr/bin/python3"
pytestmark = needs(
    os.access("/usr/bin/bwrap", os.X_OK)
    and os.access("/usr/bin/git", os.X_OK)
    and os.access(PYTHON, os.X_OK),
    "bwrap, git and a system python are required",
)

LINEAGE = "toy"
DIGEST = "a" * 64
TASK = "a" * 64
SLEEP = [PYTHON, "-I", "-c", "import time; time.sleep(30)"]


class _Launched(Exception):
    """Raised by the probe once the agent launch is reached."""


@pytest.fixture
def repo(tmp_path) -> GitRepository:
    return GitRepository.discover(make_repo(tmp_path / "repo"))


@pytest.fixture
def state() -> Path:
    # AF_UNIX paths are short; pytest's tmp_path would push the role socket over the limit.
    return Path(tempfile.mkdtemp(prefix="cl-"))


def _open_dev(repo: GitRepository, state: Path) -> None:
    store = FileStore(store_root(repo.common_dir(), state))
    store.save_dev_state(DevState(LINEAGE).spec_done(DIGEST))


def _session(repo, state, tmp_path, **kwargs):
    kwargs.setdefault("role", "author")
    kwargs.setdefault("agent_command", ["/bin/true"])
    return host_session(
        state_dir=state,
        cwd=repo.root,
        task_id=TASK,
        projection_dir=tmp_path / "projection",
        socket_dir=tmp_path / "sockets",
        **kwargs,
    )


def _author_pane(**extra):
    return {
        "role": "author",
        "dev_lineage": LINEAGE,
        "agent_pty": (24, 80),
        "pane_lineage": LINEAGE,
        **extra,
    }


@pytest.fixture
def probe(monkeypatch):
    seen: dict[str, object] = {}

    def launch(_build_argv, _fds, **kwargs):
        seen["launch"] = kwargs
        raise _Launched

    monkeypatch.setattr(bootstrap, "_launch_agent", launch)
    return seen


@pytest.mark.parametrize(
    "kwargs",
    [
        {"role": "reviewer", "agent_pty": (24, 80), "pane_lineage": LINEAGE},
        {"role": "author", "agent_pty": (24, 80), "pane_lineage": LINEAGE},  # not a dev author
        {"role": "author", "dev_lineage": LINEAGE, "agent_pty": (24, 80)},  # nothing names it
        _author_pane(pane_lineage="other"),  # not the dev session's lineage
        _author_pane(agent_stdio=True),  # stdio and a pty are exclusive
        {"role": "author", "dev_lineage": LINEAGE, "pane_lineage": LINEAGE},  # a name, no pty
        {"role": "author", "dev_lineage": LINEAGE, "log_file": Path("/nonexistent/log")},
    ],
)
def test_an_unsupported_pty_request_is_refused_before_anything_starts(
    repo, state, tmp_path, probe, kwargs
):
    _open_dev(repo, state)
    with pytest.raises(SessionOptionError):
        with _session(repo, state, tmp_path, **kwargs):
            pass
    assert "launch" not in probe


def test_a_dev_author_pty_reaches_the_launcher_and_gets_a_controlling_tty(
    repo, state, tmp_path, probe, monkeypatch
):
    _open_dev(repo, state)
    wrapped: list[list[str]] = []
    real = bootstrap.pty_launch.with_controlling_tty

    def spy(python, command):
        wrapped.append(list(command))
        return real(python, command)

    monkeypatch.setattr(bootstrap.pty_launch, "with_controlling_tty", spy)
    with pytest.raises(_Launched):
        with _session(repo, state, tmp_path, **_author_pane()):
            pass
    launch = probe["launch"]
    assert isinstance(launch, dict)
    assert launch["pty_slave"] is not None and launch["log_fd"] is None
    assert launch["stdio"] is False
    assert len(wrapped) == 1, "a resize on the master must reach the child"


def test_the_author_relay_is_interactive_and_lives_at_the_role_path(repo, state, tmp_path):
    _open_dev(repo, state)
    with _session(repo, state, tmp_path, **_author_pane(agent_command=SLEEP)) as info:
        relay = info["relay"]
        assert relay is not None
        assert relay.view_only is False
        expected = pane_layout.socket_path(state, LINEAGE, pane_layout.PaneRole.TEST_WRITER)
        assert relay.socket_path == expected
    assert not relay.socket_path.exists(), "the host removes the socket when the session ends"


def _implementer_pane(**extra):
    return {"role": "implementer", "agent_pty": (24, 80), "pane_lineage": LINEAGE, **extra}


def test_a_coder_pane_of_another_lineage_is_refused(repo, state, tmp_path, probe):
    _open_dev(repo, state)
    with pytest.raises(SessionOptionError, match="lineage"):
        with _session(repo, state, tmp_path, **_implementer_pane(pane_lineage="other")):
            pass
    assert "launch" not in probe


def test_a_coder_pane_without_an_open_dev_session_is_refused(repo, state, tmp_path, probe):
    with pytest.raises(SessionOptionError):
        with _session(repo, state, tmp_path, **_implementer_pane()):
            pass
    assert "launch" not in probe


def test_the_implementer_relay_is_view_only_at_the_coder_path(repo, state, tmp_path):
    _open_dev(repo, state)
    store = FileStore(store_root(repo.common_dir(), state))
    commit(prepare(repo, PytestRunner(), BwrapSandbox()), store)
    kwargs = {"role": "implementer", "agent_pty": (24, 80), "pane_lineage": LINEAGE}
    with host_session(
        state_dir=state,
        cwd=repo.root,
        projection_dir=tmp_path / "projection",
        socket_dir=tmp_path / "sockets",
        agent_command=SLEEP,
        **kwargs,
    ) as info:
        relay = info["relay"]
        assert relay is not None and relay.view_only is True
        assert relay.socket_path == pane_layout.socket_path(
            state, LINEAGE, pane_layout.PaneRole.CODER
        )
    assert not relay.socket_path.exists()


def _reference(**extra):
    return {
        "role": "reference",
        "new_reference": NewReferenceAttempt("att-1", LINEAGE, DIGEST, ReferenceMode.TUI),
        "agent_pty": (24, 80),
        **extra,
    }


def test_a_reference_relay_is_view_only_at_the_reference_path(repo, state, tmp_path):
    with _session(
        repo, state, tmp_path, **_reference(pane_lineage=LINEAGE, agent_command=SLEEP)
    ) as info:
        relay = info["relay"]
        assert relay is not None and relay.view_only is True
        assert relay.socket_path == pane_layout.socket_path(
            state, LINEAGE, pane_layout.PaneRole.REFERENCE
        )
    assert not relay.socket_path.exists()


def test_a_reference_relay_without_a_lineage_is_still_view_only(repo, state, tmp_path):
    with _session(repo, state, tmp_path, **_reference(agent_command=SLEEP)) as info:
        assert info["relay"] is not None and info["relay"].view_only is True


def test_a_reference_pane_lineage_must_be_the_attempts_lineage(repo, state, tmp_path, probe):
    with pytest.raises(SessionOptionError):
        with _session(repo, state, tmp_path, **_reference(pane_lineage="other")):
            pass
    assert "launch" not in probe


# The agent puts its tty in raw mode, prints a tick every 50 ms and reports every byte that
# reaches its stdin as ``RX:<hex>``; a frame leaking into the pty would show up in the output.
_ECHO_AGENT = [
    PYTHON,
    "-I",
    "-c",
    "import os,select,sys,time,tty\n"
    "tty.setraw(0)\n"
    "end=time.time()+12\n"
    "while time.time()<end:\n"
    "    if select.select([0],[],[],0.05)[0]:\n"
    "        os.write(1,b'RX:'+os.read(0,4096).hex().encode()+b' ')\n"
    "    else:\n"
    "        os.write(1,b'TICK ')\n",
]
_CANARY = b"CANARY-SENT-TO-A-VIEW-ONLY-ROLE-SOCKET"


def _assert_view_only_through_the_wiring(relay) -> None:
    import socket
    import time

    from cheeseless.domain.pane_frames import Data, FrameDecoder, encode_data

    assert relay.view_only is True
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(3)
    sock.connect(str(relay.socket_path))
    sock.sendall(encode_data(_CANARY))
    try:
        assert sock.recv(4096) == b"", "the relay must close a view-only client that sends"
    except ConnectionResetError:
        pass
    sock.close()
    # Reconnect as a plain reader and read the pane's output for a moment.
    reader = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    reader.settimeout(0.5)
    reader.connect(str(relay.socket_path))
    decoder = FrameDecoder()
    seen = b""
    end = time.monotonic() + 1.5
    while time.monotonic() < end:
        try:
            chunk = reader.recv(4096)
        except TimeoutError:
            continue
        if not chunk:
            break
        seen += b"".join(f.payload for f in decoder.feed(chunk) if isinstance(f, Data))
    reader.close()
    assert b"TICK" in seen, "the agent must have been running and visible"
    assert b"RX:" not in seen, "bytes reached the pty"
    assert _CANARY.hex().encode() not in seen


def test_a_canary_frame_to_the_coder_socket_never_reaches_the_pty(repo, state, tmp_path):
    _open_dev(repo, state)
    store = FileStore(store_root(repo.common_dir(), state))
    commit(prepare(repo, PytestRunner(), BwrapSandbox()), store)
    with host_session(
        state_dir=state,
        cwd=repo.root,
        projection_dir=tmp_path / "projection",
        socket_dir=tmp_path / "sockets",
        agent_command=_ECHO_AGENT,
        **_implementer_pane(),
    ) as info:
        _assert_view_only_through_the_wiring(info["relay"])


def test_a_canary_frame_to_the_reference_socket_never_reaches_the_pty(repo, state, tmp_path):
    with _session(
        repo, state, tmp_path, **_reference(pane_lineage=LINEAGE, agent_command=_ECHO_AGENT)
    ) as info:
        _assert_view_only_through_the_wiring(info["relay"])
