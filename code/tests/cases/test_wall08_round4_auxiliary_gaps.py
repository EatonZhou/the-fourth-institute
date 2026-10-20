from __future__ import annotations

import ipaddress
import json
import logging
import socket
import struct
import threading
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from management_portal import agent_skill_context as skills
from management_portal import ai_socket_relay as relay
from management_portal.execution import worker_manager


@pytest.fixture
def skill_root(tmp_path, monkeypatch):
    monkeypatch.setattr(skills, '_file_cache', {})
    monkeypatch.setattr(skills, '_last_good_by_role', {})
    root = tmp_path / 'tfi-agent-rule'
    root.mkdir()
    for name, content in [('SKILL.md', 'SYNTHETIC_CORE'), ('portal-resident.md', 'RESIDENT_ONLY'), ('portal-development.md', 'DEVELOPMENT_ONLY')]:
        (root / name).write_text(content)
    return tmp_path


def test_last_good_cache_is_role_scoped_and_requires_prior_success(skill_root, monkeypatch):
    """Portal Skill 源不可读时回退到同角色上次成功的内容，该角色未成功过则报错"""
    for role in ('resident', 'development'):
        skills.materialize_portal_skills(role, skill_root=str(skill_root))
    monkeypatch.setattr(skills, '_read_hashed_source', Mock(side_effect=OSError('synthetic unreadable source')))
    stale = skills.materialize_portal_skills('resident', skill_root=str(skill_root))
    assert stale.status == 'stale' and 'RESIDENT_ONLY' in stale.content and 'DEVELOPMENT_ONLY' not in stale.content
    for role, root in [('general', skill_root), ('resident', skill_root / 'another-root')]:
        with pytest.raises(RuntimeError):
            skills.materialize_portal_skills(role, skill_root=str(root))


@pytest.mark.parametrize('reason', ['source_unavailable', 'fresh_over_budget'])
def test_last_good_rendered_output_still_obeys_current_budget(skill_root, monkeypatch, reason):
    """Skill 源不可读或新内容超预算时，上次成功的内容同样受当前字符预算限制"""
    fresh = skills.materialize_portal_skills('resident', skill_root=str(skill_root))
    if reason == 'source_unavailable':
        monkeypatch.setattr(skills, '_read_hashed_source', Mock(side_effect=OSError('synthetic unreadable source')))
    else:
        (skill_root / 'tfi-agent-rule' / 'SKILL.md').write_text('LARGER_CORE' * 3000)
    with pytest.raises(ValueError, match='budget'):
        skills.materialize_portal_skills('resident', skill_root=str(skill_root), char_budget=len(fresh.content))


def test_worker_health_mismatch_never_activates_replacement(tmp_path, monkeypatch, subtests, isolated_portal_case):
    """新 Worker 健康检查不通过、build_id 或 worker_id 不符或进程退出时不启用替换"""
    for mismatch in ['ok', 'build_id', 'worker_id', 'child_exit']:
        with subtests.test(mismatch=mismatch), isolated_portal_case() as case_path:
            with monkeypatch.context() as monkeypatch:
                tmp_path = case_path
                health = {'ok': True, 'build_id': 'expected-build', 'worker_id': 'expected-generation-worker'}
                if mismatch != 'child_exit':
                    health[mismatch] = False if mismatch == 'ok' else 'other-generation'
                health_path = tmp_path / 'health.json'
                health_path.write_text(json.dumps(health))
                process = Mock()
                process.poll.return_value = 1 if mismatch == 'child_exit' else None
                candidate = SimpleNamespace(process=process, worker_id='expected-generation-worker', build_id='expected-build', health_path=health_path)
                manager = object.__new__(worker_manager.ExecutionWorkerManager)
                manager.ready_timeout = 0.1
                manager.kill_grace = 0.0
                manager._stopping = threading.Event()
                previous = object()
                manager._current = previous
                manager._spawn = Mock(return_value=candidate)
                manager._activate_replacement = Mock()
                manager._cleanup_offline_registrations = Mock()
                manager._write_health = Mock()
                clock = iter([0.0, 0.01, 0.2])
                monkeypatch.setattr(worker_manager, 'time', SimpleNamespace(monotonic=lambda: next(clock), sleep=lambda duration: None))
                with pytest.raises((TimeoutError, RuntimeError)):
                    manager._replace()
                assert manager._current is previous
                manager._activate_replacement.assert_not_called()
                manager._write_health.assert_not_called()


@pytest.fixture
def relay_fixture(monkeypatch):
    original_connect = socket.socket.connect
    allowed_ports = set()

    def connect_local(connection, address):
        assert connection.family == socket.AF_INET and address[0] == '127.0.0.1' and address[1] in allowed_ports
        return original_connect(connection, address)

    monkeypatch.setattr(socket.socket, 'connect', connect_local)
    monkeypatch.setattr(relay, '_resolve_resource', lambda resource: {'provider': 'fixture-api', 'model': 'fixed-model'})
    calls = []

    def forward(provider, payload):
        calls.append((provider, payload))
        return 200, {'choices': [{'message': {'content': payload['messages'][-1]['content']}}], 'usage': {'tokens': 3}, 'extra': {'preserved': True}}, 'application/json'

    monkeypatch.setattr(relay, '_forward_chat_completion', forward)

    @contextmanager
    def start(*, networks=None):
        semaphore = threading.BoundedSemaphore(1)
        server = relay.RelayTCPServer(('127.0.0.1', 0), relay.RelayHandler,
                                     allowed_networks=networks if networks is not None else [ipaddress.ip_network('127.0.0.0/8')],
                                     max_bytes=2048, semaphore=semaphore)
        allowed_ports.add(server.server_address[1])
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
        thread.start()
        try:
            yield server, semaphore
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)

    return SimpleNamespace(start=start, calls=calls)


def frame(number=1, **changes):
    return {'version': 1, 'request_id': f'synthetic-{number}', 'resource': 'fixture-resource',
            'payload': {'messages': [{'role': 'user', 'content': f'input-{number}'}]}, **changes}


def test_relay_one_tcp_connection_serializes_frames_and_preserves_complete_json(relay_fixture):
    """中继在同一 TCP 连接上依次处理多个帧并完整返回上游 JSON"""
    with relay_fixture.start() as (server, semaphore):
        with socket.create_connection(server.server_address, timeout=2) as connection:
            for number in (1, 2):
                relay._write_frame(connection, frame(number))
            responses = [relay._read_frame(connection, 10000), relay._read_frame(connection, 10000)]
            assert [response['request_id'] for response in responses] == ['synthetic-1', 'synthetic-2']
            assert [response['body_json']['choices'][0]['message']['content'] for response in responses] == ['input-1', 'input-2']
            assert all(response['provider'] == 'fixture-api' and response['body_json']['extra'] == {'preserved': True} for response in responses)
            assert [payload['model'] for provider, payload in relay_fixture.calls] == ['fixed-model', 'fixed-model']
        assert semaphore.acquire(timeout=2)
        semaphore.release()


@pytest.mark.parametrize('version', [0, 2, True, 1.0])
def test_relay_requires_integer_protocol_version_one(relay_fixture, version):
    """中继只接受整数 1 作为协议版本，其他值返回 400 且不转发"""
    with relay_fixture.start() as (server, semaphore):
        with socket.create_connection(server.server_address, timeout=2) as connection:
            relay._write_frame(connection, frame(version=version))
            assert relay._read_frame(connection, 10000)['status'] == 400
    assert relay_fixture.calls == []


@pytest.mark.parametrize('raw', [struct.pack('>I', 0), struct.pack('>I', 9999), struct.pack('>I', 1) + b'\xff', struct.pack('>I', 2) + b'[]'])
def test_relay_bad_frames_return_400_and_close_without_forwarding(relay_fixture, raw):
    """中继收到长度为零、超限或内容不是 JSON 对象的帧时返回 400 并关闭连接，不转发"""
    with relay_fixture.start() as (server, semaphore):
        with socket.create_connection(server.server_address, timeout=2) as connection:
            connection.sendall(raw)
            assert relay._read_frame(connection, 10000)['status'] == 400
            assert connection.recv(1) == b''
    assert relay_fixture.calls == []


def test_relay_peer_model_and_capacity_rejections_are_bounded(relay_fixture):
    """中继对不在允许网段的来源和改模型的请求返回 403，并发已满时返回 429"""
    with relay_fixture.start(networks=[]) as (server, semaphore):
        with socket.create_connection(server.server_address, timeout=2) as connection:
            assert relay._read_frame(connection, 10000)['status'] == 403
    with relay_fixture.start() as (server, semaphore):
        with socket.create_connection(server.server_address, timeout=2) as first:
            relay._write_frame(first, frame(payload={'model': 'other-model'}))
            assert relay._read_frame(first, 10000)['status'] == 403
            with socket.create_connection(server.server_address, timeout=2) as second:
                assert relay._read_frame(second, 10000)['status'] == 429
    assert relay_fixture.calls == []


def test_relay_upstream_error_does_not_echo_credential_in_logs_or_response(relay_fixture, monkeypatch, caplog):
    """中继上游出错时返回 502，响应和日志都不回显凭据"""
    sentinel = 'test-only-credential-sentinel'
    monkeypatch.setattr(relay, '_forward_chat_completion', Mock(side_effect=RuntimeError(sentinel)))
    caplog.set_level(logging.INFO)
    with relay_fixture.start() as (server, semaphore):
        with socket.create_connection(server.server_address, timeout=2) as connection:
            relay._write_frame(connection, frame())
            response = relay._read_frame(connection, 10000)
            assert response['status'] == 502 and sentinel not in json.dumps(response)
    assert sentinel not in caplog.text


def test_relay_payload_cannot_override_effective_registry_controls(monkeypatch):
    """中继请求载荷不能覆盖注册表中的 API、超时与重试设置"""
    from management_portal.execution.service import ExecutionService

    invoke = Mock(return_value={'execution_id': 'relay-test'})
    wait = Mock(return_value={'status': 'completed', 'result': {'output': {'response': {'choices': []}}}})
    monkeypatch.setattr(ExecutionService, 'submit', invoke)
    monkeypatch.setattr(ExecutionService, 'wait', wait)
    monkeypatch.setattr(relay, '_provider_registry_entry', lambda provider: {'id': 'fixed-api', 'timeout_s': 17, 'max_retries': 2})
    relay._forward_chat_completion('fixed-api', {'model': 'fixed-model', 'messages': [{'role': 'user', 'content': 'synthetic'}],
                                               'api_id': 'other-api', 'secret': 'test-only-caller-value',
                                               'base_url': 'https://not-contacted.invalid', 'timeout': 999, 'retries': 999})
    request = invoke.call_args.args[0]
    assert request.provider == 'fixed-api'
    assert request.runtime_options['timeout_seconds'] == 17
    assert request.runtime_options['max_retries'] == 2
    assert request.session.mode == 'stateless'
    assert wait.call_args.kwargs['timeout_seconds'] == 81


def test_relay_truthy_stream_is_a_protocol_error_before_forwarding(relay_fixture):
    """中继请求开启 stream 时在转发前返回 400"""
    with relay_fixture.start() as (server, semaphore):
        with socket.create_connection(server.server_address, timeout=2) as connection:
            relay._write_frame(connection, frame(payload={'stream': True}))
            assert relay._read_frame(connection, 10000)['status'] == 400
    assert relay_fixture.calls == []
