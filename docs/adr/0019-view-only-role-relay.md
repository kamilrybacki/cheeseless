---
status: proposed
date: 2026-11-19
reviewers: []
---
# 0019. Relay every role to a GUI pane; only the test writer pane takes input

## Context
ADR 0014 relays only the reference to a pane. The operator wants all three roles visible in one
GUI (T3 Code terminal panels). A GUI thread whose agent is a role would run outside the sandbox
and could read hidden tests (ADR 0003, 0009), so the roles must stay host-owned. This extends
ADR 0014; it supersedes none.

## Options
- **Agent per GUI thread, GUI orchestrator above**: role agents run in the GUI's process, outside
  bwrap (breaks ADR 0003/0009), and the orchestrator thread takes phase control (breaks ADR 0013).
- **Sandboxed launch from the GUI**: needs a non-pty session mode and a GUI custom launcher.
- **Host-owned relay per role**: the GUI shows what `cheeseless pane-relay` prints.

## Decision
Host-owned relay per role, because it reuses the ADR 0014 boundary and adds no agent.
- The host keeps owning each sandbox and pty; the relay holds no secrets and takes only its
  socket path. No GUI process, socket or fd enters a sandbox.
- Modes: the test writer pane is interactive (the spec interview). The reference and coder panes
  are **view-only**: the host-side relay reads no client frame into the pty, neither `Data` nor
  `Resize`, on every connection including reconnects. The host alone sets the pty size. A
  client frame in view-only mode closes that connection; the session keeps running.
- Output boundary: the reference pane prints only the test-free reference sandbox (ADR 0009).
  The coder pane prints only what the coder's sandbox holds (no hidden tests, ADR 0003) and
  disclosure-filtered verdicts; it can hold visible tests and code.
- Phase changes, the lock and operator decisions stay host CLI decisions (ADR 0009, 0013, 0017).
- The host lists role to socket path and mode in a closed event (`planning/phase-view-design.md`).

## Consequences
- ✅ All roles visible without moving any agent out of its sandbox.
- ⚠️ GUI scrollback holds what a role printed (hidden tests for the test writer, visible tests
  for the coder); it must never be fed to any agent, the reference above all.
- ⚠️ The relay for coder and test writer is new code to secure; same-UID agents still reach the store.
