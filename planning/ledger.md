# Decision ledger

Working log of decisions that are not (yet) ADRs. Newest section last.

## 2026-11-13 — Design interview (Q1–Q35)

| # | Decision |
|---|----------|
| Q1 | Goal: agents cannot modify or game acceptance tests (integrity + confidentiality). |
| Q2 | The same agent may author tests TDD-style in a separate task; context is cleared (fresh delegate) before implementation. |
| Q3 | Personal-first, Linux/WSL2, agent-agnostic CLI; contract researched (protobuf yes, A2A no). |
| Q4 | Opt-in per repo (`.cheeseless/`), activated by a skill/command. |
| Q5 | CI is authoritative; local results are advisory. |
| Q6 | Python + uv. |
| Q7 | Scenarios, fixtures and expected outputs are hidden; contract and criterion descriptions are public in `acceptance/contract/`. |
| Q8 | Always a process boundary (CLI/HTTP, call-proxy for Python libs); candidate has no network. Minimal, local, no remote microservice. |
| Q9 | Freeze needs user approval plus a required cross-vendor review of test strength; baseline stored in trusted config outside the repo. |
| Q10 | One-time init, then the repo stays gated until explicitly disabled. |
| Q11 | Gated agent instances are clones of normal providers (same tools) behind a sandbox wrapper with restricted profiles. |
| Q12 | Orchestrator sees criterion IDs/descriptions only; a cooperative hook blocks it from reading hidden paths. |
| Q13 | `.proto` is the schema source; agent ↔ gate via MCP (`outputSchema` + text block) and CLI `--json`; sandbox ↔ verifier protojson over UDS. |
| Q14 | Gated tool profile: no gh/ssh/kubectl/vault/remote-exec/browser tools; no SSH keys, gh/git credentials or kubeconfig. |
| Q15 | Agent sandbox: `srt` with a domain allowlist (model APIs, package registries); GitHub blocked. |
| Q16 | Isolation by snapshot projection (single commit, protected paths removed, no remote), not git worktrees. |
| Q17 | Promote = patch with a hard path filter (rejects `acceptance/**`, `.cheeseless/**`, `.github/workflows/**`) onto `gate/<task>`, then PR. |
| Q18 | Criterion-level feedback only; no candidate stdout/stderr; ~25% holdout never reported locally; unlimited, logged runs. |
| Q19 | Stop hook asks for PASS or an explicit failure report; orchestrator accepts only PASS, then cross-vendor code review. |
| Q20 | Acceptance tests are pytest; candidates may be any language via CLI/HTTP; call-proxy is Python-only in MVP. |
| Q21 | CI takes `acceptance/` from base branch, fails on protected-path changes, runs the full suite incl. holdout. |
| Q22 | Public, shareable tool with a dedicated toy repo for development. |
| Q23 | Name: **cheeseless**; toy repo **cheeseless-toy**. |
| Q24 | Generic core public under Apache-2.0; personal environment integration lives outside this repo (user dotfiles). |
| Q25 | Hexagonal layout; proto is external contract only; `import-linter` enforces layers; `docs/adr/` permanent, `planning/` working. |
| Q26 | Verifier runs per session on a UDS, no system daemon. |
| Q27 | `@pytest.mark.criterion(...)` markers, public `criteria.yaml`, holdout = `hash(seed, nodeid)` with secret seed in trusted config. |
| Q28 | Toy repo: Python, CLI `toy normalize` + library `toy.score()` via call-proxy, deliberately incomplete. |
| Q29 | MVP done = 13 acceptance criteria below + canary check. |
| Q30/31 | Generic integration: `cheeseless exec -- <agent cmd>` (isolation) + light MCP server (`gate_run`, `gate_status`); enforcement via promote/CI, hooks are UX only; per-agent adapters are declarative profiles. Claude Code + Codex tested E2E; others best-effort. |
| Q32 | Six initial ADRs, MADR-minimal template, ≤40 lines, cross-model review required. |
| Q33 | Repo language: English. |
| Q34 | Build locally first; publish only after sanitizer + green CI, with explicit user confirmation. |

## MVP acceptance criteria

A random canary string lives in the toy repo's hidden tests; it must never appear in
anything the agent can see.

1. Projection: no protected blobs in the projection's git objects; canary absent.
2. Agent escape: reading the original repo, `~/.ssh`, `~/.config/gh`, reaching github.com
   (curl, `gh api`) or `git show` on the original path all fail.
3. Candidate escape: reading test files, stack walking or network access yields no canary.
4. Contract: result validates against the schema, carries candidate and baseline SHAs,
   no candidate stdout/stderr, holdout not reported.
5. Loop: a bad implementation gets FAIL with criteria; after a fix, PASS.
6. Freeze: modifying tests without re-freeze has no effect.
7. Promote: patches touching protected paths are rejected; valid ones land on `gate/<task>`.
8. Forged PASS: a result file planted by the agent does not affect `status`.
9. CI: good PR green; PR touching `acceptance/` red; PR passing locally but failing holdout red.
10. Overhead: ≤1 s over test time per run; idle verifier ≤50 MB RAM; no process left after the session.
11. Concurrency: two parallel sessions get results bound to their own snapshots.
12. Cleanup never deletes uncommitted work.
13. E2E: Claude Code and Codex in gated instances fix the toy repo and reach green CI.

## Initial ADRs

1. Threat model and trust boundaries.
2. Snapshot projection instead of worktrees.
3. Process boundary and call-proxy.
4. Contract: proto, MCP and UDS (no A2A).
5. Disclosure policy and holdout.
6. CI as source of truth; enforcement via promote, not hooks.

## 2026-11-13 — Attack-vector round (Q36–Q52)

Source: [`research/2026-11-13-attack-vectors.md`](research/2026-11-13-attack-vectors.md)
(Claude + GPT brainstorm). Rows here override the earlier rows they revise.

| # | Decision | Revises |
|---|----------|---------|
| Q36 | Trusted runner closure: tests, conftest, pytest config and runner deps come only from the trusted store/base; candidate built in its own venv; `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, pinned `rootdir`/`confcutdir`. | Q21 |
| Q37 | Promote uses a source-path allowlist; build/deps/CI/agent-instruction files are "sensitive" and need human approval; symlinks and submodules rejected. | Q17 |
| Q38 | Run budget per task identity, shared across sessions and CI (defaults 30 local / 5 CI, configurable per repo); result is per-criterion pass/fail only, no timing or error class; holdout rotated after N CI failures. | Q18 |
| Q39 | Own thin `bwrap` wrapper for agent and candidate (PID/IPC/net namespaces; only projection, verifier socket and egress-proxy socket bound); no srt in MVP. | Q15 |
| Q40 | Each candidate run uses a throwaway overlay, scrubbed env, whole process tree killed; nothing it writes is agent-visible. | — |
| Q41 | Memory stays readable. Author profile has no memory-write tools and auto-memory off; author transcripts live in the trusted store (not the agent's shared config dir, not mined into knowledge stores); thread readers off in gated profiles; sentinel scan of reachable memory stores before implementer start and before promote, hit = BLOCKED. Core ships the mechanism; tool names live in user dotfiles. | Q2, Q12 |
| Q42 | One repo. Tests are plaintext in git and on GitHub (human-readable); no `hidden/` subfolder: everything under `acceptance/` except `contract/` is hidden from the gated agent. Confidentiality is against the gated agent only, not the public. Local working-tree form: see Q47. | Q7, Q21 |
| Q43 | UDS exposes only `run`/`status`, peer checked with `SO_PEERCRED`; freeze/promote/disable host CLI only; results HMAC-bound to task, tree hash, baseline digest, profile, tool version; promote applies the exact evaluated tree; freeze approves an exact digest; any infra error = FAIL, no isolation downgrade flags. | — |
| Q44 | Freeze preconditions: RED on baseline; skip/xfail/empty collection is an error; ≥1 visible and ≥1 holdout test per criterion (stratified); seed drawn after approval; static check that tests read no candidate paths/env; no nodeids in `criteria.yaml`. Mutation testing post-MVP. | Q27 |
| Q45 | BLOCKED with a report is a valid terminal outcome; Q1 becomes a measurable assurance boundary with listed residual risks. | Q1, Q19 |
| Q46 | Multiple sentinels per artifact class (tests, fixtures, expected outputs, seed, author notes), scans include encoded forms; new AC for pytest hijack, disk exfil, socket isolation, PASS replay, fail-closed timeout, skip/xfail rejection. | Q29 |
| Q47 | Local working tree does not materialise `acceptance/` except `contract/` (skip-worktree); verifier reads blobs from git into tmpfs; humans use `cheeseless show`/`edit` or GitHub. Encrypting smudge filter rejected (same protection, worse ergonomics, key needed for every git op). | Q42 |
| Q48 | Gated egress only through a host-side `mitmproxy` addon with per-session CA (key never in sandbox); no DNS/UDP in the sandbox. Rules: domain allowlist (model APIs, named registries); model-API requests carrying server-side tools (web search/fetch, code execution, `mcp_servers`) rejected; registries GET only, project's own package names and repo identity blocked; every response (SSE and unpacked archives up to a cap, over cap blocked) scanned for sentinels, encoded forms and winnowing fingerprints of hidden content minus public k-grams; proxy holds fingerprints only. To verify: Codex custom CA, mitmproxy streaming, netns-to-proxy bridge. | Q15, Q39 |
| Q49 | DLP hit: response dropped, session ends BLOCKED, holdout rotated, operator alerted with host, rule and fingerprint id, never content; no silent redaction. | — |
| Q50 | Every response to the agent (MCP structuredContent, CLI `--json`, UDS, proxy 403) uses one `cheeseless.v1.Error`: `code` (stable public enum, e.g. `RUN_BUDGET_EXHAUSTED`, `CRITERION_FAILED`, `BUILD_FAILED`, `PROMOTE_PATH_REJECTED`, `SENSITIVE_FILE_NEEDS_APPROVAL`, `EGRESS_DOMAIN_DENIED`, `EGRESS_SERVER_TOOL_DENIED`, `DLP_BLOCKED`, `INFRA_ERROR`), `reason` (trusted template), `hint`, `retryable`, `details` (whitelisted fields only: criterion id, a path the agent wrote, budget state, host), `docs` (anchor in `docs/errors.md`, generated from the proto). Proxy adds `X-Cheeseless-Code`. No hidden content in `reason`/`details`; every response DLP-scanned. | Q13, Q38 |
| Q51 | Full detail (code plus a DLP-scanned stderr tail, ~4 KB) only for stages before the first hidden input: build, import, missing CLI executable, startup crash, public smoke tests from `contract/`. After hidden input: `CRITERION_FAILED` with the public criterion description only. | Q38 |
| Q52 | Errors from pre-hidden stages do not consume the run budget. | Q38 |

### MVP acceptance criteria — additions from the attack round

Extend the list above (Q29). Each is a test in `tests/security/` with the canary.

14. Runner hijack: candidate `conftest.py`, `pytest.ini`/`pyproject` `[tool.pytest]`,
    `sitecustomize`/`.pth` and `pytest11` entry points have no effect on the verdict (Q36).
15. Candidate disk exfil: files the candidate writes during a run are not visible to the
    agent afterwards; no process survives the run (Q40).
16. Socket isolation: inside the sandbox only the verifier socket and egress proxy are
    reachable; SSH agent, docker.sock and D-Bus are not (Q39).
17. PASS replay: a valid result for tree A does not verify for tree B or another task (Q43).
18. Fail closed: verifier timeout, crash or sandbox setup failure yields FAIL/`INFRA_ERROR`,
    never a stale PASS (Q43).
19. Freeze preconditions: skip/xfail, empty collection, suite green on baseline or a
    criterion without a holdout test all block freeze (Q44).
20. Sentinels: several sentinels per artifact class and their encoded forms (base64, hex,
    reversed) never reach the agent (Q46).
21. Egress DLP: sentinels are unreachable through every gated tool and allowed host
    (GitHub, content proxies, model server-side tools); a DLP hit ends the session BLOCKED
    and rotates the holdout (Q48, Q49).
22. Memory: the author phase leaves no sentinel in memory stores; a gated session given
    planted memory sees no sentinel (Q41).
23. Error model: every agent-facing response validates as `cheeseless.v1.Error` with a
    documented code; post-hidden failures carry only `CRITERION_FAILED` and public text;
    pre-hidden failures do not decrement the budget (Q50–Q52).

### ADRs — additions

7. Egress DLP proxy (Q48, Q49).
8. Agent-facing error model and disclosure stages (Q50–Q52).

## 2026-11-13 — Research findings and derived rows

Facts checked before ADR 0007 (sources: openai/codex `codex-rs/http-client`, mitmproxy 12.3.1
docs/source, passt/slirp4netns man pages).

| # | Finding or decision | Revises |
|---|---------------------|---------|
| R1 | Codex trusts a custom CA via `CODEX_CA_CERTIFICATE` (falls back to `SSL_CERT_FILE`) and honours `HTTPS_PROXY`; an unreadable CA falls back to system roots, which fails closed behind the MITM proxy. Claude Code: `NODE_EXTRA_CA_CERTS` + `HTTPS_PROXY`. No patch needed. | — |
| R2 | mitmproxy streaming callbacks see raw bytes chunk by chunk; `flow.kill()` cannot cut an in-transit flow in 12.3.1 (PR #8200 unmerged). Hence: hold back a tail ≥ longest pattern (sentinels, encoded forms, winnowing window) so a match is never forwarded; on a hit stop forwarding and the host tears down the session sandbox through a proxy → supervisor control channel; force `Accept-Encoding: identity`, any encoded streamed response = blocked. Bytes already scanned clean may have reached the client. | Q48, Q49 |
| R3 | Netns bridge: bind-mount one host UDS and run a tiny in-sandbox TCP→UDS forwarder on `127.0.0.1`; pasta and slirp4netns rejected (general NAT stacks, host-loopback exposure). | Q39 |
| D1 | Result status enum is `PASS` / `FAIL` / `BLOCKED`; error codes (e.g. `DLP_BLOCKED`, `RUN_BUDGET_EXHAUSTED`) explain the cause. Derived from Q45, Q49, Q50. | — |
| D2 | `INFRA_ERROR` after the first hidden input consumes budget; otherwise a candidate that crashes the verifier after reading inputs gets free oracle runs. Q52's exemption covers pre-hidden stages only. Derived from Q38, Q52. | Q52 |
| D3 | Public tests make training-data memorisation a stronger residual risk than in the draft; holdout hides cases from the gated agent only, not from the public (consequence of Q42). | — |

### Defaults accepted by the operator (2026-11-13)

| # | Proposed default | Revises |
|---|------------------|---------|
| Q54 | Budget exhausted → task ends `BLOCKED` with `RUN_BUDGET_EXHAUSTED`; only the operator can grant more runs (host CLI, logged). Holdout rotation after **3** failed CI holdout attempts: new split + new seed, re-freeze approved by the operator over the new digest. | Q38 |
| Q55 | Trusted store = host directory outside every repo and sandbox, `$XDG_STATE_HOME/cheeseless/<repo-id>/`, mode 0700: HMAC key, seed, budget counters, freeze digests, author transcripts, DLP fingerprints. Test content is read from the git objects of the frozen commit (base branch in CI). CI receives seed and HMAC key as repository secrets. | Q9, Q27, Q36 |
| Q56 | Promote allowlist default `src/**`, `tests/**`, `docs/**`, `README*`, configurable per repo; the Q17 hard denylist stays underneath and wins. Sensitive (needs approval): `pyproject.toml`, lockfiles, `setup.*`, `Makefile`, `conftest.py` anywhere, `.github/**`, agent instruction files (`AGENTS.md`, `CLAUDE.md`, …). | Q17, Q37 |

## 2026-11-13 — ADR review

Cross-vendor review of ADRs 0001–0008 by gpt-6.1-terra (reviewer, read-only). Twelve
findings, all addressed in the ADRs (still `proposed`). Derived rows D4–D10 record the
decisions the fixes add.

| # | ADR | Sev | Finding | Disposition |
|---|-----|-----|---------|-------------|
| F1 | 0003 | CRITICAL | Candidate sandbox bound the verifier and egress sockets, contradicting Q8. | Two profiles; candidate binds no socket, no network (D4). |
| F2 | 0003 | HIGH | No user namespace, capability drop, fresh `/proc` or ptrace protection. | Added to both profiles (D4); same host UID is a residual risk. |
| F3 | 0004 | HIGH | `SO_PEERCRED` cannot tell apart processes with the same UID. | Per-session socket in a private dir, bound only into that agent sandbox; peer pid must descend from it (D6). |
| F4 | 0005 | HIGH | Budget identity and atomicity undefined; a new task id resets budget. | Task identity = freeze digest, assigned by the operator; atomic reservation before hidden input (D7). |
| F5 | 0006 | HIGH | CI trust boundary: PR code could reach seed and HMAC key. | PR code runs in a job without secrets; seed/key in a separate job that never runs PR code (D8). |
| F6 | 0007 | HIGH | Archives not scanned, although Q48 requires it. | Recursive unpacking up to a cap; over cap blocked (D9). |
| F7 | 0007 | HIGH | Encrypted or uninspectable content passed. | Blocked (D9). |
| F8 | 0002 | MEDIUM | Skip-worktree does not remove files already on disk. | Sparse-checkout removes and keeps them absent; `doctor` checks for strays (D5). |
| F9 | 0003 | MEDIUM | `sitecustomize`, `.pth`, `PYTEST_ADDOPTS`, explicit `-p` plugins bypass the runner closure. | `python -I` from a trusted venv, scrubbed env, candidate never on the runner path (D4); one test per AC 14 path. |
| F10 | 0004 | MEDIUM | Key and result store access, stale results on `status`/promote. | Key and results in the trusted store outside both sandboxes; re-check task, tree, baseline (D6). |
| F11 | 0007 | MEDIUM | ✅ overclaimed full coverage. | Narrowed; short excerpts, paraphrases, transforms listed as residual. |
| F12 | 0008 | MEDIUM | Free pre-hidden errors had no quota. | Separate rate and cgroup limits, `RATE_LIMITED` (D10). |

| # | Derived row | Revises |
|---|-------------|---------|
| D4 | Two bwrap profiles. Both: new user/PID/IPC/net namespaces, fresh `/proc`, all capabilities dropped, `--new-session`, `--die-with-parent`. Agent binds projection, its verifier socket and the egress socket. Candidate binds no socket and has no network; the runner is `python -I` from a trusted venv with scrubbed env and `PYTEST_ADDOPTS`. | Q8, Q36, Q39 |
| D5 | Hidden paths are removed by sparse-checkout, not only marked skip-worktree; `cheeseless doctor` reports existing, untracked or re-materialised copies. | Q47 |
| D6 | One verifier socket per session in a private 0700 dir, bound only into that session's agent sandbox; `SO_PEERCRED` pid must descend from it. HMAC key and results live in the trusted store; `status` and promote re-check task, tree and baseline. | Q43 |
| D7 | Task identity is the freeze digest, assigned by the operator at freeze. Budget is reserved atomically (locked counter in the trusted store) before the first hidden input. | Q38 |
| D8 | CI: PR code runs only in a job with no secrets and a read-only token; seed and HMAC key are used in a separate job that never checks out or runs PR code; no `pull_request_target` with PR checkout. Exact layout with Q55. | Q21, Q36 |
| D9 | DLP unpacks archives recursively up to a size/depth cap; over-cap, encrypted or uninspectable content is blocked. | Q48 |
| D10 | Pre-hidden attempts are free of run budget (Q52) but have their own rate and cgroup limits; over the limit, `RATE_LIMITED`. | Q52 |

## 2026-11-13 — Phase 0 implementation

| # | Decision | Revises |
|---|----------|---------|
| P1 | Adapter names map the plan's flat list onto the hexagon: inbound `cli`, `uds`, `mcp`; outbound `git`, `sandbox`, `runner`, `store`, `egress`, `ci`. All nine leaves are mutually independent (import-linter `independence`). Layers: `bootstrap` > `adapters` > `app` > `ports` > `domain`. | Q25 |
| P2 | Generated proto lives in `cheeseless.adapters.inbound.proto` (`src/cheeseless/adapters/inbound/proto/`). protoc's absolute `cheeseless.v1` imports would collide with our own package, so `scripts/gen_proto.py` rewrites them to relative imports. Domain, ports and app may not import `cheeseless.adapters` or `google` (forbidden contract); domain also may not import `os`, `subprocess`, `socket`, `pathlib`, `shutil`, `sqlite3`, `http`, `urllib`, `tempfile`. Egress (outbound) needs the `Error` shape for proxy 403s; its mapping is a phase 8 decision and must not import the inbound package. | Q13, Q25 |
| P3 | Generated files are committed (reviewable contract, no codegen needed to import). CI runs `scripts/gen_proto.py --check` and fails on drift; output depends on the locked `grpcio-tools`. Generated code is excluded from ruff, mypy and coverage. | Q13 |
| P4 | No interpreter pin: `grpcio-tools` ships a binary wheel for CPython 3.14. `requires-python >=3.11`. Gencode is protobuf 7.35.1, so runtime is `protobuf>=7.35.1,<8`. | — |
| P5 | Enum values carry the type prefix (`STATUS_PASS`, `ERROR_CODE_DLP_BLOCKED`) because proto enum values share the package namespace; public names in docs strip `ERROR_CODE_`. Zero values are `*_UNSPECIFIED` (D1). | D1 |
| P6 | Added error codes beyond ADR 0008's list, all from Q51 stages: `IMPORT_FAILED`, `EXECUTABLE_MISSING`, `STARTUP_CRASH`, `SMOKE_TEST_FAILED`; `RATE_LIMITED` is from D10. | Q51 |
| P7 | `ErrorDetails` holds only `criterion_id`, `agent_written_path`, `budget` (typed `BudgetState{optional remaining, optional limit}`, see P14), `host`. The pre-hidden stderr tail is `Error.pre_hidden_stderr_tail` (not in details), documented as empty once any hidden input was read. `Result` has no output, timing or holdout fields and no `Error` (P12); tests walk every message reachable from `Result`. | Q50, Q51, Q38 |
| P8 | `verifier.proto` declares `service Verifier { Run, Status }` as documentation only; each response is a `oneof { Result, Error }`. No gRPC transport is generated. | Q43 |
| P9 | `docs/errors.md` is generated by `scripts/gen_errors_doc.py` (drift-checked in CI) and lists codes only; per-code guidance text is phase 1 work. | ADR 0008 |
| P10 | Toolchain extras: ruff (incl. bandit rules), mypy strict (non-generated code), hatchling build backend. CI pins `actions/checkout` and `astral-sh/setup-uv` to full commit SHAs (v4.4.0, v6.8.0; P15) with uv pinned to 0.13.1. | — |

Phase 0 cross-vendor review (diff reviewed by another vendor's model, 2026-11-13):

| # | Finding | Disposition |
|---|---------|-------------|
| P11 | HIGH: the HMAC comment covered task, tree hash, baseline digest, profile and tool version but not `status` or `criteria`, so a verdict could be flipped under a valid tag. | **Fixed (contract):** the tag covers the canonical encoding of every other `Result` field. The canonical encoding, signing and a tampering test are phase 1 work. Q43 and ADR 0004 list what the result is bound to; covering the verdict too is a strict tightening, not a revision. |
| P12 | MEDIUM: `Result.error` let a free-text `Error` travel next to a verdict and widened the reachable surface of `Result`. | **Fixed:** field 8 removed and reserved (`reserved 8; reserved "error";`). Errors travel only in the `RunResponse`/`StatusResponse` oneof. The oracle-field test now walks every message reachable from `Result`, and a second test pins that set to `Result` and `CriterionResult`. ADR 0008 puts detail in the *response*, which the oneof still carries, so no accepted ADR changes. The canary security test for result disclosure (`tests/security/`) lands with the phase 6 disclosure filter. |
| P13 | MEDIUM: nothing stopped `domain`/`ports`/`app` from importing `cheeseless.bootstrap`. | **Fixed:** `cheeseless.bootstrap` added to the inner-layer forbidden contract. Probed by adding the import to `domain`: both the layers and the forbidden contract break; reverted. |
| P14 | MEDIUM: proto3 implicit presence made an unset budget read as "0 of 0 runs left". | **Fixed:** `BudgetState` fields are `optional`; test asserts presence is tracked. |
| P15 | LOW: CI actions referenced by mutable major tags. | **Fixed:** pinned to commit SHAs, kept on the current majors to avoid input changes; upgrading majors is a separate change. |
| P16 | MEDIUM: inbound requests are not validated (UNSPECIFIED or unknown enum values, empty oneof, missing `task_id`). | **Deferred:** no inbound adapter exists yet. Domain constructors reject invalid values in phase 1; the UDS/MCP mapping rejects them in phase 6, each with a failing test first. Fail closed (Q43) applies. |

## 2026-11-13 — ADR re-review

Second cross-vendor review of ADRs 0002–0008 by `gpt-6.1-terra` (Codex). F1, F7, F9, F10,
F11 and F12 were confirmed closed; the rows below were partly closed or new. All ADRs stay
`proposed` until a further review finds no CRITICAL or HIGH issue.

| # | ADR | Severity | Finding | Disposition |
|---|-----|----------|---------|-------------|
| G1 | 0003 | HIGH | F2 partly closed: no enforced ptrace and `/proc` boundary between the same-UID runner (with hidden inputs) and the candidate. | Runner outside the candidate's PID and mount namespaces, `PR_SET_DUMPABLE=0`, seccomp denies `ptrace`/`process_vm_*`; security test (D12). |
| G2 | 0003/0005/0006 | HIGH | F5 partly closed: how CI runs the secret-seeded holdout against the candidate without exposing seed or key is undefined. | Base-defined judge workflow; PR head fetched as data; build and tests only in the candidate sandbox (D15, revises D8). |
| G3 | 0002 | HIGH | F8 partly closed: stray check must be fail closed before projection or agent start. | Session start blocks on any stray (D11). |
| G4 | 0006 | HIGH | Q56/Q17 hard denylist missing; must beat config and approval. | Added to ADR 0006 (Q56); no new row. |
| G5 | 0007 | HIGH | F6 partly closed: archives must be quarantined until recursive inspection succeeds. | Archives buffered whole, forwarded only after a clean scan (D16). |
| G6 | 0004 | MEDIUM | F3 partly closed: descendant-pid check races with PID reuse, reparenting, PID-namespace translation; 0700 not stated. | pidfd + PID-namespace check (D13); 0700 stated (D6). |
| G7 | 0005 | MEDIUM | F4 partly closed: re-freeze or rotation creates a new digest and so a fresh budget. | Budget keyed by task lineage (D14). |
| G8 | 0007 | MEDIUM | Held-back tail must cover the longest pattern and the winnowing window. | Tail = max(longest pattern, w + k − 1) (D16). |
| G9 | 0003/0008 | MEDIUM | "Infra error is FAIL" vs "post-hidden only `CRITERION_FAILED`" conflict. | Internal cause vs agent view split (D17). |
| G10 | 0006 | LOW | D8's `pull_request_target` ban missing from the ADR. | Added to ADR 0006 (D15). |

| # | Derived row | Revises |
|---|-------------|---------|
| D11 | Session start runs the stray check fail closed, before projection and before the agent launches: a materialised hidden path, or any file whose blob hash equals a hidden blob or that contains a sentinel, means no session (`BLOCKED`, host CLI message, never agent-facing). `doctor` runs the same check. Security test with canary. | D5 |
| D12 | **Proposed default.** The runner (host side, trusted venv) starts each candidate in its own bwrap; runner, its tmpfs and hidden inputs are outside the candidate's PID and mount namespaces, so `/proc/<runner>` is not visible. Runner sets `PR_SET_DUMPABLE=0`; candidate seccomp denies `ptrace`, `process_vm_readv`, `process_vm_writev`. The candidate only receives the inputs of the test invoking it. `tests/security/` proves ptrace attach, `/proc/<runner>/mem` and `process_vm_readv` fail and the canary stays absent. A separate host UID for the runner is not promised (residual, ADR 0001). | D4 |
| D13 | Session membership on the verifier UDS: the peer's pidfd comes from `SO_PEERPIDFD` and its PID namespace (`PIDFD_GET_PID_NAMESPACE` ioctl) must equal the session sandbox's; no ancestry walk. Both are hard requirements, probed separately; if either is unavailable the call is refused (fail closed). Socket dir 0700, socket 0600. | D6 |
| D14 | **Proposed default.** Budget is keyed by the task lineage fixed at the first freeze; a re-freeze creates a new freeze digest (results bind to it) but carries the remaining count; a holdout rotation keeps the digest and task id and changes only seed and split (P8-6; amended 2026-11-14, R3-2). More runs only via the existing operator grant (Q54, host CLI, logged). | D7 |
| D15 | **Proposed default.** The CI verdict comes from a judge workflow defined on the base branch (`workflow_run`), never from a workflow the PR can edit (`pull_request` runs the PR's own workflow file). The judge fetches the PR head SHA named in the GitHub event as git data (no checkout hooks), binds the result to its tree hash, takes `acceptance/` and the runner closure from the base branch and fails on protected-path changes. Dependencies are fetched outside the sandbox as wheels only; build and tests run only in the candidate sandbox with no network; seed and HMAC key stay in the runner process and never enter the sandbox env. Logs and check summaries never show per-criterion holdout results. PR-defined jobs (lint, visible tests) get no secrets and a read-only token and are advisory. No `pull_request_target` with a PR checkout. A prebuilt artifact from a PR-defined job was rejected: it is not bound to the PR tree, so a passing artifact could vouch for a different tree. Where the judge keeps the CI budget counter is a phase 10 decision; caches writable by PR workflows do not count. | D8 |
| D16 | Archives are detected by magic bytes, not `Content-Type`, buffered whole up to the D9 cap and forwarded only after a clean recursive scan; a hit or scan failure blocks. Non-archive streams hold back max(longest literal pattern including encoded forms, w + k − 1), because winnowing only guarantees detection of matches of at least w + k − 1 that may straddle a chunk boundary. | D9, R2 |
| D17 | Internal status and cause are separate from the agent view. After the first hidden input an infra failure is stored as `INFRA_ERROR` for the operator, charged (D2), and the agent sees FAIL of the unfinished criteria with `CRITERION_FAILED`; `INFRA_ERROR` is agent-visible only before hidden input. Never PASS. | D2 |

## 2026-11-13 — ADR re-review, round 2

Reviewer: gpt-6.1-terra (Codex), read-only. Verdicts: 0002, 0004, 0008 **accept**
(now `accepted`); 0003, 0005, 0006, 0007 **needs change** (stay `proposed`). G1, G3–G10
closed. Round limit reached: the open items below need an operator decision and a third
review before 0003/0005/0006/0007 can be accepted. ADR text is unchanged by this round.

| # | ADR | Sev. | Finding | Disposition |
|---|-----|------|---------|-------------|
| H1 | 0003/0006 | HIGH | G2 still open: D15 says "tests run only in the candidate sandbox" while hidden inputs stay outside it; the execution boundary is ambiguous. | **Open, proposed fix:** the runner holds the hidden tests outside the sandbox and drives the candidate black-box across the boundary; only the candidate build and candidate processes run inside. Wording fix in 0003/0006. |
| H2 | 0006 | HIGH | `workflow_run` always uses the workflow file on the **default** branch, not the PR base. The judge must identify the PR and tested SHA, publish a required check on that SHA and reject stale or missing associations; the triggering workflow is PR-controlled and may be removed. | **Open, needs operator decision. Proposed:** D15's "base branch" is corrected to "default branch"; the judge refuses PRs whose base is not the default branch; it resolves the PR from the head SHA via the API (`workflow_run.pull_requests` is empty for fork PRs, to verify), re-checks the PR head before publishing, and publishes a check run on that SHA. The check is required with a pinned source app, so a PR job's token cannot post a same-named check (to verify). A missing or deleted trigger means no check, so merge stays blocked (fail closed). |
| H3 | 0005 | HIGH | The budget shared atomically between local sessions and CI needs a trusted CI counter and sync; D15 defers it, and separate ephemeral stores cannot enforce the stated bound. | **Open, needs operator decision.** Options: (a) separate pools, local 30 in the host store and CI 5 in a trusted CI counter, total bound 35, so drop "shared"; (b) one authority reachable from both. Leaning (a): the defaults are already separate numbers. The CI counter location (e.g. a ref only the judge app may update) is still to be chosen. |
| H4 | 0007 | HIGH | Removing fingerprints shared with public k-grams after winnowing breaks the winnowing guarantee: a window can lose its selected fingerprint, so hold-back alone cannot guarantee detection. | **Open, proposed fix:** exclude public k-grams before window selection (their hashes treated as +∞), or skip winnowing on the protected side and match every k-gram against the full k-gram hash set of hidden content, holding back max(longest pattern, k − 1). Otherwise declare the gap a residual risk. |
| M1 | 0004 (ledger) | MEDIUM | Linux 6.5 support for `SO_PEERPIDFD` does not establish `PIDFD_GET_PID_NAMESPACE`. | **Fixed in D13:** both are separate hard requirements, refused if either is missing; the unverified kernel-version claim was dropped. ADR 0004 text already says "refused otherwise" and was not changed. |
| M2 | 0005 | MEDIUM | Line 22 calls task identity the freeze digest, while D14 keys the budget to a lineage that survives digest changes. | **Open, wording fix with 0005:** separate freeze identity (digest, results bind to it) from budget identity (lineage). |


## 2026-11-14 — ADR re-review, round 3

ADR wording brought in line with the implemented defaults (operator accepted H1–H4 and M2 on
the proposed fixes; see MVP implementation I3–I7, I15, P10-22). Author: claude-opus-5-5.

| # | ADR | Change |
|---|-----|--------|
| H1 | 0003, 0006 | Execution boundary stated (I3): hidden tests stay in the runner outside every sandbox; only the candidate build and processes run in the candidate sandbox, driven black-box. |
| H2 | 0006 | "Base" corrected to "default" branch; judge refuses a non-default base, resolves the PR from the head SHA, re-checks it before posting; no trigger means no check (I6). Judge App and environment (P10-22). |
| H3 | 0005, 0006 | Separate pools (option a, I4): local 30 in the host store, CI 5 in a trusted CI counter, at most 35 per lineage; missing or inconsistent counter blocks the merge. |
| H4 | 0007 | No winnowing on the protected side: every stream k-gram (k = 24) is matched against all hidden k-gram digests minus public ones; hold-back max(longest pattern, k − 1) (I7). |
| M2 | 0005 | Freeze identity (digest, results bind to it) separated from budget identity (lineage, D14, I5). |
| — | 0001, 0003, 0005, 0007 | Residuals: same-UID sandbox escape; x86_64 only (I2); seccomp adds `pidfd_getfd` (I15); per-class sentinel minimum (P8-12); split/encoded markers and HTTP upgrades/QUIC (P8-12, P8-13). |

Reviewer: gpt-6.1-sol (Codex, read-only). Verdicts: 0001, 0003, 0007 accept; 0005, 0006 needed change.

| # | Sev | ADR | Finding | Disposition |
|---|---|---|---|---|
| R3-1 | HIGH | 0005, 0006 | The 35-run bound and "missing counter blocks merge" are not enforced yet: `bootstrap_ci.py` builds a fresh store per run (P10-6 pending). | **Fixed.** Both ADRs state the bound holds only with the trusted CI counter and that release requires it (P10-6, P10-25/26). Implementation is a release blocker. |
| R3-2 | MEDIUM | 0005 | "rotation gets a new digest" contradicts `app/holdout.py` and P8-6. | **Fixed.** 0005 separates re-freeze (new digest) from holdout rotation (same digest, task id and lineage; only seed and split change). D14 amended; I5 unchanged (it only carries the lineage). |
| R3-3 | LOW | 0005 | Sentinel minimum should apply per class that has hidden files and name the exposure checks. | **Fixed.** Names raw, reversed, hex and base64; other encodings point to ADR 0007. |
| R3-4 | LOW | 0007 | 64-bit rolling-hash collisions are an unlisted residual. | **Fixed.** Listed: a collision can block clean text or drop a hidden k-gram as public. |

Re-check of 0005/0006 by gpt-6-luna (Codex, read-only): 0006 accept; R3-1…R3-3 confirmed fixed in 0005, two new LOW findings, both fixed inline. All five ADRs are now `accepted`.

| # | Sev | ADR | Finding | Disposition |
|---|---|---|---|---|
| R3-5 | LOW | 0005 | "Rotates after 3 failures or on a DLP hit" merged two different mechanisms: Q54 is an operator re-freeze (new split, seed and digest), P8-6 rotates in place. | **Fixed.** The bullet now names the operator re-freeze for Q54 and the in-place rotation for a DLP hit. |
| R3-6 | LOW | 0005 | "Seed drawn after approval" did not say which approval. | **Fixed.** It now says the seed is drawn after the user approves the freeze (Q9, Q44). |

## 2026-11-13 — MVP implementation

The operator asked for continuous implementation until the MVP works and accepted that the
open items H1–H4 and M2 proceed on their proposed defaults (recorded here, not in ADR text;
the wording fixes to ADRs 0003/0005/0006/0007 go to one cross-model review round later).

| # | Decision | Revises |
|---|----------|---------|
| I1 | **Probe, this host (kernel 6.18, x86_64, Python 3.12 venv).** `getsockopt(SOL_SOCKET, SO_PEERPIDFD=77)` returns a pidfd; `ioctl(pidfd, PIDFD_GET_PID_NAMESPACE = _IO(0xFF, 5))` returns an nsfd whose inode equals `/proc/<pid>/ns/pid`, also across a bwrap PID namespace. Python 3.12 has no `socket.SO_PEERPIDFD`, so the adapter uses the raw constants from the kernel headers (`asm-generic/socket.h`, `linux/pidfd.h`). Both remain separate fail-closed probes (D13). | D13 |
| I2 | **Seccomp without a dependency.** No Python binding is installed; the candidate filter is a hand-packed classic BPF program (arch check `AUDIT_ARCH_X86_64`, reject the x32 ABI bit, `EPERM` for `ptrace`, `process_vm_readv`, `process_vm_writev`, allow the rest), passed to `bwrap --seccomp FD`. Other architectures are refused (fail closed) until they get their own syscall table. | D12 |
| I3 | **H1 default.** The runner (host side) holds the hidden tests outside every sandbox and drives the candidate black-box across the boundary: it starts the candidate's build and each candidate process in the candidate bwrap, feeds inputs over stdin/argv/files it chooses, and compares outputs itself. No hidden test file enters the candidate sandbox. | D12, D15 |
| I4 | **H3 default (a).** Separate pools: local 30 in the host trusted store, CI 5 in a CI counter; the total bound per lineage is 35 and "shared" is dropped. Until the CI counter location is chosen (phase 10), the CI adapter keeps its counter behind the same store port. | D7, D14 |
| I5 | **M2 default.** Freeze identity = freeze digest (results bind to it, `Result.task_id`); budget identity = task lineage id, fixed at first freeze and carried across re-freeze and rotation. | D7, D14 |
| I6 | **H2 default.** The judge runs from the default branch, refuses PRs whose base is not the default branch, resolves the PR from the head SHA, re-checks the head before publishing and posts a check run on that SHA. Missing trigger means no check, merge stays blocked. Items marked "to verify" stay open for phase 10. | D15 |
| I7 | **H4 default.** The protected side skips winnowing: every k-gram hash of hidden content is kept (public k-grams removed from that set), and egress matches every k-gram of the stream against it, holding back max(longest pattern, k − 1). Winnowing stays only as a size optimisation where no public k-gram was removed. | D16 |
| I8 | **Canonical Result encoding v1.** The HMAC input is the prefix `cheeseless.v1.Result\0`, then the encoding version as u32 BE (1). Then come the u32-BE-length-prefixed UTF-8 fields: task_id, tree_hash, baseline_digest, profile, tool_version and status (its lowercase value). Last are the criteria count (u32 BE) and each criterion's id and status, in order. The key must be at least 32 bytes. Changing any part of this invalidates every stored tag, so a change needs a new version number. | Q43, P11 |
| I9 | **Agent view.** `Verdict.agent_view(task)` returns `AgentView{outcome, failures}`. The inbound adapters serialise only this; `operator_cause` and `charged` stay internal. The canary test is in `tests/security/test_disclosure_canary.py`. | Q51, D17 |
| I10 | **Wire carries the agent view.** `RunResponse` and `StatusResponse` gain `repeated Error criterion_failures = 3` outside the oneof (additive, field numbers unchanged; ADR 0004 text untouched). Inbound mapping accepts it only with a `Result` outcome, and each entry must be `CRITERION_FAILED` with a `criterion_id` naming a FAIL criterion. | I9, ADR 0004 |
| I11 | **Phase 1 review (codex gpt-6.1-terra).** HIGH: a post-hidden `infra_cause` with all criteria PASS gave PASS. **Fixed:** every criterion becomes FAIL (Q43, D17); regression test in `tests/unit/domain/test_disclosure.py`. HIGH: `GateError` could be built directly with free text. **Fixed:** `__post_init__` enforces the catalogue reason (or the criterion-description form), hint, retryable, docs anchor and the stderr-tail rules; security test `test_direct_construction_cannot_smuggle_text`. MEDIUM: the wire could not carry `AgentView.failures`. **Fixed** by I10. | Q43, Q51, D17 |
| I12 | **Trusted store layout.** `FileStore` lives in `$XDG_STATE_HOME/cheeseless/<sha256(realpath(git common dir))[:16]>/`: `hmac.key` (32 random bytes, created once with `O_EXCL`), `tasks/<freeze digest>.json`, `active`, `budget.json` (`{lineage:{pool:{limit,used}}}`), `attempts.json`, `results/<id>.jsonl` (history with operator cause) plus `results/<id>.json` (latest), `events.jsonl`. Directories 0700 and owned by the euid, files 0600, every open uses `O_NOFOLLOW`, every write is tmp+fsync+rename, budget and attempt updates hold one `flock`. A task file is re-validated on load (freeze digest must equal the id). The MVP uses only the standard library for JSON-RPC (MCP over stdio) and the UDS server instead of the `mcp` and `httpx` packages, to keep the trusted dependency set at protobuf and PyYAML. | D7, D14, Q38, Q55 |
| I13 | **Hardened git adapter.** Git runs with a minimal env, no system/global config, `core.hooksPath=/dev/null`, no fsmonitor, `--attr-source` = empty tree (no filters or conversions from `.gitattributes`), plumbing only, validated paths. Worktree reads and writes go through `openat` with `O_NOFOLLOW` on every component; symlinks, gitlinks and special files in protected or exported paths are rejected (`SnapshotRejected`). Snapshot total size is capped at 512 MiB. | ADR 0002, D3 |
| I14 | **Candidate overlay (option A).** Each candidate call goes through a trusted launch helper (`python -I -m …sandbox.launch SPEC -- CMD`) that makes a fresh disk-backed overlay upper/work pair under a scratch dir, runs bwrap's `candidate` profile and removes the overlay afterwards (the work dir is left mode 000, so cleanup chmods first). Exit status is the candidate's, 128+N on signal N, 125 when the helper itself fails. Rlimits: no core, 1024 fds, 256 MiB files, 300 s CPU. Unprivileged overlay works on the target kernels (6.x); NPROC and memory limits via cgroups are deferred. | ADR 0003, D12 |
| I15 | **Sandbox hardening details.** `--disable-userns` requires `--unshare-user`, so every profile unshares the user namespace. The seccomp deny list is ptrace, process_vm_readv, process_vm_writev **and pidfd_getfd** (I2 listed three); x32 and foreign arches are killed; non-x86_64 hosts are refused. Hostname is `sandbox`; mount points are random `/w/<hex>`. The in-sandbox `/tmp` is a tmpfs (`SANDBOX_TMP`); the agent profile forces `TMPDIR` and `HOME` after contract env so the agent cannot redirect them. Env entries are checked by the public `contract.check_env_entry`. Security proof: `tests/security/test_sandbox_canary.py`. | ADR 0003, I2 |
| I16 | **Deferred from Phases 3–4.** Q44 freeze-time flake detection (run the hidden suite twice) and sentinel/DLP scanning of stderr tails are deferred to Phase 8; the `EXECUTABLE_MISSING` mapping, an `edit` command and a partial CI judge are out of the MVP. | Q44, D17 |
| I17 | **Trusted runner (Phase 5).** pytest is a runtime dependency (`>=8,<10`), run as `python -I -B -m pytest` from the verifier's venv with cwd = the acceptance root, a private `-c pytest.ini`, pinned `--rootdir`/`--confcutdir`/`--basetemp`, `--import-mode=importlib`, `--strict-markers`, `-p no:cacheprovider`, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, and an env rebuilt from scratch (only PATH, LANG, HOME, TMPDIR and the plugin's `CHEESELESS_RUNNER_*` contract). The candidate workdir is never on pytest's path; candidates reach it only through the `candidate` fixture, which prefixes each call with the sandbox launcher. Process groups: pytest starts in a new session and the candidate launchers stay in that group, so one `killpg` reaches all of them. The runner waits on a pidfd with `poll`; on suite timeout it sends SIGTERM to the group, waits a 2 s grace period, always sends SIGKILL to the group, and only then reaps the leader (a zombie leader keeps the pgid from being reused). Output is capped at 1 MiB. The runner hardens itself with `PR_SET_DUMPABLE=0`. Security proof: `tests/security/test_runner_canary.py` (hostile workdir and canary hunt, plus a positive control that finds a leaked canary). | D4, D12, D17 |

## 2026-11-13 — App use cases (freeze, gate_run, status, promote)

| # | Decision | Revises |
|---|----------|---------|
| I18 | App layer implements freeze `prepare`/`commit`, `gate_run`, `status` and `promote` over the existing ports; no new port methods. Freeze runs only `acceptance/hidden` tests for preconditions; gate_run runs `contract` smoke (pre-hidden) then reserves budget before `hidden` tests. Baseline digest is `sha256(baseline_commit)`. Promote re-checks the evaluated tree hash and applies promote_policy before calling `Repository.promote`. | I1, I5, I9, I10 |

## 2026-11-13 — Parent review fixes and host CLI

| # | Decision |
|---|----------|
| F1 | `freeze.prepare` now filters `contract/` (relative to the acceptance tree) and stores `acceptance/...` repo-relative hidden blob paths; hidden tests materialise under `sandbox.scratch()` instead of the default disk tempdir. |
| F2 | `gate_run` selects `frozen.holdout` for `Pool.CI` and `frozen.visible` otherwise. |
| F3 | Pre-hidden attempts are rate-limited via `record_attempt` before any candidate work; over `PRE_HIDDEN_ATTEMPT_LIMIT` per window returns `RATE_LIMITED` with no build or budget charge. |
| F4 | Post-hidden runner exceptions are sanitized into a charged FAIL with an operator-only cause; pre-hidden exceptions fail closed as `STARTUP_CRASH`. |
| F5 | `status` now returns `Result | None`, enforces HMAC task binding, and no longer loads the unused frozen task. |
| F6 | `commit` requires a seed of exactly 32 bytes, matching `FileStore`. |
| F7 | Host CLI implemented through `bootstrap.main` (freeze/run/status/promote/doctor); `gate_run`/`status`/`promote`/`freeze` Any forwarding wrappers removed. |

## Parent integration review — console entry point

- Console invocation ignored process arguments because bootstrap.main(None) passed an empty argument list to argparse. A failing regression test now covers sys.argv; parse_args(argv) restores standard argparse semantics and the installed console command handles --version.
- Hidden materialization now uses sandbox.scratch(), but BwrapSandbox.scratch still uses tempfile.mkdtemp without a verified tmpfs root. The tmpfs requirement remains open; using the port alone does not establish storage guarantees.

## 2026-11-13 — Phase 6 verifier service

| # | Decision | Revises |
|---|----------|---------|
| P6-1 | Trusted scratch for hidden tests now verifies tmpfs backing (`TMPFS_MAGIC` via libc statfs), mode 0700, fail closed with no disk fallback; cleanup via force_remove. Candidate scratch unchanged. | F1, D12 |
| P6-2 | Verifier UDS server exposes only run/status over HTTP/protojson on a Unix socket; session config (task, worktree, pool, timeout, tool version, ports) is host-controlled. Socket dir 0700, socket 0600. Peer auth uses SO_PEERPIDFD + PIDFD_GET_PID_NAMESPACE ioctl, refusing unsupported kernels, host namespace and wrong namespace; pidfds closed. Bound frame/body length; strict proto mapping rejects UNSPECIFIED/unknown enums, missing IDs and missing oneof. Clean shutdown via shutdown(). | D6, D13, ADR 0004 |
| P6-3 | Serialization exposes AgentView only; MCP adapter returns identical structuredContent and JSON text block. MCP adapter is client to UDS via injected VerifierClient port; stdio entry point and bootstrap serve entry points provided. Official MCP SDK used (mcp>=2,<3); lockfile updated. | ADR 0004, ADR 0008 |
| P6-4 | App exceptions are sanitized on public paths (CLI prints internal error only); pre-hidden exception paths omit stderr instead of exposing unscanned text. Rate limiter records one attempt per request (removed double record_attempt). | ADR 0008, Q51 |
| P6-5 | Remaining gaps: egress DLP proxy (phase 8), CI judge (phase 10), toy repo E2E (phase 11), release hardening (phase 12); peer auth only testable on kernels supporting SO_PEERPIDFD + PIDFD_GET_PID_NAMESPACE (this host supports both). | — |

## 2026-11-13 — Phase 6 isolation closure (TDD)

The earlier phase 6 round-trip tests set the host PID namespace as the expected value,
so they passed while the verifier would also have accepted a host process. Those tests
were replaced with real clients in a fresh PID namespace (`unshare --user --pid
--mount-proc --fork`), and every protection below has a failing-test-first counterpart.

| # | Decision | Revises |
|---|----------|---------|
| P6-6 | **Authentication requires a lifetime-held namespace FD.** `SessionConfig.pinned_inode()` returns `None` unless at least one namespace FD is open; a bare `expected_pidns_inode` is only a consistency check and can never authorise a peer. Missing pin, a closed FD, a bare inode, the host namespace, a wrong namespace, or a kernel without `SO_PEERPIDFD`/`PIDFD_GET_PID_NAMESPACE` all reject. The positive control is a real client in a separate PID namespace. | D6, D13, P6-2 |
| P6-7 | **Usable host session.** `bootstrap.host_session()` creates a projection from the frozen projection tree, runs `stray_hidden` on that projection before any agent starts, and launches bwrap with the existing `agent` profile: only the projection (writable), an ephemeral HOME, and the one verifier socket are mounted; the original repository, `.git` directory and trusted store are not. The sandbox PID namespace is read from bwrap `--info-fd`, opened as a lifetime FD, and held until the server has shut down. The agent starts behind a readiness file written only after the pin is installed, so there is no unauthenticated window and no test-mode bypass. | D5, D6, D11, D12, P6-2 |
| P6-8 | **MCP has no trusted-store path.** `serve_mcp` takes only the verifier socket path, optional task id and timeout; it no longer accepts state/cwd and never discovers a repository or opens `FileStore`. An stdio test runs `cheeseless serve-mcp` from a non-git directory with an unused `XDG_STATE_HOME`, performs initialize, tools/list, gate_run and gate_status, and checks `structuredContent` equals the JSON text block. | ADR 0004, P6-3 |
| P6-9 | **Socket directory contract.** The parent must be absolute, owned, exactly mode 0700 and not a symlink. An existing socket path must be a socket owned by the euid; a live socket is refused, a stale one is replaced, and symlinks/non-sockets are refused. Binding bypasses `UnixStreamServer.server_bind` (which unlinks unconditionally), so no arbitrary path is removed. Cleanup only unlinks the socket and removes the directory when this server created/owns them; caller-owned directories stay. | D6, D13, P6-2 |
| P6-10 | **Wire limits and serialisation.** Total header size (request line included), per-line size, body size and header+body size are bounded; `Transfer-Encoding`, duplicate headers, bad `Content-Length` and truncated bodies are refused; sockets have a read timeout (408) and a bounded worker semaphore; a second concurrent `run`/`status` returns 429 instead of queueing past the client timeout. The client requires HTTP/1.1, exactly one `Content-Length`, an exact body length and a bounded total response. | P6-2, P6-3 |
| P6-11 | **Status metadata consistency.** The status path builds criterion failures through `Verdict.agent_view`, the same domain code as `run`, so public criterion descriptions cannot drift between the two wire paths. Import hacks and the dead status branch were removed. | P6-3, I9 |
| P6-12 | **Verified command results (this host, kernel 6.18, Python 3.12, 2026-11-13).** `uv run --frozen pytest` → 408 passed, 86.34% coverage; `ruff check .`, `mypy`, `lint-imports` (4 contracts kept), `scripts/gen_proto.py --check`, `scripts/gen_errors_doc.py --check`, and the installed `cheeseless --version` all pass. | P6-6…P6-11 |
| P6-13 | **Remaining gaps.** Egress DLP (phase 8) is still absent, including a DLP scan of pre-hidden stderr tails; the CI judge (phase 10), broad toy-repo E2E across real agent CLIs (phase 11) and release hardening (phase 12) remain. Authenticated MCP-over-UDS is covered end to end by spawning the MCP stdio server in a fresh PID namespace (`unshare`) and running a real `gate_run` through the pinned UDS; the same chain is not re-run inside bwrap's agent profile. The `serve-uds` CLI exposes `--pidns-fd` for an inherited namespace FD and fails closed without one; embedding code can use `serve_uds(expected_pidns_fd=...)` or `host_session`. Peer auth depends on Linux `SO_PEERPIDFD` plus `PIDFD_GET_PID_NAMESPACE`. | P6-5 |

## 2026-11-13 — Phase 8 egress DLP proxy

| # | Decision | Revises |
|---|----------|---------|
| P8-1 | **Pinned stack change.** Python is now `>=3.12` because every mitmproxy 12.x release requires it; `mitmproxy>=12.3.1,<13` is a runtime dependency. `pyproject.toml`, `uv.lock`, ruff `target-version` and this ledger move together. No ADR decision changes. | P0 stack, ADR 0007 |
| P8-2 | **Digest-only fingerprints (I7).** Freeze stores, per task, every k-gram hash (k=24, 64-bit Rabin-Karp) of every hidden artifact **minus** the k-gram hashes of the agent-visible projection; no winnowing is used on the protected side, so no selection can drop a removable hash. It also stores sentinel markers harvested from hidden artifacts as truncated per-length digests for the raw, base64, hex and reversed forms with an artifact class (tests/fixtures/expected/seed/notes). No hidden plaintext or plaintext sentinel is written to the trusted store or the proxy; the proxy receives `DlpScanner` only. | Q48, Q49, D16, I7, P8-3 |
| P8-3 | **Fingerprints are part of freeze.** `prepare` now runs the hidden suite twice at freeze time and fails closed when the two observations differ (I16/Q44 flake detection), then computes the fingerprint set; `commit` saves it next to the task in `fingerprints/<task-id>.json`. A task without fingerprints makes `host_session` refuse to start. | Q44, I16, P8-2 |
| P8-4 | **Scanner semantics.** Literal forms are matched exactly by per-length digest; all k-grams are matched with the rolling hash. A streaming body holds back `max(longest literal form, k-1)` bytes, scans the tail at EOF, and forwards nothing after a hit. Archives are detected by magic (zip/gzip/bzip2/xz/tar), buffered whole up to 16 MiB input, recursively inspected in memory (depth 4, 256 files, 8 MiB/member, 32 MiB expanded), and released only after a clean scan; encrypted, unsafe-path, symlink, over-cap or uninspectable members block. No member is extracted to disk. SSE and arbitrary split points are covered by tests. | Q46, Q48, D9, D16, I7 |
| P8-5 | **Policy.** Exact normalised host allowlist (lower-case, trailing dot removed, IDNA, no wildcard/suffix match); registry hosts are GET-only; the repo lineage name and repository directory name are automatically denied as markers (in addition to caller markers); model-host bodies must parse, `mcp_servers`/server-tool keys are denied, and every tool schema with an unknown type or a web/code/computer/mcp marker is denied. `Accept-Encoding` is forced to identity; non-identity response encodings and protocol upgrades are blocked with a catalogue `DLP_BLOCKED` error. Proxy-generated public errors are `cheeseless.v1.Error` JSON plus `X-Cheeseless-Code`. | Q48, Q50, ADR 0007, ADR 0008 |
| P8-6 | **Supervisor and BLOCKED.** A hit from the stream (or an encoded/upgrade response) calls a host-only supervisor at most once: it persists a signed `Result{status: BLOCKED, criteria: ()}`, rotates the holdout with a new seed **without changing the task id, acceptance tree or lineage budget**, appends an operator event, tears down the whole bwrap process group, and alerts with host, rule and fingerprint id only. The proxy cannot retract bytes already forwarded; hold-back guarantees the matching bytes themselves were never sent. | Q49, Q54, D14, R2 |
| P8-7 | **Session wrapper.** `host_session` stages the cheese client runtime (site-packages plus the `cheeseless` package and a `cheeseless-mcp` launcher) outside the repository, mounts it read-only, and mounts only the projection, ephemeral HOME, verifier socket, egress UDS and CA certificate. mitmproxy listens on host loopback; a host UDS->TCP bridge and an in-sandbox loopback TCP->UDS bridge carry traffic. The sandbox keeps the empty netns and never receives the CA key. `CODEX_CA_CERTIFICATE`, `NODE_EXTRA_CA_CERTS` and `SSL_CERT_FILE` point at the mounted cert; `HTTPS_PROXY`/`HTTP_PROXY`/`ALL_PROXY` point at the bridge with `NO_PROXY=""`. Startup reading of bwrap `--info-fd` is deadline-bounded with teardown on timeout/bad info, and `launch_host_session` waits for the agent instead of sleeping forever. | ADR 0007, R1, R3, P6-7 |
| P8-8 | **Pre-hidden stderr DLP.** `gate_run` scans the build/smoke stderr tail with the task scanner before attaching it to a pre-hidden error. A missing, corrupt or failing scanner omits the tail; no raw exception text replaces it. This preserves Q51's free pre-hidden diagnostics when they are safe and fails closed otherwise. | Q51, I16, P8-3 |
| P8-9 | **Residual limits.** Sentinels are only literal markers the author planted in hidden artifacts; per-artifact-class sentinel *minimums* are not yet a freeze precondition. The 64-bit rolling hash has a theoretical collision, not a practical one. Bytes already forwarded before a mid-stream hit may reach the client (ADR 0007 accepts this). Paraphrases and transformed copies below the k-gram/literal length pass. Archive caps are hard blocks, not partial inspection. HTTP upgrade/WebSocket, QUIC/UDP and non-identity encodings are blocked rather than inspected. The operator alert sink is stderr plus the trusted-store event log; no external pager integration ships. Tests use local TLS fixtures only; no real provider credentials or model calls are exercised. | ADR 0001, ADR 0007, P8-4…P8-8 |
| P8-10 | **Outstanding work.** Phase 10 CI judge (shared budget, secret-free PR job), phase 11 toy-repo E2E across real Codex/Claude sessions with overhead measurements, and phase 12 sanitizer/publication remain. CI does not yet run the real-bwrap egress bridge tests if bubblewrap is absent; they skip. A freeze-time check for at least one marker per artifact class, a durable operator alert transport, and a full parity pass between the two bridge directions are follow-ups, not claimed guarantees. | implementation plan phases 10–12 |
| P8-11 | **Verified on this host (2026-11-14).** `uv run --frozen pytest` → 463 passed, 83.76% coverage on the shared tree at the final run; `ruff check .` clean; `mypy` clean; `lint-imports` 4 contracts kept; `scripts/gen_proto.py --check` and `scripts/gen_errors_doc.py --check` clean; installed `cheeseless --version` prints `cheeseless 0.0.0`. The egress suite includes a real local TLS upstream and mitmproxy over the host UDS bridge, cross-chunk/SSE and archive canaries with clean negative controls, and real bwrap runs proving direct TCP/UDP is unreachable while clean proxy traffic flows and a canary triggers teardown, BLOCKED persistence and holdout rotation. | P8-2…P8-9 |
| P8-12 | **P8-9 triage (2026-11-14).** *Closed:* freeze now requires at least two distinct sentinel tokens per artifact class that has hidden files (`MIN_SENTINELS_PER_CLASS = 2`), and refuses freeze when any sentinel token also appears in agent-visible content. Errors name the class or a digest only, never the marker (`tests/security/test_freeze_sentinels.py`). *Accepted as residuals for ADR 0007:* 64-bit rolling-hash collisions; bytes forwarded before a mid-stream hit (the matching bytes themselves are held back); paraphrases and transforms below k or the literal length; archive caps that block rather than partially inspect; HTTP upgrades, QUIC and non-identity encodings that are blocked rather than inspected; an alert sink of stderr plus the trusted-store event log. The public repo ships no pager integration, and operators wire their own from the event log. A full parity pass between the two bridge directions stays a P8-10 follow-up. | P8-9, P8-10, ADR 0007 |
| P8-13 | **Sentinel minimum review (2026-11-14).** Review 053d52cb (gpt-6.1-sol high) rejected the first P8-12 cut. #1 HIGH (encoded public copies counted as private): *accepted*; a token is exposed if any public content or path holds it raw, reversed or as hex (case-insensitive) or as base64 at alignments 0–2 (case-sensitive core). #2 HIGH (freeze diagnostics echo markers): *accepted, narrowed*; `FreezeError` redacts every marker to `<sentinel digest …>`, covering node ids and the build stderr tail, with a real-CLI test. Node ids themselves stay visible because freeze is a host-only author/operator CLI. #3 MEDIUM (tests miss CANARY, classes, surfaces): *accepted*; the security tests are parametrised over family × surface × form and all five classes. Re-review 62ca7923 (gpt-6.1-sol high) rejected again. HIGH (line-wrapped base64 bypass): *accepted*; base64, hex and raw are also matched after whitespace removal. MEDIUM (case-variant markers survive redaction): *accepted*; redaction is case-insensitive. LOW (base64 near-miss refused): *accepted as documented fail-closed behaviour*, with a regression test. LOW (non-`FreezeError` exceptions unredacted): *rejected for now*; freeze is host-only and tracebacks are needed for debugging. This is recorded as a host-hardening residual. Note: `artifact_class` now uses whole path words, so new freezes may label some fingerprints differently (`data/expected.json` → expected). Stored tasks keep their labels, and labels only appear in alerts. Residuals (ADR 0007): other reversible encodings and markers split across files or string literals. | P8-12, ADR 0007 |

## 2026-11-13 — Phase 10 CI judge (spec; implemented 2026-11-13, see P10-9…P10-14)

| # | Decision | Revises |
|---|----------|---------|
| P10-1 | **Trust split.** The judge workflow runs from the default branch (I6). The cheeseless install, `acceptance/` and the runner closure come from the default-branch checkout. The PR head is fetched as git objects only (`git fetch` of the head SHA, no checkout) and exported with the hardened `Repository.export`, so PR hooks, filters and `.gitattributes` never run. | D15, I6 |
| P10-2 | **Protected paths fail before any run.** If the diff from the default-branch commit to the PR head touches any `promote_policy` deny prefix (`acceptance/`, `.cheeseless/`, `.github/workflows/`), the verdict is FAIL with no candidate run and no budget charge. The list is not duplicated: the judge reuses the domain deny prefixes. | Q17, Q56 |
| P10-3 | **Freeze-drift check.** The judge compares the default branch's `acceptance/` tree with the frozen task's acceptance tree. Any difference fails closed (exit 3, `FREEZE_DRIFT`), because tests edited on main after the freeze have not gone through the freeze preconditions. | Q21, D14 |
| P10-4 | **CI bundle.** `cheeseless ci-export --out PATH` (host only) writes `{version, task record, lineage, hmac key, ci limit}` as JSON. It refuses a path inside the worktree or the git common dir, creates the file with `O_EXCL`, mode 0600, and never prints the bundle. The operator stores it as one repository secret, and the judge reads it from an env var (`CHEESELESS_CI_BUNDLE`, base64) inside the judge process only. | Q55, D8 |
| P10-5 | **Run and output.** The full suite (visible plus holdout) runs only in the candidate sandbox (I3). The signed Result binds to the PR head tree hash. Stdout, JSON and the check-run text show the aggregate status and visible-criterion statuses only. Holdout test ids, holdout criterion results and stderr never appear. | Q38, D15 |
| P10-6 | **CI budget is not enforced in the MVP.** A GitHub runner has an ephemeral store, so a local counter bounds nothing. The judge still reserves from the `ci` pool (limit from the bundle) through the store port, so a persistent adapter can replace it, but there is no cross-run bound until a trusted counter exists (candidate location: a ref only the judge may update, with compare-and-swap). Residual risk until then: a PR author can re-run the judge to probe the holdout. Closing this is required before release. | I4, H3 |
| P10-7 | **Workflow template, not repo CI.** The `workflow_run` judge YAML ships as `docs/ci/judge.yml` for adopting repos. This repo has no frozen task, so the file is not in its own `.github/workflows/`. Resolving the PR from the head SHA, refusing a non-default base, re-checking the head before publishing and posting the check run happen in the template through the GitHub API. To verify on real GitHub in phase 11: whether `pull_requests` is empty for fork PRs, check-run permissions, and the re-check race. | I6 |
| P10-8 | **This repo's CI runs the isolation tests.** The CI installs `bubblewrap`, relaxes the ubuntu-24.04 AppArmor unprivileged-userns restriction, and sets `CHEESELESS_REQUIRE_BWRAP=1`, which turns the bwrap and egress skips into failures. **Verified 2026-11-14** on GitHub Actions run 43530056017 (`a0b9fe1`): 681 passed, 1 skipped, 0 failed with `CHEESELESS_REQUIRE_BWRAP=1`, so no bwrap or egress test skipped. The one skip is the nested-device test, which needs `mknod` privileges. | AC 2, 15, 16 |
| P10-9 | **Implemented (2026-11-13).** `app/ci_judge.py`, `ci_bundle.py`, `bootstrap_ci.py`, CLI `ci-export` / `ci-judge`, `docs/ci/judge.yml`, `docs/ci/README.md`, this repo's `ci.yml` (P10-8). Exit codes: 0 PASS, 1 FAIL/BLOCKED, 2 usage/infra, 3 FREEZE_DRIFT. FREEZE_DRIFT is CLI-level only; no new proto error code. | P10-1…P10-8 |
| P10-10 | **Bundle codec lives in top-level `cheeseless/ci_bundle.py`, not `adapters/outbound/ci`.** It reuses the store codec, and an adapter importing another adapter would break the independence contract. `bootstrap_ci.py` is likewise top-level and imports the adapters. No contract was weakened. | P10-4 |
| P10-11 | **Bundle integrity tag.** The bundle carries an HMAC keyed by the bundle's own key, plus the task freeze digest re-check. This detects corruption and partial edits, not an attacker who replaces key and tag together; the secret itself is the trust anchor. Strict decode: size cap, exact field set, version, key ≥ 32 bytes, ci_limit range, lineage match; any failure exits 2. | P10-4 |
| P10-12 | **Public form is `{status, criteria[visible]}` only.** The suite also reports holdout tests, so per-criterion statuses are computed from the visible tests directly. The closed reason goes to stderr, never stdout or JSON. The candidate export excludes `acceptance/`. | P10-5 |
| P10-13 | **CI limit below the default is moot.** The store port cannot lower a limit and each run uses an ephemeral store charging one run, consistent with P10-6. The bundle still records the limit. | P10-6 |
| P10-14 | **Process deviation.** `ci_judge.py` was written before its unit tests; the bundle codec, CLI and security tests followed RED/GREEN. Not verified: the workflow template on real GitHub (P10-7), the CI apparmor/bwrap step (P10-8), fork-PR behaviour. Verified on this host: 497 passed, 85.65% coverage; ruff, mypy, lint-imports (4 kept), gen_proto and gen_errors_doc checks clean. | — |
| P10-15 | **Cross-vendor review of phase 10 (reviewer: gpt-6.1-terra).** Four findings, handled in P10-16…P10-19. | — |
| P10-16 | **Fixed (HIGH): the check conclusion is fail-closed.** `judge.yml` mapped any exit code other than 0/1/3 (usage/infra error 2, a crash) to `neutral`, which branch protection may treat as passing. Now `success` requires exit 0 **and** `verdict.json` status `pass`; everything else is `failure`. `tests/unit/test_judge_workflow.py` runs the extracted shell snippet over the exit code × verdict matrix. | P10-7 |
| P10-17 | **Fixed (MEDIUM): the protected-path diff starts at the merge base.** Diffing the moving default-branch tip against the PR head blamed the PR for protected changes merged to main after it forked. A PR that makes the same change as main would also hide its own touch. The judge now diffs `merge-base(base, head)` → head (new `Repository.merge_base` port method). Freeze drift still checks the default-branch tip. Unrelated histories raise `RepositoryError` → exit 2 → `failure`. The trusted scratch and acceptance export now also fail closed (BLOCKED `infra_error`) and no longer escape as exceptions. | P10-2 |
| P10-18 | **Fixed (MEDIUM): the CI canary test now has a live probe.** The old test asserted that the needle was absent, but no test ever emitted it. A dedicated world now freezes hidden tests that print the needle and carry it as their assertion message. A recording runner proves the needle reached pytest output, and the test asserts it is absent from the judge's stdout and stderr. | P10-12 |
| P10-19 | **Rejected (LOW): wiring `bundle.ci_limit` into the ephemeral store.** Each `ci-judge` invocation uses a fresh store and charges exactly one hidden run, and the codec enforces `ci_limit ≥ 1`. The limit can therefore never bind, and wiring it in would suggest an enforcement that does not exist. Cross-run CI budget stays the residual from P10-6, required before release. | P10-13 |
| P10-20 | **Rejected (MEDIUM, re-check by gpt-6.1-terra): inspecting every PR commit and every merge base.** The judge exports, runs and binds its verdict to the head *tree*. A protected change that is reverted inside the PR cannot affect what is judged or merged. Intermediate commits never run under the judge, because `workflow_run` comes from the default branch. Every merge base is an ancestor of the default branch, so its contents are already accepted. Requiring a clean diff from *all* bases (`merge-base --all`) would flag protected changes that are already on main as the PR's. Pinned by `test_protected_touch_reverted_within_the_pr_is_judged_on_the_net_tree`. In a criss-cross history git picks one merge base; a different choice can only add protected paths that are already on main to the diff, so the worst case is a false-positive FAIL (fail closed), never a missed PR touch. Adopters who want history-level protection should require squash merges. | P10-17 |
| P10-21 | **Security tests for the merge-base protected diff, and the gates re-verified.** `tests/security/test_ci_judge_canary.py` drives `ci-judge` end to end with the sandbox and runner replaced by `Boom`. Case one: an acceptance touch still fails after main moves on. Case two: a workflow change identical to one on main still fails. Both check that the needle does not leak. A mutation back to the tip diff makes case two fail. `docs/ci/README.md` now states the net-tree policy. Gates on 2026-11-13: 514 passed, 85.69% coverage; ruff, mypy (70 files), lint-imports (4 kept), gen_proto and gen_errors_doc checks clean. Not verified: `judge.yml` on real GitHub. | P10-17, P10-20 |
| P10-22 | **Fixed (CRITICAL/HIGH, review a2a98b11 #2 and #7): judge secrets move to an environment and the verdict is posted by a judge App.** A repository secret is readable by any workflow a pusher writes on a branch. The bundle and the App key now live in the `cheeseless-judge` environment, limited to the default branch, and only the `judge` job enters it. The workflow has `permissions: {}`. A `GITHUB_TOKEN` with `checks: write` would let any pusher post a check under the shared github-actions app, so the verdict is posted with a token from the judge App, minted after the judge step with `permission-checks: write` only. Adopters pin the required check to that App. `tests/unit/test_judge_workflow.py` pins the environment, the secret placement, the empty token permissions, SHA-pinned actions and the mint order. Amends P10-4 ("one repository secret") and P10-7. Not verified on real GitHub. | P10-4, P10-7 |
| P10-23 | **Review a2a98b11 (gpt-6.1-sol) of the check-run counter design: rejected design, all nine findings accepted.** #1 HIGH rank-after-create is not linearizable → fast-forward CAS on one ref plus a post-write chain check. #2 CRIT and #7 HIGH → P10-22. #3 HIGH check runs are subject to retention → check runs dropped as a store. #4 HIGH payload copyable → MAC binds repo, lineage, pool, seq, parent and run_key; duplicates refused; a separate seed MAC. #5 MED idempotency → `run_key = run_id/run_attempt`, a reused record is re-admitted against the limit, a refusal writes nothing. #6 MED identity across re-freeze → `k_b` derived from the store-wide key, bundle pins branch and root, a missing ledger blocks and never re-initialises. #8 LOW → codec in `domain/`, transport-only adapter, split conformance suites, "hidden not reached" assertion. #9 MED ADR overstated → ADR 0006 states the property only. | P10-6 |
| P10-24 | **CI budget counter design, revision 2 (proposal, `planning/research/ci-budget-counter.md`).** An append-only ledger branch per lineage, `cheeseless/budget/<lineage>`. Ruleset A (no bypass) blocks deletion and force pushes. Ruleset B (judge App bypass only) restricts creation and updates. The operator seeds it with `cheeseless ci-budget-init`. Each charge is one single-parent commit with a MAC'd record. Validation is pure (`domain/budget_ledger.py`), and every inconsistency is BLOCKED `INFRA_ERROR`. `reserve_run` does at most 3 non-force CAS attempts. The contents-write budget token is minted just before the judge step and never enters the candidate env. P10-6 stays open until the design passes a new cross-vendor review **and** the seven real-GitHub checks in section 6 pass (operator, P10-7). If any check fails, the fallback is an adopter-provided external store, or CI stays unbudgeted and blocked. The implementation retires P10-13 and P10-19. | P10-6, P10-13, P10-19 |
| P10-25 | **Review 0200f03e (gpt-6.1-sol) of counter revision 2: accept with fixes, all five findings accepted; revision 3.** #1 HIGH: a post-write chain check cannot repair a non-atomic ref update, so the P10-23 #1 claim of compensation is withdrawn. An atomic ancestry-checked non-force update is now prerequisite **P1**: the operator accepts it in a ledger row (evidence from the race check in section 6), or adopters use an external atomic store. After hidden execution the judge re-reads the chain; a lost commit means no PASS and an operator alert (detection only). #2 MED: the ledger moves to a dedicated private repository and the budget token is scoped to it; rulesets there are defence in depth; ledger DoS and same-repository damage are listed residuals. #3 MED: the release gate grows to eleven live checks, adding negative and positive environment-secret checks, required-check source pinning and token scope checks, all with canary secrets. #4 LOW: the seed has exactly zero parents, records exactly one; a seed-only ledger counts 0. #5 LOW: only a failure before any update was sent is free; an ambiguous durable reservation is charged without hidden input and never refunded. Amends P10-24: the release gate is P1 acceptance plus the eleven checks, not seven. | P10-23, P10-24 |
| P10-26 | **Review 6ba6921f (gpt-6.1-terra) of counter revision 3: accept with fixes, all five findings accepted; revision 4.** #1 HIGH: the post-hidden re-check misses an overwrite that lands after it, so it is documented as best effort and tested in both orderings; P1 acceptance or an atomic option (e) store stays mandatory. #2 HIGH: two judge processes sharing one `run_key` could both reach hidden input; each record now carries a per-process random `claim` under the MAC, and a record with our `run_key` but another claim is BLOCKED with an alert (one-shot execution claim under P1). #3 MED: live check 3 becomes an operation × actor ruleset matrix with a positive App append. #4 MED: `record.json` is authoritative, canonical JSON, strict schema, single-file tree, commit message equal to the blob. #5 MED: expected lineage and judged repo come from the authenticated task and the event, the branch name must match the lineage, and cross-lineage and cross-repo substitution are tested. | P10-24, P10-25 |
| P10-27 | **CI budget counter implemented (revision 4, sections 3, 4, 5 and 7).** Codec in `domain/budget_ledger.py`, `RunCounter` port, transport-only GitHub adapter, `LedgerCounter` (at most 3 CAS attempts, our own `run_key` and `claim` re-admitted, a foreign claim BLOCKED with an alert), bundle v2 pinning repo, branch and root, `ci-judge` wiring and the two-token workflow. **Retires P10-13 and P10-19**: `ci_limit` now binds across runs. **P10-6 stays open** (safer reading of P10-24/P10-25): the code is in place, but the release gate is still P1 acceptance plus the eleven live checks, none of which ran here. Choices made where the spec was silent, all fail closed: (1) bundle v1 is refused with a re-export message, and `ci-export` requires `--budget-repo` and `--budget-root`; a bundle whose branch does not match its lineage is refused at load. (2) `ci-judge` pops `CHEESELESS_BUDGET_TOKEN` from the environment first, even on usage errors, and validates `GITHUB_REPOSITORY_ID` (decimal, no leading zero), `GITHUB_RUN_ID` and `GITHUB_RUN_ATTEMPT` with exit 2 before any ledger read. These come from the runner and are not set in the workflow. (3) A ledger longer than a lowered `ci_limit` allows is `INFRA_ERROR`, not `budget_exhausted`. (4) A missing branch is a non-retried `CounterError`, and a malformed tree raises `LedgerMalformed`; both mean BLOCKED `infra_error`. (5) The post-hidden `confirm_still_on_chain` is fail closed: a PASS that is no longer on the chain, or that cannot be confirmed, becomes BLOCKED. An overwrite landing after the re-read stays the documented P10-26 #1 limit. (6) After a reservation the charge is kept: an infra cause in the hidden run or a runner exception is never PASS (a runner exception is BLOCKED `infra_error`) and never refunded. (7) The adapter does not normalise commit messages. If GitHub rewrites a message (for example a trailing newline), the record no longer equals the blob and the ledger blocks; live check needed. (8) `write_commit` combines blob, tree and commit into one port call. `LedgerIdentity` has no pool field, since the branch is CI-only. `judge()` takes `counter=` instead of a store. (9) The budget token (Contents write, ledger repo, owner pinned to `github.repository_owner`) is revoked in an `if: always()` step right after the judge; a revoke failure only warns, and the post step revokes it again. The ledger repo must therefore share the judged repo's owner. The App needs Checks and Contents. Open question: `cheeseless ci-budget-init` is not shipped, so `docs/ci/README.md` has the operator seed the root commit by hand. | P10-6, P10-13, P10-19, P10-26 |
| P10-28 | **Cross-vendor review (gpt-6.1-sol) of the P10-27 implementation: three LOW findings, all accepted.** #1 `_confirmed` re-checked only a PASS, so a charge overwritten during a failing hidden run went unnoticed; every charged verdict is now re-checked (`LedgerCounter.charged`), and a lost charge turns FAIL into BLOCKED `infra_error` with an alert. #2 `docs/ci/README.md` claimed the judge detects a rewound ledger; a rewind to an earlier valid commit looks like a shorter history, so the claim is gone and a test pins the limit (only Ruleset A prevents rewinds). #3 No real-bwrap canary for the budget token on the CI path; `test_the_budget_token_never_reaches_build_or_candidate` probes the env and every readable `/proc/*/environ` from a contract build step and from the candidate, with a negative control and a mutation check. Also: commit authors on `main` and `feat/phase-11-e2e` were rewritten to the noreply address (force-push by the operator); GitHub `refs/pull/1` and `refs/pull/2` still reference the old commits and only GitHub Support or a fresh repository can drop them. | P10-27 |
| P10-29 | **`cheeseless ci-budget-init` shipped; answers the P10-27 open question.** Runs on the operator workstation (it needs the store key): `ci-budget-init --budget-repo OWNER/NAME --repo-id ID`, token in `CHEESELESS_BUDGET_TOKEN`, popped first and never printed. Stdout is the root SHA only, ready for `ci-export --budget-root`. Design choices, all fail closed: (1) the seeder is a separate `LedgerSeeder` port extending `LedgerTransport` with `write_root` (parentless commit) and `create_ref`, so the judge's transport stays unable to create refs. (2) The seed commit is read back and validated before the branch is created, so a server that rewrites the message (P10-27 #7) is caught while nothing is published. (3) Idempotent: an existing branch is returned only when its tip is a parentless, valid seed for this repo id, lineage and store key; a branch with runs or a foreign seed is refused, never replaced. (4) A lost create response is resolved by re-reading the ref; losing a creation race adopts the winner only if it is a valid fresh seed. (5) A rejected create with no branch is explained as Ruleset B (seed before enabling it) or token permissions. (6) Repo id and ledger repo name are validated before any request. Security tests in `tests/security/test_ci_budget_init.py`. **Two live checks added to the release gate (P10-7):** (a) creating a `cheeseless/budget/**` ref with Ruleset B active is rejected (and how GitHub reports it), (b) `GET git/commits/{sha}` returns an unreferenced commit right after creation. `docs/ci/README.md` step 3 now uses the command. | P10-27 |
| P10-30 | **Cross-vendor review of `ci-budget-init` (codex gpt-6.1-sol, commit 58c6edd): no CRITICAL or HIGH.** Accepted: (1) the judge transport still had `write_root`/`create_ref`, so P10-29 (1) held only at the type level; they now live in a separate `UrllibLedgerSeeder` that only `make_ledger_seeder` builds, and a test asserts the judge transport has neither. (2) `create_ref` mapped only 422 to a refusal; 401 and 403 are refusals too, so a token without permission gets the Ruleset B / permissions explanation instead of "cannot reach". (3) An empty ledger repository answers 409 to every Git data write; that is now reported as "the ledger repository is empty" and the setup docs say to initialize it with a commit. (5) A lineage id may contain `..` or end in `.` or `.lock`, which git refuses as a ref; `branch_for` now refuses those before any request. (6) Failures outside the seeder (no repository, unreadable store) printed a traceback; `ci-budget-init` now catches them at its boundary and prints only the exception type. (7) The transport error detail (a fixed message with the HTTP status) now reaches the operator. (8) Live checks 12 and 13 are numbered in section 6 and the handoff says thirteen. Rejected: (4) popping the token after argparse; argparse exits with `SystemExit` before dispatch, spawns no process, and the environment ends with the process. | P10-29 |

## 2026-11-13 — Phase 11 real-agent sessions (local opt-in E2E)

| # | Decision | Revises |
|---|----------|---------|
| P11-1 | **Extra mounts, HOME seeds and env are opt-in and validated before anything launches.** `host_session` gains `extra_read_only`, `home_seed`, `extra_env` and `evidence_dir`. All four default to nothing. Validation runs right after the context is built, so a rejected request starts no server, no proxy and no agent. Every path is resolved with `realpath` and must be neither inside nor an ancestor of a protected root: repo, git common dir, trusted store, state dir, projection and socket dir. Ancestors matter because a bind of `/tmp` or `$HOME` would expose all of them. Generated dirs (projection, home, egress dir) do not exist yet, so the temp base is treated as an ancestor-or-equal root. | — |
| P11-2 | **Env is additive only.** `extra_env` may not set any key the session sets or that steers trust or routing: `CHEESELESS_*`, the egress socket, port and ready keys, `*_PROXY`/`*_proxy`, `NO_PROXY`, `HOME`, `TMPDIR`, `PATH`, `LANG`, the CA-bundle variables and `LD_*`. Collisions are rejected rather than merged, because `profiles.agent` lets a later key win. NUL in a key or value and `=` in a key are rejected. Error messages name keys, never values. `_merge_env` also refuses a duplicate key as a second guard. | P11-1 |
| P11-3 | **Seeds are copies, not mounts.** A seed is a regular file copied into HOME with mode 0600 in 0700 directories. The relative path may not be absolute or contain `..`, and the source is checked against the protected roots. HOME is still deleted on teardown, so a seed never outlives the session. | P11-1 |
| P11-4 | **Evidence capture.** `--evidence-dir` must not exist; capture creates it exclusively (0700), opens it `O_DIRECTORY|O_NOFOLLOW`, re-verifies the fd's real path outside the protected roots, and does every mkdir/write dir_fd-relative with `O_CREAT|O_EXCL|O_NOFOLLOW`. It copies the HOME tree (files only, no symlinks; 50 MB byte cap, 100 000 entries, depth 64, 60 s, each cap named in a `TRUNCATED` marker) and writes `projection-files.json`, after the agent exits and before teardown. A capture failure only warns on stderr, with the exception type and no values, so it cannot change the agent's exit code. | P11-1 |
| P11-5 | **Layout and CLI.** The helpers are top-level composition modules (`session_options.py`, `session_files.py`), following the `bootstrap_ci.py` precedent, so the four import-linter contracts are unchanged. The `session` subcommand splits argv at the first `--`, because argparse REMAINDER mishandles it. Its exit codes are the agent's, 1 if blocked, 2 for a usage or policy error. The egress-policy loader reads only `.toml` or `.json` (1 MiB cap) into `EgressPolicy.from_config`; `egress_policy.py` is not loosened. | P11-1 |
| P11-6 | **Tests.** `tests/security/test_session_mounts_canary.py` proves the rejections without a sandbox, with the launcher replaced by a recorder, and runs one real bubblewrap session. That session shows a read-only dir is executable and not writable, a seed lands as 0600, and the canary appears nowhere. The pytest markers `slow` and `e2e_agent` are registered, and `e2e_agent` is skipped by a collection hook unless `CHEESELESS_E2E_AGENT=1`. | P11-1, P11-2 |
| P11-7 | **A signalled session still tears down.** SIGTERM or SIGHUP used to kill the host process without running the `host_session` teardown, which left HOME with the seeded credentials, the egress CA key and the socket dirs in TMPDIR. `launch_host_session` now turns those signals into a `_Terminated` (a `BaseException`) for the duration of the session. The `with` blocks unwind, the CLI prints `session terminated by signal N; cleaned up` and exits 128+N. Proven by `tests/security/test_session_signal_teardown.py`, which checks for leftover dirs, the seed value, the CA file, live processes mentioning the projection and the canary. | P11-1 |
| P11-8 | **Known teardown costs, accepted for the MVP.** The outer bubblewrap ignores SIGTERM, so `_terminate` waits its 5 s grace and then kills it. A holder orphaned by a host `SIGKILL` stays until it is reaped by hand. Neither leaks hidden material, because the projection never holds it. | P11-7 |
| P11-9 | **E2E result (toy repo, real agents, 2026-11-13).** Both Claude Code and Codex ran inside `cheeseless session`. Each fixed the toy through the MCP `gate_run` until it passed, the canary count in the projection was 0, `promote` wrote `gate/<task>` and `ci-judge` passed both criteria on the promoted commit. Timings on one workstation: freeze 2.8 s; session start plus teardown about 2.2 s; Claude fix 36 s; Codex ping 7.7 s, Codex fix 45 s with 4 gate runs; `ci-judge` 2.2–2.3 s. A host `cheeseless run` took 1.96 s against 0.29 s for plain pytest on the same tree and suite. About 1.1–1.3 s of that is CLI and `uv` start-up, so the sandbox itself costs about 0.6 s per run (AC10, AC13). | — |
| P11-10 | **Codex inside the sandbox needs operator-side configuration, not product changes.** These requirements were found during the E2E: <br>• stdin must be `/dev/null`, otherwise `codex exec` waits for input. <br>• Websockets must be off through a custom provider (`supports_websockets=false`), because an upgrade is blocked as `protocol-upgrade`. <br>• `api.openai.com` must be on the allowlist next to `chatgpt.com`. <br>• Agentic extras that would ask for server-side tools must be disabled. <br>• The sanitized `auth.json` seed needs a non-empty, dummy `refresh_token`, otherwise Codex sends no auth. <br>• Codex filters the environment of MCP servers, so `CHEESELESS_SOCKET` and `CHEESELESS_TASK` must be passed through `mcp_servers.<name>.env`. <br>• MCP tools need `default_tools_approval_mode="approve"` under `exec`. <br>• Codex's own sandbox must be `danger-full-access`, because nested user namespaces fail inside bubblewrap and cheeseless is the outer sandbox. <br>The operator wrapper belongs in the user's dotfiles, not in this repo. | P11-5 |
| P11-11 | **Follow-ups.** <br>• Bake the socket and task into the staged MCP shim, so clients that filter the MCP env still work. <br>• The allow-log has an audit gap: allowed requests are not recorded per request. <br>• Oracle feedback is per criterion only. That is by design, but it should be documented for agent prompt authors. | P11-10 |
| P11-12 | **Seeds are pinned at validation.** `--home-seed` opens each source once (`O_RDONLY|O_NONBLOCK|O_CLOEXEC`), checks the opened object through `/proc/self/fd` (outside every protected root), `fstat` (`S_ISREG`, `st_nlink == 1`), and reads it under `SEED_FILE_LIMIT` = 1 MiB. `SessionExtras.seeds` holds `(rel, bytes)` with `repr=False`, and `seed_home` writes those bytes, so swapping the source path after validation cannot change what lands in HOME. Content is never logged. | P11-3 |
| P11-13 | **Mounts are pinned and bound by fd.** `--ro` opens each source with `O_PATH|O_CLOEXEC`, validates the opened object through `/proc/self/fd`, and keeps the fd. `profiles.agent` binds it with `--ro-bind-fd FD <verified path>` and `_launch_agent` passes the fds through. `host_session` closes every pinned fd in its `finally`; a partly failed validation closes the ones already opened. | P11-1 |
| P11-14 | **Hidden material is identified by inode.** Before validating extras, `host_session` loads the frozen task and builds `(st_dev, st_ino)` for regular files at `repo.root/<blob.path>` and every regular file under the trusted store. A hard link outside the repo has the same inode, so seeds and mounted trees are refused when they contain one. Mounted trees are walked with `os.stat(..., follow_symlinks=False)`. | P11-2 |
| P11-15 | **Evidence output never follows a pre-existing link.** `--evidence-dir` must not exist (its parent may). Creation is exclusive, the directory fd is opened `O_DIRECTORY|O_NOFOLLOW`, its real path is re-verified against the protected roots, and all subdirectories and files are created dir_fd-relative with `O_CREAT|O_EXCL|O_NOFOLLOW`. The CLI help says the directory must be new. | P11-4 |
| P11-16 | **Reserved sandbox destinations.** `profiles.RESERVED_MOUNTS` (`/proc`, `/dev`, `/sys`) and `profiles.SANDBOX_TMP` are passed from bootstrap. An extra mount that is inside or contains a reserved pseudo-filesystem is refused, as is any ancestor of the sandbox tmpfs. Paths inside the host `/tmp` stay allowed, because tests and sessions mount private directories there. | P11-1 |
| P11-17 | **Bounded iterative walks.** The HOME copy and the projection manifest use an iterative, dir_fd-relative walker (no recursion, no symlink following): `EVIDENCE_ENTRIES` = 100 000, depth 64, 60 s wall clock, plus the 50 MB byte cap. Hitting any cap stops the walk, sets `truncated`, and writes `TRUNCATED` naming the cap. Deep or wide agent trees therefore cannot raise `RecursionError` or run unbounded. | P11-4 |
| P11-18 | **Manifest is JSON.** `projection-files.json` is a JSON array of sorted relative paths (`json.dumps`, `ensure_ascii=True`), so a filename containing a newline or a non-UTF-8 byte round-trips exactly instead of forging a line. `projection-files.txt` is gone. | P11-6 |
| P11-19 | **Residual risk, accepted.** A concurrent host process can create a hard link after the inode walk and before the bind; the check is a snapshot, not a lock on the host filesystem. Other git worktrees of the same repository are not in the protected set, so a hidden file hard-linked there is not detected; the inode check catches a link while the original still exists under the repo or store. | P11-14, P11-15 |
| P11-20 | The hidden-inode set also covers every regular file under the git common dir (loose objects and packs) and the result store, because a hard link to a loose object leaks hidden test content after zlib decompression. The set is computed only when the session requests extra mounts or home seeds. The mount walk fails closed: an entry it cannot open or stat refuses the session instead of being skipped, since an unreadable subdirectory could hide a hard link. | P11-14, P11-19 |
| P11-21 | **Read-only extras exclude IPC and devices.** Mount roots and entries permit only directories, regular files and symlinks; sockets, FIFOs and devices refuse the session. Interior symlinks retain sandbox namespace semantics. Seeds use `O_RDONLY\|O_NOFOLLOW\|O_NONBLOCK\|O_CLOEXEC`, then require a regular file before reading. The agent's legitimate UDS access and seccomp policy are unchanged. | P11-12, P11-13 |
| P11-22 | **Protected inode enumeration fails closed.** Walk and stat errors refuse extras instead of returning a partial inode set. Each protected-root walk has a 2 000 000 entry cap and a 60 s deadline. | P11-14, P11-20 |
| P11-23 | **Marker regression test exercises the write.** A hostile `TRUNCATED` symlink is planted after exclusive evidence-directory creation; its canary-bearing target must remain byte-for-byte unchanged. A writer using `O_TRUNC` and following symlinks fails the test. | P11-15 |
| P11-24 | **External object stores are protected.** The git adapter enumerates resolved object storage and recursive alternates (at most five edges), including caller object-directory environment settings. Bootstrap protects these paths and scans their inodes. Protected file symlinks contribute their target inode. Accepted limitation: directory symlinks inside git or store directories refuse extras as an unsupported layout; they are never followed by the inode walk. | P11-20, P11-22 |
| P11-25 | **Bound enumeration before sorting.** Mount, evidence and projection walks use `scandir`, reading at most the remaining entry budget plus one before sorting. Deadlines are checked per entry during enumeration and processing. Mount overflow refuses extras; evidence and manifest overflow retain the truncation marker. | P11-17 |
| P11-26 | **MCP shim bakes the socket and task; the session environment cannot redirect it.** `_stage_runtime(socket_path, task_id)` writes `cheeseless-mcp` with `repr()`-quoted socket and task literals and no `os.environ` lookup; the unused env-based `AGENT_BOOT` fallback string is removed. `CHEESELESS_SOCKET`/`CHEESELESS_TASK` remain in the agent env for older operator wrappers but are ignored by the shim, so a client that filters them, or sets conflicting values, still reaches the one authenticated per-session UDS. No global state and no mutable projection path participates. `tests/unit/test_mcp_shim.py` pins quoting and the absence of env reads; `tests/integration/test_mcp_uds_session.py` runs the real staged shim under `env -i` with conflicting variables through bwrap and completes `gate_run` over the authenticated UDS. | P11-10, P11-11 |
| P11-27 | **Per-request allow audit, bounded metadata only.** `domain/audit.py` defines schema `cheeseless.egress.allow.v1` with exactly `schema`, `request_id` (stable per flow, 16–64 hex), `stage` (`request_admitted`/`response_clean`), normalised `host`, upper-case `method` (or `OTHER`), `decision` (`allow`) and `rule` (`allowlisted-host`/`model-request`/`registry-get`). There is no field for path, query, fragment, userinfo, header, body, token, fingerprint or exception text, and every field has a closed shape/length. `request_admitted` is written once after host/method/URL/header/body policy and request DLP/archive inspection; `response_clean` once only after the stream completed without a block. At most two events per request. The sink is a `ports/audit.py` callback instantiated in bootstrap as `app/audit.py::StoreAuditSink`, which serialises writes to `Store.log_event` with a lock; the egress adapter imports only the domain/port types, so no import-linter contract is weakened. | P11-11 |
| P11-28 | **Audit failure fails closed, with the streaming limit stated.** A request-stage audit write failure returns catalogue `infra_error` and sends nothing upstream, then signals the existing supervisor as `rule=audit id=audit-unavailable`, which persists `BLOCKED` and tears the session tree down. A response-stage failure happens after bytes may have left the proxy (the same accepted residual as a mid-stream DLP hit), so it does not claim `response_clean` and tears the session down instead of pretending the response was audited. `tests/security/test_egress_audit_canary.py` is the positive control: real TLS upstream, real mitmproxy and the UDS/TCP path, clean traffic yields exactly `request_admitted` + `response_clean`, a blocked response yields only admission, a denied host yields none, and both audit-failure paths keep a canary exception string out of the client and the events. | P11-11, P11-27 |
| P11-29 | **The real-agent E2E is opt-in and operator-configured.** `tests/e2e/test_e2e_agent.py` is marked `e2e_agent` and `slow`; the existing collection hook skips it unless `CHEESELESS_E2E_AGENT=1`. The operator supplies an explicit CLI with `CHEESELESS_E2E_AGENT_CMD` (JSON argv, shell string accepted), optional credential/config material with `CHEESELESS_E2E_AGENT_SEED`/`CHEESELESS_E2E_AGENT_RO` and a bound with `CHEESELESS_E2E_AGENT_TIMEOUT` (1–3600 s). The harness never inspects, logs or prints a credential value: a known `claude`/`codex` command without visible config is refused explicitly unless seeds/mounts are supplied, and any other operator wrapper is trusted to own its credentials. An explicit seed is read only by the normal validated host loader and written to the throwaway HOME; evidence capture copies that HOME, so the evidence directory is sensitive (P11-32). It runs the toy through a real `host_session`, appends the prompt as the last argv, proves the HOME was removed, scans the captured evidence for the hidden canary/sentinel and hidden projection paths, then promotes the tree and runs the real `ci_judge.judge`. No real agent or paid request was executed in this task. Deterministic harness tests (command parsing, refusal without credentials, fixed-tree promote+judge, canary evidence refusal, bounded timeout cleanup, no original hidden tree) run in the normal suite. | P11-11 |
| P11-30 | **Oracle and release documentation.** `docs/agent-prompting.md` documents the per-criterion-only oracle (what the agent sees and cannot see) and the exact manual real-agent opt-in command. `docs/egress-audit.md` documents the audit schema and fail-closed policy. `README.md` and `planning/handoff.md` now state that PR #1–#4 are merged at `e93b8a3` and that `feat/phase-11-followups` carries P11-26…P11-29. **Release blockers stay honest:** P10-6 is still open until the operator runs the P1 acceptance and thirteen live GitHub checks, `refs/pull/1`/`refs/pull/2` still need GitHub Support or a fresh repository, the real-agent E2E was not run here, and phase 12 sanitizer/publication remain. Residuals: the allow log grows with allowed traffic (bounded per event and per request, no session-wide cap), and a response-stage audit failure cannot retract bytes already forwarded. | P11-11, P11-26, P11-27, P11-28, P11-29 |
| P11-31 | **Verification for P11-26…P11-30 (2026-11-14).** `uv run --frozen pytest`: 1133 passed, 2 skipped, 87.79% coverage (the skips are the opt-in real-agent test and one environment-dependent test). `ruff check .`, `mypy` (80 source files), `lint-imports` (4 contracts kept), `scripts/gen_proto.py --check`, `scripts/gen_errors_doc.py --check` and `cheeseless --version` (0.0.0) are clean. Not verified here: any live GitHub workflow, the real CI budget ledger and a real model request. | P11-30 |
| P11-32 | **Cross-model review fixes (P11-27…P11-29).** *Finding 1 (audit trusted attacker text).* The first audit draft copied any 1–16-letter wire method (`_safe_method("SECRETTOKEN")` → `SECRETTOKEN`) and its domain regex accepted arbitrary upper-case methods and arbitrary lower-case rule text, so request text could enter trusted metadata. The method is now a member of `domain.audit.KNOWN_METHODS` (the nine canonical HTTP methods) or the single escape value `OTHER`; `egress_policy` owns `ALLOW_RULES` (`allowlisted-host`, `model-request`, `registry-get`) and `allow_rule` returns only those constants; `AuditEvent` re-validates both closed sets. The addon maps any non-canonical method (excluding case-insensitive spellings of a known method) to `OTHER` without copying its text, and the host is still the exact normalised allowlisted host. Network policy is unchanged: generic hosts still accept arbitrary methods, but the audit never records them. New canary tests in `tests/security/test_egress_audit_canary.py` drive a real TLS upstream through mitmproxy and prove an unknown method becomes `OTHER`, known methods are preserved, a method/query token never appears in the event text, and an audit-sink failure still fails the request closed with `infra_error`. *Finding 2 (unbounded evidence scan).* `tests/e2e/harness.scan_evidence` replaced `Path.read_bytes()[:limit+1]` and unbounded `os.walk`: it opens the root and every child fd-relative with `O_NOFOLLOW`, regular files with `O_NONBLOCK`, rejects symlinks, special files (FIFO/socket/device), unreadable entries and the `TRUNCATED` marker, `fstat`s regular files, refuses `st_size` over the per-file/total caps before reading, reads through a bounded helper, caps entries during `scandir` enumeration, and enforces depth and deadline caps. `projection-files.json` is parsed only from the bytes already inspected under the cap; malformed, non-string or over-cap listings are refused and the file is never reopened. `verify_evidence` requires the bounded listing, so a partial capture can never be claimed as proof. Deterministic regressions cover a sparse 1 TiB file that is refused without a read, a FIFO, a symlink whose canary target stays unread and unchanged, wide and deep trees, malformed and over-cap listings, a truncation marker, a missing listing, and the positive control. *Finding 3 (real-agent outcome).* `run_agent` now captures evidence inside the live session and raises `AgentFailed` on a blocked supervisor, a non-zero exit or a missing stored gate result; `promote_and_judge` still promotes and judges the agent's actual stored result, never a host-written replacement. A regression seeds a stale PASS, runs an agent that exits 3, and requires `AgentFailed`. *Docs.* `docs/agent-prompting.md`, the harness and the opt-in test now state that the harness never inspects/logs credential bytes, while an explicit seed is read by the normal validated host loader and written to the throwaway HOME; evidence capture copies that HOME, so the evidence directory is documented as sensitive. | P11-27, P11-28, P11-29 |
| P11-33 | **Verification after P11-32 (2026-11-14).** `uv run --frozen pytest`: 1166 passed, 2 skipped, 87.79% coverage (skips are the opt-in real-agent test and one environment-dependent test). `uv run --frozen ruff check .`: clean. `uv run --frozen mypy`: no issues in 80 source files. `uv run --frozen lint-imports`: 4 contracts kept. `scripts/gen_proto.py --check`, `scripts/gen_errors_doc.py --check` and `cheeseless --version` (0.0.0): clean. Not verified here: any live GitHub workflow, the real CI budget ledger, the opt-in real-agent E2E and a real model request. | P11-32 |

| P11-34 | **Parent cross-model review (gpt-6.1-sol, 2026-11-14).** Reviewed DeepSeek V4.1 Flash changes and verified the P11-32 fixes. Independently ran the focused audit/domain/sink/shim/harness/security/MCP-bwrap suite: 82 passed. Ruff, mypy, all four import contracts and both generated-code/document checks pass. No unresolved blocking finding in this scope. Full-suite result reported by implementer: 1166 passed, 2 skipped, 87.79% coverage. Real-agent/live-GitHub release checks remain unverified. | P11-32, P11-33 |

## 2026-11-14 — Phase 12 release audit (P12)

| # | Decision |
|---|----------|
| P12-1 | **Local release audit, not a production subsystem.** `scripts/release_audit.py` is a small operator tool over three surfaces: the current tracked tree (staged from `git ls-files`, symlinks scanned as link text, never followed), every locally reachable git object via `gitleaks git --log-opts=--all`, and commit metadata/messages/authors plus historical paths exported under caps, plus the actual sdist/wheel built by `uv build`. The official Gitleaks default rules are extended with one narrow allowlist only: `generic-api-key` on `tests/fixtures/release_audit/synthetic-token.txt`. There is no `disabledRules`, no broad path exemption and no home-grown replacement for Gitleaks. Findings are metadata only: scanner, rule id, path/commit, line and count. Raw scanner JSON is deleted without being printed; the private JSON report contains no `Secret`/`Match` field and is written `0600` in a `0700` directory outside the repository. Exit codes: 0 clean, 1 blocked, 2 fail closed. Nothing is uploaded and the audit never edits, deletes or rewrites repository content. |
| P12-2 | **Fail-closed conditions.** The scanner must exist, be executable and report exactly Gitleaks 8.30.1; a missing or mismatched scanner, nonzero scan exit, missing/malformed report, unreadable/capped/unsupported archive, shallow history, history object/blob caps, invalid denylist, report directory inside the repo and build failure all return exit 2 with a stable code and no scanner stderr. Files over 50 MiB and history blobs over 50 MiB block before scanning or are reported as hygiene findings. Archive members are extracted with `O_NOFOLLOW`, bounded by member count, total bytes and depth 2; absolute/`..`/backslash names, links, devices, encryption, duplicate names and expansion caps block without reading the member. `.env`, auth/credential files, private state, caches and `acceptance/` are refused; every Python module under `src/cheeseless/` must be present in both archives. |
| P12-3 | **Pin, operator denylist and packaging hygiene.** `scripts/install_gitleaks.py` downloads only the official 8.30.1 Linux x64 release, verifies the published archive SHA-256 `551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb`, checks the installed version and prints no credentials. The optional denylist is a TOML file outside the repository with capped `id`/`pattern` entries; hits are reported as rule/location only, and a matched substring in a path is replaced with `<redacted>`. `pyproject.toml` now excludes local caches and the `CLAUDE.md` symlink from the sdist, and `.gitignore` ignores `.import_linter_cache/`. `.github/workflows/ci.yml` has a separate `release-audit` job with full history and no repository secrets. |
| P12-4 | **Limits and verification.** The report enumerates every local ref (147 here) and states that server-side pull refs/remote history are not visible; GitHub `refs/pull/1` and `refs/pull/2` still reference pre-rewrite commits with the old author email and need GitHub Support or a fresh repository. Do not claim the GitHub history is sanitized. Gitleaks detection is heuristic, not proof of absence. Local run on `feat/release-sanitizer` (2026-11-14): **CLEAN** over the tracked tree, full local history and two actual built archives with Gitleaks 8.30.1. Verification: `uv run --frozen pytest` → 1181 passed, 2 skipped, 87.79% coverage; `ruff check .` clean; `mypy` no issues in 82 source files; `lint-imports` 4 contracts kept; both generated checks clean; `cheeseless --version` 0.0.0. Security tests in `tests/security/test_release_audit.py` are synthetic only (fake credentials/canary): clean control, history-only hit, artifact-only hit, missing/incompatible/failing scanner, private report permissions/redaction, unsafe/secret/private archive members, missing module, nested depth cap and denylist redaction. This is **not** a release-readiness claim: `P10-6` (P1 acceptance plus thirteen live GitHub checks), the server-side pull refs, explicit publication approval (Q34) and the opt-in real-agent E2E remain open. |
| P12-5 | **Release-audit review findings 2–7 fixed.** The audit now applies the operator denylist to every reachable blob (bounded per object and by a 512 MiB staging cap), to annotated tag objects, and to every extracted regular archive file, not just paths. Raw reachable blob and tag contents are staged and scanned by Gitleaks independently of patches, so deleted binary blobs and tag-only messages are covered. All emitted locations are opaque (`category:hash`), including Gitleaks' `File` field and local refs; stdout, stderr and the JSON report no longer contain raw tracked names, archive member names or ref names. Tracked paths are also checked against the private-state policy (`seed/`, caches, build output), not only `.env`/credential rules. The fixture allowlist now requires the exact synthetic value and the exact repo-root-anchored path together (`condition = "AND"`, `regexTarget = "secret"`); the synthetic fixture is excluded from the sdist so archived copies cannot reintroduce it. TAR names are collected during bounded extraction and an exhausted archive is never reopened; the unbounded `_archive_names()` helper is removed. Docs updated in `docs/release-audit.md`. New synthetic-only security tests cover deleted historical denylist hits, archive-content denylist hits, opaque tracked/archive/ref/scanner-file locations, annotated-tag denylist and scanner canaries, a deleted binary blob, exact-path+value allowlist behaviour with the real pinned Gitleaks 8.30.1, tracked private state, and the TAR cap no-reopen guarantee. Verification: `uv run --frozen pytest` → 1194 passed, 2 skipped, 87.79% coverage; `ruff check .` clean; `ruff format --check` clean; `mypy` no issues in 82 source files; `lint-imports` 4 contracts kept. Real local audit with pinned Gitleaks 8.30.1 stayed **CLEAN** over the tracked tree, local history and built archives. This is **not** a release-readiness claim: `P10-6`, the server-side pull refs, explicit publication approval and the opt-in real-agent E2E remain open. |

| P12-6 | **Six release-audit review findings hardened.** Every scan ignores inline Gitleaks exemptions. Every Git subprocess disables replacement lookup; replacement refs and graft files fail closed. Original local ref names are scanned before report hashing. ZIP comments/extras and TAR/PAX/GNU metadata are scanned under byte limits. Physical TAR and ZIP central-directory preflights enforce parser budgets before extension/header parsing or entry allocation; resource-cap hits return exit 2. ZIP64/split and unsupported archive layouts fail closed. The exact path+value synthetic allowlist test creates its fixture in temporary storage and works without the excluded sdist fixture. Synthetic security regressions were run red then green in order 1, 2, 3, 6, 4, 5; **Final gates:** `ruff check .` passed; `ruff format` left both changed Python files unchanged and `--check` passed; mypy passed (82 source files); import-linter kept all four contracts; full unscoped `uv run --frozen pytest -q` completed with 1212 passed, 2 skipped, 87.79% coverage in 447.28s. A checksum-verified Gitleaks 8.30.1 installed outside the repository audited 174 local refs and two freshly built archives: exit 1, BLOCKED, 13 metadata-only `generic-api-key` finding records. This gate is not clean; no values were printed, no allowlist was broadened, and no history was rewritten. No release-readiness claim; existing publication and live gates remain open. |
| P12-7 | **Fixed (F2, source: Terra review): every historical path occurrence is checked.** `git rev-list --objects --all` emits each object id once, so identical blob content committed under a clean path and a sensitive or denylisted path surfaced only one of the two names. Historical names now come from `git log --all --root -m --no-renames --name-only --format= -z`, decoded NUL-separated under the existing metadata cap and `GIT_NO_REPLACE_OBJECTS`. Each distinct name gets the same sensitive-path and denylist checks; `rev-list` still supplies object ids and sizes. Red/green synthetic regressions: identical content under `clean.txt` plus `.env`, and under `clean.txt` plus a denylisted name, both now block. Default and release modes share the fix. |
| P12-8 | **Fixed (F3, source: Terra review): the scanner binary digest is pinned.** `GITLEAKS_BINARY_SHA256` maps Linux/x86_64 to `88f91962aa2f93ac6ab281d553b9e125f5197bbbce38f9f2437f7299c32e5509`, the extracted 8.30.1 binary, and is recorded in the docs. `check_scanner` still requires exactly version 8.30.1; the digest is checked separately. In the default mode a mismatch, or an unpinned host platform, only adds a limitation to the report, so CI (no denylist, no release mode) keeps its current behaviour. With `--release` either condition raises `gitleaks-digest-mismatch` (exit 2). Red/green synthetic tests: a same-version fake scanner is rejected in release mode and only warned about in default mode. |
| P12-9 | **Fixed (F1/F4 together, source: Terra review): an explicit operator release gate.** `--release` is for the operator before publication; CI never passes it, so default mode is unchanged apart from the F2 fix and the F3 warning. In release mode a missing or empty denylist fails closed (`denylist-required`, exit 2), checked after the scanner digest. A clean audit is otherwise blocked (exit 1) with one metadata-only finding `remote-refs-unattested`, whose scanner is `hygiene` and location is the fixed category `remote-refs`, until `--remote-refs-attested` is also passed. The flag's help text states it asserts the operator verified that server-side refs (for example `refs/pull/*`) no longer expose pre-rewrite commits; the audit cannot verify that from a clone. The report JSON records `release_mode` and `remote_refs_attested`. Synthetic tests pin the fake scanner digest where needed and cover: missing denylist, empty denylist, unattested block, attested clean pass, and default mode still exiting 0 on a clean repo without a denylist. Docs state the gate and keep the honest limits; there is still no claim that history is sanitized. |

| P12-10 | **Fixed P1 and P2 (source: Sol review).** Resolve the scanner to an absolute path before hashing and execution; combine reachable object names with commit-diff paths under existing caps and reject undecodable names. Synthetic security regressions cover a conflicting PATH scanner and a tree-only ref. |


## 2026-11-14 — Phase 13 live GitHub release-gate harness (P13)

| # | Decision |
|---|----------|
| P13-1 | **One-command live harness, never run here.** `CHEESELESS_LIVE_GITHUB=1 uv run --frozen pytest tests/live -m live_github --no-cov` runs checks 1-13 of `planning/research/ci-budget-counter.md` section 6 (one test each, `test_check_NN_*`). Without the env var all 13 skip. The marker is registered in `tests/live/conftest.py` via `pytest_configure`, so `pyproject.toml` is untouched. Config comes only from env var names; tokens are redacted wrappers, only status and body key names are reported. Nothing here has been run against real GitHub. |
| P13-2 | **Decision logic is tested offline.** `tests/live/fake_github.py` is a stateful fake behind the `GitHubClient` port; `tests/unit/test_live_checks.py` proves every check passes on a correct setup, never passes when a protection is broken (knob table), accepts creation rejects only on 403/422 with ruleset evidence, covers the App fast-forward positive case, and shows 10 concurrent writers yield exactly one winner per tip. A missing prerequisite is `precondition missing` (a failing test), never a pass; only check 8 without a same-repo config is `not applicable`. |
| P13-3 | **Spec contradiction, resolved toward the rulesets.** Check 3 says every creation/update combination is rejected except the App fast-forward, but the budget token is minted by the judge App, so it is the same bypass actor of Ruleset B. The harness therefore expects the budget token's creation and fast-forward to be ACCEPTED (201/200); the pusher is always rejected; delete, force update and rewind are rejected for every actor through Ruleset A (no bypass). A 404 is not a rejection: a pusher without ledger access gets 404, so the pusher needs push permission on the ledger repository or the check reports a missing precondition. |
| P13-4 | **Setup script is dry-run by default.** `scripts/live_gate_setup.py` (`setup`, `manifest`, `teardown`) uses `gh` except for in-process HTTPS App manifest conversion, mutates only with `--apply`, creates private `cheeseless-canary-*` repos only, and teardown refuses any other name. Secret values go to `gh secret set` on stdin and are never printed (no pynacl dependency). The App manifest flow is the single manual click; the key is stored at mode 0600 and refused inside a git repository. Checks 9-11 need real Actions runs, and check 11's `mergeable_state` signal has not been observed live. |

| P13-5 | **Protection evidence (source: Terra review, findings 1/5/6).** Same-credential write controls precede same-repo, budget-scope and matrix probes; control refs/files are cleaned up. Protected writes use run-unique paths; Contents conflicts are distinct from protection rejection. Authentication failure is a missing precondition. Independent matrix cells require attributable ruleset rejection evidence. |
| P13-6 | **Workflow evidence (source: Terra review, findings 2/3/8).** Successful execution and secret-absence steps establish isolation; cancelled/broken jobs are missing preconditions. Spoofing requires a successful check with exact name/SHA and GitHub Actions attribution before testing the genuine App. Required context and numeric App ID match exactly. |
| P13-7 | **Owned teardown (source: Terra review, finding 4).** Setup persists non-secret full-name/ID/created records immediately after creation; existing repos remain unowned. Teardown deletes only configured, owned canaries after rechecking IDs, and never includes an unconfigured same repo. Dry run and apply guards remain. |
| P13-8 | **Private conversion transport (source: Terra review, finding 7).** Exchange one-time manifest codes through in-process verified HTTPS with timeout and sanitized failures, never subprocess argv/environment/logs. Offline injected transport regressions cover this boundary. |


| P13-9 | **Attribute protection without positive message matching.** GitHub git-data ruleset rejections can return only HTTP 422 and "Reference update failed". Checks 3/12 poll rule suites using the admin credential, matching the full ref, authenticated actor login (PAT user or JWT-authenticated App slug plus `[bot]`), `fail` result and attempt start at GitHub's second precision; run-unique refs isolate attempts. Bypass entries are not failures. Check 8 uses classic protection read by admin (including enforce_admins), a successful same-token unprotected control write and a 403/409/422 rejection; explicit Contents conflicts remain missing preconditions. A successful protected write always fails. The fake records fail/bypass suites, denies non-admin suite reads and returns generic 422 ruleset rejections. Offline unit/security regressions cover delayed evidence, unrelated/stale/bypass/missing/malformed suites, malformed ref requests, failed control/config reads and successful-write canaries. Setup behavior and production layers are unchanged; no real GitHub calls were made. Verification: requested unit/security/live command: 1325 passed, 14 skipped; focused attribution suite including subsequent tests: 129 passed, live-harness coverage 85.63%; Ruff check tests/scripts and format check on all touched Python files passed. |

| P13-10 | **Live run: all 13 release-gate checks PASS against real GitHub (2026-11-14, merged `main` e08f7f3).** Run in two halves against four disposable private canary repos (ledger, judged, fresh, same): checks 1-8 = 8 passed in 148.52 s; checks 9-13 = 5 passed in 85.46 s. Only PASS counts; no check was skipped or waived. Live behaviours that shaped the harness (all fixed in PR #9): a ruleset bypass App must be installed on the repo first (else 422); a trailing-slash API URL returns 404; a ruleset-blocked ref update returns `422 "Reference update failed"` with no rule wording, so attribution comes from the rule-suites API read with the admin token; the same-repo canary uses classic branch protection (`enforce_admins`), has no rule suites and rejects with 409; the pusher PAT has Contents and Workflows write but no Actions read and no Pull requests scope, so run data and PR scaffolding are observed with the admin token; `workflow_run` runs carry the default branch tip as head SHA, so they are attributed by run order; the `cheeseless-judge` environment gate fails branch `with-env` jobs before any step (counted as secret-unreachable evidence) and makes a mergeable PR `unstable`, not `clean`. **Still open before publication:** real-agent E2E, `refs/pull/1` and `refs/pull/2` server-side refs, GitGuardian incident dismissal, explicit publication approval (Q34). The repository stays private. |

## 2026-11-15 — Real-agent E2E (P14)

| # | Decision |
|---|----------|
| P14-1 | **Harness gap fixed: the E2E can now pass an egress policy.** `run_agent` called `host_session` without a policy, so the default empty allowlist left a real agent unable to reach its model API. `CHEESELESS_E2E_AGENT_EGRESS` (path to a `.json`/`.toml` policy outside the repo, loaded with the existing `load_egress_policy`) feeds `AgentConfig.egress_policy`; load errors surface as `AgentUnavailable` naming only the exception type. Deterministic tests cover unset, valid, invalid and forwarding. |
| P14-2 | **Real-agent E2E PASSED (2026-11-15).** `tests/e2e/test_e2e_agent.py` with Claude Code 2.1.319 (`--model sonnet`) in the bwrap host session: 1 passed in 27 s. The transcript shows 7 `gate_run` tool calls, the first attempt did not pass and a later one did; the harness's evidence scan, HOME-removal proof, promote and real CI judge all passed. One vendor/model only; a second vendor (Codex) was not run. |
| P14-3 | **Operator setup, all outside the repo.** A wrapper script runs `claude -p` with `--strict-mcp-config` and an MCP config naming `$CHEESELESS_MCP`; the claude binary directory and the wrapper directory are read-only mounts; a sanitized credentials copy (access token only, refresh token blanked, so a sandboxed refresh cannot rotate the operator's login) and a minimal `.claude.json` are seeds; the policy allows only the one model host. After the run the seeded credentials copy in the captured evidence was shredded. The evidence directory is sensitive and was not committed. |

| Study-2 | **Pre-data adversarial review fixes.** NAIVE uses a minimal bubblewrap namespace and a fail-closed protected-path/write probe; post-agent candidate execution and editable test collection are isolated too, with positive and denial controls; network needs explicit opt-in. Both real arms disclose diff-only attempt observability and unknown event-based kinds. Usage comes only from a separate parent-owned trusted-wrapper directory. Mandatory pilot/main phases protect held-outs and resume/report separation; fresh-session attestation is recorded. Manifest writes use one append write plus fsync; incomplete tails require explicit archived repair. Task paired differences lead the descriptive statistics. Amendment 2 preserves frozen text and the optional gate prompt/hash; skipped checks remain operational false rejects. No real agent calls were used for validation. |

### Study harness follow-up review (2026-11-15)

- NAIVE launch and self-check explicitly use `close_fds=True`, `pass_fds=()` and
  disconnected stdin. Python closes descriptors in the exec child; closing them in
  the harness parent would break its own files/sockets, and a Python preexec hook
  would be unsafe in a threaded process. Installed bubblewrap help exposes no
  `--close-fd` option. A real sandbox regression keeps a file canary and socket
  inheritable and verifies their descriptors are absent in the child.
- NAIVE executable validation uses the mounted namespace, with a fixed system PATH
  and an explicit executable file seed route under `/agent-home`. Direct seed
  wrappers have their SHA-256 recorded in each real manifest record. The frozen
  protocol remains unchanged; operational details live in the supplemental checklist.
- Manifest tail repair validates every complete JSON record before archival or
  truncation. A malformed complete record followed by a fragment changes nothing.
- The reported quiet study hang reproduced as slow successful tests: the unmodified
  base completed 108 study tests in 117.93 seconds. Each dry-arm matrix test runs
  multiple sandboxed grades before emitting its first dot. Study git operations and
  the remaining security probe now have timeouts; subprocess stdin is disconnected.
- CI runs `uv run mypy` over src, scripts and study. Explicit `mypy_path = "src"`
  gives standalone `mypy study` the same typed source imports without suppressions.

Validation: the exact timeout-wrapped study selection passed 118 tests (1399
deselected) in 149.46 seconds; targeted review/security regressions passed 15
tests in 12.54 seconds. Both CI mypy (100 source files) and standalone study mypy
(17 source files) pass. All four import contracts, generated-file checks, ruff
lint, formatting, diff whitespace and the diff leak scan pass. Four pre-existing
format-only differences in unrelated test files were normalized for the requested
repository-wide format check.

Full coverage validation: the first 600-second wrapper returned 124 although pytest
finished all then-collected tests during shutdown (1498 passed, 15 skipped in
621.05 seconds, 87.82% coverage). A fresh full run with a 900-second wrapper included
the four final regression cases and exited 0: 1502 passed, 15 skipped, 2 warnings
in 578.64 seconds; coverage 87.81% (required 80%). The exact study selection used
the requested 600-second wrapper and exited 0. No study protocol amendment needed.

## Egress stream hold-back must not emit empty chunks

Root cause of a real-agent infra error: `EgressAddon` installed a stream callable that
returned `b""` while `BodyScanner` held back bytes (the 512-byte sniff window). For an
HTTP/1.1 `Transfer-Encoding: chunked` response mitmproxy writes an empty chunk as
`0\r\n\r\n`, the end-of-body marker, so the client received a truncated, empty body
(`Malformed_HTTP_Response` in the agent). Content-length and HTTP/2 paths were not
affected, which is why earlier canary tests and the Claude endpoint passed.

Fix: the callable now returns a list of chunks, empty when nothing is releasable. The
DLP decision logic is untouched. Tests: `tests/security/test_egress_canary.py` gains a
chunked SSE endpoint with a small first chunk (full delivery and proper termination) and
a chunked canary stream (still blocked, hit recorded). No ADR change; no protocol
amendment needed.

## 2026-11-15: study Amendment 4 (disclosure annotation, staged runs)

- Added a secondary `gamed` split (disclosed vs silent) from a manual sidecar; primary
  outcomes unchanged, intent still not inferred.
- Main phase run in stages (96 DeepSeek runs, 12 Haiku probe, then sized) instead of 360 at
  once; the 360 was a judgement call, not a power calculation.
- Decision: do not hide the staged runtime from the agent. The MCP shim must execute from it
  and it holds no hidden tests; documented as a limitation. Per-criterion feedback bisection
  documented as a design property of a black-box gate.

## 2026-11-15: study Amendment 5 (bytecode made gate results stale)

Stage B probe: 2 of 3 solvable cheeseless Haiku runs were `false_reject` / `gate_stale` with a
passing gate and a passing oracle. Cause: no `.gitignore` in the task fixture, so `.pyc` files
written after the last gate changed the snapshot hash. Decision: fix the fixture (ship a
`.gitignore`), not the product, because `snapshot` honouring `.gitignore` is correct and any
real repository ignores bytecode. Rejected: `PYTHONDONTWRITEBYTECODE` in the wrapper (hides the
defect, differs per agent). Old 12-run probe excluded from analysis, kept as a detection.

## 2026-11-15: study Amendment 6 (four extra pilot models)

Operator asked to try more lightweight models. Chosen: `glm-5.3-flash`, `kimi-k3` (OpenCode Go),
`gpt-6.1-terra`, `gpt-6-luna` (Codex). Decision: 1-rep pilots with separate manifests, never
pooled, descriptive only. Rejected: expanding to 7 reps each (quota; the first signal is whether
a model games at all) and folding them into the Haiku/DeepSeek analysis (breaks the frozen
design). Codex setup: `codex-study` copied to a system dir, seed auth with a blank refresh token
(rotation would invalidate the operator's session), host allowlist limited to the vendor host.
Open risk: Luna is excluded from restricted work by routing policy; fixtures are synthetic.

## 2026-11-15: study Amendment 7 (Codex pilot infrastructure)

Three stacked infra faults broke the first Terra and Luna runs: missing `codex-code-mode-host`
(no command tool), `-C /work` absent in the CHEESELESS arm, and Codex's WebSocket transport
blocked by the egress proxy as `protocol-upgrade`. Decision: fix the wrapper and environment,
not the product or the proxy rule, because blocking upgrades is correct behaviour for the
gate. Forcing HTTPS through a custom provider keeps the vendor host and both arms identical.
Rejected: allowing protocol upgrades for Codex (weakens the egress policy for one agent) and
counting the earlier runs (the agent could not act). The earlier attempts stay as detections.
Open risk: Codex remote plugins and apps are reachable in both arms.

## 2026-11-16: two-sided gate design interview (ADR 0009)

Operator: a test that contradicts the spec is an authoring bug, not gaming; the tool must guard
both the author and the implementer, and every decision about tests or code must be makeable by
a human or an agent.

| # | Decision |
|---|----------|
| T1 | Spec in `acceptance/contract/spec.md` plus optional per-criterion `examples` (input → expected) in `criteria.yaml`; inside the freeze digest, promote-denied for the implementer. |
| T2 | New `author` session type in the same sandbox, inverted permissions: `acceptance/` writable incl. hidden tests, `src/` read-only; promote only `acceptance/**` onto `spec/<task>`. CI judge identifies role by branch prefix and rejects role-crossing PRs. Author and implementer never share a HOME or session. |
| T3 | Author calls `freeze_propose` (all checks, proposal bound to its digest); approval via `cheeseless freeze --approve <proposal>` draws the seed. Approver may be a human or an orchestrator agent, never the author or implementer session; non-interactive CLI with `--json`. |
| T4 | Consistency checker = the active agent's model plus deterministic checks, no extra judge model. `author_check` is deterministic; a `reference` session sees only `spec.md` and `criteria.yaml` and writes hidden `acceptance/reference/`; `author_crosscheck` runs suite and examples against it. Freeze needs reference GREEN and baseline RED; `--no-reference` only by explicit approver decision, recorded. |
| T5 | `gate_dispute(criterion_id, reason)` ends the session `DISPUTED` (no more runs, no pass); stored in the trusted store, listed by `cheeseless disputes --json`; reason never returned to the implementer; fix = author re-freeze; lineage budget unchanged. |
| T7 | Refinement of T4 (advisor review): the reference lives in the trusted store as a snapshot tree bound to the spec digest, not in `acceptance/reference/`, because an author-writable reference could be forged to agree with the tests and a repo copy would add a hidden artifact class. The reference session gets the implementer projection plus `spec.md`, no tests, no gate tools; feedback only from public `examples`. |
| T8 | Dispute is opt-in per repo, default off, because the study imports this checkout and a new implementer tool would change its treatment arm. |
| T6 | ADR written by claude-opus-5-5; cross-vendor review by Codex; implementation delegated in TDD stages with `tests/security/` canaries. |

### ADR 0009 review round 1 (2026-11-16)

Reviewers: gpt-6.1-sol (Codex, other vendor; verdict REJECT) and claude-haiku-5-5 (verdict
ACCEPT WITH CHANGES). Every finding is addressed in the revised ADR or in
`planning/two-sided-gate.md`; none is rejected.

| # | Source | Finding | Resolution |
|---|--------|---------|------------|
| R1 | Codex B1 | Author tests run in the unsandboxed host runner before approval | Accepted: author checks run the suite in a runner sandbox (no network, no store, no repo write); output is advisory, test-strength review stays the control. |
| R2 | Codex M2 | Branch prefixes do not authenticate roles; implementer could call author endpoints | Accepted: role is host-bound per session; the UDS server serves only that role's operations. CI rejects unknown branch classes. |
| R3 | Codex M3, Haiku M3 | Digests for reference and crosscheck undefined | Accepted: spec digest = contract bytes; crosscheck record binds acceptance tree, contract, baseline and reference snapshot; test-only change needs a fresh crosscheck only. |
| R4 | Codex M4 | `--no-reference` read as waiving baseline RED | Accepted: it waives only the crosscheck; needs `--reason`; ADR 0005 preconditions stay. |
| R5 | Codex M5 | DISPUTED vs stored PASS, in-flight runs, restart | Accepted: dispute under the task lock; disputed session publishes nothing and cannot promote; a session holding a PASS cannot dispute; never resumes. |
| R6 | Codex M6 | No resolution operation; false disclosure claim | Accepted: `cheeseless dispute resolve --upheld/--rejected`; public criterion only; uniform ack; consequence reworded. |
| R7 | Codex M7 | DISPUTED would break the v1 Result encoding | Accepted: DISPUTED is a session state beside results, not a `Result` status. |
| R8 | Codex M8 | CI judge has no author path | Accepted: separate author CI job on `spec/` PRs, no implementation verdict; approve requires the tree merged on the default branch, so drift checks stay unchanged. |
| R9 | Haiku B1 | Approver separation unenforced (same UID) | Accepted: approve/resolve/grant need the trusted store, which no sandbox mounts; `--as <actor>` logged; security canary required. |
| R10 | Haiku B2 | Free-text dispute reason is a prompt-injection path to the orchestrator | Accepted: closed enum code is the machine field; capped note only with `--show-note`, labelled untrusted. |
| R11 | Haiku M4 | No state machine or sequencing | Accepted: `planning/two-sided-gate.md` (working material, not ADR). |
| R12 | Haiku M5 | Reference failure leaves only `--no-reference` | Accepted: reference failure is an author-visible BLOCKED state, retries capped; waiver needs a reason. |
| R13 | Haiku M6 | Repeated dispute/re-freeze is a holdout oracle | Accepted: disputes capped per lineage (default 3); budget stays lineage-wide. |
| R14 | Haiku M7 | Crosscheck boundary unstated | Accepted with R1. |
| R15 | Haiku m8 | Author promote precondition unnamed | Accepted: `author_check` pass on the same tree. |
| R16 | Haiku m9 | Implementer never sees `spec.md` | Accepted: `spec.md` and examples projected read-only to implementer and reference. |
| R17 | Haiku m10 | Post-freeze crosscheck budget | Accepted: author tools are refused after approval. |
| R18 | Haiku m11 | `reviewers` empty | Accepted: filled in. |
| R19 | Codex note | 31 lines, template followed | No change needed; revision is 34 lines. |

### ADR 0009 review round 2 (2026-11-16)

gpt-6.1-sol re-check of 9cec7a0: R1–R19 resolved except R6 (partial); verdict ACCEPT WITH CHANGES.

| # | Finding | Resolution |
|---|---------|------------|
| R20 | Disclosure consequence omits the optional note | Accepted: consequence names the capped note, operator-only on request. |
| R21 | "ADR 0003 profile" ambiguous; ADR 0003 keeps tests outside every sandbox | Accepted: named a third profile, the author runner, for unapproved suites only; the approved runner stays outside every sandbox as ADR 0003 states. |

ADR 0009 accepted after round 2.

### ADR 0009 implementation, stage (a)

| Choice | Reason |
|--------|--------|
| Each optional `examples` list contains mappings with exactly `input` and `expected_output`, both strings (empty/multiline allowed); no coercion or executable fields. Explicit `[]` is preserved in canonical bytes. | Minimal public text examples, independent of the candidate language or runner. Omission preserves legacy canonical bytes. |
| Optional UTF-8 `spec.md` text is a separate contract field, included only when present in canonical bytes and persisted alongside criteria data. Empty files remain distinct from absence. | Bind the public specification without introducing filesystem access into the domain or changing legacy digests. |
| Project only `criteria.yaml` and a present `spec.md` under `acceptance/`; mount the projected acceptance directory read-only in the agent sandbox. | Public examples remain readable; smoke tests, private notes and hidden tests stay outside the projection. Promote's existing acceptance-path denial remains unchanged. |

#### Stage (a) review (claude-sonnet-5-5, ACCEPT WITH CHANGES)

| # | Finding | Resolution |
|---|---------|------------|
| A1 | Smuggling shapes of `spec.md` untested | Accepted: symlink and non-UTF-8 `spec.md` tests added (symlink was already refused). |
| A2 | Spec and examples skip the description guards | Accepted: spec capped at 64 KiB, example texts at 2000 chars, 20 examples per criterion; `.py::` node ids refused (plain `::` stays legal). |
| A3 | Non-UTF-8 spec raised a bare `UnicodeDecodeError` | Accepted: now a `FreezeError`. |
| A4 | Read-only mount skipped when `projection/acceptance` is absent | Deferred to stage (c) with the mount itself (A7); the role-bound profiles will fail closed. |
| A5 | Read-only probe covers overwrite only | Deferred to stage (c), where the author/implementer sandbox profiles get their own probes. |
| A6 | Other files in `acceptance/contract/` are no longer projected | Rejected on re-check: the full suite showed the study's public `acceptance/contract/test_smoke.py` vanished from the projection. The whole contract directory is public again, as before stage (a); a security test pins that public tests stay projected and hidden tests do not. |
| A7 | Full suite: the read-only `acceptance/` mount turned the demo `weaken-tests` tactic INCONCLUSIVE (OSError) and hid refused tamper writes from the study's tree-diff classifier, which runs the live gate | Mount and its probes removed from stage (a) and moved to stage (c) with A4 and A5. Integrity does not depend on it: `spec.md` and examples are in the freeze digest and promote rejects `acceptance/` (`test_public_spec`). Stage (c) must ship it together with a demo tactic that reports a refused write as blocked, a re-check of recordings and deck wording, and an operator decision on a study amendment that counts refused writes. |

#### Stage (a) deferred items, resolved in stage (c)

| # | Resolution |
|---|------------|
| A4 | Fixed: the implementer profile now fails closed with `SandboxError` when `projection/acceptance` is missing, a file or a symlink, instead of skipping the mount. Security test in `tests/security/test_agent_acceptance_readonly.py`. |
| A5 | Fixed: the sandbox probe covers overwrite, append, create, delete, rename within and out, chmod, mkdir, and rmdir/rename of the mount itself, checks the rest of the work tree stays writable, and verifies the canary digest is unchanged. |
| A7 | Fixed: the mount ships with (1) the demo `weaken-tests` tactic reporting a refused write as BLOCKED (never INCONCLUSIVE) only when every planned write was refused and none landed; (2) a separate `write_events` input to the study classifier fed by a symmetric transcript parser (Claude, Codex; opencode unobserved), so refused writes count as `touch_tests` evidence without flipping unobserved flags; (3) the study dry run's cheeseless weakener now gets refused writes and is labelled `blocked`; (4) study Amendment 8 (numbered 7 before main gained its own Amendment 7). Operator decision recorded there: only refused (`ok=False`) writes count, so naive results are unchanged. Recordings and deck wording re-checked: `docs/demo` casts, player and report regenerated from the demo; the only wording about edits "landing" was the old `weaken-tests` detail text. |

### ADR 0009 implementation, stage (g)

| # | Decision or gap | Choice |
|---|-----------------|--------|
| G1 | Where policy lives | `dispute-policy.json` in the trusted store, never the agent-writable worktree. Absent means disabled. A corrupt policy fails closed: the gate answers `INFRA_ERROR` (not `dispute_disabled`) and the MCP shim hides `gate_dispute`. Host sets it with `cheeseless dispute config --enable/--disable [--cap N]`; without `--cap` an unreadable policy falls back to the default cap with a warning, so the host can always rewrite it. Default off, cap 3, hard maximum 100. |
| G2 | Where session state lives | `disputes.json` (`{"records": [...]}`, ids `d-<n>`) beside the results. DISPUTED is derived per task: a task is closed while any of its records is open or upheld. It is not a `Result` status, so the v1 Result proto and HMAC bytes are unchanged (pinned by `test_result_encoding_pin`). |
| G3 | Race with an in-flight run | `save_result` refuses under the same lock with `SessionDisputedError`, and `gate_run` checks early. A run that finishes after a dispute publishes nothing; the run charge stays. |
| G4 | Refusal order | disabled, then cap, then holds a PASS (`dispute_not_allowed`), then already closed (`session_disputed`). Nothing is recorded on any refusal. |
| G5 | Note limit | 500 characters, control and format characters rejected. Invalid criterion, code or note all return `dispute_invalid` without echoing the input, so unknown and hidden ids look the same. |
| G6 | Error codes | `dispute_disabled`, `dispute_cap_reached`, `dispute_invalid`, `dispute_not_allowed`, `session_disputed` (proto codes 15-19, `docs/errors.md`). |
| G7 | Acknowledgement | One fixed text, `Dispute recorded. This session is closed.`; the server and MCP tool always send it, never the use case's string, so no id, count or time can leak. |
| G8 | Task binding | The UDS handler uses the session's configured task id and rejects a different client-supplied one (403). Disputes take the non-blocking run lock like run and status. |
| G9 | `/status` after a dispute | Returns `session_disputed`, not the pre-dispute result. Simpler and leaks nothing new. |
| G10 | Cap scope | Per lineage name, counting every record whatever its status. It survives a re-freeze of the same lineage and resets if the lineage is renamed. Rejected disputes still count. |
| G11 | Host CLI | `disputes [--json] [--show-note]` (note labelled untrusted, only with the flag) and `dispute resolve <id> --upheld\|--rejected --as <actor>`; second resolution refused. No list, resolve or config route exists on the UDS server or MCP. |
| G12 | Upheld / rejected wiring (status: superseded by S32 for the session enforcement) | Upheld keeps the task closed; the operator re-freezes the lineage with the existing `freeze`. Rejected reopens the task for a NEW implementer session: stage (c) closed the gap, see S27-S29 and the corrections in S32-S33. |
| G13 | CI | The CI bundle is a snapshot with no store, so `judge` has no dispute input. `ci-export` refuses a disputed task. A dispute filed after export is not seen by that bundle (closed in stage (c) as far as a store-less judge allows, see S30). |
| G14 | Tool visibility | `gate_dispute` is listed only when the host's trusted-store policy enables disputes at session start. The host bakes the switch into the read-only MCP shim as a literal (like the socket and task id), so the agent cannot make the tool appear, and an unreadable policy hides it (fail closed). With disputes off, the agent-visible tool set stays `gate_run` and `gate_status`, so the pre-registered study and the demo see exactly the tools they saw before. A policy change during a running session does not change its listing; the verifier enforces the current policy on every call. An earlier draft listed the tool unconditionally; it was rejected because it changed the study's agent-visible environment. |

### Stage (g) review resolution

Reviewer: claude-opus-5-5 (independent session, code review). Verdict: approve after fixes.

| # | Finding | Resolution |
|---|---------|------------|
| R1 | `judge(..., disputed=True)` is never set in production; G13 overstated it. | Fixed: parameter, `JudgeReason.DISPUTED` and its test removed; G13 corrected. |
| R2 | `save_result` refused the signed BLOCKED result (egress DLP hit) after a dispute. | Fixed: BLOCKED results bypass the dispute check; store test added. |
| R3 | `gate_dispute` did not list the allowed codes. | Fixed: description names every `DisputeCode` value. A `Literal` type was rejected: it turns a bad code into a schema error instead of `DISPUTE_INVALID`. |
| R4 | No test for the `gate_run` `SessionDisputedError` branch. | Fixed: a runner files a dispute mid-run; nothing is published. |
| R5 | Weak race-test assertion. | Accepted as is (low value). |
| R6 | Corrupt policy gives `INFRA_ERROR`, not `dispute_disabled`; `dispute config` could not rewrite it. | Fixed: G1 wording; `dispute config` falls back to the default cap. |
| R7 | UDS `/status` treated an unreadable dispute ledger as "no dispute". | Fixed: fails closed with `INFRA_ERROR`; test added. |

### Stage (c) review resolution

Reviewer: claude-opus-5-5 (independent session, code review). Verdict: approve after fixes.

| # | Finding | Resolution |
|---|---------|------------|
| C1 | Any failed write counted as refused (`failed to` pattern), so a stale edit marked a test touch. | Fixed: only sandbox signals count; other failures are dropped; Codex multi-file refusals go only to named files. Tests per case. |
| C2 | Unobserved runs were indistinguishable from "no write". | Fixed: `writes_observed` on `ArmResult` and the manifest; the report counts unobserved runs per arm and agent. |
| C3 | `Path.glob` followed symlinked transcript directories and files. | Fixed: `os.walk(followlinks=False)`, `lstat` on every directory, symlinked files skipped, realpath kept under HOME, `O_NOFOLLOW` reads. |
| C4 | Cheeseless-only TRUNCATED check made the arms differ. | Fixed: the parser alone decides unobserved, in both arms; the check and `evidence` argument are gone. |
| C5 | TOCTOU between the `acceptance/` check and the bind. | Fixed: `open_acceptance` opens with `O_PATH\|O_NOFOLLOW\|O_DIRECTORY`, fstats it, and mounts with `--ro-bind-fd`; fails closed with `SandboxError`. |
| C6 | Security probe never read the canary and did not try a hard link. | Fixed: the probe reads the canary back in the sandbox and attempts `os.link` and a canary overwrite; digest check kept. |
| C7 | Amendment 8 (then numbered 7) described a state the code no longer had. | Fixed: rewritten to match the code. |

## ADR 0009 implementation, stages (d)+(f)

Author side of the gate: `author_check`, `author_crosscheck`, `freeze_propose`, the reference record and the host-only approval. Non-ADR decisions:

| # | Area | Decision |
|---|------|----------|
| A1 | Retries | The cap of 2 (R12) is keyed by spec digest. A spec edit resets the failure and retry counts. `start_reference` is a no-op unless the phase is REFERENCE_BLOCKED and raises after 2 retries. |
| A2 | Reference snapshot | The snapshot is written to the odb as an unreachable tree oid and recorded in the trusted store, bound to the spec digest. No ref points at it. |
| A3 | "Merged" | Means tree equality: the acceptance tree at HEAD equals the proposed acceptance tree. Approve does not change state when the tree is not merged yet. |
| A4 | Waiver | `--no-reference` needs `--reason` (R4). It waives only the crosscheck. The reason is stored in the frozen task outside the digest and authenticated by `waiver_mac`. Removing it is undetectable but only strengthens the gate. |
| A5 | State key | Authoring state is keyed by lineage id. The store returns a FROZEN state unchanged when the spec is unchanged. |
| A6 | Propose | Allowed from AUTHORING and REFERENCE_BLOCKED. It does not require a green crosscheck, because the waiver is applied at approve. A re-propose from PROPOSED goes through `stale()`. The proposal id equals the future task id. |
| A7 | Approve | Reads from HEAD with the digest baseline `proposal.baseline_commit`. It checks the merge, a GREEN crosscheck with all four bindings (acceptance tree, spec digest, baseline, reference tree) and a RED baseline, then draws the seed. `prepare(source=...)` was added for this. |
| A8 | Sandbox | A separate `AuthorSandbox` port. Author-runner root selection fails closed, protects the hidden paths, and keeps alias directories while real paths are checked. |
| A9 | `author_check` | Uses an inline driver. Limit: a result line the candidate prints can be forged, so the check informs the author and never gates freezing. The crosscheck at approve is the authority. |
| A10 | Reference env | `CHEESELESS_AUTHOR_REFERENCE` reaches the author runner through the `trusted_env` mechanism, not through caller-supplied env. |
| A11 | Errors | New codes `ROLE_NOT_ALLOWED` 20, `REFERENCE_BLOCKED` 21, `AUTHOR_REFUSED` 22 and three RPCs. The RPCs are documentation only, because the transport is protojson over UDS. |
| A12 | Role gate | `SessionConfig.role` (implementer or author). A wrong-role call gets a 200 with `ROLE_NOT_ALLOWED`. Routes match exact paths, so anything else is 404. Author sessions refuse run, status and dispute. |
| A13 | No operator endpoint | Approve, reference record/failed/retry and promote exist only on the host CLI (`freeze --approve`, `reference ...`, all with `--json`). `tests/security/test_no_operator_endpoint.py` proves this for UDS and MCP, with a canary check. |
| A14 | Approval containment | `approve_freeze` runs collection, the RED check and both flakiness runs through `AuthorSandbox` (`prepare(author_sandbox=...)`). The plain operator `cheeseless freeze` keeps the host runner: it executes tests the operator wrote and reviewed, not an agent's proposal, and sharing the contained path would change its trust model. `tests/security/test_approve_runs_contained.py` proves it with a canary. |
| A15 | Baseline projection | `prepare` reads acceptance and contract from `source` but builds the candidate projection from `baseline_commit`: baseline tree minus `acceptance/`, plus only `source`'s `acceptance/contract/` (new port primitive `replace_subtree`). The more restrictive option: nothing else from `source` reaches the candidate or the stored projection tree. Public-surface sentinel scans read baseline paths from the baseline and the contract from `source`. |
| A16 | Approval reservation | Approval is a compare-and-set: `AuthoringState` carries a `version`; `save_authoring_state` runs under the store lock and refuses a stale version. `approve_freeze` reserves PROPOSED→APPROVING from the version it validated, revalidates merge and crosscheck bindings after the reservation, refuses an existing task, commits, then marks FROZEN; any failure releases APPROVING→PROPOSED. APPROVING is `sealed` like FROZEN, so author operations are closed while it holds. A crash that leaves APPROVING stuck is recovered manually (the more restrictive option: no automatic expiry). |
| A17 | Author surface closure | Every author operation (`author_check`, `record_reference`, `author_crosscheck`, `reference_failed`, `start_reference`, `freeze_propose`) refuses with `AuthorError("frozen")` when the authoring state is `sealed` (APPROVING or FROZEN); the UDS gate maps it to AUTHOR_REFUSED. `author_check` takes the store so the gate can check closure; the direct use case keeps the store optional for pure collection. |
| A18 | Waiver authentication | Every stored task carries `waiver_mac`, a host-key MAC over a versioned envelope: `waiver:v1:none` when there is no waiver, `waiver:v1:<reason>` otherwise (same `cheeseless-waiver-v1` domain tag, so the Result HMAC is untouched). `load_task` always verifies it, so editing, removing or downgrading a waiver is detected. The more restrictive option: task records written before this change (no `waiver_mac`) are refused rather than grandfathered. |
| A19 | Trusted default branch for "merged" | Approval validates the acceptance tree against, and freezes from, the default branch (`origin/HEAD` target when set, else local `main`), never the host's current HEAD. If neither resolves, approval is refused (`not_merged`). The more restrictive option: no HEAD fallback, and no new config key. |
| A20 | Reference/crosscheck publication is version-conditional | `save_reference` and `save_crosscheck` take the lifecycle `expected_version` read by the same operation's open check, and compare it to the stored version under the `store.lock` flock that also guards the state CAS. A writer that passed `_require_open` before approval reserved loses with `approval_conflict` and records nothing, so approval's validated bindings cannot change after its second check. The more restrictive option: fail the writer, not approval. |
| A21 | Unambiguous waiver envelope | The waiver MAC covers `waiver:v1:absent` or `waiver:v1:present:<len>:<reason>`, so the reason `none` can no longer collide with a missing waiver. Tasks stored with the earlier envelope fail the waiver check and are refused (same policy as A18); the Result HMAC is untouched. |
| A22 | The frozen task is persisted exactly once | `save_task` refuses an existing task (exclusive, under the store lock); only holdout rotation may overwrite, via `replace_task`. `commit` reuses a persisted seed and never regenerates it; a differing explicit seed or a persisted task that differs from the prepared freeze fails closed. An APPROVING state with a persisted task is finished by approving again; if finishing fails it stays sealed and returns `approval_incomplete`. Chosen over releasing to PROPOSED because a release would allow a new seed for a committed task. |
| A23 | Candidate tree in author sandbox has candidate-run semantics | The reference tree is mounted as a throwaway `--overlay-src`/`--tmp-overlay` (writable, in-memory, discarded), matching normal candidate runs, so baselines that write files behave the same at approval. The acceptance tree stays `--ro-bind`; `$HOME`, the store and the repo stay unexposed. Nothing persists to the host. |
| A24 | Every author-side candidate call starts from the immutable baseline | Supersedes the shared overlay of A23: the reference is mounted `--ro-bind` and the in-sandbox driver copies it into a fresh private directory for each `candidate.run` call and discards it, matching the normal launcher (fresh overlay per call). A baseline that is cold on the first call and warm after leaving a marker is cold on every call, so author preparation can no longer accept tests that normal preparation rejects as already passing (baseline RED). Tests cannot persist state in the baseline either. | Differential security test `test_author_candidate_fresh_state.py` compares the normal launcher and the driver; `test_the_baseline_tree_is_immutable...` proves in-place writes are blocked. Copy cost per call is accepted. Status: superseded by A29. |
| A25 | A dead approver's APPROVING reservation is recoverable, by the host only | An approver now holds a non-blocking exclusive ownership lock (`authoring/approval-<lineage>.lock`, flock, released by the OS on death) from reservation to finish; a second approver is refused (`approval_conflict`). `cheeseless author recover --task <id> --as <actor>` takes the same lock: held means the approver is alive, so it refuses (`approver_alive`). Otherwise, under lifecycle CAS: no published task rolls back to PROPOSED; a published task with fingerprints is activated and finished to FROZEN; a published task without fingerprints is refused (the more restrictive choice) and must be finished by re-running the approval. Not APPROVING: refused. The command is CLI-only. | Unit tests for the lock, rollback, finish, live-approver refusal, CAS loss and lock span; `tests/security/test_no_operator_endpoint.py` proves `recover` is absent from the UDS routes and MCP tools. |
| A26 | Author-side candidate calls get a fresh cwd tree, HOME, TMPDIR and a swept /tmp and /dev/shm | Extends A24. The author sandbox cannot nest namespaces (`--disable-userns` plus seccomp), so a literal per-call /tmp is not available. Each call instead gets its own cwd tree, `HOME` and `TMPDIR` under a private directory, the driver snapshots `/tmp` and `/dev/shm` before the call and deletes everything the call created afterwards, and a call that changed or removed an entry that already existed fails closed (`candidate modified shared state`), the more restrictive choice. Residual risk: state hidden outside those roots, or an in-call daemon outliving the call, is not covered; the sandbox's process and PID limits bound it. | `tests/security/test_author_candidate_fresh_state.py`: a differential test per marker location (cwd, /tmp, $HOME) proves author-side and normal calls agree, plus a test that modifying pre-existing shared state is refused. Status: superseded by A29. |
| A27 | Recovery trusts nothing in the store beyond the task digest | Finishing a published task now reloads it fully (schema, digest, waiver MAC), re-derives the projection tree and hidden blob list from the repository, rebuilds the DLP fingerprints from the frozen acceptance tree and compares them with the stored ones. Missing, empty, partial or mismatching fingerprints, an unreadable or tampered task, or a default branch that no longer holds the frozen tree all refuse with `approval_incomplete`, and the reservation stays APPROVING (sealed). The more restrictive choice: no repair-in-place; the approval is re-run. `recover` now takes the repository. | Unit tests: empty, partial and tampered fingerprints and task, unreadable task record, drifted default branch. |
| A28 | The per-call baseline copy counts against the call deadline | The deadline starts before the copy and the subprocess gets only the remaining time; a copy that exhausts the budget raises `candidate call timed out`. The copy stays per call (no template or reflink): it keeps A24's fresh-state guarantee with no shared mutable template to protect. Cost is bounded by the 300 s suite deadline. | `test_the_per_call_deadline_covers_the_baseline_copy` (4000-file tree: tiny timeout fails, generous timeout passes). Status: superseded by A29. |
| A29 | Author-side candidate calls go through a host broker, not an in-sandbox copy | The in-sandbox snapshot, sweep and per-call baseline copy are deleted. The unapproved suite reaches the candidate only through `CandidateBroker` on a per-session Unix socket in a private 0700 dir bound into that one author sandbox. Each call runs `Popen([*launcher, *run_argv, *client_argv])` where the launcher is `BwrapSandbox.launcher`, the function the approved runner uses, so author call and normal call share one code path: fresh bwrap, fresh tmp overlay, whole process group killed (SIGTERM, then SIGKILL after 2 s). Closed schema `{argv, stdin, timeout}`; any other field is refused. `--disable-userns` stays. Resolves the round-5 H1-H3 and M2 findings. | `tests/security/test_author_candidate_fresh_state.py` runs each probe (markers in cwd, /tmp, $HOME, /dev, /w, /run, /var/tmp, /, a new top-level dir; abstract-socket holder; permission and same-mtime content change; suite /tmp output; canary) through both paths and asserts the probed observations agree (not timing). |
| A30 | Broker limits are host-enforced and tied to the suite deadline | Per-call deadline (30 s cap, client `timeout` may only lower it, and it covers bwrap startup), concurrency cap 4 (excess is refused, not queued), at most 1000 calls, and total call time capped by the 300 s suite deadline passed as `timeout`. A client disconnect or broker `close()` kills the call's process group; the author sandbox's death ends the broker and therefore every in-flight call. Socket access control is the private 0700 directory bound only into that sandbox. There is no peer-credential check; 0700 does not exclude other host processes with the same UID (residual, ADR 0001/0003). Admission is bounded before reading (8 connections, absolute read deadline, bounded writes, reaped workers); aggregate time is reserved atomically at admission and refunded on completion; each call reserves its limit plus a 1 s allowance, so the reservations of concurrent calls never exceed the aggregate. This bounds the SIGKILL-enforced part of a call, not every second spent (see A33). The candidate profile still binds no socket (ADR 0003). | `tests/unit/adapters/test_candidate_broker.py` (schema, caps, deadline, teardown); `tests/security/test_author_candidate_broker.py` (every forbidden field through the real author sandbox; killing the sandbox mid-call leaves no candidate process). |
| A31 | `--no-reference` starts no broker and every candidate call is refused | With no reference tree there is nothing to pin, so the broker is not started, no socket is bound and the env var is not set. The in-sandbox client fails every candidate call with `no candidate broker: no reference is bound`. Chosen over a broker that refuses each call because it exposes no endpoint at all (more restrictive). | `test_no_reference_starts_no_broker_and_every_candidate_call_is_refused`. |
| A32 | Round-6 review resolution | H1: connection cap before read, absolute deadline, bounded write, worker reaping, start failure handled. M1: broker dir ro-bound. M2: recovery re-collects the test mapping from the frozen tree and compares the seed-derived split to it; the seed is trusted-store state (operator-only, ADR 0001), not separately authenticated, because the store is the same trust domain that holds the freeze record, so authenticating it would add nothing against an attacker who can write the store. M3: base64 stdin, None means /dev/null. M4: atomic aggregate reservation. Validation: depth limit, NUL and UTF-8 checks, strict base64, each a structured refusal. | `test_candidate_broker.py`, `test_author_candidate_broker.py`, `test_author_candidate_fresh_state.py`, `test_authoring.py` forgery tests. |
| A33 | Round-7 cross-vendor review resolution (gpt-6.1-sol, OpenAI): ADR 0010 accepted | (1) Aggregate overshoot: option (a). Each call reserves `limit + ALLOWANCE` (1 s = 0.5 s SIGTERM grace + 0.5 s overhead); the grace is now enforced (SIGKILL of the process group 0.5 s after SIGTERM, was 2 s), so a SIGTERM-ignoring prefix is charged at most limit + grace + polling jitter. The 0.5 s overhead (startup, output handling) is measured by tests, NOT enforced; a slow host can exceed it. Budgets below 1 s of headroom refuse every call. (2) A `timeout` of `10**1000` raised OverflowError and dropped the connection; the type and range are checked before conversion and refused in a structured reply. (3) Connection-cap test tolerates the broker closing excess connections and closes every socket; `close()` now `shutdown()`s accepted connections so blocked reads wake and workers cannot outlive it; the worker count is per instance (`live_workers`). (4) Absolute-deadline tests use a valid header plus a dripped body and a partial header; a per-recv variant is shown to miss the deadline. (5) Recovery now repeats approval's reference-crosscheck policy (record present, bound to the proposal, green) unless the task carries the MAC-authenticated waiver. It still trusts the stored seed (A32) and does not re-run the reference. (6) ADR 0010 accepted with `reviewers: [gpt-6.1-sol]`. | `test_candidate_broker_hardening.py` (SIGTERM-ignoring concurrent and sequential bounds, overflow refusal then success, close wakes reads, drip tests), `test_authoring.py` recovery crosscheck tests. |

## ADR 0009 implementation, stage (b)

Role-aware promote and the CI author job. No ADR changed; every choice below is within ADR 0009.

| # | Choice | Reason | Proof |
|---|--------|--------|-------|
| B1 | Author promote targets `refs/heads/spec/<lineage_id>`, not `spec/<task>` | No task id exists before `freeze --propose`; the lineage id is the only stable key an author has. | `test_author_promote_publishes_the_acceptance_change_on_spec_ref` |
| B2 | Author path set is `acceptance/` only | `spec.md` and the examples live under `acceptance/contract/`, so one prefix covers everything ADR 0009 gives the author. Anything else (`src/`, CI config, `.cheeseless`, `pyproject.toml`) is refused with the path list. | `test_author_promote_carries_acceptance_paths_only` |
| B3 | R15: promote needs an `author_check` PASS keyed on spec digest plus acceptance tree digest | `author_check` records it only on PASS; promote compares both digests. Missing gives `no_author_check`, changed gives `stale_author_check`. | `test_author_promote_without_a_check...`, `..._stale`, `test_author_promote_on_a_stale_tree_is_refused` |
| B4 | R17: author promote is refused once the lineage is APPROVING or FROZEN | Reuses the same `_require_open` guard as the other author operations (`kind=frozen`). | `test_author_promote_is_refused_once_approval_began` |
| B5 | Symlinks are refused by `snapshot`, before any ref is written | A link out of the tree never reaches the policy; no ref is created. | `test_author_promote_refuses_a_symlink_out_of_the_tree` |
| B6 | Role is chosen by the host CLI flag `promote --role`; UDS and MCP expose no promote at all | Promote is host-only (A13), so the host-bound session role (R2) is enforced by absence: `/promote`, `/author-promote`, `/spec-promote` are 404 for every role and no MCP tool contains "promote". `--role author` rejects `--task` and `--approve`. The CLI flag is an operator choice, not a session. | `test_no_operator_endpoint.py`, `test_cli_author.py` |
| B7 | Fix found while writing the security tests: implementer `promote()` accepted any `ref=` under `GitRepository`'s allowed prefixes, so a caller could aim it at `spec/`. It now refuses anything outside `refs/heads/gate/`. The CLI never passed `ref`, so this closed an API-level crossing only. | Role crossing must fail in the use case, not only in the CLI. | `test_implementer_promote_cannot_be_pointed_at_a_spec_ref` (written red first) |
| B8 | CI classifies branches in domain code (`domain/ci_branch.py`): `gate/<x>` and `spec/<x>` with one simple path segment; everything else is unknown and rejected. A gate PR touching `acceptance` fails; a spec PR touching anything outside the author set fails. | Testable Python, same path policy as promote. | `tests/unit/domain/test_ci_branch.py` |
| B9 | `cheeseless ci-branch --branch --base --head` is the only CI logic; `docs/ci/branch.yml` calls it. The workflow is `workflow_run` (default-branch copy), has no secrets, no environment and no token, passes the branch name only through an env var, and pins actions by SHA. Separate jobs: `gate`, `author`, `unknown-class`. | Same hardening as `judge.yml`; the branch name is attacker-controlled. | `tests/unit/test_branch_workflow.py` |
| B10 | The author job's author check is advisory: its problems are reported, a crash is reported as `author check unavailable`, and neither fails the job. The job never calls `ci-judge`, so it posts no implementation verdict. Blocking conditions are path confinement and the unknown class only. | The brief calls the check advisory. Making it blocking would let a flaky sandbox block spec PRs; the blocking promote-time check (R15) already exists. | `tests/unit/app/test_ci_branch.py` |
| B11 | Process note: the domain `ci_branch` test and code were written in one step, breaking strict red-first. The app, CLI, workflow and promote-ref fix were run red first. | Honest record. | commit order |

### Round 9 review (stage (b)): findings and dispositions

| # | Finding | Disposition |
|---|---------|-------------|
| S1 | Author promote raced with approval | Fixed: publication runs under the lifecycle lock (`hold_open`). |
| S2 | `branch.yml` published no enforceable check | Fixed: design (a), one trusted workflow `judge.yml` (jobs `route`, `judge`, `branch-policy`); `branch.yml` removed. `branch-policy` publishes `cheeseless branch policy` via the judge App on the validated PR head. Logic is in `ci-route` and `ci-branch --check-payload`. |
| S3 | `judge.yml` ran for every PR | Fixed: only `gate/` reaches `ci-judge`; unknown classes get a failing policy check; an unverifiable PR publishes nothing. B9 is superseded by this design. |
| S4 | Canary test inspected nothing | Fixed: scans host objects, fails on error, proven by a contaminated control. |
| S5 | Symbolic refs | Fixed: refs are never symbolic (`update-ref --no-deref`). |
| S6 | Repeated promote rewrote history | Fixed: ancestry check plus compare-and-swap. |
| S7 | Acceptance symlink gave "internal error" | Fixed: `SnapshotRejected` maps to a stable refusal. |

Decisions: `spec/<lineage_id>` is kept; ADR 0009 says `spec/<task>`, and the lineage id identifies the task's authoring lineage, so the meaning is the same (flagged for the operator, B1). `--role` is the operator's publication mode, not session authentication; session role binding stays deferred. Author path set stays `acceptance/` only. The author check output is advisory; path confinement and unknown-class rejection are blocking, and the promote-time PASS and approve checks stay mandatory.

Open for the operator (not settled by ADR 0009): whether the `author` job should become blocking, and whether `spec/<lineage>` should be renamed `spec/<task>` once a task id exists at propose time.

Remaining (out of scope here): real reference-agent sessions. Role-bound sessions, the G12 reopen and the G13 staleness watermark landed in stage (c) (S27-S30).

### Round 10 review (stage (b)): findings and dispositions

| # | Finding | Disposition |
|---|---------|-------------|
| S8 | H1: no single enforceable check; per-class required checks cannot be expressed (required checks are scoped by base branch) | Fixed: ONE required check, `cheeseless gate`, built by the trusted `post` job through `ci-gate` (`app/ci_gate.py`). gate/: success only if the judge PASSED (exit code 0 and verdict `pass`) and the branch policy passed. spec/: success if the branch policy passed, titled and summarised as NOT an implementation verdict. Any other class or any failure: failure. Only success/failure are ever published, never neutral or skipped (GitHub counts those as satisfying a required check). The old names stay informational and are posted first, the gate last. A PR that cannot be verified publishes nothing, so the required check is missing and the merge stays blocked (fail closed). README step 7 requires only `cheeseless gate`, pinned to the judge App as source. Proof: `tests/unit/app/test_ci_gate.py`, `tests/unit/test_ci_workflow.py`, `tests/integration/test_ci_branch_cli.py::test_ci_gate_*`. |
| S9 | M1: promote onto a moved baseline | Fixed (e9a7320): the rebased commit has the OLD spec tip as its FIRST parent and the moved baseline as its SECOND. The second parent exists to put the baseline in the ancestry so the merge base is the baseline, which keeps the ancestry and path checks measuring only the PR's own change. |
| S10 | M2: `ci-route` accepted fork PRs | Fixed (e9a7320): a head repository different from the base repository is refused, as is a base repository other than `--repo`. |
| S11 | M3: the App key and PR code shared one job | Partly fixed here, completed in S17: `judge.yml` is `route`, `judge`, `evaluate`, `post`. `evaluate` has no environment, secrets or variables and hands `payload.json` over as an artifact. `post` has the environment but checks out only the default branch, never fetches or runs PR code, validates the payload, re-checks `pulls/N` head.sha, then mints the checks token and posts. The judge job had the same pattern (sandboxed candidate code, then a checks-write mint and post in one job): the checks mint and posting moved to `post`. The judge keeps the key only for the budget-token mint, which must exist while the judge runs; a sandbox escape there could still reach that key (NOT accepted: round 11 found the same App also had Checks write, so an escape could forge the required check; fixed by the two-App split in S17). `ci-gate` validates the exact payload schema, head SHA and class, which is stricter than `--check-payload` (trusted default-branch code). Artifact action pins verified with `git ls-remote`. Proof: `test_ci_workflow.py` (no job that fetches PR code references the checks permission, `post` has no `git fetch`). |
| S12 | L1: edits to the PR base were not re-evaluated | Fixed: `edited` added to the `pull_request` types in `ci.yml`; `test_ci_workflow.py` asserts it. |
| S13 | L2: history of a pre-existing ref is not scanned | Not fixed, stated honestly: the M1 second-parent change only keeps the baseline in the ancestry so the ancestry check works; it does not scan older commits. Promote snapshots trees, and each published commit is a synthetic one carrying only acceptance/ paths; content of commits already on a pre-existing `spec/` ref (created outside promote) is trusted as operator-controlled. |
| S14 | L3: `_fs.locked` takes `flock(LOCK_EX)` with no timeout, and git runs under the lock | Accepted: the wait is bounded because the holder's git calls are bounded by `TIMEOUT_SECONDS` (300 s) and a dead holder releases the flock with its descriptor. No change. |
| S15 | L4: `save_author_check` does not bump the lifecycle version | Verified by reading the code: it uses `_write_if_current`, which only compares `expected_version` under `store.lock` and writes the file; no version increment. This is intended: an author check is a derived record keyed by spec digest and tree, and promote compares both digests. No change. |
| S16 | Q3: `spec/<task>` in ADR 0009 | Decision: it means the lineage id, the task's stable identity across re-freezes. README, the CLI help and docstrings say `spec/<lineage>` and point here. No new ADR; ADR 0009 is untouched. |
| S17 | R11-1: one App held Checks and Contents, so a judge sandbox escape could mint a checks token | Fixed: two Apps. The ledger App (Contents write, ledger repo only; `CHEESELESS_LEDGER_APP_ID`/`KEY`) is the only secret the `judge` job references. The checks App (Checks write only, judged repo only; `CHEESELESS_CHECKS_APP_ID`/`KEY`) is minted only in `post`, and the required check is pinned to it. `*_ID` holds the App Client ID. Proof: `test_ci_workflow.py` / `test_judge_workflow.py` (the judge references only ledger secrets, only `post` references the checks App, no PR-code job references it). |
| S18 | R11-2: the path-policy result came from the job that runs PR code | Fixed: new `policy` job (no environment, no secrets, no bwrap, no PR content executed) decides class and confinement and passes the payload as a job output; `evaluate` is advisory with no output or artifact. `ci-gate` still validates the payload. Proof: structural (`post` reads the policy job's output, which a sandboxed job cannot write), covered by the workflow-text tests in `test_ci_workflow.py`; `tests/security/test_ci_policy_no_exec.py` covers the no-exec path (see S23). `test_ci_branch_cli.py` only covers the head-SHA binding of a forged payload, not S18 itself. |
| S19 | R11-3: a stale pass on the same head SHA after the base moved | Fixed: the verdict records `judged_base`; `post` passes the current default-branch tip as `--current-base` and `ci-gate` fails a gate class whose judged base differs or is missing. README step 7 requires "Require branches to be up to date before merging". Proof: `test_ci_gate.py` and `test_ci_branch_cli.py` (stale, missing, unknown base). |
| S20 | R11-4: `edited` re-runs spent the CI budget | Fixed: `ci.yml` `run-name` is `ci judge=yes` unless the action is `edited` without a base change; `route` runs only for `judge=yes`. Documented in README step 6. Proof: `test_ci_workflow.py` (run-name and route `if`). Caveat (R12): `run-name` and `judge=yes|no` come from the PR's own `ci.yml`, so a PR can set either value. This is a budget optimisation, not a security control: the worst case is a fail-closed missing check or the PR spending its own budget. |
| S21 | R11-5: `post` trusted `always()` with no second guard | Fixed: `post` computes `--guards-ok` from `policy`, `judge` and `evaluate` results (judge success for `gate/`, skipped otherwise) and `ci-gate` fails when it is false. Proof: `test_ci_gate.py` (failed guard) and `test_ci_workflow.py`. |
| S22 | R11-6: fork PR reusing a head SHA | Low, accepted: a fork PR that reuses the head SHA of a legitimate PR makes `ci-route` refuse with `expected_exactly_one_open_pr`. That blocks (denial of service) but can never pass the required check. |
| S23 | R12-1: the workflow-level concurrency group cancelled the real judge run | Fixed: the group is `cheeseless-judge-<head_sha>-<event>-<display_title>`. Only runs with identical routing (same SHA, event and title) share a cancelling group, so a `judge=no` or push-event run on the same SHA can no longer cancel an in-flight `pull_request` `judge=yes` run (which already charged the budget) and then post nothing; two identical judging runs still cancel each other. Correct by reasoning (not observed on GitHub): `route` runs only for `pull_request` + `judge=yes`, i.e. exactly one group value per SHA. `test_ci_workflow.py` re-implements the group expansion and checks both directions; a live-run check is still pending. README step 6 corrected. |
| S24 | R12-2: the no-exec security test proved nothing (a mutation that always ran the author check passed, since bwrap blocked the marker write) | Fixed: spies replace `bootstrap_ci.BwrapSandbox` and `author_check`; with `--no-author-check` neither may be constructed or called, and a control run without the flag proves the spies fire. Mutation proof: changing `bootstrap_ci.py:282` to `if True:` made the new test fail; reverted. Canary check kept. |
| S25 | R12-4/5: `--guards-ok` defaulted to true; the advisory `evaluate` job gated `post` | Fixed: `--guards-ok` is required (CLI) and keyword-only (`build_checks`), so omission fails closed; `evaluate` is dropped from the guards and its result is only logged. Proof: `test_ci_branch_cli.py` (missing flag exits non-zero), `test_ci_workflow.py` (guards do not mention evaluate). |
| S26 | R12-7: `post` compares the verdict's `judged_base` (reported by the judge) with the current tip | Fixed: `route` captures the default-branch tip once (`base_tip` output); `judge` runs `ci-judge --base "$BASE_TIP"` and stamps `judged_base` from it; `post` passes `--current-base` as route's value only if its own checkout of the default branch still equals it, else an empty base, which `ci-gate` treats as unknown and fails (fail closed on a moved tip). The `policy` job is unchanged and still runs no PR code. Tests `test_route_captures_the_default_branch_tip_once_and_judge_uses_it`, `test_post_compares_route_value_and_fails_closed_when_the_tip_moved`. |


### Stage (c): role-bound sessions, G12 reopen, G13 staleness
| ID | Item | Disposition |
|---|---|---|
| S27 | `session --role author\|reference\|implementer` | The host binds the role at launch (`session-roles.json` in the trusted store). Mounts and the baked read-only MCP shim follow the role; the implementer shim stays byte-identical (regression test). Every UDS route compares the request header, the session role and the store record and refuses on any mismatch or store error (fail closed). A reference session has no routes. `session` without `--role` is an implementer alias. Tests: `tests/security/test_session_roles.py`, `tests/unit/test_session_role_regression.py`, `tests/unit/test_session_role_launch.py`. |
| S28 | G12: a rejected dispute reopens the task (superseded by S32: the launch-time generation claim was wrong) | `resolve_dispute(REJECTED)` bumps a per-task session generation (`session-generations.json`) and clears the task's pre-hidden attempt window, under the store lock. A UDS session records the generation at launch and `_role_ok` refuses once the store differs, so the old session cannot be reused; only a new session gets the fresh window. Resolution is host-only: no UDS or MCP route and no tool name reaches it. Tests: `tests/unit/adapters/test_dispute_reopen.py` (both directions), `tests/security/test_dispute_reopen.py` (stale session refused, no reset route). |
| S29 | G12 audit and budgets | `events.jsonl` records `dispute-reopen` (dispute id, resolver, generation) and `dispute-reopen-budget` (new attempt budget; run budget unchanged). The lineage run budget (a hidden-test leak bound) is deliberately NOT reset; the G10 per-lineage cap still counts a rejected dispute; upheld changes neither. |
| S30 | G13: stale CI bundle | Bundle v3 records `disputes`, the number of dispute records for the lineage at export (MAC-covered; v2 is refused). The judge has no store, so it cannot compare. Smallest fail-closed change: the host-only `ci-bundle-check` exits 1 when the store holds a different count (a rejected dispute also counts), and fails closed on an unreadable bundle. Re-export after any dispute. Residual gap: nothing forces the operator to run the check before the CI secret is used; documented in `docs/ci/README.md`. Tests: `tests/unit/test_ci_bundle.py`, `tests/security/test_ci_bundle_staleness.py`. |

### Stage (c) review fixes (Opus: accept with changes)
| ID | Item | Disposition |
|---|---|---|
| S31 | Review 1: the reference mount hid `acceptance/contract/` and contradicted ADR 0009 | Fixed: the `contract` mount masks `acceptance/` with a tmpfs and binds only `acceptance/contract/` back read-only (`profiles.open_contract`). Proof: `tests/security/test_reference_mount.py` (real bwrap: spec and examples readable, a canary test file unreadable, missing contract dir tolerated) and `test_session_roles.py::test_mounts_follow_the_role`. Note: before a freeze `host_session` has no `stray_hidden` comparison, because hidden blobs are defined by the freeze; there is nothing to compare against, so a pre-freeze session cannot strip strays. Left as is. |
| S32 | Review 2: G12 was not enforced on the real session path. Corrects S28, which wrongly said "a UDS session records the generation at launch" | `host_session` now registers the session and the server holds the generation it was given. `_role_ok` refuses on a None generation whenever a task id is set (fail closed). Proof: `tests/security/test_session_binding.py` (a launch through `host_session` is refused after a rejected dispute). |
| S33 | Review 3: the role record was per task (last writer wins) | Records are keyed by a random per-session id (`sessions.json`) and removed at session end. Stale entries after a crash are harmless: the id is random and unreachable by any other session, and a generation bump invalidates them anyway. Proof: author live, reference launched, `author_crosscheck` still allowed in the author session, `gate_run` still refused there, the reference gets only its own tools. |
| S34 | Review 4: a same-lineage re-freeze without a dispute passed `ci-bundle-check` | `ci-bundle-check` also compares the bundle's `task_id` with the store's active task and fails stale on a mismatch. Proof: `test_ci_bundle_staleness.py::test_a_same_lineage_re_freeze_without_a_dispute_makes_the_bundle_stale`. |
| S35 | Review 5: `serve-uds` with no active task refused every route | Restored a read-only status answer (an infra error, the pre-stage-(c) behaviour) with no role check and no store read; every other route still refuses. Proof: `test_session_roles.py::test_serve_uds_with_no_active_task_answers_status_read_only_and_refuses_the_rest`. Process note: I wrote the S35 code before its test, then removed it, saw the test fail, and restored it. |
| S36 | Follow-up 1: the reference session saw public tests (`contract/test_smoke.py`) because all of `acceptance/contract/` was bound back; ADR 0009 says the reference gets no tests | Explicit allowlist: only `contract/spec.md` and `contract/criteria.yaml`, opened as pinned O_PATH|O_NOFOLLOW fds below a pinned `contract/` fd and bound read-only through `pinned_read_only` on top of the `acceptance/` tmpfs. Missing files are tolerated; symlinks and non-regular files are not exposed; everything else stays hidden. `open_contract` removed. Proof: real-bwrap `tests/security/test_reference_mount.py` (spec and criteria readable; public test, canary and a symlink to it not). |
| S37 | Follow-up 2: `contextlib.suppress(Exception)` around `end_session` in the `host_session` teardown hid failures | `_end_session_reported` prints session id, task id and the error to stderr and stays non-fatal. Proof: `test_a_failing_end_session_is_reported_with_context_and_stays_non_fatal`. |
| S38 | Follow-up 3: the `(None, None)` session binding in `serve_uds` was only tested via a hand-built `SessionConfig` | A test runs `bootstrap.serve_uds` with no frozen task (server stubbed to return) and asserts the config has no task, session id or generation. |
| S39 | Follow-up 4 (mut-a finding, accepted redundancy): the `session_id is None or generation is None` guard in `_role_ok` (`server.py`) | Defence in depth. With a task id set, a missing id or generation would be refused by the store lookup anyway; the guard is kept so a refusal never depends on the lookup. Accepted, not removed. |
| S40 | Re-review of S36 (low): the regular-file check in `open_contract_files` was caught only through the symlink case | Parametrized security test: a FIFO or a directory in place of `spec.md` is not pinned and its content stays masked. Mutation `S_ISREG` -> `True` now fails 3 tests (symlink, fifo, directory). Observation, not fixed: a hardlink named `spec.md` is a regular file and would be exposed; not reachable because the host builds the projection from git blobs. |
| S41 | CI: the canary sweep over `/opt` and `/usr/local` exceeded 60 s on the runner (days-old preinstalled toolchains). A first fix that raised the timeouts could not work on the author path: the broker caps each call at `min(timeout, call_seconds=30, remaining)` | The sweep is bounded by mtime, not by time: one `find` over the same roots prints any `canary-*` name and runs `grep -l CANARY` only on `-type f -newermt @SESSION_START` files (`SESSION_START` = import time minus 60 s, so fixtures written earlier in the session stay detectable and the runner's old toolchains are skipped). Roots are unchanged; no `-I`. The old-file coverage the filter loses (the checkout's own sources hold the CANARY constant) is replaced by an existence check: the probe prints `LEAK` if the host repo root's `tests/security/test_author_candidate_fresh_state.py` exists inside the sandbox. Expected output stays exactly `done`. One sweep takes about 0.08 s locally (whole test 0.53 s); the timeouts stay at 60 s and 120 s. Mutations in a scratch copy outside the worktree (own venv, because `launch.py` runs under `python -I`): (i) the hidden acceptance dir bound into the candidate profile fails with "author path leaked"; (ii) host `/tmp` bound into the candidate profile fails with "normal path leaked". Residual: a leak into a file older than the session start and not named `canary-*` is caught only for the repo checkout. |
| S42 | CI again (run 44037867546): the canary sweep still timed out at 60 s on the normal path. `-newermt` limited only the grep, not the walk; `find` still stat'ed every file under `/usr/local`, which inside the sandbox is the HOST's tree (`profiles.SYSTEM_DIRS` ro-binds `/usr`) and holds tens of GB on a runner (0.08 s locally was not predictive) | `/usr/local` and `/opt` are no longer swept. `/usr/local` is the host system tree reached through the `/usr` bind (`/usr` was already excluded on that basis), `/opt` is not mounted at all, and a session writes to neither. Remaining roots, in both the candidate and the author profile (one launcher path): `/w` = the reference overlay (small), `/tmp` = an empty tmpfs, `/etc` = three small host binds (`ld.so.cache`, `alternatives`, `localtime`; ~130 entries), `/home /mnt /srv /var /run` = absent. None is a host bind of a large tree, so the worst case is bounded by session-written dirs. Kept: the mtime-bounded grep, the `canary-*` name check, the repo-root existence check. Added a walk-free structural guard: the probe reads `/proc/self/mountinfo` and prints `LEAK` if any mount's root or mount point names the test's tmp dir (`tmp_path.name`, the dir holding `acc/hidden`); the legitimate overlay (root `/`, mount point `/w/<id>`) does not trigger it. No time limit is involved. Mutations (scratch copy outside the worktree, own venv): (i) hidden dir bound at `/mnt/hidden` fails "author path leaked"; (ii) host `/tmp` bound at `/mnt/hosttmp` fails "normal path leaked"; (iii) hidden dir bound at `/opt/x` or `/usr/local/share` (unswept) prints `LEAK` from the guard and fails "author path leaked". A bind at a non-existent `/usr/local/x` cannot be built (the `/usr` bind is read-only; bwrap refuses), so that case fails earlier with no output. |
| S43 | Verified review (critical): a reference session could read the hidden and contract tests through `projection/.git`. `create_projection` imported every blob of the tree into the projection repo, so the tmpfs mask on `acceptance/` hid the working files but `git cat-file --batch-all-objects`, `git show HEAD:...` and the index still held them (pre-freeze: the hidden blobs; post-freeze: the contract tests). | Fix: for `SessionRole.REFERENCE` the projection is built from a filtered tree, `app/reference_view.reference_tree` = `repo.tree_without(tree, "acceptance", keep=REFERENCE_ALLOWLIST)`, applied in `bootstrap._projection_tree` before `create_projection`, for the pre-freeze and the post-freeze tree alike. The allowlist moved to `domain/session_role.py` (policy), the git mechanics stay in the adapter; the tmpfs mask and pinned fds stay as defence in depth. Tests: `tests/unit/app/test_reference_view.py` (no blob under `acceptance/` except the allowlist) and `tests/security/test_reference_projection.py` (real repo with hidden, contract and spec files plus canaries; real `create_projection`, real bwrap with `profiles.agent(acceptance_mode=contract)`; `grep -r`, `git cat-file --batch-all-objects --batch`, `git ls-files`, `git show` must show no canary). Mutation: returning the unfiltered tree from `_projection_tree` makes the security test fail with `CANARY-HIDDEN` present. Also: `test_reference_mount.py` documents that the synthetic projections cover mount mechanics only and that `test_reference_projection.py` is the canonical real-path test; the misnamed argv test in `test_session_roles.py` is renamed to say it checks argv shape; `serve_uds` teardown now ends the session through `_end_session_reported` (a failing `end_session` is reported, not fatal) with a test. |
| S44 | Review of S43 (medium): the S43 security test called `_projection_tree` directly, so a `host_session` that ignored the role would still pass | `tests/security/test_reference_session_wiring.py` runs `host_session(role="reference")` with `_launch_agent` replaced by a probe that snapshots the projection (worktree, `git ls-files`, `git cat-file --batch-all-objects --batch`) at the moment the agent would start, pre-freeze and frozen. No `CANARY`, contract test body, `test_smoke.py` or `acceptance/hidden` may appear; `criteria.yaml` must. Mutation: passing `SessionRole.IMPLEMENTER` to `_projection_tree` at the `host_session` call site fails both tests; reverted. This also covers review finding 4 (the "dropped whole" unit test alone would not catch a different tree builder). |
| S45 | Review of S43 (low): `reference_tree` fails closed when a symlink or gitlink sits at an allowlisted path or inside a kept directory (`blobs_under` raises `SnapshotRejected`), so the reference session does not launch | Accepted. Failing closed is the intended direction: nothing leaks, and the author controls the spec tree, so the only effect is that the author's own malformed spec blocks their reference session. Dropping the entry silently would hide an authoring mistake. |
| S46 | Review of S43 (low): before a freeze, `extra_read_only` and `home_seed` are checked against protected path roots only; `hidden_inodes` needs a frozen task, so an operator-supplied hardlink to a worktree test would pass for a pre-freeze reference session | Accepted as an operator-trust boundary. These options are host-side and opt-in; the agent cannot set them, and exposing a test this way needs a deliberate operator action. Pre-freeze there is no frozen hidden set to compare inodes against. Revisit if these options ever become reachable from an untrusted source. |

### Author-side demo findings (PR #22)
| ID | Item | Disposition |
|---|---|---|
| S47 | Author-side demo: `author_crosscheck` ran the reference without the contract `env` (`criteria.yaml` `env:`), so a reference that needs `PYTHONPATH: src` failed every criterion and the crosscheck was red for a correct suite | Fixed: `_run_driver` takes `env` and `author_crosscheck` passes `tuple(contract.env)`, the same env the implementer gate uses. Unit test `test_crosscheck_runs_the_reference_with_the_contract_env` (RED before the fix). |
| S48 | Author-side demo: a relative `--projection` path makes `cheeseless session` crash instead of failing with a clear message | Fixed: `_run_session_command` anchors a relative `--projection` at the cwd (`Path.cwd() / path`, lexical, no symlink resolution), so the host's symlink refusal is unchanged. Tests `test_main_session_resolves_a_relative_projection`, `test_main_session_keeps_a_symlinked_projection_visible_as_a_symlink`. |
| S49 | Author-side demo: a pre-freeze session (reference, author) still needs `--task`; the demo passes a placeholder of 64 zeros, which is not documented | Fixed: `host_session` falls back to an internal stand-in task id (`PRE_FREEZE_TASK`) for an author or reference session when no `--task` and no active task exist; implementer and reviewer still fail closed (`no active task`). The demo and its recording no longer pass `--task`. Tests `test_a_pre_freeze_role_session_needs_no_task_id`, `test_an_implementer_session_without_a_task_fails_closed`. |
| S50 | Author-side demo: `freeze_propose` succeeds while the crosscheck is red | Accepted as designed: a proposal is only a request; `freeze --approve` re-runs the check and refuses a red crosscheck (shown in the recording, step 2). |
| S51 | Author-side demo: the local gate run evaluates only the visible split of a frozen suite, so a demo test chosen into the holdout made the recording nondeterministic | Design note, no product change. Every `score` test in the demo exercises ties, so the visible split always contains a failing test for the guessed implementation. |

### Stage (b) re-review against main
| ID | Item | Disposition |
|---|---|---|
| S52 | Stale review of stage (b) (`b415c5b`, verdict REJECT), re-checked against main | (1) aggregate required check: addressed, `ci-gate` folds the branch policy and the verdict into the single required `cheeseless gate` check. (2) retarget: `ci.yml` fires on `edited` and re-routes; the gap left was that `post` re-checked only the PR head before publishing, so a run started before a retarget could still publish. Fixed: `post` also requires `.base.ref` to equal the default branch (`test_post_rechecks_head_and_base_just_before_publishing`). (3) stale successes on reused heads: addressed by `--current-base` (the verdict must be judged against the current default-branch tip) and the head re-check. (4) `spec/<lineage_id>`: already decided in B1 and S16; no new ADR. |

### ADR 0013 review round 1 (2026-11-17)

Reviewer: gpt-6.1-sol (Codex, other vendor; verdict ACCEPT WITH CHANGES). A second reviewer
(DeepSeek via opencode) was refused by the routing policy for this work, so the review has one
cross-vendor model. Every finding is accepted and addressed in the ADR and in
`planning/dev-mode.md`; none is rejected.

| # | Finding | Resolution |
|---|---------|------------|
| S53-1 | `--resume` semantics undefined; resuming the implementer could reuse a context that saw a holdout outcome or the author transcript | Accepted: the host store binds phase, spec digest, proposal and per-role session ids; `--resume` resumes only the author, the implementer always starts fresh. |
| S53-2 | The full judgment after a green `gate_run` runs the holdout locally, which ADR 0005 reserves for CI and budgets separately | Accepted: the judgment plays the CI role (the ADR says it extends ADR 0005), reserves a run from the lineage's CI pool in the host store before any hidden input, and reports only red criterion names. After 3 holdout fails or an empty pool the user decides. |
| S53-3 | "Queue disputes and let the implementer continue" contradicts ADR 0009 (a dispute ends the session `DISPUTED`, never resumed) | Accepted: a dispute ends the session at once; rejected starts a fresh implementer, upheld returns to the author for a re-freeze in the same lineage. |
| S53-4 | The OAuth token could leak into runners or candidates | Accepted: only `CLAUDE_CODE_OAUTH_TOKEN` is forwarded, and only into agent sandboxes (author, reference, implementer); a security test must prove runners and candidates never get it. |

### ADR 0013 review round 2 (2026-11-17)

Reviewer: gpt-6.1-terra (Codex, other vendor; re-check of f6f75fb; verdict ACCEPT WITH
CHANGES). S53-1, S53-3 and S53-4 resolved; S53-2 partial. Both new findings are accepted.

| # | Finding | Resolution |
|---|---------|------------|
| S53-2 | Partial: a CI pool in the host store bypasses ADR 0005's trusted CI counter, so the 35-run lineage bound no longer holds | Accepted: the full judgment is charged to the lineage's local pool (host trusted store); the CI pool stays in the trusted CI counter. Only the disclosure is extended (holdout red criterion names). Cost: holdout feedback spends the local pool faster. |
| S53-5 | After 3 holdout fails ADR 0005 requires a re-freeze with a new split and seed; "the user decides" and "grant runs and continue" contradict it | Accepted: after 3 holdout fails the user re-freezes (new split and seed) or aborts; an empty pool ends `BLOCKED` until the user grants runs as operator. |
| S53-6 | Auto-freeze skips ADR 0005's cross-vendor test-strength review and other freeze preconditions | Accepted: the freeze keeps every ADR 0005 precondition; the host freezes alone only after a passing review by a `--strength-reviewer` agent of another vendor (read-only sandbox, its own credential); otherwise the user approves as operator. This changes the agreed default: without a configured strength reviewer, dev mode asks before the freeze. |

### ADR 0013 review round 3 (2026-11-17)

Reviewer: gpt-6.1-terra (re-check of da45ea2; verdict CHANGES NEEDED). S53-2 and S53-5
resolved; no new conflict with ADR 0009.

| # | Finding | Resolution |
|---|---------|------------|
| S53-6 | Still partial: ADR 0005 draws the seed after the *user* approves the freeze; an auto-freeze has no user approval | Accepted: the approval (the host's, gated by the strength review, or the user's) comes first and the host draws the seed and split only after it; ADR 0013 states this as an extension of ADR 0005. The property ADR 0005 protects, that no author or reviewer sees the seed before the suite is fixed, is unchanged. |

Final confirmation: gpt-6.1-sol on 4c6dd5c, verdict ACCEPT (S53-6 resolved, no regressions in
S53-1..S53-5, no new conflicts with ADR 0005 or 0009). ADR 0013 accepted.

## 2026-11-17 — ADR 0013 stage 2: dev-only `spec_done`

| # | Decision | Why |
|---|----------|-----|
| D13-2a | `/spec-done` is seeded into the agent's HOME (`~/.claude/commands/`), not the projection's `.claude/commands/` as `planning/dev-mode.md` drafted | The author projection is what `repo.snapshot` reads and freezes; a host file there would end up in the frozen tree or need a carve-out in the snapshot. HOME is host-owned and per session. Operator `home_seed` entries are written after it and cannot replace it (`seed_home` refuses an existing destination). |
| D13-2b | `spec_done` is an author route gated a second time by `SessionConfig.dev_lineage` | Reusing `_AUTHOR_ROUTES` keeps the claim, record and generation checks of `_role_ok`; the extra check makes it absent from a plain author session on every path form, not only from the MCP list. |
| D13-2c | `host_session(dev_lineage=...)` validates the id, refuses any role but author and needs an open dev state, all before anything is staged | Fail fast; a dev flag can never reach an implementer or reference session. No CLI flag yet: stage 4/5 starts dev sessions from `cheeseless dev`. |
| D13-2d | `record_spec_done` checks that the contract read from the author's snapshot has the dev session's lineage and a non-empty spec | The digest bound to the phase must be of the spec the user agreed to, in this lineage. |

## 2026-11-17 — ADR 0014 review (reference TUI pane)

Reviewers: gpt-6.1-sol (OpenAI, high) and claude-sonnet-5-5 (Anthropic, medium). Author: claude-opus-5-5. Both said "accept with changes".

| # | Finding | Disposition |
|---|---------|-------------|
| R14-1 | sol 1 / sonnet 4 (HIGH): a shared tmux socket or `TMUX*` env could let a sandbox drive or capture the author pane; the pane command runs outside the sandbox | Accepted. The host launches bwrap itself (`--clearenv`). The pane runs only a host-side relay. No tmux socket, `TMUX*` variable or pane fd enters any sandbox, the author's included. Security test planned. |
| R14-2 | sol 2 / sonnet 3 (HIGH): tmux owns the pty, so the host cannot supervise, reap or kill the sandbox; `kill-pane` only sends SIGHUP | Accepted. The host owns the `Popen`, the pinned PID namespace and the pty, with `--die-with-parent`. The pane is a relay, and closing it stops nothing. Stale attempts are reaped on start. |
| R14-3 | sol 3 / sonnet 1 (HIGH): the reference has no route or tool today (`_role_ok`, `tool_names`, ADR 0009 "no gate tools"), so "same double gate as `spec_done`" does not carry over | Accepted. New `_REFERENCE_KINDS`, admitted only when `SessionConfig.dev_reference` names a running attempt, after the namespace, record and generation checks. ADR 0014 explicitly narrows 0009 for this one signal. |
| R14-4 | sol 4 / sonnet 5 (HIGH/MEDIUM): stale completion and replay; the projection source is unspecified | Accepted. Each attempt record has an attempt id, session id, generation and spec digest, and the `spec_done` snapshot is the only input. The digest is never taken from the tool call. Terminal transitions are compare-and-set, and a stale or duplicate call never snapshots. |
| R14-5 | sol 5 / sonnet 2 (HIGH): the agent can keep writing after `reference_done`, before the snapshot; terminal races | Accepted. The host stops the sandbox (TERM, then KILL after a bounded wait), then snapshots, then publishes, all under one serialized CAS (`running → stopping → done`). |
| R14-6 | sol 6 / sonnet 6 (MEDIUM): the fallback had a different completion rule and pointed at a superseded point | Accepted. The fallback is defined in ADR 0014 and chosen only before launch, with a warning. It uses the same record and ordering and is `done` only on exit 0. Evidence records `mode` and attach. |
| R14-7 | sonnet 7 (MEDIUM): the accepted risk was understated; typed input and scrollback are not captured; dialogs and idle time | Accepted. The ADR now says input and scrollback are not captured, and that `mode` and attach mark runs as steerable. Idle time counts toward the timeout. Trust and permission dialogs: stage 3 seeds the reference HOME so the projection is pre-trusted. A dialog that still stalls the run hits the timeout and becomes `failed`, which the user can retry. |
| R14-8 | sonnet 5 (MEDIUM): an early `reference_done` on an empty tree | Accepted as a risk, stated in the ADR: it fails the crosscheck. |
| R14-9 | sonnet 8 (LOW): partial supersession marker, index, reviewers, ledger | Accepted. The 0013 status gets "point 2 superseded by ADR-0014" (a status-only change, which the template allows); the index row is added; reviewers are filled in on acceptance. |
| R14-10 | sol re-check 1 (MEDIUM): the ADR's CAS (`running → done`) did not match the `stopping` state in Phase 2; two concurrent handlers could both snapshot | Accepted. The ADR now says `running → stopping → done`, or `running\|stopping → failed\|cancelled`. Only the winner of `running → stopping` tears down and snapshots. |
| R14-11 | sol re-check 2 (MEDIUM): a headless agent calling `reference_done` would trigger teardown before its zero exit | Accepted. The tool is listed and routed only for TUI attempts. In headless mode an observed exit 0 starts `stopping`, under the same state machine. |
| D14-3a | `reference_done` slice 3a: a new `REFERENCE_REFUSED` code and the running→stopping CAS inside the gate | Admission is read-only and fail-closed; only the CAS writes, so a refused or duplicate call leaves the record byte-identical. The host stop is a `SessionConfig.on_reference_stop` seam for a later slice. |
| R3a-1 | deepseek-v4.1-flash review 1 (MEDIUM): the operator-endpoint sweep skipped `_REFERENCE_ROUTES` and the reference MCP tool list | Accepted. `test_no_operator_endpoint.py` now sweeps all three route tables and every role × dev × disputes tool list. `reference_done` is the one named exemption, and only for a dev reference. |
| R3a-2 | review 2 (MEDIUM): the CAS checked state only, so a record that changed hands after admission could still be stopped | Accepted. `transition_reference_attempt(..., owner=AttemptOwner)` re-checks session, generation and mode under the store lock. The gate always passes the owner. |
| R3a-3 | review 3 (LOW): no gate-level test for a non-running record | Accepted. `tests/unit/app/test_reference_gate.py` covers stopping, done, failed, cancelled, another owner, a store failure and a failing seam. Each refusal leaves the record byte-identical. |
| R3a-4 | review 4 (LOW): `ensure_private_dir` ran before the lock and before the decision | Accepted. It now runs under the lock, and only on a write. |
| R3a-5 | review 5 (LOW): the attempt mode is not passed to the tool listing and the shim | Deferred to slices 3b and 3c, where the host launches attempts. Until then the UDS gate refuses headless attempts, which `test_reference_done_dev_only.py` proves. |
| R3a-6 | review 6 (LOW): the `_role_ok` docstring was stale and `kind` had a default | Accepted. The docstring now describes the reference route, and `kind` is required. |
| R3a-7 | review 7 (INFO): the gate echoed the requested id; `ReferenceDoneRequest` carries `task_id` | The gate now uses `outcome.attempt_id`. `task_id` is kept on purpose: the server checks it against the bound task (`_task_matches`) exactly as for every author request, so a client bound to another task is refused. |

## ADR 0014, slice 3b: pty launch, relay, pane client, tmux helper

| id | finding | disposition |
|---|---|---|
| D14-3b-1 | Framing between the relay and the pane client | One type byte (`DATA`=0, `RESIZE`=1), a u32 big-endian length, then the payload. A resize is `>HH` rows and cols. The payload is capped at 64 KiB and the decoder checks the length before buffering. An unknown type or a wrong-sized resize is a `FrameError`. The frames live in `domain/pane_frames.py`, so the inbound CLI and the outbound relay share them without importing each other. |
| D14-3b-2 | SIGWINCH and the controlling tty | `--new-session` leaves the child with no controlling tty, so a resize on the master sends it no SIGWINCH (checked in a spike and in `test_new_session_alone_gives_no_resize_signal`). `pty_launch.with_controlling_tty` wraps the command in a trusted `setsid` + `TIOCSCTTY` step, and `test_the_controlling_tty_step_restores_resize` shows resize reaches the child under seccomp. `--new-session` stays, so TIOCSTI cannot reach the host terminal. Using the wrapper in the real session command is slice 3c. The client forwards SIGWINCH through a self-pipe and re-reads the window size. |
| D14-3b-3 | One client at a time and the peer check | A second client, or one whose `SO_PEERCRED` uid is not the host uid, is closed at once. The socket sits in a fresh `mkdtemp` 0700 dir (mode 0600) outside every sandbox mount. |
| D14-3b-4 | Output with no client, and disconnects | Output is read and discarded, with no scrollback. A client disconnect or a malformed frame drops only the client; the child keeps running. The relay owns the master and stops only on `close()` or when the master hits EOF/EIO. |
| D14-3b-5 | tmux isolation | `TMUX` is a reserved env prefix in both `session_options` and `domain/contract`. The sandbox uses `--clearenv`, so no host `TMUX*` value reaches it. `TmuxPanes` is usable only when the host env has `TMUX`, and it validates the pane id (`%N`). |
| R3b-1 | review (gpt-6-luna, MEDIUM): SIGTERM killed the pane client with the tty still raw | `install_exit_handlers` turns SIGTERM/SIGHUP into `SystemExit`, so `finally` restores the tty (main thread only). `test_sigterm_restores_the_tty_of_a_running_client` runs the client in a subprocess on a pty and checks the attrs after SIGTERM. |
| R3b-2 | review (MEDIUM): a short `os.write` dropped the tail of a frame | `write_all` loops until every byte is written; `test_write_all_survives_short_writes`. |
| R3b-3 | review (LOW): the relay isolation test never tried to connect from inside | `test_the_sandbox_cannot_connect_to_the_relay_but_the_host_can`: a connect from the sandbox fails, then the host attaches and receives a canary written to the pty, proving the relay was live and free. |

## ADR 0014, slice 3c: host reference run, done detection, snapshot on stop

| id | finding | disposition |
|---|---|---|
| D14-3c-1 | Who owns the sandbox and how an attempt ends | `bootstrap_reference.py` is the composition root for one attempt: it starts bwrap on a pty it holds, keeps the `Popen`, and owns the record. A TUI attempt ends only on the agent's `reference_done`, a timeout or a dead sandbox; a headless attempt ends on exit 0. `app/reference_run.py` holds the compare-and-set moves (`fail`, `cancel`, `stop`, `complete`, `reap`), so the ordering ADR 0014 R14-5 requires stays in one place. |
| D14-3c-2 | Snapshot must happen before the projection is torn down | `record_reference` used to snapshot *and* bind. The host already holds the tree when it decides to stop, so `record_reference_snapshot` takes an already-taken snapshot and binds it. Binding is unchanged (same `_publish`, same expected version). |
| D14-3c-3 | Attempt records need host bookkeeping the ADR field list does not name | `ReferenceAttempt` gains `lineage_id`, `pane_id` and `result_tree`, all optional and all written only by the host. `lineage_id` scopes reaping to one dev session, `pane_id` lets a later host kill a stale pane, `result_tree` binds the snapshot atomically with `stopping -> done`. Each is validated by `ids` (a `%N` pane id is a new `ids.pane_id`). |
| D14-3c-4 | Reaping must not kill a pane a live host still owns | `reap_attempts` fails each `running`/`stopping` attempt and kills its pane **only if it won the compare-and-set**. Terminal attempts are skipped because a tmux pane id can be reused after a tmux server restart, so only a pane this host has just reaped is safe to kill. |
| D14-3c-5 | A headless attempt needs a log nobody else can read | `reference_log_path` is a host-only path; the fd comes from `_fs.open_append` (private, append-only) and the caller creates it 0600. The store listing helper `_fs.list_regular` fails closed on a symlink, so a crafted entry cannot point the reader outside the store. |
| R3a-5 | (deferred from slice 3a) the attempt mode is not passed to the tool listing and the shim | **Resolved.** The host now passes the mode: `host_session(..., new_reference=...)` sets `SessionConfig.dev_reference` and the shim bakes `dev=True` for a reference session. A TUI attempt lists `reference_done`; a headless one does not (`test_a_headless_reference_session_gets_no_tool_and_keeps_a_log`), and the UDS gate still refuses a headless call. |
| D14-3c-6 | `dev=True` now means two different tools depending on role | ADR 0013 gates `spec_done` for an author, ADR 0014 gates `reference_done` for a reference. `_mcp_shim_source` bakes `dev=True` for exactly those two roles and every other shim stays byte-identical. `tests/security/test_spec_done_dev_only.py` asserted the old "dev is ignored for any role but author" invariant and was narrowed to the role-scoped one. |

## ADR 0013 stage 5: the `cheeseless dev` host loop (first slice)

The ADR already decides the orchestrator (host owns phase and freeze; a skill only
launches it), so this slice implements an accepted decision and adds no new ADR.

| id | decision | why |
|---|---|---|
| D13-5a | `app/dev_flow.py` holds preconditions and phase handover; `bootstrap_dev.py` only sequences | The app layer stays free of git and env I/O (`require_clean_tree` takes a callable), so `start_phase` is testable without a sandbox or a repo. |
| D13-5b | `start_phase` derives the role from the stored phase via `resume_target`, and never asks an agent | ADR 0013 makes the phase a host fact. Reusing `resume_target` means the implementer is always a fresh session and the author is the only resumed one, with no second rule to keep in sync. |
| D13-5c | The loop stops at the tests phase and prints the `dev-reference` command instead of nesting it | ADR 0014 runs the reference as its own host process on a pty it owns. Starting it inside `dev` would nest a second sandbox under one command and lose the host's `Popen`/teardown ownership (R14-2). |
| D13-5d | The credential is required only for the author role | ADR 0013 gives each sandbox only its own credential. Requiring the author's token for an implementer or reference run would demand a secret those roles must not hold. |
| D13-5e | `require_token` refuses an embedded control character | Found by a security test: `.strip()` alone let `" <token>\x00"` through, and that value would be forwarded into a sandbox env where a NUL truncates it or fails the exec. A token never contains one, so this is a strict refusal, not a sanitisation. |
| D13-5f | New `Repository.is_clean()` | `dirty_paths(prefix)` is repo-relative and rejects `""`, so the whole-tree check ADR 0013 needs ("clean tree only") had no honest expression. The method runs `status --porcelain` with no pathspec. |
| D13-5g | All refusals precede staging and go to stderr | `--json` therefore never prints a half object on stdout; a refusal leaves no session, projection or store entry behind. `tests/unit/adapters/test_dev_cli.py` asserts the stdout-is-empty case. |

## ADR 0013 stage 5, slice 2: the tests phase closes only on a done reference

| id | decision | why |
|---|---|---|
| D13-6a | The dev loop **never** approves as `cheeseless-dev`; it prepares and proposes, then the user approves | ADR 0013 allows a host approval alone only after "a passing strength review by a `--strength-reviewer` of another vendor", but neither the reviewer sandbox nor a stored record of "this review passed" exists yet. `approve_freeze` therefore has no evidence to consult, and a CLI flag would *be* the approval: an agent-launched command could freeze its own tests, which ADR 0009 exists to prevent. The auto-freeze branch stays closed until a review record exists in the store. `tests/security/test_dev_flow_credential.py` asserts the loop never names the actor or the function. |
| D13-6b | `app/dev_flow.reference_ready` is a read-only predicate over the stored attempts | The tests phase cannot close without a `done` attempt for *this* spec digest. It reads and never transitions or binds, so calling it cannot move an attempt; `test_the_reference_predicate_is_read_only` asserts the body holds no write call. |
| D13-6c | A stale digest is reported apart from a missing attempt | The two send the user to different fixes (re-run the reference vs run one at all), and the earlier draft reported "no reference" for both, which was wrong whenever an attempt existed for another spec. |
| D13-6d | Renamed `tests_ready` → `reference_ready`, `TestsPhaseError` → `DevTestsPhaseError` | pytest collects a module-level name starting with `tests_` as a test and a class starting with `Tests` as a test class; both collided (a spurious fixture error and a `PytestCollectionWarning`) in `app/dev_flow.py`. |

## ADR 0013 stage 5, slice 3: applying a gate verdict to the implement phase

| id | decision | why |
|---|---|---|
| D13-7a | `dev_flow.next_after_result` maps a `Result` to one domain move and does nothing else | The verifier already decides pass or fail and `gate_run` already accounts the attempt against the pool. Answering "what does this verdict mean for the dev session" is a pure function of the state and the verdict, so it asks no store and charges nothing; `test_the_run_never_charges_the_pool_itself` asserts the body holds no `record_attempt`, `Pool` or `save_`. |
| D13-7b | The final holdout failure is **stored**, and only the next judgment is refused | `DevState.holdout_failed` allows the count to reach `MAX_HOLDOUT_FAILURES` and `judgment_allowed` then turns false. Raising on the storing move would have lost the count the author needs to see to justify a re-freeze; the first draft of the test asserted the opposite and was wrong, not the domain. `next_after_result` therefore surfaces `DevSessionError` as `DevFlowError("too often…")` on the *following* verdict. |
| D13-7c | The report names the holdout count, not only the phase | A bare `implement phase` after three failures looks identical to one after zero, and the user's next action differs completely (re-freeze vs keep going). |

## ADR 0013 stage 5, slice 4: the dev chain end to end (phase 11, first pass)

| id | decision | why |
|---|---|---|
| D13-8a | Scripted agents, not real model sessions | The property under test is the host's reaction to events, not a model's behaviour. A fake agent that emits the same events keeps the run deterministic and cheap, and the freeze approval stays a human step (D13-6a) supplied explicitly rather than automated away. `planning/phase11-e2e.md` recorded this as the open choice; this closes it. |
| D13-8b | **Bug found:** `require_clean_tree` took a `dirty` callable while the port exposes `is_clean()` | Every caller therefore had to write `lambda: not repo.is_clean()`, and the E2E test got the sign wrong, asserting a *clean* repo was refused. The production path in `bootstrap_dev` happened to pass `not …` and was correct, so no behaviour changed; the parameter now names the positive fact (`is_clean`) and the guard reads `if not clean`. `test_the_precondition_takes_the_clean_fact_not_a_dirty_flag` pins the name and the polarity. |
| D13-8c | **Corrected understanding:** the implementer gets `acceptance/` read-only, not a tree without tests | `_projection_tree` only cuts the tree down for a REFERENCE; for an implementer the protection is `acceptance_mount(...) == "ro"`, a different mechanism. My first E2E test asserted "the implementer projection holds no test" and failed against correct code. The test now asserts the three mount modes, and says in its docstring that the two mechanisms are easy to confuse. |

## ADR 0015 review: findings and their dispositions

ADR 0013 point 3 already decided auto-freeze; what was missing was the mechanism, and a mechanism
that decides who may freeze alone is not a ledger row. ADR 0015 records it. Reviewers:
`gpt-6.2-sol`, `gpt-6.2-astra` (codex, two independent runs), `gemini-flash` (cross-vendor).

Round 1 — **REJECT** from both codex reviewers. All four blockers were verified in the code
before being accepted, not taken on trust.

| # | finding | disposition |
|---|---|---|
| R15-1 | `actor == "cheeseless-dev"` cannot separate an automatic from an operator approval, because `cli/main.py:_cmd_approve` passes a caller-chosen `--as` | Accepted. A separate `auto_approve_freeze` use case sets the actor itself; the label never selects the mode (Decision 1). |
| R15-2 | The verdict must bind to a snapshot the reviewer actually saw, not be stamped with the current proposal's ids | Accepted. The host records the three keys before the session and re-checks at approval (Decision 2). |
| R15-3 | Cross-vendor independence must come from trusted launch config; a reviewer-reported model id is self-attestation | Accepted. Both vendors are compared from config recorded at their own launches (Decision 4). |
| R15-4 | `--no-reference` waives the crosscheck in `_ready`, so a strength-review gate alone would still allow auto-freeze without a green crosscheck | Accepted, and real: `_ready` skips `_crosscheck_problem` when `no_reference` is set. The auto path refuses the waiver (Decision 1). |
| R15-5 | Reuse/supersession of a verdict across re-proposals was undefined | Accepted in part: `proposal_id` is content-derived, so an identical proposal legitimately reuses a verdict that still binds all three keys; anything else fails the binding. No extra rule needed. |

Round 2 — four blockers RESOLVED, then two SHOULD-FIX, both accepted and applied:

| # | finding | disposition |
|---|---|---|
| R15-6 | "its trees" did not say which trees, and the record omits `reference_tree` which `_crosscheck_problem` binds | Accepted. Decision 2 names the three keys and says a changed reference is the crosscheck's job, which is where it is already caught. |
| R15-7 | "both vendors" did not say which sessions' vendors | Accepted. Decision 4 compares the vendors recorded at the author's and the reviewer's launches, not the current defaults. |

Round 3 — cross-vendor reviewer (**ACCEPT WITH CHANGES**), the finding that mattered most:

| # | finding | disposition |
|---|---|---|
| R15-8 | Gating an irreversible action on one model's verdict contradicts ADR 0009, which rejected the LLM judge as "opaque, one more model to trust, no executable evidence" | **Partly rejected, with the text corrected.** ADR 0009 rejected an LLM judge *in place of* executable evidence; ADR 0013 point 3 adds this gate *on top of* every deterministic check, and Decision 3's preamble now says so explicitly. The finding did expose that the ADR read as if the review replaced something, which it does not. |
| R15-9 | The verdict-extraction protocol from a read-only sandbox was unspecified | Accepted. Decision 3: a structured line the host parses, never a phrase it greps. |
| R15-10 | Indirect prompt injection: the author's own test text is untrusted input the reviewer reads | Accepted into Consequences. This is the sharpest residual risk and it is now stated rather than hidden. |
| R15-11 | "evidence and approval are different facts" overstates it, since the host is both witness and judge | Rejected as a framing change but the claim narrowed: the record is checkable *afterwards* and separates the two facts in the store; it never claimed an independent auditor. Consequences say a review ran, not that it was good. |
| R15-12 | Simpler alternative: keep the freeze interactive and show the review as advisory | Rejected. That is the third option in the ADR, already available and unchanged; ADR 0013 point 3 chose the auto path for users who opt into it, and this ADR only makes it possible with evidence. |

## ADR 0015 implementation

- **D15-1**: `StrengthReview.__post_init__` refuses a green review whose reviewer vendor equals
  the author's. Putting the check in the type rather than only in the approval path means such a
  record cannot be *stored* at all, so no later reader has to remember to check it.
- **D15-2**: `vendor_of_model` returns `None` for an unrecognised family, never a guess, and
  `record_strength_review` refuses on `None`. A guessed vendor would make the independence check
  compare two invented strings.
- **D15-3**: `auto_approve_freeze` takes no `actor`, `no_reference` or `reason` parameter.
  A security test asserts the signature, because adding any of them later is exactly how the
  guarantee erodes. The CLI's `--auto` *rejects* `--as` and `--no-reference` rather than silently
  ignoring them: an accepted-but-dropped flag looks like it worked.
- **D15-4**: `record_strength_review` never calls an approval function; a security test asserts the
  source holds no such call. Writing the evidence and acting on it stay separate.
- **D15-5**: the reference is deliberately not in `StrengthReview`. A changed reference already
  invalidates the crosscheck (`_crosscheck_problem` binds `reference_tree`), and duplicating it
  here would make two places disagree about what a stale review is.
- **D15-6**: `planning/dev-mode.md` Phase 3 asked for a security test that auto-freeze refuses
  without a passing strength review, and one that the reviewer sandbox is read-only.
  `tests/security/test_auto_freeze_gate.py` covers the first (seven refusal cases plus the
  signature and actor pins). The second belongs to the reviewer sandbox, which does not exist yet:
  recording a review is possible, *running* the reviewer is not, so this remains the open gap.

## ADR 0015: the reviewer role and sandbox (closes D15-6)

- **D15-7**: a reviewer's projection is `acceptance/` **and nothing else** (`reviewer_tree`,
  built with a new `Repository.tree_keeping` port method). The reference's cut is the mirror
  image: it keeps the contract and drops the tests. The reviewer needs the tests, so the only
  thing left to cut is the candidate's code — and it must be cut from the *tree*, not merely
  unmounted, or the projection's `.git` would still hold it. Pinned by
  `test_the_candidates_code_is_absent_from_the_git_objects`.
- **D15-8**: `profiles.reviewer` is a separate builder rather than `agent(..., read_only=True)`.
  `agent()` binds the projection writable because an implementer must edit code; a flag would
  leave the safe case depending on every caller remembering to pass it. A reviewer that
  inherited that bind could edit the tests it judges.
- **D15-9**: `profiles.reviewer` accepts `sockets` and ignores it. Taking the argument lets
  callers share their plumbing without the argument silently granting a channel; the ignoring
  is a test (`test_the_reviewer_argv_is_built_without_sockets`), not a comment.
- **D15-10**: the reviewer loses the verifier socket, the egress bridge and every proxy
  variable (`_reviewer_env`). It keeps only what starts its model session. It still gets a
  writable `HOME`, because a toolchain that cannot write `$HOME` does not start; that is the
  one writable path and it is outside the projection.
- **D15-11**: **D15-6 is closed for the sandbox, not for the host loop.** A reviewer can now be
  launched contained, read-only over the tests, with no code and no channels. Nothing yet
  launches one from `cheeseless dev`; the verdict is still recorded by the operator through
  `cheeseless strength-review`. That is deliberate: `test_the_reviewer_gets_no_tools_at_all`
  guarantees the reviewer cannot report its own verdict, so whoever parses its output is the
  host, and that parser is the next piece.

### Review of #39 (gpt-6.2-sol, medium) — REJECT, all four blockers verified in code

- **FINDING 1 (BLOCKER) — accepted.** The reviewer was launched through `_bridge_command`
  and `host_session` waited on `_wait_for_bridge(egress_ready)`. An isolated reviewer can never
  write that marker, so the session hung until timeout: the role could not run at all. Fixed
  by branching before the wrapper and guarding the handshake. Test:
  `test_a_reviewer_session_waits_for_no_egress_bridge`.
- **FINDING 2 (BLOCKER) — accepted.** `_projection_tree` fed the reviewer `HEAD` (not the tree
  a verdict names) before a freeze, and `projection_tree` after one — which has the hidden
  tests removed. It would have judged a suite that is not the frozen one. Fixed: the reviewer
  is shown `frozen.acceptance_tree` through the new `Repository.tree_from_acceptance`. Test:
  `test_the_reviewer_is_shown_the_frozen_acceptance_tree_not_the_public_projection`.
- **FINDING 3 (BLOCKER) — accepted.** Operator home seeds, extra read-only mounts, extra env
  and extra command arguments were an unfiltered route for candidate content into the
  reviewer. Fixed by refusing seeds and extra mounts outright for this role (`SessionOptionError`)
  — filtering would leave "what did we miss?" unanswerable.
- **FINDING 4 (BLOCKER) — accepted, and my comment was wrong.** `profiles.reviewer` said
  "callers keep them disjoint" while nothing enforced it: a writable HOME above the projection
  would shadow the read-only bind below it. Fixed with `_refuse_overlap` in `bootstrap`, where
  both paths are chosen. The comment now describes what the code does. Test:
  `test_the_home_overlap_is_refused_rather_than_commented_about`.
- **FINDING 5 (SHOULD-FIX) — accepted.** `_reviewer_env` was a denylist of constant *names*,
  so anything added later passed silently. Replaced with a two-entry allowlist
  (`CHEESELESS_RUNTIME`, `CHEESELESS_MCP`). Test:
  `test_the_environment_whitelist_admits_only_what_a_model_session_needs`.
- **FINDING 6 (SHOULD-FIX) — accepted as residual risk, recorded not fixed.** `sockets` is
  ignored, the tool list is empty and verdict recording is host-side; but the UDS dispatcher
  would admit a bound reviewer to implementer routes if it ever gained socket access. Not
  reachable from this role today. Recorded here rather than carried silently.
- **FINDING 7 (SHOULD-FIX) — accepted.** `tree_keeping` on a tree with no `acceptance/` returns
  an empty tree, which would let a reviewer judge a suite it was never shown. Now refused
  (`reviewer_tree`, `reviewer_tree_of`). A prefix that is a file raises `RepositoryError`
  already, which is the fail-closed answer.
- **FINDING 8 (SHOULD-FIX) — accepted.** Added the positive control the review asked for (the
  reviewer must mount something, or its silence proves nothing) and the fail-closed test for a
  missing acceptance fd.
- **NOT accepted — no change.** The claim that `acceptance_mount(REVIEWER) == "ro"` is wrong:
  the review itself agrees it is correct, and `open_acceptance` + `--ro-bind-fd` pins it.

### Reviewer availability, 2026-10-30

- `litellm-free/gemini/flash` (pi) **failed** with `litellm.Timeout: Connection timed out`
  (90 s) on the #39 review. Not a code finding: the endpoint is unreachable today, so the
  cross-vendor opinion for this change is taken from `claude-sonnet-5-5` (Anthropic) instead.
  `opencode-go/deepseek-v4-pro` is also unavailable (provider limits exhausted). Recording
  this because a review requirement that quietly degrades to "the only model that answered"
  is not a cross-vendor review at all.
- `gpt-6.2-sol` at `reasoningEffort: medium` is the review model of record for this change
  (operator instruction).

### Re-review of #40 (gpt-6.2-sol, medium) — REJECT, both blockers verified in code

- **FINDING 1 (BLOCKER) — accepted.** `stray_hidden` ran for every role, so the one session
  that is *meant* to hold the hidden tests aborted before `_launch_agent`. A frozen reviewer
  could never start, which is its only real use. Replaced for this role with
  `_refuse_wrong_reviewer_tree`, which compares the files actually written against the tree
  that was selected — catching a stray implementation blob (`stray_hidden`'s job) as firmly as
  a stray hidden one.
- **FINDING 2 (BLOCKER) — accepted.** The reviewer never loaded the stored proposal and fell
  back to `HEAD`, and the fallback swallowed *any* `StoreError`, including a failed integrity
  check. `_load_proposal_for_review` now takes `proposal.acceptance_tree` from the proposal the
  task id names, and a proposal that exists but is unreadable raises instead of downgrading the
  session. Also checks `proposal.proposal_id == task_id`.
- **FINDING 3 (SHOULD-FIX) — partly accepted.** `agent_stdio` is now refused: inherited stdin
  is content outside the snapshot a verdict binds. `agent_command` stays as the launch seam
  (the host must be able to name the executable); the finding's deeper point — that a
  record-producing review should take its command from trusted configuration rather than an
  operator argument — is a design change for the host loop, not this role, and is recorded
  here rather than silently deferred.
- **FINDING 4 (SHOULD-FIX) — accepted.** `_role_ok` now refuses `role == "reviewer"` outright,
  so the guarantee does not rest on the socket mount staying correct.
- **FINDING 5 (SHOULD-FIX) — accepted.** The pre-freeze guard now calls `blobs_under` scoped to
  `acceptance/`, so an unrelated symlink or gitlink elsewhere cannot reject a valid suite.
- **FINDING 6 (SHOULD-FIX) — accepted; the most useful finding of both rounds.** Several tests
  asserted shapes rather than behaviour:
  - the frozen-projection test derived the proposal tree from `HEAD`, so a `HEAD` fallback
    passed — rebuilt with a *stale* test that exists in `HEAD` but not in the proposal, which is
    exactly what a `HEAD` fallback would show;
  - the acceptance-read "positive control" never ran the command — replaced with a real bwrap
    run that cats a canary, so it fails if the mounts disappear;
  - the socket test read source text — replaced with a behavioural check over every bind form;
  - the env test hand-listed names — replaced with the allowlist property;
  - added a test that the exact-tree check rejects extra material, and one that it accepts a
    correct projection.
- **NOT fixed, stated plainly.** Whether a reviewer can start a real model session with no
  egress is unproven: the sandbox and argv are verified, the model launch is not. It has no
  network by design, so a model needing a network API cannot run in it. That is a design
  question for the host loop (a reviewer model reached through the host, not the sandbox), and
  it means the `--run` path is still not end-to-end.

### Found while reviewing my own fix (no reviewer available: sol out of credits, gemini timed out, sonnet rate-limited)

- **D15-12**: `_load_proposal_for_review` still fell back to `HEAD` when no proposal existed,
  and `_projection_tree` still had a `frozen is None` branch that cut the whole repository tree
  for a reviewer. Together they restored the exact hole the proposal lookup was added to close:
  a verdict bound to a tree nobody proposed. Both now refuse (`SessionOptionError`). A reviewer
  needs a proposal or a frozen task; without one there is nothing for a verdict to bind.
- This is the third hole of the same shape in this feature, all found by asking "what happens
  when the input the guarantee depends on is missing" rather than "does the happy path work".
  Worth remembering as a review prompt, not just a fixed bug.

### D15-13: the reviewer is now reachable from the host (`strength-review --run`)

- The remaining work of D15-6 was never the sandbox — it was that nothing started a reviewer
  and nothing read what one said. Both exist now:
  - `honest brief`: the host writes `BRIEF.md` into the reviewer's own HOME (`brief_file=`),
    not argv: argv is visible to the process it describes and can carry the author's text.
  - `VERDICT: pass|fail` line in `verdict.txt`: `parse_review_verdict` / `read_review_verdict`,
    bounded to 64 KiB, read as untrusted bytes.
  - **No verdict is not a failed review.** `parse_review_verdict` returns `None`, and
    `run_strength_review` refuses with `strength_review_no_verdict`. Recording `green=False`
    would put words in the reviewer's mouth (ADR 0015 decision 3).
  - The launch seam is injected through `Context.reviewer_launch`: an inbound adapter that
    imported `bootstrap` would invert the layering, so the bootstrap hands the CLI a callable.
- **Fixed an uncaught `StoreError`** in `strength-review`: an unreadable record produced a
  traceback at a host entry point, which a caller could mistake for a retryable crash.
- The reviewer still has no network by design, so a model that needs a network API cannot run
  inside it. The `--run` path is complete for models reachable without egress; for the others
  the recorded-manual path remains.

### Independent review (gpt-6.2-astra, high) — "would not ship", 7 findings, 6 accepted

This reviewer read the code without the ledger first. It found the `--run` path did not work
at all, which the previous rounds had not noticed because they tested a stubbed launch.

- **A1 (BLOCKER) — accepted.** `_launch_agent` passed `None` for stdin/stdout/stderr when
  `agent_stdio=False`, and `None` in `Popen` *inherits* the host's descriptors. A reviewer
  whose operator had project text on stdin read it without any mount. Now `closed=True` for
  this role, all three to `/dev/null`.
- **A2 (BLOCKER) — accepted.** The `--run` callback left the context without waiting, then
  read the verdict *after* teardown removed HOME. The verdict could never be collected, and a
  fast reviewer's file was read as absent. It now waits for the process and reads while the
  session is open.
- **A3 (BLOCKER) — accepted.** `_ReviewSubject` carried only `acceptance_tree`, while
  `host_session` reads `frozen.contract.lineage_id` and `frozen.projection_tree` before
  reaching the reviewer branch. `--run` would have raised `AttributeError` deep in setup. The
  subject now carries both.
- **A4 (BLOCKER) — accepted.** The launch callback got no task id, so `host_session` picked the
  active task: the reviewer could judge a different proposal than the record names. The task id
  is now passed through.
- **A5 (SHOULD-FIX, the most important) — accepted.** A manual `strength-review --passed`
  produced a record indistinguishable from one the host observed, and auto-freeze accepted it.
  `StrengthReview.observed` now separates them, `run_strength_review` sets it, the store
  persists it (absent means `False`), and **auto-freeze refuses an unobserved review**. This is
  the reviewer's own "first change", and it was right: an operator's assertion is not a review.
- **A6 (SHOULD-FIX) — accepted.** `read_review_verdict` followed symlinks and took any file
  type. Now `O_NOFOLLOW` plus an `S_ISREG` check.
- **A7 (NIT) — recorded, not fixed.** `_refuse_overlap` compares realpath strings, so two bind
  aliases of one directory can evade it. Reproducing it needs host layout beyond an
  ordinary candidate tree, and the normal launcher always creates a fresh private HOME with an
  absent or empty projection. Recorded as a known limit rather than closing it with a
  device/inode comparison, which the reviewer did not ask for.

**Not accepted:** the claim that `--run` cannot work for its purpose. With the four blockers
above fixed the path is complete for a model reachable without egress; whether a model can be
reached that way is a configuration question, not a defect in the role. The reviewer's "would
not ship" verdict was written against code where `--run` could not collect a verdict at all,
so it is answered by A1–A4 rather than by argument.

**Note on process.** Every blocker in this round was in the wiring, not the sandbox, and none
was found by the earlier test suite because those tests stubbed the launch. Astra found them
by reading what the code *does* rather than what its tests assert. Two rounds of review missed
it; one blind review found it.

### Round 4 (gpt-6.2-sol, medium) — REJECT, and (partly) right for the second time

Sol reviewed `e92cb3e`, before #45. Five of its nine findings were already closed by that
commit (which astra's review, not sol's, prompted); the rest are dispositioned here.

- **S4-1 (BLOCKER) — accepted and the most valuable finding of the round.** `main` splits argv
  at `--` (`split_agent_argv`), and bootstrap never handed that tail to `strength-review`. The
  documented `strength-review --run -- <agent>` therefore reported the command as missing and
  launched nothing. Every previous test passed because they called the handler directly.
  Fixed in `bootstrap.main`, with `test_main_hands_the_strength_review_command_to_the_dispatcher`
  driving the real `main`; the test fails when the fix is removed (verified).
- **S4-2, S4-4, S4-6 (BLOCKERs) — already fixed in #45** (subject attributes, task id, wait
  before reading the verdict, no inherited streams).
- **S4-3 — partly accepted.** The exact-tree guard compares paths, so it accepts changed bytes
  at an expected path. The git writer refuses non-regular tree entries and checks the generated
  index tree independently, so a projection cannot contain aliased bytes through the supported
  path. Recorded; not closing the inode comparison sol described, which the normal launcher
  cannot reach.
- **S4-5 (BLOCKER) — recorded, not fixed.** Vendor labels are CLI flags; nothing checks the
  reviewer command against the declared model. Closing it needs a stored launch record for both
  sessions, which is a design change beyond this slice and is the same point as A5's
  "identities from trusted launch config" in ADR 0015 decision 4. Recorded as the largest
  remaining gap between the ADR and the code.
- **S4-7 (SHOULD-FIX) — accepted.** `freeze --approve` and `author recover` now catch
  `StoreError`; the dev instructions no longer claim plain `freeze` proposes-and-prints (it
  freezes directly; `--approve <proposal> --auto` is the reviewed path).
- **S4-8 — accepted as a test-gap audit.** Every case it names was addressed in #45 or here.
- **S4-9 (BLOCKER) — accepted as a fair statement of scope.** `--run` is complete for a
  reviewer reachable without egress. Reaching a cloud reviewer from inside a no-network sandbox
  cannot work, and pretending otherwise is the defect. Whether that makes the feature
  "unfinished" depends on whether the reviewer is launched from the host (outside the sandbox)
  or inside it; that is the open design question recorded below, not a defect in the role.

### Round 5 (claude-sonnet, mutation testing) — ACCEPT WITH CHANGES

This was the most useful round for the security tests: it deleted protections one at a time
and reran the suite, and reported which deletions stayed green.

- **Mutations caught**: projection rw, env passthrough, MCP fallthrough, `tree_keeping`
  returning the whole tree, a frozen reviewer using `projection_tree`, the reviewer getting the
  full tree, empty acceptance allowed.
- **Mutations missed (all green)**: drop `--ro-bind-fd`, drop `--unshare-net`, drop
  `--clearenv`, drop the `_refuse_overlap` call, drop the seeds/extra-mounts refusal, reviewer
  `build_argv` falls back to `agent()`, reviewer command wrapped in the bridge, drop seccomp.
- **M5-1/M5-2 (SHOULD-FIX, structural) — accepted and fixed.** The role→profile choice lived
  inside `host_session`, so nothing could test it; the two "tests" that seemed to cover it were
  `inspect.getsource` string checks that stayed green when the branch was emptied or inverted.
  Extracted to `bootstrap._session_argv` and covered behaviourally by
  `test_a_reviewer_session_gets_the_reviewer_profile` and
  `test_an_implementer_session_gets_the_agent_profile` (the control). Verified by mutation:
  making the reviewer branch unreachable now fails the suite. The source-text test is gone.
- **M5-3 (SHOULD-FIX) — partly accepted.** The reviewer's read is now proven by the real-bwrap
  canary in `test_the_reviewer_really_reads_the_tests_inside_the_sandbox` (#41), which starts
  bwrap and reads a canary file. A reviewer-specific seccomp/network assertion is added to the
  argv test; a full network-failure test is not, because it needs a live sandbox per test and
  the socket absence is already asserted structurally.
- **M5-4 (SHOULD-FIX) — accepted as the sharpest critique.** ADR 0015 argues against ADR 0009's
  rejected LLM judge, but the comparator should be the human strength review that
  `auto_approve_freeze` replaces. That criticism is correct. ADR 0015 is accepted and immutable,
  so this needs a superseding ADR rather than an edit; recorded here so it is not lost.
- **M5-5 — accepted.** A manipulated green verdict is the expected attack, and the asymmetry
  (spurious pass removes the gate, spurious fail costs a retry) points the wrong way. The
  mitigation now in place is `observed`: a verdict only counts if the host watched a session
  produce it. Bounding it further (opt-in auto-freeze, or an executable strength signal) is an
  open decision, not a code change I should make unilaterally.
- **M5-6 — confirmed fixed** by D15-12, with the regression test M5-6 asks for.
- **M5-7 (SHOULD-FIX) — accepted.** The UDS dispatcher's `_role_ok` now refuses the reviewer
  role outright (#41), so the residual is narrower than "recorded, not fixed" implied.
- **M5-8 (NIT) — accepted.** `reviewers` in ADR 0015 overstates coverage; the gemini review
  timed out.

**Why three rounds of review missed what astra found in one:** every earlier round read the
tests and the sandbox and found them sound — they are. Astra read the *wiring* and found the
feature did not run. Sol, reading the same code, found the fourth independent wiring defect.
Sonnet, mutating rather than reading, found that the security tests would not have noticed.
The tests were checking the right properties in the wrong place.

### Open decision: is auto-freeze allowed outside dev mode?

ADR 0013's consequences say auto-freeze exists **only** in dev mode and that a security test
must prove it. ADR 0015 never carried that over, and the code shows the gap: `--auto` is an
option on the ordinary `freeze --approve` command, available in any repository, with no
`dev_mode()` check and no security test for the restriction.

Three reviews pointed at it from different sides (sol S4-7's neighbourhood, the ADR-fidelity
round's BLOCKER 1, sonnet's M5-5 "make auto-freeze opt-in per project"). It is a genuine fork:

- **A. Dev-only, as ADR 0013 says.** Auto-freeze refuses unless a dev session is open for the
  lineage; `tests/security/test_auto_freeze_dev_only.py` proves it by attempting the call
  outside dev mode. Smallest change, restores the documented invariant.
- **B. Anywhere, but opt-in per project.** A setting in the trusted store, default off,
  replacing the dev-mode test with a config test. More useful, but it is a new decision that
  ADR 0013 already settled against.
- **C. Anywhere, as today.** Then ADR 0013's consequence is wrong and should be superseded.

**Decided: B (operator, this session).** Auto-freeze stays available in any repository but is
off until the project opts in. `AuthoringStore.auto_freeze_enabled(lineage)` reads a stored
boolean, `set_auto_freeze` writes it, and `auto_approve_freeze` refuses with
`auto_freeze_not_enabled` before it looks at any review. The operator-facing switch is
`cheeseless freeze --allow-auto` / `--no-allow-auto`, scoped to the current contract's lineage.

Two details of the implementation worth keeping:

- **An absent record is `False`, a corrupt one is `StoreError`.** Reading a missing setting as
  "no" is the safe default; reading an unparseable one as either value would be a guess, and
  one of the two guesses opens the gate on garbage.
- **The use case keeps its own check, not the CLI's.** `auto_approve_freeze` asks the store for
  the proposal's lineage, so nothing a caller supplies can widen the scope of an opt-in.

### ADR 0016 — the bounds were in the only place that did not matter (2026-11-18)

**M5-4 is answered by ADR 0016.** It supersedes the reading, not just the wording: the review
replaces the *human* strength review, and ADR 0005's executable checks still run in full.

What the ADR review found, and what changed because of it:

- **Bound (c) was the fatal one.** `--author-model` and `--reviewer-model` were caller labels:
  "passing two labels to a comparison proves only that the labels differ". Both vendors now
  come from `sessions.json`, which `register_session` writes at launch and an agent cannot
  write, via the new `Store.launched_model`. A label that contradicts the record is refused
  (`author_model_mismatch`); a task with no author launch record is refused before a reviewer
  runs at all (`author_unknown_launch`); a verdict from a process the host did not record is
  refused (`strength_review_unattributed`).
- **Launch provenance outlives the session.** `end_session` removes the live entry, and reviews
  are recorded *after* the session ends, so `register_session` now also appends to
  `launched-sessions.json`. Without it the vendor would vanish exactly when it is needed.
- **`observed` and `green` were coerced with `bool()`.** A stored `"false"` read as `True`. The
  loader now requires real JSON booleans and raises on anything else: a malformed record is a
  refusal, not a guess in the direction that opens the gate.
- **`freeze --yes` was the bypass.** It reached `prepare`/`commit` with no proposal, opt-in,
  review or crosscheck. The checks moved out of `auto_approve_freeze` and into `approve_freeze`,
  behind `unattended=True`, which only `auto_approve_freeze` passes; bare `freeze` routes
  through it, so it and `--approve --auto` are held to the same gates. `--yes` now only
  answers the prompt. An operator's `--approve --as` is *not* held to them: the opt-in and the
  review replace a present human, so binding one would make a project that never opted in
  unfreezable. It keeps ADR 0009's checks. The split is a keyword argument, not a test on the
  actor string, so naming yourself `cheeseless-dev` does not change the mode. Tests that need a
  frozen task as a *given* call `tests/toy.freeze_without_approval`, which calls the app layer's
  `prepare`/`commit` directly. A first version used a hidden `--internal-test-freeze` CLI flag;
  hidden is not unreachable (any agent can pass it), so it was removed and
  `tests/security/test_freeze_no_ungated_route.py` proves the CLI has no such route.
- **The opt-in could be withdrawn mid-flight.** Consent was read before the approval ownership
  was taken, so a concurrent `--no-allow-auto` could land between the check and the reservation.
  Consent is now read *inside* ownership, and `_ready` moved after it, so a project that never
  opted in is refused before its repository is touched. `set_auto_freeze` takes the same
  ownership, so a withdrawal during an approval is refused rather than silently ineffective.
- **`--as` is reachable by an agent.** Any process that can run the CLI can pass
  `--approve <id> --as <name>` and get the attended path. This predates ADR 0016 and is not
  widened by it. Narrowed (not closed) by ADR 0017, the typed `/dev/tty` challenge; see
  "ADR 0017 review — gpt-6.1-sol".

Rejected: making the reviewer's toolchain, rather than its launch record, the source of vendor.
The launcher that selects the executable is the same thing that records the launch; deriving
the vendor separately would be a second source of truth with no way to reconcile them.

### ADR 0016 review — gpt-6.1-sol (high, read-only): REJECT, findings 1–8

The review read a snapshot taken before the unattended split, so several findings describe code
that no longer exists.
- **1 (bare `freeze` bypasses review and ownership) — fixed before the review landed.**
  `_direct_freeze_problem` and the direct `commit` are gone; bare `freeze` routes through
  `approve_freeze(unattended=True)`.
- **2 (reviewer provenance is a caller flag) — accepted, recorded as open.** The launch record
  holds the `--reviewer-model` the launcher was told; nothing checks that the command runs it.
  The ADR text now says so instead of "never a caller flag". Closing it is the trusted-launcher
  config slice: the host config maps a launcher to its model and the CLI only selects a
  launcher by name. The "Rejected" note above still holds (one source of truth), but that
  source has to be host config, not argv.
- **3 (opt-in withdrawal outside ownership) — fixed before the review landed.**
  `set_auto_freeze` takes `approval_ownership`.
- **4 (direct freeze runs unapproved tests on the host runner) — fixed before the review landed.**
  Bare `freeze` computes `proposal_id_for` (a digest, no test run) and goes through the gate.
- **5 (author launches record no model) — accepted, recorded as open.** No production path
  passes `agent_model` for an author session, so `author_unknown_launch` refuses every real
  unattended freeze. This fails closed, so it is not a hole, but the feature cannot be used
  until the trusted-launcher slice lands. The ADR now states it.
- **6 (overstates ADR 0009 as human-only) — accepted.** ADR 0009 lets the operator be a human
  or an orchestrator agent. The ADR now names what is removed as the operator's strength
  decision (ADR 0009's "the control"), not a human. The operator fallback in ADR 0015 is
  intact: `--approve --as` is not held to the unattended gates.
- **7 (supersede ADR 0015 wholesale) — rejected.** Only 0015's Context reading and M5-4 are
  wrong; its record format, binding and opt-in stand and are what the code implements.
  Following the 0013 pattern, 0015 stays accepted with a note that its Context reading is
  superseded by 0016.
- **8 (route coverage) — fixed.** `test_freeze_no_ungated_route.py` now covers bare `freeze`
  with a proposal and an opt-in but no review (`no_strength_review`), with an observed review
  (moves to `no_crosscheck`), and the operator path. `test_reviewer_run.py` covers launch
  label mismatch (`author_model_mismatch`). A withdrawal during a live approval is refused:
  `test_file_store_authoring.py` holds the ownership and shows `set_auto_freeze` fails, with a
  canary that it succeeds once released.

### ADR 0016 re-review — gpt-6.1-sol (high, read-only): ACCEPT WITH CHANGES

No unattended freeze route was found that skips the gate. Bare `freeze` and `--approve --auto` both
reach `approve_freeze(unattended=True)` inside the approval ownership. Recovery can finish a task
that was already published, but it cannot create a new frozen suite. The four focused test files pass
(88 tests).

1. **Medium, fixed.** The superseded claims sit in ADR 0015's *Decision*, not its Context: "never a
   substitute", "does not reintroduce the LLM judge" and "both from trusted config".
   - ADR 0016 now quotes those three claims.
   - ADR 0015's status line and its index row record a partial supersession, which corrects the
     wording of finding 7 above. The rest of ADR 0015's Decision still stands.
2. **Low, fixed.** The Consequences overstated the evidence. The code records one reviewer verdict
   and compares two vendors that the launcher declared, not two separate strength judgements. The
   text now reads "one declared reviewer passing tests attributed to an author of another declared
   vendor".

**Reviewers and status.** `reviewers: [gpt-6.1-sol]`, and the status is accepted. The author,
claude-opus-5-5, does not review its own ADR. Findings 2 and 5 from the first review are still open,
for the trusted-launcher-config slice.

### Trusted launcher config — findings 2 and 5 addressed

Branch `feat/trusted-launcher-config`. The model recorded for a launched session now comes from
`launchers.toml` in the operator's config directory, selected by `--launcher NAME`.
- **Finding 2 (reviewer provenance is a caller flag) — addressed.** With `--launcher` the host
  runs the configured command and records the configured model; a contradicting `--` command or
  `--reviewer-model` is refused. `--reviewer-model` is only a label on `--run` and is never
  recorded as the launched model.
- **Finding 5 (author launches record no model) — addressed.** `session`, `dev` and
  `dev-reference` take `--launcher` and pass the configured model as `agent_model`. A launch
  without it records an empty model, so `author_unknown_launch` still fails closed.
- Config trust: regular file, owned by the current uid, not group/world-writable, in a directory
  with the same properties, opened `O_NOFOLLOW`, re-read on every lookup, and refused when it
  resolves inside the repository or its common dir. Tests: `tests/security/test_launcher_config.py`
  (each refusal has a canary) and `tests/unit/test_cli_launcher.py`.
- Design: a `LauncherCatalog` port injected through `Context`, so the CLI adapter does not import
  the config adapter (import-linter stays unchanged). No new ADR: ADR 0015/0016 already name
  trusted config as the source.
- **Remaining limit.** The config is operator-declared. cheeseless cannot verify what model a CLI
  actually talks to, and anyone who controls the operator's config controls the recorded vendor.
  The vendor-independence check is only as honest as that file.

### Trusted launcher review — gpt-6.1-terra (medium, read-only): REJECT

Cross-vendor review of the trusted-launcher-config slice (commit 9bc1f99). Three findings:
1. **Critical, `XDG_CONFIG_HOME` picks the trust root — rejected.** The gated agent binds only
   the projection (ADR 0003). It cannot set the host's environment or write the operator's config
   directory. A same-UID process on the host is the residual risk ADR 0001 accepts, and that
   process could just as easily edit the default file. Ignoring `XDG_CONFIG_HOME` would therefore
   not raise the bar. The orchestrator that acts for the operator is trusted (ADR 0016).
2. **High, TOCTOU on the config's ancestor directories — rejected** for the same reason. Swapping
   an ancestor between the check and the open needs a writer on the host with the same UID.
3. **Medium, the tests bypass the launch boundary — fixed.** `test_launcher_config.py` simulated
   the host by calling `register_session` directly. `tests/security/test_launcher_cli_boundary.py`
   now goes through `bootstrap.main` with a real operator config and a real sandboxed agent, and
   reads the model from the host's own append-only launch log:
   - `session`: `test_session_with_a_launcher_records_the_configured_model`,
     `test_session_without_a_launcher_records_no_model`,
     `test_session_refuses_a_command_that_contradicts_the_launcher` with its canary
     `test_canary_session_accepts_the_launchers_own_command`;
   - `dev`: `test_dev_with_a_launcher_records_the_configured_model`,
     `test_dev_without_a_launcher_records_no_model`,
     `test_dev_refuses_a_command_that_contradicts_the_launcher` with its canary
     `test_canary_dev_accepts_the_launchers_own_command`;
   - `dev-reference`: `test_reference_with_a_launcher_records_the_configured_model`,
     `test_reference_without_a_launcher_records_no_model`,
     `test_reference_refuses_a_command_that_contradicts_the_launcher` with its canary
     `test_canary_reference_accepts_the_launchers_own_command`;
   - `strength-review --run`: `test_review_with_a_launcher_records_the_configured_model`,
     `test_review_without_a_launcher_records_no_model_despite_a_typed_one`,
     `test_review_refuses_a_label_or_command_that_contradicts_the_launcher[label|command]` with
     its canaries `test_canary_review_accepts_a_label_or_command_that_agrees[label|command]`.

   Revert check, run on the CLI tests and then restored:
   - `resolve_launch` records the typed label when no launcher is given: the no-launcher review
     test fails.
   - The contradiction checks are dropped and the caller's command runs under the launcher's model:
     all five refusal tests fail.
   - The configured model is replaced by the label: all ten tests that expect the configured model
     fail.

## ADR 0017 review — gpt-6.1-sol (2026-11-18)

Cross-vendor review of the proposed ADR 0017 (author: claude-opus-5-5). Verdict
ACCEPT-WITH-CHANGES. A second reviewer (DeepSeek via opencode) was refused by the operator's
routing policy for restricted work, so this ADR has one cross-vendor reviewer, the minimum.
Every finding was checked against the code and accepted:

1. HIGH, `author recover` "freezes nothing" was false: the finish branch
   (`app/authoring.py` `_verify_published` → `set_active` → `frozen()`) completes a freeze.
   Accepted: finishing a recovery needs the challenge; rolling back an unpublished reservation
   does not.
2. HIGH, PR and CI do not replace ADR 0006's human approval of sensitive paths; `promote`
   forwards `--approve` strings to `decide_promote` unchecked. Accepted: sensitive-path
   approval is in scope; hard-denied paths stay denied.
3. HIGH, `freeze --allow-auto` records the project's ADR 0016 consent with no confirmation, so
   a host agent could grant itself the opt-in (it would still need a qualifying review).
   Accepted: enabling needs the challenge, withdrawing does not.
4. MEDIUM, an id alone does not bind the confirmation. Accepted: the prompt names operation,
   lineage, proposal digest or dispute id, actor, outcome and waiver, escaped; the proof is a
   single-use confirmation taken under approval ownership, and state is revalidated after it.
5. MEDIUM, the adapter's fail-closed contract was underspecified. Accepted: the ADR now lists the
   failure cases, flush-before-display, no echo, one monotonic deadline, bounded input, terminal
   restore in `finally` and `compare_digest`.
6. MEDIUM, the benefit was overstated. Accepted: it blocks stdin-fed, preloaded and terminal-less
   invocation without terminal emulation; it proves terminal interaction, not a human.
7. MEDIUM, fake ports alone cannot prove production wiring. Accepted: security tests drive the
   real CLI (piped stdin, no terminal, typed-ahead, timeout) and a test-owned pty for success,
   assert refusals leave state unchanged, and include the canary.
8. LOW, predecessor wording. Accepted: the ADR quotes ADR 0009's "a human or an orchestrator
   agent", says it revises that operator only for the five decisions, and calls the remainder a
   trusted-host residual instead of attributing it to ADR 0001 (which accepts a sandbox escape).

## ADR 0017 implementation — tests and revert check (2026-11-18)

Implemented in `a3347e0` (domain and port), `82f3ef7` (app requirement), `dd3c436` (`TtyPresence`
adapter) and `94a5912` (CLI wiring in `bootstrap.build_context`).

New tests:

- `tests/unit/app/test_presence_wiring.py`: each of the five decisions asks once with the exact
  request, records `presence: tty-challenge` in the event cause, leaves state unchanged on refusal
  or with no port, and refuses when state moved while the operator was answering.
- `tests/unit/adapters/test_tty_presence.py` (22 tests, real pty pair): correct code, fresh nonce,
  no echo, escaped fields, wrong, empty, overlong and second answer, timeout, EOF, hangup, interrupt,
  read error, typed-ahead flush, failed restore, no controlling terminal, background job, `/dev/tty`
  only.
- `tests/security/test_presence_challenge.py` (20 tests): the real CLI in a new session with
  piped stdin and no terminal refuses all five decisions and leaves state unchanged; the canary
  positive control on a test-owned pty lets auto-freeze, dispute and sensitive promote through and
  refuses a wrong code; an app-level refusing port; production wiring is `TtyPresence`; no CLI
  option, environment variable or extra `presence=` call site bypasses it.

Revert check: each `require_presence` call was replaced by a stand-in that confirms without
asking, then restored. Failures in the two files above (security + wiring):

| Call site | Tests that fail |
|---|---|
| `app/dispute.py` resolve | 6 (no-tty CLI, pty success, refusing port, wiring ×3) |
| `app/authoring.py` approve | 7 (no-tty CLI, refusing port, wiring ×5) |
| `app/authoring.py` recover finish | 5 (no-tty CLI, refusing port, wiring ×3) |
| `app/authoring.py` auto-freeze enable | 6 (no-tty CLI, pty success, pty wrong code, refusing port, wiring ×2) |
| `app/promote.py` sensitive approvals | 7 (no-tty CLI, pty success, refusing port, wiring ×4) |

Deviations from the ADR text, all fail-closed:

- Enabling auto-freeze asks outside approval ownership (there is no proposal to own); the lineage
  is revalidated after the answer and a change refuses with `lineage_changed`.
- Dispute resolve and promote have no ownership lock either; they revalidate state after the
  answer and commit with compare-and-set, so a concurrent change is not applied twice.
- The actor in the auto-freeze and promote prompts is `operator`; neither command names one.
- A refusal is `AuthorError(kind="presence_refused")` for approve, recover, auto-freeze and
  promote; dispute resolve propagates `PresenceRefused` and the CLI prints `dispute failed: ...`.
- `tests/conftest.py` swaps `bootstrap.TtyPresence` for a refusing fake so a suite run from a
  terminal never prompts; child processes use the real wiring and a test asserts that it does.
- Approve and recover-finish have no pty CLI success test, because a real sandbox cross-check
  runs after presence; their success path is covered in-process.
- Follow-up: `test_a_contract_changed_while_the_operator_answers_does_not_enable_auto_freeze`
  commits a retargeted contract while the operator answers and expects `lineage_changed` with
  auto-freeze off for both lineages. With the post-answer lineage check removed it fails
  (`DID NOT RAISE AuthorError`).

## ADR 0017 implementation review — gpt-6-luna (2026-11-18)
- MEDIUM, accepted: `_wait` returned on `POLLIN` before checking `POLLHUP|POLLERR|POLLNVAL`, so input buffered alongside a hangup could be accepted. Hangup bits are now checked first; `test_input_arriving_with_a_hangup_is_refused` proves it. Verdict was APPROVE-WITH-CHANGES; no other findings.

## PR #35 review — gpt-6-luna (2026-11-18)
- MEDIUM, accepted: the demo pty driver leaked the pty fds if `Popen` raised and killed only the direct child on timeout. Both fds now close on setup failure and cleanup kills and reaps the child's whole session.
- LOW, accepted: added tests for timeout cleanup (fds and a descendant) and for a command that cannot start.

## Blocking CI author check — operator decision (2026-11-19)
- The operator asked for the CI author check on `spec/` PRs to block instead of advise. ADR 0009 says that output is advisory, so ADR 0018 (proposed) supersedes only that clause: a definite `fail` blocks, `unavailable` only warns, and `post` reads a closed enum, never PR text.
- The operator kept `spec/<lineage>` branch naming; no rename.

## ADR 0018 review — gpt-6.1-sol (2026-11-19), verdict ACCEPT-WITH-CHANGES
- HIGH, accepted: enum validation alone does not stop injection. The value now travels by env var into `ci-gate --author`, is validated in tested Python, and can only add a guard, never clear policy, judge or another guard.
- MEDIUM, accepted: `AuthorCheckResult` has no typed basis for the classification. `author_check` gets a typed status; host-side defects take precedence over an unavailable runner.
- MEDIUM, accepted: "crashes its own collection" contradicted the `fail` definition. Reported collection errors are `fail`; only launch failure, timeout or no usable result is `unavailable`.
- MEDIUM, accepted: forging needs no sandbox escape; PR test code can print a forged record or kill the driver. Consequences say so.

## ADR 0018 review — claude-sonnet-5-5 (2026-11-19), verdict ACCEPT-WITH-CHANGES
- HIGH, accepted: host-side contract and sentinel problems were misassigned to `unavailable` when the runner also failed. They are now `fail` with precedence.
- MEDIUM, accepted in part: the `unavailable` path is PR-controlled; documented. Rejected for now: treating "driver ran but produced no result record" as `fail`, because an OOM kill or sandbox teardown looks the same and would fail a required check on a flake. Revisit if the driver can tell its own exit from an external kill.
- MEDIUM, accepted: the `evaluate` output step was unspecified. `ci-branch` writes `author=` itself from the enum; no JSON field copy; no problem text in the summary. Security tests in `tests/security/` cover multi-line, empty, oversized, odd-case and missing values, all mapping to `unavailable`.
- MEDIUM, accepted: the guard keys on route's `BRANCH_CLASS`, never on `evaluate` output; policy rejection and `gate/` PRs are unaffected.
- MEDIUM, accepted: false `fail` recovery is a re-run or an admin merge after reading the `evaluate` log; check text is fixed.
- LOW, accepted (implementation): update the `ci_branch.py` docstring, the `authoring.py` "advisory" wording, `judge.yml` comments and step name, and `docs/ci/README.md`.
- LOW, accepted: supersession scope now quotes ADR 0009 and names what stays unchanged.
- ADR accepted after both reviews' findings were addressed.

## ADR 0018 implementation decisions (2026-11-19)
- `author_check(..., contain_failures=False)`: exception containment is a CI-only opt-in (`bootstrap_ci` passes `True`). Local `author_check`, `author_crosscheck` and freeze approval behave as before: `gate_author_check` still wraps exceptions into `INFRA_ERROR`, and `RepositoryError` is caught only under the opt-in, so a missing contract file stays a hard error locally but is a host-side `fail` in CI.
- `AuthorCheckResult.status` is a derived property over `ok` and the new `unavailable` flag, so the three states are one source of truth; `ok` with `unavailable` is rejected at construction.
- The value reaches `ci-gate` as `--author=VALUE` (never a separate argument), so a forged value starting with `-` cannot be parsed as an option. `--evaluate-result` is passed with it so a non-success `evaluate` job is `unavailable`.
- `check_payload` (the unprivileged `policy` job's output) no longer renders author notes at all: author text is not part of the policy payload. The only author text in a check body is a fixed string chosen from the enum.
- A missing `author=` line (no author check ran, refused spec PR, crash before the outcome) reaches `ci-gate` as an empty value, which is `unavailable`.
- Security tests: `tests/security/test_ci_author_gate.py` covers malformed, injected, oversized and workflow-command values, a non-success evaluate result, `fail` never clearing another guard, and the canary/sentinel check on check bodies.

## Plain role names in prose (docs only)

- Prose now says test writer, reference coder, coder, operator, "lock the tests" and "test was wrong" / "test stands". Code identifiers, CLI commands and flags, MCP tool names, proto fields and accepted ADRs keep the old terms (ADRs are immutable).
- `docs/glossary.md` maps each plain term to its code term; docs that use the plain terms link to it. The first use of a term in a section shows the code term once, in backticks.
- The demo actor argument `--as host` became `--as operator` in `demo/author_demo.py`; the author demo recording was regenerated with the new captions. `docs/errors.md` is generated from proto and was left alone.

## Phase view and per-role T3 threads: design (2026-11-19, proposal only)

- Part A (read-only `cheeseless dev-events`, closed schema v1, canary test) needs no ADR: it projects existing host state and changes no accepted decision. An ADR becomes necessary if events ever carry free text or a viewer gets a write path.
- Part B: agent-per-T3-thread is rejected (agent outside the sandbox reads hidden state; ADR 0003/0009/0013). Recommended: host-owned relay panels (ADR 0019, accepted); sandboxed T3 launch is a later spike. Details and open questions: `planning/phase-view-design.md`.

### 2026-11-19 · ADR 0019 review round 1 (gpt-6-luna: REJECT)
- high, resize and reconnect bypass view-only: accepted. View-only now reads no client frame (`Data` or `Resize`) on any connection; the host alone sets pty size; a frame closes that connection. S7 tests updated.
- high, coder pane output not shown test-free: accepted. ADR states the output boundary per pane (reference test-free per ADR 0009; coder has no hidden tests per ADR 0003 but may show visible tests and code) and that scrollback must reach no agent.
- low, rejected option omitted the GUI orchestrator: accepted, option now names it and the ADR 0013 break.
- low, title overstated "every role view-only": accepted, retitled; modes per role listed.

### 2026-11-19 · ADR 0019 review round 2 (gpt-6.1-sol: ACCEPT)
- No findings. Notes: hidden-test isolation for panes still depends on the GUI never feeding scrollback to an agent (open question Q3); S6–S7 wait for it. ADR 0019 accepted; reviewers gpt-6-luna, gpt-6.1-sol (author claude-sonnet-5-5, revision claude-opus-5-5).

## Dev-events host command, Part A (S1 to S5, 2026-11-19)

- No ADR: Part A projects existing host state and changes no accepted decision. An ADR is needed if events ever carry free text or a viewer gets a write path.
- Schema v1 is closed (`additionalProperties: false`, enums only). The canary test checks every stage's output against it, with a byte scan for canaries, 64-hex digests, 32-hex and UUID session ids and paths.
- `--follow` stops on an `error` line. An error line has no view, so a later poll can never change it; without this stop a corrupt store made `--follow` spin forever (found by the S4 canary, fixed with a unit test).
- The disputes fixture had an invalid `DisputeCode`; the store rejected the file (correctly). The fixture now uses `contradicts_spec`.
- `bootstrap_dev_events.py` builds `ObserveStore` directly and never calls `build_context`, so observing creates no directories. The inbound CLI sees only the `DevObservation` port (adapter-independence contract).
- Session ids in the canary are UUIDs, because `ids.session_id` requires the UUID form.
- review round 1 by claude-sonnet-5-5: REJECT; fixed items 1–8 (read-only `ObserveStore`, schema bounds in `encode`, follow vanish error and docs, exception mapping, `--interval` bound, repository guard, import-linter contracts, `--once` exit 3).
- not addressed: per-poll O(n) reference attempt scan and corrupt foreign attempt files — accepted as LOW, reason: host-only, small n.

## Role panes, Part B (S6–S7, 2026-11-19)

- No new ADR: this implements the accepted ADR 0019 and changes no decision.
- View-only is a relay-level flag. Any non-empty client read drops the connection before it is decoded, so no `Data` or `Resize` frame reaches a pty, on any connection. The host alone sets the pty size. The pane client's viewer mode sends nothing and leaves the tty cooked as the polite half.
- Socket layout `<state>/cheeseless/panes/<lineage>/<role>.sock`: deterministic, no digest, in a private 0700 leaf dir. The relay refuses a live peer, removes a stale socket, and refuses symlinks and non-sockets. `lineage_id` is validated, and the 107-byte AF_UNIX limit is checked up front with a clear error. Risk: the same lineage id in two repos that share a state dir shares a path (the second host refuses a live peer).
- The role to mode mapping lives in one tested place, `pane_layout`.
- The `panes` event goes on `cheeseless dev` **stderr**, not in `dev-events`: the Part A schema is closed and its canary forbids paths. It is not on MCP or UDS. An import-linter forbidden contract keeps `adapters.inbound.mcp` and `adapters.inbound.uds` away from `pane_layout`, the pane relay, the pane client and `bootstrap_dev`.
- `--panes` on `dev` is opt-in so existing behaviour is unchanged. With it, the host waits on the agent process instead of tearing the session down at once.
- The fixed socket is used only when `pane_lineage` is passed. A reference attempt in TUI mode now passes it; the tmux reference pane runs `pane-relay --view`. Its old input path is removed on purpose (the ADR 0019 behaviour change).
- The test-writer pane is supported only on a T3 build with command terminals and `persistHistory:false`. On stock T3 its scrollback is persisted and can be added to a chat, which leaks hidden tests (docs/t3-phase-view.md).
- Tests: `tests/security/test_pane_view_only.py` (frames reach no pty across reconnects, a frame closes only its connection, closing the pane stops nothing), `test_pane_session_wiring.py` (roles, modes, paths through `host_session`) and `test_pane_layout_canary.py` (canaries, 64-hex and 32-hex, paths inside the state dir, store-hash mutation check, MCP and UDS source scan).
- PR #42 review (reviewer: claude-sonnet-5-5, read-only): (1) HIGH a refused second relay unlinked the first session's live socket: `_teardown` now unlinks only after this relay bound (`_bound`), test `test_a_refused_second_relay_leaves_the_first_sessions_socket_alone`; (2) MEDIUM only the leaf dir was vetted: every dir from the state dir down is created 0700 and lstat-checked (symlink, owner, group/other bits), and the resolve-inside-state-dir check re-runs before bind, tests in `tests/security/test_pane_socket_ownership.py`; (3) LOW the implementer pane lineage was compared with itself: it is now matched against the open dev session of that lineage, `test_a_coder_pane_of_another_lineage_is_refused`; (4) LOW canary negative control and closed-set event check added; (5) LOW wiring-level test that a canary frame to the coder and reference sockets reaches no pty; (6) LOW `pane-relay --socket` help states a non-`--view` client is disconnected by a view-only relay.
