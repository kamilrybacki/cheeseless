# Handoff — start here

State on 2026-11-14: phases 0–11 are implemented and merged. PR #1–#5 are merged and
`main` is `924d953`; PR #1/#2 landed the core phases, PR #3 the cross-run CI budget
counter, PR #4 `ci-budget-init`, and PR #5 the MCP identity and egress-audit follow-ups.
Branch `feat/release-sanitizer` adds the phase 12 local release audit (tracked tree,
local history including commit metadata, and built sdist/wheel) but is not merged yet.
ADRs 0001/0003/0005/0006/0007 were reworded to close H1–H4 and M2 and are `accepted`
after cross-vendor review (ledger "ADR re-review, round 3"). Release remains pending:
a clean local audit is not a release-readiness claim. This file is the entry point for a
fresh session on this repository.

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
- Phase 11 follow-ups (2026-11-14, ledger P11-26…P11-33): the staged MCP shim bakes the
  verifier socket and task id as quoted literals and ignores the session environment;
  every admitted egress request gets bounded allow-audit metadata in the trusted store and
  an audit failure fails closed; `tests/e2e/test_e2e_agent.py` is a real, operator-opt-in
  agent test with a deterministic harness suite. Cross-model review fixes (P11-32) closed
  both raised gaps: the audit method and rule are closed canonical sets (an unknown method
  is `OTHER`, never copied text), and the evidence scan is bounded, fd-relative,
  non-following and fail-closed, so a sparse file, FIFO, symlink or truncated capture is
  refused before any unbounded read. The harness also refuses a blocked or non-zero agent
  and requires the agent's own stored gate result. No paid agent request was made here.
  [`docs/agent-prompting.md`](../docs/agent-prompting.md) documents the
  per-criterion-only oracle for prompt authors; [`docs/egress-audit.md`](../docs/egress-audit.md)
  documents the audit schema.

- Phase 12 local release audit (2026-11-14, ledger P12-1…P12-4): `scripts/release_audit.py`
  scans the current tracked tree, every locally reachable git object plus commit
  messages/authors/paths, and the real sdist/wheel built by `uv build`. The official
  Gitleaks release is pinned to 8.30.1 with the published archive SHA-256
  (`scripts/install_gitleaks.py`); missing, incompatible or failing scanners and
  incomplete/capped/unsupported artifacts fail closed. Output is metadata only
  (rule/path/commit/line/count), raw scanner reports are deleted unread, and the private
  JSON report is `0600` outside the repository. The optional operator denylist also lives
  outside the repo and reports rule/location only. Archive inspection refuses secret/auth
  files, private state, `.env`, `acceptance/`, unsafe links/members, encryption and
  expansion caps; package completeness is checked against `src/cheeseless`. The only
  Gitleaks exception is `generic-api-key` for the synthetic fixture
  `tests/fixtures/release_audit/synthetic-token.txt`. `docs/release-audit.md` is the
  runbook and `.github/workflows/ci.yml` has a separate `release-audit` job. The local
  run on this branch was CLEAN over 147 local refs and two built archives; nothing was
  uploaded and no repository content was changed.

## Remaining

- Phase 10 follow-ups: the cross-run CI budget counter and `ci-budget-init` are merged
  (PR #3, PR #4; ledger P10-27…P10-30; retires P10-13 and P10-19). **P10-6 stays
  open** until the operator release gate runs on real GitHub: P1 acceptance plus the thirteen
  live checks in section 6 of [`research/ci-budget-counter.md`](research/ci-budget-counter.md)
  (P10-7); checks 12 and 13 cover `cheeseless ci-budget-init`, which seeds the ledger root
  (P10-29, P10-30). The `ci.yml` bwrap step is verified (P10-8).
- The repository is public since 2026-11-18 (operator decision). GitHub `refs/pull/1` and
  `refs/pull/2` still hold commits with the old author email; the operator accepted this at
  publication. Removing them needs GitHub Support.
- The real-agent E2E ran and PASSED on 2026-11-15 with Claude Code (ledger P14-2). It stays
  opt-in; run it with the manual command in
  [`docs/agent-prompting.md`](../docs/agent-prompting.md); it needs an operator-owned
  wrapper, credentials and an egress policy outside this repository.
- Phase 12 sanitizer: before publication on 2026-11-18 every branch and tag on GitHub was
  re-checked and carries only noreply author addresses; the only residue is the pull refs
  above. Public runners made CI free, so the release gate runs on every push again.
- Residual phase 8 limits (P8-12): freeze requires two private sentinel markers per
  artifact class, but markers split across files or hidden behind other reversible
  encodings are not detected; 64-bit rolling-hash collisions are theoretical; bytes clean before a mid-stream
  hit may reach the client; below-k paraphrases/transforms pass; archive caps block
  rather than partially inspect; HTTP upgrades/QUIC are blocked, not inspected; the
  operator alert sink is stderr plus the trusted-store event log.
- `serve-uds` accepts `--pidns-fd` for an inherited namespace FD and fails closed without
  one; the normal path is `host_session`, which obtains and pins the sandbox FD itself.
- Real-bwrap egress bridge tests skip when bubblewrap is unavailable.
- The allow audit is one line per admitted stage. Total log growth is proportional to
  allowed traffic (bounded per event and at most two events per request, but no global
  session cap), and a failed response-stage audit cannot retract response bytes already
  forwarded. Both are operator-visible residuals, not hidden-content paths.

## Verification on 2026-11-14 (phase 12 release audit)

```
uv run --frozen pytest                 # 1181 passed, 2 skipped, 87.79% coverage
uv run --frozen ruff check .           # clean
uv run --frozen mypy                   # no issues in 82 source files
uv run --frozen lint-imports           # 4 contracts kept
uv run --frozen python scripts/gen_proto.py --check
uv run --frozen python scripts/gen_errors_doc.py --check
uv run --frozen cheeseless --version   # cheeseless 0.0.0
uv run --frozen python scripts/release_audit.py \
  --gitleaks /tmp/gitleaks --report-dir /tmp/cheeseless-release-audit-run2
# CLEAN: 147 local refs, 2 built archives, Gitleaks 8.30.1
```

The audit run covered the current tree, full local history and the actual sdist/wheel.
It did not contact a remote, upload a scan, inspect server-side pull refs, run the live
GitHub `P10-6` checks or run the opt-in real-agent E2E. Two tests are skipped: the
opt-in real-agent test and one environment-dependent test.

## Verification on 2026-11-13 (phase 10)

```
uv run --frozen pytest                 # 497 passed, 85.65% coverage
ruff check . / mypy / lint-imports (4 kept) / gen_proto --check / gen_errors_doc --check: clean
```

## Verification on 2026-11-14 (P11-11 follow-ups, after cross-model review fixes)

```
uv run --frozen pytest                 # 1166 passed, 2 skipped, 87.79% coverage
uv run --frozen ruff check .           # clean
uv run --frozen mypy                   # no issues in 80 source files
uv run --frozen lint-imports           # 4 contracts kept
uv run --frozen python scripts/gen_proto.py --check
uv run --frozen python scripts/gen_errors_doc.py --check
uv run --frozen cheeseless --version   # 0.0.0
```

The earlier 1133/87.79% run (ledger P11-31) predates the review fixes; P11-33 records this
run. No live GitHub workflow, real CI budget ledger, opt-in real-agent E2E or real model
request was run for this change.

## Verification on 2026-11-14

```
uv run pytest --cov                    # 1037 passed, 1 skipped, 87.55% coverage (pre-PR #3/#4)
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