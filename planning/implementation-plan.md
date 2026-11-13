# Implementation plan (MVP)

Source of truth: [`ledger.md`](ledger.md) (decisions Q1–Q56, R1–R3, D1–D10, acceptance
criteria AC 1–23) and [`docs/adr/`](../docs/adr/README.md). This plan orders the work; it
decides nothing new. If a phase needs a decision the ledger lacks, add a ledger row first.

## Ground rules

- **TDD.** Every phase starts with failing tests. Security ACs live in `tests/security/`,
  each one plants the canary/sentinels and asserts they never reach the agent.
- **Hexagonal layout**, enforced by import-linter from phase 0:
  `cheeseless.domain` (pure) ← `cheeseless.ports` ← `cheeseless.app` ← `cheeseless.adapters`
  (`git`, `sandbox`, `runner`, `store`, `uds`, `mcp`, `egress`, `ci`). Adapters are
  independent of each other.
- **Contract first.** `proto/cheeseless/v1/*.proto` is the external contract; wire format is
  protojson over HTTP on a UDS (stack research §4). MCP is a thin adapter over it.
- **Fail closed** everywhere (Q43): any unexpected state yields FAIL or BLOCKED, never PASS.
- Pinned stack (stack research): Python ≥3.11, `uv`, `mcp>=2,<3` (`MCPServer`),
  `protobuf` + `grpcio-tools` or `buf-bin`, `httpx` (UDS transport), `import-linter`,
  `mitmproxy` 12.x, system `bwrap` ≥0.11. No srt (it cannot allow a single UDS on Linux).
- Each phase ends with: tests green, `lint-imports` green, ADR references checked, a
  ledger row for any deviation, and a cross-vendor review of the diff.

## Phases

| # | Phase | Delivers | ACs | Depends on | Gated by |
|---|-------|----------|-----|------------|----------|
| 0 | Skeleton | `pyproject.toml`, uv lock, package layout, import-linter contracts, `proto/cheeseless/v1` (Result, Criterion, Error, status enum PASS/FAIL/BLOCKED per D1), codegen, CI running lint + unit tests | 4 (schema), 23 (schema) | — | — |
| 1 | Domain | Task, Criterion, Result, `Error{code, reason, hint, retryable, details, docs}` with the code catalogue (ADR 0008); HMAC binding over task, tree hash, baseline digest, profile, tool version (Q43); disclosure stages pre/post hidden input (Q51) | 17, 23 | 0 | — |
| 2 | Trusted store and budget | Store port + filesystem adapter (mode 0700); HMAC key, seed, budget counters keyed by freeze digest, reserved atomically, shared by sessions and CI (30 local / 5 CI, D7), freeze digests; budget accounting rules: pre-hidden errors free (Q52), `INFRA_ERROR` after hidden input charged (D2), exhaustion behaviour | 17, 23 (budget part) | 1 | **Q55** (location, contents), **Q54** (exhaustion) |
| 3 | Snapshot projection | Projection of the repo without protected blobs (ADR 0002); sparse-checkout removes `acceptance/` except `contract/`, `doctor` checks for strays (D5); verifier materialises hidden tests from frozen git objects into tmpfs; git run with no hooks, filters or config; cleanup that never touches uncommitted work | 1, 11, 12 | 0 | — |
| 4 | Sandbox wrapper | Own `bwrap` wrapper with two profiles (ADR 0003). Both: user/PID/IPC/net namespaces, fresh `/proc`, no capabilities (D4). Agent: projection, its verifier socket, egress socket, ephemeral `HOME`. Candidate: no sockets, no network, throwaway overlay, scrubbed env, randomised paths, cgroup/rlimit limits, whole process tree killed | 2, 3, 15, 16 | 3 | — |
| 5 | Trusted runner | pytest from a trusted venv with tests and conftest from the store/base; `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, pinned `rootdir`/`confcutdir`, `-p no:cacheprovider`; `python -I`, scrubbed env and `PYTEST_ADDOPTS` (D4); criterion mapping; strict comparators; timeouts and crashes map to fail-closed results | 5, 14, 18 | 2, 4 | — |
| 6 | Verifier service | UDS server exposing only `run` and `status`; one socket per session in a private dir, peer pid must descend from the agent sandbox (D6); MCP adapter `gate_run`/`gate_status` with identical `structuredContent` and text block (stack research §2); staged disclosure (≈4 KB DLP-scanned stderr tail only pre-hidden, Q51); result HMAC verified on `status`; pre-hidden rate limit `RATE_LIMITED` (D10) | 4, 8, 10, 11, 17, 23 | 1, 2, 5 | — |
| 7 | Author phase and freeze | Host-only CLI for freeze/re-freeze/disable; preconditions (suite RED on baseline, no skip/xfail, no empty collection, ≥1 visible and ≥1 holdout test per criterion, static check that tests read no candidate paths/env); stratified holdout, seed generated after approval; sentinels per artifact class plus encoded forms; author memory write-ban; holdout rotation | 6, 19, 20, 22 | 2, 3, 5 | **Q54** (rotation trigger) |
| 8 | Egress DLP proxy | mitmproxy addon (ADR 0007): host UDS bind-mounted plus in-sandbox TCP→UDS forwarder (R3); per-session CA, `CODEX_CA_CERTIFICATE` / `NODE_EXTRA_CA_CERTS` (R1); domain allowlist, server-side tool rejection, registries GET-only, own package names and repo identity blocked; recursive archive scan with cap, uninspectable blocked (D9); streaming scan with held-back tail and forced identity encoding; teardown via supervisor, BLOCKED, alert without content (R2) | 2, 21 | 4, 7 (fingerprints) | — |
| 9 | Promote | Host CLI applying the exact evaluated tree to `gate/<task>`; path allowlist with the Q17 denylist underneath; sensitive files need operator approval | 7 | 6 | **Q56** (allowlist, sensitive list) |
| 10 | CI | Workflow where PR code runs without secrets and seed/HMAC key live in a separate job that never runs PR code (D8); criterion-level logs only; PR touching `acceptance/` fails; shares the run budget (5 CI) | 9 | 2, 5, 9 | **Q55** (secrets layout) |
| 11 | Toy repo and E2E | Separate toy repo with a canary in hidden tests; gated Claude Code and Codex sessions fix it end to end and reach green CI; overhead measurements | 10, 13 | 0–10 | — |
| 12 | Release hardening | Sanitizer pass over the whole tree and history; publish only after green CI and explicit operator confirmation (Q34) | — | 11 | operator |

Phases 3 and 4 can run in parallel with 1–2. Phase 8 can start once phase 4 exists, but
its fingerprint input comes from phase 7.

## Acceptance-criteria coverage

Every AC 1–23 appears in the table above at least once. AC 2 is covered twice: filesystem
escape in phase 4 and network escape in phase 8. AC 21 can only pass once phases 7 and 8
are both done.

## Open decisions that block phases

- **Q54** budget exhaustion and holdout rotation trigger → phases 2, 7.
- **Q55** trusted store location and contents, CI secret layout → phases 2, 10.
- **Q56** promote allowlist and sensitive-file list → phase 9.

Proposed defaults are in the ledger ("Proposed defaults pending operator confirmation").
Phases 0, 1, 3, 4, 5 and 6 can proceed before they are confirmed; design the store and
promote ports so the defaults can change without touching the domain.

## Accepted residual risks (not phases)

Training-data memorisation of public tests (D3), correlated model blind spots, timing
side channels below the run budget, bytes scanned clean before a DLP hit, and transformed
leaks below k-gram size. They are documented in ADR 0001 and ADR 0007, not solved.
