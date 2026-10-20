"""Streaming HTTP/WebSocket transport for native Providers; no content logging.

Only output IDs and local transport timestamps are retained, in a bounded cache.
Provider-owned retries remain separate requests. The original stream bytes are
forwarded unchanged; parsing is an observation alongside forwarding.
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
from datetime import datetime, timezone
import hmac
import json
import logging
import os
import re
import socket
import time

from aiohttp import ClientError, ClientSession, ClientTimeout, DummyCookieJar, TCPConnector, WSMsgType, web
from multidict import CIMultiDict
from yarl import URL
from ..provider_timing import decode_route, signing_key, root, DEFAULT_PORT

HOP = {'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
       'te', 'trailer', 'transfer-encoding', 'upgrade'}
CORRELATION_HEADER = 'x-portal-output-id'
MAX_FRAME = 1024 * 1024  # Observation budget only; never a transport limit.


def forward_headers(headers, *, request=False):
    excluded = HOP | {part.strip().lower() for value in headers.getall('Connection', [])
                      for part in value.split(',')}
    if request:
        excluded |= {'host', CORRELATION_HEADER}
    return CIMultiDict((k, v) for k, v in headers.items() if k.lower() not in excluded)


def observe(observer, method, *args):
    """Observation is disposable; a parser/cache failure must never break transport."""
    if not observer.observing or not observer.cache.enabled or observer.epoch != observer.cache.epoch:
        return
    try:
        getattr(observer, method)(*args)
    except Exception:
        observer.buffer, observer.observing = b'', False


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


class Timings:
    def __init__(self, limit: int = 100000, ttl: float = 86400):
        self.entries = OrderedDict()
        self.limit, self.ttl = limit, ttl
        self.enabled = True
        self.epoch = 0
        self.sink = None

    def set_enabled(self, enabled: bool) -> None:
        if self.enabled != enabled:
            self.enabled = enabled
            self.epoch += 1
            self.entries.clear()

    def put(self, provider: str, output: str, timing: dict) -> None:
        if not self.enabled or not output or len(output) > 1024:
            return
        key = (provider, output)
        self.entries[key] = (time.monotonic(), dict(timing))
        self.entries.move_to_end(key)
        self.prune()
        if self.sink is not None:
            self.sink(provider, output, timing)

    def prune(self) -> None:
        while self.entries:
            _, (created, _) = next(iter(self.entries.items()))
            if len(self.entries) <= self.limit and time.monotonic() - created < self.ttl:
                break
            self.entries.popitem(last=False)

    def get(self, provider: str, output: str) -> dict:
        if not self.enabled:
            return {}
        self.prune()
        return dict(self.entries.get((provider, output), (0, {}))[1])


class ResponseObserver:
    def __init__(self, cache: Timings, provider: str, started: str, correlation: str = ''):
        self.cache, self.provider = cache, provider
        self.epoch = cache.epoch
        self.started, self.correlation = started, correlation
        self.ids: set[str] = set()
        self.finished: dict[str, str] = {}
        self.buffer = b''
        self.observing = True

    def record(self, identity: str, *, finished: bool = False) -> None:
        if not identity or len(identity) > 1024:
            return
        if len(self.ids) >= 1024 and identity not in self.ids:
            self.observing = False
            return
        self.ids.add(identity)
        value = {'started_at': self.started}
        if finished:
            self.finished.setdefault(identity, now())
        if identity in self.finished:
            value['finished_at'] = self.finished[identity]
        self.cache.put(self.provider, identity, value)

    def event(self, event: dict) -> None:
        if not isinstance(event, dict):
            return
        typ = event.get('type', '')
        identity = str(event.get('id') or '')
        if typ == 'message_start':
            self.record(str((event.get('message') or {}).get('id') or ''))
        elif typ in {'response.output_item.added', 'response.output_item.done'}:
            self.record(str((event.get('item') or {}).get('id') or ''), finished=typ.endswith('.done'))
        elif typ in {'response.created', 'response.completed'}:
            response = event.get('response') or {}
            self.record(str(response.get('id') or ''), finished=typ.endswith('.completed'))
            for item in response.get('output') or []:
                if not self.cache.get(self.provider, str(item.get('id') or '')).get('finished_at'):
                    self.record(str(item.get('id') or ''), finished=typ.endswith('.completed'))
        elif identity and (event.get('choices') is not None or event.get('object') in {'chat.completion', 'chat.completion.chunk'}):
            self.record(identity)
        if self.correlation:
            self.record(self.correlation)
        if typ in {'message_stop', 'response.completed'} or any(c.get('finish_reason') for c in event.get('choices') or []):
            self.finish()

    def feed(self, chunk: bytes) -> None:
        if not self.observing:
            return
        self.buffer += chunk
        if len(self.buffer) > MAX_FRAME:
            self.buffer, self.observing = b'', False
            return
        # SSE parsing does not change the bytes sent to the Provider.
        while boundary := re.search(rb'\r\n\r\n|\n\n|\r\r', self.buffer):
            frame, self.buffer = self.buffer[:boundary.start()], self.buffer[boundary.end():]
            event_type = next((line[6:].strip() for line in frame.splitlines() if line.startswith(b'event:')), b'')
            # Text/reasoning/tool deltas contain no identity we need to retain.
            if event_type.endswith(b'.delta') or event_type.startswith(b'content_block_'):
                continue
            data = b'\n'.join(line[5:].lstrip(b' ') for line in frame.splitlines() if line.startswith(b'data:'))
            if data == b'[DONE]':
                self.finish()
            else:
                try:
                    self.event(json.loads(data))
                except (ValueError, TypeError):
                    pass

    def json_body(self, body) -> None:
        self.event(json.loads(body))

    def finish(self) -> None:
        for identity in self.ids | ({self.correlation} if self.correlation else set()):
            if identity not in self.finished:
                self.record(identity, finished=True)


class Proxy:
    def __init__(self, settings=None):
        self.cache = Timings()
        self.settings = settings
        self.session: ClientSession | None = None

    async def refresh_settings(self):
        if self.settings is not None:
            value = await asyncio.to_thread(self.settings.get)
            self.cache.set_enabled(value['provider_timing_proxy_enabled'])

    async def lifecycle(self, _app):
        async with ClientSession(timeout=ClientTimeout(total=None), auto_decompress=False,
                                 cookie_jar=DummyCookieJar(), connector=TCPConnector(limit=0),
                                 skip_auto_headers={'Accept', 'Accept-Encoding', 'Content-Type', 'User-Agent'},
                                 trust_env=False) as self.session:
            yield

    async def timing(self, request):
        if not hmac.compare_digest(request.headers.get('Authorization', ''), 'Bearer ' + signing_key().hex()):
            raise web.HTTPForbidden()
        await self.refresh_settings()
        return web.json_response(self.cache.get(request.query.get('provider', ''), request.query.get('id', '')))

    async def forward(self, request):
        try:
            provider, upstream, egress = decode_route(request.match_info['route'])
        except (ValueError, TypeError, KeyError):
            raise web.HTTPForbidden()
        # Preserve escaped path/query bytes (including repeated query parameters).
        suffix = request.raw_path[len('/p/' + request.match_info['route']):]
        target = URL(upstream + suffix, encoded=True)
        headers = forward_headers(request.headers, request=True)
        correlation = request.headers.get(CORRELATION_HEADER, '')
        try:
            if request.headers.get('Upgrade', '').lower() == 'websocket':
                return await self.websocket(request, provider, target, headers, egress)
            observer = ResponseObserver(self.cache, provider, now(), correlation)
            body = request.content.iter_chunked(64 * 1024) if request.can_read_body else None
            async with self.session.request(request.method, target, data=body, headers=headers,
                                            proxy=egress or None, allow_redirects=False) as response:
                outgoing = web.StreamResponse(status=response.status, reason=response.reason,
                                              headers=forward_headers(response.headers))
                await outgoing.prepare(request)
                sse = 'text/event-stream' in response.headers.get('Content-Type', '')
                is_json = 'json' in response.headers.get('Content-Type', '')
                observable = self.cache.enabled and response.status < 300 and response.headers.get('Content-Encoding', 'identity').lower() == 'identity'
                collected = bytearray()
                async for chunk in response.content.iter_any():
                    if not self.cache.enabled or observer.epoch != self.cache.epoch:
                        observable = False
                        collected.clear()
                    if observable:
                        if sse:
                            observe(observer, 'feed', chunk)
                        elif is_json:
                            if len(collected) + len(chunk) <= MAX_FRAME:
                                collected.extend(chunk)
                            else:
                                collected.clear()
                                observable = False
                    await outgoing.write(chunk)
                if observable and is_json and not sse:
                    observe(observer, 'json_body', collected)
                    observe(observer, 'finish')
                await outgoing.write_eof()
                return outgoing
        except (ClientError, ConnectionError, asyncio.TimeoutError):
            raise web.HTTPBadGateway(text='Model upstream unavailable')

    async def websocket(self, request, provider, target, headers, egress):
        # aiohttp owns framing, fragmentation, ping/pong and TLS validation.
        protocols = [value.strip() for value in request.headers.get('Sec-WebSocket-Protocol', '').split(',') if value.strip()]
        headers = CIMultiDict((k, v) for k, v in headers.items() if not k.lower().startswith('sec-websocket-'))
        async with self.session.ws_connect(target, headers=headers, proxy=egress or None,
                                           protocols=protocols, max_msg_size=0, autoping=False, autoclose=False) as upstream:
            downstream = web.WebSocketResponse(protocols=[upstream.protocol] if upstream.protocol else (),
                                               max_msg_size=0, autoping=False, autoclose=False)
            await downstream.prepare(request)
            pending = deque()
            responses = {}
            active = None
            observing = True
            epoch = self.cache.epoch

            def can_observe():
                nonlocal active, epoch
                if epoch != self.cache.epoch:
                    epoch = self.cache.epoch
                    pending.clear()
                    responses.clear()
                    active = None
                return observing and self.cache.enabled

            async def forward_control(message, peer):
                if message.type == WSMsgType.PING:
                    await peer.ping(message.data)
                elif message.type == WSMsgType.PONG:
                    await peer.pong(message.data)
                elif message.type == WSMsgType.CLOSE:
                    await peer.close(code=message.data, message=message.extra.encode())
                    return False
                elif message.type in {WSMsgType.CLOSED, WSMsgType.ERROR}:
                    return False
                return True

            async def send_requests():
                nonlocal observing
                while True:
                    message = await downstream.receive()
                    if message.type == WSMsgType.TEXT:
                        if can_observe():
                            try:
                                if len(message.data) > MAX_FRAME or len(pending) >= 1024:
                                    raise ValueError('Observation budget exceeded')
                                payload = json.loads(message.data)
                                if isinstance(payload, dict) and payload.get('type') == 'response.create':
                                    pending.append(None if payload.get('generate') is False else ResponseObserver(self.cache, provider, now()))
                            except Exception:
                                observing = False
                                pending.clear()
                                responses.clear()
                        await upstream.send_str(message.data)
                    elif message.type == WSMsgType.BINARY:
                        await upstream.send_bytes(message.data)
                    elif not await forward_control(message, upstream):
                        break

            async def send_responses():
                nonlocal active, observing
                while True:
                    message = await upstream.receive()
                    if message.type == WSMsgType.TEXT:
                        if can_observe():
                            try:
                                if len(message.data) > MAX_FRAME or len(responses) >= 1024:
                                    raise ValueError('Observation budget exceeded')
                                event = json.loads(message.data)
                                if isinstance(event, dict):
                                    if event.get('type') == 'response.created':
                                        active = pending.popleft() if pending else None
                                        rid = (event.get('response') or {}).get('id')
                                        if rid:
                                            responses[rid] = active
                                    observer = responses.get(event.get('response_id'), active)
                                    if observer:
                                        observe(observer, 'event', event)
                                    if event.get('type') in {'response.completed', 'response.failed', 'response.incomplete', 'error'}:
                                        responses.pop((event.get('response') or {}).get('id'), None)
                                        active = None
                                        if event.get('type') == 'error' and pending:
                                            pending.popleft()
                            except Exception:
                                observing = False
                                pending.clear()
                                responses.clear()
                                active = None
                        await downstream.send_str(message.data)
                    elif message.type == WSMsgType.BINARY:
                        await downstream.send_bytes(message.data)
                    elif not await forward_control(message, downstream):
                        break

            tasks = [asyncio.create_task(send_requests()), asyncio.create_task(send_responses())]
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                await downstream.close()
            return downstream


def application(proxy: Proxy | None = None) -> web.Application:
    proxy = proxy or Proxy()
    # Native clients also compress request bodies (notably Codex compact with zstd).
    # Preserve both bytes and Content-Encoding; decoding here corrupts forwarding
    # and would unnecessarily require every provider's compression codec locally.
    app = web.Application(client_max_size=0,
                          handler_args={'auto_decompress': False})
    app.cleanup_ctx.append(proxy.lifecycle)
    async def health(_request):
        await proxy.refresh_settings()
        return web.json_response({'ok': True, 'enabled': proxy.cache.enabled})
    app.router.add_get('/health', health)
    app.router.add_get('/timing', proxy.timing)
    app.router.add_route('*', '/p/{route}/{tail:.*}', proxy.forward)
    app.router.add_route('*', '/p/{route}', proxy.forward)
    return app


class TimingProxyModule:
    """HTTP observation owned and started by ExecutionService, not a separate service."""

    def __init__(self, settings, *, port=DEFAULT_PORT, inherited=None):
        import threading
        self.settings = settings
        self.port = port
        self.proxy = Proxy(settings)
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, name='execution-timing-proxy', daemon=True)
        self.runner = None
        self.lease = None
        self.monitor = None
        self.site = None
        self.inherited = inherited
        self.retiring = False
        self.handover_pending = False
        self.active = {}
        self.ownership = asyncio.Lock()

    def start(self):
        logging.getLogger('aiohttp.server').disabled = True
        signing_key()
        self.proxy.cache.set_enabled(self.settings.get()['provider_timing_proxy_enabled'])
        self.thread.start()

        async def start():
            await self._maintain()
            self.monitor = asyncio.create_task(self._monitor())
        try:
            asyncio.run_coroutine_threadsafe(start(), self.loop).result(10)
        except Exception:
            self.close()
            raise

    async def _maintain(self):
        async with self.ownership:
            await self._maintain_listener()

    async def _maintain_listener(self):
        """The two ExecutionService pools share one endpoint, held by a file lease."""
        import errno
        from management_common.file_lock import try_lock_descriptor
        if self.retiring or self.handover_pending:
            return
        if self.runner is not None:
            await self.proxy.refresh_settings()
            return
        inherited = self.inherited
        if inherited:
            lease = os.fdopen(inherited['lease'], 'a')
        else:
            lease = (root() / f'listener-{self.port}.lock').open('a')
            if not try_lock_descriptor(lease.fileno()):
                lease.close()
                return
        app = application(self.proxy)
        @web.middleware
        async def track(request, handler):
            task = asyncio.current_task()
            self.active[task] = request.protocol
            try:
                return await handler(request)
            finally:
                self.active.pop(task, None)
                if self.retiring:
                    request.protocol.close()
        app.middlewares.append(track)
        runner = web.AppRunner(app, access_log=None, handler_cancellation=True)
        try:
            await runner.setup()
            site = (web.SockSite(runner, socket.socket(fileno=inherited['listener']))
                    if inherited else web.TCPSite(runner, '127.0.0.1', self.port))
            await site.start()
        except BaseException as error:
            await runner.cleanup()
            lease.close()
            if isinstance(error, OSError) and error.errno == errno.EADDRINUSE:
                return
            raise
        self.runner, self.lease, self.site = runner, lease, site
        self.inherited = None
        self.port = site._server.sockets[0].getsockname()[1]
        await self.proxy.refresh_settings()

    async def _monitor(self):
        while True:
            await asyncio.sleep(.5)
            try:
                await self._maintain()
            except Exception:
                logging.getLogger(__name__).exception('Timing module maintenance failed')

    def configure(self, enabled):
        self.settings.update(enabled)
        async def apply():
            await self.proxy.refresh_settings()
        asyncio.run_coroutine_threadsafe(apply(), self.loop).result(5)
        # Health refreshes the active pool as well when this instance is standby.
        if self.runner is None:
            from ..provider_timing import enabled as refresh_active
            refresh_active()
        return self.settings.get()

    def close(self):
        if self.loop.is_closed():
            return
        try:
            async def stop():
                await self._retire()
                # AppRunner's bounded shutdown would cancel slow responses or
                # open WebSockets. Wait for transport before invoking cleanup.
                while self.active:
                    await asyncio.gather(*list(self.active), return_exceptions=True)
                if self.runner is not None:
                    await self.runner.cleanup()
                if self.lease is not None:
                    self.lease.close()
            if self.thread.is_alive():
                asyncio.run_coroutine_threadsafe(stop(), self.loop).result()
        finally:
            if self.thread.is_alive():
                self.loop.call_soon_threadsafe(self.loop.stop)
                self.thread.join(5)
            self.loop.close()

    def handover_descriptors(self):
        async def snapshot():
            async with self.ownership:
                self.handover_pending = True
                if self.retiring or self.runner is None:
                    return None
                return {'listener': self.site._server.sockets[0].fileno(),
                        'lease': self.lease.fileno()}
        return asyncio.run_coroutine_threadsafe(snapshot(), self.loop).result(5)

    def cancel_handover(self):
        self.loop.call_soon_threadsafe(setattr, self, 'handover_pending', False)

    async def _retire(self):
        self.retiring = True
        if self.monitor is not None:
            self.monitor.cancel()
            await asyncio.gather(self.monitor, return_exceptions=True)
        if self.site is not None:
            await self.site.stop()
            self.site = None
        if self.lease is not None:
            # Close this generation's reference, never LOCK_UN: the successor
            # inherited the same flock. Retired streams must not retain the
            # listener lease if the active generation later crashes.
            self.lease.close()
            self.lease = None
        if self.runner is not None:
            # RequestHandler.close() stops inbound parsing too. Calling it on
            # active handlers would drop an upload or ongoing WebSocket frames.
            active_protocols = set(self.active.values())
            for protocol in self.runner.server.connections:
                if protocol not in active_protocols:
                    protocol.close()

    def retire(self):
        asyncio.run_coroutine_threadsafe(self._retire(), self.loop).result(5)

    def relay_timings(self, sink):
        async def attach():
            self.proxy.cache.prune()
            self.proxy.cache.sink = sink
            for (provider, output), (_, value) in self.proxy.cache.entries.items():
                sink(provider, output, value)
        asyncio.run_coroutine_threadsafe(attach(), self.loop).result(5)

    def receive_timing(self, provider, output, value):
        def receive():
            try:
                self.proxy.cache.put(provider, output, value)
            except Exception:
                pass  # Optional observation must never affect transport.
        if not self.loop.is_closed():
            self.loop.call_soon_threadsafe(receive)
