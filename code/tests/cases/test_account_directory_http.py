"""Management-only directory queries share existing registration permissions."""
from urllib.parse import urlencode, urlparse
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
    for name in ('first', 'second'):
        (tmp_path / name).mkdir()
    arguments = {'action': 'browse', 'path': str(tmp_path), 'limit': 1}
    status, page = query(urlencode(arguments), 'viewer')
    assert status == 200
    assert page['next_cursor']
    web.deny('viewer', '@shared', 'RegisterProviderAccount')
    arguments['cursor'] = page['next_cursor']
    monkeypatch.setattr(discovery, 'browse', lambda *a, **kw: pytest.fail('denied continuation read filesystem'))
    assert query(urlencode(arguments), 'viewer')[0] == 403


def test_browse_http_can_read_past_both_previous_cutoffs(web, tmp_path):
    tmp_path = tmp_path / "browser"
    tmp_path.mkdir()
    names = [f"directory-{n:04d}" for n in range(513)]
    for name in reversed(names):
        (tmp_path / name).mkdir()
    for n in range(2051):
        (tmp_path / f"data-{n:04d}").touch()
    (tmp_path / "alias").symlink_to(tmp_path / names[0], target_is_directory=True)

    arguments = {"action": "browse", "path": str(tmp_path), "limit": 500}
    actual = []
    for page_number in range(1, 5):
        status, page = query(urlencode(arguments))
        assert status == 200, page
        assert page["path"] == str(tmp_path)
        assert page["truncated"] == bool(page["next_cursor"])
        assert len(page["directories"]) <= 500
        actual.extend(page["directories"])
        if not page["next_cursor"]:
            break
        arguments["cursor"] = page["next_cursor"]
    else:
        pytest.fail("Directory listing did not reach the end")
    assert page_number > 1
    assert actual == [{"name": name, "path": str(tmp_path / name)} for name in names]


@pytest.mark.parametrize('value', [
    'action=other', 'action=discover&provider=codex&path=/tmp',
    'action=browse&path=/tmp&path=/etc', 'action=browse&unexpected=1',
    'action=discover&provider=codex&cursor=one', 'action=discover&provider=codex&limit=1',
    'action=browse&cursor=one&cursor=two', 'action=browse&limit=',
    'action=browse&limit=1.5', 'action=browse&limit=1&limit=2',
])
def test_malformed_queries_are_rejected_before_filesystem(web, monkeypatch, value):
    monkeypatch.setattr(discovery, 'browse', lambda *args, **kwargs: pytest.fail('unexpected browse'))
    monkeypatch.setattr(discovery, 'discover', lambda *args, **kwargs: pytest.fail('unexpected discovery'))
    assert query(value)[0] == 400


def test_no_new_directory_instructions_and_protected_route():
    from management_portal.hatg.catalog import instruction_catalog
    from management_portal.http_portal_session import PortalSessionHttp
    from management_portal.http_routes import RouteDefinition
    assert not {'DiscoverProviderAccountDirectories', 'BrowseProviderAccountDirectories'} & instruction_catalog().keys()
    routes = PortalSessionHttp.routes(object.__new__(PortalSessionHttp), lambda *a: RouteDefinition(*a))
    route = next(r for r in routes if r.path == '/api/management/account-directories')
    assert route.phase == 'protected' and route.method == 'GET'
