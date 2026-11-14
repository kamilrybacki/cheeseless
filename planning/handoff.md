# Handoff — start here

State on 2026-11-14: phases 0–11 are implemented and merged (PR #1, PR #2). Branch
`feat/release-blockers` closes release blockers: judge secret isolation (P10-22), the CI
budget counter design (P10-23…P10-26) and the per-class sentinel minimum (P8-12, P8-13).
Release (phase 12) is pending.
This file is the entry point for a fresh session on this repository.

## What cheeseless is

A git-native black-box quality gate for coding agents. A human (or an author agent) freezes
acceptance tests; a gated coding agent then works on the code without ever seeing the
hidden tests, and can only ask a sandboxed verifier "does my tree pass?". The verifier
answers per criterion, within a run budget, with results bound to the exact tree. CI is the
source of truth; changes land through `promote`, not hooks.

## Read in this order

1. [`ledger.md`](ledger.md) — every decision (Q1–Q56), research findings (R1–R3), derived
   rows (D1–D17) and the MVP acceptance criteria (AC 1–23). Authoritative.
2. [`../docs/adr/`](../docs/adr/README.md) — ADRs 0001–0008, status `proposed`/`accepted`.
3. [`implementation-plan.md`](implementation-plan.md) — phases 0–12 mapped to ACs.
4. [`research/`](research/) — stack verification and the two independent attack-vector
   reviews plus their merge. Background only; the ledger wins on conflicts.
5. [`../AGENTS.md`](../AGENTS.md) — rules for agents working here (ADR format, review, security).

## Implemented so far

- Phase 0 skeleton, proto contract, import-linter.
- Phase 1 domain, Phase 2 trusted store/budget, Phase 3 projection, Phase 4 sandbox,
  Phase 5 trusted runner, Phase 6 verifier service, Phase 7 freeze, Phase 8 egress DLP
  proxy, Phase 9 promote.
- Phase 6 closure: UDS peer auth requires a lifetime-held sandbox PID-namespace FD and
  refuses missing/host/wrong/unsupported peers; `bootstrap.host_session()` launches the
  bwrap `agent` profile with only the projection, HOME, verifier socket, egress UDS and a
  read-only staged runtime, runs
  `stray_hidden` before launch and holds the namespace FD through teardown; MCP stdio
  needs only the socket and task and never opens the trusted store; socket directory and
  wire limits are hardened (details in ledger P6-6…P6-12).
- Host CLI via composition root: `freeze`, `run`, `status`, `promote`, `doctor`,
  `serve-uds`, `serve-mcp`.
- Phase 8: digest-only hidden fingerprints (every hidden k-gram minus public k-grams,
  k=24; sentinel raw/base64/hex/reversed digests; no winnowing on the protected side),
  freeze stores them with the task and runs the hidden suite twice to reject flakes.
  mitmproxy addon: exact host allowlist, GET-only registries, own-identity markers,
  server-side tool schema rejection, identity encoding, URL/header/body response scan,
  hold-back streaming with EOF tail scan, magic-byte archive detection with recursive
  in-memory inspection and caps, catalogue errors plus `X-Cheeseless-Code`. A hit
  persists signed `BLOCKED`, rotates the holdout (same task id, same lineage budget),
  kills the whole agent process tree and alerts with host/rule/fingerprint id only.
  `host_session` mounts a staged read-only client runtime outside the repo, trusts the
  per-session CA via `CODEX_CA_CERTIFICATE`/`NODE_EXTRA_CA_CERTS`/`SSL_CERT_FILE`, and
  exposes only the verifier UDS plus the egress UDS/loopback bridge. bwrap startup is
  deadline-bounded and `launch_host_session` waits for the agent.
- Pre-hidden build/smoke stderr tails are DLP-scanned; a hit, missing scanner or scanner
  failure omits the tail rather than exposing unscanned text.

- Phase 10 CI judge (2026-11-13): `ci-export` writes the 0600 secret bundle; `ci-judge`
  checks freeze drift (exit 3), fails PRs touching protected paths with no run, exports the
  head tree (never a checkout), runs the full suite in the sandbox and prints only the
  aggregate and visible-criterion statuses. Template in `docs/ci/`. See ledger P10-9…P10-14.

- Phase 11 E2E (2026-11-13): Claude Code and Codex each fixed the toy inside
  `cheeseless session` via MCP `gate_run`, the canary stayed out of the projection, and
  `promote` + `ci-judge` passed. Signalled sessions now tear down (P11-7). Timings and Codex
  operator requirements are in ledger P11-9 and P11-10.

## Remaining

- Phase 10 follow-ups: implement the trusted cross-run CI budget counter designed in
  [`research/ci-budget-counter.md`](research/ci-budget-counter.md) (P10-6, required before
  release); then the operator release gate on real GitHub (section 6 of that doc, P10-7).
  The `ci.yml` bwrap step is verified (P10-8).
- Phase 11 follow-ups (ledger P11-11): bake socket/task into the MCP shim; per-request allow-log; an opt-in `e2e_agent` test that drives a real agent.
- Phase 12 sanitizer and publication.
- Residual phase 8 limits (P8-12): freeze requires two private sentinel markers per
  artifact class, but markers split across files or hidden behind other reversible
  encodings are not detected; 64-bit rolling-hash collisions are theoretical; bytes clean before a mid-stream
  hit may reach the client; below-k paraphrases/transforms pass; archive caps block
  rather than partially inspect; HTTP upgrades/QUIC are blocked, not inspected; the
  operator alert sink is stderr plus the trusted-store event log.
- `serve-uds` accepts `--pidns-fd` for an inherited namespace FD and fails closed without
  one; the normal path is `host_session`, which obtains and pins the sandbox FD itself.
- Real-bwrap egress bridge tests skip when bubblewrap is unavailable.

## Verification on 2026-11-13 (phase 10)

```
uv run --frozen pytest                 # 497 passed, 85.65% coverage
ruff check . / mypy / lint-imports (4 kept) / gen_proto --check / gen_errors_doc --check: clean
```

## Verification on 2026-11-14

```
uv run --frozen pytest                 # 781 passed, 1 skipped, 86.29% coverage
uv run --frozen ruff check .
uv run --frozen mypy                   # no issues
uv run --frozen lint-imports           # 4 contracts kept
uv run --frozen python scripts/gen_proto.py --check
uv run --frozen python scripts/gen_errors_doc.py --check
uv run --frozen cheeseless --version
```

## Constraints

- The repository is **private** until the sanitizer passes, CI is green and the operator
  explicitly confirms publication (Q34).
- No secrets, host names or personal infrastructure in this repo. Integration with a
  particular workstation or agent platform lives outside it.
- Accepted ADRs are immutable; supersede them with a new ADR.