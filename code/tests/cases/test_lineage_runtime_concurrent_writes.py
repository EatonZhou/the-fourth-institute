"""Concurrent handoff progress commits preserve independent edges and fence stale writers.

:module: lineage.service
:importance: P1
"""
from __future__ import annotations

import threading

import pytest

from management_portal.society_service import lineage_data_runtime
from management_portal.society_service.lineage_data_runtime import RuntimeCASConflict
from management_portal.society_service.state_store import LineageDataStore
from tests.cases.test_lineage_object_state import LINEAGE_ID, _store, object_resources  # noqa: F401 - fixture


def _runtime(store: LineageDataStore) -> dict:
    definition = store.lineage_definition(LINEAGE_ID)
    definition["edges"] = [{"id": "handoff_" + suffix, "type": "handoff",
        "source": "resident_111111111111", "target": "resident_222222222222"}
        for suffix in ("111111111111", "222222222222")]
    store.put_lineage_definition(LINEAGE_ID, definition, {})
    store.put_lineage_runtime(LINEAGE_ID, {
        "schema": "tfi.lineage-runtime-state.v1",
        "lineage_id": LINEAGE_ID,
        "runtime_revision": 1,
        "edges": {"handoff_111111111111": {"handoff": {"status": "pending"}}, "handoff_222222222222": {"handoff": {"status": "pending"}}},
    })
    return store.lineage_snapshot(LINEAGE_ID)[1]


def _with(expected: dict, resident_id: str, status: str) -> dict:
    return {**expected, "edges": {**expected["edges"], resident_id: {"handoff": {"status": status}}}}


def _race(monkeypatch, first, second) -> list[BaseException]:
    """Run ``first`` until it has read the runtime, then ``second``, then let ``first`` finish."""

    first_read = threading.Event()
    second_done = threading.Event()
    errors: list[BaseException] = []
    original = lineage_data_runtime.runtime_from_connection

    def paused(connection, lineage_id):
        value = original(connection, lineage_id)
        if threading.current_thread().name == "first-writer" and not first_read.is_set():
            first_read.set()
            second_done.wait(1.0)
        return value

    monkeypatch.setattr(lineage_data_runtime, "runtime_from_connection", paused)

    def run(operation, done=None):
        try:
            operation()
        except BaseException as error:  # noqa: BLE001 - reported to the test
            errors.append(error)
        finally:
            if done is not None:
                done.set()

    writer = threading.Thread(target=run, args=(first,), name="first-writer")
    writer.start()
    assert first_read.wait(5)
    other = threading.Thread(target=run, args=(second, second_done), name="second-writer")
    other.start()
    writer.join(10)
    other.join(10)
    return errors


def test_compare_and_swap_writes_of_different_objects_both_land(monkeypatch, isolated_portal_case):
    """不同运行对象的并发 CAS 写入都保留"""
    with isolated_portal_case() as case_path:
        store = _store(case_path)
        expected = _runtime(store)
        errors = _race(
            monkeypatch,
            lambda: store.put_lineage_runtime_cas(LINEAGE_ID, expected, _with(expected, "handoff_111111111111", "running")),
            lambda: store.put_lineage_runtime_cas(LINEAGE_ID, expected, _with(expected, "handoff_222222222222", "running")),
        )
        assert errors == []
        nodes = store.lineage_snapshot(LINEAGE_ID)[1]["edges"]
        assert nodes["handoff_111111111111"]["handoff"]["status"] == "running"
        assert nodes["handoff_222222222222"]["handoff"]["status"] == "running"


def test_a_later_compare_and_swap_write_of_the_same_object_conflicts(monkeypatch, isolated_portal_case):
    """同一运行对象的后到 CAS 写入冲突"""
    with isolated_portal_case() as case_path:
        store = _store(case_path)
        expected = _runtime(store)
        errors = _race(
            monkeypatch,
            lambda: store.put_lineage_runtime_cas(LINEAGE_ID, expected, _with(expected, "handoff_111111111111", "running")),
            lambda: store.put_lineage_runtime_cas(LINEAGE_ID, expected, _with(expected, "handoff_111111111111", "done")),
        )
        assert len(errors) == 1 and isinstance(errors[0], RuntimeCASConflict)
        assert store.lineage_snapshot(LINEAGE_ID)[1]["edges"]["handoff_111111111111"]["handoff"]["status"] == "running"


def test_definition_runtime_changes_and_compare_and_swap_writes_both_land(monkeypatch, isolated_portal_case):
    """定义写入的运行变化与并发 CAS 写入都保留"""
    with isolated_portal_case() as case_path:
        store = _store(case_path)
        expected = _runtime(store)
        errors = _race(
            monkeypatch,
            lambda: store.put_lineage_runtime_changes(LINEAGE_ID, expected, _with(expected, "handoff_111111111111", "running")),
            lambda: store.put_lineage_runtime_cas(LINEAGE_ID, expected, _with(expected, "handoff_222222222222", "running")),
        )
        assert errors == []
        nodes = store.lineage_snapshot(LINEAGE_ID)[1]["edges"]
        assert nodes["handoff_111111111111"]["handoff"]["status"] == "running"
        assert nodes["handoff_222222222222"]["handoff"]["status"] == "running"
