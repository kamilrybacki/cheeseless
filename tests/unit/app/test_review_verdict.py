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
        ("\n  VERDICT: pass  \n\n", True),
    ],
)
def test_a_structured_line_gives_the_verdict(text: str, expected: bool) -> None:
    assert parse_review_verdict(text) is expected


@pytest.mark.parametrize("text", ["", "looks fine to me", "VERDICT: maybe", "VERDICT:"])
def test_no_usable_line_is_not_a_failure(text: str) -> None:
    """A reviewer that said nothing has not reviewed; recording `False` would invent a no."""
    assert parse_review_verdict(text) is None


@pytest.mark.parametrize(
    "text",
    [
        "# VERDICT: pass\nVERDICT: fail\n",
        "VERDICT: pass\nVERDICT: fail\n",
        "I looked at the tests and they seem fine.\nVERDICT: pass\n",
    ],
)
def test_anything_besides_the_one_line_is_no_verdict(text: str) -> None:
    """The author writes the tests; a reviewer copying a comment must not cast a verdict.

    Only a file holding the verdict and nothing else counts, so text lifted from the suite
    cannot arrive as a verdict.
    """
    assert parse_review_verdict(text) is None


def test_reading_a_missing_verdict_file_yields_none(tmp_path) -> None:
    assert read_review_verdict(tmp_path) is None


def test_reading_a_verdict_file_from_the_home(tmp_path) -> None:
    (tmp_path / "verdict.txt").write_text("VERDICT: pass\n")
    assert read_review_verdict(tmp_path) is True


def test_a_verdict_file_is_read_as_untrusted_text(tmp_path) -> None:
    """It is written by a model fed untrusted input, so it is bounded, not trusted."""
    (tmp_path / "verdict.txt").write_text("x" * 200_000 + "\nVERDICT: pass\n")
    assert read_review_verdict(tmp_path) is None
    # A huge file is not read in full: the bound is what keeps a runaway reviewer cheap.
    (tmp_path / "verdict.txt").write_text("VERDICT: pass\n")
    assert read_review_verdict(tmp_path) is True


def test_a_directory_named_like_the_verdict_is_not_a_verdict(tmp_path) -> None:
    (tmp_path / "verdict.txt").mkdir()
    assert read_review_verdict(tmp_path) is None


def test_a_symlinked_verdict_is_not_followed(tmp_path) -> None:
    """The reviewer owns its HOME, so it can make verdict.txt a symlink.

    Following one would parse a file of the reviewer's choosing as its own verdict; the
    protection is O_NOFOLLOW, and this fails if that flag is dropped.
    """
    target = tmp_path / "elsewhere"
    target.write_text("VERDICT: pass\n")
    (tmp_path / "verdict.txt").symlink_to(target)
    assert read_review_verdict(tmp_path) is None


def test_the_reviewer_is_not_shown_or_given_operator_stdin() -> None:
    """A reviewer inherits no host descriptor: `None` in Popen would inherit all three."""
    import inspect

    from cheeseless import bootstrap

    source = inspect.getsource(bootstrap._launch_agent)
    assert "closed" in source
    # The branch that sets every stream to /dev/null must exist.
    assert "subprocess.DEVNULL, subprocess.DEVNULL, subprocess.DEVNULL" in source
