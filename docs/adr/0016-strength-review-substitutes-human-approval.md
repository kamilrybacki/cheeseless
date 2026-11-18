---
status: accepted
date: 2026-11-18
reviewers: [gpt-6.1-sol]
---
# 0016. Name what auto-freeze replaces: the operator's strength review, not the executable checks

## Context
ADR 0015 argued the stored strength review "does not reintroduce the LLM judge ADR 0009
rejected". That compares it with the wrong thing. ADR 0005's executable checks still run in full
at freeze. What `auto_approve_freeze` removes is the step ADR 0009 kept as "the control": the
operator (a human or an orchestrator agent acting for them) reading the tests and deciding
they are strong enough. Auto-freeze turns a model's verdict into that decision with nobody making
it, which is what ADR 0009 declined to trust. This supersedes three claims in ADR 0015's
Decision ("never a substitute", "does not reintroduce the LLM judge", vendors "both from trusted
config") and M5-4's reading of them; the rest of ADR 0015 stands.

## Options
- **A**: Deny it: auto-freeze only where an operator also approves, and the review is advisory.
- **B**: Accept the substitution, bounded by opt-in, a host-observed review and vendor checks.
- **C**: Add an executable strength signal (mutation testing); the verdict only gates computing it.

## Decision
**B**, for now: it is the only option that keeps the feature and states what it costs. The bounds
live in `approve_freeze`'s unattended branch, which every unattended route shares (`--approve
--auto`, bare `freeze`). `--approve --as` is the operator and keeps ADR 0009's checks.
**C** succeeds this.
- **Opt-in** (`auto_freeze_enabled`, default off) is read inside the approval ownership, and
  `--no-allow-auto` takes that ownership too.
- **Observed** means the host launched the reviewer session and read its verdict.
  `record_strength_review` cannot assert it, and stored flags must be real booleans.
- **Vendors** are read from the host's launch records, and a contradicting flag is refused. A
  record holds the model the launcher was *told*, not one it verified. Author launches record
  none yet, so unattended freeze fails closed until a trusted launcher config supplies them.

## Consequences
- ✅ The ADR names the step it removes, and the bounds are tested on every unattended route.
- ⚠️ ADR 0009's objection stands: a manipulated pass removes the operator's judgement. The bounds
  make that deliberate and reversible, not safe. Until **C** exists, "the tests are strong" rests
  on one declared reviewer passing tests attributed to an author of another declared vendor.
