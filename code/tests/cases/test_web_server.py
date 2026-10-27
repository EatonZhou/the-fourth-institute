"""The Web process ends realtime event streams as soon as it starts to stop.

uvicorn's graceful shutdown waits for open responses while it no longer
accepts connections; an open page's event stream would hold every Web restart
for the whole shutdown timeout.

:module: operations.deployment
:design: decision_08bb2d9141f9
:importance: P1
"""
from __future__ import annotations

import asyncio
import signal

import uvicorn

from management_portal import asgi, web_server


def test_stop_signal_ends_realtime_streams_without_waiting_for_them(monkeypatch):
    """收到停止信号时 Web 立即结束实时事件流，不等页面断开"""
    ended = []
    monkeypatch.setattr(asgi, "end_realtime_streams", ended.append)
    server = web_server.PortalWebServer(uvicorn.Config(web_server.APP))

    async def exercise():
        server.event_loop = asyncio.get_running_loop()
        server.handle_exit(signal.SIGTERM, None)
        await asyncio.sleep(0)

    asyncio.run(exercise())
    assert server.should_exit
    assert ended == [asgi.app.state]


def test_entry_serves_portal_app_on_the_given_address(monkeypatch):
    """Web 入口按给定地址提供 Portal 应用，关闭访问日志，其他请求仍有关闭等待上限"""
    seen = {}
    monkeypatch.setattr(web_server.PortalWebServer, "run", lambda self: seen.update(config=self.config))
    web_server.main(["--host", "127.0.0.1", "--port", "19001"])
    config = seen["config"]
    assert (config.app, config.host, config.port, config.access_log, config.timeout_graceful_shutdown) == (
        "management_portal.asgi:app", "127.0.0.1", 19001, False, web_server.GRACEFUL_SHUTDOWN_SECONDS)
