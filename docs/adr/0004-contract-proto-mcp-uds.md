---
status: proposed
date: 2026-11-13
reviewers: [gpt-6.1-terra]
---
# 0004. Define the contract in proto, expose it through MCP, CLI and a minimal UDS

## Context
The gate has to talk to several agents, to the sandbox and to CI without tying the
core to one transport (ledger Q13, Q30/31). The agent must not reach admin verbs (Q43).

## Options
- **`cheeseless.v1` proto as external contract**, MCP and CLI `--json` for agents, protojson over UDS for the sandbox (Q13).
- **A2A**: heavier than needed for a local, single-gate tool; rejected (Q3, Q43).

## Decision
Proto is the schema source; generated classes stay in inbound adapters, never in
`domain/` or `app/` (Q13, Q25). MCP exposes `gate_run` and `gate_status` with
`outputSchema` plus a text block (Q13, Q30/31). The verifier UDS exposes only `run`
and `status`; one socket per session in a private dir, bound only into that session's
agent sandbox; `SO_PEERCRED` pid must descend from that sandbox (D6); freeze, promote
and disable are host CLI only (Q43). Results are HMAC-bound to task, tree hash, baseline digest,
profile and tool version; promote applies the exact evaluated tree; freeze approves an
exact digest (Q43).
The HMAC key and results stay in the trusted store, outside both sandboxes; `status`
and promote re-check task, tree and baseline against the current state (D6). The verifier runs per session, no system daemon (Q26). No A2A (Q43).

## Consequences
- ✅ A forged or replayed PASS fails verification (criteria 8, 17).
- ✅ Any agent with MCP or a shell can use the gate; adapters are declarative profiles (Q30/31).
- ⚠️ Proto versioning is a public compatibility burden.
- ⚠️ The agent-facing error shape is fixed in ADR 0008.
