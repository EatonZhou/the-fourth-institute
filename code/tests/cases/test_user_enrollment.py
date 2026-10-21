"""Unified account enrollment and User bearer authentication boundaries.

:module: security.identity-access
:importance: P1
"""
import hashlib
import json
import os
import secrets
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from management_portal.portal_access_control import PortalAccessStore, PortalCredentialStore
from management_portal.user_enrollment import UserEnrollment
from tests.cases.permission_test_support import permissions  # noqa: F401


def digest(secret):
    return hashlib.sha256(secret.encode()).hexdigest()


@pytest.fixture
def enrollment(tmp_path):
    return UserEnrollment(PortalAccessStore(), PortalCredentialStore())


def invitation(service, username="sample_editor"):
    secret = secrets.token_urlsafe(32)
    row = service.create_invitation("CASE-A", username, digest(secret))
    return secret, row


def test_token_only_user_and_secret_redaction(enrollment):
    """接受邀请可只签发访问令牌，存储和列表不含明文秘密或哈希，令牌可撤销

    :design: decision_6eac174d85bf"""
    secret, invite = invitation(enrollment)
    token = "dbu_" + secrets.token_urlsafe(32)
    result = enrollment.accept_invitation(secret, secret_sha256=digest(token))
    assert result["user"]["username"] == "sample_editor"
    assert "memberships" not in result["user"]
    assert "sample_editor" not in enrollment.credentials.load()
    assert enrollment.authenticate(token) == "sample_editor"
    assert enrollment.authenticate("dbu_" + secrets.token_urlsafe(32)) == ""
    assert secret not in json.dumps(enrollment.state.read({}))
    assert token not in json.dumps(enrollment.state.read({}))
    assert "secret_sha256" not in json.dumps(enrollment.list_tokens("sample_editor"))
    assert "secret_sha256" not in json.dumps(enrollment.list_invitations())
    with pytest.raises(PermissionError):
        enrollment.accept_invitation(secret, secret_sha256=digest(token))
    with pytest.raises(KeyError):
        enrollment.revoke_token("someone_else", result["credential"]["credential_id"])
    enrollment.revoke_token("sample_editor", result["credential"]["credential_id"])
    assert not enrollment.authenticate(token)


@pytest.mark.parametrize("state", ["revoked", "expired"])
def test_unusable_invitation(enrollment, state):
    """已撤销或已过期的邀请不能用于创建账号

    :design: decision_6eac174d85bf"""
    secret, row = invitation(enrollment)
    if state == "revoked":
        enrollment.revoke_invitation(row["invitation_id"])
    else:
        data = enrollment._read()
        data["invitations"][row["invitation_id"]]["expires_at"] = 1
        enrollment.state.replace(data)
    with pytest.raises(PermissionError):
        enrollment.accept_invitation(secret, password="A-secure-password-123")
    assert not enrollment.access.user("sample_editor")["configured"]


def test_failed_creation_is_atomic_and_invitation_remains_pending(enrollment, monkeypatch):
    """接受邀请中途写入失败时不留下账号，邀请保持待用可再次接受

    :design: decision_6eac174d85bf"""
    secret, _ = invitation(enrollment)
    original = enrollment.create_token
    def fail(*args, **kwargs):
        raise RuntimeError("simulated write failure")
    monkeypatch.setattr(enrollment, "create_token", fail)
    with pytest.raises(RuntimeError):
        enrollment.accept_invitation(secret, password="A-secure-password-123", secret_sha256=digest("dbu_" + "x" * 43))
    assert not enrollment.access.user("sample_editor")["configured"]
    assert "sample_editor" not in enrollment.credentials.load()
    assert enrollment.list_invitations()[0]["status"] == "pending"
    monkeypatch.setattr(enrollment, "create_token", original)
    assert enrollment.accept_invitation(secret, password="A-secure-password-123")["user"]["username"] == "sample_editor"


def test_concurrent_redemption_has_one_winner(enrollment):
    """同一邀请被并发接受时只有一次成功

    :design: decision_6eac174d85bf"""
    secret, _ = invitation(enrollment)
    def accept():
        try:
            return enrollment.accept_invitation(secret, secret_sha256=digest("dbu_" + secrets.token_urlsafe(32)))["user"]["username"]
        except PermissionError:
            return "rejected"
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: accept(), range(2))) == ["rejected", "sample_editor"]


@pytest.mark.parametrize("action", ["disable", "revoke_sessions", "expire"])
def test_account_lifecycle_invalidates_tokens(enrollment, action):
    """停用账号、撤销会话或令牌过期后用户访问令牌失效，重新启用也不恢复

    :design: decision_6eac174d85bf"""
    secret, _ = invitation(enrollment)
    token = "dbu_" + secrets.token_urlsafe(32)
    result = enrollment.accept_invitation(secret, secret_sha256=digest(token))
    if action == "expire":
        data = enrollment._read()
        data["tokens"][result["credential"]["credential_id"]]["expires_at"] = 1
        enrollment.state.replace(data)
    else:
        enrollment.access.upsert_user("sample_editor", actor="CASE-A", revoke_sessions=True,
                                      status="disabled" if action == "disable" else None)
    assert not enrollment.authenticate(token)
    if action == "disable":
        enrollment.access.upsert_user("sample_editor", actor="CASE-A", status="active")
        assert not enrollment.authenticate(token)


def test_existing_user_not_replaced(enrollment):
    """用户名已存在时接受邀请失败，原账号不被替换，邀请保持待用

    :module: platform.core
    :importance: P2
    """
    invitation(enrollment, "CASE-A")
    secret, _ = invitation(enrollment)
    enrollment.create_account("CASE-A", "sample_editor", password="Existing-password-123")
    with pytest.raises(FileExistsError):
        enrollment.accept_invitation(secret, password="Replacement-password-123")
    assert enrollment.list_invitations()[0]["status"] == "pending"


def test_user_bearer_is_not_a_resident(enrollment, monkeypatch):
    """用户访问令牌识别为用户身份，忽略伪造的 Lineage 头和授权头

    :design: decision_6eac174d85bf"""
    from management_portal import asgi, serve_management_portal as portal, page_api_auth
    from management_common.principal import authenticated_principal

    secret, _ = invitation(enrollment)
    token = "dbu_" + secrets.token_urlsafe(32)
    enrollment.accept_invitation(secret, secret_sha256=digest(token))
    monkeypatch.setattr(portal, "_USER_ENROLLMENT", enrollment)
    monkeypatch.setattr(portal, "_PORTAL_ACCESS", enrollment.access)
    monkeypatch.setattr(portal, "_get_session_username", lambda request: "")
    handler = SimpleNamespace(headers={"Authorization": "Bearer " + token, "X-Portal-Lineage-ID": "spoof"})
    auth = portal._get_agent_api_auth(handler)
    assert authenticated_principal(auth).kind == "user"
    assert page_api_auth.authorize(auth) == "sample_editor"
    req = Request({"type": "http", "headers": [(b"authorization", ("Bearer " + token).encode())]})
    assert asgi._instruction_principal(req) == ("sample_editor", "cli")
    spoof = Request({"type": "http", "headers": [(b"authorization", ("Bearer " + token).encode()),
                                                 (b"x-tfi-authorization", b"{}")]})
    assert asgi._instruction_principal(spoof) == ("sample_editor", "cli")


def test_passwordless_user_cookie_and_revocation(enrollment, tmp_path):
    """无密码用户可获得会话 Cookie，撤销会话后 Cookie 失效

    :design: decision_6eac174d85bf"""
    from management_portal.portal_session_auth import PortalSessionAuthority

    secret, _ = invitation(enrollment)
    enrollment.accept_invitation(secret, secret_sha256=digest("dbu_" + secrets.token_urlsafe(32)))
    key = tmp_path / "signing.key"
    key.write_bytes(secrets.token_bytes(32))
    authority = PortalSessionAuthority(key, access_store=enrollment.access)
    cookie = authority.issue("sample_editor", max_age=3600)
    assert authority.verify(cookie) == "sample_editor"
    with pytest.raises(PermissionError):
        authority.issue("unknown", max_age=3600)
    enrollment.access.upsert_user("sample_editor", actor="CASE-A", revoke_sessions=True)
    assert authority.verify(cookie) == ""


def test_invitation_http_contract(enrollment, monkeypatch):
    """接受邀请接口要求 JSON 和同源请求，不能指定用户名，邀请只能使用一次

    :design: decision_6eac174d85bf"""
    from management_portal import serve_management_portal as portal, http_user_enrollment
    monkeypatch.setattr(portal, "_USER_ENROLLMENT", enrollment)
    monkeypatch.setattr(portal, "_serve_json", lambda request, code, body: (code, body))
    secret, _ = invitation(enrollment)
    payload = {"invitation_secret": secret, "secret_sha256": digest("dbu_" + secrets.token_urlsafe(32))}
    monkeypatch.setattr(portal, "_read_bounded_json_object", lambda request, **kwargs: payload)
    request = SimpleNamespace(headers={"Content-Type": "text/plain"})
    assert http_user_enrollment.accept_invitation(request, None, "")[0] == 403
    request.headers = {"Content-Type": "application/json", "Origin": "https://evil.invalid"}
    monkeypatch.setattr(portal, "_same_origin_request", lambda request: False)
    assert http_user_enrollment.accept_invitation(request, None, "")[0] == 403
    request.headers = {"Content-Type": "application/json"}
    payload["username"] = "CASE-A"
    assert http_user_enrollment.accept_invitation(request, None, "")[0] == 400
    del payload["username"]
    code, body = http_user_enrollment.accept_invitation(request, None, "")
    assert code == 201 and body["username"] == "sample_editor"
    assert http_user_enrollment.accept_invitation(request, None, "")[0] == 400


def test_invitation_preview_shows_only_the_offer_and_only_while_pending(enrollment, monkeypatch):
    """邀请预览只返回用户名、显示名和过期时间，邀请用过后预览失效

    :module: platform.core
    :importance: P2
    """
    from management_portal import serve_management_portal as portal, http_user_enrollment
    monkeypatch.setattr(portal, "_USER_ENROLLMENT", enrollment)
    monkeypatch.setattr(portal, "_serve_json", lambda request, code, body: (code, body))
    secret, _ = invitation(enrollment)
    payload = {"invitation_secret": secret}
    monkeypatch.setattr(portal, "_read_bounded_json_object", lambda request, **kwargs: payload)
    request = SimpleNamespace(headers={"Content-Type": "application/json"})
    code, body = http_user_enrollment.preview_invitation(request, None, "")
    assert code == 200 and set(body) == {"username", "display_name", "expires_at"}
    assert body["username"] == "sample_editor" and "CASE-A" not in json.dumps(body)
    assert enrollment.list_invitations()[0]["status"] == "pending"
    payload["password"] = "A-secure-password-123"
    assert http_user_enrollment.preview_invitation(request, None, "")[0] == 400
    del payload["password"]
    enrollment.accept_invitation(secret, password="A-secure-password-123")
    code, body = http_user_enrollment.preview_invitation(request, None, "")
    assert code == 400 and body["error"] == "Invitation is invalid, expired or already used"
    payload["invitation_secret"] = "short"
    assert http_user_enrollment.preview_invitation(request, None, "")[0] == 400


def test_the_first_account_of_a_portal_is_its_first_administrator(enrollment, permissions):
    """新部署时第一个注册的账号自动成为 Portal 管理员，之后的账号都是一般用户

    不论账号由初始化链接、普通邀请还是 CreateUser 创建，Portal 中还没有任何账号记录时，新账号在创建它的同一事务里成为管理员；
    已删除的账号留下的记录也算账号，所以删光账号不会让下一个账号自动成为管理员。

    :module: platform.core
    :importance: P1
    :design: decision_b820faf42218
    """
    from management_portal import permission_system

    enrollment.create_account("portal-setup", "first", password="A-secure-password-123")
    enrollment.create_account("first", "second", password="A-secure-password-123")
    assert permission_system.user_categories() == {"first": "portal_admin"}
    assert permission_system.allows("first", "CreateUser") and not permission_system.allows("second", "CreateUser")
    enrollment.access.retire_user("second", actor="first")
    enrollment.create_account("first", "third", password="A-secure-password-123")
    assert permission_system.user_categories() == {"first": "portal_admin"}


def test_setup_link_makes_the_first_administrator_only_while_there_is_none(enrollment, monkeypatch, permissions):
    """初始化链接只在还没有任何账号时可用，用它建的第一个账号成为首位管理员，重新生成使旧链接失效

    权限系统开启时，用初始化链接建好的账号成为权限系统的管理员；之后的普通邀请不授予管理员；已有账号后不能再生成初始化链接。

    :module: platform.core
    :importance: P2
    :design: decision_b820faf42218
    """
    from management_portal import permission_system, serve_management_portal as portal, http_user_enrollment
    monkeypatch.setattr(portal, "_USER_ENROLLMENT", enrollment)
    monkeypatch.setattr(portal, "_PORTAL_ACCESS", enrollment.access)
    monkeypatch.setattr(portal, "_serve_json", lambda request, code, body: (code, body))
    old = secrets.token_urlsafe(32)
    enrollment.create_invitation("portal-setup", "root", digest(old), expires_in_days=1, first_admin=True)
    # Running the setup command again replaces the earlier link.
    secret = secrets.token_urlsafe(32)
    enrollment.create_invitation("portal-setup", "root", digest(secret), expires_in_days=1, first_admin=True)
    with pytest.raises(PermissionError):
        enrollment.preview_invitation(old)
    assert enrollment.preview_invitation(secret)["first_admin"] is True
    plain, _ = invitation(enrollment)
    assert "first_admin" not in enrollment.preview_invitation(plain)

    payload = {"invitation_secret": secret, "password": "A-secure-password-123"}
    monkeypatch.setattr(portal, "_read_bounded_json_object", lambda request, **kwargs: payload)
    code, body = http_user_enrollment.accept_invitation(SimpleNamespace(headers={"Content-Type": "application/json"}), None, "")
    assert code == 201 and body["administrator"] == {"granted": True}
    assert permission_system.user_categories() == {"root": "portal_admin"} and permission_system.allows("root", "CreateUser")
    # An ordinary invitation grants nothing; once someone may create accounts no setup link works.
    payload.update(invitation_secret=plain)
    assert "administrator" not in http_user_enrollment.accept_invitation(SimpleNamespace(headers={"Content-Type": "application/json"}), None, "")[1]
    assert not permission_system.allows("sample_editor", "CreateUser")
    with pytest.raises(PermissionError):
        enrollment.create_invitation("portal-setup", "second", digest(secrets.token_urlsafe(32)), first_admin=True)


def test_setup_link_is_refused_when_an_account_appeared_after_it_was_made(enrollment, permissions):
    """初始化链接生成后出现了账号时，该链接被拒绝

    :module: platform.core
    :importance: P2
    """
    secret = secrets.token_urlsafe(32)
    enrollment.create_invitation("portal-setup", "root", digest(secret), expires_in_days=1, first_admin=True)
    enrollment.create_account("portal-setup", "other", password="A-secure-password-123")
    with pytest.raises(PermissionError):
        enrollment.accept_invitation(secret, password="A-secure-password-123")
    assert not enrollment.access.user("root")["configured"]


def test_setup_command_prints_the_link_once_and_reports_refusal(enrollment, monkeypatch, capsys, permissions):
    """初始化命令打印首位管理员的邀请链接，已有账号时拒绝并报错

    :module: platform.core
    :importance: P2
    """
    from scripts import portal_setup
    monkeypatch.setattr("management_portal.portal_access_control.get_portal_access_store", lambda: enrollment.access)
    assert os.environ.get("PORTAL_DATABASE_URL"), "the enrollment store already runs on the test database"
    monkeypatch.setattr("sys.argv", ["portal_setup.py", "first-admin", "--username", "root", "--url", "https://portal.example/"])
    portal_setup.main()
    out = capsys.readouterr().out
    link = next(line.strip() for line in out.splitlines() if "/join#invite=" in line)
    assert link.startswith("https://portal.example/join#invite=") and "/guide#admin" in out
    assert enrollment.preview_invitation(link.split("invite=", 1)[1])["first_admin"] is True
    enrollment.create_account("CASE-A", "boss", password="A-secure-password-123")
    with pytest.raises(SystemExit):
        portal_setup.main()
    assert "already has accounts" in capsys.readouterr().err


def test_user_isa_permissions_and_idempotency(enrollment, monkeypatch):
    """普通用户可创建邀请且重放幂等，可读自己的信息和令牌，不能撤销他人令牌

    :design: decision_6eac174d85bf"""
    from management_portal import serve_management_portal as portal, web_isa
    from management_portal.hatg.contracts import InstructionCall, InstructionContext
    monkeypatch.setattr(portal, "_USER_ENROLLMENT", enrollment)
    monkeypatch.setattr(portal, "_PORTAL_ACCESS", enrollment.access)
    enrollment.access.upsert_user("CASE-A", actor="CASE-A")
    enrollment.create_account("CASE-A", "ordinary", password="Ordinary-password-123")
    kernel = web_isa.build_kernel()
    secret = secrets.token_urlsafe(32)
    call = InstructionCall("CreateUserInvitation", {"user_id": "sample_editor", "secret_sha256": digest(secret)}, idempotency_key="invite-sample_editor")
    result = kernel.execute(call, InstructionContext(caller="ordinary", entry="cli"))
    assert result.status == "committed", result.error
    replay = kernel.execute(call, InstructionContext(caller="ordinary", entry="cli"))
    assert replay.replayed
    assert replay.result["invitation"]["invitation_id"] == result.result["invitation"]["invitation_id"]
    token = "dbu_" + secrets.token_urlsafe(32)
    enrollment.accept_invitation(secret, secret_sha256=digest(token))
    read = kernel.execute(InstructionCall("GetUser", {"user_id": "sample_editor"}), InstructionContext(caller="sample_editor", entry="cli"))
    assert read.status == "committed", read.error
    assert read.result["user"]["username"] == "sample_editor"
    list_tokens = kernel.execute(InstructionCall("ListUserAccessTokens", {"user_id": "sample_editor"}), InstructionContext(caller="sample_editor", entry="cli"))
    assert len(list_tokens.result["credentials"]) == 1
    assert "secret_sha256" not in json.dumps(list_tokens.result)
    other = kernel.execute(InstructionCall("RevokeUserAccessToken", {"credential_id": list_tokens.result["credentials"][0]["credential_id"]}), InstructionContext(caller="ordinary", entry="cli"))
    assert other.status == "failed"


def test_create_user_creates_only_the_account(enrollment, monkeypatch):
    """CreateUser 只创建账号，传入创建模板时在写入任何账号前拒绝

    Credentials are issued once the account exists, so CreateUser takes no
    creation template; one passed is refused before any account is written.

    :importance: P2
    :design: decision_6eac174d85bf
    """
    from management_portal import serve_management_portal as portal, web_isa
    from management_portal.hatg.contracts import InstructionCall, InstructionContext
    monkeypatch.setattr(portal, "_USER_ENROLLMENT", enrollment)
    monkeypatch.setattr(portal, "_PORTAL_ACCESS", enrollment.access)
    enrollment.access.upsert_user("CASE-A", actor="CASE-A")
    kernel = web_isa.build_kernel()
    account = {"user_id": "sample_editor", "initial_password": "Sample-Editor-password-1234"}
    templated = kernel.execute(InstructionCall("CreateUser", {**account, "template": "user"}), InstructionContext(caller="CASE-A", entry="cli"))
    assert templated.error["code"] == "instruction.invalid_arguments"
    assert templated.error["details"]["unknown_arguments"] == ["template"]
    assert not enrollment.access.user("sample_editor")["configured"]
    created = kernel.execute(InstructionCall("CreateUser", account), InstructionContext(caller="CASE-A", entry="cli"))
    assert created.status == "committed", created.error
    assert created.result["user"]["username"] == "sample_editor"
    assert enrollment.access.user("sample_editor")["configured"]


def test_user_updates_own_profile_but_not_other_accounts(enrollment, monkeypatch, permissions):
    """普通用户可修改自己的资料，不能修改其他账号

    权限系统开启时，默认方案让账号本人对自己的账号有 admin，可以修改自己的资料，但账号状态只有管理员能改；修改其他账号返回 instruction.forbidden，管理员可以修改任何账号。

    :importance: P2
    :design: decision_b820faf42218
    """
    from management_portal import serve_management_portal as portal, web_isa
    from management_portal.hatg.contracts import InstructionCall, InstructionContext

    monkeypatch.setattr(portal, "_USER_ENROLLMENT", enrollment)
    monkeypatch.setattr(portal, "_PORTAL_ACCESS", enrollment.access)
    invalidated = []
    monkeypatch.setattr(portal._PORTAL_PASSWORD_RESETS, "invalidate_user", invalidated.append)
    enrollment.access.upsert_user("CASE-A", actor="CASE-A")
    enrollment.create_account("CASE-A", "ordinary", password="Ordinary-password-123")
    enrollment.create_account("CASE-A", "neighbour", password="Neighbour-password-123")
    permissions.users("CASE-A", "ordinary", "neighbour")
    permissions.administrator("CASE-A")
    kernel = web_isa.build_kernel()

    def update(caller, username, changes, key):
        return kernel.execute(
            InstructionCall("UpdateUser", {"user_id": username, "changes": changes}, idempotency_key=key),
            InstructionContext(caller=caller, entry="cli"),
        )

    own = update("ordinary", "ordinary", {"display_name": "Ordinary Person", "recovery_email": "ordinary@example.com"}, "own")
    assert own.status == "committed", own.error
    assert enrollment.access.user("ordinary")["display_name"] == "Ordinary Person"
    assert invalidated == ["ordinary"]
    status = update("ordinary", "ordinary", {"status": "disabled"}, "own-status")
    assert status.error["code"] == "instruction.forbidden"
    assert enrollment.access.user("ordinary")["status"] == "active"
    other = update("ordinary", "neighbour", {"display_name": "Renamed"}, "other")
    assert other.error["code"] == "instruction.forbidden"
    assert enrollment.access.user("neighbour")["display_name"] != "Renamed"
    admin = update("CASE-A", "neighbour", {"display_name": "Renamed by admin"}, "admin")
    assert admin.status == "committed", admin.error
    assert enrollment.access.user("neighbour")["display_name"] == "Renamed by admin"
