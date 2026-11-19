# Phase view and per-role T3 threads: design (not implemented)

Status: proposal for the operator. Terms: see `docs/glossary.md`. Constraints: ADR 0003, 0009,
0013, 0014, 0017.

## Goal

The operator wants to see spec, reference, tests, lock, code and dispute live inside T3 Code
(runs agent threads, renders HTML panels). Part A is a read-only phase view. Part B asks
whether each role can be its own T3 thread.

## Facts this design rests on

- Phase state: `DevState` in the trusted store (`dev/state-<lineage>.json`, `version`
  compare-and-set). Phases: spec, tests, implement, blocked, disputed, done, aborted.
  "Reference" is not a phase; it is a host reference attempt record (ADR 0014) while the phase
  is `spec`/`tests`.
- All phase changes, the lock and dispute resolution are host CLI decisions (ADR 0009/0013);
  the five attended ones need a typed challenge on `/dev/tty` (ADR 0017), which a T3 thread
  agent has no controlling terminal for, so it fails closed.
- Agents run inside host-owned bwrap sandboxes on host-held ptys (ADR 0003/0014). Nothing in a
  sandbox mounts the trusted store.
- A T3 thread agent is a process of T3, running as the operator's UID. That is the threat model's
  "host agent" (ADR 0017): it can read the trusted store, the acceptance tree and the author
  transcript from disk. No T3 permission mode is a sandbox boundary.
- Not verified here: T3 internals (provider launch, panel spec escaping). They are open questions.

## Part A: read-only live phase view

### Options
- **A1 `cheeseless dev-events`**: a new host command that reads the store and prints a closed,
  versioned NDJSON stream. A viewer renders it.
- **A2 Reuse `status --json`**: it is about the latest verified `Result`, not dev phase; extending
  it mixes two concepts and widens a command agents may already call.
- **A3 A T3 agent reads the store directly**: simplest, but then hidden state is in a transcript
  by construction. Rejected.

### Recommendation: A1

`cheeseless dev-events --lineage L [--follow] [--interval 1.0] [--once] [--state-dir D]`.
`--once` prints one snapshot and exits; `--follow` prints a snapshot, then one line per change
and a heartbeat every 15 s, and exits 0 after a terminal phase (done, aborted). It is
host-only: it needs the trusted store, like `disputes`.

### Event schema v1 (closed; `docs/schemas/dev-event-v1.json`, JSON Schema, additionalProperties false)

| field | type | values |
| --- | --- | --- |
| `v` | int | `1` |
| `kind` | enum | `snapshot`, `change`, `heartbeat`, `end` |
| `observed_at` | string | RFC 3339 UTC, formatted by the host |
| `lineage` | string | the lineage id (already on the command line and in branch names) |
| `stage` | enum | `spec`, `reference`, `tests`, `lock`, `code`, `dispute`, `blocked`, `done`, `aborted` |
| `actor` | enum | `test_writer`, `reference_coder`, `coder`, `operator`, `host`, `none` |
| `awaiting` | enum | `none`, `spec_done`, `reference_done`, `lock_approval`, `dispute_resolution`, `run_grant`, `refreeze_or_abort` |
| `reference` | enum | `none`, `running_tui`, `running_headless`, `done`, `failed`, `cancelled` |
| `holdout_failures` | int 0..3 | count only, no criterion names |
| `open_disputes` | int | count only, no ids, codes or notes |
| `state_version` | int | `DevState.version`, monotonic per lineage |
| `hint` | enum | fixed message key, e.g. `run_dev_resume`, `run_freeze_approve`, `run_dispute_resolve` |

Rules:
- Every value but `lineage`, `observed_at` and the integers is an enum member. The encoder takes
  enum members, never free strings; there is no field that can hold agent-, PR- or
  repo-controlled text, so there is nothing to escape. `lineage` already passes `ids.lineage_id`.
- Left out on purpose: spec and proposal digests, session ids, attempt ids, seeds, HMACs, run
  ids and result ids, criterion ids, dispute notes and codes, strength-review text, test names,
  paths, any transcript or error text. If a viewer needs the proposal id, the operator reads it
  in the terminal where ADR 0017 asks for the challenge.
- Timestamps are observation times: the store keeps no transition time. Exact times need a store
  field (`updated_at`, a codec change). Open question Q2; not part of v1.
- Derivation `stage`/`actor`/`awaiting` is a pure function in `domain/dev_events.py` over `DevState`,
  the reference attempt state and two counts. S1 fixes each rule against `dev_flow.py` and the
  freeze reads; unknown or corrupt state yields no event and a fixed `error` line, never a guess.
- Versioning: a new field or enum member is a new `v`; a viewer must ignore a line whose `v` it
  does not know.

### No write path (by construction, then by test)

- The app use case takes a `DevObservation` Protocol with read methods only (`dev_state`,
  reference attempt read, dispute count). It gets no `save_*`, no `PresenceProof`, no `--as`.
  An import-linter contract forbids `dev_events` from importing the write ports.
- It opens the store without creating, locking for write or repairing anything.
- Exposed on the CLI only, never on UDS or MCP (ADR 0009: no operator endpoint there).

### Viewer contract (T3)

A viewer is any process that reads NDJSON on stdin or from `--once` output.
1. **Panel**: a T3 thread whose job is only the panel. Its agent runs `cheeseless dev-events
   --once` at intervals and calls `render_panel` with the fields as data, then `html_render`.
   Because the schema is closed, putting the lines in an agent transcript discloses nothing the
   operator did not already see on the host. This thread has no gate tools and no `dev` access.
2. **Terminal**: the same command with `--follow` in a T3 terminal panel; no agent at all.
Preferred is 2 when T3 offers it, else 1. A viewer must not parse free text, must not link to or
run `hint` as a command, and displays `awaiting` as "run this in your own terminal".

### Security test

`tests/security/test_dev_events_canary.py`:
- Build a store through every stage (spec, reference attempt in each state, lock proposal,
  implement, holdout failure 3, disputed with a note, blocked, done). Plant canaries: a
  `CANARY-NOTE-` dispute note, a hidden test name and body, an author-transcript line, a seed,
  a session id and a strength-review reason. Run `--once` and `--follow` (bounded) and assert
  none of the canaries, no 64-hex string and no path appears in any output byte.
- Every line validates against the JSON schema, and every enum field is a schema member.
- Mutation: hash the store directory before and after; equal. The command with `--as` or a write
  flag exits 2.
- Layer: the import-linter contract above, plus a test that the MCP and UDS tool lists have no
  `dev-events`.

### ADR decision for A: ledger entry only

It adds a read-only projection of state the operator already holds; it changes no accepted
decision. The disclosure boundary (ADR 0005/0008) concerns what gated agents learn; this command
is host-only and its schema is closed, so the guarantee is the schema test, not a policy. An ADR
is warranted only if events ever carry free text or a viewer gets a write path.

## Part B: each role as a separate T3 thread

### Options
- **B1 Thread agents are the roles**: three T3 threads (test writer, reference coder, coder)
  with a T3 orchestrator thread above. Infeasible. The agent runs in T3's process outside bwrap,
  so the coder thread reads hidden tests and the store (ADR 0003/0009 broken), and the
  orchestrator thread would hold phase control (ADR 0013 reverses). T3 permissions and
  worktrees are not a boundary.
- **B2 Sandboxed launch through T3**: the thread's agent is `cheeseless session --role R -- claude`
  (a custom launcher, if T3 can start any executable as a provider). Possible in principle,
  unproven: the Claude and Codex providers speak a structured stdio protocol to T3, while
  `cheeseless session` puts the agent on a host-held pty. Needs a non-pty passthrough mode, a
  per-role fresh projection, role binding the thread cannot change, and the host still driving
  phases. That is a new session mode with its own attack surface (ADR 0014 shows the cost).
- **B3 Viewer/relay panes (recommended)**: the host stays the orchestrator and keeps owning every
  sandbox and pty. Each role appears in T3 as a terminal panel running the existing
  `cheeseless pane-relay --socket <path>` (ADR 0014), beside the Part A panel. No agent runs in
  T3 for a role. The operator types only into the test writer's pane (the spec interview);
  reference and coder panes are view-only.
- **B4 Do nothing in T3**: keep tmux panes. Valid fallback.

### Recommendation: B3, with B2 as a later experiment

It reuses the boundary already accepted: the sandbox, the pty and the relay are host-owned; the
relay holds no secrets and the socket path is its only input.

What T3 must support (all unverified; Q1):
1. A terminal panel (or thread tool) that runs a host command in a given directory and shows it,
   several side by side. The `pane-relay` needs only a tty and a socket path.
2. Passing the socket path: `dev` prints it in `dev-events` or in a `panes` list (S6), never in a
   prompt to an agent.
3. For the Part A panel path 1: a way to keep a thread polling, or `--follow` in a terminal.
4. Nothing for custom providers, worktrees or approval prompts.

Gaps in the code today (slices below): the relay exists only for the reference; the test writer
is the operator's own TUI and the coder runs with its own pty. A view-only relay mode (no stdin
forwarded) is new behaviour for ADR 0014's "anyone at the pane can steer" risk, so it gets a short
ADR draft: `docs/adr/0019-view-only-role-relay.md`. It extends 0014; it supersedes nothing.

### Residual risks of B3
- A T3 terminal runs as the operator's UID; scrollback holds what the agent printed. The reference pane
  is test-free (ADR 0009); the coder pane holds no hidden tests but can hold visible tests and code; for the test writer it holds hidden
  tests, which the operator is allowed to see. T3 must not feed terminal text to another agent
  (Q3): a viewer thread must never be given a role pane's scrollback.
- A thread agent in the same T3 project can still read the store. The view does not make that
  worse, but nothing here prevents it; only a sandboxed launch (B2) or a separate OS user (ADR 0017
  option C) would.

## Implementation slices (TDD; each slice: failing test first, then code)

| # | Slice | Tests |
| --- | --- | --- |
| S1 | `domain/dev_events.py`: closed enums, `derive`, JSON encode | unit: every `DevPhase` x attempt state maps to one stage/actor/awaiting; corrupt input gives the error line; schema file matches the enums |
| S2 | `app/dev_observe.py` with the read-only `DevObservation` port; import-linter contract | layer test; a fake port with no write methods |
| S3 | CLI `dev-events` (`--once`, `--follow`, `--interval`, heartbeat, end on terminal) | CLI: output lines validate; `--follow` emits one line per change and exits on done; unknown flag exits 2 |
| S4 | `tests/security/test_dev_events_canary.py` (above) | canary, schema, mutation, endpoint-absence |
| S5 | `docs/t3-phase-view.md`: viewer recipe, a `render_panel` spec example, "do not trust, do not run `hint`" | docs link check in `test_adr_set`-style test if the repo has one |
| S6 | `cheeseless dev` prints a closed `panes` event (role to relay socket path, view/interactive) on stderr or in `dev-events` | no path outside the state dir; canary test: socket paths carry no digest |
| S7 | `pane-relay --view-only` (host relay reads no client frame, `Data` or `Resize`, on any connection; a frame closes that connection) and relay for coder and test writer panes, behind ADR 0019 acceptance | security: stdin and resize frames reach no pty across reconnects; a client frame closes the connection; closing the pane stops nothing |
| S8 | B2 spike (only if T3 lists a custom launcher and a non-pty protocol): design note, no code | none until an ADR exists |

S1 to S5 are independent of every T3 question and deliver Part A. S6 and S7 wait for Q1/Q3 and
for ADR 0019 to pass cross-model review (AGENTS.md).

## Open questions for the operator

1. Does T3 offer a terminal panel or thread tool that runs a given command side by side, and can it
   start any executable as a provider (B2)? This decides B3 vs B2 feasibility.
2. Do you want exact transition times (adds `updated_at` to the dev state; codec change) or are
   observation times enough?
3. Can T3 guarantee that a panel's terminal scrollback is never fed to an agent thread?
4. Is the phase view allowed to show the test writer's pane to the same T3 project that runs other
   agents (same UID), given they can read the store anyway?
5. Does `render_panel` escape text values? Irrelevant for v1 (enums only), but needed before any
   free-text field is ever proposed.
