"""Append-only, redacted JSONL manifest: one line per run attempt.

Every record is scanned before it is written. A hit raises ``RedactionError`` and nothing is
written. The manifest never holds credentials, home paths, usernames or hidden-test content.
"""

from __future__ import annotations

import getpass
import hashlib
import json
import os
import re
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from study.scorer import INFRA
from study.tasks import CANARY, SENTINEL

SCHEMA_VERSION = 1
_PATTERNS = (
    re.compile(r"/home/[^/\s\"]+"),
    re.compile(r"/Users/[^/\s\"]+"),
    re.compile(r"/root\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"\bghp_[A-Za-z0-9]{8,}"),
    re.compile(r"Bearer\s+\S+"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}"),
    re.compile(r"\bAKIA[0-9A-Z]{8,}"),
)


class RedactionError(ValueError):
    """A record contained something that must never be stored (the message names no content)."""


def _literals() -> list[str]:
    out = [CANARY, SENTINEL]
    home = os.environ.get("HOME", "")
    if len(home) > 1:
        out.append(home)
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001 - getuser can fail in odd sandboxes
        user = ""
    if len(user) >= 3:
        out.append(user)
    return out


def scan_text(text: str) -> str | None:
    """Return a label for the first forbidden thing found, never the thing itself."""
    for lit in _literals():
        if lit in text:
            return "literal"
    for i, pat in enumerate(_PATTERNS):
        if pat.search(text):
            return f"pattern-{i}"
    return None


def make_run_id(study_id: str, seed: int, arm: str, task_id: str, agent: str, rep: int) -> str:
    raw = "|".join([study_id, str(seed), arm, task_id, agent, str(rep)])
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def dumps(record: Mapping[str, Any]) -> str:
    line = json.dumps(record, sort_keys=True, ensure_ascii=True)
    hit = scan_text(line)
    if hit:
        raise RedactionError(f"record refused by redaction scan ({hit})")
    return line


def append(path: Path, record: Mapping[str, Any]) -> None:
    line = dumps({"schema_version": SCHEMA_VERSION, **record})
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def read_all(path: Path) -> Iterator[dict[str, Any]]:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.strip():
            yield json.loads(raw)


def latest_runs(path: Path) -> dict[str, dict[str, Any]]:
    """Latest ``record_type == "run"`` line per run id (later lines supersede retried infra)."""
    out: dict[str, dict[str, Any]] = {}
    for rec in read_all(path):
        if rec.get("record_type", "run") == "run":
            out[rec["run_id"]] = rec
    return out


def adjudications(path: Path) -> dict[str, dict[str, Any]]:
    return {r["run_id"]: r for r in read_all(path) if r.get("record_type") == "adjudication"}


def completed_ids(path: Path) -> set[str]:
    """Run ids that need no retry: everything except an infra-error."""
    return {rid for rid, rec in latest_runs(path).items() if rec.get("status") != INFRA}


def next_attempt(path: Path, run_id: str) -> int:
    prev = latest_runs(path).get(run_id)
    return 1 if prev is None else int(prev.get("attempt", 1)) + 1
