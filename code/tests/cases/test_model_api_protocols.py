from types import SimpleNamespace

import pytest

from management_portal.execution.model_api_protocols import ChatProtocol


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
