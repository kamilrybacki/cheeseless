---
status: proposed
date: 2026-11-13
reviewers: [gpt-6.1-terra]
---
# 0003. Run the agent and the candidate behind our own bwrap sandbox

## Context
Candidates are always behind a process boundary and have no network (ledger Q8). The
previous choice, an off-the-shelf sandbox runtime with a domain allowlist (Q15), cannot
allow a single Unix socket without allowing all of them (SSH agent, docker socket, D-Bus).

## Options
- **Off-the-shelf sandbox runtime**: less code, coarse socket handling; dropped for the MVP (Q39).
- **Own thin `bwrap` wrapper**: PID, IPC and net namespaces; only chosen paths are bound.

## Decision
Own thin `bwrap` wrapper, two profiles (Q39), both with new user/PID/IPC/net namespaces,
fresh `/proc`, all capabilities dropped, `--new-session`, `--die-with-parent` (ledger D4).
- Agent: binds the projection, its session's verifier socket and the egress socket only.
- Candidate: binds no socket at all and has no network (Q8); it talks to the runner over
  inherited pipes or a socket inside its own sandbox.
- Each candidate run uses a throwaway overlay, scrubbed env, and the whole process tree
  is killed; nothing it writes is agent-visible (Q40).
- Trusted runner closure (Q36): tests, conftest, pytest config and runner deps come only
  from the trusted store/base (`$XDG_STATE_HOME/cheeseless/<repo-id>/`, Q55); the candidate is built in its own venv;
  `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`; `rootdir`/`confcutdir` pinned; runner is `python -I`
from a trusted venv, env and `PYTEST_ADDOPTS` scrubbed, candidate never on its path (D4).
- Fail closed (Q43): any infra error is FAIL; no isolation-downgrade flags.

## Consequences
- ✅ Closes runner hijack, candidate disk exfiltration and socket leaks (criteria 14-16, 18).
- ✅ Verifier never imports candidate code (AGENTS.md).
- ⚠️ We own the wrapper's security; Linux/WSL2 only (Q3). Same host UID: an escape
  from a sandbox reaches the user's files (residual, ADR 0001).
- ⚠️ A hard sandbox failure means no verdict, by design.
