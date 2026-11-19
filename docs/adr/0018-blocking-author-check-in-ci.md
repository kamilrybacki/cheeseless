---
status: accepted
date: 2026-11-19
reviewers: [gpt-6.1-sol, claude-sonnet-5-5]
---
# 0018. A definite author-check failure fails the CI gate on spec/ PRs; an unavailable check only warns

## Context
ADR 0009 says of `author_check` and `author_crosscheck`: "Their output is advisory". In CI, `evaluate` runs PR acceptance content in the author runner and `post` consumes nothing it produces, so a `spec/` PR with an unreadable contract, a leaked sentinel, collection errors or an unmapped criterion merges green and first fails at freeze approval. The operator wants such PRs blocked (ledger 2026-11-19). The forces: everything `evaluate` emits is forgeable by the PR's own test code, and an infra flake must not fail a required check.

## Options
- **Keep advisory**: definite authoring errors reach the default branch.
- **Block on any non-success of `evaluate`**: a flake, a cancel and a PR-induced crash look the same and all fail the gate.
- **Fail-only on a closed enum**: `evaluate` emits one value, `post` blocks only on a definite failure.

## Decision
Fail-only on a closed enum: a forged value can only block the PR that forged it, never pass one that would otherwise fail.
- `author_check` returns a typed status, not just problem strings. `fail`: any host-side problem (unreadable contract, sentinel leak), or a usable driver result with problems (collection errors, criterion mapping). `unavailable`: an exception, a launch failure or timeout, or no usable driver result, and no host-side problem. `pass`: neither. Host-side problems take precedence, so a leak plus a dead runner is `fail`.
- `ci-branch` writes `author=<status>` itself from that enum through a fixed mapping; the workflow never copies a JSON field, and the step summary gets no problem text.
- `post` passes the job output to `ci-gate --author` through an environment variable, never inlined in `run:`. Tested Python maps anything but an exact `pass`, `fail` or `unavailable` (empty, multi-line, oversized, other casing, missing) and a non-success `evaluate` result to `unavailable`.
- The guard keys on route's trusted `BRANCH_CLASS`: only a `spec/` PR with `fail` gets a failing guard on the required "cheeseless gate" check. It adds a failure and never clears policy, judge or any other guard. `unavailable` is a warning in fixed text.
- The check body is fixed text pointing at the `evaluate` log; `post` renders no PR-controlled text.
- Supersedes ADR 0009's "advisory" only for this CI use. Local `author_check`, `author_crosscheck`, freeze approval (full checks, RED-on-baseline, flakiness) and the ADR 0005 strength review are unchanged and stay the control.

## Consequences
- ✅ Honest authoring errors are caught at PR time instead of at approval.
- ✅ A flake cannot fail the gate, and the trusted job still reads only a validated enum.
- ⚠️ PR test code can forge a `pass` record or kill the driver to land on `unavailable` without any sandbox escape; the check catches mistakes, not attacks, as before.
- ⚠️ A false `fail` (e.g. pytest drift) blocks a required check; recovery is a re-run or an admin merge with the `evaluate` log reviewed.
- ⚠️ `evaluate` logs can still carry workflow commands from PR output, as today; keeping problem text in the log does not make it safe to render.
