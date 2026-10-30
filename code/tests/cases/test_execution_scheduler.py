"""Scheduler queue and claims: capacity and dispatch intervals, cancellation, lease expiry, deferred
work, fairness, controls, retention and snapshots.

:module: execution.service
:importance: P2
"""
from __future__ import annotations

from tests.cases.database_test_support import connect
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pytest

pytestmark = pytest.mark.usefixtures("registered_execution_defaults")

from management_common.json_columns import json_value

from management_portal.agent_prompt_context import (
    portal_system_instructions,
)
from management_portal.execution import backend_contracts, codex_backend
from management_portal.execution.backend_contracts import BackendInvocation
from management_portal.execution.contracts import (
    ExecutionInputControl,
    ExecutionTurnSettingsControl,
    ExecutionRequest,
    ExecutionResult,
    SessionOptions,
)
from management_portal.execution.scheduler import (
    Scheduler,
    SchedulerClient,
)
from management_portal.execution.service import ExecutionServiceServer
from management_portal.execution.session_manager import RuntimeSession
from management_portal.execution.transport import ExecutionClaimOwnershipLost
from tests.cases.execution_layer_test_support import _stack, _close


def test_scheduler_capacity_setting_releases_an_obsolete_dispatch_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """重新配置容量时按新派发间隔释放旧的等待窗口，下一条执行可立即认领"""
    now = 1_000.0
    monkeypatch.setattr(
        "time.time",
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
        with first.changes.signal("execution"):
            first_event = first.changes.event(None, "execution-one", "provider_event")

        replacement = Scheduler()
        with replacement.changes.signal("execution"):
            replacement_event = replacement.changes.event(
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
            codex_backend.CodexBackend(
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
                backend_contracts.BackendContext(
                    emit_event=lambda *_args: None,
                    cancellation_requested=lambda: False,
                    set_interrupt_target=lambda *_args: None,
                ),
            )


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


def test_scheduler_dispatch_intervals_are_independent_by_capacity_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """各容量分区独立计算派发间隔，账号单独配置的间隔只作用于该账号"""
    monkeypatch.setattr(
        "time.time",
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
        "random.uniform",
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
        "time.time",
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
    condition = worker_scheduler.changes.signal("control")
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


from tests.cases.compatibility_test_support import setUpModule, tearDownModule
