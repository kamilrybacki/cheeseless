# `cheeseless dev`: interactive spec → tests → code

Working design for ADR 0013, agreed with the user on 2026-11-17 (Q1–Q20). This is planning
material; the ADR holds the decision.

## Start
- `cheeseless dev -- claude [--model M] [--reference-model M] [--implementer-model M]
  [--confirm-freeze] [--skill PATH] [--resume]`.
- Requires a git repo with a clean working tree; a dirty tree is refused with a message.
- No `criteria.yaml`: the host runs `cheeseless init` first.
- Auth: `CLAUDE_CODE_OAUTH_TOKEN` from the environment only (from `claude setup-token`). Missing
  token: refuse with a message that names the command. Never a credentials file in the repo.
  The host forwards only this variable, and only into agent sandboxes (author, reference,
  implementer); test runners and candidates never get it.
- `--model` applies to every phase unless a per-role flag overrides it.
- A T3 or Claude Code skill may only launch this command; it never decides a phase.

## Phase 1: spec (with the user)
- The author TUI runs in the author sandbox, with a built-in interview prompt (grill-me style)
  that `--skill` can replace.
- Output: `acceptance/contract/spec.md` and `criteria.yaml`, one criterion per named spec rule.
- Ends only via the user's `/spec-done`: a custom command in the projection's
  `.claude/commands/` that tells the agent to call the MCP tool `spec_done`.
- `spec_done` exists only in dev mode (not in the normal author profile).
- Accepted risk (ADR 0013): the agent may call `spec_done` unprompted.

## Phase 2: reference (background)
- Fresh headless `claude -p` in a test-free reference sandbox, same model by default.
- Its log goes to a host file, not to the TUI.
- A spec change gives a new digest, which re-runs the reference.

## Phase 3: tests (same conversation as the spec)
- The author writes hidden tests.
- `author_crosscheck` returns `reference_pending` while the reference runs. Otherwise it
  returns the red criteria with the reference's stdout/stderr for failing tests (ADR 0009
  disclosure).
- If the spec is ambiguous, the agent asks the user.
- When `author_check` and the crosscheck are green, the host runs `approve_freeze` itself,
  as actor `cheeseless-dev`. With `--confirm-freeze`, the host asks the user first.
- Freeze result: a local commit `test: freeze <task>` on the default branch.

## Phase 4: implementation (fresh context)
- The host sends the author a short closing message, then SIGTERM, and prints a transition line.
- A new TUI starts in the implementer sandbox. Its first prompt is the spec plus
  "implement it, check with `gate_run`".
- It never sees tests, the author transcript or the author session id.
- The implementer works alone; the user watches and may interrupt.
- A green visible `gate_run` triggers the full judgment, including holdout:
  - PASS: commit to `impl/<task>`, print a summary, end.
  - Holdout FAIL: the implementer gets only the red criterion names and continues.
- Budget (ADR 0013 extends ADR 0005): each full judgment plays the CI role and reserves one
  run from the lineage's CI pool (default 5) in the host store, before any hidden input.
  `gate_run` keeps using the local pool. After 3 holdout fails or an empty pool the user picks:
  grant more runs, return to the author phase (re-freeze), or abort.
- Disputes follow ADR 0009 (review finding S53-3 corrected the earlier "queue and continue"):
  a dispute ends the implementer session `DISPUTED` at once and the host asks the user:
  - rejected: a fresh implementer session starts (never a resume);
  - upheld: back to the author phase, then a re-freeze in the same lineage.

## Resume
- Host state (phase, spec digest, proposal id, session ids) is kept in the result store.
- `--resume` returns to the saved phase: spec and tests via `claude --resume`; the implementer
  always starts fresh.

## Security tests (tests/security/)
- The implementer projection and its first prompt contain no hidden test, sentinel, canary or
  author transcript.
- `spec_done` and auto-freeze are absent outside dev mode.
- Auto-freeze refuses without a green, bound crosscheck (it reuses `_ready`).

## Related open ledger items
- S49: `--task` placeholder for pre-freeze roles; dev mode needs a task id before the freeze.
