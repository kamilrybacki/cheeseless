# Per-criterion-only oracle feedback

The gate deliberately tells a gated agent *whether* each public criterion passed, not
*why*. This document is for people writing prompts for that agent. Role names follow the
[glossary](glossary.md).

## What the agent can see

- The overall result and one `pass`/`fail` status per public criterion.
- Catalogue errors such as `dlp_blocked`, `egress_domain_denied`, `run_budget_exhausted`
  or `infra_error`, with their fixed reason and hint.
- The public contract tests and criterion descriptions that were visible before the
  tests were locked.
- Its own code, the projection and its build/smoke output.

## What it never sees

- Hidden test names, source, assertions, fixtures, stack traces or output snippets.
- A failing assertion message, expected value, line number, diff or per-test status.
- Hidden-content fingerprints, the trusted store, the holdout seed or another task's
  result.
- Any request path, query, header or body copied into the allow-audit log.

A criterion status is therefore the only oracle. A `fail` means at least one hidden
test behind that criterion failed; it does not identify the test, the input or the
expected output.

## Writing prompts that work with that oracle

1. Point the agent at the public contract tests and criterion descriptions, and ask it
   to reason from those, not to guess hidden test details.
2. Require it to call `gate_run` after each coherent change, and to stop when every
   criterion passes or the budget is exhausted.
3. Forbid edits under `acceptance/`; promotion rejects protected paths anyway.
4. Tell it that a `fail` is actionable only as "make the public behaviour match the
   public description", and that asking for hidden details or test output is futile.
5. Ask it to keep the change inside `src/`, `tests/`, `docs/` and README files so the
   promote path filter accepts it.
6. Do not ask it to fetch remote tools or URLs beyond the session allowlist; server-side
   tool schemas are rejected by the egress policy.

## Running the opt-in real-agent E2E

The suite never calls a real or paid agent unless the operator opts in. The agent command,
credentials and configuration stay outside this repository. The harness itself never
inspects, logs or reports credential bytes; its availability check looks only at whether
the operator's config directory exists. When an explicit seed is supplied, the normal
validated host loader opens that source without following symlinks, reads it under a
size cap and writes the pinned bytes into the throwaway session HOME. Evidence capture
then copies that HOME like any other session file, so the operator must treat the
evidence directory as sensitive; the harness scan only searches for hidden canaries and
the bounded projection manifest.

```sh
CHEESELESS_E2E_AGENT=1 \
CHEESELESS_E2E_AGENT_CMD='["/home/me/bin/cheeseless-agent-wrapper"]' \
CHEESELESS_E2E_AGENT_SEED='{".config/tool/credentials.json": "/home/me/.config/tool/credentials.json"}' \
CHEESELESS_E2E_AGENT_RO='["/home/me/.config/tool"]' \
CHEESELESS_E2E_AGENT_TIMEOUT=900 \
CHEESELESS_E2E_AGENT_EGRESS=/home/me/.config/cheeseless/agent-egress.json \
uv run --frozen pytest tests/e2e/test_e2e_agent.py -m e2e_agent -s
```

- `CHEESELESS_E2E_AGENT_CMD` is a JSON argv list (a shell string is also accepted). The
  prompt is appended as the last argument, so the wrapper must accept it.
- The wrapper configures its own MCP server. The staged shim at `$CHEESELESS_MCP` already
  carries the session's verifier socket and task id as literals; `CHEESELESS_SOCKET` and
  `CHEESELESS_TASK` are not needed and conflicting values cannot redirect it.
- `CHEESELESS_E2E_AGENT_SEED` and `CHEESELESS_E2E_AGENT_RO` are optional operator-owned
  credential/config material passed through the normal validated session extras.
- `CHEESELESS_E2E_AGENT_EGRESS` is an optional path to an egress-policy file (`.json` or
  `.toml`, parsed by the same loader as the CLI `--egress-policy` option). The default
  policy allows **no** hosts, so a real agent can never reach its model API without one:
  list the model API host(s) under both `model_hosts` and `allowed_hosts`, for example
  `{"allowed_hosts": ["api.example.com"], "model_hosts": ["api.example.com"]}`. Keep the
  file outside this repository. An unreadable or invalid file refuses the run and the
  error names only the exception type, never the file contents.
- Without an explicit command it tries an installed `claude`, then `codex`. A known CLI
  with no visible config directory is refused explicitly unless seeds or mounts are
  supplied.
- The test fails on a wall-clock timeout, proves the session HOME was removed, scans the
  captured evidence for canaries, then promotes the tree and runs the real CI judge.

### Example wrapper (Claude Code)

A minimal operator wrapper, kept outside the repository. The agent binary directory and the
wrapper directory are passed as `CHEESELESS_E2E_AGENT_RO`; credentials and a minimal
`.claude.json` are passed as seeds. Prefer a sanitized credentials copy (access token only,
empty refresh token) so a refresh inside the throwaway session cannot rotate your real login.

```sh
#!/bin/bash
set -eu
cfg="${TMPDIR:-/tmp}/cheeseless-mcp.json"
printf '{"mcpServers":{"cheeseless":{"command":"%s","args":[]}}}' "$CHEESELESS_MCP" > "$cfg"
export DISABLE_AUTOUPDATER=1 CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
exec /path/to/claude -p "$1" --mcp-config "$cfg" --strict-mcp-config \
  --permission-mode acceptEdits \
  --allowedTools mcp__cheeseless__gate_run Read Edit Write Glob Grep Bash
```

The egress policy then needs only the model host, for example
`{"allowed_hosts": ["api.example.com"], "model_hosts": ["api.example.com"]}`. Delete the
captured evidence afterwards: it contains the seeded files.

Deterministic tests for the harness itself run in the normal suite and make no network or
paid calls (`tests/e2e/test_agent_harness.py`).

## Operator decisions need a terminal

locking the tests (`freeze --approve ... --as`), `dispute resolve`, the test writer's recovery finish step (`author recover`),
`promote --approve` on sensitive paths and `freeze --allow-auto` require a random code retyped
on the controlling terminal (ADR 0017). They need an interactive terminal; piped or headless
runs are refused. Do not script them for an agent. Headless operators use the ADR 0016
`freeze --approve ID --auto` route, which takes no challenge.
