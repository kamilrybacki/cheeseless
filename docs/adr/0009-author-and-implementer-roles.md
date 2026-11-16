---
status: proposed
date: 2026-11-16
reviewers: [gpt-6.1-sol, claude-haiku-5-5]
---
# 0009. Gate both sides: a separate author role, a reference crosscheck and a dispute channel

## Context
The gate only guards the implementer. A hidden test that contradicts the spec is an authoring bug, not gaming, yet today it surfaces only as an unpassable criterion. Authoring and implementing must be separate, tool-enforced roles, and freeze and dispute decisions must be makeable by a human or an orchestrating agent (ledger 2026-11-16, T1–T8, R1–R19). Once authors are agents, their tests are untrusted code until the freeze is approved. This extends ADR 0001, 0003, 0005 and 0006; it supersedes none.

## Options
- **Guard the implementer only**: no change; spec/test bugs stay invisible.
- **Two gated roles plus a blind reference session**: contradictions surface as executable evidence before freeze.
- **LLM judge of test/spec consistency**: opaque, one more model to trust, no executable evidence.

## Decision
Two gated roles plus a blind reference session, because executable disagreement is evidence and needs no trusted model beyond the active agent's model and deterministic checks. Lifecycle and states: `planning/two-sided-gate.md`.
- **Spec**: `acceptance/contract/spec.md` plus optional per-criterion `examples` in `criteria.yaml`, inside the freeze digest; a contract without them keeps today's canonical bytes. The spec digest covers the contract bytes only. `spec.md` and the examples are projected read-only to the implementer and the reference; promote already denies `acceptance/`.
- **Roles are host-bound**: the host starts each session with one role, its own HOME and a UDS server that serves only that role's operations; any other call is rejected, whatever tools are advertised.
- **Author**: `acceptance/` writable, hidden tests visible, the rest read-only; author tools are refused after approval. Promote takes only `acceptance/` paths onto `spec/<task>` and needs an `author_check` pass on the same tree. CI runs a separate author job on `spec/` PRs (deterministic checks, never an implementation verdict) and rejects other paths there, `acceptance/` on `gate/` PRs and unknown branch classes.
- **Containment**: `author_check` and `author_crosscheck` run the unapproved suite in a runner sandbox (ADR 0003 profile, no network, no trusted store, no repo write, reference mounted read-only). Their output is advisory: an author can always write weak tests, so the ADR 0005 test-strength review stays the control.
- **Reference** session: the implementer projection plus the spec, no tests and no gate tools; its only feedback is the public examples. At session end the host snapshots its tree into the trusted store, bound to the spec digest. It is never in the repo, never projected and never promoted.
- **Evidence**: `author_crosscheck` (no budget charge) writes a host record binding acceptance tree, contract bytes, baseline commit and reference snapshot; a red criterion is reported as a possible spec contradiction. A test-only change needs a fresh crosscheck, not a new reference.
- **Freeze**: `freeze_propose` stores a proposal bound to its digest. `cheeseless freeze --approve <id>` checks that the tree is merged on the default branch and a matching crosscheck is GREEN, then draws the seed. `--no-reference --reason <text>` waives only the crosscheck and is recorded in the frozen task; baseline RED and every ADR 0005 precondition stay mandatory.
- **Dispute** (opt-in per repo, default off, capped per lineage): `gate_dispute(criterion_id, code, note)` takes a public criterion and a code from a closed enum. Under the task lock it ends the implementer session `DISPUTED`, a session state stored beside results, not a `Result` status, so v1 result encoding is unchanged. A disputed session publishes no further result and cannot promote; a session holding a PASS cannot dispute. The acknowledgement is uniform and reveals no evaluation. Charges stay; the lineage budget is unchanged.
- **Resolution**: `cheeseless disputes --json` lists id, criterion and code; the capped note appears only with `--show-note`, labelled untrusted, and never reaches a gated session. `cheeseless dispute resolve <id> --upheld|--rejected` records the outcome: upheld leads to a re-freeze in the same lineage, rejected to a fresh implementer session. A disputed session never resumes.
- **Operator**: approve, resolve and grant are the ADR 0001 operator, a human or an orchestrator agent on the host CLI with `--json`. They need the trusted store, which no sandbox mounts, and log `--as <actor>`. No such endpoint exists on UDS or MCP.

## Consequences
- ✅ Spec/test contradictions surface before implementation, to agent and user alike.
- ✅ Neither role can write the other side; host role binding and CI enforce it.
- ⚠️ One extra agent session per task for the reference, and a runner sandbox for author checks.
- ⚠️ A reference that agrees with a wrong test hides the bug, and an author can forge advisory evidence; test review stays required.
- ⚠️ A dispute reveals only what the implementer chose to send: a public criterion id and a code (ADR 0005).
