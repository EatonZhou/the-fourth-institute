from __future__ import annotations

import pytest

from management_common.async_questions import question_set
from management_portal.execution.agent_data_projection import AgentDataProjector
from management_portal.execution.agent_data_store import AgentDataStore, stable_segment_id
from management_portal.execution.message_projection import project_message
from management_portal.society_service.resident_conversation_records import message_from_storage
from tests.cases.test_agent_data_projection import _turn_request, _runtime_session, _invocation
from tests.cases.resident_execution_test_support import resident_record


@pytest.mark.parametrize('text', ['', '请选择下一步。'])
def test_native_async_questions_survive_completion_and_reload(text, resident_record):
    """Native question-only and mixed messages remain answerable after the turn.

    :module: execution.service
    :importance: P1
    :design: decision_1e2eb869bc10
    """
    store = AgentDataStore()
    request = _turn_request(resident_record['resident_id'])
    projector = AgentDataProjector(store)
    session = _runtime_session(request, 'question-session')
    projector.begin(request, session)
    projector.observe_invocation(request.execution_id, _invocation(request, session), [])
    event = {'method': 'item/completed', 'params': {'turnId': 'turn-card', 'item': {
        'type': 'agentMessage', 'id': 'call-native-card', 'phase': 'final_answer',
        'delivery': 'async', 'text': text,
        'questions': [{'title': '是否继续？', 'options': ['继续', '暂停']}],
    }}}
    for _ in range(2):
        projector.observe(provider='codex', backend_route_id='zero', backend_session_id='question-session',
                          execution_id=request.execution_id, event=event)
    final_id = projector.complete(execution_id=request.execution_id, backend_session_id='question-session',
                                 backend_route_id='zero', turn_id='turn-card', response='检查已完成。')
    assert store.segment(final_id)['content'] == '检查已完成。'
    # Resident output capture intentionally waits for ordered Conversation
    # delivery before appending to Chat. Reload the captured fact from SQL,
    # then deliver through that same Chat owner.
    segment_id = stable_segment_id('provider-model', request.execution_id,
                                  'call-native-card', 'async')
    segment = store.segment(segment_id)
    from management_portal.execution.event_projection import execution_outputs_from_events
    from management_portal.society_service import resident_execution_engine_domain, resident_execution_work
    resident_execution_work.write(resident_record, {
        'execution_id': request.execution_id, 'status': 'running', 'actor': request.actor, 'queue': [],
        'submitted_at': '2026-11-03T00:00:00Z',
    })
    outputs = execution_outputs_from_events([{
        'type': 'provider_event', 'execution_id': request.execution_id, 'sequence': 1, 'details': event,
    }])
    for _ in range(2):
        delivered = resident_execution_engine_domain.persist_execution_display_messages(
            request.agent_id, request.execution_id, outputs)
        assert delivered[0]['message_id'] == segment['metadata']['message']['message_id']
    reloaded = AgentDataStore()
    segment_ids = reloaded.sequence_segment_ids(reloaded.trace_sequence_id(request.agent_id))
    assert segment_ids.count(segment_id) == 1
    segments = [reloaded.segment(sid) for sid in segment_ids]
    messages = [project_message(s['segment_id'], s['metadata']['message'], s['content'])
                for s in segments if s['segment_type'] == 'model' and s.get('metadata', {}).get('message')]
    cards = [message_from_storage(m) for m in messages if m.get('role') == 'agent']
    cards = [m for m in cards if question_set(m)]
    assert len(cards) == 1, messages
    card = question_set(cards[0])
    assert card['questions'][0]['title'] == '是否继续？'
    assert card['questions'][0]['options'] == ['继续', '暂停']
    assert 'credentials' not in card['questions'][0]
    assert card['answer_message_id']
    from management_portal.society_service.resident_questions import prepare_question_answer
    from management_portal.society_service import resident_conversation_stream
    answer = {'message_id': card['answer_message_id'], 'role': 'user', 'content_parts': [
        {'type': 'async_question_answer', 'source_message_id': cards[0]['message_id'],
         'answers': [{'question_index': 0, 'answer': '继续'}]},
    ]}
    assert prepare_question_answer(request.agent_id, answer,
                                   resident_conversation_stream.get_repository())[1]['role'] == 'user'
    if text:
        assert any(p.get('text', '').strip() == text for p in cards[0]['content_parts'])
