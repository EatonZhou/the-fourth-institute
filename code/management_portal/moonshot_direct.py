"""Unix transport for the administrator-enabled, non-streaming Moonshot operation."""
import http.client
import os
import socket
import urllib.error
from pathlib import Path

ENABLE_FILE = Path('/etc/portal-moonshot-enabled')
SOCKET_PATH = '/run/portal-moonshot.sock'
URL = 'https://api.moonshot.cn/v1/chat/completions'


class LocalConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(SOCKET_PATH)


def enabled(provider_id, request):
    # A root-only policy directory may be unsearchable. Use a public,
    # non-secret switch under /etc, independent of the private policy.
    return (os.name == 'posix' and provider_id == 'kimi_primary' and request.full_url == URL
            and ENABLE_FILE.is_file())


def open_request(request, timeout):
    conn = LocalConnection('localhost', timeout=timeout)
    try:
        conn.request('POST', '/v1/chat/completions', body=request.data, headers={
            'Authorization': request.get_header('Authorization', ''),
            'Content-Type': 'application/json',
        })
        response = conn.getresponse()
        if not 200 <= response.status < 300:
            raise urllib.error.HTTPError(URL, response.status, response.reason, response.headers, response)
        return response
    except OSError as exc:
        if isinstance(exc, urllib.error.HTTPError):
            raise
        raise urllib.error.URLError(exc) from exc
    finally:
        conn.close()
