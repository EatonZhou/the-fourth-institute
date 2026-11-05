"""Static-only deployment preserves the URL-to-bytes cache contract.

:module: platform.core
:design: decision_f25266e5a0e7
:importance: P1
"""
import gzip
import hashlib
import os
import time
from html import unescape
from urllib.parse import parse_qs, urlparse

import pytest

from management_portal import http_instance, http_static_files as static
from management_portal import http_static_versions as versions
from tests.cases.test_portal_response_delivery import _ResponseHarness


@pytest.fixture(autouse=True)
def version_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(versions, '_directory', lambda: tmp_path / 'versions')


def test_static_only_update_and_old_document_recovery(tmp_path, monkeypatch):
    monkeypatch.setattr(static, 'ROOT', tmp_path)
    monkeypatch.setattr(http_instance, '_PORTAL_DEPLOYMENT', 'release')
    monkeypatch.setattr(http_instance, '_PORTAL_BUILD_ID', 'development')
    (tmp_path / 'static').mkdir()
    asset = tmp_path / 'static/app.js'
    asset.write_bytes(b'const old = true;\n' * 200)
    html = b'<script src="/static/app.js?portal_v=development"></script>'
    old_html = static._version_static_asset_urls(html)
    old_url = unescape(old_html.decode().split('"')[1])
    old_bytes = asset.read_bytes()

    def serve(url, etag=''):
        handler = _ResponseHarness()
        handler.path = url
        handler.headers['If-None-Match'] = etag
        static._serve_static_file(handler, asset, 'application/javascript')
        return handler

    first = serve(old_url)
    assert first.response_headers['Cache-Control'].endswith('immutable')
    old_stat = asset.stat()
    asset.write_bytes(b'const new = true;\n' * 200)
    # Same length/mtime must still invalidate the payload; ctime is part of its key.
    os.utime(asset, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
    new_html = static._version_static_asset_urls(html)
    assert new_html != old_html
    new_url = unescape(new_html.decode().split('"')[1])
    assert parse_qs(urlparse(new_url).query)['portal_v'] == ['sha256-' + hashlib.sha256(asset.read_bytes()).hexdigest()]
    # Simulate a new Web worker with no process-local file payloads.
    static._STATIC_PAYLOAD_CACHE.clear()
    stale = serve(old_url)
    assert stale.status == 200
    assert gzip.decompress(stale.wfile.getvalue()) == old_bytes
    assert stale.response_headers['Cache-Control'].endswith('immutable')
    assert serve(old_url, first.response_headers['ETag']).status == 304
    current = serve(new_url)
    assert current.status == 200
    assert gzip.decompress(current.wfile.getvalue()) == asset.read_bytes()
    assert current.response_headers['Cache-Control'].endswith('immutable')
    unchanged = serve(new_url, current.response_headers['ETag'])
    assert unchanged.status == 304
    assert unchanged.response_headers['Cache-Control'] == current.response_headers['Cache-Control']
    for url in ('/static/app.js', '/static/app.js?portal_v=development'):
        legacy = serve(url)
        assert legacy.status == 200
        assert legacy.response_headers['Cache-Control'] == 'public, max-age=0, must-revalidate'
    monkeypatch.setattr(http_instance, '_PORTAL_DEPLOYMENT', 'dev')
    assert serve(new_url).response_headers['Cache-Control'] == 'public, max-age=0, must-revalidate'


def test_retained_versions_are_bound_to_path_digest_and_retention(tmp_path, monkeypatch):
    monkeypatch.setattr(static, 'ROOT', tmp_path)
    (tmp_path / 'static').mkdir()
    asset = tmp_path / 'static/app.js'
    asset.write_bytes(b'old script')
    old = static._version_static_asset_urls(b'<script src="/static/app.js"></script>')
    url = unescape(old.decode().split('"')[1])
    version = parse_qs(urlparse(url).query)['portal_v'][0]
    asset.write_bytes(b'new script')
    other = tmp_path / 'static/other.js'
    other.write_bytes(b'other script')

    def rejected(path, request_url):
        handler = _ResponseHarness()
        handler.path = request_url
        static._serve_static_file(handler, path, 'application/javascript')
        assert handler.status == 409
        assert handler.response_headers['Cache-Control'] == 'no-store'

    rejected(other, url.replace('app.js', 'other.js'))
    rejected(asset, '/static/app.js?portal_v=sha256-' + 'a' * 64)
    entry = versions._entry(asset, version)
    entry.write_bytes(b'corrupt')
    rejected(asset, url)
    entry.write_bytes(b'old script')
    expired = time.time() - versions.RETENTION_SECONDS - 1
    os.utime(entry, (expired, expired))
    rejected(asset, url)


def test_version_cache_is_bounded_and_optional(tmp_path, monkeypatch):
    monkeypatch.setattr(versions, 'MAX_BYTES', 6)
    monkeypatch.setattr(versions, 'MAX_FILES', 2)
    for i in range(4):
        data = str(i).encode() * 3
        versions.remember(tmp_path / str(i), 'sha256-' + hashlib.sha256(data).hexdigest(), data)
    entries = list(versions._directory().glob('*.asset'))
    assert len(entries) == 2
    assert sum(p.stat().st_size for p in entries) <= 6
    blocked = tmp_path / 'not-a-directory'
    blocked.write_text('x')
    monkeypatch.setattr(versions, '_directory', lambda: blocked)
    versions.remember(tmp_path / 'app.js', 'sha256-' + hashlib.sha256(b'abc').hexdigest(), b'abc')
    assert versions.recall(tmp_path / 'app.js', 'sha256-' + 'a' * 64) is None


def test_versioning_cannot_read_outside_static_root(tmp_path, monkeypatch):
    monkeypatch.setattr(static, 'ROOT', tmp_path)
    (tmp_path / 'static').mkdir()
    (tmp_path / 'secret.js').write_text('not public')
    (tmp_path / 'static/link.js').symlink_to(tmp_path / 'secret.js')
    for url in ('/static/../secret.js', '/static/%2e%2e/secret.js', '/static/link.js'):
        html = f'<script src="{url}"></script>'.encode()
        assert static._version_static_asset_urls(html) == html


def test_browser_finishes_document_across_static_publication(tmp_path, monkeypatch):
    """HTML is issued, scripts change, then Chromium fetches its dependencies."""
    import json
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from pathlib import Path
    import subprocess
    from threading import Thread
    from management_portal.page_bootstrap import inject_page_bootstrap

    monkeypatch.setattr(static, 'ROOT', tmp_path)
    monkeypatch.setattr(http_instance, '_PORTAL_DEPLOYMENT', 'release')
    assets = tmp_path / 'static'
    assets.mkdir()
    (assets / 'portal_theme.js').write_text('window.themeReady = true;')

    def release(version):
        (assets / 'dependency.js').write_text(f'window.dependency = {{ version: {version} }};')
        (assets / 'entry.js').write_text(
            f'if (dependency.version !== {version}) throw new Error("mixed versions");'
            'const root = document.getElementById("workspace");'
            'root.textContent = "ready version " + dependency.version; PortalPageBoot.ready(root);')

    release(1)
    statuses = []
    documents = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def send_response(self, status, *args):
            statuses.append(status)
            super().send_response(status, *args)

        def do_GET(self):
            if self.path == '/':
                html = b'''<!doctype html><html><head><script src="/static/portal_theme.js"></script></head>
                <body><main id="workspace" data-portal-boot-required="Workspace"></main>
                <script src="/static/dependency.js"></script><script src="/static/entry.js"></script></body></html>'''
                html = inject_page_bootstrap(static._version_static_asset_urls(html))
                documents.append(html)
                if len(documents) == 1:
                    release(2)
                    static._STATIC_PAYLOAD_CACHE.clear()
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(html)))
                self.end_headers()
                self.wfile.write(html)
            elif self.path.startswith('/static/'):
                file = assets / Path(urlparse(self.path).path).name
                static._serve_static_file(self, file, 'application/javascript')
            else:
                self.send_response(204)
                self.end_headers()

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        script = Path(__file__).with_name('javascript') / 'static_publication.browser.cjs'
        result = subprocess.run(['node', str(script)], text=True, capture_output=True, timeout=45,
            input=json.dumps({'url': f'http://127.0.0.1:{server.server_port}/'}))
        assert result.returncode == 0, result.stdout + result.stderr
        assert len(documents) == 2, 'no failed boot or automatic document reload'
        assert all(s < 400 for s in statuses)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
