from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from management_portal import workflow_bindings_store


@pytest.mark.parametrize('existing', [False, True])
def test_resident_deletion_binding_index_new_and_existing_store(existing):
    """新库与旧库补相同 resident 前缀索引；只删目标关系且保唯一约束。"""
    store = workflow_bindings_store.WorkflowBindingStore()
    for rid in ('resident_one', 'resident_other'):
        for lid in ('lineage_one', 'lineage_two'):
            store.save({'lineage_id': lid, 'resident_id': rid, 'name': 'feature', 'steps': {}})
    if existing:
        with store.database.transaction() as connection:
            connection.execute('DROP INDEX IF EXISTS workflow_bindings_resident_idx')
        store = workflow_bindings_store.WorkflowBindingStore()
    with store.database.transaction() as connection:
        index = connection.execute(
            "SELECT indexdef FROM pg_indexes WHERE schemaname=current_schema() "
            "AND tablename='workflow_bindings' AND indexname='workflow_bindings_resident_idx'",
        ).fetchone()
        assert index and '(resident_id)' in index['indexdef']
        connection.execute('SET LOCAL enable_seqscan=off')
        plan = connection.execute(
            'EXPLAIN (FORMAT JSON) DELETE FROM workflow_bindings WHERE resident_id=ANY(%s)',
            (['resident_one'],),
        ).fetchone()
        assert 'workflow_bindings_resident_idx' in str(plan)
    assert store.forget_residents(['resident_one']) == 2
    assert store.forget_residents(['resident_one']) == 0
    assert all(store.get(lid, 'resident_other', 'feature') for lid in ('lineage_one', 'lineage_two'))


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
