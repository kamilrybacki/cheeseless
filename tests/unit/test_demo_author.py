"""The author-side demo masks every hex id and writes a self-contained, escaped player."""

from demo.author_demo import PLACEHOLDER_TASK, Masker, build_player

HEX_A = "a" * 64
HEX_B = "b" * 40
HEX_C = "c" * 64


def test_masker_labels_ids_stably_and_contiguously() -> None:
    mask = Masker()
    first = mask(f"task {HEX_A} commit {HEX_B}")
    again = mask(f"again {HEX_A}")
    assert first == "task <id-1> commit <id-2>"
    assert again == "again <id-1>"


def test_masker_names_proposals_and_keeps_the_placeholder() -> None:
    mask = Masker()
    out = mask(f"proposal_id={HEX_C} placeholder {PLACEHOLDER_TASK}")
    assert out == "proposal_id=<proposal-1> placeholder <placeholder-task>"
    assert mask(f"approve {HEX_C}") == "approve <proposal-1>"


def test_masker_keeps_named_labels_and_leaves_short_hex_alone() -> None:
    mask = Masker()
    mask.name(HEX_B, "<commit-1>")
    assert mask(f"{HEX_B} deadbeef") == "<commit-1> deadbeef"


def test_player_escapes_the_transcript_and_fetches_nothing() -> None:
    cast = (
        '{"version": 2, "width": 100, "height": 30, "timestamp": 0}\n'
        '[0.0, "o", "$ cheeseless <scripted agent: reference>\\r\\n"]\n'
    )
    html = build_player(cast)
    assert html.startswith("<!doctype html>")
    assert "&lt;scripted agent: reference&gt;" in html
    assert "<scripted agent" not in html
    for external in ("http://", "https://", "src=", "@import"):
        assert external not in html


def test_challenge_code_is_found_in_the_prompt() -> None:
    from demo.author_demo import find_challenge_code

    prompt = "  actor:     host\r\ntype this code to confirm: K7QX2MNP\r\n"
    assert find_challenge_code(prompt) == "K7QX2MNP"
    assert find_challenge_code("type this code to confirm: K7Q") is None
    assert find_challenge_code("no prompt yet") is None


def test_pty_operator_answers_a_challenge_on_the_controlling_terminal(tmp_path) -> None:
    import sys

    from demo.author_demo import run_on_pty

    child = tmp_path / "child.py"
    child.write_text(
        "import os\n"
        "fd = os.open('/dev/tty', os.O_RDWR)\n"
        "os.write(fd, b'type this code to confirm: ABCD2345\\n')\n"
        "answer = os.read(fd, 64).strip()\n"
        "print('accepted' if answer == b'ABCD2345' else 'refused')\n",
        encoding="utf-8",
    )
    result = run_on_pty([sys.executable, str(child)], cwd=tmp_path, env={}, timeout=30)
    assert result.returncode == 0
    assert result.typed == "ABCD2345"
    assert "type this code to confirm: ABCD2345" in result.output
    assert "accepted" in result.output


def test_pty_operator_without_a_prompt_types_nothing(tmp_path) -> None:
    import sys

    from demo.author_demo import run_on_pty

    result = run_on_pty(
        [sys.executable, "-c", "print('refused before any prompt')"],
        cwd=tmp_path,
        env={},
        timeout=30,
    )
    assert result.returncode == 0
    assert result.typed is None
    assert "refused before any prompt" in result.output
