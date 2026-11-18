"""Identity retirement uses indexed ownership, not physical containment."""
import pytest
from concurrent.futures import ThreadPoolExecutor
from psycopg.errors import LockNotAvailable, RaiseException


def setup_owner(tmp_path):
    from management_portal.resources.directory_identity_lifecycle import migrate_dependencies
    from management_portal.resources.workspace_store import get_workspace_store
    store = get_workspace_store()
    root = tmp_path / 'owner'
    root.mkdir()
    rid = 'resident_000000009001'
    with store.database.transaction() as connection:
        migrate_dependencies(connection, identities=[(rid, root)])
    return store, rid, root


def test_identity_retirement_preserves_independent_and_borrowed_roots(tmp_path):
    from management_portal.resources.directory_identity_lifecycle import (
        record_identity_directory, retire_identity_directory, migrate_dependencies,
    )
    from management_portal.resources.workspace_store import get_workspace_store
    from management_portal.resources.registry_store import get_registry_store
    from management_common.directory_cleanup_store import get_directory_cleanup_store
    store, registry = get_workspace_store(), get_registry_store()
    registry._ensure()
    root = tmp_path / 'owner'
    owned, independent = root / 'workspace', root / 'borrowed'
    article = root / 'paper'
    for path in (owned, independent, article):
        path.mkdir(parents=True)
        (path / 'content').write_text('keep or remove by ownership')
    rid = 'resident_000000009001'
    wid, bid = 'workspace_000000009001', 'workspace_000000009002'
    store.ensure(wid, str(owned), owner_id=rid)
    store.ensure(bid, str(independent))
    with registry.writer('article') as writer:
        writer.put('article', 'article_000000009001', {
            'id': 'article_000000009001', 'roots': [{'path': str(article), 'owner_id': 'article_000000009001'}]})
    with store.database.transaction() as connection:
        migrate_dependencies(connection, identities=[(rid, root)])
        record_identity_directory(rid, root, connection=connection)
        result = retire_identity_directory(rid, root, connection=connection,
            actor='test', deleted_at='2026-11-18T00:00:00Z')
    assert result['workspace_ids'] == [wid]
    assert set(result['preserved_paths']) == {str(independent), str(article)}
    assert store.rows([bid]) and not store.rows([wid])
    for cleanup_id in result['cleanup_ids']:
        get_directory_cleanup_store().run_pending(cleanup_id=cleanup_id)
    assert not owned.exists()
    assert (independent / 'content').is_file() and (article / 'content').is_file()
    with store.database.transaction() as connection:
        assert retire_identity_directory(rid, root, connection=connection,
            actor='test', deleted_at='2026-11-18T00:00:00Z') == result


def test_missing_migration_stale_source_and_root_fail_closed(tmp_path):
    from management_common import directory_dependencies as deps
    from management_portal.resources.directory_identity_lifecycle import retire_identity_directory
    store, rid, root = setup_owner(tmp_path)
    wid = 'workspace_000000009001'
    store.ensure(wid, str(root / 'own'), owner_id=rid)
    with pytest.raises(deps.DirectoryDependenciesMissing), store.database.transaction() as connection:
        retire_identity_directory(rid, root / 'wrong', connection=connection, actor='test', deleted_at='2026-11-18T00:00:00Z')
    with store.database.transaction() as connection:
        deps.prepare_write(connection)
        connection.execute(f'UPDATE {store.table} SET owner_id=%s WHERE workspace_id=%s', (wid, wid))
    with pytest.raises(deps.DirectoryDependenciesMissing), store.database.transaction() as connection:
        retire_identity_directory(rid, root, connection=connection, actor='test', deleted_at='2026-11-18T00:00:00Z')
    assert root.exists() and not store.cleanup.status(owner_id=rid)


def test_transfer_and_resource_registration_refresh_and_rollback(tmp_path):
    from management_portal.resources.directory_identity_lifecycle import retire_identity_directory
    from management_portal.resources.registry_store import get_registry_store
    from management_common import directory_dependencies as deps
    store, rid, root = setup_owner(tmp_path)
    wid = 'workspace_000000009001'
    store.ensure(wid, str(root / 'own'), owner_id=rid)
    before = store.rows([wid])[wid]
    with store.database.transaction() as connection:
        store.restore_self_owner(wid, connection=connection, expected_version=before['updated_at'],
            expected_owner=rid, expected_path=before['path'])
    registry = get_registry_store()
    article = 'article_000000009001'
    with registry.writer('article') as writer:
        writer.put('article', article, {'id': article, 'roots': [{'path': str(root / 'paper'), 'owner_id': article}]})
    class Rollback(Exception):
        pass
    with pytest.raises(Rollback), store.database.transaction() as connection:
        result = retire_identity_directory(rid, root, connection=connection, actor='test', deleted_at='2026-11-18T00:00:00Z')
        assert not result['workspace_ids']
        assert set(result['preserved_paths']) == {str(root / 'own'), str(root / 'paper')}
        raise Rollback()
    with store.database.read() as connection:
        assert connection.execute(f'SELECT 1 FROM {deps.schema()}.identity_retirements WHERE identity=%s', (rid,)).fetchone() is None
    assert not store.cleanup.status(owner_id=rid)
    assert registry.get('article', article)


def test_cleanup_retry_uses_frozen_cuts_and_no_follow(tmp_path, monkeypatch):
    from management_portal.resources.directory_identity_lifecycle import retire_identity_directory
    from management_common import directory_cleanup_store as cleanup
    from management_portal.resources.registry_store import get_registry_store
    store, rid, root = setup_owner(tmp_path)
    paper = root / 'paper'
    paper.mkdir()
    (paper / 'content').write_text('independent')
    secret = tmp_path / 'outside'
    secret.mkdir()
    (secret / 'content').write_text('outside')
    (root / 'link').symlink_to(secret, target_is_directory=True)
    with get_registry_store().writer('article') as writer:
        writer.put('article', 'article_000000009001', {'id': 'article_000000009001', 'roots': [{'path': str(paper)}]})
    with store.database.transaction() as connection:
        result = retire_identity_directory(rid, root, connection=connection, actor='test', deleted_at='2026-11-18T00:00:00Z')
    original = cleanup.remove_path
    monkeypatch.setattr(cleanup, 'remove_path', lambda *a: (_ for _ in ()).throw(OSError('injected')))
    outcome = store.cleanup.run_pending(cleanup_id=result['cleanup_ids'][0])
    assert outcome[0]['status'] == 'retry'
    monkeypatch.setattr(cleanup, 'remove_path', original)
    with store.database.transaction() as connection:
        connection.execute(f'UPDATE {store.cleanup.table} SET available_at=clock_timestamp() WHERE cleanup_id=%s', (result['cleanup_ids'][0],))
    assert store.cleanup.run_pending(cleanup_id=result['cleanup_ids'][0])[0]['status'] == 'completed'
    assert (paper / 'content').read_text() == 'independent'
    assert (secret / 'content').read_text() == 'outside'
    assert not (root / 'link').exists()


def test_fresh_install_and_writer_fence(tmp_path):
    from management_common import directory_dependencies as deps
    from management_portal.resources.directory_identity_lifecycle import record_identity_directory
    store, rid, root = setup_owner(tmp_path)
    with store.database.transaction() as connection:
        deps.require_ready(connection)
        record_identity_directory(rid, root, connection=connection)
    wid = 'workspace_000000009001'
    store.ensure(wid, str(root / 'own'), owner_id=rid)
    with pytest.raises(RaiseException, match='writer upgrade'), store.database.transaction() as connection:
        connection.execute(f'UPDATE {store.table} SET owner_id=%s WHERE workspace_id=%s', (wid, wid))
    # Ordinary metadata is not a structural writer and stays available.
    with store.database.transaction() as connection:
        connection.execute(f'UPDATE {store.table} SET title=%s WHERE workspace_id=%s', ('unchanged ownership', wid))


def test_coordinated_upgrade_refuses_busy_sources_without_changes(tmp_path):
    from scripts.migrate_identity_directory_dependencies import coordinated_upgrade
    from management_common import directory_dependencies as deps
    store, rid, root = setup_owner(tmp_path)
    wid = 'workspace_000000009001'
    store.ensure(wid, str(root / 'own'), owner_id=rid)
    def attempt():
        with pytest.raises(LockNotAvailable), store.database.transaction() as connection:
            coordinated_upgrade(connection)
    with store.database.transaction() as connection:
        connection.execute(f'UPDATE {store.table} SET title=title WHERE workspace_id=%s', (wid,))
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(attempt).result(timeout=5)
    with store.database.read() as connection:
        deps.require_ready(connection)


def test_identity_lock_blocks_registration_but_not_unrelated_fk(tmp_path):
    from management_portal.resources.directory_identity_lifecycle import retire_identity_directory
    from management_portal.resources.registry_store import get_registry_store
    store, rid, root = setup_owner(tmp_path)
    unrelated = 'workspace_000000009002'
    store.ensure(unrelated, str(tmp_path / 'other'))
    def probe():
        with store.database.transaction() as connection:
            connection.execute(f'SELECT workspace_id FROM {store.table} WHERE workspace_id=%s FOR KEY SHARE NOWAIT', (unrelated,))
        with pytest.raises(LockNotAvailable), store.database.transaction() as connection:
            connection.execute("SET LOCAL lock_timeout='100ms'")
            with get_registry_store().writer('article', connection=connection) as writer:
                writer.put('article', 'article_000000009001', {'id': 'article_000000009001', 'roots': [{'path': str(root / 'late')}]})
    with store.database.transaction() as connection:
        retire_identity_directory(rid, root, connection=connection, actor='test', deleted_at='2026-11-18T00:00:00Z')
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(probe).result(timeout=5)
    with pytest.raises(FileExistsError), get_registry_store().writer('article') as writer:
        writer.put('article', 'article_000000009001', {'id': 'article_000000009001', 'roots': [{'path': str(root / 'late')}]})


def test_indexed_work_does_not_visit_unrelated_objects_or_reservations(tmp_path, monkeypatch, record_property):
    from management_common import directory_dependencies as deps
    from management_common.directory_paths import DirectoryComparison
    from management_common.postgres_database import PostgresConnection
    from management_portal.resources.directory_identity_lifecycle import retire_identity_directory
    store, rid, root = setup_owner(tmp_path)
    with store.database.transaction() as connection:
        # The projection is populated only through the same mutation primitive.
        # No unrelated path is a member of this identity's dependency closure.
        for index in range(400):
            path = tmp_path / 'unrelated' / str(index)
            wid = f'workspace_{index + 20000:012x}'
            deps.prepare_write(connection)
            connection.execute(f'INSERT INTO {store.table}(workspace_id,path,title,owner_id,created_at,updated_at) '
                               'VALUES (%s,%s,%s,%s,clock_timestamp(),clock_timestamp())', (wid, str(path), 'unrelated', wid))
            store.refresh_directory_dependency(wid, connection=connection)
            store.cleanup.reserve(path / 'reserved', operation_id=f'op-{index}', purpose='test', connection=connection)
            store.cleanup.enqueue(path / 'expired', owner_id='elsewhere', operation_id=f'clean-{index}',
                                  actor='test', connection=connection)
    calls = []
    original = DirectoryComparison.overlaps
    def overlap(self, left, right):
        calls.append((str(left), str(right)))
        return original(self, left, right)
    monkeypatch.setattr(DirectoryComparison, 'overlaps', overlap)
    execute = PostgresConnection.execute
    queries = []
    def checked(connection, statement, params=None, *args, **kwargs):
        query = str(statement)
        queries.append(query)
        if query.startswith('SELECT') and any(name in query for name in ('registry_documents', '.workspaces', '.path_reservations')):
            assert 'WHERE' in query, query
        return execute(connection, statement, params, *args, **kwargs)
    monkeypatch.setattr(PostgresConnection, 'execute', checked)
    with store.database.transaction() as connection:
        result = retire_identity_directory(rid, root, connection=connection, actor='test', deleted_at='2026-11-18T00:00:00Z')
    store.cleanup.run_pending(cleanup_id=result['cleanup_ids'][0])
    assert not any('/unrelated/' in right for _, right in calls)
    assert len(calls) < 12
    record_property('unrelated_objects', 1200)
    record_property('native_overlap_comparisons', len(calls))


def test_retention_type_policy_and_committed_move_cancellation(tmp_path, monkeypatch):
    from management_common.directory_retention import RetentionStore
    from management_common.directory_move_store import DirectoryMoveStore, cancel_owner_moves
    from management_portal.resources.directory_identity_lifecycle import retire_identity_directory
    from management_portal.resources import directory_registrations
    store, rid, root = setup_owner(tmp_path)
    old = tmp_path / 'previous'
    old.mkdir()
    (old / 'private').write_text('old copy')
    session, tmp = root / 'session', root / 'tmp'
    session.mkdir()
    tmp.mkdir()
    retention = RetentionStore()
    retention.declare(session, category='provider_session', owner_id=rid)
    retention.declare(tmp, category='runtime_tmp', owner_id=rid)
    moves = DirectoryMoveStore()
    with store.database.transaction() as connection:
        connection.execute(f'INSERT INTO {moves.table}(operation_id,owner_id,actor,source,target,mode,phase) '
                           'VALUES (%s,%s,%s,%s,%s,%s,%s)',
                           ('old-move', rid, 'test', str(old), str(root), 'copy', 'committed'))
    monkeypatch.setattr(directory_registrations, 'unregister_tree', lambda *a, **kw: pytest.fail('global directory discovery'))
    monkeypatch.setattr(retention.__class__, 'unregister_tree', lambda *a, **kw: pytest.fail('retention tree discovery'))
    with store.database.transaction() as connection:
        cancel_owner_moves(connection, rid)
        result = retire_identity_directory(rid, root, connection=connection, actor='test', deleted_at='2026-11-18T00:00:00Z')
    assert str(session) in result['preserved_paths']
    for row in store.cleanup.status(owner_id=rid):
        store.cleanup.run_pending(cleanup_id=row['cleanup_id'])
    assert not old.exists() and not tmp.exists() and session.exists()
    with store.database.read() as connection:
        assert connection.execute(f'SELECT category FROM {retention.table} WHERE owner_id=%s', (rid,)).fetchone()['category'] == 'provider_session'


def test_coordinated_upgrade_long_reader_blocks_ddl_without_partial_commit(tmp_path):
    from scripts.migrate_identity_directory_dependencies import coordinated_upgrade
    from management_common import directory_dependencies as deps
    store, rid, root = setup_owner(tmp_path)
    def attempt():
        with pytest.raises(LockNotAvailable), store.database.transaction() as connection:
            coordinated_upgrade(connection)
    with store.database.transaction() as connection:
        connection.execute(f'SELECT cleanup_id FROM {store.cleanup.table} LIMIT 1')
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(attempt).result(timeout=5)
    with store.database.read() as connection:
        deps.require_ready(connection)


def test_source_guard_noop_upsert_and_real_change(tmp_path):
    from management_common import directory_dependencies as deps
    store, rid, root = setup_owner(tmp_path)
    table = deps.schema() + '.test_owner'
    with store.database.transaction() as connection:
        connection.execute(f'CREATE TABLE {table}(id text PRIMARY KEY, root text, status text)')
        deps.install_writer_fence(connection, table=table, name='test_owner', watched=['root'])
        deps.prepare_write(connection)
        connection.execute(f'INSERT INTO {table} VALUES (%s,%s,%s)', ('a', str(root), 'idle'))
    with store.database.transaction() as connection:
        connection.execute(f'INSERT INTO {table} VALUES (%s,%s,%s) ON CONFLICT(id) DO UPDATE SET status=EXCLUDED.status', ('a', str(root), 'running'))
    with pytest.raises(RaiseException), store.database.transaction() as connection:
        connection.execute(f'UPDATE {table} SET root=%s WHERE id=%s', (str(tmp_path / 'bad'), 'a'))


def test_installer_initializes_empty_owners_in_its_transaction(tmp_path):
    from management_common.portal_database import portal_database
    from management_common import directory_dependencies as deps
    from management_portal.resources.directory_identity_lifecycle import record_identity_directory, retire_identity_directory
    from scripts.tfi_release_initialization import initialize_directory_dependencies
    # Same owner initialization invoked by the official installer, with no
    # prerequisite Workspace/registry singleton construction in this case.
    database = portal_database(schema='directory_lifecycle')
    root = tmp_path / 'new-resident'
    root.mkdir()
    with database.transaction() as connection:
        initialize_directory_dependencies(connection.raw)
        deps.require_ready(connection)
        record_identity_directory('resident_000000009999', root, connection=connection)
    with database.transaction() as connection:
        result = retire_identity_directory('resident_000000009999', root, connection=connection,
                                           actor='test', deleted_at='2026-11-18T00:00:00Z')
    assert result['cleanup_roots'] == [str(root)]


def test_migration_preserves_existing_mixed_pending_cleanup(tmp_path):
    from management_portal.resources.directory_identity_lifecycle import migrate_dependencies
    from management_portal.resources.registry_store import get_registry_store
    store, rid, root = setup_owner(tmp_path)
    article = root / 'paper'
    article.mkdir()
    (article / 'main.tex').write_text('kept')
    with get_registry_store().writer('article') as writer:
        writer.put('article', 'article_000000009001', {'id': 'article_000000009001', 'roots': [{'path': str(article)}]})
    with store.database.transaction() as connection:
        cleanup_id = store.cleanup.enqueue(root, owner_id='old-owner', actor='test', operation_id='legacy', connection=connection)
        completed = store.cleanup.enqueue(tmp_path / 'completed', owner_id='historical', actor='before', operation_id='done', connection=connection)
        connection.execute(f'UPDATE {store.cleanup.table} SET completed_at=clock_timestamp() WHERE cleanup_id=%s', (completed,))
        before = dict(connection.execute(f'SELECT * FROM {store.cleanup.table} WHERE cleanup_id=%s', (completed,)).fetchone())
        pending_before = dict(connection.execute(f'SELECT * FROM {store.cleanup.table} WHERE cleanup_id=%s', (cleanup_id,)).fetchone())
        report = migrate_dependencies(connection, identities=[(rid, root)])
        assert report['pending_tasks_examined'] == report['pending_tasks_augmented'] == 1
        assert report['preserved_locations_added'] >= 1
        assert report['completed_tasks_modified'] == 0
        assert dict(connection.execute(f'SELECT * FROM {store.cleanup.table} WHERE cleanup_id=%s', (completed,)).fetchone()) == before
        pending_after = dict(connection.execute(f'SELECT * FROM {store.cleanup.table} WHERE cleanup_id=%s', (cleanup_id,)).fetchone())
        assert {k: v for k, v in pending_after.items() if k != 'preserved_locations'} == {k: v for k, v in pending_before.items() if k != 'preserved_locations'}
    store.cleanup.run_pending(cleanup_id=cleanup_id)
    assert (article / 'main.tex').read_text() == 'kept'


def test_cutover_rejects_waiting_old_writer_and_accepts_new_writer(tmp_path):
    from threading import Event
    from scripts.migrate_identity_directory_dependencies import coordinated_upgrade
    from management_common import directory_dependencies as deps
    from management_portal.resources.directory_identity_lifecycle import record_identity_directory
    store, rid, root = setup_owner(tmp_path)
    wid = 'workspace_000000009001'
    store.ensure(wid, str(root / 'own'), owner_id=rid)
    entered = Event()
    def old_writer():
        with pytest.raises(RaiseException, match='writer upgrade'), store.database.transaction() as connection:
            connection.execute("SET LOCAL lock_timeout='2s'")
            entered.set()
            connection.execute(f'UPDATE {store.table} SET owner_id=%s WHERE workspace_id=%s', (wid, wid))
    with ThreadPoolExecutor(max_workers=1) as pool:
        with store.database.transaction() as connection:
            coordinated_upgrade(connection)
            record_identity_directory(rid, root, connection=connection)
            pending = pool.submit(old_writer)
            assert entered.wait(timeout=2)
        pending.result(timeout=4)
    assert store.rows([wid])[wid]['owner_id'] == rid
    store.ensure('workspace_000000009003', str(root / 'new'), owner_id=rid)
    with store.database.read() as connection:
        deps.require_ready(connection)
        assert connection.execute(f'SELECT owner_id FROM {deps.schema()}.directory_dependencies '
            'WHERE kind=%s AND object_id=%s', ('workspace', 'workspace_000000009003')).fetchone()['owner_id'] == rid


def test_cutover_total_budget_rolls_back_and_releases_lock(tmp_path, monkeypatch):
    from scripts import migrate_identity_directory_dependencies as upgrade
    from management_common import directory_dependencies as deps
    from management_portal.resources import directory_identity_lifecycle as owner
    store, rid, root = setup_owner(tmp_path)
    before = None
    with store.database.read() as connection:
        before = connection.execute(f'SELECT * FROM {deps.schema()}.directory_dependencies ORDER BY kind,object_id,slot').fetchall()
    original = owner.migrate_dependencies
    elapsed = [0.0]
    monkeypatch.setattr(upgrade, 'monotonic', lambda: elapsed[0], raising=False)
    def exceed_budget(connection, **kwargs):
        result = original(connection, **kwargs)
        # One statement is short, but the whole migration has spent its budget.
        elapsed[0] = 31.0
        return result
    monkeypatch.setattr(owner, 'migrate_dependencies', exceed_budget)
    with pytest.raises(TimeoutError, match='budget'), store.database.transaction() as connection:
        upgrade.coordinated_upgrade(connection)
    with store.database.transaction() as connection:
        deps.require_ready(connection)
        assert connection.execute(f'SELECT * FROM {deps.schema()}.directory_dependencies ORDER BY kind,object_id,slot').fetchall() == before
        assert connection.execute('SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))',
                                  (store.cleanup.table + ':paths',)).fetchone()[0]


def test_independent_resource_types_survive_and_block_late_allocation(tmp_path):
    from management_portal.resources.registry_store import get_registry_store
    from management_portal.resources.directory_identity_lifecycle import retire_identity_directory
    store, rid, root = setup_owner(tmp_path)
    registry = get_registry_store()
    resources = {}
    for kind, field in [('article', 'roots'), ('repo', 'checkouts'), ('paper', 'directories'),
                        ('knowledge', 'directories'), ('report', 'directories')]:
        path = root / kind
        path.mkdir()
        (path / 'contents').write_text(kind)
        identifier = kind + '_000000009001'
        document = {'id': identifier, field: [{'path': str(path), 'owner_id': identifier}]}
        with registry.writer(kind) as writer:
            writer.put(kind, identifier, document)
        resources[kind] = (identifier, document)
    with store.database.transaction() as connection:
        plan = retire_identity_directory(rid, root, connection=connection, actor='test', deleted_at='2026-11-18T00:00:00Z')
    with pytest.raises(FileExistsError):
        store.ensure('workspace_000000009999', str(root / 'late'))
    for cleanup in plan['cleanup_ids']:
        store.cleanup.run_pending(cleanup_id=cleanup)
    for kind, (identifier, document) in resources.items():
        assert (root / kind / 'contents').read_text() == kind
        assert registry.get(kind, identifier) == document


def test_cutover_rejects_claimed_worker_then_accepts_abandoned_reservation(tmp_path):
    from scripts.migrate_identity_directory_dependencies import coordinated_upgrade
    from management_common import directory_dependencies as deps
    from management_portal.resources.registry_store import get_registry_store
    store, rid, root = setup_owner(tmp_path)
    # A real authority row is required for the backfill (the synthetic
    # identity supplied by setup_owner is only a test projection).
    article = 'article_000000009001'
    with get_registry_store().writer('article') as writer:
        writer.put('article', article, {'id': article, 'roots': [{'path': str(root / 'paper')}]})
    with store.database.transaction() as connection:
        cleanup_id = store.cleanup.enqueue(root, owner_id=rid, operation_id='old-plan', actor='test', connection=connection)
    def attempt():
        with pytest.raises(RuntimeError, match='cleanup worker'), store.database.transaction() as connection:
            coordinated_upgrade(connection)
    # This is the actual post-claim worker lease, with the queue TX committed.
    # The worker would now use its old row outside that transaction.
    with store.cleanup._claim_pending(cleanup_id) as claimed:
        assert claimed['cleanup_id'] == cleanup_id
        assert not claimed['preserved_locations']
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(attempt).result(timeout=5)
        with store.database.read() as connection:
            deps.require_ready(connection)
            row = connection.execute(f'SELECT preserved_locations FROM {store.cleanup.table} WHERE cleanup_id=%s', (cleanup_id,)).fetchone()
            assert not row['preserved_locations']
    # Crash-released lease, persistent reservation: not an active worker.
    with store.database.transaction() as connection:
        assert store.cleanup.reservations_at(root, connection=connection)
        result = coordinated_upgrade(connection)
        assert result['pending_tasks_augmented'] == 1
        deps.require_ready(connection)
