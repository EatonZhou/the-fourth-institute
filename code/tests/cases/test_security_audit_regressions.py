from __future__ import annotations

import http.client
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from management_portal import agent_token_auth
from management_portal import device_identity_backend
from management_portal import manage_agent_tokens
from management_portal import serve_management_portal as portal
from tests.cases.security_http_support import server as _server


def test_login_challenge_only_consumes_one_concurrent_valid_factor(monkeypatch):
    """同一登录挑战并发提交两个有效验证码时只有一个通过，另一个不再校验"""
    first_verification = threading.Event()
    concurrent_verification = threading.Event()
    release_verification = threading.Event()
    attempts = []

    def verify(username, code):
        attempts.append(code)
        if len(attempts) == 1:
            first_verification.set()
            assert release_verification.wait(5)
        else:
            concurrent_verification.set()
        return 'recovery'

    monkeypatch.setattr(portal, '_LOGIN_CHALLENGES', {})
    monkeypatch.setattr(portal, '_PORTAL_TWO_FACTOR', SimpleNamespace(verify=verify))
    challenge = portal._start_login_challenge('audit-user')
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(portal._verify_login_challenge, challenge, 'first-code')
        assert first_verification.wait(5)
        second = executor.submit(portal._verify_login_challenge, challenge, 'second-code')
        concurrent_verification.wait(0.15)
        release_verification.set()
        results = [first.result(timeout=5), second.result(timeout=5)]
    assert results.count(('audit-user', 'recovery')) == 1
    assert attempts == ['first-code']


def test_login_challenge_checks_attempt_budget_before_verifying(monkeypatch):
    """登录挑战尝试次数用尽后不再校验验证码并作废该挑战"""
    attempts = []
    monkeypatch.setattr(portal, '_LOGIN_CHALLENGES', {})
    monkeypatch.setattr(portal, '_PORTAL_TWO_FACTOR', SimpleNamespace(
        verify=lambda username, code: attempts.append(code) or 'recovery',
    ))
    challenge = portal._start_login_challenge('audit-user')
    portal._LOGIN_CHALLENGES[challenge]['attempts'] = portal._LOGIN_CHALLENGE_MAX_ATTEMPTS
    assert portal._verify_login_challenge(challenge, 'unused-code') is None
    assert attempts == []
    assert challenge not in portal._LOGIN_CHALLENGES


def test_admin_unexpected_errors_do_not_expose_internal_details(monkeypatch, subtests, isolated_portal_case):
    """访问控制管理接口出现意外错误时返回 500，不暴露内部细节"""
    for method in ['GET']:
        with subtests.test(method=method), isolated_portal_case() as case_path:
            with monkeypatch.context() as monkeypatch:
                monkeypatch.setattr(portal, '_check_session', lambda handler: True)
                monkeypatch.setattr(portal, '_get_session_username', lambda handler: 'test-actor')

                def fail(*args):
                    raise RuntimeError('private-storage-path-and-sensitive-diagnostic')

                monkeypatch.setattr(portal, '_admin_access_payload', fail)
                monkeypatch.setattr(portal, '_admin_access_action', fail)
                with _server(portal.Handler) as server:
                    connection = http.client.HTTPConnection('127.0.0.1', server.server_address[1], timeout=5)
                    try:
                        connection.request(method, '/api/admin/access-control', body='{}', headers={'Content-Type': 'application/json'})
                        response = connection.getresponse()
                        body = response.read()
                    finally:
                        connection.close()
                assert response.status == 500
                assert b'private-storage-path' not in body
                assert json.loads(body)['error']


def test_agent_registry_persists_through_postgres_authority():
    """Agent 令牌登记表经 PostgreSQL 持久化读写"""
    store = agent_token_auth.get_agent_token_store()
    store.put('audit', {'enabled': False})
    assert store.get('audit') == {'token_lookup': '', 'enabled': False}


def test_device_ssh_timeout_rejects_invalid_input_before_resolution(monkeypatch, subtests, isolated_portal_case):
    """设备 SSH 测试的超时参数不是 1 到 45 的整数时，在解析访问方式前就被拒绝"""
    for timeout in [0, -1, 46, True, None, 1.5, '5']:
        with subtests.test(timeout=timeout), isolated_portal_case() as case_path:
            with monkeypatch.context() as monkeypatch:
                resolved = []
                monkeypatch.setattr(device_identity_backend, 'effective_ssh_access', lambda *args, **kwargs: resolved.append(args))
                with pytest.raises(ValueError, match='integer from 1 to 45'):
                    device_identity_backend.test_ssh_connection('device_aaaaaaaaaaaa', timeout_seconds=timeout)
                assert resolved == []


def test_token_cli_and_managed_writers_share_database_transaction(capsys):
    """令牌命令行工具与服务端写入共用数据库事务，并发修改互不覆盖"""
    store = agent_token_auth.get_agent_token_store()
    store.put('target', {'enabled': True})
    started = threading.Event()

    def disable():
        started.set()
        return manage_agent_tokens.disable_agent('target')

    with ThreadPoolExecutor(max_workers=1) as executor:
        with store.transaction('target'):
            result = executor.submit(disable)
            assert started.wait(5)
            store.put('managed', {'enabled': True})
        assert result.result(timeout=5) == 0
    assert store.list() == {
        'target': {'token_lookup': '', 'enabled': False},
        'managed': {'token_lookup': '', 'enabled': True},
    }
    capsys.readouterr()


def test_device_ssh_test_enforces_accept_new_host_key_policy(monkeypatch):
    """设备 SSH 连通测试使用 StrictHostKeyChecking=accept-new 主机密钥策略"""
    commands = []
    monkeypatch.setattr(device_identity_backend, 'effective_ssh_access', lambda *args, **kwargs: {
        'via_resource_id': 'device_aaaaaaaaaaaa',
        'credential_saved': False,
        'host': '127.0.0.1',
        'user': 'audit',
    })

    def run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stderr='')

    monkeypatch.setattr(device_identity_backend.subprocess, 'run', run)
    result = device_identity_backend.test_ssh_connection('device_aaaaaaaaaaaa')
    assert result['reachable'] is True
    assert 'StrictHostKeyChecking=accept-new' in commands[0]
