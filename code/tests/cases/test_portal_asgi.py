from __future__ import annotations

import asyncio
import io
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from urllib.parse import urlsplit
from unittest import mock

import pytest

from starlette.requests import Request

from management_portal import asgi, http_ui_state
from management_portal import permission_system
from management_portal.notifications import AsyncNotificationFanout, NotificationHub


@pytest.mark.parametrize('scope,requires_snapshot', [('identity', True), ('runtime', False)])
def test_unreadable_resident_identity_revalidates_open_editors_without_disclosing_id(scope, requires_snapshot):
    """不可读 Resident 的身份变更要求快照重验而运行变更不要求，两者都不泄露 ID"""
    resident_id = 'resident_0123456789ab'
    notification = SimpleNamespace(subject=f'portal.resident.changed.{resident_id}.{scope}', payload={})
    with mock.patch.object(permission_system, 'readable_ids', return_value=set()):
        payload = asgi.portal._portal_change_payload('viewer', [notification], {'resident'})
    assert payload['snapshot_required'] is requires_snapshot
    assert resident_id not in json.dumps(payload)


def _request(
    path: str,
    *,
    method: str = "GET",
    body: bytes = b"",
    headers: dict[str, str] | None = None,
    app=None,
) -> Request:
    parsed = urlsplit(path)
    sent = False

    async def receive() -> dict:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {
            "type": "http.request",
            "body": body,
            "more_body": False,
        }

    scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": parsed.path,
            "raw_path": parsed.path.encode("ascii"),
            "query_string": parsed.query.encode("ascii"),
            "headers": [
                (key.lower().encode("latin-1"), value.encode("latin-1"))
                for key, value in (headers or {}).items()
            ],
            "client": ("127.0.0.1", 12345),
            "server": ("127.0.0.1", 18086),
    }
    if app is not None:
        scope["app"] = app
    return Request(scope, receive)


async def _response_body(response) -> bytes:
    return b"".join([chunk async for chunk in response.body_iterator])


@pytest.mark.parametrize('custom_executor', [False, True])
@pytest.mark.parametrize('handler_error', [False, True])
def test_bridge_reports_separate_queue_and_handler_timing(custom_executor, handler_error):
    """每个响应携带 Web 排队和处理墙钟耗时，异常不泄露正文或丢失计时。"""
    async def exercise():
        executor = ThreadPoolExecutor(max_workers=1) if custom_executor else None
        ticks = iter([10.0, 12.0, 12.25])
        app = SimpleNamespace(state=SimpleNamespace(http_handler_executor=executor))

        def handler(request, body):
            assert request.url.path == '/api/timing-probe'
            if handler_error:
                raise RuntimeError('private-handler-detail')
            capture = asgi._ResponseCapture()
            capture.write(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nX-Contract: preserved\r\n\r\n{"ok":true}')
            capture.finish()
            return capture

        try:
            with mock.patch.object(asgi, 'time', SimpleNamespace(perf_counter=lambda: next(ticks))), \
                 mock.patch.object(asgi, '_run_sync_handler', side_effect=handler):
                response = await asgi.portal_http(_request('/api/timing-probe', app=app))
            assert response.headers['server-timing'] == 'portal_queue;dur=2000.000, portal_handler;dur=250.000'
            if handler_error:
                assert response.status_code == 500
                assert b'private-handler-detail' not in response.body
            else:
                assert response.status_code == 200
                assert response.headers['x-contract'] == 'preserved'
                assert json.loads(await _response_body(response)) == {'ok': True}
        finally:
            if executor:
                executor.shutdown(wait=True)

    asyncio.run(exercise())


def test_static_assets_remain_available_when_domain_http_pool_is_busy(isolated_portal_case):
    """业务 HTTP 线程池阻塞时脚本仍能加载，普通 API 保留原执行容量"""
    async def exercise():
        loop = asyncio.get_running_loop()
        handlers = ThreadPoolExecutor(max_workers=1)
        assets = ThreadPoolExecutor(max_workers=1)
        started, release = threading.Event(), threading.Event()

        def occupy_handler():
            started.set()
            release.wait()

        busy = loop.run_in_executor(handlers, occupy_handler)
        ordinary = None
        app = SimpleNamespace(state=SimpleNamespace(
            http_handler_executor=handlers, static_handler_executor=assets,
        ))
        try:
            while not started.is_set():
                await asyncio.sleep(0.001)
            with mock.patch.object(asgi.portal, '_portal_health_payload', return_value={'ok': True}):
                ordinary = asyncio.create_task(asgi.portal_http(_request('/api/health', app=app)))
                for method in ('GET', 'HEAD'):
                    response = await asyncio.wait_for(
                        asgi.portal_http(_request('/static/portal_events.js', method=method, app=app)),
                        timeout=2,
                    )
                    body = await _response_body(response)
                    assert response.status_code == 200
                    if method == 'GET':
                        assert b'PortalEvents' in body
                    assert not busy.done()
                    assert not ordinary.done(), 'ordinary APIs must not consume the asset pool'
                release.set()
                await busy
                response = await asyncio.wait_for(ordinary, timeout=2)
                assert json.loads(await _response_body(response)) == {'ok': True}
        finally:
            release.set()
            await busy
            if ordinary is not None and not ordinary.done():
                ordinary.cancel()
                await asyncio.gather(ordinary, return_exceptions=True)
            handlers.shutdown(wait=True, cancel_futures=True)
            assets.shutdown(wait=True, cancel_futures=True)

    with isolated_portal_case():
        asyncio.run(exercise())


def test_asgi_errors_use_portal_json_contract():
    """ASGI 的 413、403 和 405 错误使用 Portal JSON 错误格式"""
    async def exercise():
        with mock.patch.object(asgi, "MAX_REQUEST_BODY_BYTES", 2):
            too_large = await asgi.portal_http(_request("/api/upload", method="POST", body=b"abc"))
        denied = await asgi.publish_restart_notice(
            _request("/api/internal/restart-notice", method="POST", headers={"x-forwarded-for": "203.0.113.10"})
        )
        for response, status, code in (
            (too_large, 413, "operation.too_large"),
            (denied, 403, "operation.forbidden"),
        ):
            payload = json.loads(response.body)
            assert response.status_code == status
            assert payload["code"] == code
            assert payload["message"] == payload["error"]
            assert payload["http_status"] == status

    asyncio.run(exercise())
    capture = asgi._run_sync_handler(_request("/api/unknown", method="BREW"), io.BytesIO())
    try:
        assert capture.status == 405
        assert json.loads(capture.body.read())["code"] == "operation.method_not_allowed"
    finally:
        capture.body.close()


def test_instruction_execution_continues_while_http_handlers_are_busy():
    """HTTP 处理线程占满时，指令执行仍在独立线程池中完成"""
    async def exercise():
        loop = asyncio.get_running_loop()
        http_executor = ThreadPoolExecutor(max_workers=1)
        instruction_executor = ThreadPoolExecutor(max_workers=1)
        started, release = threading.Event(), threading.Event()

        def blocking_http_handler():
            started.set()
            release.wait()

        busy = loop.run_in_executor(http_executor, blocking_http_handler)
        app = SimpleNamespace(state=SimpleNamespace(instruction_executor=instruction_executor))
        request = _request(
            '/api/instructions/execute', method='POST',
            body=json.dumps({'instruction': 'CreateUpload', 'arguments': {'purpose': 'chat_attachment'}}).encode(),
            app=app,
        )
        try:
            while not started.is_set():
                await asyncio.sleep(0.001)
            with mock.patch.object(asgi, '_instruction_principal', return_value=('alice', 'web')), \
                 mock.patch.object(asgi, '_same_origin', return_value=True), \
                 mock.patch('management_portal.hatg.routing.execute', return_value=SimpleNamespace(
                     status='committed', to_payload=lambda: {'status': 'committed', 'result': {'upload_id': 'test'}},
                 )):
                response = await asyncio.wait_for(asgi.instruction_execute(request), timeout=1)
            assert response.status_code == 200
            assert json.loads(response.body)['result']['upload_id'] == 'test'
            assert not busy.done()
        finally:
            release.set()
            await busy
            http_executor.shutdown(wait=True, cancel_futures=True)
            instruction_executor.shutdown(wait=True, cancel_futures=True)

    asyncio.run(exercise())


def test_asgi_bridge_preserves_json_response_contracts(subtests, isolated_portal_case):
    """ASGI 桥接用独立线程池分块输出响应，保持 JSON、HEAD、登录静态资源和事件流协议"""
    with subtests.test(contract='completed_response_progresses_while_handler_executor_is_full'), isolated_portal_case():
        async def saturated_handlers():
            loop = asyncio.get_running_loop()
            executor = ThreadPoolExecutor(max_workers=1)
            loop.set_default_executor(executor)
            started, release = threading.Event(), threading.Event()
            def blocking_handler():
                started.set()
                release.wait()
            busy = loop.run_in_executor(None, blocking_handler)
            while not started.is_set():
                await asyncio.sleep(0.001)
            payload = b'completed response' * 40000
            body = io.BytesIO(payload)
            chunks = asgi._body_chunks(body)
            try:
                first = await asyncio.wait_for(anext(chunks), timeout=1)
                assert first == payload[:256 * 1024]
                assert not busy.done()
                remainder = b''.join([chunk async for chunk in chunks])
                assert first + remainder == payload
                assert body.closed
            finally:
                release.set()
                await busy
                await chunks.aclose()
        asyncio.run(saturated_handlers())
    with subtests.test(contract='http_handlers_use_their_bounded_executor'), isolated_portal_case():
        async def isolated_handler_pool():
            loop = asyncio.get_running_loop()
            default_executor = ThreadPoolExecutor(max_workers=1)
            handler_executor = ThreadPoolExecutor(max_workers=1)
            loop.set_default_executor(default_executor)
            started, release = threading.Event(), threading.Event()

            def occupy_default_executor():
                started.set()
                release.wait()

            busy = loop.run_in_executor(None, occupy_default_executor)
            while not started.is_set():
                await asyncio.sleep(0.001)
            app = SimpleNamespace(
                state=SimpleNamespace(http_handler_executor=handler_executor)
            )
            try:
                with mock.patch.object(
                    asgi.portal,
                    '_portal_health_payload',
                    return_value={'ok': True, 'pool': 'handler'},
                ):
                    response = await asyncio.wait_for(
                        asgi.portal_http(_request('/api/health', app=app)),
                        timeout=1,
                    )
                    body = await _response_body(response)
                assert json.loads(body) == {'ok': True, 'pool': 'handler'}
                assert not busy.done()
            finally:
                release.set()
                await busy
                handler_executor.shutdown(wait=True, cancel_futures=True)

        asyncio.run(isolated_handler_pool())
    with subtests.test(contract='test_asgi_bridge_preserves_json_response'), isolated_portal_case() as case_path:
        request = _request('/api/health')
        with mock.patch.object(asgi.portal, '_portal_health_payload', return_value={'ok': True, 'transport': 'asgi'}):
            response = asyncio.run(asgi.portal_http(request))
            body = asyncio.run(_response_body(response))
        assert response.status_code == 200
        assert response.headers['content-type'] == 'application/json; charset=utf-8'
        assert b'"transport": "asgi"' in body
    with subtests.test(contract='test_asgi_bridge_preserves_head_authentication_without_a_body'), isolated_portal_case() as case_path:
        request = _request('/', method='HEAD')
        with mock.patch.object(asgi.portal, '_check_session', return_value=False):
            response = asyncio.run(asgi.portal_http(request))
            body = asyncio.run(_response_body(response))
        assert response.status_code == 302
        assert response.headers['location'] == '/login'
        assert 'last-modified' not in response.headers
        assert body == b''
    with subtests.test(contract='test_login_static_dependencies_are_available_without_a_session'), isolated_portal_case() as case_path:
        login_response = asyncio.run(asgi.portal_http(_request('/login')))
        login_body = asyncio.run(_response_body(login_response)).decode('utf-8')
        asset_paths = {path for path in re.findall('(?:href|src)="([^"]+)"', login_body) if path.startswith('/static/')}
        assert asset_paths
        for path in asset_paths:
            response = asyncio.run(asgi.portal_http(_request(path)))
            asyncio.run(_response_body(response))
            assert response.status_code == 200, path
            assert not response.headers['content-type'].startswith('text/html'), path
    with subtests.test(contract='test_asgi_events_wait_asynchronously_and_preserve_protocol'), isolated_portal_case() as case_path:
        async def exercise() -> None:
            hub = NotificationHub(max_items=32)
            fanout = AsyncNotificationFanout(hub)
            fanout.start()
            executor = ThreadPoolExecutor(max_workers=1)
            app = SimpleNamespace(state=SimpleNamespace(notification_fanout=fanout, sse_payload_executor=executor, realtime_lifecycle=asgi.RealtimeConnectionLifecycle()))
            request = _request('/api/events', app=app)
            with mock.patch.object(asgi.portal, '_get_session_username', return_value='alice'), mock.patch.object(asgi, 'ensure_notification_listener'), mock.patch.object(asgi, 'ensure_knowledge_archive_projection_watcher'), mock.patch.object(hub, 'wait', side_effect=AssertionError('sync wait used')), mock.patch.object(asgi.portal, '_portal_change_payload', return_value={'domains': ['resource'], 'changed': {'resource': ['skill-one']}, 'snapshot_required': False}):
                response = await asgi.portal_events(request)
                iterator = response.body_iterator
                assert await anext(iterator) == b'retry: 1500\n\n'
                assert b'event: portal-ready' in await anext(iterator)
                hub.publish('portal.resource.changed.skill.skill-one', {})
                change = await asyncio.wait_for(anext(iterator), timeout=1)
                assert b'event: portal-change' in change
                assert b'"domains":["resource"]' in change
                await iterator.aclose()
            fanout.close()
            executor.shutdown(wait=False, cancel_futures=True)
        asyncio.run(exercise())
    with subtests.test(contract='test_asgi_realtime_drain_closes_existing_and_rejects_new_streams'), isolated_portal_case() as case_path:
        async def exercise() -> None:
            hub = NotificationHub(max_items=32)
            fanout = AsyncNotificationFanout(hub)
            fanout.start()
            executor = ThreadPoolExecutor(max_workers=1)
            app = SimpleNamespace(state=SimpleNamespace(notification_fanout=fanout, sse_payload_executor=executor, realtime_lifecycle=asgi.RealtimeConnectionLifecycle()))
            subscription = fanout.subscribe()
            request = _request('/api/internal/drain-realtime', method='POST', app=app)
            response = await asgi.drain_realtime(request)
            assert response.status_code == 200
            assert app.state.realtime_lifecycle.accepting is False
            batch = await asyncio.wait_for(subscription.wait(10), timeout=0.1)
            assert batch is not None and batch.closed is True
            with mock.patch.object(asgi.portal, '_get_session_username', return_value='alice'):
                rejected = await asgi.portal_events(_request('/api/events', app=app))
            assert rejected.status_code == 503
            executor.shutdown(wait=False, cancel_futures=True)
        asyncio.run(exercise())
    with subtests.test(contract='test_asgi_realtime_drain_rejects_stream_that_has_not_started_iterating'), isolated_portal_case() as case_path:
        async def exercise() -> None:
            hub = NotificationHub(max_items=32)
            fanout = AsyncNotificationFanout(hub)
            fanout.start()
            executor = ThreadPoolExecutor(max_workers=1)
            lifecycle = asgi.RealtimeConnectionLifecycle()
            app = SimpleNamespace(state=SimpleNamespace(notification_fanout=fanout, sse_payload_executor=executor, realtime_lifecycle=lifecycle))
            with mock.patch.object(asgi.portal, '_get_session_username', return_value='alice'), mock.patch.object(asgi, 'ensure_notification_listener'), mock.patch.object(asgi, 'ensure_knowledge_archive_projection_watcher'):
                response = await asgi.portal_events(_request('/api/events', app=app))
                drained = await asgi.drain_realtime(_request('/api/internal/drain-realtime', method='POST', app=app))
                with pytest.raises(StopAsyncIteration):
                    await anext(response.body_iterator)
            assert drained.status_code == 200
            assert lifecycle.active_connections == 0
            executor.shutdown(wait=False, cancel_futures=True)
        asyncio.run(exercise())


def test_usage_routes_use_authenticated_actor(subtests, isolated_portal_case):
    """用量路由忽略 actor 参数只返回登录用户的数据，未登录时不查询"""
    for method, path in [
    ("GET", "/api/agent-status/usage?actor=other-user"),
    ("POST", "/api/agent-status/usage/refresh?actor=other-user"),
]:
        for authenticated in [True, False]:
            with subtests.test(method=method, path=path, authenticated=authenticated), isolated_portal_case() as case_path:
                snapshot = {"snapshot_version": 7, "actor": "alice", "refreshing": False}
                with (
                    mock.patch.object(asgi.portal, "_check_session", return_value=authenticated),
                    mock.patch.object(asgi.portal, "_get_session_username", return_value="alice"),
                    mock.patch.object(asgi.portal.agent_status_backend, "get_token_usage_summary", return_value=snapshot) as usage,
                ):
                    response = asyncio.run(asgi.portal_http(_request(path, method=method)))
                    body = asyncio.run(_response_body(response))

                if authenticated:
                    assert response.status_code == 200
                    assert json.loads(body) == snapshot
                    if method == "POST":
                        usage.assert_called_once_with("alice", force_refresh=True)
                    else:
                        usage.assert_called_once_with("alice")
                else:
                    assert response.status_code in {302, 401}
                    usage.assert_not_called()


def test_asgi_bridge_preserves_proxy_validated_scheme_for_origin_checks_contracts(monkeypatch, subtests, isolated_portal_case):
    """ASGI 桥接按代理确认的 https 协议做同源检查，超大请求在分发前返回 413"""
    with subtests.test(contract='test_asgi_bridge_preserves_proxy_validated_scheme_for_origin_checks'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            monkeypatch.setattr(asgi.portal, '_check_session', lambda _handler: True)
            monkeypatch.setattr(asgi.portal, '_get_session_username', lambda _handler: 'tester')
            monkeypatch.setattr(asgi.portal.local_terminal_backend, 'open_terminal', lambda *_args, **_kwargs: {'ok': True})
            payload = json.dumps({'device_id': 'device', 'session_id': 'session'}).encode()
            request = _request('/api/terminal/start', method='POST', body=payload)
            request.scope.update({'scheme': 'https', 'client': ('192.168.125.167', 54321), 'headers': [(b'host', b'192.168.115.204'), (b'origin', b'https://192.168.115.204'), (b'x-forwarded-proto', b'https'), (b'content-type', b'application/json'), (b'content-length', str(len(payload)).encode())]})
            response = asyncio.run(asgi.portal_http(request))
            body = asyncio.run(_response_body(response))
            assert response.status_code == 200
            assert json.loads(body) == {'ok': True}
    with subtests.test(contract='test_asgi_bridge_rejects_oversized_request_before_dispatch'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            monkeypatch.setattr(asgi, 'MAX_REQUEST_BODY_BYTES', 4)
            request = _request('/login', method='POST', body=b'12345')
            response = asyncio.run(asgi.portal_http(request))
            assert response.status_code == 413


def test_asgi_events_replace_a_stalled_payload_with_snapshot_recovery() -> None:
    """事件流计算变更负载超时时，改发要求快照恢复的 portal-change 事件"""
    async def exercise() -> None:
        hub = NotificationHub(max_items=32)
        fanout = AsyncNotificationFanout(hub)
        fanout.start()
        executor = ThreadPoolExecutor(max_workers=1)
        app = SimpleNamespace(
            state=SimpleNamespace(
                notification_fanout=fanout,
                sse_payload_executor=executor,
                realtime_lifecycle=asgi.RealtimeConnectionLifecycle(),
            )
        )
        request = _request("/api/events?resident_ids=resident_late", app=app)
        release_payload = threading.Event()

        def stalled_payload(*_args) -> dict:
            release_payload.wait(timeout=1)
            return {
                "domains": ["resident"],
                "changed": {"resident": ["resident_late"]},
                "snapshot_required": False,
            }

        with (
            mock.patch.object(asgi.portal, "_get_session_username", return_value="alice"),
            mock.patch.object(asgi, "ensure_notification_listener"),
            mock.patch.object(asgi, "ensure_knowledge_archive_projection_watcher"),
            mock.patch.object(asgi.portal, "_portal_change_payload", side_effect=stalled_payload),
            mock.patch.object(asgi, "SSE_PAYLOAD_TIMEOUT_SECONDS", 0.01),
        ):
            response = await asgi.portal_events(request)
            iterator = response.body_iterator
            assert await anext(iterator) == b"retry: 1500\n\n"
            assert b"event: portal-ready" in await anext(iterator)
            hub.publish("portal.resident.changed.resident_late.conversation", {})
            change = await asyncio.wait_for(anext(iterator), timeout=1)
            assert b"event: portal-change" in change
            assert b'"snapshot_required":true' in change
            release_payload.set()
            await iterator.aclose()
        fanout.close()
        executor.shutdown(wait=True, cancel_futures=True)

    asyncio.run(exercise())


def test_portal_routes_and_api_errors_share_http_contract(monkeypatch, isolated_portal_case):
    """Portal 路由和 API 的未登录、不存在、跨源错误统一返回 JSON 错误码"""
    with isolated_portal_case():
        async def call(path, *, method="GET", body=b"", headers=None):
            response = await asgi.portal_http(_request(path, method=method, body=body, headers=headers))
            return response.status_code, json.loads(await _response_body(response))

        monkeypatch.setattr(asgi.portal, "_check_session", lambda _handler: False)
        status, payload = asyncio.run(call("/api/agent-workbench/favorites"))
        assert (status, payload["code"]) == (401, "operation.unauthenticated")

        monkeypatch.setattr(asgi.portal, "_check_session", lambda _handler: True)
        monkeypatch.setattr(asgi.portal, "_get_session_username", lambda _handler: "alice")
        monkeypatch.setattr("management_portal.principal_favorites.project_favorite_statuses",
                            lambda actor, payload, forget=None: {"actor": actor, "favorites": []})
        status, payload = asyncio.run(call("/api/agent-workbench/favorites"))
        assert (status, payload) == (200, {"actor": "alice", "favorites": []})

        status, payload = asyncio.run(call("/api/unknown-route"))
        assert (status, payload["code"]) == (404, "operation.not_found")

        monkeypatch.setattr("management_portal.principal_favorites.project_favorite_statuses",
                            lambda actor, payload, forget=None: (_ for _ in ()).throw(KeyError("missing favorite")))
        status, payload = asyncio.run(call("/api/agent-workbench/favorites"))
        assert (status, payload["code"]) == (404, "operation.not_found")

        monkeypatch.setattr(asgi.portal, "_same_origin_request", lambda _handler: False)
        status, payload = asyncio.run(call("/api/ui-state/associations/put", method="POST", body=b"{}"))
        assert (status, payload["code"]) == (403, "operation.forbidden")

        monkeypatch.setattr(asgi.portal, "_same_origin_request", lambda _handler: True)
        stored = []
        monkeypatch.setattr("management_portal.portal_ui_state.get_portal_ui_state_store", lambda: type("Store", (), {
            "put": staticmethod(lambda actor, namespace, key, value: stored.append(actor) or {"key": key, "value": value}),
        })())
        posted = json.dumps({"namespace": "tests", "key": "one", "value": {"visible": True}}).encode()
        status, payload = asyncio.run(call(
            "/api/ui-state/associations/put", method="POST", body=posted,
            headers={"content-type": "application/json", "content-length": str(len(posted))},
        ))
        assert (status, payload["association"]["key"], stored) == (200, "one", ["alice"])


def test_portal_pages_routes_preserve_redirects_actor_and_admin_access(monkeypatch, isolated_portal_case):
    """页面路由保持旧地址重定向和登录用户身份，未授权访问管理页返回 403"""
    with isolated_portal_case():
        monkeypatch.setattr(asgi.portal, "_check_session", lambda _handler: True)
        monkeypatch.setattr(asgi.portal, "_get_session_username", lambda _handler: "alice")

        async def call(path):
            response = await asgi.portal_http(_request(path))
            return response.status_code, response.headers, await _response_body(response)

        status, headers, _ = asyncio.run(call("/workbenches/create?source=tests"))
        assert (status, headers["location"]) == (
            302, "/workbenches?source=tests&panel=studio",
        )

        status, headers, _ = asyncio.run(call("/workbenches/edit?id=workbench_example"))
        assert (status, headers["location"]) == (
            302, "/workbenches?id=workbench_example&panel=settings",
        )

        status, headers, body = asyncio.run(call("/"))
        assert status == 200 and headers["content-type"].startswith("text/html") and body

        monkeypatch.setattr(
            asgi.portal.resident_workspace_adapter, "get_workspace",
            lambda actor, surface, key, context: {
                "actor": actor, "surface": surface, "key": key, "context": context,
            },
        )
        status, _, body = asyncio.run(call("/chat-workspace/api/session?surface=paper&workspace_key=paper%3Aone&paper_id=one"))
        assert status == 200
        assert json.loads(body) == {
            "actor": "alice", "surface": "paper", "key": "paper:one",
            "context": {"project_id": "", "paper_id": "one", "query": ""},
        }

        monkeypatch.setattr(asgi.portal, "_resource_portal_location", lambda address, actor: f"/resource?address={address}&actor={actor}")
        status, headers, _ = asyncio.run(call("/r/repo_0123456789ab"))
        assert (status, headers["location"]) == (302, "/resource?address=repo_0123456789ab&actor=alice")

        monkeypatch.setattr(asgi.portal, "_require_authenticated_actor", lambda actor: (_ for _ in ()).throw(PermissionError("authentication required")))
        status, _, body = asyncio.run(call("/portal-management"))
        assert (status, json.loads(body)["error"]) == (403, "authentication required")


def test_ui_association_routes_use_actor_and_http_error_contract(monkeypatch, isolated_portal_case):
    """UI 关联路由按登录用户读写，参数错误返回 400，存储故障返回 500 且不泄露细节"""
    with isolated_portal_case():
        monkeypatch.setattr(asgi.portal, "_check_session", lambda _handler: True)
        monkeypatch.setattr(asgi.portal, "_get_session_username", lambda _handler: "alice")
        monkeypatch.setattr(asgi.portal, "_same_origin_request", lambda _handler: True)

        store = SimpleNamespace(
            list=lambda actor, namespace: [{"actor": actor, "namespace": namespace}],
            put=lambda actor, namespace, key, value: {
                "actor": actor, "namespace": namespace, "key": key, "value": value,
            },
        )
        monkeypatch.setattr(http_ui_state.portal_ui_state, "get_portal_ui_state_store", lambda: store)

        async def call(path, *, method="GET", body=b""):
            headers = {"content-type": "application/json", "content-length": str(len(body))} if body else None
            response = await asgi.portal_http(_request(path, method=method, body=body, headers=headers))
            return response.status_code, json.loads(await _response_body(response))

        status, payload = asyncio.run(call("/api/ui-state/associations?namespace=fixture.surface"))
        assert (status, payload) == (
            200, {"namespace": "fixture.surface", "items": [{"actor": "alice", "namespace": "fixture.surface"}]},
        )

        body = json.dumps({"namespace": "fixture.surface", "key": "one", "value": 2}).encode()
        status, payload = asyncio.run(call("/api/ui-state/associations/put", method="POST", body=body))
        assert (status, payload["association"]["actor"], payload["association"]["value"]) == (200, "alice", 2)

        monkeypatch.setattr(store, "list", lambda *_args: (_ for _ in ()).throw(ValueError("invalid namespace")))
        status, payload = asyncio.run(call("/api/ui-state/associations?namespace=bad"))
        assert (status, payload["code"]) == (400, "operation.invalid")

        monkeypatch.setattr(
            http_ui_state.portal_ui_state, "get_portal_ui_state_store",
            lambda: (_ for _ in ()).throw(RuntimeError("private storage details")),
        )
        status, payload = asyncio.run(call("/api/ui-state/associations?namespace=fixture.surface"))
        assert (status, payload["code"], payload["error"]) == (
            500, "operation.failed", "UI association storage is unavailable."
        )
        assert "private storage details" not in json.dumps(payload)


def test_agent_status_routes_preserve_actor_refresh_and_account_errors(monkeypatch, isolated_portal_case):
    """Agent 状态路由按登录用户返回数据，refresh 参数触发刷新，旧账户控制 URL 返回 404"""
    with isolated_portal_case():
        monkeypatch.setattr(asgi.portal, "_check_session", lambda _handler: True)
        monkeypatch.setattr(asgi.portal, "_get_session_username", lambda _handler: "alice")
        monkeypatch.setattr(asgi.portal, "_same_origin_request", lambda _handler: True)
        adapter = asgi.portal._AGENT_STATUS_HTTP
        monkeypatch.setattr(adapter, "cached_summary", lambda actor: {"actor": actor, "cached": True})
        monkeypatch.setattr(adapter, "cached_active", lambda actor: {"actor": actor, "active": []})
        monkeypatch.setattr(adapter, "cached_scheduled_triggered", lambda actor: {"actor": actor, "items": []})
        schedule_invalidations = []
        monkeypatch.setattr(adapter, "invalidate_scheduled_triggered", lambda actor: schedule_invalidations.append(actor))
        monkeypatch.setattr(
            asgi.portal.agent_status_backend, "get_agent_status_summary",
            lambda actor, force_refresh=False: {"actor": actor, "refreshed": force_refresh},
        )
        monkeypatch.setattr(
            asgi.portal.agent_status_backend, "get_token_usage_summary",
            lambda actor, force_refresh=False: {"actor": actor, "refreshed": force_refresh},
        )
        monkeypatch.setattr(adapter, "resident_api", SimpleNamespace(
            get_account_resident_summary=lambda account_id, *, actor: {"account_id": account_id, "actor": actor},
        ))

        async def call(path, *, method="GET", payload=None):
            body = json.dumps(payload).encode() if payload is not None else b""
            headers = {"content-type": "application/json", "content-length": str(len(body))} if body else None
            response = await asgi.portal_http(_request(path, method=method, body=body, headers=headers))
            return response.status_code, json.loads(await _response_body(response))

        assert asyncio.run(call("/api/agent-status")) == (200, {"actor": "alice", "cached": True})
        assert asyncio.run(call("/api/agent-status/usage")) == (200, {"actor": "alice", "refreshed": False})
        assert asyncio.run(call("/api/agent-status/active-agents")) == (200, {"actor": "alice", "active": []})
        assert asyncio.run(call("/api/agent-status/scheduled-triggered")) == (200, {"actor": "alice", "items": []})
        assert schedule_invalidations == []
        assert asyncio.run(call("/api/agent-status/scheduled-triggered?refresh=1")) == (200, {"actor": "alice", "items": []})
        assert schedule_invalidations == ["alice"]
        assert asyncio.run(call("/api/agent-status/background-agents"))[0] == 404
        assert asyncio.run(call("/api/agent-status/account-residents?account_id=account-1")) == (
            200, {"account_id": "account-1", "actor": "alice"}
        )
        assert asyncio.run(call("/api/agent-status/refresh", method="POST")) == (
            200, {"actor": "alice", "refreshed": True}
        )
        assert asyncio.run(call("/api/agent-status/usage/refresh", method="POST")) == (
            200, {"actor": "alice", "refreshed": True}
        )

        # Account intake and scheduler changes are instructions (OpenIntake with open,
        # UpdateProviderAccount); their old URLs are gone.
        for path in ("/api/agent-status/account-open-residents", "/api/agent-status/account-close-residents",
                     "/api/agent-status/account-scheduler-settings"):
            status, result = asyncio.run(call(path, method="POST", payload={"account_id": "account-1"}))
            assert (status, result["code"]) == (404, "operation.not_found"), path


@pytest.mark.parametrize("selected", [False, True])
def test_resident_detail_subscription_filters_before_fanout_and_keeps_read_permissions(selected):
    """Resident 事件流在分发前按订阅和读权限过滤，不推送无权 Resident 和大块活动输出"""
    async def exercise():
        visible_id = "resident_111111111111"
        other_id = "resident_222222222222"
        private_id = "resident_333333333333"
        hub = NotificationHub(max_items=512)
        fanout = AsyncNotificationFanout(hub)
        fanout.start()
        executor = ThreadPoolExecutor(max_workers=1)
        app = SimpleNamespace(state=SimpleNamespace(
            notification_fanout=fanout,
            sse_payload_executor=executor,
            realtime_lifecycle=asgi.RealtimeConnectionLifecycle(),
        ))
        requested = f"{visible_id},{private_id}" if selected else ""
        request = _request(f"/api/events?domains=resident&resident_ids={requested}", app=app)
        with (
            mock.patch.object(asgi.portal, "_get_session_username", return_value="viewer"),
            mock.patch.object(asgi, "ensure_notification_listener"),
            mock.patch.object(asgi, "ensure_knowledge_archive_projection_watcher"),
            mock.patch.object(permission_system, "readable_ids", side_effect=lambda instruction, parameter, identifiers, **kw: set(identifiers) & {visible_id, other_id}),
        ):
            response = await asgi.portal_events(request)
            iterator = response.body_iterator
            await anext(iterator)
            await anext(iterator)

            def publish(resident_id, event_type, payload):
                hub.publish(f"portal.resident.changed.{resident_id}.conversation", {
                    "kind": "conversation_event",
                    "event": {"resident_id": resident_id, "event_type": event_type, "cursor": 8, "payload": payload},
                })

            for _ in range(100):
                publish(other_id, "resident.activities.changed", {"activities": ["tool output" * 1000]})
            publish(visible_id, "conversation.message.appended", {"message": {"text": "selected chat"}})
            publish(private_id, "conversation.message.appended", {"message": {"text": "private chat"}})
            publish(other_id, "resident.state.changed", {"state": {"work_state": "running"}})
            chunk = await asyncio.wait_for(anext(iterator), timeout=2)
            payload = json.loads(chunk.decode().split("data: ", 1)[1])
            assert payload["snapshot_required"] is False
            assert {event["resident_id"] for event in payload["resident_events"]} == (
                {visible_id, other_id} if selected else {other_id}
            )
            assert b"tool output" not in chunk
            assert b"private chat" not in chunk
            assert len(chunk) < 2000
            await iterator.aclose()
        fanout.close()
        executor.shutdown(wait=True, cancel_futures=True)

    asyncio.run(exercise())


def test_asgi_realtime_drain_does_not_claim_success_with_active_streams_contracts(subtests, isolated_portal_case):
    """仍有活动连接时实时排空返回 503，重启通知先发布投影并拒绝经 Router 转发的请求"""
    with subtests.test(contract='test_asgi_realtime_drain_does_not_claim_success_with_active_streams'), isolated_portal_case() as case_path:
        async def exercise() -> None:
            hub = NotificationHub(max_items=32)
            fanout = AsyncNotificationFanout(hub)
            fanout.start()
            lifecycle = asgi.RealtimeConnectionLifecycle()
            connection_id = lifecycle.open_connection()
            assert connection_id is not None
            app = SimpleNamespace(state=SimpleNamespace(notification_fanout=fanout, realtime_lifecycle=lifecycle))
            with mock.patch.object(asgi, 'REALTIME_DRAIN_TIMEOUT_SECONDS', 0.01):
                response = await asgi.drain_realtime(_request('/api/internal/drain-realtime', method='POST', app=app))
            payload = json.loads(response.body)
            assert response.status_code == 503
            assert payload['drained'] is False
            assert payload['remaining_realtime_connections'] == 1
            assert payload['code'] == 'operation.unavailable'
            assert payload['message'] == payload['error']
            lifecycle.close_connection(connection_id)
        asyncio.run(exercise())
    with subtests.test(contract='test_asgi_restart_notice_publishes_before_realtime_drain'), isolated_portal_case() as case_path:
        async def exercise() -> None:
            hub = NotificationHub(max_items=32)
            fanout = AsyncNotificationFanout(hub)
            fanout.start()
            app = SimpleNamespace(state=SimpleNamespace(notification_fanout=fanout, realtime_lifecycle=asgi.RealtimeConnectionLifecycle()))
            projected = {'active': True, 'notice_id': 'notice-real', 'restart_at': 1005}
            with mock.patch.object(asgi.portal, '_publish_restart_notice', return_value=projected) as publish:
                response = await asgi.publish_restart_notice(_request('/api/internal/restart-notice', method='POST', body=json.dumps({'seconds': 5, 'message': 'Restart Dev'}).encode(), app=app))
            payload = json.loads(response.body)
            assert response.status_code == 200
            assert payload['projection_published'] is True
            assert payload['notice'] == projected
            publish.assert_called_once_with({'seconds': 5, 'message': 'Restart Dev'}, scheduled_by='local-restart', restart_requested=False)
            fanout.close()
        asyncio.run(exercise())
    with subtests.test(contract='test_asgi_restart_notice_rejects_router_forwarded_request'), isolated_portal_case() as case_path:
        async def exercise() -> None:
            response = await asgi.publish_restart_notice(_request('/api/internal/restart-notice', method='POST', body=b'{}', headers={'x-forwarded-for': '203.0.113.10'}))
            assert response.status_code == 403
        asyncio.run(exercise())
    with subtests.test(contract='test_asgi_lifespan_owns_background_services'), isolated_portal_case() as case_path:
        # Features start their background work with Web (``web.lifespan``); a returned stop runs when Web stops.
        feature_stop = mock.Mock()
        with_stop, without_stop = mock.Mock(return_value=feature_stop), mock.Mock(return_value=None)
        installed = {'web.lifespan': (('wall', with_stop), ('test_framework', without_stop))}
        # Other hooks keep the installed features: the Web instruction kernel the
        # lifespan may start needs the features' instruction handlers.
        real_contributions = asgi.portal_features.contributions

        async def exercise() -> None:
            with mock.patch.object(asgi, 'close_notifications') as close, mock.patch.object(
                    asgi.portal_features, 'contributions',
                    lambda hook: installed[hook] if hook in installed else real_contributions(hook)):
                async with asgi.lifespan(asgi.app):
                    with_stop.assert_called_once_with()
                    without_stop.assert_called_once_with()
                    feature_stop.assert_not_called()
                    assert isinstance(asgi.app.state.notification_fanout, AsyncNotificationFanout)
                feature_stop.assert_called_once_with()
                close.assert_called_once_with()
        asyncio.run(exercise())


def test_asgi_realtime_drain_expires_inflight_payload_before_returning_event() -> None:
    """实时排空让正在计算负载的事件流直接结束，不再发出该事件"""
    async def exercise() -> None:
        hub = NotificationHub(max_items=32)
        fanout = AsyncNotificationFanout(hub)
        fanout.start()
        executor = ThreadPoolExecutor(max_workers=1)
        payload_started = asyncio.Event()
        release_payload = asyncio.Event()
        loop = asyncio.get_running_loop()

        def blocked_payload(*_args, **_kwargs):
            loop.call_soon_threadsafe(payload_started.set)
            asyncio.run_coroutine_threadsafe(
                release_payload.wait(), loop
            ).result(timeout=2)
            return {
                "domains": ["resource"],
                "changed": {"resource": ["skill-one"]},
                "snapshot_required": False,
            }

        app = SimpleNamespace(
            state=SimpleNamespace(
                notification_fanout=fanout,
                sse_payload_executor=executor,
                realtime_lifecycle=asgi.RealtimeConnectionLifecycle(),
            )
        )
        with (
            mock.patch.object(asgi.portal, "_get_session_username", return_value="alice"),
            mock.patch.object(asgi, "ensure_notification_listener"),
            mock.patch.object(asgi, "ensure_knowledge_archive_projection_watcher"),
            mock.patch.object(asgi.portal, "_portal_change_payload", side_effect=blocked_payload),
        ):
            response = await asgi.portal_events(_request("/api/events", app=app))
            iterator = response.body_iterator
            await anext(iterator)
            await anext(iterator)
            hub.publish("portal.resource.changed.skill.skill-one", {})
            pending_change = asyncio.create_task(anext(iterator))
            await asyncio.wait_for(payload_started.wait(), timeout=1)

            drained = await asgi.drain_realtime(
                _request(
                    "/api/internal/drain-realtime",
                    method="POST",
                    app=app,
                )
            )

            assert drained.status_code == 200
            drain_payload = json.loads(drained.body)
            assert drain_payload["drained"] is True
            assert drain_payload["remaining_realtime_connections"] == 0
            with pytest.raises(StopAsyncIteration):
                await asyncio.wait_for(pending_change, timeout=0.2)
            release_payload.set()
        executor.shutdown(wait=True, cancel_futures=True)

    asyncio.run(exercise())


def test_independent_api_routes_answer_with_no_store_unless_they_choose_a_policy():
    """独立 API 路由的成功和错误响应默认 no-store，路由自选的缓存策略保留"""
    from starlette.responses import JSONResponse, Response

    async def answer(_request):
        return JSONResponse({"status": "committed"})

    async def fail(_request):
        raise PermissionError("denied")

    async def chosen(_request):
        return Response(b"x", headers={"Cache-Control": "private, max-age=300"})

    async def exercise():
        results = []
        for endpoint in (answer, fail, chosen):
            response = await asgi._independent_api_error_boundary(endpoint)(_request("/api/instructions/execute", method="POST"))
            results.append((response.status_code, response.headers.get("cache-control")))
        return results

    assert asyncio.run(exercise()) == [(200, "no-store"), (403, "no-store"), (200, "private, max-age=300")]
    wrapped = {route.path: route.endpoint.__wrapped__ for route in asgi.app.routes if hasattr(route.endpoint, "__wrapped__")}
    assert wrapped["/api/instructions/execute"] is asgi.instruction_execute
