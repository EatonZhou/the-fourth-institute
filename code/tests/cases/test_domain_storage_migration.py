"""Current domain identities, store startup and commit boundaries."""
import os

import psycopg
import pytest
from psycopg import sql







@pytest.mark.parametrize('collection', ['resident', 'lineage', 'workbench'])
def test_retired_identity_guard_rejects_reuse_and_inheritance(collection):
    """退役身份拒绝复用及被继承且允许保留删除记录"""
    from management_portal.resources.registry_store import RegistryStore
    from management_portal.resources.retired_identity_boundary import require_live_identity

    registry = RegistryStore()
    registry.snapshot(collection)
    retired_id = collection + '_111111111111'
    live_id = collection + '_222222222222'
    registry.database.write(lambda connection: connection.execute(
        'INSERT INTO retired_identities VALUES (%s)', (retired_id,)))
    with pytest.raises(PermissionError, match='retired identity'):
        with registry.writer(collection) as writer:
            writer.put(collection, retired_id, {'status': 'active'})
    with pytest.raises(PermissionError, match='retired identity'):
        registry.database.write(lambda connection: require_live_identity(
            connection, live_id, parents=[retired_id]))
    with registry.writer(collection) as writer:
        writer.put(collection, retired_id, {'status': 'deleted'})
        writer.put(collection, live_id, {'status': 'active'})
    assert registry.get(collection, retired_id) == {'status': 'deleted'}
    assert registry.get(collection, live_id) == {'status': 'active'}


def test_existing_store_starts_without_waiting_for_its_table_locks():
    """表和索引都已存在时，存储启动不再执行建索引语句，不必等别的事务释放表锁"""
    import threading
    from management_portal.resources.workspace_store import WorkspaceStore as start
    table = 'workspaces'
    schema = start().database.schema
    with psycopg.connect(os.environ['PORTAL_DATABASE_URL']) as holder:
        # Any open write on the table blocks a startup DDL statement.
        holder.execute(sql.SQL('LOCK TABLE {} IN SHARE ROW EXCLUSIVE MODE').format(sql.Identifier(schema, table)))
        starting = threading.Thread(target=start, daemon=True)
        starting.start()
        starting.join(5)
        waited = starting.is_alive()
        holder.rollback()
    starting.join(30)
    assert not waited


def test_search_identity_notifications_observe_commit_and_skip_rollback(monkeypatch):
    """检索身份通知提交后可见且回滚时不发送"""
    import pytest
    from management_portal import semantic_search_store as store

    seen = []
    def notify(identifiers):
        # Independent index readers must see the committed search, even when
        # the surface lock was nested inside an identity-wide transaction.
        with psycopg.connect(os.environ['PORTAL_DATABASE_URL']) as connection:
            rows = connection.execute(sql.SQL('SELECT search_id FROM {}').format(
                sql.Identifier(os.environ['PORTAL_DATABASE_NAMESPACE'] + '_semantic_search', 'jobs'))).fetchall()
        seen.append((set(identifiers), {row[0] for row in rows}))

    monkeypatch.setattr(store, '_notify', notify)
    with store.identity_lock():
        with store.PAPER_SEARCHES.locked():
            store.PAPER_SEARCHES.write([{'search_id': 'search_111111111111'}])
        assert seen == []
    assert seen == [({'search_111111111111'}, {'search_111111111111'})]
    with pytest.raises(ValueError):
        with store.identity_lock():
            with store.PAPER_SEARCHES.locked():
                store.PAPER_SEARCHES.write([{'search_id': 'search_222222222222'}])
            raise ValueError('cancel identity update')
    assert len(seen) == 1
    assert store.PAPER_SEARCHES.load() == [{'search_id': 'search_111111111111', 'surface': 'paper'}]
