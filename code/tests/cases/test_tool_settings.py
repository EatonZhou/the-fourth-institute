"""User tool setup reuses Resident permission/CAS truth, with explicit MCP discovery."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from management_portal import asgi, tool_settings
from management_portal.hatg import routing
from management_portal.hatg.contracts import InstructionContext, InstructionError

RID = 'resident_123456789abc'
CONFIG = {'schema': 'tfi.resident-capabilities.v1',
    'tools': [{'ref': 'portal:hatg_instruction', 'enabled': True, 'required': False}],
    'mcp': [], 'skills': [{'ref': 'skill_existing', 'enabled': True, 'required': False}]}


@pytest.fixture
def setup(monkeypatch):
    state = {'settings': {'capability_config': deepcopy(CONFIG), 'model': 'existing'}, 'revision': 'r1'}
    calls = []
    def execute(call, context):
        calls.append((call.instruction, deepcopy(call.arguments), context.caller))
        assert call.arguments['resident_id'] == RID
        if call.instruction == 'GetResidentSettings':
            result = {'settings': deepcopy(state['settings']), 'settings_revision': state['revision'],
                      'application': {'state': 'next_turn'}}
        else:
            assert call.instruction == 'UpdateResidentSettings'
            if context.caller != 'alice':
                raise InstructionError('instruction.forbidden', 'read only')
            if call.arguments['expected_revision'] != state['revision']:
                raise InstructionError('instruction.conflict', 'stale configuration')
            assert set(call.arguments['changes']) == {'capability_config'}
            state['settings'].update(deepcopy(call.arguments['changes']))
            state['revision'] = 'r2'
            result = {'settings': deepcopy(state['settings']), 'settings_revision': state['revision']}
        return SimpleNamespace(raise_for_error=lambda: result)
    monkeypatch.setattr(routing, 'execute', execute)
    monkeypatch.setattr(tool_settings, '_can_edit', lambda caller, rid: caller == 'alice')
    monkeypatch.setattr(asgi, '_instruction_principal', lambda request: ('alice', 'web'))
    monkeypatch.setattr(asgi, '_same_origin', lambda request: True)
    client = TestClient(Starlette(routes=[
        Route('/api/tool-settings', asgi.tool_settings_api, methods=['GET', 'PUT']),
        Route('/api/tool-settings/discover', asgi.tool_settings_api, methods=['POST'])]))
    return client, state, calls


def test_catalog_is_passive_and_resident_view_never_opens_mcp(setup, monkeypatch):
    client, state, calls = setup
    from management_portal.execution import resident_mcp
    monkeypatch.setattr(resident_mcp, 'discover_mcp_directory', lambda *a, **k: pytest.fail('passive view opened MCP'))
    assert len(client.get('/api/tool-settings').json()['applications']) == 14
    assert calls == []
    view = client.get('/api/tool-settings', params={'resident_id': RID}).json()
    assert view['resident']['capability_config'] == CONFIG
    assert view['resident']['can_edit'] is True
    assert len(calls) == 1 and state['revision'] == 'r1'


def test_save_is_conditioned_on_the_existing_revision_and_preserves_other_settings(setup):
    client, state, calls = setup
    config = deepcopy(CONFIG)
    config['tools'].append({'ref': 'application:pulse.read', 'enabled': True, 'required': False})
    payload = {'resident_id': RID, 'expected_revision': 'r1', 'capability_config': config}
    saved = client.put('/api/tool-settings', json=payload)
    assert saved.status_code == 200, saved.text
    assert saved.json()['settings_revision'] == 'r2'
    assert state['settings']['model'] == 'existing'
    assert state['settings']['capability_config']['skills'] == CONFIG['skills']
    assert client.put('/api/tool-settings', json=payload).status_code == 409
    assert client.put('/api/tool-settings', json=payload | {'expected_revision': ''}).status_code == 400
    assert client.put('/api/tool-settings', json=payload | {'caller': 'admin'}).status_code == 400


def test_discovery_requires_edit_authority_and_uses_target_resident_resource_permissions(setup, monkeypatch):
    client, state, _ = setup
    from management_portal.execution import resident_mcp
    seen = []
    def discover(ref, *, caller, is_cancelled=None):
        seen.append((ref, caller))
        return {'ref': ref, 'title': ref, 'tools': [{'name': 'echo', 'inputSchema': {'type': 'object'}}]}
    monkeypatch.setattr(resident_mcp, 'discover_mcp_directory', discover)
    payload = {'resident_id': RID, 'ref': 'file_123456789abc'}
    assert client.post('/api/tool-settings/discover', json=payload).status_code == 200
    assert seen == [(payload['ref'], RID)]
    assert state['revision'] == 'r1' and state['settings']['capability_config']['mcp'] == []
    monkeypatch.setattr(asgi, '_instruction_principal', lambda request: ('reader', 'web'))
    assert client.get('/api/tool-settings', params={'resident_id': RID}).json()['resident']['can_edit'] is False
    assert client.post('/api/tool-settings/discover', json=payload).status_code == 403
    assert client.put('/api/tool-settings', json={'resident_id': RID, 'expected_revision': 'r1',
        'capability_config': CONFIG}).status_code == 403
    assert len(seen) == 1


def test_authentication_origin_arguments_and_safe_discovery_errors(setup, monkeypatch):
    client, _, _ = setup
    from management_portal.execution import resident_mcp
    from management_portal.execution.resident_mcp_transport import MCPError
    def refused(*a, **k):
        raise MCPError('access_denied')
    monkeypatch.setattr(resident_mcp, 'discover_mcp_directory', refused)
    payload = {'resident_id': RID, 'ref': 'file_123456789abc'}
    failed = client.post('/api/tool-settings/discover', json=payload)
    assert failed.status_code == 400
    assert failed.json()['code'] == 'tools.discovery.access_denied'
    assert client.post('/api/tool-settings/discover', json=payload | {'caller': 'someone'}).status_code == 400
    assert client.get('/api/tool-settings?resident_id=not-a-resident').status_code == 400
    monkeypatch.setattr(asgi, '_same_origin', lambda request: False)
    assert client.post('/api/tool-settings/discover', json=payload).status_code == 403
    monkeypatch.setattr(asgi, '_instruction_principal', lambda request: ('', ''))
    assert client.get('/api/tool-settings').status_code == 401


def test_lazy_tool_panel_has_a_public_static_route():
    from pathlib import Path
    from urllib.parse import urlparse
    from management_portal.http_public_pages import PublicPagesHttp

    pages = PublicPagesHttp.__new__(PublicPagesHttp)
    pages.root = Path(asgi.__file__).parent
    pages.serve_static = lambda request, path, content_type: (path.read_text(), content_type)
    route = next(row for row in pages.routes() if row.path == '/static/portal_tool_settings.js')
    text, content_type = route.handler(None, urlparse(route.path), None)
    assert 'PortalToolSettings' in text and 'javascript' in content_type
