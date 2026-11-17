"""Open or resume a ``cheeseless dev`` session (ADR 0013).

A new session starts in the spec phase. ``--resume`` continues an open one: the author
gets its own session back, while the implementer always starts fresh.
"""

from cheeseless.domain.dev_session import DevSessionError, DevState, ResumeTarget, resume_target
from cheeseless.ports.dev_session import DevSessionStore


def open_session(
    store: DevSessionStore, lineage_id: str, *, resume: bool
) -> tuple[DevState, ResumeTarget]:
    existing = store.dev_state(lineage_id)
    if resume:
        if existing is None:
            raise DevSessionError(f"nothing to resume for {lineage_id}: start a new session")
        return existing, resume_target(existing)
    if existing is not None and not existing.terminal:
        raise DevSessionError(
            f"a dev session for {lineage_id} is already open ({existing.phase.value}): "
            "continue it with --resume or abort it"
        )
    version = 0 if existing is None else existing.version
    saved = store.save_dev_state(DevState(lineage_id, version=version))
    return saved, resume_target(saved)
