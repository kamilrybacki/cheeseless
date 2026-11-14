---
status: accepted
date: 2026-11-13
reviewers: [gpt-6.1-terra, gpt-6.1-sol, gpt-6-luna]
---
# 0006. CI is authoritative; promote filters paths; hooks are not a boundary

## Context
Local results are advisory (ledger Q5). Agent edits reach the shared repo only through
promote and CI, so both must be immune to changes of tests, runner config and CI files (Q21, Q37).

## Options
- **Trust agent-side checks and hooks**: cooperative, bypassable.
- **Enforce at promote and CI**: tests from the default branch, filtered paths, approval.

## Decision
Enforce at promote and CI.
- The CI verdict comes from a judge workflow on the **default** branch (`workflow_run`), never one
  the PR can edit (ledger D15, revises D8; I6). It refuses a non-default base, resolves the PR
  from the head SHA and re-checks that head before posting on it; no trigger means no check, so
  merge stays blocked. It fetches the head as data, takes `acceptance/` and the runner closure
  from the default branch, fails on protected-path changes and runs the full suite with holdout
  (Q21, Q36). Hidden tests stay in the runner; only the candidate build and processes run in the
  candidate sandbox (ADR 0003, I3); seed and key stay in the runner (Q55). Logs never show
  per-criterion holdout results. PR jobs get no secrets, a read-only token and no
  `pull_request_target` with a PR checkout.
- The CI budget (I4) is charged per lineage, before hidden input, by a trusted, atomic, persistent
  counter outside the runner, which release requires (P10-6, P10-25/26); a missing, unreadable or
  inconsistent counter blocks the merge. Judge secrets sit in a default-branch-only environment; a
  judge App posts the verdict and the required check is pinned to it (P10-22).
- Promote applies the exact evaluated tree (Q43) as a patch onto `gate/<task>`, then a PR (Q17),
  through a source-path allowlist (default `src/**`, `tests/**`, `docs/**`, `README*`). Build,
  dependency, CI and agent-instruction files need human approval; symlinks and submodules are
  rejected (Q37); the Q17 hard denylist wins over config and approval (Q56).
- Hooks and cooperative instructions are UX only, not a security boundary (Q12, Q30/31, Q41).

## Consequences
- ✅ A candidate cannot change what judges it, locally or in CI (criteria 7, 9).
- ⚠️ Sensitive-file changes always need human approval; per-language allowlists need upkeep.
- ⚠️ CI needs operator setup (App, environment, counter store); admins can still alter the count.
