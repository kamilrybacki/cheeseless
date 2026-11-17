---
status: proposed
date: 2026-11-17
reviewers: [gpt-6.1-sol]
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
Host orchestrator, because phase changes and freeze stay host decisions (ADR 0009); a skill may
only launch it. The host store binds phase, spec digest, proposal and per-role session ids;
`--resume` resumes only the author, never the implementer.
1. **Spec**: the author TUI interviews the user and writes `spec.md` and `criteria.yaml`. It ends
   only when the agent calls the dev-only MCP tool `spec_done`, which `/spec-done` asks it to do.
2. **Reference**: a fresh headless session, same model, test-free, bound to the spec digest.
3. **Tests**: the same conversation writes hidden tests; once `author_check` and the crosscheck
   are green the host freezes as actor `cheeseless-dev` (`--confirm-freeze`: ask the user).
4. **Code**: the host ends the author process. A fresh implementer gets only the spec and gate
   tools. A green visible `gate_run` starts a full judgment that plays the CI role (extends ADR
   0005): it reserves a run from the lineage's CI pool in the host store, and a holdout fail
   tells the implementer only the red criterion names. After 3 holdout fails or an empty pool,
   the user decides. A dispute ends the session as in ADR 0009: rejected starts a fresh
   implementer, upheld returns to the author for a re-freeze in the same lineage.
Clean tree only. Only `CLAUDE_CODE_OAUTH_TOKEN` reaches agent sandboxes, never runners/candidates.

## Consequences
- ✅ One flow, ADR 0009/0010 isolation and freeze rules; the implementer is a fresh process.
- ⚠️ Accepted risk: the agent can call `spec_done` without the user's command. Mitigation: the
  user still watches the tests phase and can stop it; the freeze still needs a green crosscheck.
- ⚠️ Auto-freeze has no human approval (actor `cheeseless-dev`); it and `spec_done` exist only
  in dev mode, which a security test must prove.
Author model: Opus 5.5 (`claude-opus-5-5`); reviewers must differ, one from another vendor.
