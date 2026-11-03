"""Repo documents and template APIs preserve authenticated User/Resident identity."""
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import urlparse

import pytest

from management_portal import http_auth, http_documents, http_repo, http_responses
from management_portal.http_routes import RouteDispatcher, ROUTE_NOT_FOUND

REPO = 'repo_0123456789ab'
RESIDENT = 'resident_0123456789ab'


@pytest.fixture
def entry(monkeypatch):
    actor = [RESIDENT]
    monkeypatch.setattr(http_auth, '_get_page_api_actor', lambda request: actor[0])
    monkeypatch.setattr(http_responses, '_serve_json', lambda request, status, value: (status, value))
    monkeypatch.setattr(http_responses, '_serve_http_error', lambda request, status, value: (status, value))
    monkeypatch.setattr(http_documents, '_serve_authenticated_document', lambda request, status, body: (status, body))
    monkeypatch.setattr(http_repo.http_request_body, '_read_request_json', lambda request: {'message': 'update'})
    required = Mock(return_value={})
    monkeypatch.setattr(http_repo.web_resources, '_require_resource', required)
    dispatch = RouteDispatcher(http_repo.routes(None))
    request = SimpleNamespace(headers={'Authorization': 'Bearer fixture'})

    def call(path, method='GET'):
        parsed = urlparse(path)
        result = dispatch.dispatch(method, parsed.path, request, parsed, '', phase='dual-auth')
        assert not getattr(request, '_portal_authenticated_actor', None)
        return result
    return SimpleNamespace(actor=actor, call=call, request=request, required=required)


@pytest.mark.parametrize('actor', [RESIDENT, 'human-reader'])
def test_document_template_read_and_commit_preserve_same_principal(entry, monkeypatch, actor):
    entry.actor[0] = actor
    for path in ('/resource/repo', '/resource/repo/'):
        assert entry.call(path)[0] == 200
    read = Mock(return_value={'content': 'source'})
    monkeypatch.setattr(http_repo.repo_workspace_files, 'read_repo_file', read)
    assert entry.call(f'/api/repos/{REPO}/workspace/file?path=README.md') == (200, {'content': 'source'})
    entry.required.assert_called_with(REPO, actor)
    read.assert_called_once_with(REPO, 'README.md')
    write_permission = Mock(return_value=True)
    monkeypatch.setattr(http_repo.permission_system, 'allows', write_permission)
    commit = Mock(return_value={'committed': True})
    monkeypatch.setattr(http_repo.repo_workspace_operations, 'commit_repo_changes', commit)
    monkeypatch.setattr(http_repo.web_resources, '_notify_resource_index', Mock())
    assert entry.call(f'/api/repos/{REPO}/workspace/commit', 'POST') == (201, {'committed': True})
    write_permission.assert_called_once_with(actor, 'UpdateResource', {'resource_id': REPO})
    commit.assert_called_once_with(REPO, 'update', description='', actor=actor)


def test_reader_cannot_write_or_read_saved_git_credentials(entry, monkeypatch):
    monkeypatch.setattr(http_repo.permission_system, 'allows', lambda *args: False)
    mutation = Mock()
    credentials = Mock()
    monkeypatch.setattr(http_repo.repo_workspace_operations, 'commit_repo_changes', mutation)
    monkeypatch.setattr(http_repo.repo_workspace_network, 'list_repo_credentials', credentials)
    assert entry.call(f'/api/repos/{REPO}/workspace/commit', 'POST')[0] == 403
    assert entry.call(f'/api/repos/{REPO}/workspace/credentials')[0] == 403
    mutation.assert_not_called()
    credentials.assert_not_called()


def test_denied_read_does_not_open_checkout(entry, monkeypatch):
    entry.required.side_effect = PermissionError('denied')
    read = Mock()
    monkeypatch.setattr(http_repo.repo_workspace_overview, 'repo_overview', read)
    assert entry.call(f'/api/repos/{REPO}/workspace')[0] == 403
    read.assert_not_called()


def test_invalid_identity_fails_before_document_or_template_and_unknown_keeps_404(entry):
    entry.actor[0] = None
    assert entry.call('/resource/repo')[0] == 401
    assert entry.call(f'/api/repos/{REPO}/workspace')[0] == 401
    entry.required.assert_not_called()
    entry.actor[0] = RESIDENT
    assert entry.call(f'/api/repos/{REPO}/workspace/unknown') is ROUTE_NOT_FOUND


def test_automation_list_filters_using_current_resident(entry, monkeypatch):
    query = Mock(return_value={'resources': [{'resource_id': REPO}]})
    monkeypatch.setattr(http_repo, 'get_resource_service', lambda: SimpleNamespace(query=query))
    overview = Mock(return_value={'status': 'ok'})
    monkeypatch.setattr(http_repo.repo_workspace_overview, 'repo_automation_status', overview)
    assert entry.call('/api/repos/automation/status?repo_id=' + REPO + ',repo_aaaaaaaaaaaa')[0] == 200
    assert query.call_args.kwargs['actor'] == RESIDENT
    overview.assert_called_once_with([REPO])


def test_resource_address_redirect_preserves_resident(entry, monkeypatch):
    from management_portal import serve_management_portal as server
    location = Mock(return_value='/resource/repo?item=' + REPO)
    monkeypatch.setattr(server._PORTAL_PAGES_HTTP, 'resource_location', location)
    request = entry.request
    request.send_response = Mock()
    request.send_header = Mock()
    request.end_headers = Mock()
    parsed = urlparse('/r/' + REPO)
    server._PORTAL_HTTP_ROUTES.dispatch('GET', parsed.path, request, parsed, '', phase='dual-auth')
    location.assert_called_once_with(REPO, RESIDENT)
    request.send_header.assert_called_once_with('Location', '/resource/repo?item=' + REPO)
    assert request._portal_authenticated_actor is None


@pytest.mark.parametrize('actor', [RESIDENT, 'human-reader'])
def test_repo_catalog_dependencies_preserve_principal(entry, monkeypatch, actor):
    from management_portal import serve_management_portal as server, semantic_search_jobs
    entry.actor[0] = actor
    owner = server._RESOURCE_HTTP
    service = Mock()
    service.query.return_value = {'resources': [{'resource_id': REPO}]}
    service.get.return_value = {'resource_id': REPO}
    monkeypatch.setattr(owner, 'respond', lambda request, status, value: (status, value))
    monkeypatch.setattr(owner, 'resource_error', lambda request, error: (403, str(error)))
    monkeypatch.setattr(owner, 'resource_service', lambda: service)
    monkeypatch.setattr(owner, 'status_payload', lambda: {'ok': True})
    monkeypatch.setattr(owner, 'read_json', lambda request: {'resource_scope': 'repo', 'adapters': ['repo']})
    monkeypatch.setattr(owner, 'notify_index', Mock())
    searches = Mock(return_value={'searches': []})
    monkeypatch.setattr(semantic_search_jobs, 'list_searches', searches)
    operations = {}
    for name in ('start', 'refresh', 'delete'):
        operations[name] = Mock(return_value={'search_id': 'search_0123456789ab'})
        monkeypatch.setattr(semantic_search_jobs, name + '_search', operations[name])

    def call(path, method='GET'):
        parsed = urlparse(path)
        result = server._PORTAL_HTTP_ROUTES.dispatch(method, parsed.path, entry.request, parsed, '', phase='dual-auth')
        assert not getattr(entry.request, '_portal_authenticated_actor', None)
        return result

    paths = ['/api/resource-index/query?resource_type=repo',
             '/api/resource-index/suggest?resource_type=repo',
             '/api/resource-index/resources/' + REPO,
             '/api/resource-index/status', '/resource/api/service-status',
             '/api/resource-semantic-searches?resource_scope=repo']
    for path in paths:
        assert call(path)[0] == 200
    assert service.query.call_args.kwargs['actor'] == actor
    assert service.suggest.call_args.kwargs['actor'] == actor
    service.get.assert_called_once_with(REPO, actor=actor)
    searches.assert_called_once_with('repo', actor)
    for name, operation in operations.items():
        assert call('/api/resource-semantic-search/' + name, 'POST')[0] == 200
        assert operation.call_args.args[-1] == actor
    assert call('/api/resource-index/refresh', 'POST')[0] == 202
    service.refresh.assert_called_once_with(adapters=['repo'], path='', wait=False, timeout=180.0)

    service.get.side_effect = PermissionError('outside caller scope')
    assert call('/api/resource-index/resources/' + REPO)[0] == 403
    entry.actor[0] = None
    service.reset_mock()
    searches.reset_mock()
    for path in paths:
        assert call(path)[0] == 401
    assert call('/api/resource-index/refresh', 'POST')[0] == 401
    for name, operation in operations.items():
        operation.reset_mock()
        assert call('/api/resource-semantic-search/' + name, 'POST')[0] == 401
        operation.assert_not_called()
    service.assert_not_called()
    assert service.method_calls == []
    searches.assert_not_called()
