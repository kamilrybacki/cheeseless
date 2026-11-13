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
| D14 | **Proposed default.** Budget is keyed by the task lineage fixed at the first freeze; a re-freeze or holdout rotation creates a new freeze digest (results bind to it) but carries the remaining count. More runs only via the existing operator grant (Q54, host CLI, logged). | D7 |
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
| P10-8 | **This repo's CI runs the isolation tests.** The CI installs `bubblewrap`, relaxes the ubuntu-24.04 AppArmor unprivileged-userns restriction, and sets `CHEESELESS_REQUIRE_BWRAP=1`, which turns the bwrap and egress skips into failures. Not verifiable without a push. | AC 2, 15, 16 |
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
