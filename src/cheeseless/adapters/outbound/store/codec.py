"""JSON encoding of frozen tasks and results. Decoding re-validates everything."""

import json
from collections.abc import Mapping
from typing import Any

from cheeseless.domain.contract import canonical_contract_bytes, contract_data, parse_contract
from cheeseless.domain.dlp import Fingerprints
from cheeseless.domain.freeze import FrozenTask, HiddenBlob, freeze_digest
from cheeseless.domain.holdout import SEED_BYTES
from cheeseless.domain.result import CriterionResult, Result
from cheeseless.domain.status import Status
from cheeseless.domain.task import Task
from cheeseless.ports.store import StoreError

TASK_VERSION = 1
RESULT_VERSION = 1


def dumps(data: Mapping[str, Any]) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode()


def loads(raw: bytes, what: str) -> dict[str, Any]:
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise StoreError(f"{what} is not valid JSON") from exc
    if not isinstance(data, dict):
        raise StoreError(f"{what} must be a JSON object")
    return data


def encode_task(frozen: FrozenTask, seed: bytes) -> bytes:
    return dumps(
        {
            "version": TASK_VERSION,
            "task_id": frozen.task.task_id,
            "seed": seed.hex(),
            "contract": contract_data(frozen.contract),
            "baseline_commit": frozen.baseline_commit,
            "projection_tree": frozen.projection_tree,
            "acceptance_tree": frozen.acceptance_tree,
            "hidden_blobs": [[b.path, b.oid] for b in frozen.hidden_blobs],
            "visible": dict(frozen.visible),
            "holdout": dict(frozen.holdout),
        }
    )


def _str_map(value: object) -> dict[str, str]:
    if not isinstance(value, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in value.items()
    ):
        raise ValueError("expected a string mapping")
    return dict(value)


def _build_task(data: dict[str, Any]) -> tuple[FrozenTask, bytes]:
    if data.get("version") != TASK_VERSION:
        raise ValueError("unsupported task version")
    seed = bytes.fromhex(data["seed"])
    if len(seed) != SEED_BYTES:
        raise ValueError("seed has the wrong length")
    contract = parse_contract(data["contract"])
    frozen = FrozenTask(
        task=Task(data["task_id"], contract.lineage_id, contract.criteria),
        contract=contract,
        baseline_commit=data["baseline_commit"],
        projection_tree=data["projection_tree"],
        acceptance_tree=data["acceptance_tree"],
        hidden_blobs=tuple(HiddenBlob(p, o) for p, o in data["hidden_blobs"]),
        visible=_str_map(data["visible"]),
        holdout=_str_map(data["holdout"]),
    )
    digest = freeze_digest(
        frozen.baseline_commit, frozen.acceptance_tree, canonical_contract_bytes(contract)
    )
    if digest != frozen.task.task_id:
        raise ValueError("freeze digest does not match the task id")
    return frozen, seed


def decode_task(raw: bytes, expected_id: str) -> tuple[FrozenTask, bytes]:
    data = loads(raw, "task file")
    try:
        frozen, seed = _build_task(data)
    except (KeyError, TypeError, ValueError) as exc:
        raise StoreError(f"task file is invalid: {exc}") from exc
    if frozen.task.task_id != expected_id:
        raise StoreError("task file names another task")
    return frozen, seed


def encode_fingerprints(fingerprints: Fingerprints) -> bytes:
    return dumps({"version": TASK_VERSION, **fingerprints.to_data()})


def decode_fingerprints(raw: bytes) -> Fingerprints:
    data = loads(raw, "fingerprint file")
    if data.get("version") != TASK_VERSION:
        raise StoreError("unsupported fingerprint version")
    payload = {k: v for k, v in data.items() if k != "version"}
    try:
        return Fingerprints.from_data(payload)
    except (KeyError, TypeError, ValueError) as exc:
        raise StoreError(f"fingerprint file is invalid: {exc}") from exc


def result_data(result: Result) -> dict[str, Any]:
    return {
        "version": RESULT_VERSION,
        "task_id": result.task_id,
        "tree_hash": result.tree_hash,
        "baseline_digest": result.baseline_digest,
        "profile": result.profile,
        "tool_version": result.tool_version,
        "status": result.status.value,
        "criteria": [[c.criterion_id, c.status.value] for c in result.criteria],
        "hmac": result.hmac_tag.hex(),
    }


def encode_result(result: Result, **extra: object) -> bytes:
    return dumps({**result_data(result), **extra})


def decode_result(raw: bytes) -> Result:
    data = loads(raw, "result file")
    try:
        if data.get("version") != RESULT_VERSION:
            raise ValueError("unsupported result version")
        return Result(
            task_id=data["task_id"],
            tree_hash=data["tree_hash"],
            baseline_digest=data["baseline_digest"],
            profile=data["profile"],
            tool_version=data["tool_version"],
            status=Status(data["status"]),
            criteria=tuple(CriterionResult(cid, Status(s)) for cid, s in data["criteria"]),
            hmac_tag=bytes.fromhex(data["hmac"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise StoreError(f"result file is invalid: {exc}") from exc
