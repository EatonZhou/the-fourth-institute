from tests.cases.resident_delivery_test_support import published_delivery as _published_delivery
from management_portal.society_service import resident_conversation_stream
from management_portal.society_service.domain_ports import execution_port
"""Regression coverage for Send now racing with a provider's final reply.

No live provider is contacted. Resident and execution persistence are real;
only the provider acknowledgement and final-answer timing are controlled.
"""

import threading
from unittest import mock

import pytest

from management_portal.codex_app_server_client import CodexAppServerClient
from management_portal.codex_app_server_protocol import TurnWaiter
from management_portal.execution.contracts import ExecutionRequest, ExecutionResult
from management_portal.execution.scheduler import Scheduler, SchedulerClient
from management_portal.society_service import (
    resident_execution_engine_domain,
    resident_execution_work,
    resident_input_queue_domain,
    resident_protocol,
    resident_record_domain,
)
from management_portal.society_service import resident_domain as backend
from management_portal.society_service import resident_execution_domain
from tests.cases.execution_layer_test_support import _stack, _close
from tests.cases.resident_execution_test_support import resident_record  # noqa: F401


@pytest.mark.parametrize("close_phase", [
    "open", "before_send", "during_fetch", "final_started", "final_started_during_fetch",
])
def test_send_now_uses_next_turn_if_final_reply_closed_input(
    resident_record, tmp_path, monkeypatch, close_phase,
):
    """最终回复关闭输入后立即发送的消息转入下一轮"""
    final_answer_seen = close_phase != "open"
    resident_id = str(resident_record["resident_id"])
    message_id = "msg_" + "b" * 32
    _scheduler, server, thread, client, service = _stack(tmp_path)
    worker = SchedulerClient(
        worker_id="explore-1:send-now-repro", build_id="test-build",
        capabilities=["agent_session:codex"], service_client=client,
    )
    provider = CodexAppServerClient(tmp_path / "unused-provider.sock")
    steer = mock.Mock(return_value={})
    monkeypatch.setattr(provider.controls, "steer_turn", steer)
    monkeypatch.setattr(execution_port, "service", lambda: service)
    try:
        worker.register()
        request = ExecutionRequest.new(
            actor="CASE-A", agent_id=resident_id, prompt="initial task",
            service_type="agent_session", provider="codex", operation="turn",
        )
        service.submit(request)
        claim = worker.claim(wait_seconds=0.1)
        token = claim["claim_token"]
        worker.start(request.execution_id, token)
        worker.checkpoint(request.execution_id, token, {
            "provider": "codex", "backend_session_id": "thread-repro",
            "backend_route_id": "zero", "turn_id": "turn-repro", "resumable": True,
        })
        server.agent_data.begin_execution(
            execution_id=request.execution_id, agent_id=resident_id,
            actor_ref="CASE-A", provider="codex", started_at="2026-09-24T00:00:00Z",
        )
        session = server.agent_data.ensure_provider_session(
            provider="codex", backend_route_id="zero", backend_session_id="thread-repro",
            agent_id=resident_id, actor_ref="CASE-A",
        )
        server.agent_data.bind_execution_provider_session(
            request.execution_id, provider_session_ref=session["provider_session_ref"],
        )
        server.agent_data.record_input_acceptance(
            request.execution_id, agent_id=resident_id, provider_turn_id="turn-repro",
        )
        server.agent_data.display_chat_message(resident_id, {
            "message_id": "msg_" + "a" * 32, "role": "user", "author_id": "CASE-A",
            "content_parts": [{"type": "text", "text": "initial task"}],
            "created_at": "2026-09-24T00:00:00Z",
        })
        resident_execution_work.write(resident_record, {
             "execution_id": request.execution_id,
            **resident_execution_work.continuation_fields(resident_record),
            "actor": "CASE-A", "status": "running", "queue": [{
                'execution_id': "job_queued_input", "user_message_id": message_id,
                "actor": "CASE-A", "raw_message": "please also check mobile",
                "actual_sent_prompt": "please also check mobile",
                "input_message": {
                    "message_id": message_id, "role": "user", "author_id": "CASE-A",
                    "content_parts": [{"type": "text", "text": "please also check mobile"}],
                    "created_at": "2026-09-24T00:00:00Z",
                },
            }],
         "submitted_at": "2026-10-14T00:00:00Z"})
        waiter = TurnWaiter(
            thread_id="thread-repro", turn_id="turn-repro",
            final_answer_seen=close_phase == "before_send",
        )
        provider.work.turns[waiter.thread_id] = waiter

        def start_final_answer():
            provider.events.dispatch_notification("item/started", {
                "threadId": waiter.thread_id,
                "turnId": waiter.turn_id,
                "item": {"id": "final-stream", "type": "agentMessage", "phase": "final_answer", "text": ""},
            })

        if close_phase == "final_started":
            start_final_answer()
        sent = _published_delivery(resident_protocol.inject_queued_input_into_active_turn(
            resident_id, "CASE-A", message_id,
        ))
        assert sent["accepted"] is True
        assert sent["input_state"]["state"] == "queued"
        assert sent["queue"]["items"] == []
        assert len(resident_execution_work.send_now_inputs(resident_execution_work.read(resident_record))) == 1
        retry = _published_delivery(resident_protocol.inject_queued_input_into_active_turn(resident_id, "CASE-A", message_id))
        assert retry["already_injected"] is True
        assert retry["queue"]["items"] == []
        control_id = resident_input_queue_domain.resident_input_control_id(resident_id, message_id)
        pending = service.input_control(control_id, agent_id=resident_id)
        assert pending["status"] == "pending"

        controls = worker.wait_for_control(
            request.execution_id, token, timeout_seconds=1,
        )["turn_controls"]
        assert len(controls) == 1
        callbacks = []

        def acknowledge(control, delivered, error):
            callbacks.append(worker.complete_input_control(
                request.execution_id, token, control["control_id"],
                delivered=delivered, error=error,
            ))

        def next_control():
            if close_phase == "during_fetch":
                waiter.final_answer_seen = True
            elif close_phase == "final_started_during_fetch":
                start_final_answer()
            return controls.pop(0) if controls else None

        provider.controls.deliver_turn_control(
            thread_id=waiter.thread_id, waiter=waiter,
            turn_control_provider=next_control,
            turn_control_result_callback=acknowledge, cancellation_requested=None,
        )
        assert steer.call_count == (0 if final_answer_seen else 1)
        assert len(callbacks) == (0 if final_answer_seen else 1)
        worker.finish(ExecutionResult(
            execution_id=request.execution_id, status="completed",
            output={"text": "finished the initial task"},
        ), token)
        receipt = service.input_control(control_id, agent_id=resident_id)
        job = resident_execution_work.read(resident_record)
        continuation_id = job["continuation_id"]
        resident_execution_domain._mark_completion_applied(job, "2026-09-24T00:01:00Z")
        resident_execution_work.write(resident_record, job)
        monkeypatch.setattr(resident_input_queue_domain, "acknowledge_execution", lambda *_: True)
        advanced = resident_execution_domain._finalize_applied_execution(resident_id, continuation_id, job)
        assert advanced is final_answer_seen
        state = resident_protocol.get_resident_input_state(resident_id, "CASE-A", message_id)
        next_job = resident_execution_work.read(resident_record)
        if final_answer_seen:
            assert receipt["status"] == "failed"
            assert receipt["error"] == "provider_turn_finished_before_delivery"
            assert next_job["user_message_id"] == message_id
            assert next_job["execution_id"].startswith("execution-")
            assert next_job["execution_id"] != request.execution_id
            assert next_job["continuation_id"] == continuation_id
            assert not next_job.get("submitted_at")
            assert next_job["queue"] == []
            assert state["state"] in {"queued", "running"}
            retry_after_promotion = _published_delivery(resident_protocol.inject_queued_input_into_active_turn(
                resident_id, "CASE-A", message_id,
            ))
            assert retry_after_promotion["already_injected"] is True
            assert retry_after_promotion["queue"]["items"] == []
            # Run the real continuation with an isolated provider boundary;
            # it must process this exact message once, then go idle.
            deliveries = []

            def respond(record, job):
                deliveries.append(job["user_message_id"])
                return {"created_at": "2026-09-24T00:02:00Z"}, record

            monkeypatch.setattr(resident_execution_domain, "send_to_resident_backend", respond)
            monkeypatch.setattr(resident_execution_domain, "_notify_lineage_message_completed", lambda *_a, **_kw: None)
            resident_execution_domain.run_resident_execution(resident_id, continuation_id)
            assert deliveries == [message_id]
            assert resident_execution_work.read(resident_record) == {}
        else:
            assert receipt["status"] == "delivered"
            assert state["state"] == "completed"
            assert next_job == {}
    finally:
        provider.close()
        _close(server, thread)


@pytest.fixture
def queued_send_now(resident_record):
    resident_id = str(resident_record["resident_id"])
    message_id = "msg_" + "c" * 32
    job = {
         "actor": "CASE-A", "status": "running",
        "execution_id": "execution-" + "d" * 24,
        **resident_execution_work.continuation_fields(resident_record),
        "queue": [{
            'execution_id': "job_followup", "user_message_id": message_id, "actor": "CASE-A",
            "actual_sent_prompt": "follow-up", "raw_message": "follow-up",
            "input_message": {
                "message_id": message_id, "role": "user", "author_id": "CASE-A",
                "content_parts": [{"type": "text", "text": "follow-up"}],
                "created_at": "2026-09-24T00:00:00Z",
            },
        }],
     "submitted_at": "2026-10-14T00:00:00Z"}
    resident_execution_work.write(resident_record, job)
    return resident_id, message_id, job


def test_send_now_retry_reconciles_delivered_receipt(
    resident_record, queued_send_now, monkeypatch,
):
    """立即发送重试复用已投递回执且只追加一次"""
    resident_id, message_id, _job = queued_send_now
    service = mock.Mock()
    service.append_input.return_value = {"status": "pending"}
    service.input_control.side_effect = [
        None, {"status": "pending"}, {"status": "delivered"},
    ]
    monkeypatch.setattr(execution_port, "service", lambda: service)

    first = _published_delivery(resident_protocol.inject_queued_input_into_active_turn(resident_id, "CASE-A", message_id))
    assert first["accepted"] is True
    assert first["queue"]["items"] == []
    retry = _published_delivery(resident_protocol.inject_queued_input_into_active_turn(resident_id, "CASE-A", message_id))
    assert retry["already_injected"] is True
    assert retry["queue"]["items"] == []
    delivered = _published_delivery(resident_protocol.inject_queued_input_into_active_turn(resident_id, "CASE-A", message_id))
    assert delivered["already_injected"] is True
    assert delivered["queue"]["items"] == []
    assert delivered["input_state"]["state"] == "completed"
    service.append_input.assert_called_once()


@pytest.mark.parametrize("marked", [False, True])
def test_send_now_is_refused_for_backend_without_active_turn_input(
    resident_record, queued_send_now, monkeypatch, marked,
):
    """后端不接收轮内输入时拒绝立即发送并保留队列位置

    A Backend that takes no input during a turn has no Send now: the
    request is refused and the message keeps its place in the queue.  Every
    current Backend takes such input (Claude Code, Kimi Code and DSH since
    2026-10-03), so the test gives one Backend that capability's absence."""
    resident_id, message_id, job = queued_send_now
    resident_record["backend"] = "claude"
    resident_record_domain.save_resident_record(resident_record)
    from dataclasses import replace
    from management_portal.society_service import resident_execution_provider

    real_provider_for = resident_execution_provider.provider_for

    def provider_without_turn_input(record):
        import copy

        provider = copy.copy(real_provider_for(record))
        provider.capabilities = replace(provider.capabilities, supports_active_turn_input=False)
        return provider

    monkeypatch.setattr(resident_execution_provider, "provider_for", provider_without_turn_input)
    if marked:  # an item marked for injection before the backend changed
        job["queue"][0]["active_turn_input"] = {
            "control_id": resident_input_queue_domain.resident_input_control_id(resident_id, message_id),
            "actor": "CASE-A",
        }
        resident_execution_work.write(resident_record, job)
    service = mock.Mock()
    monkeypatch.setattr(execution_port, "service", lambda: service)

    with pytest.raises(ValueError, match="不能在运行中接收新消息"):
        _published_delivery(resident_protocol.inject_queued_input_into_active_turn(resident_id, "CASE-A", message_id))

    current = resident_execution_work.read(resident_record)
    assert [item["user_message_id"] for item in current["queue"]] == [message_id]
    assert not current.get("send_now_next_turn")
    assert not resident_execution_work.send_now_inputs(current)
    target = resident_record_domain.resolve_resident_runtime_target(resident_id, "CASE-A")
    assert resident_protocol._conversation_controls(target, "CASE-A", {})["active_turn_input"] is False
    service.append_input.assert_not_called()


@pytest.mark.parametrize("job_status", ["running", "completed"])
def test_send_now_does_not_requeue_delivered_input_for_non_append_backend(
    resident_record, queued_send_now, job_status,
):
    """后端不支持追加时已投递输入不会重新排队"""
    resident_id, message_id, job = queued_send_now
    resident_record["backend"] = "claude"
    resident_record_domain.save_resident_record(resident_record)
    resident_execution_work.materialize_conversation_input(
        resident_record, job["queue"][0], activated_at="2026-09-24T00:01:00Z",
    )
    resident_conversation_stream.get_repository().set_input_state(
        resident_id, message_id, "completed", updated_at="2026-09-24T00:01:00Z",
    )
    job["status"] = job_status
    resident_execution_work.write(resident_record, job)

    result = _published_delivery(resident_protocol.inject_queued_input_into_active_turn(resident_id, "CASE-A", message_id))

    assert result["already_injected"] is True
    assert result["queue"]["items"] == []
    assert resident_execution_work.read(resident_record)["queue"] == []


def test_send_now_handles_append_capability_change_after_check(
    resident_record, queued_send_now,
):
    """追加能力在检查后改变时立即发送恢复普通排队"""
    resident_id, message_id, _job = queued_send_now
    service = mock.Mock()
    service.input_control.return_value = None
    service.append_input.side_effect = ValueError("active execution does not support appended input")
    with mock.patch.object(execution_port, "service", return_value=service):
        result = _published_delivery(resident_protocol.inject_queued_input_into_active_turn(
            resident_id, "CASE-A", message_id,
        ))

    assert result["accepted"] is True
    assert [item["message_id"] for item in result["queue"]["items"]] == [message_id]
    assert "active_turn_input" not in resident_execution_work.read(resident_record)["queue"][0]


@pytest.mark.parametrize("delivered", [True, False])
def test_send_now_receipt_from_peer_scheduler_updates_live_queue(
    resident_record, queued_send_now, tmp_path, monkeypatch, delivered,
):
    """其他调度器提交的投递回执及时更新运行中队列

    A quiet, still-running turn must not need a local delivery event."""
    resident_id, message_id, job = queued_send_now
    scheduler, server, thread, client, service = _stack(tmp_path)
    peer = Scheduler()
    worker = SchedulerClient(
        worker_id="explore-1:peer-receipt", build_id="test-build",
        capabilities=["agent_session:codex"], service_client=client,
    )
    request = ExecutionRequest.new(
        actor="CASE-A", agent_id=resident_id, prompt="initial task",
        service_type="agent_session", provider="codex", operation="turn",
    )
    first_snapshot, reconciled = threading.Event(), threading.Event()
    errors = []
    waiter = None
    token = ""
    monkeypatch.setattr(execution_port, "service", lambda: service)

    def observe_snapshot(*args, **kwargs):
        if not resident_execution_work.send_now_inputs(resident_execution_work.read(resident_record)):
            reconciled.set()
        first_snapshot.set()

    monkeypatch.setattr(resident_execution_engine_domain, "_project_resident_execution_snapshot", observe_snapshot)

    def wait_for_execution():
        try:
            resident_execution_engine_domain.wait_for_resident_execution(
                service, execution_id=request.execution_id, actor="CASE-A",
                resident_id=resident_id,  persist_messages=False,
            )
        except Exception as error:
            errors.append(error)

    try:
        worker.register()
        service.submit(request)
        claim = worker.claim(wait_seconds=0.1)
        token = claim["claim_token"]
        worker.start(request.execution_id, token)
        server.agent_data.begin_execution(
            execution_id=request.execution_id, agent_id=resident_id,
            actor_ref="CASE-A", provider="codex", started_at="2026-09-24T00:00:00Z",
        )
        job["execution_id"] = request.execution_id
        resident_execution_work.write(resident_record, job)
        sent = _published_delivery(resident_protocol.inject_queued_input_into_active_turn(resident_id, "CASE-A", message_id))
        assert sent["accepted"]
        assert sent["queue"]["items"] == []
        waiter = threading.Thread(target=wait_for_execution, daemon=True)
        waiter.start()
        assert first_snapshot.wait(2)
        assert not reconciled.is_set()

        peer.complete_input_control(
            resident_input_queue_domain.resident_input_control_id(resident_id, message_id),
            execution_id=request.execution_id, worker_id="explore-1:peer-receipt",
            claim_token=token, delivered=delivered, error="" if delivered else "delivery_failed",
        )
        assert reconciled.wait(2), "receipt was hidden until turn completion or a local event"
        assert not errors
        snapshot = scheduler.snapshot(request.execution_id)
        assert snapshot["status"] == "running"
        assert not any(e["type"] in {"input_control_delivered", "input_control_failed"}
                       for e in snapshot["events"])
        queue = resident_execution_work.read(resident_record)["queue"]
        state = resident_protocol.get_resident_input_state(resident_id, "CASE-A", message_id)
        if delivered:
            assert queue == []
            assert state["state"] == "completed"
            messages = resident_conversation_stream.get_repository().read_messages(resident_id)
            assert [m["message_id"] for m in messages] == [message_id]
        else:
            assert [item["user_message_id"] for item in queue] == [message_id]
            deferred = resident_execution_work.read(resident_record)["queue"]
            assert deferred[0]["user_message_id"] == message_id
            assert "active_turn_input" not in deferred[0]
            assert state["state"] == "queued"
    finally:
        if token:
            worker.finish(ExecutionResult(execution_id=request.execution_id, status="completed"), token)
        if waiter is not None:
            waiter.join(timeout=3)
            assert not waiter.is_alive()
        _close(server, thread)


def test_send_now_preserves_dispatch_across_recovery_and_queue_clear(
    resident_record, queued_send_now, monkeypatch,
):
    """立即发送的投递意图跨恢复和清空队列后保留"""
    resident_id, message_id, original = queued_send_now
    service = mock.Mock()
    service.append_input.return_value = {"status": "pending"}
    service.input_control.side_effect = [
        None, TimeoutError("temporarily unavailable"), {"status": "pending"}, {"status": "delivered"},
    ]
    monkeypatch.setattr(execution_port, "service", lambda: service)
    ack = mock.Mock(return_value=True)
    monkeypatch.setattr(resident_input_queue_domain, "acknowledge_execution", ack)
    _published_delivery(resident_protocol.inject_queued_input_into_active_turn(resident_id, "CASE-A", message_id))
    with pytest.raises(KeyError, match="queue item not found"):
        resident_protocol.cancel_resident_queue_item(resident_id, "CASE-A", message_id)
    cleared = resident_protocol.clear_resident_queue(resident_id, "CASE-A")
    assert cleared["cleared_count"] == 0
    assert message_id not in cleared["skipped_message_ids"]
    assert cleared["errors"] == []
    service.cancel.assert_not_called()
    job = resident_execution_work.read(resident_record)
    resident_execution_domain._mark_completion_applied(job, "2026-09-24T00:01:00Z")
    resident_execution_work.write(resident_record, job)

    for _attempt in range(2):
        # Each retry uses only persisted state, as after a Resident restart.
        current = resident_execution_work.read(resident_record)
        assert not resident_execution_domain._finalize_applied_execution(
            resident_id, original["continuation_id"], current,
        )
        pending = resident_execution_work.read(resident_record)
        assert pending["queue"] == []
        assert resident_execution_work.send_now_inputs(pending)[0]["user_message_id"] == message_id
        assert resident_protocol.get_resident_input_state(resident_id, "CASE-A", message_id)["state"] == "queued"
        ack.assert_not_called()

    assert not resident_execution_domain._finalize_applied_execution(resident_id, original["continuation_id"], pending)
    assert resident_execution_work.read(resident_record) == {}
    ack.assert_called_once()
    service.append_input.assert_called_once()
    retry = _published_delivery(resident_protocol.inject_queued_input_into_active_turn(resident_id, "CASE-A", message_id))
    assert retry["already_injected"] is True
    repository = resident_conversation_stream.get_repository()
    assert [item["message_id"] for item in repository.read_messages(resident_id)] == [message_id]


def test_send_now_recovers_intent_committed_before_append(
    resident_record, queued_send_now, monkeypatch,
):
    """追加调用前已提交的立即发送意图可恢复投递"""
    resident_id, message_id, original = queued_send_now
    service = mock.Mock()
    service.input_control.side_effect = [None, {"status": "failed"}]
    service.append_input.return_value = {"status": "pending"}
    monkeypatch.setattr(execution_port, "service", lambda: service)
    receipt = resident_protocol.inject_queued_input_into_active_turn(resident_id, "CASE-A", message_id)
    assert receipt["accepted"] and not receipt["injected"]
    service.append_input.assert_not_called()
    intent = resident_execution_work.send_now_inputs(resident_execution_work.read(resident_record))[0]["active_turn_input"]
    assert set(intent) == {"control_id", "actor"}
    assert not resident_input_queue_domain.reconcile_queued_turn_inputs(resident_id, original['execution_id'])
    service.append_input.assert_called_once()
    assert service.append_input.call_args.kwargs["control_id"] == resident_input_queue_domain.resident_input_control_id(resident_id, message_id)
    assert service.append_input.call_args.kwargs["input"] == [
        {"type": "text", "text": "<user-message>\nfollow-up\n</user-message>"},
    ]
    assert resident_input_queue_domain.reconcile_queued_turn_inputs(resident_id, original['execution_id'])
    current = resident_execution_work.read(resident_record)
    assert len(current["queue"]) == 1
    assert current["queue"][0]["user_message_id"] == message_id
    assert "active_turn_input" not in current["queue"][0]
    assert resident_protocol.get_resident_input_state(resident_id, "CASE-A", message_id)["state"] == "queued"


@pytest.mark.parametrize("turn_state", ["finished", "held", "stopping"])
@pytest.mark.parametrize("close_intake", [False, True])
def test_send_now_without_receiving_turn_keeps_original_for_next_turn(
    resident_record, queued_send_now, monkeypatch, turn_state, close_intake,
):
    """门面状态检查后执行变化时，原消息排到下一轮且不绕过接单门控。"""
    import copy

    resident_id, message_id, job = queued_send_now
    original = copy.deepcopy(job["queue"][0])
    job["user_message_id"] = "msg_" + "b" * 32
    job["input_message"] = {**original["input_message"], "message_id": job["user_message_id"]}
    earlier = {**copy.deepcopy(original), "user_message_id": "msg_" + "e" * 32,
               "execution_id": "job_earlier"}
    earlier["input_message"]["message_id"] = earlier["user_message_id"]
    job["queue"].insert(0, earlier)
    if close_intake or turn_state == "held":
        resident_protocol.change_resident_state(resident_id, "CASE-A", state_domain="intake", transition="close")
        job["intake_state"] = "closed"
    if turn_state == "finished":
        resident_execution_domain._mark_completion_applied(job, "2026-10-26T00:00:00Z")
    elif turn_state == "held":
        job.pop("submitted_at", None)
    else:
        job["stop_requested_at"] = "2026-10-26T00:00:00Z"
    resident_execution_work.write(resident_record, job)
    service = mock.Mock()
    monkeypatch.setattr(execution_port, "service", lambda: service)
    monkeypatch.setattr(resident_input_queue_domain, "acknowledge_execution", lambda *_: True)
    if turn_state == "held":
        assert resident_execution_work.is_execution_held(resident_execution_work.read(resident_record))
    elif turn_state == "finished":
        assert resident_execution_work.completion_applied(resident_execution_work.read(resident_record))

    for _ in range(2):
        receipt = resident_protocol.inject_queued_input_into_active_turn(resident_id, "CASE-A", message_id)
        assert receipt["accepted"] is True
        assert receipt["injected"] is False
        assert receipt["delivery_status"] == "next_turn"
        current = resident_execution_work.read(resident_record)
        assert [item["user_message_id"] for item in current["queue"]] == [message_id, earlier["user_message_id"]]
        assert current["queue"][0]["input_message"] == original["input_message"]
        assert current["queue"][0]["actor"] == original["actor"]
        assert not resident_execution_work.send_now_inputs(current)
        assert current.get("stop_requested_at") == job.get("stop_requested_at")
        assert current.get("intake_state") == ("closed" if close_intake or turn_state == "held" else "open")
    service.append_input.assert_not_called()
    if turn_state == "finished":
        assert resident_execution_domain._finalize_applied_execution(resident_id, job["continuation_id"], current)
        next_job = resident_execution_work.read(resident_record)
        assert next_job["user_message_id"] == message_id
        assert resident_execution_work.is_execution_held(next_job) is close_intake


def test_send_now_held_queue_head_is_not_lost_or_duplicated(resident_record, queued_send_now):
    resident_id, message_id, job = queued_send_now
    resident_protocol.change_resident_state(resident_id, "CASE-A", state_domain="intake", transition="close")
    job["intake_state"] = "closed"
    resident_execution_work.promote_queue_item(job, job["queue"][0], [], "2026-10-26T00:00:00Z")
    resident_execution_work.write(resident_record, job)
    for _ in range(2):
        receipt = resident_protocol.inject_queued_input_into_active_turn(resident_id, "CASE-A", message_id)
        assert receipt["delivery_status"] == "next_turn"
        assert receipt["injected"] is False
        current = resident_execution_work.read(resident_record)
        assert resident_execution_work.is_intake_held(current)
        assert [item["user_message_id"] for item in resident_execution_work.public_waiting_queue(current)] == [message_id]
        assert not resident_execution_work.send_now_inputs(current)


@pytest.mark.parametrize("close_intake", [False, True])
def test_send_now_after_execution_finished_falls_back_and_respects_intake(
    resident_record, queued_send_now, monkeypatch, close_intake,
):
    """执行已结束时立即发送转入下一轮并遵循接收状态"""
    resident_id, message_id, original = queued_send_now
    service = mock.Mock()
    service.append_input.side_effect = ValueError("execution has no active turn")
    service.input_control.return_value = None
    monkeypatch.setattr(execution_port, "service", lambda: service)
    monkeypatch.setattr(resident_input_queue_domain, "acknowledge_execution", lambda *_: True)
    if close_intake:
        resident_protocol.change_resident_state(resident_id, "CASE-A", state_domain="intake", transition="close")
    result = _published_delivery(resident_protocol.inject_queued_input_into_active_turn(resident_id, "CASE-A", message_id))
    assert result["accepted"] is True
    assert [item["message_id"] for item in result["queue"]["items"]] == [message_id]
    job = resident_execution_work.read(resident_record)
    assert "active_turn_input" not in job["queue"][0]
    resident_execution_domain._mark_completion_applied(job, "2026-09-24T00:01:00Z")
    resident_execution_work.write(resident_record, job)
    assert resident_execution_domain._finalize_applied_execution(resident_id, original["continuation_id"], job)
    next_job = resident_execution_work.read(resident_record)
    assert next_job["user_message_id"] == message_id
    assert resident_execution_work.is_execution_held(next_job) is close_intake
    assert bool(next_job.get("continuation_id")) is not close_intake
    assert not next_job.get("submitted_at")


def test_send_now_before_binding_survives_backend_startup_failure(
    resident_record, queued_send_now, monkeypatch,
):
    """绑定前立即发送的输入在后端启动失败后仍保留"""
    resident_id, message_id, original = queued_send_now
    original.pop("submitted_at", None)
    resident_execution_work.write(resident_record, original)
    service = mock.Mock()
    monkeypatch.setattr(execution_port, "service", lambda: service)
    monkeypatch.setattr(resident_execution_domain, "_notify_lineage_message_terminal", lambda *_a, **_kw: None)

    def fail_before_binding(*_args, **_kwargs):
        result = _published_delivery(resident_protocol.inject_queued_input_into_active_turn(resident_id, "CASE-A", message_id))
        assert result["accepted"] is True
        assert result["queue"]["items"] == []
        assert not resident_input_queue_domain.reconcile_queued_turn_inputs(resident_id, original['execution_id'])
        raise RuntimeError("execution startup failed")

    monkeypatch.setattr(resident_execution_domain, "send_to_resident_backend", fail_before_binding)
    resident_execution_domain.run_resident_execution(resident_id, original["continuation_id"])
    next_job = resident_execution_work.read(resident_record)
    assert next_job["user_message_id"] == message_id
    assert next_job["previous_execution_failure"]["error"] == "execution startup failed"
    assert not next_job.get("pending_active_turn_inputs")
    assert not next_job.get("active_turn_input")
    service.append_input.assert_not_called()
    service.input_control.assert_not_called()


def test_send_now_receipt_cannot_modify_replaced_job(
    resident_record, queued_send_now, monkeypatch,
):
    """旧立即发送回执不修改替换任务或输入归属"""
    resident_id, message_id, original = queued_send_now
    service = mock.Mock()
    service.append_input.return_value = {"status": "pending"}
    service.input_control.return_value = None
    monkeypatch.setattr(execution_port, "service", lambda: service)
    _published_delivery(resident_protocol.inject_queued_input_into_active_turn(resident_id, "CASE-A", message_id))
    repository = resident_conversation_stream.get_repository()
    original_occurrence = repository.agent_data.chat_message(resident_id, message_id)
    original_provenance = repository.agent_data.chat_message_provenance(resident_id, message_id)

    def replace_job(*_args, **_kwargs):
        replacement = {**original, 'execution_id': "job_replacement", "queue": []}
        resident_execution_work.write(resident_record, replacement)
        return {"status": "delivered"}

    service.input_control.side_effect = replace_job
    assert not resident_input_queue_domain.reconcile_queued_turn_inputs(resident_id, original['execution_id'])
    assert resident_execution_work.read(resident_record)['execution_id'] == "job_replacement"
    # The accepted input survives; an old receipt cannot bind its occurrence
    # to the replacement execution.
    assert repository.message(resident_id, message_id) is not None
    assert repository.agent_data.chat_message(resident_id, message_id) == original_occurrence
    assert repository.agent_data.chat_message_provenance(resident_id, message_id) == original_provenance
