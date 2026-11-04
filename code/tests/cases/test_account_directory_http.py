"""Management-only directory queries share existing registration permissions."""
from urllib.parse import urlparse
import copy

import pytest

from management_portal import http_account_directories as http, permission_system
from management_portal.execution import account_directory_discovery as discovery, account_registrations
from management_portal.permission_system.scheme.repository import builtin_documents
from tests.cases.permission_test_support import enable, user_fact


@pytest.fixture
def web(monkeypatch):
    permissions = enable(monkeypatch)
    permissions.users('registrar', 'viewer')
    permissions.grant('registrar', '@shared', 'RegisterProviderAccount')
    permissions.grant('viewer', '@shared', '@viewer')
    monkeypatch.setattr(http.http_responses, '_serve_json', lambda request, status, body: (status, body))
    monkeypatch.setattr(http.http_responses, '_serve_exception_json', lambda request, exc: (400, {'error': str(exc)}))
    return permissions


def query(value, actor='registrar'):
    return http.account_directories(None, urlparse('/api/management/account-directories?' + value), actor)


def test_http_discovery_excludes_all_registrations_and_requires_registration_permission(web, monkeypatch):
    calls = []
    monkeypatch.setattr(account_registrations, 'registrations', lambda: [{'config': {'account_dir': '/host/used'}}, {'config': {}}, {'config': {'account_dir': '/host/other'}}])
    monkeypatch.setattr(discovery, 'discover', lambda provider, **kw: calls.append((provider, kw)) or {'candidates': [], 'truncated': False, 'warnings': []})
    assert query('action=discover&provider=codex')[0] == 200
    assert calls == [('codex', {'excluded': ['/host/used', '/host/other']})]
    for actor in ('viewer', ''):
        assert query('action=discover&provider=codex', actor)[0] == 403
    assert len(calls) == 1
    web.deny('registrar', '@shared', 'RegisterProviderAccount')
    assert query('action=discover&provider=codex')[0] == 403


def test_existing_custom_scheme_and_deny_apply_without_new_instruction(web, monkeypatch, tmp_path):
    implementation = web.implementation
    document = copy.deepcopy(builtin_documents()['default'])
    document.update(scheme_id='directory_http', title='directory HTTP fixture')
    document['instructions']['RegisterProviderAccount'] = {'a': '@viewer', 'r': '@shared'}
    implementation.manage('CreatePermissionScheme', {'scheme_id': 'directory_http', 'rules': document}, actor='portal', enforce=False, collect_facts=lambda: [user_fact('viewer')])
    implementation.schemes.activate('directory_http', actor='portal')
    implementation.regenerate([user_fact('viewer')]); implementation.forget()
    web.grant('viewer', '@shared', 'RegisterProviderAccount')
    assert query('action=browse&path=' + str(tmp_path), 'viewer')[0] == 200
    web.deny('viewer', '@shared', 'RegisterProviderAccount')
    assert query('action=browse&path=' + str(tmp_path), 'viewer')[0] == 403


@pytest.mark.parametrize('value', ['action=other', 'action=discover&provider=codex&path=/tmp', 'action=browse&path=/tmp&path=/etc', 'action=browse&unexpected=1'])
def test_malformed_queries_are_rejected_before_filesystem(web, monkeypatch, value):
    monkeypatch.setattr(discovery, 'browse', lambda *args: pytest.fail('unexpected browse'))
    monkeypatch.setattr(discovery, 'discover', lambda *args: pytest.fail('unexpected discovery'))
    assert query(value)[0] == 400


def test_no_new_directory_instructions_and_protected_route():
    from management_portal.hatg.catalog import instruction_catalog
    from management_portal.http_portal_session import PortalSessionHttp
    from management_portal.http_routes import RouteDefinition
    assert not {'DiscoverProviderAccountDirectories', 'BrowseProviderAccountDirectories'} & instruction_catalog().keys()
    routes = PortalSessionHttp.routes(object.__new__(PortalSessionHttp), lambda *a: RouteDefinition(*a))
    route = next(r for r in routes if r.path == '/api/management/account-directories')
    assert route.phase == 'protected' and route.method == 'GET'
