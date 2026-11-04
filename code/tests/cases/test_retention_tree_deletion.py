"""Deletion roots invalidate nested retention in isolated PostgreSQL.

:module: operations.test-framework
:design: decision_4aa13bc21e8e
"""
from datetime import datetime, timedelta, timezone

import pytest

from management_common.directory_paths import cleanup_location
from management_common.directory_retention import RetentionStore
from management_portal.resources.directory_registrations import unregister_tree
from management_portal.resources.registry_store import get_registry_store
from management_portal.resources.workspace_store import get_workspace_store


WID = 'workspace_111111111111'


def test_completed_cleanup_replay_keeps_new_artifact_registration(tmp_path):
    from management_common.directory_retention import enqueue_directory_cleanup

    root = tmp_path / 'reused'
    root.mkdir()
    store = RetentionStore()
    with store.database.transaction() as connection:
        original = enqueue_directory_cleanup(
            root, owner_id='old', operation_id='delete-old', actor='test', connection=connection,
        )
    store.queue.run_pending()
    replacement = root / 'new-file'
    replacement.parent.mkdir()
    replacement.write_text('new generation')
    store.declare(replacement, category='object_data', owner_id='new')

    with store.database.transaction() as connection:
        replay = enqueue_directory_cleanup(
            root, owner_id='old', operation_id='delete-old', actor='test', connection=connection,
        )
    assert replay == original
    assert locations(store) == {cleanup_location(replacement)}
    assert store.queue.run_pending() == []
    assert replacement.read_text() == 'new generation'


def locations(store, connection=None):
    if connection is None:
        with store.database.read() as connection:
            return locations(store, connection)
    return {row['location'] for row in connection.execute(f'SELECT location FROM {store.table}').fetchall()}


def declare_children(store, root):
    paths = [root / 'session' / 'session.jsonl', root / 'notes' / 'notes']
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('old')
        store.declare(path, category='provider_session', owner_id='other:' + path.name,
                      finished_at=datetime.now(timezone.utc))
    return paths


def test_workspace_tree_clears_nested_artifacts_and_releases_owner_outside(tmp_path):
    root = tmp_path / 'outer'
    root.mkdir()
    workspace = get_workspace_store()
    workspace.ensure(WID, str(root))
    get_registry_store()._ensure()
    store = RetentionStore()
    children = declare_children(store, root)
    outside = tmp_path / 'outside'
    outside.write_text('owned outside')
    store.declare(outside, category='object_data', owner_id=WID)
    sibling = tmp_path / 'outer-sibling' / 'live'
    # A declaration may precede file creation. Missing files are not orphans.
    store.declare(sibling, category='provider_session', owner_id='live')
    with workspace.database.transaction() as connection:
        unregister_tree(workspace_ids=[WID], connection=connection, actor='test')
        expected = {cleanup_location(sibling)}
        assert locations(store, connection) == expected
    store.queue.run_pending()
    assert not root.exists()
    assert not outside.exists()


def test_workspace_nested_artifact_invalidation_rolls_back_with_owner(tmp_path):
    root = tmp_path / 'outer'
    root.mkdir()
    workspace = get_workspace_store()
    workspace.ensure(WID, str(root))
    get_registry_store()._ensure()
    store = RetentionStore()
    children = declare_children(store, root)
    with pytest.raises(RuntimeError, match='rollback deletion'):
        with workspace.database.transaction() as connection:
            unregister_tree(workspace_ids=[WID], connection=connection, actor='test')
            assert not locations(store, connection)
            assert store.queue.status(connection=connection)
            raise RuntimeError('rollback deletion')
    assert locations(store) == set(map(cleanup_location, children))
    assert workspace.rows([WID]) and not store.queue.status()
    assert all(path.exists() for path in children)


def test_late_sweep_cannot_delete_reused_workspace_contents(tmp_path):
    root = tmp_path / 'outer'
    root.mkdir()
    workspace = get_workspace_store()
    workspace.ensure(WID, str(root))
    get_registry_store()._ensure()
    store = RetentionStore()
    old = declare_children(store, root)[0]
    with workspace.database.transaction() as connection:
        unregister_tree(workspace_ids=[WID], connection=connection, actor='test')
    store.queue.run_pending()
    old.parent.mkdir(parents=True)
    old.write_text('new generation')
    workspace.ensure('workspace_222222222222', str(root))
    store.sweep(now=datetime.now(timezone.utc) + timedelta(days=40))
    store.queue.run_pending()
    assert old.exists() and old.read_text() == 'new generation'


@pytest.mark.parametrize('release', ['path', 'owner', 'expiry', 'run', 'cache'])
def test_retention_outer_release_invalidates_other_owners(tmp_path, release):
    root = tmp_path / 'outer'
    root.mkdir()
    store = RetentionStore()
    category = 'cache' if release == 'cache' else 'background'
    store.declare(root, category=category, owner_id='outer', bytes=10, cache_group='test',
                  finished_at=datetime.now(timezone.utc) - timedelta(days=8))
    children = declare_children(store, root)
    if release == 'path':
        store.release_path(root)
    elif release == 'owner':
        store.release_owner('outer')
    elif release == 'expiry':
        store.sweep()
    elif release == 'run':
        with store.database.transaction() as connection:
            connection.execute(f'UPDATE {store.table} SET test_run_id=%s WHERE owner_id=%s', ('run', 'outer'))
        store.release_run('run')
    else:
        store.sweep(cache_capacities={'test': 0})
    assert not locations(store)
    store.queue.run_pending()
    assert not root.exists()
    for path in children:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('new')
    store.sweep(now=datetime.now(timezone.utc) + timedelta(days=40))
    store.queue.run_pending()
    assert all(path.exists() and path.read_text() == 'new' for path in children)


def test_retention_outer_removes_live_nested_registration(tmp_path):
    root = tmp_path / 'outer'
    root.mkdir()
    store = RetentionStore()
    store.declare(root, category='object_data', owner_id='outer')
    children = declare_children(store, root)
    store.release_path(root)
    assert not locations(store)
    store.queue.run_pending()
    assert not root.exists()


def test_retention_outer_and_queue_roll_back_together(tmp_path):
    store = RetentionStore()
    root = tmp_path / 'outer'
    root.mkdir()
    store.declare(root, category='object_data', owner_id='outer')
    declare_children(store, root)
    before = locations(store)
    with pytest.raises(RuntimeError, match='rollback artifact'):
        with store.database.transaction() as connection:
            store.release_path(root, connection=connection)
            assert not locations(store, connection)
            assert store.queue.status(connection=connection)
            raise RuntimeError('rollback artifact')
    assert locations(store) == before and not store.queue.status()


def test_cleanup_enqueue_failure_rolls_back_nested_invalidation(tmp_path, monkeypatch):
    from management_common.directory_cleanup_store import DirectoryCleanupStore
    from management_common.directory_retention import enqueue_directory_cleanup
    store = RetentionStore()
    root = tmp_path / 'outer'
    declare_children(store, root)
    before = locations(store)
    enqueue = DirectoryCleanupStore.enqueue
    def fail(self, *args, **kwargs):
        enqueue(self, *args, **kwargs)
        raise RuntimeError('enqueue failed')
    monkeypatch.setattr(DirectoryCleanupStore, 'enqueue', fail)
    with pytest.raises(RuntimeError, match='enqueue failed'):
        with store.database.transaction() as connection:
            enqueue_directory_cleanup(root, owner_id='outer', operation_id='delete', actor='test', connection=connection)
    assert locations(store) == before and not store.queue.status()


def test_nested_cache_eviction_does_not_overevict_surviving_sibling(tmp_path):
    store = RetentionStore()
    now = datetime.now(timezone.utc)
    root = tmp_path / 'outer'
    child = root / 'cache'
    sibling = tmp_path / 'sibling'
    for path, size, seconds in [(root, 10, 0), (sibling, 5, 1), (child, 20, 2)]:
        path.mkdir(parents=True, exist_ok=True)
        store.declare(path, category='cache', owner_id=str(path), bytes=size,
                      cache_group='nested', created_at=now + timedelta(seconds=seconds))
    assert store.sweep(cache_capacities={'nested': 5})['queued'] == 1
    assert locations(store) == {cleanup_location(sibling)}
    store.queue.run_pending()
    assert sibling.exists() and not root.exists()


def test_release_batch_does_not_reenqueue_invalidated_child(tmp_path):
    store = RetentionStore()
    root = tmp_path / 'outer'
    child = root / 'child'
    child.mkdir(parents=True)
    for path in (root, child):
        store.declare(path, category='background', owner_id='outer',
                      finished_at=datetime.now(timezone.utc) - timedelta(days=8))
    assert store.sweep()['queued'] == 1
    assert len(store.queue.status()) == 1 and not locations(store)


def test_remove_generated_invalidates_nested_artifact(tmp_path):
    from management_common.file_retention import remove_generated
    store = RetentionStore()
    root = tmp_path / 'generated'
    declare_children(store, root)
    assert remove_generated(root, owner_id='runtime')['status'] == 'completed'
    assert not locations(store)


@pytest.mark.parametrize('operation', ['expire', 'delete', 'orphan'])
def test_report_cleanup_invalidates_nested_artifact(tmp_path, operation):
    from management_portal.test_framework.records import RunRecords, RunCoordinator
    records = RunRecords(tmp_path / 'records.json')
    run_id = records.create({}, 'test')['run_id']
    records.update(run_id, {'status': 'passed', 'finished_at': '2020-01-01T00:00:00Z'})
    root = tmp_path / 'artifacts'
    store = RetentionStore()
    declare_children(store, root / run_id)
    if operation == 'expire':
        records.expire(root)
    elif operation == 'delete':
        records.delete_many([run_id], run_root=root)
    else:
        records._write_index([])
        manager = object.__new__(RunCoordinator)
        manager.records, manager.run_root = records, root
        manager.remove_orphan_artifacts()
    assert not locations(store)


@pytest.mark.parametrize('operation', ['expire', 'test_end'])
def test_script_run_cleanup_invalidates_nested_artifact(tmp_path, monkeypatch, operation):
    from management_portal.society_service.script_runs import store as scripts, runtime, service
    from management_portal.test_framework.run_cleanup import delete_script_run
    monkeypatch.setattr(runtime, 'RUN_ROOT', tmp_path)
    identifier = 'script_run_retention_nested'
    scripts.admit({'script_run_id': identifier, 'status': 'succeeded', 'notified': False,
                   'caller_id': 'test', 'lineage_id': 'lineage_111111111111',
                   'started_at': '2020-01-01T00:00:00Z', 'finished_at': '2020-01-02T00:00:00Z'})
    store = RetentionStore()
    declare_children(store, tmp_path / identifier)
    if operation == 'expire':
        scripts.delete_expired(identifier)
        assert scripts.get(identifier) is not None  # Delivery record stays live.
    else:
        monkeypatch.setattr(service, 'cancel', lambda *a, **kw: None)
        delete_script_run(identifier)
    assert not locations(store)
