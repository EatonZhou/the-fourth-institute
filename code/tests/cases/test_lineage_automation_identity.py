"""The unattended identity is explicit, controlled, and independent of timers."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from management_common.operation_identity import operation_identity_scope
from management_common.principal import OperationIdentity
from management_portal.society_service import lineage_automation_identity as identity
from management_portal.society_service import lineage_domain, lineage_program_runtime
from management_portal.society_service.isa import definition
from tests.cases.test_lineage_generic_primitives import primitive_store  # noqa: F401
from tests.cases.permission_test_support import permissions  # noqa: F401


USER = {"kind": "user", "id": "sample_admin"}
RESIDENT = "resident_aaaaaaaaaaaa"


@pytest.fixture
def accounts(monkeypatch):
    users = {"sample_admin": True, "alice": True, "disabled": False}
    monkeypatch.setattr(identity, "get_portal_access_store", lambda: SimpleNamespace(
        is_configured=lambda user: user in users,
        is_active=lambda user: users.get(user, False)))
    return users


@pytest.mark.parametrize("value", ["sample_admin", {}, {"kind": "service", "id": "portal"},
                                    {"kind": "resident", "id": RESIDENT}, {**USER, "admin": True}])
def test_run_as_requires_an_explicit_user_object(value):
    """run_as_principal 只接受仅含 kind=user 和 id 的对象"""
    with pytest.raises(ValueError):
        identity.requested({"run_as_principal": value}, {}, "sample_admin")


def test_owner_creator_and_attribution_cannot_authorize_another_user(accounts, monkeypatch):
    """Lineage 的创建者或发起人身份都不能把运行身份设为其他用户"""
    monkeypatch.setattr(identity.permission_system, "permitted_ids", lambda actor, instruction, parameter, ids: set())
    before = {"created_by": "sample_admin"}
    with operation_identity_scope(OperationIdentity.for_actor(RESIDENT, initiated_by="sample_admin")):
        for caller in ("alice", RESIDENT):
            with pytest.raises(PermissionError, match="UpdateUser"):
                identity.requested({"run_as_principal": USER}, before, caller)
    assert identity.requested({"run_as_principal": USER}, {}, "sample_admin") == USER


def test_active_user_is_required_even_when_no_instruction_is_protected(accounts, monkeypatch):
    """run_as_principal 指向停用或不存在的用户时，管理员也不能设置"""
    monkeypatch.setattr(identity.permission_system, "permitted_ids", lambda actor, instruction, parameter, ids: set(ids))
    for user in ("disabled", "missing"):
        with pytest.raises(PermissionError, match="unavailable or inactive"):
            identity.requested({"run_as_principal": {"kind": "user", "id": user}}, {}, RESIDENT)


def test_configuration_uses_the_editors_own_administrator_entry(accounts, permissions):
    """设置运行身份按编辑者自己当前能否对该用户执行 UpdateUser 判断，撤销后即被拒绝"""
    permissions.administrator(RESIDENT)
    assert identity.requested({"run_as_principal": USER}, {}, RESIDENT) == USER
    permissions.administrator(RESIDENT, False)
    with pytest.raises(PermissionError, match="UpdateUser"):
        identity.requested({"run_as_principal": USER}, {}, RESIDENT)


def test_runtime_rechecks_user_without_ambient_authority_or_owner_fallback(accounts):
    """运行时重新检查运行用户，不借外层身份或 owner；用户停用不妨碍修图或清除设置"""
    lineage = {"run_as_principal": USER, "created_by": "alice"}
    with operation_identity_scope(OperationIdentity.for_actor("alice")):
        assert identity.runtime_actor(lineage) == "sample_admin"
        accounts["sample_admin"] = False
        with pytest.raises(PermissionError, match="inactive"):
            identity.runtime_actor(lineage)
        assert identity.runtime_actor({"created_by": "sample_admin"}) == "portal"
    # Disabling an account must not prevent unrelated graph repairs or clearing it.
    assert identity.requested({"title": "repair"}, lineage, "alice") == USER
    assert identity.requested({"run_as_principal": None}, lineage, "alice") is None


def test_isa_metadata_migration_preserves_runtime_and_survives_graph_edit(primitive_store, accounts):
    """经 UpdateLineage 设置 run_as_principal 不改动运行时状态，后续图编辑仍保留该设置"""
    current = lineage_domain.create_lineage({"title": "automated", "program": {"components": [
        {"id": "event_source_clock", "kind": "event_source", "config": {"event_type": "manual.triggered"}}
    ], "wires": []}}, "sample_admin")
    lineage_id = current["lineage_id"]
    source_id = current["program"]["components"][0]["id"]
    lineage_program_runtime.claim_event_occurrence(lineage_id, source_id, "seen", max_fires=3)
    before = deepcopy(lineage_program_runtime.read_runtime(lineage_id))
    args = {"lineage_id": lineage_id, "changes": {"run_as_principal": USER}}
    result = definition.update_lineage(SimpleNamespace(caller="sample_admin", argument=lambda name, default=None: args.get(name, default)))
    assert result["lineage"]["run_as_principal"] == USER
    assert lineage_program_runtime.read_runtime(lineage_id) == before
    saved = lineage_domain._read_lineage_state(lineage_id)
    assert saved["revision"] == current["revision"] + 1
    edited = lineage_domain.update_lineage(lineage_id, {**saved, "title": "edited"}, "sample_admin")
    assert edited["run_as_principal"] == USER
    assert lineage_program_runtime.read_runtime(lineage_id) == before
    lineage_domain.update_lineage_metadata(lineage_id, {"run_as_principal": None}, "sample_admin")
    assert "run_as_principal" not in lineage_domain._read_lineage_state(lineage_id)


def test_concurrent_graph_merge_never_uses_creator_to_reauthorize_identity(primitive_store, accounts, monkeypatch):
    """编辑者可把运行身份设为自己，并发合并保留改名且不借创建者重新授权"""
    original = lineage_domain.create_lineage({"title": "created by someone else"}, "sample_admin")
    lineage_id = original["lineage_id"]
    # A current editor may select their own active User, regardless of creator.
    # A concurrent rename must survive, and must not re-authorize as sample_admin.
    calls = []
    def no_admin(actor, instruction, parameter, ids):
        calls.append(actor)
        return set()
    monkeypatch.setattr(identity.permission_system, "permitted_ids", no_admin)
    lineage_domain.update_lineage_metadata(lineage_id, {"title": "concurrent name"}, "sample_admin")
    result = lineage_domain.update_lineage(lineage_id, {
        **original, "run_as_principal": {"kind": "user", "id": "alice"}
    }, "alice", previous=original)
    assert result["title"] == "concurrent name"
    assert result["run_as_principal"] == {"kind": "user", "id": "alice"}
    assert calls == []
