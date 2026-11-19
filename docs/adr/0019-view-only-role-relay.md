---
status: proposed
date: 2026-11-19
reviewers: []
---
# 0019. Show every role through a host-owned relay, view-only except the test writer

## Context
ADR 0014 relays only the reference to a pane. The operator wants all three roles visible in one
GUI (T3 Code terminal panels). A GUI thread whose agent is a role would run outside the sandbox
and could read hidden tests (ADR 0003, 0009), so the roles must stay host-owned. This extends
ADR 0014; it supersedes none.

## Options
- **Agent per GUI thread**: the role's agent runs in the GUI's process; breaks ADR 0003/0009.
- **Sandboxed launch from the GUI**: needs a non-pty session mode and a GUI custom launcher.
- **Host-owned relay for every role**: the GUI shows what `cheeseless pane-relay` prints.

## Decision
Host-owned relay for every role, because it reuses the ADR 0014 boundary and adds no agent.
- The host keeps owning each sandbox and pty; the relay holds no secrets and takes only its
  socket path. No GUI process, socket or fd enters a sandbox.
- `pane-relay --view-only`: the host-side relay drops client input frames, not only the client.
  The reference and coder panes use it; the test writer pane stays interactive (spec interview).
- Phase changes, the lock and operator decisions stay host CLI decisions (ADR 0009, 0013, 0017).
- The host lists role to socket path and mode in a closed event (`planning/phase-view-design.md`).

## Consequences
- ✅ All roles visible without moving any agent out of its sandbox.
- ⚠️ Scrollback in the GUI holds what a role printed (hidden tests for the test writer); it must
  never be fed to another agent.
- ⚠️ The relay for coder and test writer is new code to secure; same-UID agents still reach the store.
