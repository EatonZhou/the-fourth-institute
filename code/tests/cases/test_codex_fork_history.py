from __future__ import annotations

import copy
import json
from dataclasses import replace
from unittest import mock

import pytest

from management_portal.execution.codex_history import response_items_from_segments
from management_portal.execution.codex_history_delivery import CodexHistoryDelivery
from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.execution.backends import BackendContext, CodexBackend
from tests.cases.test_agent_data_projection import _turn_request, _runtime_session, _invocation


def test_compacted_multimodal_history_is_not_converted_to_user_text_contracts(subtests, isolated_portal_case):
    """压缩后的多模态历史原样重放，不转成用户文本，未知片段类型报错"""
    with subtests.test(contract='test_compacted_multimodal_history_is_not_converted_to_user_text'), isolated_portal_case() as case_path:
        image = 'data:image/png;base64,' + 'A' * 1_176_028
        replacement = [
            {'type': 'compaction', 'encrypted_content': 'opaque-compaction'},
            {'type': 'message', 'role': 'user', 'content': [
                {'type': 'input_text', 'text': 'Compare these diagrams'},
                {'type': 'input_image', 'image_url': image, 'detail': 'original'},
            ]},
            {'type': 'message', 'role': 'assistant', 'phase': 'final_answer',
             'content': [{'type': 'output_text', 'text': 'Original answer /files/a.pdf'}]},
        ]
        segments = [
            {'segment_id': 'compact', 'segment_type': 'compaction',
             'content': {'replacement_history': replacement}},
            {'segment_id': 'question', 'segment_type': 'input', 'content': 'What changed?'},
            {'segment_id': 'answer', 'segment_type': 'model', 'content': 'The blue node.',
             'metadata': {'kind': 'final_answer'}},
        ]
        original = copy.deepcopy(segments)
        items = response_items_from_segments(segments)
        assert items[:3] == replacement
        assert items[3]['role'] == 'user'
        assert items[3]['id'].startswith('msg_')
        assert items[4]['role'] == 'assistant'
        assert items[4]['phase'] == 'final_answer'
        assert items[0]['encrypted_content'] == 'opaque-compaction'
        assert items[1]['content'][1]['image_url'] == image
        assert max(len(part['text']) for item in items for part in item.get('content', [])
                   if 'text' in part) < 100
        assert segments == original
        items[1]['content'][0]['text'] = 'mutated'
        assert segments == original
    with subtests.test(contract='test_canonical_readable_reasoning_keeps_summary_and_content_parts'), isolated_portal_case() as case_path:
        items = response_items_from_segments([{
            'segment_id': 'reasoning-original', 'segment_type': 'model',
            'metadata': {'kind': 'reasoning'},
            'content': {'summary': ['first', {'type': 'summary_text', 'text': 'second'}],
                        'text': ['reasoning body']},
        }])
        assert items == [{'type': 'message', 'role': 'assistant', 'phase': 'commentary',
                          'content': [{'type': 'output_text',
                                       'text': '[Historical reasoning]\nfirst\nsecond\nreasoning body'}]}]
    with subtests.test(contract='test_unknown_segment_is_not_silently_dropped_or_stringified'), isolated_portal_case() as case_path:
        with pytest.raises(ValueError, match='unsupported'):
            response_items_from_segments([
                {'segment_id': 'unknown', 'segment_type': 'artifact', 'content': {'file': '/a'}},
            ])


def test_tools_keep_call_identity_arguments_and_multimodal_output(subtests, isolated_portal_case):
    """工具调用重放保留调用 ID、参数、命名空间和多模态输出"""
    for namespace in ['mcp', '']:
        with subtests.test(namespace=namespace), isolated_portal_case() as case_path:
            image = 'data:image/jpeg;base64,ZmFrZQ=='
            items = response_items_from_segments([{
                'segment_id': 'tool', 'segment_type': 'tool',
                'content': {'call_id': 'call-original', 'tool_namespace': namespace,
                            'tool_name': 'inspect', 'input': {'arguments': {'path': '/a'}},
                            'result': {'result': {'content': [
                                {'type': 'text', 'text': 'diagram'},
                                {'type': 'image', 'mimeType': 'image/jpeg', 'data': 'ZmFrZQ=='},
                            ]}}, 'error': None},
            }])
            call, output = items
            assert call['type'] == 'function_call'
            assert call['id'].startswith('fc_')
            assert call['call_id'] == output['call_id'] == 'call-original'
            assert call['name'] == 'inspect'
            if namespace:
                assert call['namespace'] == namespace
            else:
                assert 'namespace' not in call
            assert json.loads(call['arguments']) == {'path': '/a'}
            assert output['type'] == 'function_call_output'
            assert output['output'] == [
                {'type': 'input_text', 'text': 'diagram'},
                {'type': 'input_image', 'image_url': image},
            ]


def test_reasoning_replay_never_requires_an_unpersisted_item(subtests, isolated_portal_case):
    """重放推理时不引用未持久化的条目 ID，非加密推理转为历史推理文本"""
    for compacted in [False, True]:
        for encrypted in [False, True]:
            with subtests.test(compacted=compacted, encrypted=encrypted), isolated_portal_case() as case_path:
                item = {'type': 'reasoning', 'id': 'rs_unpersisted',
                        'summary': [{'type': 'summary_text', 'text': 'original summary'}],
                        'content': [{'type': 'reasoning_text', 'text': 'original body'}]}
                if encrypted:
                    item['encrypted_content'] = 'opaque-native-content'
                original = copy.deepcopy(item)
                segment = {'segment_id': 'reasoning', 'segment_type': 'model', 'content': item}
                if compacted:
                    segment.update(segment_type='compaction', content={'replacement_history': [item]})
                result = response_items_from_segments([segment])
                if encrypted:
                    assert result == [original]
                else:
                    assert result[0]['type'] == 'message'
                    assert 'id' not in result[0]
                    assert result[0]['content'][0]['text'] == '[Historical reasoning]\noriginal summary\noriginal body'
                assert item == original


def test_reference_only_or_malformed_reasoning_fails_before_import(subtests, isolated_portal_case):
    """只有引用或格式错误的推理记录在导入前报错"""
    for content in [None, [], [{'text': 123}]]:
        with subtests.test(content=content), isolated_portal_case() as case_path:
            with pytest.raises(ValueError):
                response_items_from_segments([{
                    'segment_id': 'invalid', 'segment_type': 'model',
                    'content': {'type': 'reasoning', 'id': 'rs_unpersisted', 'content': content},
                }])


def test_canonical_reasoning_placeholder_only_replays_readable_summary(subtests, isolated_portal_case):
    """加密推理记录有可读摘要时才作为历史推理重放，否则省略"""
    for summary in [[], ['summary']]:
        with subtests.test(summary=summary), isolated_portal_case() as case_path:
            result = response_items_from_segments([{
                'segment_id': 'native-reasoning', 'segment_type': 'model',
                'metadata': {'kind': 'reasoning'},
                'content': {'encrypted': True, 'summary': summary},
            }])
            if summary:
                assert result == [{'type': 'message', 'role': 'assistant', 'phase': 'commentary',
                                   'content': [{'type': 'output_text', 'text': '[Historical reasoning]\nsummary'}]}]
            else:
                assert result == []


def test_unrecoverable_compaction_fails_before_any_provider_call(subtests, isolated_portal_case):
    """缺少有效 replacement_history 的压缩记录在调用 Provider 前报错"""
    for content in [{}, {'replacement_history': None}, {'replacement_history': 'text'}]:
        with subtests.test(content=content), isolated_portal_case() as case_path:
            with pytest.raises(ValueError, match='replacement_history'):
                response_items_from_segments([
                    {'segment_id': 'compact', 'segment_type': 'compaction', 'content': content},
                ])


def _delivery(store, refs, send, execution='execution-one'):
    return CodexHistoryDelivery(
        references=refs, route_id='route-test', agent_id='resident-test',
        actor='test-owner', execution_id=execution, send=send, store=store,
    )


def test_import_ack_survives_new_worker_and_new_execution_contracts(tmp_path, subtests, isolated_portal_case):
    """历史导入回执跨 Worker 和执行保留并按账户与线程区分，结果不明时不盲目重导"""
    with subtests.test(contract='test_import_ack_survives_new_worker_and_new_execution'), isolated_portal_case() as case_path:
        tmp_path = case_path
        store = AgentDataStore()
        identity = store.put_segment(segment_type='input', content='original question')
        refs = [{'segment_id': identity}]
        sent = []
        _delivery(store, refs, lambda thread, items: sent.append((thread, items))).initialize('child')
        restarted = AgentDataStore()
        _delivery(restarted, refs, lambda *args: pytest.fail('duplicate import'), 'execution-two').initialize('child')
        _delivery(restarted, refs, lambda thread, items: sent.append((thread, items))).initialize('other-child')
        assert [thread for thread, _ in sent] == ['child', 'other-child']
        other = store.put_segment(segment_type='input', content='different window')
        with pytest.raises(ValueError, match='different PostgreSQL'):
            _delivery(store, [{'segment_id': other}], lambda *args: pytest.fail('changed import')).initialize('child')
    with subtests.test(contract='test_lost_import_ack_is_not_blindly_retried'), isolated_portal_case() as case_path:
        tmp_path = case_path
        store = AgentDataStore()
        identity = store.put_segment(segment_type='input', content='original question')
        refs = [{'segment_id': identity}]
        sent = []
        def lose_reply(thread, items):
            sent.append(items)
            raise ConnectionError('reply lost after server accepted import')
        with pytest.raises(ConnectionError):
            _delivery(store, refs, lose_reply).initialize('child')
        with pytest.raises(RuntimeError, match='outcome is unknown'):
            _delivery(store, refs, lambda *args: pytest.fail('duplicate import')).initialize('child')
        assert len(sent) == 1
    with subtests.test(contract='test_import_receipts_are_scoped_to_account_and_thread'), isolated_portal_case() as case_path:
        tmp_path = case_path
        store = AgentDataStore()
        identity = store.put_segment(segment_type='input', content='frozen question')
        sent = []
        for route_id in ['account-one', 'account-three', 'account-one']:
            delivery = CodexHistoryDelivery(
                references=[{'segment_id': identity}], route_id=route_id,
                agent_id='resident-test', actor='test-owner', execution_id='execution-test',
                send=lambda thread, items: sent.append((thread, items)), store=store,
            )
            delivery.initialize('same-thread-id')
        assert len(sent) == 2
        assert sent[0] == sent[1]
    with subtests.test(contract='test_serializer_change_does_not_reinject_an_already_imported_window'), isolated_portal_case() as case_path:
        tmp_path = case_path
        store = AgentDataStore()
        identity = store.put_segment(segment_type='input', content='frozen question')
        delivery = _delivery(store, [{'segment_id': identity}], lambda *args: None)
        with mock.patch('management_portal.execution.codex_history_delivery.response_items_from_segments',
                        return_value=[{'type': 'reasoning', 'id': 'rs_legacy'}]):
            delivery.initialize('child')
        delivery.send = lambda *args: pytest.fail('must not append a second history')
        with pytest.raises(ValueError, match='different PostgreSQL'):
            delivery.initialize('child')
    with subtests.test(contract='test_invalid_history_does_not_record_an_import_intent'), isolated_portal_case() as case_path:
        tmp_path = case_path
        store = AgentDataStore()
        broken = store.put_segment(segment_type='compaction', content={}, content_type='application/json')
        with pytest.raises(ValueError, match='replacement_history'):
            _delivery(store, [{'segment_id': broken}], lambda *args: pytest.fail('invalid import')).initialize('child')
        good = store.put_segment(segment_type='input', content='valid window')
        sent = []
        _delivery(store, [{'segment_id': good}], lambda *args: sent.append(args)).initialize('child')
        assert len(sent) == 1


def test_codex_backend_delivers_database_items_separately_from_first_user_message(tmp_path):
    """codex 后端把数据库历史单独导入线程，首条用户消息只含新问题"""
    store = AgentDataStore()
    image = 'data:image/png;base64,' + 'A' * 1_176_028
    replacement = [{'type': 'message', 'role': 'user', 'content': [
        {'type': 'input_text', 'text': 'earlier question'},
        {'type': 'input_image', 'image_url': image},
    ]}]
    compact = store.put_segment(segment_type='compaction', content_type='application/json',
                                content={'replacement_history': replacement})
    request = replace(_turn_request('resident_typed_child'), prompt='first new question',
                      metadata={'fork_context_input': {'segments': [{'segment_id': compact}],
                                                        'prompt': 'first new question'}})
    session = _runtime_session(request, 'child-session')
    client = mock.Mock()
    sent = []
    def run_turn(thread, prompt, **kwargs):
        kwargs['history_initializer'](thread)
        sent.append(prompt)
        kwargs['turn_started_callback']('child-turn')
        return mock.Mock(thread_id=thread, turn_id='child-turn', response='done', token_usage={})
    client.run_turn.side_effect = run_turn
    registry = mock.Mock()
    registry.get.return_value = mock.Mock(account_id='zero', socket_path=tmp_path / 'fake.sock')
    registry.find_thread_rollout.return_value = None
    backend = CodexBackend(account_registry=registry, client_factory=lambda _: client)
    context = BackendContext(emit_event=lambda *_: None, cancellation_requested=lambda: False,
                             set_interrupt_target=lambda *_: None)
    with mock.patch('management_portal.execution.codex_history_delivery.get_agent_data_store', return_value=store):
        result = backend.execute(_invocation(request, session), context)
    assert result.output['message'] == 'done'
    assert sent == ['first new question']
    imports = [call for call in client.request.call_args_list if call.args[0] == 'thread/inject_items']
    assert len(imports) == 1
    assert imports[0].args[1] == {'threadId': 'child-session', 'items': replacement}


@pytest.mark.parametrize("forked", [True, False])
def test_a_lost_zero_turn_fork_thread_is_replaced_on_the_first_turn(tmp_path, forked):
    """Fork 子 Resident 未用过的线程丢失时首轮换新线程并导入历史，普通会话丢失仍报错"""
    from management_portal.codex_app_server_protocol import CodexAppServerError, CodexThreadNotFound
    from management_portal.execution.agent_data_projection import AgentDataProjector
    from management_portal.execution.session_manager import SessionManager

    store = AgentDataStore()
    replacement = [{'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': 'earlier'}]}]
    compact = store.put_segment(segment_type='compaction', content_type='application/json',
                                content={'replacement_history': replacement})
    metadata = {'fork_context_input': {'segments': [{'segment_id': compact}], 'prompt': 'first question'}} if forked else {}
    request = replace(_turn_request('resident_typed_child'), prompt='first question', metadata=metadata)
    if forked:
        store.append_agent_segments(request.agent_id, [compact], actor_ref=request.actor)
    sessions = SessionManager(agent_data=store)
    sessions.bind_session(request.session.binding_id, provider='codex', backend_route_id='zero',
                          backend_session_id='lost-thread', )
    session = sessions.prepare(request)
    projector = AgentDataProjector(store)
    projector.begin(request, session)
    projector.observe_invocation(request.execution_id, _invocation(request, session), [])
    client = mock.Mock()
    turns = []

    def run_turn(thread, prompt, **kwargs):
        turns.append(thread)
        if thread == 'lost-thread':
            raise CodexThreadNotFound('Codex app-server request failed: thread not found: lost-thread')
        if kwargs.get('history_initializer'):
            kwargs['history_initializer'](thread)
        projector.observe(
            provider='codex', backend_route_id='zero', backend_session_id=thread,
            execution_id=request.execution_id,
            event={'method': 'turn/started', 'params': {'turn': {'id': 'child-turn'}}},
        )
        kwargs['turn_started_callback']('child-turn')
        return mock.Mock(thread_id=thread, turn_id='child-turn', response='done', token_usage={})
    client.run_turn.side_effect = run_turn
    client.start_thread.return_value = {'thread': {'id': 'replacement-thread'}}
    registry = mock.Mock()
    registry.get.return_value = mock.Mock(account_id='zero', socket_path=tmp_path / 'fake.sock')
    registry.find_thread_rollout.return_value = None
    backend = CodexBackend(account_registry=registry, client_factory=lambda _: client)
    events = []
    context = BackendContext(emit_event=lambda kind, details: events.append(kind), cancellation_requested=lambda: False,
                             set_interrupt_target=lambda *_: None)
    with mock.patch('management_portal.execution.codex_history_delivery.get_agent_data_store', return_value=store):
        if not forked:
            # Only a Fork child's never-used thread is replaceable; a lost ordinary session stays an error.
            with pytest.raises(CodexAppServerError):
                backend.execute(_invocation(request, session), context)
            assert turns == ['lost-thread'] and not client.start_thread.called
            return
        result = backend.execute(_invocation(request, session), context)
    assert turns == ['lost-thread', 'replacement-thread']
    assert result.backend_session_id == 'replacement-thread'
    assert 'fork_thread_replaced' in events
    imports = [call for call in client.request.call_args_list if call.args[0] == 'thread/inject_items']
    assert [call.args[1]['threadId'] for call in imports] == ['replacement-thread']
    # The answer is not enough: completion must persist the recovered binding,
    # and the next request must resume that same thread.
    projector.complete(execution_id=request.execution_id, backend_session_id=result.backend_session_id,
                       backend_route_id=result.backend_route_id, turn_id='child-turn', response='done')
    sessions.complete(request, session, backend_session_id=result.backend_session_id,
                      backend_route_id=result.backend_route_id)
    assert sessions.prepare(request).backend_session_id == 'replacement-thread'
    assert not sessions.provider_binding(f'recovery:{request.execution_id}')


@pytest.mark.parametrize('reason', ['accepted', 'concurrent_replacement'])
def test_empty_thread_recovery_cannot_reset_used_or_concurrently_replaced_binding(reason):
    """已接受输入或已被并发替换的会话绑定不能被空线程恢复重置"""
    from management_portal.execution.session_manager import SessionManager
    store = AgentDataStore()
    sessions = SessionManager(agent_data=store)
    request = _turn_request('resident_recovery_guard')
    sessions.bind_session(request.session.binding_id, provider='codex', backend_route_id='zero',
                          backend_session_id='original', )
    session = sessions.prepare(request)
    if reason == 'accepted':
        store.begin_execution(execution_id='accepted-execution', agent_id=request.agent_id,
                              actor_ref=request.actor, provider='codex', started_at='2026-10-08T00:00:00Z')
        store.bind_execution_provider_session('accepted-execution', provider_session_ref=session.provider_session_ref)
        store.record_input_acceptance('accepted-execution', agent_id=request.agent_id, provider_turn_id='accepted-turn')
        expected_thread = 'original'
    else:
        sessions.bind_session('prepared', provider='codex', backend_route_id='zero',
                              backend_session_id='concurrent', )
        sessions.replace_session(request.session.binding_id, 'prepared')
        expected_thread = 'concurrent'
    with pytest.raises(RuntimeError):
        sessions.replace_unaccepted_session(request, session, backend_session_id='unwanted', backend_route_id='zero')
    assert sessions.prepare(request).backend_session_id == expected_thread
    assert not sessions.provider_binding(f'recovery:{request.execution_id}')


@pytest.mark.parametrize('failure', ['missing_thread', 'missing_rollout', 'missing_source_rollout', 'missing_history', 'other_error'])
def test_explicit_fork_recovers_a_missing_source_only_from_recorded_context(tmp_path, failure):
    """显式会话重建在源线程丢失时仅使用已记录上下文

    显式会话重建在源线程丢失时仅使用已记录上下文；覆盖真实格式、重放及边界行为。

    :module: execution.persistence
    :importance: P1
    :design: decision_b1ab45c4b3ec
    """
    from management_portal.codex_app_server_protocol import CodexAppServerError, CodexThreadNotFound
    from tests.cases.live_trace_support import AGENT, _turn

    store, _, _, _ = _turn('codex')
    source_thread = 'unrecorded-thread' if failure == 'missing_history' else 'thread-native'
    request = replace(_turn_request(AGENT), operation='fork')
    session = replace(_runtime_session(request, source_thread), backend_route_id='one')
    client = mock.Mock()
    error = 'request denied' if failure == 'other_error' else f'thread not found: {source_thread}'
    if failure == 'missing_rollout':
        error = f'no rollout found for thread id {source_thread}'
    if failure == 'missing_source_rollout':
        error = f'invalid paginated history lineage for {source_thread}: missing source rollout'
    client.fork_thread.side_effect = (CodexAppServerError if failure == 'other_error' else CodexThreadNotFound)('Codex app-server request failed: ' + error)
    client.start_thread.return_value = {'thread': {'id': 'restored-thread'}}
    registry = mock.Mock()
    registry.get.return_value = mock.Mock(account_id='one', socket_path=tmp_path / 'fake.sock')
    backend = CodexBackend(account_registry=registry, client_factory=lambda _: client)
    context = BackendContext(emit_event=lambda *_: None, cancellation_requested=lambda: False,
                             set_interrupt_target=lambda *_: None)
    with mock.patch('management_portal.execution.agent_data_store.get_agent_data_store', return_value=store):
        if failure not in {'missing_thread', 'missing_rollout', 'missing_source_rollout'}:
            with pytest.raises(CodexAppServerError):
                backend.execute(_invocation(request, session), context)
            assert not client.start_thread.called
            return
        result = backend.execute(_invocation(request, session), context)
    assert result.backend_session_id == 'restored-thread'
    assert result.output['forked_from_session_id'] == source_thread
    imports = [call for call in client.request.call_args_list if call.args[0] == 'thread/inject_items']
    assert len(imports) == 1
    assert imports[0].args[1]['threadId'] == 'restored-thread'
    assert 'inspect the code' in json.dumps(imports[0].args[1]['items'])
    assert not client.run_turn.called


def test_codex_backend_injects_frozen_fork_context_before_initial_compact(tmp_path):
    """首轮前需压缩的 Fork 先导入冻结的继承上下文再压缩"""
    store = AgentDataStore()
    inherited = store.put_segment(segment_type='input', content='frozen inherited context')
    request = replace(
        _turn_request('resident_compacted_child'),
        prompt='',
        operation='compact',
        metadata={
            'initial_context_policy': 'compact_before_first_turn',
            'fork_context_input': {'segments': [{'segment_id': inherited}]},
        },
    )
    session = _runtime_session(request, 'compacted-child-session')
    events = []
    client = mock.Mock()
    client.request.side_effect = lambda method, *_args, **_kwargs: events.append(method)
    client.compact_thread.side_effect = lambda *_args, **_kwargs: (
        events.append('thread/compact') or {'input_tokens': 7}
    )
    registry = mock.Mock()
    registry.get.return_value = mock.Mock(
        account_id='zero', socket_path=tmp_path / 'fake.sock',
    )
    registry.find_thread_rollout.return_value = None
    backend = CodexBackend(account_registry=registry, client_factory=lambda _: client)
    backend._record_new_compaction = mock.Mock(return_value=None)
    context = BackendContext(
        emit_event=lambda *_: None,
        cancellation_requested=lambda: False,
        set_interrupt_target=lambda *_: None,
    )
    with mock.patch(
        'management_portal.execution.codex_history_delivery.get_agent_data_store',
        return_value=store,
    ):
        result = backend.execute(_invocation(request, session), context)

    assert result.output['operation'] == 'compact'
    assert events == ['thread/inject_items', 'thread/compact']
    imported = client.request.call_args_list[0]
    assert imported.args[1] == {
        'threadId': 'compacted-child-session',
        'items': [{'type': 'message', 'role': 'user', 'content': [
            {'type': 'input_text', 'text': 'frozen inherited context'},
        ], 'id': mock.ANY}],
    }
