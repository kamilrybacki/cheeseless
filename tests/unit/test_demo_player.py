"""The player is a single self-contained document with no external fetches."""

from demo.run_demo import build_player, parse_cast


def _cast(line: str) -> str:
    return (
        '{"version": 2, "width": 100, "height": 30, "timestamp": 0}\n'
        f'[0.0, "o", "{line}\\r\\n"]\n'
        f'[0.1, "o", "read-hidden-by-walk: SUCCEEDED\\r\\n"]\n'
    )


def test_player_embeds_both_casts_and_fetches_nothing() -> None:
    html = build_player(
        {
            "without-cheeseless": _cast("without mode"),
            "with-cheeseless": _cast("with mode"),
        }
    )
    assert html.startswith("<!doctype html>")
    assert "without mode" in html
    assert "with mode" in html
    assert "read-hidden-by-walk" in html
    for external in ("http://", "https://", "src=", "@import", "//cdn"):
        assert external not in html


def test_player_has_two_replay_panes_and_one_play_control() -> None:
    html = build_player(
        {
            "without-cheeseless": _cast("a"),
            "with-cheeseless": _cast("b"),
        }
    )
    assert html.count('class="pane"') == 2
    assert 'id="play"' in html


def test_parse_cast_rejects_a_non_v2_header() -> None:
    import pytest

    with pytest.raises(ValueError):
        parse_cast('{"version": 1}\n')
