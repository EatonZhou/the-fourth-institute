"""Telepus binding semantics and permission admission, isolated from real contacts."""
from types import SimpleNamespace

import pytest

from management_common.operation_errors import OperationError
from management_common.portal_database import portal_database
from management_portal import resources, web_isa
from management_portal.hatg.contracts import InstructionCall, InstructionContext
from management_portal.hatg.kernel import InstructionKernel
from management_portal.hatg.store import ExecutionStore
from management_portal.watchboards import telepus
from tests.cases.permission_test_support import permissions  # noqa: F401
from tests.cases.test_telepus_binding_and_failure_notifications import contacts  # noqa: F401

D = "article_111111111111"
R = "resident_111111111111"
ADMINISTRATION = ("BindTelepusContactForUser", "UnbindTelepusContactForUser")


@pytest.fixture
def runtime(contacts, monkeypatch):
    monkeypatch.setattr(resources, "get_resource_service", lambda: contacts.service)
    return InstructionKernel(web_isa.binding(), store=ExecutionStore(portal_database(schema="web_instructions")))


@pytest.fixture
def enabled_runtime(contacts, runtime, permissions):
    permissions.administrator("alice")
    return runtime


def call(runtime, name, actor="alice", key="", **args):
    return runtime.execute(InstructionCall(name, args, idempotency_key=key), InstructionContext(caller=actor, entry="web"))


def test_telepus_switch_requires_admin_and_uses_feature_runtime(enabled_runtime, permissions, monkeypatch):
    from management_portal.telepus_runtime import TelepusRuntime

    observed = []
    status = {"available": True, "enabled": False, "running": False, "complete": False,
              "missing": [], "units": [], "error": ""}
    monkeypatch.setattr(TelepusRuntime, "status", lambda self: status)
    monkeypatch.setattr(TelepusRuntime, "set_enabled", lambda self, enabled: observed.append(enabled) or {**status, "enabled": enabled})
    permissions.users("bob")
    assert call(enabled_runtime, "GetTelepusStatus", "bob").error["code"] == "instruction.not_found"
    assert call(enabled_runtime, "SetTelepusEnabled", "bob", enabled=False).error["code"] == "instruction.forbidden"
    assert not observed
    assert call(enabled_runtime, "GetTelepusStatus").raise_for_error()["status"] == status
    result = call(enabled_runtime, "SetTelepusEnabled", key="telepus-on", enabled=True).raise_for_error()
    assert result["status"]["enabled"] is True and observed == [True]


@pytest.mark.parametrize("name", ADMINISTRATION)
def test_administration_instruction_requires_an_administrator_even_for_own_target(enabled_runtime, contacts, permissions, name):
    """Telepus 管理指令即使操作自己的联系人也要求管理员"""
    contacts.execute("bob", id_token="123456")
    permissions.grant("bob", D, "@admin")
    before = contacts.read()
    denied = call(enabled_runtime, name, "bob", user_id="bob", id_token="123456")
    assert denied.error["code"] == "instruction.forbidden"
    assert contacts.read() == before


def test_admin_can_bind_replace_unbind_and_replay_without_account_creation(enabled_runtime, contacts):
    """管理员可为用户绑定、更换、解绑 Telegram 联系人，重放不重复执行且不建账号"""
    result = call(enabled_runtime, "BindTelepusContactForUser", key="bind-bob", user_id="BOB", id_token="123456").raise_for_error()
    assert result["contact"]["portal_actor"] == "bob"
    saved = contacts.read()
    again = call(enabled_runtime, "BindTelepusContactForUser", key="bind-bob", user_id="BOB", id_token="123456")
    # ISA replay keeps object references, not the original display payload.
    assert again.replayed and again.result == {"contact": {"telegram_user_id": "123456"}}
    assert contacts.read() == saved
    changed = call(enabled_runtime, "BindTelepusContactForUser", user_id="bob", id_token="654321", previous_id_token="123456").raise_for_error()
    assert telepus._read_telepus_telegram_user_contacts() == {"654321": changed["contact"]}
    contacts.users[1]["status"] = "disabled"
    removed = call(enabled_runtime, "UnbindTelepusContactForUser", user_id="bob", id_token="654321").raise_for_error()
    assert removed == {"user_id": "bob", "telegram_user_id": "654321", "removed": True}
    assert call(enabled_runtime, "UnbindTelepusContactForUser", user_id="bob", id_token="654321").raise_for_error()["removed"] is False
    assert [u["username"] for u in contacts.users] == ["alice", "bob"]


@pytest.mark.parametrize("username,status", [("missing", None), ("bob", "disabled"), ("bob", "deleted")])
def test_bind_never_creates_or_reactivates_target(enabled_runtime, contacts, username, status):
    """目标用户不存在、已停用或已删除时 BindTelepusContactForUser 拒绝绑定"""
    if status:
        contacts.users[1]["status"] = status
    result = call(enabled_runtime, "BindTelepusContactForUser", user_id=username, id_token="123456")
    assert result.error["code"] == "instruction.invalid_arguments"
    assert contacts.read() == {}
    assert [u["username"] for u in contacts.users] == ["alice", "bob"]


def test_admin_binding_cannot_silently_overwrite_other_user_or_stale_binding(enabled_runtime, contacts):
    """管理员绑定不能覆盖他人的 Telegram 联系人或过期的原绑定，失败时联系人不变"""
    contacts.execute("alice", id_token="123456")
    contacts.execute("bob", id_token="654321")
    before = contacts.read()
    attempts = [
        ("BindTelepusContactForUser", dict(user_id="bob", id_token="777777", previous_id_token="123456")),
        ("BindTelepusContactForUser", dict(user_id="bob", id_token="123456", previous_id_token="654321")),
        ("BindTelepusContactForUser", dict(user_id="bob", id_token="777777", previous_id_token="999999")),
        ("BindTelepusContactForUser", dict(user_id="bob", id_token="777777")),
        ("UnbindTelepusContactForUser", dict(user_id="bob", id_token="123456")),
    ]
    for name, args in attempts:
        result = call(enabled_runtime, name, **args)
        assert result.error
        assert contacts.read() == before


def test_revoking_admin_takes_effect_before_next_contact_mutation(enabled_runtime, contacts, permissions):
    """撤销管理员后，下一次联系人修改立即被拒绝"""
    permissions.administrator("bob")
    call(enabled_runtime, "BindTelepusContactForUser", "bob", user_id="alice", id_token="123456").raise_for_error()
    permissions.administrator("bob", False)
    denied = call(enabled_runtime, "UnbindTelepusContactForUser", "bob", user_id="alice", id_token="123456")
    assert denied.error["code"] == "instruction.forbidden"
    assert "123456" in telepus._read_telepus_telegram_user_contacts()


def test_self_delete_uses_authenticated_user_and_checks_binding_owner(contacts):
    """用户自行解绑只能删除本人的联系人，不能指定他人，Resident 不能操作"""
    contacts.execute("bob", id_token="123456")
    before = contacts.read()
    with pytest.raises(OperationError) as conflict:
        telepus.act(SimpleNamespace(actor="alice"), "delete", {"id_token": "123456"})
    assert conflict.value.code == "telepus.binding_conflict" and conflict.value.http_status == 409
    with pytest.raises(ValueError, match="Unsupported"):
        telepus.act(SimpleNamespace(actor="alice"), "delete", {"id_token": "123456", "username": "bob"})
    with pytest.raises(ValueError, match="User"):
        telepus.act(SimpleNamespace(actor=R), "delete", {"id_token": "123456"})
    assert contacts.read() == before
    assert telepus.act(SimpleNamespace(actor="bob"), "delete", {"id_token": "123456"})["removed"]


def test_stale_delete_cannot_remove_a_reassigned_telegram_id(contacts):
    """Telegram ID 已转给他人后，原用户的过期解绑请求被拒绝"""
    contacts.execute("alice", id_token="123456")
    telepus.unbind_contact("alice", "123456")
    contacts.execute("bob", id_token="123456")
    with pytest.raises(OperationError, match="原联系人"):
        telepus.unbind_contact("alice", "123456")
    assert telepus._read_telepus_telegram_user_contacts()["123456"]["portal_actor"] == "bob"
