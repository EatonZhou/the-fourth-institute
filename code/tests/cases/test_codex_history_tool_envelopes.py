from __future__ import annotations

import copy
import json

import pytest

from management_portal.execution.codex_history import response_items_from_segments


def _replay(result, *, error=None):
    return response_items_from_segments([{
        'segment_id': 'tool-envelope',
        'segment_type': 'tool',
        'content': {
            'call_id': 'call-envelope', 'tool_namespace': 'portal',
            'tool_name': 'inspect', 'input': {'arguments': {'path': '/workspace'}},
            'result': result, 'error': error,
        },
    }])


@pytest.mark.parametrize('kind', ['image', 'image_url', 'input_file'])
def test_business_file_descriptors_are_lossless_json(kind):
    """File classifications are data, without a media payload to replay.

    :module: execution.service
    :importance: P1
    :design: decision_3e8c2a7f91d4
    """
    payload = {'result': {
        'entries': [{'type': kind, 'path': '/workspace/diagram.png', 'size': 41}],
        'resource': {'type': 'image', 'id': 'image_0123456789ab', 'title': '流程图'},
    }, 'status': 'completed'}
    original = copy.deepcopy(payload)
    call, result = _replay(payload)
    assert json.loads(result['output']) == original
    assert call['call_id'] == result['call_id'] == 'call-envelope'
    assert call['namespace'] == 'portal'
    assert json.loads(call['arguments']) == {'path': '/workspace'}
    assert payload == original


@pytest.mark.parametrize(('part', 'expected'), [
    ({'type': 'image', 'mimeType': 'image/png', 'data': 'ZmFrZQ=='},
     {'type': 'input_image', 'image_url': 'data:image/png;base64,ZmFrZQ=='}),
    ({'type': 'image_url', 'image_url': {'url': 'https://example.test/figure.png', 'detail': 'high'}},
     {'type': 'input_image', 'image_url': 'https://example.test/figure.png', 'detail': 'high'}),
    ({'type': 'input_image', 'image_url': 'data:image/png;base64,ZmFrZQ==', 'detail': 'original'},
     {'type': 'input_image', 'image_url': 'data:image/png;base64,ZmFrZQ==', 'detail': 'original'}),
    ({'type': 'input_file', 'filename': 'report.pdf', 'file_data': 'data:application/pdf;base64,ZmFrZQ=='},
     {'type': 'input_file', 'filename': 'report.pdf', 'file_data': 'data:application/pdf;base64,ZmFrZQ=='}),
])
def test_explicit_media_remains_typed_and_detached_from_source(part, expected):
    """Explicit MCP and native media retain type, detail and file bytes.

    :module: execution.service
    :importance: P1
    :design: decision_3e8c2a7f91d4
    """
    payload = {'result': {'content': [{'type': 'text', 'text': '工具结果'}, part]}}
    original = copy.deepcopy(payload)
    output = _replay(payload)[1]['output']
    assert output == [{'type': 'input_text', 'text': '工具结果'}, expected]
    output[1]['type'] = 'changed-by-caller'
    assert payload == original


def test_media_wrapper_preserves_nested_metadata_and_tool_error():
    """A result/error wrapper preserves media and all sibling metadata.

    :module: execution.service
    :importance: P1
    :design: decision_3e8c2a7f91d4
    """
    payload = {
        'result': {
            'content': [
                {'type': 'text', 'text': 'partial diagram'},
                {'type': 'image', 'mimeType': 'image/png', 'data': 'ZmFrZQ=='},
            ],
            'isError': True,
            'structuredContent': {'files': [{'type': 'image', 'path': '/workspace/diagram.png'}]},
        },
        'status': 'partial', 'execution_id': 'execution-example',
    }
    error = {'code': 'partial-result', 'message': '一部分结果不可用'}
    original = copy.deepcopy(payload)
    output = _replay(payload, error=error)[1]['output']
    assert output[:2] == [
        {'type': 'input_text', 'text': 'partial diagram'},
        {'type': 'input_image', 'image_url': 'data:image/png;base64,ZmFrZQ=='},
    ]
    assert [json.loads(part['text']) for part in output[2:]] == [
        {'isError': True, 'structuredContent': payload['result']['structuredContent']},
        {'status': 'partial', 'execution_id': 'execution-example'},
        {'error': error},
    ]
    assert payload == original


@pytest.mark.parametrize('payload', [
    {'unrecognized': [{'type': 'image', 'mimeType': 'image/png', 'data': 'ZmFrZQ=='}]},
    {'result': 'partial', 'attachment': {'type': 'image_url', 'image_url': 'https://example.test/a.png'}},
    {'content': [{'type': 'image', 'source': {'type': 'unknown', 'data': 'ZmFrZQ=='}}]},
    {'content': [{'type': 'input_file', 'filename': 'missing-body.pdf'}]},
    {'content': [{'type': 'input_image', 'detail': 'original'}]},
])
def test_unknown_media_envelopes_are_not_downgraded_to_json(payload):
    """Unsupported media still fails instead of losing media semantics.

    :module: execution.service
    :importance: P1
    :design: decision_3e8c2a7f91d4
    """
    original = copy.deepcopy(payload)
    with pytest.raises(ValueError, match='unsupported'):
        _replay(payload)
    assert payload == original


@pytest.mark.parametrize(('source', 'expected'), [
    ({'type': 'base64', 'media_type': 'image/jpeg', 'data': 'ZmFrZQ=='},
     'data:image/jpeg;base64,ZmFrZQ=='),
    ({'type': 'url', 'url': 'https://example.test/diagram.jpg'},
     'https://example.test/diagram.jpg'),
])
def test_claude_output_blocks_replay_typed_media(source, expected):
    """Claude completed output uses image source blocks and output lists.

    :module: execution.service
    :importance: P1
    :design: decision_3e8c2a7f91d4
    """
    payload = {'output': [
        {'type': 'text', 'text': 'diagram'}, {'type': 'image', 'source': source},
    ], 'tool_use_id': 'claude-call'}
    original = copy.deepcopy(payload)
    output = _replay(payload)[1]['output']
    assert output == [
        {'type': 'input_text', 'text': 'diagram'},
        {'type': 'input_image', 'image_url': expected},
        {'type': 'input_text', 'text': '{"tool_use_id":"claude-call"}'},
    ]
    assert payload == original
