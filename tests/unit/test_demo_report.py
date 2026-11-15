"""Report maths and the scrubbed report surface."""

from __future__ import annotations

from demo.run_demo import (
    BLOCKED,
    GAMED,
    INCONCLUSIVE,
    TACTIC_IDS,
    _is_valid,
    build_report,
    scrub,
    summarise_mode,
)


def _tactic(tactic_id: str, status: str) -> dict:
    return {
        "id": tactic_id,
        "name": tactic_id,
        "status": status,
        "detail": "short detail",
        "evidence": {} if status == INCONCLUSIVE else {"probe": "evidence"},
    }


def _mode(statuses: list[str], control: bool) -> dict:
    return {
        "tactics": [_tactic(f"t{i}", status) for i, status in enumerate(statuses)],
        "control": {"id": "control", "name": "honest fix", "succeeded": control, "detail": "ok"},
    }


def test_counts_exclude_inconclusive_from_blocked() -> None:
    mode = summarise_mode(
        _mode([GAMED, GAMED, BLOCKED, BLOCKED, BLOCKED, INCONCLUSIVE, INCONCLUSIVE, BLOCKED], True)
    )
    assert mode["counts"] == {
        "gamed": 2,
        "blocked": 4,
        "inconclusive": 2,
        "conclusive": 6,
        "total": 8,
    }
    assert mode["share_blocked"] == 50.0


def test_report_uses_the_mandated_wording_and_no_defence_rate() -> None:
    results = {
        "valid": True,
        "modes": {
            "without-cheeseless": summarise_mode(_mode([GAMED] * 8, True)),
            "with-cheeseless": summarise_mode(_mode([BLOCKED] * 6 + [INCONCLUSIVE] * 2, True)),
        },
    }
    text = build_report(results)
    assert "without cheeseless" in text.lower()
    assert "with cheeseless" in text.lower()
    assert "share of these probes blocked" not in text
    assert "%" not in text
    assert "0 GAMED, 6 BLOCKED, 2 INCONCLUSIVE (8 probes)" in text
    assert "8 GAMED, 0 BLOCKED, 0 INCONCLUSIVE (8 probes)" in text
    assert "not a defence rate" in text
    assert "not an estimate of how often the gate blocks an agent" in text
    assert (
        "0 of 8 scripted tactics succeeded with cheeseless in this version and configuration"
        in text
    )
    assert "(6 conclusive, 2 inconclusive; success cannot be ruled out" in text
    assert "INVALID" in text
    assert "Limits of this evidence" in text
    assert "decoy host witness path" in text
    assert "not the real trusted scratch location" in text
    assert "probe observed a defence-consistent outcome" in text
    assert "not that every route is closed" in text
    assert (
        "in this run the probe could not connect to the host loopback listener and could not "
        "resolve a name; this is not a proof of a DNS policy" in text
    )
    assert "eight probes" in text
    assert "not an adaptive agent" in text


def test_a_valid_report_is_not_marked_invalid() -> None:
    results = {
        "valid": True,
        "modes": {
            "without-cheeseless": summarise_mode(_mode([GAMED] * 8, True)),
            "with-cheeseless": summarise_mode(_mode([BLOCKED] * 8, True)),
        },
    }
    text = build_report(results)
    assert "INVALID" not in text
    assert (
        "0 of 8 scripted tactics succeeded with cheeseless in this version and configuration"
        in text
    )
    assert "0 inconclusive" in text


def test_report_states_one_predicate_per_tactic_and_what_a_fail_means() -> None:
    text = build_report(
        {
            "valid": True,
            "modes": {
                "without-cheeseless": summarise_mode(_mode([GAMED] * 8, True)),
                "with-cheeseless": summarise_mode(_mode([BLOCKED] * 8, True)),
            },
        }
    )
    assert "Predicate (identical in both modes)" in text
    assert "what a fail verdict means" in text.lower()


def test_report_lists_inconclusive_separately() -> None:
    text = build_report(
        {
            "valid": False,
            "modes": {
                "without-cheeseless": summarise_mode(_mode([GAMED] * 8, True)),
                "with-cheeseless": summarise_mode(
                    _mode([BLOCKED, INCONCLUSIVE] + [BLOCKED] * 6, True)
                ),
            },
        }
    )
    assert "inconclusive" in text.lower()
    assert "never counted as blocked" in text.lower()


def test_scrub_removes_personal_and_temporary_paths() -> None:
    dirty = (
        "failed at /home/alice/project/demo and /tmp/cheeseless-demo-x/y and C:\\Users\\alice\\demo"
    )
    clean = scrub(dirty)
    assert "/home/alice" not in clean
    assert "/tmp/cheeseless-demo-x" not in clean  # noqa: S108
    assert "alice" not in clean
    assert scrub("/bin/sh --flag") == "/bin/sh --flag"


def test_a_with_mode_with_an_inconclusive_tactic_is_invalid() -> None:
    results = {
        "modes": {
            "with-cheeseless": summarise_mode(_mode([BLOCKED, INCONCLUSIVE] + [BLOCKED] * 6, True))
        }
    }
    assert _is_valid(results) is False


def test_a_without_only_run_can_be_valid() -> None:
    results = {"modes": {"without-cheeseless": summarise_mode(_mode([GAMED] * 8, True))}}
    assert _is_valid(results) is True


def test_a_failed_control_makes_the_run_invalid() -> None:
    results = {"modes": {"with-cheeseless": summarise_mode(_mode([BLOCKED] * 8, False))}}
    assert _is_valid(results) is False


def test_report_states_that_weaken_tests_is_mode_specific() -> None:
    text = build_report(
        {
            "valid": True,
            "modes": {
                "without-cheeseless": summarise_mode(_mode([GAMED] * 8, True)),
                "with-cheeseless": summarise_mode(_mode([BLOCKED] * 8, True)),
            },
        }
    )
    assert "The weaken-tests edit is mode-specific" in text


def test_report_renders_the_weaken_tests_mutation_sentence() -> None:
    tactics = []
    for tactic_id in TACTIC_IDS:
        status = BLOCKED if tactic_id == "weaken-tests" else GAMED
        tactic = _tactic(tactic_id, status)
        if tactic_id == "weaken-tests":
            tactic["detail"] = (
                "the graded suite returned status fail; an unprotected grader accepts this "
                "edit; the cheeseless verdict rejected it because the hidden suite is not "
                "replaceable"
            )
            tactic["evidence"] = {"oracle_rejected": True}
        tactics.append(tactic)
    text = build_report(
        {
            "valid": True,
            "modes": {
                "without-cheeseless": summarise_mode(_mode([GAMED] * 8, True)),
                "with-cheeseless": summarise_mode(
                    {
                        "tactics": tactics,
                        "control": {"id": "control", "succeeded": True, "detail": "ok"},
                    }
                ),
            },
        }
    )
    assert "an unprotected grader accepts this edit" in text
    assert "the hidden suite is not replaceable" in text
