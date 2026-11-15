"""Deterministic checks for the opt-in real-agent E2E harness (no real agent calls)."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from cheeseless.domain.egress_policy import EgressPolicy
from cheeseless.domain.status import Status
from tests.e2e.harness import (
    COMMAND_ENV,
    EGRESS_ENV,
    READ_ONLY_ENV,
    SEED_ENV,
    TIMEOUT_ENV,
    AgentConfig,
    AgentFailed,
    AgentTimedOut,
    AgentUnavailable,
    EvidenceLeak,
    load_agent_config,
    parse_agent_command,
    prepare_case,
    promote_and_judge,
    run_agent,
    scan_evidence,
    seed_fixed_projection,
    verify_evidence,
)
from tests.requirements import needs
from tests.toy import CANARY

BWRAP = os.access("/usr/bin/bwrap", os.X_OK)
needs_bwrap = needs(BWRAP, "needs bubblewrap for the deterministic harness checks")


def test_json_argv_and_shell_string_are_both_accepted():
    assert parse_agent_command('["claude", "-p"]') == ("claude", "-p")
    assert parse_agent_command("claude --flag 'two words'") == ("claude", "--flag", "two words")


@pytest.mark.parametrize("value", ["", "[]", '[""]', "{}", '["ok", 3]'])
def test_malformed_commands_are_refused(value):
    with pytest.raises(AgentUnavailable):
        parse_agent_command(value)


def test_no_discoverable_agent_is_an_explicit_refusal(tmp_path):
    with pytest.raises(AgentUnavailable, match=COMMAND_ENV):
        load_agent_config({}, home=tmp_path, which=lambda _name: None)


def test_missing_known_credentials_refuse_without_reading_any_token(tmp_path):
    env = {COMMAND_ENV: json.dumps([str(tmp_path / "claude")])}
    agent = tmp_path / "claude"
    agent.write_text("#!/bin/sh\nexit 0\n")
    agent.chmod(0o500)
    with pytest.raises(AgentUnavailable) as caught:
        load_agent_config(env, home=tmp_path / "empty-home", which=lambda _name: str(agent))
    message = str(caught.value)
    assert SEED_ENV in message and READ_ONLY_ENV in message
    assert "SECRET" not in message and "TOKEN" not in message


def test_operator_seeds_or_read_only_mounts_satisfy_the_credential_gate(tmp_path):
    agent = tmp_path / "wrapper"
    agent.write_text("#!/bin/sh\nexit 0\n")
    agent.chmod(0o500)
    seed = tmp_path / "credentials.json"
    seed.write_text("{}")
    config = load_agent_config(
        {
            COMMAND_ENV: json.dumps([str(agent)]),
            SEED_ENV: json.dumps({".config/tool/credentials.json": str(seed)}),
            TIMEOUT_ENV: "12.5",
        },
        home=tmp_path / "empty-home",
        which=lambda name: str(agent) if name == str(agent) else None,
    )
    assert config.command == (str(agent),)
    assert config.seeds == {".config/tool/credentials.json": seed}
    assert config.read_only == ()
    assert config.timeout == 12.5


def _wrapper(tmp_path: Path) -> Path:
    agent = tmp_path / "wrapper"
    agent.write_text("#!/bin/sh\nexit 0\n")
    agent.chmod(0o500)
    return agent


def _egress_config(tmp_path: Path, extra: dict[str, str]) -> AgentConfig:
    agent = _wrapper(tmp_path)
    env = {COMMAND_ENV: json.dumps([str(agent)]), SEED_ENV: json.dumps({"a": "b"}), **extra}
    return load_agent_config(env, home=tmp_path, which=lambda _n: str(agent))


@pytest.mark.parametrize("extra", [{}, {EGRESS_ENV: ""}, {EGRESS_ENV: "   "}])
def test_unset_or_empty_egress_policy_is_none(tmp_path, extra):
    assert _egress_config(tmp_path, extra).egress_policy is None


def test_a_valid_egress_policy_file_is_loaded(tmp_path):
    policy_file = tmp_path / "egress.json"
    policy_file.write_text(
        json.dumps(
            {
                "allowed_hosts": ["api.example.com", "auth.example.com"],
                "model_hosts": ["api.example.com"],
            }
        )
    )
    config = _egress_config(tmp_path, {EGRESS_ENV: str(policy_file)})
    assert config.egress_policy is not None
    assert config.egress_policy.allowed_hosts == {"api.example.com", "auth.example.com"}
    assert config.egress_policy.model_hosts == {"api.example.com"}


def test_a_missing_egress_policy_file_is_refused(tmp_path):
    with pytest.raises(AgentUnavailable, match=EGRESS_ENV):
        _egress_config(tmp_path, {EGRESS_ENV: str(tmp_path / "absent.json")})


def test_an_egress_policy_with_a_bad_extension_is_refused(tmp_path):
    policy_file = tmp_path / "egress.txt"
    policy_file.write_text("{}")
    with pytest.raises(AgentUnavailable, match=EGRESS_ENV):
        _egress_config(tmp_path, {EGRESS_ENV: str(policy_file)})


def test_an_invalid_egress_policy_is_refused_without_echoing_its_contents(tmp_path):
    policy_file = tmp_path / "egress.json"
    policy_file.write_text(
        json.dumps({"allowed_hosts": ["ok.example.com"], "model_hosts": ["leaky-host.example.com"]})
    )
    with pytest.raises(AgentUnavailable) as caught:
        _egress_config(tmp_path, {EGRESS_ENV: str(policy_file)})
    message = str(caught.value)
    assert EGRESS_ENV in message and "PolicyError" in message
    assert "leaky-host" not in message and "ok.example.com" not in message


def test_run_agent_forwards_the_egress_policy_to_host_session(tmp_path, monkeypatch):
    seen: dict[str, object] = {}

    def fake_host_session(**kwargs):
        seen.update(kwargs)
        raise RuntimeError("stop before any session starts")

    monkeypatch.setattr("tests.e2e.harness.host_session", fake_host_session)
    policy = EgressPolicy(
        allowed_hosts=frozenset({"api.example.com"}),
        model_hosts=frozenset({"api.example.com"}),
    )
    config = AgentConfig(
        command=("/bin/true",),
        seeds={},
        read_only=(),
        timeout=5.0,
        egress_policy=policy,
    )
    case = SimpleNamespace(
        state=tmp_path / "state",
        repo=SimpleNamespace(root=tmp_path / "repo"),
        task_id="task",
        projection=tmp_path / "projection",
        sockets=tmp_path / "sockets",
        evidence=tmp_path / "evidence",
    )
    with pytest.raises(RuntimeError, match="stop before"):
        run_agent(case, config)  # type: ignore[arg-type]
    assert seen["egress_policy"] is policy


@pytest.mark.parametrize(
    ("name", "value"),
    [
        (SEED_ENV, "[]"),
        (READ_ONLY_ENV, "{}"),
        (TIMEOUT_ENV, "soon"),
        (TIMEOUT_ENV, "0"),
        (TIMEOUT_ENV, "9999"),
    ],
)
def test_invalid_operator_configuration_is_refused(tmp_path, name, value):
    agent = tmp_path / "wrapper"
    agent.write_text("#!/bin/sh\nexit 0\n")
    agent.chmod(0o500)
    env = {COMMAND_ENV: json.dumps([str(agent)]), name: value}
    with pytest.raises(AgentUnavailable):
        load_agent_config(env, home=tmp_path, which=lambda _n: str(agent))


@needs_bwrap
def test_known_good_fix_promotes_and_passes_the_real_ci_judge(tmp_path):
    case = prepare_case(tmp_path)
    seed_fixed_projection(case)
    verdict = promote_and_judge(case)
    assert verdict.status is Status.PASS, verdict.reason
    assert verdict.visible and all(item.status is Status.PASS for item in verdict.visible)
    assert verdict.tree_hash and verdict.result is not None


@needs_bwrap
def test_evidence_with_a_hidden_marker_is_refused(tmp_path):
    case = prepare_case(tmp_path)
    case.evidence.mkdir()
    (case.evidence / "notes.txt").write_text(f"agent wrote {CANARY}\n")
    with pytest.raises(EvidenceLeak):
        verify_evidence(case)


@needs_bwrap
def test_clean_evidence_without_hidden_paths_is_accepted(tmp_path):
    case = prepare_case(tmp_path)
    case.evidence.mkdir()
    (case.evidence / "projection-files.json").write_text(json.dumps(["src/toy/__main__.py"]))
    verify_evidence(case)


@needs_bwrap
def test_evidence_listing_a_hidden_path_is_refused(tmp_path):
    case = prepare_case(tmp_path)
    case.evidence.mkdir()
    (case.evidence / "projection-files.json").write_text(
        json.dumps(["acceptance/hidden/test_score.py"])
    )
    with pytest.raises(EvidenceLeak):
        verify_evidence(case)


def test_a_sparse_huge_evidence_file_is_refused_before_any_read(tmp_path, monkeypatch):
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    with (evidence / "huge.bin").open("wb") as handle:
        handle.truncate(1 << 40)

    def refuse_read(*_args, **_kwargs):
        raise AssertionError("read past the cap")

    monkeypatch.setattr("tests.e2e.harness._read_bounded", refuse_read)
    with pytest.raises(EvidenceLeak, match="per-file"):
        scan_evidence(evidence)


def test_a_fifo_is_refused_without_blocking(tmp_path):
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    os.mkfifo(evidence / "pipe")
    with pytest.raises(EvidenceLeak, match="special file"):
        scan_evidence(evidence)


def test_a_symlink_is_refused_and_its_canary_target_is_not_read(tmp_path, monkeypatch):
    target = tmp_path / "outside.txt"
    target.write_text(f"private {CANARY}\n")
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "link").symlink_to(target)

    def refuse_read(*_args, **_kwargs):
        raise AssertionError("read via a symlink")

    monkeypatch.setattr("tests.e2e.harness._read_bounded", refuse_read)
    with pytest.raises(EvidenceLeak, match="symlink"):
        scan_evidence(evidence)
    assert target.read_text() == f"private {CANARY}\n"


def test_a_wide_evidence_directory_hits_the_entry_cap(tmp_path):
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    for index in range(20):
        (evidence / f"file-{index}").write_text("clean")
    with pytest.raises(EvidenceLeak, match="entry bound"):
        scan_evidence(evidence, entry_limit=5)


def test_a_deep_evidence_tree_hits_the_depth_cap(tmp_path):
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    current = evidence
    for _ in range(8):
        current = current / "nested"
        current.mkdir()
    with pytest.raises(EvidenceLeak, match="depth bound"):
        scan_evidence(evidence, depth_limit=3)


def test_a_malformed_projection_listing_is_refused(tmp_path):
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "projection-files.json").write_text("{not-json")
    with pytest.raises(EvidenceLeak, match="valid JSON"):
        scan_evidence(evidence)


def test_an_over_cap_projection_listing_is_refused(tmp_path):
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "projection-files.json").write_text(json.dumps(["src/toy/__main__.py"]))
    with pytest.raises(EvidenceLeak, match="per-file"):
        scan_evidence(evidence, file_limit=8)


@needs_bwrap
def test_a_truncated_capture_never_counts_as_proof(tmp_path):
    case = prepare_case(tmp_path)
    case.evidence.mkdir()
    (case.evidence / "TRUNCATED").write_text("entry cap reached; evidence is incomplete\n")
    (case.evidence / "projection-files.json").write_text(json.dumps(["src/toy/__main__.py"]))
    with pytest.raises(EvidenceLeak, match="truncated"):
        verify_evidence(case)


@needs_bwrap
def test_evidence_without_a_projection_listing_is_refused(tmp_path):
    case = prepare_case(tmp_path)
    case.evidence.mkdir()
    (case.evidence / "notes.txt").write_text("clean notes")
    with pytest.raises(EvidenceLeak, match="projection listing"):
        verify_evidence(case)


@needs_bwrap
def test_a_nonzero_agent_never_counts_a_stale_stored_pass(tmp_path):
    case = prepare_case(tmp_path)
    seed_fixed_projection(case)
    result = case.store.latest_result(case.task_id)
    assert result is not None and result.status is Status.PASS
    shutil.rmtree(case.projection)
    config = AgentConfig(
        command=("/bin/sh", "-c", "exit 3"),
        seeds={},
        read_only=(),
        timeout=30.0,
    )
    with pytest.raises(AgentFailed, match="status 3"):
        run_agent(case, config)


@needs_bwrap
def test_an_agent_that_stores_no_gate_result_is_refused(tmp_path):
    case = prepare_case(tmp_path)
    config = AgentConfig(
        command=("/bin/sh", "-c", "exit 0"),
        seeds={},
        read_only=(),
        timeout=30.0,
    )
    with pytest.raises(AgentFailed, match="no gate result"):
        run_agent(case, config)


@needs_bwrap
def test_a_bounded_timeout_still_tears_the_session_down(tmp_path):
    case = prepare_case(tmp_path)
    config = AgentConfig(
        command=("/bin/sh", "-c", "sleep 30"),
        seeds={},
        read_only=(),
        timeout=1.5,
    )
    with pytest.raises(AgentTimedOut):
        run_agent(case, config)
    assert not case.evidence.exists() or case.evidence.is_dir()


@needs_bwrap
def test_the_harness_never_copies_the_original_hidden_tree(tmp_path):
    case = prepare_case(tmp_path)
    assert not (case.projection / "acceptance" / "hidden").exists()
    assert Path(case.store.root) not in case.projection.parents
