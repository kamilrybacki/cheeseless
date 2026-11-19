"""The dev-lineages listing line (pure) and its closed schema, dev-lineages-v1."""

import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from cheeseless.domain.dev_events import Stage
from cheeseless.domain.dev_lineages import (
    MAX_LINEAGES,
    SCHEMA_VERSION,
    STORE_ERROR_LINE,
    LineageEntry,
    LineageStamp,
    encode_listing,
    newest_first,
)
from tests.schema_check import violations

SCHEMAS = Path(__file__).resolve().parents[3] / "docs" / "schemas"
T0 = datetime(2026, 11, 19, 10, 0, 0, tzinfo=UTC)
ROW = {"lineage": "a", "stage": "spec", "updated_at": "2026-11-19T10:00:00Z"}


def _schema(name: str = "dev-lineages-v1.json") -> dict[str, object]:
    loaded: dict[str, object] = json.loads((SCHEMAS / name).read_text(encoding="utf-8"))
    return loaded


def _entry(lineage: str, stage: Stage = Stage.SPEC, at: datetime = T0) -> LineageEntry:
    return LineageEntry(lineage_id=lineage, stage=stage, updated_at=at)


# -- encoding -------------------------------------------------------------------


def test_an_empty_listing_is_one_compact_line() -> None:
    assert encode_listing(()) == '{"schema":1,"lineages":[]}'


def test_a_listing_keeps_the_given_order_and_prints_utc_seconds() -> None:
    later = datetime(2026, 11, 19, 12, 30, 5, 999_000, tzinfo=timezone(timedelta(hours=2)))
    line = encode_listing((_entry("b", Stage.CODE, later), _entry("a", Stage.DONE)))

    assert json.loads(line) == {
        "schema": SCHEMA_VERSION,
        "lineages": [
            {"lineage": "b", "stage": "code", "updated_at": "2026-11-19T10:30:05Z"},
            {"lineage": "a", "stage": "done", "updated_at": "2026-11-19T10:00:00Z"},
        ],
    }
    assert "\n" not in line and " " not in line


def test_a_listing_longer_than_the_cap_is_refused() -> None:
    entries = tuple(_entry(f"l{i}") for i in range(MAX_LINEAGES + 1))

    with pytest.raises(ValueError, match="at most"):
        encode_listing(entries)


def test_the_store_error_line_is_fixed() -> None:
    assert STORE_ERROR_LINE == '{"schema":1,"error":"store"}'


@pytest.mark.parametrize("bad", ["../x", "UPPER", "", "a" * 65, "/home/x"])
def test_an_invalid_lineage_id_cannot_enter_a_stamp_or_an_entry(bad: str) -> None:
    with pytest.raises(ValueError):
        LineageStamp(lineage_id=bad, updated_at=T0)
    with pytest.raises(ValueError):
        _entry(bad)


def test_a_naive_time_is_refused() -> None:
    naive = datetime(2026, 1, 1)  # noqa: DTZ001
    with pytest.raises(ValueError, match="timezone"):
        LineageStamp(lineage_id="a", updated_at=naive)
    with pytest.raises(ValueError, match="timezone"):
        _entry("a", at=naive)


def test_a_stage_outside_the_enum_is_refused() -> None:
    with pytest.raises(ValueError, match="stage"):
        LineageEntry(lineage_id="a", stage="lurking", updated_at=T0)  # type: ignore[arg-type]


def test_newest_first_sorts_by_time_then_lineage() -> None:
    stamps = (
        LineageStamp("b", T0),
        LineageStamp("c", T0 + timedelta(microseconds=1)),
        LineageStamp("a", T0),
        LineageStamp("d", T0 - timedelta(days=1)),
    )

    assert [s.lineage_id for s in newest_first(stamps)] == ["c", "a", "b", "d"]


# -- schema ---------------------------------------------------------------------


def test_every_encoded_line_validates() -> None:
    schema = _schema()
    stages = list(Stage)
    full = tuple(_entry(f"l{i}", stages[i % len(stages)]) for i in range(MAX_LINEAGES))

    for line in (encode_listing(()), encode_listing(full[:3]), encode_listing(full)):
        assert violations(schema, json.loads(line)) == []
    assert violations(schema, json.loads(STORE_ERROR_LINE)) == []


def test_the_schema_is_closed_versioned_and_capped() -> None:
    schema = _schema()
    lineages = schema["properties"]["lineages"]  # type: ignore[index]

    assert schema["additionalProperties"] is False
    assert schema["properties"]["schema"] == {"const": SCHEMA_VERSION}  # type: ignore[index]
    assert lineages["maxItems"] == MAX_LINEAGES
    assert lineages["items"]["additionalProperties"] is False


def test_the_stage_enum_matches_the_domain_and_dev_events() -> None:
    item = _schema()["properties"]["lineages"]["items"]  # type: ignore[index]
    dev_event = _schema("dev-event-v1.json")["properties"]["stage"]["enum"]  # type: ignore[index]

    assert item["properties"]["stage"]["enum"] == [s.value for s in Stage]
    assert item["properties"]["stage"]["enum"] == dev_event


@pytest.mark.parametrize(
    "value",
    [
        {"schema": 1, "lineages": [{"lineage": "a", "stage": "spec"}]},  # missing field
        {"schema": 2, "lineages": []},  # wrong version
        {"schema": 1, "lineages": {}},  # not an array
        {"schema": 1, "lineages": "a"},  # not an array
        {"schema": 1},  # neither branch
        {"schema": 1, "error": "other"},  # error is a const
        {"schema": 1, "error": "store", "lineages": []},  # both branches at once
        {"schema": 1, "lineages": [], "note": "CANARY-NOTE"},  # free text at the top
        {"schema": 1, "lineages": [{**ROW, "path": "/home/x"}]},  # free text in a row
        {"schema": 1, "lineages": [{**ROW, "updated_at": "2026-11-19 10:00"}]},
        {"schema": 1, "lineages": [{**ROW, "lineage": "../a"}]},
        {"schema": 1, "lineages": [{**ROW, "stage": "lurk"}]},
        {"schema": 1, "lineages": [ROW] * (MAX_LINEAGES + 1)},  # over the cap
    ],
)
def test_the_schema_refuses_lines_outside_the_contract(value: dict[str, object]) -> None:
    assert violations(_schema(), value) != []


# -- the checker's array keywords -------------------------------------------------


def test_the_checker_supports_array_items_and_max_items() -> None:
    schema = {"type": "array", "maxItems": 2, "items": {"type": "integer"}}

    assert violations(schema, [1, 2]) == []
    assert violations(schema, [1, 2, 3]) != []
    assert violations(schema, [1, "x"]) != []
    assert violations(schema, {"a": 1}) != []
    assert violations(schema, "ab") != []
