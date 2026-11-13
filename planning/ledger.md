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

### Proposed defaults pending operator confirmation

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
