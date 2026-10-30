from __future__ import annotations

import base64
import http.client
import ipaddress
import io
import json
import queue
import subprocess
import threading
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from management_portal import device_identity_backend as device
from management_portal import local_terminal_backend as terminal
from management_portal import paper_collection_jobs, paper_library
from management_portal import portal_deployment_router as router
from management_portal import serve_management_portal as portal
from management_portal import access_audit_store
from management_portal.portal_access_control import PortalAccessStore, PortalCredentialStore
from management_portal.portal_password_reset import PortalPasswordResetService, PortalPasswordResetStore
from management_portal.portal_passkeys import PortalPasskeyService
from management_portal.portal_two_factor import PortalTwoFactorStore, generate_totp_secret, totp_code
from tests.cases.security_http_support import server as _server
from tests.cases.isa_test_support import isolated_instruction_store  # noqa: F401 - fixture
from tests.cases.test_hatg_projection_receipts import projection_fixture

from management_portal.society_service import lineage_aggregate


PASSWORD = "Fixture-only-Passphrase-47!"


@pytest.fixture
def account_boundary(tmp_path, monkeypatch):
    access = PortalAccessStore()
    credentials = PortalCredentialStore()
    for actor in ("CASE-A", "fixture-user", "fixture-peer"):
        access.upsert_user(actor, actor="CASE-A")
        credentials.set_password(actor, PASSWORD)
    factors = PortalTwoFactorStore(tmp_path / "factor.key")
    resets = SimpleNamespace(configured=False, invalidate_user=Mock())
    monkeypatch.setattr(portal, "_PORTAL_ACCESS", access)
    monkeypatch.setattr("management_portal.portal_access_control.get_portal_access_store", lambda: access)
    monkeypatch.setattr(portal, "_PORTAL_CREDENTIALS", credentials)
    monkeypatch.setattr(portal, "_PORTAL_TWO_FACTOR", factors)
    monkeypatch.setattr(portal, "_PORTAL_PASSWORD_RESETS", resets)
    monkeypatch.setattr(portal, "_PORTAL_PASSKEYS", PortalPasskeyService())
    monkeypatch.setattr(portal, "_PORTAL_ALLOWED_USERS", set())
    monkeypatch.setattr(portal, "_TWO_FACTOR_SETUPS", {})
    monkeypatch.setattr(portal, "_LOGIN_CHALLENGES", {})
    monkeypatch.setattr(portal, "_get_session_username", lambda handler: "fixture-user")
    sessions = []
    monkeypatch.setattr(portal, "_issue_session_token", lambda actor: sessions.append(actor) or "synthetic-session")
    return SimpleNamespace(root=tmp_path, access=access, credentials=credentials, factors=factors, resets=resets, sessions=sessions)


def _audit_text():
    events = access_audit_store.recent(10_000)
    assert events
    return json.dumps(events, ensure_ascii=False)


def request(server, path, payload, *, form=False):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    body = urlencode(payload) if form else json.dumps(payload)
    try:
        connection.request("POST", path, body=body, headers={"Content-Type": "application/x-www-form-urlencoded" if form else "application/json"})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def test_named_accounts_follow_the_same_lifecycle(account_boundary):
    """具名账号遵循相同的停用启用与删除流程"""
    access = account_boundary.access
    access.upsert_user("CASE-A", actor="fixture-user", status="disabled")
    assert access.user("CASE-A")["status"] == "disabled"
    access.upsert_user("CASE-A", actor="fixture-user", status="active")
    assert access.retire_user("CASE-A", actor="fixture-user")["status"] == "deleted"


def test_internal_password_reset_is_targeted(account_boundary):
    """内部密码重置仅修改目标账号并使其旧会话失效

    :design: decision_30528aff7035"""
    before = account_boundary.credentials.load()
    payload = {"action": "reset_password", "username": "fixture-peer", "password": PASSWORD + "-changed"}
    result = portal._admin_access_action("CASE-A", payload)
    after = account_boundary.credentials.load()
    assert after["fixture-user"] == before["fixture-user"]
    assert after["CASE-A"] == before["CASE-A"]
    assert after["fixture-peer"] != before["fixture-peer"]
    assert portal._verify_credentials("fixture-peer", PASSWORD + "-changed")
    assert result["user"]["session_version"] == 1
    account_boundary.resets.invalidate_user.assert_called_once_with("fixture-peer")
    assert PASSWORD not in _audit_text()


def test_deleted_user_absent_from_admin_list(account_boundary):
    """已删除用户不出现在管理列表且列表不含密码"""
    account_boundary.access.retire_user("fixture-peer", actor="CASE-A")
    users = portal._admin_access_payload("CASE-A")["users"]
    assert {user["username"] for user in users} == {"CASE-A", "fixture-user"}
    assert all("password" not in user for user in users)


def test_login_failures_have_identical_public_response(account_boundary, monkeypatch):
    """不同登录失败原因返回相同公开响应且不签发会话"""
    monkeypatch.setattr(portal, "_get_session_username", lambda handler: None)
    results = []
    with _server(portal.Handler) as server:
        for actor, password, state, allowed in (
            ("missing", PASSWORD, "active", set()),
            ("fixture-user", "wrong", "active", set()),
            ("fixture-user", PASSWORD, "disabled", set()),
            ("fixture-user", PASSWORD, "active", {"fixture-peer"}),
        ):
            account_boundary.access.upsert_user("fixture-user", actor="CASE-A", status=state)
            monkeypatch.setattr(portal, "_PORTAL_ALLOWED_USERS", allowed)
            status, headers, body = request(server, "/login", {"username": actor, "password": password}, form=True)
            assert "Set-Cookie" not in headers
            results.append((status, headers.get("Location"), body))
    assert len(set(results)) == 1
    assert results[0][:2] == (302, "/login?error=1")
    assert not account_boundary.sessions


@pytest.mark.parametrize("operation", ["disable", "recovery-codes"])
def test_two_factor_sensitive_operations_require_both_factors(account_boundary, operation, capsys):
    """双因素停用与恢复码重建同时要求密码和有效第二因素"""
    codes = account_boundary.factors.enable("fixture-user", generate_totp_secret())
    with _server(portal.Handler) as server:
        for password, code in ((PASSWORD, ""), (PASSWORD, "bad-factor"), ("wrong", codes[0])):
            status, headers, body = request(server, "/api/account/two-factor/" + operation, {"current_password": password, "verification_code": code})
            assert status == 403
            assert "Set-Cookie" not in headers
            assert PASSWORD.encode() not in body and codes[0].encode() not in body
            assert account_boundary.factors.enabled("fixture-user")
        status, headers, body = request(server, "/api/account/two-factor/" + operation, {"current_password": PASSWORD, "verification_code": codes[0]})
    assert status == 200, body
    if operation == "disable":
        assert not account_boundary.factors.enabled("fixture-user")
        assert account_boundary.access.user("fixture-user")["session_version"] == 1
        assert "Set-Cookie" in headers
    else:
        assert len(json.loads(body)["recovery_codes"]) == 10
        assert all(account_boundary.factors.verify("fixture-user", code) is None for code in codes)
    captured = capsys.readouterr()
    assert PASSWORD not in captured.err + captured.out
    assert codes[0] not in captured.err + captured.out
    assert codes[0] not in _audit_text()


def test_setup_password_only_and_ephemeral_pending(account_boundary, monkeypatch):
    """双因素设置仅暂存待确认信息且过期后无法启用"""
    monkeypatch.setattr("management_portal.portal_two_factor.site_name", lambda: "Fixture Institute")
    with _server(portal.Handler) as server:
        status, _, body = request(server, "/api/account/two-factor/setup", {"current_password": PASSWORD})
    assert status == 200
    setup = json.loads(body)
    assert not account_boundary.factors.enabled("fixture-user")
    assert setup["expires_in"] == 600
    query = parse_qs(urlsplit(setup["otpauth_uri"]).query)
    assert query["secret"] == [setup["secret"]]
    assert query["issuer"] == ["Fixture Institute"]
    assert base64.b64decode(setup["qr_code_data_url"].split(",", 1)[1]).startswith(b"<?xml")
    monkeypatch.setattr(portal, "_TWO_FACTOR_SETUPS", {})
    with pytest.raises(PermissionError, match="expired"):
        portal._confirm_two_factor_setup("fixture-user", setup["setup_token"], totp_code(setup["secret"]))
    assert not account_boundary.factors.enabled("fixture-user")


@pytest.mark.parametrize("status", ["disabled", "deleted"])
def test_passkey_login_checks_current_account_status(account_boundary, monkeypatch, status):
    """Passkey 登录在账号已停用或删除时拒绝认证"""
    if status == "deleted":
        account_boundary.access.retire_user("fixture-user", actor="CASE-A")
    else:
        account_boundary.access.upsert_user("fixture-user", actor="CASE-A", status=status)
    monkeypatch.setattr(portal._PORTAL_PASSKEYS, "finish_authentication", lambda **kwargs: "fixture-user")
    with _server(portal.Handler) as server:
        response, headers, body = request(server, "/api/auth/passkeys/verify", {"ceremony_token": "synthetic", "credential": {"id": "synthetic"}})
    assert response == 403
    assert json.loads(body)["error"] == "Passkey authentication failed."
    assert "Set-Cookie" not in headers and not account_boundary.sessions


@pytest.mark.parametrize("failure", ["missing_challenge", "corrupt_store"])
def test_passkey_corruption_never_issues_session(account_boundary, failure):
    """Passkey 挑战缺失或存储损坏时不签发会话"""
    service = portal._PORTAL_PASSKEYS
    token = "missing"
    if failure == "corrupt_store":
        token = service.begin_authentication(rp_id="localhost", origin="http://localhost")["ceremony_token"]
        service.store.state.replace({"schema": "corrupt", "users": {}})  # a stored payload that fails validation
    with _server(portal.Handler) as server:
        response, headers, body = request(server, "/api/auth/passkeys/verify", {"ceremony_token": token, "credential": {"id": "synthetic"}})
    assert response == 403
    assert "Set-Cookie" not in headers and not account_boundary.sessions
    assert json.loads(body)["error"] == "Passkey authentication failed."


@pytest.mark.parametrize("failure", ["queue_full", "delivery_failure"])
def test_failed_reset_delivery_invalidates_issued_token(tmp_path, failure):
    """密码重置邮件入队或发送失败时使已签发令牌失效"""
    store = PortalPasswordResetStore()
    service = PortalPasswordResetService.__new__(PortalPasswordResetService)
    service.store = store
    service.mailer = SimpleNamespace(configured=True)
    service._lock = threading.RLock()
    service._requests = {}
    issued = []
    issue = store.issue
    store.issue = lambda actor: issued.append(issue(actor)) or issued[-1]
    if failure == "queue_full":
        service._deliveries = queue.Queue(maxsize=1)
        service._deliveries.put_nowait(None)
        assert not service.request(username="fixture-user", email="fixture@example.invalid", client_key="offline")
    else:
        service._deliveries = queue.Queue()
        assert service.request(username="fixture-user", email="fixture@example.invalid", client_key="offline")
        deliveries = service._deliveries
        class StopWorker(BaseException):
            pass
        def finish():
            deliveries.task_done()
            raise StopWorker()
        service._deliveries = SimpleNamespace(get=deliveries.get, task_done=finish)
        def fail_send(*args):
            raise OSError("isolated delivery failure")
        service.mailer.send_reset = fail_send
        with pytest.raises(StopWorker):
            service._delivery_worker()
    assert len(issued) == 1
    with pytest.raises(PermissionError):
        store.consume(issued[0])


def test_router_direct_peer_and_backend_proxy_trust():
    """Router 校验直连来源且后端仅信任本机代理头"""
    server = SimpleNamespace(allowed_networks=[ipaddress.ip_network("127.0.0.0/8")], network_settings_path=None)
    assert router.PortalRouterServer.verify_request(server, None, ("127.0.0.1", 1234))
    assert not router.PortalRouterServer.verify_request(server, None, ("192.0.2.9", 1234))
    import asyncio
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
    for peer, expected_scheme, expected_client in (("192.0.2.9", "http", "192.0.2.9"), ("127.0.0.1", "https", "198.51.100.7")):
        handler = SimpleNamespace(client_address=(peer, 1234), headers={"X-Forwarded-Proto": "https", "X-Forwarded-For": "198.51.100.7", "Host": "fixture.invalid"})
        async def receive(): return {"type": "http.disconnect"}
        async def send(message): pass
        async def app(scope, receive, send):
            handler._asgi_request_scheme = scope['scheme']
            handler.client_address = scope['client']
        scope = {'type':'http', 'scheme':'http', 'client':(peer,1234), 'headers':[(b'x-forwarded-proto',b'https'),(b'x-forwarded-for',b'198.51.100.7')]}
        asyncio.run(ProxyHeadersMiddleware(app, trusted_hosts=['127.0.0.1'])(scope, receive, send))
        assert portal._request_scheme(handler) == expected_scheme
        assert portal._request_client_key(handler) == expected_client
    public = router._public_runtime_status({"state": "blocked", "message": "unavailable", "details": {"private_path": "/synthetic-secret", "token": "synthetic"}, "traceback": "private"})
    assert public == {"state": "blocked", "message": "unavailable"}


def test_terminal_close_preserves_device_credential(tmp_path, monkeypatch):
    """关闭终端会话后保留设备 SSH 凭证"""
    device_id = "device_123456789abc"
    device.save_device_ssh_password(device_id, PASSWORD, secret_root=tmp_path)
    closed = []
    monkeypatch.setattr(terminal, "_SESSIONS", {"fixture": SimpleNamespace(actor="fixture-user", session_id="fixture", credential_resource_id=device_id)})
    monkeypatch.setattr(terminal, "_close_session", lambda session: closed.append(session.session_id))
    assert terminal.control_terminal("fixture-user", "fixture", "close")["alive"] is False
    assert closed == ["fixture"]
    assert device.read_device_ssh_password(device_id, secret_root=tmp_path) == PASSWORD


@pytest.mark.parametrize("outcome", ["denied", "timeout", "missing_binary"])
def test_ssh_failure_projection_without_remote_process(monkeypatch, outcome):
    """SSH 失败统一返回不可达状态且不泄露凭证"""
    monkeypatch.setattr(device, "effective_ssh_access", lambda *args, **kwargs: {"via_resource_id": "device_123456789abc", "credential_saved": False, "host": "fixture.invalid", "user": "fixture", "port": 22})
    def run(argv, **kwargs):
        assert PASSWORD not in repr(argv) + repr(kwargs)
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(argv, 3)
        if outcome == "missing_binary":
            raise FileNotFoundError("ssh unavailable")
        return SimpleNamespace(returncode=255, stderr="Permission denied (publickey).")
    monkeypatch.setattr(device.subprocess, "run", run)
    result = device.test_ssh_connection("device_123456789abc", timeout_seconds=1)
    assert result["reachable"] is False
    assert result["message"] and PASSWORD not in json.dumps(result)
    assert set(result) == {"resource_id", "via_resource_id", "reachable", "message", "latency_ms", "tested_at"}


def test_router_unknown_preference_cannot_set_cookie():
    """Router 拒绝未知部署偏好且不设置偏好 Cookie"""
    calls = []
    payload = json.dumps({"instance": "retired"}).encode()
    handler = SimpleNamespace(command="POST", headers={}, client_address=("127.0.0.1", 1234), rfile=io.BytesIO(payload),
        server=SimpleNamespace(policy=SimpleNamespace(verifier=SimpleNamespace(verify_cookie=lambda _: "fixture-user")), current_backends=lambda: {"dev": None, "release": None}),
        _content_length=lambda: len(payload), _json=lambda *args: calls.append(args))
    router.PortalRouterHandler._deployment_preference(handler)
    assert calls == [(400, {"error": "unknown or unavailable Portal instance"})]
    policy = router.RouterPolicy(SimpleNamespace(verify_cookie=lambda cookie: "fixture-user" if cookie else ""), set())
    cookie = router.DEPLOYMENT_PREFERENCE_COOKIE + "=" + router._encode_deployment_preference("fixture-user", "retired")
    assert policy.preference("fixture-user", cookie, {"dev", "release"}) == ""
    for path in ("/login", "/login/verify", "/api/auth/passkeys/options", "/api/auth/passkeys/verify", "/api/auth/password-reset/request", "/api/auth/password-reset/confirm", "/api/account/security", "/api/account/two-factor/setup", "/settings.html"):
        assert policy.select(path, "", {"dev", "release"}) == "dev"


def test_qr_encoder_receives_exact_displayed_seed(account_boundary, monkeypatch):
    """二维码编码内容与页面展示的双因素种子完全一致"""
    import qrcode
    encoded = []
    make = qrcode.make
    def capture(uri, **kwargs):
        encoded.append(uri)
        return make(uri, **kwargs)
    monkeypatch.setattr(qrcode, "make", capture)
    setup = portal._begin_two_factor_setup("fixture-user")
    assert encoded == [setup["otpauth_uri"]]
    assert parse_qs(urlsplit(encoded[0]).query)["secret"] == [setup["secret"]]
    assert setup["qr_code_data_url"] == portal._totp_qr_data_url(setup["otpauth_uri"])


def test_paper_machine_entry_preserves_the_authenticated_resident(monkeypatch, record_property):
    """论文机器入口向业务后端传递已认证 Resident 身份"""
    from management_portal import society_service
    resident = "resident_123456789abc"
    monkeypatch.setattr(portal, "_get_session_username", lambda handler: None)
    monkeypatch.setattr(portal.agent_token_auth, "authenticate_bearer_token", lambda token, *, scope="": {"agent_id": resident})
    lineage = SimpleNamespace(lineage=SimpleNamespace(authorize_lineage_resident_principal=lambda lineage_id, actor: actor))
    monkeypatch.setattr(society_service, "get_society_service", lambda: lineage)
    observed = []
    monkeypatch.setattr(paper_library, "list_library", lambda params, actor: observed.append(actor) or {"papers": []})
    with _server(portal.Handler) as server:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        try:
            connection.request("GET", "/paper/api/library", headers={"Authorization": "Bearer synthetic-fixture", "X-Portal-Lineage-ID": "lineage_123456789abc"})
            response = connection.getresponse()
            response.read()
        finally:
            connection.close()
    record_property("observed_status", response.status)
    record_property("observed_backend_actors", json.dumps(observed))
    assert observed == [resident]


def test_paper_machine_entry_rechecks_current_membership(monkeypatch):
    """论文机器入口重新检查 Resident 当前 Lineage 成员身份"""
    resident = "resident_123456789abc"
    monkeypatch.setattr(portal, "_get_session_username", lambda handler: None)
    from management_portal import society_service
    monkeypatch.setattr(portal.agent_token_auth, "authenticate_bearer_token", lambda token, *, scope="": {"agent_id": resident})
    rejected = Mock(side_effect=PermissionError("Resident identity belongs to another Lineage"))
    lineage = SimpleNamespace(lineage=SimpleNamespace(authorize_lineage_resident_principal=rejected))
    monkeypatch.setattr(society_service, "get_society_service", lambda: lineage)
    observed = []
    monkeypatch.setattr(paper_library, "list_library", lambda params, actor: observed.append(actor) or {"papers": []})
    with _server(portal.Handler) as server:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        try:
            connection.request("GET", "/paper/api/library", headers={"Authorization": "Bearer synthetic-fixture", "X-Portal-Lineage-ID": "lineage_123456789abc"})
            response = connection.getresponse()
            response.read()
        finally:
            connection.close()
    assert response.status == 403
    assert not observed
    rejected.assert_called_once_with("lineage_123456789abc", resident)


@pytest.mark.parametrize("lineage_header, expected_status", [("lineage_123456789abc", 200), ("", 403)])
def test_paper_machine_write_needs_a_resident_active_in_its_lineage(monkeypatch, lineage_header, expected_status):
    """论文机器写入要求提供所属 Lineage 并保持 Resident 身份"""
    from management_portal import society_service
    resident = "resident_123456789abc"
    monkeypatch.setattr(portal, "_get_session_username", lambda handler: None)
    monkeypatch.setattr(portal.agent_token_auth, "authenticate_bearer_token", lambda token, *, scope="": {"agent_id": resident})
    lineage = SimpleNamespace(lineage=SimpleNamespace(authorize_lineage_resident_principal=lambda lineage_id, actor: actor))
    monkeypatch.setattr(society_service, "get_society_service", lambda: lineage)
    observed = []
    monkeypatch.setattr(paper_collection_jobs, "delete_paper_collection_job", lambda payload, actor: observed.append(actor) or {"deleted": True})
    headers = {"Authorization": "Bearer synthetic-fixture", "Content-Type": "application/json"}
    if lineage_header:
        headers["X-Portal-Lineage-ID"] = lineage_header
    with _server(portal.Handler) as server:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        try:
            connection.request("POST", "/paper/api/collector/delete", body=b"{}", headers=headers)
            response = connection.getresponse()
            response.read()
        finally:
            connection.close()
    assert response.status == expected_status
    assert observed == ([resident] if expected_status == 200 else [])


def test_proxy_metadata_cannot_replace_authenticated_actor(account_boundary, monkeypatch):
    """代理元数据不能替换已认证用户或 Resident 身份"""
    resident = "resident_123456789abc"
    monkeypatch.setattr(portal.agent_token_auth, "authenticate_bearer_token", lambda token, *, scope="": {"agent_id": resident})
    for peer in ("127.0.0.1", "192.0.2.1"):
        handler = SimpleNamespace(client_address=(peer, 1234), headers={"Authorization": "Bearer synthetic", "X-Forwarded-User": "forged", "X-Forwarded-For": "198.51.100.9", "X-Portal-Lineage-ID": "lineage_123456789abc"})
        assert portal._get_agent_api_auth(handler) == {"type": "human", "id": "fixture-user"}
        with monkeypatch.context() as patch:
            patch.setattr(portal, "_get_session_username", lambda handler: None)
            assert portal._get_agent_api_auth(handler)["id"] == resident


def test_account_delete_preserves_lineage_definition(projection_fixture, account_boundary, monkeypatch):
    """删除用户账号保留 Lineage 定义与项目资源"""
    created, _, _ = projection_fixture
    store = lineage_aggregate.lineage_catalog_store()
    lineage_id = created["lineage_id"]
    before = store.get(lineage_id)
    monkeypatch.setattr(portal, "_delete_portal_user_personal_data", lambda actor: {})
    monkeypatch.setattr("management_portal.portal_access_control.get_portal_access_store", lambda: account_boundary.access)
    result = portal._admin_access_action("CASE-A", {"action": "delete", "username": "fixture-peer"})
    assert result["deleted"]
    assert account_boundary.access.user("fixture-peer")["status"] == "deleted"
    assert store.get(lineage_id) == before
    assert "owner_ids" not in before
    assert "Lineage and Project resources" in result["retained"]


def test_page_machine_entry_rejects_missing_or_malformed_lineage(monkeypatch):
    """页面机器入口拒绝缺失或格式错误的 Lineage"""
    from management_portal import society_service, page_api_auth
    from management_portal.society_service import lineage_state
    service = SimpleNamespace(lineage=SimpleNamespace(authorize_lineage_resident_principal=lineage_state.authorize_lineage_resident_principal))
    monkeypatch.setattr(society_service, "get_society_service", lambda: service)
    for lineage_id in ("", "not-a-lineage", "resident_123456789abc"):
        auth = {"type": "agent", "id": "resident_123456789abc", "lineage_id": lineage_id}
        with pytest.raises(PermissionError):
            page_api_auth.authorize(auth)
