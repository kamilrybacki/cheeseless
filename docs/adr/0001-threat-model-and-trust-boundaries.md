---
status: proposed
date: 2026-11-13
reviewers: [gpt-6.1-terra]
---
# 0001. Trust the host side only and treat the gated agent and its code as hostile

## Context
cheeseless stops coding agents from editing or gaming acceptance tests (ledger Q1).
Agents are clones of normal providers with the same tools (Q11), so the boundary must
be enforced outside the agent, not by its cooperation.

## Options
- **Cooperative trust**: instructions and hooks tell the agent not to look; cheap, but not a boundary.
- **Explicit trust boundary**: name trusted and untrusted parties, protect assets by construction.

## Decision
Explicit trust boundary.
- **Trusted:** the operator, the host CLI (freeze, promote, disable; Q43), the verifier,
  and CI running on the base branch (Q21, Q36).
- **Not trusted:** the gated agent and every line of candidate code it writes (Q8, Q11).
- **Assets:** hidden tests, fixtures and expected outputs, the holdout and its seed,
  author notes and author transcripts (Q27, Q41, Q46).
- **Confidentiality** is against the gated agent only, not against the public (Q42).
- **Assurance boundary (Q45):** Q1 is not "agents cannot game tests" but a measurable
  boundary: listed attacks are tested with sentinels (Q46, Q29 and its additions). BLOCKED
  with a report is a valid terminal outcome, as is an honest failure report.
- **Accepted residual risks** (documented, not solved): training-data memorisation of
  public tests (stronger since tests are public, ledger D3), correlated blind spots of models, page-cache and timing side channels
  below the run budget. Mitigation: holdout rotation and cross-vendor review.

## Consequences
- ✅ Every later decision (ADR 0002-0008) answers one named threat.
- ✅ Success is measurable: sentinel scans and security tests, not trust.
- ⚠️ Residual risks remain and users must read them.
- ⚠️ Hooks and instructions are UX only (Q30/31); they never count as protection.
