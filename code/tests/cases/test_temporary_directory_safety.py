"""Recovery needs owner end evidence independently of a PostgreSQL lease."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from management_common import file_retention as files
from management_common.directory_retention import RetentionStore
from management_common.owned_path_deletion import OwnedPathDeletionStore


def terminate_lease(store, operation, key_function):
    key = key_function(store, operation) & ((1 << 64) - 1)
    with store.database.read() as conn:
        rows = conn.execute("SELECT pid FROM pg_locks WHERE locktype='advisory' AND classid=%s::oid "
                            "AND objid=%s::oid AND objsubid=1 AND granted",
                            (key >> 32, key & 0xffffffff)).fetchall()
        assert len(rows) == 1
        assert conn.execute('SELECT pg_terminate_backend(%s)', (rows[0][0],)).fetchone()[0]


def test_pg_lease_loss_does_not_end_live_writer(tmp_path):
    program = """
from management_common.file_retention import temporary_directory
from pathlib import Path
import sys, os
with temporary_directory(prefix='writer-', dir=sys.argv[1]) as name:
    path = Path(name)
    with (path / 'payload').open('w') as handle:
        handle.write('before'); handle.flush()
        print(name, flush=True)
        sys.stdin.readline()
        handle.write('after'); handle.flush(); os.fsync(handle.fileno())
        print('WRITER_STILL_WRITES', flush=True)
        sys.stdin.readline()
"""
    child = subprocess.Popen([sys.executable, '-c', program, str(tmp_path)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        path = Path(child.stdout.readline().strip())
        assert path.is_dir()
        store = RetentionStore()
        assert files.recover_temporary_directories()['active'] == [str(path)]
        with store.database.read() as conn:
            row = conn.execute(f'SELECT * FROM {store.queue.reservations}').fetchone()
        terminate_lease(store, row['operation_id'], files._temporary_lock_key)
        report = files.recover_temporary_directories()
        child.stdin.write('write\n'); child.stdin.flush()
        ack = child.stdout.readline().strip()
        print('LIVE_WRITER', json.dumps(dict(pid=child.pid, alive=child.poll() is None,
              ack=ack, exists=path.exists(), recovery=report)))
        assert child.poll() is None and ack == 'WRITER_STILL_WRITES'
        assert path.exists() and (path / 'payload').read_text() == 'beforeafter'
        assert report['active'] == [str(path)] and not report['cleanup']
        with store.database.read() as conn:
            row = conn.execute(f'SELECT * FROM {store.queue.reservations}').fetchone()
        assert files._temporary_metadata(row)['ended'] is False
        child.kill(); child.wait(timeout=10)
        report = files.recover_temporary_directories()
        assert not report['errors'] and not path.exists()
        assert report['cleanup'][0]['status'] == 'completed'
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=10)


@pytest.mark.parametrize('stage', ['insert', 'private'])
def test_pending_claim_recovery_respects_active_reservation(tmp_path, monkeypatch, stage):
    root = tmp_path / 'ancestor'; root.mkdir()
    deletion = OwnedPathDeletionStore()
    method = '_remove' if stage == 'insert' else '_claim_and_remove'
    def interrupted(*args):
        raise PermissionError('interrupted claim')
    with monkeypatch.context() as patch:
        patch.setattr(deletion, method, interrupted)
        info = root.stat()
        with pytest.raises(PermissionError):
            deletion.remove(root, [info.st_dev, info.st_ino])
    with files.temporary_directory(prefix='active-', dir=root) as name:
        path = Path(name); (path / 'payload').write_text('live')
        assert files.recover_temporary_directories()['active'] == [name]
        errors = deletion.recover()
        assert errors and 'reserved' in errors[0].lower()
        assert path.exists() and (path / 'payload').read_text() == 'live'
    assert deletion.recover() == []
    assert not root.exists()


def test_claim_insert_window_recovers_without_immutable_row_mutation(tmp_path, monkeypatch):
    path = tmp_path / 'owned'; path.mkdir()
    deletion = OwnedPathDeletionStore()
    def interrupted(*args):
        raise PermissionError('after INSERT')
    with monkeypatch.context() as patch:
        patch.setattr(deletion, '_remove', interrupted)
        info = path.stat()
        with pytest.raises(PermissionError):
            deletion.remove(path, [info.st_dev, info.st_ino])
    assert OwnedPathDeletionStore().recover() == []
    assert not path.exists() and not list(tmp_path.glob('.tfi-delete-*'))


def test_container_receipt_and_lost_lease_cannot_override_live_parent(tmp_path):
    from management_portal.workbench_projections import application_container_owner as owner
    program = """
from management_portal.workbench_projections import application_container_owner as owner
import sys
owner.tempfile.tempdir = sys.argv[1]
with owner._control() as (control, store, row):
    owner._write(control / 'result.json', {'cleanup_complete': True})
    print(control, flush=True)
    sys.stdin.readline()
"""
    child = subprocess.Popen([sys.executable, '-c', program, str(tmp_path)], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        control = Path(child.stdout.readline().strip())
        assert control.is_dir()
        store = owner._store()
        row = owner._row(store, control)
        terminate_lease(store, row['operation_id'], owner._key)
        report = owner.recover_completed_controls()
        assert child.poll() is None and control.exists()
        assert report['active'] == [str(control)] and not report['cleanup']
        with pytest.raises(RuntimeError, match='active'):
            owner.recover(control)
        child.kill(); child.wait(timeout=10)
        assert not owner.recover_completed_controls()['errors']
        assert not control.exists()
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=10)


@pytest.mark.parametrize('fail_body', [False, True])
def test_ended_receipt_precedes_busy_cleanup_and_same_process_retries(tmp_path, monkeypatch, fail_body):
    from management_common.process_observation import process_is_alive
    def busy(*args):
        raise PermissionError('busy after durable claim INSERT')
    with monkeypatch.context() as patch:
        patch.setattr(OwnedPathDeletionStore, '_remove', busy)
        try:
            with files.temporary_directory(prefix='busy-', dir=tmp_path) as name:
                path = Path(name)
                if fail_body:
                    raise RuntimeError('body failed')
        except RuntimeError as error:
            assert fail_body and str(error) == 'body failed'
    store = RetentionStore()
    with store.database.read() as conn:
        row = conn.execute(f'SELECT * FROM {store.queue.reservations}').fetchone()
    metadata = files._temporary_metadata(row)
    assert metadata['ended'] is True and process_is_alive(metadata['process_owner'])
    before = store.queue.status()[0]
    assert before['status'] == 'retry' and path.exists()
    # Autonomous claim recovery cannot steal even an ended owner's retry.
    assert 'reserved' in OwnedPathDeletionStore().recover()[0]
    store.database.write(lambda conn: conn.execute(f'UPDATE {store.queue.table} SET available_at=clock_timestamp()'))
    report = files.recover_temporary_directories()
    assert not report['errors'] and not path.exists()
    after = store.queue.status()[0]
    assert after['cleanup_id'] == before['cleanup_id'] and after['status'] == 'completed'
    assert after['attempts'] == 2
    assert not list(tmp_path.glob('.tfi-delete-*'))


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux boot/namespace identity cases')
@pytest.mark.parametrize('unknown', ['legacy', 'host', 'namespace', 'pid_missing', 'pid_zero', 'start_missing'])
def test_unknown_owner_after_lease_loss_retains_allocation(tmp_path, unknown):
    with files.temporary_directory(prefix='unknown-', dir=tmp_path) as name:
        path = Path(name)
        store = RetentionStore()
        with store.database.read() as conn:
            row = dict(conn.execute(f'SELECT * FROM {store.queue.reservations}').fetchone())
        terminate_lease(store, row['operation_id'], files._temporary_lock_key)
        metadata = files._temporary_metadata(row)
        if unknown == 'legacy':
            metadata.pop('process_owner', None)
            metadata.pop('ended', None)
        elif unknown == 'host':
            metadata['process_owner']['host'] = 'unobservable-host'
        elif unknown == 'namespace':
            metadata['process_owner']['pid_namespace'] = 'unobservable-namespace'
        elif unknown == 'pid_missing':
            metadata['process_owner'].pop('pid')
        elif unknown == 'pid_zero':
            metadata['process_owner']['pid'] = 0
        else:
            metadata['process_owner'].pop('start_ticks')
        store.database.write(lambda conn: conn.execute(
            f'UPDATE {store.queue.reservations} SET purpose=%s WHERE operation_id=%s',
            (files._TEMPORARY_PURPOSE + json.dumps(metadata), row['operation_id'])))
        report = files.recover_temporary_directories()
        assert report['active'] == [name] and not report['cleanup'] and path.exists()
        with store.database.read() as conn:
            persisted = conn.execute(f'SELECT * FROM {store.queue.reservations}').fetchone()
        assert files._temporary_metadata(persisted).get('ended') is not True
    assert not path.exists()


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux /proc access observations')
@pytest.mark.parametrize('unavailable', ['boot_missing', 'stat_hidden', 'stat_denied', 'namespace_denied'])
def test_process_observation_missing_access_is_not_death(monkeypatch, unavailable):
    from management_common import process_observation as observation
    from management_portal import business_tasks
    owner = observation.process_identity()
    assert business_tasks.process_identity is observation.process_identity
    assert business_tasks.process_is_alive is observation.process_is_alive
    read = Path.read_text
    def unavailable_read(path, *args, **kwargs):
        if unavailable == 'boot_missing' and str(path).endswith('/boot_id'):
            raise FileNotFoundError('boot unavailable')
        if str(path) == f'/proc/{os.getpid()}/stat':
            if unavailable == 'stat_hidden':
                raise FileNotFoundError('proc hidden')
            if unavailable == 'stat_denied':
                raise PermissionError('proc denied')
        return read(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', unavailable_read)
    if unavailable == 'namespace_denied':
        def denied(*args):
            raise PermissionError('namespace denied')
        monkeypatch.setattr(observation.os, 'readlink', denied)
    assert observation.process_is_alive(owner)


@pytest.mark.parametrize('reservation', ['same', 'parent'])
def test_claim_recovery_obeys_all_overlaps_without_starving_other_claims(tmp_path, monkeypatch, reservation):
    root = tmp_path / 'root'; root.mkdir()
    owned = root / 'owned'; owned.mkdir()
    other = tmp_path / 'other'; other.mkdir()
    deletion = OwnedPathDeletionStore()
    def interrupted(*args):
        raise PermissionError('after INSERT')
    with monkeypatch.context() as patch:
        patch.setattr(deletion, '_remove', interrupted)
        for path in (owned, other):
            info = path.stat()
            with pytest.raises(PermissionError):
                deletion.remove(path, [info.st_dev, info.st_ino])
    queue = RetentionStore().queue
    with queue.database.transaction() as conn:
        queue.reserve(owned if reservation == 'same' else root,
                      operation_id='another-owner', purpose='active-owner', connection=conn)
    errors = deletion.recover()
    assert len(errors) == 1 and 'reserved' in errors[0]
    assert owned.exists() and not other.exists()
    with queue.database.transaction() as conn:
        queue.release('another-owner', connection=conn)
    assert deletion.recover() == [] and not owned.exists()


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux persisted identity compatibility')
@pytest.mark.parametrize('ticks,alive', [('-1', False), ('not-a-tick', True), ('', True)])
def test_process_observation_preserves_reuse_and_unknown_identity(ticks, alive):
    from management_common.process_observation import process_identity, process_is_alive
    owner = process_identity()
    assert process_is_alive({**owner, 'start_ticks': ticks}) is alive
