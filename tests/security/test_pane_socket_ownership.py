"""ADR 0019: a refused second relay never touches the first one's socket, and the fixed
socket path is built from private, non-symlinked directories only."""

import os
import socket
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from cheeseless.adapters.outbound.pane.relay import (
    PaneRelay,
    PaneSocketError,
    prepare_fixed_socket,
)


@pytest.fixture
def root() -> Iterator[Path]:
    # AF_UNIX paths are short; pytest's tmp_path would push the socket over the limit.
    path = Path(tempfile.mkdtemp(prefix="cl-"))
    yield path


def _target(root: Path) -> Path:
    return root / "cheeseless" / "panes" / "lin" / "coder.sock"


def test_a_refused_second_relay_leaves_the_first_sessions_socket_alone(root):
    target = _target(root)
    master_a, slave_a = os.openpty()
    master_b, slave_b = os.openpty()
    first = PaneRelay(master_a, fixed_socket=target, state_root=root, view_only=True)
    first.start()
    second = PaneRelay(master_b, fixed_socket=target, state_root=root, view_only=True)
    try:
        with pytest.raises(PaneSocketError, match="already live"):
            second.start()
        second.close()
        assert target.exists(), "closing a relay that never bound must not unlink the socket"
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(3)
        try:
            client.connect(str(target))
        finally:
            client.close()
    finally:
        first.close()
        os.close(slave_a)
        os.close(slave_b)


def test_a_symlinked_panes_dir_is_refused_and_nothing_is_created_at_its_target(root, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "cheeseless").mkdir(mode=0o700)
    (root / "cheeseless" / "panes").symlink_to(outside)
    with pytest.raises(PaneSocketError):
        prepare_fixed_socket(_target(root), root)
    assert list(outside.iterdir()) == []


def test_every_directory_below_the_state_dir_is_created_private(root):
    prepare_fixed_socket(_target(root), root)
    for sub in ("cheeseless", "cheeseless/panes", "cheeseless/panes/lin"):
        assert (root / sub).stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize("level", ["cheeseless", "cheeseless/panes"])
def test_a_shared_ancestor_below_the_state_dir_is_refused(root, level):
    (root / "cheeseless" / "panes").mkdir(parents=True, mode=0o700)
    (root / level).chmod(0o755)
    with pytest.raises(PaneSocketError, match="private directory"):
        prepare_fixed_socket(_target(root), root)


def test_a_target_outside_the_state_dir_is_refused(root, tmp_path):
    with pytest.raises(PaneSocketError, match="outside"):
        prepare_fixed_socket(tmp_path / "x" / "coder.sock", root)
