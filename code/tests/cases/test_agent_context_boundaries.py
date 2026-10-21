"""Fork context and provider-reset boundaries remain independent of source history.

:module: spec.design-governance
:aspects: functional
"""
from management_portal.execution.agent_data_store import get_agent_data_store


def test_child_context_uses_trace_boundaries_and_receipts_not_source_records():
    """Fork 子代上下文不受源片段改写影响，Provider 重置后上下文为空但消息与片段仍在"""
    store = get_agent_data_store()
    source, child = 'resident_111111bbbbbb', 'resident_222222bbbbbb'
    one = store.put_segment(segment_type='model', content='inherited', created_by_agent_id=source)
    store.append_agent_segments(source,[one],actor_ref='test')
    store.fork_agent(source_agent_id=source,target_agent_id=child,segment_ids=[one],actor_ref='test')
    question = {'message_id':'question', 'role':'user', 'content_parts':[{'type':'text','text':'next'}]}
    store.display_chat_message(child,question)
    assert [s['segment_id'] for s in store.agent_context_snapshot(child,before_message_id='question')['segments']]==[one]
    store.transform_agent_segments(source,[one],segment_id='changed',content='future',actor_ref='test',operation_id='change',operation_type='test')
    assert store.agent_context_snapshot(child,before_message_id='question')['segments'][0]['content']=='inherited'
    store.reset_provider_context(child,reset_at='2026-10-10T00:00:00Z',actor_ref='test')
    assert store.agent_context_snapshot(child)['empty_context']
    assert store.chat_message(child,'question') is not None
    assert store.segment(one)['content']=='inherited'


def test_historical_cutoff_precedes_a_later_fresh_session_reset():
    """按历史轮次截取的上下文不受之后 Provider 会话重置的影响"""
    from tests.cases.live_trace_support import AGENT, _turn
    store, projector, request, _ = _turn('codex')
    turn = 'historical-turn'
    with store.database.transaction() as connection:
        connection.execute('UPDATE executions SET provider_turn_id=%s WHERE execution_id=%s', (turn, request.execution_id))
    before = store.agent_context_snapshot(AGENT, through_turn_id=turn)
    assert before['segments']
    store.reset_provider_context(AGENT, reset_at='2026-10-10T12:00:00Z', actor_ref='test')
    assert store.agent_context_snapshot(AGENT)['empty_context']
    assert store.agent_context_snapshot(AGENT, through_turn_id=turn) == before
