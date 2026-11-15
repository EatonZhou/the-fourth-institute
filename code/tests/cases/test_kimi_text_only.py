from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import Mock
import json
import pytest
from management_portal.execution.native_options import KimiOptions
from management_portal.execution.kimi_backend import KimiBackend
from management_portal.execution.provider_turn_usage import kimi_turn_usage, read_kimi_turn_usage
from management_portal.kimi_code_client import KimiCodeClient, KimiCodeTurnFailed

POLICY = {'version': 1, 'text_only': True, 'context': 'fresh'}
@dataclass
class Request:
    runtime_options: dict
@dataclass
class Invocation:
    request: Request
    session: object = field(default_factory=lambda: SimpleNamespace(backend_route_id='kimi-code', backend_session_id='test'))
    attach_system_context: bool = True
    is_new_backend_session: bool = True

@pytest.mark.parametrize('owner', [True, False])
@pytest.mark.parametrize('settings', [True, False])
def test_final_selection_overrides_backfill(owner, settings):
    options = dict(execution_constraints=POLICY, tools=['Bash', 'mcp__*'], is_owner=owner)
    if settings: options['runtime_settings'] = {'permission_preset': 'full-access', 'model': 'kimi-code/kimi-for-coding'}
    result = KimiOptions().prepare(Invocation(Request(options))).request.runtime_options
    assert result['tools'] == [] and result['permission_mode'] == 'manual'
    assert result['execution_constraints'] == POLICY

def test_ordinary_path_keeps_tools():
    result = KimiOptions().prepare(Invocation(Request({'runtime_settings': {}, 'is_owner': True}))).request.runtime_options
    assert 'Bash' in result['tools'] and result['permission_mode'] == 'yolo'

def client(version=1):
    c = KimiCodeClient('http://127.0.0.1:1/api/v1')
    c._request = Mock(return_value={'portal_deny_all_version': version, 'tools': [{'name': 'Read', 'active': True}]})
    c.update_session_profile = Mock()
    # Native 2.4.1 deliberately projects only the model here.
    c.get_snapshot = Mock(return_value={'session': {'agent_config': {'model': 'kimi-k3'}}})
    return c

def settle(c):
    backend = KimiBackend(c)
    invocation = Invocation(Request({'execution_constraints': POLICY, 'tools':['Bash']}))
    assert backend.prepare_turn_tools(invocation, None) == ([], None)
    return backend.client_turn_options(invocation, SimpleNamespace(is_cancelled=lambda:False), [])

def test_native_policy_is_selected_without_snapshot_profile_fields():
    c = client()
    result = settle(c)
    assert result['native_tools'] == []
    assert result['deny_all_tools'] is True
    c.get_snapshot.assert_not_called()
    c.update_session_profile.assert_not_called()
    c._request.assert_called_once_with('GET', 'tools', query={'session_id': 'test'}, timeout=15.0)

@pytest.mark.parametrize('version', [None, True, '1', 0, 2])
def test_unknown_capability_fails_before_prompt(version):
    with pytest.raises(KimiCodeTurnFailed, match='native deny-all-tools'): settle(client(version))

def test_backend_rejects_post_prepare_tool_injection():
    request = SimpleNamespace(runtime_options={'execution_constraints':POLICY, 'tools':['Read'], 'permission_mode':'yolo'},
                              session=SimpleNamespace(mode='stateless'), input_media=())
    c = client()
    with pytest.raises(ValueError, match='final native options'):
        KimiBackend(c).execute(SimpleNamespace(request=request,session=SimpleNamespace()), None)
    c.update_session_profile.assert_not_called()

def test_invalid_tool_response_is_not_a_policy_capability():
    c = client(); c._request.return_value = {'other':[]}
    with pytest.raises(KimiCodeTurnFailed, match='native deny-all-tools'): settle(c)

@pytest.mark.parametrize('bad_receipt', [False, True])
def test_one_prompt_requires_its_own_native_receipt(tmp_path, bad_receipt):
    from tests.cases.test_kimi_code_client import ScriptedKimiCodeClient, _snapshot, live_answer
    receipt = {'version': 1, 'policy': 'deny_all', 'session_id': 's',
        'prompt_id': 'wrong' if bad_receipt else 'prompt-1', 'effective_tools': [], 'source': 'kimi.native.prompt'}
    c = ScriptedKimiCodeClient([_snapshot(seq=0, busy=False), _snapshot(seq=2, busy=False, reason='completed',
        messages=[{'id': 'user-1', 'role': 'user', 'content': []}])], tmp_path)
    c.live_frames = live_answer('answer')
    original = c._request
    def request(method, path, payload=None, **kwargs):
        if path == 'tools': return {'portal_deny_all_version': 1}
        value = original(method, path, payload, **kwargs)
        if path.endswith('/prompts'): value['metadata'] = {'portal_tool_policy': receipt}
        return value
    c._request = request
    if bad_receipt:
        events = []
        with pytest.raises(KimiCodeTurnFailed, match='receipt missing or mismatched'):
            c.run_turn(session_id='s', prompt='input', native_tools=[], permission_mode='manual', deny_all_tools=True, event_callback=events.append)
        assert events[0]['method'] == 'turn/started'
        assert events[0]['params']['turn']['id'] == 'prompt-1'
    else:
        result = c.run_turn(session_id='s', prompt='input', native_tools=[], permission_mode='manual', deny_all_tools=True)
        output = KimiBackend(c).turn_output(result)
        assert output['execution_constraints_receipt']['native_policy'] == receipt
        assert output['execution_constraints_receipt']['settled_tools'] == []
    prompts = [item for item in c.requests if item[1].endswith('/prompts')]
    assert len(prompts) == 1
    assert prompts[0][2]['deny_all_tools'] is True

def test_ordinary_result_does_not_get_a_restricted_receipt():
    output = KimiBackend(client()).turn_output(SimpleNamespace(response='answer', turn_id='p', tool_policy_receipt=None))
    assert 'execution_constraints_receipt' not in output

def test_restricted_error_cannot_start_continuation():
    from management_portal.execution.provider_continuation import provider_error_continuation
    invocation = Invocation(Request({'execution_constraints': POLICY}))
    error = RuntimeError('temporary network failure')
    with pytest.raises(RuntimeError) as raised:
        provider_error_continuation(error, invocation=invocation, context=None, provider_id='kimi', turn_started=True)
    assert raised.value is error

def test_usage_model_comes_only_from_wire(tmp_path):
    wire = tmp_path/'wire.jsonl'
    wire.write_text(json.dumps({'type':'usage.record','time':1001,'model':'observed-model', 'usage':{'input_tokens':10,'output_tokens':2}})+'\n')
    observed = read_kimi_turn_usage(wire,since_ms=1000)
    usage = kimi_turn_usage({}, observed)
    assert usage['model']=='observed-model' and usage['model_source']=='kimi.wire.usage.record'
    assert 'model' not in kimi_turn_usage({'input_tokens':10}, None)
