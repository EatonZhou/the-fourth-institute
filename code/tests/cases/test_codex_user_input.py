from __future__ import annotations
from management_portal.society_service import resident_execution_work

import copy
import json
import time
from unittest.mock import Mock

import pytest

from management_common.provider_user_input import content_part
from management_portal.codex_app_server_client import CodexAppServerClient
from management_portal.codex_app_server_protocol import TurnWaiter
from management_portal.execution.agent_data_projection import AgentDataProjector
from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.execution.activity_redaction import redact_sensitive_mapping
from management_portal.execution.backends import _compact_provider_activity_event
from management_portal.execution.contracts import ExecutionInputControl, ExecutionRequest, SessionOptions
from management_portal.execution.event_projection import execution_outputs_from_events, append_execution_activity, execution_projection_from_path
from management_portal.execution.session_manager import RuntimeSession
from management_portal.society_service import (
    resident_access,
    resident_protocol,
    resident_conversation_stream,
    execution_client,
    resident_execution_engine_domain,
)
from tests.cases.test_resident_execution_protocol import resident_record



def request_params(blocking=True):
    return {"threadId": "thread-native", "turnId": "turn-native", "itemId": "item-native", "isBlocking": blocking,
            "questions": [{"id": "scope", "header": "范围", "question": "选择范围？", "isSecret": False, "options": [
                {"label": "定向", "description": "只检查相关功能"}, {"label": "全部", "description": "全部检查"}]}]}


def native_client(tmp_path):
    client = CodexAppServerClient(tmp_path / "absent.sock")
    sent, events = [], []
    client.rpc.send = sent.append
    client.user_inputs._send = sent.append
    client.work.turns["thread-native"] = TurnWaiter(thread_id="thread-native", turn_id="turn-native", event_callback=events.append)
    return client, sent, events


def test_goal_accepts_user_input_requests(tmp_path):
    """Goal 接收原生用户提问并保持待回答状态"""
    from management_portal.codex_app_server_protocol import GoalWaiter

    client, sent, events = native_client(tmp_path)
    client.work.turns.clear()
    client.work.goals['thread-native'] = GoalWaiter('thread-native', event_callback=events.append)
    client.events.answer_server_request(41, 'item/tool/requestUserInput', request_params())
    assert sent == []
    assert events[0]['params']['item']['user_input']['status'] == 'pending'


@pytest.mark.parametrize("blocking", [True, False])
def test_native_request_waits_for_real_response(tmp_path, blocking):
    """原生提问等待真实回答并在回答后拒绝重复响应"""
    client, sent, events = native_client(tmp_path)
    client.rpc.dispatch_message({"id": 41, "method": "item/tool/requestUserInput", "params": request_params(blocking)})
    assert sent == []
    assert client.user_inputs.blocking("thread-native") is blocking
    part = events[0]["params"]["item"]["user_input"]
    answer = {"type": "user_input_response", "request_id": part["request_id"], "answers": {"scope": {"answers": ["定向"]}}}
    control = ExecutionInputControl.new(execution_id="execution-" + "1" * 24, actor="sample_admin", input=[answer], expected_turn_id="turn-native")
    callback = Mock()
    client.controls.steer_turn = Mock(side_effect=AssertionError("must not steer ordinary input"))
    client.controls.deliver_turn_control(thread_id="thread-native", waiter=client.work.turns["thread-native"],
                                turn_control_provider=lambda: control.to_payload(), turn_control_result_callback=callback,
                                cancellation_requested=None)
    assert sent == [{"id": 41, "result": {"answers": {"scope": {"answers": ["定向"]}}}}]
    assert events[-1]["params"]["item"]["user_input"]["status"] == "answered"
    callback.assert_called_once_with(control.to_payload(), True, "")
    with pytest.raises(ValueError, match="expired"):
        client.user_inputs.respond("thread-native", "turn-native", answer)


@pytest.mark.parametrize("ending", ["resolved", "completed", "closed", "disconnect", "cancel"])
def test_pending_request_expires_or_cancels_without_empty_answers(tmp_path, ending):
    """原生提问结束或取消后不发送空答案"""
    client, sent, events = native_client(tmp_path)
    client.events.answer_server_request(41, "item/tool/requestUserInput", request_params())
    key = events[0]["params"]["item"]["user_input"]["request_id"]
    if ending == "resolved":
        client.events.dispatch_notification("serverRequest/resolved", {"threadId": "thread-native", "requestId": 41})
    elif ending == "completed":
        client.events.dispatch_notification("turn/completed", {"threadId": "thread-native", "turn": {"id": "turn-native", "status": "completed"}})
    elif ending == "closed":
        client.events.dispatch_notification("thread/closed", {"threadId": "thread-native"})
    elif ending == "disconnect":
        client.disconnect(RuntimeError("lost connection"))
    else:
        client.user_inputs.respond("thread-native", "turn-native", {"request_id": key, "action": "cancel"})
    results = [event["params"]["item"] for event in events if (event["params"].get("item") or {}).get("type") == "userInputResponse"]
    assert len(results) == 1
    assert results[0]["user_input"]["status"] == ("cancelled" if ending == "cancel" else "expired")
    assert all("result" not in envelope for envelope in sent)
    assert not client.user_inputs.blocking("thread-native")


def test_wrong_turn_or_incomplete_answers_cannot_resolve_request(tmp_path):
    """线程不匹配或答案不完整时原生提问保持待回答"""
    client, sent, events = native_client(tmp_path)
    client.events.answer_server_request(41, "item/tool/requestUserInput", request_params())
    key = events[0]["params"]["item"]["user_input"]["request_id"]
    with pytest.raises(ValueError, match="different turn"):
        client.user_inputs.respond("another", "turn-native", {"request_id": key, "answers": {}})
    with pytest.raises(ValueError, match="every question"):
        client.user_inputs.respond("thread-native", "turn-native", {"request_id": key, "answers": {}})
    assert sent == []


def test_native_request_and_result_are_tool_segments_and_display_outputs(tmp_path):
    """原生提问与回答投影为工具 Segment 并支持展示重放"""
    store = AgentDataStore()
    request = ExecutionRequest.new(actor="sample_admin", prompt="check", service_type="agent_session", provider="codex", operation="turn",
                                   agent_id="resident_native", session=SessionOptions(mode="persistent", binding_id="resident:resident_native"))
    session = RuntimeSession(mode="persistent", binding_id="resident:resident_native", backend_session_id="thread-native", backend_route_id="zero", is_new_backend_session=False)
    projector = AgentDataProjector(store)
    projector.begin(request, session)
    client, sent, native_events = native_client(tmp_path)
    client.events.answer_server_request(41, "item/tool/requestUserInput", request_params())
    key = native_events[0]["params"]["item"]["user_input"]["request_id"]
    client.user_inputs.respond("thread-native", "turn-native", {"request_id": key, "answers": {"scope": {"answers": ["定向"]}}})
    projected = []
    for index, event in enumerate(native_events):
        projector.observe(provider="codex", backend_route_id="zero", backend_session_id="thread-native", execution_id=request.execution_id, event=event)
        projected.append({"type": "provider_event", "sequence": index + 1, "execution_id": request.execution_id,
                          "details": _compact_provider_activity_event(event)})
    outputs = execution_outputs_from_events(projected)
    assert len(outputs) == 2
    for output in outputs:
        part = output["content_parts"][0]
        segment = store.segment(part["segment_id"])
        assert segment["segment_type"] == "tool"
        assert segment["content"] == part
        message = store.display_chat_message("resident_native", {"message_id": output["output_id"], "role": "agent",
            "author_id": "resident_native", "created_at": segment["finished_at"], "content_parts": [part]})
        # The message is a Segment of its own; its part names the tool Segment.
        assert message["content_parts"][0]["segment_id"] == part["segment_id"] != message["segment_id"]
    activity = tmp_path / (request.execution_id + ".jsonl")
    append_execution_activity(activity, projected)
    replay = execution_projection_from_path(activity)["outputs"]
    assert [output["content_parts"] for output in replay] == [output["content_parts"] for output in outputs]


def test_native_boolean_schema_metadata_survives_redaction():
    """脱敏保留布尔 isSecret 元数据且隐藏敏感值"""
    for flag in (False, True):
        payload = {"questions": [{"isSecret": flag, "secret": "sensitive-value"}],
                   "password": "sensitive-value", "other": {"isSecret": "sensitive-value"}}
        redacted = redact_sensitive_mapping(payload)
        assert redacted["questions"][0]["isSecret"] is flag
        assert redacted["questions"][0]["secret"] == "[REDACTED]"
        assert redacted["password"] == "[REDACTED]"
        assert redacted["other"]["isSecret"] == "[REDACTED]"
        assert payload["questions"][0]["secret"] == "sensitive-value"


@pytest.mark.parametrize("blocking", [True, False])
@pytest.mark.parametrize("ending", ["answered", "cancelled", "expired"])
def test_native_chat_projects_canonical_segments_from_redacted_activity(
    tmp_path, resident_record, monkeypatch, blocking, ending,
):
    """Chat 从规范 Segment 恢复脱敏活动中的原生提问"""
    resident_id = resident_record["resident_id"]
    repository = resident_conversation_stream.get_repository()
    store = repository.agent_data
    request = ExecutionRequest.new(
        actor="sample_admin", prompt="check", service_type="agent_session", provider="codex", operation="turn",
        agent_id=resident_id, session=SessionOptions(mode="persistent", binding_id=f"resident:{resident_id}"),
    )
    session = RuntimeSession(mode="persistent", binding_id=f"resident:{resident_id}", backend_session_id="thread-native",
                             backend_route_id="zero", is_new_backend_session=False)
    projector = AgentDataProjector(store)
    projector.begin(request, session)
    client, sent, native_events = native_client(tmp_path)
    params = request_params(blocking)
    params["questions"][0]["question"] = "检查 token=synthetic-question-value"
    client.events.answer_server_request(41, "item/tool/requestUserInput", params)
    assert not sent
    assert client.user_inputs.blocking("thread-native") is blocking
    key = native_events[0]["params"]["item"]["user_input"]["request_id"]
    if ending == "expired":
        client.events.dispatch_notification("turn/completed", {
            "threadId": "thread-native", "turn": {"id": "turn-native", "status": "completed"},
        })
    elif ending == "cancelled":
        client.user_inputs.respond("thread-native", "turn-native", {"request_id": key, "action": "cancel"})
    else:
        client.user_inputs.respond("thread-native", "turn-native", {
            "request_id": key, "answers": {"scope": {"answers": ["password=synthetic-answer-value"]}},
        })
        assert sent == [{"id": 41, "result": {"answers": {
            "scope": {"answers": ["password=synthetic-answer-value"]},
        }}}]
    if ending != "answered":
        assert all("result" not in envelope for envelope in sent)
    projected = []
    for index, event in enumerate(native_events):
        projector.observe(provider="codex", backend_route_id="zero", backend_session_id="thread-native",
                          execution_id=request.execution_id, event=event)
        compact = _compact_provider_activity_event(event)
        item = compact["params"].get("item") or {}
        if item.get("type") in {"userInputRequest", "userInputResponse"}:
            assert item["user_input"]["questions"][0]["isSecret"] is False
            item["user_input"]["questions"][0]["isSecret"] = "[REDACTED]"
        projected.append({"type": "provider_event", "sequence": index + 1, "execution_id": request.execution_id,
                          "details": compact})
    assert "synthetic-question-value" not in json.dumps(projected)
    assert "synthetic-answer-value" not in json.dumps(projected)
    activity = tmp_path / (request.execution_id + ".jsonl")
    append_execution_activity(activity, projected)
    outputs = execution_projection_from_path(activity)["outputs"]
    assert len(outputs) == 2
    resident_execution_work.write(resident_record, {
        "execution_id": request.execution_id, "actor": request.actor, "submitted_at": "2026-10-14T00:00:00Z"})
    messages = resident_execution_engine_domain.persist_execution_display_messages(
        resident_id,  request.execution_id, outputs,
    )
    assert len(messages) == 2
    for output, message in zip(outputs, messages):
        projected_part = output["content_parts"][0]
        canonical = store.segment(projected_part["segment_id"])["content"]
        assert projected_part != canonical
        assert message["content_parts"] == [canonical]
        stored = store.chat_message(resident_id, message["message_id"])
        assert stored["content_parts"][0]["segment_id"] == canonical["segment_id"] != stored["segment_id"]
        assert canonical["questions"][0]["isSecret"] is False
    replay = resident_execution_engine_domain.persist_execution_display_messages(
        resident_id,  request.execution_id, outputs,
    )
    assert [message["message_id"] for message in replay] == [message["message_id"] for message in messages]
    visible = repository.tool_status_messages(messages, resident_id=resident_id)
    assert visible[0]["content_parts"][0]["status"] == ending
    assert store.segment(outputs[0]["content_parts"][0]["segment_id"])["content"]["status"] == "pending"
    assert not client.user_inputs.blocking("thread-native")
    with pytest.raises(ValueError, match="expired"):
        client.user_inputs.respond("thread-native", "turn-native", {
            "request_id": key, "answers": {"scope": {"answers": ["late"]}},
        })


@pytest.mark.parametrize("field", ["resident_id", "execution_id", "request_id", "turn_id", "type", "status"])
def test_native_canonical_projection_rejects_mismatched_identity(resident_record, field):
    """原生提问投影拒绝身份不匹配及伪造问题正文"""
    resident_id = resident_record["resident_id"]
    execution_id = "execution-" + "5" * 24
    repository = resident_conversation_stream.get_repository()
    native = {"request_id": "6" * 32, "thread_id": "thread-native", "turn_id": "turn-native", "item_id": "item-native",
              "is_blocking": True, "status": "pending", "questions": request_params()["questions"]}
    canonical = content_part({"type": "userInputRequest", "user_input": native}, execution_id)
    repository.agent_data.put_segment(
        segment_id=canonical["segment_id"], segment_type="tool", content=canonical,
        content_type="application/json", created_by_agent_id=resident_id,
        created_by_actor_ref=resident_id, performed_by_actor_ref=resident_id, execution_id=execution_id,
    )
    part = copy.deepcopy(canonical)
    if field == "resident_id":
        resident_id = "resident_wrong"
    elif field == "execution_id":
        execution_id = "execution-" + "7" * 24
    else:
        part[field] = "different"
    with pytest.raises(ValueError, match="canonical Execution Segment"):
        repository.execution_content_parts(resident_id, execution_id, [part])
    forged = copy.deepcopy(canonical)
    forged["questions"][0]["question"] = "forged"
    with pytest.raises(ValueError, match="own canonical Segment"):
        repository.agent_data.display_chat_message(resident_record["resident_id"], {
            "message_id": "msg_forged", "role": "agent", "author_id": resident_record["resident_id"],
            "created_at": "2026-09-14T01:00:00Z", "content_parts": [forged],
        })


def test_native_response_permission_source_binding_and_bounded_status(resident_record, monkeypatch):
    """原生回答检查管理权限与来源绑定且状态读取有界"""
    resident_id = resident_record["resident_id"]
    repository = resident_conversation_stream.get_repository()
    store = repository.agent_data
    execution_id = "execution-" + "2" * 24
    native = {"request_id": "3" * 32, "thread_id": "thread-native", "turn_id": "turn-native", "item_id": "item-native",
              "is_blocking": True, "status": "pending", "questions": request_params()["questions"]}
    part = content_part({"type": "userInputRequest", "user_input": native}, execution_id)
    store.put_segment(segment_id=part["segment_id"], segment_type="tool", content=part, content_type="application/json", created_by_agent_id=resident_id,
                      created_by_actor_ref=resident_id, performed_by_actor_ref=resident_id, execution_id=execution_id)
    message = {"message_id": "msg_native_permission", "role": "agent", "created_at": "2026-09-13T01:00:00Z", "content_parts": [part]}
    repository.append_message(resident_id, message)
    service = Mock()
    service.append_input.return_value = {"status": "queued", "control_id": "control-" + "3" * 24}
    monkeypatch.setattr(execution_client, "service", lambda: service)
    monkeypatch.setattr(resident_access, "can_manage_resident", lambda _record, actor, **_kwargs: actor == "CASE-A")
    payload = {"message_id": message["message_id"], "answers": {"scope": {"answers": ["定向"]}}}
    with pytest.raises((PermissionError, KeyError)):
        resident_protocol.respond_resident_user_input(resident_id, "non-owner", payload)
    service.append_input.assert_not_called()
    result = resident_protocol.respond_resident_user_input(resident_id, "CASE-A", payload)
    assert result["status"] == "pending"
    sent = service.append_input.call_args
    assert sent.args == (execution_id,)
    assert sent.kwargs["expected_turn_id"] == "turn-native"
    assert sent.kwargs["input"] == [{"type": "user_input_response", "request_id": native["request_id"], "action": "answer", "answers": payload["answers"]}]
    result_part = content_part({"type": "userInputResponse", "user_input": {**native, "status": "answered", "answers": payload["answers"]}}, execution_id)
    store.put_segment(segment_id=result_part["segment_id"], segment_type="tool", content=result_part, content_type="application/json", created_by_agent_id=resident_id,
                      created_by_actor_ref=resident_id, performed_by_actor_ref=resident_id, execution_id=execution_id)
    monkeypatch.setattr(store, "read_chat_messages", Mock(side_effect=AssertionError("must not scan history")))
    snapshot = repository.snapshot(resident_id, history_limit=1)
    assert snapshot["messages"][0]["content_parts"][0]["status"] == "answered"
    with pytest.raises(ValueError, match="已回答"):
        resident_protocol.respond_resident_user_input(resident_id, "CASE-A", payload)
    monkeypatch.setattr(store, "segment", lambda _segment_id: {"created_by_agent_id": "resident_parent", "content": part})
    with pytest.raises(ValueError, match="不属于当前 Resident"):
        resident_protocol.respond_resident_user_input(resident_id, "CASE-A", payload)
    malformed = {"role": "user", "content_parts": [{"type": "tool_call", "tool_name": "request_user_input"}]}
    monkeypatch.setattr(store, "segment", Mock(side_effect=AssertionError("user content cannot query tool requests")))
    assert repository.tool_status_messages([malformed], resident_id=resident_id) == [malformed]


def test_blocking_wait_does_not_consume_resume_reconciliation_timeout(tmp_path, monkeypatch):
    """等待阻塞回答不消耗恢复轮次的核对超时"""
    client, sent, events = native_client(tmp_path)
    started = time.monotonic()
    def transport_request(method, params, **kwargs):
        if method == "thread/resume":
            client.events.answer_server_request(41, "item/tool/requestUserInput", request_params())
        return {}
    monkeypatch.setattr(client.rpc, "request", transport_request)
    monkeypatch.setattr(client.reads, "read_active_turn", lambda *args, **kwargs: {"id": "turn-native", "status": "inProgress"})
    monkeypatch.setattr(client.reads, "read_task_complete_goal", lambda *args: None)
    final = {"id": "turn-native", "status": "completed", "items": [{"id": "final", "type": "agentMessage", "phase": "final_answer", "text": "done"}]}
    monkeypatch.setattr(client.follower, "read_terminal_turn", lambda *args: final)
    def next_control():
        if time.monotonic() - started < .2:
            return None
        key = events[0]["params"]["item"]["user_input"]["request_id"]
        return {"expected_turn_id": "turn-native", "input": [{"type": "user_input_response", "request_id": key, "answers": {"scope": {"answers": ["定向"]}}}]}
    result = client.resume_turn("thread-native", "turn-native", cwd=str(tmp_path), event_callback=events.append,
                                timeout_seconds=.1, turn_control_provider=next_control)
    assert result.response == "done"
    assert time.monotonic() - started >= .2


def test_secret_input_returns_an_error_and_other_workers_requests_stay_unanswered(tmp_path):
    """密码类输入返回错误，其他 Worker 的请求不作答

    运行这一轮的客户端收到密码类用户输入请求时返回错误、不伪造答案；不运行这一轮的客户端不回答这个线程的请求，留给运行它的 Worker。
    """
    client, sent, events = native_client(tmp_path)
    params = request_params()
    params["questions"][0]["isSecret"] = True
    client.events.answer_server_request(41, "item/tool/requestUserInput", params)
    # A request of a turn another Worker runs is that Worker's to answer.
    client.work.turns.clear()
    client.events.answer_server_request(42, "item/tool/requestUserInput", request_params())
    assert len(sent) == 1 and sent[0]["id"] == 41 and "error" in sent[0]
    assert events == []
