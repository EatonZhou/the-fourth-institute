"""Real submission, recovery and launch entries obey durable move reservations.

:module: resident.workspace-memory
:design: decision_4aa13bc21e8e
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Event
from types import SimpleNamespace
import shutil

import pytest

from management_common.directory_move_store import DirectoryMoveStore
from management_portal.execution.contracts import ExecutionRequest
from management_portal.execution.scheduler import Scheduler
from management_portal.execution.service import ExecutionService
from management_portal.society_service import (
    directory_execution_admission as admission, execution_client,
    lineage_aggregate, resident_catalog, resident_domain,
    resident_execution_engine_domain as engine, resident_execution_work as work,
    resident_lifecycle_domain as lifecycle, resident_record_domain,
)
from management_portal.society_service.script_runs import service as scripts, store as script_store
from tests.cases.permission_test_support import enable
from tests.cases.test_resident_folder_creation_contract import future_creation_store, _new_session_payload
from tests.cases.test_directory_move_users import cross_device


@pytest.fixture
def admission_world(future_creation_store, monkeypatch, tmp_path, registered_execution_defaults):
    permissions = enable(monkeypatch)
    permissions.users('alice', 'bob')
    moving = resident_domain.register_resident(_new_session_payload('Moving'), 'alice')
    outside = resident_domain.register_resident(_new_session_payload('External Lineage'), 'bob')
    source = resident_record_domain.resident_folder_path(moving)
    target = source.with_name('moved')
    cross_device(monkeypatch, source)
    scheduler = Scheduler()
    calls = []

    def submit(request):
        calls.append('submit')
        return scheduler.submit(request)

    def status(execution_id, *, actor, **kwargs):
        assert scheduler.actor_of(execution_id) == actor
        return scheduler.snapshot(execution_id)

    def resume(execution_id, **kwargs):
        calls.append('resume')
        return ExecutionService.resume(SimpleNamespace(status=status), execution_id, **kwargs)

    service = SimpleNamespace(submit=submit, resume=resume, cancel=scheduler.cancel, wait=status)
    monkeypatch.setattr(execution_client, 'service', lambda: service)
    launches = []
    monkeypatch.setattr(scripts, 'launch', lambda record, args: launches.append(record['script_run_id']))
    monkeypatch.setattr(scripts, '_stop_run', lambda *_: True)
    monkeypatch.setattr(scripts, 'revoke', lambda *_: None)
    monkeypatch.setattr(scripts.runtime, 'RUN_ROOT', tmp_path / 'runs')
    script = tmp_path / 'unrelated' / 'outside.py'
    script.parent.mkdir()
    script.write_text('pass\n')
    return SimpleNamespace(moving=moving, outside=outside, source=source, target=target,
        scheduler=scheduler, service=service, calls=calls, launches=launches, script=script,
        database=resident_catalog.get_catalog().state_store.database)


def resident_attempt(world, *, resume=False, cwd=None):
    record = world.outside
    request = ExecutionRequest.new(actor='bob', agent_id=record['resident_id'],
        prompt='isolated admission', cwd=str(cwd or world.source),
        service_type='agent_session', provider='codex', operation='turn')
    # The durable input and request keep the same identity, as recovery does.
    request = request.__class__.from_payload({**request.to_payload(), 'correlation_id': request.execution_id})
    job = {'execution_id': request.execution_id, 'actor': 'bob', 'intake_admitted_at': scripts.now(),
           'prompt_cwd': request.cwd}
    if resume:
        world.scheduler.submit(request)
        job['submitted_at'] = scripts.now()
    work.write(record, job)
    return lambda: engine.bind_or_resume_resident_execution(world.service, request, record, job)


@pytest.mark.parametrize('entry', ['submit', 'resume', 'script'])
@pytest.mark.parametrize('purpose', ['resource_creation', 'directory_move_source', 'directory_move_target', 'cleanup'])
def test_child_reservation_does_not_block_execution_with_shared_parent(admission_world, entry, purpose):
    world = admission_world
    project = Path(world.outside['workspace_bindings'][0]['path'])
    target = project / 'another-resident-being-created'
    target.mkdir()
    queue = admission.get_directory_cleanup_store()
    with queue.database.transaction() as connection:
        if purpose == 'cleanup':
            queue.enqueue(target, owner_id='other-object', operation_id='another-operation',
                          actor='bob', connection=connection)
        else:
            queue.reserve(target, operation_id='another-operation', purpose=purpose, connection=connection)
    # The ordinary inherited project binding is an ancestor of the new object.
    # Neither an independent Resident turn nor a script in the parent enters it.
    if entry == 'script':
        scripts.start({'lineage_id': world.outside['lineage_id'],
            'cwd': str(project), 'path': str(world.script)}, 'bob')
        assert len(world.launches) == 1
    else:
        resident_attempt(world, resume=entry == 'resume', cwd=world.outside['default_cwd'])()
        assert world.calls == [entry]
    # Registration and execution inside the unpublished object remain blocked.
    with queue.database.transaction() as connection:
        with pytest.raises(FileExistsError, match='reserved'):
            queue.assert_available(project, connection=connection)
    with pytest.raises(FileExistsError, match='reserved'):
        resident_attempt(world, cwd=target / 'workspace')()


@pytest.mark.parametrize('entry', ['submit', 'resume', 'script-cwd', 'script-path'])
@pytest.mark.parametrize('phase', ['prepared', 'copy', 'recovery'])
def test_real_entries_refuse_persistent_reservations(admission_world, monkeypatch, entry, phase):
    world = admission_world
    mover = DirectoryMoveStore()
    script_inside = world.source / 'tool.py'
    script_inside.write_text('pass\n')
    attempts = []

    def probe():
        if entry in {'submit', 'resume'}:
            attempt = resident_attempt(world, resume=entry == 'resume')
        else:
            arguments = {'lineage_id': world.outside['lineage_id'],
                'cwd': str(world.source if entry == 'script-cwd' else world.script.parent),
                'path': str(script_inside if entry == 'script-path' else world.script)}
            attempt = lambda: scripts.start(arguments, 'bob')
        before = len(world.calls), len(world.launches)
        with pytest.raises(FileExistsError, match='reserved'):
            attempt()
        assert (len(world.calls), len(world.launches)) == before
        attempts.append(attempt)

    copier = shutil.copytree
    def copy(source, target, *args, **kwargs):
        if not args:
            if phase == 'copy':
                probe()
            if phase == 'recovery':
                raise SystemExit('simulated process loss')
        return copier(source, target, *args, **kwargs)
    monkeypatch.setattr(shutil, 'copytree', copy)
    if phase == 'recovery':
        monkeypatch.setattr(mover, 'abort', lambda *_args, **_kwargs: None)

    operation = 'directory-move-admission'
    with lifecycle.directory_move_execution_scope([world.moving], actor='alice') as stop:
        def terminate(path):
            # This callback runs after the move reservation transaction commits.
            stop(path)
            if phase == 'prepared':
                probe()
        if phase == 'recovery':
            with pytest.raises(SystemExit):
                mover.prepare(world.source, world.target, owner_id=world.moving['resident_id'],
                    origin='created', actor='alice', terminate=terminate, operation_id=operation)
        else:
            mover.prepare(world.source, world.target, owner_id=world.moving['resident_id'],
                origin='created', actor='alice', terminate=terminate, operation_id=operation)
    if phase == 'recovery':
        # A new store/process still sees the durable reservation after scope exit.
        assert DirectoryMoveStore().get(operation)['phase'] == 'prepared'
        probe()
    assert attempts
    assert resident_catalog.get_catalog().get(world.moving['resident_id'])['intake_open']
    recovered = DirectoryMoveStore().recover(dependencies=lambda *_: None)
    assert recovered[0]['phase'] == 'aborted'
    attempts[0]()
    if entry in {'submit', 'resume'}:
        assert world.calls == [entry]
    else:
        assert len(world.launches) == 1


@pytest.mark.parametrize('entry', ['submit', 'script'])
def test_move_drains_real_admission_without_lock_inversion(admission_world, monkeypatch, entry):
    world = admission_world
    entered, release, draining = Event(), Event(), Event()
    authority = admission.authority
    @contextmanager
    def observe(*, shared=False):
        if not shared:
            draining.set()
        with authority(shared=shared):
            yield
    monkeypatch.setattr(admission, 'authority', observe)
    if entry == 'submit':
        submit = world.service.submit
        def blocked(request):
            result = submit(request)
            entered.set()
            assert release.wait(10)
            return result
        world.service.submit = blocked
        attempt = resident_attempt(world)
    else:
        def blocked(record, args):
            entered.set()
            assert release.wait(10)
            world.launches.append(record['script_run_id'])
        monkeypatch.setattr(scripts, 'launch', blocked)
        attempt = lambda: scripts.start({'path': str(world.script), 'cwd': str(world.source),
            'lineage_id': world.outside['lineage_id']}, 'bob')

    copied = []
    copier = shutil.copytree
    def copy(source, target, *args, **kwargs):
        if not args:
            if entry == 'submit':
                job = work.read(world.outside)
                assert job['submitted_at']
                assert world.scheduler.snapshot(job['execution_id'])['status'] == 'cancelled'
            else:
                assert script_store.get(world.launches[0])['status'] == 'cancelled'
            assert resident_catalog.get_catalog().get(world.moving['resident_id'])['intake_open']
            copied.append(True)
        return copier(source, target, *args, **kwargs)
    monkeypatch.setattr(shutil, 'copytree', copy)

    def move():
        # Match the outer authorities of real mutations, then real PG reservation
        # and drain locks. The other Lineage's admission must finish and bind.
        with lineage_aggregate.execution_authority(world.moving['lineage_id']), \
                resident_record_domain.state_lock(world.moving['resident_id']), \
                lifecycle.directory_move_execution_scope([world.moving], actor='alice') as stop:
            mover = DirectoryMoveStore()
            operation = mover.prepare(world.source, world.target, owner_id=world.moving['resident_id'],
                origin='created', actor='alice', terminate=stop)
            mover.abort(operation)

    with ThreadPoolExecutor(max_workers=2) as pool:
        submitting = pool.submit(attempt)
        assert entered.wait(10)
        moving = pool.submit(move)
        try:
            assert draining.wait(10)
            assert not moving.done()
            assert not copied
        finally:
            release.set()
        submitting.result(timeout=15)
        moving.result(timeout=15)
    assert copied
    assert all(resident_catalog.get_catalog().get(r['resident_id'])['intake_open']
               for r in [world.moving, world.outside])


def test_unrelated_directory_can_submit_and_launch_while_copying(admission_world, monkeypatch):
    world = admission_world
    copier = shutil.copytree
    def copy(source, target, *args, **kwargs):
        if not args:
            resident_attempt(world, cwd=world.outside['default_cwd'])()
            scripts.start({'lineage_id': world.outside['lineage_id'],
                'cwd': str(world.script.parent), 'path': str(world.script)}, 'bob')
        return copier(source, target, *args, **kwargs)
    monkeypatch.setattr(shutil, 'copytree', copy)
    with lifecycle.directory_move_execution_scope([world.moving], actor='alice') as stop:
        mover = DirectoryMoveStore()
        operation = mover.prepare(world.source, world.target, owner_id=world.moving['resident_id'],
            origin='created', actor='alice', terminate=stop)
        mover.abort(operation)
    assert world.calls == ['submit']
    assert len(world.launches) == 1


def test_resident_created_after_snapshot_cannot_enter_source_or_target(admission_world, monkeypatch):
    world = admission_world
    copier = shutil.copytree
    checked = []
    def copy(source, target, *args, **kwargs):
        if not args:
            world.outside = resident_domain.register_resident(_new_session_payload('New during copy'), 'bob')
            for path in [world.source, world.target]:
                with pytest.raises(FileExistsError, match='reserved'):
                    resident_attempt(world, cwd=path)()
                checked.append(path)
        return copier(source, target, *args, **kwargs)
    monkeypatch.setattr(shutil, 'copytree', copy)
    with lifecycle.directory_move_execution_scope([world.moving], actor='alice') as stop:
        mover = DirectoryMoveStore()
        operation = mover.prepare(world.source, world.target, owner_id=world.moving['resident_id'],
            origin='created', actor='alice', terminate=stop)
        mover.abort(operation)
    assert checked == [world.source, world.target]
    assert world.calls == []
