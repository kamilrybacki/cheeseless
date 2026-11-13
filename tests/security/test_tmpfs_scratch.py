import ctypes
import stat
from pathlib import Path

import pytest

from cheeseless.adapters.outbound.sandbox.bwrap import BwrapSandbox, _is_tmpfs
from cheeseless.ports.sandbox import SandboxError


def test_scratch_is_tmpfs_and_private() -> None:
    with BwrapSandbox().scratch() as scratch:
        assert scratch.is_dir()
        assert stat.S_IMODE(scratch.stat().st_mode) == 0o700
        (ctypes.c_long * 1)()
        assert _is_tmpfs(scratch)


def test_non_tmpfs_root_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    import cheeseless.adapters.outbound.sandbox.bwrap as b

    monkeypatch.setattr(b, "_tmpfs_roots", lambda: [Path("/etc")])
    with pytest.raises(SandboxError):
        with BwrapSandbox().scratch():
            pass


def test_scratch_cleanup() -> None:
    with BwrapSandbox().scratch() as scratch:
        path = scratch
    assert not path.exists()
