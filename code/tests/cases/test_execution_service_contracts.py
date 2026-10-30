"""ExecutionService requests and RPC: wire contracts, timeouts, one shared queue, activity
notifications, health and maintenance.

:module: execution.service
:importance: P2
"""
from __future__ import annotations

from tests.cases.execution_test_support import run_worker_once

import hashlib
import json
from tests.cases.database_test_support import connect
import threading
from datetime import datetime
from pathlib import Path
from typing import get_type_hints
from unittest import mock

import pytest

pytestmark = pytest.mark.usefixtures("registered_execution_defaults")

from management_portal.agent_prompt_context import (
    portal_system_instructions,
)
from management_portal.codex_app_server_protocol import (
    CodexTurnInterrupted,
    CodexTurnUsageLimitExceeded,
)
from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.execution import chat_completion_service
from management_portal.execution.backend_contracts import BackendResult
from management_portal.execution.contracts import (
    ExecutionRequest,
    ExecutionServiceHealth,
    ExecutionSnapshot,
    InputMedia,
    SessionOptions,
)
from management_portal.execution.runtime import ExecutionRuntime
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
from management_portal.execution.transport import (
    ExecutionServiceClient,
    ExecutionServiceUnavailable,
)
from management_portal.execution.worker import (
    ExecutionWorker,
    ExecutionWorkerDaemon,
)
from tests.cases.execution_layer_test_support import _bind_session, _stack, _close


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
        assert client.request.call_args.kwargs["timeout"] == 15.0
        scheduler = SchedulerClient(
            worker_id="worker-budget", build_id="test", capabilities=["codex"],
            service_client=client,
        )
        scheduler.heartbeat("execution-budget", "claim-budget")
        assert client.request.call_args.kwargs["timeout"] == 15.0


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


@pytest.mark.parametrize("client_timeout,request_timeout,method,connect_timeout,response_timeout", [
    (None, None, "scheduler.checkpoint", 15.0, 90.0),
    (None, 1.25, "execution.wait", 1.25, 1.25),
    (2.0, None, "scheduler.event", 2.0, 2.0),
    (None, None, "health", 15.0, 15.0),
    (2.0, None, "health", 2.0, 2.0),
    (None, 60.0, "scheduler.checkpoint", 15.0, 60.0),
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
        chat_completion_service.shared_ai_provider_registry,
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
        chat_completion_service.shared_ai_provider_registry,
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
        chat_completion_service.shared_ai_provider_registry,
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
        chat_completion_service.shared_ai_provider_registry,
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


from tests.cases.compatibility_test_support import setUpModule, tearDownModule
