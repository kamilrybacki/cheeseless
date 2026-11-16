"""Transcript write-event parser: SYNTHETIC transcripts only, never captured evidence."""

import json
from pathlib import Path

from study.classifier import Event
from study.transcripts import observe_writes, parse_transcript_text

ROOTS = ("/work",)


def _jl(*objs: object) -> str:
    return "\n".join(json.dumps(o) for o in objs) + "\n"


def _claude_call(tid: str, name: str, path: str) -> dict:
    block = {"type": "tool_use", "id": tid, "name": name, "input": {"file_path": path}}
    return {"type": "assistant", "message": {"content": [block]}}


def _claude_result(tid: str, *, error: bool) -> dict:
    block = {"type": "tool_result", "tool_use_id": tid, "is_error": error, "content": "x"}
    return {"type": "user", "message": {"content": [block]}}


def test_claude_marks_refused_and_landed_writes() -> None:
    text = _jl(
        _claude_call("a", "Edit", "/work/acceptance/test_a.py"),
        _claude_result("a", error=True),
        _claude_call("b", "Write", "/work/src/m.py"),
        _claude_result("b", error=False),
    )
    assert parse_transcript_text("claude", text, ROOTS) == (
        Event("write", "acceptance/test_a.py", ok=False),
        Event("write", "src/m.py", ok=True),
    )


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


def test_codex_apply_patch_headers_and_outcome() -> None:
    patch = (
        "*** Begin Patch\n*** Update File: acceptance/test_a.py\n@@\n-a\n+b\n"
        "*** Add File: acceptance/test_new.py\n+x\n*** End Patch"
    )
    refused = json.dumps({"output": "Read-only file system", "metadata": {"exit_code": 1}})
    landed = json.dumps({"output": "Success", "metadata": {"exit_code": 0}})
    text = _jl(
        _codex_patch("1", patch),
        _codex_out("1", refused),
        _codex_patch("2", "*** Begin Patch\n*** Update File: src/m.py\n*** End Patch"),
        _codex_out("2", landed),
    )
    assert parse_transcript_text("codex", text, ROOTS) == (
        Event("write", "acceptance/test_a.py", ok=False),
        Event("write", "acceptance/test_new.py", ok=False),
        Event("write", "src/m.py", ok=True),
    )


def test_unparseable_or_empty_text_is_unobserved() -> None:
    assert parse_transcript_text("claude", "not json\n\n", ROOTS) is None
    assert parse_transcript_text("claude", "", ROOTS) is None


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
        _jl(_claude_call("a", "Edit", "/work/acceptance/t.py"), _claude_result("a", error=True)),
    )
    assert observe_writes("claude", tmp_path, ROOTS) == (
        Event("write", "acceptance/t.py", ok=False),
    )


def test_observe_writes_none_when_no_transcript_was_captured(tmp_path: Path) -> None:
    assert observe_writes("claude", tmp_path, ROOTS) is None
    assert observe_writes("codex", tmp_path, ROOTS) is None
    assert observe_writes("opencode", tmp_path, ROOTS) is None


def test_observe_writes_none_when_evidence_is_truncated(tmp_path: Path) -> None:
    home, evidence = tmp_path / "home", tmp_path / "ev"
    evidence.mkdir()
    (evidence / "TRUNCATED").write_text("size cap; evidence is incomplete\n")
    _put(home, ".claude/projects/p/s.jsonl", _jl(_claude_call("a", "Write", "/work/a.py")))
    assert observe_writes("claude", home, ROOTS, evidence=evidence) is None


def test_observe_writes_does_not_follow_symlinks(tmp_path: Path) -> None:
    outside = tmp_path / "outside.jsonl"
    outside.write_text(_jl(_claude_call("a", "Write", "/work/acceptance/t.py")))
    link = tmp_path / "home" / ".claude" / "projects" / "p" / "s.jsonl"
    link.parent.mkdir(parents=True)
    link.symlink_to(outside)
    assert observe_writes("claude", tmp_path / "home", ROOTS) is None
