from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import Mock
import json
import pytest
from management_portal.execution.native_options import KimiOptions
from management_portal.execution.kimi_backend import KimiBackend
from management_portal.execution.provider_turn_usage import kimi_turn_usage, read_kimi_turn_usage

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

def client(rows):
    return SimpleNamespace(update_session_profile=Mock(), get_snapshot=Mock(return_value={'session':{'agent_config':{'tools':[],'permission_mode':'manual'}}}), _request=Mock(return_value={'tools':rows}))

def settle(c):
    backend = KimiBackend(c)
    invocation = Invocation(Request({'execution_constraints': POLICY, 'tools':['Bash']}))
    assert backend.prepare_turn_tools(invocation, None) == ([], None)
    return backend.client_turn_options(invocation, SimpleNamespace(is_cancelled=lambda:False), [])

def test_all_sources_settled_empty():
    c = client([{'name':n,'active':False} for n in ['Bash','mcp__portal__hatg_instruction','mcp__other']])
    result = settle(c)
    assert result['native_tools'] == []
    assert set(result['disabled_tools']) >= {'Bash','mcp__portal__hatg_instruction','mcp__other'}
    assert c.update_session_profile.call_args.kwargs['permission_mode'] == 'manual'

@pytest.mark.parametrize('rows', [[{'name':'Bash','active':True}], [{'name':'mcp__portal__x','active':True}], [{'name':'mcp__foreign','active':True}], [{'name':'unknown'}], [None]])
def test_unknown_or_active_fails_before_prompt(rows):
    with pytest.raises(ValueError, match='active or unverified'): settle(client(rows))

def test_profile_backfill_is_rejected():
    c = client([]); c.get_snapshot.return_value['session']['agent_config']['tools']=['Read']
    with pytest.raises(ValueError, match='profile was not settled'): settle(c)

def test_backend_rejects_post_prepare_tool_injection():
    request = SimpleNamespace(runtime_options={'execution_constraints':POLICY, 'tools':['Read'], 'permission_mode':'yolo'},
                              session=SimpleNamespace(mode='stateless'), input_media=())
    c = client([])
    with pytest.raises(ValueError, match='final native options'):
        KimiBackend(c).execute(SimpleNamespace(request=request,session=SimpleNamespace()), None)
    c.update_session_profile.assert_not_called()

def test_invalid_tool_response_is_not_an_empty_catalog():
    c = client([]); c._request.return_value = {'other':[]}
    with pytest.raises(ValueError, match='unverified'): settle(c)

def test_receipt_only_after_successful_turn(monkeypatch):
    from management_portal.execution.backend_contracts import BackendResult
    from management_portal.execution.native_socket_backend import NativeSocketBackend
    request = SimpleNamespace(runtime_options={'execution_constraints':POLICY,'tools':[],'permission_mode':'manual'},
        session=SimpleNamespace(mode='stateless'),input_media=(),operation='turn')
    invocation = SimpleNamespace(request=request,session=SimpleNamespace(backend_route_id='',backend_session_id='existing'))
    monkeypatch.setattr(NativeSocketBackend,'execute',lambda *a: BackendResult(output={'message':'ok'}))
    result = KimiBackend(client([])).execute(invocation,None)
    assert result.output['execution_constraints_receipt']['settled_tools']==[]

def test_usage_model_comes_only_from_wire(tmp_path):
    wire = tmp_path/'wire.jsonl'
    wire.write_text(json.dumps({'type':'usage.record','time':1001,'model':'observed-model', 'usage':{'input_tokens':10,'output_tokens':2}})+'\n')
    observed = read_kimi_turn_usage(wire,since_ms=1000)
    usage = kimi_turn_usage({}, observed)
    assert usage['model']=='observed-model' and usage['model_source']=='kimi.wire.usage.record'
    assert 'model' not in kimi_turn_usage({'input_tokens':10}, None)
