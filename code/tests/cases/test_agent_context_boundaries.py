"""Fork context and provider-reset boundaries remain independent of source history.

:module: spec.design-governance
:aspects: functional
"""
import pytest

from management_portal.execution.agent_data_store import get_agent_data_store
from management_portal.society_service import resident_execution_domain as delivery, resident_execution_provider


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


@pytest.mark.parametrize('second_reset', ['fresh', 'clear'])
def test_fork_delivery_uses_current_reset_window_on_same_id_requeue(monkeypatch, second_reset):
    """同 ID 重送只读取当前有效窗口，历史截点仍保留原有 Fork 前缀。"""
    store = get_agent_data_store()
    parent, child = 'resident_159357159357', 'resident_258456258456'
    record = {'resident_id': child, 'source_resident_id': parent, 'backend': 'codex'}
    inherited = store.put_segment(segment_type='model', content='parent prefix', created_by_agent_id=parent)
    store.append_agent_segments(parent, [inherited], actor_ref='test')
    store.fork_agent(source_agent_id=parent, target_agent_id=child, segment_ids=[inherited], actor_ref='test')

    def record_input(message_id):
        store.display_chat_message(child, {
            'message_id': message_id, 'role': 'user', 'content_parts': [{'type': 'text', 'text': message_id}],
        })

    monkeypatch.setattr(delivery, '_prepare_resident_prompt',
                        lambda record, job: {**job, 'actual_sent_prompt': job['user_message_id'], 'prompt_builder_version': 1})
    submitted = []

    def send(record, job, *, persist_messages):
        submitted.append(job)
        return {}, {}

    monkeypatch.setattr(delivery, '_send_resident_execution', send)

    def send_input(message_id):
        delivery._deliver_resident_session(resident_execution_provider.DeliveryRequest(
            lineage_id='lineage_123123123123', resident_id=child, actor_id='test', message=message_id,
            resident=record, delivery={'user_message_id': message_id}, persist_messages=True,
        ))
        assert submitted[-1]['actual_sent_prompt'] == message_id
        return submitted[-1]

    original = 'original-failed-input'
    record_input(original)
    historical = store.agent_context_snapshot(child, before_message_id=original)
    assert [item['segment_id'] for item in historical['segments']] == [inherited]
    assert [item['segment_id'] for item in send_input(original)['fork_context_input']['segments']] == [inherited]

    store.reset_provider_context(child, reset_at='2026-11-19T09:00:00Z', actor_ref='test')
    assert 'fork_context_input' not in send_input(original)
    first = 'first-new-input'
    record_input(first)
    assert 'fork_context_input' not in send_input(first)

    new_context = store.put_segment(segment_type='model', content='after first reset', created_by_agent_id=child)
    store.append_agent_segments(child, [new_context], actor_ref='test')
    following = 'later-input'
    record_input(following)
    record_input('future-input')
    seed = send_input(following)['fork_context_input']
    assert new_context in {item['segment_id'] for item in seed['segments']}
    assert 'parent prefix' not in seed['portable_prompt'] and 'future-input' not in seed['portable_prompt']
    assert seed['portable_prompt'].count(following) == 1

    if second_reset == 'fresh':
        store.reset_provider_context(child, reset_at='2026-11-19T09:30:00Z', actor_ref='test')
    else:
        store.record_context_reset(child, reset_at='2026-11-19T09:30:00Z', actor_ref='test',
                                   trace_position=store.trace_length(child))
    assert 'fork_context_input' not in send_input(following)
    assert 'fork_context_input' not in send_input(original)
    assert (store.chat_message(child, original) is not None) == (second_reset == 'fresh')
    assert store.agent_context_snapshot(child, before_message_id=original) == historical
