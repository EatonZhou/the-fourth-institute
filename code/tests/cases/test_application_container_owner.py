"""Real local processes, a file-backed fake daemon; no Docker or installs."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[2]
IMAGE = 'sha256:' + 'a' * 64


def eventually(check, timeout=12):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if check():
            return
        time.sleep(.05)
    assert check()


@pytest.fixture
def daemon(tmp_path):
    executable = tmp_path / 'docker'
    executable.write_text('#!' + sys.executable + '\n' + r'''
import json, pathlib, sys, time
root = pathlib.Path(__file__).parent
args = sys.argv[1:]
state = root / 'container.json'
if 'image' in args:
    print(json.dumps({'Id': 'sha256:' + 'a' * 64, 'Os': 'linux', 'Config': {}}))
elif 'create' in args:
    name = args[args.index('--name') + 1]
    labels = dict(args[n+1].split('=', 1) for n,v in enumerate(args) if v == '--label')
    state.write_text(json.dumps({'Id': 'b' * 64, 'Name': '/' + name, 'Config': {'Labels': labels}}))
    print('b' * 64)
elif 'start' in args:
    (root / 'started').touch()
    while not (root / 'finish').exists() and state.exists(): time.sleep(.05)
    if '--interactive' in args: sys.stdout.buffer.write(sys.stdin.buffer.read())
    else: print('unicode 构建', end='')
elif 'inspect' in args:
    if not state.exists(): sys.exit(1)
    if '{{json .State}}' in args: print(json.dumps({'Running': False, 'ExitCode': 0, 'Error': ''}))
    else: print(state.read_text())
elif 'ls' in args:
    if state.exists(): print('b' * 64)
elif 'rm' in args:
    if (root / 'deny-remove').exists(): sys.exit(1)
    state.unlink(missing_ok=True)
    (root / 'removed').touch()
else: raise RuntimeError(args)
''')
    executable.chmod(0o700)
    return executable


def caller(tmp_path, daemon, *, timeout=20, payload=None):
    config = dict(docker_executable=str(daemon), docker_endpoint='unix:///fake.sock', container_image=IMAGE)
    if payload is not None:
        (tmp_path / 'client-input').write_bytes(payload)
    program = '''import json, pathlib, sys
from management_portal.workbench_projections import application_build as b
b._container_settings = lambda: json.loads(sys.argv[2])
source = pathlib.Path(sys.argv[1], 'client-input')
payload = source.read_bytes() if source.exists() else None
result = b._container_command(pathlib.Path(sys.argv[1]), ['/usr/bin/node'], timeout=TIMEOUT, input=payload)
assert result == (payload if payload is not None else 'unicode 构建'.encode())
'''.replace('TIMEOUT', repr(timeout))
    env = {**os.environ, 'TMPDIR': str(tmp_path), 'TEMP': str(tmp_path), 'TMP': str(tmp_path)}
    return subprocess.Popen([sys.executable, '-c', program, str(tmp_path), json.dumps(config)], cwd=ROOT,
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


@pytest.mark.skipif(sys.platform != 'linux', reason='native Linux process death; no foreign platform claim')
def test_abrupt_web_exit_reaps_daemon_container(tmp_path, daemon):
    process = caller(tmp_path, daemon)
    try:
        eventually(lambda: (tmp_path / 'started').exists())
        os.kill(process.pid, signal.SIGKILL)
        process.wait(timeout=5)
        eventually(lambda: (tmp_path / 'removed').exists())
        assert not (tmp_path / 'container.json').exists()
        from management_portal.workbench_projections.application_container_owner import recover_completed_controls
        eventually(lambda: bool(list(tmp_path.glob('portal-build-owner-*/result.json'))))
        report = recover_completed_controls()
        assert not report['errors']
        assert not list(tmp_path.glob('portal-build-owner-*'))
    finally:
        (tmp_path / 'finish').touch()
        if process.poll() is None: process.kill()
        process.communicate(timeout=10)


@pytest.mark.skipif(sys.platform != 'linux', reason='executable fixture uses a Linux shebang')
def test_owner_returns_bytes_after_confirmed_cleanup(tmp_path, daemon):
    (tmp_path / 'finish').touch()
    process = caller(tmp_path, daemon)
    out, err = process.communicate(timeout=20)
    assert process.returncode == 0, (out, err)
    assert (tmp_path / 'removed').exists()


@pytest.mark.skipif(sys.platform != 'linux', reason='executable fixture uses a Linux shebang')
def test_binary_input_is_separate_from_lifetime_pipe(tmp_path, daemon):
    (tmp_path / 'finish').touch()
    process = caller(tmp_path, daemon, payload=b'\x00\xff\r\n' * 20000)
    out, err = process.communicate(timeout=20)
    assert process.returncode == 0, (out, err)
    assert not list(tmp_path.glob('portal-build-owner-*'))


@pytest.mark.skipif(sys.platform != 'linux', reason='executable fixture uses a Linux shebang')
def test_deadline_reaps_daemon_before_reporting_failure(tmp_path, daemon):
    process = caller(tmp_path, daemon, timeout=.2)
    out, err = process.communicate(timeout=20)
    assert process.returncode != 0, (out, err)
    assert (tmp_path / 'removed').exists()
    assert not (tmp_path / 'container.json').exists()
    assert not list(tmp_path.glob('portal-build-owner-*'))


@pytest.mark.skipif(sys.platform != 'linux', reason='executable fixture uses a Linux shebang')
def test_cleanup_failure_retains_private_recovery_plan(tmp_path, daemon):
    (tmp_path / 'finish').touch()
    (tmp_path / 'deny-remove').touch()
    process = caller(tmp_path, daemon)
    out, err = process.communicate(timeout=20)
    assert process.returncode != 0, (out, err)
    plans = list(tmp_path.glob('portal-build-owner-*/plan.json'))
    assert len(plans) == 1
    receipt = json.loads((plans[0].parent / 'result.json').read_text())
    assert receipt['cleanup_complete'] is False
    assert (tmp_path / 'container.json').exists()
    # Recovery consumes the recorded ownership, never starts the build again.
    from management_portal.workbench_projections.application_container_owner import recover
    (tmp_path / 'deny-remove').unlink()
    recover(plans[0].parent)
    assert not plans[0].parent.exists()
    assert not (tmp_path / 'container.json').exists()


@pytest.mark.skipif(sys.platform != 'linux', reason='executable fixture uses a Linux shebang')
def test_recovery_refuses_replaced_container(tmp_path, daemon):
    (tmp_path / 'finish').touch()
    (tmp_path / 'deny-remove').touch()
    process = caller(tmp_path, daemon)
    process.communicate(timeout=20)
    control = next(tmp_path.glob('portal-build-owner-*/plan.json')).parent
    state = json.loads((tmp_path / 'container.json').read_text())
    state['Config']['Labels']['portal.application.owner'] = 'someone-else'
    (tmp_path / 'container.json').write_text(json.dumps(state))
    (tmp_path / 'deny-remove').unlink()
    from management_portal.workbench_projections.application_container_owner import recover
    with pytest.raises(RuntimeError, match='ownership changed'):
        recover(control)
    assert (tmp_path / 'container.json').exists()
    assert not json.loads((control / 'result.json').read_text())['cleanup_complete']


def test_live_parent_is_leased(tmp_path, daemon):
    from management_portal.workbench_projections import application_container_owner as owner
    process = caller(tmp_path, daemon)
    try:
        eventually(lambda: (tmp_path / 'started').exists())
        control = next(tmp_path.glob('portal-build-owner-*'))
        assert str(control) in owner.recover_completed_controls()['active']
        with pytest.raises(RuntimeError, match='active'):
            owner.recover(control)
        assert control.exists()
    finally:
        (tmp_path / 'finish').touch()
        process.communicate(timeout=20)


def test_same_process_recovery_and_occupied_retry(tmp_path, daemon, monkeypatch):
    from management_portal.workbench_projections import application_container_owner as owner
    from management_common.directory_retention import RetentionStore
    from management_common.owned_path_deletion import OwnedPathDeletionStore
    from management_common.directory_paths import cleanup_location
    monkeypatch.setattr(owner.tempfile, 'tempdir', str(tmp_path))
    (tmp_path / 'finish').touch()
    (tmp_path / 'deny-remove').touch()
    config = dict(docker_executable=str(daemon), docker_endpoint='unix:///fake.sock', container_image=IMAGE)
    with pytest.raises(Exception, match='清理未确认'):
        owner.run(tmp_path, ['/usr/bin/node'], config=config, input=b'keep')
    control = next(tmp_path.glob('portal-build-owner-*'))
    store = RetentionStore()
    with store.database.read() as conn:
        row = conn.execute(f'SELECT * FROM {store.table} WHERE location=%s', (cleanup_location(control),)).fetchone()
        assert row['category'] == 'runtime_tmp'
    assert not owner.recover_completed_controls()['cleanup']
    assert (control / 'input').read_bytes() == b'keep'
    with pytest.raises(Exception):
        owner.recover(control)
    assert (control / 'input').exists()
    (tmp_path / 'deny-remove').unlink()
    original = OwnedPathDeletionStore.remove
    def busy(*args, **kwargs):
        raise PermissionError('occupied control')
    monkeypatch.setattr(OwnedPathDeletionStore, 'remove', busy)
    owner.recover(control)
    assert any(row['status'] == 'retry' for row in store.queue.status())
    owner.recover(control)
    monkeypatch.setattr(OwnedPathDeletionStore, 'remove', original)
    store.database.write(lambda conn: conn.execute(f'UPDATE {store.queue.table} SET available_at=clock_timestamp()'))
    assert not owner.recover_completed_controls()['errors']
    assert not control.exists()
    owner.recover(control)


@pytest.mark.parametrize('stage', ['existing', 'collision', 'unproven', 'replaced'])
def test_control_allocation_identity_boundaries(tmp_path, monkeypatch, stage):
    from contextlib import contextmanager
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace
    from management_common import secure_paths as fs
    from management_common.directory_paths import cleanup_location
    from management_portal.workbench_projections import application_container_owner as owner
    monkeypatch.setattr(owner.tempfile, 'tempdir', str(tmp_path))
    monkeypatch.setattr(owner.uuid, 'uuid4', lambda: SimpleNamespace(hex='fixed'))
    control = tmp_path / 'portal-build-owner-fixed'
    store = owner._store()
    def declaration():
        with store.database.read() as conn:
            return conn.execute(f'SELECT * FROM {store.table} WHERE location=%s',
                                (cleanup_location(control),)).fetchone()
    if stage == 'existing':
        control.mkdir()
        (control / 'keep').write_text('other owner')
    elif stage == 'collision':
        original = fs.private_child
        @contextmanager
        def collide(parent, name, *, create=False):
            with ThreadPoolExecutor() as pool:
                assert pool.submit(declaration).result()['category'] == 'runtime_tmp'
            control.mkdir()
            (control / 'keep').write_text('other owner')
            with original(parent, name, create=create) as fd:
                yield fd
        monkeypatch.setattr(fs, 'private_child', collide)
    elif stage == 'unproven':
        def interrupted(*args):
            (control / 'keep').write_text('unproven')
            raise RuntimeError('interrupted identity commit')
        monkeypatch.setattr(fs, 'sync_directory', interrupted)
    if stage != 'replaced':
        with pytest.raises((FileExistsError, RuntimeError)):
            with owner._control():
                pytest.fail('must not yield')
    else:
        with owner._control() as (control, _, _):
            control.rename(tmp_path / 'original')
            control.mkdir()
            (control / 'keep').write_text('replacement')
            owner._write(control / 'result.json', {'cleanup_complete': True})
    report = owner.recover_completed_controls()
    assert (control / 'keep').exists()
    assert not store.queue.status()
    if stage in ('existing', 'collision'):
        assert declaration() is None and not report['errors']
    else:
        assert declaration() and report['errors']


def test_terminal_receipt_does_not_override_live_lease(tmp_path, monkeypatch):
    from management_portal.workbench_projections import application_container_owner as owner
    monkeypatch.setattr(owner.tempfile, 'tempdir', str(tmp_path))
    with owner._control() as (control, _, _):
        owner._write(control / 'result.json', {'cleanup_complete': True})
        assert owner.recover_completed_controls()['active'] == [str(control)]
        with pytest.raises(RuntimeError, match='active'):
            owner.recover(control)
        assert control.exists()
    assert not owner.recover_completed_controls()['errors']
    assert not control.exists()


def test_failed_plan_preparation_releases_control(tmp_path, monkeypatch):
    from management_portal.workbench_projections import application_container_owner as owner
    monkeypatch.setattr(owner.tempfile, 'tempdir', str(tmp_path))
    with pytest.raises(AttributeError):
        owner.run(None, [], config={})
    assert not list(tmp_path.glob('portal-build-owner-*'))
    assert not owner.recover_completed_controls()['errors']
