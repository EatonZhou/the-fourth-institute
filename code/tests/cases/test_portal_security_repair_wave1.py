from __future__ import annotations

from http.server import ThreadingHTTPServer
import http.client
import base64
import hashlib
import hmac
import json
from pathlib import Path

from tests.cases.security_http_support import server as _server

import pytest

from management_portal import portal_deployment_router as router_module
from management_portal import serve_management_portal as portal
from management_portal.portal_access_control import PortalAccessStore, PortalCredentialStore
from management_portal.portal_deployment_registry import PortalDeployment


class _QuietHandler(portal.Handler):
    def log_message(self, _format: str, *_args: object) -> None:
        return




class _TrustedHttpsHandler(_QuietHandler):
    # ASGI transport sets this after its trusted proxy middleware.
    _asgi_request_scheme = "https"


def _request(
    server: ThreadingHTTPServer,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    body: bytes = b"",
) -> tuple[int, dict[str, str], bytes]:
    connection = http.client.HTTPConnection(
        "127.0.0.1", server.server_address[1], timeout=5
    )
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def _access_store(tmp_path: Path) -> PortalAccessStore:
    store = PortalAccessStore()
    store.upsert_user("engineer", actor="CASE-A")
    PortalCredentialStore().set_password("engineer", "Session-Test9!")
    return store


def _configure_portal_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    access: PortalAccessStore,
) -> Path:
    key_path = tmp_path / "session.key"
    key_path.write_bytes(b"test-portal-session-signing-key-32-bytes")
    monkeypatch.setattr(portal, "_SESSION_SIGNING_KEY_PATH", key_path)
    monkeypatch.setattr(portal, "_PORTAL_ACCESS", access)
    monkeypatch.setattr(portal, "_SESSION_AUTHORITY", None)
    monkeypatch.setattr(portal, "_SESSION_AUTHORITY_CONFIG", None)
    return key_path


def _signed_token(key: bytes, username: str, session_version: int) -> str:
    payload = {
        "v": 1,
        "u": username,
        "iat": 1,
        "exp": 4_102_444_800,
        "sv": session_version,
        "nonce": "test",
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).decode("ascii").rstrip("=")
    signature = base64.urlsafe_b64encode(
        hmac.new(key, encoded.encode("ascii"), hashlib.sha256).digest()
    ).decode("ascii").rstrip("=")
    return f"{encoded}.{signature}"


def test_missing_session_key_is_generated_contracts(monkeypatch, tmp_path, subtests, isolated_portal_case):
    """缺少会话签名密钥时自动生成，Web 与 Router 共用会话校验，外源登录被拒且登出只接受 POST"""
    with subtests.test(contract='test_missing_session_key_is_generated'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            tmp_path = case_path
            signing_key_path = tmp_path / 'session.key'
            monkeypatch.setattr(portal, '_SESSION_SIGNING_KEY_PATH', signing_key_path)
            portal._ensure_session_signing_key()
            assert len(signing_key_path.read_bytes()) == 32
            assert signing_key_path.stat().st_mode & 0o777 == 0o600
    with subtests.test(contract='test_web_and_router_share_the_same_session_authority'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            tmp_path = case_path
            access = _access_store(tmp_path)
            key_path = _configure_portal_session(tmp_path, monkeypatch, access)
            token = portal._issue_session_token('engineer')
            verifier = router_module.PortalSessionAuthority(key_path, access_store=access)
            assert portal._verify_session_token(token) == 'engineer'
            assert verifier.verify(token) == 'engineer'
            assert verifier.verify_cookie(f'db_session={token}; db_session=invalid') == 'engineer'
    with subtests.test(contract='test_login_rejects_foreign_origin_before_issuing_a_cookie'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            tmp_path = case_path
            access = _access_store(tmp_path)
            _configure_portal_session(tmp_path, monkeypatch, access)
            monkeypatch.setattr(portal, '_verify_credentials', lambda *_args: True)
            monkeypatch.setattr(portal, '_portal_user_allowed', lambda *_args: True)
            monkeypatch.setattr(portal, '_load_users', lambda: {'engineer': 'hash'})
            with _server(_QuietHandler) as server:
                status, headers, _body = _request(server, 'POST', '/login', headers={'Content-Type': 'application/x-www-form-urlencoded', 'Origin': 'https://foreign.example'}, body=b'username=engineer&password=valid')
            assert status == 403
            assert 'Set-Cookie' not in headers
    with subtests.test(contract='test_logout_is_post_only_and_revokes_the_signed_session'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            tmp_path = case_path
            access = _access_store(tmp_path)
            _configure_portal_session(tmp_path, monkeypatch, access)
            monkeypatch.setattr(portal, '_portal_user_allowed', lambda *_args: True)
            monkeypatch.setattr(portal, '_load_users', lambda: {'engineer': 'hash'})
            token = portal._issue_session_token('engineer')
            cookie = f'db_session={token}'
            with _server(_TrustedHttpsHandler) as server:
                for method in ('GET', 'HEAD'):
                    status, _headers, _body = _request(server, method, '/logout', headers={'Cookie': cookie})
                    assert status == 405
                    assert portal._verify_session_token(token) == 'engineer'
                public_origin = f'https://127.0.0.1:{server.server_address[1]}'
                status, headers, _body = _request(server, 'POST', '/logout', headers={'Cookie': cookie, 'Origin': public_origin, 'X-Forwarded-Proto': 'https'})
            assert status == 302
            assert headers['Location'] == '/login'
            assert 'Max-Age=0' in headers['Set-Cookie']
            assert 'Secure' in headers['Set-Cookie']
            assert portal._verify_session_token(token) is None
    with subtests.test(contract='test_https_login_cookie_is_secure'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            tmp_path = case_path
            access = _access_store(tmp_path)
            _configure_portal_session(tmp_path, monkeypatch, access)
            monkeypatch.setattr(portal, '_verify_credentials', lambda *_args: True)
            monkeypatch.setattr(portal, '_portal_user_allowed', lambda *_args: True)
            monkeypatch.setattr(portal, '_load_users', lambda: {'engineer': 'hash'})
            with _server(_TrustedHttpsHandler) as server:
                public_origin = f'https://127.0.0.1:{server.server_address[1]}'
                status, headers, _body = _request(server, 'POST', '/login', headers={'Content-Type': 'application/x-www-form-urlencoded', 'Origin': public_origin, 'X-Forwarded-Proto': 'https'}, body=b'username=engineer&password=valid')
            assert status == 302
            assert 'Secure' in headers['Set-Cookie']


def test_router_rejects_stale_session_versions_contracts(tmp_path, subtests, isolated_portal_case):
    """会话版本被撤销或用户被停用后，Router 拒绝原会话令牌"""
    with subtests.test(contract='test_router_rejects_stale_session_versions'), isolated_portal_case() as case_path:
        tmp_path = case_path
        access = _access_store(tmp_path)
        key_path = tmp_path / 'session.key'
        key = b'router-session-security-key-32-bytes'
        key_path.write_bytes(key)
        token = _signed_token(key, 'engineer', access.session_version('engineer'))
        verifier = router_module.PortalSessionAuthority(key_path, access_store=access)
        assert verifier.verify(token) == 'engineer'
        access.upsert_user('engineer', actor='CASE-A', revoke_sessions=True)
        assert verifier.verify(token) == ''
    with subtests.test(contract='test_router_rejects_disabled_users'), isolated_portal_case() as case_path:
        tmp_path = case_path
        access = _access_store(tmp_path)
        key_path = tmp_path / 'session.key'
        key = b'router-session-security-key-32-bytes'
        key_path.write_bytes(key)
        token = _signed_token(key, 'engineer', access.session_version('engineer'))
        verifier = router_module.PortalSessionAuthority(key_path, access_store=access)
        access.upsert_user('engineer', actor='CASE-A', status='disabled')
        assert verifier.verify(token) == ''


def test_router_rebuilds_forwarded_metadata_at_the_trust_boundary_contracts(subtests, isolated_portal_case):
    """Router 只信任本机代理的转发头，部署来源只接受回环 IP，公开诊断只返回白名单字段"""
    with subtests.test(contract='test_router_rebuilds_forwarded_metadata_at_the_trust_boundary'), isolated_portal_case() as case_path:
        assert router_module._forwarded_metadata('192.168.115.25', {'X-Forwarded-For': '127.0.0.1', 'X-Forwarded-Proto': 'https'}) == ('192.168.115.25', 'http')
        assert router_module._forwarded_metadata('127.0.0.1', {'X-Forwarded-For': '203.0.113.8', 'X-Forwarded-Proto': 'https'}) == ('203.0.113.8', 'https')
    with subtests.test(contract='test_deployment_registry_only_accepts_loopback_ip_origins'), isolated_portal_case() as case_path:
        base = {'id': 'release', 'label': 'Release', 'kind': 'release', 'order': 1}
        assert PortalDeployment.from_payload({**base, 'origin': 'http://127.0.0.1:18085'}).origin == 'http://127.0.0.1:18085'
        for origin in ('http://example.invalid:18085', 'http://localhost:18085', 'http://192.168.115.10:18085'):
            with pytest.raises(ValueError, match='loopback'):
                PortalDeployment.from_payload({**base, 'origin': origin})
    with subtests.test(contract='test_public_router_diagnostics_are_allowlisted'), isolated_portal_case() as case_path:
        assert router_module._public_health_payload() == {'ok': True}
        status = router_module._public_runtime_status({'schema': 'tfi.portal-runtime-status.v1', 'ok': False, 'ready': False, 'state': 'blocked', 'code': 'runtime_status_unavailable', 'terminal': True, 'retryable': False, 'title': 'Unavailable', 'message': 'Try later', 'detail': '/private/path', 'instance': 'release'})
        assert set(status) == {'schema', 'ok', 'ready', 'state', 'code', 'terminal', 'retryable', 'title', 'message'}
        assert router_module._public_backend_error() == {'error': 'Portal backend unavailable', 'code': 'backend_unavailable'}
