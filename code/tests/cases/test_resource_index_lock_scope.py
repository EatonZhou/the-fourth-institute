"""Index reads do not wait while a refresh copies or examines an adapter's records.

Each change notification refreshes the adapter that owns the changed file.
The refresh copied all of the adapter's records (9,375 collaboration
descriptors, a quarter of a second) and resolved their source paths while
holding the in-memory index lock, so the change routing of the next
notification waited for it.  Stored records are replaced and never changed in
place, so that work now runs on a snapshot taken under the lock.
"""
from __future__ import annotations

import threading

from management_portal import resource_index_backend as backend
from management_portal import resource_index_store
from tests.cases.test_resource_index_backend import _record

FIRST = "skill_0123456789ab"
SECOND = "skill_fedcba987654"
SLOW_THREAD = "slow-refresh"


def _pause_in_slow_thread(monkeypatch, name: str):
    """Make ``resource_index_store.<name>`` stop in the refresh thread until released."""

    entered, release = threading.Event(), threading.Event()
    original = getattr(resource_index_store, name)

    def paused(*args, **kwargs):
        if threading.current_thread().name == SLOW_THREAD and not entered.is_set():
            entered.set()
            assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(resource_index_store, name, paused)
    return entered, release


def _read_while_paused(store, entered, release, target) -> tuple[list[str], list]:
    result: list = []
    worker = threading.Thread(target=lambda: result.append(target()), name=SLOW_THREAD)
    worker.start()
    seen: list[str] = []
    try:
        assert entered.wait(5)
        reader = threading.Thread(target=lambda: seen.append(store.internal_get(FIRST)["title"]))
        reader.start()
        reader.join(2)
        answered_while_paused = list(seen)
    finally:
        release.set()
        worker.join(5)
    return answered_while_paused, result


def test_reads_answer_while_an_adapters_records_are_copied(monkeypatch, isolated_portal_case):
    """复制适配器记录时索引读取不等待"""
    with isolated_portal_case():
        store = backend.IndexStore()
        store.publish_snapshot("skill", [
            _record(SECOND, "skill", adapter="skill", title="Second"),
            _record(FIRST, "skill", adapter="skill", title="First"),
        ], {})
        entered, release = _pause_in_slow_thread(monkeypatch, "_json_value")
        seen, result = _read_while_paused(
            store, entered, release, lambda: store.records_for_adapter("skill"),
        )
        assert seen == ["First"], "a read waited for the copy of the adapter's records"
        copies = result[0]
        assert [record["resource_id"] for record in copies] == [FIRST, SECOND]
        copies[0]["title"] = "Changed by the caller"
        assert store.internal_get(FIRST)["title"] == "First"


def test_reads_answer_while_a_snapshot_resolves_source_paths(monkeypatch, isolated_portal_case):
    """快照解析来源路径时索引读取不等待"""
    with isolated_portal_case():
        store = backend.IndexStore()
        store.publish_snapshot("skill", [_record(FIRST, "skill", adapter="skill", title="First")], {})
        entered, release = _pause_in_slow_thread(monkeypatch, "_resolved_source_path")
        updated = [_record(FIRST, "skill", adapter="skill", title="Updated")]
        # Only an error without a resource ID is matched to records by source path.
        errors = {"resource_errors": [{"path": f"/tmp/{SECOND}.json", "error": "unreadable"}]}
        seen, _result = _read_while_paused(
            store, entered, release, lambda: store.publish_snapshot("skill", updated, errors),
        )
        assert seen == ["First"], "a read waited for the snapshot's source path resolution"
        assert store.internal_get(FIRST)["title"] == "Updated"
