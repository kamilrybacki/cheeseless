# Implementation plan (MVP)

Source of truth: [`ledger.md`](ledger.md) (decisions Q1–Q56, R1–R3, D1–D17, acceptance
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
- Pinned stack (stack research, revised by ledger P8-1): Python ≥3.12, `uv`, `mcp>=2,<3` (`MCPServer`),
  `protobuf` + `grpcio-tools` or `buf-bin`, `httpx` (UDS transport), `import-linter`,
  `mitmproxy>=12.3.1,<13`, system `bwrap` ≥0.11. No srt (it cannot allow a single UDS on Linux).
- Each phase ends with: tests green, `lint-imports` green, ADR references checked, a
  ledger row for any deviation, and a cross-vendor review of the diff.

## Phases

| # | Phase | Delivers | ACs | Depends on | Gated by |
|---|-------|----------|-----|------------|----------|
| 0 | Skeleton | `pyproject.toml`, uv lock, package layout, import-linter contracts, `proto/cheeseless/v1` (Result, Criterion, Error, status enum PASS/FAIL/BLOCKED per D1), codegen, CI running lint + unit tests | 4 (schema), 23 (schema) | — | — |
| 1 | Domain | Task, Criterion, Result, `Error{code, reason, hint, retryable, details, docs}` with the code catalogue (ADR 0008); HMAC over the canonical encoding of every `Result` field incl. status and criteria, with a tampering test (Q43, P11); constructors reject UNSPECIFIED/unknown values (P16); disclosure stages pre/post hidden input (Q51) | 17, 23 | 0 | — |
| 2 | Trusted store and budget | Store port + filesystem adapter (mode 0700); HMAC key, seed, budget counters keyed by task lineage (re-freeze and rotation carry the count, D14), reserved atomically, shared by sessions and CI (30 local / 5 CI, D7), freeze digests; budget accounting rules: pre-hidden errors free (Q52), `INFRA_ERROR` after hidden input charged (D2), exhaustion behaviour | 17, 23 (budget part) | 1 | — |
| 3 | Snapshot projection | Projection of the repo without protected blobs (ADR 0002); sparse-checkout removes `acceptance/` except `contract/`, fail-closed stray check at session start before projection and agent launch, also run by `doctor` (D5, D11); verifier materialises hidden tests from frozen git objects into tmpfs; git run with no hooks, filters or config; cleanup that never touches uncommitted work | 1, 11, 12 | 0 | — |
| 4 | Sandbox wrapper | Own `bwrap` wrapper with two profiles (ADR 0003). Both: user/PID/IPC/net namespaces, fresh `/proc`, no capabilities (D4). Agent: projection, its verifier socket, egress socket, ephemeral `HOME`. Candidate: no sockets, no network, throwaway overlay, scrubbed env, randomised paths, cgroup/rlimit limits, whole process tree killed; seccomp denies `ptrace`/`process_vm_*`, runner outside candidate PID/mount namespaces with `PR_SET_DUMPABLE=0`, security test with canary (D12) | 2, 3, 15, 16 | 3 | — |
| 5 | Trusted runner | pytest from a trusted venv with tests and conftest from the store/base; `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, pinned `rootdir`/`confcutdir`, `-p no:cacheprovider`; `python -I`, scrubbed env and `PYTEST_ADDOPTS` (D4); criterion mapping; strict comparators; timeouts and crashes map to fail-closed results; post-hidden infra cause stored for the operator, agent sees FAIL (D17) | 5, 14, 18 | 2, 4 | — |
| 6 | Verifier service | UDS server exposing only `run` and `status`; one socket per session in a private 0700 dir, peer pidfd (`SO_PEERPIDFD`) must be in the agent sandbox's PID namespace, refuse otherwise (D6, D13); MCP adapter `gate_run`/`gate_status` with identical `structuredContent` and text block (stack research §2); staged disclosure (≈4 KB DLP-scanned stderr tail only pre-hidden, Q51); result HMAC verified on `status`; inbound mapping rejects UNSPECIFIED/unknown enums, empty oneof, missing ids (P16); pre-hidden rate limit `RATE_LIMITED` (D10) | 4, 8, 10, 11, 17, 23 | 1, 2, 5 | — |
| 7 | Author phase and freeze | Host-only CLI for freeze/re-freeze/disable; preconditions (suite RED on baseline, no skip/xfail, no empty collection, ≥1 visible and ≥1 holdout test per criterion, static check that tests read no candidate paths/env); stratified holdout, seed generated after approval; sentinels per artifact class plus encoded forms; author memory write-ban; holdout rotation | 6, 19, 20, 22 | 2, 3, 5 | — |
| 8 | Egress DLP proxy | mitmproxy addon (ADR 0007): host UDS bind-mounted plus in-sandbox TCP→UDS forwarder (R3); per-session CA, `CODEX_CA_CERTIFICATE` / `NODE_EXTRA_CA_CERTS` (R1); domain allowlist, server-side tool rejection, registries GET-only, own package names and repo identity blocked; archives detected by magic bytes, buffered whole and forwarded only after a clean recursive scan with cap, uninspectable blocked (D9, D16); streaming scan holding back max(longest pattern, w + k − 1) with forced identity encoding (D16); teardown via supervisor, BLOCKED, alert without content (R2) | 2, 21 | 4, 7 (fingerprints) | — |
| 9 | Promote | Host CLI applying the exact evaluated tree to `gate/<task>`; path allowlist with the Q17 denylist underneath, winning over config and approval (Q56); sensitive files need operator approval | 7 | 6 | — |
| 10 | CI (implemented 2026-11-13; see ledger P10-9…P10-14) | Judge workflow defined on the base branch (`workflow_run`) fetches the PR head as data and builds and tests it only in the candidate sandbox; seed and key stay in the runner process; PR-defined jobs have no secrets and a read-only token; no `pull_request_target` with PR checkout (D15, revises D8); no per-criterion holdout results in logs; PR touching `acceptance/` fails; shares the run budget (5 CI) | 9 | 2, 5, 9 | — |
| 11 | Toy repo and E2E | Separate toy repo with a canary in hidden tests; gated Claude Code and Codex sessions fix it end to end and reach green CI; overhead measurements | 10, 13 | 0–10 | — |
| 12 | Release hardening | Sanitizer pass over the whole tree and history; publish only after green CI and explicit operator confirmation (Q34) | — | 11 | operator |

Phases 3 and 4 can run in parallel with 1–2. Phase 8 can start once phase 4 exists, but
its fingerprint input comes from phase 7.

**Phase 8 status (2026-11-13).** End-to-end proxy, freeze fingerprints, pre-hidden stderr
DLP and supervisor are implemented. Residual protections are listed in ledger P8-9
(per-class sentinel minimum, alert transport, full bridge parity); they do not gate the
working end-to-end path but must close before release.

## Acceptance-criteria coverage

Every AC 1–23 appears in the table above at least once. AC 2 is covered twice: filesystem
escape in phase 4 and network escape in phase 8. AC 21 can only pass once phases 7 and 8
are both done.

## Open decisions that block phases

Q54–Q56 were accepted on 2026-11-13 with their proposed defaults (ledger). Still design
the store and promote ports so these defaults can change without touching the domain.

Open after ADR re-review round 2 (ledger, 2026-11-13; ADRs 0003/0005/0006/0007 stay
`proposed` until resolved and re-reviewed). Phases 0, 1 and 3 are not blocked.

| Item | Decision needed | Blocks phases |
|------|-----------------|---------------|
| H1 | Runner/candidate execution boundary wording (G2) | 4, 5, 10 |
| H2 | Judge on the default branch: PR/SHA binding, required check with pinned source | 10 |
| H3 | Shared budget vs separate local/CI pools; trusted CI counter | 2, 10 |
| H4 | Winnowing with public k-grams removed (or residual risk) | 8 |
| M2 | Freeze identity vs budget identity wording in 0005 | 2 |

## Accepted residual risks (not phases)

Training-data memorisation of public tests (D3), correlated model blind spots, timing
side channels below the run budget, bytes scanned clean before a DLP hit, and transformed
leaks below k-gram size. They are documented in ADR 0001 and ADR 0007, not solved.
