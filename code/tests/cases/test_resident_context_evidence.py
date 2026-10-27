"""
:module: resident.conversation
:importance: P1
:design: decision_2fe6fd5367fb
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.execution.codex_history_delivery import CodexHistoryDelivery
from management_portal.society_service import resident_context_evidence as evidence


@pytest.mark.parametrize("encoded", [False, True])
def test_authorized_context_exposes_only_original_self_state_read(encoded, monkeypatch):
    """原始工具摘要只披露当前成员的自身状态查询，保持调用和内容摘要关联。"""
    from management_portal.society_service import execution_client, resident_workspace_domain
    from management_portal.society_service import resident_conversation_projection_domain

    store, agent = AgentDataStore(), "resident_self_state_evidence"
    arguments = {"instruction": "ListResidentStates", "arguments": {
        "resident_ids": [agent], "private-extra": "private-argument-value"}}
    segment_id = store.put_segment(segment_type="tool", content_type="application/json",
        content={"call_id": "native-self-read", "tool_name": "hatg_instruction",
                 "input": {"arguments": json.dumps(arguments) if encoded else arguments},
                 "result": {"output": "private-output"}})
    store.append_agent_segments(agent, [segment_id], actor_ref="test-actor")
    original = store.segment(segment_id)
    monkeypatch.setattr(resident_conversation_projection_domain, "can_view_actual_sent_prompts",
                        lambda actor, target: actor == "reader" and target == agent)
    monkeypatch.setattr(resident_workspace_domain, "resolve_resident_runtime_target",
                        lambda *a, **kw: SimpleNamespace(resident_id=agent))
    monkeypatch.setattr(execution_client, "context_evidence_snapshot", store.context_evidence_snapshot)

    result = evidence.read_context_evidence(agent, "reader")
    row = result["effective_context"]["tool_observations"]["entries"][0]
    assert row["call_id"] == "native-self-read"
    assert row["segment_id"] == segment_id
    assert row["content_sha256"] == original["content_sha256"]
    assert row["observed_instruction"] == {"instruction": "ListResidentStates",
                                          "arguments": {"resident_ids": [agent]}}
    assert "private-" not in json.dumps(result)
    assert store.segment(segment_id) == original


@pytest.mark.parametrize("tool_name,arguments", [
    ("functions.exec", {"instruction": "ListResidentStates", "arguments": {"resident_ids": ["self"]}}),
    ("hatg_instruction", {"instruction": "GetResident", "arguments": {"resident_id": "self"}}),
    ("hatg_instruction", {"instruction": "ListResidentStates", "arguments": {"resident_ids": ["other"]}}),
    ("hatg_instruction", {"instruction": "ListResidentStates", "arguments": {"resident_ids": ["self", "other"]}}),
    ("hatg_instruction", {"instruction": "ListResidentStates", "arguments": {"resident_ids": "self"}}),
    ("hatg_instruction", {"instruction": "ListResidentStates", "arguments": None}),
    ("hatg_instruction", 'not JSON: ListResidentStates(self)'),
    ("hatg_instruction", '{"instruction":"Other","instruction":"ListResidentStates","arguments":{"resident_ids":["self"]}}'),
    ("hatg_instruction", '[' * 2000),
    ("hatg_instruction", 'x' * 65537),
])
def test_context_does_not_infer_or_leak_unrecognized_tool_arguments(tool_name, arguments):
    content = {"call_id": "call-original", "tool_name": tool_name,
               "input": {"arguments": arguments}, "result": {"output": "private-output"}}
    row = evidence._tool_observations([{"segment_type": "tool", "content": content}], "self")["entries"][0]
    assert "observed_instruction" not in row
    assert row["call_id"] == "call-original"
    assert "private-output" not in json.dumps(row)


def test_context_evidence_distinguishes_audit_from_compacted_delivery_without_leaking_content():
    """上下文证据区分完整轨迹与压缩窗口并校验原生导入

    真实数据库保存图片、工具调用、压缩和排队输入；读取当前有效窗口和冻结导入摘要，不返回原文与图片。

    :design-point:
    """
    store = AgentDataStore()
    agent = "resident_context_evidence"
    image = "data:image/png;base64," + "A" * 1_176_028
    replacement = [
        {"type": "compaction", "encrypted_content": "opaque-private-compaction"},
        {"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": "private-source-prompt"},
            {"type": "input_image", "image_url": image},
        ]},
        {"type": "function_call", "call_id": "call-1", "name": "read", "arguments": "private-arguments"},
        {"type": "function_call_output", "call_id": "call-1", "output": [
            {"type": "input_image", "image_url": "data:image/png;base64,ZmFrZQ=="},
        ]},
    ]
    old = store.put_segment(segment_type="input", content="superseded-private-input")
    compact = store.put_segment(segment_type="compaction", content_type="application/json",
                                content={"replacement_history": replacement})
    tail = store.put_segment(segment_type="model", content="private-tail")
    store.add_relations([{"relation_type": "compressed_from", "source_segment_id": old,
                         "target_segment_id": compact, "operation_id": "test-compact"}])
    store.append_agent_segments(agent, [old, compact, tail], actor_ref="test-actor")
    store.project_chat_message(agent, {"message_id": "queued-message", "role": "user",
                                      "content_parts": [{"type": "text", "text": "not-yet-delivered"}]})
    sent = []
    CodexHistoryDelivery(store=store, references=[{"segment_id": compact}, {"segment_id": tail}],
        route_id="test", agent_id=agent, actor="test-actor", execution_id="test-delivery",
        send=lambda thread, items: sent.append((thread, items))).initialize("child-thread")
    before = store.trace_length(agent)
    result = evidence.project_context_evidence(store.context_evidence_snapshot(agent))
    assert store.trace_length(agent) == before
    assert result["trace"]["segment_count"] == 4
    effective = result["effective_context"]
    assert effective["segment_count"] == 3
    assert effective["first_segment_id"] == compact
    queued = store.chat_message(agent, "queued-message")["segment_id"]
    assert effective["last_segment_id"] == queued
    assert [ref["segment_id"] for ref in effective["references"]] == [compact, tail, queued]
    assert effective["references_truncated"] is False
    assert effective["compactions"][0]["compressed_from"] == [old]
    assert effective["compactions"][0]["content_sha256"] == store.segment(compact)["content_sha256"]
    replacement_summary = effective["compactions"][0]["replacement_history"]
    assert replacement_summary["content_part_types"]["input_image"] == 2
    assert replacement_summary["image_url_utf8_bytes"] > 1_176_028
    assert replacement_summary["message_text_utf8_bytes"] == len("private-source-prompt")
    assert replacement_summary["tool_mapping"] == [{"call_id": "call-1", "calls": 1, "outputs": 1}]
    assert replacement_summary["unpaired_call_ids"] == 0
    assert {row["state"] for row in result["fork_deliveries"]} == {"requested", "acknowledged"}
    for row in result["fork_deliveries"]:
        assert [ref["segment_id"] for ref in row["references"]] == [compact, tail]
        assert row["matches_recorded_digest"]
        assert row["history"]["sha256"] != effective["codex_history"]["sha256"]
        assert row["history"]["serialized_bytes"] > 1_048_576
    rendered = json.dumps(result)
    for private in [image, "private-source-prompt", "private-arguments", "private-tail",
                    "not-yet-delivered", "superseded-private-input", "opaque-private-compaction"]:
        assert private not in rendered
    assert len(rendered) < 8000


def test_context_evidence_keeps_unknown_import_unacknowledged_and_reports_changed_digest():
    """未知导入结果保持未确认并报告摘要不一致

    导入超时只保留 requested 事实；独立重算冻结历史摘要，不能推断确认或掩盖摘要不一致。

    :design-point:
    """
    store = AgentDataStore()
    agent = "resident_unknown_import"
    segment = store.put_segment(segment_type="input", content="question")
    store.append_agent_segments(agent, [segment], actor_ref="test-actor")
    def unknown(*_args):
        raise TimeoutError("provider accepted or rejected: unknown")
    with pytest.raises(TimeoutError):
        CodexHistoryDelivery(store=store, references=[{"segment_id": segment}], route_id="test",
            agent_id=agent, actor="test-actor", execution_id="test-unknown", send=unknown).initialize("thread")
    snapshot = store.context_evidence_snapshot(agent)
    result = evidence.project_context_evidence(snapshot)
    assert len(result["fork_deliveries"]) == 1
    assert result["fork_deliveries"][0]["state"] == "requested"
    snapshot["imports"][0]["content"]["history_sha256"] = "mismatched"
    assert evidence.project_context_evidence(snapshot)["fork_deliveries"][0]["matches_recorded_digest"] is False


def test_context_evidence_has_explicit_bounds_and_empty_context():
    """上下文证据明确空窗口与导入记录截断

    空轨迹返回零计数；导入事实最多返回二十条并明确是否截断。

    :design-point:
    """
    store = AgentDataStore()
    result = evidence.project_context_evidence(store.context_evidence_snapshot("resident_empty"))
    assert result["trace"]["segment_count"] == result["effective_context"]["segment_count"] == 0
    segment = store.put_segment(segment_type="input", content="bounded")
    agent = "resident_bounded"
    store.append_agent_segments(agent, [segment], actor_ref="test-actor")
    for index in range(11):
        CodexHistoryDelivery(store=store, references=[{"segment_id": segment}], route_id="test",
            agent_id=agent, actor="test-actor", execution_id="bounded", send=lambda *_: None).initialize(str(index))
    snapshot = store.context_evidence_snapshot(agent)
    assert len(snapshot["imports"]) == 20 and snapshot["imports_truncated"]


@pytest.mark.parametrize("include", [False, True])
def test_conversation_reads_context_only_on_explicit_request(monkeypatch, include):
    """对话快照仅在显式请求时读取上下文证据

    默认聊天快照不加载模型上下文；显式诊断保留当前调用者与 Resident 身份。

    :design-point:
    """
    from management_portal.society_service.isa import conversation
    protocol = SimpleNamespace(get_resident_conversation_snapshot=Mock(return_value={"messages": []}))
    monkeypatch.setattr(conversation, "protocol", lambda: protocol)
    read = Mock(return_value={"schema": "evidence"})
    monkeypatch.setattr(evidence, "read_context_evidence", read)
    args = {"resident_id": "resident_test", "include_context_evidence": include}
    result = conversation.get_conversation(SimpleNamespace(caller="resident_caller", argument=args.get))
    if include:
        read.assert_called_once_with("resident_test", "resident_caller")
        assert result["context_evidence"] == {"schema": "evidence"}
    else:
        read.assert_not_called()
        assert "context_evidence" not in result


@pytest.mark.parametrize("deny_prompt", [False, True])
def test_context_evidence_checks_access_before_loading_any_model_content(monkeypatch, deny_prompt):
    """上下文诊断先检查身份及 Resident 可见性

    实际模型输入读取资格或 Resident 可见性失败时，底层模型内容不得被读取。

    :design-point:
    """
    from management_portal.society_service import execution_client, resident_workspace_domain
    from management_portal.society_service import resident_conversation_projection_domain
    monkeypatch.setattr(resident_conversation_projection_domain, "can_view_actual_sent_prompts",
                        lambda *_: not deny_prompt)
    resolve = Mock(side_effect=PermissionError("not visible"))
    monkeypatch.setattr(resident_workspace_domain, "resolve_resident_runtime_target", resolve)
    load = Mock(side_effect=AssertionError("content must not be loaded"))
    monkeypatch.setattr(execution_client, "context_evidence_snapshot", load)
    with pytest.raises(PermissionError):
        evidence.read_context_evidence("resident_other", "resident_caller")
    load.assert_not_called()
    if not deny_prompt:
        resolve.assert_called_once_with("resident_other", "resident_caller", require_visible=True)
