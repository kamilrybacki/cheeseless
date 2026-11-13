---
status: proposed
date: 2026-11-13
reviewers: [gpt-6.1-terra]
---
# 0005. Disclose per-criterion pass/fail only and budget the runs

## Context
Unlimited runs plus criterion bits, timing and error classes leak hidden cases, and a
per-session budget resets with every new session (ledger Q18, Q38).

## Options
- **Verbose feedback, unlimited runs**: fast TDD loop, strong oracle for the agent (Q18, revised).
- **Minimal feedback, shared budget, rotating holdout**: slower loop, weaker oracle.

## Decision
Minimal feedback.
- The result is per-criterion pass/fail only: no timing, error class or candidate
  stdout/stderr; holdout is never reported locally (Q18, Q38). Exceptions in ADR 0008.
- The run budget is per task identity, shared across sessions and CI; defaults 30 local
  and 5 CI, configurable per repo (Q38).
  Task identity is assigned at freeze by the operator (digest of the frozen criteria);
  a run reserves budget atomically in the trusted store before hidden input (D7). 
  Exhausted budget ends the task `BLOCKED`; only the operator grants more runs. The
  holdout rotates after 3 failed CI holdout attempts, re-frozen by the operator (Q54).
- Freeze preconditions (Q44, Q9): suite RED on baseline; skip/xfail/empty collection is
  an error; at least one visible and one holdout test per criterion (stratified);
  seed drawn after approval; static check that tests read no candidate paths/env; no
  nodeids in `criteria.yaml`. User approval and cross-vendor review of test strength.
- Multiple sentinels per artifact class (tests, fixtures, expected outputs, seed, author
  notes); scans include encoded forms (Q46).

## Consequences
- ✅ Bounded information leak per task, not per session.
- ⚠️ Slower fix loop for legitimate work; budgets need tuning.
- ⚠️ Mutation testing of the suite is post-MVP (Q44).
