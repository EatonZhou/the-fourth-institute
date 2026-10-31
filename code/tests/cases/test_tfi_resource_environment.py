"""Canonical resource settings and credential child-process boundaries."""
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import shutil
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
SETTINGS = [
    ('resource_auto_registration', 'DEFAULT_RUN_STATE_PATH', 'RESOURCE_AUTO_REGISTER_STATE_PATH'),
    ('resource_identity_backend', 'DEFAULT_SSH_KEY_ROOT', 'SSH_KEY_ROOT'),
    ('article_build_results', 'DEFAULT_BUILD_ROOT', 'ARTICLE_BUILD_ROOT'),
    ('device_identity_backend', 'DEFAULT_STATUS_PATH', 'DEVICE_STATUS_PATH'),
    ('device_identity_backend', 'DEFAULT_DEVICE_SECRET_ROOT', 'DEVICE_SECRET_ROOT'),
    ('repo_credentials_backend', 'DEFAULT_CREDENTIAL_ROOT', 'REPO_CREDENTIAL_ROOT'),
    ('repo_credentials_backend', 'DEFAULT_KEY_PATH', 'REPO_CREDENTIAL_KEY_PATH'),
    ('repo_workspace_network', '_SSH_KEY_ROOT', 'SSH_KEY_ROOT'),
    ('resources.providers.project', 'SHARE_ROOT', 'SHARE_ROOT'),
    ('resources.providers.project', 'PROJECTS_ROOT', 'PROJECTS_ROOT'),
]


@pytest.fixture(autouse=True)
def isolated_paths(tmp_path, monkeypatch):
    paths = tmp_path / 'paths.json'
    paths.write_text(json.dumps({key: str(tmp_path / key) for key in
        ('share_root', 'staging_root', 'instances_root', 'secrets_dir')}))
    monkeypatch.setenv('PORTAL_PATHS_CONFIG', str(paths))
    from management_common import portal_paths
    monkeypatch.setattr(portal_paths, 'CONFIG_PATH', paths)


@pytest.mark.parametrize('mode', ['new', 'both', 'empty', 'alias-only'])
def test_resource_environment_paths(mode, tmp_path):
    env = os.environ.copy()
    expected = {}
    for _, _, suffix in SETTINGS:
        old, new = 'ARCHIVE_' + suffix, 'PORTAL_' + suffix
        env.pop(old, None)
        env.pop(new, None)
        if mode in ('alias-only', 'both', 'empty'):
            env[old] = str(tmp_path / 'old' / suffix)
        if mode in ('new', 'both', 'empty'):
            env[new] = '' if mode == 'empty' else str(tmp_path / 'new' / suffix)
        if new in env:
            expected[suffix] = str(Path(env[new]))
    code = '''
import importlib, json, os, sys
from pathlib import Path
settings, expected = json.loads(sys.argv[1])
controlled = {name: value for name, value in os.environ.items() if name.startswith(('PORTAL_', 'ARCHIVE_'))}
found_paths = {}
for module, attribute, suffix in settings:
    found = str(getattr(importlib.import_module('management_portal.' + module), attribute))
    if suffix in expected:
        assert found == expected[suffix], (module, attribute, found, expected[suffix])
    found_paths[module + '.' + attribute] = found
for name, value in controlled.items(): assert os.environ[name] == value
print(json.dumps(found_paths))
'''
    result = subprocess.run([sys.executable, '-c', code, json.dumps([SETTINGS, expected])],
                            cwd=ROOT, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    if mode == 'alias-only':
        # Compare against the same site configuration with no alias inputs.
        baseline = subprocess.run([sys.executable, '-c', code, json.dumps([SETTINGS, {}])],
                                  cwd=ROOT, env={k: v for k, v in env.items() if not k.startswith('ARCHIVE_')},
                                  capture_output=True, text=True)
        assert baseline.returncode == 0, baseline.stderr
        assert json.loads(result.stdout) == json.loads(baseline.stdout)


@pytest.mark.parametrize('mode', ['new', 'both'])
def test_resource_backup_and_registration_environment(mode, tmp_path, monkeypatch):
    from management_portal import resource_auto_registration as registration
    from management_portal.resource_backup_backend import ResourceBackupBackend
    values = {'RESOURCE_BACKUP_ROOT': str(tmp_path), 'RESOURCE_AUTO_REGISTER': '0',
              'RESOURCE_AUTO_REGISTER_INTERVAL_SECONDS': '123',
              'RESOURCE_AUTO_REGISTER_INITIAL_DELAY_SECONDS': '7'}
    for suffix, value in values.items():
        for prefix in ('PORTAL_', 'ARCHIVE_'):
            monkeypatch.delenv(prefix + suffix, raising=False)
        if mode == 'both':
            monkeypatch.setenv('ARCHIVE_' + suffix, 'wrong')
        monkeypatch.setenv('PORTAL_' + suffix, value)
    before = dict(os.environ)
    assert ResourceBackupBackend(workflow=object()).service_root == tmp_path
    assert registration.start_auto_registration_worker() is None
    monkeypatch.setenv('PORTAL_RESOURCE_AUTO_REGISTER', '1')
    delays, intervals = [], []
    stop = SimpleNamespace(wait=lambda value: delays.append(value) or len(delays) > 1,
                           is_set=lambda: False)
    class InlineThread:
        def __init__(self, target, **kwargs): self.target = target
        def start(self): self.target()
    # Only registration's own name: other threads still running from earlier tests (a
    # PostgreSQL pool spawning its workers) must keep the real threading.Thread.
    monkeypatch.setattr(registration, 'threading', SimpleNamespace(Thread=InlineThread, Event=registration.threading.Event))
    monkeypatch.setattr(registration, 'ResourceAutoRegistrar', lambda **kwargs: object())
    monkeypatch.setattr(registration, 'reconcile_if_due',
                        lambda registrar, interval_seconds: intervals.append(interval_seconds) or {})
    registration.start_auto_registration_worker(stop_event=stop)
    assert delays == [7, 123] and intervals == [123]
    for name, value in before.items():
        if name != 'PORTAL_RESOURCE_AUTO_REGISTER':
            assert os.environ[name] == value


@pytest.mark.parametrize('helper', ['git_credential_portal.py', 'git_ssh_askpass_portal.py', 'ssh_askpass_secret_file.sh'])
@pytest.mark.parametrize('mode', ['new', 'both', 'empty', 'alias-only'])
def test_standalone_credential_environment(helper, mode, tmp_path):
    ssh = helper.endswith('.sh')
    suffix = 'SSH_PASSWORD_FILE' if ssh else 'GIT_CREDENTIAL_FILE'
    paths = {}
    for name in ('old', 'new'):
        path = tmp_path / (name + '.secret')
        value = 'fixture-' + name
        path.write_text(value if ssh else json.dumps({'protocol': 'https', 'host': 'example.test',
                                                      'username': 'fixture', 'secret': value}))
        path.chmod(0o600)
        paths[name] = path
    env = {k: v for k, v in os.environ.items() if k not in ('PYTHONPATH', 'PORTAL_' + suffix, 'ARCHIVE_' + suffix)}
    if mode in ('alias-only', 'both', 'empty'): env['ARCHIVE_' + suffix] = str(paths['old'])
    if mode in ('new', 'both', 'empty'): env['PORTAL_' + suffix] = '' if mode == 'empty' else str(paths['new'])
    env['PORTAL_PYTHON'] = sys.executable
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in paths.values()}
    command = (['/bin/sh'] if ssh else [sys.executable]) + [str(ROOT / 'management_portal' / helper)]
    if helper == 'git_credential_portal.py': command.append('get')
    result = subprocess.run(command, env=env, cwd=tmp_path, input='protocol=https\nhost=example.test\n\n',
                            text=True, capture_output=True)
    assert result.stderr == ''
    if mode in ('empty', 'alias-only'):
        assert 'fixture-old' not in result.stdout and 'fixture-new' not in result.stdout
        assert result.stdout == ('quit=true\n' if helper == 'git_credential_portal.py' else '')
    else:
        assert result.returncode == 0, result.stderr
        assert 'fixture-new' in result.stdout
        assert 'fixture-old' not in result.stdout
    for path, saved in before.items(): assert (path.read_bytes(), path.stat().st_mtime_ns) == saved
    if mode in ('new', 'both') and os.name != 'nt':
        paths['new'].chmod(0o644)
        refused = subprocess.run(command, env=env, cwd=tmp_path, input='protocol=https\nhost=example.test\n\n',
                                 text=True, capture_output=True)
        assert 'fixture-old' not in refused.stdout and 'fixture-new' not in refused.stdout
        assert refused.stderr == ''


def test_git_and_device_inject_canonical_child_values(tmp_path, monkeypatch):
    from management_portal import device_identity_backend as device, repo_workspace_network as repo
    captured = []
    monkeypatch.setenv('PORTAL_GIT_CREDENTIAL_FILE', 'stale-new')
    monkeypatch.setenv('ARCHIVE_GIT_CREDENTIAL_FILE', 'stale-old')
    monkeypatch.setenv('PORTAL_SSH_PASSWORD_FILE', 'stale-new')
    monkeypatch.setenv('ARCHIVE_SSH_PASSWORD_FILE', 'stale-old')
    monkeypatch.setenv('ARCHIVE_PORTAL_PYTHON', 'ignored-interpreter')
    before = dict(os.environ)
    def git(checkout, *args, **kwargs):
        env = kwargs['env_overrides']
        path = Path(env['PORTAL_GIT_CREDENTIAL_FILE'])
        assert env['PORTAL_GIT_CREDENTIAL_FILE'] == str(path)
        assert 'ARCHIVE_GIT_CREDENTIAL_FILE' not in env
        assert json.loads(path.read_text())['secret'] == 'fixture-git'
        captured.append(path)
        return 'ok'
    monkeypatch.setattr(repo, '_git', git)
    repo._network_git(tmp_path, 'fetch', repo_id='repo_0123456789ab', operation='fetch',
                      remote='origin', branch='main', context={'protocol':'https','host':'example.test'},
                      credential={'username':'fixture','secret':'fixture-git'})
    assert captured and not captured[0].exists()
    password = tmp_path / 'password'
    password.write_text('fixture-ssh')
    monkeypatch.setattr(device, 'effective_ssh_access', lambda *args: {
        'via_resource_id':'device_0123456789ab', 'credential_saved':True, 'host':'example.test', 'user':'fixture'})
    monkeypatch.setattr(device, '_device_password_path', lambda *args: password)
    def run(command, **kwargs):
        env = kwargs['env']
        assert env['PORTAL_SSH_PASSWORD_FILE'] == str(password)
        assert env['PORTAL_PYTHON'] == sys.executable
        assert env['ARCHIVE_SSH_PASSWORD_FILE'] == 'stale-old'
        assert env['ARCHIVE_PORTAL_PYTHON'] == 'ignored-interpreter'
        assert str(password) not in str(command)
        assert 'fixture-ssh' not in str(command)
        return SimpleNamespace(returncode=0, stdout='', stderr='')
    monkeypatch.setattr(device.subprocess, 'run', run)
    report = device.test_ssh_connection('device_0123456789ab')
    assert 'fixture-ssh' not in str(report) and str(password) not in str(report)
    assert dict(os.environ) == before


@pytest.mark.skipif(os.name == 'nt', reason='POSIX launcher; native Windows is validated separately')
@pytest.mark.parametrize('unsafe', [None, 'permissions', 'symlink', 'hardlink'])
def test_shell_askpass_private_file_and_interpreter_paths_with_spaces(tmp_path, monkeypatch, unsafe):
    checkout = tmp_path / 'source with spaces'
    checkout.symlink_to(ROOT, target_is_directory=True)
    interpreter = tmp_path / 'current python'
    interpreter.symlink_to(sys.executable)
    secret = tmp_path / 'password file'
    secret.write_bytes(b'isolated-secret')
    secret.chmod(0o600)
    selected = secret
    if unsafe == 'permissions':
        secret.chmod(0o644)
    elif unsafe == 'symlink':
        selected = tmp_path / 'link'; selected.symlink_to(secret)
    elif unsafe == 'hardlink':
        os.link(secret, tmp_path / 'link')
    monkeypatch.setenv('PORTAL_PYTHON', str(interpreter))
    monkeypatch.setenv('ARCHIVE_PORTAL_PYTHON', 'must-not-execute')
    monkeypatch.setenv('PORTAL_SSH_PASSWORD_FILE', str(selected))
    monkeypatch.setenv('ARCHIVE_SSH_PASSWORD_FILE', 'must-not-read')
    command = [shutil.which('sh'), str(checkout / 'management_portal/ssh_askpass_secret_file.sh')]
    result = subprocess.run(command, cwd=tmp_path, capture_output=True)
    assert result.stderr == b''
    assert result.stdout == (b'isolated-secret' if unsafe is None else b'')
    assert (result.returncode == 0) == (unsafe is None)
    assert secret.read_bytes() == b'isolated-secret'


from tests.cases.compatibility_test_support import setUpModule, tearDownModule, site_environ
