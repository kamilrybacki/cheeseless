"""``cheeseless dev``: the host command that drives one dev lineage (ADR 0013).

This module only *sequences*. It asks :mod:`cheeseless.app.dev_flow` who runs next and
what the phase requires, then hands the session to the existing composition root
(``host_session``) with the role and flags that phase allows. Nothing here decides a
trust question: the domain refuses a move out of order, and the phase decides the role.
An agent can reach none of it.

Stopping points, on purpose:

* the spec phase ends when the agent calls ``spec_done`` (the host store records it);
* the tests phase needs a reference attempt, which ADR 0014 runs as its own host process
  (``cheeseless dev-reference``), so this command reports what to run next rather than
  nesting a second sandbox;
* the implement phase is a fresh implementer with no reference and no author session.

Exit codes: 0 the phase's session ended normally, 1 a refusal or a failed session,
2 usage.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from typing import Any, cast

from cheeseless.app.dev_flow import (
    CREDENTIAL_VAR,
    DevFlowError,
    PhaseStart,
    reference_ready,
    require_clean_tree,
    require_token,
    start_phase,
)
from cheeseless.app.dev_session import open_session
from cheeseless.bootstrap import build_context, host_session
from cheeseless.domain.dev_session import DevPhase, DevSessionError, DevState
from cheeseless.ports.dev_session import DevSessionStore
from cheeseless.ports.reference_attempt import ReferenceAttemptStore

# Only the author interviews the user, so only it needs the user's own CLI credential.
_AUTHOR_ROLE = "author"


def _emit(args: argparse.Namespace, payload: dict[str, Any], text: str) -> None:
    if getattr(args, "json", False):
        print(json.dumps(payload, sort_keys=True))
    else:
        print(text)


def _next_instruction(start: PhaseStart, lineage: str) -> str:
    if start.reference:
        return (
            "run the reference for this spec, then resume:\n"
            f"  cheeseless dev-reference --lineage {lineage} "
            f"--spec-digest {start.spec_digest} -- <reference agent>"
        )
    if start.role == _AUTHOR_ROLE:
        return "the author interviews you and writes the spec; the phase ends on /spec-done"
    return "the implementer works against the frozen gate; it never sees the tests"


def _preflight(
    args: argparse.Namespace, agent: Sequence[str] | None
) -> tuple[str, DevState, PhaseStart, DevSessionStore]:
    """Everything that must hold before a phase is staged; raises ``DevFlowError``."""
    lineage = args.lineage
    ctx = build_context(getattr(args, "state_dir", None), None)
    require_clean_tree(ctx.repo.is_clean)
    if not agent:
        raise DevFlowError("no agent command: put it after --, e.g. `cheeseless dev -- claude`")
    store = cast(DevSessionStore, ctx.store)
    state, _target = open_session(store, lineage, resume=bool(args.resume))
    start = start_phase(store, lineage)
    if start.role == _AUTHOR_ROLE:
        # The author's credential comes from the environment only (ADR 0013).
        require_token(os.environ.get(CREDENTIAL_VAR))
    return lineage, state, start, store


def run_dev_command(args: argparse.Namespace, agent: Sequence[str] | None) -> int:
    """Drive one dev phase. ``agent`` is the argv after ``--``, as for ``session``."""
    try:
        lineage, state, start, store = _preflight(args, agent)
    except DevFlowError as exc:
        print(f"dev: {exc}", file=sys.stderr)
        return 1
    except DevSessionError as exc:
        print(f"dev: {exc}", file=sys.stderr)
        return 1

    _emit(
        args,
        {
            "lineage": lineage,
            "phase": state.phase.value,
            "role": start.role,
            "spec_digest": start.spec_digest,
            "reference_required": start.reference,
        },
        f"{lineage}: {state.phase.value} phase, running the {start.role}\n"
        + _next_instruction(start, lineage),
    )
    if start.reference:
        return _tests_phase(args, lineage, start, store)
    return _run_session(args, start, list(agent or ()), lineage, store)


def _tests_phase(
    args: argparse.Namespace, lineage: str, start: PhaseStart, store: DevSessionStore
) -> int:
    """The tests phase cannot close without a done reference (ADR 0013 point 3).

    The reference is its own host process (ADR 0014), so this reports the next command
    rather than nesting a second sandbox. It still never approves a freeze: the automatic
    approval needs a per-proposal `StrengthReview`, and nothing in this phase has run a
    reviewer yet. `cheeseless freeze --approve … --auto` is where that gate applies
    (ADR 0015, D13-6a).
    """
    digest = start.spec_digest or ""
    try:
        ready = reference_ready(cast(ReferenceAttemptStore, store), lineage, digest)
    except DevFlowError as exc:
        print(f"dev: {exc}\n{_next_instruction(start, lineage)}", file=sys.stderr)
        return 1
    _emit(
        args,
        {
            "lineage": lineage,
            "reference_ready": ready,
            "spec_digest": digest,
            "strength_review": (
                "cheeseless strength-review --run --task <proposal> "
                "--author-model <as launched> --reviewer-model <as launched> -- <reviewer agent>"
            ),
        },
        "the reference is done for this spec:\n"
        "  1. `cheeseless freeze` proposes the tests and prints the proposal id\n"
        "  2. `cheeseless strength-review --run --task <proposal> ...` has another vendor "
        "review them, and refuses to record anything if it writes no verdict\n"
        "  3. `cheeseless freeze --approve <proposal> --auto` freezes, refusing without "
        "that review",
    )
    return 0 if ready else 1


def _phase_of(store: DevSessionStore, lineage: str) -> str:
    state = store.dev_state(lineage)
    return state.phase.value if state is not None else "unknown"


def _run_session(
    args: argparse.Namespace,
    start: PhaseStart,
    agent: Sequence[str],
    lineage: str,
    store: DevSessionStore,
) -> int:
    """Launch the one session this phase allows, with the flags that phase grants.

    ``dev_lineage`` is set only for an author, and that one flag is what gives the agent
    ``spec_done``; the implementer is started fresh and never inherits a session id.
    """
    try:
        with host_session(
            state_dir=getattr(args, "state_dir", None),
            agent_command=list(agent),
            role=start.role,
            dev_lineage=start.dev_lineage,
        ):
            # The session runs to completion inside the context; the host tears down.
            pass
        if start.role == "implementer":
            _emit(
                args,
                {"lineage": lineage, "phase": _phase_of(store, lineage)},
                _implement_report(store, lineage),
            )
    except (ValueError, RuntimeError) as exc:
        print(f"dev: {exc}", file=sys.stderr)
        return 1
    except DevSessionError as exc:
        print(f"dev: {exc}", file=sys.stderr)
        return 1
    return 0


def _implement_report(store: DevSessionStore, lineage: str) -> str:
    """What the implement phase did, including the count that will force a re-freeze."""
    state = store.dev_state(lineage)
    if state is None:
        return ""
    if state.needs_refreeze:
        return (
            f"the holdout failed {state.holdout_failures} times: re-freeze with the author "
            "or abort; the gate will not judge again"
        )
    if state.phase is DevPhase.DONE:
        return "the implement phase passed: the dev session is done"
    return f"{state.phase.value} phase ({state.holdout_failures} holdout failures so far)"


__all__ = ["run_dev_command"]
