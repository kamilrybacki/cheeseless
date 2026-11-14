---
status: proposed
date: 2026-11-13
reviewers: [gpt-6.1-terra]
---
# 0005. Disclose per-criterion pass/fail only and budget the runs

## Context
Unlimited runs plus criterion bits, timing and error classes leak hidden cases; a per-session budget resets each session (Q18, Q38).

## Options
- **Verbose feedback, unlimited runs**: fast TDD loop, strong oracle for the agent (Q18, revised).
- **Minimal feedback, run budget, rotating holdout**: slower loop, weaker oracle.

## Decision
Minimal feedback.
- The result is per-criterion pass/fail only: no timing, error class or candidate
  stdout/stderr; holdout is never reported locally (Q18, Q38). Exceptions in ADR 0008.
- Run budgets are separate pools per task lineage (I4): 30 local runs in the host trusted store
  and 5 CI runs in a trusted CI counter (ADR 0006), so at most 35 hidden executions per lineage;
  both configurable per repo (Q38). The bound holds only with that counter, which release
  requires (P10-6). A run reserves budget atomically before any hidden input (D7); pre-hidden
  failures are free (Q52), later infra errors are charged (D2).
- Freeze identity is the digest of the frozen task, assigned at freeze by the operator; results
  bind to it. Budget identity is the lineage fixed at first freeze (D14, I5): a re-freeze gets a
  new digest, a holdout rotation keeps the digest and changes only seed and split (P8-6); both
  carry the remaining counts.
- Exhausted budget ends the task `BLOCKED`; only the operator grants more runs. The holdout
  rotates after 3 failed CI holdout attempts (Q54) or on a DLP hit (ADR 0007).
- Freeze preconditions (Q44, Q9): suite RED on baseline; skip/xfail/empty collection is an
  error; at least one visible and one holdout test per criterion (stratified); seed drawn after
  approval; static check that tests read no candidate paths/env; no nodeids in `criteria.yaml`.
  User approval and cross-vendor review of test strength.
- Each artifact class that has hidden files (tests, fixtures, expected outputs, seed, author
  notes) needs two distinct private sentinels; a marker the projection shows raw, reversed, hex
  or base64 does not count and freeze refuses (Q46, P8-12; other encodings: ADR 0007).

## Consequences
- ✅ Bounded information leak per task lineage, not per session or per re-freeze.
- ⚠️ Slower fix loop for legitimate work; budgets need tuning; suite mutation testing is post-MVP (Q44).
