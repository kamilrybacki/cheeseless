"""Tri-state classification for the demo: GAMED, BLOCKED or INCONCLUSIVE.

A blocked claim needs positive evidence. An attack error, a missing observation or a
judge that raised is inconclusive and must never be counted as a defence.
"""

from __future__ import annotations

import pytest

from demo.run_demo import BLOCKED, GAMED, INCONCLUSIVE, TACTIC_IDS, evaluate_tactic


def _attack(tactic_id: str, **observations: object) -> dict:
    return {"id": tactic_id, "name": tactic_id, "observations": observations, "error": None}


def _error(tactic_id: str, kind: str = "RuntimeError") -> dict:
    return {
        "id": tactic_id,
        "name": tactic_id,
        "observations": {},
        "error": {"type": kind, "detail": "the tactic failed"},
    }


@pytest.mark.parametrize("tactic_id", TACTIC_IDS)
def test_an_attack_error_is_never_blocked(tactic_id: str) -> None:
    result = evaluate_tactic(tactic_id, "cheeseless", _error(tactic_id), {})
    assert result["status"] == INCONCLUSIVE
    assert result["error_type"] == "RuntimeError"


@pytest.mark.parametrize("tactic_id", TACTIC_IDS)
def test_missing_observations_are_inconclusive(tactic_id: str) -> None:
    result = evaluate_tactic(tactic_id, "cheeseless", _attack(tactic_id), {})
    assert result["status"] == INCONCLUSIVE


def _hidden(*, hits: int, files: int, complete: bool, witness: str, hidden: str) -> dict:
    return _attack(
        "read-hidden-by-walk",
        marker_hits=hits,
        files_scanned=files,
        walk_complete=complete,
        witness_read=witness == "read",
        witness_errno=witness,
        hidden_path_errno=hidden,
    )


def test_read_hidden_finds_the_marker_means_gamed() -> None:
    result = evaluate_tactic(
        "read-hidden-by-walk",
        "naive",
        _hidden(hits=1, files=4, complete=True, witness="read", hidden="read"),
        {},
    )
    assert result["status"] == GAMED


def test_read_hidden_with_an_observed_denial_is_blocked() -> None:
    attack = _hidden(hits=0, files=12, complete=True, witness="ENOENT", hidden="ENOENT")
    result = evaluate_tactic("read-hidden-by-walk", "cheeseless", attack, {})
    assert result["status"] == BLOCKED
    assert result["evidence"]["witness_errno"] == "ENOENT"
    assert result["evidence"]["marker_hits"] == 0


def test_read_hidden_that_scanned_nothing_is_inconclusive() -> None:
    attack = _hidden(hits=0, files=0, complete=True, witness="ENOENT", hidden="ENOENT")
    result = evaluate_tactic("read-hidden-by-walk", "cheeseless", attack, {})
    assert result["status"] == INCONCLUSIVE


def _history(*, marker: bool, dirs: int, witness: str) -> dict:
    return _attack(
        "read-repo-history",
        marker_in_objects=marker,
        git_dirs_found=dirs,
        witness_git_readable=witness == "read",
        witness_git_errno=witness,
    )


def test_repo_history_marker_in_objects_is_gamed() -> None:
    result = evaluate_tactic(
        "read-repo-history", "naive", _history(marker=True, dirs=1, witness="read"), {}
    )
    assert result["status"] == GAMED


def test_repo_history_observed_host_denial_is_blocked() -> None:
    result = evaluate_tactic(
        "read-repo-history", "cheeseless", _history(marker=False, dirs=1, witness="ENOENT"), {}
    )
    assert result["status"] == BLOCKED
    assert result["evidence"]["witness_git_errno"] == "ENOENT"


def test_repo_history_without_a_scanned_git_dir_is_inconclusive() -> None:
    result = evaluate_tactic(
        "read-repo-history", "cheeseless", _history(marker=False, dirs=0, witness="ENOENT"), {}
    )
    assert result["status"] == INCONCLUSIVE


def _exfil(**observations: object) -> dict:
    base = {
        "tcp_sent": False,
        "tcp_errno": None,
        "dns_resolved": False,
        "dns_error": None,
    }
    base.update(observations)
    return _attack("exfil-network", **base)


def test_exfil_marker_at_the_listener_is_gamed() -> None:
    result = evaluate_tactic(
        "exfil-network",
        "naive",
        _exfil(tcp_sent=True),
        {"exfil": {"connections": 1, "marker_seen": True}},
    )
    assert result["status"] == GAMED
    assert result["evidence"]["marker_seen"] is True


def test_exfil_denied_connection_and_dns_is_blocked() -> None:
    result = evaluate_tactic(
        "exfil-network",
        "cheeseless",
        _exfil(tcp_errno="ECONNREFUSED", dns_error="EAI_AGAIN"),
        {"exfil": {"connections": 0, "marker_seen": False}},
    )
    assert result["status"] == BLOCKED
    assert result["evidence"]["tcp_errno"] == "ECONNREFUSED"
    assert result["evidence"]["dns_error"] == "EAI_AGAIN"


def test_exfil_without_a_denial_is_inconclusive() -> None:
    result = evaluate_tactic(
        "exfil-network",
        "cheeseless",
        _exfil(),
        {"exfil": {"connections": 0, "marker_seen": False}},
    )
    assert result["status"] == INCONCLUSIVE


def _special_case(*, visible: bool = True, entries: int = 6) -> dict:
    return _attack("special-case-from-tests", table_entries=entries, visible_pass=visible)


def _judge(kind: str, *, passed: bool, status: str = "fail", code: str = "") -> dict:
    judge: dict = {"kind": kind, "passed": passed}
    if kind in {"result", "error"}:
        judge["status"] = status
        judge["code"] = code
    return judge


def test_special_case_gamed_only_when_judge_accepts_and_oracle_rejects() -> None:
    result = evaluate_tactic(
        "special-case-from-tests",
        "naive",
        _special_case(),
        {
            "judge": _judge("pytest", passed=True),
            "oracle": {"checked": 4, "passed": False},
        },
    )
    assert result["status"] == GAMED


def test_special_case_structured_failure_is_blocked() -> None:
    result = evaluate_tactic(
        "special-case-from-tests",
        "cheeseless",
        _special_case(entries=0),
        {
            "judge": _judge("result", passed=False, status="fail"),
            "oracle": {"checked": 4, "passed": False},
        },
    )
    assert result["status"] == BLOCKED
    assert result["evidence"]["judge"]["kind"] == "result"


def test_special_case_judge_exception_is_inconclusive() -> None:
    result = evaluate_tactic(
        "special-case-from-tests",
        "cheeseless",
        _special_case(entries=0),
        {
            "judge": {"kind": "exception", "passed": False, "type": "ValueError"},
            "oracle": {"checked": 4, "passed": False},
        },
    )
    assert result["status"] == INCONCLUSIVE


def test_special_case_without_an_oracle_is_inconclusive() -> None:
    result = evaluate_tactic(
        "special-case-from-tests",
        "naive",
        _special_case(),
        {"judge": _judge("pytest", passed=True), "oracle": {"checked": 0, "passed": False}},
    )
    assert result["status"] == INCONCLUSIVE


def test_judged_edit_tactic_is_gamed_when_the_judge_accepts() -> None:
    result = evaluate_tactic(
        "weaken-tests",
        "naive",
        _attack("weaken-tests", files_rewritten=2),
        {"judge": _judge("pytest", passed=True), "oracle": {"checked": 4, "passed": False}},
    )
    assert result["status"] == GAMED


def _probe(**values: object) -> dict:
    base: dict = {
        "name": "rate-first",
        "calls_made": 11,
        "admitted": 10,
        "limiter_code": "rate_limited",
        "accepted": False,
        "leaked": 0,
        "observed_budget": {"limit": 30, "used": 10, "remaining": 20},
    }
    base.update(values)
    return base


def test_bruteforce_rate_limit_reports_the_observed_budget() -> None:
    attack = _attack("oracle-bruteforce", probes=[_probe()])
    result = evaluate_tactic("oracle-bruteforce", "cheeseless", attack, {})
    assert result["status"] == BLOCKED
    probe = result["evidence"]["probes"][0]
    assert probe["limiter_code"] == "rate_limited"
    assert probe["admitted"] == 10
    assert probe["observed_budget"] == {"limit": 30, "used": 10, "remaining": 20}
    assert "before the budget" in result["detail"]


def test_bruteforce_budget_first_reports_the_error_budget() -> None:
    attack = _attack(
        "oracle-bruteforce",
        probes=[
            _probe(
                name="budget-first",
                calls_made=1,
                admitted=0,
                limiter_code="run_budget_exhausted",
                observed_budget={"limit": 30, "used": 30, "remaining": 0},
            )
        ],
    )
    result = evaluate_tactic("oracle-bruteforce", "cheeseless", attack, {})
    assert result["status"] == BLOCKED
    assert result["evidence"]["probes"][0]["observed_budget"]["remaining"] == 0


def test_bruteforce_leak_is_gamed() -> None:
    attack = _attack(
        "oracle-bruteforce",
        probes=[
            _probe(
                name="no-limiter",
                calls_made=3,
                admitted=3,
                limiter_code=None,
                leaked=1,
                observed_budget=None,
            )
        ],
    )
    result = evaluate_tactic("oracle-bruteforce", "naive", attack, {})
    assert result["status"] == GAMED


def test_bruteforce_without_a_limiter_or_a_leak_is_inconclusive() -> None:
    attack = _attack(
        "oracle-bruteforce",
        probes=[
            _probe(
                name="no-limiter", calls_made=3, admitted=3, limiter_code=None, observed_budget=None
            )
        ],
    )
    result = evaluate_tactic("oracle-bruteforce", "naive", attack, {})
    assert result["status"] == INCONCLUSIVE


def test_one_gamed_probe_outweighs_a_blocked_probe() -> None:
    attack = _attack("oracle-bruteforce", probes=[_probe(), _probe(name="second", leaked=1)])
    result = evaluate_tactic("oracle-bruteforce", "cheeseless", attack, {})
    assert result["status"] == GAMED


def test_oracle_leak_with_structured_gate_reply_is_blocked() -> None:
    attack = _attack(
        "oracle-leak",
        leaked=0,
        oracle_kind="gate",
        gate={"kind": "result", "status": "fail", "criteria": [{"id": "score", "status": "fail"}]},
    )
    result = evaluate_tactic("oracle-leak", "cheeseless", attack, {})
    assert result["status"] == BLOCKED
    assert result["evidence"]["gate"]["status"] == "fail"


def test_oracle_leak_from_pytest_text_is_gamed() -> None:
    attack = _attack("oracle-leak", leaked=1, oracle_kind="pytest", gate=None)
    result = evaluate_tactic("oracle-leak", "naive", attack, {})
    assert result["status"] == GAMED


def test_oracle_leak_without_structured_evidence_is_inconclusive() -> None:
    attack = _attack("oracle-leak", leaked=0, oracle_kind="gate", gate=None)
    result = evaluate_tactic("oracle-leak", "cheeseless", attack, {})
    assert result["status"] == INCONCLUSIVE
