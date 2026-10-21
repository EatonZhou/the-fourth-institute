"""TAU targets the selected shared-code instance without old deployment paths."""
import json

import pytest

from management_portal.test_framework.dev_target import (
    DevTargetError, DevTargetVerifier, resolve_test_target,
)


def test_explicit_release_target_verifies_identity_build_and_actor(tmp_path, monkeypatch):
    target_file = tmp_path / 'target.json'
    target = {'schema': 'tfi.portal-test-target.v1', 'deployment': {
        'id': 'release', 'kind': 'release', 'label': 'Test instance',
        'origin': 'http://127.0.0.1:28399', 'build_id': 'candidate-123',
    }}
    target_file.write_text(json.dumps(target))
    monkeypatch.setenv('PORTAL_TEST_TARGET_FILE', str(target_file))
    assert resolve_test_target().instance_id == 'release'
    storage = tmp_path / 'storage.json'
    storage.write_text(json.dumps({'cookies': [{'name': 'session', 'value': 'test-only', 'domain': '127.0.0.1'}]}))
    replies = {
        '/api/health': {'instance': 'release', 'build_id': 'candidate-123'},
        '/development?view=test': {},
        '/development/native-views?view=test': {'html': '<section data-test-workbench-version="component-v2"></section>'},
        '/api/me': {'username': 'test-actor', 'portal_instance': 'release', 'build_id': 'candidate-123'},
    }
    def get(url, headers):
        path = '/' + url.split('/', 3)[-1]
        if path != '/api/health':
            assert headers['Cookie'] == 'session=test-only'
        return json.dumps(replies[path]).encode()
    verifier = DevTargetVerifier(http_get=get)
    assert verifier.verify(storage).actual_build_id == 'candidate-123'
    replies['/api/health']['instance'] = 'other'
    with pytest.raises(DevTargetError):
        verifier.verify(storage)
    replies['/api/health']['instance'] = 'release'
    replies['/api/me']['username'] = 'unrelated-user'
    with pytest.raises(DevTargetError):
        verifier.verify(storage)


@pytest.mark.parametrize('deployment', [
    {},
    {'id': 'release', 'kind': 'release', 'label': 'Test', 'origin': 'http://127.0.0.1:28399'},
    {'id': 'release', 'kind': 'release', 'label': 'Test', 'origin': 'http://192.0.2.1:28399', 'build_id': 'candidate'},
])
def test_incomplete_or_nonlocal_target_is_rejected(tmp_path, deployment):
    path = tmp_path / 'target.json'
    path.write_text(json.dumps({'schema': 'tfi.portal-test-target.v1', 'deployment': deployment}))
    with pytest.raises(DevTargetError):
        resolve_test_target(path)


def test_absent_target_does_not_fall_back_to_old_dev(tmp_path, monkeypatch):
    monkeypatch.setenv('PORTAL_TEST_TARGET_FILE', str(tmp_path / 'absent.json'))
    with pytest.raises(DevTargetError):
        DevTargetVerifier().verify(tmp_path / 'absent-storage.json')


def test_tau_executor_retains_instance_browser_installation(monkeypatch, tmp_path):
    """TAU 子进程使用实例的浏览器目录，普通隔离测试不继承这些运行态设置"""
    from management_portal.test_framework.executor import unit_environment
    monkeypatch.setenv('HOME', str(tmp_path / 'instance-home'))
    monkeypatch.setenv('PLAYWRIGHT_BROWSERS_PATH', str(tmp_path / 'browsers'))
    monkeypatch.setenv('SECRET_TOKEN', 'never-forward')
    tau = unit_environment(tmp_path, tau=True)
    assert tau['HOME'] == str(tmp_path / 'instance-home')
    assert tau['PLAYWRIGHT_BROWSERS_PATH'] == str(tmp_path / 'browsers')
    assert 'SECRET_TOKEN' not in tau
    local = unit_environment(tmp_path, tau=False)
    assert 'HOME' not in local
    assert local['PLAYWRIGHT_BROWSERS_PATH'] == str(tmp_path / 'browsers')


@pytest.mark.parametrize('origin,valid', [
    ('http://127.0.0.1:28399', True), ('http://[::1]:28399', True),
    ('http://127.example.invalid:28399', False), ('http://user:secret@127.0.0.1:28399', False),
    ('http://192.0.2.1:28399', False), ('http://127.0.0.1:28399/path', False),
])
def test_python_and_browser_target_reject_nonlocal_or_credential_urls(tmp_path, origin, valid):
    """Python 与浏览器目标均拒绝伪回环主机、凭据 URL 和非根路径"""
    import os
    import subprocess
    from pathlib import Path
    path = tmp_path / 'target.json'
    path.write_text(json.dumps({'schema': 'tfi.portal-test-target.v1', 'deployment': {
        'id':'release', 'kind':'release', 'label':'Target', 'origin':origin, 'build_id':'candidate'}}))
    if valid:
        assert resolve_test_target(path).instance_id == 'release'
    else:
        with pytest.raises(DevTargetError):
            resolve_test_target(path)
    module = Path(__file__).resolve().parents[2] / 'management_portal/test_framework/dev_target.js'
    result = subprocess.run(['node', '-e', "require(process.argv[1]).resolveTestTarget()", str(module)],
        env={**os.environ, 'PORTAL_TEST_TARGET_FILE': str(path)}, capture_output=True, text=True)
    assert (result.returncode == 0) is valid, result.stderr
