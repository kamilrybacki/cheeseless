"""Open and close a tmux pane for the reference relay client (ADR 0014).

tmux is a host-only concern. Nothing here, and nothing derived from tmux, is ever passed
to the sandbox: the sandbox environment is built from an explicit allow-list, ``TMUX*``
names are reserved, and the pane only runs a host command that talks to the relay socket.
"""

import re
import subprocess
from collections.abc import Callable, Mapping

_PANE_ID = re.compile(r"^%[0-9]{1,9}$")
_TIMEOUT = 10.0

Runner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


class PaneError(RuntimeError):
    """A tmux pane could not be opened or closed."""


def _run(argv: list[str]) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(  # noqa: S603 - fixed tmux argv, no shell
        argv, capture_output=True, text=True, timeout=_TIMEOUT, check=False
    )


def _check_pane_id(pane_id: str) -> str:
    if not _PANE_ID.fullmatch(pane_id):
        raise PaneError(f"unexpected tmux pane id: {pane_id!r}")
    return pane_id


class TmuxPanes:
    """Split a pane beside the current one and kill it again by id."""

    def __init__(self, runner: Runner = _run) -> None:
        self._runner = runner

    @staticmethod
    def available(host_env: Mapping[str, str]) -> bool:
        """tmux panes are usable only when the host is itself inside tmux."""
        return bool(host_env.get("TMUX"))

    def open(self, argv: list[str]) -> str:
        if not argv:
            raise PaneError("no command to run in the pane")
        result = self._runner(
            ["tmux", "split-window", "-h", "-d", "-P", "-F", "#{pane_id}", "--", *argv]
        )
        if result.returncode != 0:
            raise PaneError(f"tmux split-window failed: {result.stderr.strip()}")
        return _check_pane_id(result.stdout.strip())

    def close(self, pane_id: str) -> None:
        result = self._runner(["tmux", "kill-pane", "-t", _check_pane_id(pane_id)])
        if result.returncode != 0:
            raise PaneError(f"tmux kill-pane failed: {result.stderr.strip()}")
