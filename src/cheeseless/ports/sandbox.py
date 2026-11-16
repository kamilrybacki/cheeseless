"""Candidate sandbox port (ADR 0003, D4, D12)."""

from collections.abc import Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class SandboxError(Exception):
    """The sandbox could not be set up. Callers fail closed."""


@dataclass(frozen=True, slots=True)
class StepOutcome:
    ok: bool
    exit_code: int
    stderr_tail: str = ""


class Sandbox(Protocol):
    def build(
        self, workdir: Path, steps: Sequence[Sequence[str]], env: Sequence[tuple[str, str]]
    ) -> StepOutcome:
        """Run build steps with ``workdir`` writable, no network, no sockets."""
        ...

    def launcher(
        self, workdir: Path, env: Sequence[tuple[str, str]], scratch: Path
    ) -> tuple[str, ...]:
        """argv prefix that runs one candidate command on a throwaway overlay."""
        ...

    def scratch(self) -> AbstractContextManager[Path]:
        """A fresh private directory for launcher overlays, removed on exit."""
        ...

    def trusted_scratch(self) -> AbstractContextManager[Path]:
        """A fresh private tmpfs-backed directory for hidden tests, removed on exit."""
        ...


@dataclass(frozen=True, slots=True)
class AuthorOutcome:
    exit_code: int
    stdout: str = ""
    stderr_tail: str = ""
    timed_out: bool = False


# Set by the author runner to the read-only inside path of the reference, when mounted.
AUTHOR_REFERENCE_ENV = "CHEESELESS_AUTHOR_REFERENCE"


class AuthorSandbox(Protocol):
    def author_run(
        self,
        acceptance: Path,
        reference: Path | None,
        command: Sequence[str],
        *,
        env: Sequence[tuple[str, str]],
        timeout: float = 300.0,
    ) -> AuthorOutcome:
        """Run ``command`` with the unapproved ``acceptance`` tree read-only.

        New user, PID, IPC and network namespaces; no trusted store and no repo write;
        ``reference`` (when given) is mounted read-only. Output is advisory.
        """
        ...
