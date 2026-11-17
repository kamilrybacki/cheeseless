"""The ``cheeseless dev`` host loop: preconditions and phase handover (ADR 0013).

The orchestrator owns no trust decision of its own. It sequences the moves the domain
already allows or forbids (:mod:`cheeseless.domain.dev_session`), and refuses before
anything is staged. Every function here runs on the host; an agent cannot reach one, and
the values it returns are the arguments the host then hands to ``host_session``.

ADR 0013 keeps the phase changes and the freeze a host decision, so this module never
asks an agent what to do next: it reads the stored phase and says who runs next.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from cheeseless.domain.dev_session import DevPhase, DevSessionError, resume_target
from cheeseless.ports.dev_session import DevSessionStore

CREDENTIAL_VAR = "CLAUDE_CODE_OAUTH_TOKEN"
CREDENTIAL_HINT = "claude setup-token"


class DevFlowError(Exception):
    """A precondition of the dev loop does not hold; nothing has been staged."""


def require_clean_tree(dirty: Callable[[], bool] | bool) -> None:
    """Refuse a dirty working tree (ADR 0013: clean tree only).

    ``dirty`` is a callable or a bool so this layer stays free of git I/O: the caller
    passes ``ctx.repo.is_clean`` or a boolean.
    """
    is_dirty = dirty() if callable(dirty) else dirty
    if is_dirty:
        raise DevFlowError("the working tree is not clean: commit or stash before `cheeseless dev`")


def require_token(value: str | None) -> str:
    """The author's own credential, from the environment only (ADR 0013).

    A credentials file in the repo is never read. The message names the command that
    produces a token, and the token itself is never echoed. Surrounding whitespace is
    stripped for the emptiness check but the value is returned untouched; an embedded
    control character is refused, because it would be forwarded into a sandbox env where
    a NUL truncates the value or makes the exec fail, and it is never part of a token.
    """
    if value is None or not value.strip():
        raise DevFlowError(
            f"{CREDENTIAL_VAR} is not set: run `{CREDENTIAL_HINT}` and export it "
            "before `cheeseless dev`"
        )
    if any(character < " " or character == "\x7f" for character in value):
        raise DevFlowError(
            f"{CREDENTIAL_VAR} contains a control character: re-export a clean one"
        )
    return value


@dataclass(frozen=True, slots=True)
class PhaseStart:
    """What the host needs to launch the next session of a dev lineage.

    ``dev_lineage`` is set only for an author session (that flag is what gives the agent
    ``spec_done``); ``reference`` asks the host to run a reference attempt first, because
    the tests phase cannot close without one.
    """

    role: str
    dev_lineage: str | None
    session: str | None
    spec_digest: str | None
    reference: bool


def start_phase(store: DevSessionStore, lineage_id: str) -> PhaseStart:
    """Who runs next for ``lineage_id``, from the stored phase alone.

    Refuses a terminal, blocked or disputed phase with the reason the domain recorded.
    """
    state = store.dev_state(lineage_id)
    if state is None:
        raise DevFlowError(f"no dev session is open for {lineage_id}")
    try:
        target = resume_target(state)
    except DevSessionError as exc:
        raise DevFlowError(str(exc)) from exc
    author = target.role == "author"
    return PhaseStart(
        role=target.role,
        dev_lineage=lineage_id if author else None,
        session=target.session,
        spec_digest=state.spec_digest,
        # A reference attempt belongs to the tests phase only: the spec phase has no
        # digest yet, and the implement phase must not see the reference at all.
        reference=state.phase is DevPhase.TESTS,
    )


__all__ = [
    "CREDENTIAL_HINT",
    "CREDENTIAL_VAR",
    "DevFlowError",
    "PhaseStart",
    "require_clean_tree",
    "require_token",
    "start_phase",
]
