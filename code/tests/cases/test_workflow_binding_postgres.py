from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from management_portal import workflow_bindings_store


def test_workflow_postgres_lock_preserves_committed_progress_across_connections():
    """多个连接在 Lineage 锁内并发推进工作流绑定不丢更新，响应丢失后已保存的进度仍可见"""
    stores = []
    try:
        for number in range(4):
            stores.append(workflow_bindings_store.WorkflowBindingStore())
        state = {"lineage_id": "lineage_test", "resident_id": "resident_test", "name": "feature", "steps": {}}
        stores[0].save(state)

        def advance(number):
            store = stores[number % len(stores)]
            with store.lineage_lock("lineage_test"):
                record = store.get("lineage_test", "resident_test", "feature")
                store.save(record)

        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(advance, range(16)))
        assert stores[0].get("lineage_test", "resident_test", "feature")["version"] == 17
        with pytest.raises(TimeoutError):
            with stores[0].lineage_lock("lineage_test"):
                state = stores[0].get("lineage_test", "resident_test", "feature")
                state["steps"]["event"] = "event_source_test"
                stores[0].save(state)
                assert stores[1].get("lineage_test", "resident_test", "feature")["steps"] == state["steps"]
                raise TimeoutError("response lost")
        with stores[1].lineage_lock("lineage_test"):
            assert stores[1].get("lineage_test", "resident_test", "feature")["steps"] == state["steps"]
    finally:
        for store in stores:
            store.database.close()
