"""The permission system switch in 执行设置 (decision_b820faf42218 §3.1).

Off, there is no permission system: every signed-in User and Resident may do
everything and a personal AMA stays its owner's; nothing else is asked and
the PAR implementation is not loaded.  Permission instructions still run,
unchecked.  Turned back on, the instance is brought up to date first.
"""
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from management_portal import permission_system
from management_portal.hatg.contracts import InstructionContext, InstructionError
from tests.cases.permission_test_support import enable

ROOT = Path(__file__).resolve().parents[2]
LINEAGE = "lineage_222222222222"
# The suite runs with the permission system off; cases of the saved switch read the real one.
SAVED_MODE = permission_system.configured_mode


@pytest.fixture
def open_mode(monkeypatch):
    monkeypatch.setattr(permission_system, "configured_mode", lambda: permission_system.OPEN)


def test_open_mode_never_loads_the_permission_system():
    """权限系统关闭时只看是否登录和个人 AMA，不加载任何权限代码

    在新进程中以关闭模式导入各服务入口并调用权限端口的全部函数（权限指令除外）：已登录主体全部放行，OpenTerminal 等也不例外；
    个人 AMA 只给所有者；PAR 引擎、方案、适配层、临时凭证和管理模块都不会被加载。
    """
    code = """
import sys
from management_portal import permission_system as ps
ps.configured_mode = lambda: ps.OPEN
import management_portal.serve_management_portal, management_portal.resources.server
import management_portal.society_service.server, management_portal.execution.service
from management_portal.hatg.catalog import instruction_catalog
from management_portal.hatg.contracts import InstructionContext
context = InstructionContext(caller="alice", entry="web")
lineage = "lineage_222222222222"
assert ps.authorize_instruction(instruction_catalog()["GetLineage"], {"lineage_id": lineage}, context) is None
assert ps.authorize_reads(instruction_catalog()["GetLineage"], [{}, {}], context) == [None, None]
assert ps.readable_ids("GetLineage", "lineage_id", [lineage], actor="alice") == {lineage}
assert ps.instruction_requirement("GetLineage") == {"required": False, "requirements": []}
assert ps.allows("alice", "UpdatePermissionScheme") and not ps.owners_only()
assert dict(ps.permitted("alice", "UpdateLineage", "lineage_id", [lineage])) == {lineage: True}
assert ps.permitted_ids("alice", "GetProject", "project_id", ["AI员工"]) == {"AI员工"}
# A personal AMA stays its owner's: the one rule kept with the switch off.
assert ps.permitted_ids("alice", "GetLineage", "lineage_id", [lineage], owners={lineage: "bob"}) == set()
assert ps.permitted_ids("bob", "GetLineage", "lineage_id", [lineage], owners={lineage: "bob"}) == {lineage}
assert not ps.private_reaches("alice", "bob", "GetLineage", "lineage_id", lineage) and ps.hidden_private("alice", {lineage: "bob"}) == {lineage}
assert ps.users_permitted("UpdateLineage", "lineage_id", lineage) is None
assert ps.sync_resource({"resource_id": lineage}, resource_type="lineage") is False
assert ps.retire_resources([lineage]) == [] and ps.sync_user("alice") is None
assert ps.rename_element(None, "a", "b", apply=True) == 0
assert ps.allows("alice", "OpenTerminal") and ps.allows("alice", "UpdateUser")
assert ps.instruction_requirement("OpenTerminal") == {"required": False, "requirements": []}
from management_portal.portal_access_control import get_portal_access_store
get_portal_access_store().upsert_user("alice", actor="portal")
assert ps.set_user_category("alice", "intern", actor="portal") and ps.user_categories() == {"alice": "intern"}
ps.maintain()
assert "权限系统已关闭" in ps.prompt_rule()
GUARDED = ("management_portal.permission_system.par", "management_portal.permission_system.engine",
           "management_portal.permission_system.facts", "management_portal.permission_system.requests",
           "management_portal.permission_system.scheme", "management_portal.permission_system.management",
           "management_portal.permission_system.temporary")
def loaded():
    return sorted(name for name in sys.modules if any(name == g or name.startswith(g + ".") for g in GUARDED))
assert not loaded(), loaded()
import management_portal.permission_system.par
assert "management_portal.permission_system.par" in loaded()  # the check does see a loaded permission module
print("ok")
"""
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT)},
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0 and result.stdout.strip().endswith("ok"), result.stderr[-3000:]


def test_permission_instructions_run_unchecked_while_the_system_is_off(monkeypatch):
    """权限系统关闭时，权限指令照常执行，不问调用者能否调用

    入口对全部权限指令放行；没有任何表项的实习用户也能修改、删除别人的表项，查看别人的有效权限；临时凭证仍只能由签发者撤回或接收者交还。
    指令本身的含义不变：试判定（CheckPermission）和有效权限按启用中方案的实例作答，试判定报告结果、原因与命中的表项
    （decision_b820faf42218 §5.10）；签发临时凭证仍要求签发者此刻持有。
    """
    from management_portal.hatg.catalog import instruction_catalog

    permissions = enable(monkeypatch)
    permissions.users("alice")
    permissions.users("bob", category="intern")
    granted = permissions.grant("alice", LINEAGE, "@admin")
    entry = permission_system.manage("CreatePermissionEntry", {"p": "alice", "a": "@viewer", "r": "@operations"},
                                     actor="alice")["entry"]
    loan = permission_system.manage("IssueTemporaryCredential", {"principal_id": "carol", "a": "@viewer", "resource_id": LINEAGE},
                                    actor="alice")
    monkeypatch.setattr(permission_system, "configured_mode", lambda: permission_system.OPEN)
    context = InstructionContext(caller="bob", entry="web")
    for name in ("IssueTemporaryCredential", "RevokeTemporaryCredential", "UpdatePermissionScheme", "DeletePermissionEntry",
                 "GetEffectivePermissions", "UpdateUser", "OpenTerminal"):
        assert permission_system.authorize_instruction(instruction_catalog()[name], {"principal_id": "alice"}, context) is None
    assert permission_system.manage("GetPermissionSystem", {}, actor="bob")["allows"]["UpdatePermissionScheme"] is True
    updated = permission_system.manage("UpdatePermissionEntry", {"entry_id": entry["entry_id"], "disabled": True},
                                       actor="bob", expected_version=1)["entry"]
    assert updated["disabled"] is True
    assert permission_system.manage("GetEffectivePermissions", {"principal_id": "alice"}, actor="bob")["principal_id"] == "alice"
    checked = permission_system.manage("CheckPermission", {"principal_id": "alice", "a": "@admin", "resource_id": LINEAGE},
                                       actor="bob")
    assert checked["allowed"] is True and checked["requests"][0]["permit"]["entry_id"] == granted.entry_id
    refused = permission_system.manage("CheckPermission", {"principal_id": "bob", "a": "@admin", "resource_id": LINEAGE},
                                       actor="bob")["requests"][0]
    assert refused["allowed"] is False and refused["reason"] and "permit" not in refused
    with pytest.raises(PermissionError):
        permission_system.manage("IssueTemporaryCredential", {"principal_id": "carol", "a": "@admin", "resource_id": LINEAGE},
                                 actor="bob")
    # Only the issuer or the recipient revokes a credential: a domain rule, in force with the system off too.
    with pytest.raises(PermissionError):
        permission_system.manage("RevokeTemporaryCredential", {"credential_id": loan["credential_id"]}, actor="bob")
    assert permission_system.manage("RevokeTemporaryCredential", {"credential_id": loan["credential_id"]},
                                    actor="carol") == {"revoked": loan["credential_id"]}
    assert permission_system.manage("DeletePermissionEntry", {"entry_id": entry["entry_id"]}, actor="bob")["deleted"] == [entry["entry_id"]]


def test_the_system_tells_which_editor_instructions_the_caller_passes(monkeypatch):
    """权限系统开启时按编辑器的每个按钮报告调用者能否通过它调用的指令

    allows 按指令名给出调用者在启用中方案下能否通过：默认方案里管理员能通过方案的增删改与启用，普通 User 都不能；
    表项指令按每条表项的 r 判定，这里对所有人都答 true，由 ListPermissionEntries 的 allows 逐条作答。
    """
    permissions = enable(monkeypatch)
    try:
        permissions.users("alice", "admin")
        permissions.administrator("admin")
        admin = permission_system.manage("GetPermissionSystem", {}, actor="admin")["allows"]
        alice = permission_system.manage("GetPermissionSystem", {}, actor="alice")["allows"]
        schemes = ("CreatePermissionScheme", "UpdatePermissionScheme", "DeletePermissionScheme", "ActivatePermissionScheme")
        assert all(admin[name] for name in schemes) and not any(alice[name] for name in schemes)
        entries = ("CreatePermissionEntry", "UpdatePermissionEntry", "DeletePermissionEntry")
        assert set(admin) == {*schemes, *entries}
        assert all(admin[name] and alice[name] for name in entries)
    finally:
        permission_system.forget_implementation()


def test_listing_a_resources_entries_tells_its_grant_and_delete_buttons(monkeypatch):
    """列出某个资源的表项时，同时报告调用者能否在这个资源上写入、删除表项，以及能否修改、删除列出的每一条

    授予和删除按钮都问 CreatePermissionEntry 对 r 的要求：Project 的创建者能写入，实习用户不能。
    每条表项的 allows 按它自己的 r 作答：上级节点上的表项，创建者也不能修改或删除。
    只按主体列出时没有可判定的资源，结果不带 allows。
    """
    from tests.cases.permission_test_support import Fact

    permissions = enable(monkeypatch)
    try:
        permissions.users("alice", "bob", category="intern")
        permissions.place(Fact("project", "Demo", creator="alice"))
        listed = lambda actor: permission_system.manage("ListPermissionEntries", {"r": "Demo", "r_scope": "ancestors"}, actor=actor)  # noqa: E731
        assert listed("alice")["allows"] == {"CreatePermissionEntry": True, "DeletePermissionEntry": True}
        assert listed("bob")["allows"] == {"CreatePermissionEntry": False, "DeletePermissionEntry": False}
        rows = {(entry["r"], entry["allows"]["UpdatePermissionEntry"], entry["allows"]["DeletePermissionEntry"])
                for entry in listed("alice")["entries"]}
        assert ("Demo", True, True) in rows and ("@shared", False, False) in rows
        assert not any(entry["allows"]["DeletePermissionEntry"] for entry in listed("bob")["entries"])
        assert "allows" not in permission_system.manage("ListPermissionEntries", {"p": "alice"}, actor="alice")
    finally:
        permission_system.forget_implementation()


def test_failures_go_to_the_creating_user_while_the_system_is_off(open_mode, monkeypatch):
    """权限系统关闭时失败通知发给创建者

    关闭时没有权限表项可以说明谁能修复，端口回答“没有权限系统”，失败通知改取 Lineage 描述记录中的创建 User；Resident 创建的没有收件人。
    """
    from management_portal.society_service import lineage_failure_notifications as notifications
    from management_portal.resources import collaboration_registry

    creators = {LINEAGE: "alice", "lineage_666666666666": "resident_555555555555"}

    class Descriptors:
        def get(self, resource_id):
            if resource_id not in creators:
                raise KeyError(resource_id)
            return {"attributes": {"created_by": creators[resource_id]}}

    monkeypatch.setattr(collaboration_registry, "CollaborationDescriptorStore", Descriptors)
    assert permission_system.users_permitted("UpdateLineage", "lineage_id", LINEAGE) is None
    assert notifications.creating_user(LINEAGE) == ["alice"]
    assert notifications.creating_user("lineage_666666666666") == []
    assert notifications.creating_user("lineage_777777777777") == []


def test_open_mode_lifts_creator_and_uploader_rules(monkeypatch, tmp_path):
    """权限系统关闭时取消上传者限制

    附件删除不再只限上传者；任何已登录调用者都可以操作。
    """
    from management_portal.resources import isa as resource_isa

    monkeypatch.setattr(permission_system, "configured_mode", lambda: permission_system.PAR)
    monkeypatch.setattr(resource_isa, "read_upload", lambda upload_id: {"created_by": "alice"})
    monkeypatch.setattr(resource_isa, "_upload_dir", lambda upload_id: tmp_path / upload_id)
    invocation = SimpleNamespace(argument=lambda name: "upload_abc", caller="bob")
    with pytest.raises(InstructionError):
        resource_isa.delete_upload(invocation)

    monkeypatch.setattr(permission_system, "configured_mode", lambda: permission_system.OPEN)
    assert resource_isa.delete_upload(invocation) == {"upload_id": "upload_abc"}


def test_mode_follows_the_saved_setting(monkeypatch):
    """权限模式跟随执行设置中保存的开关

    权限端口按 permission_system_enabled 选择 PAR 引擎或开放模式；非布尔值被拒绝，默认开启。
    """
    from management_portal.execution.service_settings import ExecutionSettings

    monkeypatch.setattr(permission_system, "configured_mode", SAVED_MODE)
    settings = ExecutionSettings()
    try:
        assert settings.get()["permission_system_enabled"] is True
        permission_system.forget_mode()
        assert permission_system.mode() == permission_system.PAR
        assert settings.update_permission_system(False)["permission_system_enabled"] is False
        permission_system.forget_mode()
        assert permission_system.mode() == permission_system.OPEN
        # Other processes follow within the refresh period, without a restart.
        assert permission_system.configured_mode() == permission_system.OPEN
        with pytest.raises(ValueError):
            settings.update_permission_system("off")
    finally:
        settings.update_permission_system(True)
        permission_system.forget_mode()
    assert permission_system.mode() == permission_system.PAR


def test_turning_it_on_completes_the_instance_before_checks_resume(monkeypatch):
    """开启权限系统前先按当前对象补齐启用中方案的实例

    补齐在开关仍关闭时执行，随后写入开关；重复开启不再补齐。
    """
    from management_portal.execution.service_settings import ExecutionSettings
    from management_portal.society_service.isa import system_workflows

    settings = ExecutionSettings()
    settings.update_permission_system(True)
    calls = []
    report = {"scheme_id": "default", "regenerate": False, "added_nodes": 3, "added_entries": 5}

    def complete(actor):
        calls.append(("complete", actor, settings.permission_system_enabled()))
        return report

    monkeypatch.setattr(permission_system, "reconcile_before_enabling", complete)
    try:
        off, reconciled = system_workflows._switch_permission_system(False, "alice")
        assert off["permission_system_enabled"] is False and reconciled is None
        assert calls == []
        on, reconciled = system_workflows._switch_permission_system(True, "alice")
        assert on["permission_system_enabled"] is True and reconciled == report
        assert calls == [("complete", "alice", False)]
        assert system_workflows._switch_permission_system(True, "alice")[1] is None
        assert len(calls) == 1
    finally:
        settings.update_permission_system(True)
        permission_system.forget_mode()


def test_generating_reads_every_current_object(isolated_portal_case, monkeypatch):
    """生成权限实例时读取全部现有对象

    User、Project、Lineage、Resident 及其自有 Workspace、Family、聊天室、帖子和索引中的资源都按归属放进树里，已删除的对象不放；
    索引里同样列出的 Resident 自有 Workspace 仍放在它的 Resident 下。
    """
    from management_portal import chatroom_store, notice_store, portal_access_control, project_registry
    from management_portal.society_service import state_store
    from management_portal.permission_system.facts import collect_portal_facts
    from management_portal.permission_system.par import PARPermissions

    resident, workspace, family = "resident_111111111111", "workspace_111111111111", "family_111111111111"
    records = {
        "lineage": [{"resource_id": LINEAGE, "status": "active", "project_ids": ["AI员工"], "attributes": {"created_by": "alice"}},
                    {"resource_id": "lineage_999999999999", "status": "deleted", "deleted_at": "2026-10-16T00:00:00Z"}],
        "resident": [{"resource_id": resident, "status": "active", "relations": [{"kind": "lineage", "target_id": LINEAGE}],
                      "attributes": {"created_by": "alice"}}],
        "family": [{"resource_id": family, "status": "active",
                    "attributes": {"lineage_id": LINEAGE, "steward_resident_id": resident}}],
    }
    service = SimpleNamespace(
        _collaboration=SimpleNamespace(list=lambda kind: records[kind]),
        _index=lambda: SimpleNamespace(authorization_snapshot=lambda: {"records": [
            {"resource_id": "decision_111111111111", "resource_type": "decision", "attributes": {"project_ids": ["AI员工"]}},
            # The index also lists the Resident's own Workspace, without naming the Resident.
            {"resource_id": workspace, "resource_type": "workspace", "attributes": {"status": "active"}}]}),
    )
    monkeypatch.setattr(portal_access_control, "get_portal_access_store",
                        lambda: SimpleNamespace(snapshot=lambda: {"users": {"alice": {}, "bob": {}}}))
    monkeypatch.setattr(project_registry, "load", lambda: {"projects": [{"id": "AI员工"}]})
    monkeypatch.setattr(chatroom_store.ChatroomState, "list", lambda self, actor=None: [
        {"chatroom_id": "chatroom_111111111111", "created_by": "alice", "members": ["alice", "bob"]}])
    monkeypatch.setattr(notice_store.NoticeState, "read", lambda self, default=None: {"posts": [
        {"post_id": "notice_111111111111", "author": "bob"}]})
    owned = SimpleNamespace(fetchall=lambda: [{"resident_id": resident, "owned_workspace_id": workspace}])
    reader = SimpleNamespace(execute=lambda *args: owned)

    class Database:
        def read(self):
            from contextlib import nullcontext
            return nullcontext(reader)

    monkeypatch.setattr(state_store, "get_lineage_data_store", lambda: SimpleNamespace(database=Database()))

    facts = {fact.id: fact for fact in collect_portal_facts(service)}
    assert set(facts) == {"alice", "bob", "AI员工", LINEAGE, resident, family, "chatroom_111111111111",
                          "notice_111111111111", "decision_111111111111"}
    assert [dependent.id for dependent in facts[resident].dependents] == [workspace]
    with isolated_portal_case():
        permissions = PARPermissions()
        report = permissions.regenerate(facts.values())
        assert report["scheme_id"] == "default" and not permissions.stale_builtin()
        paths = permissions.store.paths("R", [workspace, "decision_111111111111", "notice_111111111111", "personal_space:bob"])
        assert paths[workspace] == ["@portal", "@shared", "@projects", "AI员工", LINEAGE, resident, workspace]
        assert paths["decision_111111111111"][-2] == "AI员工"
        assert paths["notice_111111111111"][-2] == "@shared"
        assert paths["personal_space:bob"] == ["@portal", "@personal", "personal_space:bob"]
