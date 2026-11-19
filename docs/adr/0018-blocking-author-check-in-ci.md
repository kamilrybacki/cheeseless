---
status: proposed
date: 2026-11-19
reviewers: []
---
# 0018. A definite author-check failure fails the CI gate on spec/ PRs; an unavailable check only warns

## Context
ADR 0009 makes the CI author check on `spec/` PRs advisory: `evaluate` runs PR acceptance content in the author runner, so `post` consumes nothing it produces. A `spec/` PR whose tests do not collect, miss a criterion or leak a sentinel therefore merges with a green gate, and the problem first surfaces at freeze approval. The operator wants such PRs blocked (ledger 2026-11-19). The forces: `evaluate` output is forgeable by the PR it checks, and an install flake must not fail the gate.

## Options
- **Keep advisory**: no change; definite authoring errors reach the default branch.
- **Block on any non-success of `evaluate`**: an apt or uv flake, a cancel or a PR-induced crash fails the gate, and a job result cannot tell them apart.
- **Fail-only on a closed enum**: `evaluate` emits one value, `post` blocks only on a definite failure.

## Decision
Fail-only on a closed enum, because a forged value can only block the PR that forged it, never pass one that today would fail.
- `ci-branch` classifies the spec/ author check as `pass` (no problems), `fail` (the runner returned a usable result with problems: sentinel, criterion mapping or collection problems) or `unavailable` (no usable result, an exception, or the check did not run). The classification lives in `app/ci_branch.py`, not in the workflow.
- `evaluate` writes only `author=<value>` to its job outputs. `post` reads that one output, accepts it only if it is exactly one of the three values, and treats anything else, a missing output or a non-success `evaluate` result as `unavailable`.
- For a `spec/` PR, `fail` adds a failing guard to the required "cheeseless gate" check. `unavailable` is a visible warning in the step summary and the check body, never a guard. `gate/` and other classes ignore the value.
- Problem text stays in the `evaluate` log; `post` never renders PR-controlled text.
- Everything else in ADR 0009 holds: the output is still forgeable evidence, freeze approval still runs the full author check, RED-on-baseline and flakiness checks, and the ADR 0005 test-strength review stays the control. This supersedes only ADR 0009's "Their output is advisory" for the CI job.

## Consequences
- ✅ Definite authoring errors are caught at PR time instead of at approval.
- ✅ A flake cannot fail the gate, and the trusted job still reads no PR-controlled text.
- ⚠️ A PR that crashes its own collection lands on `unavailable` and passes with a warning, as today; approval stays the control for it.
- ⚠️ A sandbox escape in `evaluate` can forge `pass`, as today; it gains nothing it could not forge before.
- ⚠️ A local `promote` already requires an author-check pass; CI now repeats that check for PRs that did not use `promote`.
