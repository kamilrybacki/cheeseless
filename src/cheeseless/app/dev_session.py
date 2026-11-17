"""Open or resume a ``cheeseless dev`` session (ADR 0013).

A new session starts in the spec phase. ``--resume`` continues an open one: the author
gets its own session back, while the implementer always starts fresh.

``record_spec_done`` closes the spec phase: it binds the session to the digest of the
spec the author committed and moves it to the tests phase.
"""

from cheeseless.app.authoring import spec_of
from cheeseless.app.freeze import read_contract
from cheeseless.domain.dev_session import DevSessionError, DevState, ResumeTarget, resume_target
from cheeseless.ports.dev_session import DevSessionStore
from cheeseless.ports.repository import Repository


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


def record_spec_done(
    repo: Repository, store: DevSessionStore, source: str, lineage_id: str
) -> DevState:
    """Bind the dev session of ``lineage_id`` to the spec at ``source``; refused unless in SPEC."""
    contract = read_contract(repo, source)
    if contract.lineage_id != lineage_id:
        raise DevSessionError("the contract belongs to another lineage than this dev session")
    if contract.spec is None or not contract.spec.strip():
        raise DevSessionError("write the spec (spec.md) before closing the spec phase")
    state = store.dev_state(lineage_id)
    if state is None:
        raise DevSessionError(f"no dev session is open for {lineage_id}")
    return store.save_dev_state(state.spec_done(spec_of(contract)))
