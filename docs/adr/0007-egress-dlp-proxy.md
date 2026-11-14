---
status: accepted
date: 2026-11-13
reviewers: [gpt-6.1-terra, gpt-6.1-sol]
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
Host-side MITM proxy (mitmproxy addon): only content inspection closes these paths (Q49, R1–R3).
- Empty netns, no DNS, no UDP; one host UDS is bind-mounted and an in-sandbox forwarder exposes
  it on `127.0.0.1` as `HTTPS_PROXY` (R3). Per-session CA, key never in the sandbox; Codex trusts
  it via `CODEX_CA_CERTIFICATE`, Claude Code via `NODE_EXTRA_CA_CERTS` (R1).
- Rules: domain allowlist (model APIs, named registries); model-API requests carrying server-side
  tools rejected (`EGRESS_SERVER_TOOL_DENIED`); registries GET only, the project's own package
  names and repo identity blocked.
- Every response and SSE stream is scanned for sentinels and their encoded forms; every k-gram
  (k = 24) is matched against digests of all hidden k-grams minus public ones, with no winnowing on
  the protected side (Q48, I7). Archives (by magic bytes) are buffered and forwarded only after a
  clean recursive inspection; over-cap, encrypted or uninspectable content is blocked (D9, D16).
- Streaming forces `Accept-Encoding: identity` and blocks encoded streams; a chunk is forwarded
  once scanned, holding back max(longest pattern, k − 1) (D16, I7).
- On a hit, forwarding stops and the proxy tells the session supervisor to tear down the sandbox
  (`flow.kill()` cannot cut in-transit flows); session ends `BLOCKED`, holdout rotates, operator
  gets host, rule and fingerprint id, never content (Q49, R2).

## Consequences
- ✅ Known copies of hidden content are blocked on every allowed host and model-side tool.
- ⚠️ Bytes scanned clean before a hit may reach the client; hold-back adds latency; a 64-bit
  k-gram hash collision can block clean text or drop a hidden k-gram as public (P8-12).
- ⚠️ Below-k excerpts, paraphrases, markers split across files or in other reversible encodings
  pass; HTTP upgrades and QUIC are blocked, not inspected (ADR 0001, P8-12, P8-13).
