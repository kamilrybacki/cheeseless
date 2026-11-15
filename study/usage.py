"""Optional token and cost channel from the operator wrapper.

Only an explicitly configured parent-owned directory is read, with per-attempt filenames.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

USAGE_FILE = "usage.json"
MAX_BYTES = 4096


@dataclass(frozen=True)
class Usage:
    tokens: int | None
    cost_usd: float | None


def _num(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if value != value or value < 0 or value > 1e12:  # NaN, negative or absurd
        return None
    return float(value)


def read_usage(root: Path | None, attempt_id: str | None = None) -> Usage | None:
    if root is None or attempt_id is None:
        return None
    path = root / f"{attempt_id}.json"
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
            return None
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    tokens = _num(data.get("tokens"))
    cost = _num(data.get("cost_usd"))
    if tokens is None and cost is None:
        return None
    return Usage(int(tokens) if tokens is not None else None, cost)
