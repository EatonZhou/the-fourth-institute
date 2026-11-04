"""One isolated real cross-device writer / session-loss / recovery probe."""
from contextvars import copy_context
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from uuid import uuid4
from urllib.parse import parse_qs, urlsplit

import pytest

from management_common.directory_move_store import DirectoryMoveStore, MoveWriterUnavailable
from management_common.directory_paths import cleanup_path
from tests.cases.test_workspace_unregistration import workspace
from tests.cases.test_workspace_resource_rename import workspace as resource_workspace

WRITER = r'''
"""Real prepare/copytree/copyfile/sendfile writer; pause only at first partial sendfile."""
import json
import os
from pathlib import Path
import sys
import time

from management_common.directory_move_store import DirectoryMoveStore
from management_common import host_instances

source, target, control, operation = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4]
# Inherit the parent fixture's host registry, just as its instance and database.
# A subprocess does not inherit conftest's in-memory monkeypatch.
host_instances.user_home = lambda: Path(sys.argv[5])
original = os.sendfile
paused = False

def sendfile(out_fd, in_fd, offset, count):
    global paused
    if not paused and os.readlink(f'/proc/self/fd/{in_fd}') == str(source / 'payload.bin'):
        paused = True
        copied = original(out_fd, in_fd, offset, min(count, 4096))
        (control / 'ready.json').write_text(json.dumps({'pid': os.getpid(), 'copied': copied,
            'destination': os.readlink(f'/proc/self/fd/{out_fd}'), 'nlink': os.fstat(out_fd).st_nlink}))
        deadline = time.monotonic() + 40
        while not (control / 'resume').exists():
            if time.monotonic() > deadline:
                raise TimeoutError('probe control deadline')
            time.sleep(.01)
        # Still execute the real kernel copy on the original open descriptors.
        after = original(out_fd, in_fd, offset + copied, min(count - copied, 4096))
        (control / 'after.json').write_text(json.dumps({'nlink': os.fstat(out_fd).st_nlink,
            'copied_after_recovery': after, 'destination': os.readlink(f'/proc/self/fd/{out_fd}')}))
        while not (control / 'finish').exists():
            if time.monotonic() > deadline:
                raise TimeoutError('probe finish deadline')
            time.sleep(.01)
        return copied + after
    return original(out_fd, in_fd, offset, count)

os.sendfile = sendfile
try:
    DirectoryMoveStore().prepare(source, target, owner_id='workspace_123456789abc',
        actor='move-lease-probe', terminate=lambda _: None, operation_id=operation)
except BaseException as exc:
    (control / 'outcome.json').write_text(json.dumps({'type': type(exc).__name__, 'message': str(exc)}))
else:
    (control / 'outcome.json').write_text(json.dumps({'type': 'success'}))

deadline = time.monotonic() + 40
while not (control / 'exit').exists():
    if time.monotonic() > deadline:
        raise TimeoutError('probe exit deadline')
    time.sleep(.01)
'''

def wait_for(predicate, seconds=20):
    deadline = time.monotonic() + seconds
    while not predicate():
        assert time.monotonic() < deadline, 'probe timeout'
        time.sleep(.01)

@pytest.mark.skipif(sys.platform != 'linux', reason='real sendfile and distinct /dev/shm mount')
@pytest.mark.parametrize('ending', ['death', 'ended'])
def test_live_copy_survives_session_loss_then_recovers(workspace, tmp_path, ending):
    from management_common import host_instances
    mover = DirectoryMoveStore()
    # Refuse anything except the disposable server created by the standard fixture.
    host = parse_qs(urlsplit(mover.database.dsn).query)['host'][0]
    assert host.startswith('/tmp/tfi-portal-pytest-pg-') and host.endswith('/socket')
    cluster = Path(host).parent
    assert int((cluster / 'owner.pid').read_text()) == os.getpid()
    assert mover.database.schema.startswith(f'pytest_{os.getpid()}_')
    operation = 'move-lease-probe-' + uuid4().hex
    key = int.from_bytes(hashlib.sha256(operation.encode()).digest()[:8], 'big', signed=True)
    unsigned = key & ((1 << 64) - 1)
    (workspace.root / 'payload.bin').write_bytes(b'A' * 32768)
    control = tmp_path / 'control'
    control.mkdir()
    with tempfile.TemporaryDirectory(prefix='move-lease-probe-', dir='/dev/shm') as directory:
        target = Path(directory) / 'target'
        assert workspace.root.stat().st_dev != target.parent.stat().st_dev
        child_env = dict(os.environ, PYTHONPATH=str(Path.cwd()), PYTHONDONTWRITEBYTECODE='1')
        writer = control / 'writer.py'
        writer.write_text(WRITER)
        child = subprocess.Popen([sys.executable, str(writer), str(workspace.root),
            str(target), str(control), operation, str(host_instances.user_home())], env=child_env, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, text=True)
        try:
            wait_for(lambda: (control / 'ready.json').exists() or (control / 'outcome.json').exists() or child.poll() is not None)
            assert child.poll() is None, child.stderr.read()
            assert not (control / 'outcome.json').exists(), (control / 'outcome.json').read_text()
            ready = json.loads((control / 'ready.json').read_text())
            assert ready['pid'] == child.pid and ready['copied'] == 4096 and ready['nlink'] == 1
            row = mover.get(operation)
            stage = cleanup_path(row['staging'])
            assert row['mode'] == 'copy' and row['phase'] == 'prepared'
            assert (stage / 'directory/payload.bin').stat().st_size == 4096
            with mover.database.read() as conn:
                own_locks = conn.execute("SELECT l.pid,a.application_name FROM pg_locks l JOIN pg_stat_activity a ON a.pid=l.pid "
                    "WHERE l.locktype='advisory' AND l.database=(SELECT oid FROM pg_database WHERE datname=current_database()) "
                    "AND l.classid=%s AND l.objid=%s AND l.objsubid=1 AND l.granted",
                    (unsigned >> 32, unsigned & 0xffffffff)).fetchall()
                assert len(own_locks) == 1
                lock_pid = own_locks[0]['pid']
                assert conn.execute('SELECT pg_terminate_backend(%s)', (lock_pid,)).fetchone()[0]
            def released():
                with mover.database.read() as conn:
                    return not conn.execute('SELECT 1 FROM pg_stat_activity WHERE pid=%s', (lock_pid,)).fetchone()
            wait_for(released)
            assert child.poll() is None
            recovered = DirectoryMoveStore().recover(dependencies=lambda *_: None)
            row = next(r for r in recovered if r['operation_id'] == operation)
            with mover.database.read() as conn:
                reservations = conn.execute(f'SELECT count(*) FROM {mover.cleanup.reservations} WHERE operation_id=%s',
                                           (operation,)).fetchone()[0]
            assert child.poll() is None
            assert row['phase'] == 'prepared' and stage.is_dir() and reservations == 3
            (control / 'resume').touch()
            wait_for(lambda: (control / 'after.json').exists())
            after = json.loads((control / 'after.json').read_text())
            assert after['nlink'] == 1 and after['copied_after_recovery'] == 4096
            assert (stage / 'directory/payload.bin').stat().st_size == 8192
            assert child.poll() is None
            if ending == 'death':
                child.kill()  # only our exact disposable writer; no end receipt
                child.communicate(timeout=10)
            else:
                (control / 'finish').touch()
                wait_for(lambda: (control / 'outcome.json').exists())
                assert child.poll() is None  # explicit end, while hosting process lives
                outcome = json.loads((control / 'outcome.json').read_text())
                assert outcome['type'] != 'success', outcome
                assert mover.get(operation)['phase'] == 'prepared'
                assert stage.is_dir() and not target.exists()
            result = next(r for r in mover.recover(dependencies=lambda *_: None)
                          if r['operation_id'] == operation)
            assert result['phase'] == 'aborted' and not stage.exists() and not target.exists()
            if ending == 'ended':
                assert child.poll() is None
                (control / 'exit').touch()
                child.communicate(timeout=10)
            assert (workspace.root / 'payload.bin').read_bytes() == b'A' * 32768
            with mover.database.read() as conn:
                assert conn.execute(f'SELECT count(*) FROM {mover.cleanup.reservations} WHERE operation_id=%s',
                                    (operation,)).fetchone()[0] == 0
            # A completed recovery must never delete a replacement at the old name.
            stage.mkdir()
            (stage / 'replacement').write_text('host')
            mover.recover(dependencies=lambda *_: None)
            mover.abort(operation)
            assert (stage / 'replacement').read_text() == 'host'
            print('MOVE_WRITER_PROOF', json.dumps({'operation': operation, 'ending': ending,
                'writer_pid': child.pid, 'terminated_backend_pid': lock_pid,
                'source_device': workspace.root.stat().st_dev, 'target_device': target.parent.stat().st_dev,
                'live_phase': row['phase'], 'protected_reservations': reservations,
                'after': after, 'recovered_phase': result['phase']}))
        finally:
            if child.poll() is None:
                child.terminate()  # exact Popen child only
                child.communicate(timeout=10)


def prepare(mover, workspace, *, operation='owner-test'):
    target = workspace.root.with_name('moved')
    return mover.prepare(workspace.root, target, owner_id='owner',
                         actor='actor', terminate=lambda _: None, operation_id=operation)


def writer_receipt(mover, operation):
    with mover.database.read() as connection:
        rows = connection.execute(f'SELECT purpose FROM {mover.cleanup.reservations} '
                                  'WHERE operation_id=%s', (operation,)).fetchall()
    receipts = [json.loads(r['purpose'].split(':', 1)[1]) for r in rows
                if r['purpose'].startswith('directory-move-writer-v1:')]
    assert len(receipts) == 1
    return receipts[0]


def kill_test_lease(mover, operation):
    """Terminate exactly one lock backend, only in our owned disposable cluster."""
    host = parse_qs(urlsplit(mover.database.dsn).query)['host'][0]
    assert host.startswith('/tmp/tfi-portal-pytest-pg-') and host.endswith('/socket')
    assert int((Path(host).parent / 'owner.pid').read_text()) == os.getpid()
    assert mover.database.schema.startswith(f'pytest_{os.getpid()}_')
    unsigned = int.from_bytes(hashlib.sha256(operation.encode()).digest()[:8], 'big')
    with mover.database.read() as connection:
        rows = connection.execute("SELECT pid FROM pg_locks WHERE locktype='advisory' "
            "AND database=(SELECT oid FROM pg_database WHERE datname=current_database()) "
            'AND classid=%s AND objid=%s AND objsubid=1 AND granted',
            (unsigned >> 32, unsigned & 0xffffffff)).fetchall()
        assert len(rows) == 1
        backend = rows[0]['pid']
        assert connection.execute('SELECT pg_terminate_backend(%s)', (backend,)).fetchone()[0]
    def gone():
        with mover.database.read() as connection:
            return not connection.execute('SELECT 1 FROM pg_stat_activity WHERE pid=%s', (backend,)).fetchone()
    wait_for(gone)


def test_lost_session_fences_registration_abort_and_phase_until_explicit_end(workspace):
    mover = DirectoryMoveStore()
    operation = 'lost-owner'
    with mover.operation_lock(operation):
        prepare(mover, workspace, operation=operation)
        before = writer_receipt(mover, operation)
        assert before['ended'] is False and before['process']['pid'] == os.getpid()
        kill_test_lease(mover, operation)
        for effect in (
            lambda: mover.database.write(lambda c: mover.commit(operation, connection=c,
                         update=lambda *_: pytest.fail('stale registration'))),
            lambda: mover.abort(operation),
            lambda: mover._phase(operation, 'aborted'),
            lambda: mover.finish(operation, dependencies=lambda *_: pytest.fail('stale dependency')),
        ):
            with pytest.raises(MoveWriterUnavailable):
                effect()
        # A new thread cannot inherit ownership; free PG lock still needs end.
        with ThreadPoolExecutor(max_workers=1) as executor:
            result, = executor.submit(lambda: DirectoryMoveStore().recover(dependencies=lambda *_: None)).result(10)
        assert result['phase'] == 'files_ready'
        assert writer_receipt(mover, operation) == before
        assert workspace.root.with_name('moved').is_dir() and not workspace.root.exists()
    ended = writer_receipt(mover, operation)
    assert ended == {**before, 'ended': True}
    result, = mover.recover(dependencies=lambda *_: pytest.fail('uncommitted'))
    assert result['phase'] == 'aborted' and workspace.root.is_dir()


def test_nested_ownership_rejects_copied_context_and_new_threads(workspace):
    mover = DirectoryMoveStore()
    operation = 'nested-owner'
    with mover.operation_lock(operation):
        prepare(mover, workspace, operation=operation)
        before = writer_receipt(mover, operation)
        with DirectoryMoveStore().operation_lock(operation):
            assert writer_receipt(mover, operation) == before
        with pytest.raises(MoveWriterUnavailable, match='context'):
            copy_context().run(mover.abort, operation)
        with ThreadPoolExecutor(max_workers=1) as executor:
            for callback in (lambda: mover.abort(operation),
                             lambda: copy_context().run(mover.abort, operation)):
                with pytest.raises(MoveWriterUnavailable):
                    executor.submit(callback).result(10)
            context = copy_context()
            with pytest.raises(MoveWriterUnavailable, match='execution'):
                executor.submit(context.run, mover.abort, operation).result(10)
        assert mover.get(operation)['phase'] == 'files_ready'
        assert writer_receipt(mover, operation) == before
    assert writer_receipt(mover, operation)['ended'] is True
    mover.abort(operation)
    assert workspace.root.is_dir()


@pytest.mark.skipif(not hasattr(os, 'fork'), reason='fork inheritance check')
def test_fork_cannot_borrow_parent_execution(workspace):
    mover = DirectoryMoveStore()
    operation = 'fork-owner'
    with mover.operation_lock(operation):
        prepare(mover, workspace, operation=operation)
        read_fd, write_fd = os.pipe()
        # The fork warning is expected with fixture pool threads. Do not wrap
        # fork in a pytest assertion: the child must reach os._exit immediately.
        pid = os.fork()
        if pid == 0:
            os.close(read_fd)
            try:
                mover.abort(operation)
            except MoveWriterUnavailable:
                os.write(write_fd, b'fenced')
            finally:
                os._exit(0)
        os.close(write_fd)
        try:
            import select
            assert select.select([read_fd], [], [], 10)[0]
            assert os.read(read_fd, 32) == b'fenced'
        finally:
            os.close(read_fd)
            os.waitpid(pid, 0)
        assert mover.get(operation)['phase'] == 'files_ready'
    mover.abort(operation)


@pytest.mark.parametrize('prior', ['missing', 'malformed', 'foreign-host', 'incomplete'])
def test_unknown_legacy_writer_preserves_paths_and_reservations(workspace, prior):
    mover = DirectoryMoveStore()
    operation = prepare(mover, workspace)
    receipt = writer_receipt(mover, operation)
    receipt['ended'] = False
    receipt['process'] = ({'host': 'unobservable-host', 'pid': 999999}
                          if prior == 'foreign-host' else {'pid': 999999})
    purpose = ('move-source' if prior == 'missing' else 'directory-move-writer-v1:{bad'
               if prior == 'malformed' else 'directory-move-writer-v1:' + json.dumps(receipt))
    mover.database.write(lambda c: c.execute(f'UPDATE {mover.cleanup.reservations} SET purpose=%s '
        'WHERE operation_id=%s AND purpose LIKE %s',
        (purpose, operation, 'directory-move-writer-v1:%')))
    row, = mover.recover(dependencies=lambda *_: pytest.fail('unknown writer'))
    assert row['phase'] == 'files_ready'
    assert workspace.root.with_name('moved').is_dir() and not workspace.root.exists()
    with pytest.raises(MoveWriterUnavailable):
        mover.abort(operation)
    with mover.database.read() as c:
        assert c.execute(f'SELECT count(*) FROM {mover.cleanup.reservations} WHERE operation_id=%s',
                         (operation,)).fetchone()[0] == 2


def test_changed_token_fences_effects_and_old_end_cannot_overwrite_receipt(workspace):
    mover = DirectoryMoveStore()
    operation = 'token-owner'
    with mover.operation_lock(operation):
        prepare(mover, workspace, operation=operation)
        replacement = {**writer_receipt(mover, operation), 'token': 'successor-token'}
        mover.database.write(lambda c: c.execute(f'UPDATE {mover.cleanup.reservations} SET purpose=%s '
            'WHERE operation_id=%s AND purpose LIKE %s',
            ('directory-move-writer-v1:' + json.dumps(replacement), operation, 'directory-move-writer-v1:%')))
        with pytest.raises(MoveWriterUnavailable, match='receipt'):
            mover.abort(operation)
        with pytest.raises(MoveWriterUnavailable, match='receipt'):
            mover.database.write(lambda c: mover.commit(operation, connection=c,
                update=lambda *_: pytest.fail('superseded registration')))
    assert writer_receipt(mover, operation) == replacement
    assert mover.get(operation)['phase'] == 'files_ready'


@pytest.mark.parametrize('mode', ['rename', 'external'])
def test_registration_callback_session_loss_rolls_back_and_never_finishes(workspace, mode):
    mover = DirectoryMoveStore()
    target = workspace.root.with_name('moved')
    operation_ids = []
    def update(connection, *_):
        row, = connection.execute(f'SELECT * FROM {mover.table}').fetchall()
        operation_ids.append(row['operation_id'])
        # An actual SQL mutation in the owner's transaction must roll back.
        connection.execute(f'UPDATE {mover.table} SET actor=%s WHERE operation_id=%s',
                           ('must-rollback', row['operation_id']))
        kill_test_lease(mover, row['operation_id'])
    kwargs = dict(owner_id='owner', actor='actor', database=workspace.store.database,
                  update=update, dependencies=lambda *_: pytest.fail('lost writer dependencies'))
    with pytest.raises(MoveWriterUnavailable):
        if mode == 'external':
            workspace.root.rename(target)
            mover.register_external_move(workspace.root, target, **kwargs)
        else:
            mover.move(workspace.root, target, terminate=lambda _: None, **kwargs)
    operation, = operation_ids
    row = mover.get(operation)
    assert row['actor'] == 'actor' and row['phase'] == 'files_ready'
    assert writer_receipt(mover, operation)['ended'] is True
    assert target.is_dir()
    result, = mover.recover(dependencies=lambda *_: pytest.fail('uncommitted'))
    assert result['phase'] == 'aborted'
    assert target.exists() == (mode == 'external')


@pytest.mark.parametrize('mode', ['rename', 'external'])
def test_dependency_session_loss_keeps_receipt_until_callback_quiesces(workspace, mode):
    mover = DirectoryMoveStore()
    target = workspace.root.with_name('moved')
    operations = []
    def dependencies(*_):
        with mover.database.read() as c:
            row, = c.execute(f'SELECT * FROM {mover.table}').fetchall()
        operation = row['operation_id']
        operations.append(operation)
        assert row['phase'] == 'committed'
        receipt = writer_receipt(mover, operation)
        assert receipt['ended'] is False
        kill_test_lease(mover, operation)
        with ThreadPoolExecutor(max_workers=1) as executor:
            result, = executor.submit(lambda: DirectoryMoveStore().recover(
                dependencies=lambda *_: pytest.fail('live dependency takeover'))).result(10)
        assert result['phase'] == 'committed'
        assert writer_receipt(mover, operation) == receipt
        assert not mover.cleanup.pending_at(workspace.root)
    kwargs = dict(owner_id='owner', actor='actor', database=workspace.store.database,
                  update=lambda *_: None, dependencies=dependencies)
    with pytest.raises(MoveWriterUnavailable):
        if mode == 'external':
            workspace.root.rename(target)
            mover.register_external_move(workspace.root, target, **kwargs)
        else:
            mover.move(workspace.root, target, terminate=lambda _: None, **kwargs)
    operation, = operations
    assert writer_receipt(mover, operation)['ended'] is True
    assert mover.get(operation)['phase'] == 'committed'
    completed = []
    row, = mover.recover(dependencies=lambda *_: completed.append('completed'))
    assert completed == ['completed'] and row['phase'] == 'completed'
    mover.cleanup.run_pending()
    assert target.is_dir()


@pytest.mark.skipif(sys.platform != 'linux', reason='real distinct /dev/shm filesystem')
def test_termination_callback_lease_loss_prevents_copy(workspace):
    mover = DirectoryMoveStore()
    operation = 'termination-owner'
    (workspace.root / 'keep').write_text('source')
    with tempfile.TemporaryDirectory(prefix='move-termination-', dir='/dev/shm') as directory:
        target = Path(directory) / 'target'
        assert workspace.root.stat().st_dev != target.parent.stat().st_dev
        def terminate(source):
            assert source == workspace.root
            assert writer_receipt(mover, operation)['ended'] is False
            kill_test_lease(mover, operation)
        with pytest.raises(MoveWriterUnavailable):
            mover.prepare(workspace.root, target, owner_id='owner',
                          actor='actor', terminate=terminate, operation_id=operation)
        row = mover.get(operation)
        assert row['phase'] == 'prepared' and not target.exists()
        assert not cleanup_path(row['staging']).exists()
        assert writer_receipt(mover, operation)['ended'] is True
        result, = mover.recover(dependencies=lambda *_: pytest.fail('uncommitted'))
        assert result['phase'] == 'aborted'
        assert (workspace.root / 'keep').read_text() == 'source'


@pytest.mark.skipif(sys.platform != 'linux', reason='real distinct /dev/shm filesystem')
def test_cancelled_move_retains_live_dependency_writer_even_after_session_loss(workspace):
    from management_common.directory_move_store import cancel_owner_moves
    mover = DirectoryMoveStore()
    operation = 'cancelled-live-owner'
    (workspace.root / 'keep').write_text('source')
    with tempfile.TemporaryDirectory(prefix='move-cancel-', dir='/dev/shm') as directory:
        target = Path(directory) / 'target'
        assert workspace.root.stat().st_dev != target.parent.stat().st_dev
        def dependencies(*_):
            kill_test_lease(mover, operation)
            mover.database.write(lambda c: cancel_owner_moves(c, 'owner'))
            assert mover.get(operation)['phase'] == 'cancelled'
            assert writer_receipt(mover, operation)['ended'] is False
            with ThreadPoolExecutor(max_workers=1) as executor:
                result, = executor.submit(lambda: DirectoryMoveStore().recover(dependencies=lambda *_: None)).result(10)
            assert result['phase'] == 'cancelled'
            mover.cleanup.run_pending()
            assert (workspace.root / 'keep').read_text() == 'source'
            assert writer_receipt(mover, operation)['ended'] is False
        with pytest.raises(MoveWriterUnavailable):
            mover.move(workspace.root, target, owner_id='owner', actor='actor',
                       terminate=lambda _: None, database=workspace.store.database,
                       update=lambda *_: None, dependencies=dependencies, operation_id=operation)
        with mover.database.read() as c:
            assert c.execute(f'SELECT count(*) FROM {mover.cleanup.reservations} WHERE operation_id=%s',
                             (operation,)).fetchone()[0] == 0
        mover.cleanup.run_pending()
        assert not workspace.root.exists() and (target / 'keep').read_text() == 'source'
