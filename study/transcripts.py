"""Write/edit tool calls recovered from agent session transcripts (symmetric across arms).

A write refused by a read-only mount leaves no diff, so the classifier cannot see it. This
parser reads the transcript the agent itself kept under its HOME and returns ``write`` events,
``ok=False`` when the tool call failed. The same parser runs for the naive and the cheeseless arm.

Contract: ``None`` means *unobserved* (no transcript captured, unknown vendor, unparseable,
or the evidence copy was truncated), never an empty tuple. ``()`` means a transcript was read
and held no write call. Only structured file-edit tool calls are seen: a write made through a
shell redirect or an interpreter is not, so such agents are undercounted. Transcripts are
read as data and never executed; symlinks are not followed.
"""

from __future__ import annotations

import json
import posixpath
import re
from collections.abc import Callable, Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any

from study.classifier import Event

MAX_TRANSCRIPT_BYTES = 64 << 20
TRUNCATED_MARKER = "TRUNCATED"

_CLAUDE_WRITE_TOOLS = {
    "Write": "file_path",
    "Edit": "file_path",
    "MultiEdit": "file_path",
    "NotebookEdit": "notebook_path",
}
_PATCH_HEADER = re.compile(r"^\*\*\* (?:Update|Add|Delete) File: (.+?)\s*$", re.MULTILINE)
_PATCH_MOVE = re.compile(r"^\*\*\* Move to: (.+?)\s*$", re.MULTILINE)
_REFUSAL_TEXT = re.compile(
    r"read-only file system|permission denied|operation not permitted|verification failed"
    r"|failed to",
    re.IGNORECASE,
)
# vendor label fragment -> (parser family, transcript globs under the agent HOME)
_FAMILIES: dict[str, tuple[str, tuple[str, ...]]] = {
    "claude": ("claude", (".claude/projects/**/*.jsonl",)),
    "anthropic": ("claude", (".claude/projects/**/*.jsonl",)),
    "codex": ("codex", (".codex/sessions/**/rollout-*.jsonl",)),
    "openai": ("codex", (".codex/sessions/**/rollout-*.jsonl",)),
}


def _family(vendor: str) -> tuple[str, tuple[str, ...]] | None:
    label = vendor.lower()
    for fragment, family in _FAMILIES.items():
        if fragment in label:
            return family
    return None  # includes opencode: its session store format is not captured/parsed


def _relative(target: str, roots: tuple[str, ...]) -> str | None:
    """The work-tree-relative path of ``target``, or None when it lies outside every root."""
    if not target or "\0" in target:
        return None
    if not posixpath.isabs(target):
        rel = posixpath.normpath(target)
        return None if rel.startswith("..") or rel == "." else rel
    path = posixpath.normpath(target)
    for root in roots:
        base = posixpath.normpath(root)
        if path.startswith(base.rstrip("/") + "/"):
            return posixpath.relpath(path, base)
    return None


def _records(text: str) -> list[Mapping[str, Any]] | None:
    records: list[Mapping[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            records.append(obj)
    return records or None


def _claude_calls(records: Iterable[Mapping[str, Any]]) -> Iterator[tuple[str, str, bool | None]]:
    """(call id, target, failed?) per write tool call; failed is None when no result was seen."""
    calls: dict[str, str] = {}
    results: dict[str, bool] = {}
    for rec in records:
        message = rec.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and block.get("name") in _CLAUDE_WRITE_TOOLS:
                key = _CLAUDE_WRITE_TOOLS[str(block["name"])]
                args = block.get("input")
                target = args.get(key) if isinstance(args, dict) else None
                if isinstance(target, str):
                    calls[str(block.get("id"))] = target
            elif block.get("type") == "tool_result":
                results[str(block.get("tool_use_id"))] = bool(block.get("is_error"))
    for call_id, target in calls.items():
        yield call_id, target, results.get(call_id)


def _codex_failed(output: object) -> bool:
    text = output if isinstance(output, str) else json.dumps(output)
    try:
        parsed = json.loads(text)
    except ValueError:
        parsed = None
    if isinstance(parsed, dict):
        meta = parsed.get("metadata")
        if isinstance(meta, dict) and isinstance(meta.get("exit_code"), int):
            return bool(meta["exit_code"] != 0)
        text = str(parsed.get("output", text))
    return bool(_REFUSAL_TEXT.search(text))


def _codex_patch_text(payload: Mapping[str, Any]) -> str | None:
    raw = payload.get("arguments", payload.get("input"))
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except ValueError:
            return raw
        if isinstance(parsed, dict):
            inner = parsed.get("input")
            return inner if isinstance(inner, str) else None
        return raw
    return None


def _codex_calls(records: Iterable[Mapping[str, Any]]) -> Iterator[tuple[str, str, bool | None]]:
    patches: dict[str, str] = {}
    results: dict[str, bool] = {}
    for rec in records:
        payload = rec.get("payload")
        if rec.get("type") != "response_item" or not isinstance(payload, dict):
            continue
        kind, call_id = payload.get("type"), str(payload.get("call_id"))
        if kind in ("function_call", "custom_tool_call") and payload.get("name") == "apply_patch":
            patch = _codex_patch_text(payload)
            if patch is not None:
                patches[call_id] = patch
        elif kind in ("function_call_output", "custom_tool_call_output"):
            results[call_id] = _codex_failed(payload.get("output"))
    for call_id, patch in patches.items():
        for target in (*_PATCH_HEADER.findall(patch), *_PATCH_MOVE.findall(patch)):
            yield call_id, target, results.get(call_id)


_PARSERS: dict[str, Callable[[Iterable[Mapping[str, Any]]], Iterator[tuple[str, str, bool | None]]]]
_PARSERS = {"claude": _claude_calls, "codex": _codex_calls}


def parse_transcript_text(
    vendor: str, text: str, roots: tuple[str, ...]
) -> tuple[Event, ...] | None:
    """Write events in one transcript, or None when the vendor/format is not parsed."""
    family = _family(vendor)
    records = _records(text)
    if family is None or records is None:
        return None
    events = []
    for _call, target, failed in _PARSERS[family[0]](records):
        rel = _relative(target, roots)
        if rel is not None:
            events.append(Event("write", rel, ok=not failed))
    return tuple(events)


def _transcript_files(home: Path, patterns: tuple[str, ...]) -> list[Path]:
    found: list[Path] = []
    for pattern in patterns:
        for path in sorted(home.glob(pattern)):
            if (
                path.is_symlink()
                or not path.is_file()
                or path.stat().st_size > MAX_TRANSCRIPT_BYTES
            ):
                continue
            found.append(path)
    return found


def observe_writes(
    vendor: str, home: Path, roots: tuple[str, ...], *, evidence: Path | None = None
) -> tuple[Event, ...] | None:
    """Write events from the agent's own transcript under ``home``; None when unobserved."""
    family = _family(vendor)
    if family is None or (evidence is not None and (evidence / TRUNCATED_MARKER).exists()):
        return None
    files = _transcript_files(home, family[1])
    if not files:
        return None
    events: list[Event] = []
    for path in files:
        try:
            parsed = parse_transcript_text(vendor, path.read_text(errors="replace"), roots)
        except OSError:
            return None
        if parsed is None:
            return None
        events.extend(parsed)
    return tuple(events)
