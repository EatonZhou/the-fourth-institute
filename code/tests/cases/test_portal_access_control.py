""":importance: P1"""
from __future__ import annotations


import json
from pathlib import Path

import pytest

from management_portal import permission_system
from management_portal.resources import query as resource_query
from management_portal.resources import access as resource_access
from management_portal.resources.service import ResourceService
from management_portal.society_service import (
    lineage_domain,
    resource_client,
)
from management_portal.portal_access_control import (
    PortalAccessStore,
    PortalCredentialStore,
)
from tests.cases import permission_test_support
from tests.cases.society_service_test_support import install_resource_boundary
from tests.cases.registry_support import write_projects


from management_portal.society_service import resident_access, resident_runtime_domain

from management_portal.society_service import resident_directory_domain

def _projects() -> None:
    write_projects("schema_version: 1\nprojects:\n  - id: RobotWorker\n  - id: Robolab\n")


@pytest.mark.parametrize("actor,collaboration_allowed,resource_allowed", [
    ("test-actor", True, True), ("TEST-ACTOR", True, True),
    ("test-actor-other", True, True), ("CASE-A", True, True),
    ("", False, False),
])
def test_open_mode_collaboration_and_resource_read_access_agree(
    monkeypatch: pytest.MonkeyPatch,
    actor: str,
    collaboration_allowed: bool,
    resource_allowed: bool,
) -> None:
    """开放模式下协作访问与资源读取判断一致，非空用户名都可访问，空用户名被拒绝"""
    lineage = {
        "lineage_id": "lineage_111111111111",
    }
    resident = {"resident_id": "resident_222222222222"}
    identity_reads = []

    def lineage_identity(record):
        identity_reads.append(record)
        return {}

    monkeypatch.setattr(
        resident_runtime_domain, "_lineage_identity_record",
        lineage_identity,
    )
    if actor:
        assert lineage_domain._actor_can_manage_lineage(lineage, actor) is collaboration_allowed
    else:
        with pytest.raises(PermissionError):
            lineage_domain._actor_can_manage_lineage(lineage, actor)
    assert resident_directory_domain._can_manage(resident, actor) is collaboration_allowed
    assert resident_access.resident_visible_to_actor(resident, actor) is collaboration_allowed
    assert resident_directory_domain._has_resident_workspace_authority(resident, actor) is collaboration_allowed
    assert identity_reads == []
    assert resource_query.resource_is_readable({
        "resource_id": resident["resident_id"],
        "resource_type": "resident",
        "attributes": {},
    }, actor) is resource_allowed
    if collaboration_allowed:
        lineage_domain._ensure_visible(lineage, actor)
        lineage_domain._ensure_manager(lineage, actor)
    else:
        with pytest.raises(PermissionError):
            lineage_domain._ensure_manager(lineage, actor)
    assert lineage == {"lineage_id": "lineage_111111111111"}


def test_project_catalog_names_each_project_only_by_its_id_contracts(tmp_path, subtests, isolated_portal_case):
    """项目目录只按 ID 命名项目，账户不含旧角色字段，密码以哈希存储并检查强度"""
    with subtests.test(contract='test_project_catalog_names_each_project_only_by_its_id'), isolated_portal_case() as case_path:
        tmp_path = case_path
        write_projects('schema_version: 1\nprojects:\n  - id: AI员工\n  - id: RobotWorker\n')
        store = PortalAccessStore()
        assert store.project_catalog() == [{'id': 'AI员工'}, {'id': 'RobotWorker'}]
        assert store.project_ids() == ['AI员工', 'RobotWorker']
    with subtests.test(contract='test_accounts_omit_retired_role_fields'), isolated_portal_case() as case_path:
        tmp_path = case_path
        _projects()
        store = PortalAccessStore()
        user = store.user('existing-user')
        assert 'access_mode' not in user
        assert user['status'] == 'active'
        assert 'memberships' not in user and 'system_capabilities' not in user
        assert store.is_active('existing-user')
        assert resource_query.resource_is_readable({'resource_id': 'repo_0123456789ab', 'attributes': {'project_ids': ['private-project']}}, 'existing-user', portal_access=store)
    with subtests.test(contract='test_user_updates_revoke_only_that_users_sessions_and_write_audit'), isolated_portal_case() as case_path:
        tmp_path = case_path
        _projects()
        store = PortalAccessStore()
        store.upsert_user('engineer', actor='CASE-A')
        before = store.session_version('engineer')
        updated = store.upsert_user('engineer', actor='tfi',  revoke_sessions=True)
        assert updated['session_version'] == before + 1
        assert 'memberships' not in updated
        assert store.recent_audit()[0]['target'] == 'engineer'
        persisted = store.state.read({})
        assert persisted['schema'] == 'tfi.portal-access.v1'
    with subtests.test(contract='test_credential_store_hashes_and_updates_atomically'), isolated_portal_case() as case_path:
        tmp_path = case_path
        store = PortalCredentialStore()
        store.set_password('engineer', 'Initial-Pass9!')
        encoded = store.load()['engineer']
        assert encoded.startswith('pbkdf2:sha256:')
        assert 'Initial-Pass9!' not in json.dumps(store.state.read({}))
        assert store.state.kind == 'credentials'
        with pytest.raises(ValueError, match='at least 12'):
            store.set_password('engineer', 'short')
        with pytest.raises(ValueError, match='at least three'):
            store.set_password('engineer', 'lowercase-only-password')
        with pytest.raises(ValueError, match='no more than 256'):
            store.set_password('engineer', 'Aa1!' * 65)


def test_access_audit_is_appended_to_its_table_and_read_newest_first() -> None:
    """访问审计只追加写入 portal_identity.access_audit 表并从新到旧读取

    PortalAccessStore.audit 把每个账号与登录事件追加为 PostgreSQL portal_identity.access_audit 的一行（不再写 access_audit.jsonl），recent_audit 按时间和写入顺序从新到旧返回，并按 limit 截断。
    """
    from management_common.portal_database import portal_database

    store = PortalAccessStore()
    store.audit("CASE-A", "password_changed", target="CASE-A")
    store.audit("CASE-A", "user_created", target="bob", details={"via": "invitation"})
    with portal_database(schema="portal_identity").read() as connection:
        rows = [tuple(row) for row in connection.execute(
            "SELECT actor, action, target, details FROM access_audit ORDER BY event_id")]
    assert rows == [("CASE-A", "password_changed", "CASE-A", {}), ("CASE-A", "user_created", "bob", {"via": "invitation"})]
    # Newest first; events recorded within one second keep their insertion order.
    assert [event["action"] for event in store.recent_audit()] == ["user_created", "password_changed"]
    [latest] = store.recent_audit(1)
    assert set(latest) == {"at", "actor", "action", "target", "details"}
    assert latest["details"] == {"via": "invitation"}


def test_access_audit_stores_absolute_times_and_orders_them_by_instant() -> None:
    """访问审计以 TIMESTAMPTZ 保存事件时间并按真实时刻排序

    portal_identity.access_audit 的 at 列是 TIMESTAMPTZ；带不同时区偏移写入的事件按真实时刻从新到旧返回，返回值是以 Z 结尾的 UTC ISO 时间。

    :importance: P2
    """
    from management_common.portal_database import portal_database
    from management_portal import access_audit_store

    with access_audit_store.database().transaction() as connection:
        # The earlier instant has the larger local text, so only TIMESTAMPTZ orders these correctly.
        access_audit_store.append(connection, {"at": "2026-10-14T09:00:00+08:00", "actor": "CASE-A",
                                               "action": "earlier", "target": "", "details": {}})
        access_audit_store.append(connection, {"at": "2026-10-14T02:00:00Z", "actor": "CASE-A",
                                               "action": "later", "target": "", "details": {}})
    with portal_database(schema="portal_identity").read() as connection:
        column_type = connection.execute(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_schema=current_schema() AND table_name='access_audit' AND column_name='at'").fetchone()[0]
    assert column_type == "timestamp with time zone"
    events = access_audit_store.recent(2)
    assert [(event["action"], event["at"]) for event in events] == [
        ("later", "2026-10-14T02:00:00Z"), ("earlier", "2026-10-14T01:00:00Z")]


def test_every_user_may_operate_the_test_lab() -> None:
    """每个登录用户都可以操作 Test Lab

    Test Lab 的访问信息对任何已登录用户都给出 can_operate=true，不再按 Project 范围或旧的系统能力区分。

    :importance: P2
    """
    from management_portal import serve_management_portal as portal_server

    for actor in ("viewer", "engineer"):
        assert portal_server._test_lab_access(actor) == {"actor": actor, "can_operate": True}


def test_user_disable_and_retirement_are_distinct_lifecycle_states_contracts(tmp_path, subtests, isolated_portal_case):
    """停用和删除是不同的账户状态，没有归档态，任何账户名都没有生命周期豁免"""
    with subtests.test(contract='test_user_disable_and_retirement_are_distinct_lifecycle_states'), isolated_portal_case() as case_path:
        tmp_path = case_path
        _projects()
        store = PortalAccessStore()
        store.upsert_user('engineer', actor='CASE-A')
        # Deletion is deletion: there is no archived account between disabled and deleted.
        with pytest.raises(ValueError):
            store.upsert_user('engineer', actor='CASE-A', status='archived')
        disabled = store.upsert_user('engineer', actor='CASE-A', status='disabled', revoke_sessions=True)
        assert disabled['status'] == 'disabled'
        assert not store.is_active('engineer')
        assert store.list_users(['engineer'])[0]['username'] == 'engineer'
        retired = store.retire_user('engineer', actor='CASE-A')
        assert retired['status'] == 'deleted'
        assert 'memberships' not in retired
        assert store.list_users(['engineer']) == []
        assert store.user('engineer')['configured']
    with subtests.test(contract='test_credential_store_deletes_only_the_selected_user'), isolated_portal_case() as case_path:
        tmp_path = case_path
        store = PortalCredentialStore()
        store.set_password('engineer', 'Initial-Pass9!')
        store.set_password('colleague', 'Another-Pass8!')
        assert store.delete_user('engineer')
        assert not store.delete_user('engineer')
        assert set(store.load()) == {'colleague'}
    with subtests.test(contract='test_account_names_have_no_lifecycle_exemption'), isolated_portal_case() as case_path, \
            pytest.MonkeyPatch.context() as patch:
        tmp_path = case_path
        store = PortalAccessStore()
        permission_test_support.enable(patch)
        try:
            for name in ('CASE-A', 'tfi', 'sample_admin', 'test-actor'):
                # No account name passes the account instructions by itself.
                assert not permission_system.allows(name, "CreateUser")
                store.upsert_user(name, actor='ordinary', status='disabled')
                assert store.user(name)['status'] == 'disabled'
                assert store.retire_user(name, actor='ordinary')['status'] == 'deleted'
        finally:
            permission_system.forget_implementation()
    with subtests.test(contract='test_portal_service_actor_is_unscoped'), isolated_portal_case() as case_path:
        tmp_path = case_path
        store = PortalAccessStore()
        assert store.is_service_actor('portal')
    with subtests.test(contract='test_global_read_scope_is_readable_without_project_membership'), isolated_portal_case() as case_path:
        tmp_path = case_path
        _projects()
        store = PortalAccessStore()
        store.upsert_user('reader', actor='CASE-A')
        record = {'resource_id': 'skill_0123456789ab', 'resource_type': 'skill', 'attributes': {'project_ids': ['Shared Skills'], 'read_scope': 'global'}}
        assert resource_query.resource_is_readable(record, 'reader', portal_access=store)
        resource_access.ResourceAccessPolicy().authorize_read(record, actor='reader')


def test_admin_delete_user_removes_only_account_private_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """管理员删除用户只清除其密码、偏好、收藏、上传、头像和 Telegram 绑定，用户名不能再创建"""
    from management_portal import serve_management_portal as portal_server
    from management_portal import user_avatar as avatars

    _projects()
    access = PortalAccessStore()
    credentials = PortalCredentialStore()
    access.upsert_user("CASE-A", actor="CASE-A")
    credentials.set_password("engineer", "Initial-Pass9!")
    credentials.set_password("colleague", "Another-Pass8!")
    access.upsert_user(
        "engineer",
        actor="CASE-A",

    )

    from management_portal import presentation_store as presentation
    for username in ('engineer','colleague'):
        presentation.update_preferences(username, lambda value: {'theme':'dark'})
        presentation.set_favorite(username, 'lineage', 'lineage_0123456789ab', True)
        presentation.set_favorite(username, 'resident', 'resident_0123456789ab', True)
    upload_roots = tuple(tmp_path / name for name in ("images", "attachments", "files"))
    for root in upload_roots:
        (root / "engineer").mkdir(parents=True)
        (root / "engineer" / "private.txt").write_text("private", encoding="utf-8")
        (root / "colleague").mkdir(parents=True)
    avatar_store = avatars.UserAvatarStore(tmp_path / "avatars")
    avatar_store.update("engineer", {"kind": "sprite", "sprite_index": 1})
    colleague_avatar = avatar_store.update("colleague", {"kind": "sprite", "sprite_index": 2})
    from management_portal import telepus_contacts_store as telegram
    with telegram.database().transaction(telegram.LOCK) as connection:
        telegram.save(connection, {"telegram_user_id": "1111111", "name": "engineer", "portal_actor": "Engineer"})
        telegram.save(connection, {"telegram_user_id": "2222222", "name": "colleague", "portal_actor": "colleague"})

    monkeypatch.setattr(portal_server, "_PORTAL_ACCESS", access)
    monkeypatch.setattr(portal_server, "_PORTAL_CREDENTIALS", credentials)
    monkeypatch.setattr(portal_server, "_PORTAL_USER_UPLOAD_ROOTS", upload_roots)
    monkeypatch.setattr(avatars, "user_avatar_store", lambda: avatar_store)

    with pytest.raises(ValueError, match="unknown access-control action"):
        portal_server._admin_access_action("CASE-A", {"action": "archive", "username": "engineer"})
    assert "engineer" in credentials.load()
    assert avatar_store.get("engineer") == avatars.sprite(1)

    result = portal_server._admin_access_action(
        "CASE-A", {"action": "delete", "username": "engineer"}
    )

    assert result["deleted"]
    assert result["deleted_data"]["avatar"] is True
    # The Telegram router authorizes from these bindings; a deleted User's would keep acting as it.
    assert result["deleted_data"]["telepus"] is True
    with telegram.database().read() as connection:
        assert list(telegram.contacts(connection)) == ["2222222"]
    assert access.user("engineer")["status"] == "deleted"
    assert "engineer" not in credentials.load()
    assert "colleague" in credentials.load()
    assert presentation.preferences('engineer') == {}
    assert presentation.preferences('colleague') == {'theme':'dark'}
    assert presentation.favorite_ids('engineer','lineage') == []
    assert presentation.favorite_ids('colleague','lineage') == ['lineage_0123456789ab']
    assert presentation.favorite_ids('engineer','resident') == []
    assert presentation.favorite_ids('colleague','resident') == ['resident_0123456789ab']
    assert all(not (root / "engineer").exists() for root in upload_roots)
    assert all((root / "colleague").is_dir() for root in upload_roots)
    assert avatar_store.get("engineer") == avatars.sprite(0)
    assert avatar_store.get("colleague") == colleague_avatar
    with pytest.raises(FileExistsError, match="retired"):
        portal_server._admin_access_action(
            "CASE-A",
            {
                "action": "create",
                "username": "engineer",
                "password": "Replacement-Pass8!",
            },
        )


def test_user_can_change_own_password_and_revoke_existing_sessions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """用户校验当前密码和强度后可改自己的密码，旧会话随之失效并写入审计"""
    from management_portal import serve_management_portal as portal_server

    access = PortalAccessStore()
    credentials = PortalCredentialStore()
    credentials.set_password("engineer", "Current-Pass7!")
    access.upsert_user("engineer", actor="CASE-A")
    original_version = access.session_version("engineer")
    monkeypatch.setattr(portal_server, "_PORTAL_ACCESS", access)
    monkeypatch.setattr(portal_server, "_PORTAL_CREDENTIALS", credentials)
    monkeypatch.setattr(
        portal_server,
        "_PORTAL_TWO_FACTOR",
        type("DisabledTwoFactor", (), {"enabled": lambda self, username: False})(),
    )
    signing_key_path = tmp_path / "session.key"
    signing_key_path.write_bytes(b"test-portal-session-signing-key-32-bytes")
    monkeypatch.setattr(portal_server, "_SESSION_SIGNING_KEY_PATH", signing_key_path)
    monkeypatch.setattr(portal_server, "_SESSION_AUTHORITY", None)
    monkeypatch.setattr(portal_server, "_SESSION_AUTHORITY_CONFIG", None)
    original_token = portal_server._issue_session_token("engineer")

    with pytest.raises(PermissionError, match="Current password"):
        portal_server._change_portal_password(
            "engineer",
            {
                "current_password": "Wrong-Pass6!",
                "new_password": "Replacement-Pass8!",
                "new_password_confirmation": "Replacement-Pass8!",
            },
        )

    with pytest.raises(ValueError, match="at least three"):
        portal_server._change_portal_password(
            "engineer",
            {
                "current_password": "Current-Pass7!",
                "new_password": "weak-password",
                "new_password_confirmation": "weak-password",
            },
        )
    assert portal_server._verify_credentials("engineer", "Current-Pass7!")

    result = portal_server._change_portal_password(
        "engineer",
        {
            "current_password": "Current-Pass7!",
            "new_password": "Replacement-Pass8!",
            "new_password_confirmation": "Replacement-Pass8!",
        },
    )

    assert result["changed"]
    assert access.session_version("engineer") == original_version + 1
    assert portal_server._verify_session_token(original_token) is None
    assert portal_server._verify_session_token(
        portal_server._issue_session_token("engineer")
    ) == "engineer"
    assert not portal_server._verify_credentials("engineer", "Current-Pass7!")
    assert portal_server._verify_credentials("engineer", "Replacement-Pass8!")
    assert access.recent_audit()[0]["action"] == "password_changed"


def test_password_change_requires_second_factor_when_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """启用两步验证后修改密码必须提供验证码"""
    from management_portal import serve_management_portal as portal_server

    access = PortalAccessStore()
    credentials = PortalCredentialStore()
    credentials.set_password("engineer", "Current-Pass7!")
    access.upsert_user("engineer", actor="CASE-A")

    class EnabledTwoFactor:
        def enabled(self, username: str) -> bool:
            return username == "engineer"

        def verify(self, username: str, code: str) -> str | None:
            return "totp" if username == "engineer" and code == "123456" else None

    monkeypatch.setattr(portal_server, "_PORTAL_ACCESS", access)
    monkeypatch.setattr(portal_server, "_PORTAL_CREDENTIALS", credentials)
    monkeypatch.setattr(portal_server, "_PORTAL_TWO_FACTOR", EnabledTwoFactor())

    payload = {
        "current_password": "Current-Pass7!",
        "new_password": "Replacement-Pass8!",
        "new_password_confirmation": "Replacement-Pass8!",
    }
    with pytest.raises(PermissionError, match="authenticator"):
        portal_server._change_portal_password("engineer", payload)
    assert portal_server._verify_credentials("engineer", "Current-Pass7!")

    result = portal_server._change_portal_password(
        "engineer",
        {**payload, "verification_code": "123456"},
    )
    assert result["changed"]
    assert portal_server._verify_credentials("engineer", "Replacement-Pass8!")


def test_registered_project_residents_share_resource_read_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """已登记项目的 Resident 对所有用户开放 GetResident 读取

    权限系统开启时，默认方案让每个用户对共享对象有 viewer：Project 下 Lineage 里的 Resident 对没有任何手动表项的用户也可由 GetResident 读取。

    :design: decision_b820faf42218
    """
    from tests.cases.permission_test_support import Fact

    _projects()
    store = PortalAccessStore()
    store.upsert_user(
        "engineer",
        actor="CASE-A",

    )
    permissions = permission_test_support.enable(monkeypatch)
    identifiers = ["resident_0123456789ab", "resident_abcdefabcdef"]
    permissions.users("engineer")
    permissions.place(Fact("project", "RobotWorker"), Fact("lineage", "lineage_0123456789ab", {"project": "RobotWorker"}),
                      *(Fact("resident", rid, {"lineage": "lineage_0123456789ab"}) for rid in identifiers))
    try:
        assert permission_system.readable_ids("GetResident", "resident_id", identifiers, actor="engineer") == set(identifiers)
    finally:
        permission_system.forget_implementation()


def test_open_mode_lets_any_user_view_and_manage_lineages_and_residents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """开放模式下任何用户都可查看和管理 Lineage 与 Resident"""
    _projects()
    store = PortalAccessStore()
    for username in ("viewer", "contributor", "project-admin"):
        store.upsert_user(
            username,
            actor="CASE-A",

        )
    monkeypatch.setattr(lineage_domain, "get_portal_access_store", lambda: store)
    monkeypatch.setattr(resident_directory_domain, "get_portal_access_store", lambda: store)
    lineage = {
        "lineage_id": "lineage_0123456789ab",
        "project_id": "RobotWorker",
        "leader_resident_id": "resident_0123456789ab",
    }
    install_resource_boundary(monkeypatch)
    resource_client.sync_lineage(lineage)

    for actor in ("viewer", "contributor", "project-admin", "outsider"):
        lineage_domain._ensure_visible(lineage, actor)
        lineage_domain._ensure_manager(lineage, actor)

    resident = {
        "resident_id": "resident_0123456789ab",
        "lineage_id": "lineage_0123456789ab",
        "project_id": "RobotWorker",
        "lineage_membership": "formal",
    }
    resource_client.sync_resident(resident)
    monkeypatch.setattr(
        lineage_domain,
        "lineage_access_projection",
        lambda _lineage_id: {
            **lineage,
            "lineage_node_ids": [resident["resident_id"]],
            "leader_resident_id": resident["resident_id"],
        },
    )
    for actor in ("viewer", "contributor", "project-admin", "outsider"):
        assert resident_access.resident_visible_to_actor(resident, actor) is True
        assert resident_directory_domain._can_manage(resident, actor) is True
