from __future__ import annotations

import http.client
import json
from pathlib import Path

from tests.cases.security_http_support import server as _server

import pytest

from management_portal import serve_management_portal as portal
from management_portal.portal_access_control import PortalAccessStore, PortalCredentialStore
from management_portal.portal_password_reset import PortalPasswordResetStore


def test_reset_tokens_are_hashed_single_use_and_replace_older_tokens_contracts(tmp_path, subtests, isolated_portal_case):
    """重置令牌哈希存储、只能用一次且新令牌作废旧令牌，找回邮箱不能重复分配"""
    with subtests.test(contract='test_reset_tokens_are_hashed_single_use_and_replace_older_tokens'), isolated_portal_case() as case_path:
        tmp_path = case_path
        store = PortalPasswordResetStore()
        first = store.issue('engineer')
        second = store.issue('engineer')
        raw = json.dumps(store.state.read({}))
        assert first not in raw
        assert second not in raw
        assert store.state.kind == 'password_resets'
        with pytest.raises(PermissionError, match='invalid or expired'):
            store.consume(first)
        assert store.consume(second) == 'engineer'
        with pytest.raises(PermissionError, match='invalid or expired'):
            store.consume(second)
    with subtests.test(contract='test_recovery_email_is_unique_and_missing_required_fields_are_rejected'), isolated_portal_case() as case_path:
        tmp_path = case_path
        access = PortalAccessStore()
        access.upsert_user('engineer', actor='CASE-A', email='Engineer@example.com')
        access.upsert_user('colleague', actor='CASE-A')
        with pytest.raises(ValueError, match='already assigned'):
            access.upsert_user('colleague', actor='CASE-A', email='engineer@example.com')
        payload = access.state.read({})
        payload['users']['engineer'].pop('email')
        from psycopg.errors import NotNullViolation
        with pytest.raises(NotNullViolation):
            access.state.replace(payload)
        assert access.user('engineer')['email'] == 'Engineer@example.com'


class _FakePasswordResets:
    configured = True

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.invalidated: list[str] = []
        self.notifications: list[str] = []

    def request(self, **payload) -> bool:
        self.requests.append(payload)
        return bool(payload.get("username"))

    def consume(self, token: str) -> str:
        if token != "valid-reset-token-that-is-long-enough":
            raise PermissionError("invalid")
        return "engineer"

    def invalidate_user(self, username: str) -> None:
        self.invalidated.append(username)

    def notify_password_changed(self, email: str) -> None:
        self.notifications.append(email)


class _QuietHandler(portal.Handler):
    def log_message(self, _format: str, *_args: object) -> None:
        return




def _post(server, path: str, payload: dict):
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
        return response.status, json.loads(response.read())
    finally:
        connection.close()


def test_password_reset_request_is_non_enumerating_and_confirm_revokes_sessions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """重置密码请求对已知和未知邮箱响应相同，确认重置后旧会话失效"""
    access = PortalAccessStore()
    access.upsert_user("engineer", actor="CASE-A", email="engineer@example.com")
    credentials = PortalCredentialStore()
    credentials.set_password("engineer", "Current-Pass7!")
    resets = _FakePasswordResets()
    monkeypatch.setattr(portal, "_PORTAL_ACCESS", access)
    monkeypatch.setattr(portal, "_PORTAL_CREDENTIALS", credentials)
    monkeypatch.setattr(portal, "_PORTAL_PASSWORD_RESETS", resets)
    monkeypatch.setattr(portal, "_load_users", credentials.load)
    monkeypatch.setattr(portal, "_portal_user_allowed", lambda username: username == "engineer")

    with _server(_QuietHandler) as server:
        known = _post(server, "/api/auth/password-reset/request", {"email": "engineer@example.com"})
        unknown = _post(server, "/api/auth/password-reset/request", {"email": "missing@example.com"})
        status, result = _post(
            server,
            "/api/auth/password-reset/confirm",
            {
                "token": "valid-reset-token-that-is-long-enough",
                "new_password": "Replacement-Pass8!",
                "new_password_confirmation": "Replacement-Pass8!",
            },
        )

    assert known == unknown
    assert known[0] == 202
    assert resets.requests[0]["username"] == "engineer"
    assert resets.requests[1]["username"] == ""
    assert status == 200
    assert result["message"].startswith("密码已重置")
    assert portal._verify_credentials("engineer", "Replacement-Pass8!") is True
    assert access.session_version("engineer") == 1
    assert resets.invalidated == ["engineer"]
    assert resets.notifications == ["engineer@example.com"]


def test_password_reset_cannot_open_login_for_an_unfinished_account(tmp_path, monkeypatch):
    """尚未设置密码的账户不能通过重置密码开通登录

    :design: decision_30528aff7035, decision_3bce8b455a3c"""
    access = PortalAccessStore()
    access.upsert_user("engineer", actor="CASE-A", status="active", email="engineer@example.com")
    credentials = PortalCredentialStore()
    resets = _FakePasswordResets()
    monkeypatch.setattr(portal, "_PORTAL_ACCESS", access)
    monkeypatch.setattr(portal, "_PORTAL_CREDENTIALS", credentials)
    monkeypatch.setattr(portal, "_PORTAL_PASSWORD_RESETS", resets)
    monkeypatch.setattr(portal, "_load_users", credentials.load)
    monkeypatch.setattr(portal, "_portal_user_allowed", lambda username: username == "engineer")
    with _server(_QuietHandler) as server:
        known = _post(server, "/api/auth/password-reset/request", {"email": "engineer@example.com"})
        unknown = _post(server, "/api/auth/password-reset/request", {"email": "missing@example.com"})
        status, _ = _post(server, "/api/auth/password-reset/confirm", {
            "token": "valid-reset-token-that-is-long-enough", "new_password": "Replacement-Pass8!",
            "new_password_confirmation": "Replacement-Pass8!",
        })
    assert known == unknown and known[0] == 202
    assert all(not request["username"] for request in resets.requests)
    assert status == 400
    assert credentials.load() == {}
    assert not resets.notifications
    assert access.session_version("engineer") == 0
