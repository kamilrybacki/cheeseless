"""Port: the operator's trusted launcher declarations."""

from typing import Protocol

from cheeseless.domain.launcher import Launcher


class LauncherCatalog(Protocol):
    def get(self, name: str) -> Launcher:
        """The launcher called ``name``; raises ``LauncherError`` if it cannot be trusted."""
        ...
