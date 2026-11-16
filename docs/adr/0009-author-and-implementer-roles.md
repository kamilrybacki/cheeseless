---
status: proposed
date: 2026-11-16
reviewers: []
---
# 0009. Gate both sides: a separate author role, a reference crosscheck and a dispute channel

## Context
The gate only guards the implementer. A hidden test that contradicts the spec is an authoring bug, not gaming, yet today it surfaces only as an unpassable criterion. Authoring and implementing must be separate, tool-enforced roles, and freeze and dispute decisions must be makeable by a human or an orchestrating agent (ledger 2026-11-16, T1–T7). This extends ADR 0001 and 0005; it supersedes neither.

## Options
- **Guard the implementer only**: no change; spec/test bugs stay invisible.
- **Two gated roles plus a blind reference session**: contradictions surface as executable evidence before freeze.
- **LLM judge of test/spec consistency**: opaque, one more model to trust, no executable evidence.

## Decision
Two gated roles plus a blind reference session, because executable disagreement is evidence and needs no trusted model beyond the active agent's model and deterministic checks.
- The spec lives in `acceptance/contract/` (`spec.md`, optional per-criterion `examples` in `criteria.yaml`), inside the freeze digest. A contract without them keeps today's canonical bytes.
- **Author** session: `acceptance/` writable, hidden tests visible, everything else read-only. Promote takes `acceptance/` paths only onto `spec/<task>`. The CI judge rejects a `spec/` PR touching other paths and a `gate/` PR touching `acceptance/`. Implementer policy unchanged.
- **Reference** session: the implementer projection plus `spec.md`, no tests and no gate tools; its only feedback is the public `examples`. At session end the host snapshots its tree into the trusted store, bound to the spec digest. It is never in the repo, never projected and never promoted, so neither the author nor the implementer can write or read it.
- Author tools: `author_check` (deterministic: coverage per criterion, no orphans, RED on baseline, no skip/xfail, sentinels) and `author_crosscheck` (full suite against the stored reference, no budget charge, per-criterion red reported as a possible spec contradiction).
- `freeze_propose` stores a proposal bound to its digest. `cheeseless freeze --approve <id>` draws the seed. It needs reference GREEN and baseline RED unless the approver passes `--no-reference`, which is recorded in the frozen task. The cross-vendor test-strength review of ADR 0005 still applies.
- **Dispute** (opt-in per repo, default off): `gate_dispute(criterion_id, reason)` ends the implementer session `DISPUTED`, with no more runs and no pass. The capped reason goes to the trusted store (`cheeseless disputes --json`, labelled untrusted) and never back to a gated session. A fix is a re-freeze; the lineage budget is unchanged.
- The approver of freeze, the resolver of disputes and the grantor of budget act as the ADR 0001 operator. That is a human or an orchestrator agent using the host CLI with `--json`, outside every sandbox. No approve or resolve endpoint exists on UDS or MCP. Each role gets its own session and HOME.

## Consequences
- ✅ Spec/test contradictions surface before implementation, to agent and user alike.
- ✅ Neither role can write the other side; CI enforces it by branch prefix.
- ⚠️ One extra agent session per task for the reference.
- ⚠️ A reference that agrees with a wrong test hides the bug; examples and review remain advisable.
- ⚠️ A dispute reveals that the implementer saw a failing criterion, nothing more (ADR 0005).
