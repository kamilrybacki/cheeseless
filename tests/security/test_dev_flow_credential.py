"""The dev loop never leaks a credential and never lets an agent pick the phase (ADR 0013).

``require_token`` handles the author's own OAuth token, the one secret the dev flow reads.
Its refusal text is printed to a terminal and may reach a log, so it must name the command
that produces a token without ever echoing the value. ``start_phase`` decides who runs
next; it is a host function, and no adapter surface may reach it.
"""

import inspect
from pathlib import Path

import pytest

from cheeseless.adapters.outbound.store.file_store import FileStore
from cheeseless.app.dev_flow import DevFlowError, require_token, start_phase

CANARY = "CANARY-TOKEN-7f3b91d2"


def test_a_refusal_never_echoes_the_token() -> None:
    # Whatever shape the rejected value has, its bytes must not reach the message.
    for bad in (f" {CANARY} \x00", f"\t{CANARY}\n"):
        with pytest.raises(DevFlowError) as caught:
            require_token(bad)
        assert CANARY not in str(caught.value)


def test_the_refusal_names_the_variable_and_the_command() -> None:
    with pytest.raises(DevFlowError) as caught:
        require_token(None)
    text = str(caught.value)
    assert "CLAUDE_CODE_OAUTH_TOKEN" in text and "claude setup-token" in text
    # a credentials file in the repo is never suggested
    assert "credentials" not in text.lower() and ".json" not in text
    assert CANARY not in text


def test_missing_and_empty_tokens_are_refused_alike() -> None:
    for bad in (None, "", "   ", "\n"):
        with pytest.raises(DevFlowError):
            require_token(bad)


def test_no_adapter_surface_exposes_the_phase_orchestrator() -> None:
    """``start_phase`` is host-only: no MCP tool and no UDS route may reach it."""
    from cheeseless.adapters.inbound.mcp import server as mcp
    from cheeseless.adapters.inbound.uds import server as uds

    for module in (mcp, uds):
        source = inspect.getsource(module)
        assert "dev_flow" not in source
        assert "start_phase" not in source


def test_the_phase_is_read_from_the_host_store(tmp_path: Path) -> None:
    store = FileStore(tmp_path / "state")
    with pytest.raises(DevFlowError, match="no dev session"):
        start_phase(store, "demo")
