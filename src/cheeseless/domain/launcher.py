"""A named, operator-declared way to launch an agent (pure types, no I/O)."""

import re
from dataclasses import dataclass

_NAME = re.compile(r"[a-z0-9._-]+")


class LauncherError(ValueError):
    """The launcher config is unusable, or a launch contradicts it. Always a refusal."""


def check_launcher_name(name: object) -> str:
    if not isinstance(name, str) or _NAME.fullmatch(name) is None:
        raise LauncherError(f"launcher name {name!r} must match [a-z0-9._-]+")
    return name


@dataclass(frozen=True, slots=True)
class Launcher:
    """What the operator declared for one launcher: the command to run and its model.

    The model is the operator's declaration, not something cheeseless can observe.
    """

    name: str
    command: tuple[str, ...]
    model: str

    def __post_init__(self) -> None:
        check_launcher_name(self.name)
        if not self.command or not all(isinstance(a, str) and a for a in self.command):
            raise LauncherError(f"launcher {self.name!r}: command must be non-empty strings")
        if not isinstance(self.model, str) or not self.model.strip():
            raise LauncherError(f"launcher {self.name!r}: model must be a non-empty string")
