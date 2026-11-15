"""Report maths and the scrubbed report surface."""

from demo.run_demo import build_report, scrub, summarise_mode


def _tactic(tactic_id: str, succeeded: bool) -> dict:
    return {
        "id": tactic_id,
        "name": tactic_id,
        "succeeded": succeeded,
        "detail": "short detail",
    }


def _mode(successes: list[bool], control: bool) -> dict:
    return {
        "tactics": [_tactic(f"t{i}", ok) for i, ok in enumerate(successes)],
        "control": {"id": "control", "name": "honest fix", "succeeded": control, "detail": "ok"},
    }


def test_percentages_are_share_of_tactics_that_gamed_the_gate() -> None:
    mode = summarise_mode(_mode([True, True, True, True, True, False, False, False], True))
    assert mode["gamed"] == 5
    assert mode["total"] == 8
    assert mode["gamed_percent"] == 62.5
    assert mode["control_percent"] == 100.0
    assert mode["defence_percent"] == 37.5


def test_report_has_both_modes_and_the_percentages() -> None:
    results = {
        "modes": {
            "without-cheeseless": summarise_mode(_mode([True] * 8, True)),
            "with-cheeseless": summarise_mode(_mode([False] * 8, True)),
        }
    }
    text = build_report(results)
    assert "without cheeseless" in text.lower()
    assert "with cheeseless" in text.lower()
    assert "100.0%" in text
    assert "0.0%" in text
    assert "Limits of this demo" in text
    assert "read-hidden-by-walk" in text or "t0" in text


def test_scrub_removes_personal_and_temporary_paths() -> None:
    dirty = (
        "failed at /home/alice/project/demo and /tmp/cheeseless-demo-x/y "
        "and C:\\Users\\alice\\demo"
    )
    clean = scrub(dirty)
    assert "/home/alice" not in clean
    assert "/tmp/cheeseless-demo-x" not in clean  # noqa: S108
    assert "alice" not in clean
    assert scrub("/bin/sh --flag") == "/bin/sh --flag"
