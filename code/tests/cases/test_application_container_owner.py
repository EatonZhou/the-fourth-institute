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
    assert json.loads((plans[0].parent / 'result.json').read_text())['cleanup_complete']
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
