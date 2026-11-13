# Handoff — start here

State on 2026-11-13: design done, no code yet. This file is the entry point for a fresh
session on this repository.

## What cheeseless is

A git-native black-box quality gate for coding agents. A human (or an author agent) freezes
acceptance tests; a gated coding agent then works on the code without ever seeing the
hidden tests, and can only ask a sandboxed verifier "does my tree pass?". The verifier
answers per criterion, within a run budget, with results bound to the exact tree. CI is the
source of truth; changes land through `promote`, not hooks.

## Read in this order

1. [`ledger.md`](ledger.md) — every decision (Q1–Q56), research findings (R1–R3), derived
   rows (D1–D10) and the MVP acceptance criteria (AC 1–23). Authoritative.
2. [`../docs/adr/`](../docs/adr/README.md) — ADRs 0001–0008, status `proposed`.
3. [`implementation-plan.md`](implementation-plan.md) — phases 0–12 mapped to ACs.
4. [`research/`](research/) — stack verification and the two independent attack-vector
   reviews plus their merge. Background only; the ledger wins on conflicts.
5. [`../AGENTS.md`](../AGENTS.md) — rules for agents working here (ADR format, review, security).

## Open decisions

Nothing. Q54–Q56 were accepted with their proposed defaults on 2026-11-13 (ledger).

## Next steps

1. The cross-vendor review of ADRs 0001–0008 is addressed (ledger "ADR review", D4–D10).
   Re-review the ADRs changed since (0002–0008) once, then move them to `accepted`.
2. Phase 0 of the implementation plan: skeleton, import-linter contracts, proto, CI.
3. Continue phase by phase, tests first, cross-vendor review of each phase's diff.

## Constraints

- The repository is **private** until the sanitizer passes, CI is green and the operator
  explicitly confirms publication (Q34).
- No secrets, host names or personal infrastructure in this repo. Integration with a
  particular workstation or agent platform lives outside it.
- Accepted ADRs are immutable; supersede them with a new ADR.
