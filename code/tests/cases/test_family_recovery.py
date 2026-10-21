"""Facade continue restores the original input and keeps its projection identity.

:module: lineage.hatg
:importance: P1
:design: decision_44cf587c1721
"""
from datetime import datetime, timedelta, timezone

import pytest

from management_portal.society_service import family_facade, family_recovery, lineage_message_events, resident_conversation_stream, resident_execution_work
from management_portal.society_service.resident_workspace_domain import _find_resident
from tests.cases.isa_test_support import run
from tests.cases.test_family_reassign import family, delivery_resident, future_creation_store  # noqa: F401
from tests.cases.test_resident_mailbox_requeue import _round
from tests.cases.family_test_support import set_role


def _send(family, text, quote="", actor="sample_admin"):
    message = {"content_parts": [{"type": "text", "text": text}]}
    if quote:
        message["quote"] = {"message_id": quote}
    return run("DeliverMessage", {"resident_id": family["facade"], "message": message}, actor=actor).raise_for_error()["message_id"]


def _record(family):
    return _find_resident(family["facade"])


def _view(family, message_id):
    facade = _record(family)
    return family_recovery.view(facade, family_recovery.records(facade)[message_id])


def test_continue_requeues_failed_member_once_and_keeps_original_link(family):
    """Family continue 恢复原成员输入并保留投影关联"""
    repository = resident_conversation_stream.get_repository()
    root = _send(family, "完成双芯片方案，保留原始要求")
    member_message, _ = _round(family["wrong"], state="failed", text="完成双芯片方案，保留原始要求")
    family_facade.complete_relay((family["facade"], root), family["wrong"], member_message)
    before = family_facade.relay_links(_record(family))[root]
    command = _send(family, "continue")
    assert repository.input_state(family["wrong"], member_message)["state"] == "queued"
    assert _view(family, command)["original_message_id"] == root
    assert _view(family, command)["resident_id"] == family["wrong"]
    assert family_facade.relay_links(_record(family))[root] == before
    # Retrying the same delivery and sending another continue do not add work.
    family_recovery.handle(_record(family), command)
    second = _send(family, "continue")
    work = resident_execution_work.read(_find_resident(family["wrong"]))
    assert [item["user_message_id"] for item in resident_execution_work.normalized_queue(work)] == [member_message]
    assert _view(family, second)["original_message_id"] == root
    assert _view(family, second)["status"] == "queued"
    assert repository.input_state(family["facade"], command)["state"] == "completed"
    assert command not in family_facade.relay_links(_record(family))


def test_failed_routing_uses_original_payload_and_new_deduplicated_occurrence(family, monkeypatch):
    """Family 路由重试保留原请求并使用独立去重标识"""
    monkeypatch.setattr(family_facade, "family_members", lambda _record: list(family["members"]))
    root = _send(family, "请处理原始任务而不是 continue")
    facade, repository = _record(family), resident_conversation_stream.get_repository()
    original = lineage_message_events.arrival_details(repository, family["facade"], root, family["steward"], facade)
    first_event = lineage_message_events.arrival_event("source", family["facade"], original)
    family_recovery.routing_failed(first_event.to_dict(), "Router 额度不足")
    assert repository.input_state(family["facade"], root)["state"] == "failed"
    command = _send(family, "continue")
    details = lineage_message_events.arrival_details(repository, family["facade"], command, family["steward"], facade)
    assert details["message_id"] == root and details["author_id"] == "sample_admin"
    retry_event = lineage_message_events.arrival_event("source", family["facade"], details)
    assert retry_event.id != first_event.id
    assert retry_event.payload["message_id"] == first_event.payload["message_id"]
    assert lineage_message_events.arrival_details(repository, family["facade"], root, family["steward"], facade) is None
    assert _view(family, command)["status"] == "routing"
    duplicate = _send(family, "continue")
    assert lineage_message_events.arrival_details(repository, family["facade"], duplicate, family["steward"], facade) is None
    assert lineage_message_events.arrival_event("source", family["facade"], details).id == retry_event.id
    # An old failure cannot cancel the retry now underway.
    family_recovery.routing_failed(first_event.to_dict(), "旧失败")
    assert repository.input_state(family["facade"], root)["state"] == "queued"
    member_message, _ = _round(family["wrong"], state="failed", text="原始任务")
    family_facade.complete_relay((family["facade"], root), family["wrong"], member_message)
    assert repository.input_state(family["facade"], command)["state"] == "completed"
    projected = family_facade.project_messages(facade, repository.read_messages(family["facade"]), input_states={})
    shown = next(item for item in projected if item.get("message_id") == command)
    assert shown["relay"]["original_message_id"] == root
    assert shown["relay"]["resident_id"] == family["wrong"]


def test_own_steward_can_resume_a_quoted_failed_route_without_replacing_the_request(family):
    """Steward 的定向恢复沿原消息重试，保留作者、正文和幂等路由。"""
    facade, repository = _record(family), resident_conversation_stream.get_repository()
    set_role(family["steward"], family_id=facade["family"]["family_id"], role="steward")
    root = _send(family, "三平台适配的完整原请求")
    before = repository.message(family["facade"], root)
    repository.set_input_state(family["facade"], root, "failed", error={"message": "指令未分类"})

    command = _send(family, "continue", quote=root, actor=family["steward"])
    assert repository.input_state(family["facade"], root)["state"] == "queued"
    assert repository.message(family["facade"], root) == before
    assert _view(family, command)["original_message_id"] == root
    assert _view(family, command)["status"] == "routing"
    assert family_recovery.arrival(_record(family), command) == root
    assert family_recovery.arrival(_record(family), root) is None
    family_recovery.handle(_record(family), command)
    assert _view(family, command)["status"] == "routing"


@pytest.mark.parametrize("relayed", [False, True])
def test_quoted_continue_does_not_revive_a_withdrawn_request(family, relayed):
    """撤回原请求或成员副本后，Steward 定向 continue 只能观察取消结果。"""
    facade, repository = _record(family), resident_conversation_stream.get_repository()
    set_role(family["steward"], family_id=facade["family"]["family_id"], role="steward")
    root = _send(family, "已撤回的原始任务")
    if relayed:
        target = family["wrong"]
        target_message, _ = _round(target, state="cancelled", text="已撤回的原始任务")
        family_facade.complete_relay((family["facade"], root), target, target_message)
    else:
        target, target_message = family["facade"], root
        repository.set_input_state(target, target_message, "cancelled")
    before = repository.message(family["facade"], root)

    command = _send(family, "continue", quote=root, actor=family["steward"])
    family_recovery.handle(_record(family), command)

    assert repository.input_state(target, target_message)["state"] == "cancelled"
    assert repository.message(family["facade"], root) == before
    assert repository.input_state(family["facade"], command)["state"] == "completed"
    assert _view(family, command)["status"] == "cancelled"
    assert family_recovery.arrival(_record(family), command) is None


@pytest.mark.parametrize("cancel_before_intent", [False, True])
def test_saved_retry_intent_does_not_route_a_withdrawn_original(family, monkeypatch, cancel_before_intent):
    """恢复意图中断后，原请求取消优先于持久意图，重放不派发路由。"""
    from management_portal.society_service.state_store import get_lineage_data_store

    root = _send(family, "恢复途中撤回的原请求")
    facade, repository = _record(family), resident_conversation_stream.get_repository()
    if cancel_before_intent:
        repository.set_input_state(family["facade"], root, "cancelled")
    else:
        repository.set_input_state(family["facade"], root, "failed")
    with monkeypatch.context() as interrupted:
        interrupted.setattr(family_recovery, "handle", lambda *_a: True)
        command = _send(family, "continue", quote=root)
    with get_lineage_data_store().family_relays.transaction(facade["lineage_id"], family["facade"]) as state:
        state["recoveries"][command].update(
            status="routing", route_occurrence=command, request_message_id=root,
            requested_at=repository.input_state(family["facade"], command)["updated_at"],
        )
    if not cancel_before_intent:
        repository.reopen_input(family["facade"], root)
        repository.set_input_state(family["facade"], root, "cancelled")

    assert repository.input_state(family["facade"], root)["state"] == "cancelled"
    assert family_recovery.arrival(facade, command) is None
    family_recovery.handle(facade, command)
    assert repository.input_state(family["facade"], root)["state"] == "cancelled"
    assert repository.input_state(family["facade"], command)["state"] == "completed"
    assert _view(family, command)["status"] == "cancelled"


@pytest.mark.parametrize("text,quoted", [("continue", False), ("内部进度汇报", True)])
def test_steward_messages_are_admitted_without_reopening_an_unselected_request(family, text, quoted):
    facade = _record(family)
    set_role(family["steward"], family_id=facade["family"]["family_id"], role="steward")
    root = _send(family, "原业务请求")
    repository = resident_conversation_stream.get_repository()
    repository.set_input_state(family["facade"], root, "failed")
    result = run("DeliverMessage", {"resident_id": family["facade"], "message": {
        "content_parts": [{"type": "text", "text": text}],
        **({"quote": {"message_id": root}} if quoted else {}),
    }}, actor=family["steward"])
    accepted = result.raise_for_error()
    assert repository.message(family["facade"], accepted["message_id"])["author_id"] == family["steward"]
    assert repository.input_state(family["facade"], root)["state"] == "failed"


def test_ambiguous_failures_require_a_quote_and_retry_failure_is_visible(family):
    """Family 多个失败请求要求引用选择并显示恢复失败"""
    repository = resident_conversation_stream.get_repository()
    first = _send(family, "第一个任务")
    second = _send(family, "第二个任务")
    for mid in [first, second]:
        repository.set_input_state(family["facade"], mid, "failed")
    command = _send(family, "continue")
    assert _view(family, command)["status"] == "choose"
    assert {item["message_id"] for item in _view(family, command)["candidates"]} == {first, second}
    chosen = _send(family, "continue", quote=first)
    assert _view(family, chosen)["original_message_id"] == first
    assert repository.input_state(family["facade"], second)["state"] == "failed"
    event = {"payload": {"resident_id": family["facade"], "message_id": first},
             "occurred_at": (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()}
    family_recovery.routing_failed(event, "投递步骤失败")
    assert _view(family, chosen)["status"] == "failed"
    assert _view(family, chosen)["error"]["message"] == "投递步骤失败"
    assert repository.input_state(family["facade"], chosen)["state"] == "failed"


def test_unavailable_member_checkpoint_reports_error_without_routing_continue(family):
    """Family 缺失原输入检查点时显示恢复失败"""
    from management_portal.society_service import resident_input_recovery

    root = _send(family, "已过期的执行")
    member_message, _ = _round(family["wrong"], state="failed", text="已过期的执行")
    family_facade.complete_relay((family["facade"], root), family["wrong"], member_message)
    # Checkpoints now belong to mailbox progress, not the execution-work snapshot.
    resident_input_recovery._forget(_find_resident(family["wrong"]), member_message, {}, "sample_admin")
    command = _send(family, "continue", quote=root)
    status = _view(family, command)
    assert status["status"] == "failed" and "检查点" in status["error"]
    assert family_recovery.arrival(_record(family), command) is None


def test_only_family_routing_failures_close_the_request_including_historical_paths(family, monkeypatch):
    """Family 只根据自身路由步骤的失败恢复原请求"""
    from management_portal.society_service import lineage_failure_notifications, trigger_paths

    monkeypatch.setattr(lineage_failure_notifications, "notify", lambda *_a, **_k: None)
    root = _send(family, "带其他监听节点的请求")
    facade, repository = _record(family), resident_conversation_stream.get_repository()
    set_role(family["steward"], family_id=facade["family"]["family_id"], role="router")

    def fail(node, occurrence):
        event = {"id": occurrence, "type": "resident.message_received", "occurred_at": trigger_paths.now(),
                 "payload": {"resident_id": family["facade"], "message_id": root}}
        path = trigger_paths.begin_event(facade["lineage_id"], node, event, cause_kind="input", cause_id=root)
        trigger_paths.event_settled(path, node, occurrence, error="执行失败")
        return path

    fail("unrelated-watcher", "event_unrelated")
    assert repository.input_state(family["facade"], root)["state"] == "queued"
    assert family_recovery.request_status(facade, root)["state"] == "routing"
    # Old failures predate the new notification hook; the path itself is evidence.
    with monkeypatch.context() as old_version:
        old_version.setattr(family_recovery, "routing_failed", lambda *_a, **_k: None)
        path = fail(family["steward"], "event_router")
    assert repository.input_state(family["facade"], root)["state"] == "queued"
    assert family_recovery.request_status(facade, root)["state"] == "failed"
    trigger_paths._notify_failed_path(path)
    assert repository.input_state(family["facade"], root)["state"] == "failed"


def test_saved_routing_intent_finishes_after_crash_but_does_not_revive_cancelled_control(family, monkeypatch):
    """Family 恢复意图跨进程中断完成且尊重取消"""
    from management_portal.society_service.state_store import get_lineage_data_store

    root = _send(family, "进程中断前的原请求")
    facade, repository = _record(family), resident_conversation_stream.get_repository()
    repository.set_input_state(family["facade"], root, "failed")
    with monkeypatch.context() as interrupted:
        interrupted.setattr(family_recovery, "handle", lambda *_a: True)
        command = _send(family, "continue", quote=root)
    with get_lineage_data_store().family_relays.transaction(facade["lineage_id"], family["facade"]) as state:
        state["recoveries"][command].update(status="routing", route_occurrence=command, request_message_id=root,
                                           requested_at=repository.input_state(family["facade"], command)["updated_at"])
    family_recovery.handle(facade, command)
    assert repository.input_state(family["facade"], root)["state"] == "queued"
    # A failed retry must stay failed on redelivery, and a cancelled control stays cancelled.
    repository.set_input_state(family["facade"], root, "failed")
    family_recovery.handle(facade, command)
    assert repository.input_state(family["facade"], root)["state"] == "failed"
    repository.set_input_state(family["facade"], command, "cancelled")
    family_recovery.relay_completed(facade, root)
    assert repository.input_state(family["facade"], command)["state"] == "cancelled"
    assert family_recovery.arrival(facade, command) is None


def test_completed_retry_is_observed_and_other_users_cannot_resume_the_request(family, monkeypatch):
    """Family 已完成请求不重复启动且恢复验证请求者"""
    from management_portal.society_service import resident_directory_domain

    repository = resident_conversation_stream.get_repository()
    root = _send(family, "仅请求者和门面 owner 可恢复")
    member_message, _ = _round(family["wrong"], state="failed")
    family_facade.complete_relay((family["facade"], root), family["wrong"], member_message)
    monkeypatch.setattr(resident_directory_domain, "_can_manage", lambda *_a: False)
    denied = _send(family, "continue", quote=root, actor="CASE-A")
    assert _view(family, denied)["status"] == "unavailable"
    assert repository.input_state(family["wrong"], member_message)["state"] == "failed"
    _send(family, "continue", quote=root)
    repository.set_input_state(family["wrong"], member_message, "completed")
    command = _send(family, "continue")
    assert _view(family, command)["original_message_id"] == root
    assert _view(family, command)["status"] == "completed"
