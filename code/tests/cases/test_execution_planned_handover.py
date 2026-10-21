""":importance: P2"""
import threading
import time
from unittest import mock

import pytest

from management_common.json_columns import json_value
from management_portal.codex_app_server_client import CodexTurnInterrupted
from management_portal.execution import backends
from management_portal.execution.contracts import ExecutionRecoveryDeferred, ExecutionRequest, SessionOptions
from management_portal.execution.scheduler import Scheduler
from management_portal.execution.session_manager import RuntimeSession
from management_portal.execution.runtime import ExecutionRuntime
from management_portal.execution.worker import ExecutionWorker
from management_portal.execution.service import ExecutionServiceServer
from management_portal.execution.transport import ExecutionServiceClient
from management_portal.execution.scheduler import SchedulerClient


THREAD_ID = "019f0000-0000-7000-8000-000000000498"
TURN_ID = "turn-before-update"




@pytest.fixture
def handover_claim(tmp_path):
    scheduler = Scheduler(
        dispatch_interval_min_seconds=0,
        dispatch_interval_max_seconds=0,
    )
    scheduler.register_worker(
        worker_id="dev:old", build_id="old",
        capabilities=["agent_session:codex"], pid=1,
    )
    request = ExecutionRequest.new(
        actor="sample_admin", prompt="finish the original task",
        service_type="agent_session", provider="codex", operation="turn",
        session=SessionOptions(mode="stateless", backend_route_id="zero"),
    )
    scheduler.submit(request)
    claim = scheduler.claim(worker_id="dev:old", capabilities=["agent_session:codex"])
    owner = {"worker_id": "dev:old", "claim_token": claim["claim_token"]}
    scheduler.start(request.execution_id, **owner)
    return scheduler, request, owner


def accepted_checkpoint():
    return {
        "provider": "codex", "operation": "turn", "resumable": True,
        "backend_session_id": THREAD_ID, "backend_route_id": "zero",
        "turn_id": TURN_ID,
    }


def stored_checkpoint(scheduler, execution_id):
    with scheduler._connect() as connection:
        row = connection.execute(
            'SELECT checkpoint_json FROM executions WHERE execution_id = %s',
            (execution_id,),
        ).fetchone()
    return json_value(row[0] or "{}")


def test_handover_is_durable_before_interrupt_and_release(handover_claim):
    """计划交接先持久记下原轮次再释放 claim，执行回到队列且不产生失败或取消"""
    scheduler, request, owner = handover_claim
    scheduler.checkpoint(request.execution_id, **owner, checkpoint=accepted_checkpoint())
    prepared = scheduler.prepare_handover(request.execution_id, **owner)
    assert prepared["ready"] is True
    snapshot = scheduler.snapshot(request.execution_id)
    assert snapshot["status"] == "running"
    assert stored_checkpoint(scheduler, request.execution_id)["worker_handover"]["turn_id"] == TURN_ID
    scheduler.requeue_claim(request.execution_id, **owner, reason="worker generation handover")
    snapshot = scheduler.snapshot(request.execution_id)
    assert snapshot["status"] == "queued"
    assert stored_checkpoint(scheduler, request.execution_id)["turn_start_reason"] == "backend_service_update"
    assert all(event["type"] not in {"failed", "cancelled"} for event in snapshot["events"])


def test_handover_waits_for_provider_acceptance(handover_claim):
    """Provider 尚未接受轮次时不能准备交接，也不写交接标记"""
    scheduler, request, owner = handover_claim
    assert scheduler.prepare_handover(request.execution_id, **owner)["ready"] is False
    assert not stored_checkpoint(scheduler, request.execution_id).get("worker_handover")


def test_completed_checkpoint_does_not_interrupt_provider(handover_claim):
    """Provider 已完成的执行准备交接时不要求中断，也不写交接标记"""
    scheduler, request, owner = handover_claim
    scheduler.checkpoint(
        request.execution_id, **owner,
        checkpoint={**accepted_checkpoint(), "provider_completed": True, "output": {"message": "done"}},
    )
    prepared = scheduler.prepare_handover(request.execution_id, **owner)
    assert prepared["ready"] is True
    assert prepared["interrupt_required"] is False
    assert not stored_checkpoint(scheduler, request.execution_id).get("worker_handover")


def test_late_checkpoint_preserves_prepared_handover(handover_claim):
    """准备交接后再写入的检查点保留已有的交接标记"""
    scheduler, request, owner = handover_claim
    scheduler.checkpoint(request.execution_id, **owner, checkpoint=accepted_checkpoint())
    scheduler.prepare_handover(request.execution_id, **owner)
    scheduler.checkpoint(
        request.execution_id, **owner,
        checkpoint={**accepted_checkpoint(), "context_usage": {"input_tokens": 17}},
    )
    assert stored_checkpoint(scheduler, request.execution_id)["worker_handover"]["turn_id"] == TURN_ID


@pytest.mark.parametrize("cancel_before_prepare", [True, False])
def test_user_stop_wins_during_handover(handover_claim, cancel_before_prepare):
    """交接前后收到用户停止时，执行回队后标记为放弃并以取消结束"""
    scheduler, request, owner = handover_claim
    scheduler.checkpoint(request.execution_id, **owner, checkpoint=accepted_checkpoint())
    if not cancel_before_prepare:
        scheduler.prepare_handover(request.execution_id, **owner)
    scheduler.cancel(request.execution_id, actor=request.actor)
    assert scheduler.prepare_handover(request.execution_id, **owner)["cancel_requested"]
    scheduler.requeue_claim(request.execution_id, **owner, reason="worker generation handover")
    # An accepted native turn must be interrupted by its next owner even if
    # cancellation arrived before the handover marker was written.
    assert scheduler.snapshot(request.execution_id)["status"] == "queued"
    assert stored_checkpoint(scheduler, request.execution_id)["abandoned"]["status"] == "cancelled"


def test_stale_owner_cannot_prepare_handover(handover_claim):
    """持过期 claim 的 worker 不能准备交接"""
    scheduler, request, owner = handover_claim
    with pytest.raises(PermissionError):
        scheduler.prepare_handover(request.execution_id, **{**owner, "claim_token": "stale"})


@pytest.mark.parametrize("turn_id", [TURN_ID, "continued-turn"])
def test_handover_marker_lasts_until_new_turn_is_accepted(handover_claim, turn_id):
    """交接标记保留到新 worker 接受新的轮次为止，接回原轮次时仍保留"""
    scheduler, request, owner = handover_claim
    scheduler.checkpoint(request.execution_id, **owner, checkpoint=accepted_checkpoint())
    scheduler.prepare_handover(request.execution_id, **owner)
    scheduler.requeue_claim(request.execution_id, **owner, reason="worker generation handover")
    scheduler.register_worker(
        worker_id="dev:new", build_id="new", capabilities=["agent_session:codex"], pid=2,
        state="standby",
    )
    scheduler.activate_worker("dev:new", previous_worker_id="dev:old")
    claim = scheduler.claim(worker_id="dev:new", capabilities=["agent_session:codex"])
    scheduler.checkpoint(
        request.execution_id, worker_id="dev:new", claim_token=claim["claim_token"],
        checkpoint={**accepted_checkpoint(), "turn_id": turn_id},
    )
    assert bool(stored_checkpoint(scheduler, request.execution_id).get("worker_handover")) == (turn_id == TURN_ID)


@pytest.mark.parametrize("delayed_control", [False, True])
def test_worker_releases_one_execution_without_waiting_for_generation_exit(handover_claim, tmp_path, delayed_control):
    """worker 关闭时逐个释放执行而不中断原生轮次，新 worker 接回原轮次完成"""
    scheduler, request, owner = handover_claim
    shutdown = threading.Event()
    provider_started = threading.Event()
    interrupted = threading.Event()
    release_control = threading.Event()
    result = {}

    class BlockingBackend:
        provider_id = "codex"

        def execute(self, _invocation, context):
            context.set_interrupt_target(THREAD_ID, TURN_ID, "zero")
            provider_started.set()
            deadline = time.monotonic() + 3
            while not context.is_handover():
                assert time.monotonic() < deadline
                time.sleep(.01)
            checkpoint = stored_checkpoint(scheduler, request.execution_id)
            assert checkpoint["worker_handover"]["turn_kept_running"] is True
            interrupted.set()  # Follower detached; native turn remains running.
            if delayed_control:
                assert release_control.wait(3)
            raise ExecutionRecoveryDeferred("planned follower detachment")

        def interrupt(self, **_values):
            pytest.fail("Planned handover must not interrupt a native Codex turn")

    client = mock.Mock()
    client.start.side_effect = lambda execution_id, token: scheduler.start(execution_id, **owner)
    client.prepare_handover.side_effect = lambda execution_id, token: scheduler.prepare_handover(execution_id, **owner)
    client.checkpoint.side_effect = lambda execution_id, token, checkpoint: scheduler.checkpoint(execution_id, **owner, checkpoint=checkpoint)
    client.wait_for_control.side_effect = lambda *_args, **_kwargs: (time.sleep(0.01) or {"cancel_requested": False})
    client.heartbeat.return_value = {"cancel_requested": False}
    client.requeue.side_effect = lambda execution_id, token, reason: scheduler.requeue_claim(execution_id, **owner, reason=reason)
    worker = ExecutionWorker(
        build_id="old", worker_id="dev:old", scheduler=client,
        runtime=ExecutionRuntime(agent_providers={"codex": BlockingBackend()}, state_root=tmp_path / "runtime"),
        shutdown_requested=shutdown.is_set,
    )
    runner = threading.Thread(target=lambda: result.update(worker.execute_claim({
        "claim_token": owner["claim_token"],
        "execution": {"execution_id": request.execution_id, "request": request.to_payload(), "checkpoint": {}},
    })))
    runner.start()
    assert provider_started.wait(2)
    shutdown.set()
    assert interrupted.wait(2)
    if delayed_control:
        time.sleep(0.05)
        assert scheduler.snapshot(request.execution_id)["status"] == "running"
        client.requeue.assert_not_called()
        release_control.set()
    runner.join(timeout=4)
    assert not runner.is_alive()
    assert result["status"] == "requeued"
    assert scheduler.snapshot(request.execution_id)["status"] == "queued"
    client.finish.assert_not_called()
    backend_result, new_client, events, _recovery = invoke_backend(
        stored_checkpoint(scheduler, request.execution_id), completed=True,
    )
    assert backend_result.output["message"] == "finished"
    assert new_client.resume_turn.call_args.args == (THREAD_ID, TURN_ID)
    new_client.run_turn.assert_not_called()
    assert not any(kind == "agent_harness_provider_error_continue" for kind, _details in events)


def invoke_backend(checkpoint, *, cancelled=False, interrupted_turn=TURN_ID, completed=False, goal_status=""):
    client = mock.Mock()
    client.resume_turn.side_effect = CodexTurnInterrupted(THREAD_ID, interrupted_turn)
    client.read_turn.return_value = {"id": interrupted_turn, "status": "interrupted", "items": []}
    client.run_turn.return_value = mock.Mock(
        response="finished", thread_id=THREAD_ID, turn_id="turn-after-update",
        token_usage={}, goal=None,
    )
    def accept_turn(*_args, **kwargs):
        kwargs["turn_started_callback"]("turn-after-update")
        return client.run_turn.return_value
    client.run_turn.side_effect = accept_turn
    if completed:
        client.resume_turn.side_effect = None
        client.resume_turn.return_value = client.run_turn.return_value
    if goal_status:
        client.request.return_value = {"goal": {"status": goal_status}}
        client.run_goal.return_value = client.run_turn.return_value
    request = ExecutionRequest.new(
        actor="sample_admin", prompt="original task", service_type="agent_session",
        provider="codex", operation="turn", cwd="/tmp",
        runtime_options=({"goal": {"action": "set", "objective": "finish work"}} if goal_status else {}),
        session=SessionOptions(mode="persistent", binding_id="planned-handover", backend_route_id="zero"),
    )
    invocation = backends.BackendInvocation(
        request=request,
        session=RuntimeSession(
            mode="persistent", binding_id=request.session.binding_id,
            backend_session_id=THREAD_ID, backend_route_id="zero",
            is_new_backend_session=False,
        ),
        user_prompt=request.prompt, system_context="", attach_system_context=False,
        is_new_backend_session=False, recovery_checkpoint=checkpoint,
    )
    events = []
    recovery = []
    context = backends.BackendContext(
        emit_event=lambda kind, payload: events.append((kind, payload)),
        cancellation_requested=lambda: cancelled,
        set_interrupt_target=lambda *_args: None,
        set_provider_recovery_state=recovery.append,
    )
    result = backends.CodexBackend(client_factory=lambda _socket: client).execute(invocation, context)
    return result, client, events, recovery


def planned_checkpoint():
    return {
        **accepted_checkpoint(),
        "worker_handover": {"worker_id": "dev:old", "turn_id": TURN_ID},
        "turn_start_reason": "backend_service_update",
    }


def test_planned_handover_observes_same_turn_without_new_input(subtests, isolated_portal_case):
    """计划交接接回原轮次并保留用户停止

    新 Worker 观察已接受的原 turn，不发送额外输入或中断，不消耗 Provider 错误恢复预算；用户停止优先，新执行只送达一次原输入。

    :design: decision_075f66430606, decision_3e8c2a7f91d4
    """
    with subtests.test(contract='accepted_turn_is_observed'), isolated_portal_case():
        result, client, events, recovery = invoke_backend(planned_checkpoint(), completed=True)
        assert result.output["message"] == "finished"
        assert client.resume_turn.call_args.args == (THREAD_ID, TURN_ID)
        client.run_turn.assert_not_called()
        client.interrupt_turn.assert_not_called()
        assert not any(kind in {"recovery_input_sent", "agent_harness_provider_error_continue"}
                       for kind, _payload in events)
        assert all(state.get("provider_error_continue_count", 0) == 0 for state in recovery)
        assert all(state.get("recovery_budget", {}).get("attempts", 0) == 0 for state in recovery)
    with subtests.test(contract='user_stop_prevents_observation'), isolated_portal_case():
        with pytest.raises(InterruptedError):
            invoke_backend(planned_checkpoint(), cancelled=True)
    with subtests.test(contract='new_execution_sends_original_input_once'), isolated_portal_case():
        _result, client, _events, _recovery = invoke_backend({})
        client.resume_turn.assert_not_called()
        assert client.run_turn.call_count == 1
        assert client.run_turn.call_args.args == (THREAD_ID, "original task")


def test_unrelated_interruption_still_fails(subtests, isolated_portal_case):
    """交接恢复中遇到 codex 轮次中断时仍按失败处理"""
    for checkpoint in [accepted_checkpoint(), planned_checkpoint()]:
        with subtests.test(checkpoint=checkpoint), isolated_portal_case() as case_path:
            with pytest.raises(CodexTurnInterrupted):
                invoke_backend(checkpoint)
            with pytest.raises(CodexTurnInterrupted):
                invoke_backend(checkpoint, interrupted_turn="unrelated-turn")






def test_handover_preserves_native_goal_state(subtests, isolated_portal_case):
    """交接后无论 Goal 处于暂停、完成或进行中，新 worker 都只观察 Goal 不发新轮次"""
    for goal_status in ("paused", "complete", "active"):
        with subtests.test(goal_status=goal_status), isolated_portal_case() as case_path:
            _result, client, _events, _recovery = invoke_backend(planned_checkpoint(), goal_status=goal_status)
            assert client.run_goal.call_args.kwargs["action"] == "observe"
            client.run_turn.assert_not_called()


def test_handover_prepare_uses_real_execution_socket(handover_claim, tmp_path):
    """经真实执行服务 socket 准备交接时写入原轮次的交接标记"""
    scheduler, request, owner = handover_claim
    scheduler.checkpoint(request.execution_id, **owner, checkpoint=accepted_checkpoint())
    server = ExecutionServiceServer(tmp_path / "service.sock", scheduler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        client = SchedulerClient(worker_id="dev:old", build_id="old", capabilities=["agent_session:codex"])
        client.service = ExecutionServiceClient(socket_path=server.socket_path)
        assert client.prepare_handover(request.execution_id, owner["claim_token"])["ready"]
        assert stored_checkpoint(scheduler, request.execution_id)["worker_handover"]["turn_id"] == TURN_ID
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)


def test_new_worker_finishes_same_execution_through_real_socket(handover_claim, tmp_path):
    """新 worker 经真实 socket 接回原 codex 轮次并完成同一执行，不重发也不中断"""
    scheduler, request, owner = handover_claim
    scheduler.checkpoint(request.execution_id, **owner, checkpoint=accepted_checkpoint())
    scheduler.prepare_handover(request.execution_id, **owner)
    scheduler.requeue_claim(request.execution_id, **owner, reason="worker generation handover")
    scheduler.register_worker(
        worker_id="dev:new", build_id="new", capabilities=["agent_session:codex"],
        state="standby",
    )
    scheduler.activate_worker("dev:new", previous_worker_id="dev:old")
    provider = mock.Mock()
    def observe_turn(thread_id, turn_id, **kwargs):
        assert (thread_id, turn_id) == (THREAD_ID, TURN_ID)
        kwargs["turn_started_callback"](TURN_ID)
        return mock.Mock(response="task finished", thread_id=THREAD_ID, turn_id=TURN_ID, token_usage={})

    provider.resume_turn.side_effect = observe_turn
    server = ExecutionServiceServer(tmp_path / "new-worker.sock", scheduler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        client = SchedulerClient(worker_id="dev:new", build_id="new", capabilities=["agent_session:codex"])
        client.service = ExecutionServiceClient(socket_path=server.socket_path)
        worker = ExecutionWorker(
            build_id="new", worker_id="dev:new", scheduler=client,
            runtime=ExecutionRuntime(
                agent_providers={"codex": backends.CodexBackend(client_factory=lambda _socket: provider)},
                state_root=tmp_path / "new-runtime",
            ),
        )
        claim = worker.claim(wait_seconds=0.1)
        assert claim["execution"]["execution_id"] == request.execution_id
        worker.execute_claim(claim)
        snapshot = scheduler.snapshot(request.execution_id)
        assert snapshot["status"] == "completed", snapshot
        assert snapshot["result"]["output"]["message"] == "task finished"
        provider.run_turn.assert_not_called()
        provider.interrupt_turn.assert_not_called()
        assert stored_checkpoint(scheduler, request.execution_id)["turn_id"] == TURN_ID
        assert all(event["type"] not in {"failed", "cancelled"} for event in snapshot["events"])
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)
