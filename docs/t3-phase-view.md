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

### Test-writer pane: supported only with `persistHistory:false`

The test-writer pane shows the hidden tests as they are written. **Support it only on a T3
build with command terminals and `persistHistory:false`** (a fork patch is in progress). On
stock T3 that pane's scrollback is persisted to disk and can be added to a chat, which leaks
the hidden tests to an agent. On stock T3, use only the `reference` and `coder` panes, and
keep the test-writer in your own terminal.
