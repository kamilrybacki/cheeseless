"""Presence-proof fakes for tests (ADR 0017).

These are injected only through Python construction. Nothing in ``src/`` reads a flag, an
environment variable or a config value that could swap them in.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable

from cheeseless.domain.presence import PresenceConfirmation, PresenceRefused, PresenceRequest


class Approving:
    """Confirms every request and remembers what it was asked."""

    def __init__(self, before_answer: Callable[[], None] | None = None) -> None:
        self.asked: list[PresenceRequest] = []
        self._before_answer = before_answer

    def confirm(self, request: PresenceRequest) -> PresenceConfirmation:
        self.asked.append(request)
        hook, self._before_answer = self._before_answer, None
        if hook is not None:
            hook()  # the world moves on while the operator is typing
        return PresenceConfirmation(request, secrets.token_hex(16))


class Refusing:
    """Refuses every request, as the terminal adapter does without a controlling tty."""

    def __init__(self) -> None:
        self.asked: list[PresenceRequest] = []

    def confirm(self, request: PresenceRequest) -> PresenceConfirmation:
        self.asked.append(request)
        raise PresenceRefused("no controlling terminal")


__all__ = ["Approving", "Refusing"]
