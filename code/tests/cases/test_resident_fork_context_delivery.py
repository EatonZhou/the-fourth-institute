from __future__ import annotations
from tests.cases.agent_data_test_support import register_session
from management_portal.society_service import resident_backend
from management_portal.society_service import resident_lineage
from management_portal.society_service.domain_ports import execution_port

import json
import pytest

from management_portal.society_service import (
    execution_client,
    lineage_domain,
    resident_catalog,
    resident_conversation_stream,
    resident_domain,
    resident_domain_common,
    resident_execution_engine_domain,
    resident_execution_work,
    resident_input_queue_domain,
)
from management_portal.society_service import resident_fork_domain
from management_portal.society_service import resident_record_domain
from management_portal.society_service import resident_fork_context
from management_portal.execution.agent_data_store import get_agent_data_store
from management_portal.execution.codex_history import response_items_from_segments
from management_portal.execution.contracts import ExecutionInputControl
from tests.cases.test_resident_folder_creation_contract import (
    future_creation_store,
    _activate_created_resident,
    _append_conversation_turn,
    _new_session_payload,
)


from management_portal.society_service import (
    resident_execution_domain,
    resident_session_backend_domain,
)
from management_portal.society_service import project_folders

def _delivered_window(prepared):
    history = prepared['fork_context_input']
    assert history['prompt'] == prepared['actual_sent_prompt']
    items = response_items_from_segments(get_agent_data_store().fork_context_segments(history['segments']))
    # String form is only a test assertion aid, never the wire representation.
    return json.dumps(items, ensure_ascii=False) + prepared['actual_sent_prompt']


@pytest.mark.parametrize('backend,provider', [
    ('codex', 'codex'), ('claude', 'claude'), ('kimi', 'kimi'),
])
def test_fork_snapshot_resolves_execution_provider_identity(
    future_creation_store, monkeypatch, backend, provider,
):
    """codex、claude、kimi Resident 的 fork 快照按执行绑定的 Provider 会话取到轨迹片段"""
    record = {'resident_id': 'resident_abcdef123456', 'backend': backend}
    route = provider + '_one'
    session_id = '01a00000-0000-7000-8000-000000000001'
    monkeypatch.setattr(resident_backend, 'record_session_id', lambda _: session_id)
    store = get_agent_data_store()
    session = store.ensure_provider_session(
        provider=provider, backend_route_id=route, backend_session_id=session_id,
        agent_id=record['resident_id'], actor_ref='sample_admin',
    )
    store.begin_execution(execution_id='execution-fork-marker', agent_id=record['resident_id'],
                          actor_ref='sample_admin', provider=provider, started_at='2026-09-08T00:00:00Z')
    store.bind_execution_provider_session('execution-fork-marker', provider_session_ref=session['provider_session_ref'])
    segment_id = store.put_segment(segment_type='input', content='remember the fork marker',
                                   execution_id='execution-fork-marker', created_by_agent_id=record['resident_id'])
    store.append_agent_segments(record['resident_id'], [segment_id], actor_ref='sample_admin')

    snapshot = resident_fork_domain._fork_provider_context_snapshot(record)

    assert snapshot is not None
    assert [item['segment_id'] for item in snapshot['segments']] == [segment_id]


def test_fork_window_keeps_every_reply_and_exact_link(subtests, isolated_portal_case):
    """fork 最近 5 或 10 轮时保留这些轮的全部回复和完整下载链接"""
    for count in [5, 10]:
        with subtests.test(count=count), isolated_portal_case() as case_path:
            segments = []
            for index in range(12):
                segments.extend([
                    {
                        'segment_id': f'input-{index}',
                        'provider_turn_id': f'turn-{index}',
                        'segment_type': 'harness',
                        'metadata': {'role': 'user', 'kind': 'effective_provider_input'},
                        'content': f'question-{index}',
                    },
                    {
                        'segment_id': f'download-{index}',
                        'provider_turn_id': f'turn-{index}',
                        'segment_type': 'model',
                        'content': f'download-{index}: /files/report-{index}.pdf?download=1',
                    },
                    {
                        'segment_id': f'final-{index}',
                        'provider_turn_id': f'turn-{index}',
                        'segment_type': 'model',
                        'content': f'final-{index}',
                    },
                ])
            selected = resident_fork_context.seed_messages(
                {'provider_session_ref': 'source', 'segments': segments},
                recent_turns=count,
            )
            texts = [resident_domain_common.protocol_message_text(item) for item in selected]
            assert len(selected) == count * 3
            for index in range(12 - count, 12):
                assert f'download-{index}: /files/report-{index}.pdf?download=1' in texts
            assert f'question-{11 - count}' not in texts


def test_fork_context_does_not_silently_truncate_delivery_links_contracts(subtests, isolated_portal_case):
    """Fork 继承的上下文不截断交付链接

    长消息末尾的交付链接完整保留在 Fork 的首条上下文中；只有加密推理的片段不生成占位文本。

    :importance: P1
    """
    with subtests.test(contract='test_fork_context_does_not_silently_truncate_delivery_links'), isolated_portal_case() as case_path:
        text = 'evidence ' * 2000 + '\n/files/late-delivery.zip?download=1'
        messages = resident_fork_context.seed_messages({
            'provider_session_ref': 'source',
            'segments': [{
                'segment_id': 'delivery',
                'provider_turn_id': 'turn-delivery',
                'segment_type': 'model',
                'content': text,
            }],
        })
        assert resident_domain_common.protocol_message_text(messages[0]) == text
    with subtests.test(contract='test_fork_does_not_inject_encrypted_reasoning_placeholders'), isolated_portal_case() as case_path:
        snapshot = {'provider_session_ref': 'source', 'segments': [{'segment_id': 'encrypted-only', 'segment_type': 'model', 'content': {'encrypted': True}, 'metadata': {'kind': 'reasoning'}}, {'segment_id': 'readable', 'segment_type': 'model', 'content': {'encrypted': True, 'summary': 'readable summary'}, 'metadata': {'kind': 'reasoning'}}, {'segment_id': 'native', 'segment_type': 'model', 'content': {'encrypted': True, 'encrypted_content': 'opaque-native-content'}, 'metadata': {'kind': 'reasoning'}}]}
        messages = resident_fork_context.seed_messages(snapshot)
        assert [item['source_segment_id'] for item in messages] == ['readable', 'native']
        assert 'readable summary' in messages[0]['content_parts'][0]['text']
    with subtests.test(contract='test_recent_fork_counts_harness_transformed_user_turns'), isolated_portal_case() as case_path:
        segments = []
        for index in range(7):
            segments.extend([{'segment_id': f'input-{index}', 'provider_turn_id': f'turn-{index}', 'segment_type': 'harness', 'metadata': {'role': 'user', 'kind': 'effective_provider_input'}, 'content': f'question-{index}'}, {'segment_id': f'output-{index}', 'provider_turn_id': f'turn-{index}', 'segment_type': 'model', 'content': f'answer-{index}'}])
        messages = resident_fork_context.seed_messages({'segments': segments, 'provider_session_ref': 'source'}, recent_turns=5)
        assert [item['source_segment_id'] for item in messages] == [item['segment_id'] for item in segments[4:]]
        assert messages[0]['role'] == 'user'


def test_fork_never_inherits_another_session_startup_instructions(isolated_portal_case):
    """Fork 不继承另一个 session 的一次性启动指令（包括经未启动完整 Fork 继承的副本）

    未启动完整 Fork 的种子以来源 session 的隐藏启动指令开头；从它再 Fork 时不得携带，目标 session 使用自己的启动指令，否则模型把继承事实当作其他 Resident 的。
    """
    # An unstarted full Fork's seed begins with the source session's hidden startup
    # instructions (no provider turn); forking from it must not carry them along.
    with isolated_portal_case():
        messages = resident_fork_context.seed_messages({'provider_session_ref': 'source', 'segments': [
            {'segment_id': 'startup', 'segment_type': 'input', 'content': '<portal-system-prompt role="resident">resident_id: resident_aaaaaaaaaaaa',
             'metadata': {'role': 'system', 'source': 'system_context', 'kind': 'system_instructions', 'hidden_from_chat': True}},
            # DSH records the system and developer messages of its own session as Provider input.
            {'segment_id': 'dsh-system', 'provider_turn_id': 'turn-1', 'segment_type': 'input',
             'content': [{'type': 'text', 'text': '<portal-system-prompt role="resident">'}], 'metadata': {'role': 'system', 'source': 'dsh_input'}},
            {'segment_id': 'dsh-developer', 'provider_turn_id': 'turn-1', 'segment_type': 'input',
             'content': [{'toolName': 'search_harness_artifact', 'type': 'tool-addition'}], 'metadata': {'role': 'developer', 'source': 'dsh_input'}},
            {'segment_id': 'question', 'provider_turn_id': 'turn-1', 'segment_type': 'input', 'content': 'K01?'},
            {'segment_id': 'answer', 'provider_turn_id': 'turn-1', 'segment_type': 'model', 'content': 'K01 a7c39e10'},
        ]}, recent_turns=1)
        assert [item['source_segment_id'] for item in messages] == ['question', 'answer']


def test_fork_context_carries_text_not_base64_media(isolated_portal_case):
    """Fork 继承的上下文只带文字，不带 base64 图片

    源会话里用户附带的图片和工具读取的截图，在 Fork 首条上下文中不带 base64 数据，避免首轮超出上下文窗口。

    :importance: P1
    """
    # A Fork's inherited history is text in its first prompt.  Screenshots and
    # images a tool read were base64 there; three of them took a Fork's first
    # turn past a 1M-token window ("Prompt is too long").
    png = "iVBORw0KGgo" + "A" * 200_000
    with isolated_portal_case():
        messages = resident_fork_context.seed_messages({'provider_session_ref': 'source', 'segments': [
            {'segment_id': 'question', 'provider_turn_id': 'turn-1', 'segment_type': 'harness',
             'metadata': {'kind': 'effective_provider_input', 'role': 'user'},
             'content': {'content': [{'type': 'text', 'text': 'K02 这张图对齐吗？'},
                                     {'type': 'image', 'image_url': f'data:image/png;base64,{png}'}]}},
            {'segment_id': 'read', 'provider_turn_id': 'turn-1', 'segment_type': 'tool',
             'content': {'call_id': 'toolu_1', 'input': {'arguments': {'file_path': '/tmp/k02.png'}},
                         'result': {'output': [{'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png', 'data': png}}]}}},
            {'segment_id': 'mcp', 'provider_turn_id': 'turn-1', 'segment_type': 'tool',
             'content': {'result': {'content': [{'type': 'image', 'data': png, 'mimeType': 'image/jpeg'}]}}},
            {'segment_id': 'answer', 'provider_turn_id': 'turn-1', 'segment_type': 'model', 'content': 'K02 对齐了'},
        ]})
        texts = [resident_domain_common.protocol_message_text(item) for item in messages]
        assert [item['source_segment_id'] for item in messages] == ['question', 'read', 'mcp', 'answer']
        assert not any('iVBORw0KGgo' in text for text in texts)
        assert sum(map(len, texts)) < 2_000
        assert 'K02 这张图对齐吗？' in texts[0] and '[image/png，约 146 KB，继承的历史只带文字，此处省略]' in texts[0]
        assert '/tmp/k02.png' in texts[1] and '[image/png，约 146 KB' in texts[1]
        assert '[image/jpeg，约 146 KB' in texts[2]
        assert texts[3] == 'K02 对齐了'


def test_fork_context_keeps_the_recent_turns_that_fit_the_window():
    """Fork 上下文保留窗口放得下的最近几轮

    按估算的 token 数，Fork 首条上下文保留压缩摘要和窗口 60% 以内的最近完整轮次，更早的轮次略去并计数。

    :importance: P1
    """
    def message(segment_id, role, text, kind="input"):
        return {"source_segment_id": segment_id, "role": role, "source_segment_type": kind,
                "content_parts": [{"type": "text", "text": text}]}
    # 1,000 Latin characters count as 420 tokens; 8,000 Chinese ones as 10,000.
    assert resident_fork_context.estimated_tokens("x" * 1_000) == 420
    assert resident_fork_context.estimated_tokens("测" * 8_000) == 10_000
    history = [message("summary", "system", "x" * 1_000, "compaction")] + [
        item for turn in range(1, 5) for item in (
            message(f"q{turn}", "user", "x" * 1_000), message(f"t{turn}", "system", "测" * 8_000, "tool"),
            message(f"a{turn}", "agent", "x" * 1_000))]
    # 60% of a 50k-token window is 30,000 tokens: the summary and two 10,840-token turns.
    kept, left_out = resident_fork_context.fit_to_window(history, 50_000)
    assert [item["source_segment_id"] for item in kept] == ["summary", "q3", "t3", "a3", "q4", "t4", "a4"]
    assert left_out == 6
    assert resident_fork_context.fit_to_window(history, 1_000_000) == (history, 0)
    # A turn larger than the window leaves only the summary of older history.
    assert resident_fork_context.fit_to_window(history, 10_000)[0] == history[:1]


def test_fork_context_text_says_what_did_not_fit(monkeypatch):
    """Fork 上下文说明有多少历史没放进去

    源会话历史超出新会话窗口时，Fork 首条上下文开头说明更早的几条历史未包含，只带最近放得下的消息。

    :importance: P1
    """
    history = [{"source_segment_id": f"m{index}", "role": "user" if index % 2 == 0 else "agent",
                "source_segment_type": "input" if index % 2 == 0 else "model",
                "content_parts": [{"type": "text", "text": f"K03-{index} " + "文" * 20_000}]} for index in range(6)]
    monkeypatch.setattr(resident_fork_context, "seed_messages", lambda snapshot: history)
    monkeypatch.setattr("management_portal.society_service.resident_record_domain.find_resident",
                        lambda resident_id: {"last_model_context_window": 100_000} if resident_id == "resident_source00001" else None)
    text = resident_execution_domain.resident_trace_context_text(
        {"last_model_context_window": 100_000}, {"segments": []})
    # 60% of the source's 100k-token window is 60,000 tokens: two of these 25,003-token messages.
    assert text.startswith("（更早的 4 条历史超出新会话可用上下文")
    assert "K03-3 " not in text and "K03-4 " in text and "K03-5 " in text


def test_fork_rejects_compaction_without_database_replacement_body(subtests, isolated_portal_case):
    """压缩记录缺少数据库中的替换历史时 fork 报错"""
    for content in [None, '', {}, {'provider_checkpoint': {'as_of_seq': 42}},
                                     {'replacement_history': []}]:
        with subtests.test(content=content), isolated_portal_case() as case_path:
            with pytest.raises(ValueError, match='压缩记录缺少'):
                resident_fork_context.seed_messages({
                    'provider_session_ref': 'source', 'segments': [{
                        'segment_id': 'opaque', 'segment_type': 'compaction', 'content': content,
                    }],
                })


@pytest.mark.parametrize('nested', [False, True])
@pytest.mark.parametrize('route', ['', 'one'])
def test_recent_fork_uses_child_trace_before_first_run(future_creation_store, monkeypatch, nested, route):
    """最近 5 轮 fork 在首次运行前使用冻结的子轨迹，不含来源之后的新轮次"""
    monkeypatch.setattr(project_folders, 'project_root_for_id', lambda _: future_creation_store)
    source = resident_domain.register_resident(_new_session_payload('Selected source'), 'sample_admin')
    source = _activate_created_resident(source['resident_id'])
    store = get_agent_data_store()
    selectors = []
    for index in range(7):
        message_id = f'msg_{50000 + index:032x}'
        store.display_chat_message(source['resident_id'], {
            'message_id': message_id, 'role': 'user', 'author_id': 'sample_admin',
            'content_parts': [{'type': 'text', 'text': f'question {index}'}],
            'created_at': f'2026-09-08T00:{index:02d}:00Z',
        })
        accepted = store.chat_message(source['resident_id'], message_id)
        _, ids, _ = _record_provider_turn(store, source, index)
        _compose_fixture(store, source, accepted, ids)
        if index in (1, 3):
            selectors.append({'message_id': message_id})
    fork_source = source
    if nested:
        branch = resident_domain.create_resident_full_context_fork(source['resident_id'], 'sample_admin')
        fork_source = resident_record_domain.find_resident(branch['resident_id'])
    result = resident_domain.create_resident_context_fork(
        fork_source['resident_id'], 'sample_admin', 5, backend_route_id=route,
    )
    child = resident_record_domain.find_resident(result['resident_id'])
    assert child['backend_route_id'] == (route or 'zero')
    _record_provider_turn(store, source, 7, question='future-parent-input', answer='future-parent-answer')
    prepared = resident_execution_domain._portable_context_execution(
        child, {'actor': 'sample_admin', 'raw_message': 'child-question'},
    )
    prompt = _delivered_window(prepared)
    for index in range(7):
        assert (f'question {index}' in prompt) == (index >= 2)
        assert (f'answer {index}' in prompt) == (index >= 2)
    assert 'future-parent' not in prompt
    # The inherited history carries the source's identity; the child is told it is its own now.
    assert f"由 {fork_source['resident_id']} Fork 而来" in prepared['fork_context_input']['portable_prompt']


@pytest.mark.parametrize('nested', [False, True])
def test_from_here_reads_recorded_input_without_repairing_execution_receipts(
    future_creation_store, monkeypatch, nested,
):
    """从消息 fork 时使用已记录的 Chat 输入，不读取执行回执补全 Provider 追加内容"""
    from types import SimpleNamespace

    monkeypatch.setattr(project_folders, 'project_root_for_id', lambda _: future_creation_store)
    source = resident_domain.register_resident(_new_session_payload('Appended source'), 'sample_admin')
    source = _activate_created_resident(source['resident_id'])
    store = get_agent_data_store()
    execution_id = 'execution-' + source['resident_id'][-12:] + 'a' * 12
    session, first_ids, turn_id = _record_provider_turn(store, source, 0, execution_id=execution_id)
    store.bind_execution_provider_session(execution_id, provider_session_ref=session['provider_session_ref'])
    message_id = 'msg_' + 'a1' * 16
    store.display_chat_message(source['resident_id'], {
        'message_id': message_id, 'role': 'user', 'author_id': 'sample_admin',
        'content_parts': [{'type': 'text', 'text': 'display-only appendix'}],
        'created_at': '2026-09-08T00:00:30Z',
    })
    original_trace = store.read_sequence(store.trace_sequence_id(source['resident_id']), limit=1000)['segments']
    frozen = resident_domain.create_resident_full_context_fork(source['resident_id'], 'sample_admin')
    _, future_ids, _ = _record_provider_turn(store, source, 1, question='future-input', answer='future-answer')
    control_id = resident_input_queue_domain.resident_input_control_id(source['resident_id'], message_id)
    receipt = {
        **ExecutionInputControl.new(
            control_id=control_id,
            execution_id=execution_id,
            actor='sample_admin',
            input=[{
                'type': 'text',
                'text': '<user-message>verified Provider appendix</user-message>',
            }],
            expected_turn_id=turn_id,
        ).to_payload(),
        'status': 'delivered',
        'updated_at': '2026-09-08T00:00:31Z',
    }
    reads = []

    def input_control(requested_control, *, agent_id):
        reads.append((requested_control, agent_id))
        assert (requested_control, agent_id) == (control_id, source['resident_id'])
        return receipt

    monkeypatch.setattr(execution_client, 'service', lambda: SimpleNamespace(input_control=input_control))
    result = resident_domain.create_resident_fork_from_message(source['resident_id'], 'sample_admin', {'message_id': message_id})
    child = resident_record_domain.find_resident(result['resident_id'])
    assert reads == []
    assert store.chat_message(frozen['resident_id'], message_id)
    source_trace = store.read_sequence(store.trace_sequence_id(source['resident_id']), limit=1000)['segments']
    assert [item['segment_id'] for item in source_trace[:2]] == [item['segment_id'] for item in original_trace[:2]]
    assert original_trace[2]['segment_id'] in {item['segment_id'] for item in source_trace}
    assert store.segment(original_trace[2]['segment_id'])  # Immutable source remains for audit.
    if nested:
        result = resident_domain.create_resident_fork_from_message(child['resident_id'], 'sample_admin', {'message_id': message_id})
        child = resident_record_domain.find_resident(result['resident_id'])
    trace_ids = {item['segment_id'] for item in store.read_sequence(store.trace_sequence_id(child['resident_id']), limit=1000)['segments']}
    assert set(first_ids) <= trace_ids
    assert not set(future_ids) & trace_ids
    assert store.chat_message_provenance(child['resident_id'], message_id)['provider_turn_id'] == ''
    prompt = _delivered_window(resident_execution_domain._portable_context_execution(child, {'actor': 'sample_admin', 'raw_message': 'child'}))
    assert 'verified Provider appendix' not in prompt
    assert 'display-only appendix' in prompt
    assert 'future-input' not in prompt and 'future-answer' not in prompt




def test_database_fork_creation_remains_a_prerequisite_after_reload(
    future_creation_store, monkeypatch,
):
    """full fork 重新读取后仍先执行会话创建任务，完成后绑定新会话"""
    monkeypatch.setattr(project_folders, 'project_root_for_id', lambda _: future_creation_store)
    source = resident_domain.register_resident(_new_session_payload('Fork source'), 'sample_admin')
    source = _activate_created_resident(source['resident_id'])
    _append_conversation_turn(source['resident_id'], 0)
    _record_provider_turn(get_agent_data_store(), source, 0)
    result = resident_domain.create_resident_full_context_fork(source['resident_id'], 'sample_admin')
    child = resident_record_domain.find_resident(result['resident_id'])
    job = resident_execution_work.read(child)
    assert job['operation'] == 'context_fork_create'
    assert resident_execution_work.is_active(job)
    assert child['session_state'] == 'creating'
    assert not resident_backend.record_session_id(child)

    operations = []
    run_session_operation = resident_session_backend_domain.run_backend_session_operation

    def admit(_service, request, _record, _work):
        # Admission must agree with the identity reserved by Lineage before
        # the Provider starts; a binding name is not the child's Agent ID.
        get_agent_data_store().reserve_execution(
            execution_id=request.execution_id, agent_id=request.agent_id,
            actor_ref=request.actor, provider=request.provider,
        )
        assert request.agent_id == child['resident_id']
        return request.execution_id

    def fork(**kwargs):
        operations.append(kwargs)
        result = run_session_operation(**kwargs)
        work = kwargs['execution_work']
        reloaded = resident_execution_work.read(child)
        assert reloaded['execution_request']['execution_id'] == work['execution_id']
        register_session(
            child, '01a00000-0000-7000-8000-000000000777', backend_route_id='zero',
        )
        return result

    monkeypatch.setattr(resident_execution_engine_domain, 'bind_or_resume_resident_execution', admit)
    monkeypatch.setattr(resident_execution_engine_domain, 'wait_for_resident_execution', lambda *_args, **_kwargs: {
        'status': 'completed', 'result': {'status': 'completed',
            'backend_session_id': '01a00000-0000-7000-8000-000000000777', 'backend_route_id': 'zero'},
    })
    monkeypatch.setattr(resident_session_backend_domain, 'run_backend_session_operation', fork)
    monkeypatch.setattr(resident_input_queue_domain, 'acknowledge_execution', lambda _: True)
    prepared = resident_execution_domain._prepare_resident_continuation(child)
    assert prepared is not None
    resident_execution_domain.run_resident_execution(*prepared)
    assert len(operations) == 1
    assert operations[0]['operation'] == 'create'
    assert _trace_refs(child)
    assert resident_backend.record_session_id(child) == '01a00000-0000-7000-8000-000000000777'


def test_compacted_fork_compacts_frozen_context_before_first_turn(
    future_creation_store, monkeypatch,
):
    """compacted fork 在首轮前压缩冻结的上下文，压缩完成前不能以完整上下文运行"""
    monkeypatch.setattr(project_folders, 'project_root_for_id', lambda _: future_creation_store)
    source = resident_domain.register_resident(_new_session_payload('Compacted source'), 'sample_admin')
    source = _activate_created_resident(source['resident_id'])
    _append_conversation_turn(source['resident_id'], 0)
    _record_provider_turn(get_agent_data_store(), source, 0)
    source_trace = get_agent_data_store().sequence_segment_ids(get_agent_data_store().trace_sequence_id(source['resident_id']))

    result = resident_domain.create_resident_compacted_context_fork(
        source['resident_id'], 'sample_admin', backend_route_id='one',
    )
    child = resident_record_domain.find_resident(result['resident_id'])
    job = resident_execution_work.read(child)
    assert job['operation'] == 'context_fork_create'
    assert child['fork_mode'] == 'compacted'
    assert child['context_compaction_policy'] == 'before_first_turn'
    assert child['context_compaction_state'] == 'pending'
    with pytest.raises(RuntimeError, match='不能回退为完整上下文'):
        resident_execution_domain._portable_context_execution(
            child, {'actor': 'sample_admin', 'raw_message': 'must wait'},
        )

    def create_session(record, creation_job):
        resident_session_backend_domain._materialize_queued_agent_data_fork(record, creation_job)
        register_session(
            record, '01a00000-0000-7000-8000-000000000778',
            backend_route_id='one',
        )
        return {
            'id': '01a00000-0000-7000-8000-000000000778',
            'sessionId': '01a00000-0000-7000-8000-000000000778',
            'backend_route_id': 'one',
        }

    compact_calls = []

    def compact(**kwargs):
        compact_calls.append(kwargs)
        return {'usage': {'input_tokens': 20, 'output_tokens': 4}}

    monkeypatch.setattr(resident_session_backend_domain, 'create_exact_resident_session', create_session)
    monkeypatch.setattr(resident_session_backend_domain, 'run_backend_session_operation', compact)
    monkeypatch.setattr(resident_input_queue_domain, 'acknowledge_execution', lambda _: True)
    prepared = resident_execution_domain._prepare_resident_continuation(child)
    assert prepared is not None
    resident_execution_domain.run_resident_execution(*prepared)

    child = resident_record_domain.find_resident(result['resident_id'])
    assert child['session_state'] == 'active'
    assert child['context_compaction_state'] == 'completed'
    assert child['context_compacted_at']
    assert len(compact_calls) == 1
    call = compact_calls[0]
    assert call['operation'] == 'compact'
    assert call['agent_id'] == child['resident_id']
    assert call['backend_route_id'] == 'one'
    assert call['metadata']['initial_context_policy'] == 'compact_before_first_turn'
    assert call['metadata']['fork_context_input']['segments'] == _trace_refs(child)
    assert get_agent_data_store().sequence_segment_ids(get_agent_data_store().trace_sequence_id(child['resident_id'])) == source_trace
    first_turn = resident_execution_domain._portable_context_execution(
        child, {'actor': 'sample_admin', 'raw_message': 'work after compact'},
    )
    assert 'fork_context_input' not in first_turn


def test_hatg_fork_preserves_context_and_creation_job(future_creation_store, monkeypatch):
    """经 HATG 创建的 full fork 带有待执行的上下文 fork 创建任务"""
    monkeypatch.setattr(project_folders, 'project_root_for_id', lambda _: future_creation_store)
    source = resident_domain.register_resident(_new_session_payload('Graph fork source'), 'sample_admin')
    source = _activate_created_resident(source['resident_id'])
    _append_conversation_turn(source['resident_id'], 0)
    _record_provider_turn(get_agent_data_store(), source, 0)
    result = lineage_domain.create_lineage_branch(source['lineage_id'], {
        'source_resident_id': source['resident_id'],
        'edge_id': 'edge_fork_context_regression', 'mode': 'full',
    }, 'sample_admin')
    child = resident_record_domain.find_resident(result['created_resident']['id'])
    job = resident_execution_work.read(child)
    assert job['operation'] == 'context_fork_create'
    assert resident_execution_work.is_active(job)
    assert child['session_state'] == 'creating'


def _compose_fixture(store, record, accepted, ids):
    """As the live writer does, consume the input instead of appending its revision."""
    from management_portal.execution.message_projection import wrap_message
    target = store.segment(ids[0])
    target_id = ids[0] + "_composed"
    store.transform_agent_segments(record['resident_id'], [accepted['segment_id']],
        segment_id=target_id, content=wrap_message(target['content']), actor_ref='portal',
        execution_id=target['execution_id'], operation_id=target['execution_id'],
        operation_type='provider_input.compose')
    sequence_id = store.trace_sequence_id(record['resident_id'])
    store.database.write(lambda connection: connection.execute(
        "DELETE FROM sequence_segments WHERE sequence_id = %s AND segment_id = %s", (sequence_id, ids[0]),
    ))
    ids[0] = target_id


def _record_provider_turn(store, record, index, *, question=None, answer=None, execution_id=None, final_message=None):
    """Record actual model context separately from the visible Chat fixture."""
    session_id = resident_backend.record_session_id(record)
    session = store.ensure_provider_session(
        provider='codex', backend_route_id='zero', backend_session_id=session_id,
        agent_id=record['resident_id'], actor_ref='sample_admin',
    )
    execution_id = execution_id or f'execution-fork-context-{record["resident_id"]}-{index}'
    turn_id = f'01a00000-0000-7000-8000-{index + 1:012d}'
    store.begin_execution(execution_id=execution_id, agent_id=record['resident_id'],
                          actor_ref='sample_admin', provider='codex', started_at=f'2026-09-08T00:{index:02d}:00Z')
    store.bind_execution_provider_session(
        execution_id, provider_session_ref=session['provider_session_ref'],
    )
    ids = []
    for kind, text in [('input', question or f'question {index}'), ('model', answer or f'answer {index}')]:
        ids.append(store.put_segment(
            segment_type=kind, content=text, execution_id=execution_id,
            created_by_agent_id=record['resident_id'], metadata={'role': 'user' if kind == 'input' else 'assistant',
                **({'message': final_message} if kind == 'model' and final_message else {})},
        ))
    store.append_agent_segments(record['resident_id'], ids, actor_ref='sample_admin')
    store.finish_execution(execution_id, status='completed', provider_turn_id=turn_id)
    return session, ids, turn_id


def test_from_here_uses_message_session_after_resident_session_migration(
    future_creation_store, monkeypatch,
):
    """Resident 换到新 Provider 会话后，从旧消息 fork 仍带上旧会话中的问答"""
    monkeypatch.setattr(project_folders, 'project_root_for_id', lambda _: future_creation_store)
    source = resident_domain.register_resident(_new_session_payload('Migrated source'), 'sample_admin')
    source = _activate_created_resident(source['resident_id'])
    store = get_agent_data_store()
    message_id = 'msg_' + 'c1' * 16
    store.display_chat_message(source['resident_id'], {
        'message_id': message_id, 'role': 'user', 'author_id': 'sample_admin',
        'content_parts': [{'type': 'text', 'text': 'question before migration'}],
        'created_at': '2026-09-08T00:00:00Z',
    })
    accepted = store.chat_message(source['resident_id'], message_id)
    old_session, old_ids, old_turn = _record_provider_turn(
        store, source, 0, question='provider question before migration',
        answer='provider answer before migration',
    )
    _compose_fixture(store, source, accepted, old_ids)
    store.display_chat_message(source['resident_id'], {
        'message_id': 'msg_' + 'c2' * 16, 'segment_id': old_ids[1],
        'role': 'agent', 'reply_to_message_id': message_id,
        'content_parts': [{'type': 'text', 'text': 'provider answer before migration'}],
        'created_at': '2026-09-08T00:00:30Z',
    })

    prepared_id = 'session-restart:test-message-cutoff'
    new_session = store.ensure_provider_session(
        provider='codex', backend_route_id='zero', backend_session_id='thread-after-migration',
        agent_id=source['resident_id'], actor_ref='sample_admin',
    )
    store.bind_provider_session(prepared_id, new_session['provider_session_ref'])
    store.replace_provider_binding(
        f'resident:{source["resident_id"]}', prepared_id,
        expected_provider_session_ref=old_session['provider_session_ref'],
    )
    assert resident_backend.session_record(source)['provider_session_ref'] == new_session['provider_session_ref']

    result = resident_domain.create_resident_fork_from_message(
        source['resident_id'], 'sample_admin', {'message_id': message_id},
    )
    child = resident_record_domain.find_resident(result['resident_id'])
    assert _trace_refs(child)
    prompt = _delivered_window(resident_execution_domain._portable_context_execution(
        child, {'actor': 'sample_admin', 'raw_message': 'question after fork'},
    ))
    assert 'provider question before migration' in prompt
    assert 'provider answer before migration' in prompt


def test_fork_preserves_full_trace_and_starts_its_own_session_instructions(
    future_creation_store, monkeypatch,
):
    """full fork 继承压缩摘要之后的完整轨迹，并以自己的启动指令开启新会话"""
    monkeypatch.setattr(project_folders, 'project_root_for_id', lambda _: future_creation_store)
    source = resident_domain.register_resident(_new_session_payload('Compacted source'), 'sample_admin')
    source = _activate_created_resident(source['resident_id'])
    store = get_agent_data_store()
    session, old_ids, _ = _record_provider_turn(store, source, 0, question='obsolete-input-1', answer='obsolete-output-2')
    summary = store.put_segment(segment_type='compaction', content_type='application/json', content={'replacement_history': [
        {'type': 'message', 'role': 'developer', 'content': [
            {'type': 'input_text', 'text': '<portal-system-prompt>inherited startup</portal-system-prompt>'},
        ]},
        {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': 'compacted-summary-fact'}]},
    ]}, execution_id=f'execution-fork-context-{source["resident_id"]}-0')
    store.append_agent_segments(source['resident_id'], [summary], actor_ref='sample_admin')
    _, new_ids, _ = _record_provider_turn(store, source, 1, answer='/files/segment-4.pdf?download=1')
    source_trace = store.sequence_segment_ids(store.trace_sequence_id(source['resident_id']))
    result = resident_domain.create_resident_full_context_fork(source['resident_id'], 'sample_admin')
    child = resident_record_domain.find_resident(result['resident_id'])
    # The parent can keep working after Fork; the child's seed stays frozen.
    _record_provider_turn(store, source, 2, answer='future-parent-answer-must-not-leak')
    job = resident_execution_work.read(child)
    assert job['operation'] == 'context_fork_create'
    resident_session_backend_domain.materialize_agent_data_fork(child['resident_id'], job['agent_data_fork'], 'sample_admin')
    prepared = resident_execution_domain._portable_context_execution(child, {'actor': 'sample_admin', 'raw_message': 'segment-5'})
    prompt = _delivered_window(prepared)
    assert prompt.count('<portal-system-prompt>') == 1
    # The child starts its own Provider session with its own startup instructions;
    # the runtime injects session_start instructions once per Execution session.
    def parts(record, job):
        return {part.name: part for part in resident_execution_domain.resident_system_instructions(record, job).parts}

    first = parts(child, prepared)
    assert '<portal-system-prompt' in first['core_rules'].content
    assert first['core_rules'].timing == 'session_start_and_after_compaction'
    assert 'compacted-summary-fact' not in prepared['actual_sent_prompt']
    assert 'compacted-summary-fact' in prompt
    assert '/files/segment-4.pdf?download=1' in prompt
    assert prompt.index('compacted-summary-fact') < prompt.index('/files/segment-4.pdf?download=1') < prompt.index('segment-5')
    assert 'obsolete-input-1' not in prompt and 'obsolete-output-2' not in prompt
    assert 'future-parent-answer-must-not-leak' not in prompt
    assert store.sequence_segment_ids(store.trace_sequence_id(child['resident_id'])) == [summary] + new_ids
    assert set(old_ids + [summary] + new_ids) <= set(source_trace)
    _accept_child_context(child)
    assert parts(
        child, {'actor': 'sample_admin', 'raw_message': 'second child turn'},
    )['core_rules'].timing == 'session_start_and_after_compaction'
    with_skill = parts(
        child,
        {
            'actor': 'sample_admin',
            'raw_message': 'turn with a task skill',
            'additional_skills': ['tfi-unified-id'],
            'allow_subagents': True,
        },
    )
    # The task Skill list travels with this turn only; the rules keep their timing.
    assert with_skill['turn_skills'].timing == 'every_turn'
    assert with_skill['turn_skills'].placement == 'prompt_prefix'
    assert 'tfi-unified-id/SKILL.md' in with_skill['turn_skills'].content
    assert '允许使用sub agent。' in with_skill['core_rules'].content
    restarted = dict(child)
    assert '<portal-system-prompt' in parts(
        restarted, {'actor': 'sample_admin', 'raw_message': 'fresh restart'},
    )['core_rules'].content


def test_historical_native_fork_provenance_does_not_require_old_provider_session(future_creation_store, monkeypatch):
    """historical_native 模式的 fork 没有旧 Provider 会话也能构建执行请求"""
    monkeypatch.setattr(project_folders, 'project_root_for_id', lambda _: future_creation_store)
    source = resident_domain.register_resident(_new_session_payload('Historical native source'), 'sample_admin')
    source = _activate_created_resident(source['resident_id'])
    child_id = resident_domain.create_resident_full_context_fork(
        source['resident_id'], 'sample_admin', defer_backend_start=True,
    )['resident_id']
    child = resident_record_domain.find_resident(child_id)
    child['fork_mode'] = 'historical_native'
    resident_record_domain.save_resident_record(child)
    assert not resident_backend.record_session_id(child)
    trace = get_agent_data_store().read_sequence(
        get_agent_data_store().trace_sequence_id(child_id), limit=100,
    )
    assert trace['segments'] == []
    request = resident_execution_domain._build_resident_execution_request(
        child, {'actor': 'sample_admin', 'message': 'new question'},
    )
    assert request.agent_id == child_id
    assert resident_record_domain.find_resident(child_id)['fork_mode'] == 'historical_native'


def test_fork_inherits_trace_messages_without_execution_records(future_creation_store, monkeypatch):
    """来源只有 Chat 消息没有执行记录时，fork 出的 Resident 继承相同轨迹并处于 ready"""
    monkeypatch.setattr(project_folders, 'project_root_for_id', lambda _: future_creation_store)
    source = resident_domain.register_resident(_new_session_payload('Chat only'), 'sample_admin')
    get_agent_data_store().display_chat_message(source['resident_id'], {
        'message_id': 'msg_eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee', 'role': 'user',
        'content_parts': [{'type': 'text', 'text': 'Chat-only history'}],
    })
    child = resident_domain.create_resident_full_context_fork(source['resident_id'], 'sample_admin')
    # A derived Resident stores its ready state; nothing reads an absent state as ready.
    assert resident_catalog.get_catalog().state_store.resident_record(child['resident_id'])['registration_state'] == 'ready'
    store = get_agent_data_store()
    parent_ids = store.sequence_segment_ids(store.trace_sequence_id(source['resident_id']))
    assert parent_ids
    assert store.sequence_segment_ids(store.trace_sequence_id(child['resident_id'])) == parent_ids


@pytest.mark.parametrize('mode', ['full', 'recent', 'message'])
def test_fork_historical_trace_respects_compaction_and_freezes_its_window(
    future_creation_store, monkeypatch, mode,
):
    """full、recent、message 三种 fork 的轨迹从最近一次压缩开始，来源随后清空也不受影响"""
    monkeypatch.setattr(project_folders, 'project_root_for_id', lambda _: future_creation_store)
    source = resident_domain.register_resident(_new_session_payload('Historical trace'), 'sample_admin')
    store = get_agent_data_store()
    agent_id = source['resident_id']
    old = store.put_segment(segment_type='input', content='before-clear')
    store.append_agent_segments(agent_id, [old], actor_ref='sample_admin')
    store.clear_chat(agent_id, actor_ref='sample_admin')
    superseded = store.put_segment(segment_type='input', content='before-compaction')
    compact = store.put_segment(segment_type='compaction', content={'replacement_history': [
        {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': 'summary'}]},
    ]})
    store.append_agent_segments(agent_id, [superseded, compact], actor_ref='sample_admin')
    ids = []
    for index in range(7):
        for role in ['user', 'agent']:
            message_id = f'msg_{50000 + index * 2 + (role == "agent"):032x}'
            store.display_chat_message(agent_id, {
                'message_id': message_id, 'role': role,
                'content_parts': [{'type': 'text', 'text': f'{role}-{index}'}],
            })
            ids.append(store.chat_message(agent_id, message_id)['segment_id'])
    if mode == 'full':
        child = resident_domain.create_resident_full_context_fork(agent_id, 'sample_admin')
        expected = [compact, *ids]
    elif mode == 'recent':
        child = resident_domain.create_resident_context_fork(agent_id, 'sample_admin', turns=5)
        expected = ids[4:]
    else:
        child = resident_domain.create_resident_fork_from_message(
            agent_id, 'sample_admin', {'message_id': f'msg_{50004:032x}'},
        )
        expected = [compact, *ids[:6]]
    child_id = child['resident_id']
    assert store.sequence_segment_ids(store.trace_sequence_id(child_id)) == expected
    future = store.put_segment(segment_type='model', content='future-parent')
    store.append_agent_segments(agent_id, [future], actor_ref='sample_admin')
    store.clear_chat(agent_id, actor_ref='sample_admin')
    assert store.agent_context_snapshot(agent_id)['empty_context']
    assert [s['segment_id'] for s in store.agent_context_snapshot(child_id)['segments']] == expected


def test_empty_database_history_is_distinct_from_missing_context(future_creation_store, monkeypatch):
    """来源历史为空时 fork 的上下文标记为空，首轮直接发送用户输入"""
    monkeypatch.setattr(project_folders, 'project_root_for_id', lambda _: future_creation_store)
    source = resident_domain.register_resident(_new_session_payload('Truly empty source'), 'sample_admin')
    child_id = resident_domain.create_resident_full_context_fork(source['resident_id'], 'sample_admin')['resident_id']
    child = resident_record_domain.find_resident(child_id)
    assert get_agent_data_store().agent_context_snapshot(child['resident_id'])['empty_context'] is True
    assert _trace_refs(child) == []
    sent = resident_execution_domain._portable_context_execution(child, {'actor': 'sample_admin', 'raw_message': 'first real input'})
    assert 'first real input' in sent['actual_sent_prompt']


def test_fork_of_unstarted_branch_uses_its_database_window(future_creation_store, monkeypatch):
    """从尚未启动的 fork 再 fork 时使用其数据库中的轨迹窗口"""
    monkeypatch.setattr(project_folders, 'project_root_for_id', lambda _: future_creation_store)
    source = resident_domain.register_resident(_new_session_payload('Nested source'), 'sample_admin')
    _append_conversation_turn(source['resident_id'], 0)
    first_id = resident_domain.create_resident_full_context_fork(source['resident_id'], 'sample_admin')['resident_id']
    first = resident_record_domain.find_resident(first_id)
    assert not resident_backend.record_session_id(first)
    second_id = resident_domain.create_resident_full_context_fork(first_id, 'sample_admin')['resident_id']
    second = resident_record_domain.find_resident(second_id)
    assert _trace_refs(second) == _trace_refs(first)
    store = get_agent_data_store()
    assert store.sequence_segment_ids(store.trace_sequence_id(second_id)) == store.sequence_segment_ids(store.trace_sequence_id(source['resident_id']))
    prompt = _delivered_window(resident_execution_domain._portable_context_execution(second, {'actor': 'sample_admin', 'raw_message': 'nested question'}))
    assert 'question 0' in prompt and 'answer 0' in prompt and 'nested question' in prompt


def test_historical_window_uses_compaction_before_cutoff_not_a_later_summary(future_creation_store, monkeypatch):
    """截止到某轮的 fork 快照使用该轮之前的压缩摘要，不用之后的摘要"""
    monkeypatch.setattr(project_folders, 'project_root_for_id', lambda _: future_creation_store)
    source = resident_domain.register_resident(_new_session_payload('Historical source'), 'sample_admin')
    source = _activate_created_resident(source['resident_id'])
    store = get_agent_data_store()
    session, old_ids, _ = _record_provider_turn(store, source, 0, answer='pre-compaction-obsolete')
    summary = store.put_segment(segment_type='compaction', content={'replacement_history': ['first-summary']},
                                execution_id=f'execution-fork-context-{source["resident_id"]}-0')
    store.append_agent_segments(source['resident_id'], [summary], actor_ref='sample_admin')
    _, target_ids, target_turn = _record_provider_turn(store, source, 1, answer='cutoff-answer')
    _record_provider_turn(store, source, 2, answer='future-turn')
    later = store.put_segment(segment_type='compaction', content={'replacement_history': ['future-summary']},
                              execution_id=f'execution-fork-context-{source["resident_id"]}-2')
    store.append_agent_segments(source['resident_id'], [later], actor_ref='sample_admin')
    snapshot = resident_fork_domain._fork_provider_context_snapshot(source, through_turn_id=target_turn)
    assert [item['segment_id'] for item in snapshot['segments']] == [summary, *target_ids]
    trace_ids = store.sequence_segment_ids(store.trace_sequence_id(source['resident_id']))
    assert snapshot['segments'][-1]['segment_id'] == target_ids[-1]
    texts = [resident_domain_common.protocol_message_text(item) for item in resident_fork_context.seed_messages(snapshot)]
    assert 'first-summary' in '\n'.join(texts)
    assert 'future-summary' not in '\n'.join(texts)


@pytest.mark.parametrize('fork_mode', ['source', 'full', 'recent10'])
@pytest.mark.parametrize('route', ['', 'one'])
@pytest.mark.parametrize('legacy_turn_metadata', [False, True])
def test_from_here_resolves_canonical_chat_input_via_composition_graph(
    future_creation_store, monkeypatch, fork_mode, route, legacy_turn_metadata,
):
    """从消息 fork 时经输入组合关系定位所选消息的 Provider 轮次，轨迹截止到该轮回复

    Live Chat inputs do not carry Provider IDs; immutable relations do."""
    monkeypatch.setattr(project_folders, 'project_root_for_id', lambda _: future_creation_store)
    source = resident_domain.register_resident(_new_session_payload('Composed input'), 'sample_admin')
    source = _activate_created_resident(source['resident_id'])
    store = get_agent_data_store()
    selected = None
    count = 12 if fork_mode == 'recent10' else 3
    selected_index = 5 if fork_mode == 'recent10' else 1
    for index in range(count):
        message_id = f'msg_{30000 + index:032x}'
        store.display_chat_message(source['resident_id'], {
            'message_id': message_id, 'role': 'user', 'author_id': 'sample_admin',
            'content_parts': [{'type': 'text', 'text': f'question {index}'}],
            'created_at': f'2026-09-08T00:{index:02d}:00Z',
        })
        accepted = store.chat_message(source['resident_id'], message_id)
        from management_portal.execution.message_projection import message_facts
        final_message = message_facts({
            'message_id': f'msg_{40000 + index:032x}', 'role': 'agent', 'reply_to_message_id': message_id,
            'created_at': f'2026-09-08T00:{index:02d}:30Z',
        })
        _, ids, turn_id = _record_provider_turn(store, source, index, final_message=final_message)
        _compose_fixture(store, source, accepted, ids)
        if index == selected_index:
            selected = (message_id, turn_id, ids[-1])
    if legacy_turn_metadata:
        # Historical Chat rows still carry their Provider turn. Composing
        # inputs above leaves gaps in the trace's stable ordinals, as it does
        # in a long-lived, repeatedly forked Resident.
        reply = store.chat_message(source['resident_id'], f'msg_{40000 + selected_index:032x}')
        store.database.write(lambda connection: connection.execute(
            "UPDATE segments SET metadata_json = jsonb_set(metadata_json::jsonb, "
            "'{message,provider_turn_id}', to_jsonb(%s::text)) WHERE segment_id = %s",
            (selected[1], reply['segment_id']),
        ))
        with store.database.read() as connection:
            position = connection.execute(
                'SELECT COUNT(*) AS count, MAX(ordinal) AS last FROM sequence_segments WHERE sequence_id = %s',
                (store.trace_sequence_id(source['resident_id']),),
            ).fetchone()
        assert position['last'] > position['count']
    fork_source = source
    if fork_mode != 'source':
        if fork_mode == 'recent10':
            first_id = resident_domain.create_resident_context_fork(source['resident_id'], 'sample_admin', 10)['resident_id']
        else:
            first_id = resident_domain.create_resident_full_context_fork(source['resident_id'], 'sample_admin')['resident_id']
        fork_source = resident_record_domain.find_resident(first_id)
        assert not resident_backend.record_session_id(fork_source)
        # The same accepted message can acquire a later execution in the
        # parent. That relation is not part of the child's frozen trace.
        _, later_ids, later_turn = _record_provider_turn(
            store, source, count, question='late parent retry', answer='late parent answer',
        )
        accepted = store.chat_message(source['resident_id'], selected[0])
        _compose_fixture(store, source, accepted, later_ids)
        assert store.chat_message_provenance(source['resident_id'], selected[0])['provider_turn_id'] == later_turn
    message_id, turn_id, last_segment = selected
    with pytest.raises(KeyError):
        store.chat_message_provenance('resident_other_scope', message_id)
    result = resident_domain.create_resident_fork_from_message(
        fork_source['resident_id'], 'sample_admin', {'message_id': message_id}, backend_route_id=route,
    )
    child = resident_record_domain.find_resident(result['resident_id'])
    assert child['backend_route_id'] == (route or 'zero')
    prompt = _delivered_window(resident_execution_domain._portable_context_execution(
        child, {'actor': 'sample_admin', 'raw_message': 'new branch question'},
    ))
    first_kept = 2 if fork_mode == 'recent10' else 0
    assert f'question {first_kept}' in prompt and f'answer {selected_index}' in prompt
    assert f'question {selected_index + 1}' not in prompt and f'answer {selected_index + 1}' not in prompt
    if fork_mode == 'recent10':
        assert 'question 0' not in prompt and 'answer 1' not in prompt
    assert 'late parent' not in prompt
    source_trace = store.sequence_segment_ids(store.trace_sequence_id(fork_source['resident_id']))
    # The child's trace runs through the turn and the last message it inherits, the turn's reply.
    reply_segment = store.chat_message(source['resident_id'], f'msg_{40000 + selected_index:032x}')['segment_id']
    assert last_segment == reply_segment
    assert store.sequence_segment_ids(store.trace_sequence_id(child['resident_id'])) == source_trace[:source_trace.index(reply_segment) + 1]
    copied = store.read_chat_messages(child['resident_id'])
    assert copied[-1]['message_id'] == f'msg_{40000 + selected_index:032x}'
    assert not any(item['message_id'] == f'msg_{30000 + selected_index + 1:032x}' for item in copied)
    workspace_snapshot = resident_conversation_stream.get_repository().snapshot(
        child['resident_id'],
    )
    assert [item['message_id'] for item in workspace_snapshot['messages']] == [
        item['message_id'] for item in copied
    ]








def test_queued_followup_does_not_repeat_initial_context(future_creation_store, monkeypatch):
    """fork 的继承上下文被接受后，排队的后续输入不再重复携带"""
    monkeypatch.setattr(project_folders, 'project_root_for_id', lambda _: future_creation_store)
    source = resident_domain.register_resident(_new_session_payload('Queued context'), 'sample_admin')
    _append_conversation_turn(source['resident_id'], 0)
    child_id = resident_domain.create_resident_full_context_fork(source['resident_id'], 'sample_admin')['resident_id']
    child = resident_record_domain.find_resident(child_id)
    first = resident_execution_domain._portable_context_execution(child, {'actor': 'sample_admin', 'raw_message': 'first'})
    queued = resident_execution_domain._portable_context_execution(child, {'actor': 'sample_admin', 'raw_message': 'second'})
    assert 'question 0' in _delivered_window(first) and 'question 0' in _delivered_window(queued)
    assert 'question 0' not in first['actual_sent_prompt']
    _accept_child_context(child)
    sent = resident_execution_domain._portable_context_execution(child, queued)
    assert 'question 0' not in sent['actual_sent_prompt']
    assert 'second' in sent['actual_sent_prompt']
    assert 'fork_context_input' not in sent


@pytest.mark.parametrize('count', [5, 10])
def test_recent_fork_uses_effective_provider_turns_with_tools(future_creation_store, monkeypatch, count):
    """最近 5 或 10 轮 fork 按实际 Provider 轮次保留回复和工具结果"""
    monkeypatch.setattr(project_folders, 'project_root_for_id', lambda _: future_creation_store)
    source = resident_domain.register_resident(_new_session_payload('Recent source'), 'sample_admin')
    source = _activate_created_resident(source['resident_id'])
    store = get_agent_data_store()
    for index in range(12):
        _append_conversation_turn(source['resident_id'], index)
        session, ids, turn = _record_provider_turn(store, source, index, answer=f'unique-download-{index}.pdf')
        segment = store.segment(ids[-1])
        tool_id = store.put_segment(segment_type='tool', content_type='application/json', content={'call_id': f'call-{index}', 'tool_name': 'inspect', 'input': {}, 'result': f'tool-evidence-{index}'}, execution_id=segment['execution_id'])
        store.append_agent_segments(source['resident_id'], [tool_id], actor_ref='sample_admin')
    child_id = resident_domain.create_resident_context_fork(source['resident_id'], 'sample_admin', count)['resident_id']
    child = resident_record_domain.find_resident(child_id)
    workspace_snapshot = resident_conversation_stream.get_repository().snapshot(child_id)
    workspace_text = '\n'.join(
        resident_domain_common.protocol_message_text(item)
        for item in workspace_snapshot['messages']
    )
    assert 'question 0' not in workspace_text and 'answer 11' in workspace_text
    prompt = _delivered_window(resident_execution_domain._portable_context_execution(child, {'actor': 'sample_admin', 'raw_message': 'next-question'}))
    for index in range(12):
        assert (f'unique-download-{index}.pdf' in prompt) == (index >= 12 - count)
        assert (f'tool-evidence-{index}"' in prompt) == (index >= 12 - count)


def _trace_refs(child):
    return [{"segment_id": s["segment_id"], "provider_turn_id": s.get("provider_turn_id", "")}
            for s in get_agent_data_store().agent_context_snapshot(child["resident_id"])["segments"]]


def _accept_child_context(child):
    store = get_agent_data_store()
    if not resident_backend.record_session_id(child):
        register_session(child, '01a00000-0000-7000-8000-' + child['resident_id'][-12:], backend_route_id='zero')
    session = resident_backend.session_record(child)
    identity = 'execution-accepted-' + child['resident_id']
    store.begin_execution(execution_id=identity, agent_id=child['resident_id'], actor_ref='sample_admin', provider='codex', started_at='2026-10-10T00:00:00Z')
    store.bind_execution_provider_session(identity, provider_session_ref=session['provider_session_ref'])
    store.finish_execution(identity, status='completed', provider_turn_id='accepted-turn')
