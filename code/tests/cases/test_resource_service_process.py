from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path
from unittest import mock

import pytest

from management_portal.resources.errors import ResourceVersionConflict
from management_portal.resources.rpc_auth import sign_rpc_auth
from management_portal.resources.server import ResourceServiceServer
from management_portal.resources.service import ResourceServiceUnavailable
from management_portal.resources.transport import ResourceServiceClient


class FakePublisher:
    def health(self):
        return {"connected": False}

    def publish(self, _subject, _payload):
        return True


class FakeResourceService:
    def status(self):
        return {
            "schema": "tfi.resource-index.v3",
            "resource_count": 1,
        }

    def query(self, **params):
        return {
            "actor": params.get("actor", ""),
            "resources": [
                {
                    "resource_id": "paper_0123456789ab",
                    "resource_type": "paper",
                    "title": "Test paper",
                }
            ],
        }

    def suggest(self, **params):
        return {
            "actor": params.get("actor", ""),
            "query": params.get("q", ""),
            "resource_type": params.get("resource_type", ""),
            "resources": [{
                "resource_id": "resident_c74705728adf",
                "resource_type": "resident",
                "title": "代码整理",
                "url": "/lineage?resident=resident_c74705728adf",
            }],
        }

    def open_content(self, address, *, actor="", max_bytes=4 * 1024 * 1024):
        assert actor == "alice"
        if address.endswith("/file/large.pdf"):
            return {"address": address, "target_kind": "file", "content_size": 10, "content_mtime_ns": 1}
        return b"body", "text/plain", {"address": address}


    def register(self, _resource_type, payload, **_kwargs):
        if payload.get("missing"):
            raise FileNotFoundError("experiment workspace missing")
        raise PermissionError("Project contributor permission required")

    def sync_collaboration_resources(self, resources, *, domain_identity=""):
        assert domain_identity == "society-service"
        return {
            "schema": "tfi.collaboration-sync-result.v1",
            "resources": [
                {"resource_id": item["descriptor"]["resource_id"]}
                for item in resources
            ],
        }


class RecordingMutationResourceService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def update(self, _resource_id, _payload, *, resource_type="", **_kwargs):
        self.calls.append(("update", resource_type))
        return {"ok": True}

    def unregister(self, _resource_id, *, resource_type="", **_kwargs):
        self.calls.append(("unregister", resource_type))
        return {"ok": True}

    def action(
        self,
        _resource_id,
        _action,
        _payload,
        *,
        resource_type="",
        **_kwargs,
    ):
        self.calls.append(("action", resource_type))
        return {"ok": True}


def test_mutation_dispatch_preserves_explicit_resource_type_contracts(subtests, isolated_portal_case):
    """ResourceService 进程分发更新、注销和动作时保留显式资源类型，远端错误还原为对应异常"""
    with subtests.test(contract='test_mutation_dispatch_preserves_explicit_resource_type'), isolated_portal_case() as case_path:
        service = RecordingMutationResourceService()
        server = object.__new__(ResourceServiceServer)
        server.service = service
        server._dispatch_mutation('resource.update', {'resource_id': '2HourResearch', 'resource_type': 'project', 'payload': {'title': '2 Hour Research'}})
        server._dispatch_mutation('resource.unregister', {'resource_id': '2HourResearch', 'resource_type': 'project'})
        server._dispatch_mutation('resource.action', {'resource_id': '2HourResearch', 'resource_type': 'project', 'action': 'state.read'})
        assert service.calls == [('update', 'project'), ('unregister', 'project'), ('action', 'project')]
    with subtests.test(contract='test_remote_projection_failure_remains_service_unavailable'), isolated_portal_case() as case_path:
        with pytest.raises(ResourceServiceUnavailable, match='projection unavailable'):
            ResourceServiceClient._raise_remote_error({'code': 'resource_service_unavailable', 'message': 'resource projection unavailable: paper'})
    with subtests.test(contract='test_remote_resource_version_conflict_preserves_version_details'), isolated_portal_case() as case_path:
        with pytest.raises(ResourceVersionConflict) as captured:
            ResourceServiceClient._raise_remote_error({'code': 'resource_version_conflict', 'message': 'resource version conflict', 'details': {'expected_version': '2', 'current_version': '3'}})
        assert captured.value.expected_version == '2'
        assert captured.value.current_version == '3'


def test_resource_service_unix_socket_protocol_contracts(tmp_path, subtests, isolated_portal_case):
    """ResourceService 经 Unix socket 提供状态、查询、联想、内容和登记，客户端超时覆盖服务端等待"""
    with subtests.test(contract='test_resource_service_unix_socket_protocol'), isolated_portal_case() as case_path:
        tmp_path = case_path
        socket_path = tmp_path / 'resource-service.sock'
        from management_portal.resources.config import resource_rpc_signing_key_file
        from management_portal.resources.rpc_auth import load_rpc_signing_key
        load_rpc_signing_key(required=True, create=True,
                             path=resource_rpc_signing_key_file(socket_path=socket_path))
        server = ResourceServiceServer(socket_path, service=FakeResourceService(), publisher=FakePublisher())
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        assert server.started.wait(1)
        client = ResourceServiceClient(socket_path, timeout=2)
        try:
            status = client.status()
            assert status['resource_count'] == 1
            assert status['service']['schema'] == 'tfi.resource-service.v3'
            result = client.query(actor='alice', resource_types={'paper'})
            assert result['actor'] == 'alice'
            assert result['resources'][0]['resource_id'] == 'paper_0123456789ab'
            suggestions = client.suggest(actor='alice', q='代码整理', resource_type='resident')
            assert suggestions['actor'] == 'alice'
            assert suggestions['query'] == '代码整理'
            assert suggestions['resource_type'] == 'resident'
            assert suggestions['resources'][0]['resource_id'] == 'resident_c74705728adf'
            assert client.open_content('information_0123456789ab', actor='alice') == (
                b'body', 'text/plain', {'address': 'information_0123456789ab'},
            )
            assert client.open_content('skill_0123456789ab/file/large.pdf', actor='alice')['content_size'] == 10
            with pytest.raises(PermissionError, match='contributor'):
                client.register('experiment', {'project_id': 'ACLink'})
            with pytest.raises(FileNotFoundError, match='workspace missing'):
                client.register('experiment', {'missing': True})
            bundle = client.sync_collaboration_resources([{'descriptor': {'resource_id': 'resident_0123456789ab'}}])
            assert bundle['resources'] == [{'resource_id': 'resident_0123456789ab'}]
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        assert not socket_path.exists()
    with subtests.test(contract='test_missing_resource_service_socket_is_transport_unavailable'), isolated_portal_case() as case_path:
        tmp_path = case_path
        client = ResourceServiceClient(tmp_path / 'missing.sock', timeout=0.1)
        with pytest.raises(ResourceServiceUnavailable, match='unavailable'):
            client.status()
    with subtests.test(contract='test_client_deadlines_cover_server_side_index_waits'), isolated_portal_case() as case_path:
        tmp_path = case_path
        client = ResourceServiceClient(tmp_path / 'unused.sock', timeout=0.1)
        with mock.patch.object(client, '_exchange', return_value={}) as exchange:
            client.register('experiment', {}, wait_for_index=True, index_timeout=20)
            assert exchange.call_args.kwargs['timeout'] == 35
            client.query(resource_types=['paper'])
            assert exchange.call_args.kwargs['timeout'] == 105
            client.refresh(wait=True, timeout=120)
            assert exchange.call_args.kwargs['timeout'] == 135


def test_resource_service_socket_rejects_unsigned_actor_assertion_contracts(monkeypatch, tmp_path, subtests, isolated_portal_case):
    """ResourceService socket 拒绝未签名的身份声明、篡改参数和重放请求"""
    with subtests.test(contract='test_resource_service_socket_rejects_unsigned_actor_assertion'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            tmp_path = case_path
            signing_key = tmp_path / 'resource-rpc-signing.key'
            signing_key.write_bytes(b'resource-rpc-test-signing-key-32-bytes')
            signing_key.chmod(384)
            monkeypatch.setenv('PORTAL_RESOURCE_SERVICE_PROCESS', '1')
            monkeypatch.setenv('PORTAL_RESOURCE_RPC_SIGNING_KEY_FILE', str(signing_key))
            socket_path = tmp_path / 'resource-service.sock'
            server = ResourceServiceServer(socket_path, service=FakeResourceService(), publisher=FakePublisher())
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            assert server.started.wait(1)
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                    connection.connect(str(socket_path))
                    connection.sendall(json.dumps({'id': 'unsigned-actor', 'method': 'resource.query', 'params': {'actor': 'alice'}}).encode('utf-8') + b'\n')
                    response = json.loads(connection.makefile('rb').readline())
                assert response['ok'] is False
                assert response['error']['code'] == 'permission_denied'
                assert 'authenticated RPC actor' in response['error']['message']
                client = ResourceServiceClient(socket_path, timeout=2)
                assert client.query(actor='alice')['actor'] == 'alice'
                assert client.suggest(actor='alice', q='代码整理')['actor'] == 'alice'
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
    with subtests.test(contract='test_resource_service_rpc_auth_binds_params_and_rejects_replay'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            tmp_path = case_path
            key = b'resource-rpc-test-signing-key-32-bytes'
            signing_key = tmp_path / 'resource-rpc-signing.key'
            signing_key.write_bytes(key)
            signing_key.chmod(384)
            monkeypatch.setenv('PORTAL_RESOURCE_SERVICE_PROCESS', '1')
            monkeypatch.setenv('PORTAL_RESOURCE_RPC_SIGNING_KEY_FILE', str(signing_key))
            server = ResourceServiceServer(tmp_path / 'resource-service.sock', service=FakeResourceService(), publisher=FakePublisher())
            params = {'resource_types': ['paper']}
            auth = sign_rpc_auth(key, 'resource.query', params, actor='alice')
            try:
                assert server.authenticate('resource.query', dict(params), auth) == 'alice'
                with pytest.raises(PermissionError, match='replayed'):
                    server.authenticate('resource.query', dict(params), auth)
                with pytest.raises(PermissionError, match='invalid'):
                    server.authenticate('resource.query', {'resource_types': ['repo']}, sign_rpc_auth(key, 'resource.query', params, actor='alice'))
            finally:
                server.server_close()
    with subtests.test(contract='test_resource_service_creates_private_rpc_key_beside_socket'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            tmp_path = case_path
            monkeypatch.setenv('PORTAL_RESOURCE_SERVICE_PROCESS', '1')
            monkeypatch.delenv('PORTAL_RESOURCE_RPC_SIGNING_KEY_FILE', raising=False)
            server = ResourceServiceServer(tmp_path / 'resource-service.sock', service=FakeResourceService(), publisher=FakePublisher())
            try:
                signing_key = tmp_path / 'resource-rpc-signing.key'
                assert len(signing_key.read_bytes()) == 32
                assert signing_key.stat().st_mode & 511 == 384
            finally:
                server.server_close()


def test_client_does_not_timeout_before_a_waiting_mutation_finishes(
    tmp_path: Path,
) -> None:
    """等待索引的登记请求耗时超过客户端默认超时时，客户端仍等到结果返回"""
    class SlowMutationService:
        def status(self):
            return {"resource_count": 0}

        def register(self, _resource_type, _payload, **_kwargs):
            time.sleep(0.2)
            return {"resource_id": "experiment_0123456789ab"}

    socket_path = tmp_path / "resource-service.sock"
    server = ResourceServiceServer(
        socket_path,
        service=SlowMutationService(),
        publisher=FakePublisher(),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    assert server.started.wait(1)
    client = ResourceServiceClient(socket_path, timeout=0.1)
    try:
        result = client.register(
            "experiment",
            {},
            wait_for_index=True,
            index_timeout=0.3,
        )
        assert result["resource_id"] == "experiment_0123456789ab"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_resource_service_warmup_retries_after_temporary_failure(
    tmp_path: Path,
) -> None:
    """ResourceService 预热遇到临时故障后，下次状态查询触发重试并就绪"""
    class FlakyStatusService:
        def __init__(self) -> None:
            self.calls = 0

        def status(self):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("temporary startup failure")
            return {"resource_count": 7}

    service = FlakyStatusService()
    server = ResourceServiceServer(
        tmp_path / "resource-service.sock",
        service=service,
        publisher=FakePublisher(),
    )
    try:
        server._start_warmup()
        deadline = time.monotonic() + 1
        while not server._warm_error and time.monotonic() < deadline:
            time.sleep(0.005)
        assert server._warm_error == "RuntimeError: temporary startup failure"

        server._status()
        deadline = time.monotonic() + 1
        while not server._warm_ready and time.monotonic() < deadline:
            time.sleep(0.005)

        status = server._status()
        assert status["ready"] is True
        assert status["resource_count"] == 7
        assert service.calls >= 2
    finally:
        server.server_close()
