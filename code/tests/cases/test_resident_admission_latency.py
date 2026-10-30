"""Admission latency contracts with real persistence and controlled slow dependencies.

:aspects: functional, performance
"""
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

import pytest

from management_portal.society_service import (
    invariant_locks,
    lifecycle,
    lineage_data_residents,
    resident_catalog,
    resident_conversation_stream,
    resident_delivery_work,
    resident_execution_domain,
    resident_execution_work,
    resident_protocol,
    resident_registration_domain,
    lineage_message_events,
    resident_input_queue_domain,
)
from management_portal.society_service.domain_ports import execution_port
from tests.cases.resident_execution_test_support import resident_record, input_delivery  # noqa: F401


def send(record, suffix="a", text="A durable input"):
    return resident_protocol.deliver_resident_input({
        "resident_id": record["resident_id"],
        "message": {"message_id": "msg_" + suffix * 32, "role": "user",
                    "content_parts": [{"type": "text", "text": text}]},
    }, "CASE-A")


def test_plain_admission_never_loads_workspace_or_projects_inline(input_delivery, monkeypatch):
    """普通消息受理不解析工作目录或同步发布展示

    普通消息受理不解析工作目录或同步发布展示。使用真实持久层和受控依赖验证消息身份、恢复及并发边界。
    """
    monkeypatch.setattr(lineage_data_residents, "residents_from_rows", lambda *a, **kw: pytest.fail("Workspace hydration during admission"))
    monkeypatch.setattr(resident_execution_work, "project_latest_conversation_state", lambda *a, **kw: pytest.fail("inline projection"))
    monkeypatch.setattr(lineage_message_events, "wake_watchers", lambda *a, **kw: pytest.fail("inline watcher dispatch"))
    first = send(input_delivery)
    second = send(input_delivery, "b", "Next input")
    assert first["accepted"] and second["accepted"]
    assert "state" not in first and "cursor" not in first
    assert resident_delivery_work.pending_ids() == [input_delivery["resident_id"]]
    repository = resident_conversation_stream.get_repository()
    assert repository.message(input_delivery["resident_id"], first["message_id"]) is not None
    assert repository.input_state(input_delivery["resident_id"], first["message_id"])["state"] == "queued"
    assert send(input_delivery)["message_id"] == first["message_id"]
    job = resident_execution_work.read(input_delivery)
    assert [item["user_message_id"] for item in job["queue"]] == [second["message_id"]]


def test_plain_admission_uses_real_command_and_context_preparation(resident_record, monkeypatch):
    """普通消息使用真实命令和引用准备，受理阶段不读取 Workspace。"""
    monkeypatch.setattr(lineage_data_residents, "residents_from_rows", lambda *a, **kw: pytest.fail("Workspace hydration during admission"))
    result = send(resident_record)
    assert result["accepted"]
    assert resident_conversation_stream.get_repository().input_state(
        resident_record["resident_id"], result["message_id"],
    )["state"] == "queued"


def test_image_cleanup_failure_retries_without_rejecting_admitted_input(input_delivery, monkeypatch):
    """图片清理仅在后台加载完整记录后执行，失败保留发布待办。"""
    from management_portal.society_service import resident_media_domain

    calls = []

    def cleanup(record):
        calls.append(record)
        assert record["owned_workspace"]["path"] == input_delivery["owned_workspace"]["path"]
        if len(calls) == 1:
            raise OSError("image cleanup unavailable")
        return []

    monkeypatch.setattr(resident_media_domain, "cleanup_expired_input_images", cleanup)
    result = send(input_delivery)
    assert result["accepted"] and not calls
    rid = input_delivery["resident_id"]
    with pytest.raises(OSError, match="image cleanup unavailable"):
        resident_delivery_work.drain(rid)
    assert resident_delivery_work.pending_ids() == [rid]
    assert resident_conversation_stream.get_repository().input_state(rid, result["message_id"])["state"] in {"queued", "running"}
    resident_delivery_work.drain(rid)
    assert len(calls) == 2
    assert resident_delivery_work.pending_ids() == []


def test_admission_rolls_back_input_with_failed_job_commit(input_delivery, monkeypatch):
    """任务提交失败时原子回滚消息和发布待办

    任务提交失败时原子回滚消息和发布待办。使用真实持久层和受控依赖验证消息身份、恢复及并发边界。
    """
    store = resident_catalog.get_catalog().state_store
    monkeypatch.setattr(store, "put_resident_execution_record", mock.Mock(side_effect=RuntimeError("commit failed")))
    with pytest.raises(RuntimeError, match="commit failed"):
        send(input_delivery)
    assert resident_delivery_work.pending_ids() == []
    assert resident_conversation_stream.get_repository().message(input_delivery["resident_id"], "msg_" + "a" * 32) is None


def test_projection_failure_recovers_and_ack_cannot_delete_newer_delivery(input_delivery, monkeypatch):
    """展示发布失败可恢复且不删除并发新消息待办

    展示发布失败可恢复且不删除并发新消息待办。使用真实持久层和受控依赖验证消息身份、恢复及并发边界。
    """
    rid = input_delivery["resident_id"]
    send(input_delivery)
    projection = resident_execution_work.project_latest_conversation_state
    monkeypatch.setattr(resident_execution_work, "project_latest_conversation_state", mock.Mock(side_effect=RuntimeError("slow storage unavailable")))
    with pytest.raises(RuntimeError, match="storage unavailable"):
        resident_delivery_work.drain(rid)
    assert resident_delivery_work.pending_ids() == [rid]
    events = []
    monkeypatch.setattr(lineage_message_events, "wake_watchers", lambda record, mid: events.append(mid))
    def publish_and_accept_next(*args, **kwargs):
        projection(*args, **kwargs)
        send(input_delivery, "b", "Arrived during projection")
    monkeypatch.setattr(resident_execution_work, "project_latest_conversation_state", publish_and_accept_next)
    resident_delivery_work.drain(rid)
    assert resident_delivery_work.pending_ids() == [rid]
    monkeypatch.setattr(resident_execution_work, "project_latest_conversation_state", projection)
    resident_delivery_work.recover()
    assert resident_delivery_work.pending_ids() == []
    assert events == ["msg_" + "a" * 32, "msg_" + "b" * 32]


def test_failed_wake_does_not_turn_committed_send_into_failure(input_delivery, monkeypatch):
    """后台唤醒失败仍返回持久受理成功

    后台唤醒失败仍返回持久受理成功。使用真实持久层和受控依赖验证消息身份、恢复及并发边界。
    """
    monkeypatch.setattr(lifecycle, "notify_execution_binding", mock.Mock(side_effect=RuntimeError("coordinator unavailable")))
    assert send(input_delivery)["accepted"]
    assert resident_delivery_work.pending_ids() == [input_delivery["resident_id"]]


def prepare_injection(record):
    mid = "msg_" + "b" * 32
    resident_execution_work.write(record, {
        "execution_id": "execution-" + "1" * 24, "actor": "CASE-A",
        "submitted_at": "2026-10-14T00:00:00Z", "queue": [{
            "user_message_id": mid, "actor": "CASE-A", "raw_message": "Additional input",
            "effective_message": "Additional input", "input_message": {
                "message_id": mid, "role": "user", "author_id": "CASE-A",
                "content_parts": [{"type": "text", "text": "Additional input"}],
                "created_at": "2026-10-14T00:00:00Z",
            },
        }],
    })
    return mid


@pytest.mark.parametrize("status", ["delivered", "pending", "refused"])
def test_injection_ack_is_durable_and_dispatch_is_unlocked(input_delivery, monkeypatch, status):
    """立即注入先持久受理并在状态锁外投递

    立即注入先持久受理并在状态锁外投递。使用真实持久层和受控依赖验证消息身份、恢复及并发边界。
    """
    rid = input_delivery["resident_id"]
    mid = prepare_injection(input_delivery)
    service = mock.Mock()
    service.input_control.return_value = None
    def append(*args, **kwargs):
        assert not invariant_locks.lock_held()
        if status == "refused":
            raise ValueError("turn has ended")
        return {"status": status}
    service.append_input.side_effect = append
    monkeypatch.setattr(execution_port, "service", lambda: service)
    prompt = resident_input_queue_domain._running_turn_prompt
    def prepare(*args, **kwargs):
        assert not invariant_locks.lock_held()
        return prompt(*args, **kwargs)
    monkeypatch.setattr(resident_input_queue_domain, "_running_turn_prompt", prepare)
    with monkeypatch.context() as admission:
        admission.setattr(lineage_data_residents, "residents_from_rows",
                          lambda *a, **kw: pytest.fail("Workspace hydration during injection admission"))
        receipt = resident_protocol.inject_queued_input_into_active_turn(rid, "CASE-A", mid)
    assert receipt["accepted"] and not receipt["injected"] and receipt["delivery_status"] == "pending"
    service.append_input.assert_not_called()
    assert resident_protocol.inject_queued_input_into_active_turn(rid, "CASE-A", mid)["already_injected"]
    resident_delivery_work.drain(rid)
    service.append_input.assert_called_once()
    job = resident_execution_work.read(input_delivery)
    if status == "delivered":
        assert not resident_execution_work.send_now_inputs(job)
        assert resident_conversation_stream.get_repository().input_state(rid, mid)["state"] == "completed"
    elif status == "pending":
        assert resident_execution_work.send_now_inputs(job)[0]["user_message_id"] == mid
    else:
        assert job["queue"][0]["user_message_id"] == mid


def test_blocked_projection_does_not_block_next_send(input_delivery, monkeypatch):
    """展示发布阻塞时仍能受理下一条消息

    展示发布阻塞时仍能受理下一条消息。使用真实持久层和受控依赖验证消息身份、恢复及并发边界。
    """
    send(input_delivery)
    entered, release = threading.Event(), threading.Event()
    original = resident_execution_work.project_latest_conversation_state
    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(resident_execution_work, "project_latest_conversation_state", slow)
    with ThreadPoolExecutor(max_workers=2) as executor:
        pending = executor.submit(resident_delivery_work.drain, input_delivery["resident_id"])
        try:
            assert entered.wait(2)
            assert executor.submit(send, input_delivery, "b", "Second input").result(timeout=2)["accepted"]
        finally:
            release.set()
        pending.result(timeout=5)


@pytest.mark.parametrize("change", ["stop", "replace"])
def test_injection_preparation_revalidates_turn_without_blocking_state(input_delivery, monkeypatch, change):
    """注入准备期间允许停止且防止投递到替换轮次

    注入准备期间允许停止且防止投递到替换轮次。使用真实持久层和受控依赖验证消息身份、恢复及并发边界。
    """
    rid = input_delivery["resident_id"]
    mid = prepare_injection(input_delivery)
    service = mock.Mock()
    service.input_control.return_value = None
    monkeypatch.setattr(execution_port, "service", lambda: service)
    resident_protocol.inject_queued_input_into_active_turn(rid, "CASE-A", mid)
    entered, release = threading.Event(), threading.Event()
    prepare = resident_input_queue_domain._running_turn_prompt
    def slow(*args):
        entered.set()
        assert release.wait(5)
        return prepare(*args)
    monkeypatch.setattr(resident_input_queue_domain, "_running_turn_prompt", slow)
    from management_portal.society_service.resident_record_domain import state_lock as _state_lock
    def change_turn():
        with _state_lock(rid):
            current = resident_execution_work.read(input_delivery)
            if change == "stop":
                current["stop_requested_at"] = "2026-10-16T00:00:00Z"
            else:
                current["execution_id"] = "execution-" + "2" * 24
            resident_execution_work.write(input_delivery, current)
    with ThreadPoolExecutor(max_workers=2) as executor:
        pending = executor.submit(resident_delivery_work.drain, rid)
        try:
            assert entered.wait(2)
            executor.submit(change_turn).result(timeout=2)
        finally:
            release.set()
        pending.result(timeout=5)
    service.append_input.assert_not_called()
    current = resident_execution_work.read(input_delivery)
    if change == "stop":
        assert current["queue"][0]["user_message_id"] == mid
    else:
        assert current["execution_id"] == "execution-" + "2" * 24


def test_admission_and_streaming_state_preserve_event_cursor_order(input_delivery, monkeypatch):
    """消息受理与流式状态并发写入保持事件游标顺序

    消息受理与流式状态并发写入保持事件游标顺序。使用真实持久层和受控依赖验证消息身份、恢复及并发边界。
    """
    rid = input_delivery["resident_id"]
    repository = resident_conversation_stream.get_repository()
    entered, release, writer_started = threading.Event(), threading.Event(), threading.Event()
    record_event = repository.events.record_event
    def pause_initial_input(connection, resident_id, event_type, payload, **kwargs):
        if kwargs.get("subject_id") == "msg_" + "a" * 32:
            entered.set()
            assert release.wait(5)
        return record_event(connection, resident_id, event_type, payload, **kwargs)
    monkeypatch.setattr(repository.events, "record_event", pause_initial_input)
    def publish_state():
        writer_started.set()
        return repository.set_state(rid, {"work_state": "running", "active_message_id": "msg_" + "b" * 32,
                                          "queued_count": 0, "intake_state": "open"})
    with ThreadPoolExecutor(max_workers=2) as executor:
        admission = executor.submit(send, input_delivery)
        try:
            assert entered.wait(2)
            streaming = executor.submit(publish_state)
            assert writer_started.wait(2)
            with pytest.raises(TimeoutError):
                streaming.result(timeout=0.1)
        finally:
            release.set()
        assert admission.result(timeout=5)["accepted"]
        streaming.result(timeout=5)
    events = repository.events_after(rid, 0)["events"]
    assert [event["cursor"] for event in events] == list(range(1, len(events) + 1))
    assert any(event["payload"].get("input_state", {}).get("message_id") == "msg_" + "a" * 32 for event in events)
