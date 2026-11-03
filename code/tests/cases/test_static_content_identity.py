"""Static-only deployment preserves the URL-to-bytes cache contract.

:module: platform.core
:design: decision_f25266e5a0e7
:importance: P1
"""
import gzip
import hashlib
import os
from html import unescape
from urllib.parse import parse_qs, urlparse

from management_portal import http_instance, http_static_files as static
from tests.cases.test_portal_response_delivery import _ResponseHarness


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
    stale = serve(old_url, first.response_headers['ETag'])
    assert stale.status == 409
    assert stale.response_headers['Cache-Control'] == 'no-store'
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


def test_versioning_cannot_read_outside_static_root(tmp_path, monkeypatch):
    monkeypatch.setattr(static, 'ROOT', tmp_path)
    (tmp_path / 'static').mkdir()
    (tmp_path / 'secret.js').write_text('not public')
    (tmp_path / 'static/link.js').symlink_to(tmp_path / 'secret.js')
    for url in ('/static/../secret.js', '/static/%2e%2e/secret.js', '/static/link.js'):
        html = f'<script src="{url}"></script>'.encode()
        assert static._version_static_asset_urls(html) == html
