from types import SimpleNamespace

import pytest

from management_portal.execution.model_api_protocols import ChatProtocol, protocol_adapter


@pytest.mark.parametrize('content, expected', [
    ('answer', 'answer'),
    ([{'type': 'text', 'text': 'answer'}], 'answer'),
    ({'unexpected': 'object'}, None),
    (None, None),
])
def test_chat_protocol_preserves_caller_extensions_and_requires_text(content, expected):
    """Chat 协议透传调用方的扩展参数，回复内容为空或不是文本时报错"""
    payloads = []

    def request(api_id, endpoint, payload, **kwargs):
        payloads.append(payload)
        return {'choices': [{'message': {'content': content}}]}

    adapter = ChatProtocol()
    parameters = {'provider_extension': {'enabled': True}}
    adapter.validate(parameters)
    args = (SimpleNamespace(request=request), {'id': 'api'}, 'model', [], parameters,
            10, 0, lambda: False, lambda report: None, lambda fn: fn())
    if expected is None:
        with pytest.raises(RuntimeError, match='empty completion'):
            adapter.execute(*args)
    else:
        assert adapter.execute(*args).content == expected
    assert payloads[0]['provider_extension'] == {'enabled': True}


@pytest.mark.parametrize('kind, endpoint', [('openai_responses', 'responses'), ('anthropic_messages', 'v1/messages')])
def test_direct_calls_use_the_registered_primary_protocol(kind, endpoint):
    calls = []
    def request(api_id, path, payload, **options):
        calls.append((path, payload, options))
        text = {'type': 'text' if kind == 'anthropic_messages' else 'output_text', 'text': 'answer'}
        return {'content': [text]} if kind == 'anthropic_messages' else {'output': [{'type': 'message', 'content': [text]}]}
    adapter = protocol_adapter(kind)
    args = (SimpleNamespace(request=request), {'id': 'api'}, 'model', [{'role': 'system', 'content': 'rules'}, {'role': 'user', 'content': 'hello'}],
            {'max_tokens': 123, 'reasoning_effort': 'high'}, 10, 0, lambda: False, lambda report: None, lambda fn: fn())
    assert adapter.execute(*args).content == 'answer'
    path, payload, options = calls[0]
    assert path == endpoint and options['protocol'] == kind and payload['stream'] is False
    assert payload.get('max_tokens', payload.get('max_output_tokens')) == 123
    assert 'rules' in str(payload)
