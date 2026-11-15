"""Operator-owned real-agent configuration (opt-in; never reads credential contents).

Mirrors ``tests/e2e/harness.py`` with ``CHEESELESS_STUDY_AGENT*`` names. The study never
inspects, logs or stores a credential value: seeds and read-only mounts are paths the operator
owns, and only the validated host loader opens them.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from cheeseless.domain.egress_policy import EgressPolicy, PolicyError
from cheeseless.session_options import SessionOptionError, load_egress_policy

OPT_IN_ENV = "CHEESELESS_STUDY_AGENT"
COMMAND_ENV = "CHEESELESS_STUDY_AGENT_CMD"
NAIVE_COMMAND_ENV = "CHEESELESS_STUDY_AGENT_CMD_NAIVE"
SEED_ENV = "CHEESELESS_STUDY_AGENT_SEED"
READ_ONLY_ENV = "CHEESELESS_STUDY_AGENT_RO"
TIMEOUT_ENV = "CHEESELESS_STUDY_AGENT_TIMEOUT"
EGRESS_ENV = "CHEESELESS_STUDY_AGENT_EGRESS"
ID_ENV = "CHEESELESS_STUDY_AGENT_ID"
DEFAULT_TIMEOUT = 900.0


class AgentUnavailable(RuntimeError):
    """The operator did not opt in or gave an unusable configuration."""


@dataclass(frozen=True)
class AgentId:
    vendor: str
    model: str
    version: str

    @property
    def label(self) -> str:
        return f"{self.vendor}/{self.model}/{self.version}"


@dataclass(frozen=True)
class StudyAgent:
    agent: AgentId
    command: tuple[str, ...]
    naive_command: tuple[str, ...]
    seeds: Mapping[str, Path]
    read_only: tuple[Path, ...]
    timeout: float
    egress_policy: EgressPolicy | None = None


def _json_env(env: Mapping[str, str], name: str, default: object) -> object:
    raw = env.get(name, "")
    if not raw.strip():
        return default
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise AgentUnavailable(f"{name} must be valid JSON") from exc


def parse_command(name: str, value: str) -> tuple[str, ...]:
    try:
        parsed = json.loads(value)
    except ValueError:
        parsed = shlex.split(value)
    if (
        not isinstance(parsed, list)
        or not parsed
        or not all(isinstance(i, str) and i for i in parsed)
    ):
        raise AgentUnavailable(f"{name} must be a non-empty argv list")
    return tuple(parsed)


def _executable(command: tuple[str, ...], name: str) -> None:
    if shutil.which(command[0]) is None and not Path(command[0]).is_file():
        raise AgentUnavailable(f"{name} is not an executable command")


def _agent_id(env: Mapping[str, str]) -> AgentId:
    raw = _json_env(env, ID_ENV, None)
    keys = ("vendor", "model", "version")
    if not isinstance(raw, dict) or not all(isinstance(raw.get(k), str) and raw[k] for k in keys):
        raise AgentUnavailable(f'{ID_ENV} must be JSON {{"vendor","model","version"}} (strings)')
    return AgentId(raw["vendor"], raw["model"], raw["version"])


def _egress(env: Mapping[str, str]) -> EgressPolicy | None:
    raw = env.get(EGRESS_ENV, "").strip()
    if not raw:
        return None
    try:
        return load_egress_policy(Path(raw))
    except (SessionOptionError, PolicyError) as exc:
        raise AgentUnavailable(f"{EGRESS_ENV} is not usable: {type(exc).__name__}") from exc


def _timeout(env: Mapping[str, str]) -> float:
    text = env.get(TIMEOUT_ENV, "")
    try:
        value = float(text) if text else DEFAULT_TIMEOUT
    except ValueError as exc:
        raise AgentUnavailable(f"{TIMEOUT_ENV} must be a number") from exc
    if not 1.0 <= value <= 3600.0:
        raise AgentUnavailable(f"{TIMEOUT_ENV} must be between 1 and 3600 seconds")
    return value


def load_study_agent(env: Mapping[str, str] | None = None) -> StudyAgent:
    """Resolve the operator agent. Refuses unless ``CHEESELESS_STUDY_AGENT=1``."""
    values = os.environ if env is None else env
    if values.get(OPT_IN_ENV) != "1":
        raise AgentUnavailable(f"real mode is opt-in: set {OPT_IN_ENV}=1 (and see docs/study)")
    raw = values.get(COMMAND_ENV, "").strip()
    if not raw:
        raise AgentUnavailable(f"set {COMMAND_ENV} to the operator wrapper command")
    command = parse_command(COMMAND_ENV, raw)
    _executable(command, COMMAND_ENV)
    raw_naive = values.get(NAIVE_COMMAND_ENV, "").strip()
    naive = parse_command(NAIVE_COMMAND_ENV, raw_naive) if raw_naive else command
    _executable(naive, NAIVE_COMMAND_ENV)

    raw_seeds = _json_env(values, SEED_ENV, {})
    if not isinstance(raw_seeds, dict) or not all(
        isinstance(k, str) and isinstance(v, str) and k and v for k, v in raw_seeds.items()
    ):
        raise AgentUnavailable(f"{SEED_ENV} must be a JSON object of REL: SOURCE")
    raw_ro = _json_env(values, READ_ONLY_ENV, [])
    if not isinstance(raw_ro, list) or not all(isinstance(v, str) and v for v in raw_ro):
        raise AgentUnavailable(f"{READ_ONLY_ENV} must be a JSON list of paths")
    return StudyAgent(
        agent=_agent_id(values),
        command=command,
        naive_command=naive,
        seeds={rel: Path(src) for rel, src in raw_seeds.items()},
        read_only=tuple(Path(p) for p in raw_ro),
        timeout=_timeout(values),
        egress_policy=_egress(values),
    )
