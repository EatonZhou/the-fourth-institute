"""SessionManager and Provider bindings: a bound session is never replaced, is reused and released, and
keeps its account route.

:module: execution.service
:importance: P2
"""
from __future__ import annotations

from tests.cases.execution_test_support import run_worker_once

import os
from tests.cases.database_test_support import connect
from pathlib import Path
from unittest import mock

import pytest

pytestmark = pytest.mark.usefixtures("registered_execution_defaults")

from management_common.portal_database import portal_database

from management_portal.agent_prompt_context import (
    portal_system_instructions,
)
from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.execution.backend_contracts import BackendResult
from management_portal.execution.contracts import ExecutionRequest, ExecutionResult, SessionOptions
from management_portal.execution.runtime import ExecutionRuntime
from management_portal.execution.scheduler import (
    Scheduler,
    SchedulerClient,
)
from management_portal.execution.session_manager import SessionManager
from management_portal.execution.worker import ExecutionWorker
from tests.cases.execution_layer_test_support import _bind_session, _stack, _close


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
            "time.time",
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
            "time.time",
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
            "time.time",
            return_value=100.0,
        ):
            scheduler.register_worker(
                worker_id="dev:abandoned-worker",
                build_id="abandoned-build",
                capabilities=["chat_completion"],
            )

        with mock.patch(
            "time.time",
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


from tests.cases.compatibility_test_support import setUpModule, tearDownModule
