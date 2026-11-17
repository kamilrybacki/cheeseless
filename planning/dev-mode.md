# `cheeseless dev`: interactive spec → tests → code

Working design for ADR 0013, agreed with the user on 2026-11-17 (Q1–Q20). This is planning
material; the ADR holds the decision.

## Start
- `cheeseless dev -- claude [--model M] [--reference-model M] [--implementer-model M]
  [--confirm-freeze] [--strength-reviewer -- <agent>] [--skill PATH] [--resume]`.
- Requires a git repo with a clean working tree; a dirty tree is refused with a message.
- No `criteria.yaml`: the host runs `cheeseless init` first.
- Auth: `CLAUDE_CODE_OAUTH_TOKEN` from the environment only (from `claude setup-token`). Missing
  token: refuse with a message that names the command. Never a credentials file in the repo.
  The host forwards only this variable, and only into agent sandboxes (author, reference,
  implementer); a `--strength-reviewer` sandbox gets only its own CLI credential. Test runners
  and candidates never get any credential.
- `--model` applies to every phase unless a per-role flag overrides it.
- A T3 or Claude Code skill may only launch this command; it never decides a phase.

## Phase 1: spec (with the user)
- The author TUI runs in the author sandbox, with a built-in interview prompt (grill-me style)
  that `--skill` can replace.
- Output: `acceptance/contract/spec.md` and `criteria.yaml`, one criterion per named spec rule.
- Ends only via the user's `/spec-done`: a custom command seeded into the agent's HOME
  (`~/.claude/commands/`, ledger D13-2a) that tells the agent to call the MCP tool `spec_done`.
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
- The freeze keeps every ADR 0005 precondition (baseline RED, stratified split, sentinels,
  static checks, cross-vendor test-strength review). Round-2 finding S53-6:
  - with `--strength-reviewer -- <agent>` (another vendor's CLI, its own credential, read-only
    sandbox over spec and hidden tests), a passing review plus green `author_check` and
    crosscheck lets the host run `approve_freeze` itself as actor `cheeseless-dev`;
  - without a strength reviewer, or with `--confirm-freeze`, the host asks the user, who
    approves as operator and owns the strength review, as in the regular `freeze --approve`.
  - either way the host draws the seed (and split) only after that approval, so neither the
    author nor the strength reviewer sees it before the suite is fixed (round-3 S53-6).
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
- Budget (round-2 findings S53-2, S53-5): each full judgment reserves one run from the
  lineage's local pool (default 30, host trusted store) before any hidden input, like
  `gate_run`. The CI pool stays in the trusted CI counter, so the ADR 0005 bound of 35 hidden
  executions per lineage holds. Only the disclosure is extended: holdout red criterion names.
- After 3 failed holdout judgments the user re-freezes with a new split and seed (back to the
  author phase) or aborts; there is no "grant and continue" here. An empty pool ends `BLOCKED`
  until the user, as operator, grants more runs.
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
- Auto-freeze refuses without a passing strength review; the strength reviewer sandbox is
  read-only (its writes never reach the store or the acceptance tree).
- Each agent sandbox env holds only its own credential; runner and candidate envs hold none.
- Full judgments reserve from the local pool; 3 holdout fails refuse further judgments
  until a re-freeze.

## Related open ledger items
- S49: `--task` placeholder for pre-freeze roles; dev mode needs a task id before the freeze.
