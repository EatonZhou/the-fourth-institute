"""Workspace naming propagates through Resident reads without changing bindings.

:module: resident.workspace-memory
:importance: P2
"""
from copy import deepcopy
from types import SimpleNamespace

from management_common.portal_database import portal_database
from management_portal import permission_system
from management_portal.hatg.contracts import InstructionCall, InstructionContext
from management_portal.hatg.kernel import InstructionKernel
from management_portal.hatg.store import ExecutionStore
from management_portal.society_service import isa as lineage_isa
from management_portal.society_service import resident_catalog, resident_domain, resident_workspace_domain
from management_portal.resources import isa as resource_isa
from management_portal.resources.providers import install_mutation_providers, install_resolvers
from management_portal.resources.service import ResourceService
from management_portal.resources.workspace_store import get_workspace_store
from tests.cases.test_resident_folder_creation_contract import _new_session_payload
from tests.cases.test_resident_workspace_binding_identity import future_creation_store, resident_creation_store


def test_rename_owned_and_bound_workspaces_read_back_through_get_resident(future_creation_store, tmp_path, monkeypatch):
    """正式UpdateResource后GetResident的自有与额外目录名称立即更新，路径/绑定不变。"""
    monkeypatch.setattr(permission_system, "configured_mode", lambda: permission_system.OPEN)
    extra = tmp_path / "extra-basename"
    extra.mkdir()
    payload = _new_session_payload("Workspace rename projection")
    payload["workspace_paths"] = [str(extra)]
    original = resident_domain.register_resident(payload, "sample_admin")
    rid = original["resident_id"]
    catalog = resident_catalog.get_catalog()
    before = catalog.get(rid)
    service = ResourceService(index_provider=SimpleNamespace(supports_type=lambda kind: True),
                              resolver_loader=install_resolvers, mutation_provider_loader=install_mutation_providers)
    monkeypatch.setattr(service, "notify_source_changed", lambda **kw: {"targets": [], "finished": False})
    monkeypatch.setattr(resource_isa, "_service", lambda: service)
    resource = InstructionKernel(resource_isa.binding(), store=ExecutionStore(portal_database(schema="rename_resource")))
    lineage = InstructionKernel(lineage_isa.binding(), store=ExecutionStore(portal_database(schema="rename_lineage")))

    def call(kernel, name, arguments):
        return kernel.execute(InstructionCall.from_payload({"instruction": name, "arguments": arguments}),
                              InstructionContext(caller="sample_admin", entry="web")).raise_for_error()

    targets = {before["owned_workspace_id"]: "我的开发目录"}
    targets.update({binding["workspace_id"]: "共享源码" for binding in before["workspace_bindings"]})
    for wid, title in targets.items():
        record = call(resource, "ResolveResource", {"address": wid})["resource"]
        result = call(resource, "UpdateResource", {
            "resource_id": wid, "changes": {"title": title, "expected_version": record["version"]},
        })
        assert result["resource"]["title"] == title
    result = call(lineage, "GetResident", {"resident_id": rid})["resident"]
    roots = result["workspaces"]
    assert {root["workspace_id"]: root["title"] for root in roots} == targets
    assert {root["workspace_id"]: root["label"] for root in roots} == targets
    after = catalog.get(rid)
    for key in ("owned_workspace_id", "owned_workspace", "workspace_bindings", "workspace_paths", "default_cwd"):
        assert after[key] == before[key]


def test_workspace_root_names_batch_current_rows_without_mutating_record(tmp_path, monkeypatch):
    """目录根投影一次批量读取当前名称，不缓存名称、不改写Resident绑定记录。"""
    store = get_workspace_store()
    own, extra = "workspace_111111111111", "workspace_222222222222"
    store.ensure(own, str(tmp_path / "own"), title="Owned")
    store.ensure(extra, str(tmp_path / "extra"), title="Extra")
    record = {"owned_workspace_id": own, "owned_workspace": {"workspace_id": own, "path": str(tmp_path / "own")},
              "workspace_bindings": [{"workspace_id": extra, "path": str(tmp_path / "extra"), "purpose": "code"}]}
    before = deepcopy(record)
    read = store.rows
    calls = []
    def rows(ids, **kwargs):
        selected = list(ids)
        calls.append(selected)
        return read(selected, **kwargs)
    monkeypatch.setattr(store, "rows", rows)
    projected = resident_workspace_domain.resident_workspace_roots(record)
    assert calls == [[own, extra]]
    assert [root["title"] for root in projected] == ["Owned", "Extra"]
    current = read([extra])[extra]
    store.rename(extra, "Renamed", expected_version=current["updated_at"], authorize=lambda descriptor: None)
    projected = resident_workspace_domain.resident_workspace_roots(record)
    assert projected[1]["title"] == projected[1]["label"] == "Renamed"
    assert calls == [[own, extra], [own, extra]]
    assert record == before


def test_initial_workspace_projection_does_not_register_missing_identity(tmp_path):
    """创建过程尚无Workspace行时保留目录名展示，投影不登记或复活身份。"""
    wid = "workspace_333333333333"
    record = {"owned_workspace_id": wid, "owned_workspace": {"workspace_id": wid, "path": str(tmp_path / "workspace")},
              "workspace_bindings": []}
    root, = resident_workspace_domain.resident_workspace_roots(record)
    assert root["title"] == root["label"] == "workspace"
    assert get_workspace_store().rows([wid], include_deleted=True) == {}
