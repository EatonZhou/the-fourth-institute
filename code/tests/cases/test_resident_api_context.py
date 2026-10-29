from types import SimpleNamespace

import pytest

from management_portal.society_service import resident_api_context as context
from management_portal.society_service import resident_conversation_stream, resident_input_scope
from management_portal.execution.agent_data_store import AgentDataStore
from tests.cases.test_agent_data_store import _chat

RID = 'resident_abcdef123456'


def pair(mid, user='question', answer='answer'):
    return [_chat(mid, 'user', user), _chat(mid + '-reply', 'agent', answer, reply_to_message_id=mid)]


def test_history_selects_complete_turns_with_budget_and_scoped_exclusion():
    """历史只选已完成的整轮问答，按 token 预算截断并排除限定范围的消息"""
    messages = pair('old') + pair('private') + pair('failed') + pair('last') + [_chat('queued', 'user', 'future')]
    states = {mid: {'state': 'completed', 'reply_message_ids': [mid + '-reply']} for mid in ['old', 'private', 'last']}
    states['failed'] = {'state': 'failed', 'reply_message_ids': ['failed-reply']}
    history, report = context.select_history(messages, states, {'private'}, max_tokens=35)
    assert history == [{'role': 'user', 'content': 'question'}, {'role': 'assistant', 'content': 'answer'}]
    assert report == {'included_turns': 1, 'estimated_tokens': 30, 'truncated': True}
    assert context.select_history(messages, states, {'private'}, max_tokens=29)[0] == []


def test_assembly_presets_history_off_and_isolation(monkeypatch):
    """模型 API 消息组装先放预设消息再接历史，新上下文或关闭历史时不读取历史"""
    calls = []
    repository = SimpleNamespace(
        messages_before_input=lambda rid, mid, limit: calls.append((rid, mid, limit)) or pair('old', 'Remember BLUE', 'BLUE'),
        input_states_by_id=lambda keys: {(RID, 'old'): {'state': 'completed', 'reply_message_ids': ['old-reply']}},
    )
    monkeypatch.setattr(resident_conversation_stream, 'get_repository', lambda: repository)
    monkeypatch.setattr(resident_input_scope, 'scoped_message_ids', lambda *args: set())
    preset = {'role': 'assistant', 'content': 'preset'}
    settings = {'messages': [preset], 'history': {'enabled': True, 'max_tokens': 8000}}
    first, report = context.assemble_api_messages(RID, {'user_message_id': 'second'}, settings)
    second, _ = context.assemble_api_messages(RID, {'user_message_id': 'second'}, settings)
    assert first == second == [preset, {'role': 'user', 'content': 'Remember BLUE'}, {'role': 'assistant', 'content': 'BLUE'}]
    assert report['included_turns'] == 1
    before = len(calls)
    assert context.assemble_api_messages(RID, {'user_message_id': 'second'}, settings, fresh_context=True)[0] == []
    assert context.assemble_api_messages(RID, {}, {'messages': [preset]})[0] == [preset]
    assert context.assemble_api_messages(RID, {}, {'history': {'enabled': False}})[0] == []
    assert len(calls) == before


def test_history_read_fence_does_not_include_current_or_future_messages():
    """历史读取只取当前输入之前的消息，不含当前与之后的消息"""
    store = AgentDataStore()
    for message in pair('first') + [_chat('current', 'user', 'now')] + pair('future'):
        store.display_chat_message(RID, message)
    history = store.read_chat_messages_through_turn(RID, ['current'], limit=200, include_turn=False)
    assert [message['message_id'] for message in history] == ['first', 'first-reply']
    assert [message['message_id'] for message in store.read_chat_messages_through_turn(RID, ['current'], limit=1, include_turn=False)] == ['first-reply']
    assert store.read_chat_messages_through_turn(RID, ['missing'], limit=200, include_turn=False) == []


def test_two_requests_pass_history_once_and_keep_system_instructions(monkeypatch):
    """连续两次模型 API 请求各带一次历史，并都保留固定的 system 指令"""
    from copy import deepcopy
    from management_portal.society_service import resident_execution_domain as execution, resident_runtime_domain
    from management_portal.execution.backends import BackendInvocation, ChatCompletionService
    from management_portal.execution.contracts import SystemInstructions
    from management_portal.execution.session_manager import RuntimeSession
    settings = {'model': 'm', 'prompt_context': {'history': {'enabled': True, 'max_tokens': 8000}}}
    monkeypatch.setattr(resident_runtime_domain, 'read_resident_runtime_settings', lambda record: deepcopy(settings))
    monkeypatch.setattr(resident_input_scope, 'get', lambda *args: {})
    monkeypatch.setattr(resident_input_scope, 'scoped_message_ids', lambda *args: set())
    monkeypatch.setattr(execution, '_lineage_delivery_profile', lambda job: {})
    monkeypatch.setattr(execution, 'resident_system_instructions', lambda *args: SystemInstructions())
    repository = SimpleNamespace(
        messages_before_input=lambda rid, mid, limit: pair('first', 'Remember BLUE', 'BLUE') if mid == 'second' else [],
        input_states_by_id=lambda keys: {(RID, 'first'): {'state': 'completed', 'reply_message_ids': ['first-reply']}},
    )
    monkeypatch.setattr(resident_conversation_stream, 'get_repository', lambda: repository)
    payloads = []
    def request(provider, endpoint, payload, **kwargs):
        payloads.append(payload)
        return {'choices': [{'message': {'content': 'ok'}}], 'usage': {}}
    service = ChatCompletionService(SimpleNamespace(get=lambda pid: {'id': pid, 'kind': 'openai_compatible_chat'}, request=request))
    record = {'resident_id': RID, 'lineage_id': 'lineage_abcdef123456', 'backend': 'provider_api', 'backend_route_id': 'api'}
    for mid in ('first', 'second'):
        req = execution._build_resident_execution_request(record, {'actor': 'CASE-A', 'job_id': mid, 'user_message_id': mid, 'raw_message': mid})
        # A Resident request passes Resident capability assembly, which reads the
        # whole invocation and records its plan on the backend context.
        session = RuntimeSession(mode='stateless', binding_id='', backend_session_id='',
                                 is_new_backend_session=True, backend_route_id='api')
        invocation = BackendInvocation(request=req, session=session, user_prompt=mid, system_context='Fixed instructions',
                                       attach_system_context=True, is_new_backend_session=True)
        service.execute(invocation, SimpleNamespace(is_cancelled=lambda: False, emit=lambda *args: None,
                                                    record_capability_assembly=lambda plan: None))
    assert payloads[0]['messages'] == [{'role': 'system', 'content': 'Fixed instructions'}, {'role': 'user', 'content': 'first'}]
    assert payloads[1]['messages'] == [{'role': 'system', 'content': 'Fixed instructions'}, {'role': 'user', 'content': 'Remember BLUE'}, {'role': 'assistant', 'content': 'BLUE'}, {'role': 'user', 'content': 'second'}]


def test_cleared_chat_is_not_resent_as_api_history():
    """清空 Chat 之前的消息不再作为模型 API 历史发送"""
    store = AgentDataStore()
    for message in pair('old'):
        store.display_chat_message(RID, message)
    store.clear_chat(RID, actor_ref='CASE-A')
    store.display_chat_message(RID, _chat('current', 'user', 'now'))
    assert store.read_chat_messages_through_turn(RID, ['current'], limit=200, include_turn=False) == []


def test_fresh_migration_keeps_chat_but_starts_new_model_api_history():
    """重置 Provider 上下文后 Chat 保留，模型 API 历史只从重置之后开始"""
    store = AgentDataStore()
    for message in pair('old'):
        store.display_chat_message(RID, message)
    store.reset_provider_context(RID, reset_at='2026-10-13T00:00:00+00:00', actor_ref='test')
    for message in pair('new') + [_chat('current', 'user', 'now')]:
        store.display_chat_message(RID, message)
    selected = store.read_chat_messages_through_turn(RID, ['current'], limit=200, include_turn=False)
    assert [item['message_id'] for item in selected] == ['new', 'new-reply']
    assert store.read_chat_messages_through_turn(RID, ['old'], limit=200)
