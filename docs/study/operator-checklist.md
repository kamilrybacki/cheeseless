# Real-agent operator checklist

This implementation checklist supplements the frozen study protocol; it changes no
study prompts, tasks, outcomes, or analysis rules.

- Opt in explicitly and use a fresh session for every run. Start with the pilot.
- Set the CHEESELESS command to the approved host wrapper and expose its dependencies
  through the configured read-only mounts. Host visibility does not imply NAIVE visibility.
- Set `CHEESELESS_STUDY_AGENT_CMD_NAIVE` explicitly. Its first argument must be an
  executable in the mounted system directories, or an approved executable file seed
  addressed as `/agent-home/<seed>`. Bare system commands resolve using only
  `/usr/bin:/bin:/usr/sbin:/sbin`. Host-only paths and relative wrapper paths are rejected.
- To run a seeded wrapper directly, add `agent.sh` to `CHEESELESS_STUDY_AGENT_SEED`
  with the approved source file, make that source executable, and set the NAIVE argv
  to `["/agent-home/agent.sh"]`. The copied file retains owner execution permission;
  each real manifest record contains `naive_wrapper_sha256`. Review that digest before
  accepting results. Interpreter-invoked scripts remain the operator's responsibility.
- Ensure the wrapper's interpreter and dependencies also exist inside the sandbox.
  Only system directories, `/work`, and `/agent-home` are available by default.
- Keep work, evidence, and manifests separate. Never mount scorer or oracle paths.
- NAIVE launches close all descriptors above standard I/O before exec, with no passed
  descriptors and stdin disconnected. No bubblewrap FD option is assumed.
- Run `timeout 600 uv run --frozen pytest -q --no-cov -k study` before collecting
  results. Quiet pytest prints dots only after tests finish; the two dry-arm matrix
  tests each execute multiple full sandboxed grades and can take tens of seconds.
  For diagnostics use `-v --durations=10 -o faulthandler_timeout=30`.
- Repair only an incomplete final manifest fragment with explicit tail repair.
  A malformed newline-terminated record is an error and must remain untouched.
- Two agents, one wrapper each. Keep a separate env file per agent (agent command, agent id,
  provider allowlist host, fresh-session declaration) outside the repo, source one per batch,
  and use a separate manifest per agent. Refresh any short-lived token before each batch.
- Run in stages (protocol Amendment 4): small batches, read the provider usage dashboard
  after each, and stop if quota pressure makes timings unrepresentative.
- Disclosure review: after a batch, read the final message of each gamed cheeseless run and
  record `disclosed` or `silent` in a JSON file outside the repo; pass it as
  `--disclosure-review`.
