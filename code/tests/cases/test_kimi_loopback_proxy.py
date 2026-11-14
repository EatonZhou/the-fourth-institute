"""Native control HTTP must not mistake a VPN proxy's 503 for Kimi failure."""
from contextlib import nullcontext
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
import threading
from urllib.error import HTTPError
from urllib.request import urlopen as ordinary_open

import pytest

from management_common.loopback_http import urlopen
from management_portal.execution.account_observation import ObservationLifetime
from management_portal.kimi_code_client import KimiCodeClient, KimiCodeError, KimiCodeUnavailable


@pytest.fixture
def endpoints(monkeypatch):
    requests = []
    class Proxy(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(503)
            self.end_headers()
        def log_message(self, *_): pass
    class Native(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(503 if self.path.endswith('/failure') else 200)
            self.end_headers()
            self.wfile.write(b'{"code":0,"data":[]}')
        def log_message(self, *_): pass
    servers = [ThreadingHTTPServer(('127.0.0.1', 0), handler) for handler in (Proxy, Native)]
    threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in servers]
    for t in threads: t.start()
    proxy = 'http://127.0.0.1:' + str(servers[0].server_port)
    for key in ('http_proxy', 'HTTP_PROXY', 'https_proxy', 'HTTPS_PROXY'):
        monkeypatch.setenv(key, proxy)
    for key in ('no_proxy', 'NO_PROXY'):
        monkeypatch.setenv(key, '')
    try:
        yield 'http://127.0.0.1:' + str(servers[1].server_port), requests
    finally:
        for s in servers: s.shutdown(); s.server_close()
        for t in threads: t.join(2)


@pytest.mark.parametrize('observing', [False, True])
def test_kimi_control_and_owned_observer_skip_proxy(endpoints, observing, tmp_path):
    base, requests = endpoints
    owner = ObservationLifetime()
    client = KimiCodeClient(base + '/api/v1', home=tmp_path)
    with owner.scope() if observing else nullcontext():
        assert client._request('GET', 'sessions', timeout=5) == []
    owner.close()
    assert requests == []


def test_startup_connection_refusal_remains_retryable(endpoints, tmp_path):
    _, requests = endpoints
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
    base = 'http://127.0.0.1:' + str(port)
    with pytest.raises(HTTPError) as failure:
        ordinary_open(base, timeout=2)
    assert failure.value.code == 503
    requests.clear()
    client = KimiCodeClient(base + '/api/v1', home=tmp_path)
    with pytest.raises(KimiCodeUnavailable):
        client._request('GET', 'sessions', timeout=2)
    assert requests == []


def test_remote_requests_keep_proxy_and_native_http_errors_remain_errors(endpoints, tmp_path):
    base, requests = endpoints
    with pytest.raises(HTTPError) as failure:
        urlopen('http://provider.invalid/models', timeout=2)
    assert failure.value.code == 503 and requests == ['http://provider.invalid/models']
    requests.clear()
    client = KimiCodeClient(base + '/api/v1', home=tmp_path)
    with pytest.raises(KimiCodeError, match='HTTP 503'):
        client._request('GET', 'failure', timeout=2)
    assert requests == []
