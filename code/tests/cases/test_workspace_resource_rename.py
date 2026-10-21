"""Workspace metadata CAS through the formal Resource ISA and domain transaction.

:module: resource.service
:importance: P2
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from types import SimpleNamespace

import pytest

from management_common.portal_database import portal_database
from management_portal import permission_system
from management_portal.hatg.contracts import InstructionCall, InstructionContext, InstructionError
from management_portal.hatg.kernel import InstructionKernel
from management_portal.hatg.store import ExecutionStore
from management_portal.resources import isa
from management_portal.resources.providers import install_mutation_providers, install_resolvers
from management_portal.resources.service import ResourceService
from management_portal.resources.workspace_store import get_workspace_store


WID = "workspace_123456789abc"


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    store = get_workspace_store()
    store.ensure(WID, str(tmp_path), title="Before")
    # No discovery record is available: all identity reads must use the owner.
    index = SimpleNamespace(supports_type=lambda kind: True, type_counts=dict)
    service = ResourceService(index_provider=index, resolver_loader=install_resolvers,
                              mutation_provider_loader=install_mutation_providers)
    monkeypatch.setattr(service, "notify_source_changed", lambda **kw: {"targets": [], "finished": False})
    monkeypatch.setattr(isa, "_service", lambda: service)
    monkeypatch.setattr(permission_system, "configured_mode", lambda: permission_system.OPEN)
    kernel = InstructionKernel(isa.binding(), store=ExecutionStore(portal_database(schema="rename_isa")))

    def call(instruction, arguments, *, actor="alice", **controls):
        return kernel.execute(InstructionCall.from_payload({
            "instruction": instruction, "arguments": arguments, **controls,
        }), InstructionContext(caller=actor, entry="web"))

    def resolve(actor="alice"):
        return call("ResolveResource", {"address": WID}, actor=actor).raise_for_error()["resource"]

    def update(changes, *, actor="alice", wid=WID, **controls):
        return call("UpdateResource", {"resource_id": wid, "changes": changes}, actor=actor, **controls)

    return SimpleNamespace(store=store, service=service, call=call, resolve=resolve, update=update, root=tmp_path)


def assert_error(result, code):
    assert result.status == "failed", result.to_payload()
    assert result.error["code"] == code, result.to_payload()
    assert result.error["commit_state"] == "not_committed"
    with pytest.raises(InstructionError) as failure:
        result.raise_for_error()
    assert failure.value.http_status == {
        "instruction.invalid_arguments": 400, "instruction.forbidden": 403,
        "instruction.not_found": 404, "instruction.version_conflict": 409,
    }[code]


def test_formal_rename_round_trip_capability_and_stale_version(workspace):
    """正式读取、更新和能力声明同源，索引未确认不影响提交真值，旧版本报409。"""
    before = workspace.resolve()
    token = before["version"]
    assert token == before["attributes"]["updated_at"]
    capabilities = workspace.call("GetResourceType", {"resource_type": "workspace"}).raise_for_error()
    assert capabilities["updatable"] is True
    assert capabilities["registerable"] is False
    assert capabilities["unregisterable"] is False
    assert workspace.service.capabilities("workspace")["content_updatable"] is False
    written = workspace.update({"title": "  新名称  ", "expected_version": token}).raise_for_error()
    assert written["resource"]["title"] == "新名称"
    assert written["version"] == written["resource"]["version"] != token
    assert written["resource"]["index_projection"]["status"] == "unconfirmed"
    current = workspace.resolve()
    assert current["version"] == written["version"] == current["attributes"]["updated_at"]
    assert current["title"] == "新名称"
    assert current["attributes"]["path"] == before["attributes"]["path"]
    conflict = workspace.update({"title": "Late", "expected_version": token})
    assert_error(conflict, "instruction.version_conflict")
    assert conflict.error["details"] == {"expected_version": token, "current_version": written["version"]}
    same = workspace.update({"title": "新名称"}, expected_version={WID: written["version"]}).raise_for_error()
    assert same["version"] == written["version"]
    assert workspace.store.rows([WID])[WID]["title"] == "新名称"


def test_workspace_description_is_metadata_with_same_version_guard(workspace):
    before = workspace.resolve()
    assert before["summary"] == ""
    written = workspace.update({"summary": "  Shared notes  ", "expected_version": before["version"]}).raise_for_error()
    assert written["resource"]["title"] == "Before"
    assert written["resource"]["summary"] == "Shared notes"
    assert workspace.resolve()["summary"] == "Shared notes"
    assert_error(workspace.update({"title": "Late", "expected_version": before["version"]}), "instruction.version_conflict")
    current = workspace.resolve()
    cleared = workspace.update({"title": "New", "summary": "", "expected_version": current["version"]}).raise_for_error()
    assert (cleared["resource"]["title"], cleared["resource"]["summary"]) == ("New", "")
    assert workspace.store.rows([WID])[WID]["summary"] == ""


def test_workspace_resolution_reports_physical_directory_separately(workspace):
    resolved = workspace.call("ResolveResource", {"address": WID}).raise_for_error()
    assert resolved["target"]["exists"] is True
    assert resolved["target"]["directory_status"] == "available"
    missing = workspace.root / "not-created"
    workspace.store.ensure(WID, str(missing), move=True)
    resolved = workspace.call("ResolveResource", {"address": WID}).raise_for_error()
    assert resolved["target"]["exists"] is True
    assert resolved["target"]["directory_status"] == "missing"
    missing.write_text("not a directory", encoding="utf-8")
    resolved = workspace.call("ResolveResource", {"address": WID}).raise_for_error()
    assert resolved["target"]["directory_status"] == "not_directory"


@pytest.mark.parametrize("changes", [
    {}, {"title": ""}, {"title": " \t\n"}, {"title": None}, {"title": 123}, {"title": "bad\x00name"},
    {"title": "Good", "path": "/other"}, {"title": "Good", "resource_id": WID},
    {"title": "Good", "workspace_id": WID}, {"title": "Good", "owned_workspace_id": WID},
    {"title": "Good", "bindings": []}, {"title": "Good", "attributes": {"path": "/other"}},
    {"title": "Good", "expected_version": ""}, {"title": "Good", "expected_version": 12},
    {"summary": None}, {"summary": "bad\x00text"}, {"summary": "x" * 2001},
])
def test_invalid_name_and_readonly_fields_leave_row_unchanged(workspace, changes):
    """非法名称、版本和只读字段经正式入口报400，整行不变。"""
    before = workspace.store.rows([WID])
    payload = {"expected_version": before[WID]["updated_at"], **changes}
    assert_error(workspace.update(payload), "instruction.invalid_arguments")
    assert workspace.store.rows([WID]) == before


def test_version_required_and_missing_deleted_resources(workspace):
    """缺少版本报400，缺失或已删除Workspace报404，不能借改名恢复。"""
    token = workspace.store.rows([WID])[WID]["updated_at"]
    assert_error(workspace.update({"title": "After"}), "instruction.invalid_arguments")
    payload = {"title": "After", "expected_version": token}
    assert_error(workspace.update(payload, wid="workspace_ffffffffffff"), "instruction.not_found")
    with workspace.store.database.transaction() as conn:
        workspace.store.mark_deleted(WID, deleted_at=token, deleted_by="alice", connection=conn)
    assert_error(workspace.update(payload), "instruction.not_found")
    assert_error(workspace.call("ResolveResource", {"address": WID}), "instruction.not_found")
    assert workspace.store.rows([WID], include_deleted=True)[WID]["title"] == "Before"


def test_same_token_competition_has_one_winner(workspace):
    """两个正式更新竞争同一版本时仅一个提交，另一个得到真实当前版本。"""
    token = workspace.store.rows([WID])[WID]["updated_at"]
    ready = Barrier(2)
    def rename(title):
        ready.wait()
        return workspace.update({"title": title, "expected_version": token})
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(rename, ["One", "Two"]))
    success, = [result for result in results if result.status == "committed"]
    failure, = [result for result in results if result.status == "failed"]
    assert_error(failure, "instruction.version_conflict")
    assert failure.error["details"]["current_version"] == success.result["version"]
    assert workspace.resolve()["title"] == success.result["resource"]["title"]


@pytest.mark.parametrize("operation", ["move", "delete"])
def test_rename_waits_for_current_row_transaction(workspace, operation):
    """移动或删除持锁时，名称CAS等待事务提交后检验最新行。"""
    token = workspace.store.rows([WID])[WID]["updated_at"]
    entered = Event()
    def rename():
        entered.set()
        return workspace.update({"title": "Late", "expected_version": token})
    with ThreadPoolExecutor(max_workers=1) as pool:
        with workspace.store.database.transaction() as conn:
            if operation == "move":
                workspace.store.ensure(WID, str(workspace.root / "moved"), move=True, connection=conn)
            else:
                workspace.store.mark_deleted(WID, deleted_at=token, deleted_by="alice", connection=conn)
            future = pool.submit(rename)
            assert entered.wait(2)
            assert not future.done()
        result = future.result(timeout=10)
    assert_error(result, "instruction.version_conflict" if operation == "move" else "instruction.not_found")
    assert workspace.store.rows([WID], include_deleted=True)[WID]["title"] == "Before"


def test_clock_rollback_cannot_reuse_version(workspace, monkeypatch):
    """系统时间回拨时名称和位置变更仍产生不同版本，旧保存不重置名称。"""
    from management_portal.resources import workspace_store
    token = workspace.store.rows([WID])[WID]["updated_at"]
    monkeypatch.setattr(workspace_store, "now", lambda: "2000-01-01T00:00:00Z")
    updated = workspace.update({"title": "After", "expected_version": token}).raise_for_error()
    assert updated["version"] > token
    workspace.store.ensure(WID, str(workspace.root), title="Stale save")
    assert workspace.resolve()["title"] == "After"
    workspace.store.ensure(WID, str(workspace.root / "moved"), move=True)
    assert workspace.resolve()["version"] > updated["version"]


def test_off_mode_real_actor_and_personal_ama(workspace, monkeypatch):
    """权限关闭仍按真实actor保留个人AMA隐私，公共Workspace向已登录actor开放。"""
    from management_portal.society_service import lineage_access_policy
    token = workspace.store.rows([WID])[WID]["updated_at"]
    with pytest.raises(InstructionError) as anonymous:
        workspace.update({"title": "No", "expected_version": token}, actor="")
    assert anonymous.value.code == "instruction.forbidden"
    with pytest.raises(PermissionError):
        workspace.service.update(WID, {"title": "No", "expected_version": token}, actor="")
    monkeypatch.setattr(lineage_access_policy, "personal_folders", lambda: [
        (str(workspace.root), "lineage_123456789abc", "alice"),
    ])
    assert_error(workspace.update({"title": "No", "expected_version": token}, actor="bob"), "instruction.not_found")
    assert_error(workspace.call("ResolveResource", {"address": WID}, actor="bob"), "instruction.not_found")
    result = workspace.update({"title": "Owner", "expected_version": token}, actor="alice").raise_for_error()
    assert result["resource"]["title"] == "Owner"


def test_permission_port_enforces_update_for_direct_service_and_isa(workspace, monkeypatch):
    """真实PAR默认方案区分查看和更新权限，直接ResourceService也不能绕过。"""
    from tests.cases.permission_test_support import enable, Fact
    permissions = enable(monkeypatch)
    permissions.place(Fact("workspace", WID))
    permissions.account("alice")
    permissions.grant("alice", WID, "@viewer")
    token = workspace.store.rows([WID])[WID]["updated_at"]
    payload = {"title": "After", "expected_version": token}
    assert_error(workspace.update(payload), "instruction.forbidden")
    with pytest.raises(PermissionError):
        workspace.service.update(WID, payload, actor="alice")
    permissions.grant("alice", WID, "@contributor")
    assert workspace.update(payload).raise_for_error()["resource"]["title"] == "After"


def test_permission_uses_locked_location_before_reporting_conflict(workspace, monkeypatch):
    """Workspace移入他人的AMA后，旧版本更新先隐藏资源，不泄露新版本。"""
    from management_portal.society_service import lineage_access_policy
    token = workspace.resolve()["version"]
    private = workspace.root / "private"
    monkeypatch.setattr(lineage_access_policy, "personal_folders", lambda: [
        (str(private), "lineage_123456789abc", "bob"),
    ])
    workspace.store.ensure(WID, str(private), move=True)
    result = workspace.update({"title": "Late", "expected_version": token})
    assert_error(result, "instruction.not_found")
    assert "current_version" not in result.error["details"]
    assert workspace.store.rows([WID])[WID]["title"] == "Before"
