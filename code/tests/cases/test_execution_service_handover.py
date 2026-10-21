"""Real process generations and live streams, without model credentials or DB."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import http.client
import os
from pathlib import Path
import signal
import socket
import socketserver
import subprocess
import sys
import threading
import time
import urllib.request

from aiohttp import ClientSession, web
import pytest
from management_common import process_runtime

from management_portal import provider_timing as timing
from management_portal.execution.provider_timing_proxy import TimingProxyModule
from management_portal.execution.service_lifecycle import ServiceLifecycle, inherited_descriptors
from tests.cases.test_native_provider_timing import serving


class Settings:
    def get(self):
        return {'provider_timing_proxy_enabled': True}


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.wfile.write(f'{os.getpid()}\n'.encode())


class Server(socketserver.ThreadingUnixStreamServer):
    def __init__(self, path, inherited):
        self.preserve_socket = bool(inherited)
        self.started = threading.Event()
        self.timing_proxy = (None if os.getenv('NO_TIMING_PROXY') else
                            TimingProxyModule(Settings(), port=int(os.environ['PORTAL_PROVIDER_TIMING_PORT']),
                                              inherited=inherited.get('proxy')))
        super().__init__(path, Handler, bind_and_activate=not inherited)
        if inherited:
            self.socket.close()
            self.socket = socket.socket(fileno=inherited['rpc'])
        self.socket.setblocking(False)

    def serve_forever(self):
        if self.timing_proxy is not None:
            self.timing_proxy.start()
        self.started.set()
        super().serve_forever(poll_interval=.02)

    def server_close(self):
        if self.timing_proxy is not None:
            self.timing_proxy.close()
        super().server_close()


def run_helper():
    with open(os.environ['PROBE_GENERATIONS'], 'a', encoding='utf-8') as generations:
        generations.write(f'{os.getpid()}\n')
    inherited = inherited_descriptors()
    if inherited and os.getenv('FAIL_SUCCESSOR'):
        Path(os.environ['PROBE_SOCKET'] + '.failure').touch()
        raise SystemExit(3)
    server = Server(os.environ['PROBE_SOCKET'], inherited)
    command = [sys.executable, '-c', 'from tests.cases.test_execution_service_handover import run_helper; run_helper()']
    ServiceLifecycle(server, inherited, command=command).run()


def wait_for(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = predicate()
            if value:
                return value
        except (OSError, ValueError):
            pass
        time.sleep(.02)
    raise AssertionError('Timed out waiting for process handover')


@pytest.fixture
def process_proxy(tmp_path, monkeypatch):
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
    monkeypatch.setenv('PORTAL_PROVIDER_TIMING_ROOT', str(tmp_path / 'timing'))
    monkeypatch.setenv('PORTAL_PROVIDER_TIMING_PORT', str(port))
    monkeypatch.setenv('PROBE_SOCKET', str(tmp_path / 'rpc.sock'))
    generations = tmp_path / 'generations.txt'
    monkeypatch.setenv('PROBE_GENERATIONS', str(generations))
    processes = []

    def start(*, fail=False, no_proxy=False):
        env = dict(os.environ)
        env.pop('NOTIFY_SOCKET', None)
        if no_proxy:
            env['NO_TIMING_PROXY'] = '1'
        if fail:
            env['FAIL_SUCCESSOR'] = '1'
        env['PYTHONPATH'] = os.pathsep.join(str(item) for item in sys.path if item)
        process = process_runtime.spawn_process([sys.executable, '-c',
            'from tests.cases.test_execution_service_handover import run_helper; run_helper()'],
            env=env, start_new_session=True)
        processes.append(process)
        def pid():
            with socket.socket(socket.AF_UNIX) as sock:
                sock.settimeout(1)
                sock.connect(env['PROBE_SOCKET'])
                return int(sock.recv(100))
        wait_for(pid)
        return process, pid

    yield start
    # Each successor now owns a separate session. Reaping the first root
    # alone would leave later generations running after the test finishes.
    if generations.exists():
        for pid in reversed(list(dict.fromkeys(map(int, generations.read_text().splitlines())))):
            process_runtime.terminate_process_tree(pid, force=True)
    for process in processes:
        process_runtime.terminate_process_tree(process, force=True)
        process.wait(timeout=5)


def test_rpc_handover_without_any_model_proxy(process_proxy):
    """未启用模型代理时执行服务进程仍可连续两次交接 RPC 监听"""
    process, pid = process_proxy(no_proxy=True)
    first = pid()
    os.kill(first, signal.SIGHUP)
    second = wait_for(lambda: (value if (value := pid()) != first else None))
    wait_for(lambda: process.poll() is not None)
    os.kill(second, signal.SIGHUP)
    third = wait_for(lambda: (value if (value := pid()) != second else None))
    assert third not in {first, second}


def test_stream_survives_two_process_replacements_and_timings_follow(process_proxy):
    """进程连续替换两次时进行中的流式响应完整送达，计时记录随之延续"""
    process, pid = process_proxy()
    opened, release = threading.Event(), threading.Event()
    chunks = [b'data: {"id":"handover-response","choices":[]}\n\n', b'data: [DONE]\n\n']
    async def upstream(request):
        if request.path == '/new':
            return web.Response(body=b'new request works')
        response = web.StreamResponse(headers={'Content-Type': 'text/event-stream'})
        await response.prepare(request)
        await response.write(chunks[0])
        opened.set()
        while not release.is_set():
            await asyncio.sleep(.01)
        await response.write(chunks[1])
        return response
    app = web.Application()
    app.router.add_get('/{name}', upstream)
    with serving(app) as upstream_url, ThreadPoolExecutor() as pool:
        base = timing.route_url('claude', upstream_url)
        def read():
            with urllib.request.urlopen(base + '/old', timeout=15) as response:
                return response.read()
        response = pool.submit(read)
        try:
            assert opened.wait(5)
            original = wait_for(lambda: timing.lookup('claude', 'handover-response'))
            current = process.pid
            for _ in range(2):
                os.kill(current, signal.SIGHUP)
                current = wait_for(lambda: (value if (value := pid()) != current else None))
                assert process.poll() is None
                with urllib.request.urlopen(base + '/new', timeout=3) as new:
                    assert new.read() == b'new request works'
            release.set()
            assert response.result(5) == b''.join(chunks)
            final = wait_for(lambda: (value if (value := timing.lookup('claude', 'handover-response')).get('finished_at') else None))
            assert final['started_at'] == original['started_at']
            assert final['finished_at'] >= final['started_at']
            process.wait(timeout=5)
            assert pid() == current
        finally:
            release.set()


def test_websocket_stays_on_old_process_until_peer_closes(process_proxy):
    """进程交接后已有 WebSocket 留在旧进程，直到对端关闭"""
    process, pid = process_proxy()
    async def upstream(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        async for message in ws:
            await ws.send_str(message.data)
        return ws
    app = web.Application()
    app.router.add_get('/', upstream)
    with serving(app) as upstream_url:
        url = timing.route_url('codex', upstream_url) + '/'
        async def check():
            async with ClientSession() as session, session.ws_connect(url) as ws:
                await ws.send_str('before')
                assert (await ws.receive(timeout=3)).data == 'before'
                os.kill(process.pid, signal.SIGHUP)
                current = await asyncio.to_thread(wait_for, lambda: (p if (p := pid()) != process.pid else None))
                assert process.poll() is None
                await ws.send_str('after')
                assert (await ws.receive(timeout=3)).data == 'after'
            return current
        current = asyncio.run(check())
    process.wait(timeout=5)
    assert pid() == current


def test_failed_successor_keeps_current_listener(process_proxy):
    """接替进程启动失败时当前进程继续监听并正常服务"""
    process, pid = process_proxy(fail=True)
    os.kill(process.pid, signal.SIGHUP)
    wait_for(lambda: Path(os.environ['PROBE_SOCKET'] + '.failure').exists())
    assert process.poll() is None
    assert pid() == process.pid
    with urllib.request.urlopen(timing.endpoint() + '/health', timeout=3) as response:
        assert json.load(response)['ok'] is True


def test_upload_and_response_survive_handover(process_proxy):
    """上传中途发生进程交接时请求体和响应都完整送达"""
    process, pid = process_proxy()
    received = threading.Event()
    async def upstream(request):
        first = await request.content.readexactly(5)
        received.set()
        return web.Response(body=first + await request.read())
    app = web.Application(handler_args={'auto_decompress': False})
    app.router.add_post('/', upstream)
    with serving(app) as base:
        route = timing.route_url('codex', base).split(timing.endpoint(), 1)[1] + '/'
        client = http.client.HTTPConnection('127.0.0.1', int(os.environ['PORTAL_PROVIDER_TIMING_PORT']), timeout=5)
        try:
            client.putrequest('POST', route)
            client.putheader('Content-Length', '10')
            client.putheader('Content-Encoding', 'zstd')
            client.endheaders()
            client.send(b'first')
            assert received.wait(3)
            os.kill(process.pid, signal.SIGHUP)
            current = wait_for(lambda: (p if (p := pid()) != process.pid else None))
            assert process.poll() is None
            client.send(b'final')
            response = client.getresponse()
            assert response.status == 200
            assert response.read() == b'firstfinal'
        finally:
            client.close()
    process.wait(timeout=5)
    assert pid() == current
