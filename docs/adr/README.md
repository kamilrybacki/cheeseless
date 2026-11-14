# Architecture Decision Records

Only critical, long-lived decisions live here. Everything else goes to
[`planning/ledger.md`](../../planning/ledger.md). Use [`template.md`](template.md).

| ADR | Decision | Status |
|-----|----------|--------|
| [0001](0001-threat-model-and-trust-boundaries.md) | Trust the host side only; gated agent and its code are hostile | accepted |
| [0002](0002-snapshot-projection.md) | Snapshot projection instead of worktrees; one repo, sparse-checkout | accepted |
| [0003](0003-process-boundary-and-sandbox.md) | Own bwrap sandbox, throwaway overlay, trusted runner, fail closed | accepted |
| [0004](0004-contract-proto-mcp-uds.md) | Proto contract, MCP, minimal UDS, HMAC-bound results, no A2A | accepted |
| [0005](0005-disclosure-policy-and-holdout.md) | Per-criterion pass/fail, per-lineage run budget, stratified holdout | accepted |
| [0006](0006-ci-source-of-truth-and-promote.md) | CI authoritative, promote path allowlist, hooks not a boundary | accepted |
| [0007](0007-egress-dlp-proxy.md) | Host-side MITM proxy with DLP for all gated egress | accepted |
| [0008](0008-agent-facing-error-model.md) | One typed agent-facing error and staged disclosure | accepted |
