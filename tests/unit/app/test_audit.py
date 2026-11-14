"""The audit sink writes one bounded JSON line per event to the trusted store."""

import json
import threading
from pathlib import Path

from cheeseless.adapters.outbound.store.file_store import FileStore, store_root
from cheeseless.app.audit import StoreAuditSink
from cheeseless.domain.audit import (
    AUDIT_SCHEMA,
    REQUEST_ADMITTED,
    RESPONSE_CLEAN,
    AuditEvent,
)


def _store(tmp_path: Path) -> FileStore:
    return FileStore(store_root(tmp_path / "repo.git", tmp_path / "state"))


def _event(index: int, stage: str) -> AuditEvent:
    return AuditEvent(
        request_id=f"{index:016x}",
        stage=stage,
        host="api.example.test",
        method="POST",
        rule="model-request",
    )


def test_a_record_is_one_allow_event_with_safe_metadata(tmp_path):
    store = _store(tmp_path)
    sink = StoreAuditSink(store, "a" * 64)
    sink.record(_event(1, REQUEST_ADMITTED))
    sink.record(_event(1, RESPONSE_CLEAN))
    lines = (store.root / "events.jsonl").read_text().splitlines()
    assert len(lines) == 2
    first, second = (json.loads(line) for line in lines)
    assert first["subject"] == "a" * 64
    assert first["kind"] == "egress_allow"
    cause = json.loads(first["cause"])
    assert cause["schema"] == AUDIT_SCHEMA
    assert cause["stage"] == REQUEST_ADMITTED
    assert cause["request_id"] == "0000000000000001"
    assert cause["host"] == "api.example.test"
    assert cause["method"] == "POST"
    assert cause["decision"] == "allow"
    assert cause["rule"] == "model-request"
    assert json.loads(second["cause"])["stage"] == RESPONSE_CLEAN


def test_concurrent_records_are_intact_complete_lines(tmp_path):
    store = _store(tmp_path)
    sink = StoreAuditSink(store, "a" * 64)

    def writer(base: int) -> None:
        for offset in range(50):
            sink.record(_event(base + offset, REQUEST_ADMITTED))

    threads = [threading.Thread(target=writer, args=(base,)) for base in (0, 1000, 2000)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    lines = (store.root / "events.jsonl").read_text().splitlines()
    assert len(lines) == 150
    ids = {json.loads(json.loads(line)["cause"])["request_id"] for line in lines}
    assert len(ids) == 150
