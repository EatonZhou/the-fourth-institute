from __future__ import annotations

import base64
import http.client

import json
from pathlib import Path
from urllib.parse import urlencode

from tests.cases.security_http_support import server as _server

import pytest

from management_portal import serve_management_portal as portal
from management_portal.portal_access_control import PortalAccessStore, PortalCredentialStore
from management_portal.portal_two_factor import (
    PortalTwoFactorStore,
    generate_totp_secret,
    match_totp,
    totp_code,
)


def test_rfc_6238_sha1_vector_contracts(subtests, isolated_portal_case):
    """TOTP 计算符合 RFC 6238 SHA1 测试向量，时间窗内可匹配且同一计数不能重放"""
    with subtests.test(contract='test_rfc_6238_sha1_vector'), isolated_portal_case() as case_path:
        secret = base64.b32encode(b'12345678901234567890').decode('ascii')
        assert totp_code(secret, at_time=59, digits=8) == '94287082'
    with subtests.test(contract='test_totp_window_and_replay_counter'), isolated_portal_case() as case_path:
        secret = generate_totp_secret()
        code = totp_code(secret, at_time=90)
        counter = match_totp(secret, code, at_time=119)
        assert counter == 3
        assert match_totp(secret, code, at_time=119, last_counter=counter) is None


def test_store_encrypts_seed_and_consumes_recovery_code_once_contracts(tmp_path, subtests, isolated_portal_case):
    """两步验证种子和恢复码加密存储，恢复码只能用一次，密钥文件丢失时拒绝验证"""
    with subtests.test(contract='test_store_encrypts_seed_and_consumes_recovery_code_once'), isolated_portal_case() as case_path:
        tmp_path = case_path
        key_path = tmp_path / 'two_factor.key'
        store = PortalTwoFactorStore(key_path)
        secret = generate_totp_secret()
        recovery_codes = store.enable('engineer', secret)
        raw = json.dumps(store.state.read({}))
        assert secret not in raw
        assert recovery_codes[0] not in raw
        assert store.state.kind == 'two_factor'
        assert key_path.stat().st_mode & 511 == 384
        assert store.verify('engineer', recovery_codes[0]) == 'recovery'
        assert store.verify('engineer', recovery_codes[0]) is None
        assert store.status('engineer')['recovery_codes_remaining'] == len(recovery_codes) - 1
    with subtests.test(contract='test_existing_store_without_key_fails_closed'), isolated_portal_case() as case_path:
        tmp_path = case_path
        store = PortalTwoFactorStore(tmp_path / 'missing.key')
        store.enable('engineer', generate_totp_secret())
        store.key_path.unlink()
        with pytest.raises(RuntimeError, match='key is missing'):
            store.verify('engineer', '123456')


def test_setup_confirmation_enables_factor_and_rejects_initial_code_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """确认两步验证设置后启用并生成 10 个恢复码，设置时用过的验证码不能重放"""
    store = PortalTwoFactorStore(tmp_path / "two_factor.key")
    secret = generate_totp_secret()
    token = "pending-setup"
    monkeypatch.setattr(portal, "_PORTAL_TWO_FACTOR", store)
    portal._TWO_FACTOR_SETUPS.clear()
    portal._TWO_FACTOR_SETUPS[token] = {
        "username": "engineer",
        "secret": secret,
        "expires_at": 4_102_444_800,
    }
    code = totp_code(secret)

    recovery_codes = portal._confirm_two_factor_setup("engineer", token, code)

    assert len(recovery_codes) == 10
    assert store.enabled("engineer") is True
    assert store.verify("engineer", code) is None
    assert token not in portal._TWO_FACTOR_SETUPS


class _QuietHandler(portal.Handler):
    def log_message(self, _format: str, *_args: object) -> None:
        return




def _request(server, method: str, path: str, *, headers=None, body: bytes = b""):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        return response.status, response.getheaders(), response.read()
    finally:
        connection.close()


def test_enrolled_login_requires_second_factor_before_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """已启用两步验证的用户登录时先跳转验证页，通过验证码后才签发会话"""
    access = PortalAccessStore()
    access.upsert_user("engineer", actor="CASE-A")
    PortalCredentialStore().set_password("engineer", "TwoFactor-Test9!")
    factor_store = PortalTwoFactorStore(tmp_path / "two_factor.key")
    secret = generate_totp_secret()
    factor_store.enable("engineer", secret)
    signing_key_path = tmp_path / "session.key"
    signing_key_path.write_bytes(b"two-factor-test-session-signing-key-32-bytes")
    monkeypatch.setattr(portal, "_PORTAL_ACCESS", access)
    monkeypatch.setattr(portal, "_PORTAL_TWO_FACTOR", factor_store)
    monkeypatch.setattr(portal, "_SESSION_SIGNING_KEY_PATH", signing_key_path)
    monkeypatch.setattr(portal, "_SESSION_AUTHORITY", None)
    monkeypatch.setattr(portal, "_SESSION_AUTHORITY_CONFIG", None)
    monkeypatch.setattr(portal, "_verify_credentials", lambda *_args: True)
    monkeypatch.setattr(portal, "_portal_user_allowed", lambda *_args: True)
    monkeypatch.setattr(portal, "_load_users", lambda: {"engineer": "hash"})
    portal._LOGIN_CHALLENGES.clear()

    with _server(_QuietHandler) as server:
        origin = f"http://127.0.0.1:{server.server_address[1]}"
        body = urlencode({"username": "engineer", "password": "valid"}).encode()
        status, headers, _body = _request(
            server,
            "POST",
            "/login",
            headers={"Content-Type": "application/x-www-form-urlencoded", "Origin": origin},
            body=body,
        )
        header_map = dict(headers)
        assert status == 302
        assert header_map["Location"] == "/login/verify"
        challenge_cookie = next(
            value.split(";", 1)[0] for key, value in headers
            if key.lower() == "set-cookie" and value.startswith("db_login_challenge=")
        )
        assert all("db_session=" not in value for key, value in headers if key.lower() == "set-cookie")

        verify_body = urlencode({"code": totp_code(secret)}).encode()
        status, headers, _body = _request(
            server,
            "POST",
            "/login/verify",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Cookie": challenge_cookie,
                "Origin": origin,
            },
            body=verify_body,
        )

    assert status == 302
    assert dict(headers)["Location"] == "/"
    assert any(
        value.startswith("db_session=") and "HttpOnly" in value
        for key, value in headers if key.lower() == "set-cookie"
    )
