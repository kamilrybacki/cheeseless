---
status: proposed
date: 2026-11-13
reviewers: [gpt-6.1-terra]
---
# 0007. Route all gated egress through a host-side DLP proxy

## Context
Tests are plaintext on GitHub (ledger Q42): any network path to the repo, its mirrors or
a model's server-side web tools hands the agent hidden cases; an allowlist cannot stop it (Q48).

## Options
- **Allowlist only** (sandbox network filter): cheap, blind to content and server tools.
- **Host-side MITM proxy with DLP**: inspects every request and response.
- **No network**: impossible, the agent needs its model API and package registries.

## Decision
Host-side MITM proxy (mitmproxy addon), because only content inspection closes the
repo-content and server-tool paths (Q48, Q49, ledger R1–R3).
- Sandbox has an empty netns, no DNS, no UDP; one host UDS is bind-mounted and a tiny
  in-sandbox forwarder exposes it on `127.0.0.1` as `HTTPS_PROXY` (R3).
- Per-session CA; the key never enters the sandbox. Codex trusts it via
  `CODEX_CA_CERTIFICATE`, Claude Code via `NODE_EXTRA_CA_CERTS` (R1).
- Rules: domain allowlist (model APIs, named registries); model-API requests carrying
  server-side tools rejected (`EGRESS_SERVER_TOOL_DENIED`); registries GET only, the
  project's own package names and repo identity blocked.
- Every response and SSE stream is scanned for sentinels, encoded forms and winnowing
  fingerprints of hidden content minus public k-grams (Q48). Archives (by magic bytes) are
  buffered whole and forwarded only after recursive inspection is clean; over-cap,
  encrypted or uninspectable content is blocked (D9, D16).
- Streaming: `Accept-Encoding: identity` is forced; encoded streams are blocked. A chunk
  is forwarded only once scanned, holding back max(longest pattern, w + k − 1) (D16).
- On a hit, forwarding stops and the proxy tells the session supervisor to tear down
  the sandbox (`flow.kill()` cannot cut in-transit flows); session ends `BLOCKED`,
  holdout rotates, operator gets host, rule and fingerprint id, never content (Q49, R2).

## Consequences
- ✅ Known copies of hidden content are blocked on every allowed host and model-side tool.
- ⚠️ Bytes scanned clean before a hit may reach the client; hold-back adds latency.
- ⚠️ Short excerpts, paraphrases and transformed copies below k-gram size pass (ADR 0001).
