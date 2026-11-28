from types import SimpleNamespace

import pytest

from management_portal.society_service import resident_api_context as context
from management_portal.society_service import resident_input_scope
from management_portal.execution.agent_data_store import AgentDataStore
from tests.cases.test_agent_data_store import _chat

RID = 'resident_abcdef123456'


def pair(mid, user='question', answer='answer'):
    return [_chat(mid, 'user', user), _chat(mid + '-reply', 'agent', answer, reply_to_message_id=mid)]


def test_preserves_compaction_and_media(monkeypatch):
    """Model API 保留压缩替代项、图像细节和开发者角色。"""
    image = {"type": "input_image", "image_url": "data:image/png;base64,YQ==", "detail": "high"}
    msg = {"type": "message", "role": "user", "content": [image]}
    segments = [{"segment_type": "compaction", "content": {"replacement_history": [msg]}},
                {"segment_type": "input", "content": " rules ", "metadata": {"role": "developer"}}]
    monkeypatch.setattr(context.execution_port, "delivery_context_snapshot", lambda *args, **kwargs: {"segments": segments})
    history = context.api_history(RID, "current")
    from management_portal.execution.model_api_protocols import _chat_message
    assert [_chat_message(it) for it in history] == [
        {"role": "user", "content": [{"type": "image_url", "image_url": {"url": image["image_url"], "detail": "high"}}]},
        {"role": "developer", "content": [{"type": "text", "text": " rules "}]},
    ]


def test_unrepresentable_history_errors(monkeypatch):
    """无法无损映射的历史附件显式报错。"""
    segment = {"segment_id": "s", "segment_type": "input", "content": "text",
               "metadata": {"message": {"parts": [{"type": "attachment"}]}}}
    monkeypatch.setattr(context.execution_port, "delivery_context_snapshot", lambda *args, **kwargs: {"segments": [segment]})
    with pytest.raises(ValueError, match="attachment"):
        context.api_history(RID, "current")


def test_history_read_fence_does_not_include_current_or_future_messages():
    """历史读取只取当前输入之前的消息，不含当前与之后的消息"""
    store = AgentDataStore()
    for message in pair('first') + [_chat('current', 'user', 'now')] + pair('future'):
        store.display_chat_message(RID, message)
    assert [item["role"] for item in context.api_history(RID, "current")] == ["user", "assistant"]
    assert "future" not in str(context.api_history(RID, "current"))
    with pytest.raises(ValueError, match="boundary"):
        context.api_history(RID, "missing")


@pytest.mark.parametrize('forked', [False, True])
@pytest.mark.parametrize('fresh', [False, True])
def test_two_requests_pass_history_once_and_keep_system_instructions(monkeypatch, forked, fresh):
    """连续两次模型 API 请求各带一次历史，并都保留固定的 system 指令"""
    from copy import deepcopy
    from management_portal.society_service import resident_execution_domain as execution, resident_runtime_domain
    from management_portal.execution.backend_contracts import BackendInvocation
    from management_portal.execution.chat_completion_service import ChatCompletionService
    from management_portal.execution.contracts import SystemInstructions
    from management_portal.execution.session_manager import RuntimeSession
    settings = {'model': 'm', 'prompt_context': {'history': {'enabled': False, 'max_tokens': 1}}}
    monkeypatch.setattr(resident_runtime_domain, 'read_resident_runtime_settings', lambda record: deepcopy(settings))
    monkeypatch.setattr(resident_input_scope, 'get', lambda *args: {})
    monkeypatch.setattr(execution, '_lineage_delivery_profile', lambda job: {'isolated': True} if fresh else {})
    monkeypatch.setattr(execution, 'resident_system_instructions', lambda *args: SystemInstructions())
    reads = []
    def snapshot(resident_id, *, message_id):
        reads.append((resident_id, message_id))
        return {"segments": [] if message_id == "first" else [
            {"segment_type": "input", "content": "Remember BLUE"},
            {"segment_type": "model", "content": "BLUE"},
        ]}
    monkeypatch.setattr(context.execution_port, "delivery_context_snapshot", snapshot)
    payloads = []
    def request(provider, endpoint, payload, **kwargs):
        payloads.append(payload)
        return {'choices': [{'message': {'content': 'ok'}}], 'usage': {}}
    service = ChatCompletionService(SimpleNamespace(get=lambda pid: {'id': pid, 'kind': 'openai_compatible_chat'}, request=request))
    record = {'resident_id': RID, 'lineage_id': 'lineage_abcdef123456', 'backend': 'provider_api', 'backend_route_id': 'api'}
    if forked:
        record['source_resident_id'] = 'resident_source'
    for mid in ('first', 'second'):
        job = {'actor': 'CASE-A', 'job_id': mid, 'user_message_id': mid, 'raw_message': mid}
        if forked:
            job['fork_context_input'] = {'segments': [], 'prompt': mid}
        req = execution._build_resident_execution_request(record, job)
        # A Resident request passes Resident capability assembly, which reads the
        # whole invocation and records its plan on the backend context.
        session = RuntimeSession(mode='stateless', binding_id='', backend_session_id='',
                                 is_new_backend_session=True, backend_route_id='api')
        invocation = BackendInvocation(request=req, session=session, user_prompt=req.prompt, system_context='Fixed instructions',
                                       attach_system_context=True, is_new_backend_session=True)
        service.execute(invocation, SimpleNamespace(is_cancelled=lambda: False, emit=lambda *args: None,
                                                    record_capability_assembly=lambda plan: None))
    assert reads == ([] if fresh else [(RID, 'first'), (RID, 'second')])
    if fresh:
        for mid, payload in zip(('first', 'second'), payloads):
            assert payload['messages'] == [{'role': 'system', 'content': 'Fixed instructions'},
                                           {'role': 'user', 'content': mid}]
    else:
        assert payloads[0]['messages'] == [{'role': 'system', 'content': 'Fixed instructions'}, {'role': 'user', 'content': 'first'}]
        assert payloads[1]['messages'] == [{'role': 'system', 'content': 'Fixed instructions'}, {'role': 'user', 'content': [{'type': 'text', 'text': 'Remember BLUE'}]}, {'role': 'assistant', 'content': [{'type': 'text', 'text': 'BLUE'}]}, {'role': 'user', 'content': 'second'}]


def test_cleared_chat_is_not_resent_as_api_history():
    """清空 Chat 之前的消息不再作为模型 API 历史发送"""
    store = AgentDataStore()
    for message in pair('old'):
        store.display_chat_message(RID, message)
    store.clear_chat(RID, actor_ref='CASE-A')
    store.display_chat_message(RID, _chat('current', 'user', 'now'))
    assert context.api_history(RID, 'current') == []


def test_fresh_migration_keeps_chat_but_starts_new_model_api_history():
    """重置 Provider 上下文后 Chat 保留，模型 API 历史只从重置之后开始"""
    store = AgentDataStore()
    for message in pair('old'):
        store.display_chat_message(RID, message)
    store.reset_provider_context(RID, reset_at='2026-10-13T00:00:00+00:00', actor_ref='test')
    for message in pair('new') + [_chat('current', 'user', 'now')]:
        store.display_chat_message(RID, message)
    selected = context.api_history(RID, 'current')
    assert [item["role"] for item in selected] == ["user", "assistant"]
    expected = store.delivery_context_snapshot(RID, message_id="current")["segments"]
    assert [item["content"][0]["text"] for item in selected] == [item["content"] for item in expected]
