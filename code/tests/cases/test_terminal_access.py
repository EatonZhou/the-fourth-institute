"""Opening a Terminal is the OpenTerminal instruction; Users fall into three categories.

:design: decision_600ae27b0538
"""
from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from management_portal import local_terminal_backend, permission_system, web_isa
from management_portal.hatg.catalog import instruction_catalog
from management_portal.hatg.contracts import InstructionContext, InstructionError
from management_portal.permission_system.categories import Categories
from management_portal.permission_system.scheme.compiler import SchemeError, compile_scheme
from management_portal.permission_system.scheme.repository import builtin_documents
from tests.cases.permission_test_support import permissions  # noqa: F401


@pytest.fixture
def shells(monkeypatch):
    """Record the shells a route would start instead of spawning them."""
    started = []
    monkeypatch.setattr(local_terminal_backend, "start_terminal",
                        lambda actor, *args, **kwargs: started.append(("host", actor)) or {"session_id": "s1", "alive": True})
    monkeypatch.setattr(local_terminal_backend, "start_device_terminal",
                        lambda actor, device, *args, **kwargs: started.append((device, actor)) or {"session_id": "s2", "alive": True})
    return started


@pytest.fixture
def open_mode(monkeypatch):
    monkeypatch.setattr(permission_system, "configured_mode", lambda: permission_system.OPEN)


def test_open_mode_opens_the_terminal_to_every_signed_in_user(open_mode, shells):
    """权限系统关闭时，任何已登录 User 都能打开 Terminal，也能调用 UpdateUser

    关闭时没有按 User 分类的例外（sample_admin 2026-10-18）：入口检查、allows、主机 shell 与 Device 终端都放行一般用户；声明不再写分类。
    一般用户与实习用户之间的调整同样放开；管理员名单只有 Portal 管理员能改（test_portal_admin_guarantee）。
    """
    context = InstructionContext(caller="alice", entry="web")
    for name in ("OpenTerminal", "UpdateUser"):
        assert permission_system.authorize_instruction(instruction_catalog()[name], {}, context) is None
        assert permission_system.allows("alice", name) and "user_categories" not in instruction_catalog()[name].schema()
    local_terminal_backend.open_terminal("alice")
    local_terminal_backend.open_terminal("alice", device_id="device_aaaaaaaaaaaa")
    assert shells == [("host", "alice"), ("device_aaaaaaaaaaaa", "alice")]


def test_terminal_routes_answer_403_and_residents_never_open_one(permissions, shells, monkeypatch):  # noqa: F811
    """权限系统开启时，Terminal 的 HTTP 入口对没有 OpenTerminal 的 User 返回 403；Resident 不能打开 Terminal"""
    from management_portal.http_agent_workbench import AgentWorkbenchHttp
    from management_portal.http_terminal import TerminalHttp

    permissions.users("bob")
    answered = []
    respond = lambda _request, status, body: answered.append((status, body)) or (status, body)  # noqa: E731
    workbench = AgentWorkbenchHttp.__new__(AgentWorkbenchHttp)
    workbench.local_terminal_backend = lambda: local_terminal_backend
    workbench.respond = respond
    workbench.error = lambda request, exc: respond(request, 403 if isinstance(exc, PermissionError) else 500, {"error": str(exc)})
    assert workbench.post_local_terminal_start(None, {}, "bob")[0] == 403
    assert workbench.post_local_terminal_control(None, {"action": "restart"}, "bob")[0] == 403
    device = TerminalHttp(session_actor=lambda _request: "bob", read_json=lambda _request: {"device_id": "device_aaaaaaaaaaaa"},
                          respond=respond, exception_response=lambda _request, exc: pytest.fail(f"unexpected {exc!r}"))
    assert device.start(None, None, "bob")[0] == 403
    assert shells == []

    permissions.administrator("bob")
    with pytest.raises(InstructionError, match="Resident"):
        web_isa.open_terminal(SimpleNamespace(caller="resident_aaaaaaaaaaaa", argument=lambda *_args: None))
    result = web_isa.open_terminal(SimpleNamespace(caller="bob", argument=lambda *_args: None))
    assert result == {"session_id": "s1", "alive": True, "cwd": ""} and shells == [("host", "bob")]


def test_user_category_changes_are_audited_and_unchanged_ones_are_not_changes(open_mode, monkeypatch):
    """UpdateUser 修改 User 分类写入访问审计，不变的分类不算修改，不存在的 User 被拒绝

    分类写在 changes.category 中；一般用户与实习用户之间的调整按 UpdateUser 自身的检查，关闭时全部放开（sample_admin 2026-10-18）。
    """
    users = {"alice": {"configured": True, "status": "active"}, "bob": {"configured": True, "status": "active"}}
    audit = []
    monkeypatch.setattr(web_isa, "web", lambda _module: SimpleNamespace(_require_authenticated_actor=lambda _caller: None))
    monkeypatch.setattr(web_isa, "_access", lambda: SimpleNamespace(
        user=lambda name: users.get(name, {"configured": False}), is_active=lambda name: users[name]["status"] == "active",
        audit=lambda *args, **kwargs: audit.append((args, kwargs))))
    change = lambda username, category, caller="bob": web_isa.update_user(SimpleNamespace(  # noqa: E731
        caller=caller, argument={"user_id": username, "changes": {"category": category}}.get))
    from tests.cases.permission_test_support import Permissions

    Permissions.account("alice")
    Permissions.account("bob")
    result = change("alice", "intern")
    assert result["category_changed"] is True and result["user"]["category"] == "intern"
    assert change("alice", "intern")["category_changed"] is False
    assert change("bob", "intern", caller="alice")["category_changed"] is True
    assert permission_system.user_categories() == {"alice": "intern", "bob": "intern"}
    assert [args[1] for args, _ in audit] == ["user_category_updated"] * 2
    with pytest.raises(InstructionError):
        change("nobody", "member")


def test_default_scheme_places_users_by_category(permissions):  # noqa: F811
    """默认方案按 User 分类把 User 放进 P 树，OpenTerminal 只给 Portal 管理员，失败通知发给他们

    User 位于 Portal 管理员、一般用户或实习用户节点下，改分类即移动位置；Portal 管理员的表项在分类节点上，
    能执行 OpenTerminal，并作为能修改共享 Lineage 的 User 收到失败通知。
    """
    from tests.cases.permission_test_support import Fact

    permissions.users("alice", "bob")
    store = permissions.store
    assert store.paths("P", ["alice"])["alice"] == ["@principals", "@users", "@members", "alice"]
    permissions.administrator("alice")
    assert store.paths("P", ["alice"])["alice"] == ["@principals", "@users", "@portal_admins", "alice"]
    assert permission_system.allows("alice", "OpenTerminal")
    assert not permission_system.allows("bob", "OpenTerminal")
    permission_system.set_user_category("bob", "intern", actor="test-actor")
    assert store.paths("P", ["bob"])["bob"] == ["@principals", "@users", "@interns", "bob"]
    lineage = "lineage_aaaaaaaaaaaa"
    permissions.place(Fact("lineage", lineage))
    assert permission_system.users_permitted("UpdateLineage", "lineage_id", lineage) == ["alice"]


def test_schemes_name_user_categories_on_p_class_nodes():
    """User 分类只能写在 P 树分类节点上，每类一个"""
    default = builtin_documents()["default"]
    compiled = compile_scheme(default)
    assert dict(compiled.user_categories) == {"portal_admin": "@portal_admins", "member": "@members", "intern": "@interns"}

    def errors(document):
        with pytest.raises(SchemeError) as raised:
            compile_scheme(document)
        return {item["path"] for item in raised.value.errors}

    broken = copy.deepcopy(default)
    nodes = broken["trees"]["P"]["nodes"]
    index = {node["id"]: position for position, node in enumerate(nodes)}
    nodes[index["@interns"]]["user_category"] = "portal_admin"  # a second node for one category
    nodes[index["@resident"]]["user_category"] = "member"  # an each node
    broken["trees"]["R"]["nodes"][0]["user_category"] = "member"  # not the P tree
    broken["entries"].append({"id": "old_selector", "effect": "permit", "p": {"select": "creator"},
                              "a": "@viewer", "r": "@shared"})
    assert errors(broken) == {
        f"trees.P.nodes[{index['@interns']}].user_category", f"trees.P.nodes[{index['@resident']}].user_category",
        "trees.R.nodes[0].user_category", f"entries[{len(broken['entries']) - 1}].p"}


def test_a_permitted_terminal_joins_the_hidden_rail_after_the_page_is_built():
    """/api/me 晚于工作台返回时，有 OpenTerminal 权限的终端以隐藏面板加入，不改动已固定的布局

    :module: workbench.panels
    """
    import subprocess
    from pathlib import Path

    source = Path(__file__).with_name("javascript") / "workbench_hidden_panel_add.cjs"
    result = subprocess.run(["node", str(source)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_users_category_is_its_account_record_and_moves_it_in_the_same_transaction(permissions, monkeypatch):
    """User 分类保存在账号记录中，改分类与 P 树中的移动同一事务提交

    分类是账号记录的 category 字段，账号列表和 Categories 都从这里读；移动失败时分类改动一起回滚，账号记录和 P 树位置都不变；
    删除账号把 User 移出 P 树，分类回到一般用户。
    """
    from management_portal.permission_system import par
    from management_portal.portal_access_control import get_portal_access_store

    permissions.users("bob")
    store = permissions.store
    assert permission_system.set_user_category("bob", "intern", actor="test-actor")
    assert get_portal_access_store().user("bob")["category"] == "intern" and Categories().all() == {"bob": "intern"}
    assert store.paths("P", ["bob"])["bob"][-2] == "@interns"

    def fails(*_args, **_kwargs):
        raise RuntimeError("moving failed")
    with monkeypatch.context() as patched:
        patched.setattr(par.PARPermissions, "sync_user", fails)
        with pytest.raises(RuntimeError, match="moving failed"):
            permission_system.set_user_category("bob", "portal_admin", actor="test-actor")
    assert get_portal_access_store().user("bob")["category"] == "intern"
    assert store.paths("P", ["bob"])["bob"][-2] == "@interns"

    get_portal_access_store().retire_user("bob", actor="test-actor")
    assert store.paths("P", ["bob"]) == {} and Categories().all() == {}
