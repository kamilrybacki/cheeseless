# Attack vectors — merged (Claude + GPT)

Sources: [`-claude.md`](2026-11-13-attack-vectors-claude.md) (≈40 vectors, ids A–G) and
[`-gpt.md`](2026-11-13-attack-vectors-gpt.md) (64 vectors, ids L/O/G/V/T/S), written
independently by Claude Opus and GPT (codex). Both drafts converge on the critical gaps below.
Q35 in the interview was the shared-understanding confirmation, not a decision.

> **Status:** themes 5 and 6 are superseded by ledger Q41, Q42, Q47 and Q48 (memory stays
> available with author write-ban and sentinel scans; one repo with plaintext tests, local
> skip-worktree and DLP egress instead of a private companion repo). The ledger is authoritative.

## Critical themes and proposed decisions

| # | Theme | Vectors | Proposed decision |
|---|-------|---------|-------------------|
| 1 | **Trusted execution closure.** Pytest config, `conftest.py`, `sitecustomize`/`.pth`, `pytest11` plugins, build hooks, lockfile, CI helper scripts, local actions, `.gitattributes` filters and caches in the candidate tree can decide the verdict, locally and in CI. | C1 C2 D1 D3 D4 / V01–V03 V10–V14 | Tests, conftest, pytest config and runner deps come from a trusted store/base, never from the candidate tree; `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, pinned `rootdir`/`confcutdir`, `-p no:cacheprovider`; candidate built and run in its own sandbox/venv; promote uses a path **allowlist**; build/dep/CI/instruction files are "sensitive" and need human approval; git materialised with no hooks/filters/config. |
| 2 | **Oracle attacks.** Unlimited runs plus criterion bits, timing, error classes and CI red/green leak hidden cases; new sessions reset any per-session budget. | B2 B3 / O01–O05 O07 | Run budget per task identity, shared across sessions and CI; result = per-criterion pass/fail only, no duration or error class; holdout acts as final evaluation and is rotated after N failed CI attempts. |
| 3 | **Candidate exfiltration and state.** Candidate writes inputs to disk, keeps a daemon between runs, reads env/fds/proc. | B1 / L12 G04 T08 | Candidate runs on a throwaway overlay, separate PID/IPC/net namespace, scrubbed env, whole process tree killed; nothing it writes is visible to the agent. |
| 4 | **Sandbox boundary.** srt on Linux cannot allow a single UDS (all blocked or all allowed: SSH agent, docker.sock, D-Bus). Same-user `/proc`/ptrace on the verifier. | A3 A4 / L03 L10 L11 | Own thin `bwrap` wrapper for agent and candidate: new PID/IPC/net namespaces; bind only the projection, the verifier socket and an allowlist egress proxy socket. |
| 5 | **Tools and memory outside the sandbox.** Host-side MCP servers, knowledge stores, auto-memory, thread readers, web fetchers reach tests or author notes. A "fresh delegate" is not fresh. | A5 A6 A7 / L05 L06 L14 T05 T06 | Gated instance gets an ephemeral `HOME`, auto-memory off, no memory/thread/web/filesystem MCP tools; the author phase writes only to the trusted store. |
| 6 | **Public repos.** Hidden tests in a public repo are reachable via web tools, CI logs, packages, training data. | A7–A10 / L07 L09 | Hidden tests live in a trusted store (private companion repo or local dir) unless the repo is private; CI fetches them with a secret; CI logs criterion level only; sdist excludes `acceptance/`. |
| 7 | **Control plane and evidence.** Agent calls admin verbs; results replayed for another tree; TOCTOU between run, freeze approval and promote; peer spoofing on the UDS. | D2 / V05–V09 V15–V17 V19 | UDS exposes only `run`/`status`, peer checked with `SO_PEERCRED`, socket in a private dir; freeze/promote/disable are host CLI only; results HMAC-bound to task, tree hash, baseline digest, profile, tool version; promote applies the exact evaluated tree; freeze approves an exact content digest. |
| 8 | **Weak or rigged tests (author phase).** Vacuous asserts, skip/xfail, empty collection, missing requirements, predictable holdout, answer keys in public artifacts, tests reading candidate-controlled files/env. | E1 E2 / T01–T05 T07 O09 O10 | Freeze preconditions: suite RED on current baseline; skip/xfail/empty collection = error; every criterion has ≥1 visible and ≥1 holdout test (stratified split); seed generated after approval; static check that tests read no candidate paths/env; public artifacts reviewed for answer leakage. Mutation score post-MVP. |
| 9 | **Fail-open infrastructure.** Timeout, crash or sandbox failure keeps an old PASS or silently downgrades isolation. | C5 / G07 V16 V18 | Fail closed: any infra error = FAIL; no downgrade flags; cgroup/rlimit limits. |
| 10 | **Social / confused deputy.** Injection into orchestrator/reviewer inputs, persuading the human to waive or weaken tests, reviewers running candidate scripts with host access. | F1–F4 / S01–S06 | Orchestrator has no freeze/disable authority; reviewers run gated; re-freeze UI shows digests and lost coverage; honest FAIL/BLOCKED is a valid terminal outcome. |
| 11 | **Gaming the finite suite.** Harness detection, lookup tables, memorised corpora, permissive comparators, behaviour change after N calls. | G1 G2 / G01–G03 G05 G06 | Production-like scrubbed env with randomised paths; fresh generation seeds; strict comparators; sequence and size cases. Q1 is reworded as a measurable assurance boundary, not "cannot game". |
| 12 | **Canary is necessary, not sufficient.** A transformed leak never shows the literal string. | GPT closing note | Several sentinels per artifact class (tests, fixtures, expected outputs, seed, author notes); scans also check encoded forms (base64, hex, reversed). |

## Accepted residual risks

Training-data memorisation of public tests (A10), correlated blind spots of models (E3/T03),
page-cache/timing side channels below the run budget (L13). Mitigation: holdout rotation and
cross-vendor review; they are documented, not solved.

## Ledger decisions that need changes

Q1 (scope wording), Q2 (fresh delegate), Q12 (hook is not a boundary), Q15 (srt → bwrap),
Q17 (denylist → allowlist), Q18 (unlimited runs, timing), Q19 (BLOCKED outcome),
Q21 (runner config from base), Q27 (stratified split, seed after approval). New MVP
acceptance criteria follow from themes 1, 3, 4, 7, 8, 9 and 12.
