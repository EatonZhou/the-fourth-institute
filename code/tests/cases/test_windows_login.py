"""The instance directory projection must not prevent anonymous HTML rendering.

:module: operations.deployment
:design: decision_d88306d0bd17
:profiles: local.fixture
:risk: no-external-side-effects
"""
import asyncio
import json

import pytest

from management_common.instance_manifest import AREAS, load
from management_portal import asgi, page_bootstrap
from tests.cases.instance_test_support import select_instance


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


@pytest.mark.parametrize('name', ['plain', '测试 User with spaces'])
def test_login_and_enrollment_render_with_the_instance_areas(tmp_path, monkeypatch, name):
    select_instance(monkeypatch, tmp_path / name / 'instance')
    instance = load()

    async def exercise():
        for route in ('/login', '/join'):
            status, text = await get_page(route)
            assert status == 200, (route, text)
            assert 'id="portalSiteScript"' in text
            for area in AREAS:
                assert json.dumps(instance.area(area).as_posix(), ensure_ascii=True) in text
            assert '"secrets"' not in text and '"database"' not in text
    asyncio.run(exercise())
    assert page_bootstrap._public_paths()['test_results_root'] == (instance.root / 'cache/test-results').as_posix()
