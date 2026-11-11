"""Thread-local iLink keep-alive connections; no automatic replay of POSTs."""
from __future__ import annotations

import base64
import time
from http.client import HTTPSConnection
from threading import local
from urllib.parse import unquote, urlsplit
from urllib.request import getproxies, proxy_bypass


class WeixinHTTP:
    def __init__(self):
        self.local = local()

    def request(self, url, method, headers, body, timeout, *, limit, fallback):
        target = urlsplit(url)
        proxy = getproxies().get('https') if not proxy_bypass(target.hostname) else None
        parsed_proxy = urlsplit(proxy) if proxy else None
        # urllib owns HTTPS-proxy TLS-in-TLS handling. Keep that supported path.
        if parsed_proxy and parsed_proxy.scheme != 'http':
            self.local.timing = {}
            return fallback(url, method, headers, body, timeout)
        pool = getattr(self.local, 'pool', None)
        if pool is None:
            pool = self.local.pool = {}
        key = (target.hostname, target.port or 443, proxy)
        connection = pool.get(key)
        reused = connection is not None and connection.sock is not None
        if connection is None:
            if len(pool) >= 8:
                _, old = pool.popitem()
                old.close()
            host, port = (parsed_proxy.hostname, parsed_proxy.port or 80) if parsed_proxy else key[:2]
            connection = pool[key] = HTTPSConnection(host, port, timeout=timeout)
            if parsed_proxy:
                tunnel_headers = {}
                if parsed_proxy.username:
                    credentials = unquote(parsed_proxy.username) + ':' + unquote(parsed_proxy.password or '')
                    tunnel_headers['Proxy-Authorization'] = 'Basic ' + base64.b64encode(credentials.encode()).decode()
                connection.set_tunnel(target.hostname, target.port or 443, headers=tunnel_headers)
        connection.timeout = timeout
        started = time.monotonic()
        try:
            if connection.sock is None:
                connection.connect()
            else:
                connection.sock.settimeout(timeout)
            connected = time.monotonic()
            path = target.path or '/'
            if target.query:
                path += '?' + target.query
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            data = response.read(limit + 1)
            if len(data) > limit or not 200 <= response.status < 300:
                raise OSError('iLink HTTP response unavailable')
            self.local.timing = {'connection_reused': reused,
                'connect_seconds': connected - started, 'http_seconds': time.monotonic() - connected}
            return data, dict(response.headers)
        except Exception:
            pool.pop(key, None)
            connection.close()
            self.local.timing = {}
            raise


HTTP = WeixinHTTP()
