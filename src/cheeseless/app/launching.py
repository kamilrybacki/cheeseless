"""Resolve what a launch actually runs, from operator config rather than from typed labels."""

from collections.abc import Sequence

from cheeseless.domain.launcher import Launcher, LauncherError


def resolve_launch(
    launcher: Launcher | None,
    caller_command: Sequence[str],
    label: str | None = None,
) -> tuple[list[str], str]:
    """The ``(command, model)`` the host runs and records.

    With a launcher, the configured command runs and the configured model is recorded; a
    caller-supplied command or model label that disagrees is refused, not quietly overridden,
    because a mismatch means the caller believes something else is about to run.

    Without one the caller's command runs but **no model is recorded**: a label somebody typed
    is not provenance, so such a launch can never satisfy the unattended-freeze vendor check.
    """
    command = [a for a in caller_command if a != "--"]
    if launcher is None:
        return command, ""
    if command and tuple(command) != launcher.command:
        raise LauncherError(
            f"the command given after -- contradicts launcher {launcher.name!r}; drop one of them"
        )
    if label is not None and label != launcher.model:
        raise LauncherError(
            f"--reviewer-model {label!r} contradicts launcher {launcher.name!r} "
            f"(configured model {launcher.model!r})"
        )
    return list(launcher.command), launcher.model
