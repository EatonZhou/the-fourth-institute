"""RunScript owns complete process trees across cancellation and owner restart.

:design: decision_8b510cbd9c58
:module: platform.core
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from management_common import process_runtime
from management_portal.society_service.script_runs import runner, runtime, service, store


LINEAGE = 'lineage_aaaaaaaaaaaa'


def script_fixture(tmp_path, source, **config):
    script = tmp_path / 'payload.py'
    script.write_text(source)
    root = tmp_path / 'script_run_process_test'
    (root / 'io/artifacts').mkdir(parents=True)
    return root, {'runtime': 'python', 'path': str(script), 'cwd': str(tmp_path),
                  'run_dir': str(root), 'script_run_id': root.name,
                  'process_tree_id': service._payload_tree(root.name), **config}


@pytest.mark.skipif(sys.platform != 'linux', reason='native Linux process-tree verification')
@pytest.mark.parametrize('leader_exit', [True, False])
def test_runner_reaps_descendants_on_exit_or_timeout(tmp_path, leader_exit):
    """A leader that exits or times out cannot leave a child holding output open."""
    pids = tmp_path / 'pids.json'
    root, config = script_fixture(tmp_path, f'''
import json,os,subprocess,sys,time
from pathlib import Path
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
Path({str(pids)!r}).write_text(json.dumps([os.getpid(), child.pid]))
{'pass' if leader_exit else 'time.sleep(60)'}
''', timeout_seconds=1)
    results, errors = [], []
    def run():
        try:
            results.append(runner.run(config, root, forward=lambda _: {}))
        except Exception as error:
            errors.append(error)
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        thread.join(timeout=7)
        assert not thread.is_alive(), 'runner waited on a surviving descendant output pipe'
        assert not errors, errors
        assert results[0]['status'] == ('succeeded' if leader_exit else 'timed_out')
        assert results[0]['duration_seconds'] < 6
        assert all(not process_runtime.process_alive(pid) for pid in json.loads(pids.read_text()))
    finally:
        # Also bounds the deliberately failing old-source reproduction.
        if pids.exists():
            leader, _ = json.loads(pids.read_text())
            try:
                os.killpg(leader, signal.SIGKILL)
            except ProcessLookupError:
                pass
        thread.join(timeout=3)


@pytest.mark.parametrize('phase', ['command', 'spawn'])
def test_cancel_during_startup_retains_original_child_ownership(tmp_path, monkeypatch, phase):
    root, config = script_fixture(tmp_path, 'import time; time.sleep(60)')
    callbacks, restored, children = [], [], []
    monkeypatch.setattr(process_runtime, 'install_shutdown_handlers', lambda callback: callbacks.append(callback) or {})
    monkeypatch.setattr(process_runtime, 'restore_shutdown_handlers', lambda previous: restored.append(previous))
    original_command, original_spawn = runtime.command, process_runtime.spawn_process
    def command(*args, **kwargs):
        value = original_command(*args, **kwargs)
        if phase == 'command':
            callbacks[0](None, None)
        return value
    def spawn(*args, **kwargs):
        value = original_spawn(*args, **kwargs)
        children.append(value)
        assert kwargs['tree_id'] == config['process_tree_id']
        callbacks[0](None, None)
        return value
    monkeypatch.setattr(runtime, 'command', command)
    monkeypatch.setattr(process_runtime, 'spawn_process', spawn)
    result = runner.run(config, root, forward=lambda _: {})
    assert result['status'] == 'cancelled'
    assert len(children) == (phase == 'spawn')
    assert all(child.poll() is not None for child in children)
    assert restored == [{}]


def test_cleanup_failure_never_becomes_successful_terminal_result(tmp_path, monkeypatch):
    root, config = script_fixture(tmp_path, 'pass')
    original = process_runtime.terminate_process_tree
    def terminate(process, **kwargs):
        original(process, **kwargs)
        raise TimeoutError('tree cleanup unconfirmed')
    monkeypatch.setattr(process_runtime, 'terminate_process_tree', terminate)
    with pytest.raises(TimeoutError, match='tree cleanup unconfirmed'):
        runner.run(config, root, forward=lambda _: {})
    assert not (root / 'result.json').exists()


@pytest.mark.skipif(sys.platform != 'linux', reason='native Linux SIGTERM runner entry point')
def test_runner_entrypoint_persists_identity_and_revokes_before_cancel_result(tmp_path):
    ready = tmp_path / 'ready'
    root, config = script_fixture(tmp_path, f'from pathlib import Path\nimport time\nPath({str(ready)!r}).touch()\ntime.sleep(60)',
                                  token='test-only', portal_url='http://127.0.0.1:1')
    private = tmp_path / (root.name + '.json')
    private.write_text(json.dumps(config))
    child = process_runtime.spawn_process([sys.executable, '-m', runner.__name__, str(private)],
                                           cwd=runtime.REPOSITORY, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(.02)
        assert ready.exists()
        receipt = json.loads((root / 'runner.json').read_text())
        assert receipt == {'script_run_id': root.name, 'pid': child.pid}
        child.send_signal(signal.SIGTERM)
        assert child.wait(timeout=8) == 0
        assert json.loads((root / 'result.json').read_text())['status'] == 'cancelled'
        assert not private.exists()
    finally:
        process_runtime.terminate_process_tree(child, force=True)
        child.communicate(timeout=2)


@pytest.mark.parametrize('cleanup_failed', [False, True])
def test_runner_receipt_precedes_execution_and_terminal_result_follows_revocation(tmp_path, monkeypatch, cleanup_failed):
    root, config = script_fixture(tmp_path, 'pass')
    private = tmp_path / (root.name + '.json')
    private.write_text(json.dumps(config))
    monkeypatch.setattr(runner, 'sys', SimpleNamespace(argv=['runner', str(private)]))
    published = []
    publish = runner.atomic_json
    def observe(path, value):
        if path.name == 'result.json':
            assert not private.exists(), 'terminal publication must follow credential revocation'
        else:
            assert path.name == 'runner.json' and private.exists()
        published.append(path.name)
        publish(path, value)
    def execute(actual, run_dir):
        receipt = json.loads((run_dir / 'runner.json').read_text())
        assert receipt == {'script_run_id': root.name, 'pid': os.getpid()}
        assert private.exists() and actual == config
        if cleanup_failed:
            raise TimeoutError('payload cleanup unconfirmed')
        return {'status': 'succeeded'}
    monkeypatch.setattr(runner, 'atomic_json', observe)
    monkeypatch.setattr(runner, 'run', execute)
    if cleanup_failed:
        with pytest.raises(TimeoutError, match='payload cleanup unconfirmed'):
            runner.main()
        assert published == ['runner.json'] and not (root / 'result.json').exists()
    else:
        runner.main()
        assert published == ['runner.json', 'result.json']
        assert json.loads((root / 'result.json').read_text())['status'] == 'succeeded'
    assert not private.exists()


@pytest.fixture
def records(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, 'RUN_ROOT', tmp_path / 'runs')
    monkeypatch.setattr(runtime, 'PRIVATE_ROOT', tmp_path / 'private')
    monkeypatch.setattr(service, 'revoke', lambda _: None)
    def add(identifier='script_run_example', *, pid=None, pending=False, **extra):
        row = {'script_run_id': identifier, 'lineage_id': LINEAGE, 'status': 'running',
               'unit': '' if pending else 'portal-script-' + identifier, 'started_at': datetime.now(timezone.utc).isoformat(),
               'notified': False, **extra}
        store.admit(row)
        root = runtime.RUN_ROOT / identifier
        root.mkdir(parents=True)
        if pid is not None:
            (root / 'runner.json').write_text(json.dumps({'script_run_id': identifier, 'pid': pid}))
        return row, root
    return add


@pytest.mark.parametrize('platform', ['darwin', 'win32'])
def test_detached_launch_preserves_run_identity_and_allowed_environment(tmp_path, monkeypatch, records, platform):
    row, root = records()
    row.update(arguments={}, caller_id='sample_admin', trigger_path_id='', effect_id='')
    monkeypatch.setattr(service, 'sys', SimpleNamespace(platform=platform, executable=sys.executable))
    monkeypatch.setattr(service, '_issue_token', lambda *args: 'test-only')
    monkeypatch.setenv('DATABASE_PASSWORD_TEST', 'not-forwarded')
    monkeypatch.setenv('SystemRoot', 'C:\\Windows')
    calls = []
    monkeypatch.setattr(service, 'launch_job', lambda *a, **kw: calls.append((a, kw)) or 451)
    assert service.launch(row, {'cwd': str(tmp_path)}) == 451
    args, kwargs = calls[0]
    assert args[0] == row['unit']
    assert kwargs['platform'] == platform and kwargs['inherit_environment'] is False
    assert 'DATABASE_PASSWORD_TEST' not in kwargs['environment']
    assert kwargs['environment']['SystemRoot'] == 'C:\\Windows'
    saved = json.loads(Path(args[1][-1]).read_text())
    assert saved['script_run_id'] == row['script_run_id']
    assert saved['process_tree_id'] == service._payload_tree(row['script_run_id'])


def test_linux_launch_cancel_race_stops_original_unit_after_admission(tmp_path, monkeypatch, records):
    script = tmp_path / 'test.py'; script.write_text('pass')
    monkeypatch.setattr(service, 'sys', SimpleNamespace(platform='linux'))
    seen, stopped = [], []
    def launch(record, config):
        seen.append(record['script_run_id'])
        assert service.cancel(record['script_run_id'], 'sample_admin', lineage_id=LINEAGE)['status'] == 'running'
        assert not stopped
        return None  # systemd owns the PID
    monkeypatch.setattr(service, 'launch', launch)
    monkeypatch.setattr(process_runtime, 'run_process', lambda *args, **kwargs: stopped.append(args) or SimpleNamespace(returncode=0))
    first = service.start({'path': str(script), 'lineage_id': LINEAGE}, 'sample_admin', idempotency_key='original')
    again = service.start({'path': str(script), 'lineage_id': LINEAGE}, 'sample_admin', idempotency_key='original')
    assert len(seen) == len(stopped) == 1
    assert first['script_run_id'] == again['script_run_id'] == seen[0]
    assert store.get(seen[0])['status'] == 'cancelled'
    assert stopped[0][0] == ['systemctl', '--user', 'stop', 'portal-script-' + seen[0]]


def test_failed_systemd_stop_retains_pending_cancel(records, monkeypatch):
    row, root = records()
    monkeypatch.setattr(service, 'sys', SimpleNamespace(platform='linux'))
    monkeypatch.setattr(process_runtime, 'run_process', lambda *args, **kwargs: SimpleNamespace(returncode=1, stderr='stop denied'))
    with pytest.raises(RuntimeError, match='stop denied'):
        service.cancel(row['script_run_id'], 'sample_admin', lineage_id=LINEAGE)
    saved = store.get(row['script_run_id'])
    assert saved['status'] == 'running' and saved['cancel_requested']


@pytest.mark.parametrize('platform', ['linux', 'darwin'])
def test_stop_preserves_racing_natural_result_without_holding_record_lock(records, monkeypatch, platform):
    row, root = records(pid=451)
    monkeypatch.setattr(service, 'sys', SimpleNamespace(platform=platform))
    monkeypatch.setattr(process_runtime, 'process_alive', lambda pid: True)
    monkeypatch.setattr(service, '_mac_runner_matches', lambda pid, identifier: True)
    def stop(*args, **kwargs):
        with store.edit(row['script_run_id']) as current:
            assert current['cancel_requested']
        (root / 'result.json').write_text(json.dumps({'status': 'succeeded', 'finished_at': service.now()}))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(process_runtime, 'run_process', stop)
    monkeypatch.setattr(process_runtime, 'terminate_process_tree', stop)
    assert service.cancel(row['script_run_id'], 'sample_admin', lineage_id=LINEAGE)['status'] == 'succeeded'


def test_mac_restart_uses_original_runner_receipt_and_requires_payload_result(records, monkeypatch):
    row, root = records(pending=True)
    (root / 'runner.json').write_text(json.dumps({'script_run_id': row['script_run_id'], 'pid': 451}))
    monkeypatch.setattr(service, 'sys', SimpleNamespace(platform='darwin'))
    monkeypatch.setattr(process_runtime, 'process_alive', lambda pid: pid == 451)
    monkeypatch.setattr(service, '_mac_runner_matches', lambda pid, identifier: pid == 451 and identifier == row['script_run_id'])
    stopped = []
    monkeypatch.setattr(process_runtime, 'terminate_process_tree', lambda *args, **kwargs: stopped.append(args))
    with pytest.raises(RuntimeError, match='confirmed payload cleanup'):
        service.cancel(row['script_run_id'], 'sample_admin', lineage_id=LINEAGE)
    assert stopped == [(451,)]
    assert store.get(row['script_run_id'])['status'] == 'running'


def test_mac_start_persists_pid_outside_database_without_relaunch(tmp_path, monkeypatch, records):
    script = tmp_path / 'test.py'; script.write_text('pass')
    monkeypatch.setattr(service, 'sys', SimpleNamespace(platform='darwin'))
    launched = []
    def launch(record, config):
        launched.append(record['script_run_id'])
        (runtime.RUN_ROOT / record['script_run_id']).mkdir(parents=True)
        return 451
    monkeypatch.setattr(service, 'launch', launch)
    arguments = {'path': str(script), 'lineage_id': LINEAGE}
    first = service.start(arguments, 'sample_admin', idempotency_key='same-native-launch')
    again = service.start(arguments, 'sample_admin', idempotency_key='same-native-launch')
    assert first == again and launched == [first['script_run_id']]
    row = store.get(first['script_run_id'])
    assert 'pid' not in row and row['unit'] == 'portal-script-' + first['script_run_id']
    assert service._runner_pid(row) == 451


def test_runner_receipt_mismatch_cannot_signal_another_run(records, monkeypatch):
    row, root = records()
    (root / 'runner.json').write_text(json.dumps({'script_run_id': 'other', 'pid': 451}))
    monkeypatch.setattr(service, 'sys', SimpleNamespace(platform='darwin'))
    monkeypatch.setattr(process_runtime, 'terminate_process_tree', lambda *a, **kw: pytest.fail('wrong run signalled'))
    with pytest.raises(RuntimeError, match='receipt identity'):
        service.cancel(row['script_run_id'], 'sample_admin', lineage_id=LINEAGE)
    assert store.get(row['script_run_id'])['status'] == 'running'


def test_mac_reused_pid_cannot_be_cancelled(records, monkeypatch):
    row, root = records(pid=451)
    monkeypatch.setattr(service, 'sys', SimpleNamespace(platform='darwin'))
    monkeypatch.setattr(process_runtime, 'process_alive', lambda pid: True)
    monkeypatch.setattr(service, '_mac_runner_matches', lambda *args: False)
    monkeypatch.setattr(process_runtime, 'terminate_process_tree', lambda *a, **kw: pytest.fail('reused PID signalled'))
    with pytest.raises(RuntimeError, match='identity could not be verified'):
        service.cancel(row['script_run_id'], 'sample_admin', lineage_id=LINEAGE)
    assert store.get(row['script_run_id'])['status'] == 'running'


@pytest.mark.parametrize('fail_payload', [True, False])
def test_windows_cancel_reopens_both_named_jobs_even_when_parent_exited(records, monkeypatch, fail_payload):
    row, root = records(pid=451)
    (root / 'payload.json').write_text(json.dumps({'script_run_id': row['script_run_id'], 'state': 'running', 'pid': 452}))
    monkeypatch.setattr(service, 'sys', SimpleNamespace(platform='win32'))
    monkeypatch.setattr(process_runtime, 'process_alive', lambda pid: pytest.fail('PID death is not Job emptiness'))
    monkeypatch.setattr(process_runtime, 'process_tree_alive', lambda *args, **kwargs: False)
    monkeypatch.setattr(service.portal_background_jobs, 'job_alive', lambda *args: False)
    calls = []
    def terminate(pid, **kwargs):
        calls.append((pid, kwargs['tree_id']))
        if fail_payload and kwargs['tree_id'].endswith('-payload'):
            raise TimeoutError('descendants remain')
    monkeypatch.setattr(process_runtime, 'terminate_process_tree', terminate)
    if fail_payload:
        with pytest.raises(TimeoutError, match='descendants remain'):
            service.cancel(row['script_run_id'], 'sample_admin', lineage_id=LINEAGE)
        assert store.get(row['script_run_id'])['status'] == 'running'
    else:
        assert service.cancel(row['script_run_id'], 'sample_admin', lineage_id=LINEAGE)['status'] == 'cancelled'
        assert service.cancel(row['script_run_id'], 'sample_admin', lineage_id=LINEAGE)['status'] == 'cancelled'
    assert calls == [(451, row['unit']), (451, service._payload_tree(row['script_run_id']))]


def test_unknown_platform_rejected_before_validation_or_spawn(monkeypatch):
    monkeypatch.setattr(service, 'sys', SimpleNamespace(platform='unknown'))
    with pytest.raises(NotImplementedError, match='Unsupported ScriptRun platform'):
        service._validate({'path': '/nonexistent'})
    with pytest.raises(NotImplementedError, match='Unsupported ScriptRun platform'):
        service.launch({}, {})
