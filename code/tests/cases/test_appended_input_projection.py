from tests.cases.agent_data_test_support import session_transcript_ids
from copy import deepcopy

import pytest

from management_portal.execution.agent_data_store import AgentDataStore, chat_segment_id
from management_portal.execution.appended_input_projection import materialize_delivered_input
from management_portal.execution.contracts import ExecutionInputControl


@pytest.fixture
def delivered_input(tmp_path):
    store = AgentDataStore()
    agent_id = "resident_111111111111"
    execution_id = "execution-" + "a" * 24
    turn_id = "01a00000-0000-7000-8000-000000000111"
    session = store.ensure_provider_session(
        provider="codex", backend_route_id="zero", backend_session_id="thread-appended",
        agent_id=agent_id, actor_ref="sample_admin",
    )
    store.begin_execution(execution_id=execution_id, agent_id=agent_id, actor_ref="sample_admin",
                          provider="codex", started_at="2026-09-11T00:00:00Z")
    store.bind_execution_provider_session(execution_id, provider_session_ref=session["provider_session_ref"])
    store.record_input_acceptance(execution_id, agent_id=agent_id, provider_turn_id=turn_id)
    initial = store.put_segment(segment_type="input", content="initial Provider input", execution_id=execution_id,
                                metadata={"role": "user"}, created_by_agent_id=agent_id)
    store.append_agent_segments(agent_id, [initial], actor_ref='sample_admin')
    message_id = "msg_" + "b" * 32
    store.display_chat_message(agent_id, {
        "message_id": message_id, "role": "user", "author_id": "sample_admin",
        "content_parts": [{"type": "text", "text": "display only"}], "created_at": "2026-09-11T00:00:01Z",
    })
    source = store.chat_message_provenance(agent_id, message_id)["segment_id"]
    control = ExecutionInputControl.new(
        execution_id=execution_id, actor="sample_admin", expected_turn_id=turn_id,
        source_segment_id=source, input=[{"type": "text", "text": "actual delivered Provider input"}],
    )
    receipt = {**control.to_payload(), "status": "delivered", "updated_at": "2026-09-11T00:00:02Z"}
    return store, agent_id, message_id, session, receipt


def test_delivered_input_projection_is_idempotent_and_keeps_provider_content(delivered_input):
    """已送达的追加输入可重复物化，上下文保存实际发给 Provider 的内容，不含仅显示的文本"""
    store, agent_id, message_id, session, receipt = delivered_input
    before = store.read_sequence(store.trace_sequence_id(agent_id))["segments"]
    first = materialize_delivered_input(store, receipt)
    assert materialize_delivered_input(store, receipt) == first
    trace = store.read_sequence(store.trace_sequence_id(agent_id))["segments"]
    assert len(trace) == len(before)
    assert trace[:-1] == before[:-1]
    assert trace[-1]["segment_id"] == first["segment_id"]
    assert store.chat_message_provenance(agent_id, message_id)["provider_turn_id"] == receipt["expected_turn_id"]
    context = store.provider_context_snapshot(provider="codex", backend_route_id="zero",
                                              backend_session_id=session["backend_session_id"], agent_id=agent_id)
    assert context["segments"][-1]["content"] == "actual delivered Provider input"
    assert "display only" not in [item["content"] for item in context["segments"]]


@pytest.mark.parametrize("status", ["pending", "failed"])
def test_unconfirmed_input_does_not_create_context(delivered_input, status):
    """未确认送达（pending 或 failed）的追加输入不写入 trace 和上下文"""
    store, agent_id, message_id, _, receipt = delivered_input
    receipt["status"] = status
    before = store.read_sequence(store.trace_sequence_id(agent_id))
    with pytest.raises(ValueError, match="confirmed Provider delivery"):
        materialize_delivered_input(store, receipt)
    assert store.read_sequence(store.trace_sequence_id(agent_id)) == before
    assert not store.chat_message_provenance(agent_id, message_id)["provider_turn_id"]


def test_input_projection_rejects_mismatched_turn_and_cross_agent_source(delivered_input):
    """追加输入的轮次不符或来源片段属于其他 Agent 时拒绝物化"""
    store, agent_id, _, _, receipt = delivered_input
    before = store.read_sequence(store.trace_sequence_id(agent_id))
    changed = {**receipt, "expected_turn_id": "another-turn"}
    with pytest.raises(ValueError, match="does not match"):
        materialize_delivered_input(store, changed)
    changed = deepcopy(receipt)
    changed.pop("source_segment_id")
    other = store.put_segment(segment_type="input", content="private other input", created_by_agent_id="resident_other")
    with pytest.raises(ValueError, match="outside the Agent Trace"):
        materialize_delivered_input(store, changed, source_segment_id=other)
    assert store.read_sequence(store.trace_sequence_id(agent_id)) == before


def test_input_origin_survives_contract_round_trip(delivered_input):
    """ExecutionInputControl 序列化往返保留来源片段，缺少 schema 时拒绝解析"""
    _, _, _, _, receipt = delivered_input
    assert ExecutionInputControl.from_payload(receipt).to_payload()["source_segment_id"] == receipt["source_segment_id"]
    without_source = dict(receipt)
    without_source.pop("source_segment_id")
    assert "source_segment_id" not in ExecutionInputControl.from_payload(without_source).to_payload()
    without_schema = dict(receipt)
    without_schema.pop("schema")
    with pytest.raises(ValueError, match="unsupported execution input control schema"):
        ExecutionInputControl.from_payload(without_schema)


def test_historical_projection_does_not_restore_compacted_input(delivered_input):
    """会话压缩后补记的追加输入插在压缩之前，不回到当前上下文"""
    store, agent_id, _, session, receipt = delivered_input
    # A later turn of the session compacted it.
    store.begin_execution(execution_id="execution-later-turn", agent_id=agent_id, actor_ref="sample_admin",
                          provider="codex", started_at="2026-09-11T01:00:00Z")
    store.bind_execution_provider_session("execution-later-turn", provider_session_ref=session["provider_session_ref"])
    compaction = store.put_segment(
        segment_type="compaction", content="later compacted context", created_by_agent_id=agent_id,
        execution_id="execution-later-turn",
    )
    store.append_agent_segments(agent_id, [compaction], actor_ref='sample_admin')
    projected = materialize_delivered_input(store, receipt)
    assert store.session_context_ids(session["provider_session_ref"]) == [compaction]
    # The input goes into the trace right after the message it was sent as,
    # before the later compaction.
    trace = store.sequence_segment_ids(store.trace_sequence_id(agent_id))
    assert trace[-2:] == [projected["segment_id"], compaction]
    assert session_transcript_ids(store, session["provider_session_ref"])[-2:] == [projected["segment_id"], compaction]
    snapshot = store.provider_context_snapshot(
        provider="codex", backend_route_id="zero", backend_session_id=session["backend_session_id"],
        agent_id=agent_id, through_turn_id=receipt["expected_turn_id"],
    )
    assert snapshot["segments"][-1]["segment_id"] == projected["segment_id"]
    assert compaction not in [item["segment_id"] for item in snapshot["segments"]]


def test_delivery_requires_the_canonical_input_before_provider_dispatch(delivered_input):
    """来源消息不在 Agent trace 中时拒绝物化追加输入"""
    store, agent_id, _, _, receipt = delivered_input
    missing = chat_segment_id(agent_id, "msg_" + "d" * 32)
    with pytest.raises(ValueError, match="outside the Agent Trace"):
        materialize_delivered_input(store, {**receipt, "source_segment_id": missing})


def test_append_freezes_image_bytes_and_replay_uses_the_current_occurrence(delivered_input, tmp_path):
    """追加输入中的图片冻结为 base64，原文件删除后重放仍得到同一结果"""
    store, agent_id, _, _, receipt = delivered_input
    image = tmp_path / 'input.png'
    image.write_bytes(b'fixed image bytes')
    receipt = deepcopy(receipt)
    receipt['input'].append({'type': 'localImage', 'path': str(image)})
    result = materialize_delivered_input(store, receipt)
    current = store.segment(result['segment_id'])
    assert current['content']['content'][1]['image_url'].startswith('data:image/png;base64,')
    image.unlink()
    assert materialize_delivered_input(store, receipt, source_segment_id=result['segment_id']) == result
