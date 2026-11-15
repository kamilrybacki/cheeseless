import os

import pytest

from study import manifest
from study.stats import (
    Cell,
    Rate,
    cluster_bootstrap,
    paired_differences,
    pooled_difference,
    rule_of_three,
    shuffled_order,
    wilson,
)
from study.tasks import CANARY


def test_wilson_known_values():
    lo, hi = wilson(0, 90)
    assert lo == 0.0 and 0.03 < hi < 0.05
    lo, hi = wilson(27, 90)
    assert lo == pytest.approx(0.2151, abs=0.001) and hi == pytest.approx(0.4013, abs=0.001)
    assert wilson(0, 0) is None
    with pytest.raises(ValueError):
        wilson(5, 3)


def test_rule_of_three_and_zero_events():
    assert rule_of_three(90) == pytest.approx(3 / 90)
    assert rule_of_three(0) is None
    assert Rate(0, 30).zero_event_bound == pytest.approx(0.1)
    assert Rate(1, 30).zero_event_bound is None
    assert Rate(0, 0).point is None and Rate(0, 0).ci is None


def _cells():
    return [
        Cell("t1/a", Rate(6, 10), Rate(1, 10)),
        Cell("t2/a", Rate(4, 10), Rate(0, 10)),
        Cell("t3/a", Rate(5, 10), Rate(2, 10)),
    ]


def test_paired_and_pooled_difference():
    d = paired_differences(_cells() + [Cell("t4/a", Rate(0, 0), Rate(1, 5))])
    assert d["t1/a"] == pytest.approx(-0.5) and d["t4/a"] is None
    assert pooled_difference(_cells()) == pytest.approx(3 / 30 - 15 / 30)


def test_bootstrap_is_seeded_and_brackets_the_estimate():
    a = cluster_bootstrap(_cells(), seed=7)
    assert a == cluster_bootstrap(_cells(), seed=7)
    assert a[0] <= a[1] and a[0] < 0 and a[2] == 3
    assert cluster_bootstrap(_cells()[:1], seed=7) is None


def test_bootstrap_resamples_tasks_keeping_agent_cells_together():
    cells = [
        Cell("t1/x", Rate(5, 10), Rate(0, 10)),
        Cell("t1/y", Rate(5, 10), Rate(0, 10)),
        Cell("t2/x", Rate(5, 10), Rate(0, 10)),
    ]
    assert cluster_bootstrap(cells, seed=1)[2] == 2


def test_shuffle_is_deterministic_permutation():
    items = [str(i) for i in range(20)]
    assert shuffled_order(items, 3) == shuffled_order(items, 3)
    assert sorted(shuffled_order(items, 3)) == sorted(items)
    assert shuffled_order(items, 3) != shuffled_order(items, 4)


def test_run_id_stable_and_distinct():
    a = manifest.make_run_id("s", 1, "naive", "slug", "ag", 0)
    assert a == manifest.make_run_id("s", 1, "naive", "slug", "ag", 0)
    assert a != manifest.make_run_id("s", 1, "cheeseless", "slug", "ag", 0)


@pytest.mark.parametrize(
    "bad",
    [
        CANARY,
        "/home/someone/x",
        "/Users/x/y",
        "sk-abcdefgh12345",
        "ghp_abcdefgh1234",
        "Bearer abcdef",
        "eyJhbGciOiJIUzI1NiJ9",
        "AKIAABCDEFGHIJ",
        os.environ.get("HOME", "/nonexistent-home") + "/data",
    ],
)
def test_redaction_refuses_and_writes_nothing(tmp_path, bad):
    path = tmp_path / "m.jsonl"
    with pytest.raises(manifest.RedactionError) as exc:
        manifest.append(path, {"run_id": "r", "note": bad})
    assert bad not in str(exc.value)
    assert not path.exists()


def test_append_read_resume_and_retry(tmp_path):
    p = tmp_path / "m.jsonl"
    manifest.append(p, {"run_id": "a", "status": "valid", "attempt": 1})
    manifest.append(p, {"run_id": "b", "status": "infra-error", "attempt": 1})
    assert manifest.completed_ids(p) == {"a"}
    assert manifest.next_attempt(p, "b") == 2 and manifest.next_attempt(p, "z") == 1
    manifest.append(p, {"run_id": "b", "status": "valid", "attempt": 2})
    assert manifest.completed_ids(p) == {"a", "b"}
    assert len(p.read_text().splitlines()) == 3
    manifest.append(p, {"record_type": "adjudication", "run_id": "a", "verdict": "x"})
    assert manifest.adjudications(p)["a"]["verdict"] == "x"
    assert set(manifest.latest_runs(p)) == {"a", "b"}


def test_missing_manifest_is_empty(tmp_path):
    assert manifest.latest_runs(tmp_path / "none.jsonl") == {}
