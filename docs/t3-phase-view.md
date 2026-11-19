# Phase view for T3 panels

`cheeseless dev-events` prints the state of one lineage as newline-delimited JSON. A viewer
(a T3 panel or a terminal) reads it and draws the current stage. The command is host-only and
read-only: it is not on MCP or UDS, and it writes nothing. Part B (per-role panes) is not
implemented; see `planning/phase-view-design.md`.

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
