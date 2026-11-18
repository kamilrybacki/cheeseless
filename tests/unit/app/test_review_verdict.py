"""The reviewer's verdict is parsed, never inferred from an exit code (ADR 0015 decision 3)."""

import pytest

from cheeseless.app.authoring import parse_review_verdict, read_review_verdict


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("VERDICT: pass\n", True),
        ("VERDICT: PASS", True),
        ("  verdict: green  ", True),
        ("VERDICT: fail", False),
        ("VERDICT: red", False),
        ("I looked at the tests.\n\nVERDICT: pass\n", True),
    ],
)
def test_a_structured_line_gives_the_verdict(text: str, expected: bool) -> None:
    assert parse_review_verdict(text) is expected


@pytest.mark.parametrize("text", ["", "looks fine to me", "VERDICT: maybe", "VERDICT:"])
def test_no_usable_line_is_not_a_failure(text: str) -> None:
    """A reviewer that said nothing has not reviewed; recording `False` would invent a no."""
    assert parse_review_verdict(text) is None


def test_the_last_usable_line_wins() -> None:
    assert parse_review_verdict("VERDICT: fail\nVERDICT: pass") is True


def test_reading_a_missing_verdict_file_yields_none(tmp_path) -> None:
    assert read_review_verdict(tmp_path) is None


def test_reading_a_verdict_file_from_the_home(tmp_path) -> None:
    (tmp_path / "verdict.txt").write_text("VERDICT: pass\n")
    assert read_review_verdict(tmp_path) is True


def test_a_verdict_file_is_read_as_untrusted_text(tmp_path) -> None:
    """It is written by a model fed untrusted input, so it is bounded, not trusted."""
    (tmp_path / "verdict.txt").write_text("VERDICT: pass\n" + "x" * 200_000)
    assert read_review_verdict(tmp_path) is True


def test_a_directory_named_like_the_verdict_is_not_a_verdict(tmp_path) -> None:
    (tmp_path / "verdict.txt").mkdir()
    assert read_review_verdict(tmp_path) is None
