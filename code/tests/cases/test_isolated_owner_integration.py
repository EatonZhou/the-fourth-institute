from __future__ import annotations

import json
import multiprocessing
import os
import shutil
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.cases.isolated_owner_test_support import (
    ControlledStatusCollectors,
    PortalOwnerHTTP,
    local_connections_only,
    long_task_worker,
)


@pytest.fixture
def owner_http(tmp_path, monkeypatch):
    owner = PortalOwnerHTTP(tmp_path, monkeypatch)
    with local_connections_only(tcp_addresses=[owner.address]) as rejected:
        owner.start()
        try:
            yield owner
        finally:
            owner.close()
        assert rejected == []


def test_association_authenticated_http_write_failure_keeps_the_committed_value(owner_http, monkeypatch, record_property):
    """关联状态经认证 HTTP 写入失败时保留已提交值

    Signed synthetic auth and the actual Store keep the committed value when a write fails."""
    from management_portal import portal_ui_state

    owner = owner_http
    endpoint = "/api/ui-state/associations/put"
    payload = {"namespace": "fixture.surface", "key": "one", "value": {"revision": 1}}
    assert owner.request(endpoint, payload, authenticated=False)[0] == 401
    assert owner.request(endpoint, payload, origin="https://rejected.invalid")[0] == 403
    status, response = owner.request(endpoint, payload)
    assert status == 200 and response["association"]["value"] == {"revision": 1}

    from contextlib import contextmanager
    transaction = owner.store.database.transaction
    @contextmanager
    def failing_write(*args, **kwargs):
        with transaction(*args, **kwargs) as connection:
            yield connection
            raise OSError("isolated association write failed")

    with monkeypatch.context() as scoped:
        scoped.setattr(owner.store.database, "transaction", failing_write)
        status, response = owner.request(endpoint, {**payload, "value": {"revision": 2}})
    assert status == 500
    # The transaction rolled back: the store and a reopened store keep the committed value.
    assert owner.store.get(owner.actor, "fixture.surface", "one")["value"] == {"revision": 1}
    restored = portal_ui_state.PortalUIStateStore()
    assert restored.get(owner.actor, "fixture.surface", "one")["value"] == {"revision": 1}
    assert owner.request(endpoint, {**payload, "value": {"revision": 3}})[0] == 200
    record_property("owner_evidence", json.dumps({"auth_statuses": [401, 403, 200], "write_failure": 500, "committed_value_preserved": True, "reopened_store_value": 1, "retry_status": 200}))


@pytest.mark.parametrize("operation", ["put", "remove", "list"])
@pytest.mark.parametrize("error_type", [OSError, ValueError])
def test_association_store_factory_failure_reaches_transport_boundary(
    owner_http, monkeypatch, record_property, operation, error_type
):
    """关联存储创建失败返回安全 JSON 错误且恢复后可用

    Factory failures return safe JSON 500 without dispatch; recovery and input 400 remain intact."""
    from management_portal import portal_ui_state

    owner = owner_http
    namespace = "fixture.surface"
    owner.store.put(owner.actor, namespace, "one", {"revision": 1})
    before = owner.store.list(owner.actor, namespace)
    payload = {"namespace": namespace, "key": "one", "value": {"revision": 2}}
    endpoint = (
        f"/api/ui-state/associations?namespace={namespace}"
        if operation == "list"
        else f"/api/ui-state/associations/{operation}"
    )
    request_payload = None if operation == "list" else payload
    factory = Mock(side_effect=error_type("/private/fixture-path credential=fixture-sensitive"))
    methods = {
        name: Mock(wraps=getattr(owner.store, name))
        for name in ("put", "remove", "list")
    }
    with monkeypatch.context() as scoped:
        scoped.setattr(portal_ui_state, "get_portal_ui_state_store", factory)
        for name, method in methods.items():
            scoped.setattr(owner.store, name, method)
        status, response = owner.request(endpoint, request_payload)
    factory.assert_called_once_with()
    assert status == 500
    # The shared operation-error envelope carries the safe message only.
    assert response["error"] == "UI association storage is unavailable."
    assert (response["code"], response["http_status"], response["details"]) == ("operation.failed", 500, {})
    assert "fixture-sensitive" not in json.dumps(response)
    assert "/private/fixture-path" not in json.dumps(response)
    for method in methods.values():
        method.assert_not_called()
    assert owner.store.list(owner.actor, namespace) == before
    assert owner.server.errors == []

    recovery_status, recovery = owner.request(endpoint, request_payload)
    assert recovery_status == 200
    if operation == "put":
        assert recovery["association"]["value"] == {"revision": 2}
    elif operation == "remove":
        assert recovery == {"removed": True}
    else:
        assert recovery["namespace"] == namespace
        assert [item["value"] for item in recovery["items"]] == [{"revision": 1}]
    recovered = owner.store.list(owner.actor, namespace)
    invalid_endpoint = (
        "/api/ui-state/associations?namespace=invalid/namespace"
        if operation == "list" else endpoint
    )
    invalid_payload = None if operation == "list" else {
        **payload, "namespace": "invalid/namespace"
    }
    invalid_status, invalid = owner.request(invalid_endpoint, invalid_payload)
    assert invalid_status == 400 and "namespace" in invalid["error"]
    assert owner.store.list(owner.actor, namespace) == recovered
    assert owner.server.errors == []
    record_property("owner_evidence", json.dumps({
        "operation": operation,
        "injected_factory_exception": error_type.__name__,
        "status": status,
        "response": response,
        "store_methods_dispatched_on_failure": 0,
        "stored_values_preserved": True,
        "underlying_path_and_sensitive_message_exposed": False,
        "recovery_status": recovery_status,
        "input_validation_status": invalid_status,
        "transport_errors": owner.server.errors,
    }))


def test_home_cache_http_collector_window_and_browser_notice(owner_http, monkeypatch, record_property):
    """首页刷新缓存遵守时间窗口且浏览器提示同步更新

    Actual cache and HTTP owners compose with controlled RPC collectors and the real Home renderer."""
    collectors = ControlledStatusCollectors(owner_http, monkeypatch)
    assert collectors.backend.MANUAL_REFRESH_MIN_SECONDS == 300
    try:
        status, accepted = owner_http.request("/api/agent-status/refresh", {})
        assert status == 200
        collectors.wait()
        assert len(collectors.calls) == 1
        first = owner_http.request("/api/agent-status")[1]
        assert first["accounts"][0]["status"] == "ok"
        repo = Path(__file__).resolve().parents[2]
        static = owner_http.root / "static-snapshot"
        static.mkdir()
        shutil.copy2(repo / "management_portal/index.html", static / "index.html")
        shutil.copytree(repo / "management_portal/static", static / "static")
        probe = repo / "tests/cases/javascript/home_owner_integration.cjs"
        completed = subprocess.run(
            ["node", str(probe)],
            input=json.dumps({"origin": owner_http.origin, "cookie": owner_http.token, "static": str(static)}),
            text=True,
            capture_output=True,
            timeout=35,
            cwd=repo,
        )
        assert completed.returncode == 0, completed.stderr + completed.stdout
        evidence = json.loads(completed.stdout)
        assert evidence["same_window_notice_matches_response"] is True
        assert evidence["window_reopened_notice_cleared"] is True
        collectors.wait()
        assert [call["clock"] for call in collectors.calls] == [1000.0, 1301.0]
        stored = json.loads(collectors.backend.STATUS_CACHE_PATH.read_text())
        assert stored[collectors.backend._agent_status_summary_cache_key(owner_http.actor)]["last_forced_at"] == 1301.0
        evidence.update({"initial_http_status": status, "initial_response_status": accepted.get("status"), "rpc_calls": collectors.calls, "stored_last_forced_at": 1301.0, "trace": owner_http.trace})
        record_property("owner_evidence", json.dumps(evidence))
    finally:
        collectors.release.set()
        collectors.wait()


def test_execution_owner_capacity_preserves_independent_running_workers(tmp_path, monkeypatch, record_property):
    """降低执行账号容量保留已运行进程并阻止新任务抢占

    Three separate harmless worker processes use the real temporary Execution RPC owner."""
    from management_portal.execution import service as execution_module
    from management_portal.execution.agent_data_store import AgentDataStore
    from tests.cases.account_registration_test_support import register_codex
    from management_portal.execution.contracts import ExecutionRequest, SessionOptions
    from management_portal.execution.scheduler import Scheduler
    from management_portal.execution.service import ExecutionPreparation, ExecutionService, ExecutionServiceServer
    from management_portal.execution.session_manager import SessionManager
    from management_portal.execution.transport import ExecutionServiceClient

    accounts = register_codex(monkeypatch, tmp_path, names=("fixture",), default="fixture")
    agent_data = AgentDataStore()
    from management_portal.execution.account_adapters import CodexAccounts
    from management_portal.execution.accounts import ExecutionAccounts

    # Only the fixture Codex account: the default set also reaches the real Claude daemon and Provider APIs.
    preparation = ExecutionPreparation(accounts=ExecutionAccounts((CodexAccounts(account_registry=accounts),)),
                                       session_manager=SessionManager(agent_data=agent_data))
    socket_path = tmp_path / "owner.sock"
    scheduler = Scheduler(accounts=preparation.accounts, max_concurrency=3, capacity_limits={"codex:fixture": 2}, dispatch_interval_min_seconds=0, dispatch_interval_max_seconds=0)
    monkeypatch.setattr(execution_module, "get_notification_publisher", lambda: SimpleNamespace(publish=lambda *args, **kwargs: None, health=lambda: {}))
    server = ExecutionServiceServer(socket_path, scheduler, instance="round6", build_id="isolated-owner", preparation=preparation, agent_data=agent_data)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    workers = []
    channels = []
    received = []
    context = multiprocessing.get_context("spawn")

    def receive(channel):
        assert channel.poll(8), "isolated worker did not reply"
        message = channel.recv()
        received.append(message)
        return message

    # The account monitor probes the fixture account's (absent) app-server socket for quota; nothing else leaves the test.
    fixture_socket = accounts.get("fixture").socket_path
    assert fixture_socket.is_relative_to(tmp_path) and not fixture_socket.exists()
    with local_connections_only(unix_paths=[socket_path, fixture_socket]) as rejected:
        thread.start()
        assert server.started.wait(2)
        try:
            client = ExecutionServiceClient(socket_path, timeout=3)
            service = ExecutionService(client=client)
            client.request("scheduler.worker.register", {"worker_id": "round6:worker-pool", "state": "active", "build_id": "isolated-owner", "pid": os.getpid(), "capabilities": ["agent_session:codex"]})
            requests = [ExecutionRequest.new(actor="isolated-owner", agent_id=f"isolated-worker-{index}", prompt="Harmless local heartbeat loop; never sent to a Provider", service_type="agent_session", provider="codex", operation="turn", session=SessionOptions(mode="persistent", binding_id=f"fixture-r6:{index}", backend_route_id="fixture"), cwd=str(tmp_path)) for index in range(3)]
            for request in requests:
                client.request("execution.submit", request.to_payload())
            for ordinal in range(3):
                parent, child = context.Pipe()
                worker = context.Process(target=long_task_worker, args=(str(socket_path), child, ordinal))
                worker.start()
                child.close()
                workers.append(worker)
                channels.append(parent)
                assert receive(parent)["event"] == ("running" if ordinal < 2 else "blocked")
            assert len({worker.pid for worker in workers}) == 3
            service.configure_account_scheduler("fixture", 1, 0, 0, actor="isolated-owner")
            for channel in channels[:2]:
                channel.send("probe")
                assert receive(channel)["event"] == "alive"
            assert all(worker.is_alive() for worker in workers)
            channels[2].send("claim")
            assert receive(channels[2])["event"] == "blocked"
            channels[0].send("release")
            assert receive(channels[0])["event"] == "completed"
            channels[2].send("claim")
            assert receive(channels[2])["event"] == "blocked"
            channels[1].send("release")
            assert receive(channels[1])["event"] == "completed"
            channels[2].send("claim")
            assert receive(channels[2])["event"] == "running"
            channels[2].send("release")
            assert receive(channels[2])["event"] == "completed"
            for worker in workers:
                worker.join(timeout=5)
                assert worker.exitcode == 0
            for request in requests:
                assert client.request("execution.status", {"execution_id": request.execution_id, "actor": "isolated-owner"})["status"] == "completed"
            record_property("owner_evidence", json.dumps({"processes": [worker.pid for worker in workers], "parent_pid": os.getpid(), "logical_active_worker_pools": 1, "events": received, "capacity_change": [2, 1], "provider_executions": 0, "all_completed": True}))
        finally:
            for worker in workers:
                if worker.is_alive():
                    worker.terminate()
                worker.join(timeout=5)
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        assert rejected == []
