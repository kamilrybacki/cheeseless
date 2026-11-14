"""Opt-in real-agent E2E: a real CLI fixes the toy through MCP ``gate_run``.

The whole module is collected but skipped unless ``CHEESELESS_E2E_AGENT=1``. It never
calls a paid API by default, never inspects or logs credential bytes, and refuses
explicitly when the operator has not supplied a CLI or its credential material. An
explicit seed is read only by the normal validated host loader and written to the
throwaway session HOME; evidence capture copies that HOME, so the evidence directory is
sensitive.

Manual opt-in (operator-owned config stays outside this repository):

    CHEESELESS_E2E_AGENT=1 \\
    CHEESELESS_E2E_AGENT_CMD='["/home/me/bin/cheeseless-agent-wrapper"]' \\
    CHEESELESS_E2E_AGENT_SEED='{".config/tool/creds.json":"/home/me/.config/tool/creds.json"}' \\
    uv run --frozen pytest tests/e2e/test_e2e_agent.py -m e2e_agent -s

The seed line is one JSON object mapping a destination inside HOME to an operator source.

The wrapper receives the prompt as its last argument, configures its own MCP server as
``$CHEESELESS_MCP`` (the socket and task are already baked into that shim) and may rely
on the supplied seeds or read-only mounts for credentials.
"""

from __future__ import annotations

import os

import pytest

from tests.e2e.harness import (
    AgentUnavailable,
    load_agent_config,
    prepare_case,
    promote_and_judge,
    run_agent,
    verify_evidence,
)
from tests.requirements import needs

pytestmark = [
    pytest.mark.e2e_agent,
    pytest.mark.slow,
    needs(os.access("/usr/bin/bwrap", os.X_OK), "needs bubblewrap for a real host session"),
]


def test_a_real_agent_fixes_the_toy_through_the_mcp_gate(tmp_path):
    try:
        config = load_agent_config()
    except AgentUnavailable as exc:
        pytest.skip(f"real-agent E2E not configured: {exc}")

    case = prepare_case(tmp_path)
    run_agent(case, config)
    verify_evidence(case)
    verdict = promote_and_judge(case)
    assert verdict.passed, verdict.reason
