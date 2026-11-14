# cheeseless

A git-native black-box quality gate for coding agents: frozen acceptance tests the agent
cannot see, a sandboxed verifier it can only ask "does my tree pass?", a shared run
budget, egress DLP, and CI as the source of truth.

**Status:** host-side core, authenticated UDS/MCP, isolated host sessions and the
phase 8 egress DLP proxy are implemented. CI, the toy-repo E2E and release hardening
remain (see `planning/handoff.md`).

Usage:
```
cheeseless freeze --yes
cheeseless run [--worktree PATH] [--json]
cheeseless status [--json]
cheeseless promote [--worktree PATH] [--approve PATH ...]
cheeseless doctor
cheeseless serve-uds --socket PATH --pidns-fd N
cheeseless serve-mcp --uds-socket PATH --task TASK
```

## Egress DLP guarantees (phase 8)

- The sandbox has no direct network, DNS or UDP. The only egress path is a per-session
  UDS mounted into the sandbox and exposed to clients as loopback `HTTPS_PROXY` through a
  tiny in-sandbox TCP->UDS forwarder; mitmproxy listens on host loopback behind a host
  UDS->TCP bridge.
- Freeze stores digest-only fingerprints: every k-gram hash of the hidden suite minus
  the k-grams of the agent-visible projection (k=24, no winnowing on the protected side),
  plus raw/base64/hex/reversed digests of planted sentinel markers per artifact class.
  Hidden plaintext never enters the trusted-store fingerprint file or the proxy.
- The proxy applies an exact host allowlist (no wildcard/suffix), GET-only registries,
  own package/repo identity denial, server-side tool schema rejection and forced identity
  encoding. Requests and responses (URL, headers, body) are scanned. Bytes already clean
  before a hit may have reached the client; the matching bytes are held back.
- Archives are detected by magic, buffered within caps and recursively inspected in
  memory; they are forwarded only after a clean scan. Encrypted, unsafe-path, symlink,
  over-cap and uninspectable archives are blocked.
- A hit persists a signed `BLOCKED` result, rotates the holdout without changing the task
  id or lineage budget, kills the whole agent process tree and alerts with host, rule and
  fingerprint id only. Public proxy errors are `cheeseless.v1.Error` JSON with
  `X-Cheeseless-Code`.

Residual limits (ADR 0001/0007 and ledger P8-9): paraphrase/transform leaks below the
k-gram or marker length, theoretical 64-bit rolling-hash collisions, bytes already
forwarded before a mid-stream hit, archive caps are hard blocks, and HTTP upgrades/QUIC
are blocked rather than inspected. Sentinel markers are harvested from the author's
hidden artifacts, and freeze requires at least two distinct markers per artifact class
that has hidden files. A marker that also appears in the agent-visible projection does
not count and makes freeze refuse.

Requires Python >=3.12 and `mitmproxy>=12.3.1,<13` (pinned in `pyproject.toml`).

- Start here: [`planning/handoff.md`](planning/handoff.md)
- Plan: [`planning/implementation-plan.md`](planning/implementation-plan.md)
- Decisions: [`planning/ledger.md`](planning/ledger.md), [`docs/adr/`](docs/adr/README.md)
- Agent rules: [`AGENTS.md`](AGENTS.md)

Licensed under Apache-2.0.
