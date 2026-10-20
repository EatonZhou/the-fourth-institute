""":importance: P2"""
from __future__ import annotations

from tests.cases.execution_test_support import run_worker_once

import hashlib
import json
import os
from tests.cases.database_test_support import connect
from tests.cases import capability_contracts
import threading
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import get_type_hints
from unittest import mock

import pytest

pytestmark = pytest.mark.usefixtures("registered_execution_defaults")

from management_common.json_columns import json_value
from management_common.portal_database import portal_database

from management_portal.agent_prompt_context import (
    portal_system_instructions,
)
from management_portal.codex_app_server_client import (
    CodexCompactionInProgress,
    CodexThreadWriterBusy,
    CodexTurnConnectionLost,
    CodexTurnContinuationRequired,
    CodexTurnInterrupted,
    CodexTurnUsageLimitExceeded,
    CodexTurnRetryableFailure,
)
from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.execution.agent_data_projection import AgentDataProjector
from management_portal.execution import backends as execution_backends
from management_portal.execution.backends import (
    BackendInvocation,
    BackendResult,
)
from management_portal.execution.contracts import (
    ExecutionInputControl,
    ExecutionTurnSettingsControl,
    ExecutionRecoveryDeferred,
    ExecutionRequest,
    ExecutionResult,
    ExecutionServiceHealth,
    ExecutionSnapshot,
    InputMedia,
    SessionOptions,
)
from management_portal.execution.harness import (
    AgentHarness,
    AgentLoopProviderEventProcessor,
    ProviderErrorRecoveryPolicy,
)
from management_portal.execution.runtime import (
    ExecutionRuntime,
    RuntimeContext,
)
from management_portal.execution.native_options import CodexOptions
from management_portal.execution.scheduler import (
    Scheduler,
    SchedulerClient,
)
from management_portal.execution.service import (
    ExecutionPreparation,
    ExecutionService,
    ExecutionServiceServer,
)
from management_portal.execution.session_manager import SessionManager
from management_portal.execution.session_manager import RuntimeSession
from management_portal.execution.transport import (
    ExecutionClaimOwnershipLost,
    ExecutionServiceClient,
    ExecutionServiceUnavailable,
)
from management_portal.execution.worker import (
    ExecutionWorker,
    ExecutionWorkerDaemon,
)


@pytest.fixture
def recorded_codex_input(monkeypatch):
    """Recovery-policy cases start after Codex has recorded the original input.

    Delivery absence/uncertainty and resend behavior are exercised separately
    against real rollout fixtures in test_codex_input_recovery.py.
    """
    monkeypatch.setattr(
        execution_backends, "read_codex_turn_input_recorded", lambda *_args, **_kwargs: True,
    )


def _bind_session(
    manager: SessionManager,
    binding_id: str,
    provider: str,
    backend_session_id: str,
    *,
    backend_route_id: str = "",
) -> None:
    session = manager.agent_data.ensure_provider_session(
        provider=provider,
        backend_route_id=backend_route_id,
        backend_session_id=backend_session_id,
        agent_id=binding_id,
        actor_ref="test",
    )
    manager.agent_data.bind_provider_session(
        binding_id,
        session["provider_session_ref"],
    )


class TestAgentProvider:
    provider_id = "codex"

    def execute(self, invocation, context):
        request = invocation.request
        shared_path = Path(str(request.metadata["shared_resource_path"]))
        shared_bytes = shared_path.read_bytes()
        context.emit(
            "provider_output",
            {"text": request.prompt.strip(), "final": True},
        )
        return BackendResult(
            output={
                "message": request.prompt.strip(),
                "layers": [
                    "ExecutionService",
                    "Scheduler",
                    "ExecutionWorker",
                    "ExecutionRuntime",
                    "SessionManager",
                    "TestAgentProvider",
                ],
                "shared_resource": {
                    "sha256": hashlib.sha256(shared_bytes).hexdigest(),
                },
            }
        )

    def interrupt(self, **_values):
        return True


@pytest.fixture
def no_continue_pause(monkeypatch):
    """Continue at once: these tests are about whether a turn continues, not when."""
    monkeypatch.setattr(
        "management_portal.execution.backends.wait_before_continuation",
        lambda _policy, context: not context.is_cancelled(),
    )


def test_codex_runtime_options_leave_backend_defaults_implicit_contracts(subtests, isolated_portal_case):
    """codex 运行选项只含显式设置，执行请求往返保留输入媒体，绑定不被其他 agent 复用

    :module: resident.fork-handoff
    :importance: P1
    """
    with subtests.test(contract='test_codex_runtime_options_leave_backend_defaults_implicit'), isolated_portal_case() as case_path:
        assert CodexOptions.runtime_options({}) == {}
    with subtests.test(contract='test_codex_runtime_options_preserve_explicit_collaboration_settings'), isolated_portal_case() as case_path:
        assert CodexOptions.runtime_options(
            {
                "model": "gpt-5.6-sol",
                "reasoning_effort": "high",
                "collaboration_mode": "plan",
            }
        ) == {
            "model": "gpt-5.6-sol",
            "effort": "high",
            "collaboration_mode": "plan",
        }
    with subtests.test(contract='test_execution_request_round_trips_provider_neutral_input_media'), isolated_portal_case() as case_path:
        request = ExecutionRequest.new(
            actor="sample_admin",
            prompt="inspect the attached image",
            service_type="agent_session",
            provider="codex",
            operation="turn",
            input_media=(
                InputMedia(
                    media_type="image",
                    source_type="local_path",
                    source="/tmp/probe.png",
                    mime_type="image/png",
                    name="probe.png",
                ),
                InputMedia(
                    media_type="image",
                    source_type="url",
                    source="https://example.test/probe.jpg",
                    mime_type="image/jpeg",
                ),
            ),
        )

        payload = request.to_payload()
        restored = ExecutionRequest.from_payload(payload)

        assert payload["schema"] == "tfi.execution-request.v8"
        assert payload["schema_version"] == 8
        assert payload["input_media"][0]["source"] == "/tmp/probe.png"
        assert restored.input_media == request.input_media
    with subtests.test(contract='test_execution_request_requires_current_input_media_field'), isolated_portal_case() as case_path:
        request = ExecutionRequest.new(
            actor="sample_admin",
            prompt="continue",
            service_type="agent_session",
            provider="codex",
            operation="turn",
        )
        payload = request.to_payload()
        payload.pop("input_media")

        with pytest.raises(ValueError, match="input media must be an array"):
            ExecutionRequest.from_payload(payload)
    with subtests.test(contract='test_session_manager_rejects_existing_binding_for_another_agent'), isolated_portal_case() as case_path:
        manager = SessionManager()
        binding_id = "resident:resident_111111111111"
        _bind_session(manager, binding_id, "codex", "thread-original")
        binding = manager.provider_binding(binding_id)
        manager.agent_data.begin_execution(
            execution_id="execution-agent-binding-owner",
            agent_id="resident_111111111111",
            actor_ref="sample_admin",
            provider="codex",
            started_at="2026-08-26T00:00:00Z",
        )
        manager.agent_data.bind_execution_provider_session(
            "execution-agent-binding-owner",
            provider_session_ref=binding["provider_session_ref"],
        )
        request = ExecutionRequest.new(
            actor="sample_admin",
            agent_id="resident_222222222222",
            prompt="continue",
            service_type="agent_session",
            provider="codex",
            operation="turn",
            session=SessionOptions(mode="persistent", binding_id=binding_id),
            system_instructions=portal_system_instructions("resident"),
        )
        with pytest.raises(ValueError, match="another agent"):
            manager.prepare(request)
    with subtests.test(contract='test_execution_cancel_and_heartbeat_keep_short_rpc_budgets'), isolated_portal_case() as case_path:
        client = mock.Mock()
        client.request.return_value = {}
        ExecutionService(client=client).cancel("execution-budget", actor="test")
        assert client.request.call_args.kwargs["timeout"] == 5.0
        scheduler = SchedulerClient(
            worker_id="worker-budget", build_id="test", capabilities=["codex"],
            service_client=client,
        )
        scheduler.heartbeat("execution-budget", "claim-budget")
        assert client.request.call_args.kwargs["timeout"] == 5.0


def test_execution_request_rejects_invalid_input_media(subtests, isolated_portal_case):
    """执行请求拒绝相对路径、非 HTTP 地址或 MIME 类型不符的输入媒体"""
    for media, error in (
        (
            InputMedia("image", "local_path", "relative.png"),
            "must be absolute",
        ),
        (
            InputMedia("image", "url", "file:///tmp/probe.png"),
            "HTTP",
        ),
        (
            InputMedia("image", "local_path", "/tmp/probe.png", "text/plain"),
            "does not match",
        ),
    ):
        with subtests.test(media=media, error=error), isolated_portal_case() as case_path:
            with pytest.raises(ValueError, match=error):
                ExecutionRequest.new(
                    actor="sample_admin",
                    prompt="inspect",
                    service_type="agent_session",
                    provider="codex",
                    operation="turn",
                    input_media=(media,),
                )


def test_session_manager_rejects_backend_session_replacement_contracts(tmp_path, subtests, isolated_portal_case, monkeypatch):
    """已绑定的后端会话不可替换，release 可删除绑定，容量设置重启后仍保留

    :module: resident.fork-handoff
    :importance: P1
    """
    with subtests.test(contract='test_session_manager_rejects_backend_session_replacement'), isolated_portal_case() as case_path:
        tmp_path = case_path
        manager = SessionManager()
        binding_id = "resident:resident_111111111111"
        _bind_session(manager, binding_id, "codex", "thread-original")
        request = ExecutionRequest.new(
            actor="sample_admin",
            prompt="continue",
            service_type="agent_session",
            provider="codex",
            operation="turn",
            session=SessionOptions(mode="persistent", binding_id=binding_id),
        )
        session = manager.prepare(request)

        with pytest.raises(
            ValueError,
            match="immutable",
        ):
            manager.complete(request, session, "thread-replacement")

        assert manager.provider_binding(binding_id)["backend_session_id"] == (
            "thread-original"
        )
    with subtests.test(contract='test_session_manager_release_can_remove_provider_binding_after_cleanup'), isolated_portal_case() as case_path:
        tmp_path = case_path
        store = AgentDataStore()
        manager = SessionManager(agent_data=store)
        binding_id = "resident:resident_111111111111"
        _bind_session(manager, binding_id, "codex", "thread-release")
        request = ExecutionRequest.new(
            actor="sample_admin",
            prompt="release",
            service_type="agent_session",
            provider="codex",
            operation="release",
            runtime_options={"delete_binding_on_release": True},
            session=SessionOptions(mode="persistent", binding_id=binding_id),
        )
        session = manager.prepare(request)

        manager.complete(request, session, "")

        assert manager.provider_binding(binding_id) == {}
    with subtests.test(contract='test_session_manager_accepts_resident_after_synthetic_session_operation'), isolated_portal_case() as case_path:
        tmp_path = case_path
        store = AgentDataStore()
        manager = SessionManager(agent_data=store)
        binding_id = "resident:resident_111111111111"
        _bind_session(manager, binding_id, "codex", "thread-forked")
        binding = manager.provider_binding(binding_id)
        store.begin_execution(
            execution_id="execution-synthetic-session-operation",
            agent_id=binding_id,
            actor_ref="sample_admin",
            provider="codex",
            started_at="2026-08-26T00:00:00Z",
        )
        store.bind_execution_provider_session(
            "execution-synthetic-session-operation",
            provider_session_ref=binding["provider_session_ref"],
        )
        request = ExecutionRequest.new(
            actor="sample_admin",
            agent_id="resident_111111111111",
            prompt="continue",
            service_type="agent_session",
            provider="codex",
            operation="turn",
            session=SessionOptions(mode="persistent", binding_id=binding_id),
            system_instructions=portal_system_instructions("resident"),
        )

        session = manager.prepare(request)

        assert session.backend_session_id == "thread-forked"
    with subtests.test(contract='test_session_manager_persists_registered_harness_tool_schema'), isolated_portal_case() as case_path:
        tmp_path = case_path
        manager = SessionManager()
        binding_id = "resident:resident_111111111111"
        new_request = ExecutionRequest.new(
            actor="sample_admin",
            prompt="start",
            service_type="agent_session",
            provider="codex",
            operation="turn",
            session=SessionOptions(mode="persistent", binding_id=binding_id),
        )
        new_session = manager.prepare(new_request)

        manager.complete(
            new_request,
            new_session,
            "thread-with-harness-tools",
            backend_route_id="zero",
            harness_tool_names=frozenset({
                "read_harness_artifact",
                "search_harness_artifact",
            }),
        )

        continued = manager.prepare(ExecutionRequest.new(
            actor="sample_admin",
            prompt="continue",
            service_type="agent_session",
            provider="codex",
            operation="turn",
            session=SessionOptions(mode="persistent", binding_id=binding_id),
        ))
        assert continued.harness_tool_names == (
            "read_harness_artifact",
            "search_harness_artifact",
        )
    with subtests.test(contract='test_scheduler_rejects_noncurrent_store_without_rewriting_it'), isolated_portal_case() as case_path:
        tmp_path = case_path
        with monkeypatch.context() as isolated:
            isolated.setenv(
                "PORTAL_DATABASE_NAMESPACE",
                f"{os.environ['PORTAL_DATABASE_NAMESPACE'][:32]}_ec",
            )
            database = portal_database(schema="execution_runtime")
            with connect(database) as connection:
                connection.execute(
                    "CREATE TABLE executions (execution_id TEXT PRIMARY KEY)"
                )

            with pytest.raises(RuntimeError, match="current contract"):
                Scheduler()

            with connect(database) as connection:
                tables = {
                    str(row[0])
                    for row in connection.execute(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema=current_schema()"
                    )
                }
            assert tables == {"executions"}
            database.close()
    with subtests.test(contract='test_scheduler_capacity_setting_is_live_and_persistent'), isolated_portal_case() as case_path:
        tmp_path = case_path
        scheduler = Scheduler(
            capacity_limits={"codex:zero": 10},
        )

        setting = scheduler.configure_capacity(
            "codex:zero",
            12,
            3,
            7,
            actor="admin",
        )

        assert setting["limit"] == 12
        assert setting["dispatch_interval_min_seconds"] == 3
        assert setting["dispatch_interval_max_seconds"] == 7
        partition = scheduler.health()["capacity_partitions"]["codex:zero"]
        assert partition["limit"] == 12
        assert partition["dispatch_interval_min_seconds"] == 3
        assert partition["dispatch_interval_max_seconds"] == 7
        with pytest.raises(ValueError, match="minimum cannot exceed maximum"):
            scheduler.configure_capacity("codex:zero", 15, 9, 4, actor="admin")
        assert scheduler.health()["capacity_partitions"]["codex:zero"]["limit"] == 12
        restarted = Scheduler(
            capacity_limits={"codex:zero": 10},
        )
        restarted_partition = restarted.health()["capacity_partitions"]["codex:zero"]
        assert restarted_partition["limit"] == 12
        assert restarted_partition["dispatch_interval_min_seconds"] == 3
        assert restarted_partition["dispatch_interval_max_seconds"] == 7


def test_scheduler_capacity_setting_releases_an_obsolete_dispatch_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """重新配置容量时按新派发间隔释放旧的等待窗口，下一条执行可立即认领"""
    now = 1_000.0
    monkeypatch.setattr(
        "management_portal.execution.scheduler.time.time",
        lambda: now,
    )
    scheduler = Scheduler(
        capacity_limits={"codex:zero": 10},
        dispatch_interval_min_seconds=3_600,
        dispatch_interval_max_seconds=3_600,
    )
    worker_id = "dev:capacity-reconfiguration-worker"
    capabilities = ["agent_session:codex"]
    scheduler.register_worker(
        worker_id=worker_id,
        build_id="test-build",
        capabilities=capabilities,
    )
    requests = [
        ExecutionRequest.new(
            actor=f"resident-{index}",
            prompt=f"message-{index}",
            service_type="agent_session",
            provider="codex",
            operation="turn",
            session=SessionOptions(
                mode="persistent",
                binding_id=f"resident:capacity-{index}",
                backend_route_id="zero",
            ),
        )
        for index in range(2)
    ]
    for request in requests:
        scheduler.submit(request)

    first = scheduler.claim(
        worker_id=worker_id,
        capabilities=capabilities,
    )
    assert first["claimed"] is True
    assert scheduler.claim(
        worker_id=worker_id,
        capabilities=capabilities,
    ) == {"claimed": False, "reason": "dispatch_interval"}

    now = 2_000.0
    scheduler.configure_capacity(
        "codex:zero",
        10,
        2,
        6,
        actor="admin",
    )

    partition = scheduler.health()["capacity_partitions"]["codex:zero"]
    dispatch = scheduler.health()["dispatch_interval"]["partitions"][
        "codex:zero"
    ]
    assert partition["limit"] == 10
    assert 2 <= dispatch["interval_seconds"] <= 6
    assert dispatch["remaining_seconds"] == 0
    scheduler.register_worker(
        worker_id=worker_id,
        build_id="test-build",
        capabilities=capabilities,
    )
    second = scheduler.claim(
        worker_id=worker_id,
        capabilities=capabilities,
    )
    assert second["claimed"] is True
    assert second["execution"]["execution_id"] == requests[1].execution_id


def test_scheduler_event_cursor_advances_across_service_replacement_contracts(tmp_path, subtests, isolated_portal_case):
    """替换后的调度服务事件序号继续递增，Dev 与 Release 共用一个无部署闸门的队列

    :module: resident.fork-handoff
    :importance: P1
    """
    with subtests.test(contract='test_scheduler_event_cursor_advances_across_service_replacement'), isolated_portal_case() as case_path:
        tmp_path = case_path
        first = Scheduler()
        with first._condition:
            first_event = first._event(None, "execution-one", "provider_event")

        replacement = Scheduler()
        with replacement._condition:
            replacement_event = replacement._event(
                None,
                "execution-one",
                "provider_event",
            )

        assert replacement_event["sequence"] > first_event["sequence"]
    with subtests.test(contract='test_execution_has_one_queue_without_deployment_gates'), isolated_portal_case() as case_path:
        tmp_path = case_path
        scheduler, server, thread, client, service = _stack(tmp_path)
        try:
            request = ExecutionRequest.new(
                actor="sample_admin",
                prompt="continue resident work during release rollout",
                service_type="agent_session",
                provider="codex",
                operation="turn",
            )

            submitted = service.submit(request)
            health = service.health()

            assert submitted["status"] == "queued"
            assert "queue_namespace" not in submitted["request"]
            assert "namespace" not in submitted["scheduler"]
            assert "admission_open" not in health

            release_worker = SchedulerClient(
                worker_id="release:worker",
                build_id="release-build",
                capabilities=["agent_session:codex"],
                service_client=client,
            )
            release_worker.register()
            claim = release_worker.claim(wait_seconds=0.1)
            assert claim["claimed"] is True
            assert claim["execution"]["execution_id"] == request.execution_id
        finally:
            _close(server, thread)
    with subtests.test(contract='test_execution_client_uses_the_other_live_instance_endpoint'), isolated_portal_case() as case_path:
        tmp_path = case_path
        _scheduler, server, thread, client, _service = _stack(tmp_path)
        try:
            client.socket_paths = (
                tmp_path / "missing-execution-service.sock",
                client.socket_path,
            )

            assert client.health()["ok"] is True
        finally:
            _close(server, thread)
    with subtests.test(contract='test_execution_service_configures_codex_account_scheduler'), isolated_portal_case() as case_path:
        tmp_path = case_path
        scheduler, server, thread, _client, service = _stack(tmp_path)
        try:
            result = service.configure_account_scheduler(
                "zero",
                14,
                4,
                9,
                actor="admin",
            )

            assert result["account_id"] == "codex_zero"
            assert result["max_concurrency"] == 14
            assert result["dispatch_interval_min_seconds"] == 4
            assert result["dispatch_interval_max_seconds"] == 9
            partition = scheduler.health()["capacity_partitions"]["codex:zero"]
            assert partition["limit"] == 14
            assert partition["dispatch_interval_min_seconds"] == 4
            assert partition["dispatch_interval_max_seconds"] == 9
        finally:
            _close(server, thread)
    with subtests.test(contract='test_execution_service_owns_runtime_catalog'), isolated_portal_case() as case_path:
        tmp_path = case_path
        _scheduler, server, thread, _client, service = _stack(tmp_path)
        try:
            with mock.patch(
                "management_portal.codex_capabilities.runtime_catalog",
                return_value={"models": ["gpt-test"]},
            ):
                catalog = service.runtime_catalog()
                assert catalog["models"] == ["gpt-test"]
                assert catalog["schema"] == "tfi.agent-runtime-catalog.v1"
                assert catalog["account_id"] == "codex_one"
        finally:
            _close(server, thread)
    with subtests.test(contract='test_codex_backend_rejects_conflicting_sandbox_options'), isolated_portal_case() as case_path:
        tmp_path = case_path
        request = ExecutionRequest.new(
            actor="sample_admin",
            prompt="author graph",
            service_type="agent_session",
            provider="codex",
            operation="turn",
            cwd=str(tmp_path),
            runtime_options={
                "sandbox": "workspace-write",
                "sandbox_policy": {"type": "workspaceWrite"},
            },
            session=SessionOptions(mode="persistent", binding_id="sandbox:conflict"),
            system_instructions=portal_system_instructions("general"),
        )
        session = RuntimeSession(
            mode="persistent",
            binding_id=request.session.binding_id,
            backend_session_id="",
            backend_route_id="",
            is_new_backend_session=True,
        )

        with pytest.raises(
            ValueError,
            match="cannot combine sandbox and sandbox_policy",
        ):
            execution_backends.CodexBackend(
                client_factory=lambda _socket_path: mock.Mock(),
            ).execute(
                BackendInvocation(
                    request=request,
                    session=session,
                    user_prompt=request.prompt,
                    system_context="rules",
                    attach_system_context=True,
                    is_new_backend_session=True,
                ),
                execution_backends.BackendContext(
                    emit_event=lambda *_args: None,
                    cancellation_requested=lambda: False,
                    set_interrupt_target=lambda *_args: None,
                ),
            )


@pytest.mark.parametrize("client_timeout,request_timeout,method,connect_timeout,response_timeout", [
    (None, None, "scheduler.checkpoint", 5.0, 30.0),
    (None, 1.25, "execution.wait", 1.25, 1.25),
    (2.0, None, "scheduler.event", 2.0, 2.0),
    (None, None, "health", 5.0, 5.0),
    (2.0, None, "health", 2.0, 2.0),
    (None, 60.0, "scheduler.checkpoint", 5.0, 60.0),
])
def test_execution_rpc_timeout_budgets(
    tmp_path: Path,
    client_timeout: float | None,
    request_timeout: float | None,
    method: str,
    connect_timeout: float,
    response_timeout: float,
) -> None:
    """执行服务 RPC 按方法和调用方设置分别使用连接超时与响应超时"""
    options = {"timeout": client_timeout} if client_timeout is not None else {}
    from management_common.rpc_auth import RpcAuthenticator
    RpcAuthenticator(tmp_path / "budgets.sock")
    client = ExecutionServiceClient(tmp_path / "budgets.sock", **options)
    connection = mock.Mock()

    def response():
        request = json.loads(connection.sendall.call_args.args[0])
        return json.dumps({"id": request["id"], "ok": True, "result": {"ok": True}}).encode()

    connection.makefile.return_value.readline.side_effect = response
    with mock.patch("management_portal.execution.transport.socket.socket", return_value=connection):
        result = client.health() if method == "health" else client.request(method, timeout=request_timeout)
    assert result == {"ok": True}
    assert connection.settimeout.call_args_list == [
        mock.call(connect_timeout), mock.call(response_timeout),
    ]
    connection.close.assert_called_once()


def _stack(tmp_path: Path):
    socket_path = tmp_path / "execution-service.sock"
    scheduler = Scheduler(
        max_concurrency=2,
        dispatch_interval_min_seconds=0,
        dispatch_interval_max_seconds=0,
    )
    server = ExecutionServiceServer(
        socket_path,
        scheduler,
        instance="explore-1",
        build_id="test-build",
        preparation=ExecutionPreparation(),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    assert server.started.wait(1)
    client = ExecutionServiceClient(socket_path, timeout=2)
    service = ExecutionService(client=client)
    return scheduler, server, thread, client, service


def _close(server: ExecutionServiceServer, thread: threading.Thread) -> None:
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def test_execution_service_query_contracts_preserve_wire_payloads_contracts(subtests, isolated_portal_case):
    """执行服务查询原样返回线上载荷，worker、wait 和 submit 在控制面短暂不可用时重试

    :module: resident.fork-handoff
    :importance: P1
    """
    with subtests.test(contract='test_execution_service_query_contracts_preserve_wire_payloads'), isolated_portal_case() as case_path:
        client = mock.Mock()
        health = {
            "ok": True,
            "schema": "archive.execution-service.v1",
            "instance": "dev",
            "build_id": "test-build",
            "scheduler": {"active_count": 1},
            "notifications": {"ok": True},
            "agent_data": {"ok": True},
        }
        snapshot = {
            "execution_id": "execution-000000000000000000000000",
            "actor": "sample_admin",
            "status": "running",
            "request": {"actor": "sample_admin"},
            "events": [],
            "result": {},
            "error": "",
            "scheduler": {"attempts": 1},
        }
        client.health.return_value = health
        client.request.side_effect = [snapshot, [snapshot]]
        service = ExecutionService(client=client)

        assert service.health() is health
        assert service.status(snapshot["execution_id"]) is snapshot
        assert ExecutionServiceHealth.__required_keys__ == frozenset(health)
        assert ExecutionSnapshot.__required_keys__ == frozenset(snapshot)
        assert get_type_hints(ExecutionService.health)["return"] is (
            ExecutionServiceHealth
        )
        assert get_type_hints(ExecutionService.status)["return"] is (
            ExecutionSnapshot
        )
    with subtests.test(contract='test_execution_worker_retries_transient_control_plane_outage'), isolated_portal_case() as case_path:
        daemon = ExecutionWorkerDaemon.__new__(ExecutionWorkerDaemon)
        daemon._service_error = ""
        with mock.patch.object(
            daemon,
            "_fill_slots",
            side_effect=ExecutionServiceUnavailable("temporary outage"),
        ):
            assert daemon._fill_slots_resilient() is False
        assert "temporary outage" in daemon._service_error

        with mock.patch.object(
            daemon,
            "_fill_slots",
            return_value=True,
        ):
            assert daemon._fill_slots_resilient() is True
        assert daemon._service_error == ""
    with subtests.test(contract='test_execution_worker_leaves_when_retired_and_waits_while_standby'), isolated_portal_case() as case_path:
        def daemon_claiming(claim: dict) -> ExecutionWorkerDaemon:
            daemon = ExecutionWorkerDaemon.__new__(ExecutionWorkerDaemon)
            daemon.worker_id = "dev:execution-worker:test"
            daemon.max_parallel = 1
            daemon._active = {}
            daemon._active_lock = threading.RLock()
            daemon._active_changed = threading.Condition(daemon._active_lock)
            daemon._stopping = threading.Event()
            daemon._draining_started_at = 0.0
            daemon._retired = False
            daemon.worker = mock.Mock()
            daemon.worker.claim.return_value = claim
            return daemon

        standby = daemon_claiming(
            {"claimed": False, "reason": "worker_not_active", "worker_state": "standby"}
        )
        with mock.patch.object(standby._stopping, "wait") as waited:
            assert standby._fill_slots() is False
        waited.assert_called_once_with(1.0)
        assert not standby._stopping.is_set()
        assert standby._retired is False

        # Retired by another generation, or already removed from the
        # registry: hand over and exit instead of asking again forever.
        for claim in (
            {"claimed": False, "reason": "worker_not_active", "worker_state": "draining"},
            {"claimed": False, "reason": "worker_not_registered"},
        ):
            retired = daemon_claiming(claim)
            assert retired._fill_slots() is False
            assert retired._stopping.is_set()
            assert retired._retired is True
    with subtests.test(contract='test_execution_worker_stale_claim_cannot_remove_replacement_activity'), isolated_portal_case() as case_path:
        daemon = ExecutionWorkerDaemon.__new__(ExecutionWorkerDaemon)
        daemon.worker = mock.Mock()
        daemon.worker.execute_claim.return_value = {"status": "claim_lost"}
        daemon._active_lock = threading.RLock()
        daemon._active_changed = threading.Condition(daemon._active_lock)
        daemon._write_health = mock.Mock()
        execution_id = "execution-reclaimed"
        old_marker = object()
        replacement_marker = object()
        daemon._active = {
            execution_id: {
                "_claim_marker": replacement_marker,
                "execution_id": execution_id,
            }
        }
        claim = {"execution": {"execution_id": execution_id}}

        daemon._execute(claim, old_marker)

        assert daemon._active[execution_id]["_claim_marker"] is replacement_marker

        daemon._execute(claim, replacement_marker)

        assert execution_id not in daemon._active
    with subtests.test(contract='test_execution_wait_retries_transient_control_plane_outage'), isolated_portal_case() as case_path:
        client = mock.Mock()
        client.request.side_effect = [
            ExecutionServiceUnavailable("service socket replaced"),
            {
                "status": "running",
                "scheduler": {"updated_at": "2026-08-10T07:42:47Z"},
                "events": [{"sequence": 4, "type": "provider_output"}],
            },
            {
                "status": "completed",
                "scheduler": {"updated_at": "2026-08-10T07:42:48Z"},
                "events": [{"sequence": 5, "type": "completed"}],
                "result": {"status": "completed", "output": {"message": "done"}},
            },
        ]
        snapshots: list[str] = []
        events: list[int] = []

        with mock.patch("management_portal.execution.service.time.sleep") as sleep:
            completed = ExecutionService(
                client=client,
            ).wait(
                "execution-restart-safe",
                actor="sample_admin",
                on_snapshot=lambda item: snapshots.append(str(item.get("status") or "")),
                on_events=lambda items: events.extend(
                    int(item.get("sequence") or 0) for item in items
                ),
            )

        assert completed["status"] == "completed"
        assert snapshots == ["running", "completed"]
        assert events == [4, 5]
        assert client.request.call_count == 3
        sleep.assert_called_once_with(0.25)
    with subtests.test(contract='test_execution_wait_rechecks_terminal_snapshot_without_result'), isolated_portal_case() as case_path:
        execution_id = "execution-stale-terminal-projection"
        client = mock.Mock()
        client.request.side_effect = [
            {
                "execution_id": execution_id,
                "status": "completed",
                "scheduler": {"updated_at": "2026-09-05T00:55:53.100Z"},
                "events": [],
                "result": {},
            },
            {
                "execution_id": execution_id,
                "status": "running",
                "scheduler": {"updated_at": "2026-09-05T00:55:53.200Z"},
                "events": [],
                "result": {},
            },
            {
                "execution_id": execution_id,
                "status": "completed",
                "scheduler": {"updated_at": "2026-09-05T00:55:54.000Z"},
                "events": [{"sequence": 1, "type": "completed"}],
                "result": {
                    "execution_id": execution_id,
                    "status": "completed",
                    "output": {"message": "done"},
                },
            },
        ]
        projected: list[str] = []

        completed = ExecutionService(client=client).wait(
            execution_id,
            actor="sample_admin",
            on_snapshot=lambda item: projected.append(
                str(item.get("status") or "")
            ),
        )

        assert completed["result"]["output"]["message"] == "done"
        assert projected == ["running", "completed"]
        assert [call.args[0] for call in client.request.call_args_list] == [
            "execution.wait",
            "execution.status",
            "execution.wait",
        ]
    with subtests.test(contract='test_execution_submit_retries_transient_control_plane_outage'), isolated_portal_case() as case_path:
        client = mock.Mock()
        request = ExecutionRequest.new(
            actor="sample_admin",
            prompt="continue",
            service_type="agent_session",
            provider="codex",
            operation="turn",
        )
        client.request.side_effect = [
            ExecutionServiceUnavailable("dev socket replaced"),
            ExecutionServiceUnavailable("release socket not ready"),
            {
                "execution_id": request.execution_id,
                "status": "queued",
            },
        ]

        with mock.patch("management_portal.execution.service.time.sleep") as sleep:
            submitted = ExecutionService(client=client).submit(request)

        assert submitted["execution_id"] == request.execution_id
        assert client.request.call_count == 3
        assert sleep.call_count == 2


def test_dev_and_release_endpoints_share_one_execution_queue(
    tmp_path: Path,
) -> None:
    """Dev 与 Release 执行服务端点共用一个队列，重复提交得到同一执行"""
    agent_data = AgentDataStore()
    dev_server = ExecutionServiceServer(
        tmp_path / "dev.sock",
        Scheduler(),
        instance="dev",
        build_id="dev-build",
        preparation=ExecutionPreparation(),
        agent_data=agent_data,
    )
    release_server = ExecutionServiceServer(
        tmp_path / "release.sock",
        Scheduler(),
        instance="release",
        build_id="release-build",
        preparation=ExecutionPreparation(),
        agent_data=agent_data,
    )
    dev_thread = threading.Thread(target=dev_server.serve_forever, daemon=True)
    release_thread = threading.Thread(
        target=release_server.serve_forever,
        daemon=True,
    )
    dev_thread.start()
    release_thread.start()
    assert dev_server.started.wait(1)
    assert release_server.started.wait(1)
    dev_client = ExecutionServiceClient(tmp_path / "dev.sock", timeout=2)
    release_client = ExecutionServiceClient(tmp_path / "release.sock", timeout=2)
    try:
        request = ExecutionRequest.new(
            actor="sample_admin",
            prompt="continue across a release rollout",
            service_type="agent_session",
            provider="codex",
            operation="turn",
        )
        submitted = ExecutionService(client=dev_client).submit(request)
        resubmitted = ExecutionService(client=release_client).submit(request)
        observed = ExecutionService(client=release_client).status(
            request.execution_id,
            actor="sample_admin",
        )
        release_worker = SchedulerClient(
            worker_id="release:worker",
            build_id="release-build",
            capabilities=["agent_session:codex"],
            service_client=release_client,
        )
        release_worker.register()
        claim = release_worker.claim(wait_seconds=0.1)

        assert submitted["execution_id"] == request.execution_id
        assert resubmitted["execution_id"] == request.execution_id
        assert observed["execution_id"] == request.execution_id
        assert claim["claimed"] is True
        assert claim["execution"]["execution_id"] == request.execution_id
    finally:
        _close(release_server, release_thread)
        _close(dev_server, dev_thread)


def test_execution_activity_notification_keeps_request_actor(
    tmp_path: Path,
) -> None:
    """执行活动投影和活动通知都带上请求的 actor"""
    _scheduler, server, thread, client, service = _stack(tmp_path)
    publisher = mock.Mock()
    server.notification_publisher = publisher
    worker = SchedulerClient(
        worker_id="activity-worker",
        build_id="test-build",
        capabilities=["agent_session:codex"],
        service_client=client,
    )
    try:
        worker.register()
        request = ExecutionRequest.new(
            actor="sample_admin",
            prompt="activity actor",
            service_type="agent_session",
            provider="codex",
            operation="turn",
        )
        service.submit(request)
        claim = worker.claim(wait_seconds=0.1)

        activity_path = tmp_path / "activity.jsonl"
        with (
            mock.patch(
                "management_portal.execution.service.execution_activity_path",
                return_value=activity_path,
            ) as resolve_activity_path,
            mock.patch(
                "management_portal.execution.service.append_execution_activity",
            ) as append_activity,
        ):
            client.request(
                "scheduler.event",
                {
                    "execution_id": request.execution_id,
                    "worker_id": "activity-worker",
                    "claim_token": claim["claim_token"],
                    "event_type": "provider_output",
                    "details": {"text": "transient"},
                },
            )

            resolve_activity_path.assert_called_once_with(
                request.execution_id,
            )
        projected_path, projected_events = append_activity.call_args.args
        assert projected_path == activity_path
        assert len(projected_events) == 1
        assert projected_events[0]["execution_id"] == request.execution_id
        assert projected_events[0]["sequence"] > 0
        assert projected_events[0]["type"] == "provider_output"
        assert projected_events[0]["details"] == {"text": "transient"}
        assert projected_events[0]["actor"] == "sample_admin"

        subject, payload = publisher.publish.call_args.args
        assert subject == f"portal.execution.activity.{request.execution_id}"
        assert payload == {
            "kind": "activity",
            "type": "provider_output",
            "actor": "sample_admin",
        }
    finally:
        _close(server, thread)


def test_provider_activity_is_redacted_before_scheduler_and_trace_persistence(
    tmp_path: Path,
) -> None:
    """Provider 活动中的令牌、Cookie 和密钥在写入调度器和活动记录前被遮蔽"""
    _scheduler, server, thread, client, service = _stack(tmp_path)
    worker = SchedulerClient(
        worker_id="activity-redaction-worker",
        build_id="test-build",
        capabilities=["agent_session:codex"],
        service_client=client,
    )
    synthetic_secret = "sentinel-activity-secret"
    try:
        worker.register()
        request = ExecutionRequest.new(
            actor="sample_admin",
            prompt="activity redaction",
            service_type="agent_session",
            provider="codex",
            operation="turn",
        )
        service.submit(request)
        claim = worker.claim(wait_seconds=0.1)

        with mock.patch(
            "management_portal.execution.service.append_execution_activity",
        ) as append_activity:
            client.request(
                "scheduler.event",
                {
                    "execution_id": request.execution_id,
                    "worker_id": "activity-redaction-worker",
                    "claim_token": claim["claim_token"],
                    "event_type": "provider_event",
                    "details": {
                        "method": "guardianWarning",
                        "params": {
                            "Authorization": f"Bearer {synthetic_secret}",
                            "headers": {
                                "Set-Cookie": f"session={synthetic_secret}",
                                "X-API-Key": synthetic_secret,
                                "X-Request-ID": "request-safe",
                            },
                            "items": [{"clientSecret": synthetic_secret}],
                            "error": {
                                "additionalDetails": (
                                    f"token={synthetic_secret}"
                                ),
                            },
                        },
                    },
                },
            )

        projected_events = append_activity.call_args.args[1]
        snapshot = service.status(request.execution_id, actor="sample_admin")
        persisted_json = json.dumps(projected_events, sort_keys=True)
        status_json = json.dumps(snapshot["events"], sort_keys=True)
        assert synthetic_secret not in persisted_json
        assert synthetic_secret not in status_json
        assert "[REDACTED]" in persisted_json
        assert "[REDACTED]" in status_json
        assert "request-safe" in persisted_json
        assert "request-safe" in status_json
    finally:
        _close(server, thread)




def test_execution_service_scheduler_worker_runtime_vertical_slice(
    tmp_path: Path,
) -> None:
    """一次执行贯穿服务、调度器、worker、运行时和 Provider 各层，事件顺序完整

    :design: decision_6ad0384e020d
    :design-point:
    """
    scheduler, server, thread, client, service = _stack(tmp_path)
    shared_resource = tmp_path / "shared-summary.md"
    shared_resource.write_text("# Shared Portal resource\n", encoding="utf-8")
    runtime = ExecutionRuntime(
        agent_providers={"codex": TestAgentProvider()},
        state_root=tmp_path / "execution-state",
    )
    scheduler_client = SchedulerClient(
        worker_id="explore-1:execution-worker",
        build_id="test-build",
        capabilities=runtime.capabilities(),
        pid=1234,
        service_client=client,
    )
    worker = ExecutionWorker(
        build_id="test-build",
        worker_id="explore-1:execution-worker",
        scheduler=scheduler_client,
        runtime=runtime,
        state_root=tmp_path / "execution-state",
    )
    try:
        worker.register()
        submitted = service.submit(ExecutionRequest.new(
            actor="sample_admin",
            prompt="validate execution layer",
            service_type="agent_session",
            provider="codex",
            operation="turn",
            correlation_id="test:vertical-slice",
            cwd="/portal/explore-1",
            session=SessionOptions(
                mode="stateless",
            ),
            metadata={
                "instance": "explore-1",
                "build_id": "test-build",
                "shared_resource_path": str(shared_resource),
            },
        ))

        execution_id = submitted["execution_id"]
        assert submitted["status"] == "queued"
        assert run_worker_once(worker) is True
        completed = service.status(execution_id, actor="sample_admin")
        assert completed["status"] == "completed"
        compact = service.status(
            execution_id,
            actor="sample_admin",
            include_events=False,
            include_result=False,
        )
        assert compact["status"] == "completed"
        assert compact["events"] == []
        assert compact["result"] == {}
        assert compact["error"] == ""
        assert service.resume(
            execution_id,
            actor="sample_admin",
            correlation_id=submitted["request"]["correlation_id"],
            service_type="agent_session",
            provider="codex",
        )["status"] == "completed"
        service.acknowledge(execution_id, actor="sample_admin")
        resumed_after_acknowledge = service.resume(
            execution_id,
            actor="sample_admin",
            correlation_id="test:vertical-slice",
            service_type="agent_session",
            provider="codex",
        )
        assert resumed_after_acknowledge["status"] == "completed"
        assert resumed_after_acknowledge["request"] == {}
        assert [event["type"] for event in completed["events"]] == [
            "queued",
            "claimed",
            "started",
            "runtime_started",
                "context_prepared",
                "service_started",
                # The Harness plan is kept with the recovery checkpoint
                # before the Provider is called.
                "checkpointed",
                "agent_harness_prepared",
                "provider_output",
                "agent_harness_completed",
                "checkpointed",
                "service_completed",
            "completed",
        ]
        output = completed["result"]["output"]
        assert output["layers"] == [
            "ExecutionService",
            "Scheduler",
            "ExecutionWorker",
            "ExecutionRuntime",
            "SessionManager",
            "TestAgentProvider",
        ]
        assert output["shared_resource"]["sha256"] == hashlib.sha256(
            shared_resource.read_bytes()
        ).hexdigest()
        assert completed["scheduler"]["status"] == "completed"
        assert run_worker_once(worker) is False
        assert scheduler.health()["active_count"] == 0
    finally:
        _close(server, thread)


def test_worker_terminally_fails_a_claimed_nonconforming_request(
    tmp_path: Path,
) -> None:
    """认领到不合规请求时 worker 直接以失败终止，不调用 Provider 也不重试"""
    class BackendMustNotRun:
        provider_id = "codex"

        def execute(self, _invocation, _context):
            raise AssertionError("an invalid request must not reach its Provider")

        def interrupt(self, **_values):
            return True

    scheduler, server, thread, client, service = _stack(tmp_path)
    runtime = ExecutionRuntime(
        agent_providers={"codex": BackendMustNotRun()},
        state_root=tmp_path / "execution-state",
    )
    worker = ExecutionWorker(
        build_id="test-build",
        worker_id="explore-1:invalid-request-worker",
        scheduler=SchedulerClient(
            worker_id="explore-1:invalid-request-worker",
            build_id="test-build",
            capabilities=runtime.capabilities(),
            service_client=client,
        ),
        runtime=runtime,
        state_root=tmp_path / "execution-state",
    )
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="must fail before Provider execution",
        service_type="agent_session",
        provider="codex",
        operation="turn",
    )

    try:
        worker.register()
        service.submit(request)
        with connect(scheduler.database) as connection:
            stored = json_value(connection.execute(
                'SELECT request_json FROM executions WHERE execution_id = %s',
                (request.execution_id,),
            ).fetchone()[0])
            stored["schema"] = "invalid"
            connection.execute(
                'UPDATE executions SET request_json = %s WHERE execution_id = %s',
                (
                    json.dumps(stored, ensure_ascii=False, separators=(",", ":")),
                    request.execution_id,
                ),
            )

        assert run_worker_once(worker) is True
        failed = service.status(request.execution_id, actor="sample_admin")
        assert failed["status"] == "failed"
        assert failed["scheduler"]["attempts"] == 1
        assert failed["scheduler"]["owner_worker_id"] == ""
        assert failed["scheduler"]["lease_expires_at"] == 0
        assert failed["result"]["status"] == "failed"
        assert "invalid execution request schema" in failed["error"]
        assert run_worker_once(worker) is False
    finally:
        _close(server, thread)


def test_session_manager_reuses_a_backend_binding(tmp_path: Path) -> None:
    """同一绑定的第二个轮次复用第一个轮次创建的原生会话"""
    _scheduler, server, thread, client, service = _stack(tmp_path)
    seen_sessions: list[str] = []

    class RecordingBackend:
        provider_id = "codex"

        def execute(self, invocation, context):
            request = invocation.request
            session = invocation.session
            seen_sessions.append(session.backend_session_id)
            context.emit("provider_output", {"text": request.prompt})
            return BackendResult(
                output={"message": request.prompt},
                backend_session_id=session.backend_session_id or "native-session-1",
            )

        def interrupt(self, **_values):
            return True

    runtime = ExecutionRuntime(
        agent_providers={"codex": RecordingBackend()},
        session_manager=SessionManager(),
    )
    scheduler_client = SchedulerClient(
        worker_id="explore-1:test-worker",
        build_id="test-build",
        capabilities=runtime.capabilities(),
        service_client=client,
    )
    worker = ExecutionWorker(
        build_id="test-build",
        worker_id="explore-1:test-worker",
        scheduler=scheduler_client,
        runtime=runtime,
    )
    try:
        worker.register()
        first = ExecutionRequest.new(
            actor="sample_admin",
            prompt="first",
            service_type="agent_session",
            provider="codex",
            operation="turn",
            session=SessionOptions(
                mode="persistent",
                binding_id="resident:test-main",
            ),
            system_instructions=portal_system_instructions("resident"),
        )
        service.submit(first)
        assert run_worker_once(worker)
        second = ExecutionRequest.new(
            actor="sample_admin",
            prompt="second",
            service_type="agent_session",
            provider="codex",
            operation="turn",
            session=SessionOptions(
                mode="persistent",
                binding_id="resident:test-main",
            ),
            system_instructions=portal_system_instructions("resident"),
        )
        service.submit(second)
        assert run_worker_once(worker)
        assert seen_sessions == ["", "native-session-1"]
        assert service.status(second.execution_id)["result"][
            "backend_session_id"
        ] == "native-session-1"
    finally:
        _close(server, thread)


def test_runtime_release_preserves_agent_session_identity(tmp_path: Path) -> None:
    """release 操作后绑定仍保留原后端会话 ID"""
    class ReleasingBackend:
        provider_id = "codex"

        def execute(self, invocation, context):
            request = invocation.request
            if request.operation == "release":
                return BackendResult(output={"message": ""})
            return BackendResult(
                output={"message": request.prompt},
                backend_session_id="native-session-1",
            )

        def interrupt(self, **_values):
            return True

    runtime = ExecutionRuntime(
        agent_providers={"codex": ReleasingBackend()},
        state_root=tmp_path / "execution-state",
    )
    binding_id = "resident:test-main"
    create = ExecutionRequest.new(
        actor="sample_admin",
        prompt="create",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        session=SessionOptions(mode="persistent", binding_id=binding_id),
        system_instructions=portal_system_instructions("resident"),
    )
    runtime.execute(create)
    assert runtime.sessions.provider_binding(binding_id)["backend_session_id"] == (
        "native-session-1"
    )
    release = ExecutionRequest.new(
        actor="sample_admin",
        prompt="release backend session",
        service_type="agent_session",
        provider="codex",
        operation="release",
        session=SessionOptions(mode="persistent", binding_id=binding_id),
        system_instructions=portal_system_instructions("resident"),
    )
    runtime.execute(release)
    assert runtime.sessions.provider_binding(binding_id)["backend_session_id"] == (
        "native-session-1"
    )


def test_provider_backends_execute_only_inside_agent_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """chat_completion 请求在 ExecutionRuntime 内调用模型 API 并返回结果和用量"""
    response = {
        "choices": [{"message": {"content": "provider result"}}],
        "usage": {"total_tokens": 12},
    }
    monkeypatch.setattr(
        execution_backends.shared_ai_provider_registry,
        "get",
        lambda _provider_id: {
            "id": "test_primary",
            "model": "test-model",
            "timeout_s": 5,
        },
    )

    def request_provider(
        _provider_id: str,
        endpoint: str,
        _payload: dict[str, object],
        **_kwargs: object,
    ) -> dict[str, object]:
        assert endpoint == "chat/completions"
        return response

    monkeypatch.setattr(
        execution_backends.shared_ai_provider_registry,
        "request",
        request_provider,
    )
    runtime = ExecutionRuntime(
        state_root=tmp_path / "execution-state",
    )
    chat = runtime.execute(
        ExecutionRequest.new(
            actor="sample_admin",
            prompt="hello",
            service_type="chat_completion",
            provider="test_primary",
            operation="generate",
            runtime_options={
                "model": "test-model",
            },
            session=SessionOptions(mode="stateless"),
            system_instructions=portal_system_instructions("general"),
        )
    )
    assert chat.output["message"] == "provider result"
    assert chat.usage["total_tokens"] == 12
    assert "chat_completion" in runtime.capabilities()


def test_chat_completion_explicit_zero_retries_overrides_registry_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """chat_completion 显式设 max_retries=0 时覆盖注册表默认值，只请求一次"""
    calls = 0
    monkeypatch.setattr(
        execution_backends.shared_ai_provider_registry,
        "get",
        lambda _provider_id: {
            "id": "test_primary",
            "model": "test-model",
            "timeout_s": 5,
            "max_retries": 2,
        },
    )

    def fail_provider(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(
        execution_backends.shared_ai_provider_registry,
        "request",
        fail_provider,
    )
    runtime = ExecutionRuntime(
        state_root=tmp_path / "execution-state",
    )

    with pytest.raises(RuntimeError, match="provider unavailable"):
        runtime.execute(
            ExecutionRequest.new(
                actor="sample_admin",
                prompt="hello",
                service_type="chat_completion",
                provider="test_primary",
                operation="generate",
                runtime_options={
                    "model": "test-model",
                    "max_retries": 0,
                },
                session=SessionOptions(mode="stateless"),
            )
        )

    assert calls == 1


def test_codex_backend_defaults_subagents_off_in_thread_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """codex 轮次配置默认关闭子 agent，并用本次执行的上下文文件替换旧的执行 ID 环境变量"""
    client = mock.Mock()

    def run_turn(*_args, **kwargs):
        kwargs["turn_started_callback"](
            "019f0000-0000-7000-8000-000000000402"
        )
        return mock.Mock(
            thread_id="019f0000-0000-7000-8000-000000000401",
            turn_id="019f0000-0000-7000-8000-000000000402",
            response="done",
            token_usage={},
        )

    client.run_turn.side_effect = run_turn
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="continue",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        cwd="/tmp",
        runtime_options={
            "client_user_message_id": "msg_0123456789abcdef",
            "config_overrides": {"shell_environment_policy.set": {
                "PORTAL_EXECUTION_ID": "stale-execution", "PORTAL_RESIDENT_ID": "resident_0123456789ab",
            }},
        },
        session=SessionOptions(
            mode="persistent",
            binding_id="provider:test-session",
            backend_route_id="one",
        ),
        system_instructions=portal_system_instructions("resident"),
    )
    events: list[tuple[str, dict]] = []
    context = execution_backends.BackendContext(
        emit_event=lambda name, details: events.append((name, details)),
        cancellation_requested=lambda: False,
        set_interrupt_target=lambda *_args: None,
    )

    session = RuntimeSession(
        mode="persistent",
        binding_id="",
        backend_session_id="019f0000-0000-7000-8000-000000000401",
        backend_route_id=request.session.backend_route_id,
        is_new_backend_session=False,
    )
    execution_backends.CodexBackend(
        client_factory=lambda _socket_path: client,
    ).execute(
        BackendInvocation(
            request=request,
            session=session,
            user_prompt=request.prompt,
            system_context="",
            attach_system_context=False,
            is_new_backend_session=False,
        ),
        context,
    )

    assert client.run_turn.call_args.kwargs["config"] == {
        "show_raw_agent_reasoning": True,
        "model_reasoning_summary": "auto",
        "agents": {"enabled": False, "interrupt_message": False},
        "compact_prompt": execution_backends._CODEX_COMPACTION_PROMPT,
        "project_doc_max_bytes": 0,
        "shell_environment_policy.set": {
            "PORTAL_EXECUTION_CONTEXT_FILE": str(execution_backends.CodexBackend._execution_context_path(request)), "PORTAL_RESIDENT_ID": "resident_0123456789ab",
        },
    }
    assert request.runtime_options["config_overrides"]["shell_environment_policy.set"]["PORTAL_EXECUTION_ID"] == "stale-execution"
    assert events == [
        (
            "provider_input_bound",
            {
                "provider": "codex",
                "backend_session_id": (
                    "019f0000-0000-7000-8000-000000000401"
                ),
                "backend_route_id": "one",
                "turn_id": "019f0000-0000-7000-8000-000000000402",
                "provider_message_id": "",
            },
        )
    ]


def test_codex_backend_uses_client_final_answer() -> None:
    """codex 轮次结果采用客户端选定的最终回答，不取事件流中的最后一条"""
    thread_id = "019f0000-0000-7000-8000-000000000489"
    turn_id = "019f0000-0000-7000-8000-000000000490"
    client = mock.Mock()

    def run_turn(*_args, **kwargs):
        kwargs["turn_started_callback"](turn_id)
        callback = kwargs["event_callback"]
        for text in ("complete answer", "trailing summary"):
            callback({
                "method": "item/completed",
                "params": {
                    "threadId": thread_id,
                    "turnId": turn_id,
                    "item": {
                        "type": "agentMessage",
                        "phase": "final_answer",
                        "text": text,
                    },
                },
            })
        return mock.Mock(
            thread_id=thread_id,
            turn_id=turn_id,
            response="selected answer",
            token_usage={},
        )

    client.run_turn.side_effect = run_turn
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="continue",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        cwd="/tmp",
        session=SessionOptions(
            mode="persistent",
            binding_id="resident:canonical-final-answer",
            backend_route_id="one",
        ),
        system_instructions=portal_system_instructions("resident"),
    )

    result = execution_backends.CodexBackend(
        client_factory=lambda _socket_path: client,
    ).execute(
        BackendInvocation(
            request=request,
            session=RuntimeSession(
                mode="persistent",
                binding_id=request.session.binding_id,
                backend_session_id=thread_id,
                backend_route_id="one",
                is_new_backend_session=False,
            ),
            user_prompt=request.prompt,
            system_context="",
            attach_system_context=False,
            is_new_backend_session=False,
        ),
        execution_backends.BackendContext(
            emit_event=lambda *_args: None,
            cancellation_requested=lambda: False,
            set_interrupt_target=lambda *_args: None,
        ),
    )

    assert result.output["message"] == "selected answer"


def test_codex_backend_resumes_an_accepted_turn_after_disconnect() -> None:
    """codex 轮次被接受后连接断开时按原线程和轮次恢复，不重新发起"""
    thread_id = "019f0000-0000-7000-8000-000000000491"
    turn_id = "019f0000-0000-7000-8000-000000000492"
    client = mock.Mock()

    def disconnect_after_accept(*_args, **kwargs):
        kwargs["turn_started_callback"](turn_id)
        raise CodexTurnConnectionLost(thread_id, turn_id)

    client.run_turn.side_effect = disconnect_after_accept
    client.resume_turn.return_value = mock.Mock(
        thread_id=thread_id,
        turn_id=turn_id,
        response="recovered response",
        token_usage={},
    )
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="continue",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        cwd="/tmp",
        session=SessionOptions(
            mode="persistent",
            binding_id="resident:disconnect-recovery",
            backend_route_id="one",
        ),
        system_instructions=portal_system_instructions("resident"),
    )

    result = execution_backends.CodexBackend(
        client_factory=lambda _socket_path: client,
    ).execute(
        BackendInvocation(
            request=request,
            session=RuntimeSession(
                mode="persistent",
                binding_id=request.session.binding_id,
                backend_session_id=thread_id,
                backend_route_id="one",
                is_new_backend_session=False,
            ),
            user_prompt=request.prompt,
            system_context="",
            attach_system_context=False,
            is_new_backend_session=False,
        ),
        execution_backends.BackendContext(
            emit_event=lambda *_args: None,
            cancellation_requested=lambda: False,
            set_interrupt_target=lambda *_args: None,
        ),
    )

    assert result.output["message"] == "recovered response"
    client.resume_turn.assert_called_once_with(
        thread_id,
        turn_id,
        cwd="/tmp",
        config={
            "show_raw_agent_reasoning": True,
            "model_reasoning_summary": "auto",
            "agents": {"enabled": False, "interrupt_message": False},
            "compact_prompt": execution_backends._CODEX_COMPACTION_PROMPT,
            "project_doc_max_bytes": 0,
            "shell_environment_policy.set": {"PORTAL_EXECUTION_CONTEXT_FILE": str(execution_backends.CodexBackend._execution_context_path(request))},
        },
        event_callback=mock.ANY,
        turn_started_callback=mock.ANY,
        turn_snapshot_transform=mock.ANY,
        detach_requested=mock.ANY,
        cancellation_requested=mock.ANY,
        dynamic_tool_handler=None,
        turn_control_provider=mock.ANY,
        turn_control_result_callback=mock.ANY,
        timeout_seconds=None,
    )


def test_codex_backend_keeps_waiting_for_an_unavailable_accepted_turn() -> None:
    """恢复已接受的 codex 轮次暂时超时时继续重试等待，不重发轮次"""
    thread_id = "019f0000-0000-7000-8000-000000000493"
    turn_id = "019f0000-0000-7000-8000-000000000494"
    client = mock.Mock()

    def disconnect_after_accept(*_args, **kwargs):
        kwargs["turn_started_callback"](turn_id)
        raise CodexTurnConnectionLost(thread_id, turn_id)

    client.run_turn.side_effect = disconnect_after_accept
    client.resume_turn.side_effect = [
        TimeoutError("temporary reconciliation timeout"),
        mock.Mock(
            thread_id=thread_id,
            turn_id=turn_id,
            response="recovered response",
            token_usage={},
        ),
    ]
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="continue",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        cwd="/tmp",
        session=SessionOptions(
            mode="persistent",
            binding_id="resident:deferred-recovery",
            backend_route_id="one",
        ),
        system_instructions=portal_system_instructions("resident"),
    )

    with mock.patch.object(execution_backends.time, "sleep"):
        result = execution_backends.CodexBackend(
            client_factory=lambda _socket_path: client,
        ).execute(
            BackendInvocation(
                request=request,
                session=RuntimeSession(
                    mode="persistent",
                    binding_id=request.session.binding_id,
                    backend_session_id=thread_id,
                    backend_route_id="one",
                    is_new_backend_session=False,
                ),
                user_prompt=request.prompt,
                system_context="",
                attach_system_context=False,
                is_new_backend_session=False,
            ),
            execution_backends.BackendContext(
                emit_event=lambda *_args: None,
                cancellation_requested=lambda: False,
                set_interrupt_target=lambda *_args: None,
            ),
        )

    assert result.output["message"] == "recovered response"
    assert client.run_turn.call_count == 1
    assert client.resume_turn.call_count == 2
    assert all(
        call.kwargs["timeout_seconds"] is None
        for call in client.resume_turn.call_args_list
    )


def test_codex_backend_recovers_terminal_event_without_resuming_thread() -> None:
    """codex 发出终止事件后 worker 交接，恢复时直接用记录的最终回答，不再续接线程"""
    thread_id = "019f0000-0000-7000-8000-000000000495"
    turn_id = "019f0000-0000-7000-8000-000000000496"
    client = mock.Mock()
    recovery_states: list[dict] = []

    def complete_before_transport_returns(*_args, **kwargs):
        kwargs["turn_started_callback"](turn_id)
        callback = kwargs["event_callback"]
        callback({
            "method": "item/completed",
            "params": {
                "threadId": thread_id,
                "turnId": turn_id,
                "item": {
                    "type": "agentMessage",
                    "phase": "commentary",
                    "text": "still working",
                },
            },
        })
        callback({
            "method": "item/completed",
            "params": {
                "threadId": thread_id,
                "turnId": turn_id,
                "item": {
                    "type": "agentMessage",
                    "phase": "final_answer",
                    "text": "durable final answer",
                },
            },
        })
        callback({
            "method": "item/completed",
            "params": {
                "threadId": thread_id,
                "turnId": turn_id,
                "item": {
                    "type": "agentMessage",
                    "phase": "final_answer",
                    "text": "trailing final summary",
                },
            },
        })
        callback({
            "method": "turn/completed",
            "params": {
                "threadId": thread_id,
                "turn": {
                    "id": turn_id,
                    "status": "completed",
                    "items": [{"type": "agentMessage", "phase": "final_answer", "text": "durable final answer"}],
                },
            },
        })
        # Reproduce the production failure boundary: the Provider has emitted
        # its terminal event, but the transport call never gets to return a
        # BackendResult before this worker is handed over.
        raise ExecutionRecoveryDeferred("worker retired after provider completion")

    client.run_turn.side_effect = complete_before_transport_returns
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="continue",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        cwd="/tmp",
        session=SessionOptions(
            mode="persistent",
            binding_id="resident:terminal-event-recovery",
            backend_route_id="one",
        ),
        system_instructions=portal_system_instructions("resident"),
    )
    session = RuntimeSession(
        mode="persistent",
        binding_id=request.session.binding_id,
        backend_session_id=thread_id,
        backend_route_id="one",
        is_new_backend_session=False,
    )
    backend = execution_backends.CodexBackend(
        client_factory=lambda _socket_path: client,
    )

    with pytest.raises(ExecutionRecoveryDeferred):
        backend.execute(
            BackendInvocation(
                request=request,
                session=session,
                user_prompt=request.prompt,
                system_context="",
                attach_system_context=False,
                is_new_backend_session=False,
            ),
            execution_backends.BackendContext(
                emit_event=lambda *_args: None,
                cancellation_requested=lambda: False,
                set_interrupt_target=lambda *_args: None,
                set_provider_recovery_state=recovery_states.append,
            ),
        )

    terminal_state = recovery_states[-1]
    assert terminal_state == {
        "kind": "completed_turn",
        "turn_id": turn_id,
        "response": "durable final answer",

        "usage": {},
        "provider_error_continue_count": 0,
        "original_input_recorded": False,
        "recovery_budget": {"attempts": 0, "turn_id": turn_id, "completed_item_ids": []},
    }

    client.reset_mock()
    result = backend.execute(
        BackendInvocation(
            request=request,
            session=session,
            user_prompt=request.prompt,
            system_context="",
            attach_system_context=False,
            is_new_backend_session=False,
            recovery_checkpoint={
                "turn_id": turn_id,
                "provider_recovery_state": terminal_state,
            },
        ),
        execution_backends.BackendContext(
            emit_event=lambda *_args: None,
            cancellation_requested=lambda: False,
            set_interrupt_target=lambda *_args: None,
            set_provider_recovery_state=recovery_states.append,
        ),
    )

    assert result.output == {
        "message": "durable final answer",
        "provider": "codex",
        "turn_id": turn_id,
    }
    assert result.backend_session_id == thread_id
    assert result.backend_route_id == "one"
    client.run_turn.assert_not_called()
    client.resume_turn.assert_not_called()


@pytest.mark.parametrize(("reason", "expected_reason"), [
    ("", "provider_recovery"),
    ("backend_service_update", "backend_service_update"),
    ("context_compaction", "context_compaction"),
])
def test_execution_runtime_recovery_does_not_repeat_start_activity(
    tmp_path: Path, reason: str, expected_reason: str,
) -> None:
    """恢复执行时不重复发出启动类事件，并标明轮次开始原因"""
    class RecoveredProvider:
        provider_id = "codex"

        def execute(self, _invocation, _context):
            _context.observe_provider_event({
                "method": "turn/started",
                "params": {
                    "threadId": "thread-recovered",
                    "turn": {"id": "turn-recovered", "status": "inProgress"},
                },
            })
            return BackendResult(
                output={"message": "done", "turn_id": "turn-recovered"},
                backend_session_id="thread-recovered",
                backend_route_id="one",
            )

        def interrupt(self, **_values):
            return True

    session = RuntimeSession(
        mode="persistent",
        binding_id="resident:recovered",
        backend_session_id="thread-recovered",
        backend_route_id="one",
        is_new_backend_session=False,
    )
    session_manager = mock.Mock()
    session_manager.prepare.return_value = session
    projector = mock.Mock()
    runtime = ExecutionRuntime(
        agent_providers={"codex": RecoveredProvider()},
        agent_harness=AgentHarness(()),
        session_manager=session_manager,
        agent_data_projector=projector,
        state_root=tmp_path,
    )
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="continue",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        session=SessionOptions(
            mode="persistent",
            binding_id="resident:recovered",
            backend_route_id="one",
        ),
    )
    events: list[str] = []
    activities: list[dict] = []
    checkpoints: list[dict] = []

    def record_event(event_type, details):
        events.append(event_type)
        if event_type == "provider_event":
            activities.append(details)

    runtime.execute(
        request,
        RuntimeContext(
            recovery_checkpoint={
                "provider": "codex",
                "backend_session_id": "thread-recovered",
                "backend_route_id": "one",
                "turn_id": "turn-recovered",
                "resumable": True,
                "turn_start_reason": reason,
            },
            emit_event=record_event,
            checkpoint_provider=checkpoints.append,
        ),
    )

    assert "runtime_started" not in events
    assert "context_prepared" not in events
    assert "service_started" not in events
    assert "service_completed" in events
    assert activities[0]["turn_start_reason"] == expected_reason
    assert any(
        checkpoint.get("turn_start_reason") == "continuation"
        for checkpoint in checkpoints
    )


def test_codex_backend_maps_input_media_to_native_app_server_images() -> None:
    """codex 后端把本地路径和 URL 图片分别映射为 app-server 的 images 和 image_urls"""
    client = mock.Mock()

    def run_turn(*_args, **kwargs):
        kwargs["turn_started_callback"](
            "019f0000-0000-7000-8000-000000000412"
        )
        return mock.Mock(
            thread_id="019f0000-0000-7000-8000-000000000411",
            turn_id="019f0000-0000-7000-8000-000000000412",
            response="done",
            token_usage={},
        )

    client.run_turn.side_effect = run_turn
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="inspect the image",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        cwd="/tmp",
        input_media=(
            InputMedia("image", "local_path", "/tmp/probe.png"),
            InputMedia("image", "url", "https://example.test/probe.png"),
        ),
        session=SessionOptions(
            mode="persistent",
            binding_id="provider:test-session",
            backend_route_id="one",
        ),
        system_instructions=portal_system_instructions("resident"),
    )

    execution_backends.CodexBackend(
        client_factory=lambda _socket_path: client,
    ).execute(
        BackendInvocation(
            request=request,
            session=RuntimeSession(
                mode="persistent",
                binding_id="",
                backend_session_id="019f0000-0000-7000-8000-000000000411",
                backend_route_id="one",
                is_new_backend_session=False,
            ),
            user_prompt=request.prompt,
            system_context="",
            attach_system_context=False,
            is_new_backend_session=False,
        ),
        execution_backends.BackendContext(
            emit_event=lambda *_args: None,
            cancellation_requested=lambda: False,
            set_interrupt_target=lambda *_args: None,
        ),
    )

    assert client.run_turn.call_args.kwargs["images"] == ["/tmp/probe.png"]
    assert client.run_turn.call_args.kwargs["image_urls"] == [
        "https://example.test/probe.png",
    ]


def test_codex_backend_resumes_a_thread_created_by_another_worker() -> None:
    """codex 后端在另一个 worker 上续用已有线程，不新建线程"""
    old_thread_id = "019f0000-0000-7000-8000-000000000451"
    route = mock.Mock(
        account_id="one",
        socket_path=Path("/tmp/codex-one.sock"),
    )
    registry = mock.Mock()
    registry.get.return_value = route
    registry.find_thread_rollout.return_value = None
    client = mock.Mock()
    client.run_turn.return_value = mock.Mock(
        thread_id=old_thread_id,
        turn_id="019f0000-0000-7000-8000-000000000452",
        response="cross-worker resumed",
        token_usage={},
    )
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="continue",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        cwd="/tmp",
        session=SessionOptions(
            mode="persistent",
            binding_id="resident:recovery-main",
            backend_route_id="one",
        ),
        system_instructions=portal_system_instructions("resident"),
    )
    session = RuntimeSession(
        mode="persistent",
        binding_id=request.session.binding_id,
        backend_session_id=old_thread_id,
        backend_route_id="one",
        is_new_backend_session=False,
    )
    result = execution_backends.CodexBackend(
        account_registry=registry,
        client_factory=lambda _socket_path: client,
    ).execute(
        BackendInvocation(
            request=request,
            session=session,
            user_prompt=request.prompt,
            system_context="resident rules",
            attach_system_context=False,
            is_new_backend_session=False,
        ),
        execution_backends.BackendContext(
            emit_event=lambda *_args: None,
            cancellation_requested=lambda: False,
            set_interrupt_target=lambda *_args: None,
        ),
    )

    assert result.output["message"] == "cross-worker resumed"
    assert client.run_turn.call_count == 1

    client.start_thread.assert_not_called()


def test_codex_subagent_requests_count_toward_the_turn_but_not_its_context() -> None:
    """Codex 子 agent 的请求计入本轮用量但不计入上下文

    Codex 子 agent 在自己的 thread 中发出的模型请求作为本轮用量增量上报（事件 ID 带子 thread 前缀、重复通知只计一次），当前上下文大小只取主 thread 的请求。

    A Codex subagent works in its own thread; its requests are the turn's usage too.

    :importance: P1
    :design: decision_f1331d57be89
    """
    client = mock.Mock()
    parent, child = "019f0000-0000-7000-8000-000000000431", "019f0000-0000-7000-8000-000000000439"

    def token_usage(thread_id, tokens):
        return {"method": "thread/tokenUsage/updated", "params": {"threadId": thread_id, "tokenUsage": {
            "last": {"inputTokens": tokens, "outputTokens": 100, "totalTokens": tokens + 100},
            "total": {"totalTokens": tokens + 100}, "modelContextWindow": 258_400,
        }}}

    def run_turn(*_args, **kwargs):
        kwargs["turn_started_callback"]("019f0000-0000-7000-8000-000000000432")
        kwargs["event_callback"](token_usage(parent, 150_000))
        child_usage = {"method": "subagent/event", "params": {
            "traceKey": child, "agentId": child, "ancestorTraceKeys": [], "parentToolUseId": "call-1",
            "event": token_usage(child, 40_000),
        }}
        kwargs["event_callback"](child_usage)
        kwargs["event_callback"](child_usage)  # The same notification again counts once.
        return mock.Mock(thread_id=parent, turn_id="019f0000-0000-7000-8000-000000000432", response="done", token_usage={})

    client.run_turn.side_effect = run_turn
    request = ExecutionRequest.new(
        actor="sample_admin", prompt="continue", service_type="agent_session", provider="codex",
        operation="turn", cwd="/tmp",
        session=SessionOptions(mode="persistent", binding_id="provider:test-session", backend_route_id="one"),
    )
    observed: list[tuple[dict[str, int], int]] = []
    events: list[tuple[str, dict]] = []
    context = execution_backends.BackendContext(
        emit_event=lambda name, details: events.append((name, details)),
        cancellation_requested=lambda: False,
        set_interrupt_target=lambda *_args: None,
        update_context_usage=lambda usage, window: observed.append((usage, window)),
    )
    execution_backends.CodexBackend(client_factory=lambda _socket_path: client).execute(
        BackendInvocation(
            request=request,
            session=RuntimeSession(
                mode="persistent", binding_id="", backend_session_id=parent,
                backend_route_id="one", is_new_backend_session=False,
            ),
            user_prompt=request.prompt, system_context="", attach_system_context=False,
            is_new_backend_session=False,
        ),
        context,
    )

    deltas = [details for name, details in events if name == "token_usage_delta"]
    assert [delta["usage"]["last"]["inputTokens"] for delta in deltas] == [150_000, 40_000]
    assert deltas[1]["event_id"].startswith(f"{child}:")
    assert [usage["input_tokens"] for usage, _window in observed] == [150_000]


def test_codex_requests_no_worker_heard_are_reported_from_the_rollouts(tmp_path: Path, monkeypatch) -> None:
    """Worker 没收到的 Codex 请求从 rollout 补记且只记一次

    Worker 交接期间结束的请求和子 agent 的请求在执行尝试结束时从 rollout 读出补报（事件 ID 与实时通知一致，已实时上报的不重报），执行开始前开始的轮次和子 agent 复制的父历史不计入。

    A request that ends during a Worker handover still counts, from the rollout, once.

    :importance: P1
    :design: decision_f1331d57be89
    """
    from tests.cases.account_registration_test_support import register_codex
    registry = register_codex(monkeypatch, tmp_path, names=("one",))
    parent, child = "019f0000-0000-7000-8000-000000000441", "019f0000-0000-7000-8000-000000000449"
    now = datetime.now(timezone.utc)

    def at(seconds: int) -> str:
        return (now + timedelta(seconds=seconds)).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def token_count(seconds: int, tokens: int, total: int) -> dict:
        return {"timestamp": at(seconds), "type": "event_msg", "payload": {"type": "token_count", "info": {
            "last_token_usage": {"input_tokens": tokens, "output_tokens": 100, "total_tokens": tokens + 100},
            "total_token_usage": {"input_tokens": total, "output_tokens": 300, "total_tokens": total + 300},
            "model_context_window": 258_400,
        }}}

    def task_started(seconds: int) -> dict:
        return {"timestamp": at(seconds), "type": "event_msg", "payload": {"type": "task_started"}}

    day = registry.get("one").codex_home / "sessions" / f"{datetime.now():%Y/%m/%d}"
    day.mkdir(parents=True)
    (day / f"rollout-x-{parent}.jsonl").write_text("".join(json.dumps(line) + "\n" for line in [
        {"timestamp": at(-600), "type": "session_meta", "payload": {"id": parent}},
        task_started(-120), token_count(-110, 70_000, 70_000),  # An earlier Execution's turn
        task_started(-30), token_count(-20, 80_000, 150_000),  # reported live
        token_count(-10, 90_000, 240_000),  # ended while no Worker listened
    ]))
    (day / f"rollout-y-{child}.jsonl").write_text("".join(json.dumps(line) + "\n" for line in [
        {"timestamp": at(-15), "ordinal": 0, "type": "session_meta", "payload": {
            "id": child, "subagent_history_start_ordinal": 2,
            "source": {"subagent": {"thread_spawn": {"parent_thread_id": parent}}},
        }},
        {**token_count(-15, 70_000, 70_000), "ordinal": 1},  # copied from the parent
        {**task_started(-14), "ordinal": 2}, {**token_count(-5, 40_000, 40_000), "ordinal": 3},
    ]))
    client = mock.Mock()

    def run_turn(*_args, **kwargs):
        kwargs["turn_started_callback"]("019f0000-0000-7000-8000-000000000442")
        kwargs["event_callback"]({"method": "thread/tokenUsage/updated", "params": {"threadId": parent, "tokenUsage": {
            "last": {"inputTokens": 80_000, "outputTokens": 100, "totalTokens": 80_100},
            "total": {"inputTokens": 150_000, "outputTokens": 300, "totalTokens": 150_300},
            "modelContextWindow": 258_400,
        }}})
        return mock.Mock(thread_id=parent, turn_id="019f0000-0000-7000-8000-000000000442", response="done", token_usage={})

    client.run_turn.side_effect = run_turn
    request = ExecutionRequest.new(
        actor="sample_admin", prompt="continue", service_type="agent_session", provider="codex",
        operation="turn", cwd="/tmp",
        session=SessionOptions(mode="persistent", binding_id="provider:test-session", backend_route_id="one"),
    )
    events: list[tuple[str, dict]] = []
    context = execution_backends.BackendContext(
        emit_event=lambda name, details: events.append((name, details)),
        cancellation_requested=lambda: False,
        set_interrupt_target=lambda *_args: None,
        execution_started_at=at(-60),
    )
    execution_backends.CodexBackend(
        client_factory=lambda _socket_path: client,
        account_registry=registry,
    ).execute(
        BackendInvocation(
            request=request,
            session=RuntimeSession(
                mode="persistent", binding_id="", backend_session_id=parent,
                backend_route_id="one", is_new_backend_session=False,
            ),
            user_prompt=request.prompt, system_context="", attach_system_context=False,
            is_new_backend_session=False,
        ),
        context,
    )

    deltas = [details for name, details in events if name == "token_usage_delta"]
    assert [delta["usage"]["last"]["inputTokens"] for delta in deltas] == [80_000, 90_000, 40_000]
    assert deltas[1]["occurred_at"] == at(-10)
    assert deltas[2]["event_id"].startswith(f"{child}:")


def test_codex_backend_projects_current_context_usage_from_token_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """codex 后端从 token 用量事件得出当前上下文大小，并上报一次用量增量"""
    client = mock.Mock()

    def run_turn(*_args, **kwargs):
        kwargs["turn_started_callback"](
            "019f0000-0000-7000-8000-000000000422"
        )
        kwargs["event_callback"]({
            "method": "thread/tokenUsage/updated",
            "params": {
                "threadId": "019f0000-0000-7000-8000-000000000421",
                "turnId": "019f0000-0000-7000-8000-000000000422",
                "tokenUsage": {
                    "last": {
                        "inputTokens": 153_000,
                        "cachedInputTokens": 149_000,
                        "outputTokens": 700,
                        "reasoningOutputTokens": 400,
                        "totalTokens": 153_700,
                    },
                    "total": {"totalTokens": 600_000},
                    "modelContextWindow": 258_400,
                },
            },
        })
        return mock.Mock(
            thread_id="019f0000-0000-7000-8000-000000000421",
            turn_id="019f0000-0000-7000-8000-000000000422",
            response="done",
            token_usage={},
        )

    client.run_turn.side_effect = run_turn
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="continue",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        cwd="/tmp",
        session=SessionOptions(
            mode="persistent",
            binding_id="provider:test-session",
            backend_route_id="one",
        ),
    )
    session = RuntimeSession(
        mode="persistent",
        binding_id="",
        backend_session_id="019f0000-0000-7000-8000-000000000421",
        backend_route_id=request.session.backend_route_id,
        is_new_backend_session=False,
    )
    observed: list[tuple[dict[str, int], int]] = []
    events: list[tuple[str, dict]] = []
    context = execution_backends.BackendContext(
        emit_event=lambda name, details: events.append((name, details)),
        cancellation_requested=lambda: False,
        set_interrupt_target=lambda *_args: None,
        update_context_usage=lambda usage, window: observed.append(
            (usage, window)
        ),
    )

    execution_backends.CodexBackend(
        client_factory=lambda _socket_path: client,
    ).execute(
        BackendInvocation(
            request=request,
            session=session,
            user_prompt=request.prompt,
            system_context="",
            attach_system_context=False,
            is_new_backend_session=False,
        ),
        context,
    )

    assert observed == [(
        {
            "input_tokens": 153_000,
            "cached_input_tokens": 149_000,
            "output_tokens": 700,
            "reasoning_output_tokens": 400,
            "total_tokens": 153_700,
        },
        258_400,
    )]
    usage_events = [
        details
        for name, details in events
        if name == "token_usage_delta"
    ]
    assert len(usage_events) == 1
    assert usage_events[0]["event_id"]
    assert usage_events[0]["usage"] == {
        "last": {
            "inputTokens": 153_000,
            "cachedInputTokens": 149_000,
            "outputTokens": 700,
            "reasoningOutputTokens": 400,
            "totalTokens": 153_700,
        }
    }
    assert not any(name == "provider_event" for name, _details in events)


def test_codex_backend_bridges_native_events_and_turn_controls() -> None:
    """codex 后端把原生工具事件交给事件处理器，并回报轮内控制输入的应用结果"""
    client = mock.Mock()
    observed_events: list[dict] = []
    controls = [{
        "control_id": "checkpoint-1",
        "input": [{"type": "text", "text": "consolidate"}],
    }]
    control_results: list[tuple[dict, bool, str]] = []
    processor = AgentLoopProviderEventProcessor(
        feature_id="native_tool_loop_governor",
        process_event=observed_events.append,
        next_control=lambda: controls.pop(0) if controls else None,
        record_control_result=(
            lambda control, applied, error: control_results.append(
                (control, applied, error)
            )
        ),
        report=lambda: {},
    )

    def run_turn(*_args, **kwargs):
        item = {"id": "command-native-1", "type": "commandExecution", "command": "rg target"}
        kwargs["event_callback"]({"method": "item/started", "params": {
            "turnId": "turn-native-1", "item": {**item, "status": "inProgress"}}})
        kwargs["event_callback"]({"method": "item/completed", "params": {
            "turnId": "turn-native-1", "item": {**item, "status": "completed", "exitCode": 0}}})
        control = kwargs["turn_control_provider"]()
        kwargs["turn_control_result_callback"](control, True, "")
        return mock.Mock(
            thread_id="019f0000-0000-7000-8000-000000000471",
            turn_id="turn-native-1",
            response="done",
            token_usage={},
        )

    client.run_turn.side_effect = run_turn
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="continue",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        cwd="/tmp",
        session=SessionOptions(
            mode="persistent",
            binding_id="provider:test-session",
            backend_route_id="one",
        ),
    )
    execution_backends.CodexBackend(
        client_factory=lambda _socket_path: client,
    ).execute(
        BackendInvocation(
            request=request,
            session=RuntimeSession(
                mode="persistent",
                binding_id="",
                backend_session_id="019f0000-0000-7000-8000-000000000471",
                backend_route_id="one",
                is_new_backend_session=False,
            ),
            user_prompt=request.prompt,
            system_context="",
            attach_system_context=False,
            is_new_backend_session=False,
            provider_event_processor=processor,
        ),
        execution_backends.BackendContext(
            emit_event=lambda *_args: None,
            cancellation_requested=lambda: False,
            set_interrupt_target=lambda *_args: None,
        ),
    )

    assert [event["method"] for event in observed_events] == ["item/started", "item/completed"]
    capability_contracts.check_tool_events(observed_events)
    assert control_results == [(
        {
            "control_id": "checkpoint-1",
            "input": [{"type": "text", "text": "consolidate"}],
        },
        True,
        "",
    )]








@pytest.mark.parametrize(("reason", "start_reason"), [
    ("missing_final_response", "provider_recovery"),
 ])
@pytest.mark.usefixtures("recorded_codex_input")
def test_codex_backend_continues_provider_turn_without_final_response(
    reason: str, start_reason: str,
) -> None:
    """codex 轮次没有最终回答时发送续接提示继续，并记录 provider_recovery 开始原因"""
    client = mock.Mock()
    thread_id = "019f0000-0000-7000-8000-000000000482"
    client.run_turn.side_effect = [
        CodexTurnContinuationRequired(
            thread_id,
            "turn-without-final",
            reason,
        ),
        mock.Mock(
            thread_id=thread_id,
            turn_id="turn-after-continuation",
            response="done",
            token_usage={},
        ),
    ]
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="continue",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        cwd="/tmp",
        session=SessionOptions(
            mode="persistent",
            binding_id="provider:test-session",
            backend_route_id="one",
        ),
    )
    recovery_states: list[dict] = []
    start_reasons: list[str] = []
    result = execution_backends.CodexBackend(
        client_factory=lambda _socket_path: client,
    ).execute(
        BackendInvocation(
            request=request,
            session=RuntimeSession(
                mode="persistent",
                binding_id="",
                backend_session_id=thread_id,
                backend_route_id="one",
                is_new_backend_session=False,
            ),
            user_prompt=request.prompt,
            system_context="",
            attach_system_context=False,
            is_new_backend_session=False,
        ),
        execution_backends.BackendContext(
            emit_event=lambda *_args: None,
            cancellation_requested=lambda: False,
            set_interrupt_target=lambda *_args: None,
            set_provider_recovery_state=recovery_states.append,
            turn_start_reason_changed=start_reasons.append,
        ),
    )

    assert result.output["message"] == "done"
    assert client.run_turn.call_count == 2
    assert client.run_turn.call_args_list[1].args[1] == (
        execution_backends.INTERRUPTED_TURN_CONTINUATION
    )
    reasons = [state["reason"] for state in recovery_states if "reason" in state]
    assert reasons and set(reasons) == {reason}
    assert start_reasons == [start_reason]


def test_codex_interrupt_reaches_turn_before_blocking_goal_pause() -> None:
    """codex 中断先送达轮次，不被阻塞的 Goal 暂停请求拖延"""
    from concurrent.futures import ThreadPoolExecutor

    interrupted = threading.Event()
    pausing = threading.Event()
    release = threading.Event()
    client = mock.Mock()
    client.interrupt_turn.side_effect = lambda *_: interrupted.set() or {}

    def pause(method, params, **kwargs):
        assert method == "thread/goal/set"
        assert params == {"threadId": "thread-stop", "status": "paused"}
        pausing.set()
        release.wait(5)
        return {}

    client.request.side_effect = pause
    backend = mock.Mock()
    backend.client_factory.return_value = client
    with ThreadPoolExecutor(max_workers=1) as executor:
        stopped = executor.submit(
            execution_backends.CodexBackend.interrupt, backend,
            backend_session_id="thread-stop", turn_id="turn-stop",
            backend_route_id="route-stop", runtime_options={"goal": {"objective": "finish"}},
        )
        try:
            assert pausing.wait(1)
            assert interrupted.is_set(), "Goal pause must not delay turn/interrupt"
        finally:
            release.set()
        assert stopped.result(timeout=1) is True
    client.interrupt_turn.assert_called_once_with("thread-stop", "turn-stop")


def test_codex_interrupt_preserves_goal_pause_semantics(subtests, isolated_portal_case):
    """codex 中断在有轮次时中断轮次、有 Goal 时暂停 Goal，两者都未成功时返回失败"""
    for turn_id, goal, pause_fails, expected in [
    ("turn-stop", None, False, True),
    ("turn-stop", {}, True, True),
    ("", {}, False, True),
    ("", {}, True, False),
    ("", None, False, False),
]:
        with subtests.test(turn_id=turn_id, goal=goal, pause_fails=pause_fails, expected=expected), isolated_portal_case() as case_path:
            client = mock.Mock()
            if pause_fails:
                client.request.side_effect = TimeoutError("Goal API unavailable")
            backend = mock.Mock()
            backend.client_factory.return_value = client
            result = execution_backends.CodexBackend.interrupt(
                backend, backend_session_id="thread-stop", turn_id=turn_id,
                backend_route_id="route-stop", runtime_options={"goal": goal},
            )
            assert result is expected
            assert client.interrupt_turn.call_count == bool(turn_id)
            assert client.request.call_count == isinstance(goal, dict)


@pytest.mark.usefixtures("no_continue_pause")
@pytest.mark.usefixtures("recorded_codex_input")
def test_codex_interrupt_attempts_goal_pause_even_if_turn_request_fails_contracts(subtests, isolated_portal_case):
    """codex 轮次中断失败仍尝试暂停 Goal，线程被占用或正在压缩时延后重试

    :module: resident.fork-handoff
    :importance: P1
    """
    with subtests.test(contract='test_codex_interrupt_attempts_goal_pause_even_if_turn_request_fails'), isolated_portal_case() as case_path:
        client = mock.Mock()
        client.interrupt_turn.side_effect = RuntimeError("turn interrupt failed")
        backend = mock.Mock()
        backend.client_factory.return_value = client
        with pytest.raises(RuntimeError, match="turn interrupt failed"):
            execution_backends.CodexBackend.interrupt(
                backend, backend_session_id="thread-stop", turn_id="turn-stop",
                backend_route_id="route-stop", runtime_options={"goal": {}},
            )
        client.request.assert_called_once_with(
            "thread/goal/set", {"threadId": "thread-stop", "status": "paused"}, timeout=10.0,
        )
    with subtests.test(contract='test_codex_backend_continues_after_unexplained_provider_interruption'), isolated_portal_case() as case_path:
        client = mock.Mock()
        thread_id = "019f0000-0000-7000-8000-000000000487"
        client.run_turn.side_effect = [
            CodexTurnInterrupted(thread_id, "turn-interrupted"),
            mock.Mock(
                thread_id=thread_id,
                turn_id="turn-after-interruption",
                response="recovered",
                token_usage={},
            ),
        ]
        request = ExecutionRequest.new(
            actor="sample_admin",
            prompt="continue",
            service_type="agent_session",
            provider="codex",
            operation="turn",
            cwd="/tmp",
            session=SessionOptions(
                mode="persistent",
                binding_id="provider:interruption-recovery",
                backend_route_id="one",
            ),
        )

        result = execution_backends.CodexBackend(
            client_factory=lambda _socket_path: client,
        ).execute(
            BackendInvocation(
                request=request,
                session=RuntimeSession(
                    mode="persistent",
                    binding_id=request.session.binding_id,
                    backend_session_id=thread_id,
                    backend_route_id="one",
                    is_new_backend_session=False,
                ),
                user_prompt=request.prompt,
                system_context="",
                attach_system_context=False,
                is_new_backend_session=False,
                provider_error_recovery=ProviderErrorRecoveryPolicy(),
            ),
            execution_backends.BackendContext(
                emit_event=lambda *_args: None,
                cancellation_requested=lambda: False,
                set_interrupt_target=lambda *_args: None,
            ),
        )

        assert result.output["message"] == "recovered"
        assert client.run_turn.call_count == 2
        assert client.run_turn.call_args_list[1].args[1] == "continue"
    with subtests.test(contract='test_codex_backend_does_not_continue_interruption_without_harness_policy'), isolated_portal_case() as case_path:
        client = mock.Mock()
        thread_id = "019f0000-0000-7000-8000-000000000488"
        client.run_turn.side_effect = CodexTurnInterrupted(
            thread_id,
            "turn-interrupted",
        )
        request = ExecutionRequest.new(
            actor="sample_admin",
            prompt="work",
            service_type="agent_session",
            provider="codex",
            operation="turn",
            cwd="/tmp",
            session=SessionOptions(
                mode="persistent",
                binding_id="provider:no-interruption-recovery",
                backend_route_id="one",
            ),
        )

        with pytest.raises(CodexTurnInterrupted):
            execution_backends.CodexBackend(
                client_factory=lambda _socket_path: client,
            ).execute(
                BackendInvocation(
                    request=request,
                    session=RuntimeSession(
                        mode="persistent",
                        binding_id=request.session.binding_id,
                        backend_session_id=thread_id,
                        backend_route_id="one",
                        is_new_backend_session=False,
                    ),
                    user_prompt=request.prompt,
                    system_context="",
                    attach_system_context=False,
                    is_new_backend_session=False,
                ),
                execution_backends.BackendContext(
                    emit_event=lambda *_args: None,
                    cancellation_requested=lambda: False,
                    set_interrupt_target=lambda *_args: None,
                ),
            )

        assert client.run_turn.call_count == 1
    with subtests.test(contract='test_codex_backend_bounds_repeated_turns_without_final_response'), isolated_portal_case() as case_path:
        client = mock.Mock()
        thread_id = "019f0000-0000-7000-8000-000000000483"
        client.run_turn.side_effect = [
            CodexTurnContinuationRequired(
                thread_id,
                f"turn-without-final-{index}",
                "missing_final_response",
            )
            for index in range(3)
        ] + [RuntimeError("unbounded continuation guard")]
        request = ExecutionRequest.new(
            actor="sample_admin",
            prompt="continue",
            service_type="agent_session",
            provider="codex",
            operation="turn",
            cwd="/tmp",
            session=SessionOptions(
                mode="persistent",
                binding_id="provider:test-session",
                backend_route_id="one",
            ),
        )

        with pytest.raises(RuntimeError, match="continuation limit"):
            execution_backends.CodexBackend(
                client_factory=lambda _socket_path: client,
            ).execute(
                BackendInvocation(
                    request=request,
                    session=RuntimeSession(
                        mode="persistent",
                        binding_id="",
                        backend_session_id=thread_id,
                        backend_route_id="one",
                        is_new_backend_session=False,
                    ),
                    user_prompt=request.prompt,
                    system_context="",
                    attach_system_context=False,
                    is_new_backend_session=False,
                ),
                execution_backends.BackendContext(
                    emit_event=lambda *_args: None,
                    cancellation_requested=lambda: False,
                    set_interrupt_target=lambda *_args: None,
                ),
            )

        assert client.run_turn.call_count == 3
    with subtests.test(contract='test_codex_backend_defers_thread_owned_by_another_process'), isolated_portal_case() as case_path:
        client = mock.Mock()
        thread_id = "019f0000-0000-7000-8000-000000000486"
        client.run_turn.side_effect = CodexThreadWriterBusy(
            f"thread {thread_id} already has an active writer"
        )
        request = ExecutionRequest.new(
            actor="sample_admin",
            prompt="continue when the terminal releases the session",
            service_type="agent_session",
            provider="codex",
            operation="turn",
            cwd="/tmp",
            session=SessionOptions(
                mode="persistent",
                binding_id="resident:writer-busy",
                backend_route_id="zero",
            ),
        )
        recovery_states: list[dict] = []

        with pytest.raises(ExecutionRecoveryDeferred) as raised:
            execution_backends.CodexBackend(
                client_factory=lambda _socket_path: client,
            ).execute(
                BackendInvocation(
                    request=request,
                    session=RuntimeSession(
                        mode="persistent",
                        binding_id=request.session.binding_id,
                        backend_session_id=thread_id,
                        backend_route_id="zero",
                        is_new_backend_session=False,
                    ),
                    user_prompt=request.prompt,
                    system_context="",
                    attach_system_context=False,
                    is_new_backend_session=False,
                ),
                execution_backends.BackendContext(
                    emit_event=lambda *_args: None,
                    cancellation_requested=lambda: False,
                    set_interrupt_target=lambda *_args: None,
                    set_provider_recovery_state=recovery_states.append,
                ),
            )

        assert raised.value.retry_after_seconds == 30
        assert recovery_states[-1] == {
            "kind": "thread_writer_busy",
            "thread_id": thread_id,

            "provider_error_continue_count": 0,
            "original_input_recorded": False,
            "recovery_budget": {"attempts": 1, "turn_id": "", "completed_item_ids": []},
        }
    with subtests.test(contract='test_codex_backend_defers_provider_compaction_without_failing_task'), isolated_portal_case() as case_path:
        client = mock.Mock()
        thread_id = "019f0000-0000-7000-8000-000000000487"
        client.run_turn.side_effect = CodexCompactionInProgress(
            "Codex context compaction is still active"
        )
        request = ExecutionRequest.new(
            actor="sample_admin",
            prompt="continue after provider compaction",
            service_type="agent_session",
            provider="codex",
            operation="turn",
            cwd="/tmp",
            session=SessionOptions(
                mode="persistent",
                binding_id="resident:compaction-in-progress",
                backend_route_id="zero",
            ),
        )
        recovery_states: list[dict] = []

        with pytest.raises(ExecutionRecoveryDeferred) as raised:
            execution_backends.CodexBackend(
                client_factory=lambda _socket_path: client,
            ).execute(
                BackendInvocation(
                    request=request,
                    session=RuntimeSession(
                        mode="persistent",
                        binding_id=request.session.binding_id,
                        backend_session_id=thread_id,
                        backend_route_id="zero",
                        is_new_backend_session=False,
                    ),
                    user_prompt=request.prompt,
                    system_context="",
                    attach_system_context=False,
                    is_new_backend_session=False,
                ),
                execution_backends.BackendContext(
                    emit_event=lambda *_args: None,
                    cancellation_requested=lambda: False,
                    set_interrupt_target=lambda *_args: None,
                    set_provider_recovery_state=recovery_states.append,
                ),
            )

        assert raised.value.retry_after_seconds == 5
        assert recovery_states[-1] == {
            "kind": "thread_writer_busy",
            "thread_id": thread_id,

            "provider_error_continue_count": 0,
            "original_input_recorded": False,
            "recovery_budget": {"attempts": 0, "turn_id": "", "completed_item_ids": []},
        }


@pytest.mark.usefixtures("no_continue_pause")
@pytest.mark.usefixtures("recorded_codex_input")
def test_codex_backend_continues_typed_interruption_with_provider_detail(
) -> None:
    """带 Provider 错误详情的 codex 中断按 Harness 策略发送 continue 续接"""
    client = mock.Mock()
    thread_id = "019f0000-0000-7000-8000-00000000048a"
    client.run_turn.side_effect = [
        CodexTurnInterrupted(
            thread_id,
            "turn-interrupted",
            "provider internal error",
        ),
        mock.Mock(
            thread_id=thread_id,
            turn_id="turn-after-interruption",
            response="recovered",
            token_usage={},
        ),
    ]
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="work",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        cwd="/tmp",
        session=SessionOptions(
            mode="persistent",
            binding_id="provider:detailed-interruption-recovery",
            backend_route_id="one",
        ),
    )

    result = execution_backends.CodexBackend(
        client_factory=lambda _socket_path: client,
    ).execute(
        BackendInvocation(
            request=request,
            session=RuntimeSession(
                mode="persistent",
                binding_id=request.session.binding_id,
                backend_session_id=thread_id,
                backend_route_id="one",
                is_new_backend_session=False,
            ),
            user_prompt=request.prompt,
            system_context="",
            attach_system_context=False,
            is_new_backend_session=False,
            provider_error_recovery=ProviderErrorRecoveryPolicy(),
        ),
        execution_backends.BackendContext(
            emit_event=lambda *_args: None,
            cancellation_requested=lambda: False,
            set_interrupt_target=lambda *_args: None,
        ),
    )

    assert result.output["message"] == "recovered"
    assert client.run_turn.call_count == 2
    assert client.run_turn.call_args_list[1].args[1] == "continue"


@pytest.mark.usefixtures("recorded_codex_input")
def test_codex_backend_continues_capacity_failure_once() -> None:
    """codex 因模型容量不足失败后等待 3 秒发送一次 continue，并记录续接事件"""
    client = mock.Mock()
    thread_id = "019f0000-0000-7000-8000-00000000048c"
    client.run_turn.side_effect = [
        CodexTurnRetryableFailure(
            thread_id,
            "turn-capacity",
            "Selected model is at capacity. Please try a different model.",
            "capacity",
        ),
        mock.Mock(
            thread_id=thread_id,
            turn_id="turn-after-capacity",
            response="recovered",
            token_usage={},
        ),
    ]
    events: list[tuple[str, dict]] = []
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="work",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        cwd="/tmp",
        session=SessionOptions(
            mode="persistent",
            binding_id="provider:capacity-recovery",
            backend_route_id="one",
        ),
    )

    sleep = mock.Mock()
    with mock.patch.object(execution_backends.time, "sleep", sleep):
        result = execution_backends.CodexBackend(
            client_factory=lambda _socket_path: client,
        ).execute(
            BackendInvocation(
                request=request,
                session=RuntimeSession(
                    mode="persistent",
                    binding_id=request.session.binding_id,
                    backend_session_id=thread_id,
                    backend_route_id="one",
                    is_new_backend_session=False,
                ),
                user_prompt=request.prompt,
                system_context="",
                attach_system_context=False,
                is_new_backend_session=False,
                provider_error_recovery=ProviderErrorRecoveryPolicy(),
            ),
            execution_backends.BackendContext(
                emit_event=lambda kind, payload: events.append((kind, payload)),
                cancellation_requested=lambda: False,
                set_interrupt_target=lambda *_args: None,
            ),
        )

    assert result.output["message"] == "recovered"
    assert client.run_turn.call_count == 2
    # The continuation went out three seconds after the failure.
    assert sum(call.args[0] for call in sleep.call_args_list) == 3.0
    assert client.run_turn.call_args_list[1].args[1] == "continue"
    assert events[0] == (
        "agent_harness_provider_error_continue",
        {"attempt": 1, "max_attempts": 1, "provider": "codex",
         "reason": "provider_error",
         "error": "Codex turn failed: Selected model is at capacity. Please try a different model."},
    )
    # The continuation is recorded as it goes out.
    assert [(kind, details["reason"], details["prompt"], details["input_delivery"]) for kind, details in events[1:]] == [
        ("recovery_input_sent", "provider_error", "continue", "continued"),
    ]


def test_codex_backend_does_not_continue_cancelled_interruption() -> None:
    """用户取消导致的 codex 中断不自动续接，以取消结束"""
    client = mock.Mock()
    thread_id = "019f0000-0000-7000-8000-00000000048b"
    cancelled = False

    def interrupt_turn(*_args, **_kwargs):
        nonlocal cancelled
        cancelled = True
        raise CodexTurnInterrupted(thread_id, "turn-cancelled", "cancelled")

    client.run_turn.side_effect = interrupt_turn
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="work",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        cwd="/tmp",
        session=SessionOptions(
            mode="persistent",
            binding_id="provider:cancelled-interruption",
            backend_route_id="one",
        ),
    )

    with pytest.raises(InterruptedError, match="cancelled"):
        execution_backends.CodexBackend(
            client_factory=lambda _socket_path: client,
        ).execute(
            BackendInvocation(
                request=request,
                session=RuntimeSession(
                    mode="persistent",
                    binding_id=request.session.binding_id,
                    backend_session_id=thread_id,
                    backend_route_id="one",
                    is_new_backend_session=False,
                ),
                user_prompt=request.prompt,
                system_context="",
                attach_system_context=False,
                is_new_backend_session=False,
                provider_error_recovery=ProviderErrorRecoveryPolicy(),
            ),
            execution_backends.BackendContext(
                emit_event=lambda *_args: None,
                cancellation_requested=lambda: cancelled,
                set_interrupt_target=lambda *_args: None,
            ),
        )

    assert client.run_turn.call_count == 1


@pytest.mark.usefixtures("no_continue_pause")
@pytest.mark.usefixtures("recorded_codex_input")
def test_codex_backend_continues_unexplained_interruption_only_once() -> None:
    """原因不明的 codex 中断只自动续接一次，再次中断时延后恢复"""
    client = mock.Mock()
    thread_id = "019f0000-0000-7000-8000-000000000489"
    client.run_turn.side_effect = [
        CodexTurnInterrupted(thread_id, "turn-interrupted-1"),
        CodexTurnInterrupted(thread_id, "turn-interrupted-2"),
        RuntimeError("automatic continue was not bounded"),
    ]
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="work",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        cwd="/tmp",
        session=SessionOptions(
            mode="persistent",
            binding_id="provider:bounded-interruption-recovery",
            backend_route_id="one",
        ),
    )

    recovery_states: list[dict] = []
    with pytest.raises(ExecutionRecoveryDeferred) as raised:
        execution_backends.CodexBackend(
            client_factory=lambda _socket_path: client,
        ).execute(
            BackendInvocation(
                request=request,
                session=RuntimeSession(
                    mode="persistent",
                    binding_id=request.session.binding_id,
                    backend_session_id=thread_id,
                    backend_route_id="one",
                    is_new_backend_session=False,
                ),
                user_prompt=request.prompt,
                system_context="",
                attach_system_context=False,
                is_new_backend_session=False,
                provider_error_recovery=ProviderErrorRecoveryPolicy(),
            ),
            execution_backends.BackendContext(
                emit_event=lambda *_args: None,
                cancellation_requested=lambda: False,
                set_interrupt_target=lambda *_args: None,
                set_provider_recovery_state=recovery_states.append,
            ),
        )

    assert client.run_turn.call_count == 2
    assert client.run_turn.call_args_list[1].args[1] == "continue"
    assert raised.value.retry_after_seconds == 5
    assert recovery_states[-1]["phase"] == "ready_to_continue"
    assert recovery_states[-1]["original_turn_id"] == "turn-interrupted-2"
    assert recovery_states[-1]["provider_error_continue_count"] == 1
    assert recovery_states[-1]["recovery_budget"]["attempts"] == 2


@pytest.mark.usefixtures("no_continue_pause")
@pytest.mark.usefixtures("recorded_codex_input")
def test_codex_backend_bounds_repeated_provider_system_errors() -> None:
    """codex 连续出现 Provider 系统错误时自动续接有次数上限，超出后延后恢复"""
    client = mock.Mock()
    thread_id = "019f0000-0000-7000-8000-000000000485"
    client.run_turn.side_effect = [
        CodexTurnContinuationRequired(
            thread_id,
            f"turn-system-error-{index}",
            "provider_system_error",
        )
        for index in range(3)
    ]
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="continue",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        cwd="/tmp",
        session=SessionOptions(
            mode="persistent",
            binding_id="provider:system-error-recovery",
            backend_route_id="one",
        ),
    )
    recovery_states: list[dict] = []

    with pytest.raises(ExecutionRecoveryDeferred) as raised:
        execution_backends.CodexBackend(
            client_factory=lambda _socket_path: client,
        ).execute(
            BackendInvocation(
                request=request,
                session=RuntimeSession(
                    mode="persistent",
                    binding_id=request.session.binding_id,
                    backend_session_id=thread_id,
                    backend_route_id="one",
                    is_new_backend_session=False,
                ),
                user_prompt=request.prompt,
                system_context="",
                attach_system_context=False,
                is_new_backend_session=False,
                provider_error_recovery=ProviderErrorRecoveryPolicy(),
            ),
            execution_backends.BackendContext(
                emit_event=lambda *_args: None,
                cancellation_requested=lambda: False,
                set_interrupt_target=lambda *_args: None,
                set_provider_recovery_state=recovery_states.append,
            ),
        )

    assert client.run_turn.call_count == 3
    assert client.run_turn.call_args_list[1].args[1] == "continue"
    assert raised.value.retry_after_seconds == 5
    assert recovery_states[-1]["phase"] == "ready_to_continue"
    assert recovery_states[-1]["transition_count"] == 3
    assert recovery_states[-1]["provider_error_continue_count"] == 1
    assert recovery_states[-1]["recovery_budget"]["attempts"] == 3


def test_codex_backend_does_not_require_new_compaction_for_replayed_item() -> None:
    """恢复时回放的历史压缩事件不要求生成新的压缩记录"""
    client = mock.Mock()
    thread_id = "019f0000-0000-7000-8000-000000000488"

    def resume_turn(*_args: object, **kwargs: object) -> object:
        callback = kwargs["event_callback"]
        assert callable(callback)
        callback({
            "method": "item/completed",
            "params": {
                "threadId": thread_id,
                "turnId": "historical-compaction-turn",
                "item": {"type": "contextCompaction"},
            },
        })
        return mock.Mock(
            thread_id=thread_id,
            turn_id="recovered-successor-turn",
            response="recovered result",
            token_usage={},
        )

    client.resume_turn.side_effect = resume_turn
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="continue",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        cwd="/tmp",
        session=SessionOptions(
            mode="persistent",
            binding_id="resident:replayed-compaction",
            backend_route_id="zero",
        ),
    )
    backend = execution_backends.CodexBackend(
        client_factory=lambda _socket_path: client,
    )
    existing = mock.Mock(fingerprint="already-committed-compaction")
    backend._latest_compaction = mock.Mock(return_value=existing)

    def record_new_compaction(**kwargs: object) -> None:
        if kwargs["required"]:
            raise RuntimeError(
                "Codex compact completed without a committed replacement context"
            )

    backend._record_new_compaction = mock.Mock(
        side_effect=record_new_compaction,
    )

    result = backend.execute(
        BackendInvocation(
            request=request,
            session=RuntimeSession(
                mode="persistent",
                binding_id=request.session.binding_id,
                backend_session_id=thread_id,
                backend_route_id="zero",
                is_new_backend_session=False,
            ),
            user_prompt=request.prompt,
            system_context="",
            attach_system_context=False,
            is_new_backend_session=False,
            recovery_checkpoint={"turn_id": "historical-compaction-turn"},
        ),
        execution_backends.BackendContext(
            emit_event=lambda *_args: None,
            cancellation_requested=lambda: False,
            set_interrupt_target=lambda *_args: None,
        ),
    )

    assert result.output["message"] == "recovered result"
    assert backend._record_new_compaction.call_args.kwargs["required"] is False


@pytest.mark.usefixtures("recorded_codex_input")
def test_codex_backend_recovery_uses_the_same_continuation_limit() -> None:
    """恢复中的 codex 轮次缺少最终回答时沿用同一续接次数上限"""
    client = mock.Mock()
    thread_id = "019f0000-0000-7000-8000-000000000484"
    client.resume_turn.side_effect = CodexTurnContinuationRequired(
        thread_id,
        "recovered-turn-without-final",
        "missing_final_response",
    )
    client.run_turn.side_effect = [
        CodexTurnContinuationRequired(
            thread_id,
            f"recovery-continuation-{index}",
            "missing_final_response",
        )
        for index in range(2)
    ] + [RuntimeError("unbounded recovery guard")]
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="continue",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        cwd="/tmp",
        session=SessionOptions(
            mode="persistent",
            binding_id="provider:test-session",
            backend_route_id="one",
        ),
    )

    with pytest.raises(RuntimeError, match="continuation limit"):
        execution_backends.CodexBackend(
            client_factory=lambda _socket_path: client,
        ).execute(
            BackendInvocation(
                request=request,
                session=RuntimeSession(
                    mode="persistent",
                    binding_id="",
                    backend_session_id=thread_id,
                    backend_route_id="one",
                    is_new_backend_session=False,
                ),
                user_prompt=request.prompt,
                system_context="",
                attach_system_context=False,
                is_new_backend_session=False,
                recovery_checkpoint={"turn_id": "accepted-turn"},
            ),
            execution_backends.BackendContext(
                emit_event=lambda *_args: None,
                cancellation_requested=lambda: False,
                set_interrupt_target=lambda *_args: None,
            ),
        )

    assert client.resume_turn.call_count == 1
    assert client.run_turn.call_count == 2


def test_execution_runtime_checkpoints_live_context_usage(
    tmp_path: Path,
) -> None:
    """ExecutionRuntime 把轮次中的实时上下文用量和窗口大小写入检查点"""
    class LiveUsageProvider:
        provider_id = "codex"

        def execute(self, _invocation, context):
            context.set_interrupt_target("thread-live", "turn-live", "one")
            context.observe_context_usage(
                {
                    "input_tokens": 153_000,
                    "cached_input_tokens": 149_000,
                    "total_tokens": 153_700,
                },
                258_400,
            )
            return BackendResult(
                output={"message": "done"},
                backend_session_id="thread-live",
                backend_route_id="one",
            )

        def interrupt(self, **_values):
            return True

    checkpoints: list[dict] = []
    runtime = ExecutionRuntime(
        agent_providers={"codex": LiveUsageProvider()},
        agent_harness=AgentHarness(()),
        state_root=tmp_path,
    )
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="continue",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        session=SessionOptions(mode="persistent", binding_id="resident:live"),
    )

    runtime.execute(
        request,
        RuntimeContext(
            checkpoint_provider=lambda checkpoint: checkpoints.append(
                checkpoint
            ),
        ),
    )

    live = next(
        checkpoint
        for checkpoint in checkpoints
        if checkpoint.get("context_usage")
    )
    assert live["context_usage"]["input_tokens"] == 153_000
    assert live["model_context_window"] == 258_400
    assert live["context_usage_at"].endswith("Z")


def test_execution_runtime_serializes_reclaimed_local_attempt(
    tmp_path: Path,
) -> None:
    """同一执行在本地被重新认领时，新尝试等旧尝试交接结束后才调用 Provider"""
    first_started = threading.Event()
    release_first = threading.Event()
    second_started = threading.Event()
    first_handover = threading.Event()
    call_lock = threading.Lock()
    call_count = 0

    class BlockingProvider:
        provider_id = "codex"

        def execute(self, _invocation, _context):
            nonlocal call_count
            with call_lock:
                call_count += 1
                call_number = call_count
            if call_number == 1:
                first_started.set()
                assert release_first.wait(3)
            else:
                second_started.set()
            return BackendResult(
                output={"message": f"attempt {call_number}"},
                backend_session_id=f"thread-{call_number}",
                backend_route_id="zero",
            )

        def interrupt(self, **_values):
            release_first.set()
            return True

    store = AgentDataStore()
    runtime = ExecutionRuntime(
        agent_providers={"codex": BlockingProvider()},
        agent_harness=AgentHarness(()),
        agent_data_projector=AgentDataProjector(store),
        state_root=tmp_path / "runtime",
    )
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="continue after reclaim",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        session=SessionOptions(mode="stateless"),
    )
    results: list[BackendResult] = []
    errors: list[BaseException] = []

    def execute(context: RuntimeContext) -> None:
        try:
            results.append(runtime.execute(
                request,
                context,
            ))
        except BaseException as exc:
            errors.append(exc)

    first = threading.Thread(
        target=execute,
        args=(RuntimeContext(
            accepted_at="2026-09-04T15:05:18.020Z",
            handover_requested=first_handover.is_set,
        ),),
    )
    second = threading.Thread(
        target=execute,
        args=(RuntimeContext(
            accepted_at="2026-09-04T15:05:18.020Z",
        ),),
    )
    first.start()
    assert first_started.wait(1)
    second.start()
    assert not second_started.wait(0.2)
    first_handover.set()
    release_first.set()
    first.join(3)
    second.join(3)

    assert not first.is_alive()
    assert not second.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], ExecutionRecoveryDeferred)
    assert second_started.is_set()
    assert len(results) == 1
    assert results[0].output["message"] == "attempt 2"


def test_execution_runtime_releases_projection_when_start_event_loses_claim(
    tmp_path: Path,
) -> None:
    """启动事件发现 claim 已丢失时释放投影，之后的重试可正常完成"""
    class CompletingProvider:
        provider_id = "codex"

        def execute(self, _invocation, _context):
            return BackendResult(
                output={"message": "recovered"},
                backend_session_id="thread-recovered",
                backend_route_id="zero",
            )

        def interrupt(self, **_values):
            return True

    store = AgentDataStore()
    runtime = ExecutionRuntime(
        agent_providers={"codex": CompletingProvider()},
        agent_harness=AgentHarness(()),
        agent_data_projector=AgentDataProjector(store),
        state_root=tmp_path / "runtime",
    )
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="resume after start event claim loss",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        session=SessionOptions(mode="stateless"),
    )
    accepted_at = "2026-09-04T15:05:18.020Z"
    ownership_lost = threading.Event()

    def lose_claim(*_args) -> None:
        ownership_lost.set()
        raise ExecutionClaimOwnershipLost("claim replaced")

    with pytest.raises(ExecutionClaimOwnershipLost):
        runtime.execute(
            request,
            RuntimeContext(
                accepted_at=accepted_at,
                emit_event=lose_claim,
                handover_requested=ownership_lost.is_set,
            ),
        )

    result = runtime.execute(
        request,
        RuntimeContext(accepted_at=accepted_at),
    )

    assert result.output["message"] == "recovered"


def test_execution_runtime_checkpoints_provider_recovery_state(
    tmp_path: Path,
) -> None:
    """Provider 设置的恢复状态写入执行检查点"""
    state = {
        "kind": "turn_and_continue",
        "phase": "ready_to_continue",
        "original_turn_id": "turn-before-compact",
        "continuation_prompt": "continue",
    }

    class TransitioningProvider:
        provider_id = "codex"

        def execute(self, _invocation, context):
            context.set_interrupt_target(
                "thread-transition",
                "turn-before-compact",
                "one",
            )
            context.set_provider_recovery_state(state)
            return BackendResult(
                output={"message": "done"},
                backend_session_id="thread-transition",
                backend_route_id="one",
            )

        def interrupt(self, **_values):
            return True

    checkpoints: list[dict] = []
    runtime = ExecutionRuntime(
        agent_providers={"codex": TransitioningProvider()},
        agent_harness=AgentHarness(()),
        state_root=tmp_path,
    )
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="continue",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        session=SessionOptions(
            mode="persistent",
            binding_id="resident:transition",
        ),
    )

    runtime.execute(
        request,
        RuntimeContext(checkpoint_provider=checkpoints.append),
    )

    transition_checkpoint = next(
        checkpoint
        for checkpoint in checkpoints
        if checkpoint.get("provider_recovery_state")
    )
    assert transition_checkpoint["provider_recovery_state"] == state


def test_execution_runtime_recovers_completed_provider_after_handover(
    tmp_path: Path,
) -> None:
    """Provider 完成后遇到 worker 交接时检查点保存最终结果，恢复时直接投影不再调用 Provider

    :module: execution.persistence
    :design: decision_b1ab45c4b3ec
    :design-point:
    """
    class CompletingProvider:
        provider_id = "codex"

        def __init__(self) -> None:
            self.execute_count = 0

        def execute(self, _invocation, context):
            self.execute_count += 1
            context.set_interrupt_target(
                "thread-completed",
                "turn-completed",
                "one",
            )
            return BackendResult(
                output={
                    "message": "durable final response",
                    "turn_id": "turn-completed",
                },
                backend_session_id="thread-completed",
                backend_route_id="one",
                usage={"total_tokens": 42},
            )

        def interrupt(self, **_values):
            return True

    provider = CompletingProvider()
    projector = mock.Mock()
    checkpoints: list[dict] = []
    runtime = ExecutionRuntime(
        agent_providers={"codex": provider},
        agent_harness=AgentHarness(()),
        agent_data_projector=projector,
        state_root=tmp_path,
    )
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="finish across worker handover",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        session=SessionOptions(
            mode="stateless",
            backend_route_id="one",
        ),
    )

    with pytest.raises(ExecutionRecoveryDeferred):
        runtime.execute(
            request,
            RuntimeContext(
                checkpoint_provider=checkpoints.append,
                handover_requested=lambda: True,
            ),
        )

    terminal_checkpoint = checkpoints[-1]
    assert terminal_checkpoint["provider_completed"] is True
    assert terminal_checkpoint["output"]["message"] == (
        "durable final response"
    )
    projector.complete.assert_not_called()

    result = runtime.execute(
        request,
        RuntimeContext(recovery_checkpoint=terminal_checkpoint),
    )

    assert provider.execute_count == 1
    assert result.output["message"] == "durable final response"
    projector.complete.assert_called_once_with(
        execution_id=request.execution_id,
        backend_route_id="one",
        backend_session_id="thread-completed",
        turn_id="turn-completed",
        response="durable final response",
        usage={"total_tokens": 42},
    )


def test_codex_backend_forwards_per_session_capability_disables() -> None:
    """codex 后端把会话级关闭 web 搜索、插件和 MCP 等配置原样传给轮次"""
    client = mock.Mock()

    def run_turn(*_args, **kwargs):
        kwargs["turn_started_callback"](
            "019f0000-0000-7000-8000-000000000405"
        )
        return mock.Mock(
            thread_id="019f0000-0000-7000-8000-000000000404",
            turn_id="019f0000-0000-7000-8000-000000000405",
            response="done",
            token_usage={},
        )

    client.run_turn.side_effect = run_turn
    config_overrides = {
        "web_search": "disabled",
        "features": {
            "apps": False,
            "memories": False,
            "multi_agent": False,
            "plugins": False,
        },
        "mcp_servers": {
            "openaiDeveloperDocs": {"enabled": False},
        },
    }
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="offline turn",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        cwd="/tmp",
        runtime_options={"config_overrides": config_overrides},
        session=SessionOptions(
            mode="persistent",
            binding_id="provider:test-session",
            backend_route_id="one",
        ),
        system_instructions=portal_system_instructions("general"),
    )
    session = RuntimeSession(
        mode="persistent",
        binding_id="",
        backend_session_id="019f0000-0000-7000-8000-000000000404",
        backend_route_id=request.session.backend_route_id,
        is_new_backend_session=False,
    )

    execution_backends.CodexBackend(
        client_factory=lambda _socket_path: client,
    ).execute(
        BackendInvocation(
            request=request,
            session=session,
            user_prompt=request.prompt,
            system_context="",
            attach_system_context=False,
            is_new_backend_session=False,
        ),
        execution_backends.BackendContext(
            emit_event=lambda *_args: None,
            cancellation_requested=lambda: False,
            set_interrupt_target=lambda *_args: None,
        ),
    )

    assert client.run_turn.call_args.kwargs["config"] == {
        **config_overrides,
        "shell_environment_policy.set": {"PORTAL_EXECUTION_CONTEXT_FILE": str(execution_backends.CodexBackend._execution_context_path(request))},
        "show_raw_agent_reasoning": True,
        "model_reasoning_summary": "auto",
        "agents": {"enabled": False, "interrupt_message": False},
        "compact_prompt": execution_backends._CODEX_COMPACTION_PROMPT,
        "project_doc_max_bytes": 0,
    }


def test_codex_backend_enables_subagents_only_when_explicitly_allowed_contracts(subtests, isolated_portal_case):
    """codex 仅在显式允许时开启子 agent，Provider 活动去掉增量和原始命令输出

    :module: resident.fork-handoff
    :importance: P1
    """
    with subtests.test(contract='test_codex_backend_enables_subagents_only_when_explicitly_allowed'), isolated_portal_case() as case_path:
        assert execution_backends.CodexBackend._thread_config(
            {"allow_subagents": True}
        ) == {
            "show_raw_agent_reasoning": True,
            "model_reasoning_summary": "auto",
            "agents": {"enabled": True, "interrupt_message": False},
            "compact_prompt": execution_backends._CODEX_COMPACTION_PROMPT,
            "project_doc_max_bytes": 0,
        }
    with subtests.test(contract='test_backend_context_emits_uniform_provider_input_binding'), isolated_portal_case() as case_path:
        events: list[tuple[str, dict]] = []
        interrupt_targets: list[tuple[str, str, str]] = []
        context = execution_backends.BackendContext(
            emit_event=lambda name, details: events.append((name, details)),
            cancellation_requested=lambda: False,
            set_interrupt_target=lambda *values: interrupt_targets.append(values),
        )

        context.bind_input(
            provider="codex",
            backend_session_id="thread-1",
            backend_route_id="zero",
            turn_id="turn-1",
            provider_message_id="provider-message-1",
        )

        assert interrupt_targets == [("thread-1", "turn-1", "zero")]
        assert events == [
            (
                "provider_input_bound",
                {
                    "provider": "codex",
                    "backend_session_id": "thread-1",
                    "backend_route_id": "zero",
                    "turn_id": "turn-1",
                    "provider_message_id": "provider-message-1",
                },
            )
        ]
    with subtests.test(contract='test_provider_activity_drops_deltas_and_raw_command_output'), isolated_portal_case() as case_path:
        assert execution_backends._compact_provider_activity_event({
            "method": "item/commandExecution/outputDelta",
            "params": {"delta": "unbounded output"},
        }) is None
        assert execution_backends._compact_provider_activity_event({
            "method": "item/agentMessage/delta",
            "params": {"delta": "token delta"},
        }) is None

        compact = execution_backends._compact_provider_activity_event({
            "method": "item/completed",
            "params": {
                "turnId": "turn-1",
                "item": {
                    "id": "command-1",
                    "type": "commandExecution",
                    "command": "pytest -q",
                    "status": "completed",
                    "exitCode": 0,
                    "aggregatedOutput": "private raw output" * 1000,
                },
            },
        })

        assert compact == {
            "method": "item/completed",
            "params": {
                "turnId": "turn-1",
                "item": {
                    "id": "command-1",
                    "type": "commandExecution",
                    "status": "completed",
                    "exitCode": 0,
                    "command": "pytest -q",
                },
            },
        }
    with subtests.test(contract='test_contract_requires_explicit_fork_source_and_new_destination'), isolated_portal_case() as case_path:
        with pytest.raises(ValueError, match="requires one context source"):
            ExecutionRequest.new(
                actor="sample_admin",
                prompt="fork",
                service_type="agent_session",
                provider="codex",
                operation="fork",
                session=SessionOptions(mode="persistent", binding_id="fork:target"),
            )

        request = ExecutionRequest.new(
            actor="sample_admin",
            prompt="fork",
            service_type="agent_session",
            provider="codex",
            operation="fork",
            session=SessionOptions(
                mode="persistent",
                binding_id="fork:target",
                context_source_binding_id="resident:source",
            ),
        )
        assert request.session.context_source_binding_id == "resident:source"
    with subtests.test(contract='test_codex_backend_rejects_goal_on_ephemeral_thread_before_creation'), isolated_portal_case() as case_path:
        registry = mock.Mock()
        request = ExecutionRequest.new(
            actor="sample_admin",
            prompt="/goal finish the portal goal integration",
            service_type="agent_session",
            provider="codex",
            operation="turn",
            cwd="/tmp",
            runtime_options={
                "ephemeral": True,
                "goal": {
                    "action": "set",
                    "objective": "finish the portal goal integration",
                },
            },
        )

        with pytest.raises(
            ValueError,
            match="native goals require a persistent thread",
        ):
            execution_backends.CodexBackend(
                account_registry=registry,
                client_factory=mock.Mock(),
            ).execute(
                BackendInvocation(
                    request=request,
                    session=RuntimeSession(
                        mode="persistent",
                        binding_id="",
                        backend_session_id="",
                        backend_route_id="",
                        is_new_backend_session=True,
                    ),
                    user_prompt=request.prompt,
                    system_context="",
                    attach_system_context=False,
                    is_new_backend_session=True,
                ),
                execution_backends.BackendContext(
                    emit_event=lambda *_args: None,
                    cancellation_requested=lambda: False,
                    set_interrupt_target=lambda *_args: None,
                ),
            )

        registry.candidates.assert_not_called()
    with subtests.test(contract='test_execution_worker_never_self_reactivates_after_scheduler_fence'), isolated_portal_case() as case_path:
        scheduler_client = mock.Mock()
        scheduler_client.claim.return_value = {
            "claimed": False,
            "reason": "worker_not_active",
        }
        worker = ExecutionWorker(
            build_id="current-build",
            worker_id="dev:recovering-worker",
            scheduler=scheduler_client,
            runtime=mock.Mock(),
        )

        assert worker.claim(wait_seconds=0.1) == scheduler_client.claim.return_value
        scheduler_client.register.assert_not_called()
        scheduler_client.claim.assert_called_once_with(90, wait_seconds=0.1)


def test_codex_backend_owns_interrupt_attribution(subtests, isolated_portal_case):
    """codex 线程配置固定关闭中断提示并使用 Portal 压缩提示词，不受覆盖配置影响"""
    for allow_subagents in [False, True]:
        for interrupt_message in [False, True]:
            with subtests.test(allow_subagents=allow_subagents, interrupt_message=interrupt_message), isolated_portal_case() as case_path:
                overrides = {
                    "agents": {"interrupt_message": interrupt_message, "max_threads": 2},
                    "compact_prompt": "Create a handoff for the next LLM.",
                }
                config = execution_backends.CodexBackend._thread_config({
                    "allow_subagents": allow_subagents,
                    "config_overrides": overrides,
                })

                assert config["agents"] == {
                    "enabled": allow_subagents,
                    "interrupt_message": False,
                    "max_threads": 2,
                }
                assert config["compact_prompt"] == execution_backends._CODEX_COMPACTION_PROMPT
                assert overrides == {
                    "agents": {"interrupt_message": interrupt_message, "max_threads": 2},
                    "compact_prompt": "Create a handoff for the next LLM.",
                }


def test_codex_backend_forwards_structured_sandbox_through_unified_exit(
    tmp_path: Path,
) -> None:
    """codex 新线程按 sandbox 模式启动，轮次传入结构化 sandbox_policy 和审批策略"""
    client = mock.Mock()
    client.start_thread.return_value = {
        "thread": {"id": "019f0000-0000-7000-8000-000000000411"}
    }

    def run_turn(*_args, **kwargs):
        kwargs["turn_started_callback"](
            "019f0000-0000-7000-8000-000000000412"
        )
        return mock.Mock(
            thread_id="019f0000-0000-7000-8000-000000000411",
            turn_id="019f0000-0000-7000-8000-000000000412",
            response="done",
            token_usage={},
        )

    client.run_turn.side_effect = run_turn
    sandbox_policy = {
        "type": "workspaceWrite",
        "writableRoots": [str(tmp_path)],
        "networkAccess": False,
    }
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="author graph",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        cwd=str(tmp_path),
        runtime_options={
            "sandbox_policy": sandbox_policy,
            "approval_policy": "never",
        },
        session=SessionOptions(mode="persistent", binding_id="sandbox:test"),
        system_instructions=portal_system_instructions("general"),
    )
    session = RuntimeSession(
        mode="persistent",
        binding_id=request.session.binding_id,
        backend_session_id="",
        backend_route_id="",
        is_new_backend_session=True,
    )

    execution_backends.CodexBackend(
        client_factory=lambda _socket_path: client,
    ).execute(
        BackendInvocation(
            request=request,
            session=session,
            user_prompt=request.prompt,
            system_context="rules",
            attach_system_context=True,
            is_new_backend_session=True,
        ),
        execution_backends.BackendContext(
            emit_event=lambda *_args: None,
            cancellation_requested=lambda: False,
            set_interrupt_target=lambda *_args: None,
        ),
    )

    assert client.start_thread.call_args.args[0]["sandbox"] == "workspace-write"
    assert client.start_thread.call_args.args[0]["approvalPolicy"] == "never"
    assert client.start_thread.call_args.args[0]["developerInstructions"] == (
        "rules"
    )
    assert client.run_turn.call_args.args[1] == "author graph"
    assert client.run_turn.call_args.kwargs["sandbox_policy"] == sandbox_policy
    assert client.run_turn.call_args.kwargs["approval_policy"] == "never"


def test_codex_backend_selects_native_permission_profile_and_approval(
    tmp_path: Path,
) -> None:
    """指定 permissions 时 codex 线程和轮次使用原生权限配置，不再传 sandbox"""
    client = mock.Mock()
    client.start_thread.return_value = {
        "thread": {"id": "019f0000-0000-7000-8000-000000000421"}
    }
    client.run_turn.return_value = mock.Mock(
        thread_id="019f0000-0000-7000-8000-000000000421",
        turn_id="019f0000-0000-7000-8000-000000000422",
        response="done",
        token_usage={},
    )
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="author graph",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        cwd=str(tmp_path),
        runtime_options={
            "permissions": "portal-closed-cwd",
            "sandbox": "danger-full-access",
            "approval_policy": "never",
        },
        session=SessionOptions(
            mode="persistent",
            binding_id="permissions:test",
        ),
        system_instructions=portal_system_instructions("general"),
    )
    session = RuntimeSession(
        mode="persistent",
        binding_id=request.session.binding_id,
        backend_session_id="",
        backend_route_id="",
        is_new_backend_session=True,
    )

    execution_backends.CodexBackend(
        client_factory=lambda _socket_path: client,
    ).execute(
        BackendInvocation(
            request=request,
            session=session,
            user_prompt=request.prompt,
            system_context="rules",
            attach_system_context=True,
            is_new_backend_session=True,
        ),
        execution_backends.BackendContext(
            emit_event=lambda *_args: None,
            cancellation_requested=lambda: False,
            set_interrupt_target=lambda *_args: None,
        ),
    )

    start_params = client.start_thread.call_args.args[0]
    assert start_params["permissions"] == "portal-closed-cwd"
    assert start_params["approvalPolicy"] == "never"
    assert "sandbox" not in start_params
    assert (
        client.run_turn.call_args.kwargs["permissions"]
        == "portal-closed-cwd"
    )
    assert client.run_turn.call_args.kwargs["approval_policy"] == "never"
    assert "sandbox_policy" not in client.run_turn.call_args.kwargs




def test_codex_permission_profile_owns_sandbox_policy(subtests, isolated_portal_case):
    """codex 指定 permissions 时忽略 sandbox 和 sandbox_policy"""
    for lower_level_policy in (
        {"sandbox": "danger-full-access"},
        {"sandbox_policy": {"type": "dangerFullAccess"}},
    ):
        with subtests.test(lower_level_policy=lower_level_policy), isolated_portal_case() as case_path:
            sandbox, sandbox_policy = execution_backends.CodexBackend._sandbox_options({
                "permissions": ":danger-full-access",
                **lower_level_policy,
            })

            assert sandbox == ""
            assert sandbox_policy is None


def test_claude_backend_projects_provider_activity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """claude 后端把原生事件作为 provider_event 上报，并传递权限配置"""
    raw_event = {
        "type": "response_item",
        "payload": {
            "type": "function_call",
            "name": "Read",
            "call_id": "tool-1",
            "arguments": {"path": "/tmp/example.md"},
        },
    }
    client = mock.Mock()

    def run_turn(**kwargs):
        kwargs["event_callback"](raw_event)
        return mock.Mock(
            response="done",
            session_id="claude-session-1",
            turn_id="claude-turn-1",
            usage={},
        )

    client.run_turn.side_effect = run_turn
    monkeypatch.setattr(
        execution_backends,
        "shared_claude_client",
        lambda: client,
    )
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="continue",
        service_type="agent_session",
        provider="claude",
        operation="turn",
        cwd="/tmp",
        permission_profile="portal-closed-cwd",
        session=SessionOptions(
            mode="persistent",
        binding_id="provider:test-session",
        ),
    )
    events: list[tuple[str, dict]] = []

    execution_backends.ClaudeBackend().execute(
        BackendInvocation(
            request=request,
            session=RuntimeSession(
                mode="persistent",
                binding_id="",
                backend_session_id="claude-session-1",
                is_new_backend_session=False,
            ),
            user_prompt=request.prompt,
            system_context="",
            attach_system_context=False,
            is_new_backend_session=False,
        ),
        execution_backends.BackendContext(
            emit_event=lambda name, details: events.append((name, details)),
            cancellation_requested=lambda: False,
            set_interrupt_target=lambda *_args: None,
        ),
    )

    assert events == [("provider_event", raw_event)]
    assert (
        client.run_turn.call_args.kwargs["permission_profile"]
        == "portal-closed-cwd"
    )


def test_kimi_backend_projects_provider_activity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """kimi 后端把原生事件作为 provider_event 上报"""
    raw_event = {
        "method": "item/completed",
        "params": {
            "threadId": "kimi-session-1",
            "turnId": "kimi-turn-1",
            "item": {"id": "kimi:turn:assistant:0", "type": "agentMessage", "text": "Inspecting the workspace"},
        },
    }
    client = mock.Mock()

    def run_turn(**kwargs):
        kwargs["event_callback"](raw_event)
        return mock.Mock(
            response="done",
            session_id="kimi-session-1",
            turn_id="kimi-turn-1",
            usage={},
        )

    client.run_turn.side_effect = run_turn
    monkeypatch.setattr(
        execution_backends,
        "shared_kimi_client",
        lambda: client,
    )
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="continue",
        service_type="agent_session",
        provider="kimi",
        operation="turn",
        cwd="/tmp",
        session=SessionOptions(
            mode="persistent",
        binding_id="provider:test-session",
        ),
    )
    events: list[tuple[str, dict]] = []

    execution_backends.KimiBackend().execute(
        BackendInvocation(
            request=request,
            session=RuntimeSession(
                mode="persistent",
                binding_id="",
                backend_session_id="kimi-session-1",
                is_new_backend_session=False,
            ),
            user_prompt=request.prompt,
            system_context="",
            attach_system_context=False,
            is_new_backend_session=False,
        ),
        execution_backends.BackendContext(
            emit_event=lambda name, details: events.append((name, details)),
            cancellation_requested=lambda: False,
            set_interrupt_target=lambda *_args: None,
        ),
    )

    assert events == [("provider_event", raw_event)]


def test_provider_activity_recursively_redacts_sensitive_values() -> None:
    """Provider 活动的命令、URL、参数、警告和工具输出中的密钥被递归遮蔽"""
    synthetic_secret = "sentinel-provider-secret"
    compact = execution_backends._compact_provider_activity_event({
        "method": "item/completed",
        "params": {
            "item": {
                "id": "tool-1",
                "type": "functionCall",
                "status": "completed",
                "command": f"API_TOKEN={synthetic_secret} python3 safe.py",
                "display": {
                    "url": (
                        "https://user:password@example.test/run?"
                        f"token={synthetic_secret}&mode=safe"
                    ),
                },
                "arguments": {
                    "api_key": synthetic_secret,
                    "headers": {
                        "Authorization": f"Bearer {synthetic_secret}",
                    },
                    "query": "public status",
                },
            },
        },
    })
    warning = execution_backends._compact_provider_activity_event({
        "method": "guardianWarning",
        "params": {
            "warning": f"credential={synthetic_secret}",
            "error": {
                "message": f"password: {synthetic_secret}",
                "additionalDetails": (
                    f"authorization: Bearer {synthetic_secret}\n"
                    "https://example.test/failure?"
                    f"access_token={synthetic_secret}"
                ),
            },
        },
    })
    tool_output = execution_backends._compact_provider_activity_event({
        "type": "response_item",
        "payload": {
            "type": "function_call_output",
            "call_id": "tool-1",
            "output": f"cookie={synthetic_secret} safe-output",
        },
    })

    serialized = json.dumps([compact, warning, tool_output], sort_keys=True)
    assert synthetic_secret not in serialized
    assert "password@example.test" not in serialized
    assert "[REDACTED]" in serialized
    assert "public status" in serialized
    assert "mode=safe" in serialized
    assert "safe-output" in serialized


def test_codex_account_route_uses_one_then_falls_back_before_thread_start(
    tmp_path: Path,
) -> None:
    """codex 新线程启动前 one 账号不可用时回退到 zero，并记录回退事件"""
    one_client = mock.Mock()
    one_client.start_thread.side_effect = (
        execution_backends.CodexAppServerUnavailable("socket unavailable")
    )
    zero_client = mock.Mock()
    zero_client.start_thread.return_value = {
        "thread": {"id": "019f0000-0000-7000-8000-000000000501"}
    }
    zero_client.run_turn.return_value = mock.Mock(
        thread_id="019f0000-0000-7000-8000-000000000501",
        turn_id="019f0000-0000-7000-8000-000000000502",
        response="done",
        token_usage={},
    )

    def client_factory(socket_path):
        return (
            one_client
            if str(socket_path).endswith("codex-app-server-one.sock")
            else zero_client
        )

    request = ExecutionRequest.new(
        actor="sample_reader",
        prompt="new turn",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        cwd=str(tmp_path),
        session=SessionOptions(mode="persistent", binding_id="resident:test-sample_reader"),
        system_instructions=portal_system_instructions("resident"),
    )
    session = RuntimeSession(
        mode="persistent",
        binding_id=request.session.binding_id,
        backend_session_id="",
        is_new_backend_session=True,
    )
    events: list[tuple[str, dict]] = []
    result = execution_backends.CodexBackend(
        client_factory=client_factory,
    ).execute(
        BackendInvocation(
            request=request,
            session=session,
            user_prompt=request.prompt,
            system_context="rules",
            attach_system_context=True,
            is_new_backend_session=True,
        ),
        execution_backends.BackendContext(
            emit_event=lambda name, details: events.append((name, details)),
            cancellation_requested=lambda: False,
            set_interrupt_target=lambda *_args: None,
        ),
    )

    assert result.backend_route_id == "zero"
    assert one_client.start_thread.call_count == 1
    assert zero_client.start_thread.call_count == 1
    assert events[0] == (
        "backend_route_fallback",
        {
            "provider": "codex",
            "from_route_id": "one",
            "to_route_id": "zero",
            "reason": "CodexAppServerUnavailable",
        },
    )


@pytest.mark.parametrize("blocked", [{"zero"}, {"zero", "two"}])
def test_codex_fallback_skips_accounts_that_reject_capacity(tmp_path, blocked):
    """codex 账号回退跳过拒绝转移容量的账号，全部拒绝时报原错误

    :design: decision_3dd52c0692c1
    :design-point:
    """
    request = ExecutionRequest.new(
        actor="test", prompt="hello", service_type="agent_session", provider="codex", operation="turn",
        cwd=str(tmp_path), session=SessionOptions(mode="persistent", binding_id="resident:fallback", backend_route_id="one"),
    )
    invocation = BackendInvocation(
        request=request, session=RuntimeSession(mode="persistent", binding_id=request.session.binding_id,
                                               backend_session_id="", backend_route_id="one", is_new_backend_session=True),
        user_prompt=request.prompt, system_context="", attach_system_context=False, is_new_backend_session=True,
    )
    attempts, events, started = [], [], []

    def reassign(route):
        attempts.append(route)
        return {"accepted": route not in blocked}

    registry = mock.Mock()
    registry.candidates.return_value = [
        mock.Mock(account_id=route, socket_path=tmp_path / f"codex-app-server-{route}.sock")
        for route in ("one", "zero", "two")
    ]
    backend = execution_backends.CodexBackend(account_registry=registry, client_factory=lambda socket: socket)

    def start(client, *_args):
        started.append(str(client))
        if str(client).endswith("codex-app-server-one.sock"):
            raise execution_backends.CodexAppServerUnavailable("primary unavailable")
        return "new-thread"

    context = execution_backends.BackendContext(
        emit_event=lambda *event: events.append(event), cancellation_requested=lambda: False,
        set_interrupt_target=lambda *_args: None, reassign_backend_route=reassign,
    )
    with mock.patch.object(backend, "_start_thread", side_effect=start):
        if "two" in blocked:
            with pytest.raises(execution_backends.CodexAppServerUnavailable, match="primary unavailable"):
                backend._resolve_client(invocation, context)
            assert events == []
            assert len(started) == 1
        else:
            route, _client, thread_id, _attached = backend._resolve_client(invocation, context)
            assert (route.account_id, thread_id) == ("two", "new-thread")
            assert events[0][1]["from_route_id"] == "one"
            assert events[0][1]["to_route_id"] == "two"
            assert not any(path.endswith("codex-app-server-zero.sock") for path in started)
    assert attempts == ["zero", "two"]


def test_explicit_codex_account_route_does_not_fall_back(
    tmp_path: Path,
) -> None:
    """禁止回退的显式 codex 账号路线不可用时直接报错，不尝试其他账号"""
    one_client = mock.Mock()
    one_client.start_thread.side_effect = (
        execution_backends.CodexAppServerUnavailable("socket unavailable")
    )
    zero_client = mock.Mock()

    def client_factory(socket_path):
        return (
            one_client
            if str(socket_path).endswith("codex-app-server-one.sock")
            else zero_client
        )

    request = ExecutionRequest.new(
        actor="sample_reader",
        prompt="new turn",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        cwd=str(tmp_path),
        runtime_options={"allow_backend_route_fallback": False},
        session=SessionOptions(
            mode="persistent",
            binding_id="resident:test-explicit-one",
            backend_route_id="one",
        ),
        system_instructions=portal_system_instructions("resident"),
    )
    session = RuntimeSession(
        mode="persistent",
        binding_id=request.session.binding_id,
        backend_session_id="",
        backend_route_id="one",
        is_new_backend_session=True,
    )

    with pytest.raises(
        execution_backends.CodexAppServerUnavailable,
        match="socket unavailable",
    ):
        execution_backends.CodexBackend(
            client_factory=client_factory,
        ).execute(
            BackendInvocation(
                request=request,
                session=session,
                user_prompt=request.prompt,
                system_context="rules",
                attach_system_context=True,
                is_new_backend_session=True,
            ),
            execution_backends.BackendContext(
                emit_event=lambda *_args: None,
                cancellation_requested=lambda: False,
                set_interrupt_target=lambda *_args: None,
            ),
        )

    assert one_client.start_thread.call_count == 1
    assert zero_client.mock_calls == []


def test_codex_account_route_never_moves_an_existing_thread() -> None:
    """已有线程的 codex 轮次在账号不可用时报错，不迁移到其他账号"""
    one_client = mock.Mock()
    one_client.run_turn.side_effect = (
        execution_backends.CodexAppServerUnavailable("socket unavailable")
    )
    zero_client = mock.Mock()

    def client_factory(socket_path):
        return (
            one_client
            if str(socket_path).endswith("codex-app-server-one.sock")
            else zero_client
        )

    request = ExecutionRequest.new(
        actor="sample_reader",
        prompt="continue",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        cwd="/tmp",
        session=SessionOptions(
            mode="persistent",
            binding_id="provider:test-session",
            backend_route_id="one",
        ),
        system_instructions=portal_system_instructions("resident"),
    )
    session = RuntimeSession(
        mode="persistent",
        binding_id="",
        backend_session_id="019f0000-0000-7000-8000-000000000601",
        backend_route_id="one",
        is_new_backend_session=False,
    )

    with pytest.raises(
        execution_backends.CodexAppServerUnavailable,
        match="socket unavailable",
    ):
        execution_backends.CodexBackend(
            client_factory=client_factory,
        ).execute(
            BackendInvocation(
                request=request,
                session=session,
                user_prompt=request.prompt,
                system_context="",
                attach_system_context=False,
                is_new_backend_session=False,
            ),
            execution_backends.BackendContext(
                emit_event=lambda *_args: None,
                cancellation_requested=lambda: False,
                set_interrupt_target=lambda *_args: None,
            ),
        )
    assert zero_client.mock_calls == []


def test_codex_account_route_does_not_treat_auth_failure_as_failover() -> None:
    """codex 认证失败不触发账号回退"""
    one_client = mock.Mock()
    one_client.start_thread.side_effect = RuntimeError(
        "authentication failed"
    )
    zero_client = mock.Mock()

    def client_factory(socket_path):
        return (
            one_client
            if str(socket_path).endswith("codex-app-server-one.sock")
            else zero_client
        )

    request = ExecutionRequest.new(
        actor="sample_reader",
        prompt="new turn",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        cwd="/tmp",
        session=SessionOptions(mode="persistent", binding_id="resident:test-auth"),
        system_instructions=portal_system_instructions("resident"),
    )
    with pytest.raises(RuntimeError, match="authentication failed"):
        execution_backends.CodexBackend(
            client_factory=client_factory,
        ).execute(
            BackendInvocation(
                request=request,
                session=RuntimeSession(
                    mode="persistent",
                    binding_id=request.session.binding_id,
                    backend_session_id="",
                    is_new_backend_session=True,
                ),
                user_prompt=request.prompt,
                system_context="rules",
                attach_system_context=True,
                is_new_backend_session=True,
            ),
            execution_backends.BackendContext(
                emit_event=lambda *_args: None,
                cancellation_requested=lambda: False,
                set_interrupt_target=lambda *_args: None,
            ),
        )
    assert zero_client.mock_calls == []


@pytest.mark.parametrize("phase", ["queued", "claimed", "running"])
def test_cancel_queued_only_preserves_claimed_and_running_execution(
    tmp_path: Path, phase: str,
) -> None:
    """只取消排队项的请求不影响已认领或运行中的执行"""
    scheduler, server, thread, client, service = _stack(tmp_path)
    try:
        request = ExecutionRequest.new(
            actor="sample_admin", prompt="queued-only cancellation",
            service_type="agent_session", provider="codex", operation="turn",
        )
        service.submit(request)
        if phase != "queued":
            worker = SchedulerClient(
                worker_id="test:queue-clear", build_id="test-build",
                capabilities=["agent_session:codex"], service_client=client,
            )
            worker.register()
            claim = worker.claim()
            assert claim["claimed"]
            if phase == "running":
                worker.start(request.execution_id, claim["claim_token"])
        result = service.cancel(
            request.execution_id, actor="sample_admin", queued_only=True,
            reason="clear pending messages",
        )
        assert result["status"] == ("cancelled" if phase == "queued" else phase)
        if phase != "queued":
            with connect(scheduler.database) as connection:
                assert connection.execute(
                    'SELECT cancel_requested FROM executions WHERE execution_id = %s',
                    (request.execution_id,),
                ).fetchone()[0] == 0
            service.cancel(request.execution_id, actor="sample_admin")
            with connect(scheduler.database) as connection:
                assert connection.execute(
                    'SELECT cancel_requested FROM executions WHERE execution_id = %s',
                    (request.execution_id,),
                ).fetchone()[0] == 1
    finally:
        _close(server, thread)


def test_scheduler_cancels_queued_work_and_recovers_expired_claims_contracts(tmp_path, subtests, isolated_portal_case):
    """调度器可取消排队执行，租约过期的认领回到队列，已开始的执行不重放而标记放弃

    :module: resident.fork-handoff
    :importance: P1
    """
    with subtests.test(contract='test_scheduler_cancels_queued_work_and_recovers_expired_claims'), isolated_portal_case() as case_path:
        tmp_path = case_path
        scheduler, server, thread, client, service = _stack(tmp_path)
        try:
            queued = ExecutionRequest.new(
                actor="sample_admin",
                prompt="cancel me",
                service_type="agent_session",
                provider="codex",
                operation="turn",
            )
            service.submit(queued)
            cancelled = service.cancel(
                queued.execution_id,
                actor="sample_admin",
                reason="test cancellation",
            )
            assert cancelled["status"] == "cancelled"

            recoverable = ExecutionRequest.new(
                actor="sample_admin",
                prompt="recover me",
                service_type="agent_session",
                provider="codex",
                operation="turn",
            )
            service.submit(recoverable)
            worker = SchedulerClient(
                worker_id="explore-1:lost-worker",
                build_id="test-build",
                capabilities=["agent_session:codex"],
                service_client=client,
            )
            worker.register()
            assert worker.claim()["claimed"]
            with connect(scheduler.database) as connection:
                connection.execute(
                    '\n                    UPDATE executions\n                    SET lease_expires_at = TIMESTAMPTZ \'1970-01-01 00:00:01+00\'\n                    WHERE execution_id = %s\n                    ',
                    (recoverable.execution_id,),
                )
            scheduler.maintain()
            recovered = service.status(recoverable.execution_id)
            assert recovered["status"] == "queued"
            assert "requeued" in [event["type"] for event in recovered["events"]]
        finally:
            _close(server, thread)
    with subtests.test(contract='test_scheduler_keeps_explicit_priority_above_actor_fairness'), isolated_portal_case() as case_path:
        tmp_path = case_path
        scheduler = Scheduler(
            max_concurrency=4,
            dispatch_interval_min_seconds=0,
            dispatch_interval_max_seconds=0,
        )
        worker_id = "dev:priority-fairness-worker"
        capabilities = ["agent_session:codex"]
        scheduler.register_worker(
            worker_id=worker_id,
            build_id="test-build",
            capabilities=capabilities,
        )
        for actor, priority in (("A", 0), ("B", 10)):
            scheduler.submit(ExecutionRequest.new(
                actor=actor,
                prompt=actor,
                service_type="agent_session",
                provider="codex",
                operation="turn",
                priority=priority,
                session=SessionOptions(
                    mode="persistent",
                    binding_id=f"provider:priority-{actor}",
                    backend_route_id="one",
                ),
                system_instructions=portal_system_instructions("resident"),
            ))

        claimed = scheduler.claim(
            worker_id=worker_id,
            capabilities=capabilities,
        )

        assert claimed["claimed"] is True
        assert claimed["execution"]["actor"] == "B"
    with subtests.test(contract='test_scheduler_does_not_replay_expired_running_execution'), isolated_portal_case() as case_path:
        tmp_path = case_path
        scheduler, server, thread, client, service = _stack(tmp_path)
        worker = SchedulerClient(
            worker_id="explore-1:lost-running-worker",
            build_id="test-build",
            capabilities=["agent_session:codex"],
            service_client=client,
        )
        try:
            worker.register()
            request = ExecutionRequest.new(
                actor="sample_admin",
                prompt="do not replay me",
                service_type="agent_session",
                provider="codex",
                operation="turn",
            )
            service.submit(request)
            claim = worker.claim()
            assert claim["claimed"]
            worker.start(request.execution_id, claim["claim_token"])
            with connect(scheduler.database) as connection:
                connection.execute(
                    '\n                    UPDATE executions\n                    SET lease_expires_at = TIMESTAMPTZ \'1970-01-01 00:00:01+00\'\n                    WHERE execution_id = %s\n                    ',
                    (request.execution_id,),
                )

            scheduler.maintain()
            # Not replayed: the next Worker only interrupts the Provider work
            # the lost one may have started, then fails it.
            recovered = service.status(request.execution_id)
            assert recovered["status"] == "queued"
            assert recovered["scheduler"]["attempts"] == 1
            assert recovered["events"][-1]["type"] == "requeued"
            abandoned = recovered["events"][-1]["details"]["abandoned"]
            assert abandoned["status"] == "failed"
            assert "lease expired after backend execution started" in abandoned["error"]
        finally:
            _close(server, thread)
    with subtests.test(contract='test_execution_service_runs_maintenance_from_server_cycle'), isolated_portal_case() as case_path:
        tmp_path = case_path
        server = ExecutionServiceServer(
            tmp_path / "execution-service.sock",
            Scheduler(),
            maintenance_interval_seconds=30,
        )
        try:
            server._next_maintenance_at = 0
            with (
                mock.patch(
                    "management_portal.execution.service.time.monotonic",
                    return_value=100,
                ),
                mock.patch.object(server, "run_maintenance") as maintain,
            ):
                server.service_actions()
                server.service_actions()
            maintain.assert_called_once_with()
        finally:
            server.server_close()
    with subtests.test(contract='test_scheduler_finalizes_cancelled_execution_after_lease_expiry'), isolated_portal_case() as case_path:
        tmp_path = case_path
        scheduler, server, thread, client, service = _stack(tmp_path)
        worker = SchedulerClient(
            worker_id="explore-1:lost-cancel-worker",
            build_id="test-build",
            capabilities=["agent_session:codex"],
            service_client=client,
        )
        try:
            worker.register()
            request = ExecutionRequest.new(
                actor="sample_admin",
                prompt="cancel after worker loss",
                service_type="agent_session",
                provider="codex",
                operation="turn",
            )
            service.submit(request)
            claim = worker.claim()
            assert claim["claimed"]
            worker.start(request.execution_id, claim["claim_token"])
            service.cancel(
                request.execution_id,
                actor="sample_admin",
                reason="stop the lost execution",
            )
            with connect(scheduler.database) as connection:
                connection.execute(
                    '\n                    UPDATE executions\n                    SET lease_expires_at = TIMESTAMPTZ \'1970-01-01 00:00:01+00\'\n                    WHERE execution_id = %s\n                    ',
                    (request.execution_id,),
                )

            scheduler.maintain()
            # Cancelled once the next Worker has interrupted the Provider work.
            recovered = service.status(request.execution_id)
            assert recovered["status"] == "queued"
            assert recovered["scheduler"]["cancel_requested"] is True
            assert recovered["events"][-1]["type"] == "requeued"
            assert recovered["events"][-1]["details"]["abandoned"]["status"] == "cancelled"
            assert "lease expired" in recovered["events"][-1]["details"]["abandoned"]["error"]
        finally:
            _close(server, thread)


def test_scheduler_round_robins_queued_executions_by_actor() -> None:
    """调度器按 actor 轮流派发排队执行，重启后轮转顺序不变"""
    scheduler = Scheduler(
        max_concurrency=16,
        dispatch_interval_min_seconds=0,
        dispatch_interval_max_seconds=0,
    )
    worker_id = "dev:actor-fairness-worker"
    capabilities = ["agent_session:codex"]
    scheduler.register_worker(
        worker_id=worker_id,
        build_id="test-build",
        capabilities=capabilities,
    )
    requests_by_actor: dict[str, list[ExecutionRequest]] = {}
    for actor, count in (("A", 5), ("B", 2), ("C", 1)):
        requests_by_actor[actor] = []
        for index in range(count):
            request = ExecutionRequest.new(
                actor=actor,
                prompt=f"{actor}-{index}",
                service_type="agent_session",
                provider="codex",
                operation="turn",
                session=SessionOptions(
                    mode="persistent",
                    binding_id=f"provider:{actor}-{index}",
                    backend_route_id="one",
                ),
                system_instructions=portal_system_instructions("resident"),
            )
            scheduler.submit(request)
            requests_by_actor[actor].append(request)

    assert scheduler.snapshot(
        requests_by_actor["A"][0].execution_id,
    )["scheduler"]["queue_position"] == 1
    assert scheduler.snapshot(
        requests_by_actor["B"][0].execution_id,
    )["scheduler"]["queue_position"] == 2
    assert scheduler.snapshot(
        requests_by_actor["C"][0].execution_id,
    )["scheduler"]["queue_position"] == 3
    assert scheduler.snapshot(
        requests_by_actor["A"][1].execution_id,
    )["scheduler"]["queue_position"] == 4

    claimed_actors = []
    for _index in range(8):
        if _index == 3:
            scheduler = Scheduler(
                max_concurrency=16,
                dispatch_interval_min_seconds=0,
                dispatch_interval_max_seconds=0,
            )
        claimed = scheduler.claim(
            worker_id=worker_id,
            capabilities=capabilities,
        )
        assert claimed["claimed"] is True
        claimed_actors.append(claimed["execution"]["actor"])

    assert claimed_actors == ["A", "B", "C", "A", "B", "A", "A", "A"]


def test_execution_service_health_is_read_only_and_maintenance_projects_terminal_state(
    tmp_path: Path,
) -> None:
    """执行服务健康检查只读，维护周期才把调度器终态同步到 AgentData"""
    scheduler = Scheduler()
    agent_data = AgentDataStore()
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="do not leave canonical execution running",
        agent_id="resident-probe",
        service_type="agent_session",
        provider="kimi",
        operation="turn",
    )
    scheduler.submit(request)
    agent_data.begin_execution(
        execution_id=request.execution_id,
        agent_id="resident-probe",
        actor_ref=request.actor,
        provider=request.provider,
        started_at="2026-08-25T00:00:00.000Z",
    )
    finished_at = "2026-08-25T00:01:00.000Z"
    with connect(scheduler.database) as connection:
        connection.execute(
            "\n            UPDATE executions\n            SET status = 'failed', error = 'worker lease expired', updated_at = %s\n            WHERE execution_id = %s\n            ",
            (finished_at, request.execution_id),
        )

    server = ExecutionServiceServer(
        tmp_path / "execution-service.sock",
        scheduler,
        instance="explore-1",
        agent_data=agent_data,
    )
    try:
        with mock.patch.object(
            server.notification_publisher,
            "publish",
        ) as publish:
            health = server.dispatch("health", {})
            canonical = agent_data.execution(request.execution_id)
            assert canonical is not None
            assert canonical["status"] == "running"
            publish.assert_not_called()

            server.run_maintenance()
            publish.assert_called_once()
    finally:
        server.server_close()

    assert set(health) == {
        "ok",
        "schema",
        "instance",
        "build_id",
        "scheduler",
        "notifications",
        "agent_data",
    }
    canonical = agent_data.execution(request.execution_id)
    assert canonical is not None
    assert canonical["status"] == "failed"
    assert datetime.fromisoformat(canonical["finished_at"]) == datetime.fromisoformat(finished_at)


def test_execution_maintenance_preserves_worker_terminal_fact_after_stale_scan(tmp_path: Path) -> None:
    """维护扫描基于过期快照时不覆盖 worker 已写入的终态"""
    scheduler = Scheduler()
    store = AgentDataStore()
    request = ExecutionRequest.new(
        actor="sample_admin", prompt="terminal race", service_type="agent_session",
        provider="kimi", operation="turn",
    )
    scheduler.submit(request)
    store.begin_execution(
        execution_id=request.execution_id, agent_id=request.agent_id,
        actor_ref=request.actor, provider=request.provider,
        started_at="2026-08-25T00:00:00.000Z",
    )
    stale_running = store.running_executions()
    store.finish_execution(
        request.execution_id, status="completed",
        finished_at="2026-08-25T00:01:00.000Z",
        usage={"input_tokens": 41, "output_tokens": 9},
    )
    worker_fact = store.execution(request.execution_id)
    stale_snapshot = {
        "status": "failed", "result": {},
        "scheduler": {"updated_at": "2026-08-25T00:01:01.000Z"},
    }
    server = ExecutionServiceServer(
        tmp_path / "terminal-race.sock", scheduler, instance="explore-1", agent_data=store,
    )
    try:
        with (
            mock.patch.object(store, "running_executions", return_value=stale_running),
            mock.patch.object(scheduler, "snapshot", return_value=stale_snapshot),
            mock.patch.object(server.notification_publisher, "publish") as publish,
        ):
            server._reconcile_terminal_agent_data()
            publish.assert_not_called()
        assert store.execution(request.execution_id) == worker_fact
        with pytest.raises(ValueError, match="terminal fact"):
            store.finish_execution(request.execution_id, status="failed")
    finally:
        server.server_close()


def test_scheduler_requeues_expired_resumable_provider_turn_from_checkpoint(
    tmp_path: Path,
) -> None:
    """可恢复的 Provider 轮次租约过期后带检查点回到队列，旧 claim 失效"""
    scheduler, server, thread, client, service = _stack(tmp_path)
    worker = SchedulerClient(
        worker_id="explore-1:recovering-worker",
        build_id="test-build",
        capabilities=["agent_session:codex"],
        service_client=client,
    )
    try:
        worker.register()
        request = ExecutionRequest.new(
            actor="sample_admin",
            prompt="resume this provider turn",
            service_type="agent_session",
            provider="codex",
            operation="turn",
        )
        service.submit(request)
        claim = worker.claim()
        token = claim["claim_token"]
        worker.start(request.execution_id, token)
        worker.checkpoint(
            request.execution_id,
            token,
            {
                "provider": "codex",
                "operation": "turn",
                "backend_session_id": "thread-1",
                "backend_route_id": "one",
                "turn_id": "turn-1",
                "context_attached": True,
                "resumable": True,
            },
        )
        with connect(scheduler.database) as connection:
            connection.execute(
                '\n                UPDATE executions\n                SET lease_expires_at = TIMESTAMPTZ \'1970-01-01 00:00:01+00\'\n                WHERE execution_id = %s\n                ',
                (request.execution_id,),
            )

        scheduler.maintain()
        recovered = service.status(request.execution_id)
        assert recovered["status"] == "queued"
        with connect(scheduler.database) as connection:
            stored_checkpoint = json_value(
                connection.execute(
                    'SELECT checkpoint_json FROM executions WHERE execution_id = %s',
                    (request.execution_id,),
                ).fetchone()[0]
            )
        assert stored_checkpoint["turn_id"] == "turn-1"
        assert recovered["scheduler"]["attempts"] == 1
        assert recovered["events"][-1]["type"] == "requeued"
        with pytest.raises(ExecutionClaimOwnershipLost):
            worker.heartbeat(
                request.execution_id,
                token,
                lease_seconds=90,
            )
    finally:
        _close(server, thread)


def test_contract_rejects_unsupported_provider_operations(subtests, isolated_portal_case):
    """执行请求拒绝 claude create、dsh fork 等 Provider 不支持的操作"""
    for provider, operation in [
        ("claude", "create"),
        ("dsh", "fork"),
    ]:
        with subtests.test(provider=provider, operation=operation), isolated_portal_case() as case_path:
            with pytest.raises(ValueError, match="does not support"):
                ExecutionRequest.new(
                    actor="sample_admin",
                    prompt=operation,
                    service_type="agent_session",
                    provider=provider,
                    operation=operation,
                    session=SessionOptions(
                        mode="persistent",
                        binding_id="provider:test",
                    ),
                )


def test_worker_control_wait_wakes_immediately_for_cancellation_contracts(tmp_path, subtests, isolated_portal_case):
    """取消请求立即唤醒 worker 的控制等待，排队或启动中的 codex 轮次接收持久追加输入

    :module: resident.fork-handoff
    :importance: P1
    """
    with subtests.test(contract='test_worker_control_wait_wakes_immediately_for_cancellation'), isolated_portal_case() as case_path:
        tmp_path = case_path
        _scheduler, server, thread, client, service = _stack(tmp_path)
        worker = SchedulerClient(
            worker_id="explore-1:control-wait-worker",
            build_id="test-build",
            capabilities=["agent_session:codex"],
            service_client=client,
        )
        try:
            worker.register()
            request = ExecutionRequest.new(
                actor="sample_admin",
                prompt="wait for explicit cancellation",
                service_type="agent_session",
                provider="codex",
                operation="turn",
            )
            service.submit(request)
            claim = worker.claim(wait_seconds=0.1)
            token = claim["claim_token"]
            worker.start(request.execution_id, token)

            result: dict[str, object] = {}
            waiter = threading.Thread(
                target=lambda: result.update(
                    worker.wait_for_control(
                        request.execution_id,
                        token,
                        timeout_seconds=2.0,
                    )
                )
            )
            waiter.start()
            time.sleep(0.1)
            assert waiter.is_alive()

            started_at = time.monotonic()
            service.cancel(request.execution_id, actor="sample_admin")
            waiter.join(timeout=1)

            assert not waiter.is_alive()
            assert time.monotonic() - started_at < 0.75
            assert result == {
                "cancel_requested": True,
                "handover_requested": False,
                "status": "running",
            }
        finally:
            _close(server, thread)
    with subtests.test(contract='test_starting_codex_turn_accepts_durable_appended_input'), isolated_portal_case() as case_path:
        tmp_path = case_path
        _scheduler, server, thread, client, service = _stack(tmp_path)
        worker = SchedulerClient(
            worker_id="explore-1:starting-input-control-worker",
            build_id="test-build",
            capabilities=["agent_session:codex"],
            service_client=client,
        )
        try:
            worker.register()
            request = ExecutionRequest.new(
                actor="sample_admin",
                prompt="start the task",
                service_type="agent_session",
                provider="codex",
                operation="turn",
            )
            service.submit(request)
            claim = worker.claim(wait_seconds=0.1)
            token = claim["claim_token"]
            worker.start(request.execution_id, token)

            submitted = service.append_input(
                request.execution_id,
                actor="sample_admin",
                input=[{"type": "text", "text": "include this when the turn starts"}],
            )
            control = worker.wait_for_control(
                request.execution_id,
                token,
                timeout_seconds=1.0,
            )["turn_controls"][0]

            assert submitted["status"] == "pending"
            assert control["expected_turn_id"] == ""
            assert control["input"] == [
                {"type": "text", "text": "include this when the turn starts"}
            ]
        finally:
            _close(server, thread)
    with subtests.test(contract='test_queued_codex_turn_accepts_durable_appended_input'), isolated_portal_case() as case_path:
        tmp_path = case_path
        _scheduler, server, thread, client, service = _stack(tmp_path)
        worker = SchedulerClient(
            worker_id="explore-1:queued-input-control-worker",
            build_id="test-build",
            capabilities=["agent_session:codex"],
            service_client=client,
        )
        try:
            worker.register()
            request = ExecutionRequest.new(
                actor="sample_admin",
                prompt="start the task",
                service_type="agent_session",
                provider="codex",
                operation="turn",
            )
            service.submit(request)

            submitted = service.append_input(
                request.execution_id,
                actor="sample_admin",
                input=[{"type": "text", "text": "include this after dispatch"}],
            )
            claim = worker.claim(wait_seconds=0.1)
            token = claim["claim_token"]
            worker.start(request.execution_id, token)
            control = worker.wait_for_control(
                request.execution_id,
                token,
                timeout_seconds=1.0,
            )["turn_controls"][0]

            assert submitted["status"] == "pending"
            assert control["expected_turn_id"] == ""
            assert control["input"] == [
                {"type": "text", "text": "include this after dispatch"}
            ]
        finally:
            _close(server, thread)
    with subtests.test(contract='test_scheduler_heartbeat_does_not_change_business_updated_at'), isolated_portal_case() as case_path:
        tmp_path = case_path
        _scheduler, server, thread, client, service = _stack(tmp_path)
        worker = SchedulerClient(
            worker_id="explore-1:heartbeat-worker",
            build_id="test-build",
            capabilities=["agent_session:codex"],
            service_client=client,
        )
        try:
            worker.register()
            request = ExecutionRequest.new(
                actor="sample_admin",
                prompt="renew only the lease",
                service_type="agent_session",
                provider="codex",
                operation="turn",
            )
            service.submit(request)
            claim = worker.claim(wait_seconds=0.1)
            token = claim["claim_token"]
            started = worker.start(request.execution_id, token)

            worker.heartbeat(request.execution_id, token)
            current = service.status(request.execution_id)

            assert current["scheduler"]["updated_at"] == (
                started["scheduler"]["updated_at"]
            )
            assert current["scheduler"]["lease_expires_at"] >= (
                started["scheduler"]["lease_expires_at"]
            )
        finally:
            _close(server, thread)
    with subtests.test(contract='test_scheduler_serializes_concurrent_turns_by_execution_binding'), isolated_portal_case() as case_path:
        tmp_path = case_path
        scheduler, server, thread, client, service = _stack(tmp_path)
        worker = SchedulerClient(
            worker_id="explore-1:binding-worker",
            build_id="test-build",
            capabilities=["agent_session:codex"],
            service_client=client,
        )
        try:
            worker.register()
            requests = [
                ExecutionRequest.new(
                    actor="sample_admin",
                    prompt=prompt,
                    service_type="agent_session",
                    provider="codex",
                    operation="turn",
                    session=SessionOptions(
                        mode="persistent",
                        binding_id=binding_id,
                    ),
                )
                for prompt, binding_id in (
                    ("first", "resident:test-main"),
                    ("second", "resident:test-main"),
                    ("parallel", "resident:other:main"),
                )
            ]
            for request in requests:
                service.submit(request)

            first = worker.claim()
            parallel = worker.claim()

            assert first["execution"]["request"]["execution_id"] == requests[0].execution_id
            assert parallel["execution"]["request"]["execution_id"] == requests[2].execution_id
            assert service.status(requests[1].execution_id)["status"] == "queued"
        finally:
            _close(server, thread)
    with subtests.test(contract='test_execution_preparation_persists_codex_route_before_queueing'), isolated_portal_case() as case_path:
        tmp_path = case_path
        _scheduler, server, thread, _client, service = _stack(tmp_path)
        try:
            request = ExecutionRequest.new(
                actor="sample_admin",
                prompt="route before scheduling",
                service_type="agent_session",
                provider="codex",
                operation="turn",
                system_instructions=portal_system_instructions("resident"),
            )

            submitted = service.submit(request)

            assert submitted["request"]["session"]["backend_route_id"] == "one"
            queued_event = next(
                event
                for event in submitted["events"]
                if event["type"] == "queued"
            )
            assert queued_event["details"]["capacity_key"] == "codex:one"
        finally:
            _close(server, thread)


def test_execution_wait_wakes_immediately_when_worker_finishes_cancelled(
    tmp_path: Path,
) -> None:
    """worker 以 cancelled 结束执行时等待者立即被唤醒"""
    scheduler = Scheduler(
        dispatch_interval_min_seconds=0,
        dispatch_interval_max_seconds=0,
    )
    worker_id = "dev:terminal-wake-worker"
    capabilities = ["agent_session:codex"]
    scheduler.register_worker(
        worker_id=worker_id,
        build_id="test-build",
        capabilities=capabilities,
    )
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="stop this turn",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        session=SessionOptions(
            mode="persistent",
            binding_id="resident:terminal-wake",
            backend_route_id="zero",
        ),
    )
    scheduler.submit(request)
    claim = scheduler.claim(
        worker_id=worker_id,
        capabilities=capabilities,
    )
    claim_token = str(claim["claim_token"])
    scheduler.start(
        request.execution_id,
        worker_id=worker_id,
        claim_token=claim_token,
    )
    running = scheduler.snapshot(request.execution_id)
    result: dict[str, object] = {}
    waiter = threading.Thread(
        target=lambda: result.update(
            scheduler.wait_for_change(
                request.execution_id,
                after_updated_at=str(running["scheduler"]["updated_at"]),
                after_sequence=max(
                    int(event["sequence"])
                    for event in running["events"]
                ),
                timeout_seconds=2.0,
            )
        )
    )
    waiter.start()
    time.sleep(0.1)
    assert waiter.is_alive()

    started_at = time.monotonic()
    scheduler.finish(
        ExecutionResult(
            execution_id=request.execution_id,
            status="cancelled",
            error="stopped by user",
        ),
        worker_id=worker_id,
        claim_token=claim_token,
    )
    waiter.join(timeout=1)

    assert not waiter.is_alive()
    assert time.monotonic() - started_at < 0.75
    assert result["status"] == "cancelled"


def test_active_codex_turn_accepts_durable_appended_input(
    tmp_path: Path,
) -> None:
    """运行中的 codex 轮次接收持久追加输入，按 control_id 幂等，送达后记入消息来源"""
    _scheduler, server, thread, client, service = _stack(tmp_path)
    worker = SchedulerClient(
        worker_id="explore-1:input-control-worker",
        build_id="test-build",
        capabilities=["agent_session:codex"],
        service_client=client,
    )
    try:
        worker.register()
        request = ExecutionRequest.new(
            actor="sample_admin",
            prompt="start the task",
            agent_id="resident_333333333333",
            service_type="agent_session",
            provider="codex",
            operation="turn",
        )
        service.submit(request)
        claim = worker.claim(wait_seconds=0.1)
        token = claim["claim_token"]
        worker.start(request.execution_id, token)
        worker.checkpoint(
            request.execution_id,
            token,
            {
                "provider": "codex",
                "backend_session_id": "thread-active",
                "backend_route_id": "one",
                "turn_id": "turn-active",
                "resumable": True,
            },
        )

        control_id = "control-111111111111111111111111"
        store = server.agent_data
        store.begin_execution(execution_id=request.execution_id, agent_id=request.agent_id, actor_ref=request.actor,
                              provider="codex", started_at="2026-09-11T00:00:00Z")
        session = store.ensure_provider_session(provider="codex", backend_route_id="one", backend_session_id="thread-active",
                                                agent_id=request.agent_id, actor_ref=request.actor)
        store.bind_execution_provider_session(request.execution_id, provider_session_ref=session["provider_session_ref"])
        store.record_input_acceptance(request.execution_id, agent_id=request.agent_id, provider_turn_id="turn-active")
        message_id = "msg_" + "c" * 32
        store.display_chat_message(request.agent_id, {
            "message_id": message_id, "role": "user", "author_id": "CASE-A",
            "content_parts": [{"type": "text", "text": "focus on the UI first"}], "created_at": "2026-09-11T00:00:01Z",
        })
        source_segment_id = store.chat_message_provenance(request.agent_id, message_id)["segment_id"]
        submitted = service.append_input(
            request.execution_id,
            actor="CASE-A",
            input=[{"type": "text", "text": "focus on the UI first"}],
            control_id=control_id,
            source_segment_id=source_segment_id,
        )
        assert submitted["status"] == "pending"
        retried = service.append_input(
            request.execution_id,
            actor="CASE-A",
            input=[{"type": "text", "text": "focus on the UI first"}],
            control_id=control_id,
        )
        assert retried["control_id"] == control_id
        assert retried["status"] == "pending"
        control = worker.wait_for_control(
            request.execution_id,
            token,
            timeout_seconds=1.0,
        )["turn_controls"][0]

        assert control["schema"] == "tfi.execution-input-control.v1"
        assert control["expected_turn_id"] == "turn-active"
        assert control["input"] == [
            {"type": "text", "text": "focus on the UI first"}
        ]
        delivered = worker.complete_input_control(
            request.execution_id,
            token,
            control["control_id"],
            delivered=True,
        )
        assert delivered["status"] == "delivered"
        assert delivered["control_id"] == control["control_id"]
        assert service.input_control(control_id, agent_id=request.agent_id)["status"] == "delivered"
        with pytest.raises(KeyError):
            service.input_control(control_id, agent_id="resident_other")
        assert store.chat_message_provenance(request.agent_id, message_id)["provider_turn_id"] == "turn-active"
        assert service.input_control(control_id, agent_id=request.agent_id)["status"] == "delivered"
        completed_retry = service.append_input(
            request.execution_id,
            actor="CASE-A",
            input=[{"type": "text", "text": "focus on the UI first"}],
            control_id=control_id,
        )
        assert completed_retry["status"] == "delivered"
    finally:
        _close(server, thread)


def test_codex_backend_maps_execution_input_control_to_native_steer() -> None:
    """codex 后端把执行输入控制作为原生 steer 送入当前轮次并回报结果"""
    client = mock.Mock()
    controls = [{
        "schema": "tfi.execution-input-control.v1",
        "control_id": "control-333333333333333333333333",
        "execution_id": "execution-444444444444444444444444",
        "actor": "sample_admin",
        "expected_turn_id": "turn-active",
        "input": [{"type": "text", "text": "adjust the active task"}],
    }]
    results: list[tuple[dict, bool, str]] = []

    def run_turn(*_args, **kwargs):
        control = kwargs["turn_control_provider"]()
        kwargs["turn_control_result_callback"](control, True, "")
        return mock.Mock(
            thread_id="019f0000-0000-7000-8000-000000000472",
            turn_id="turn-active",
            response="done",
            token_usage={},
        )

    client.run_turn.side_effect = run_turn
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="continue",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        cwd="/tmp",
        session=SessionOptions(
            mode="persistent",
            binding_id="provider:test-session",
            backend_route_id="one",
        ),
    )

    execution_backends.CodexBackend(
        client_factory=lambda _socket_path: client,
    ).execute(
        BackendInvocation(
            request=request,
            session=RuntimeSession(
                mode="persistent",
                binding_id="",
                backend_session_id="019f0000-0000-7000-8000-000000000472",
                backend_route_id="one",
                is_new_backend_session=False,
            ),
            user_prompt=request.prompt,
            system_context="",
            attach_system_context=False,
            is_new_backend_session=False,
        ),
        execution_backends.BackendContext(
            emit_event=lambda *_args: None,
            cancellation_requested=lambda: False,
            set_interrupt_target=lambda *_args: None,
            turn_control_provider=(
                lambda: controls.pop(0) if controls else None
            ),
            turn_control_result_callback=(
                lambda control, delivered, error: results.append(
                    (control, delivered, error)
                )
            ),
        ),
    )

    assert results == [(
        {
            "schema": "tfi.execution-input-control.v1",
            "control_id": "control-333333333333333333333333",
            "execution_id": "execution-444444444444444444444444",
            "actor": "sample_admin",
            "expected_turn_id": "turn-active",
            "input": [{"type": "text", "text": "adjust the active task"}],
        },
        True,
        "",
    )]


def test_codex_backend_maps_goal_to_native_protocol() -> None:
    """codex 的 goal 请求走原生 run_goal 协议，返回 Goal 状态和用量"""
    thread_id = "019f0000-0000-7000-8000-000000000480"
    turn_id = "019f0000-0000-7000-8000-000000000481"
    route = mock.Mock(
        account_id="one",
        socket_path=Path("/tmp/codex-one.sock"),
    )
    registry = mock.Mock()
    registry.get.return_value = route
    client = mock.Mock()
    client.run_goal.return_value = mock.Mock(
        thread_id=thread_id,
        turn_id=turn_id,
        response="goal finished",
        goal={
            "objective": "finish the portal goal integration",
            "status": "complete",
            "tokensUsed": 21,
        },
        token_usage={"last": {"totalTokens": 21}},
    )
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="/goal finish the portal goal integration",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        cwd="/tmp",
        runtime_options={
            "goal": {
                "action": "set",
                "objective": "finish the portal goal integration",
            },
        },
        session=SessionOptions(
            mode="persistent",
            binding_id="provider:test-session",
            backend_route_id="one",
        ),
    )

    result = execution_backends.CodexBackend(
        account_registry=registry,
        client_factory=lambda _socket_path: client,
    ).execute(
        BackendInvocation(
            request=request,
            session=RuntimeSession(
                mode="persistent",
                binding_id="",
                backend_session_id=thread_id,
                backend_route_id="one",
                is_new_backend_session=False,
            ),
            user_prompt=request.prompt,
            system_context="",
            attach_system_context=False,
            is_new_backend_session=False,
        ),
        execution_backends.BackendContext(
            emit_event=lambda *_args: None,
            cancellation_requested=lambda: False,
            set_interrupt_target=lambda *_args: None,
        ),
    )

    client.run_turn.assert_not_called()
    self_call = client.run_goal.call_args
    assert self_call.args == (thread_id,)
    assert self_call.kwargs["action"] == "set"
    assert self_call.kwargs["objective"] == (
        "finish the portal goal integration"
    )
    assert self_call.kwargs["config"]["features"] == {"goals": True}
    assert result.output["message"] == "goal finished"
    assert result.output["goal"]["status"] == "complete"
    assert result.usage["last"]["totalTokens"] == 21


def test_execution_preparation_uses_existing_binding_route_contracts(tmp_path, subtests, isolated_portal_case):
    """执行准备沿用绑定已有的账号路线，离线且无活动执行的 worker 才能注销

    :module: resident.fork-handoff
    :importance: P1
    """
    with subtests.test(contract='test_execution_preparation_uses_existing_binding_route'), isolated_portal_case() as case_path:
        tmp_path = case_path
        _scheduler, server, thread, _client, service = _stack(tmp_path)
        manager = SessionManager()
        _bind_session(manager,
            "resident:test-zero",
            "codex",
            "019f0000-0000-7000-8000-000000000801",
            backend_route_id="zero",
        )
        try:
            request = ExecutionRequest.new(
                actor="sample_admin",
                prompt="continue on the bound account",
                service_type="agent_session",
                provider="codex",
                operation="turn",
                session=SessionOptions(
                    mode="persistent",
                    binding_id="resident:test-zero",
                ),
                system_instructions=portal_system_instructions("resident"),
            )

            submitted = service.submit(request)

            assert submitted["request"]["session"]["backend_route_id"] == "zero"
        finally:
            _close(server, thread)
    with subtests.test(contract='test_draining_worker_does_not_pause_scheduler_dispatch'), isolated_portal_case() as case_path:
        tmp_path = case_path
        scheduler = Scheduler(
            dispatch_interval_min_seconds=0,
            dispatch_interval_max_seconds=0,
        )
        old_worker_id = "dev:old-worker"
        new_worker_id = "dev:new-worker"
        capabilities = ["chat_completion"]
        scheduler.register_worker(
            worker_id=old_worker_id,
            build_id="old-build",
            capabilities=capabilities,
        )
        scheduler.register_worker(
            worker_id=new_worker_id,
            build_id="new-build",
            capabilities=capabilities,
            state="standby",
        )
        scheduler.activate_worker(
            new_worker_id,
            previous_worker_id=old_worker_id,
        )
        request = ExecutionRequest.new(
            actor="rollout-test",
            prompt="dispatch while the previous generation drains",
            service_type="chat_completion",
            provider="openai",
            operation="generate",
        )
        queued = scheduler.submit(request)

        assert queued["status"] == "queued"
        assert scheduler.claim(
            worker_id=old_worker_id,
            capabilities=capabilities,
        ) == {"claimed": False, "reason": "worker_not_active", "worker_state": "draining"}
        claimed = scheduler.claim(
            worker_id=new_worker_id,
            capabilities=capabilities,
        )
        assert claimed["claimed"] is True
        assert claimed["execution"]["execution_id"] == request.execution_id
    with subtests.test(contract='test_scheduler_unregisters_only_offline_workers_without_active_work'), isolated_portal_case() as case_path:
        tmp_path = case_path
        scheduler = Scheduler(
            dispatch_interval_min_seconds=0,
            dispatch_interval_max_seconds=0,
        )
        worker_id = "dev:retired-worker"
        scheduler.register_worker(
            worker_id=worker_id,
            build_id="test-build",
            capabilities=["chat_completion"],
        )
        with pytest.raises(RuntimeError, match="state active"):
            scheduler.unregister_worker(worker_id)

        request = ExecutionRequest.new(
            actor="worker-cleanup",
            prompt="finish before unregister",
            service_type="chat_completion",
            provider="openai",
            operation="generate",
        )
        scheduler.submit(request)
        claim = scheduler.claim(
            worker_id=worker_id,
            capabilities=["chat_completion"],
        )
        token = str(claim["claim_token"])
        scheduler.start(
            request.execution_id,
            worker_id=worker_id,
            claim_token=token,
        )
        scheduler.set_worker_state(worker_id, "offline")
        with pytest.raises(RuntimeError, match="active executions"):
            scheduler.unregister_worker(worker_id)

        scheduler.finish(
            ExecutionResult(
                execution_id=request.execution_id,
                status="completed",
                output={"message": "done"},
            ),
            worker_id=worker_id,
            claim_token=token,
        )

        assert scheduler.unregister_worker(worker_id) is True
        assert scheduler.unregister_worker(worker_id) is False
        assert scheduler.health()["workers"] == []
    with subtests.test(contract='test_scheduler_prunes_stale_worker_registrations_while_refreshing_caller'), isolated_portal_case() as case_path:
        tmp_path = case_path
        scheduler = Scheduler(
            worker_heartbeat_timeout_seconds=30,
            dispatch_interval_min_seconds=0,
            dispatch_interval_max_seconds=0,
        )
        capabilities = ["chat_completion"]
        with mock.patch(
            "management_portal.execution.scheduler.time.time",
            return_value=100.0,
        ):
            scheduler.register_worker(
                worker_id="dev:current-worker",
                build_id="current-build",
                capabilities=capabilities,
            )
            scheduler.register_worker(
                worker_id="dev:stale-worker",
                build_id="stale-build",
                capabilities=capabilities,
                state="standby",
            )

        with mock.patch(
            "management_portal.execution.scheduler.time.time",
            return_value=131.0,
        ):
            assert scheduler.claim(
                worker_id="dev:current-worker",
                capabilities=capabilities,
            ) == {"claimed": False, "reason": "empty"}
            # A claim refreshes only its own Worker; maintenance prunes the rest.
            assert [worker["worker_id"] for worker in scheduler.health()["workers"]] == [
                "dev:current-worker", "dev:stale-worker",
            ]
            assert scheduler.maintain()["expired_worker_registrations"] == 1
            health = scheduler.health()

        assert [worker["worker_id"] for worker in health["workers"]] == [
            "dev:current-worker",
        ]
    with subtests.test(contract='test_scheduler_health_is_read_only_and_maintenance_prunes_stale_worker'), isolated_portal_case() as case_path:
        tmp_path = case_path
        scheduler = Scheduler(
            worker_heartbeat_timeout_seconds=30,
        )
        with mock.patch(
            "management_portal.execution.scheduler.time.time",
            return_value=100.0,
        ):
            scheduler.register_worker(
                worker_id="dev:abandoned-worker",
                build_id="abandoned-build",
                capabilities=["chat_completion"],
            )

        with mock.patch(
            "management_portal.execution.scheduler.time.time",
            return_value=131.0,
        ):
            health = scheduler.health()
            maintenance = scheduler.maintain()
            maintained_health = scheduler.health()

        assert health["expired_worker_registrations"] == 0
        assert [worker["worker_id"] for worker in health["workers"]] == [
            "dev:abandoned-worker",
        ]
        assert maintenance["expired_worker_registrations"] == 1
        assert maintained_health["expired_worker_registrations"] == 1
        assert maintained_health["workers"] == []


def test_scheduler_dispatch_intervals_are_independent_by_capacity_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """各容量分区独立计算派发间隔，账号单独配置的间隔只作用于该账号"""
    monkeypatch.setattr(
        "management_portal.execution.scheduler.time.time",
        lambda: 2_000.0,
    )
    scheduler = Scheduler(
        max_concurrency=4,
        capacity_limits={"codex:one": 4, "codex:zero": 4},
        dispatch_interval_min_seconds=2,
        dispatch_interval_max_seconds=6,
    )
    scheduler.configure_capacity("codex:one", 4, 7, 9, actor="admin")
    worker_id = "dev:independent-partition-worker"
    capabilities = ["agent_session:codex", "chat_completion"]
    scheduler.register_worker(
        worker_id=worker_id,
        build_id="test-build",
        capabilities=capabilities,
    )
    requests = [
        ExecutionRequest.new(
            actor=f"resident-{route}",
            prompt=f"message for {route}",
            service_type="agent_session",
            provider="codex",
            operation="turn",
            session=SessionOptions(
                mode="persistent",
                binding_id=f"resident:{route}",
                backend_route_id=route,
            ),
        )
        for route in ("one", "zero")
    ]
    requests.append(
        ExecutionRequest.new(
            actor="review",
            prompt="non-conversational AI request",
            service_type="chat_completion",
            provider="openai",
            operation="generate",
        )
    )
    requests.append(
        ExecutionRequest.new(
            actor="another-resident",
            prompt="second message for one",
            service_type="agent_session",
            provider="codex",
            operation="turn",
            session=SessionOptions(
                mode="persistent",
                binding_id="resident:another",
                backend_route_id="one",
            ),
        )
    )
    for request in requests:
        scheduler.submit(request)

    with mock.patch(
        "management_portal.execution.scheduler.random.uniform",
        side_effect=lambda lower, upper: (lower + upper) / 2,
    ) as interval:
        claims = [
            scheduler.claim(
                worker_id=worker_id,
                capabilities=capabilities,
            )
            for _ in range(3)
        ]
        blocked = scheduler.claim(
            worker_id=worker_id,
            capabilities=capabilities,
        )

    assert all(claim["claimed"] for claim in claims)
    assert interval.call_count == 3
    interval.assert_any_call(2.0, 6.0)
    interval.assert_any_call(7.0, 9.0)
    assert blocked == {
        "claimed": False,
        "reason": "dispatch_interval",
    }
    health = scheduler.health()
    assert set(health["dispatch_interval"]["partitions"]) == {
        "codex:one",
        "codex:zero",
        "shared-ai",
    }
    assert {
        state["interval_seconds"]
        for state in health["dispatch_interval"]["partitions"].values()
    } == {4.0, 8.0}
    assert health["capacity_partitions"]["codex:one"][
        "dispatch_interval_min_seconds"
    ] == 7


def test_session_release_bypasses_model_dispatch_interval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """会话 release 走会话控制分区，不受模型派发间隔限制"""
    monkeypatch.setattr(
        "management_portal.execution.scheduler.time.time",
        lambda: 2_000.0,
    )
    scheduler = Scheduler(
        max_concurrency=4,
        dispatch_interval_min_seconds=60,
        dispatch_interval_max_seconds=60,
    )
    worker_id = "dev:session-release-worker"
    capabilities = ["agent_session:codex"]
    scheduler.register_worker(
        worker_id=worker_id,
        build_id="test-build",
        capabilities=capabilities,
    )
    first_turn = ExecutionRequest.new(
        actor="resident-one",
        prompt="first model turn",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        session=SessionOptions(
            mode="persistent",
            binding_id="resident:one",
            backend_route_id="zero",
        ),
    )
    scheduler.submit(first_turn)
    first_claim = scheduler.claim(
        worker_id=worker_id,
        capabilities=capabilities,
    )
    scheduler.finish(
        ExecutionResult(
            execution_id=first_turn.execution_id,
            status="completed",
            output={"message": "done"},
        ),
        worker_id=worker_id,
        claim_token=str(first_claim["claim_token"]),
    )

    blocked_turn = ExecutionRequest.new(
        actor="resident-two",
        prompt="second model turn",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        session=SessionOptions(
            mode="persistent",
            binding_id="resident:two",
            backend_route_id="zero",
        ),
    )
    release = ExecutionRequest.new(
        actor="resident-three",
        prompt="release backend session",
        service_type="agent_session",
        provider="codex",
        operation="release",
        priority=100,
        session=SessionOptions(
            mode="persistent",
            binding_id="resident:three",
            backend_route_id="zero",
        ),
    )
    scheduler.submit(blocked_turn)
    scheduler.submit(release)

    release_claim = scheduler.claim(
        worker_id=worker_id,
        capabilities=capabilities,
    )

    assert release_claim["claimed"] is True
    assert release_claim["execution"]["execution_id"] == release.execution_id
    control_partition = scheduler.health()["capacity_partitions"][
        "session-control:codex"
    ]
    assert control_partition["dispatch_interval_min_seconds"] == 0
    assert control_partition["dispatch_interval_max_seconds"] == 0


def test_session_release_waits_for_the_same_binding(
    tmp_path: Path,
) -> None:
    """同一绑定有轮次运行时 release 等待，不被认领"""
    scheduler = Scheduler(
        dispatch_interval_min_seconds=0,
        dispatch_interval_max_seconds=0,
    )
    worker_id = "dev:session-release-binding-worker"
    capabilities = ["agent_session:codex"]
    scheduler.register_worker(
        worker_id=worker_id,
        build_id="test-build",
        capabilities=capabilities,
    )
    turn = ExecutionRequest.new(
        actor="resident-one",
        prompt="active model turn",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        session=SessionOptions(
            mode="persistent",
            binding_id="resident:one",
            backend_route_id="zero",
        ),
    )
    release = ExecutionRequest.new(
        actor="resident-one",
        prompt="release backend session",
        service_type="agent_session",
        provider="codex",
        operation="release",
        priority=100,
        session=SessionOptions(
            mode="persistent",
            binding_id="resident:one",
            backend_route_id="zero",
        ),
    )
    scheduler.submit(turn)
    turn_claim = scheduler.claim(
        worker_id=worker_id,
        capabilities=capabilities,
    )
    scheduler.start(
        turn.execution_id,
        worker_id=worker_id,
        claim_token=str(turn_claim["claim_token"]),
    )
    scheduler.submit(release)

    assert scheduler.claim(
        worker_id=worker_id,
        capabilities=capabilities,
    ) == {"claimed": False, "reason": "empty"}


def test_worker_registration_atomically_fences_replaced_generation_contracts(tmp_path, subtests, isolated_portal_case):
    """新一代 worker 激活时旧代原子转为 draining，退役 worker 不能自行恢复

    :module: resident.fork-handoff
    :importance: P1
    """
    with subtests.test(contract='test_worker_registration_atomically_fences_replaced_generation'), isolated_portal_case() as case_path:
        tmp_path = case_path
        scheduler = Scheduler()
        old_worker_id = "dev:execution-worker:old"
        new_worker_id = "dev:execution-worker:new"
        capabilities = ["chat_completion"]
        scheduler.register_worker(
            worker_id=old_worker_id,
            build_id="old-build",
            capabilities=capabilities,
        )

        scheduler.register_worker(
            worker_id=new_worker_id,
            build_id="new-build",
            capabilities=capabilities,
            state="standby",
        )
        activated = scheduler.activate_worker(
            new_worker_id,
            previous_worker_id=old_worker_id,
        )

        workers = {
            item["worker_id"]: item
            for item in scheduler.health()["workers"]
        }
        assert activated["previous_worker_id"] == old_worker_id
        assert workers[old_worker_id]["state"] == "draining"
        assert workers[new_worker_id]["state"] == "active"
        assert scheduler.claim(
            worker_id=old_worker_id,
            capabilities=capabilities,
        ) == {"claimed": False, "reason": "worker_not_active", "worker_state": "draining"}
    with subtests.test(contract='test_worker_activation_accepts_already_expired_previous_generation'), isolated_portal_case() as case_path:
        tmp_path = case_path
        scheduler = Scheduler()
        new_worker_id = "dev:execution-worker:new"
        scheduler.register_worker(
            worker_id=new_worker_id,
            build_id="new-build",
            capabilities=["chat_completion"],
            state="standby",
        )

        activated = scheduler.activate_worker(
            new_worker_id,
            previous_worker_id="dev:execution-worker:expired",
        )

        assert activated == {
            "worker_id": new_worker_id,
            "state": "active",
            "previous_worker_id": "dev:execution-worker:expired",
            "previous_state": "",
        }
        assert scheduler.health()["workers"][0]["state"] == "active"
    with subtests.test(contract='test_worker_activation_retires_other_generations_but_keeps_its_own'), isolated_portal_case() as case_path:
        tmp_path = case_path
        scheduler = Scheduler()
        release_worker_id = "release:execution-worker:old"
        dev_worker_id = "dev:execution-worker:new"
        capabilities = ["agent_session:codex"]
        scheduler.register_worker(
            worker_id=release_worker_id,
            build_id="release-old",
            capabilities=capabilities,
        )
        scheduler.register_worker(
            worker_id=dev_worker_id,
            build_id="dev-new",
            capabilities=capabilities,
            state="standby",
        )

        scheduler.activate_worker(dev_worker_id)

        workers = {
            item["worker_id"]: item
            for item in scheduler.health()["workers"]
        }
        assert workers[release_worker_id]["state"] == "draining"
        assert workers[dev_worker_id]["state"] == "active"
        assert scheduler.claim(
            worker_id=release_worker_id,
            capabilities=capabilities,
        ) == {"claimed": False, "reason": "worker_not_active", "worker_state": "draining"}

        # The Release pool then starts the same generation; it does not
        # retire the Dev Worker, so executions the Dev Worker took over in
        # this publish are not handed over a second time.
        release_new_id = "release:execution-worker:new"
        scheduler.register_worker(
            worker_id=release_new_id,
            build_id="dev-new",
            capabilities=capabilities,
            state="standby",
        )
        scheduler.activate_worker(
            release_new_id,
            previous_worker_id=release_worker_id,
        )

        workers = {
            item["worker_id"]: item
            for item in scheduler.health()["workers"]
        }
        assert workers[dev_worker_id]["state"] == "active"
        assert workers[release_new_id]["state"] == "active"
        assert workers[release_worker_id]["state"] == "draining"
        for worker_id in (dev_worker_id, release_new_id):
            assert scheduler.claim(
                worker_id=worker_id,
                capabilities=capabilities,
            ).get("reason") != "worker_not_active"

        # A retired Worker leaves by itself; its successor still names it.
        scheduler.handover_worker(release_worker_id)
        release_next_id = "release:execution-worker:next"
        scheduler.register_worker(
            worker_id=release_next_id,
            build_id="dev-new",
            capabilities=capabilities,
            state="standby",
        )
        assert scheduler.activate_worker(
            release_next_id,
            previous_worker_id=release_worker_id,
        )["previous_state"] == "offline"
    with subtests.test(contract='test_submit_wakes_worker_claim_wait_immediately'), isolated_portal_case() as case_path:
        tmp_path = case_path
        scheduler = Scheduler(
            dispatch_interval_min_seconds=0,
            dispatch_interval_max_seconds=0,
        )
        worker_id = "dev:execution-worker:submit-wakeup"
        capabilities = ["chat_completion"]
        scheduler.register_worker(
            worker_id=worker_id,
            build_id="test-build",
            capabilities=capabilities,
        )
        claimed: dict[str, object] = {}

        def wait_for_submission() -> None:
            claimed.update(
                scheduler.claim_wait(
                    worker_id=worker_id,
                    capabilities=capabilities,
                    timeout_seconds=5,
                )
            )

        waiting = threading.Thread(target=wait_for_submission)
        waiting.start()
        time.sleep(0.05)
        request = ExecutionRequest.new(
            actor="submit-wakeup-test",
            prompt="wake the waiting worker",
            service_type="chat_completion",
            provider="openai",
            operation="generate",
        )

        started = time.monotonic()
        scheduler.submit(request)
        waiting.join(timeout=0.5)

        assert not waiting.is_alive()
        assert time.monotonic() - started < 0.5
        assert claimed["claimed"] is True
        assert claimed["execution"]["execution_id"] == request.execution_id
    with subtests.test(contract='test_peer_scheduler_submit_wakes_worker_claim_wait_promptly'), isolated_portal_case() as case_path:
        tmp_path = case_path
        worker_scheduler = Scheduler(
            dispatch_interval_min_seconds=0,
            dispatch_interval_max_seconds=0,
        )
        submitting_scheduler = Scheduler(
            dispatch_interval_min_seconds=0,
            dispatch_interval_max_seconds=0,
        )
        worker_id = "release:execution-worker:peer-submit-wakeup"
        capabilities = ["chat_completion"]
        worker_scheduler.register_worker(
            worker_id=worker_id,
            build_id="release-build",
            capabilities=capabilities,
        )
        claimed: dict[str, object] = {}

        def wait_for_peer_submission() -> None:
            claimed.update(
                worker_scheduler.claim_wait(
                    worker_id=worker_id,
                    capabilities=capabilities,
                    timeout_seconds=5,
                )
            )

        waiting = threading.Thread(target=wait_for_peer_submission)
        waiting.start()
        time.sleep(0.05)
        request = ExecutionRequest.new(
            actor="peer-submit-wakeup-test",
            prompt="wake the worker through the shared Scheduler database",
            service_type="chat_completion",
            provider="openai",
            operation="generate",
        )

        started = time.monotonic()
        submitting_scheduler.submit(request)
        waiting.join(timeout=0.75)

        assert not waiting.is_alive()
        assert time.monotonic() - started < 0.75
        assert claimed["claimed"] is True
        assert claimed["execution"]["execution_id"] == request.execution_id
    with subtests.test(contract='test_streaming_updates_do_not_wake_idle_claim_waiters'), isolated_portal_case() as case_path:
        tmp_path = case_path
        worker_scheduler = Scheduler(
            dispatch_interval_min_seconds=0,
            dispatch_interval_max_seconds=0,
        )
        peer_scheduler = Scheduler(
            dispatch_interval_min_seconds=0,
            dispatch_interval_max_seconds=0,
        )
        worker_id = "dev:execution-worker:streaming-isolation"
        capabilities = ["agent_session:codex"]
        worker_scheduler.register_worker(
            worker_id=worker_id,
            build_id="test-build",
            capabilities=capabilities,
        )
        request = ExecutionRequest.new(
            actor="streaming-isolation-test",
            prompt="long running turn",
            service_type="agent_session",
            provider="codex",
            operation="turn",
            session=SessionOptions(
                mode="persistent",
                binding_id="resident:streaming-isolation",
                backend_route_id="zero",
            ),
        )
        worker_scheduler.submit(request)
        claimed = worker_scheduler.claim(
            worker_id=worker_id,
            capabilities=capabilities,
        )
        owner = {
            "worker_id": worker_id,
            "claim_token": claimed["claim_token"],
        }
        worker_scheduler.start(request.execution_id, **owner)
        original_claim = worker_scheduler.claim
        wait_result: dict[str, object] = {}
        first_check = threading.Event()

        def observed_claim(**kwargs):
            first_check.set()
            return original_claim(**kwargs)

        def wait_for_queue() -> None:
            wait_result.update(worker_scheduler.claim_wait(
                worker_id=worker_id,
                capabilities=capabilities,
                timeout_seconds=0.5,
            ))

        with mock.patch.object(
            worker_scheduler,
            "claim",
            side_effect=observed_claim,
        ) as claim_call:
            waiting = threading.Thread(target=wait_for_queue, daemon=True)
            waiting.start()
            assert first_check.wait(1)
            time.sleep(0.05)
            for index in range(8):
                peer_scheduler.checkpoint(
                    request.execution_id,
                    **owner,
                    checkpoint={
                        "provider": "codex",
                        "turn_id": f"turn-{index}",
                        "resumable": True,
                    },
                )
            time.sleep(0.15)
            assert waiting.is_alive()
            assert claim_call.call_count == 1
            waiting.join(timeout=1)
            assert not waiting.is_alive()
            assert claim_call.call_count == 2
            assert wait_result["claimed"] is False
    with subtests.test(contract='test_retired_worker_cannot_promote_itself'), isolated_portal_case() as case_path:
        tmp_path = case_path
        scheduler = Scheduler()
        worker_id = "dev:execution-worker:retired"
        scheduler.register_worker(
            worker_id=worker_id,
            build_id="retired-build",
            capabilities=["chat_completion"],
        )
        scheduler.set_worker_state(worker_id, "draining")

        with pytest.raises(
            RuntimeError,
            match="retired worker cannot reactivate",
        ):
            scheduler.register_worker(
                worker_id=worker_id,
                build_id="retired-build",
                capabilities=["chat_completion"],
                state="active",
            )
        with pytest.raises(ValueError, match="can only retire"):
            scheduler.set_worker_state(worker_id, "active")

        worker = next(
            item
            for item in scheduler.health()["workers"]
            if item["worker_id"] == worker_id
        )
        assert worker["state"] == "draining"


def test_worker_handover_requeues_resumable_turn_without_terminal_projection(
    tmp_path: Path,
) -> None:
    """worker 交接时可恢复轮次带检查点回到队列由新 worker 接续，不产生终态"""
    scheduler = Scheduler(
        dispatch_interval_min_seconds=0,
        dispatch_interval_max_seconds=0,
    )
    old_worker_id = "dev:worker:old"
    new_worker_id = "dev:worker:new"
    scheduler.register_worker(
        worker_id=old_worker_id,
        build_id="old",
        capabilities=["agent_session:codex"],
        pid=1,
    )
    scheduler.register_worker(
        worker_id=new_worker_id,
        build_id="new",
        capabilities=["agent_session:codex"],
        pid=2,
        state="standby",
    )
    request = ExecutionRequest.new(
        actor="sample_peer",
        prompt="continue the same accepted turn",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        system_instructions=portal_system_instructions("resident"),
        session=SessionOptions(
            mode="stateless",
            backend_route_id="zero",
        ),
    )
    scheduler.submit(request)
    claim = scheduler.claim(
        worker_id=old_worker_id,
        capabilities=["agent_session:codex"],
    )
    scheduler.start(
        request.execution_id,
        worker_id=old_worker_id,
        claim_token=claim["claim_token"],
    )
    scheduler.checkpoint(
        request.execution_id,
        worker_id=old_worker_id,
        claim_token=claim["claim_token"],
        checkpoint={
            "resumable": True,
            "backend_session_id": "thread-existing",
            "turn_id": "turn-existing",
        },
    )
    scheduler.activate_worker(
        new_worker_id,
        previous_worker_id=old_worker_id,
    )

    handover = scheduler.handover_worker(old_worker_id)
    resumed = scheduler.claim(
        worker_id=new_worker_id,
        capabilities=["agent_session:codex"],
    )

    assert handover["requeued_execution_ids"] == [request.execution_id]
    assert handover["blocked_execution_ids"] == []
    assert resumed["execution"]["execution_id"] == request.execution_id
    assert resumed["execution"]["checkpoint"]["turn_id"] == "turn-existing"
    assert resumed["execution"]["checkpoint"]["turn_start_reason"] == "backend_service_update"
    snapshot = scheduler.snapshot(request.execution_id)
    assert snapshot["status"] == "claimed"
    assert all(
        event["type"] not in {"failed", "cancelled"}
        for event in snapshot["events"]
    )
    with pytest.raises(PermissionError):
        scheduler.finish(
            ExecutionResult(
                execution_id=request.execution_id,
                status="completed",
                output={"message": "stale"},
            ),
            worker_id=old_worker_id,
            claim_token=claim["claim_token"],
        )


def test_draining_worker_cannot_receive_new_input_before_handover(
    tmp_path: Path,
) -> None:
    """交接中的旧 worker 只收到交接请求，追加输入留给接手的新 worker"""
    scheduler = Scheduler(
        dispatch_interval_min_seconds=0,
        dispatch_interval_max_seconds=0,
    )
    old_worker_id = "dev:worker:old-input"
    new_worker_id = "dev:worker:new-input"
    capabilities = ["agent_session:codex"]
    scheduler.register_worker(
        worker_id=old_worker_id,
        build_id="old",
        capabilities=capabilities,
    )
    scheduler.register_worker(
        worker_id=new_worker_id,
        build_id="new",
        capabilities=capabilities,
        state="standby",
    )
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="start on the old generation",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        system_instructions=portal_system_instructions("resident"),
        session=SessionOptions(mode="stateless", backend_route_id="zero"),
    )
    scheduler.submit(request)
    old_claim = scheduler.claim(
        worker_id=old_worker_id,
        capabilities=capabilities,
    )
    scheduler.start(
        request.execution_id,
        worker_id=old_worker_id,
        claim_token=old_claim["claim_token"],
    )
    scheduler.checkpoint(
        request.execution_id,
        worker_id=old_worker_id,
        claim_token=old_claim["claim_token"],
        checkpoint={
            "resumable": True,
            "backend_session_id": "thread-input",
            "turn_id": "turn-input",
        },
    )
    scheduler.activate_worker(
        new_worker_id,
        previous_worker_id=old_worker_id,
    )
    control = ExecutionInputControl.new(
        execution_id=request.execution_id,
        actor="sample_admin",
        input=[{"type": "text", "text": "send this to the latest worker"}],
        expected_turn_id="turn-input",
    )
    scheduler.submit_input_control(control)

    old_control = scheduler.wait_for_worker_control(
        request.execution_id,
        worker_id=old_worker_id,
        claim_token=old_claim["claim_token"],
        timeout_seconds=0.1,
    )
    assert old_control["handover_requested"] is True
    assert "input_controls" not in old_control

    scheduler.handover_worker(old_worker_id)
    new_claim = scheduler.claim(
        worker_id=new_worker_id,
        capabilities=capabilities,
    )
    new_control = scheduler.wait_for_worker_control(
        request.execution_id,
        worker_id=new_worker_id,
        claim_token=new_claim["claim_token"],
        timeout_seconds=0.1,
    )
    assert new_control["handover_requested"] is False
    assert [item["control_id"] for item in new_control["turn_controls"]] == [
        control.control_id
    ]


def test_scheduler_delivers_native_turn_settings_control(
    tmp_path: Path,
) -> None:
    """调度器把运行中轮次的设置变更作为 update_settings 控制送给 worker"""
    scheduler = Scheduler(
        dispatch_interval_min_seconds=0,
        dispatch_interval_max_seconds=0,
    )
    worker_id = "dev:worker:turn-settings"
    capabilities = ["agent_session:codex"]
    scheduler.register_worker(
        worker_id=worker_id,
        build_id="test",
        capabilities=capabilities,
    )
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="running turn",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        system_instructions=portal_system_instructions("resident"),
        session=SessionOptions(mode="stateless", backend_route_id="zero"),
    )
    scheduler.submit(request)
    claim = scheduler.claim(worker_id=worker_id, capabilities=capabilities)
    owner = {
        "worker_id": worker_id,
        "claim_token": claim["claim_token"],
    }
    scheduler.start(request.execution_id, **owner)
    scheduler.checkpoint(
        request.execution_id,
        **owner,
        checkpoint={"turn_id": "turn-speed"},
    )
    control = ExecutionTurnSettingsControl.new(
        execution_id=request.execution_id,
        actor=request.actor,
        settings={"service_tier": "priority"},
        expected_turn_id="turn-speed",
    )

    receipt = scheduler.submit_turn_settings_control(control)
    delivered = scheduler.wait_for_worker_control(
        request.execution_id,
        **owner,
        timeout_seconds=0.1,
    )["turn_controls"]

    assert receipt["status"] == "pending"
    assert [item["control_id"] for item in delivered] == [control.control_id]
    assert delivered[0]["action"] == "update_settings"
    assert delivered[0]["settings"] == {"service_tier": "priority"}


def test_draining_worker_requests_handover_and_keeps_claim_until_release(
    tmp_path: Path,
) -> None:
    """draining 的 worker 收到交接请求但保留 claim，不能认领新执行"""
    scheduler = Scheduler(
        dispatch_interval_min_seconds=0,
        dispatch_interval_max_seconds=0,
    )
    old_worker_id = "dev:worker:old-drain"
    new_worker_id = "dev:worker:new-drain"
    capabilities = ["agent_session:codex"]
    scheduler.register_worker(
        worker_id=old_worker_id,
        build_id="old",
        capabilities=capabilities,
    )
    scheduler.register_worker(
        worker_id=new_worker_id,
        build_id="new",
        capabilities=capabilities,
        state="standby",
    )
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="continue on the next generation",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        system_instructions=portal_system_instructions("resident"),
        session=SessionOptions(mode="stateless", backend_route_id="zero"),
    )
    scheduler.submit(request)
    old_claim = scheduler.claim(
        worker_id=old_worker_id,
        capabilities=capabilities,
    )
    scheduler.start(
        request.execution_id,
        worker_id=old_worker_id,
        claim_token=old_claim["claim_token"],
    )
    scheduler.activate_worker(
        new_worker_id,
        previous_worker_id=old_worker_id,
    )

    control = scheduler.wait_for_worker_control(
        request.execution_id,
        worker_id=old_worker_id,
        claim_token=old_claim["claim_token"],
        timeout_seconds=0.01,
    )
    heartbeat = scheduler.heartbeat(
        request.execution_id,
        worker_id=old_worker_id,
        claim_token=old_claim["claim_token"],
    )

    assert control["handover_requested"] is True
    assert heartbeat["cancel_requested"] is False
    assert scheduler.claim(
        worker_id=old_worker_id,
        capabilities=capabilities,
    ) == {"claimed": False, "reason": "worker_not_active", "worker_state": "draining"}


def test_recoverable_claim_returns_to_queue_without_terminal_projection(
    tmp_path: Path,
) -> None:
    """可恢复的 claim 重新排队时保留检查点且不产生终态，旧 claim 失效"""
    scheduler = Scheduler(
        dispatch_interval_min_seconds=0,
        dispatch_interval_max_seconds=0,
    )
    worker_id = "dev:worker:recovery"
    scheduler.register_worker(
        worker_id=worker_id,
        build_id="current",
        capabilities=["agent_session:codex"],
        pid=1,
    )
    request = ExecutionRequest.new(
        actor="sample_peer",
        prompt="observe the same accepted turn",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        system_instructions=portal_system_instructions("resident"),
        session=SessionOptions(
            mode="stateless",
            backend_route_id="zero",
        ),
    )
    scheduler.submit(request)
    claim = scheduler.claim(
        worker_id=worker_id,
        capabilities=["agent_session:codex"],
    )
    scheduler.start(
        request.execution_id,
        worker_id=worker_id,
        claim_token=claim["claim_token"],
    )
    scheduler.checkpoint(
        request.execution_id,
        worker_id=worker_id,
        claim_token=claim["claim_token"],
        checkpoint={
            "resumable": True,
            "backend_session_id": "thread-existing",
            "turn_id": "turn-existing",
        },
    )

    snapshot = scheduler.requeue_claim(
        request.execution_id,
        worker_id=worker_id,
        claim_token=claim["claim_token"],
        reason="provider recovery deferred",
    )
    resumed = scheduler.claim(
        worker_id=worker_id,
        capabilities=["agent_session:codex"],
    )

    assert snapshot["status"] == "queued"
    assert resumed["execution"]["checkpoint"]["turn_id"] == "turn-existing"
    assert all(
        event["type"] not in {"failed", "cancelled"}
        for event in scheduler.snapshot(request.execution_id)["events"]
    )
    with pytest.raises(PermissionError):
        scheduler.heartbeat(
            request.execution_id,
            worker_id=worker_id,
            claim_token=claim["claim_token"],
        )


def test_scheduler_delays_a_deferred_execution_before_reclaim(
    tmp_path: Path,
) -> None:
    """延后重试的执行在等待期内不能被认领，到期后可再次认领"""
    scheduler = Scheduler(
        dispatch_interval_min_seconds=0,
        dispatch_interval_max_seconds=0,
    )
    worker_id = "dev:deferred-retry-worker"
    capabilities = ["agent_session:codex"]
    scheduler.register_worker(
        worker_id=worker_id,
        build_id="test-build",
        capabilities=capabilities,
    )
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="retry after external writer release",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        session=SessionOptions(
            mode="persistent",
            binding_id="resident:deferred-retry",
            backend_route_id="zero",
        ),
    )
    scheduler.submit(request)
    claim = scheduler.claim(
        worker_id=worker_id,
        capabilities=capabilities,
    )
    scheduler.start(
        request.execution_id,
        worker_id=worker_id,
        claim_token=claim["claim_token"],
    )
    scheduler.checkpoint(
        request.execution_id,
        worker_id=worker_id,
        claim_token=claim["claim_token"],
        checkpoint={
            "resumable": True,
            "backend_session_id": "thread-writer-busy",
            "provider_recovery_state": {"kind": "thread_writer_busy"},
        },
    )

    queued = scheduler.requeue_claim(
        request.execution_id,
        worker_id=worker_id,
        claim_token=claim["claim_token"],
        reason="thread writer busy",
        retry_after_seconds=0.15,
    )
    blocked = scheduler.claim(
        worker_id=worker_id,
        capabilities=capabilities,
    )
    time.sleep(0.18)
    resumed = scheduler.claim(
        worker_id=worker_id,
        capabilities=capabilities,
    )

    assert queued["scheduler"]["queue_reason"] == "deferred"
    assert blocked == {"claimed": False, "reason": "deferred"}
    assert resumed["claimed"] is True
    assert resumed["execution"]["execution_id"] == request.execution_id


def test_rollout_wakes_old_long_poll_without_allowing_a_new_claim(
    tmp_path: Path,
) -> None:
    """发布换代时旧 worker 的长轮询被唤醒但不能认领，新执行由新 worker 认领"""
    scheduler = Scheduler(
        dispatch_interval_min_seconds=0,
        dispatch_interval_max_seconds=0,
    )
    old_worker_id = "dev:execution-worker:old-long-poll"
    new_worker_id = "dev:execution-worker:new-long-poll"
    capabilities = ["chat_completion"]
    scheduler.register_worker(
        worker_id=old_worker_id,
        build_id="old-build",
        capabilities=capabilities,
    )
    old_claim: dict[str, object] = {}

    def wait_as_old_worker() -> None:
        old_claim.update(
            scheduler.claim_wait(
                worker_id=old_worker_id,
                capabilities=capabilities,
                timeout_seconds=2,
            )
        )

    waiting = threading.Thread(target=wait_as_old_worker)
    waiting.start()
    scheduler.register_worker(
        worker_id=new_worker_id,
        build_id="new-build",
        capabilities=capabilities,
        state="standby",
    )
    scheduler.activate_worker(
        new_worker_id,
        previous_worker_id=old_worker_id,
    )
    request = ExecutionRequest.new(
        actor="rollout-race-test",
        prompt="must be claimed by the replacement",
        service_type="chat_completion",
        provider="openai",
        operation="generate",
    )
    scheduler.submit(request)
    waiting.join(timeout=1)

    assert not waiting.is_alive()
    assert old_claim == {"claimed": False, "reason": "worker_not_active", "worker_state": "draining"}
    claimed = scheduler.claim(
        worker_id=new_worker_id,
        capabilities=capabilities,
    )
    assert claimed["claimed"] is True
    assert claimed["execution"]["execution_id"] == request.execution_id


@pytest.mark.parametrize("change", ["cancel", "handover", "append"])
def test_peer_scheduler_control_changes_wake_waiters_promptly(
    change: str,
) -> None:
    """另一调度器实例提交的取消、交接或追加输入立即唤醒等待控制的 worker"""
    options = dict(dispatch_interval_min_seconds=0, dispatch_interval_max_seconds=0)
    worker_scheduler = Scheduler(**options)
    peer_scheduler = Scheduler(**options)
    worker_id = "release:execution-worker:control-wait"
    capabilities = ["agent_session:codex"]
    worker_scheduler.register_worker(worker_id=worker_id, build_id="release", capabilities=capabilities)
    peer_scheduler.register_worker(worker_id="dev:execution-worker:next", build_id="dev", capabilities=capabilities, state="standby")
    request = ExecutionRequest.new(
        actor="control-test", prompt="one turn", service_type="agent_session",
        provider="codex", operation="turn",
        session=SessionOptions(mode="persistent", binding_id="resident:control-test", backend_route_id="zero"),
    )
    peer_scheduler.submit(request)
    claim = worker_scheduler.claim(worker_id=worker_id, capabilities=capabilities)
    token = claim["claim_token"]
    owner = dict(worker_id=worker_id, claim_token=token)
    worker_scheduler.start(request.execution_id, **owner)
    control = ExecutionInputControl.new(execution_id=request.execution_id, actor=request.actor, input=[{"type": "text", "text": "one appended input"}])
    if change == "acknowledge":
        worker_scheduler.submit_input_control(control)
    result = {}
    waiting = threading.Event()
    condition = worker_scheduler._worker_control_changed
    original_wait = condition.wait

    def observe_wait(timeout):
        waiting.set()
        return original_wait(timeout)

    def wait_for_control():
        if change == "acknowledge":
            result.update(worker_scheduler.input_control(control.control_id))
        else:
            result.update(worker_scheduler.wait_for_worker_control(request.execution_id, **owner, timeout_seconds=5))

    with mock.patch.object(condition, "wait", side_effect=observe_wait):
        waiter = threading.Thread(target=wait_for_control, daemon=True)
        waiter.start()
        try:
            assert waiting.wait(1), "Waiter must have checked the database before peer mutation"
            started = time.monotonic()
            if change == "cancel":
                peer_scheduler.cancel(request.execution_id, actor=request.actor)
            elif change == "handover":
                peer_scheduler.activate_worker("dev:execution-worker:next")
            elif change == "append":
                peer_scheduler.submit_input_control(control)
            else:
                peer_scheduler.complete_input_control(control.control_id, execution_id=request.execution_id, **owner, delivered=True)
            waiter.join(timeout=0.75)
            assert not waiter.is_alive(), "A peer commit must not wait for the long-poll heartbeat timeout"
            assert time.monotonic() - started < 0.75
            if change == "cancel":
                assert result["cancel_requested"] is True
            elif change == "handover":
                assert result["handover_requested"] is True
                assert not result.get("input_controls")
            elif change == "append":
                assert [item["control_id"] for item in result["turn_controls"]] == [control.control_id]
            else:
                assert result["control_id"] == control.control_id
                assert result["status"] == "delivered"
        finally:
            # Wake the old implementation only for test cleanup, after verdict.
            with condition:
                condition.notify_all()
            waiter.join(timeout=1)


def test_terminal_retention_preserves_unacknowledged_results() -> None:
    """终态清理在一天后删除已确认结果，未确认结果保留一周"""
    scheduler = Scheduler(
        dispatch_interval_min_seconds=0,
        dispatch_interval_max_seconds=0,
    )
    worker_id = "dev:retention-worker"
    scheduler.register_worker(
        worker_id=worker_id,
        build_id="test-build",
        capabilities=["chat_completion"],
    )

    def complete(actor: str) -> str:
        request = ExecutionRequest.new(
            actor=actor,
            prompt="terminal retention",
            service_type="chat_completion",
            provider="openai",
            operation="generate",
        )
        scheduler.submit(request)
        claim = scheduler.claim(
            worker_id=worker_id,
            capabilities=["chat_completion"],
        )
        assert claim["claimed"] is True
        token = str(claim["claim_token"])
        scheduler.start(
            request.execution_id,
            worker_id=worker_id,
            claim_token=token,
        )
        scheduler.finish(
            ExecutionResult(
                execution_id=request.execution_id,
                status="completed",
                output={"message": "done"},
            ),
            worker_id=worker_id,
            claim_token=token,
        )
        return request.execution_id

    two_days_ago = datetime.fromtimestamp(time.time() - 2 * 24 * 60 * 60, timezone.utc).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")
    stale_unacknowledged = complete("stale-unacknowledged")
    abandoned_unacknowledged = complete("abandoned-unacknowledged")
    stale_acknowledged = complete("stale-acknowledged")
    fresh_unacknowledged = complete("fresh-unacknowledged")
    scheduler.acknowledge(
        stale_acknowledged,
        actor="stale-acknowledged",
    )
    with connect(scheduler.database) as connection:
        connection.executemany(
            'UPDATE executions SET updated_at = %s WHERE execution_id = %s',
            [
                (two_days_ago, stale_unacknowledged),
                ("2000-01-01T00:00:00.000Z", abandoned_unacknowledged),
                ("2000-01-01T00:00:00.000Z", stale_acknowledged),
            ],
        )

    # Acknowledged results go after a day; unacknowledged ones after a week.
    assert scheduler.prune_terminal(
        older_than_epoch=time.time() - 24 * 60 * 60,
    ) == 2
    for execution_id in (stale_acknowledged, abandoned_unacknowledged):
        with pytest.raises(KeyError):
            scheduler.snapshot(execution_id)
    assert scheduler.snapshot(stale_unacknowledged)["status"] == "completed"
    assert scheduler.snapshot(fresh_unacknowledged)["status"] == "completed"
    health = scheduler.health()
    assert health["unacknowledged_terminal_count"] == 2
    assert datetime.fromisoformat(health["oldest_unacknowledged_terminal_at"]) == datetime.fromisoformat(two_days_ago)


def test_scheduler_snapshot_explains_capacity_blocked_queue(
    tmp_path: Path,
) -> None:
    """容量占满时执行快照给出排队原因、位置和容量占用"""
    scheduler, server, thread, client, service = _stack(tmp_path)
    scheduler.configure_capacity("codex:one", 2, 0, 0, actor="test")
    worker = SchedulerClient(
        worker_id="explore-1:queue-state-worker",
        build_id="test-build",
        capabilities=["agent_session:codex"],
        service_client=client,
    )
    try:
        worker.register()
        requests = [
            ExecutionRequest.new(
                actor="sample_admin",
                prompt=f"queue-state-{index}",
                service_type="agent_session",
                provider="codex",
                operation="turn",
                session=SessionOptions(
                    mode="persistent",
                    binding_id=f"provider:test-session-{index}",
                    backend_route_id="one",
                ),
                system_instructions=portal_system_instructions("resident"),
            )
            for index in range(3)
        ]
        for request in requests:
            service.submit(request)

        ready = service.status(requests[0].execution_id)
        assert ready["scheduler"]["queue_reason"] == "ready"
        not_yet_claimed = service.status(requests[2].execution_id)
        assert not_yet_claimed["scheduler"]["queue_reason"] == "ready"
        assert not_yet_claimed["scheduler"]["active_capacity_count"] == 0

        worker.claim()
        still_not_capacity_blocked = service.status(requests[2].execution_id)
        assert (
            still_not_capacity_blocked["scheduler"]["queue_reason"]
            == "ready"
        )
        assert (
            still_not_capacity_blocked["scheduler"]["active_capacity_count"]
            == 1
        )

        worker.claim()
        blocked = service.status(requests[2].execution_id)

        assert blocked["status"] == "queued"
        assert blocked["scheduler"] | {
            "queue_reason": "capacity",
            "queue_position": 1,
            "capacity_key": "codex:one",
            "capacity_limit": 2,
            "active_capacity_count": 2,
        } == blocked["scheduler"]
    finally:
        _close(server, thread)


def test_scheduler_snapshot_does_not_count_binding_blocked_work_as_capacity(
    tmp_path: Path,
) -> None:
    """等待同一绑定的执行标为 session_binding，不占其他执行的排队位置"""
    _scheduler, server, thread, client, service = _stack(tmp_path)
    worker = SchedulerClient(
        worker_id="explore-1:binding-filter-worker",
        build_id="test-build",
        capabilities=["agent_session:codex"],
        service_client=client,
    )
    try:
        worker.register()

        def request(binding_id: str) -> ExecutionRequest:
            return ExecutionRequest.new(
                actor="sample_admin",
                prompt=binding_id,
                service_type="agent_session",
                provider="codex",
                operation="turn",
                session=SessionOptions(
                    mode="persistent",
                    binding_id=binding_id,
                    backend_route_id="one",
                ),
                system_instructions=portal_system_instructions("resident"),
            )

        active = request("provider:shared-session")
        service.submit(active)
        assert worker.claim()["execution"]["execution_id"] == active.execution_id

        blocked = request("provider:shared-session")
        ready = request("provider:independent-session")
        service.submit(blocked)
        service.submit(ready)

        assert service.status(blocked.execution_id)["scheduler"][
            "queue_reason"
        ] == "session_binding"
        ready_status = service.status(ready.execution_id)
        assert ready_status["scheduler"]["queue_reason"] == "ready"
        assert ready_status["scheduler"]["queue_position"] == 1
        assert worker.claim()["execution"]["execution_id"] == ready.execution_id
    finally:
        _close(server, thread)


def test_new_codex_session_moves_capacity_when_backend_falls_back(
    tmp_path: Path,
) -> None:
    """新 codex 会话回退到其他账号时执行的容量分区和绑定路线随之迁移"""
    scheduler, server, thread, client, service = _stack(tmp_path)

    class FallbackBackend:
        provider_id = "codex"

        def execute(self, invocation, context):
            context.reassign_backend_route("zero")
            return BackendResult(
                output={"response": "fallback completed"},
                backend_session_id=(
                    "019f0000-0000-7000-8000-000000000802"
                ),
                backend_route_id="zero",
            )

        def interrupt(self, **_values):
            return True

    runtime = ExecutionRuntime(
        agent_providers={"codex": FallbackBackend()},
        state_root=tmp_path / "execution-state",
    )
    worker = ExecutionWorker(
        build_id="test-build",
        worker_id="explore-1:fallback-worker",
        scheduler=SchedulerClient(
            worker_id="explore-1:fallback-worker",
            build_id="test-build",
            capabilities=["agent_session:codex"],
            service_client=client,
        ),
        runtime=runtime,
        state_root=tmp_path / "execution-state",
    )
    try:
        worker.register()
        request = ExecutionRequest.new(
            actor="sample_admin",
            prompt="use the available account",
            service_type="agent_session",
            provider="codex",
            operation="turn",
            session=SessionOptions(
                mode="persistent",
                binding_id="resident:test-fallback",
            ),
            system_instructions=portal_system_instructions("resident"),
        )
        service.submit(request)

        assert run_worker_once(worker) is True

        snapshot = service.status(request.execution_id)
        assert snapshot["status"] == "completed"
        assert snapshot["request"]["session"]["backend_route_id"] == "zero"
        assert snapshot["result"]["backend_route_id"] == "zero"
        assert any(
            event["type"] == "capacity_reassigned"
            and event["details"] == {
                "from_capacity_key": "codex:one",
                "to_capacity_key": "codex:zero",
            }
            for event in snapshot["events"]
        )
        binding = runtime.sessions.provider_binding(
            "resident:test-fallback"
        )
        assert binding["backend_route_id"] == "zero"
        assert scheduler.health()["active_count"] == 0
    finally:
        _close(server, thread)


def test_worker_propagates_running_cancellation_promptly(
    tmp_path: Path,
) -> None:
    """取消运行中的执行时 worker 立即中断 Provider 并以 cancelled 结束"""
    _scheduler, server, thread, client, service = _stack(tmp_path)
    started = threading.Event()
    interrupted = threading.Event()

    class BlockingBackend:
        provider_id = "codex"

        def execute(self, invocation, context):
            started.set()
            while not context.is_cancelled():
                time.sleep(0.01)
            raise InterruptedError("cancelled by test")

        def interrupt(self, **values):
            interrupted.set()
            return True

    runtime = ExecutionRuntime(
        agent_providers={"codex": BlockingBackend()},
        state_root=tmp_path / "execution-state",
    )
    scheduler_client = SchedulerClient(
        worker_id="explore-1:cancel-worker",
        build_id="test-build",
        capabilities=runtime.capabilities(),
        service_client=client,
    )
    worker = ExecutionWorker(
        build_id="test-build",
        worker_id="explore-1:cancel-worker",
        scheduler=scheduler_client,
        runtime=runtime,
    )
    try:
        worker.register()
        request = ExecutionRequest.new(
            actor="sample_admin",
            prompt="block until cancelled",
            service_type="agent_session",
            provider="codex",
            operation="turn",
            system_instructions=portal_system_instructions("resident"),
        )
        service.submit(request)
        runner = threading.Thread(target=run_worker_once, args=(worker,))
        runner.start()
        assert started.wait(1)
        begin = time.monotonic()
        service.cancel(request.execution_id, actor="sample_admin")
        runner.join(timeout=2)
        assert not runner.is_alive()
        assert time.monotonic() - begin < 1.5
        assert interrupted.is_set()
        assert service.status(request.execution_id)["status"] == "cancelled"
    finally:
        _close(server, thread)


def test_worker_classifies_backend_error_after_cancel_request_as_cancelled(
    tmp_path: Path,
) -> None:
    """取消请求之后 Provider 报出的错误归为 cancelled"""
    _scheduler, server, thread, client, service = _stack(tmp_path)
    started = threading.Event()

    class ProviderStyleInterruptBackend:
        provider_id = "codex"

        def execute(self, invocation, context):
            started.set()
            while not context.is_cancelled():
                time.sleep(0.01)
            raise RuntimeError("provider reported an interrupted turn")

        def interrupt(self, **_values):
            return True

    runtime = ExecutionRuntime(
        agent_providers={"codex": ProviderStyleInterruptBackend()},
        state_root=tmp_path / "execution-state",
    )
    scheduler_client = SchedulerClient(
        worker_id="explore-1:provider-cancel-worker",
        build_id="test-build",
        capabilities=runtime.capabilities(),
        service_client=client,
    )
    worker = ExecutionWorker(
        build_id="test-build",
        worker_id="explore-1:provider-cancel-worker",
        scheduler=scheduler_client,
        runtime=runtime,
    )
    try:
        worker.register()
        request = ExecutionRequest.new(
            actor="sample_admin",
            prompt="cancel a provider that reports interruption as an error",
            service_type="agent_session",
            provider="codex",
            operation="turn",
            system_instructions=portal_system_instructions("resident"),
        )
        service.submit(request)
        runner = threading.Thread(target=run_worker_once, args=(worker,))
        runner.start()
        try:
            assert started.wait(10), "test provider did not start"
            service.cancel(request.execution_id, actor="sample_admin")
            runner.join(timeout=10)

            assert not runner.is_alive()
            snapshot = service.status(request.execution_id)
            assert snapshot["status"] == "cancelled"
            assert snapshot["scheduler"]["cancel_requested"] is True
        finally:
            if runner.is_alive():
                service.cancel(request.execution_id, actor="sample_admin")
                runner.join(timeout=10)
    finally:
        _close(server, thread)


def test_worker_interrupts_provider_after_handover_requested(
    tmp_path: Path,
) -> None:
    """worker 收到交接请求且 claim 已转移时中断 Provider，不发布结果"""
    provider_started = threading.Event()
    release_provider = threading.Event()
    ownership_changed = threading.Event()

    class BlockingBackend:
        provider_id = "codex"

        def __init__(self) -> None:
            self.interrupt_count = 0

        def execute(self, _invocation, _context):
            provider_started.set()
            assert release_provider.wait(3)
            return BackendResult(output={"message": "finished"})

        def interrupt(self, **_values):
            self.interrupt_count += 1
            return True

    class OwnershipChangingScheduler:
        def __init__(self) -> None:
            self.control_calls = 0
            self.finish_called = False

        def start(self, *_args):
            return {}

        def wait_for_control(self, *_args, **_kwargs):
            self.control_calls += 1
            if self.control_calls == 1:
                raise ExecutionServiceUnavailable("service restarting")
            ownership_changed.set()
            return {"handover_requested": True}

        def heartbeat(self, *_args, **_kwargs):
            return {}

        def emit(self, *_args, **_kwargs):
            return None

        def checkpoint(self, *_args, **_kwargs):
            return {}

        def reassign_capacity(self, *_args, **_kwargs):
            return {}

        def requeue(self, *_args, **_kwargs):
            raise ExecutionClaimOwnershipLost(
                "execution claim ownership changed"
            )

        def finish(self, *_args, **_kwargs):
            self.finish_called = True
            raise AssertionError("a fenced Worker must not publish a result")

    backend = BlockingBackend()
    scheduler = OwnershipChangingScheduler()
    runtime = ExecutionRuntime(
        agent_providers={"codex": backend},
        agent_data_projector=AgentDataProjector(
            AgentDataStore()
        ),
        state_root=tmp_path / "execution-state",
    )
    worker = ExecutionWorker(
        build_id="old-build",
        worker_id="explore-1:old-worker",
        scheduler=scheduler,
        runtime=runtime,
    )
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="keep the accepted provider turn alive",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        system_instructions=portal_system_instructions("resident"),
    )
    result: dict[str, object] = {}

    runner = threading.Thread(
        target=lambda: result.update(
            worker.execute_claim(
                {
                    "claim_token": "old-claim-token",
                    "execution": {
                        "execution_id": request.execution_id,
                        "request": request.to_payload(),
                        "checkpoint": {},
                    },
                },
                lease_seconds=15,
            )
        )
    )
    runner.start()
    assert provider_started.wait(1)
    assert ownership_changed.wait(2)
    release_provider.set()
    runner.join(timeout=3)

    assert not runner.is_alive()
    assert result["status"] == "claim_lost"
    assert backend.interrupt_count == 1
    assert scheduler.finish_called is False


def test_worker_shutdown_fences_provider_without_publishing_cancelled(
    tmp_path: Path,
) -> None:
    """worker 关闭时中断 Provider 并放弃 claim，不发布 cancelled 结果"""
    provider_started = threading.Event()
    release_provider = threading.Event()
    shutdown = threading.Event()

    class BlockingBackend:
        provider_id = "codex"

        def __init__(self) -> None:
            self.interrupt_count = 0

        def execute(self, _invocation, _context):
            provider_started.set()
            assert release_provider.wait(2)
            return BackendResult(output={"message": "provider terminal"})

        def interrupt(self, **_values):
            self.interrupt_count += 1
            return True

    class SchedulerStub:
        def __init__(self) -> None:
            self.finish_called = False
            self.finished = None

        def start(self, *_args):
            return {}

        def wait_for_control(self, *_args, **_kwargs):
            time.sleep(0.05)
            return {"cancel_requested": False}

        def heartbeat(self, *_args, **_kwargs):
            return {"cancel_requested": False}

        def emit(self, *_args, **_kwargs):
            return None

        def checkpoint(self, *_args, **_kwargs):
            return {}

        def reassign_capacity(self, *_args, **_kwargs):
            return {}

        def requeue(self, *_args, **_kwargs):
            return {"status": "queued"}

        def finish(self, result, *_args, **_kwargs):
            self.finish_called = True
            self.finished = result
            return {"status": result.status}

    scheduler = SchedulerStub()
    backend = BlockingBackend()
    runtime = ExecutionRuntime(
        agent_providers={"codex": backend},
        state_root=tmp_path / "execution-state",
    )
    complete_projection = mock.Mock(
        wraps=runtime.agent_data_projector.complete
    )
    runtime.agent_data_projector.complete = complete_projection
    worker = ExecutionWorker(
        build_id="old-build",
        worker_id="dev:old-worker",
        scheduler=scheduler,
        runtime=runtime,
        shutdown_requested=shutdown.is_set,
    )
    request = ExecutionRequest.new(
        actor="sample_peer",
        prompt="keep the accepted turn recoverable",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        system_instructions=portal_system_instructions("resident"),
        session=SessionOptions(
            mode="stateless",
            backend_route_id="zero",
        ),
    )
    result: dict[str, object] = {}
    runner = threading.Thread(
        target=lambda: result.update(worker.execute_claim({
            "claim_token": "old-claim",
            "execution": {
                "execution_id": request.execution_id,
                "request": request.to_payload(),
                "checkpoint": {
                    "resumable": True,
                    "provider": "codex",
                    "backend_session_id": "thread-existing",
                    "backend_route_id": "zero",
                    "turn_id": "turn-existing",
                },
            },
        }))
    )

    runner.start()
    assert provider_started.wait(1), scheduler.finished
    shutdown.set()
    time.sleep(0.1)
    release_provider.set()
    runner.join(timeout=2)

    assert not runner.is_alive()
    assert result["status"] == "claim_lost"
    assert scheduler.finish_called is False
    assert backend.interrupt_count == 1
    complete_projection.assert_not_called()


@pytest.mark.parametrize("checkpoint_turn", ["turn-existing", ""])
def test_worker_requeues_a_deferred_provider_recovery(
    tmp_path: Path,
    checkpoint_turn: str,
) -> None:
    """Provider 恢复被延后时 worker 写入可恢复检查点并重新排队，不发布终态"""
    class DeferredBackend:
        provider_id = "codex"

        def execute(self, _invocation, _context):
            _context.set_provider_recovery_state({"kind": "thread_writer_busy"})
            raise ExecutionRecoveryDeferred("provider recovery deadline")

        def interrupt(self, **_values):
            return True

    class SchedulerStub:
        def __init__(self) -> None:
            self.requeued = None
            self.finish_called = False
            self.finished = None

        def start(self, *_args):
            return {}

        def wait_for_control(self, *_args, **_kwargs):
            time.sleep(0.01)
            return {"cancel_requested": False}

        def heartbeat(self, *_args, **_kwargs):
            return {"cancel_requested": False}

        def emit(self, *_args, **_kwargs):
            return None

        def checkpoint(self, *_args, **_kwargs):
            self.last_checkpoint = _args[-1]
            return {}

        def reassign_capacity(self, *_args, **_kwargs):
            return {}

        def requeue(self, execution_id, claim_token, reason):
            assert self.last_checkpoint["resumable"] is True
            assert self.last_checkpoint["provider_recovery_state"]["kind"] == "thread_writer_busy"
            self.requeued = (execution_id, claim_token, reason)
            return {"status": "queued"}

        def finish(self, result, *_args, **_kwargs):
            self.finish_called = True
            self.finished = result
            return {}

    scheduler = SchedulerStub()
    worker = ExecutionWorker(
        build_id="current",
        worker_id="dev:current-worker",
        scheduler=scheduler,
        runtime=ExecutionRuntime(
            agent_providers={"codex": DeferredBackend()},
            state_root=tmp_path / "execution-state",
        ),
    )
    request = ExecutionRequest.new(
        actor="sample_peer",
        prompt="resume later",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        system_instructions=portal_system_instructions("resident"),
        session=SessionOptions(
            mode="stateless",
            backend_route_id="zero",
        ),
    )

    result = worker.execute_claim({
        "claim_token": "claim-current",
        "execution": {
            "execution_id": request.execution_id,
            "request": request.to_payload(),
            "checkpoint": {
                "resumable": True,
                "provider": "codex",
                "backend_session_id": "thread-existing",
                "backend_route_id": "zero",
                "turn_id": checkpoint_turn,
            },
        },
    })

    assert result["status"] == "requeued", scheduler.finished
    assert scheduler.requeued == (
        request.execution_id,
        "claim-current",
        "provider recovery deadline",
    )
    assert scheduler.finish_called is False


def test_worker_self_fences_when_control_plane_outage_exceeds_lease(
    tmp_path: Path,
) -> None:
    """控制面不可用超过租约时 worker 自行中断 Provider 并放弃 claim，不发布结果"""
    provider_started = threading.Event()

    class CancellableBackend:
        provider_id = "codex"

        def __init__(self) -> None:
            self.interrupt_count = 0

        def execute(self, _invocation, context):
            provider_started.set()
            while not context.is_cancelled():
                time.sleep(0.01)
            raise InterruptedError("worker lease expired")

        def interrupt(self, **_values):
            self.interrupt_count += 1
            return True

    class UnavailableScheduler:
        def __init__(self) -> None:
            self.finish_called = False

        def start(self, *_args):
            return {}

        def wait_for_control(self, *_args, **_kwargs):
            raise ExecutionServiceUnavailable("service unavailable")

        def emit(self, *_args, **_kwargs):
            return None

        def checkpoint(self, *_args, **_kwargs):
            return {}

        def reassign_capacity(self, *_args, **_kwargs):
            return {}

        def finish(self, *_args, **_kwargs):
            self.finish_called = True
            raise AssertionError("a self-fenced Worker must not publish a result")

    backend = CancellableBackend()
    scheduler = UnavailableScheduler()
    runtime = ExecutionRuntime(
        agent_providers={"codex": backend},
        state_root=tmp_path / "execution-state",
    )
    worker = ExecutionWorker(
        build_id="test-build",
        worker_id="explore-1:self-fencing-worker",
        scheduler=scheduler,
        runtime=runtime,
    )
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="stop when the lease can no longer be renewed",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        system_instructions=portal_system_instructions("resident"),
    )
    result: dict[str, object] = {}
    runner = threading.Thread(
        target=lambda: result.update(
            worker.execute_claim(
                {
                    "claim_token": "claim-token",
                    "execution": {
                        "execution_id": request.execution_id,
                        "request": request.to_payload(),
                        "checkpoint": {},
                    },
                },
                lease_seconds=1,
            )
        )
    )

    runner.start()
    assert provider_started.wait(1)
    runner.join(timeout=3)

    assert not runner.is_alive()
    assert result["status"] == "claim_lost"
    assert backend.interrupt_count == 1
    assert scheduler.finish_called is False


def test_worker_terminal_publish_wakes_blocked_heartbeat_before_return(
    tmp_path: Path,
) -> None:
    """worker 发布终态后立即唤醒阻塞的控制等待，不等心跳超时"""
    control_wait_started = threading.Event()
    terminal_published = threading.Event()
    control_wait_returned = threading.Event()

    class ImmediateBackend:
        provider_id = "codex"

        def execute(self, _invocation, _context):
            assert control_wait_started.wait(1)
            return BackendResult(output={"message": "finished"})

        def interrupt(self, **_values):
            return True

    class BlockingControlScheduler:
        def __init__(self) -> None:
            self.finished: ExecutionResult | None = None

        def start(self, *_args):
            return {}

        def wait_for_control(self, *_args, **_kwargs):
            control_wait_started.set()
            assert terminal_published.wait(3)
            control_wait_returned.set()
            raise ExecutionClaimOwnershipLost(
                "terminal publish released claim ownership"
            )

        def heartbeat(self, *_args, **_kwargs):
            return {}

        def emit(self, *_args, **_kwargs):
            return None

        def checkpoint(self, *_args, **_kwargs):
            return {}

        def reassign_capacity(self, *_args, **_kwargs):
            return {}

        def finish(self, result, _claim_token):
            self.finished = result
            terminal_published.set()
            return {"status": result.status}

    scheduler = BlockingControlScheduler()
    worker = ExecutionWorker(
        build_id="test-build",
        worker_id="explore-1:test-worker",
        scheduler=scheduler,
        runtime=ExecutionRuntime(
            agent_providers={"codex": ImmediateBackend()},
            state_root=tmp_path / "execution-state",
        ),
    )
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="finish without waiting for the heartbeat timeout",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        system_instructions=portal_system_instructions("resident"),
    )

    begin = time.monotonic()
    outcome = worker.execute_claim(
        {
            "claim_token": "claim-token",
            "execution": {
                "execution_id": request.execution_id,
                "request": request.to_payload(),
                "checkpoint": {},
            },
        },
        lease_seconds=15,
    )
    elapsed = time.monotonic() - begin

    assert outcome["status"] == "completed"
    assert scheduler.finished is not None
    assert control_wait_returned.is_set()
    assert elapsed < 1.0


def test_worker_transports_structured_provider_interruption(
    tmp_path: Path,
) -> None:
    """worker 把 Provider 中断作为结构化错误码和详情上报"""
    class InterruptedBackend:
        provider_id = "codex"

        def execute(self, _invocation, _context):
            raise CodexTurnInterrupted("thread-1", "turn-1")

        def interrupt(self, **_values):
            return True

    class CapturingScheduler:
        def __init__(self) -> None:
            self.finished: ExecutionResult | None = None

        def start(self, *_args):
            return {}

        def wait_for_control(self, *_args, **_kwargs):
            time.sleep(0.01)
            return {}

        def heartbeat(self, *_args, **_kwargs):
            return {}

        def emit(self, *_args, **_kwargs):
            return None

        def checkpoint(self, *_args, **_kwargs):
            return {}

        def reassign_capacity(self, *_args, **_kwargs):
            return {}

        def finish(self, result, _claim_token):
            self.finished = result
            return {"status": result.status}

    scheduler = CapturingScheduler()
    worker = ExecutionWorker(
        build_id="test-build",
        worker_id="explore-1:test-worker",
        scheduler=scheduler,
        runtime=ExecutionRuntime(
            agent_providers={"codex": InterruptedBackend()},
            state_root=tmp_path / "execution-state",
        ),
    )
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="surface the provider interruption",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        system_instructions=portal_system_instructions("resident"),
    )

    outcome = worker.execute_claim({
        "claim_token": "claim-token",
        "execution": {
            "execution_id": request.execution_id,
            "request": request.to_payload(),
            "checkpoint": {},
        },
    })

    assert outcome["status"] == "failed"
    assert scheduler.finished is not None
    assert scheduler.finished.error_code == "provider_turn_interrupted"
    assert scheduler.finished.error_details == {
        "provider": "codex",
        "provider_status": "interrupted",
        "thread_id": "thread-1",
        "turn_id": "turn-1",
    }
    assert "unknown error" not in scheduler.finished.error


@pytest.mark.parametrize("failure", [
    CodexTurnInterrupted("thread-1", "turn-1"),
    CodexTurnUsageLimitExceeded("thread-1", "turn-1", "You've hit your usage limit."),
])
def test_execution_service_preserves_structured_provider_interruption(
    tmp_path: Path,
    failure: Exception,
) -> None:
    """执行服务快照保留 Provider 中断和用量超限的结构化错误码与详情

    :design: decision_6ad0384e020d
    :design-point:
    """
    class InterruptedBackend:
        provider_id = "codex"

        def execute(self, _invocation, _context):
            raise failure

        def interrupt(self, **_values):
            return True

    _scheduler, server, thread, client, service = _stack(tmp_path)
    runtime = ExecutionRuntime(
        agent_providers={"codex": InterruptedBackend()},
        state_root=tmp_path / "execution-state",
    )
    worker = ExecutionWorker(
        build_id="test-build",
        worker_id="explore-1:structured-error-worker",
        scheduler=SchedulerClient(
            worker_id="explore-1:structured-error-worker",
            build_id="test-build",
            capabilities=runtime.capabilities(),
            service_client=client,
        ),
        runtime=runtime,
    )
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="surface the provider interruption through the service",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        system_instructions=portal_system_instructions("resident"),
    )

    try:
        worker.register()
        service.submit(request)
        assert run_worker_once(worker) is True

        snapshot = service.status(request.execution_id)
        assert snapshot["status"] == "failed"
        assert snapshot["result"]["error_code"] == failure.error_code
        assert snapshot["result"]["error_details"] == failure.error_details
        assert str(failure) in snapshot["result"]["error"]
        assert "unknown error" not in snapshot["result"]["error"]
    finally:
        _close(server, thread)


def test_resident_queue_promotion_clears_previous_execution_binding_contracts(subtests, isolated_portal_case):
    """Resident 队列项提升时清除上一执行的绑定并换新执行 ID，已有执行 ID 不能被替换

    :module: resident.fork-handoff
    :importance: P1
    """
    with subtests.test(contract='execution replacement cannot inherit setup acknowledgement'), isolated_portal_case():
        from management_portal.society_service.resident_execution_work import bind_execution
        job = {"execution_id": "setup", "execution_acknowledged": True, "execution_event_cursor": 42}
        bind_execution(job, execution_id="setup", now="same")
        assert job["execution_acknowledged"] is True
        assert job["execution_event_cursor"] == 42
        with pytest.raises(ValueError, match="reserved identity"):
            bind_execution(job, execution_id="turn", now="replacement")
        assert job["execution_id"] == "setup"
        assert job["execution_acknowledged"] is True
    with subtests.test(contract='test_resident_queue_promotion_clears_previous_execution_binding'), isolated_portal_case() as case_path:
        from management_portal.society_service import resident_execution_work

        job = {

            "status": "running",
                    "execution_id": "execution-" + "d" * 24,
         "submitted_at": "2026-10-14T00:00:00Z"}
        resident_execution_work.promote_queue_item(
            job,
            {
                'execution_id': "job_next",
                "user_message_id": "message_next",
                "message": "next",
                        },
            [],
            "2026-07-26T12:00:00Z",
        )

        assert job["execution_id"].startswith("execution-")
        assert job["execution_id"] != "execution-" + "d" * 24
        assert "status" not in job
        assert "submitted_at" not in job
    with subtests.test(contract='test_resident_queue_promotion_restores_nested_message_identity'), isolated_portal_case() as case_path:
        from management_portal.society_service import resident_execution_work

        job = {'execution_id': "job_current", "status": "running"}
        resident_execution_work.promote_queue_item(
            job,
            {
                'execution_id': "job_next",
                "user_message_id": "message_next",
                "input_message": {"content_parts": []},
                "message": "next",
            },
            [],
            "2026-08-31T12:00:00Z",
        )

        assert job["input_message"]["message_id"] == "message_next"


from tests.cases.compatibility_test_support import setUpModule, tearDownModule, site_environ
