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
| [0009](0009-author-and-implementer-roles.md) | Separate author and implementer roles, reference crosscheck, dispute channel | accepted (advisory CI author check superseded by ADR-0018) |
| [0010](0010-author-runner-candidate-broker.md) | Reach the candidate from the author sandbox only through a host broker | accepted |
| [0013](0013-interactive-dev-mode.md) | Drive spec, tests and code from one host command, `cheeseless dev` | accepted (point 2 superseded by ADR-0014) |
| [0014](0014-dev-reference-tui-pane.md) | Run the dev-mode reference as a visible TUI in a second pane | accepted |
| [0015](0015-strength-review-record.md) | Gate auto-freeze on a stored, bound strength-review record | accepted (substitute and trusted-config claims superseded by ADR-0016) |
| [0016](0016-strength-review-substitutes-human-approval.md) | A strength review replaces the operator's strength review, not the executable checks; bounded, not safe | accepted |
| [0017](0017-attended-approval-tty-challenge.md) | Attended operator decisions (approve, resolve, recover-finish, sensitive promote, auto-freeze opt-in) need a typed challenge on the controlling terminal | accepted |
| [0018](0018-blocking-author-check-in-ci.md) | A definite author-check failure fails the CI gate on spec/ PRs; an unavailable check only warns | accepted |
| [0019](0019-view-only-role-relay.md) | Relay every role to a GUI pane; only the test writer pane takes input | accepted |
