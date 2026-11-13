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
- CI takes `acceptance/` and the runner closure from the base branch, fails on
  protected-path changes and runs the full suite including holdout (Q21, Q36). PR code runs only in a job with
  no secrets and a read-only token; seed and key live in a separate job that never
  checks out or runs PR code (ledger D8; layout with Q55).
- Promote applies the exact evaluated tree (Q43) as a patch onto `gate/<task>`, then a
  PR (Q17). It uses a source-path allowlist (contents: open, ledger Q56); build, dependency, CI and agent-instruction
  files are "sensitive" and need human approval; symlinks and submodules are rejected (Q37).
- Hooks and cooperative instructions are UX only, not a security boundary (Q12, Q30/31, Q41).

## Consequences
- ✅ A candidate cannot change what judges it, locally or in CI (criteria 7, 9).
- ✅ Sensitive-file changes get a human in the loop.
- ⚠️ Legitimate dependency or CI changes always need manual approval.
- ⚠️ Fixing the allowlist per language is ongoing maintenance.
