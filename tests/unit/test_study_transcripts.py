"""Transcript write-event parser: SYNTHETIC transcripts only, never captured evidence."""

import json
import os
from pathlib import Path

import pytest

from study.classifier import Event
from study.transcripts import observe_writes, parse_transcript_text

ROOTS = ("/work",)
READ_ONLY = "Read-only file system"


def _jl(*objs: object) -> str:
    return "\n".join(json.dumps(o) for o in objs) + "\n"


def _claude_call(tid: str, name: str, path: str) -> dict:
    block = {"type": "tool_use", "id": tid, "name": name, "input": {"file_path": path}}
    return {"type": "assistant", "message": {"content": [block]}}


def _claude_result(tid: str, *, error: bool, content: str = "x") -> dict:
    block = {"type": "tool_result", "tool_use_id": tid, "is_error": error, "content": content}
    return {"type": "user", "message": {"content": [block]}}


def test_claude_marks_refused_and_landed_writes() -> None:
    text = _jl(
        _claude_call("a", "Edit", "/work/acceptance/test_a.py"),
        _claude_result("a", error=True, content=READ_ONLY),
        _claude_call("b", "Write", "/work/src/m.py"),
        _claude_result("b", error=False),
    )
    assert parse_transcript_text("claude", text, ROOTS) == (
        Event("write", "acceptance/test_a.py", ok=False),
        Event("write", "src/m.py", ok=True),
    )


@pytest.mark.parametrize(
    "message",
    [
        "Read-only file system",
        "EROFS: read-only file system",
        "Permission denied",
        "EACCES",
        "Operation not permitted",
        "EPERM",
    ],
)
def test_claude_sandbox_signals_count_as_refused(message: str) -> None:
    text = _jl(
        _claude_call("a", "Edit", "/work/tests/test_core.py"),
        _claude_result("a", error=True, content=message),
    )
    assert parse_transcript_text("claude", text, ROOTS) == (
        Event("write", "tests/test_core.py", ok=False),
    )


@pytest.mark.parametrize(
    "message",
    [
        "String to replace not found in file.",
        "File has not been read yet. Read it first before writing to it.",
        "Failed to find expected lines in tests/test_core.py",
    ],
)
def test_claude_other_failures_are_dropped(message: str) -> None:
    text = _jl(
        _claude_call("a", "Edit", "/work/tests/test_core.py"),
        _claude_result("a", error=True, content=message),
    )
    assert parse_transcript_text("claude", text, ROOTS) == ()


def test_claude_ignores_reads_and_paths_outside_the_work_root() -> None:
    text = _jl(
        _claude_call("a", "Read", "/work/acceptance/test_a.py"),
        _claude_call("b", "Write", "/elsewhere/acceptance/test_a.py"),
        _claude_call("c", "Write", "/work/../acceptance/x.py"),
    )
    assert parse_transcript_text("claude", text, ROOTS) == ()


def test_relative_and_dotted_targets_are_normalised() -> None:
    text = _jl(
        _claude_call("a", "Write", "acceptance/./sub/../test_a.py"),
        _claude_call("b", "Write", "/work/acceptance//test_b.py"),
    )
    got = parse_transcript_text("claude", text, ROOTS)
    assert got is not None
    assert [e.target for e in got] == ["acceptance/test_a.py", "acceptance/test_b.py"]


def test_projection_root_is_recognised() -> None:
    text = _jl(_claude_call("a", "Write", "/state/proj/acceptance/test_a.py"))
    got = parse_transcript_text("claude", text, ("/work", "/state/proj"))
    assert got == (Event("write", "acceptance/test_a.py", ok=True),)


def _codex_patch(cid: str, patch: str) -> dict:
    args = json.dumps({"input": patch})
    payload = {"type": "function_call", "name": "apply_patch", "arguments": args, "call_id": cid}
    return {"type": "response_item", "payload": payload}


def _codex_out(cid: str, output: str) -> dict:
    payload = {"type": "function_call_output", "call_id": cid, "output": output}
    return {"type": "response_item", "payload": payload}


def _codex_output(text: str, exit_code: int) -> str:
    return json.dumps({"output": text, "metadata": {"exit_code": exit_code}})


def test_codex_refusal_only_names_the_files_in_the_error() -> None:
    patch = (
        "*** Begin Patch\n*** Update File: acceptance/test_a.py\n@@\n-a\n+b\n"
        "*** Add File: acceptance/test_new.py\n+x\n*** End Patch"
    )
    refused = _codex_output("Read-only file system: /work/acceptance/test_a.py", 1)
    landed = _codex_output("Success", 0)
    text = _jl(
        _codex_patch("1", patch),
        _codex_out("1", refused),
        _codex_patch("2", "*** Begin Patch\n*** Update File: src/m.py\n*** End Patch"),
        _codex_out("2", landed),
    )
    assert parse_transcript_text("codex", text, ROOTS) == (
        Event("write", "acceptance/test_a.py", ok=False),
        Event("write", "src/m.py", ok=True),
    )


def test_codex_unnamed_refusal_in_a_multi_file_patch_emits_nothing() -> None:
    patch = (
        "*** Begin Patch\n*** Update File: acceptance/test_a.py\n*** End Patch\n"
        "*** Update File: acceptance/test_b.py\n*** End Patch"
    )
    text = _jl(_codex_patch("1", patch), _codex_out("1", _codex_output(READ_ONLY, 1)))
    assert parse_transcript_text("codex", text, ROOTS) == ()


def test_codex_unnamed_refusal_in_a_single_file_patch_is_refused() -> None:
    patch = "*** Begin Patch\n*** Update File: acceptance/test_a.py\n*** End Patch"
    text = _jl(_codex_patch("1", patch), _codex_out("1", _codex_output(READ_ONLY, 1)))
    assert parse_transcript_text("codex", text, ROOTS) == (
        Event("write", "acceptance/test_a.py", ok=False),
    )


def test_codex_non_sandbox_failure_is_dropped() -> None:
    patch = "*** Begin Patch\n*** Update File: src/m.py\n*** End Patch"
    failed = _codex_output("Failed to find expected lines in src/m.py", 1)
    text = _jl(_codex_patch("1", patch), _codex_out("1", failed))
    assert parse_transcript_text("codex", text, ROOTS) == ()


def test_unparseable_or_empty_text_is_unobserved() -> None:
    assert parse_transcript_text("claude", "not json\n\n", ROOTS) is None
    assert parse_transcript_text("claude", "", ROOTS) is None


def test_truncated_or_undecodable_final_line_is_unobserved() -> None:
    good = _jl(_claude_call("a", "Write", "/work/a.py"))
    assert parse_transcript_text("claude", good + '{"type": "assistant", "mess', ROOTS) is None


def test_unknown_vendor_is_unobserved() -> None:
    assert parse_transcript_text("opencode", _jl({"type": "x"}), ROOTS) is None
    assert parse_transcript_text("mystery", _jl({"type": "x"}), ROOTS) is None


def test_transcript_without_write_calls_is_observed_empty() -> None:
    assert parse_transcript_text("claude", _jl({"type": "system"}), ROOTS) == ()


def _put(home: Path, rel: str, text: str) -> None:
    path = home / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_observe_writes_reads_the_vendor_transcript_location(tmp_path: Path) -> None:
    _put(
        tmp_path,
        ".claude/projects/p/s.jsonl",
        _jl(
            _claude_call("a", "Edit", "/work/acceptance/t.py"),
            _claude_result("a", error=True, content=READ_ONLY),
        ),
    )
    assert observe_writes("claude", tmp_path, ROOTS) == (
        Event("write", "acceptance/t.py", ok=False),
    )


def test_observe_writes_none_when_no_transcript_was_captured(tmp_path: Path) -> None:
    assert observe_writes("claude", tmp_path, ROOTS) is None
    assert observe_writes("codex", tmp_path, ROOTS) is None
    assert observe_writes("opencode", tmp_path, ROOTS) is None


def test_observe_writes_none_when_final_line_is_truncated(tmp_path: Path) -> None:
    good = _jl(_claude_call("a", "Write", "/work/a.py"))
    _put(tmp_path, ".claude/projects/p/s.jsonl", good + '{"type": "assistant", "mess')
    assert observe_writes("claude", tmp_path, ROOTS) is None


def test_observe_writes_none_when_a_transcript_cannot_be_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _put(tmp_path, ".claude/projects/p/s.jsonl", _jl(_claude_call("a", "Write", "/work/a.py")))

    real_open = os.open

    def boom(path: object, *args: object, **kwargs: object) -> int:
        if str(path).endswith("s.jsonl"):
            raise OSError("unreadable")
        return real_open(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "open", boom)
    assert observe_writes("claude", tmp_path, ROOTS) is None


def test_observe_writes_does_not_follow_symlinks(tmp_path: Path) -> None:
    outside = tmp_path / "outside.jsonl"
    outside.write_text(_jl(_claude_call("a", "Write", "/work/acceptance/t.py")))
    link = tmp_path / "home" / ".claude" / "projects" / "p" / "s.jsonl"
    link.parent.mkdir(parents=True)
    link.symlink_to(outside)
    assert observe_writes("claude", tmp_path / "home", ROOTS) is None


def test_symlinked_transcript_dir_is_unobserved(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    outside = tmp_path / "outside"
    _put(outside, "projects/p/s.jsonl", _jl(_claude_call("a", "Write", "/work/a.py")))
    (home / ".claude").symlink_to(outside)
    assert observe_writes("claude", home, ROOTS) is None


def test_symlinked_subdirectory_is_unobserved(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    outside = tmp_path / "outside"
    _put(outside, "p/s.jsonl", _jl(_claude_call("a", "Write", "/work/a.py")))
    (home / ".claude" / "projects").symlink_to(outside)
    assert observe_writes("claude", home, ROOTS) is None


def test_symlinked_jsonl_is_skipped_but_its_sibling_is_read(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _put(home, ".claude/projects/p/a.jsonl", _jl(_claude_call("a", "Write", "/work/x.py")))
    outside = tmp_path / "outside.jsonl"
    outside.write_text(_jl(_claude_call("b", "Write", "/work/y.py")))
    (home / ".claude/projects/p/b.jsonl").symlink_to(outside)
    assert observe_writes("claude", home, ROOTS) == (Event("write", "x.py", ok=True),)
