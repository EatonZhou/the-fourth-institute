"""Recovery observes owned trees, never substitutes a dead root for completion."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from management_common import process_runtime
from management_common import _process_windows as windows
from management_portal.execution import account_login as login
from management_portal.society_service.script_runs import service, worker, runtime, store, events, runner


@pytest.fixture
def recovery(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime, 'RUN_ROOT', tmp_path)
    monkeypatch.setattr(service, 'revoke', lambda _: None)
    monkeypatch.setattr(events, 'publish', lambda _: None)
    monkeypatch.setattr(service.portal_background_jobs, 'job_alive', lambda *args: False)
    def create(*, unit='portal-script-run', payload=None, runner=True, **values):
        identifier = 'run'
        root = tmp_path / identifier
        root.mkdir(exist_ok=True)
        if runner:
            (root / 'runner.json').write_text(json.dumps({'script_run_id': identifier, 'pid': 701}))
        if payload is not None:
            (root / 'payload.json').write_text(json.dumps({'script_run_id': identifier, **payload}))
        row = store.admit({'script_run_id': identifier, 'lineage_id': 'lineage_aaaaaaaaaaaa',
            'caller_id': 'sample_admin', 'started_at': (datetime.now(timezone.utc)-timedelta(seconds=60)).isoformat(),
            'status': 'running', 'notified': False, 'unit': unit, **values})[0]
        owner = worker.Worker()
        owner.lease_until = time.monotonic()+60
        return owner, row, root
    return create


@pytest.mark.parametrize('platform', ['linux', 'darwin', 'win32'])
def test_recovery_does_not_finish_when_descendants_outlive_runner(recovery, monkeypatch, platform):
    monkeypatch.setattr(service, 'sys', SimpleNamespace(platform=platform))
    owner, row, _ = recovery(payload={'state': 'running', 'pid': 702})
    monkeypatch.setattr(process_runtime, 'process_tree_alive',
        lambda pid=None, tree_id=None: tree_id == 'portal-script-run-payload' if platform == 'win32' else pid == 702)
    owner.reconcile('run', row)
    assert store.get('run')['status'] == 'running'
    assert not store.get('run')['notified']


@pytest.mark.parametrize('payload', [None, {'state': 'starting'}, {'state': 'running', 'pid': 'bad'},
                                   {'state': 'stopped', 'script_run_id': 'another'}])
def test_posix_incomplete_or_foreign_receipts_stay_unknown(recovery, monkeypatch, payload):
    monkeypatch.setattr(service, 'sys', SimpleNamespace(platform='darwin'))
    owner, row, _ = recovery(payload=payload)
    monkeypatch.setattr(process_runtime, 'process_tree_alive', lambda *args, **kwargs: False)
    owner.reconcile('run', row)
    assert store.get('run')['status'] == 'running'


@pytest.mark.parametrize('failure', [PermissionError('denied'), RuntimeError('status unavailable')])
def test_unavailable_native_status_never_becomes_terminal(recovery, monkeypatch, failure):
    owner, row, _ = recovery(payload={'state': 'stopped'})
    def unavailable(*args):
        raise failure
    monkeypatch.setattr(service.portal_background_jobs, 'job_alive', unavailable)
    owner.reconcile('run', row)
    assert store.get('run')['status'] == 'running'


def test_restart_recovers_pending_unit_from_original_runner_receipt(recovery, monkeypatch):
    owner, row, _ = recovery(unit='', payload={'state': 'running', 'pid': 702})
    seen = []
    monkeypatch.setattr(service.portal_background_jobs, 'job_alive', lambda *args: seen.append(args) or True)
    monkeypatch.setattr(service, 'launch_job', lambda *args, **kwargs: pytest.fail('restarted original run'))
    owner.reconcile('run', row)
    assert seen == [('portal-script-run', 701)]
    assert store.get('run')['status'] == 'running'


@pytest.mark.parametrize('timeout,status', [(None, 'lost'), (1, 'timed_out')])
def test_confirmed_empty_payload_and_runner_allow_missing_result(recovery, monkeypatch, timeout, status):
    owner, row, _ = recovery(payload={'state': 'running', 'pid': 702}, timeout_seconds=timeout)
    monkeypatch.setattr(process_runtime, 'process_tree_alive', lambda *args, **kwargs: False)
    owner.reconcile('run', row)
    assert store.get('run')['status'] == status


def test_natural_result_wins_race_with_empty_tree_observation(recovery, monkeypatch):
    owner, row, root = recovery(payload={'state': 'stopped'})
    def stopped(_):
        (root/'result.json').write_text(json.dumps({'status': 'succeeded', 'finished_at': service.now()}))
        return 'stopped'
    monkeypatch.setattr(service, 'running_state', stopped)
    owner.reconcile('run', row)
    assert store.get('run')['status'] == 'succeeded'


def test_restart_honors_persisted_cancellation(recovery, monkeypatch):
    owner, row, _ = recovery(unit='', cancel_requested=True)
    stopped = []
    monkeypatch.setattr(service, '_stop_run', lambda record, path: stopped.append(record['script_run_id']) or True)
    owner.reconcile('run', row)
    assert stopped == ['run']
    assert store.get('run')['status'] == 'cancelled'


def test_windows_named_job_state_ignores_recycled_parent_pid(recovery, monkeypatch):
    monkeypatch.setattr(service, 'sys', SimpleNamespace(platform='win32'))
    owner, row, _ = recovery(payload={'state': 'stopped'})
    monkeypatch.setattr(service.portal_background_jobs, 'job_alive', lambda *args: True)
    names = []
    monkeypatch.setattr(process_runtime, 'process_tree_alive', lambda pid, tree_id: names.append(tree_id) or False)
    owner.reconcile('run', row)
    assert names == ['portal-script-run', 'portal-script-run-payload']
    assert store.get('run')['status'] == 'lost'


def test_windows_validation_accepts_supported_runtime_and_rejects_unknown_platform(tmp_path, monkeypatch):
    script = tmp_path/'sample.py'; script.write_text('pass')
    monkeypatch.setattr(service, 'sys', SimpleNamespace(platform='win32'))
    assert service._validate({'path': str(script)})['runtime'] == 'python'
    monkeypatch.setattr(service, 'sys', SimpleNamespace(platform='plan9'))
    with pytest.raises(NotImplementedError):
        service._validate({'path': str(script)})


@pytest.mark.parametrize('active', [False, True])
def test_windows_readonly_tree_query_reopens_and_closes_job(monkeypatch, active):
    closed = []
    job = SimpleNamespace(active=lambda: active, close=lambda: closed.append(True))
    monkeypatch.setattr(windows.Job, 'open', lambda name: job)
    assert windows.process_tree_alive(999, tree_id='stable-run') is active
    assert closed == [True]
    monkeypatch.setattr(windows.Job, 'open', lambda name: None)
    assert not windows.process_tree_alive(999, tree_id='absent-run')


@pytest.mark.skipif(sys.platform not in {'linux', 'darwin'}, reason='POSIX native group observation')
def test_tree_observation_survives_leader_exit(tmp_path):
    pidfile = tmp_path/'child.pid'
    child = "import time,os;from pathlib import Path;Path("+repr(str(pidfile))+").write_text(str(os.getpid()));time.sleep(30)"
    parent = "import subprocess,sys,time;from pathlib import Path;subprocess.Popen([sys.executable,'-c',"+repr(child)+"]);\nwhile not Path("+repr(str(pidfile))+").exists():time.sleep(.01)\n"
    process = process_runtime.spawn_process([sys.executable, '-c', parent])
    try:
        process.wait(timeout=5)
        assert process_runtime.process_tree_alive(process)
    finally:
        process_runtime.terminate_process_tree(process, force=True)
    assert not process_runtime.process_tree_alive(process)


@pytest.mark.parametrize('provider', ['claude', 'codex'])
def test_windows_login_environment_uses_native_bootstrap_only(tmp_path, monkeypatch, provider):
    monkeypatch.setattr(login, 'sys', SimpleNamespace(platform='win32'))
    monkeypatch.setattr(login, 'account_home', lambda *args: tmp_path/'account')
    monkeypatch.setattr(login.portal_network, 'saved_proxy_environment', lambda: {'HTTPS_PROXY':'http://127.0.0.1:1'})
    monkeypatch.setenv('SystemRoot', r'C:\Windows')
    monkeypatch.setenv('COMSPEC', r'C:\Windows\System32\cmd.exe')
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'do-not-copy')
    monkeypatch.setenv('CODEX_HOME', 'another-account')
    env = login.login_environment(provider, 'test')
    assert env['SystemRoot'] == r'C:\Windows'
    assert env['USERPROFILE'] == str(Path.home())
    assert env['BROWSER'] == r'C:\Windows\System32\cmd.exe /d /c exit 0'
    assert '/bin/true' not in env['BROWSER'] and 'ANTHROPIC_API_KEY' not in env
    assert env['CODEX_HOME' if provider == 'codex' else 'CLAUDE_CONFIG_DIR'] == str(tmp_path/'account')
    if provider == 'claude':
        assert 'CODEX_HOME' not in env


def test_windows_failed_creation_rollback_remains_unconfirmed(monkeypatch):
    closed = []
    job = SimpleNamespace(handle=1, terminate=lambda: None, close=lambda: closed.append(True))
    def denied(*args, **kwargs):
        raise PermissionError('denied')
    process = SimpleNamespace(pid=567, _handle=99, kill=denied, wait=lambda **kwargs: None)
    monkeypatch.setattr(windows.Job, 'create', lambda *args: job)
    monkeypatch.setattr(windows.subprocess, 'Popen', lambda *args, **kwargs: process)
    monkeypatch.setattr(windows, '_api', lambda: SimpleNamespace(AssignProcessToJobObject=denied))
    with pytest.raises(process_runtime.ProcessCleanupError, match='rollback is unconfirmed'):
        windows.spawn_process(['never-resumed'])
    assert closed == [True]


@pytest.mark.parametrize('cleanup_unknown', [False, True])
def test_failed_spawn_only_finishes_when_rollback_is_known(tmp_path, monkeypatch, cleanup_unknown):
    from tests.cases.test_script_run_process_runtime import script_fixture
    root, config = script_fixture(tmp_path, 'pass')
    error = process_runtime.ProcessCleanupError('unconfirmed') if cleanup_unknown else FileNotFoundError('missing executable')
    def failed(*args, **kwargs):
        raise error
    monkeypatch.setattr(process_runtime, 'spawn_process', failed)
    if cleanup_unknown:
        with pytest.raises(process_runtime.ProcessCleanupError):
            runner.run(config, root, forward=lambda _: {})
        assert json.loads((root/'payload.json').read_text())['state'] == 'starting'
    else:
        assert runner.run(config, root, forward=lambda _: {})['status'] == 'failed'
        assert json.loads((root/'payload.json').read_text())['state'] == 'stopped'


def test_windows_missing_launch_proof_cannot_finish_or_cancel(recovery, monkeypatch):
    owner, row, root = recovery(payload={'state': 'starting'})
    monkeypatch.setattr(service, 'sys', SimpleNamespace(platform='win32'))
    monkeypatch.setattr(process_runtime, 'process_tree_alive', lambda *args, **kwargs: False)
    monkeypatch.setattr(process_runtime, 'terminate_process_tree', lambda *args, **kwargs: None)
    owner.reconcile('run', row)
    assert store.get('run')['status'] == 'running'
    with pytest.raises(RuntimeError, match='without confirmed payload cleanup'):
        service.cancel('run', 'sample_admin', lineage_id=row['lineage_id'])
    assert store.get('run')['status'] == 'running'


@pytest.mark.parametrize('language', ['python', 'javascript'])
def test_runscript_tcp_relay_and_runtime_clients(tmp_path, monkeypatch, language):
    # Select the Windows transport without pretending Linux can execute Job APIs.
    # The actual runner, subprocess, authenticated relay and client code all run.
    from management_common.local_ipc import LocalListener
    from tests.cases.test_script_runs import execute
    monkeypatch.setattr(runner, 'LocalListener', lambda path: LocalListener(path, transport='tcp'))
    if language == 'python':
        code = '''import functools
import management_common.local_ipc as ipc
ipc.connect_local = functools.partial(ipc.connect_local, transport='tcp')
from portal_script import isa, set_output
set_output(isa('GetPortalStatus'))
'''
    else:
        code = '''import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const ipc=require(process.env.PORTAL_LOCAL_IPC_MODULE), connect=ipc.connectLocal;
ipc.connectLocal=(endpoint, ready, options={})=>connect(endpoint, ready, {...options,tcp:true});
const {isa,setOutput}=require('portal-script');
setOutput(await isa('GetPortalStatus'));
'''
    result, root, calls = execute(tmp_path, language, code)
    assert result['status'] == 'succeeded', result
    assert result['outputs'] == {'caller_id': 'resident_aaaaaaaaaaaa'}
    assert calls == [{'instruction': 'GetPortalStatus', 'arguments': {}}]
    assert json.loads((root/'payload.json').read_text())['state'] == 'stopped'
