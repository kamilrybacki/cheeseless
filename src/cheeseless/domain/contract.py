"""The public contract in ``acceptance/contract/criteria.yaml`` (Q27, Q44).

It names the task lineage, the public criteria and how the candidate is built and
started. It never names test node ids (Q44): which test proves which criterion is
part of the hidden suite.
"""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass

from cheeseless.domain import ids
from cheeseless.domain.task import Criterion

CONTRACT_DIR = "acceptance/contract"
CONTRACT_PATH = f"{CONTRACT_DIR}/criteria.yaml"
ARG_LIMIT = 256
ARGV_LIMIT = 64
BUILD_STEP_LIMIT = 8
ENV_LIMIT = 32
_ENV_KEY = re.compile(r"[A-Z_][A-Z0-9_]{0,63}")
# The candidate sandbox owns these; a contract may not override them.
_RESERVED_ENV = frozenset({"PATH", "HOME", "TMPDIR", "PWD", "SHELL", "USER", "LOGNAME"})
_RESERVED_ENV_PREFIXES = ("LD_", "CHEESELESS_", "PYTEST_", "BASH_")
_TOP_KEYS = frozenset({"task", "build", "run", "env", "criteria"})
_CRITERION_KEYS = frozenset({"id", "description"})


@dataclass(frozen=True, slots=True)
class Contract:
    lineage_id: str
    criteria: tuple[Criterion, ...]
    run: tuple[str, ...]
    build: tuple[tuple[str, ...], ...] = ()
    env: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        ids.lineage_id(self.lineage_id)
        if not self.criteria:
            raise ValueError("the contract needs at least one criterion")
        if len({c.id for c in self.criteria}) != len(self.criteria):
            raise ValueError("duplicate criterion id")
        _argv(self.run, "run")
        if len(self.build) > BUILD_STEP_LIMIT:
            raise ValueError("too many build steps")
        for step in self.build:
            _argv(step, "build step")
        if len(self.env) > ENV_LIMIT or len({k for k, _ in self.env}) != len(self.env):
            raise ValueError("invalid contract env")
        for key, value in self.env:
            check_env_entry(key, value)


def _arg(value: object, what: str) -> str:
    if not isinstance(value, str) or not value or len(value) > ARG_LIMIT:
        raise ValueError(f"invalid {what} argument: {value!r}")
    if not value.isprintable():
        raise ValueError(f"{what} argument must be printable")
    return value


def _argv(argv: object, what: str) -> tuple[str, ...]:
    if not isinstance(argv, tuple | list) or not argv or len(argv) > ARGV_LIMIT:
        raise ValueError(f"{what} must be a non-empty list of strings")
    return tuple(_arg(a, what) for a in argv)


def check_env_entry(key: object, value: object) -> None:
    """Raise ValueError unless ``key=value`` may be set for a candidate."""
    if not isinstance(key, str) or _ENV_KEY.fullmatch(key) is None:
        raise ValueError(f"invalid env name: {key!r}")
    if key in _RESERVED_ENV or key.startswith(_RESERVED_ENV_PREFIXES):
        raise ValueError(f"env name {key} is reserved")
    _arg(value, f"env {key}")


def _criterion(item: object) -> Criterion:
    if not isinstance(item, Mapping) or set(item) != _CRITERION_KEYS:
        raise ValueError("each criterion needs exactly 'id' and 'description'")
    description = item["description"]
    if isinstance(description, str) and "::" in description:
        raise ValueError("criterion descriptions may not name test node ids (Q44)")
    return Criterion(ids.criterion_id(item["id"]), description)


def parse_contract(data: object) -> Contract:
    """Validate the parsed YAML document. Unknown keys are refused (fail closed)."""
    if not isinstance(data, Mapping):
        raise ValueError("criteria.yaml must be a mapping")
    unknown = set(data) - _TOP_KEYS
    if unknown:
        raise ValueError(f"unknown contract keys: {', '.join(sorted(map(str, unknown)))}")
    for key in ("task", "run", "criteria"):
        if key not in data:
            raise ValueError(f"criteria.yaml is missing '{key}'")
    criteria = data["criteria"]
    if not isinstance(criteria, list):
        raise ValueError("'criteria' must be a list")
    build = data.get("build") or []
    if not isinstance(build, list):
        raise ValueError("'build' must be a list of argv lists")
    env = data.get("env") or {}
    if not isinstance(env, Mapping):
        raise ValueError("'env' must be a mapping")
    return Contract(
        lineage_id=ids.lineage_id(data["task"]),
        criteria=tuple(_criterion(c) for c in criteria),
        run=_argv(data["run"], "run"),
        build=tuple(_argv(step, "build step") for step in build),
        env=tuple(sorted((str(k), v) for k, v in env.items())),
    )


def contract_data(contract: Contract) -> dict[str, object]:
    """The inverse of ``parse_contract``: plain data that parses back to ``contract``."""
    return {
        "task": contract.lineage_id,
        "criteria": [{"id": c.id, "description": c.description} for c in contract.criteria],
        "run": list(contract.run),
        "build": [list(step) for step in contract.build],
        "env": dict(contract.env),
    }


def canonical_contract_bytes(contract: Contract) -> bytes:
    """Deterministic encoding bound into the task id (``freeze_digest``)."""
    return json.dumps(
        contract_data(contract), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
