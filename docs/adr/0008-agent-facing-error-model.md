---
status: accepted
date: 2026-11-13
reviewers: [gpt-6.1-terra]
---
# 0008. Return one typed error to the agent and stage what it may see

## Context
Error text is an oracle channel: stack traces and class names leak hidden inputs. Yet
an agent that gets no useful message cannot fix a broken build (ledger Q38, Q50-Q52).

## Options
- **Free-form errors per channel**: simple, inconsistent, hard to scan.
- **One typed `cheeseless.v1.Error`** with stages of disclosure.

## Decision
One typed error (Q50). Every agent-facing response (MCP structuredContent, CLI
`--json`, UDS, proxy 403) uses `cheeseless.v1.Error`:
- `code`: stable public enum (e.g. `RUN_BUDGET_EXHAUSTED`, `CRITERION_FAILED`,
  `BUILD_FAILED`, `PROMOTE_PATH_REJECTED`, `SENSITIVE_FILE_NEEDS_APPROVAL`,
  `EGRESS_DOMAIN_DENIED`, `EGRESS_SERVER_TOOL_DENIED`, `DLP_BLOCKED`, `INFRA_ERROR`);
- `reason` (trusted template), `hint`, `retryable`, `details` (whitelisted fields only:
  criterion id, a path the agent wrote, budget state, host), `docs` (anchor in
  `docs/errors.md`, generated from the proto). The proxy adds `X-Cheeseless-Code`.
- No hidden content in `reason` or `details`; every response is DLP-scanned (Q50).
Stages (Q51): before the first hidden input (build, import, missing CLI executable,
startup crash, public smoke tests from `contract/`) the response carries full detail
(code plus a DLP-scanned stderr tail, about 4 KB). After hidden input the agent sees
only per-criterion `CRITERION_FAILED` with the public criterion description; an infra
failure there is stored as `INFRA_ERROR` for the operator, charged (ledger D2) and shown
as FAIL of the unfinished criteria (D17). Pre-hidden errors cost no run budget (Q52).
Pre-hidden attempts have their own rate and cgroup limits; over it, `RATE_LIMITED` (D10).

## Consequences
- ✅ Agents get actionable build feedback with no hidden-test leak (criterion 23).
- ⚠️ The enum is a public API; new codes need docs.
