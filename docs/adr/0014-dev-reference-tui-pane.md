---
status: accepted
date: 2026-11-17
reviewers: [gpt-6.1-sol, claude-sonnet-5-5]
---
# 0014. Run the dev-mode reference as a visible TUI in a second pane

## Context
ADR 0013 point 2 runs the reference headless. The user wants to watch and record every agent in
the real Claude Code TUI. The reference must stay test-free, bound to one spec, host-owned.

## Options
- **TUI pane**: an interactive reference TUI in a second pane of the user's tmux, beside the author.
- **Headless with a live log**: `claude -p` as in ADR 0013, its stream shown read-only in a pane.
- **Headless, hidden**: unchanged ADR 0013, the author TUI shows only `reference_pending`.

## Decision
TUI pane, because only the real TUI shows what the reference does. Supersedes ADR 0013 point 2.
- **Host owns the process.** The host starts the sandbox (bwrap `--clearenv --die-with-parent`,
  pinned PID namespace) on a pty it holds; the pane runs only a host-side relay to that pty. No
  tmux socket, `TMUX*` variable or pane fd enters any sandbox. Closing the pane stops nothing.
- **Attempt record.** Each launch stores attempt id, session id, generation, spec digest and the
  `spec_done` snapshot as the only input (the first prompt, as a staged file). State moves only by
  compare-and-set: `running → stopping → done`, or `running|stopping → failed|cancelled`.
- **`reference_done`** takes no arguments and is listed and routed (`_REFERENCE_KINDS`) only for a
  session whose record holds a running TUI dev attempt, with the namespace, record and generation
  checks. It narrows ADR 0009 "no gate tools" to this one signal; plain references have none.
- **Ordering.** Only the winner of `running → stopping` stops the sandbox (TERM, then KILL after a
  bounded wait), snapshots, then sets `done`. A stale or losing call never snapshots.
- **Failure.** Sandbox exit without the tool, timeout or a lost host: `failed` (infra). A new spec
  digest: `cancelled`. Neither keeps a result. The next host start reaps stale attempts and panes.
- **Fallback**, chosen only before launch (no tmux, or `--headless-reference`): same record and
  ordering, no tool; a zero exit starts `stopping`. Evidence records `mode` and pane attach.

## Consequences
- ✅ Recordings show both agents working at once, in the real TUI.
- ⚠️ Accepted risk: anyone at the pane can steer the reference; input and scrollback are not
  captured. `mode` and attach mark such runs as steerable. The agent still sees no tests.
- ⚠️ An early `reference_done` on an empty tree fails the crosscheck; idle time counts too.
- ⚠️ tmux becomes optional; the relay and the attempt state machine are new code to secure.
