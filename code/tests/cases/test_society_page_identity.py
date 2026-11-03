"""Native Society documents and their file/catalog dependencies use one principal."""
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import urlparse

import pytest

from management_portal import http_auth, http_overview, http_responses
from management_portal import serve_management_portal as server

RESIDENT = 'resident_0123456789ab'
LINEAGE = 'lineage_0123456789ab'
DOCUMENTS = ['/lineage?workspace=workspace_0123456789ab', '/lineage/',
             '/lineage.html', '/hatg-panel.html']


@pytest.fixture
def entry(monkeypatch, tmp_path):
    actor = [RESIDENT]
    monkeypatch.setattr(http_auth, '_get_page_api_actor', lambda request: actor[0])
    monkeypatch.setattr(http_auth, '_get_session_username', Mock(side_effect=AssertionError('No identity reread')))
    response = lambda request, status, payload, **kwargs: (status, payload)
    monkeypatch.setattr(http_responses, '_serve_http_error', response)
    monkeypatch.setattr(http_responses, '_serve_json', response)
    for owner in [server._PORTAL_PAGES_HTTP, server._LINEAGE_HTTP, server._AGENT_WORKBENCH_HTTP]:
        monkeypatch.setattr(owner, 'respond', response)
    pages = server._PORTAL_PAGES_HTTP
    monkeypatch.setattr(pages, 'document', response)
    monkeypatch.setattr(pages, 'lineage_html', lambda: b'Society')
    monkeypatch.setattr(pages, 'hatg_html', lambda: b'HATG')
    lineage = Mock()
    monkeypatch.setattr(server._LINEAGE_HTTP, 'lineage', lambda: lineage)
    monkeypatch.setattr(server._LINEAGE_HTTP, 'service_status', lambda: {'ready': True})
    workbench = server._AGENT_WORKBENCH_HTTP
    resident, workspace, discovery, recommendation, execution = [Mock() for _ in range(5)]
    for name, domain in [('resident_api', resident), ('workspace_api', workspace),
                         ('discovery_client', discovery), ('recommendation_backend', recommendation),
                         ('execution_api', execution)]:
        monkeypatch.setattr(workbench, name, lambda domain=domain: domain)
    sidebar = Mock(return_value={'residents': []})
    shortcuts = Mock(return_value={'shortcuts': []})
    monkeypatch.setattr(workbench, 'resident_sidebar_snapshot', sidebar)
    monkeypatch.setattr(workbench, 'resident_sidebar_backend', lambda: SimpleNamespace(shortcuts=shortcuts))
    monkeypatch.setattr(workbench, 'read_json', lambda *args, **kwargs: {})
    monkeypatch.setattr(workbench, 'link_permission_denied', lambda request: (403, 'denied'))
    users = Mock(return_value=[{'id': 'human-reader', 'type': 'human'}])
    monkeypatch.setattr(http_overview, 'list_users', users)
    request = SimpleNamespace(headers={'Authorization': 'Bearer fixture'})

    def call(path, method='GET'):
        parsed = urlparse(path)
        result = server._PORTAL_HTTP_ROUTES.dispatch(method, parsed.path, request, parsed, '', phase='dual-auth')
        assert not getattr(request, '_portal_authenticated_actor', None)
        return result
    return SimpleNamespace(**locals())


@pytest.mark.parametrize('principal', [RESIDENT, 'human-reader'])
def test_society_page_catalog_and_file_download_keep_principal(entry, monkeypatch, principal):
    e = entry
    e.actor[0] = principal
    for path in DOCUMENTS:
        assert e.call(path)[0] == 200
    assert e.call('/api/users')[1][0]['id'] == 'human-reader'
    assert e.call('/lineage/api/service-status') == (200, {'ready': True})
    assert e.call('/lineage/api/lineages')[0] == 200
    e.lineage.list_lineages.assert_called_once_with(principal)
    assert e.call('/lineage/api/node-message-counts?id=' + LINEAGE)[0] == 200
    e.lineage.get_node_message_counts.assert_called_once_with(LINEAGE, principal)
    assert e.call(f'/lineage/api/node-metadata?lineage_id={LINEAGE}&resident_id={RESIDENT}')[0] == 200
    e.lineage.get_lineage_node_metadata.assert_called_once_with(LINEAGE, RESIDENT, principal)
    assert e.call('/api/agent-workbench/residents')[0] == 200
    e.sidebar.assert_called_once_with(principal)
    assert e.call('/api/agent-workbench/residents/shortcuts')[0] == 200
    e.shortcuts.assert_called_once_with(principal)
    assert e.call('/api/agent-workbench/residents/navigation-target?id=' + RESIDENT)[0] == 200
    e.resident.resolve_resident_identity.assert_called_once_with(RESIDENT, principal)
    e.resident.get_resident_runtime_route.return_value = {'account_id': 'codex_fixture'}
    assert e.call('/api/agent-workbench/agent-backends?resident_id=' + RESIDENT)[0] == 200
    e.resident.get_resident_runtime_route.assert_called_once_with(RESIDENT, principal)
    assert e.call('/api/agent-workbench/resident-discovery/recommend/latest', 'POST')[0] == 200
    e.recommendation.latest_recommendation.assert_called_once_with(actor=principal)
    assert e.call('/api/agent-workbench/resident-discovery/contexts', 'POST')[0] == 200
    e.discovery.contexts.assert_called_once_with(actor=principal, resident_ids=[])
    e.workspace.get_workspace_link_file.return_value = {'kind': 'binary', 'name': 'release.zip'}
    assert e.call('/api/agent-workbench/link-file?path=release.zip&meta=1')[0] == 200
    e.workspace.get_workspace_link_file.assert_called_once_with('release.zip', principal, meta_only=True, allow_outside_roots=False)
    path = e.tmp_path / 'release.zip'
    path.write_bytes(b'PK\x03\x04fixture')
    e.workspace.resolve_workspace_link_file_path.return_value = path
    monkeypatch.setattr(e.workbench, 'resolved_path', lambda value, label: value)
    download = Mock(return_value=(200, 'download'))
    monkeypatch.setattr(e.workbench, 'download_file', download)
    assert e.call('/api/agent-workbench/link-file-raw?path=release.zip&download=1') == (200, 'download')
    e.workspace.resolve_workspace_link_file_path.assert_called_once_with('release.zip', allow_outside_roots=False, actor=principal)
    download.assert_called_once_with(e.request, path)
    e.workspace.resolve_workspace_link_file_path.side_effect = PermissionError('outside access scope')
    download.reset_mock()
    assert e.call('/api/agent-workbench/link-file-raw?path=private.zip&download=1')[0] == 403
    download.assert_not_called()
    e.lineage.get_node_message_counts.side_effect = PermissionError('outside lineage scope')
    assert e.call('/lineage/api/node-message-counts?id=' + LINEAGE)[0] == 403


def test_society_dependencies_reject_missing_identity_and_keep_terminal_boundary(entry):
    entry.actor[0] = None
    paths = DOCUMENTS + ['/api/users', '/lineage/api/service-status', '/lineage/api/lineages',
        '/lineage/api/node-message-counts?id=' + LINEAGE,
        f'/lineage/api/node-metadata?lineage_id={LINEAGE}&resident_id={RESIDENT}',
        '/api/agent-workbench/residents', '/api/agent-workbench/favorites',
        '/api/agent-workbench/agent-backends', '/api/agent-workbench/link-file?path=x',
        '/api/agent-workbench/link-file-raw?path=x&download=1', '/chat-workspace/api/session']
    for path in paths:
        assert entry.call(path)[0] == 401
    assert entry.call('/api/agent-workbench/resident-discovery/recommend/latest', 'POST')[0] == 401
    entry.sidebar.assert_not_called()
    entry.users.assert_not_called()
    assert entry.workspace.method_calls == []
    assert entry.lineage.method_calls == []
    entry.actor[0] = RESIDENT
    assert entry.call('/api/agent-workbench/local-terminal/read?session_id=x') is server.ROUTE_NOT_FOUND
