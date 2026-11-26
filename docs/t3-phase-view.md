# Phase view for T3 panels

`cheeseless dev-events` prints the state of one lineage as newline-delimited JSON. A viewer
(a T3 panel or a terminal) reads it and draws the current stage. The command is host-only and
read-only: it is not on MCP or UDS, and it writes nothing. Part B (per-role panes, ADR 0019)
is described in [Role panes](#role-panes); the design is in `planning/phase-view-design.md`.

## The contract

- One JSON object per line, schema `docs/schemas/dev-event-v1.json` (closed: unknown fields
  fail validation, and every enum is closed).
- Kinds: `snapshot` (first line), `change` (the view changed), `heartbeat` (no change for 15
  seconds), `end` (the stage is `done` or `aborted`; the stream closes), `error` (the store is
  corrupt; a fixed line with no detail, and the stream closes).
- Fields: `v`, `kind`, `observed_at`, `lineage`, and when there is a view: `stage`, `actor`,
  `awaiting`, `reference`, `holdout_failures`, `open_disputes`, `state_version`, `hint`.
- Nothing free-form is ever printed. There are no digests, session or attempt ids, paths,
  test names, dispute notes, transcripts or error text. Every value is an enum member or an
  integer.

## Recipe

```bash
# one snapshot
cheeseless dev-events --lineage <lineage> --once

# a live stream; exits by itself at end or error
cheeseless dev-events --lineage <lineage> --follow --interval 1.0
```

The viewer should:

1. Read stdout line by line and parse each line against the schema. Drop lines that fail.
2. Keep only the latest line with a view; render `stage`, `actor`, `awaiting`, `reference`
   and `open_disputes`.
3. Stop when it sees `end` or `error`, or when the process exits.

Exit codes: `0` normal (also a closed pipe), `1` after an `error` line (including state that
vanishes mid-`--follow`, a store or I/O error, or a value outside the schema bounds), `2` for bad
usage (a write flag such as `--as`, a bad lineage, `--once` with `--follow`, `--interval` outside
`0 < interval <= 60`, or running outside a git repository), `3` when `--once` names a lineage with
no state (nothing is printed), `130` when interrupted.

`--follow` on a lineage with no state yet waits silently until state appears, so a viewer can start
before the session does. If state was seen and then disappears, it prints the fixed `error` line
and exits `1`.

## Render spec example

A panel can be described with fields only, never with text from the store:

```json
{
  "title": "phase",
  "rows": [
    {"label": "stage", "value": "<stage from the latest line>"},
    {"label": "waiting on", "value": "<awaiting, shown as text from a fixed table>"},
    {"label": "open disputes", "value": "<open_disputes as an integer>"}
  ]
}
```

Map each enum value to a fixed label in the viewer. Do not interpolate anything else.

## Listing lineages

A panel that does not know the lineage yet can list them:

```sh
cheeseless dev-lineages [--state-dir DIR]
```

It is host-only and read-only. It writes nothing, and it is not exposed on MCP or UDS. It
prints exactly one line, which validates against
[`schemas/dev-lineages-v1.json`](schemas/dev-lineages-v1.json):

```json
{"schema":1,"lineages":[{"lineage":"demo","stage":"code","updated_at":"2026-11-20T09:15:02Z"}]}
```

The list is newest first, at most 100 entries. `stage` is the same enum `dev-events` reports,
and `updated_at` is when the lineage last changed on disk, in whole seconds, UTC. There are no
paths, digests or free text.

| Exit | Meaning |
| --- | --- |
| 0 | the listing was printed; an empty list is normal |
| 1 | the store could not be read; prints exactly `{"schema":1,"error":"store"}` |
| 2 | bad usage, or not inside a git repository |

## Rules for viewers

- **Do not trust `hint`.** It is a message key such as `run_dev_resume`, and the viewer shows
  it as a label. The viewer never runs it, never builds a command from it, and never feeds it
  to an agent.
- **`awaiting` is a prompt for a person, not a command.** Show it as "run this in your own
  terminal" with the operator's own command; the viewer does not run anything.
- **Never give a viewer thread a role pane's scrollback or the stream of another agent.** The
  viewer reads only the dev-events lines.
- **Read-only.** A viewer must not write to the state directory or call a write command.

## Role panes

Each role can show its live terminal in a T3 terminal pane. The host owns the pty and a
relay socket (ADR 0014); the pane is only a client of that socket. Nothing GUI-side enters a
sandbox.

| Role | Pane mode | Input |
| --- | --- | --- |
| test writer (`test-writer`) | interactive | the operator types into the agent |
| reference coder (`reference`) | view only | none |
| coder (`coder`) | view only | none |

A view-only relay reads no client frame into the pty, neither input nor resize, on any
connection including reconnects. The host alone sets the pty size. Any client frame closes
that connection only; the session keeps running, and closing a pane stops nothing.

### Starting a pane

`cheeseless dev --panes` runs the phase's agent on a host pty and prints one `panes` event on
**stderr** (not in `dev-events`, whose closed schema has no paths):

```json
{"event":"panes","lineage":"demo","panes":[{"mode":"interactive","role":"test-writer","socket":"<state>/cheeseless/panes/demo/test-writer.sock"}],"schema":1}
```

The author phase announces the `test-writer` pane, the implement phase announces `coder`. A
TUI reference attempt serves the `reference` pane at its role path too, but does not print a
`panes` event; its own tmux pane already runs the viewer. The socket path is
`<state>/cheeseless/panes/<lineage>/<role>.sock`: it holds no digest, no session id and no
hidden-test information, and it never leaves the state directory. The pane client resolves it
itself:

```sh
cheeseless pane-relay --role coder --lineage demo           # view only, from the role
cheeseless pane-relay --role test-writer --lineage demo     # interactive
cheeseless pane-relay --socket /path/to/x.sock [--view]     # the older form still works
```

`--view` is allowed only with `--socket`; a role fixes its own mode. Use `--state-dir` if the
host was started with one.

A pane opened before the host has started can wait for its socket:

```sh
cheeseless pane-relay --role coder --lineage demo --wait 120
```

`--wait SECONDS` (more than 0, at most 600) polls until the socket path exists, then connects.
It waits only for the path to exist. A socket that exists but refuses the connection, in the
short window between the host binding it and listening, or because it is stale, is not retried
and exits 1.

| `pane-relay` exit | Meaning |
| --- | --- |
| 0 | the relay closed the connection (the host session ended) |
| 1 | cannot connect, or a relay or terminal error |
| 2 | bad usage |
| 3 | `--wait` ran out before the socket appeared: the host has not started yet |
| 130 | interrupted while waiting |

### Example T3 project scripts

One script per role. Each is a terminal command the T3 project runs; replace `demo` with the
lineage.

```sh
# test-writer: interactive
cheeseless pane-relay --role test-writer --lineage demo
# reference: view only
cheeseless pane-relay --role reference --lineage demo
# coder: view only
cheeseless pane-relay --role coder --lineage demo
```

On a T3 build with command terminals, declare each script as a terminal command, so it runs
from `argv` with no shell and no history on disk. `command` is then only the display label:

```json
{
  "id": "cheeseless-test-writer",
  "name": "Test writer",
  "command": "cheeseless pane-relay --role test-writer --lineage demo",
  "icon": "test",
  "runOnWorktreeCreate": false,
  "runAs": "terminal-command",
  "argv": ["cheeseless", "pane-relay", "--role", "test-writer", "--lineage", "demo"]
}
```

The `reference` and `coder` scripts have the same shape, with only their `--role` changed.
`argv` is required with `runAs`, and its first entry names the program.

### Test-writer pane: supported only as a command terminal

The test-writer pane shows the hidden tests as they are written. **Run it only as a
`runAs: "terminal-command"` script on a T3 build with command terminals.** Such a build
enforces these rules on the server:

- A command terminal never writes history to disk.
- A command terminal keeps the command it was launched with. It cannot be turned into a shell
  by a plain attach.
- "Add to chat" stays hidden for a terminal without history.

On stock T3, that pane's scrollback is saved to disk and can be added to a chat, which leaks
the hidden tests to an agent. There, use only the `reference` and `coder` panes and keep the
test-writer in your own terminal.

One gap remains. The server forgets which terminals were command terminals when it restarts.
After a restart, a plain attach can open a shell in that terminal's slot. The shell holds only
its own new output, never the earlier test-writer output, but close the test-writer pane
before restarting T3 anyway.

### Native cheeseless tab (fork build only)

A T3 fork build has a native **cheeseless** tab in the right panel. It replaces the three
project scripts above with one surface: a phase header fed by `dev-events` and three role
panes. The coder and reference panes are view-only; the test-writer pane is interactive and
isolated. Pane traffic is not persisted, not traced, not exposed through MCP and cannot be
added to chat. Panes are keyed by project, lineage, role and generation, and the server runs
`dev-lineages`, `dev-events --follow` and `pane-relay --wait` itself, with argv built from
enums only.

Status: the tab is implemented and covered by canary tests (a real server, a real browser and
a mutation runner). Its evidence and limits are in the fork's
`apps/server/src/cheeseless/security-canary.md`. Two limits are not closed: there is no real
preview capture against the mounted tab with a non-T3 positive control, and the thread-scoped
MCP tools are checked only for their refusal. A run of a real `cheeseless dev --panes` lineage
through the tab has not been done, so the scripts above stay the documented path until it has.
