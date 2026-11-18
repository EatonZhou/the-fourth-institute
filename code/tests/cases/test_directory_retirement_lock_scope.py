"""Directory retirement locks its closure without blocking unrelated work."""
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
from contextlib import contextmanager

import pytest
from psycopg.errors import LockNotAvailable

from management_portal.resources.directory_registrations import unregister_tree
from management_portal.resources.registry_store import get_registry_store
from management_portal.resources.workspace_store import get_workspace_store
from management_portal.society_service.resident_workspace_deletion import unregister_workspace


def test_retirement_locks_selected_rows_and_queue_but_allows_unrelated_update(tmp_path):
    store = get_workspace_store()
    parent, child, other = [f'workspace_{n:012x}' for n in (1, 2, 3)]
    root = tmp_path / 'parent'
    for identity, path in ((parent, root), (child, root / 'child'), (other, tmp_path / 'other')):
        path.mkdir(parents=True, exist_ok=True)
        store.ensure(identity, str(path))

    def concurrent_checks():
        for identity in (parent, child):
            with pytest.raises(LockNotAvailable), store.database.transaction() as connection:
                connection.execute(f'SELECT workspace_id FROM {store.table} '
                                   'WHERE workspace_id=%s FOR KEY SHARE NOWAIT', (identity,))
        with store.database.transaction() as connection:
            connection.execute("SET LOCAL lock_timeout = '100ms'")
            connection.execute(f'UPDATE {store.table} SET title=%s WHERE workspace_id=%s',
                               ('unrelated update', other))
        with pytest.raises(LockNotAvailable), store.database.transaction() as connection:
            connection.execute("SET LOCAL lock_timeout = '100ms'")
            store.ensure('workspace_000000000004', str(root / 'new'), connection=connection)

    with store.database.transaction() as connection:
        result = unregister_tree([root], connection=connection, actor='test', preserve_resources=True)
        assert set(result['resource_ids']) == {parent, child}
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(concurrent_checks).result(timeout=5)
    assert not store.rows([parent, child])
    assert store.rows([other])[other]['title'] == 'unrelated update'
    with pytest.raises(FileExistsError):
        store.ensure('workspace_000000000004', str(root / 'new'))


def test_retirement_rejects_changed_ownership_before_mutation(tmp_path, monkeypatch):
    store = get_workspace_store()
    identity, owner = 'workspace_000000000005', 'lineage_000000000001'
    root = tmp_path / 'handoff'
    root.mkdir()
    store.ensure(identity, str(root), owner_id=owner)
    before = store.rows([identity])[identity]
    registry = get_registry_store()
    original_writer = registry.writer

    def finish_handoff():
        with store.database.transaction() as connection:
            store.restore_self_owner(identity, connection=connection,
                expected_owner=owner, expected_path=before['path'],
                expected_version=before['updated_at'])

    @contextmanager
    def writer_after_handoff(*args, **kwargs):
        # The Workspace observation has happened; a separately committed
        # ownership-only repair finishes before retirement locks its selection.
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(finish_handoff).result(timeout=5)
        with original_writer(*args, **kwargs) as writer:
            yield writer

    monkeypatch.setattr(registry, 'writer', writer_after_handoff)
    with pytest.raises(RuntimeError, match='ownership changed'):
        with store.database.transaction() as connection:
            unregister_tree(owner_ids=[owner], connection=connection, actor='test', preserve_resources=True)
    assert store.rows([identity])[identity]['owner_id'] == identity
    assert not store.cleanup.status(owner_id=identity)
    assert root.is_dir()


def test_retirement_reuses_path_evidence_only_within_one_closure(tmp_path, monkeypatch, record_property):
    import json
    from management_common import directory_paths
    from management_portal.resources import directory_registrations as registrations

    store = get_workspace_store()
    root = tmp_path / 'Owner'
    protected = root / 'Manuscript'
    protected.mkdir(parents=True)
    target = 'workspace_000000000101'
    alias = 'workspace_000000000102'
    selected = {target}
    store.ensure(target, str(root))
    (root / 'manuscript' / 'assets').mkdir(parents=True)
    store.ensure(alias, str(root / 'manuscript' / 'assets'))
    for index in range(12):
        identity = f'workspace_{0x200 + index:012x}'
        path = root / f'child-{index}'
        path.mkdir()
        store.ensure(identity, str(path))
        selected.add(identity)
    article = 'article_000000000103'
    with get_registry_store().writer('article') as writer:
        writer.put('article', article, {'id': article, 'roots': [{'path': str(protected), 'owner_id': article}]})

    probes, resolutions, snapshots = Counter(), Counter(), []
    sensitive = True
    original_located = registrations.located_path
    original_roots = directory_paths.deletion_roots

    def probe(parent):
        probes[parent] += 1
        return sensitive

    def locate(value):
        resolutions[str(value)] += 1
        return original_located(value)

    def cleanup_roots(path, preserved):
        if not snapshots or snapshots[-1]['sensitive'] != sensitive:
            snapshots.append({'sensitive': sensitive, 'probes': list(probes.values()),
                              'resolutions': list(resolutions.values())})
        return original_roots(path, preserved)

    monkeypatch.setattr(directory_paths, '_directory_case_sensitive', probe)
    monkeypatch.setattr(registrations, 'located_path', locate)
    monkeypatch.setattr(directory_paths, 'deletion_roots', cleanup_roots)

    class Rollback(Exception):
        pass

    for sensitive in (True, False):
        probes.clear()
        resolutions.clear()
        with pytest.raises(Rollback), store.database.transaction() as connection:
            result = unregister_tree([root], connection=connection, actor='test', preserve_resources=True)
            assert set(result['resource_ids']) == selected | ({alias} if sensitive else set())
            assert get_registry_store().get('article', article) is not None
            raise Rollback()
    record_property('closure_evidence_calls', json.dumps(snapshots))
    assert len(snapshots) == 2
    for snapshot in snapshots:
        assert snapshot['probes'] and max(snapshot['probes']) == 1
        assert snapshot['resolutions'] and max(snapshot['resolutions']) == 1


def test_explicit_unregister_allows_unrelated_workspace_writes(tmp_path):
    store = get_workspace_store()
    target, child, other = [f'workspace_{n:012x}' for n in (11, 12, 13)]
    root = tmp_path / 'target'
    for identity, path in ((target, root), (child, root / 'child'), (other, tmp_path / 'other')):
        path.mkdir(parents=True, exist_ok=True)
        store.ensure(identity, str(path))

    def write_unrelated_workspace():
        with store.database.transaction() as connection:
            connection.execute("SET LOCAL lock_timeout = '100ms'")
            connection.execute(f'UPDATE {store.table} SET title=%s WHERE workspace_id=%s',
                               ('still usable', other))
            connection.execute(f'SELECT workspace_id FROM {store.table} '
                               'WHERE workspace_id=%s FOR KEY SHARE NOWAIT', (other,))

    with store.database.transaction() as connection:
        result = unregister_workspace(target, connection=connection, actor='test', authorize=lambda _: None)
        assert result['resource_id'] == target and result['status'] == 'deleted'
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(write_unrelated_workspace).result(timeout=5)
    assert not store.rows([target, child])
    assert store.rows([other])[other]['title'] == 'still usable'


def test_explicit_unregister_authorizes_locked_target_and_denial_rolls_back(tmp_path):
    store = get_workspace_store()
    target, child = 'workspace_000000000015', 'workspace_000000000016'
    root = tmp_path / 'target'
    root.mkdir()
    store.ensure(target, str(root))
    store.ensure(child, str(root / 'child'))
    before = store.rows([target, child])

    def check_target_lock():
        with pytest.raises(LockNotAvailable), store.database.transaction() as connection:
            connection.execute(f'SELECT workspace_id FROM {store.table} '
                               'WHERE workspace_id=%s FOR KEY SHARE NOWAIT', (target,))

    def deny(descriptor):
        assert descriptor == store.descriptor(before[target])
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(check_target_lock).result(timeout=5)
        raise PermissionError('not allowed to delete')

    with pytest.raises(PermissionError, match='not allowed to delete'):
        with store.database.transaction() as connection:
            unregister_workspace(target, connection=connection, actor='test', authorize=deny)
    assert store.rows([target, child]) == before
    assert not store.cleanup.status(owner_id=target)
    assert not store.cleanup.status(owner_id=child)
    assert root.is_dir()
