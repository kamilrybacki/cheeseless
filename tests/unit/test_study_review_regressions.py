import json
import subprocess

import pytest

from study import agent_config, manifest, real
from tests.unit.test_study_run_report import _env


def test_study_malformed_complete_line_is_never_repaired(tmp_path):
    path = tmp_path / "m.jsonl"
    data = b'{"phase":"pilot"}\nnot-json\n{"partial":'
    path.write_bytes(data)
    with pytest.raises(manifest.CorruptManifest, match="record 2"):
        manifest.validate_resume(path, "pilot", repair_tail=True)
    assert path.read_bytes() == data
    assert not path.with_suffix(".jsonl.corrupt-tail").exists()


def test_study_repair_archive_is_private_even_with_permissive_umask(tmp_path):
    import os
    import stat

    path = tmp_path / "m.jsonl"
    path.write_bytes(b'{"phase":"pilot"}\n{"partial":')
    old = os.umask(0o022)
    try:
        manifest.validate_resume(path, "pilot", repair_tail=True)
    finally:
        os.umask(old)
    archive = path.with_suffix(".jsonl.corrupt-tail")
    assert archive.read_bytes() == b'{"partial":'
    assert stat.S_IMODE(archive.stat().st_mode) == 0o600


def test_study_naive_rejects_host_only_command(tmp_path):
    env = _env(tmp_path, "#!/bin/sh\nexit 0\n")
    env[agent_config.NAIVE_COMMAND_ENV] = env[agent_config.COMMAND_ENV]
    with pytest.raises(agent_config.AgentUnavailable, match="sandbox namespace"):
        agent_config.load_study_agent(env)


def test_study_naive_accepts_system_and_seeded_wrapper(tmp_path):
    env = _env(tmp_path, "#!/bin/sh\nexit 0\n")
    assert agent_config.load_study_agent(env).naive_command[0] == "/bin/sh"
    env[agent_config.NAIVE_COMMAND_ENV] = json.dumps(["/agent-home/agent.sh"])
    agent = agent_config.load_study_agent(env)
    assert agent.naive_command == ("/agent-home/agent.sh",)
    assert len(agent.wrapper_sha256) == 64


def test_study_process_explicit_descriptor_hygiene(tmp_path, monkeypatch):
    calls = []

    class Process:
        def wait(self, *, timeout):
            return 0

    def popen(*args, **kwargs):
        calls.append(kwargs)
        return Process()

    monkeypatch.setattr(real.subprocess, "Popen", popen)
    assert real._run_process(["/bin/true"], tmp_path, {}, 1) == 0
    assert calls[0]["close_fds"] is True
    assert calls[0]["pass_fds"] == ()
    assert calls[0]["stdin"] == subprocess.DEVNULL


def test_study_seed_copy_preserves_executable(tmp_path):
    source = tmp_path / "source"
    source.write_text("#!/bin/sh\nexit 0\n")
    source.chmod(0o700)
    target = tmp_path / "target"
    real._copy_seed(source, target)
    assert target.stat().st_mode & 0o100


@pytest.mark.parametrize("program", ["relative/agent.sh", "/work/missing", "/agent-home/missing"])
def test_study_naive_rejects_unavailable_namespace_paths(tmp_path, program):
    env = _env(tmp_path, "#!/bin/sh\nexit 0\n")
    env[agent_config.NAIVE_COMMAND_ENV] = json.dumps([program])
    with pytest.raises(agent_config.AgentUnavailable, match="sandbox namespace"):
        agent_config.load_study_agent(env)
