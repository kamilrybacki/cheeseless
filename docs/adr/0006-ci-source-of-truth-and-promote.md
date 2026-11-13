---
status: proposed
date: 2026-11-13
reviewers: [gpt-6.1-terra]
---
# 0006. CI is authoritative; promote filters paths; hooks are not a boundary

## Context
Local results are advisory (ledger Q5). Agent edits reach the shared repo only through
promote and CI, so both must be immune to changes of tests, runner config and CI files (Q21, Q37).

## Options
- **Trust agent-side checks and hooks**: cooperative, bypassable.
- **Enforce at promote and CI**: take tests from the base branch, filter paths, require approval.

## Decision
Enforce at promote and CI.
- The CI verdict comes from a judge workflow defined on the base branch (`workflow_run`),
  never from a workflow the PR can edit (ledger D15, revises D8). It fetches the PR head
  named in the event as data, takes `acceptance/` and the runner closure from the base
  branch, fails on protected-path changes and runs the full suite including holdout
  (Q21, Q36). Build and tests run only in the candidate sandbox (ADR 0003); seed and key
  stay in the runner process (Q55). Logs never show per-criterion holdout results. PR-defined
  jobs get no secrets and a read-only token; no `pull_request_target` with a PR checkout.
- Promote applies the exact evaluated tree (Q43) as a patch onto `gate/<task>`, then a
  PR (Q17). It uses a source-path allowlist (default `src/**`, `tests/**`, `docs/**`, `README*`; Q56); build, dependency, CI and agent-instruction
  files are "sensitive" and need human approval; symlinks and submodules are rejected (Q37).
  The Q17 hard denylist sits under the allowlist and wins over repo config and approval (Q56).
- Hooks and cooperative instructions are UX only, not a security boundary (Q12, Q30/31, Q41).

## Consequences
- ✅ A candidate cannot change what judges it, locally or in CI (criteria 7, 9).
- ✅ Sensitive-file changes get a human in the loop.
- ⚠️ Legitimate dependency or CI changes always need manual approval.
- ⚠️ Fixing the allowlist per language is ongoing maintenance.
