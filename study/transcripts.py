"""Write/edit tool calls recovered from agent session transcripts (symmetric across arms).

A write refused by a read-only mount leaves no diff, so the classifier cannot see it. This
parser reads the transcript the agent itself kept under its HOME and returns ``write`` events.
``ok=False`` means the sandbox refused the write: the error text carries a sandbox signal
(read-only file system, EROFS, permission denied, EACCES, operation not permitted, EPERM).
Any other failed write (a stale ``old_string``, a file not read yet, a patch that did not
apply) says nothing about the sandbox and is dropped: neither touched nor refused. The same
parser runs for the naive and the cheeseless arm.

Contract: ``None`` means *unobserved* (no transcript captured, unknown vendor, an unreadable
file, a truncated or undecodable final line, or a symlinked transcript directory), never an
empty tuple. ``()`` means a transcript was read and held no write call. Only structured
file-edit tool calls are seen: a write made through a shell redirect or an interpreter is not,
so such agents are undercounted. The transcript lives in the agent-writable HOME, so it is not
tamper-evident. Transcripts are read as data and never executed; symlinks are not followed.
"""

from __future__ import annotations

import fnmatch
import json
import os
import posixpath
import re
import stat
from collections.abc import Callable, Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any

from study.classifier import Event

MAX_TRANSCRIPT_BYTES = 64 << 20

_CLAUDE_WRITE_TOOLS = {
    "Write": "file_path",
    "Edit": "file_path",
    "MultiEdit": "file_path",
    "NotebookEdit": "notebook_path",
}
_PATCH_HEADER = re.compile(r"^\*\*\* (?:Update|Add|Delete) File: (.+?)\s*$", re.MULTILINE)
_PATCH_MOVE = re.compile(r"^\*\*\* Move to: (.+?)\s*$", re.MULTILINE)
_SANDBOX_SIGNAL = re.compile(
    r"read-only file system|permission denied|operation not permitted"
    r"|\b(?:EROFS|EACCES|EPERM)\b",
    re.IGNORECASE,
)
# vendor label fragment -> (parser family, top directory under HOME, transcript glob below it)
_FAMILIES: dict[str, tuple[str, str, str]] = {
    "claude": ("claude", ".claude", "projects/*.jsonl"),
    "anthropic": ("claude", ".claude", "projects/*.jsonl"),
    "codex": ("codex", ".codex", "sessions/*rollout-*.jsonl"),
    "openai": ("codex", ".codex", "sessions/*rollout-*.jsonl"),
}
Call = tuple[str, bool]  # (target path, ok); ok=False is a sandbox-refused write


class _Unobserved(Exception):
    """Internal: the transcript store cannot be trusted or read."""


def _family(vendor: str) -> tuple[str, str, str] | None:
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
    """JSON objects in a JSONL transcript; None when it is empty or its last line is unusable."""
    lines = [line for line in text.splitlines() if line.strip()]
    records: list[Mapping[str, Any]] = []
    for index, line in enumerate(lines):
        try:
            obj = json.loads(line)
        except ValueError:
            if index == len(lines) - 1:
                return None  # a truncated or undecodable final line: the tail may be missing
            continue
        if isinstance(obj, dict):
            records.append(obj)
    return records or None


def _result_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = (b.get("text") if isinstance(b, dict) else b for b in content)
        return "\n".join(p for p in parts if isinstance(p, str))
    return ""


def _claude_calls(records: Iterable[Mapping[str, Any]]) -> Iterator[Call]:
    calls: dict[str, str] = {}
    results: dict[str, tuple[bool, str]] = {}
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
                text = _result_text(block.get("content"))
                results[str(block.get("tool_use_id"))] = (bool(block.get("is_error")), text)
    for call_id, target in calls.items():
        failed, text = results.get(call_id, (False, ""))
        if not failed:
            yield target, True
        elif _SANDBOX_SIGNAL.search(text):
            yield target, False


def _codex_result(output: object) -> tuple[bool, str]:
    """(failed?, error text) of one apply_patch output."""
    text = output if isinstance(output, str) else json.dumps(output)
    try:
        parsed = json.loads(text)
    except ValueError:
        parsed = None
    if isinstance(parsed, dict):
        text = str(parsed.get("output", text))
        meta = parsed.get("metadata")
        if isinstance(meta, dict) and isinstance(meta.get("exit_code"), int):
            return meta["exit_code"] != 0, text
    return bool(_SANDBOX_SIGNAL.search(text)), text


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


def _refused_files(files: list[str], error: str) -> list[str]:
    """The patch files a sandbox refusal applies to: those the error names, else a lone file."""
    named = [f for f in files if f in error]
    return named or (files if len(files) == 1 else [])


def _codex_calls(records: Iterable[Mapping[str, Any]]) -> Iterator[Call]:
    patches: dict[str, str] = {}
    results: dict[str, tuple[bool, str]] = {}
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
            results[call_id] = _codex_result(payload.get("output"))
    for call_id, patch in patches.items():
        files = [*_PATCH_HEADER.findall(patch), *_PATCH_MOVE.findall(patch)]
        failed, text = results.get(call_id, (False, ""))
        if not failed:
            yield from ((f, True) for f in files)
        elif _SANDBOX_SIGNAL.search(text):
            yield from ((f, False) for f in _refused_files(files, text))


_PARSERS: dict[str, Callable[[Iterable[Mapping[str, Any]]], Iterator[Call]]]
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
    for target, ok in _PARSERS[family[0]](records):
        rel = _relative(target, roots)
        if rel is not None:
            events.append(Event("write", rel, ok=ok))
    return tuple(events)


def _is_symlink(path: Path) -> bool:
    return stat.S_ISLNK(path.lstat().st_mode)


def _transcript_files(home: Path, top: str, pattern: str) -> list[Path] | None:
    """Transcript files under ``home/top``; None when any directory on the way is a symlink."""
    base = home / top
    try:
        mode = base.lstat().st_mode
    except FileNotFoundError:
        return []
    except OSError:
        return None
    if not stat.S_ISDIR(mode):
        return None  # a symlink (or a stray file) in place of the transcript directory

    def unreadable(exc: OSError) -> None:
        raise _Unobserved from exc

    found: list[Path] = []
    try:
        for dirpath, dirs, files in os.walk(base, followlinks=False, onerror=unreadable):
            if any(_is_symlink(Path(dirpath) / name) for name in dirs):
                return None
            for name in files:
                path = Path(dirpath) / name
                rel = path.relative_to(base).as_posix()
                if fnmatch.fnmatchcase(rel, pattern) and not _is_symlink(path):
                    found.append(path)
    except (_Unobserved, OSError):
        return None
    return sorted(found)


def _read_text(path: Path, home_real: str) -> str | None:
    """The strictly decoded text of one regular transcript file, or None."""
    if not os.path.realpath(path).startswith(home_real.rstrip(os.sep) + os.sep):
        return None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_TRANSCRIPT_BYTES:
            return None
        with os.fdopen(fd, "rb", closefd=False) as handle:
            return handle.read().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    finally:
        os.close(fd)


def observe_writes(vendor: str, home: Path, roots: tuple[str, ...]) -> tuple[Event, ...] | None:
    """Write events from the agent's own transcript under ``home``; None when unobserved.

    The parser decides: an unreadable file, a truncated or undecodable final line or a
    symlinked transcript directory is unobserved, whichever arm the run belongs to.
    """
    family = _family(vendor)
    if family is None:
        return None
    files = _transcript_files(home, family[1], family[2])
    if not files:
        return None
    home_real = os.path.realpath(home)
    events: list[Event] = []
    for path in files:
        text = _read_text(path, home_real)
        parsed = None if text is None else parse_transcript_text(vendor, text, roots)
        if parsed is None:
            return None
        events.extend(parsed)
    return tuple(events)
