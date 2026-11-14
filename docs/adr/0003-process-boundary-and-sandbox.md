---
status: accepted
date: 2026-11-13
reviewers: [gpt-6.1-terra, gpt-6.1-sol]
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
- Candidate: binds no socket and has no network (Q8). Throwaway overlay, scrubbed env,
  whole process tree killed per run; nothing it writes is agent-visible (Q40).
- Execution boundary (I3): hidden tests stay in the runner, outside every sandbox; only
  the candidate build and candidate processes run in the candidate sandbox, driven
  black-box over pipes, argv and files the runner chooses.
- Trusted runner closure (Q36): tests, conftest, pytest config and runner deps come only
  from the trusted store/base (`$XDG_STATE_HOME/cheeseless/<repo-id>/`, Q55); the candidate
  is built in its own venv. The runner is `python -I` from a trusted venv with plugin
  autoload off, pinned `rootdir`/`confcutdir`, scrubbed env and `PYTEST_ADDOPTS` (D4).
- Runner isolation (D12): runner, tmpfs and hidden inputs are outside the candidate's PID and
  mount namespaces; `PR_SET_DUMPABLE=0`; seccomp denies `ptrace`, `process_vm_*`, `pidfd_getfd`.
- Fail closed (Q43): an infra error is never PASS; the cause is stored for the operator,
  the agent sees only what ADR 0008 allows (D17); no isolation-downgrade flags.

## Consequences
- ✅ Closes runner hijack, candidate disk exfiltration and socket leaks (criteria 14-16, 18).
- ✅ Verifier never imports candidate code (AGENTS.md).
- ⚠️ A hard sandbox failure means no verdict, by design.
- ⚠️ We own the wrapper's security; Linux/WSL2 x86_64 only (Q3, I2). Same host UID: an
  escape from a sandbox reaches the user's files (residual, ADR 0001).
