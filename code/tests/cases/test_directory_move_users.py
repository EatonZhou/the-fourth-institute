"""Directory moves stop every Portal user, regardless of graph membership.

:module: resident.workspace-memory
:design: decision_4aa13bc21e8e
"""
from pathlib import Path
from types import SimpleNamespace
import os
import shutil

import pytest

from management_portal.society_service import (
    resident_catalog, resident_domain, resident_lifecycle_domain as lifecycle,
    resident_workspace_domain, resident_execution_work, execution_client, directory_execution_admission,
)
from management_portal.society_service.script_runs import service as scripts, store as script_store
from tests.cases.test_resident_folder_creation_contract import future_creation_store, _new_session_payload
from tests.cases.permission_test_support import enable


@pytest.fixture(params=['resident', 'lineage'])
def users(future_creation_store, monkeypatch, tmp_path, request):
    permissions = enable(monkeypatch)
    permissions.users('alice', 'bob')
    from management_common.resource_lifecycle import LifecycleStore, lifecycle_scope
    scope = LifecycleStore().open('move-users', actor='alice', run_id='move-users', case_id='move-users')
    with lifecycle_scope(scope, actor='alice'):
        records = [resident_domain.register_resident(_new_session_payload(title), actor)
                   for title, actor in [('Moving', 'alice'), ('Bound elsewhere', 'bob'), ('Cwd elsewhere', 'bob')]]
    moving, bound, cwd = records
    source = Path(moving['workspace_path'])
    if request.param == 'resident':
        from management_portal.society_service.resident_record_domain import resident_folder_path
        source = resident_folder_path(moving)
    resident_workspace_domain.add_resident_reference_workspace(
        bound['resident_id'], moving['owned_workspace']['path'], 'bob',
        workspace_id=moving['owned_workspace_id'])
    for record in records:
        resident_execution_work.write(record, {
            'execution_id': 'execution-' + record['resident_id'], 'actor': record['created_by'],
            'submitted_at': scripts.now(), 'prompt_cwd': str(source / 'workspace') if record == cwd else '',
        })
    # This Resident was deliberately closed before the move.
    database = resident_catalog.get_catalog().state_store.database
    database.write(lambda c: c.execute('UPDATE residents SET intake_open=FALSE WHERE resident_id=%s', (cwd['resident_id'],)))
    script_store.admit({'script_run_id': 'external-script', 'status': 'running',
        'lineage_id': bound['lineage_id'], 'caller_id': 'bob', 'cwd': str(source / 'workspace'),
        'path': str(tmp_path / 'external.py'), 'started_at': scripts.now(), 'unit': 'isolated'})
    cancelled = []
    def cancel(execution_id, *, actor, **kwargs):
        record = next(r for r in records if execution_id == 'execution-' + r['resident_id'])
        assert actor == record['created_by']
        cancelled.append(record['resident_id'])
        return {'status': 'cancelled'}
    monkeypatch.setattr(execution_client, 'service', lambda: SimpleNamespace(
        cancel=cancel, wait=lambda execution_id, **kw: {'status': 'cancelled'}))
    monkeypatch.setattr(scripts.runtime, 'RUN_ROOT', tmp_path / 'script-runs')
    monkeypatch.setattr(scripts, '_stop_run', lambda record, result: True)
    monkeypatch.setattr(scripts, 'revoke', lambda record: None)
    return SimpleNamespace(records=records, source=source, cancelled=cancelled, database=database)


def test_other_lineage_binding_cwd_and_script_are_stopped(users):
    moving, bound, cwd = users.records
    assert len({r['lineage_id'] for r in users.records}) == 3
    with lifecycle.directory_move_execution_scope([moving], actor='alice') as terminate:
        terminate(users.source)
        assert set(users.cancelled) == {r['resident_id'] for r in users.records}
        assert script_store.get('external-script')['status'] == 'cancelled'
        assert [resident_catalog.get_catalog().get(r['resident_id'])['intake_open']
                for r in users.records] == [True, True, False]
    assert resident_catalog.get_catalog().get(moving['resident_id'])['intake_open'] is True
    assert resident_catalog.get_catalog().get(bound['resident_id'])['intake_open'] is True
    assert resident_catalog.get_catalog().get(cwd['resident_id'])['intake_open'] is False


def test_shared_parent_does_not_make_sibling_resident_or_script_a_move_user(users, monkeypatch):
    outside = resident_domain.register_resident(_new_session_payload('Independent sibling'), 'bob')
    # The fixture places Lineages outside its synthetic Project directory.
    # Supply the ordinary shared ancestor binding in the catalog projection.
    project = users.source.parent.parent.parent
    catalog = resident_catalog.get_catalog()
    records = catalog.all()
    next(r for r in records if r['resident_id'] == outside['resident_id'])['workspace_bindings'].append(
        {'path': str(project)})
    monkeypatch.setattr(catalog, 'all', lambda: records)
    script_store.admit({'script_run_id': 'parent-script', 'status': 'running',
        'lineage_id': outside['lineage_id'], 'caller_id': 'bob', 'cwd': str(project),
        'path': str(project / 'independent.py'), 'started_at': scripts.now(), 'unit': 'isolated'})
    records, runs = lifecycle.directory_execution_users(users.source)
    assert outside['resident_id'] not in {r['resident_id'] for r in records}
    assert 'parent-script' not in {r['script_run_id'] for r in runs}
    assert {r['resident_id'] for r in records} == {r['resident_id'] for r in users.records}


def cross_device(monkeypatch, source):
    stat = Path.stat
    def changed(path, *args, **kwargs):
        result = stat(path, *args, **kwargs)
        if path == source:
            values = list(result)
            values[2] += 1
            return os.stat_result(values)
        return result
    monkeypatch.setattr(Path, 'stat', changed)


@pytest.mark.parametrize('failure', ['', 'copy', 'registration', 'dependencies'])
def test_copy_runs_only_after_all_users_stop_and_intake_restores(users, monkeypatch, failure):
    from management_common.directory_move_store import DirectoryMoveStore
    from management_portal.directory_move_owner import relocate_nested
    cross_device(monkeypatch, users.source)
    target = users.source.with_name('moved')
    copier = shutil.copytree
    copied = []
    def copy(source, destination, *args, **kwargs):
        if args:
            return copier(source, destination, *args, **kwargs)
        assert set(users.cancelled) == {r['resident_id'] for r in users.records}
        assert script_store.get('external-script')['status'] == 'cancelled'
        assert [resident_catalog.get_catalog().get(r['resident_id'])['intake_open']
                for r in users.records] == [True, True, False]
        with pytest.raises(FileExistsError, match='reserved'):
            with directory_execution_admission.admit([str(users.source / 'workspace')]):
                pytest.fail('copy admitted another writer')
        copied.append(True)
        if failure == 'copy':
            raise RuntimeError('copy failed')
        return copier(source, destination, **kwargs)
    monkeypatch.setattr(shutil, 'copytree', copy)
    def update(connection, source, target):
        if failure == 'registration':
            raise RuntimeError('registration failed')
        relocate_nested(connection, source, target)
    def dependencies(*_):
        if failure == 'dependencies':
            raise RuntimeError('dependencies failed')
    mover = DirectoryMoveStore()
    def move():
        with lifecycle.directory_move_execution_scope([users.records[0]], actor='alice') as terminate:
            return mover.move(users.source, target, owner_id=users.records[0]['lineage_id'],
                actor='alice', terminate=terminate, database=users.database,
                update=update, dependencies=dependencies)
    if failure:
        with pytest.raises(RuntimeError, match=failure):
            move()
    else:
        operation, _ = move()
        assert mover.get(operation)['actor'] == 'alice'
    assert copied
    assert [resident_catalog.get_catalog().get(r['resident_id'])['intake_open'] for r in users.records] == [True, True, False]


def test_closed_callback_cannot_be_reused_to_skip_termination(users):
    with lifecycle.directory_move_execution_scope([users.records[0]], actor='alice') as terminate:
        terminate(users.source)
        terminate(users.source)
        assert len(users.cancelled) == 6
        assert [resident_catalog.get_catalog().get(r['resident_id'])['intake_open']
                for r in users.records] == [True, True, False]
    with pytest.raises(RuntimeError, match='already closed'):
        terminate(users.source)


def test_cancel_uses_actual_execution_owner_and_preserves_move_actor(users, monkeypatch, registered_execution_defaults):
    from management_portal.execution.contracts import ExecutionRequest
    from management_portal.execution.scheduler import Scheduler
    scheduler = Scheduler()
    requests = []
    for record in users.records:
        request = ExecutionRequest.new(actor='alice' if record is users.records[2] else 'bob', agent_id=record['resident_id'],
            prompt='isolated queued work', service_type='agent_session', provider='codex', operation='turn')
        scheduler.submit(request)
        requests.append(request)
        work = resident_execution_work.read(record)
        work['execution_id'] = request.execution_id
        work['actor'] = request.actor
        resident_execution_work.write(record, work)
    with pytest.raises(PermissionError, match='another actor'):
        scheduler.cancel(requests[1].execution_id, actor='alice')
    def wait(execution_id, *, actor, **kwargs):
        assert scheduler.actor_of(execution_id) == actor
        return scheduler.snapshot(execution_id)
    monkeypatch.setattr(execution_client, 'service', lambda: SimpleNamespace(cancel=scheduler.cancel, wait=wait))
    with lifecycle.directory_move_execution_scope([users.records[0]], actor='alice') as terminate:
        terminate(users.source)
    for request in requests:
        assert scheduler.actor_of(request.execution_id) == request.actor
        snapshot = scheduler.snapshot(request.execution_id)
        assert snapshot['status'] == 'cancelled'
        event = next(item for item in snapshot['events'] if item['type'] == 'cancelled')
        assert event['details']['actor'] == 'alice'
        assert event['details']['request_actor'] == request.actor


def test_project_preview_uses_same_directory_users(users):
    from management_portal.project_rename import ProjectRename
    from management_portal.society_service.project_domain import ProjectExecutionControl
    change = ProjectRename('unrelated-project', 'renamed', users.source, users.source.with_name('moved'))
    assert set(ProjectExecutionControl('alice').affected_residents(change)) == {r['resident_id'] for r in users.records}


def test_active_preparation_without_submission_blocks_copy(users, monkeypatch):
    from management_common.directory_move_store import DirectoryMoveStore
    record = users.records[1]
    work = resident_execution_work.read(record)
    work.pop('submitted_at')
    work['intake_admitted_at'] = scripts.now()
    resident_execution_work.write(record, work)
    cross_device(monkeypatch, users.source)
    monkeypatch.setattr(shutil, 'copytree', lambda *a, **kw: pytest.fail('unconfirmed preparation copied'))
    with lifecycle.directory_move_execution_scope([users.records[0]], actor='alice') as terminate:
        with pytest.raises(RuntimeError, match='preparation is not yet stopped'):
            DirectoryMoveStore().prepare(users.source, users.source.with_name('moved'),
                owner_id=record['resident_id'], actor='alice', terminate=terminate)
    assert users.source.is_dir()
    assert resident_catalog.get_catalog().get(record['resident_id'])['intake_open'] is True


def test_unconfirmed_script_termination_blocks_copy(users, monkeypatch):
    from management_common.directory_move_store import DirectoryMoveStore
    monkeypatch.setattr(scripts, '_stop_run', lambda *_: False)
    cross_device(monkeypatch, users.source)
    monkeypatch.setattr(shutil, 'copytree', lambda *a, **kw: pytest.fail('live script copied'))
    with lifecycle.directory_move_execution_scope([users.records[0]], actor='alice') as terminate:
        with pytest.raises(RuntimeError, match='Script termination is not yet confirmed'):
            DirectoryMoveStore().prepare(users.source, users.source.with_name('moved'),
                owner_id=users.records[0]['resident_id'], actor='alice', terminate=terminate)
    assert users.source.is_dir()
    assert [resident_catalog.get_catalog().get(r['resident_id'])['intake_open'] for r in users.records] == [True, True, False]


def test_script_discovery_is_not_limited_to_first_page(users, tmp_path):
    for index in range(205):
        script_store.admit({'script_run_id': f'extra-{index:03}', 'status': 'running',
            'lineage_id': users.records[1]['lineage_id'], 'caller_id': 'bob',
            'cwd': str(tmp_path), 'path': str(users.source / 'tool.py'),
            'started_at': scripts.now(), 'unit': 'isolated'})
    script_store.admit({'script_run_id': 'unrelated', 'status': 'running',
        'lineage_id': users.records[2]['lineage_id'], 'caller_id': 'bob',
        'cwd': str(tmp_path / 'outside'), 'path': str(tmp_path / 'outside' / 'tool.py'),
        'started_at': scripts.now(), 'unit': 'isolated'})
    with lifecycle.directory_move_execution_scope([users.records[0]], actor='alice') as terminate:
        terminate(users.source)
    remaining = script_store.list_runs({'status': 'running', 'limit': 200})
    assert remaining['total'] == 1
    assert remaining['runs'][0]['script_run_id'] == 'unrelated'


def test_directory_snapshot_waits_for_inflight_admission(users, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import contextmanager
    from threading import Event
    waiting = Event()
    authority = directory_execution_admission.authority
    @contextmanager
    def observe(*, shared=False):
        if not shared:
            waiting.set()
        with authority(shared=shared):
            yield
    monkeypatch.setattr(directory_execution_admission, 'authority', observe)
    def terminate():
        with lifecycle.directory_move_execution_scope([users.records[0]], actor='alice') as stop:
            stop(users.source)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with directory_execution_admission.admit([users.source]):
            future = pool.submit(terminate)
            assert waiting.wait(5)
            assert not future.done()
            assert not users.cancelled
        future.result(timeout=10)
    assert set(users.cancelled) == {r['resident_id'] for r in users.records}


@pytest.mark.parametrize('failure', [False, True])
def test_project_rename_rpc_owns_cross_lineage_stop_and_restore(users, monkeypatch, failure):
    import contextvars
    import json
    from management_portal import project_rename
    from management_portal.resources import isa
    from management_portal.society_service.server import SocietyServiceServer
    from management_portal.society_service.transport import SocietyServiceClient
    from tests.cases.registry_support import write_projects

    write_projects({'schema_version': 1, 'projects': [{'id': 'Old', 'path': str(users.source),
        'directory': {'owner_id': 'Old'}}]})
    monkeypatch.setattr(project_rename, 'PROJECTS_ROOT', users.source.parent)
    monkeypatch.setattr(project_rename, 'external_blockers', lambda *_: [])
    monkeypatch.setattr(project_rename, 'external_references', lambda *_: [])
    monkeypatch.setattr(isa, '_project_record', lambda *_: {'resource_id': 'Old'})
    server = SocietyServiceServer.__new__(SocietyServiceServer)
    server.read_pool = SimpleNamespace(workers=0)
    server._finish_changes = lambda _: None
    wire = []
    def request(_self, method, params):
        assert method == 'domain.call'
        params = json.loads(json.dumps(params))
        wire.append(params)
        # Neither a callback nor a caller-local control context survives RPC.
        result = contextvars.Context().run(server._domain_call, params)
        return json.loads(json.dumps(result))
    monkeypatch.setattr(SocietyServiceClient, '_request', request)
    client = SocietyServiceClient('/unused-test-socket')
    monkeypatch.setattr('management_portal.society_service.get_society_service', lambda: client)
    cross_device(monkeypatch, users.source)
    copied = []
    copier = shutil.copytree
    def copy(source, target, *args, **kwargs):
        if not args:
            assert set(users.cancelled) == {r['resident_id'] for r in users.records}
            assert script_store.get('external-script')['status'] == 'cancelled'
            assert [resident_catalog.get_catalog().get(r['resident_id'])['intake_open']
                    for r in users.records] == [True, True, False]
            copied.append(True)
            if failure:
                raise RuntimeError('copy interrupted')
        return copier(source, target, *args, **kwargs)
    monkeypatch.setattr(shutil, 'copytree', copy)
    args = {'project_id': 'Old', 'new_project_id': 'New', 'dry_run': False}
    inv = SimpleNamespace(argument=args.get, caller='alice', context=SimpleNamespace(entry='system'))
    if failure:
        with pytest.raises(RuntimeError, match='copy interrupted'):
            isa.rename_project(inv)
        assert users.source.is_dir()
    else:
        result = isa.rename_project(inv)
        assert result['project_id'] == 'New'
        assert users.source.with_name('New').is_dir()
    assert copied
    assert wire[0]['domain'] == 'project' and wire[0]['name'] == 'rename_project'
    assert [resident_catalog.get_catalog().get(r['resident_id'])['intake_open']
            for r in users.records] == [True, True, False]
