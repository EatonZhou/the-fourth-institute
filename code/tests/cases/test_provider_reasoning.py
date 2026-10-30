from __future__ import annotations
from tests.cases.agent_data_test_support import session_transcript_ids

import json
from tests.cases.database_test_support import connect
from dataclasses import replace
from unittest import mock

import pytest

from management_portal.execution.agent_data_projection import AgentDataProjector
from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.execution.provider_reasoning import provider_reasoning
from management_portal.execution.backend_contracts import BackendContext
from management_portal.execution.codex_backend import CodexBackend
from tests.cases.test_agent_data_projection import _invocation, _runtime_session, _turn_request


def _event(item: dict, turn_id: str = "turn-one") -> dict:
    return {
        "method": "item/completed",
        "params": {"turnId": turn_id, "item": item},
    }


@pytest.mark.parametrize("mode", ["live", "no_event", "cancelled", "recovered"])
def test_codex_reasoning_is_recorded_during_notifications_without_backfill(tmp_path, mode):
    """Codex 思考仅从运行通知录入

    收到运行通知即保存；重复通知幂等，取消保留已完成片段，恢复和无通知时不扫描日志补录。
    """
    path = tmp_path / "rollout.jsonl"
    path.write_text('\n'.join(json.dumps(row) for row in [
        {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "current"}},
        {"type": "response_item", "payload": {"type": "reasoning", "id": "file-only", "summary": ["file-only summary"]}},
    ]) + '\n')
    store = AgentDataStore()
    request = _turn_request("resident_live_reasoning")
    session = _runtime_session(request, "provider-session")
    projector = AgentDataProjector(store)
    projector.begin(request, session)
    route = mock.Mock(account_id="zero", socket_path=tmp_path / "fake.sock")
    registry = mock.Mock()
    registry.get.return_value = route
    registry.find_thread_rollout.return_value = path
    client = mock.Mock()

    def thoughts():
        return [s for s in store.read_sequence(store.trace_sequence_id(request.agent_id))["segments"]
                if s["metadata"].get("kind") == "reasoning"]

    def run_turn(*args, **kwargs):
        kwargs["turn_started_callback"]("current")
        observe = kwargs["event_callback"]
        if mode != "no_event":
            event = _event({"id": "live-item", "type": "reasoning", "summary": ["live summary"]}, "current")
            observe(event)
            assert [s["content"] for s in thoughts()] == [{"summary": ["live summary"]}]
            observe(event)
            observe({**_event({"id": "snapshot-item", "type": "reasoning", "summary": ["snapshot summary"]}, "current"),
                     "origin": "turn_snapshot"})
            assert len(thoughts()) == 1
        if mode == "cancelled":
            raise InterruptedError("cancelled after a completed reasoning item")
        return mock.Mock(thread_id="provider-session", turn_id="current", response="done", token_usage={})

    client.run_turn.side_effect = run_turn
    backend = CodexBackend(account_registry=registry, client_factory=lambda socket: client)
    context = BackendContext(
        emit_event=lambda *args: None, cancellation_requested=lambda: False,
        set_interrupt_target=lambda *args: None,
        provider_event_observer=lambda provider, backend_session_id, backend_route_id, event:
            projector.observe(provider=provider, backend_session_id=backend_session_id,
                              backend_route_id=backend_route_id, execution_id=request.execution_id, event=event),
    )
    invocation = _invocation(request, session)
    if mode == "recovered":
        invocation = replace(invocation, recovery_checkpoint={"turn_id": "current", "provider_recovery_state": {
            "kind": "completed_turn", "turn_id": "current", "response": "done",
        }})
    if mode == "cancelled":
        with pytest.raises(InterruptedError):
            backend.execute(invocation, context)
    else:
        assert backend.execute(invocation, context).output["message"] == "done"
    assert len(thoughts()) == (1 if mode in {"live", "cancelled"} else 0)
    assert "file-only" not in repr(thoughts())
    client.read_turn.assert_not_called()


def test_completed_reasoning_preserves_provider_representation(subtests, isolated_portal_case):
    """已完成的思考条目按 Provider 原表示规范为全文、摘要或加密标记"""
    for item, content, formats in [
    ({"type": "reasoning", "content": ["full text"]},
     {"text": ["full text"]}, ("full",)),
    ({"type": "reasoning", "summary": [{"type": "summary_text", "text": "summary"}]},
     {"summary": ["summary"]}, ("summary",)),
    ({"type": "reasoning", "encrypted_content": "opaque"},
     {"encrypted": True}, ("encrypted",)),
    ({"type": "reasoning", "content": ["text"], "summary": ["summary"], "encrypted_content": "opaque"},
     {"text": ["text"], "summary": ["summary"]},
     ("full", "summary")),
    ({"type": "thinking", "thinking": "thought", "signature": "signature"},
     {"text": ["thought"], "signature": "signature"}, ("full",)),
    ({"type": "redacted_thinking", "data": "opaque"},
     {"encrypted": True}, ("encrypted",)),
]:
        with subtests.test(item=item, content=content, formats=formats), isolated_portal_case() as case_path:
            normalized = provider_reasoning(_event({"id": "item-one", **item}))
            assert normalized is not None
            assert normalized.item_id == "item-one"
            assert normalized.content == content
            assert normalized.formats == formats


def test_streaming_empty_and_non_reasoning_events_are_not_segments(subtests, isolated_portal_case):
    """流式增量、空内容、未完成与非思考事件不生成思考片段"""
    for event in [
    {"method": "item/started", "params": {"item": {"type": "reasoning", "content": ["partial"]}}},
    {"method": "item/reasoning/textDelta", "params": {"delta": "partial"}},
    _event({"type": "reasoning", "summary": [], "content": []}),
    _event({"type": "reasoning", "content": ["partial"], "status": "incomplete"}),
    _event({"type": "agentMessage", "text": "answer"}),
    {"method": "thread/tokenUsage/updated", "params": {"usage": {"reasoningOutputTokens": 100}}},
]:
        with subtests.test(event=event), isolated_portal_case() as case_path:
            assert provider_reasoning(event) is None


def test_completed_response_item_reasoning_is_normalized(subtests, isolated_portal_case):
    """已完成思考事件规范化

    规范化实时 response_item 所提供的思考内容。
    """
    for event in [
    {"type": "response_item", "payload": {"type": "reasoning", "id": "item-one", "content": [{"type": "reasoning_text", "text": "text"}]}},
]:
        with subtests.test(event=event), isolated_portal_case() as case_path:
            normalized = provider_reasoning(event)
            assert normalized is not None
            assert normalized.item_id == "item-one"
            assert normalized.content == {"text": ["text"]}


def test_reasoning_replay_fork_and_final_answer_are_independent_contracts(tmp_path, subtests, isolated_portal_case):
    """思考重放、跨轮身份、Fork 与最终回复相互独立

    重复完成事件沿用 Segment，跨轮相同条目 ID 保持独立，冲突重放被拒绝；Fork 共享引用，取消中的流式增量不写成完成片段。
    """
    with subtests.test(contract='test_reasoning_replay_fork_and_final_answer_are_independent'), isolated_portal_case() as case_path:
        tmp_path = case_path
        store = AgentDataStore()
        request = _turn_request('resident_reasoning_source')
        session = _runtime_session(request, 'session-one')
        events = [_event({'id': 'thought-one', 'type': 'reasoning', 'summary': ['evaluate choices']}), _event({'id': 'thought-two', 'type': 'reasoning', 'content': ['same content']}), _event({'id': 'thought-three', 'type': 'reasoning', 'content': ['same content']})]
        for _attempt in range(2):
            projector = AgentDataProjector(store)
            projector.begin(request, session, created_at='2026-09-05T00:00:00Z')
            for event in events:
                projector.observe(provider='codex', backend_route_id='zero', backend_session_id='session-one', execution_id=request.execution_id, event=event)
        trace = store.read_sequence(store.trace_sequence_id(request.agent_id))['segments']
        thoughts = [segment for segment in trace if segment['metadata'].get('kind') == 'reasoning']
        assert len(thoughts) == 3
        assert {segment['segment_type'] for segment in thoughts} == {'model'}
        assert store.read_chat_messages(request.agent_id) == []
        assert projector._active[request.execution_id].final_segment_id == ''
        final_id = projector.complete(execution_id=request.execution_id, backend_session_id='session-one', backend_route_id='zero', turn_id='turn-one', response='final answer')
        assert store.segment(final_id)['content'] == 'final answer'
        before_ids = store.sequence_segment_ids(store.trace_sequence_id(request.agent_id))
        store.fork_agent(source_agent_id=request.agent_id, target_agent_id='resident_reasoning_child', actor_ref='sample_admin', segment_ids=store.sequence_segment_ids(store.trace_sequence_id(request.agent_id))[:3])
        assert store.sequence_segment_ids(store.trace_sequence_id('resident_reasoning_child')) == before_ids[:3]
        with connect(store) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM segments "
                "WHERE metadata_json::jsonb ->> 'kind' = 'reasoning'"
            ).fetchone()[0] == 3
        for thought in thoughts:
            assert thought['segment_id'] in session_transcript_ids(store, projector._active[request.execution_id].provider_session_ref)
    with subtests.test(contract='test_reasoning_identity_includes_turn_and_rejects_conflicting_replay'), isolated_portal_case() as case_path:
        tmp_path = case_path
        store = AgentDataStore()
        request = _turn_request('resident_reasoning_turns')
        projector = AgentDataProjector(store)
        projector.begin(request, _runtime_session(request, 'session-one'))

        def observe(event):
            projector.observe(provider='codex', backend_route_id='zero', backend_session_id='session-one', execution_id=request.execution_id, event=event)
        for turn in ('turn-one', 'turn-two'):
            observe(_event({'id': 'reused-id', 'type': 'reasoning', 'content': ['text']}, turn))
        assert len(store.sequence_segment_ids(store.trace_sequence_id(request.agent_id))) == 3
        with pytest.raises(ValueError, match='immutable content'):
            observe(_event({'id': 'reused-id', 'type': 'reasoning', 'content': ['changed']}, 'turn-two'))
    with subtests.test(contract='test_aborted_stream_does_not_become_completed_reasoning'), isolated_portal_case() as case_path:
        tmp_path = case_path
        store = AgentDataStore()
        request = _turn_request('resident_partial_reasoning')
        projector = AgentDataProjector(store)
        projector.begin(request, _runtime_session(request, 'session-one'))
        projector.observe(provider='codex', backend_route_id='zero', backend_session_id='session-one', execution_id=request.execution_id, event={'type': 'event_msg', 'payload': {'type': 'agent_reasoning', 'text': 'partial', 'update_id': 'thinking-one', 'replace_previous': True}})
        store.finish_execution(request.execution_id, status='cancelled')
        projector.discard(request.execution_id)
        assert len(store.sequence_segment_ids(store.trace_sequence_id(request.agent_id))) == 1


def test_only_explicit_item_reasoning_tokens_are_kept(subtests, isolated_portal_case):
    """思考条目只保留自身显式给出的非负整数 reasoning_tokens"""
    for usage, expected in [
    ({"reasoning_tokens": 42}, 42),
    ({"usage": {"reasoning_tokens": 0}}, 0),
    ({"usage": {"output_tokens_details": {"reasoning_tokens": 12}}}, 12),
    ({"usage": {"output_tokens": 99, "total_tokens": 120}}, None),
    ({"reasoning_tokens": -1}, None),
    ({"reasoning_tokens": True}, None),
    ({"reasoning_tokens": "12"}, None),
]:
        with subtests.test(usage=usage, expected=expected), isolated_portal_case() as case_path:
            event = _event({"type": "reasoning", "encrypted_content": "never-store-this", **usage})
            event["params"]["usage"] = {"reasoning_tokens": 999}
            normalized = provider_reasoning(event)
            assert normalized.content == {"encrypted": True, **({"reasoning_tokens": expected} if expected is not None else {})}
            assert "never-store-this" not in repr(normalized)


def test_summary_is_readable_but_not_claimed_to_be_raw_reasoning_contracts(subtests, isolated_portal_case):
    """思考摘要与原文区分，显式配置优先

    摘要保留为摘要，加密内容仅记录存在标记；Codex 请求可读思考但尊重调用者显式选项。
    """
    with subtests.test(contract='test_summary_is_readable_but_not_claimed_to_be_raw_reasoning'), isolated_portal_case() as case_path:
        normalized = provider_reasoning(_event({'type': 'reasoning', 'summary': ['provider summary'], 'encrypted_content': 'never-store-this', 'signature': 'not-needed-for-marker'}))
        assert normalized.content == {'summary': ['provider summary'], 'encrypted': True}
        assert normalized.formats == ('summary', 'encrypted')
    with subtests.test(contract='test_codex_requests_readable_reasoning_but_respects_explicit_settings'), isolated_portal_case() as case_path:
        config = CodexBackend._thread_config({})
        assert config['show_raw_agent_reasoning'] is True
        assert config['model_reasoning_summary'] == 'auto'
        config = CodexBackend._thread_config({'config_overrides': {'show_raw_agent_reasoning': False, 'model_reasoning_summary': 'none'}})
        assert config['show_raw_agent_reasoning'] is False
        assert config['model_reasoning_summary'] == 'none'
