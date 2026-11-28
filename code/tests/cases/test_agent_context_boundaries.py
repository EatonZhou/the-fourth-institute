"""Fork context and provider-reset boundaries remain independent of source history.

:module: spec.design-governance
:aspects: functional
"""
import pytest

from management_portal.execution.agent_data_store import get_agent_data_store
from management_portal.society_service import resident_execution_domain as delivery, resident_execution_provider


@pytest.mark.parametrize('carrier_delivery', ['native', 'receipt', 'pending', 'other_session'])
def test_held_info_delivery_follows_its_confirmed_carrier(carrier_delivery):
    """随请求送达的 info 不再补发；未送达或只送到别的会话仍保留。"""
    from tests.cases.live_trace_support import AGENT, _turn

    store, _, request, session_ref = _turn('codex')
    with store.database.transaction() as connection:
        connection.execute('UPDATE executions SET provider_turn_id=%s WHERE execution_id=%s',
                           ('accepted-turn', request.execution_id))
    carrier = store.display_chat_message(AGENT, {
        'message_id': 'carrier', 'role': 'user',
        'content_parts': [{'type': 'text', 'text': 'background info\nrequest'}],
        **({'execution_id': request.execution_id} if carrier_delivery == 'native' else {}),
    })
    info = store.display_chat_message(AGENT, {
        'message_id': 'info', 'role': 'user', 'released_with': 'carrier',
        'content_parts': [{'type': 'text', 'text': 'background info'}],
    })
    binding = request.session.binding_id
    store.bind_provider_session(binding, session_ref)
    if carrier_delivery == 'receipt':
        store.record_context_received(AGENT, session_ref, [carrier['segment_id']], actor_ref='test')
    elif carrier_delivery == 'other_session':
        other = store.ensure_provider_session(provider='codex', backend_route_id='one',
                                              backend_session_id='other', agent_id=AGENT, actor_ref='test')
        store.record_context_received(AGENT, other['provider_session_ref'], [carrier['segment_id']], actor_ref='test')

    before = store.agent_context_snapshot(AGENT)
    pending = store.delivery_context_snapshot(AGENT, message_id="", binding_id=binding)['segments']
    if carrier_delivery in {'native', 'receipt'}:
        assert pending == []
    else:
        assert [s['segment_id'] for s in pending] == [carrier['segment_id'], info['segment_id']]

    # A later native output must not turn a genuinely missing input into a receipt.
    output = store.put_segment(segment_type='model', content='answer', execution_id=request.execution_id)
    store.append_agent_segments(AGENT, [output], actor_ref='test')
    if carrier_delivery in {'native', 'receipt'}:
        assert store.delivery_context_snapshot(AGENT, message_id="", binding_id=binding)['segments'] == []
    else:
        with pytest.raises(ValueError, match='cannot append missing history'):
            store.delivery_context_snapshot(AGENT, message_id="", binding_id=binding)
    assert store.agent_context_snapshot(AGENT)['segments'][:-1] == before['segments']


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
    assert [s['segment_id'] for s in seed['segments']] == [s['segment_id'] for s in store.agent_context_snapshot(child, before_message_id=following)['segments']]
    assert seed['prompt'] == following

    if second_reset == 'fresh':
        store.reset_provider_context(child, reset_at='2026-11-19T09:30:00Z', actor_ref='test')
    else:
        store.record_context_reset(child, reset_at='2026-11-19T09:30:00Z', actor_ref='test',
                                   trace_position=store.trace_length(child))
    assert 'fork_context_input' not in send_input(following)
    assert 'fork_context_input' not in send_input(original)
    assert (store.chat_message(child, original) is not None) == (second_reset == 'fresh')
    assert store.agent_context_snapshot(child, before_message_id=original) == historical


def test_native_and_stateless_delivery_preserve_the_same_frozen_history(monkeypatch):
    from management_portal.execution.codex_history import response_items_from_segments
    from management_portal.society_service.resident_api_context import api_history

    store = get_agent_data_store()
    rid = 'resident_123abc123abc'
    record = {'resident_id': rid, 'backend': 'codex'}
    image = {'type': 'input_image', 'image_url': 'data:image/png;base64,YQ==', 'detail': 'high'}
    summary = {'type': 'message', 'role': 'user', 'content': [image]}
    call = {'type': 'function_call', 'name': 'inspect', 'call_id': 'a', 'arguments': '{}'}
    output = {'type': 'function_call_output', 'call_id': 'a', 'output': '  exact output\n'}
    ids = [store.put_segment(segment_type=kind, content_type='application/json', content=content)
           for kind, content in [('compaction', {'replacement_history': [summary]}),
                                 ('model', call), ('tool', output)]]
    store.append_agent_segments(rid, ids, actor_ref='test')
    store.display_chat_message(rid, {'message_id': 'current', 'role': 'user',
                                    'content_parts': [{'type': 'text', 'text': 'continue'}]})
    job = {'user_message_id': 'current', 'message': 'continue'}
    monkeypatch.setattr(delivery, '_prepare_resident_prompt', lambda _, j: {**j, 'actual_sent_prompt': j['message']})
    def native_items():
        prepared = delivery._portable_context_execution(record, job)
        refs = prepared.get('fork_context_input', {}).get('segments', [])
        return response_items_from_segments(store.fork_context_segments(refs)) if refs else []

    assert native_items() == api_history(rid, 'current') == [summary, call, output]
    session = store.ensure_provider_session(provider='codex', backend_route_id='one',
                                            backend_session_id='target', agent_id=rid, actor_ref='test')
    store.bind_provider_session(delivery.resident_backend.execution_binding_id(record), session['provider_session_ref'])
    store.record_context_received(rid, session['provider_session_ref'], ids[:2], actor_ref='test')
    assert native_items() == [output]
    assert api_history(rid, 'current') == [summary, call, output]


def test_codex_gap_replacement_uses_original_window_and_checks_binding(monkeypatch):
    from management_portal.execution.agent_data_contexts import SessionHistoryGap
    from management_portal.execution.contracts import ExecutionRequest, SessionOptions
    from management_portal.execution.session_manager import SessionManager
    rid = 'resident_123456789abc'
    binding = 'resident:' + rid
    manager = SessionManager(agent_data=get_agent_data_store())
    manager.bind_session(binding, provider='codex', backend_route_id='one', backend_session_id='old')
    gap = SessionHistoryGap(manager.provider_binding(binding))
    gap.snapshot = {'segments': [{'segment_id': 's', 'segment_type': 'model', 'content': 'answer'}]}
    def snapshot(*args, **kwargs):
        raise gap
    monkeypatch.setattr(delivery.execution_port, 'delivery_context_snapshot', snapshot)
    monkeypatch.setattr(delivery, '_prepare_resident_prompt', lambda _, j: {**j, 'actual_sent_prompt': 'current'})
    record = {'resident_id': rid, 'backend': 'codex'}
    job = delivery._portable_context_execution(record, {})
    req = ExecutionRequest.new(actor='test', agent_id=rid, prompt='current', service_type='agent_session',
        provider='codex', operation='turn', session=SessionOptions(mode='persistent', binding_id=binding),
        metadata={k:job[k] for k in ('codex_history_reset','fork_context_input')})
    session = manager.prepare(req)
    assert session.is_new_backend_session and not session.backend_session_id
    assert manager.provider_binding(binding)['backend_session_id'] == 'old'
    manager.complete(req, session, 'replacement')
    assert manager.provider_binding(binding)['backend_session_id'] == 'replacement'
    with pytest.raises(ValueError, match='binding changed'):
        manager.prepare(req)
    gap.snapshot['segments'][0]['metadata'] = {'kind':'reasoning'}
    with pytest.raises(ValueError, match='Cannot rebuild Codex session with exact history'):
        delivery._portable_context_execution(record, {})
