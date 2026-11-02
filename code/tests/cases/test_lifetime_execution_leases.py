"""Execution leases survive database loss while actual filesystem users run."""
from contextlib import contextmanager
import errno
import hashlib
import json
import os
from pathlib import Path
from queue import Queue
from threading import Event, Thread
import time
import subprocess
import sys
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize('failure', ['observation', 'guard'])
def test_native_release_closes_every_pin_despite_retained_error(tmp_path, monkeypatch, failure):
    """清理一项失败仍释放其余父侧句柄，未知原生回执保留待重试。"""
    from management_common import lifetime_execution_lease as lease, process_runtime
    monkeypatch.setattr(lease, 'instance_runtime_root', lambda: tmp_path)
    armed = [False]
    original_lock = lease.file_lock.lock_stream

    def observe(_receipt):
        if armed[0] and failure == 'observation':
            armed[0] = False
            raise PermissionError('native observation unavailable')
        return False

    @contextmanager
    def guard(stream):
        if armed[0] and failure == 'guard':
            armed[0] = False
            raise PermissionError('guard unavailable')
        with original_lock(stream):
            yield

    monkeypatch.setattr(process_runtime, 'native_lifetime_alive', observe)
    monkeypatch.setattr(lease.file_lock, 'lock_stream', guard)
    saved = []
    with lease.execution_lease('first', namespace='release-error'):
        with lease.execution_lease('second', namespace='release-error'):
            try:
                with lease.inherit_execution_leases() as (_, record, pins):
                    record({'version': 1, 'probe': 'ended'})
                    armed[0] = True
            except PermissionError as error:
                saved.append(error)
            assert pins[0].exists()  # Uncertainty must not authorize unlink.
    assert len(saved) == 1  # Keep its traceback, not just its message, alive.
    for identity in ('second', 'first'):
        with lease.execution_lease(identity, namespace='release-error', blocking=False) as acquired:
            assert acquired
    assert not list(tmp_path.glob('execution-leases/*/*/*.lease'))


def kill_exact_shared_backend(database, key):
    import psycopg
    value = key & ((1 << 64)-1)
    with psycopg.connect(database.dsn, autocommit=True) as db:
        rows = db.execute("SELECT pid FROM pg_locks WHERE locktype='advisory' AND mode='ShareLock' AND granted AND classid=%s::oid AND objid=%s::oid AND objsubid=1 AND database=(SELECT oid FROM pg_database WHERE datname=current_database())", (value >> 32, value & 0xffffffff)).fetchall()
        assert len(rows) == 1, rows
        pid = rows[0][0]
        assert db.execute('SELECT pg_terminate_backend(%s)',(pid,)).fetchone()[0]
        for _ in range(200):
            if not db.execute('SELECT 1 FROM pg_stat_activity WHERE pid=%s',(pid,)).fetchone():
                return pid
            time.sleep(.01)
        raise AssertionError('exact backend did not exit')


def writer_body(path, ready, commands, results):
    path.mkdir(exist_ok=True)
    (path/'artifact').write_text('initial content')
    ready.set()
    while True:
        command = commands.get(timeout=30)
        if command == 'stop':
            return
        try:
            with (path/'artifact').open('w') as stream:
                stream.write(command)
                stream.flush()
                os.fsync(stream.fileno())
            results.put({'command':command,'written':True})
        except OSError as error:
            results.put({'command':command,'written':False,'error':type(error).__name__,'errno':error.errno})


def step(commands, results, name):
    commands.put(name)
    return results.get(timeout=10)


def save(name, data):
    print(name, json.dumps(data,default=str))


def test_cache_builder_session_loss(tmp_path):
    from management_common.file_retention import cache_file
    from management_common.directory_retention import RetentionStore
    from management_common.directory_paths import cleanup_location
    path = tmp_path/'cache-build'
    with cache_file(path,group='article'):
        path.mkdir()
        (path/'artifact').write_bytes(b'previous compiled cache')
    store = RetentionStore()
    ready, commands, results = Event(), Queue(), Queue()
    errors=[]
    def build():
        try:
            with cache_file(path,group='article'):
                writer_body(path,ready,commands,results)
        except BaseException as error:
            errors.append(repr(error))
    worker=Thread(target=build)
    worker.start()
    evidence={'writer_pid':os.getpid(),'path':str(path)}
    try:
        assert ready.wait(10)
        evidence['baseline_sweep']=store.sweep(cache_capacities={'article':0})
        assert evidence['baseline_sweep']=={'queued':0}
        assert store.queue.run_pending()==[] and path.exists()
        key=int.from_bytes(hashlib.sha256((store.database.schema+':cache:'+cleanup_location(path.absolute())).encode()).digest()[:8],'big',signed=True)
        evidence['lock_key']=key
        evidence['terminated_backend']=kill_exact_shared_backend(store.database,key)
        evidence['after_pg_death_write']=step(commands,results,'after-pg-death-before-cleanup')
        assert evidence['after_pg_death_write']['written'] and worker.is_alive()
        evidence['sweep_after_death']=store.sweep(cache_capacities={'article':0})
        evidence['formal_cleanup']=store.queue.run_pending()
        evidence['directory_exists']=path.exists()
        evidence['writer_alive_after_cleanup']=worker.is_alive()
        evidence['write_after_cleanup']=step(commands,results,'after-cleanup')
        assert evidence['sweep_after_death']=={'queued':0}
        assert evidence['formal_cleanup']==[]
        assert path.exists() and worker.is_alive()
        assert evidence['write_after_cleanup']['written']
    finally:
        commands.put('stop');worker.join(10)
        evidence['writer_stopped']=not worker.is_alive(); evidence['exit_errors']=errors
        save('cache',evidence)
    assert not worker.is_alive()
    assert not errors
    assert store.sweep(cache_capacities={'article':0}) == {'queued':1}
    assert store.queue.run_pending()[0]['status'] == 'completed'
    assert not path.exists()


def test_background_writer_session_loss(tmp_path):
    from management_common.resource_lifecycle import LifecycleStore,lifecycle_scope
    from management_common.directory_retention import RetentionStore
    from management_portal import business_tasks
    from management_portal.test_framework.run_cleanup import finish_run
    store=LifecycleStore()
    run_id='testrun_0123456789abcdef'
    scope=store.open(str(tmp_path),actor='audit',run_id=run_id,case_id='background')
    path=tmp_path/'background-output'
    ready, done, commands, results=Event(),Event(),Queue(),Queue()
    errors=[]
    task='other-leases-background'
    def write():
        try:
            RetentionStore().declare(path,category='background',owner_id=task)
            writer_body(path,ready,commands,results)
        finally:
            done.set()
    evidence={'writer_pid':os.getpid(),'path':str(path),'scope':scope,'run_id':run_id,'lease_entry':'business_tasks.start -> lifecycle_scope (same scope lock as owner_lease)'}
    with lifecycle_scope(scope,actor='audit'):
        assert business_tasks.start(task,write,on_error=lambda error:errors.append(repr(error)))
        assert ready.wait(10)
    try:
        evidence['baseline_finish']=finish_run(run_id)
        assert evidence['baseline_finish']['status']=='error'
        assert 'still has active requests' in str(evidence['baseline_finish']['errors'])
        assert path.exists() and business_tasks.is_active(task)
        key=store.lock_key(scope)
        evidence['lock_key']=key
        evidence['terminated_backend']=kill_exact_shared_backend(store.db,key)
        evidence['after_pg_death_write']=step(commands,results,'after-pg-death-before-cleanup')
        assert evidence['after_pg_death_write']['written'] and business_tasks.is_active(task)
        evidence['finish_after_death']=finish_run(run_id)
        evidence['scope_after_cleanup']=store.scope(scope)
        evidence['directory_exists']=path.exists()
        evidence['writer_active_after_cleanup']=business_tasks.is_active(task)
        evidence['write_after_cleanup']=step(commands,results,'after-cleanup')
        assert evidence['finish_after_death']['status']=='error'
        assert 'still has active requests' in str(evidence['finish_after_death']['errors'])
        assert path.exists() and business_tasks.is_active(task)
        assert evidence['write_after_cleanup']['written']
        assert evidence['scope_after_cleanup']['state']=='closing'
        assert evidence['finish_after_death']['files'] == []
    finally:
        commands.put('stop');assert done.wait(10)
        for _ in range(200):
            if not business_tasks.is_active(task):break
            time.sleep(.01)
        evidence['writer_stopped']=not business_tasks.is_active(task);evidence['exit_errors']=errors
        save('background',evidence)
    assert not business_tasks.is_active(task)

    assert not errors
    outcome = finish_run(run_id)
    assert outcome['status'] == 'complete', outcome
    assert any(row['status']=='completed' for row in outcome['files'])
    assert store.scope(scope)['state'] == 'complete'
    assert not path.exists()


def test_shared_threads_and_exact_identity_do_not_hold_shard_guard(tmp_path):
    from management_common.lifetime_execution_lease import execution_lease, _registry
    namespace, identity = 'thread-test', 'first'
    shard, _ = _registry(namespace, identity)
    other = next(str(i) for i in range(10000)
                 if _registry(namespace, str(i))[0] == shard)
    ready, finish = Event(), Event()
    errors = []
    def reader():
        try:
            with execution_lease(identity, namespace=namespace, shared=True) as acquired:
                assert acquired
                ready.set()
                assert finish.wait(10)
        except BaseException as error:
            errors.append(error)
    with execution_lease(identity, namespace=namespace, shared=True):
        thread = Thread(target=reader)
        thread.start()
        try:
            assert ready.wait(5), errors
            assert len(list(shard.glob('*.lease'))) == 2
            with execution_lease(identity, namespace=namespace, blocking=False) as acquired:
                assert not acquired
            # Same coordination shard, unrelated artifact: no lifetime conflict.
            with execution_lease(other, namespace=namespace, blocking=False) as acquired:
                assert acquired
        finally:
            finish.set()
            thread.join(5)
        assert not thread.is_alive() and not errors
    assert not list(shard.glob('*.lease'))
    with execution_lease(identity, namespace=namespace, blocking=False) as acquired:
        assert acquired
        with execution_lease(identity, namespace=namespace, shared=True, blocking=False) as reader:
            assert not reader


def test_control_inodes_are_stable_and_bounded(tmp_path):
    from management_common.lifetime_execution_lease import execution_lease, _registry
    namespace = 'bounded-test'
    shard, _ = _registry(namespace, 'first')
    with execution_lease('first', namespace=namespace):
        control = shard.with_suffix('.lock').stat()
    for i in range(160):
        with execution_lease(str(i), namespace=namespace, shared=True):
            pass
    with execution_lease('first', namespace=namespace):
        after = shard.with_suffix('.lock').stat()
        assert (control.st_dev, control.st_ino) == (after.st_dev, after.st_ino)
    assert len(list(shard.parent.glob('*.lock'))) <= 64
    assert len(list(shard.parent.iterdir())) <= 128
    assert not list(shard.parent.glob('*/*.lease'))


def test_holder_release_and_readback_never_unlink_with_open_probes(tmp_path, monkeypatch):
    """Exercise the Windows branch via its CRT abstraction, not native Windows."""
    from management_common import file_lock
    from management_common.lifetime_execution_lease import execution_lease, _registry
    if os.name == 'nt':
        pytest.skip('CRT emulation runs on POSIX; owner tests exercise the native platform')
    import fcntl
    descriptors = set()
    crt = SimpleNamespace(LK_NBLCK=2, LK_UNLCK=0)
    def locking(fd, mode, size):
        descriptors.add(fd)
        assert size == 1 and os.lseek(fd, 0, os.SEEK_CUR) == 0
        try:
            fcntl.flock(fd, fcntl.LOCK_UN if mode == crt.LK_UNLCK else fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise OSError(errno.EACCES, 'Windows CRT contention') from None
    crt.locking = locking
    monkeypatch.setitem(sys.modules, 'msvcrt', crt)
    monkeypatch.setattr(file_lock, 'os', SimpleNamespace(**{**vars(os), 'name':'nt'}))
    unlink = Path.unlink
    def closed_before_unlink(path, *args, **kwargs):
        target = path.stat()
        for fd in descriptors:
            try:
                info = os.fstat(fd)
            except OSError:
                continue
            assert (info.st_dev, info.st_ino) != (target.st_dev, target.st_ino)
        return unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'unlink', closed_before_unlink)
    namespace, identity = 'windows-test', 'artifact'
    shard, digest = _registry(namespace, identity)
    stale = shard / f'{digest}.r.{"a"*32}.lease'
    stale.touch()  # A crashed registration, never locked or already closed.
    with execution_lease(identity, namespace=namespace, shared=True):
        assert not stale.exists()
        with execution_lease(identity, namespace=namespace, shared=True):
            with execution_lease(identity, namespace=namespace, blocking=False) as acquired:
                assert not acquired
    assert not list(shard.glob('*.lease'))


def test_unknown_holder_lock_error_protects_and_reports(tmp_path, monkeypatch):
    from management_common import file_lock
    from management_common.lifetime_execution_lease import execution_lease, _registry
    namespace, identity = 'error-test', 'artifact'
    shard, _ = _registry(namespace, identity)
    with execution_lease(identity, namespace=namespace, shared=True):
        holder = next(shard.glob('*.lease')).stat()
        original = file_lock.try_lock_descriptor
        def unreadable(fd):
            info = os.fstat(fd)
            if (info.st_dev, info.st_ino) == (holder.st_dev, holder.st_ino):
                raise OSError(errno.EIO, 'unknown filesystem lock failure')
            return original(fd)
        with monkeypatch.context() as patch:
            patch.setattr(file_lock, 'try_lock_descriptor', unreadable)
            with pytest.raises(OSError, match='unknown filesystem'):
                with execution_lease(identity, namespace=namespace, blocking=False):
                    pytest.fail('unknown locking error admitted cleanup')
        assert len(list(shard.glob('*.lease'))) == 1
    assert not list(shard.glob('*.lease'))


def test_copied_context_has_own_holder_after_parent_exit(tmp_path):
    from contextvars import copy_context
    from management_common.resource_lifecycle import LifecycleStore, lifecycle_scope
    store = LifecycleStore()
    scope = store.open(str(tmp_path), actor='alice', run_id='copy', case_id='copy')
    ready, finish = Event(), Event()
    errors = []
    def child():
        try:
            with lifecycle_scope():
                ready.set()
                assert finish.wait(10)
        except BaseException as error:
            errors.append(error)
    with lifecycle_scope(scope, actor='alice'):
        context = copy_context()
        thread = Thread(target=context.run, args=(child,))
        thread.start()
        assert ready.wait(5), errors
    try:
        with pytest.raises(TimeoutError, match='active requests'):
            store.seal(scope, timeout=0)
    finally:
        finish.set()
        thread.join(5)
    assert not thread.is_alive() and not errors
    store.seal(scope, timeout=0)
    with pytest.raises(ValueError, match='closed'):
        with store.owner_lease(scope):
            pytest.fail('sealed scope accepted an owner')
    with pytest.raises(ValueError, match='closed'):
        with lifecycle_scope(scope, actor='alice'):
            pytest.fail('sealed scope accepted a request')


def test_owner_and_scope_share_state_and_permission_checks(tmp_path):
    from management_common.resource_lifecycle import LifecycleStore, lifecycle_scope
    store = LifecycleStore()
    scope = store.open(str(tmp_path), actor='alice', run_id='owner', case_id='owner')
    with pytest.raises(PermissionError):
        with lifecycle_scope(scope, actor='foreign'):
            pytest.fail('foreign actor admitted')
    with store.owner_lease(scope):
        kill_exact_shared_backend(store.db, store.lock_key(scope))
        with pytest.raises(TimeoutError, match='active requests'):
            store.seal(scope, timeout=0)
        store.request_close(scope)
        with pytest.raises(ValueError, match='closed'):
            with store.owner_lease(scope):
                pytest.fail('closing scope admitted owner')
        with pytest.raises(ValueError, match='closed'):
            with lifecycle_scope(scope, actor='alice'):
                pytest.fail('closing scope admitted request')
    store.seal(scope, timeout=0)
    store.complete(scope)
    with pytest.raises(ValueError):
        store.open(str(tmp_path), actor='alice', run_id='owner', case_id='owner')


@contextmanager
def child_writer(kind, path, scope=''):
    script = '''
import os, sys
from pathlib import Path
from management_common.file_retention import cache_file
from management_common.resource_lifecycle import lifecycle_scope
from management_common.directory_retention import RetentionStore
kind, path, scope = sys.argv[1:]
path = Path(path)
context = cache_file(path, group='article') if kind == 'cache' else lifecycle_scope(scope, actor='audit')
with context:
    if kind != 'cache':
        RetentionStore().declare(path, category='background', owner_id='child')
    path.mkdir(exist_ok=True)
    while True:
        with (path/'artifact').open('w') as stream:
            stream.write('actual process still writing')
            stream.flush()
            os.fsync(stream.fileno())
        print('fsynced', flush=True)
        if not sys.stdin.readline():
            break
'''
    child = subprocess.Popen([sys.executable, '-u', '-c', script, kind, str(path), scope],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True)
    lines = Queue()
    reader = Thread(target=lambda: [lines.put(line.strip()) for line in child.stdout], daemon=True)
    reader.start()
    try:
        assert lines.get(timeout=10) == 'fsynced'
        yield child, lines
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=10)
        reader.join(5)
        child.stdin.close()
        child.stdout.close()
        errors = child.stderr.read()
        child.stderr.close()
        assert not errors, errors


@pytest.mark.parametrize('kind', ['cache', 'lifecycle'])
def test_actual_process_death_releases_owner_after_pg_loss(tmp_path, kind):
    from management_common.file_retention import cache_file
    from management_common.directory_retention import RetentionStore
    from management_common.directory_paths import cleanup_location
    from management_common.resource_lifecycle import LifecycleStore, lifecycle_scope
    from management_portal.test_framework.run_cleanup import finish_run
    from management_common.instance_manifest import instance_runtime_root
    retention = RetentionStore()
    lifecycle = LifecycleStore()
    path = tmp_path/'child-artifact'
    run_id = 'testrun_0123456789abcdef'
    scope = ''
    if kind == 'cache':
        with cache_file(path, group='article'):
            path.mkdir()
            (path/'artifact').write_bytes(b'previous cache')
        database = retention.database
        key = int.from_bytes(hashlib.sha256((database.schema+':cache:'+cleanup_location(path.absolute())).encode()).digest()[:8], 'big', signed=True)
    else:
        scope = lifecycle.open(str(tmp_path), actor='audit', run_id=run_id, case_id='death')
        database = lifecycle.db
        key = lifecycle.lock_key(scope)
    with child_writer(kind, path, scope) as (child, lines):
        # A second process may enter concurrently; no single exclusive reader.
        with cache_file(path, group='article') if kind == 'cache' else lifecycle_scope(scope, actor='audit'):
            assert child.poll() is None
        kill_exact_shared_backend(database, key)
        child.stdin.write('write after PG death\n')
        child.stdin.flush()
        assert lines.get(timeout=10) == 'fsynced'
        assert child.poll() is None
        if kind == 'cache':
            assert retention.sweep(cache_capacities={'article':0}) == {'queued':0}
            assert retention.queue.run_pending() == []
        else:
            result = finish_run(run_id)
            assert result['status'] == 'error' and 'active requests' in str(result['errors'])
        assert path.exists()
        child.stdin.write('write after protected cleanup\n')
        child.stdin.flush()
        assert lines.get(timeout=10) == 'fsynced'
        child.kill()
        child.wait(timeout=10)
    if kind == 'cache':
        assert retention.sweep(cache_capacities={'article':0}) == {'queued':1}
        assert retention.queue.run_pending()[0]['status'] == 'completed'
    else:
        result = finish_run(run_id)
        assert result['status'] == 'complete', result
        assert lifecycle.scope(scope)['state'] == 'complete'
    assert not path.exists()
    assert not list((instance_runtime_root()/'execution-leases').glob('*/*/*.lease'))


def test_registry_namespaces_isolate_schemas_and_instances(tmp_path, monkeypatch):
    from management_common.lifetime_execution_lease import execution_lease
    from tests.cases.instance_test_support import write_instance
    with execution_lease('same-object', namespace='schema-one'):
        with execution_lease('same-object', namespace='schema-two', blocking=False) as acquired:
            assert acquired
        other = write_instance(tmp_path/'other-instance')
        with monkeypatch.context() as patch:
            patch.setenv('PORTAL_INSTANCE_ROOT', str(other))
            with execution_lease('same-object', namespace='schema-one', blocking=False) as acquired:
                assert acquired
        with execution_lease('same-object', namespace='schema-one', blocking=False) as acquired:
            assert not acquired


def test_blocking_admission_waits_without_holding_registry_guard(tmp_path):
    from management_common.lifetime_execution_lease import execution_lease
    namespace, identity = 'blocking-test', 'artifact'
    started, admitted = Event(), Event()
    errors = []
    def reader():
        try:
            started.set()
            with execution_lease(identity, namespace=namespace, shared=True):
                admitted.set()
        except BaseException as error:
            errors.append(error)
    with execution_lease(identity, namespace=namespace):
        thread = Thread(target=reader)
        thread.start()
        assert started.wait(5)
        assert not admitted.wait(.1)
    thread.join(5)
    assert admitted.is_set() and not thread.is_alive() and not errors
