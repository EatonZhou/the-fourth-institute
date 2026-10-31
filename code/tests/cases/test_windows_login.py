"""Windows directory projection must not prevent anonymous HTML rendering.

:module: operations.deployment
:design: decision_d88306d0bd17
:profiles: local.fixture
:risk: no-external-side-effects
"""
import asyncio
import json
from pathlib import PureWindowsPath

import pytest

from management_common import portal_paths, site_config
from management_portal import asgi, page_bootstrap


async def get_page(route):
    events = []
    consumed = False

    async def receive():
        nonlocal consumed
        if consumed:
            await asyncio.Event().wait()
        consumed = True
        return {'type': 'http.request', 'body': b'', 'more_body': False}

    async def send(event):
        events.append(event)

    try:
        await asgi.app({'type': 'http', 'asgi': {'version': '3.0'},
            'http_version': '1.1', 'method': 'GET', 'scheme': 'http',
            'path': route, 'raw_path': route.encode(), 'root_path': '',
            'query_string': b'', 'headers': [], 'client': ('127.0.0.1', 12345),
            'server': ('127.0.0.1', 18086)}, receive, send)
    except Exception:
        # ASGI error middleware sends a 500 then re-raises for server logging.
        if not any(event['type'] == 'http.response.start' for event in events):
            raise
    start = next(event for event in events if event['type'] == 'http.response.start')
    body = b''.join(event.get('body', b'') for event in events if event['type'] == 'http.response.body')
    return start['status'], body.decode()


@pytest.fixture
def windows_paths(monkeypatch):
    root = PureWindowsPath('C:/Users/测试 User/AppData/Local/TheFourthInstitute/instance/example_instance')
    values = {key: str(root / 'data' / key) for key in portal_paths.KEYS}
    monkeypatch.setattr(portal_paths, 'PORTAL_ROOT', root)
    monkeypatch.setattr(portal_paths, 'path_values', lambda: values)
    # Exercise the native Windows path flavour and the production anchoring
    # policy without requiring a Windows filesystem on the test host.
    monkeypatch.setattr(portal_paths, 'resolve_directory',
                        lambda value: portal_paths._anchor_directory(PureWindowsPath(value), root))
    monkeypatch.setattr(site_config, 'read_site_config', lambda: {})
    return root, values


@pytest.mark.parametrize('style', ['backslash', 'forward-slash', 'unc'])
def test_login_and_enrollment_render_with_windows_roots(windows_paths, style):
    root, values = windows_paths
    if style == 'forward-slash':
        values.update({key: PureWindowsPath(value).as_posix() for key, value in values.items()})
    elif style == 'unc':
        values.update({key: str(PureWindowsPath('//server/share/测试 User') / key) for key in values})

    async def exercise():
        for route in ('/login', '/join'):
            status, text = await get_page(route)
            assert status == 200, (route, text)
            assert 'id="portalSiteScript"' in text
            expected = json.dumps(PureWindowsPath(values['share_root']).as_posix())
            assert expected in text
            assert '"secrets_dir"' not in text
    asyncio.run(exercise())
    assert page_bootstrap._public_paths()['test_results_root'] == (root / 'test-results').as_posix()


@pytest.mark.parametrize('value', [r'C:\data\..\private', 'C:/data/../private',
                                 'C:/', '//server/share/', r'C:relative', r'\rooted',
                                 'C:/data/secret\x00', 'C:/data/secret\n'])
def test_windows_projection_still_rejects_unsafe_roots(windows_paths, value):
    _, values = windows_paths
    values['share_root'] = value
    with pytest.raises(ValueError, match='Invalid public directory: share_root'):
        page_bootstrap._public_paths()
