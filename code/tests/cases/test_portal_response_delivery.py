from __future__ import annotations

import gzip
import json
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import quote

from management_portal import serve_management_portal


class _QuietHandler(serve_management_portal.Handler):
    def log_message(self, _format: str, *_args: object) -> None:
        return


def test_public_login_language_assets_are_served_without_authentication(isolated_portal_case, monkeypatch):
    """未登录页面可加载语言模块与词典

    匿名 HTTP 请求直接获取语言脚本，不重定向登录页，返回正确 JavaScript 内容。

    :module: workbench.panels
    :importance: P1
    :design: decision_27253e25f016
    """
    with isolated_portal_case():
        monkeypatch.setattr(serve_management_portal, '_get_session_username', lambda handler: '')
        server = ThreadingHTTPServer(('127.0.0.1', 0), _QuietHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for name in ('portal_text.js', 'portal_i18n.js', 'portal_translations.js'):
                url = f'http://127.0.0.1:{server.server_address[1]}/static/{name}'
                with urllib.request.urlopen(url) as response:
                    assert response.status == 200
                    assert response.geturl() == url, 'public language assets must not redirect to login'
                    assert 'javascript' in response.headers['Content-Type']
                    expected = (Path(serve_management_portal.__file__).parent / 'static' / name).read_bytes()
                    assert response.read() == expected
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


def test_a_static_file_outside_the_public_list_is_revalidated_not_cached_by_heuristics(isolated_portal_case, monkeypatch):
    """公共列表外的静态脚本也要求每次重新验证，ETag 未变时返回 304

    Pages load some scripts on demand (portal_user_avatar.js) from files not
    on the public static list.  Served with Last-Modified alone, a browser kept
    an old copy for hours and new Chat code called functions it lacked."""
    with isolated_portal_case():
        monkeypatch.setattr(serve_management_portal, '_get_session_username', lambda handler: 'alice')
        server = ThreadingHTTPServer(('127.0.0.1', 0), _QuietHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f'http://127.0.0.1:{server.server_address[1]}/static/portal_user_avatar.js'
        try:
            with urllib.request.urlopen(url) as response:
                body = response.read()
                etag = response.headers['ETag']
                assert response.headers['Cache-Control'] == 'public, max-age=0, must-revalidate'
                assert etag
            expected = (Path(serve_management_portal.__file__).parent / 'static' / 'portal_user_avatar.js').read_bytes()
            assert body == expected
            try:
                urllib.request.urlopen(urllib.request.Request(url, headers={'If-None-Match': etag}))
            except urllib.error.HTTPError as error:
                assert error.code == 304
            else:
                raise AssertionError('an unchanged static file should revalidate to 304')
            for outside in ('/static/../serve_management_portal.py', '/static/%2e%2e/serve_management_portal.py'):
                try:
                    with urllib.request.urlopen(f'http://127.0.0.1:{server.server_address[1]}{outside}') as response:
                        assert response.headers.get('Cache-Control') != 'public, max-age=0, must-revalidate'
                except urllib.error.HTTPError:
                    pass
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


def test_static_assets_support_gzip_and_etag_revalidation_contracts(subtests, isolated_portal_case, monkeypatch):
    """静态资源支持 gzip 和 ETag 重新验证，只有 Release 当前构建版本的资源长期缓存"""
    with subtests.test(contract='test_static_assets_support_gzip_and_etag_revalidation'), isolated_portal_case() as case_path:
        server = ThreadingHTTPServer(('127.0.0.1', 0), _QuietHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f'http://127.0.0.1:{server.server_address[1]}/static/portal_api.js'
        try:
            request = urllib.request.Request(url, headers={'Accept-Encoding': 'gzip'})
            with urllib.request.urlopen(request) as response:
                compressed = response.read()
                etag = response.headers['ETag']
                assert response.headers['Content-Encoding'] == 'gzip'
                assert response.headers['Cache-Control'] == 'public, max-age=0, must-revalidate'
                assert response.headers['Vary'] == 'Accept-Encoding'
            expected = (Path(serve_management_portal.__file__).parent / 'static' / 'portal_api.js').read_bytes()
            assert gzip.decompress(compressed) == expected
            revalidation = urllib.request.Request(url, headers={'Accept-Encoding': 'gzip', 'If-None-Match': etag})
            try:
                urllib.request.urlopen(revalidation)
            except urllib.error.HTTPError as error:
                assert error.code == 304
                assert error.headers['ETag'] == etag
                assert error.read() == b''
            else:
                raise AssertionError('static revalidation should return 304')
            for instance in ('dev', 'release'):
                with monkeypatch.context() as patch:
                    patch.setattr(serve_management_portal, '_PORTAL_INSTANCE', instance)
                    for version in ('', 'previous-build', serve_management_portal._PORTAL_BUILD_ID):
                        versioned_url = f'{url}?portal_v={quote(str(version), safe="")}'
                        expected_cache = (
                            'public, max-age=31536000, immutable'
                            if instance == 'release' and version == serve_management_portal._PORTAL_BUILD_ID
                            else 'public, max-age=0, must-revalidate'
                        )
                        versioned = urllib.request.Request(versioned_url, headers={'Accept-Encoding': 'gzip'})
                        with urllib.request.urlopen(versioned) as response:
                            assert response.headers['Cache-Control'] == expected_cache
                            assert response.headers['ETag'] == etag
                            assert gzip.decompress(response.read()) == expected
                        revalidation = urllib.request.Request(versioned_url, headers={'Accept-Encoding': 'gzip', 'If-None-Match': etag})
                        try:
                            urllib.request.urlopen(revalidation)
                        except urllib.error.HTTPError as error:
                            assert error.code == 304
                            assert error.headers['Cache-Control'] == expected_cache
                            assert error.headers['ETag'] == etag
                            assert error.read() == b''
                        else:
                            raise AssertionError('versioned static revalidation should return 304')
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
    with subtests.test(contract='test_html_static_references_receive_current_build_version'), isolated_portal_case() as case_path:
        source = b'<script src="/static/portal_identity.js"></script><link href="/static/app.css?theme=1">'
        versioned = serve_management_portal._version_static_asset_urls(source)
        version = quote(str(serve_management_portal._PORTAL_BUILD_ID), safe='').encode('ascii')
        assert b'/static/portal_identity.js?portal_v=' + version in versioned
        assert b'/static/app.css?theme=1&amp;' not in versioned
        assert b'/static/app.css?theme=1&portal_v=' + version in versioned
    with subtests.test(contract='test_concurrent_static_requests_share_one_compression'), isolated_portal_case() as case_path:
        asset = case_path / 'cacheable.js'
        asset.write_bytes(b'const payload = "static-cache";\n' * 200)
        real_compress = serve_management_portal.gzip.compress
        compression_calls = 0

        def counted_compress(*args, **kwargs):
            nonlocal compression_calls
            compression_calls += 1
            return real_compress(*args, **kwargs)

        monkeypatch.setattr(serve_management_portal.gzip, 'compress', counted_compress)
        with serve_management_portal._STATIC_PAYLOAD_CACHE_LOCK:
            serve_management_portal._STATIC_PAYLOAD_CACHE.clear()
            serve_management_portal._STATIC_PAYLOAD_BUILD_LOCKS.clear()

        def request_static(_index):
            handler = _ResponseHarness()
            handler.path = '/static/cacheable.js?portal_v=test'
            serve_management_portal._serve_static_file(
                handler,
                asset,
                'application/javascript; charset=utf-8',
            )
            return gzip.decompress(handler.wfile.getvalue())

        with ThreadPoolExecutor(max_workers=12) as executor:
            payloads = list(executor.map(request_static, range(12)))
        assert payloads == [asset.read_bytes()] * 12
        assert compression_calls == 1
    with subtests.test(contract='test_large_json_responses_are_compressed_but_remain_no_store'), isolated_portal_case() as case_path:
        handler = _ResponseHarness()
        payload = {'items': [{'id': index, 'text': 'resource payload' * 20} for index in range(40)]}
        serve_management_portal._serve_json(handler, 200, payload)
        assert handler.status == 200
        assert handler.response_headers['Content-Encoding'] == 'gzip'
        assert handler.response_headers['Cache-Control'] == 'no-store'
        assert handler.response_headers['Vary'] == 'Accept-Encoding'
        assert json.loads(gzip.decompress(handler.wfile.getvalue())) == payload
    with subtests.test(contract='test_authenticated_documents_are_private_and_revalidated'), isolated_portal_case() as case_path:
        handler = _ResponseHarness()
        serve_management_portal._serve_authenticated_document(handler, 200, b'<!doctype html>')
        assert handler.response_headers['Cache-Control'] == 'private, no-cache'
    with subtests.test(contract='test_public_and_sensitive_html_remain_no_store'), isolated_portal_case() as case_path:
        handler = _ResponseHarness()
        serve_management_portal._serve_bytes(handler, 200, b'<!doctype html>', 'text/html; charset=utf-8')
        assert handler.response_headers['Cache-Control'] == 'no-store'
    with subtests.test(contract='test_extra_headers_cannot_bypass_cache_policy'), isolated_portal_case() as case_path:
        handler = _ResponseHarness()
        try:
            serve_management_portal._serve_bytes(
                handler,
                200,
                b'payload',
                'text/plain',
                extra_headers={'Cache-Control': 'public'},
            )
        except ValueError as error:
            assert 'response policy' in str(error)
        else:
            raise AssertionError('Cache-Control must use the response policy argument')
    with subtests.test(contract='test_document_routes_apply_cache_policy_after_authentication'), isolated_portal_case() as case_path:
        authenticated = {"value": True}
        monkeypatch.setattr(
            serve_management_portal,
            "_check_session",
            lambda _handler: authenticated["value"],
        )
        monkeypatch.setattr(
            serve_management_portal,
            "_get_session_username",
            lambda _handler: "sample_admin" if authenticated["value"] else None,
        )
        server = ThreadingHTTPServer(("127.0.0.1", 0), _QuietHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            with urllib.request.urlopen(f"{origin}/") as response:
                assert response.headers["Cache-Control"] == "private, no-cache"
            authenticated["value"] = False
            with urllib.request.urlopen(f"{origin}/login") as response:
                assert response.headers["Cache-Control"] == "no-store"
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


class _ResponseHarness:
    def __init__(self) -> None:
        from io import BytesIO

        self.headers = {"Accept-Encoding": "gzip"}
        self.wfile = BytesIO()
        self.status = 0
        self.response_headers: dict[str, str] = {}

    def send_response(self, status: int) -> None:
        self.status = status

    def send_header(self, name: str, value: str) -> None:
        self.response_headers[name] = value

    def end_headers(self) -> None:
        return
