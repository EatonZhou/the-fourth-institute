"""Portal 管理员保底：方案之外的唯一判定，开关开或关、启用哪套方案都一样

:module: security.identity-access
:importance: P1
:design: decision_b820faf42218
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from management_portal import permission_system, web_isa
from management_portal.hatg.catalog import instruction_catalog
from management_portal.hatg.contracts import InstructionContext, InstructionError
from tests.cases.permission_test_support import Permissions, permissions, portal_admin  # noqa: F401

SWITCH = {"enabled": False}


@pytest.fixture
def open_mode(monkeypatch):
    monkeypatch.setattr(permission_system, "configured_mode", lambda: permission_system.OPEN)


@pytest.fixture
def accounts(monkeypatch):
    """Account handlers use real category storage and a recorded account writer."""
    from management_portal.permission_system.categories import accounts as store

    actions = []
    monkeypatch.setattr(web_isa, "web", lambda _module: SimpleNamespace(
        _require_authenticated_actor=lambda _caller: None, _PORTAL_ACCESS=store(),
        _admin_access_action=lambda caller, payload: actions.append((caller, payload)) or {"username": payload["username"]}))
    for username in ("alice", "bob", "carol"):
        Permissions.account(username)
    portal_admin("alice")
    return actions


def _update(caller, username, **changes):
    for field, handler in (("status", web_isa.update_user_status), ("category", web_isa.update_user_category)):
        if field in changes:
            assert set(changes) == {field}
            return handler(SimpleNamespace(caller=caller, argument={"user_id": username, **changes}.get,
                                           expected_version_for=lambda _object_id: None))
    return web_isa.update_user(SimpleNamespace(caller=caller, argument={"user_id": username, "changes": changes}.get,
                                               expected_version_for=lambda _object_id: None))


def _delete(caller, username):
    return web_isa.delete_user(SimpleNamespace(caller=caller, argument={"user_id": username}.get,
                                               expected_version_for=lambda _object_id: None))


def test_administrators_always_turn_the_switch_and_activate_schemes(permissions, monkeypatch):  # noqa: F811
    """Portal 管理员永远能开关权限系统、读取方案列表、启用方案，方案收不回；非管理员和 Resident 照常按方案判定

    启用的方案拒绝管理员的一切动作时，SetPermissionSystemEnabled、ListPermissionSchemes
    和 ActivatePermissionScheme 仍然通过。读取单个方案及其它执行设置继续按方案判定。
    GetInstructionSchema 的 permission 写出这条放行，/api/me 的 access.allows 对管理员给 true。
    """
    from management_portal.http_portal_session import PAGE_INSTRUCTIONS
    from management_portal.society_service.isa import system_workflows

    permissions.users("bob")
    portal_admin("alice")
    permissions.deny("alice", "@portal", "@actions")
    catalog = instruction_catalog()
    alice, bob = InstructionContext(caller="alice", entry="web"), InstructionContext(caller="bob", entry="web")
    resident = InstructionContext(caller="resident_aaaaaaaaaaaa", entry="web")
    for name, arguments in (("SetPermissionSystemEnabled", SWITCH), ("ActivatePermissionScheme", {"scheme_id": "default_copy"})):
        assert permission_system.authorize_instruction(catalog[name], arguments, alice) is None
        assert permission_system.allows("alice", name, arguments)
        for context in (bob, resident):
            with pytest.raises(InstructionError):
                permission_system.authorize_instruction(catalog[name], arguments, context)
        assert not permission_system.allows("bob", name, arguments)
        assert permission_system.instruction_requirement(name)["portal_admin"]
    # Listing the schemes is guaranteed too, so an administrator switches schemes without turning the system off.
    assert permission_system.authorize_instruction(catalog["ListPermissionSchemes"], {}, alice) is None
    assert permission_system.allows("alice", "ListPermissionSchemes")
    assert permission_system.instruction_requirement("ListPermissionSchemes")["portal_admin"]
    with pytest.raises(InstructionError):
        permission_system.authorize_instruction(catalog["GetPermissionScheme"], {}, alice)
    # Unrelated execution settings retain the scheme's deny.
    with pytest.raises(InstructionError):
        permission_system.authorize_instruction(catalog["UpdateExecutionSettings"], {"harness_retention_days": 7}, alice)
    assert not permission_system.allows("alice", "GetExecutionSettings")
    # The three settings instructions are judged once, at the entry, by the declaration and the scheme;
    # a non-administrator never reaches a handler, and the handlers ask nothing themselves.
    for name, arguments in (("GetExecutionSettings", {}), ("UpdateExecutionSettings", {"harness_retention_days": 7}),
                            ("SetPermissionSystemEnabled", SWITCH)):
        with pytest.raises(InstructionError):
            permission_system.authorize_instruction(catalog[name], arguments, bob)
    with monkeypatch.context() as handlers:
        handlers.setattr(permission_system, "allows", lambda *args, **kwargs: pytest.fail("a handler asked the permission system"))
        handlers.setattr(system_workflows, "_switch_permission_system", lambda enabled, actor: ({"enabled": enabled}, None))
        assert system_workflows.set_permission_system_enabled(SimpleNamespace(
            caller="bob", arguments=dict(SWITCH), argument=SWITCH.get)) == {"settings": {"enabled": False}}
    # The scheme editor's activate button.
    editor = permission_system.manage("GetPermissionSystem", {}, actor="alice")
    assert editor["allows"]["ActivatePermissionScheme"] is True
    assert permission_system.manage("GetPermissionSystem", {}, actor="bob")["allows"]["ActivatePermissionScheme"] is False
    checked = permission_system.manage("CheckInstructionPermission", {"principal_id": "alice", "instruction": "ActivatePermissionScheme",
                                                            "arguments": {"scheme_id": "default_copy"}}, actor="portal")
    assert checked["allowed"] is True and checked["portal_admin"] is True
    assert {"SetPermissionSystemEnabled", "ActivatePermissionScheme"} <= set(PAGE_INSTRUCTIONS)
    assert "portal_admin" not in permission_system.instruction_requirement("CreateUser")


def test_the_guarantee_shows_while_the_system_is_off(open_mode):
    """权限系统关闭时，GetInstructionSchema 同样写出管理员的三条放行"""
    for name in ("SetPermissionSystemEnabled", "ListPermissionSchemes", "ActivatePermissionScheme"):
        assert permission_system.instruction_requirement(name)["portal_admin"]


@pytest.mark.parametrize("mode", ["open", "on"])
def test_only_administrators_change_the_administrator_list(mode, accounts, monkeypatch):
    """分类和状态只有管理员能改；管理员名单始终保留启用中的成员

    开关开或关都一样：非管理员不能把任何人设为或取消 Portal 管理员，也不能停用或删除管理员；
    管理员可以设、取消其他 User，停用或删除其他管理员，但不能取消、停用自己；Resident 永远不是管理员。
    """
    if mode == "open":
        monkeypatch.setattr(permission_system, "configured_mode", lambda: permission_system.OPEN)
    else:
        from management_portal.permission_system.facts import user_fact
        from tests.cases.permission_test_support import enable

        enable(monkeypatch).place(user_fact("alice", category="portal_admin"), user_fact("bob"), user_fact("carol"))
    for caller, target, category in (("bob", "bob", "portal_admin"), ("bob", "carol", "portal_admin"),
                                     ("bob", "alice", "member"), ("resident_aaaaaaaaaaaa", "carol", "portal_admin")):
        with pytest.raises(InstructionError) as refused:
            _update(caller, target, category=category)
        assert refused.value.code == "instruction.forbidden"
    for caller, target, changes in (("bob", "alice", {"status": "disabled"}),):
        with pytest.raises(InstructionError):
            _update(caller, target, **changes)
    with pytest.raises(InstructionError):
        _delete("bob", "alice")
    assert permission_system.user_categories() == {"alice": "portal_admin"}

    assert _update("alice", "bob", category="portal_admin")["category_changed"] is True
    with pytest.raises(InstructionError, match="自己"):
        _update("alice", "alice", category="member")
    with pytest.raises(InstructionError, match="自己"):
        _update("alice", "alice", status="disabled")
    # Separate status/category operations leave profile updates independent.
    _update("alice", "alice", status="active")
    _update("alice", "alice", category="portal_admin")
    _update("alice", "alice", display_name="Alice")
    assert accounts[-1][0] == "alice" and accounts[-1][1]["display_name"] == "Alice"
    if mode == "open":
        _update("carol", "alice", display_name="Alice")
    assert _update("bob", "alice", category="member")["category_changed"] is True
    assert permission_system.user_categories() == {"bob": "portal_admin"}
    # All category changes require an administrator, including member/intern.
    with pytest.raises(InstructionError):
        _update("carol", "alice", category="intern")
    assert _update("bob", "carol", category="intern")["category_changed"] is True
    # Disabling or deleting an administrator is for another administrator.
    assert _update("bob", "alice", category="portal_admin")["category_changed"] is True
    _update("alice", "bob", status="disabled")
    assert accounts[-1][1]["status"] == "disabled"
    _delete("alice", "bob")
    assert accounts[-1][1] == {"action": "delete", "username": "bob"}


def test_concurrent_changes_keep_an_active_administrator(open_mode, accounts, monkeypatch):
    """两位管理员同时互相停用时依次判定，名单里永远留一位启用中的管理员

    alice 停用 bob 的检查和写入在同一个账号事务里；bob 同时停用 alice，要等 alice 写完再判定，
    这时 bob 已不是启用中的管理员，被拒绝。删除管理员同样先在这个事务里停用账号。
    写入后没有启用中的管理员时整次回滚。
    """
    import threading

    from management_portal.permission_system import administrators
    from management_portal.permission_system.categories import accounts as store

    portal_admin("bob")
    checked, written = threading.Event(), threading.Event()

    def write(caller, payload):
        if caller == "alice":
            checked.set()
            assert written.wait(10)
        store().upsert_user(payload["username"], actor=caller, status=payload["status"])
        return {"user": {}}

    monkeypatch.setattr(web_isa, "web", lambda _module: SimpleNamespace(
        _require_authenticated_actor=lambda _caller: None, _PORTAL_ACCESS=store(), _admin_access_action=write))
    first = threading.Thread(target=_update, args=("alice", "bob"), kwargs={"status": "disabled"})
    first.start()
    assert checked.wait(10)
    refused = []

    def second():
        try:
            _update("bob", "alice", status="disabled")
        except InstructionError as exc:
            refused.append(exc.code)

    other = threading.Thread(target=second)
    other.start()
    other.join(0.5)
    assert other.is_alive()  # bob's change waits for alice's transaction
    written.set()
    first.join(10)
    other.join(10)
    assert refused == ["instruction.forbidden"]
    assert store().user("alice")["status"] == "active" and store().user("bob")["status"] == "disabled"

    # A write that would leave nobody active rolls back: the check passes, the block disables the caller too.
    store().upsert_user("bob", actor="alice", status="active")
    with pytest.raises(ValueError, match="至少保留"):
        with administrators.list_change("alice", "bob", status="disabled"):
            store().upsert_user("bob", actor="alice", status="disabled")
            store().upsert_user("alice", actor="alice", status="disabled")
    assert store().user("alice")["status"] == "active" and store().user("bob")["status"] == "active"
