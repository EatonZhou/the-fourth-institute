from __future__ import annotations

import errno
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

import pytest

from management_portal import serve_management_portal
from management_portal import http_auth, web_resources
from management_portal.http_errors import normalize_json_error
from management_portal.resources import ResourceOperationError, ResourceService
from management_portal.resources.errors import (
    GitCheckoutRequired,
    ResourceServiceUnavailable,
    ResourceVersionConflict,
    resource_error_from_payload,
    resource_error_http_response,
    resource_error_payload,
)
from management_portal.resources.providers.repo import install_mutation_provider
from management_portal.resources.server import ResourceServiceServer
from management_portal.resources.transport import ResourceServiceClient
from management_portal.workbench_projections.store import ProjectionError


class QuietHandler(serve_management_portal.Handler):
    def log_message(self, _format, *_args):
        return


@pytest.fixture
def resource_connection(tmp_path, monkeypatch):
    signing_key = tmp_path / "rpc.key"
    signing_key.write_bytes(b"resource-error-contract-test-key-32-bytes")
    signing_key.chmod(0o600)
    monkeypatch.setenv("PORTAL_RESOURCE_SERVICE_PROCESS", "1")
    monkeypatch.setenv("PORTAL_RESOURCE_RPC_SIGNING_KEY_FILE", str(signing_key))
    service = ResourceService( index_provider=mock.Mock())
    install_mutation_provider(service)
    monkeypatch.setattr(service, "status", lambda: {"resource_count": 0})
    server = ResourceServiceServer(
        tmp_path / "resource.sock", service=service, publisher=mock.Mock(),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    assert server.started.wait(2)
    try:
        yield service, ResourceServiceClient(server.socket_path, timeout=2)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_git_checkout_required_matches_local_and_real_rpc(resource_connection, tmp_path):
    """Git 检出缺失错误在本地与真实 RPC 中一致"""
    local, remote = resource_connection
    checkout = tmp_path / "uninitialized"
    checkout.mkdir()
    failures = []
    for service in (local, remote):
        with pytest.raises(GitCheckoutRequired) as raised:
            service.register("repo", {"path": str(checkout), "project_ids": ["@public"]}, actor="test-user")
        failures.append(resource_error_payload(raised.value))
        assert raised.value.path == str(checkout)
    assert failures[0] == failures[1] == {
        "code": "git_checkout_required",
        "message": f"directory is not a Git checkout: {checkout}",
        "details": {"path": str(checkout), "can_initialize_git": True},
    }
    assert not (checkout / ".git").exists()


@pytest.mark.parametrize("error,code,status", [
    (ResourceVersionConflict("changed", expected_version={"number": 2},
                             current_version={"number": 3}), "resource_version_conflict", 409),
    (ResourceServiceUnavailable("projection unavailable"), "resource_service_unavailable", 503),
    (KeyError("missing record"), "resource_not_found", 404),
    (ValueError("invalid input"), "invalid_argument", 400),
    (PermissionError("forbidden"), "permission_denied", 403),
    (FileNotFoundError(errno.ENOENT, "file missing", "/tmp/resource-missing"), "file_not_found", 404),
    (FileExistsError(errno.EEXIST, "file exists", "/tmp/resource-existing"), "file_exists", 409),
    (RuntimeError("internal failure"), "internal_error", 500),
    (LookupError("unknown failure"), "internal_error", 500),
    pytest.param(OSError(errno.EIO, "internal I/O failure", "/tmp/unused"),
                 "internal_error", 500, id="unknown-oserror"),
])
def test_public_failures_survive_real_socket(
    resource_connection, monkeypatch, error, code, status,
):
    """公开错误经真实套接字传输后保留类型与状态码"""
    local, remote = resource_connection
    monkeypatch.setattr(local, "register", mock.Mock(side_effect=error))
    expected_type = RuntimeError if code == "internal_error" else type(error)
    with pytest.raises(expected_type) as raised:
        remote.register("repo", {"path": "/tmp/unused"}, actor="test-user")
    assert type(raised.value) is expected_type
    assert str(raised.value) == str(error)
    assert resource_error_payload(raised.value) == resource_error_payload(error)
    http_status, body = resource_error_http_response(raised.value)
    assert http_status == status
    assert body["code"] == code
    assert body["details"] == resource_error_payload(error)["details"]
    if code != "resource_service_unavailable":
        assert "retryable" not in body


def test_unknown_codes_do_not_become_business_errors(subtests, isolated_portal_case):
    """未知远端错误码统一映射为内部错误"""
    for code in ["unknown_remote_class", "RuntimeError", ""]:
        with subtests.test(code=code), isolated_portal_case() as case_path:
            error = resource_error_from_payload({
                "code": code, "class": "GitCheckoutRequired", "message": "unknown",
                "details": {"path": "/tmp/unused", "retryable": True},
            })
            assert type(error) is RuntimeError
            assert resource_error_http_response(error) == (
                500, {"error": "unknown", "code": "internal_error", "details": {}},
            )


def test_malformed_operation_errors_remain_internal(subtests, isolated_portal_case):
    """格式错误的资源操作错误统一归为内部错误"""
    for details in [
    {},
    {"code": "", "http_status": 409, "details": {}},
    {"code": "projection.conflict", "http_status": 200, "details": {}},
    {"code": "projection.conflict", "http_status": "409", "details": {}},
    {"code": "projection.conflict", "http_status": True, "details": {}},
    {"code": "projection.conflict", "http_status": 600, "details": {}},
    {"code": "projection.conflict", "http_status": 409, "details": None},
    {"code": "projection.conflict", "http_status": 409, "details": []},
]:
        with subtests.test(details=details), isolated_portal_case() as case_path:
            error = resource_error_from_payload({
                "code": "resource_operation_error", "message": "rejected", "details": details,
            })
            assert type(error) is RuntimeError
            assert resource_error_http_response(error)[0] == 500


def test_operation_error_preserves_details_without_restoring_domain_classes():
    """资源操作错误往返传输保留错误码和版本详情"""
    error = ProjectionError(
        "projection.conflict", "changed", 409,
        details={"expected_version": {"number": 2}, "current_version": {"number": 3}},
    )
    restored = resource_error_from_payload(resource_error_payload(error))
    assert type(restored) is ResourceOperationError
    assert restored.code == error.code
    assert restored.details == error.details
    assert resource_error_payload(restored) == resource_error_payload(error)
    assert resource_error_http_response(restored) == resource_error_http_response(error)


@pytest.mark.parametrize("failure,code,status", [
    ("invalid", "projection.title", 400),
    ("conflict", "projection.conflict", 409),
    ("missing", "projection.not_found", 404),
])
def test_workbench_provider_errors_match_local_and_rpc(
    resource_connection, monkeypatch, failure, code, status,
):
    """工作台 Provider 错误在本地与 RPC 中一致"""
    from management_portal.resources.providers import workbench as provider
    from management_portal.workbench_projections import service as projections
    from management_portal.workbench_projections import store as storage

    local, remote = resource_connection
    store = storage.Store()
    monkeypatch.setattr(local, "notify_source_changed", mock.Mock(return_value={}))
    monkeypatch.setattr(storage, "Store", lambda: store)
    monkeypatch.setattr(projections, "Store", lambda: store)
    provider.install_resolver(local)
    provider.install_mutation_provider(local)
    record = remote.register("workbench", {"title": "Contract test", "project_ids": ["@public"]}, actor="test-user")
    resource_id = record["resource_id"]
    if failure == "missing":
        store.delete(resource_id, "test-user")
    responses = []
    for service in (local, remote):
        with pytest.raises(ResourceOperationError) as raised:
            if failure == "missing":
                service.read_content(resource_id, actor="test-user")
            else:
                service.update(resource_id, {
                    "expected_revision": 0 if failure == "conflict" else 1,
                    "title": "" if failure == "invalid" else "Updated",
                }, actor="test-user")
        responses.append(resource_error_http_response(raised.value))
        assert raised.value.code == code
        assert raised.value.http_status == status
    assert responses[0] == responses[1]


def test_resource_content_socket_success_and_failure(resource_connection, monkeypatch):
    """资源内容经套接字传输保留字节与文件错误详情"""
    local, remote = resource_connection
    content = b"resource content\x00\xff"
    result = (content, "application/octet-stream", {"resource_id": "repo_0123456789ab"})
    read = mock.Mock(return_value=result)
    monkeypatch.setattr(local, "read_content", read)
    assert remote.read_content("repo_0123456789ab", actor="test-user") == result
    read.side_effect = FileNotFoundError(errno.ENOENT, "file missing", "/tmp/resource-missing")
    with pytest.raises(FileNotFoundError) as raised:
        remote.read_content("repo_0123456789ab", actor="test-user")
    assert raised.value.errno == errno.ENOENT
    assert raised.value.filename == "/tmp/resource-missing"


@pytest.mark.parametrize("route", [
    "/api/resources/resolve",
    "/api/resources/content",
])
def test_resource_read_http_errors_use_the_shared_contract(
    resource_connection, monkeypatch, route,
):
    """资源读取 HTTP 路由采用共享的服务不可用错误格式"""
    local, remote = resource_connection
    error = ResourceServiceUnavailable("projection unavailable")
    monkeypatch.setattr(local, "resolve", mock.Mock(side_effect=error))
    monkeypatch.setattr(local, "read_content", mock.Mock(side_effect=error))
    monkeypatch.setattr(local, "open_content", mock.Mock(side_effect=error))
    monkeypatch.setattr(http_auth, "_check_session", lambda _handler: True)
    monkeypatch.setattr(http_auth, "_get_session_username", lambda _handler: "test-user")
    monkeypatch.setattr(serve_management_portal, "get_resource_service", lambda: remote)
    monkeypatch.setattr(web_resources, "get_resource_service", lambda: remote)
    monkeypatch.setattr(
        http_auth, "_get_agent_api_auth",
        lambda _handler: {"type": "human", "id": "test-user"},
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(
                f"http://127.0.0.1:{server.server_address[1]}{route}?address=repo_0123456789ab",
                timeout=5,
            )
        assert raised.value.code == 503
        status, payload = resource_error_http_response(error)
        assert json.loads(raised.value.read()) == normalize_json_error(status, payload)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("failure,code", [
    ("git", "instruction.conflict"),
    ("version", "instruction.version_conflict"),
    ("unavailable", "instruction.owner_unavailable"),
    ("internal", "instruction.failed"),
    ("projection-invalid", "instruction.invalid_arguments"),
    ("projection-conflict", "instruction.conflict"),
    ("projection-missing", "instruction.not_found"),
])
def test_register_resource_errors_through_the_real_resource_socket_use_the_isa_vocabulary(
    resource_connection, tmp_path, monkeypatch, failure, code,
):
    """RegisterResource 经真实资源套接字返回 ISA 错误码"""
    from types import SimpleNamespace

    from management_portal.hatg.contracts import ERROR_STATUS, normalize_error
    from management_portal.resources import isa as resource_isa

    local, remote = resource_connection
    checkout = tmp_path / "uninitialized-register"
    checkout.mkdir()
    errors = {
        "git": GitCheckoutRequired(checkout),
        "version": ResourceVersionConflict("changed", expected_version=2, current_version=3),
        "unavailable": ResourceServiceUnavailable("projection unavailable"),
        "internal": RuntimeError("unexpected failure"),
        "projection-invalid": ProjectionError("projection.title", "invalid title"),
        "projection-conflict": ProjectionError(
            "projection.conflict", "changed", 409,
            details={"expected_version": 2, "current_version": 3},
        ),
        "projection-missing": ProjectionError("projection.not_found", "missing", 404),
    }
    if failure != "git":
        monkeypatch.setattr(local, "register", mock.Mock(side_effect=errors[failure]))
    monkeypatch.setattr(resource_isa, "_service", lambda: remote)
    arguments = {"resource_type": "repo", "fields": {"path": str(checkout), "project_ids": ["@public"]}}
    with pytest.raises(Exception) as raised:
        resource_isa.register_resource(SimpleNamespace(argument=arguments.get, caller="test-user"))
    error = normalize_error(raised.value)
    assert error.code == code
    assert error.http_status == ERROR_STATUS[code]
    assert not (checkout / ".git").exists()


def test_register_resource_instruction_keeps_the_git_checkout_recovery_details():
    """RegisterResource 保留 Git 检出恢复所需详情"""
    from management_portal.hatg.contracts import InstructionError
    from management_portal.resources import isa as resource_isa

    # The instruction error carries the same recoverable facts as the resource error contract.
    with pytest.raises(InstructionError) as raised:
        with resource_isa._registry_errors():
            raise GitCheckoutRequired("/srv/candidate")
    assert raised.value.code == "instruction.conflict"
    assert raised.value.details["reason"] == "git_checkout_required"
    assert raised.value.details["path"] == "/srv/candidate"
    assert raised.value.details["can_initialize_git"] is True
