"""One isolated native-writer probe; product death/cleanup functions are real."""
import ctypes
import json
import multiprocessing
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time
import sys
import pytest

from management_common.portal_database import portal_database
from management_common.process_observation import process_is_alive
from management_common.process_runtime import process_alive, process_tree_alive, terminate_process_tree
from management_portal.resources.workspace_creation_store import WorkspaceCreationStore
from management_portal.society_service import lineage_handoffs, lineage_handoff_preparation
from tests.cases.test_handoff_directory_creation import draft
from tests.cases.test_creation_writer_ownership import _creation_key
from tests.cases.test_lineage_backend import lineage_store

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="actual native Linux ownership probe")

NATIVE = r'''
#include <sys/types.h>
#include <sys/wait.h>
#include <sys/stat.h>
#include <unistd.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <limits.h>
int main(int argc, char **argv) {
 const char *real = getenv("PROBE_REAL_GIT");
 if (argc < 8 || strcmp(argv[3],"worktree") || strcmp(argv[4],"add")) {
   execv(real,argv); return 101;
 }
 if (getpgrp() != getpid() && setsid() < 0) return 102;
 const char *stage = argv[6];
 FILE *identity=fopen(getenv("PROBE_ID"),"w");
 fprintf(identity,"%d %d %d\n%s\n",getpid(),getpgrp(),getppid(),stage); fclose(identity);
 pid_t git=fork();
 if (git==0) { execv(real,argv); _exit(103); }
 int status; if (git<0 || waitpid(git,&status,0)<0 || !WIFEXITED(status) || WEXITSTATUS(status)) return 104;
 char path[PATH_MAX]; snprintf(path,sizeof(path),"%s/native-payload",stage);
 int fd=open(path,O_CREAT|O_WRONLY|O_APPEND,0600); if(fd<0) return 105;
 FILE *log=fopen(getenv("PROBE_BEATS"),"a"); if(!log) return 106;
 for(int n=1;;n++) {
   int written=dprintf(fd,"native-write-%d\n",n); int synced=fsync(fd);
   struct stat st; if(fstat(fd,&st)) return 107;
   fprintf(log,"%d %d %d %lu %ld %d\n",n,written,synced,(unsigned long)st.st_nlink,(long)st.st_size,access(stage,F_OK)==0);
   fflush(log); fsync(fileno(log)); usleep(20000);
 }
}
'''


def wait_for(fn, seconds=20):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        value=fn()
        if value:
            return value
        time.sleep(.02)
    raise AssertionError('timed out waiting for native probe evidence')


def test_git_native_writer_protects_abort_and_rebuild(lineage_store, tmp_path):
    db=portal_database(schema='directory_lifecycle')
    with db.read() as conn:
        data=Path(conn.execute('SHOW data_directory').fetchone()[0]).resolve()
        namespace=conn.execute('SELECT current_schema()').fetchone()[0]
    cluster=data.parent
    assert cluster.parent==Path('/tmp') and cluster.name.startswith('tfi-portal-pytest-pg-')
    assert int((cluster/'owner.pid').read_text())==os.getpid()
    assert data.stat().st_uid==os.getuid()
    assert os.environ['PORTAL_DATABASE_NAMESPACE'].startswith('pytest_'+str(os.getpid())+'_')
    proof={'db':{'data_directory':str(data),'owner_pid':os.getpid(),'uid':os.getuid(),'namespace':namespace},'tmp_path':str(tmp_path)}
    # Become the reaper only for our own orphaned native child.
    libc=ctypes.CDLL(None, use_errno=True)
    previous=ctypes.c_int()
    assert libc.prctl(37,ctypes.byref(previous),0,0,0)==0
    assert libc.prctl(36,1,0,0,0)==0
    native_pid=None
    child=None
    idfile=tmp_path/'native-id'
    beats=tmp_path/'native-beats'
    def read_identity():
        if not idfile.exists(): return None
        lines=idfile.read_text().splitlines()
        return lines if len(lines)==2 and len(lines[0].split())==3 else None
    def heartbeat():
        if not beats.exists(): return None
        rows=[line.split() for line in beats.read_text().splitlines() if len(line.split())==6]
        return list(map(int,rows[-1])) if rows else None
    try:
        wrapper=tmp_path/'bin'; wrapper.mkdir()
        cfile=tmp_path/'native.c'; cfile.write_text(NATIVE)
        subprocess.run(['cc','-O2','-Wall','-o',str(wrapper/'git'),str(cfile)],check=True,capture_output=True)
        realgit=shutil.which('git'); assert realgit
        lid,handoff,target,source=draft(lineage_store,tmp_path,method='git_worktree')
        def create():
            os.environ.update(PATH=str(wrapper)+os.pathsep+os.environ['PATH'],PROBE_REAL_GIT=realgit,
                              PROBE_ID=str(idfile),PROBE_BEATS=str(beats))
            lineage_handoffs.execute_handoff(lid,handoff['id'],'sample_admin')
        child=multiprocessing.get_context('fork').Process(target=create)
        child.start()
        lines=wait_for(read_identity)
        native_pid,group,ppid=map(int,lines[0].split())
        stage=Path(lines[1])
        assert native_pid==group and ppid==child.pid and stage.is_relative_to(tmp_path)
        initial=wait_for(heartbeat)
        assert initial[1]>0 and initial[2]==0 and initial[3]==1 and initial[5]==1
        assert child.is_alive() and process_alive(native_pid)
        with db.read() as conn:
            before=dict(conn.execute('SELECT * FROM resource_creations WHERE actor=%s',(lid,)).fetchone())
        current=lineage_handoffs.get_handoff(lid,handoff['id'],'sample_admin')['handoff']
        operation=lineage_handoff_preparation.operation_id(current,current['items'][0])
        assert before['operation_id']==_creation_key(lid,operation)
        writer=before['artifacts'][0]['writer']
        assert writer['process']['pid']==child.pid and writer['ended'] is False
        assert not before['completed'] and not before['artifacts'][0]['ready']
        assert '.git' in [p.name for p in stage.iterdir()]
        proof.update(parent_pid=child.pid,native_pid=native_pid,native_pgid=group,stage=str(stage),
                     operation=operation,creation_before=before,initial_heartbeat=initial,
                     parent_wchan=Path(f'/proc/{child.pid}/wchan').read_text(),
                     native_exe=os.readlink(f'/proc/{native_pid}/exe'))
        # Kill exactly the owning Python PID, not its process group.
        child.kill(); child.join(10)
        assert child.exitcode==-signal.SIGKILL
        assert not process_is_alive(writer['process'])
        after_death=wait_for(lambda: (h if (h:=heartbeat()) and h[0]>initial[0] else None))
        assert process_alive(native_pid) and after_death[3]==1 and after_death[5]==1
        assert after_death[1]>0 and after_death[2]==0
        proof.update(parent_exitcode=child.exitcode,heartbeat_after_parent_death=after_death)
        # Real domain abort, with normal DB ownership and real Git cleanup.
        with pytest.raises(RuntimeError, match='ownership is active or unknown'):
            WorkspaceCreationStore().abort(actor=lid,operation_ids=[operation])
        from management_portal.resources.creation import Creation
        retry = Creation(before['resource_id'], actor=lid, payload=before['payload'],
                         operation_id='workspace:' + operation)
        with pytest.raises(RuntimeError, match='ownership is active or unknown'):
            with retry.prepare([]):
                pytest.fail('native writer admitted rebuild')
        after_abort=wait_for(lambda: (h if (h:=heartbeat()) and h[0]>after_death[0] else None))
        assert after_abort[1]>0 and after_abort[2]==0 and after_abort[3]==1 and after_abort[5]==1
        assert process_alive(native_pid) and process_tree_alive(native_pid) and stage.exists()
        with db.read() as conn:
            protected=dict(conn.execute('SELECT * FROM resource_creations WHERE actor=%s',(lid,)).fetchone())
        assert not protected['aborted'] and not protected['cleanup_complete']
        assert protected['artifacts'][0]['writer'] == writer
        terminate_process_tree(native_pid, timeout=5, force=True)
        os.waitpid(native_pid, 0)
        native_pid = None
        WorkspaceCreationStore().abort(actor=lid,operation_ids=[operation])
        assert not stage.exists()
        with db.read() as conn:
            after=dict(conn.execute('SELECT * FROM resource_creations WHERE actor=%s',(lid,)).fetchone())
        assert after['aborted'] and after['cleanup_complete']
        proof.update(creation_protected=protected, creation_after=after,
                     heartbeat_after_refused_abort=after_abort, protected=True)
        print('NATIVE_CHILD_RESULT',json.dumps(proof,default=str),flush=True)
    finally:
        if child is not None and child.is_alive():
            child.kill(); child.join(10)
        if native_pid is None and read_identity() and process_alive(int(read_identity()[0].split()[0])):
            native_pid=int(read_identity()[0].split()[0])
        if native_pid is not None:
            assert os.getpgid(native_pid)==native_pid
            terminate_process_tree(native_pid,timeout=5,force=True)
            reaped,status=os.waitpid(native_pid,0)
            proof['native_cleanup']={'reaped_pid':reaped,'wait_status':status,'tree_alive':process_tree_alive(native_pid)}
            assert not proof['native_cleanup']['tree_alive']
        if beats.exists():
            (tmp_path/'evidence-native-beats.txt').write_bytes(beats.read_bytes())
        if idfile.exists():
            (tmp_path/'evidence-native-identity.txt').write_bytes(idfile.read_bytes())
        assert libc.prctl(36,previous.value,0,0,0)==0
        (tmp_path/'evidence-result.json').write_text(json.dumps(proof,indent=2,default=str)+'\n')


WRITER = r'''
#include <sys/types.h>
#include <sys/stat.h>
#include <unistd.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
int main(int argc, char **argv) {
 if (argc != 4) return 101;
 if (atoi(argv[3])) {
   for (int fd=3; fd<4096; fd++) close(fd);
   pid_t child=fork();
   if (child < 0) return 102;
   if (child) return 0;
 }
 char path[4096]; snprintf(path,sizeof(path),"%s/payload",argv[1]);
 int fd=open(path,O_CREAT|O_WRONLY|O_APPEND,0600); if(fd<0) return 103;
 FILE *beats=fopen(argv[2],"a"); if(!beats) return 104;
 for(int n=1;;n++) {
   if(dprintf(fd,"write-%d\n",n)<0 || fsync(fd)) return 105;
   struct stat st; if(fstat(fd,&st)) return 106;
   fprintf(beats,"%d %d %d %lu\n",getpid(),getpgrp(),n,(unsigned long)st.st_nlink);
   fflush(beats); fsync(fileno(beats)); usleep(20000);
 }
}
'''


@pytest.fixture
def native_writer(tmp_path):
    source = tmp_path/'writer.c'
    source.write_text(WRITER)
    binary = tmp_path/'writer'
    subprocess.run(['cc', '-Wall', '-O2', '-o', str(binary), str(source)], check=True, capture_output=True)
    return binary


@pytest.mark.parametrize('kind', ['cache', 'temporary', 'lifecycle'])
@pytest.mark.parametrize('close_and_fork', [False, True])
def test_native_context_survives_parent_death(tmp_path, native_writer, kind, close_and_fork):
    from management_common.directory_retention import RetentionStore
    from management_common.file_retention import cache_file, recover_temporary_directories
    from management_common.cache_retention_lock import cache_lease
    from management_common.resource_lifecycle import LifecycleStore
    from management_common.instance_manifest import instance_runtime_root
    path = tmp_path/'artifact'
    beats = tmp_path/'beats'
    ready = tmp_path/'ready'
    lifecycle = LifecycleStore()
    scope = lifecycle.open(str(tmp_path), actor='native-test', run_id='native-run', case_id=kind)
    script = '''
import os, sys, time
from pathlib import Path
from management_common.file_retention import cache_file, temporary_directory
from management_common.resource_lifecycle import lifecycle_scope
from management_common.directory_retention import RetentionStore
from management_common.process_runtime import spawn_process
kind, target, ready, beats, native, mode, scope = sys.argv[1:]
context = (cache_file(Path(target), group='article') if kind == 'cache' else
           temporary_directory(prefix='native-', dir=Path(target).parent) if kind == 'temporary' else
           lifecycle_scope(scope, actor='native-test'))
with context as temporary:
    path = Path(temporary) if kind == 'temporary' else Path(target)
    path.mkdir(exist_ok=True)
    if kind == 'lifecycle':
        RetentionStore().declare(path, category='background', owner_id='native-test')
    child = spawn_process([native, str(path), beats, mode])
    Path(ready).write_text(str(path))
    # spawn_process's caller is deliberately killed before tree cleanup.
    while True: time.sleep(.05)
'''
    libc=ctypes.CDLL(None, use_errno=True)
    previous=ctypes.c_int()
    assert libc.prctl(37,ctypes.byref(previous),0,0,0)==0
    assert libc.prctl(36,1,0,0,0)==0
    child = subprocess.Popen([sys.executable, '-c', script, kind, str(path), str(ready),
                              str(beats), str(native_writer), str(int(close_and_fork)), scope],
                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    group = None
    def heartbeat():
        if beats.exists():
            lines = [line.split() for line in beats.read_text().splitlines() if len(line.split()) == 4]
            if lines:
                return list(map(int, lines[-1]))
    try:
        initial = wait_for(heartbeat)
        pid, group, sequence, links = initial
        path = Path(wait_for(lambda: ready.read_text() if ready.exists() else None))
        assert links == 1 and path.exists()
        child.kill()
        child.wait(timeout=10)
        after = wait_for(lambda: h if (h := heartbeat()) and h[2] > sequence else None)
        assert process_alive(pid) and after[3] == 1
        if kind == 'cache':
            with cache_lease(path, blocking=False) as acquired:
                assert not acquired
            store = RetentionStore()
            assert store.sweep(cache_capacities={'article': 0}) == {'queued': 0}
        elif kind == 'temporary':
            report = recover_temporary_directories()
            assert not report['errors'] and not report['cleanup']
            assert str(path) in report['active']
        else:
            with pytest.raises(TimeoutError, match='active requests'):
                lifecycle.seal(scope, timeout=0)
        assert path.exists()
        retained = wait_for(lambda: h if (h := heartbeat()) and h[2] > after[2] else None)
        assert retained[3] == 1
        terminate_process_tree(group, timeout=5, force=True)
        while True:
            try:
                os.waitpid(-group, 0)
            except ChildProcessError:
                break
        group = None
        if kind == 'cache':
            with cache_lease(path, blocking=False) as acquired:
                assert acquired
            # The interrupted builder did not publish size; a normal owner
            # reacquires the same cache key before capacity-driven reclamation.
            with cache_file(path, group='article'):
                pass
            assert store.sweep(cache_capacities={'article': 0}) == {'queued': 1}
            assert store.queue.run_pending()[0]['status'] == 'completed'
        elif kind == 'temporary':
            report = recover_temporary_directories()
            assert not report['errors'] and report['cleanup']
            assert not path.exists()
        else:
            lifecycle.seal(scope, timeout=0)
        assert not list((instance_runtime_root()/'execution-leases').glob('*/*/*.lease'))
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=10)
        error = child.stderr.read()
        child.stderr.close()
        if group is not None:
            terminate_process_tree(group, timeout=5, force=True)
            while True:
                try:
                    os.waitpid(-group, 0)
                except ChildProcessError:
                    break
        assert libc.prctl(36,previous.value,0,0,0)==0
        assert not error, error


@pytest.mark.parametrize('outcome', ['normal', 'timeout', 'spawn-failure', 'registration-failure'])
def test_native_exit_reclaims_holders(tmp_path, monkeypatch, outcome):
    from management_common import process_runtime as runtime, _process_posix as posix
    from management_common.lifetime_execution_lease import execution_lease, _registry
    identity, namespace = 'exit', 'native-exit'
    shard, _ = _registry(namespace, identity)
    with execution_lease(identity, namespace=namespace):
        if outcome == 'normal':
            for _ in range(8):
                assert runtime.run_process([sys.executable, '-c', "print('ok')"],
                                           capture_output=True, text=True, check=True).stdout == 'ok\n'
                assert len(list(shard.glob('*.lease'))) == 1
        elif outcome == 'timeout':
            with pytest.raises(subprocess.TimeoutExpired):
                runtime.run_process([sys.executable, '-c', 'import time; time.sleep(30)'], timeout=.05)
        elif outcome == 'spawn-failure':
            with pytest.raises(FileNotFoundError):
                runtime.run_process([str(tmp_path/'no-executable')])
        else:
            def fail(pid):
                raise OSError('registration failed')
            monkeypatch.setattr(posix, 'lifetime_receipt', fail)
            with pytest.raises(OSError, match='registration failed'):
                runtime.run_process([sys.executable, '-c', 'raise AssertionError("must never execute")'])
        assert len(list(shard.glob('*.lease'))) == 1
    assert not list(shard.glob('*.lease'))


def test_normal_context_exit_keeps_native_holder(tmp_path, native_writer):
    from management_common.lifetime_execution_lease import execution_lease, _registry
    from management_common import process_runtime as runtime
    identity, namespace = 'native-after-context', 'normal-parent'
    shard, _ = _registry(namespace, identity)
    with execution_lease(identity, namespace=namespace):
        child = runtime.spawn_process([str(native_writer), str(tmp_path), str(tmp_path/'beats'), '0'])
    try:
        assert len(list(shard.glob('*.lease'))) == 1
        with execution_lease(identity, namespace=namespace, blocking=False) as acquired:
            assert not acquired
    finally:
        runtime.terminate_process_tree(child, force=True)
    assert not list(shard.glob('*.lease'))


@pytest.mark.parametrize('registered', [False, True])
def test_owner_dies_at_launch_gate_never_runs_command(tmp_path, native_writer, registered):
    from management_common.lifetime_execution_lease import execution_lease, _registry
    ready = tmp_path/'gate-ready'
    beats = tmp_path/'beats'
    script = '''
import sys,time
from pathlib import Path
from management_common import _process_posix as posix, process_runtime as runtime
from management_common.lifetime_execution_lease import execution_lease
ready, native, path, beats, registered = sys.argv[1:]
def before_registration(pid):
    Path(ready).write_text(str(pid))
    while True: time.sleep(.02)
if registered == 'False':
    posix.lifetime_receipt = before_registration
else:
    spawn = posix.spawn_process
    def after_registration(*args, lifetime_registered, **kwargs):
        def record(receipt):
            lifetime_registered(receipt)
            before_registration(receipt['pgid'])
        return spawn(*args, lifetime_registered=record, **kwargs)
    posix.spawn_process = after_registration
with execution_lease('gate', namespace='native-gap'):
    runtime.spawn_process([native, path, beats, '0'])
'''
    libc=ctypes.CDLL(None, use_errno=True)
    previous=ctypes.c_int()
    assert libc.prctl(37,ctypes.byref(previous),0,0,0)==0
    assert libc.prctl(36,1,0,0,0)==0
    child = subprocess.Popen([sys.executable, '-c', script, str(ready), str(native_writer), str(tmp_path), str(beats), str(registered)])
    pid = None
    try:
        pid = int(wait_for(lambda: ready.read_text() if ready.exists() else None))
        assert process_alive(pid) and not beats.exists()
        child.kill()
        child.wait(timeout=10)
        wait_for(lambda: not process_alive(pid))
        os.waitpid(pid, 0)
        pid = None
        assert not beats.exists() and not (tmp_path/'payload').exists()
        with execution_lease('gate', namespace='native-gap', blocking=False) as acquired:
            assert acquired
        shard, _ = _registry('native-gap', 'gate')
        assert not list(shard.glob('*.lease'))
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=10)
        if pid is not None:
            terminate_process_tree(pid, force=True)
            os.waitpid(pid, 0)
        assert libc.prctl(36,previous.value,0,0,0)==0


def test_copied_context_launch_is_pinned_across_thread_exit(tmp_path, native_writer):
    from contextvars import copy_context
    from threading import Thread, Event
    from management_common import process_runtime as runtime
    from management_common.lifetime_execution_lease import execution_lease, _registry
    ready, release = Event(), Event()
    children, errors = [], []
    def spawn():
        try:
            children.append(runtime.spawn_process([str(native_writer), str(tmp_path), str(tmp_path/'beats'), '0']))
            ready.set()
            assert release.wait(10)
        except BaseException as error:
            errors.append(error)
    with execution_lease('thread', namespace='native-thread'):
        context = copy_context()
        worker = Thread(target=context.run, args=(spawn,))
        worker.start()
        assert ready.wait(10), errors
    try:
        with execution_lease('thread', namespace='native-thread', blocking=False) as acquired:
            assert not acquired
    finally:
        release.set()
        worker.join(10)
        for child in children:
            runtime.terminate_process_tree(child, force=True)
    assert not errors and not worker.is_alive()
    shard, _ = _registry('native-thread', 'thread')
    assert not list(shard.glob('*.lease'))
    with pytest.raises(RuntimeError, match='already ended'):
        context.run(runtime.spawn_process, ['must-not-spawn'])


def test_forked_context_cannot_release_parent_lease():
    from management_common.lifetime_execution_lease import execution_lease, _registry
    with execution_lease('fork', namespace='native-fork'):
        pid = os.fork()
        if pid == 0:
            # Inherited locks remain held until this child exits. os._exit
            # skips Python cleanup, exactly like an abruptly ended context.
            os._exit(0)
        os.waitpid(pid, 0)
        with execution_lease('fork', namespace='native-fork', blocking=False) as acquired:
            assert not acquired
    shard, _ = _registry('native-fork', 'fork')
    assert not list(shard.glob('*.lease'))


@pytest.mark.parametrize('observation', ['live', 'unknown', 'ended', 'foreign-session'])
def test_windows_job_receipt_controls_unlocked_holder(monkeypatch, observation):
    from management_common import _process_windows as windows, process_runtime as runtime
    from management_common.lifetime_execution_lease import execution_lease, inherit_execution_leases, _registry
    from tests.cases.test_process_runtime import FakeJob
    monkeypatch.setattr(windows, '_session_id', lambda: 4)
    job = FakeJob([0 if observation == 'ended' else 1])
    def open_job(identity):
        assert identity == 'unique-before-spawn'
        if observation == 'unknown':
            raise PermissionError('Job observation unavailable')
        return job
    monkeypatch.setattr(windows.Job, 'open', open_job)
    monkeypatch.setattr(runtime, 'native_lifetime_alive', windows.lifetime_alive)
    shard, _ = _registry('job-test', 'job')
    with execution_lease('job', namespace='job-test'):
        with inherit_execution_leases() as (_, record, paths):
            receipt = windows.lifetime_receipt('unique-before-spawn')
            if observation == 'foreign-session':
                receipt['session'] = 99
            record(receipt)
            if observation == 'unknown':
                # Unlocking later must report uncertainty; restore observation
                # for orderly context cleanup, then exercise error readback.
                monkeypatch.setattr(windows.Job, 'open', lambda _: job)
    if observation == 'unknown':
        monkeypatch.setattr(windows.Job, 'open', open_job)
        with pytest.raises(PermissionError, match='unavailable'):
            with execution_lease('job', namespace='job-test', blocking=False):
                pytest.fail('unknown native state admitted cleanup')
        monkeypatch.setattr(windows.Job, 'open', lambda _: job)
    else:
        with execution_lease('job', namespace='job-test', blocking=False) as acquired:
            assert acquired == (observation == 'ended')
    # Explicitly prove the Job absent in the original session for final reclaim.
    monkeypatch.setattr(windows, '_session_id', lambda: receipt['session'])
    monkeypatch.setattr(windows.Job, 'open', lambda _: None)
    with execution_lease('job', namespace='job-test', blocking=False) as acquired:
        assert acquired
    assert not list(shard.glob('*.lease'))


def test_posix_recycled_leader_never_signals_foreign_group(monkeypatch):
    from management_common import _process_posix as posix
    from types import SimpleNamespace
    receipt = posix.lifetime_receipt(os.getpid())
    receipt['start'] = str(int(receipt['start']) - 1)
    process = SimpleNamespace(pid=os.getpid(), _portal_lifetime_receipt=receipt, wait=lambda **_: 0)
    monkeypatch.setattr(posix.os, 'killpg', lambda *_: pytest.fail('recycled group signalled'))
    assert not posix.process_tree_alive(process)
    posix.terminate_process_tree(process, force=True)
    assert process._portal_tree_stopped


def test_posix_hidden_group_is_unknown_not_ended(monkeypatch):
    from management_common import _process_posix as posix
    monkeypatch.setattr(posix.os, 'killpg', lambda *_: None)
    monkeypatch.setattr(posix.Path, 'iterdir', lambda _: iter(()))
    assert posix._group_alive(123456)


def test_windows_receipt_precedes_suspended_launch(monkeypatch):
    from types import SimpleNamespace
    from management_common import process_runtime as runtime, _process_windows as windows
    from management_common.lifetime_execution_lease import execution_lease, _registry
    from tests.cases.test_process_runtime import FakeJob
    monkeypatch.setattr(runtime, 'sys', SimpleNamespace(platform='win32'))
    monkeypatch.setattr(windows, 'sys', SimpleNamespace(platform='win32'))
    monkeypatch.setattr(runtime, '_backend', lambda: windows)
    monkeypatch.setattr(windows, '_session_id', lambda: 4)
    events = []
    job = FakeJob([0])
    shard, _ = _registry('windows-launch', 'native')
    class Process:
        pid = 567
        _handle = 99
    def create(identity, kill_on_owner_exit):
        records = [json.loads(line) for path in shard.glob('*.lease') for line in path.read_text().splitlines()]
        assert len(records) == 1 and records[0]['tree_id'] == identity
        assert records[0]['session'] == 4 and kill_on_owner_exit
        events.append('receipt-before-job')
        return job
    def popen(*args, **kwargs):
        assert kwargs['creationflags'] & windows.CREATE_SUSPENDED
        assert not kwargs.get('pass_fds')
        events.append('suspended')
        return Process()
    monkeypatch.setattr(windows.Job, 'create', create)
    monkeypatch.setattr(windows.Job, 'open', lambda _: job)
    monkeypatch.setattr(windows.subprocess, 'Popen', popen)
    monkeypatch.setattr(windows, '_api', lambda: SimpleNamespace(AssignProcessToJobObject=lambda *_: events.append('assigned') or 1))
    monkeypatch.setattr(windows, '_resume', lambda _: events.append('resumed'))
    with execution_lease('native', namespace='windows-launch'):
        child = runtime.spawn_process(['child'])
        child._portal_job_finalizer()
    assert events == ['receipt-before-job', 'suspended', 'assigned', 'resumed']
    assert not list(shard.glob('*.lease'))


def test_receipt_io_obeys_mandatory_lock_handle_contract(monkeypatch):
    """Emulate mandatory byte-lock I/O: another handle cannot write byte zero."""
    from management_common import file_lock, process_runtime as runtime
    from management_common.lifetime_execution_lease import execution_lease, inherit_execution_leases
    locked = {}
    original_lock, original_open = file_lock.try_lock_descriptor, Path.open
    def lock(fd):
        acquired = original_lock(fd)
        if acquired:
            stat = os.fstat(fd)
            locked[stat.st_dev, stat.st_ino] = fd
        return acquired
    class MandatoryStream:
        def __init__(self, stream):
            self.stream = stream
        def __getattr__(self, key):
            return getattr(self.stream, key)
        def __iter__(self):
            return iter(self.stream)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return self.stream.__exit__(*args)
        def write(self, value):
            stat = os.fstat(self.stream.fileno())
            assert locked.get((stat.st_dev, stat.st_ino)) == self.stream.fileno(), 'cross-handle locked write'
            return self.stream.write(value)
    def open_stream(path, *args, **kwargs):
        stream = original_open(path, *args, **kwargs)
        return MandatoryStream(stream) if path.suffix == '.lease' else stream
    monkeypatch.setattr(file_lock, 'try_lock_descriptor', lock)
    monkeypatch.setattr(Path, 'open', open_stream)
    monkeypatch.setattr(runtime, 'native_lifetime_alive', lambda _: False)
    with execution_lease('mandatory', namespace='native-io'):
        with inherit_execution_leases() as (_, record, _):
            record({'version': 1, 'tree_id': 'planned-job'})


@pytest.mark.parametrize('uncertain', [False, True])
def test_failed_windows_launch_reclaims_only_confirmed_rollback(monkeypatch, uncertain):
    from types import SimpleNamespace
    from management_common import process_runtime as runtime
    from management_common.lifetime_execution_lease import execution_lease, _registry
    monkeypatch.setattr(runtime, 'sys', SimpleNamespace(platform='win32'))
    def spawn(*args, **kwargs):
        if uncertain:
            raise runtime.ProcessCleanupError('rollback unconfirmed')
        raise FileExistsError('existing Job identity')
    backend = SimpleNamespace(
        spawn_process=spawn, lifetime_alive=lambda _: True,
        lifetime_receipt=lambda name: dict(version=1, platform='win32', tree_id=name))
    monkeypatch.setattr(runtime, '_backend', lambda: backend)
    shard, _ = _registry('job-failure', 'native')
    with execution_lease('native', namespace='job-failure'):
        for _ in range(4):
            with pytest.raises(runtime.ProcessCleanupError if uncertain else FileExistsError):
                runtime.spawn_process(['child'], tree_id='already-existing')
        assert len(list(shard.glob('*.lease'))) == (5 if uncertain else 1)
    with execution_lease('native', namespace='job-failure', blocking=False) as acquired:
        assert acquired is not uncertain
    backend.lifetime_alive = lambda _: False
    with execution_lease('native', namespace='job-failure', blocking=False) as acquired:
        assert acquired
    assert not list(shard.glob('*.lease'))
