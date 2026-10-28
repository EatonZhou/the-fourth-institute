from __future__ import annotations
from management_portal.society_service import resident_execution_work

import copy
import json
from types import SimpleNamespace
from urllib.parse import urlparse
from unittest.mock import Mock

import pytest

from management_common.provider_interactions import APPROVAL_METHODS, MCP_METHOD
from management_common.provider_turn_artifacts import TurnArtifacts, content_part, review_target
from management_common.provider_user_input import validate_response
from management_portal import codex_capabilities
from management_portal.execution.agent_data_projection import AgentDataProjector
from management_portal.execution.backends import _compact_provider_activity_event
from management_portal.execution.contracts import ExecutionInputControl, ExecutionRequest, SessionOptions
from management_portal.execution.event_projection import append_execution_activity, execution_projection_from_path
from management_portal.execution.session_manager import RuntimeSession
from management_portal.society_service import (
    invariant_locks,
    resident_conversation_stream,
    resident_domain,
    resident_execution_engine_domain,
    resident_protocol,
    resident_record_domain,
)
from tests.cases.test_codex_user_input import native_client
from tests.cases.test_resident_execution_protocol import resident_record


from management_portal.society_service import resident_execution_domain

def approval_params():
    return {"threadId": "thread-native", "turnId": "turn-native", "itemId": "item-native", "startedAtMs": 1,
            "command": "printf hello", "cwd": "/tmp", "permissions": {"network": {"enabled": True}}}


def form_params():
    return {"threadId": "thread-native", "turnId": "turn-native", "serverName": "test-server", "mode": "form", "message": "Choose input",
            "requestedSchema": {"type": "object", "properties": {"count": {"type": "integer", "minimum": 1, "maximum": 3},
                "region": {"type": "string", "enum": ["east", "west"]}, "enabled": {"type": "boolean"}}, "required": ["count", "region"]}}


@pytest.mark.parametrize("method", list(APPROVAL_METHODS))
@pytest.mark.parametrize("action", ["accept", "decline", "cancel"])
def test_native_approval_routes_once_and_never_fabricates_answer(tmp_path, method, action):
    """原生审批请求只按用户回复发送一次，不代为回答，之后再回复视为过期"""
    client, sent, events = native_client(tmp_path)
    client.events.answer_server_request(99, method, approval_params())
    assert sent == []
    assert client.user_inputs.blocking("thread-native")
    request = events[0]["params"]["item"]["user_input"]
    response = {"type": "user_input_response", "request_id": request["request_id"], "interaction_response": True, "action": action}
    control = ExecutionInputControl.new(execution_id="execution-" + "c" * 24, actor="test-actor", input=[response], expected_turn_id="turn-native")
    callback = Mock()
    client.controls.deliver_turn_control(thread_id="thread-native", waiter=client.work.turns["thread-native"],
                                turn_control_provider=lambda: control.to_payload(), turn_control_result_callback=callback,
                                cancellation_requested=None)
    callback.assert_called_once_with(control.to_payload(), True, "")
    assert len(sent) == 1 and sent[0]["id"] == 99
    if method.endswith("permissions/requestApproval"):
        if action == "cancel":
            assert sent[0]["error"]["code"] == -32800
        else:
            assert sent[0]["result"] == {"permissions": approval_params()["permissions"] if action == "accept" else {}, "scope": "turn"}
    else:
        assert sent[0]["result"] == {"decision": action}
    assert events[-1]["params"]["item"]["user_input"]["status"] == {"accept": "answered", "decline": "declined", "cancel": "cancelled"}[action]
    with pytest.raises(ValueError, match="expired"):
        client.user_inputs.respond("thread-native", "turn-native", response)


@pytest.mark.parametrize("action", ["accept", "decline", "cancel"])
def test_mcp_form_correlates_original_wire_request(tmp_path, action):
    """MCP 表单的接受、拒绝或取消回复按原始请求 ID 发回"""
    client, sent, events = native_client(tmp_path)
    client.events.answer_server_request("mcp-wire", MCP_METHOD, form_params())
    assert not sent
    request = events[0]["params"]["item"]["user_input"]
    content = {"count": 2, "region": "west", "enabled": False} if action == "accept" else None
    client.user_inputs.respond("thread-native", "turn-native", {"request_id": request["request_id"], "action": action, "content": content})
    assert sent == [{"id": "mcp-wire", "result": {"action": action, "content": content}}]


@pytest.mark.parametrize("method", ["item/fileChange/requestApproval", MCP_METHOD])
@pytest.mark.parametrize("action", ["accept", "decline", "cancel", "expire"])
@pytest.mark.parametrize("delivery", ["delivered", "queued"])
def test_native_terminal_outcome_survives_request_query(
    tmp_path, resident_record, monkeypatch, method, action, delivery,
):
    """原生拒绝、取消和过期经过正式回答与查询仍保持区别，不重发已完成请求。"""
    from management_common.provider_user_input import content_part as input_part
    from management_portal.society_service import execution_client, resident_access

    resident_id = resident_record["resident_id"]
    repository = resident_conversation_stream.get_repository()
    store = repository.agent_data
    execution_id = "execution-" + "d" * 24
    client, sent, events = native_client(tmp_path)
    client.events.answer_server_request(71, method, form_params() if method == MCP_METHOD else approval_params())
    part = input_part(events[0]["params"]["item"], execution_id)

    def persist(value):
        store.put_segment(
            segment_id=value["segment_id"], segment_type="tool", content=value,
            content_type="application/json", created_by_agent_id=resident_id,
            created_by_actor_ref=resident_id, performed_by_actor_ref=resident_id,
            execution_id=execution_id,
        )

    persist(part)
    message_id = "msg_native_terminal_query"
    repository.append_message(resident_id, {
        "message_id": message_id, "role": "agent", "created_at": "2026-10-24T00:00:00Z",
        "content_parts": [part],
    })
    monkeypatch.setattr(resident_access, "can_manage_resident", lambda *_args, **_kwargs: True)
    assert resident_protocol.get_resident_request(resident_id, "CASE-A", message_id)["status"] == "pending"
    service = Mock()

    def deliver(_execution_id, **kwargs):
        assert _execution_id == execution_id and kwargs["expected_turn_id"] == "turn-native"
        if delivery == "delivered":
            client.user_inputs.respond("thread-native", "turn-native", kwargs["input"][0])
        return {"status": delivery}

    service.append_input.side_effect = deliver
    monkeypatch.setattr(execution_client, "service", lambda: service)
    payload = {"message_id": message_id, "action": action}
    if method == MCP_METHOD and action == "accept":
        payload["content"] = {"count": 2, "region": "west"}
    expected = {"accept": "answered", "decline": "declined", "cancel": "cancelled", "expire": "expired"}[action]
    if action == "expire":
        client.user_inputs.expire("thread-native", turn_id="turn-native")
    else:
        response = resident_protocol.respond_resident_user_input(resident_id, "CASE-A", payload)
        assert response["status"] == (expected if delivery == "delivered" else "pending")
        if delivery == "queued":
            assert not sent
            client.user_inputs.respond("thread-native", "turn-native", service.append_input.call_args.kwargs["input"][0])
    persist(input_part(events[-1]["params"]["item"], execution_id))
    assert resident_protocol.get_resident_request(resident_id, "CASE-A", message_id)["status"] == expected
    assert resident_protocol.list_resident_requests([resident_id], "CASE-A") == []
    assert repository.snapshot(resident_id, history_limit=1)["messages"][0]["content_parts"][0]["status"] == expected
    before = len(sent)
    with pytest.raises(ValueError, match="已回答"):
        resident_protocol.respond_resident_user_input(resident_id, "CASE-A", {"message_id": message_id, "action": "cancel"})
    assert len(sent) == before == (0 if action == "expire" else 1)


@pytest.mark.parametrize("content", [{}, {"count": 5, "region": "east"}, {"count": True, "region": "east"},
                                     {"count": 1, "region": "north"}, {"count": 1, "region": "east", "unexpected": 1}])
def test_mcp_invalid_form_does_not_consume_request(tmp_path, content):
    """不合法的 MCP 表单回复报错，原请求仍等待回答"""
    client, sent, events = native_client(tmp_path)
    client.events.answer_server_request(11, MCP_METHOD, form_params())
    key = events[0]["params"]["item"]["user_input"]["request_id"]
    with pytest.raises(ValueError, match="MCP"):
        client.user_inputs.respond("thread-native", "turn-native", {"request_id": key, "action": "accept", "content": content})
    assert not sent and client.user_inputs.blocking("thread-native")


@pytest.mark.parametrize("url", ["javascript:alert(1)", "http://example.test", "https://user:password@example.test"])
def test_unsafe_mcp_url_is_rejected_without_a_card(tmp_path, url):
    """不安全的 MCP URL 直接拒绝，不生成卡片"""
    client, sent, events = native_client(tmp_path)
    client.events.answer_server_request(3, MCP_METHOD, {**form_params(), "mode": "url", "elicitationId": "test", "url": url})
    assert sent[0]["error"]["code"] == -32602 and not events


def test_mcp_cancelled_expired_and_nonblocking(tmp_path):
    """非阻塞 MCP 表单在服务端已处理后标为过期，不阻塞轮次也不回复"""
    client, sent, events = native_client(tmp_path)
    client.events.answer_server_request(3, MCP_METHOD, {**form_params(), "isBlocking": False})
    assert not client.user_inputs.blocking("thread-native")
    client.events.dispatch_notification("serverRequest/resolved", {"threadId": "thread-native", "requestId": 3})
    assert not sent and events[-1]["params"]["item"]["user_input"]["status"] == "expired"


def test_approval_rejects_extra_grants_and_secret_mcp_forms(tmp_path):
    """审批回复不能追加额外权限，含 writeOnly 机密字段的 MCP 表单被拒绝"""
    client, sent, events = native_client(tmp_path)
    client.events.answer_server_request(3, "item/permissions/requestApproval", approval_params())
    part = events[0]["params"]["item"]["user_input"]
    with pytest.raises(ValueError, match="additional permissions"):
        validate_response({"request_id": part["request_id"], "action": "accept", "content": {"network": True}}, [], part["interaction"])
    params = form_params()
    params["requestedSchema"]["properties"]["credential"] = {"type": "string", "writeOnly": True}
    client.events.answer_server_request(4, MCP_METHOD, params)
    assert sent[-1]["error"]["code"] == -32602


def test_turn_artifacts_are_scoped_latest_snapshots_and_not_git_diff():
    """本轮改动取同一轮最新的 diff 和计划快照，轮次结束后清空缓存"""
    collector = TurnArtifacts()
    base = {"threadId": "thread", "turnId": "turn"}
    collector.observe("turn/plan/updated", {**base, "plan": [{"step": "verify", "status": "inProgress"}]})
    collector.observe("turn/diff/updated", {**base, "diff": "+old"})
    collector.observe("turn/diff/updated", {**base, "diff": "+actual"})
    collector.observe("item/completed", {**base, "item": {"type": "plan", "text": "Native plan text"}})
    final = collector.observe("turn/completed", {"threadId": "thread", "turn": {"id": "turn", "status": "completed"}})
    part = content_part(final["item"], "execution-native")
    assert part["diff"] == "+actual" and part["plan_text"] == "Native plan text"
    assert part["plan"][0]["status"] == "inProgress"
    assert collector.pending == {}
    assert collector.observe("turn/completed", {**base, "turn": {"id": "turn"}}) is None


@pytest.mark.parametrize("updates", [
    [("turn/diff/updated", {"diff": ""})],
    [("turn/diff/updated", {"diff": "+temporary"}), ("turn/diff/updated", {"diff": " \n"})],
    [("turn/plan/updated", {"plan": [], "explanation": " "})],
    [("item/completed", {"item": {"type": "plan", "text": "\n"}})],
])
def test_empty_turn_artifacts_do_not_publish_a_chat_message(updates):
    """本轮 diff 和计划都为空时不发布 Chat 消息"""
    collector = TurnArtifacts()
    base = {"threadId": "thread", "turnId": "turn"}
    for method, params in updates:
        collector.observe(method, {**base, **params})
    assert collector.observe("turn/completed", {**base, "turn": {"id": "turn"}}) is None
    assert collector.pending == {}


def test_empty_diff_does_not_discard_a_real_plan():
    """本轮 diff 为空时仍保留已有的计划"""
    collector = TurnArtifacts()
    base = {"threadId": "thread", "turnId": "turn"}
    collector.observe("turn/plan/updated", {**base, "plan": [{"step": "检查", "status": "completed"}]})
    collector.observe("turn/diff/updated", {**base, "diff": ""})
    result = collector.observe("turn/completed", {**base, "turn": {"id": "turn"}})
    assert result["item"]["artifacts"]["plan"] == [{"step": "检查", "status": "completed"}]


def test_turn_artifact_canonical_chat_and_review_scope(resident_record):
    """本轮改动卡片以原始记录为准，评审只限本 Resident 且拒绝截断的 diff"""
    resident_id = resident_record["resident_id"]
    repository = resident_conversation_stream.get_repository()
    part = content_part({"artifacts": {"thread_id": "thread", "turn_id": "turn", "diff": "+change", "plan": []}}, "execution-" + "d" * 24)
    repository.agent_data.put_operation_record(segment_id=part["segment_id"], segment_type="model", content=part, created_by_agent_id=resident_id,
                                     content_type="application/json", execution_id=part["execution_id"], created_by_actor_ref=resident_id, performed_by_actor_ref=resident_id)
    redacted = {**part, "diff": "redacted"}
    assert repository.execution_content_parts(resident_id, part["execution_id"], [redacted]) == [part]
    message = {"message_id": "msg_native_artifact", "role": "agent", "content_parts": [part]}
    repository.append_message(resident_id, message)
    stored = repository.agent_data.chat_message(resident_id, message["message_id"])
    assert stored["content_parts"][0]["segment_id"] == part["segment_id"] != stored["segment_id"]
    source = repository.agent_data.operation_record(part["segment_id"])
    assert review_target(source, resident_id)["type"] == "custom"
    with pytest.raises(ValueError):
        review_target(source, "resident-other")
    with pytest.raises(ValueError, match="truncated"):
        review_target({**source, "content": {**part, "diff_truncated": True}}, resident_id)


def test_capabilities_are_route_scoped_paginated_and_cached(monkeypatch):
    """codex 模型能力按路由分页读取并缓存，无可用模型时报错"""
    monkeypatch.setattr(codex_capabilities, "_cache", {})
    model = {"model": "model-a", "isDefault": True, "supportedReasoningEfforts": [{"reasoningEffort": "low"}], "defaultReasoningEffort": "low", "serviceTiers": [{"id": "priority"}]}
    client = Mock()
    client.request.side_effect = [{"data": [model], "nextCursor": "next"}, {"data": [{**model, "model": "model-b", "hidden": True}]}]
    first = codex_capabilities.runtime_catalog(client, "route-a")
    assert first["models"] == ["model-a"] and first["efforts_by_model"] == {"model-a": ["low"]}
    first["models"].append("forged")
    assert codex_capabilities.runtime_catalog(client, "route-a")["models"] == ["model-a"]
    assert client.request.call_count == 2
    other = Mock()
    other.request.return_value = {"data": []}
    with pytest.raises(ValueError, match="no available models"):
        codex_capabilities.runtime_catalog(other, "route-b")


def test_turn_artifact_projection_keeps_canonical_snapshot(tmp_path, resident_record, monkeypatch):
    """本轮改动投影保存完整的 diff 和计划快照，重复投影得到同一消息"""
    resident_id = resident_record["resident_id"]
    repository = resident_conversation_stream.get_repository()
    request = ExecutionRequest.new(
        actor="sample_admin", prompt="check", service_type="agent_session", provider="codex", operation="turn",
        agent_id=resident_id, session=SessionOptions(mode="persistent", binding_id=f"resident:{resident_id}"),
    )
    session = RuntimeSession(mode="persistent", binding_id=f"resident:{resident_id}", backend_session_id="thread-native",
                             backend_route_id="zero", is_new_backend_session=False)
    projector = AgentDataProjector(repository.agent_data)
    projector.begin(request, session)
    collector = TurnArtifacts()
    collector.observe("turn/plan/updated", {"threadId": "thread-native", "turnId": "turn-native",
                      "plan": [{"step": "核对", "status": "completed"}, {"step": "审查", "status": "pending"}]})
    collector.observe("turn/diff/updated", {"threadId": "thread-native", "turnId": "turn-native", "diff": "+token=synthetic-diff"})
    event = {"method": "item/completed", "params": collector.observe(
        "turn/completed", {"threadId": "thread-native", "turn": {"id": "turn-native", "status": "completed"}},
    )}
    projector.observe(provider="codex", backend_route_id="zero", backend_session_id="thread-native",
                      execution_id=request.execution_id, event=event)
    compact = _compact_provider_activity_event(event)
    compact["params"]["item"]["artifacts"]["diff"] = "[REDACTED]"
    activity = tmp_path / "native-artifacts.jsonl"
    append_execution_activity(activity, [{"type": "provider_event", "sequence": 1,
                                         "execution_id": request.execution_id, "details": compact}])
    outputs = execution_projection_from_path(activity)["outputs"]
    assert len(outputs) == 1
    resident_execution_work.write(resident_record, {
        "execution_id": request.execution_id, "actor": request.actor, "submitted_at": "2026-10-14T00:00:00Z"})
    messages = resident_execution_engine_domain.persist_execution_display_messages(resident_id,  request.execution_id, outputs)
    canonical = repository.agent_data.operation_record(messages[0]["content_parts"][0]["segment_id"])
    assert canonical["segment_type"] == "model"
    assert messages[0]["content_parts"] == [canonical["content"]]
    assert canonical["content"]["diff"] == "+token=synthetic-diff"
    assert canonical["content"]["plan"][1]["status"] == "pending"
    replay = resident_execution_engine_domain.persist_execution_display_messages(resident_id,  request.execution_id, outputs)
    assert [message["message_id"] for message in replay] == [message["message_id"] for message in messages]


def test_review_execution_resolves_owned_segment_only(resident_record):
    """评审执行只能引用本 Resident 自己的本轮改动记录"""
    resident_id = resident_record["resident_id"]
    repository = resident_conversation_stream.get_repository()
    part = content_part({"artifacts": {"thread_id": "thread", "turn_id": "turn", "diff": "+captured-change"}}, "execution-" + "e" * 24)
    repository.agent_data.put_operation_record(segment_id=part["segment_id"], segment_type="model", content=part,
                                     created_by_agent_id=resident_id, content_type="application/json", execution_id=part["execution_id"],
                                     created_by_actor_ref=resident_id, performed_by_actor_ref=resident_id)
    repository.append_message(resident_id, {"message_id": "msg_review_source", "role": "user", "content_parts": [
        {"type": "turn_review", "segment_id": part["segment_id"]},
    ]})
    job = {"actor": "CASE-A", 'execution_id': "job_native_review", "user_message_id": "msg_review_source", "message": "审查修改"}
    request = resident_execution_domain._build_resident_execution_request(resident_record, job)
    assert request.runtime_options["review_target"] == review_target(repository.agent_data.operation_record(part["segment_id"]), resident_id)
    foreign = {**part, "segment_id": "foreign-review-source"}
    repository.agent_data.put_operation_record(segment_id=foreign["segment_id"], segment_type="model", content=foreign,
                                     created_by_agent_id="resident_bbbbbbbbbbbb", content_type="application/json", execution_id=part["execution_id"],
                                     created_by_actor_ref="resident_bbbbbbbbbbbb", performed_by_actor_ref="resident_bbbbbbbbbbbb")
    repository.append_message(resident_id, {"message_id": "msg_review_foreign", "role": "user", "content_parts": [
        {"type": "turn_review", "segment_id": foreign["segment_id"]},
    ]})
    with pytest.raises(ValueError):
        resident_execution_domain._build_resident_execution_request(resident_record, {**job, "user_message_id": "msg_review_foreign"})


def test_nested_delivery_retains_exact_message_identity(resident_record):
    """嵌套投递 Resident 输入时保留原消息 ID 和激活策略"""
    resident_id = resident_record["resident_id"]
    message_id = "msg_" + "a" * 32
    with invariant_locks.lineage_scope(resident_record["lineage_id"]):
        result = resident_protocol.deliver_resident_input({
            "resident_id": resident_id, "activation_policy": "enqueue_and_trigger",
            "message": {"message_id": message_id, "role": "user", "content_parts": [{"type": "text", "text": "native surface acceptance"}]},
        }, "CASE-A")
        assert result["resident_id"] == resident_id
        assert result["message_id"] == message_id
    job = resident_execution_work.read(resident_record)
    assert job["user_message_id"] == message_id
    assert job["activation_policy"] == "enqueue_and_trigger"


def test_runtime_catalog_uses_authorized_society_service_route(resident_record, monkeypatch):
    """运行模型目录按调用者有权访问的 Resident 路由读取，无权时返回 403"""
    from management_portal import serve_management_portal as portal
    from tests.cases.test_portal_security_repair_wave6 import _ResponseHarness

    resident_id = resident_record["resident_id"]
    assert resident_domain.get_resident_runtime_route(resident_id, "CASE-A") == {"backend": "codex", "account_id": "codex_zero"}
    route = Mock(return_value={"backend": "codex", "account_id": "bound-route"})
    from management_portal.execution.codex_runtime_settings import resolve_codex_runtime_catalog

    # The Codex account adapter resolves the catalog inside the execution
    # service; the Provider-neutral Web endpoint passes it through for the
    # authorized route.
    catalog = Mock(return_value=resolve_codex_runtime_catalog(
        {"source": "model/list", "account_id": "bound-route", "models": ["gpt-6.1-sol"],
         "efforts_by_model": {"gpt-6.1-sol": ["high"]}},
    ))
    monkeypatch.setattr(portal, "resident_api", SimpleNamespace(get_resident_runtime_route=route))
    monkeypatch.setattr(portal, "execution_api", SimpleNamespace(runtime_catalog=catalog))
    monkeypatch.setattr(portal, "_get_session_username", lambda _handler: "test-actor")
    monkeypatch.setattr(resident_record_domain, "resolve_resident_runtime_target", Mock(side_effect=AssertionError("Web cannot access Lineage runtime ports")))
    handler = _ResponseHarness()
    parsed = urlparse(f"/api/agent-workbench/agent-runtime-catalog?resident_id={resident_id}")
    portal._AGENT_WORKBENCH_HTTP.runtime_catalog(handler, parsed, '')
    assert handler.status == 200
    payload = json.loads(handler.wfile.getvalue())
    assert payload["account_id"] == "bound-route"
    assert payload["default_model"] == "gpt-6.1-sol"
    assert payload["default_reasoning_effort"] == "high"
    assert payload["defaults_source"] == "portal"
    assert payload["models"] == ["gpt-6.1-sol"]
    route.assert_called_once_with(resident_id, "test-actor")
    catalog.assert_called_once_with(account_id="bound-route", backend="codex", include_backends=False)
    route.side_effect = PermissionError("not visible")
    denied = _ResponseHarness()
    portal._AGENT_WORKBENCH_HTTP.runtime_catalog(denied, parsed, '')
    assert denied.status == 403
    assert catalog.call_count == 1
