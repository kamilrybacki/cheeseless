---
status: accepted; point 2 superseded by ADR-0014
date: 2026-11-17
reviewers: [gpt-6.1-sol, gpt-6.1-terra]
---
# 0013. Drive spec, tests and code from one host command, `cheeseless dev`

## Context
ADR 0009 needs a person to start each session, approve the freeze and hand over. Users want one
agent session: agree a spec, then watch tests and code appear, without the implementer ever
seeing tests or the author transcript, and without any agent approving its own freeze.

## Options
- **Host orchestrator**: `cheeseless dev -- <agent>` runs the phases, owns freeze and handover.
- **Agent-held tools** (the agent freezes and switches phases) or a **launcher skill** chaining CLI.

## Decision
Host orchestrator: phase changes and freeze stay host decisions (ADR 0009); a skill only launches
it. The host store binds phase, spec digest, proposal, session ids; `--resume` is author-only.
1. **Spec**: the author TUI interviews the user and writes `spec.md` and `criteria.yaml`. It ends
   only when the agent calls the dev-only MCP tool `spec_done`, which `/spec-done` asks it to do.
2. **Reference**: a fresh headless session, same model, test-free, bound to the spec digest.
3. **Tests**: the same conversation writes hidden tests; every ADR 0005 freeze precondition holds.
   The host approves alone (actor `cheeseless-dev`) only after a green crosscheck and a passing
   strength review by a `--strength-reviewer` of another vendor (read-only sandbox); else, or with
   `--confirm-freeze`, the user approves. The seed is drawn after that approval (extends ADR 0005).
4. **Code**: the host ends the author process. A fresh implementer gets only the spec and gate
   tools. A green visible `gate_run` starts a full judgment charged to the lineage's local pool
   (so the ADR 0005 bound holds); a holdout fail tells the implementer only the red criterion
   names (extends ADR 0005). After 3 holdout fails the user re-freezes (new split and seed) or
   aborts; an empty pool is `BLOCKED` until the user grants runs. A dispute ends the session as
   in ADR 0009: rejected starts a fresh implementer, upheld returns to the author to re-freeze.
Clean tree only. Each agent sandbox gets only its own CLI credential, never runners/candidates.

## Consequences
- ✅ One flow with ADR 0009/0010 isolation and freeze rules; the implementer is a fresh process.
- ⚠️ Accepted risk: the agent may call `spec_done` unprompted; the user watches and can stop it.
- ⚠️ Auto-freeze has no human approval (actor `cheeseless-dev`); it and `spec_done` exist only
  in dev mode, which a security test must prove. Holdout feedback spends the local pool faster.
Author model: Opus 5.5 (`claude-opus-5-5`); reviewers must differ, one from another vendor.
