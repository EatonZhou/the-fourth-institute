from __future__ import annotations

import http.client
import json
from pathlib import Path

from tests.cases.security_http_support import server as _server

import pytest

from management_portal import serve_management_portal as portal
from management_portal import web_accounts
from management_portal.portal_access_control import PortalAccessStore, PortalCredentialStore
from management_portal.portal_passkeys import PortalPasskeyService, PortalPasskeyStore


def test_passkey_store_lifecycle_contracts(tmp_path, subtests, isolated_portal_case):
    """通行密钥可登记、记录使用和删除，拒绝重复登记，验证流程令牌只能用一次"""
    with subtests.test(contract='test_passkey_store_lifecycle'), isolated_portal_case() as case_path:
        tmp_path = case_path
        store = PortalPasskeyStore()
        user_handle = store.user_handle('engineer')
        credential = store.add('engineer', user_handle=user_handle, credential_id=b'credential-id', public_key=b'public-key', sign_count=0, name='Work laptop', transports=['internal'], device_type='single_device', backed_up=False)
        assert store.state.kind == 'passkeys'
        assert store.status('engineer')['count'] == 1
        assert store.credential(credential['credential_id'])[0] == 'engineer'
        store.mark_used(credential['credential_id'], sign_count=1, device_type='multi_device', backed_up=True)
        updated = store.status('engineer')['credentials'][0]
        assert updated['last_used_at'] > 0
        assert updated['backed_up'] is True
        with pytest.raises(ValueError, match='already registered'):
            store.add('engineer', user_handle=user_handle, credential_id=b'credential-id', public_key=b'public-key', sign_count=0, name='Duplicate', transports=[], device_type='single_device', backed_up=False)
        assert store.delete_credential('engineer', credential['credential_id']) is True
        assert store.status('engineer')['enabled'] is False
    with subtests.test(contract='test_passkey_ceremony_tokens_are_single_use'), isolated_portal_case() as case_path:
        tmp_path = case_path
        service = PortalPasskeyService()
        started = service.begin_authentication(rp_id='localhost', origin='http://localhost')
        token = started['ceremony_token']
        record = service._consume(token, 'authentication')
        assert record['rp_id'] == 'localhost'
        with pytest.raises(PermissionError, match='expired'):
            service._consume(token, 'authentication')


class _FakePasskeys:
    def begin_authentication(self, *, rp_id: str, origin: str) -> dict:
        assert rp_id == "127.0.0.1"
        assert origin.startswith("http://127.0.0.1:")
        return {"ceremony_token": "auth-token", "options": {"challenge": "AQID"}}

    def finish_authentication(self, *, ceremony_token: str, credential: dict) -> str:
        assert ceremony_token == "auth-token"
        assert credential["id"] == "credential"
        return "engineer"


class _QuietHandler(portal.Handler):
    def log_message(self, _format: str, *_args: object) -> None:
        return




def _json_request(server, path: str, payload: dict):
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    try:
        connection.request(
            "POST",
            path,
            body=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "Origin": origin},
        )
        response = connection.getresponse()
        return response.status, response.getheaders(), json.loads(response.read())
    finally:
        connection.close()


def test_passkey_login_issues_portal_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """通行密钥验证通过后签发 HttpOnly 的 Portal 会话 cookie"""
    access = PortalAccessStore()
    access.upsert_user("engineer", actor="CASE-A")
    PortalCredentialStore().set_password("engineer", "Passkey-Test9!")
    signing_key_path = tmp_path / "session.key"
    signing_key_path.write_bytes(b"passkey-test-portal-session-key-32-bytes")
    monkeypatch.setattr(web_accounts, "_PORTAL_ACCESS", access)
    monkeypatch.setattr(web_accounts, "_PORTAL_PASSKEYS", _FakePasskeys())
    monkeypatch.setattr(web_accounts, "_SESSION_SIGNING_KEY_PATH", signing_key_path)
    monkeypatch.setattr(web_accounts, "_SESSION_AUTHORITY", None)
    monkeypatch.setattr(web_accounts, "_SESSION_AUTHORITY_CONFIG", None)
    monkeypatch.setattr(web_accounts, "_portal_user_allowed", lambda username: username == "engineer")

    with _server(_QuietHandler) as server:
        status, _headers, options = _json_request(
            server,
            "/api/auth/passkeys/options",
            {},
        )
        assert status == 200
        assert options["ceremony_token"] == "auth-token"

        status, headers, result = _json_request(
            server,
            "/api/auth/passkeys/verify",
            {"ceremony_token": "auth-token", "credential": {"id": "credential"}},
        )

    assert status == 200
    assert result == {"authenticated": True, "username": "engineer"}
    assert any(
        value.startswith("db_session=") and "HttpOnly" in value
        for key, value in headers
        if key.lower() == "set-cookie"
    )
    assert access.recent_audit()[0]["action"] == "passkey_login_succeeded"
