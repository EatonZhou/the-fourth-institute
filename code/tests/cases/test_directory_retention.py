"""Directory category expiry and durable retry use the isolated PostgreSQL.

:module: operations.test-framework
:design: decision_4aa13bc21e8e
"""
from datetime import datetime, timedelta, timezone
from threading import Event

import pytest

from management_common.resource_lifecycle import LifecycleStore, lifecycle_scope, record_allocation
from management_portal import business_tasks
from management_portal.test_framework.records import RunRecords


def test_background_allocations_keep_run_ownership(tmp_path):
    store = LifecycleStore()
    scope = store.open(str(tmp_path), actor='alice', run_id='testrun_0123456789abcdef', case_id='case')
    done = Event()
    errors = []
    def allocate():
        record_allocation('workspace_0123456789ab')
        done.set()
    with lifecycle_scope(scope, actor='alice'):
        business_tasks.start('retention-background', allocate, on_error=errors.append)
        assert done.wait(5)
    assert not errors
    assert store.pending(scope) == ['workspace_0123456789ab']


def test_completed_records_survive_count_limit(tmp_path):
    records = RunRecords(tmp_path / 'runs.json')
    identifiers = []
    for _ in range(102):
        run_id = records.create({}, 'alice')['run_id']
        records.update(run_id, {'status': 'passed', 'finished_at': datetime.now(timezone.utc).isoformat()})
        identifiers.append(run_id)
    assert records.run_ids() == set(identifiers)


def test_expiry_queues_in_same_transaction_and_retries(tmp_path, monkeypatch):
    from management_common.directory_retention import RetentionStore
    from management_common import directory_cleanup_store as cleanup
    now = datetime.now(timezone.utc)
    store = RetentionStore()
    path = tmp_path / 'job-output'
    path.mkdir()
    store.declare(path, category='background', owner_id='job_1', finished_at=now-timedelta(days=8))
    with pytest.raises(RuntimeError):
        with store.database.transaction() as connection:
            store.release_owner('job_1', connection=connection)
            raise RuntimeError('owner transaction rolled back')
    assert not store.queue.status() and path.exists()
    assert store.sweep(now=now)['queued'] == 1
    queue = cleanup.get_directory_cleanup_store()
    remove = cleanup.remove_path
    def busy(path):
        raise PermissionError('occupied')
    monkeypatch.setattr(cleanup, 'remove_path', busy)
    assert queue.run_pending()[0]['status'] == 'retry'
    assert path.exists()
    with queue.database.transaction() as connection:
        connection.execute(f'UPDATE {queue.table} SET available_at=clock_timestamp()')
    monkeypatch.setattr(cleanup, 'remove_path', remove)
    assert cleanup.DirectoryCleanupStore().run_pending()[0]['status'] == 'completed'
    assert not path.exists()


@pytest.mark.parametrize('category,days', [('runtime_tmp', 0), ('test_evidence', 7), ('background', 7), ('provider_session', 30)])
def test_category_expiry_requires_confirmed_end(tmp_path, category, days):
    from management_common.directory_retention import RetentionStore
    store = RetentionStore()
    now = datetime.now(timezone.utc)
    path = tmp_path / category
    path.mkdir()
    store.declare(path, category=category, owner_id='operation', created_at=now-timedelta(days=100))
    assert store.sweep(now=now)['queued'] == 0
    store.finish('operation', at=now)
    assert store.sweep(now=now+timedelta(days=days)-timedelta(seconds=1))['queued'] == 0
    assert store.sweep(now=now+timedelta(days=days))['queued'] == 1
    store.queue.run_pending()
    assert not path.exists()


def test_upload_use_and_object_delete(tmp_path):
    from management_common.directory_retention import RetentionStore
    store = RetentionStore()
    now = datetime.now(timezone.utc)
    for name in ('unused', 'used'):
        path = tmp_path / name
        path.mkdir()
        store.declare(path, category='unused_upload', owner_id=name, created_at=now-timedelta(days=8))
    store.used(tmp_path / 'used', owner_id='message')
    store.sweep(now=now)
    store.queue.run_pending()
    assert not (tmp_path / 'unused').exists()
    assert (tmp_path / 'used').exists()
    store.release_owner('message')
    store.queue.run_pending()
    assert not (tmp_path / 'used').exists()


@pytest.mark.parametrize('enlisted', [False, True])
@pytest.mark.parametrize('release', ['expiry', 'owner', 'capacity'])
def test_source_column_upgrade_preserves_rows_and_unifies_cleanup(tmp_path, enlisted, release):
    from management_common.directory_retention import RetentionStore
    store = RetentionStore()
    now = datetime.now(timezone.utc)
    category = {'expiry': 'unused_upload', 'owner': 'object_data', 'capacity': 'cache'}[release]
    paths = [tmp_path / 'first', tmp_path / 'second']
    for path in paths:
        path.write_bytes(b'data')
        store.declare(path, category=category, owner_id='owner', bytes=4,
                      cache_group='sample', created_at=now-timedelta(days=8))
    with store.database.transaction() as connection:
        before = connection.execute(f'SELECT * FROM {store.table} ORDER BY location').fetchall()
        # Restore the preceding schema with both historical values. Source
        # classification is upgrade input only, never an API option.
        connection.execute(f'''ALTER TABLE {store.table} ADD COLUMN origin TEXT NOT NULL
            DEFAULT 'created' CHECK(origin IN ('created','adopted'))''')
        connection.execute(f"UPDATE {store.table} SET origin='adopted' WHERE location=%s", (before[1]['location'],))
    if enlisted:
        with store.database.transaction() as connection:
            store = RetentionStore(connection=connection)
    else:
        store = RetentionStore()
    with store.database.read() as connection:
        assert connection.execute(f'SELECT * FROM {store.table} ORDER BY location').fetchall() == before
    for path in paths:
        # A retry with a new timestamp must retain the original deadline.
        store.declare(path, category=category, owner_id='owner', created_at=now)
    with store.database.read() as connection:
        assert connection.execute(f'SELECT * FROM {store.table} ORDER BY location').fetchall() == before
    if release == 'expiry':
        assert store.sweep(now=now)['queued'] == 2
    elif release == 'owner':
        assert store.release_owner('owner') == 2
    else:
        assert store.sweep(cache_capacities={'sample': 0})['queued'] == 2
    store.queue.run_pending()
    assert all(not path.exists() for path in paths)


def test_retention_schema_upgrade_rolls_back_and_warm_open_has_no_ddl_lock(monkeypatch):
    from management_common.directory_retention import RetentionStore
    store = RetentionStore()
    with store.database.transaction() as connection:
        connection.execute(f"ALTER TABLE {store.table} ADD COLUMN origin TEXT NOT NULL DEFAULT 'adopted'")
    with pytest.raises(RuntimeError, match='rollback'):
        with store.database.transaction() as connection:
            RetentionStore(connection=connection)
            assert not store._needs_initialize(connection)
            raise RuntimeError('rollback')
    with store.database.read() as connection:
        assert store._needs_initialize(connection)
    RetentionStore()
    monkeypatch.setattr(RetentionStore, '_initialize', lambda *_: pytest.fail('warm open tried schema upgrade'))
    with store.database.transaction() as connection:
        RetentionStore(connection=connection)
        RetentionStore(connection=connection)
        locks = connection.execute('''SELECT locktype,mode FROM pg_locks WHERE pid=pg_backend_pid()
            AND relation=to_regclass(%s)''', (store.table,)).fetchall()
        assert not any(row['mode'] == 'AccessExclusiveLock' for row in locks)


def test_concurrent_retention_schema_upgrade():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from management_common.directory_retention import RetentionStore
    store = RetentionStore()
    with store.database.transaction() as connection:
        connection.execute(f"ALTER TABLE {store.table} ADD COLUMN origin TEXT NOT NULL DEFAULT 'created'")
    barrier = Barrier(2)
    def initialize():
        barrier.wait(timeout=5)
        return RetentionStore().table
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(initialize) for _ in range(2)]
        assert [future.result(timeout=10) for future in futures] == [store.table, store.table]
    with store.database.read() as connection:
        assert not store._needs_initialize(connection)


def test_offline_retention_upgrade_and_import_without_source_classification(tmp_path):
    from management_common.directory_retention import RetentionStore
    from scripts.directory_protocol_retention_migration import migrate, backfill_known_artifacts
    store = RetentionStore()
    with store.database.transaction() as connection:
        connection.execute(f"ALTER TABLE {store.table} ADD COLUMN origin TEXT NOT NULL DEFAULT 'adopted'")
    now = datetime.now(timezone.utc)
    paths = [tmp_path / name for name in ['provider', 'archive']]
    for path in paths:
        path.write_text('ended task output')
    def receipt(path):
        return dict(path=str(path), owner_id='finished-task', created_at=now-timedelta(days=40),
                    finished_at=now-timedelta(days=35))
    with store.database.transaction() as connection:
        migrate(connection)
        assert not store._needs_initialize(connection)
        report = backfill_known_artifacts(connection, provider_receipts=[receipt(paths[0])],
                                         archive_receipts=[receipt(paths[1])])
        assert report == {'registered': list(map(str, paths)), 'unresolved': []}
        assert all('origin' not in row for row in connection.execute(f'SELECT * FROM {store.table}').fetchall())
    assert store.sweep(now=now)['queued'] == 2
    store.queue.run_pending()
    assert all(not path.exists() for path in paths)


def test_cache_capacity_uses_last_access_not_age(tmp_path):
    from management_common.directory_retention import RetentionStore
    store = RetentionStore()
    now = datetime.now(timezone.utc)
    for number in range(3):
        path = tmp_path / str(number)
        path.write_bytes(b'1234')
        store.declare(path, category='cache', owner_id=str(number), bytes=4, cache_group='images',
                      created_at=now+timedelta(seconds=number))
    store.touch(tmp_path / '0', at=now+timedelta(seconds=10))
    assert store.sweep(cache_capacities={'images': 8})['queued'] == 1
    store.queue.run_pending()
    assert (tmp_path / '0').exists() and not (tmp_path / '1').exists() and (tmp_path / '2').exists()


def test_test_evidence_expires_from_finished_time_and_retry_survives_index(tmp_path, monkeypatch):
    from management_common import directory_cleanup_store as cleanup
    now = datetime.now(timezone.utc)
    records = RunRecords(tmp_path / 'runs.json')
    old = records.create({}, 'alice')['run_id']
    live = records.create({}, 'alice')['run_id']
    records.update(old, {'status': 'passed', 'finished_at': (now-timedelta(days=8)).isoformat()})
    root = tmp_path / 'run-artifacts'
    evidence = root / old
    evidence.mkdir(parents=True)
    (evidence / 'screen.png').write_bytes(b'png')
    assert records.expire(root, now=now) == [old]
    assert records.run_ids() == {live}
    remove = cleanup.remove_path
    monkeypatch.setattr(cleanup, 'remove_path', lambda p: (_ for _ in ()).throw(PermissionError('busy')))
    queue = cleanup.get_directory_cleanup_store()
    assert all(item['status'] == 'retry' for item in queue.run_pending())
    assert evidence.exists()
    with queue.database.transaction() as connection:
        connection.execute(f'UPDATE {queue.table} SET available_at=clock_timestamp()')
    monkeypatch.setattr(cleanup, 'remove_path', remove)
    assert all(item['status'] == 'completed' for item in cleanup.DirectoryCleanupStore().run_pending())
    assert not evidence.exists()


def test_script_outputs_expire_after_seven_days():
    from management_portal.society_service.script_runs import store
    now = datetime.now(timezone.utc)
    store.admit({'script_run_id': 'script_run_retention', 'status': 'succeeded', 'notified': True,
                 'started_at': (now-timedelta(days=9)).isoformat(), 'finished_at': (now-timedelta(days=8)).isoformat()})
    assert store.expired_artifacts() == ['script_run_retention']
    assert not store.expired()


def test_unnotified_script_keeps_delivery_record_but_not_expired_files(tmp_path, monkeypatch):
    from management_portal.society_service.script_runs import store, runtime
    from management_common.directory_cleanup_store import get_directory_cleanup_store
    monkeypatch.setattr(runtime, 'RUN_ROOT', tmp_path)
    directory = tmp_path / 'script_run_delivery'
    directory.mkdir()
    now = datetime.now(timezone.utc)
    store.admit({'script_run_id': directory.name, 'status': 'succeeded', 'notified': False,
                 'started_at': (now-timedelta(days=9)).isoformat(), 'finished_at': (now-timedelta(days=8)).isoformat()})
    assert store.expired_artifacts() == [directory.name]
    store.expire_artifacts(directory.name)
    get_directory_cleanup_store().run_pending()
    assert store.get(directory.name) is not None and not directory.exists()


def test_workbench_current_previous_replaced_deadline_and_shared_protection(tmp_path, monkeypatch):
    from management_portal.workbench_projections import application_build as builder
    from management_portal.workbench_projections.store import Store
    from management_common.directory_retention import RetentionStore
    from tests.cases.test_projection_application_store import definition
    import hashlib
    monkeypatch.setattr(builder, '_artifact_root', lambda: tmp_path / 'builds')
    def build(application, actor):
        contents = {'example.html': application['commit'].encode()}
        index = {'entries': application['entries'], 'files': {'example.html': {
            'sha256': hashlib.sha256(contents['example.html']).hexdigest(),
            'size': len(contents['example.html']), 'content_type': builder.CONTENT_TYPES['.html']}}}
        digest = builder._store(index, contents)
        return {'status': 'ready', 'commit': application['commit'], 'artifact_hash': digest, **index}
    monkeypatch.setattr(builder, 'build_application', build)
    apps = Store()
    app_id, shared_id = 'workbench_123456789abc', 'workbench_abcdef012345'
    first = apps.create(app_id, definition(), 'alice')
    apps.create(shared_id, definition(), 'alice')
    hashes = [first['application_build']['artifact_hash']]
    for revision, commit in [(1, 'b'), (2, 'c'), (3, 'd')]:
        app = apps.update(app_id, {'application': {**first['application'], 'commit': commit*40}}, 'alice', revision)
        hashes.append(app['application_build']['artifact_hash'])
    retention = RetentionStore()
    retention.sweep(now=datetime.now(timezone.utc)+timedelta(days=8))
    retention.queue.run_pending()
    assert (tmp_path / 'builds' / hashes[0]).is_dir()  # shared app's current
    assert not (tmp_path / 'builds' / hashes[1]).exists()  # replaced > 7 days
    assert all((tmp_path / 'builds' / digest).is_dir() for digest in hashes[2:])
    apps.delete(shared_id, 'alice')
    retention.queue.run_pending()
    assert not (tmp_path / 'builds' / hashes[0]).exists()
    assert all((tmp_path / 'builds' / digest).is_dir() for digest in hashes[2:])
    apps.delete(app_id, 'alice')
    retention.queue.run_pending()
    assert not any((tmp_path / 'builds' / digest).exists() for digest in hashes)


def test_run_cleanup_retries_domain_failure_keeps_framework_resident(tmp_path, monkeypatch):
    from management_portal.test_framework import tau_fixtures
    from management_portal.test_framework.run_cleanup import finish_run
    run_id = 'testrun_0123456789abcdef'
    store = LifecycleStore()
    scope = store.open(str(tmp_path), actor='alice', run_id=run_id, case_id='case')
    fixed = 'resident_aaaaaaaaaaaa'
    store.bind(scope, fixed)
    with lifecycle_scope(scope, actor='alice'):
        record_allocation('resident_0123456789ab')
        record_allocation('workspace_0123456789ab')
    def fail(*args):
        raise RuntimeError('termination incomplete')
    monkeypatch.setattr(tau_fixtures, '_release_resource', fail)
    assert finish_run(run_id)['status'] == 'error'
    assert set(store.pending(scope)) == {'resident_0123456789ab', 'workspace_0123456789ab'}
    released = []
    monkeypatch.setattr(tau_fixtures, '_release_resource', lambda rid, key: released.append(rid))
    assert finish_run(run_id)['status'] == 'complete'
    assert set(released) == {'resident_0123456789ab', 'workspace_0123456789ab'}
    assert fixed not in released and store.scope(scope)['state'] == 'complete'


def test_maintenance_uses_existing_time_effect_runscript():
    from management_portal.directory_maintenance import workflow_operations
    from management_portal.society_service.lineage_effect_script import parse
    operations = workflow_operations('/code/scripts/directory_retention.py')
    timer, effect, route = operations
    assert timer['value']['config']['event_type'] == 'time.reached'
    script = effect['value']['config']['script']
    parse(script)
    assert 'RunScript' in script and 'exclusive_key=directory-retention' in script
    assert route['value']['target'] == '$ref:retention_effect'


@pytest.mark.parametrize('operation', ['expire', 'delete'])
def test_test_report_index_survives_cleanup_transaction_failure(tmp_path, monkeypatch, operation):
    from contextlib import contextmanager
    from management_portal.test_framework.records import RunRecords
    from management_common.directory_cleanup_store import get_directory_cleanup_store
    records = RunRecords(tmp_path / 'runs.json')
    run_id = records.create({}, 'alice')['run_id']
    records.update(run_id, {'status': 'passed', 'finished_at': '2020-01-01T00:00:00Z'})
    queue = get_directory_cleanup_store()
    transaction = queue.database.transaction
    @contextmanager
    def failed_commit(*args, **kwargs):
        with transaction(*args, **kwargs) as connection:
            yield connection
            raise RuntimeError('cleanup commit failed')
    monkeypatch.setattr(queue.database, 'transaction', failed_commit)
    with pytest.raises(RuntimeError, match='cleanup commit failed'):
        if operation == 'expire':
            records.expire(tmp_path / 'artifacts')
        else:
            records.delete_many([run_id], run_root=tmp_path / 'artifacts')
    assert records.get(run_id)['run_id'] == run_id
    assert not queue.status(owner_id=run_id)


def test_provider_release_reopen_and_host_account_protection(tmp_path, monkeypatch):
    from management_common.portal_paths import path as portal_path
    from management_common.directory_retention import RetentionStore
    from management_portal.execution.session_retention import released, reopened
    root = portal_path('provider-state') / 'dsh' / 'dsh_test-session'
    root.mkdir(parents=True)
    (root / 'transcript').write_text('test data')
    assert released('dsh', 'dsh_test-session') == [root]
    retention = RetentionStore()
    now = datetime.now(timezone.utc)
    assert retention.sweep(now=now+timedelta(days=29))['queued'] == 0
    reopened('dsh', 'dsh_test-session')
    assert retention.sweep(now=now+timedelta(days=31))['queued'] == 0
    released('dsh', 'dsh_test-session')
    assert retention.sweep(now=now+timedelta(days=31))['queued'] == 1
    retention.queue.run_pending()
    assert not root.exists()
    host = tmp_path / 'account'
    transcript = host / 'projects' / 'project' / 'session.jsonl'
    transcript.parent.mkdir(parents=True)
    transcript.write_text('adopted')
    assert released('claude', 'session', config_dir=str(host)) == []
    assert released('claude', '*', config_dir=str(host)) == []
    assert transcript.exists()
    # Account provenance wins even if a host chose a home under an instance area.
    from management_portal.execution import account_registrations
    root.mkdir(parents=True)
    monkeypatch.setattr(account_registrations, 'registrations', lambda: [{'config': {'account_dir': str(root)}}])
    assert released('dsh', 'dsh_test-session') == []


def test_runtime_temporary_directory_is_owned_and_cleanup_retry_is_durable(monkeypatch):
    from management_common.file_retention import temporary_directory, recover_temporary_directories
    from management_common import directory_cleanup_store as cleanup
    from management_common.owned_path_deletion import OwnedPathDeletionStore
    from pathlib import Path
    queue = cleanup.get_directory_cleanup_store()
    remove = OwnedPathDeletionStore.remove
    monkeypatch.setattr(OwnedPathDeletionStore, 'remove', lambda *a, **kw: (_ for _ in ()).throw(PermissionError('occupied')))
    with temporary_directory(prefix='retention-test-') as temporary:
        path = Path(temporary)
        (path / 'data').write_text('runtime')
    assert path.exists() and queue.pending_at(path)
    monkeypatch.setattr(OwnedPathDeletionStore, 'remove', remove)
    with queue.database.transaction() as connection:
        connection.execute(f'UPDATE {queue.table} SET available_at=clock_timestamp()')
    recover_temporary_directories()
    assert not path.exists()


def test_effect_recovery_is_immediate_and_output_seven_days(tmp_path):
    from management_portal.society_service.effect_artifacts import ArtifactStore
    from management_common.directory_retention import RetentionStore
    artifacts = ArtifactStore(tmp_path / 'effects')
    recovery = artifacts.put('lineage_a', 'activation', {'resume': True}, purpose='recovery')
    output = artifacts.put('lineage_a', 'activation', {'answer': True}, purpose='output')
    artifacts.clear_recovery('lineage_a', 'activation')
    with pytest.raises(FileNotFoundError):
        artifacts.read('lineage_a', 'activation', recovery)
    assert artifacts.read('lineage_a', 'activation', output) == {'answer': True}
    retention = RetentionStore()
    retention.sweep(now=datetime.now(timezone.utc)+timedelta(days=8))
    retention.queue.run_pending()
    with pytest.raises(FileNotFoundError):
        artifacts.read('lineage_a', 'activation', output)


def test_script_run_inherits_scope_and_is_stopped_and_deleted_at_run_end(tmp_path, monkeypatch):
    from management_portal.society_service.script_runs import service, store as scripts, runtime
    from management_portal.test_framework.run_cleanup import finish_run
    run_id = 'testrun_0123456789abcdef'
    scope_store = LifecycleStore()
    scope = scope_store.open(str(tmp_path), actor='alice', run_id=run_id, case_id='case')
    script = tmp_path / 'fixture.py'
    script.write_text('print("never executed")')
    monkeypatch.setattr(runtime, 'RUN_ROOT', tmp_path / 'scripts')
    monkeypatch.setattr(service, 'owning_lineage', lambda *args: 'lineage_0123456789ab')
    from management_portal.society_service import lineage_aggregate
    from types import SimpleNamespace
    monkeypatch.setattr(lineage_aggregate, 'lineage_catalog_store',
                        lambda: SimpleNamespace(get=lambda identifier: {'lineage_id': identifier}))
    monkeypatch.setattr(service, 'launch', lambda *args: None)
    with lifecycle_scope(scope, actor='alice'):
        receipt = service.start({'path': str(script), 'cwd': str(tmp_path), 'runtime': 'python'}, 'alice')
    identifier = receipt['script_run_id']
    assert scope_store.pending(scope) == [identifier]
    assert scripts.get(identifier)['identity']['test_run_id'] == run_id
    directory = runtime.RUN_ROOT / identifier
    directory.mkdir(parents=True)
    (directory / 'output').write_text('fixture')
    stopped = []
    def stop(identifier, caller, **kwargs):
        stopped.append(identifier)
        with scripts.edit(identifier) as record:
            record.update(status='cancelled', finished_at=datetime.now(timezone.utc).isoformat())
    monkeypatch.setattr(service, 'cancel', stop)
    assert finish_run(run_id)['status'] == 'complete'
    assert stopped == [identifier] and scripts.get(identifier) is None and not directory.exists()


def test_script_rpc_carries_scope_without_a_database_connection(monkeypatch):
    import io
    from management_portal.society_service.script_runs import runner
    requests = []
    def request(value, **kwargs):
        requests.append(value)
        return io.BytesIO(b'{"ok":true}')
    monkeypatch.setattr(runner.urllib.request, 'urlopen', request)
    assert runner.http_forward({'token': 'fixture', 'portal_url': 'http://fixture.invalid',
                                'resource_lifecycle_id': 'scope'} )({'instruction': 'CreateWorkspace'}) == {'ok': True}
    assert requests[0].get_header('X-portal-resource-lifecycle') == 'scope'


def test_upload_writer_hooks_capture_run_and_transfer_directory_ownership(tmp_path):
    from management_common import upload_retention
    from management_common.directory_retention import RetentionStore
    scope_store = LifecycleStore()
    scope = scope_store.open(str(tmp_path), actor='alice', run_id='testrun_0123456789abcdef', case_id='case')
    directory = tmp_path / 'upload'
    directory.mkdir()
    with lifecycle_scope(scope, actor='alice'):
        upload_retention.created(directory, upload_id='upload_'+'a'*24, created_at=datetime.now(timezone.utc).isoformat())
    assert scope_store.pending(scope) == ['upload_'+'a'*24]
    upload_retention.consumed(directory, owner_id='message_1')
    retention = RetentionStore()
    assert retention.sweep(now=datetime.now(timezone.utc)+timedelta(days=8))['queued'] == 0
    assert upload_retention.deleted(directory)
    retention.queue.run_pending()
    assert not directory.exists()


def test_mechanical_maintenance_reports_pending_failure_and_retries(tmp_path, monkeypatch):
    from management_portal.directory_maintenance import run
    from management_common.directory_retention import RetentionStore
    from management_common import directory_cleanup_store as cleanup
    now = datetime.now(timezone.utc)
    path = tmp_path / 'background'
    path.mkdir()
    retention = RetentionStore()
    retention.declare(path, category='background', owner_id='task', finished_at=now-timedelta(days=8))
    remove = cleanup.remove_path
    monkeypatch.setattr(cleanup, 'remove_path', lambda path: (_ for _ in ()).throw(PermissionError('occupied')))
    first = run(now=now, test_roots=[])
    assert first['queued'] == 1 and not first['ok']
    assert first['pending_cleanup'][0]['last_error'] == 'occupied' and path.exists()
    monkeypatch.setattr(cleanup, 'remove_path', remove)
    with retention.queue.database.transaction() as connection:
        connection.execute(f'UPDATE {retention.queue.table} SET available_at=clock_timestamp()')
    second = run(now=now, test_roots=[])
    assert second['ok'] and not second['pending_cleanup'] and not path.exists()


def test_cache_eviction_reservation_is_a_cache_miss_and_blocks_recreation(tmp_path):
    from management_portal import status_cache_store
    from management_common.directory_retention import RetentionStore
    path = tmp_path / 'cache.json'
    status_cache_store.write(path, {'disk_status:1': {'payload': 'first'}})
    retention = RetentionStore()
    assert retention.sweep(cache_capacities={'status': 0})['queued'] == 1
    status_cache_store.write(path, {'disk_status:1': {'payload': 'second'}})
    assert status_cache_store.read(path) == {}
    retention.queue.run_pending()
    status_cache_store.write(path, {'disk_status:1': {'payload': 'third'}})
    assert status_cache_store.read(path)['disk_status:1']['payload'] == 'third'


def test_active_cache_builder_is_not_evicted_and_does_not_block_other_cleanup(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from management_common.file_retention import cache_file
    from management_common.directory_retention import RetentionStore
    active, idle = tmp_path / 'active', tmp_path / 'idle'
    for path in (active, idle):
        with cache_file(path, group='article'):
            path.write_bytes(b'content')
    ready, finish = Event(), Event()
    def build():
        with cache_file(active, group='article'):
            ready.set()
            assert finish.wait(5)
    retention = RetentionStore()
    with ThreadPoolExecutor() as executor:
        future = executor.submit(build)
        assert ready.wait(5)
        try:
            assert retention.sweep(cache_capacities={'article': 0})['queued'] == 1
            retention.queue.run_pending()
            assert active.exists() and not idle.exists()
        finally:
            finish.set()
        future.result()
    assert retention.sweep(cache_capacities={'article': 0})['queued'] == 1
    retention.queue.run_pending()
    assert not active.exists()
