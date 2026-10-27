"""Historical identities do not acquire runtime Workspace facts while being read.

:module: resident.workspace-memory
:design: decision_d19b9909a674, decision_d918ebebc062
"""
import copy
from types import SimpleNamespace

import pytest

from management_portal.society_service import (
    resident_catalog, resident_lineage, resident_record_domain,
    resident_registration_domain, resident_workspace_domain as domain,
    resident_workspace_storage as storage, resource_client,
)

RID = "resident_111111111111"


@pytest.mark.parametrize("relation", [[], [{
    "workspace_id": "workspace_111111111111", "root_id": "resident_workspace",
    "role": "resident", "access": "write", "scope": ".", "purpose": "",
    "managed": True, "default_cwd": True, "binding_origin": "",
}]])
def test_sparse_deleted_projection_does_not_require_workspace_or_lineage(relation):
    """已删除 Resident 的投影不带 Workspace 绑定、派生路径与 Lineage，保留标题

    :design-point:"""
    record = {"resident_id": RID, "deleted_at": "2026-10-08T00:00:00Z",
              "deleted_by": "sample_admin", "title": "Known title", "default_cwd": "/obsolete",
              "workspace_path": "/obsolete", "workspace_roots": [{"path": "/obsolete"}]}
    before = copy.deepcopy(record)
    projected = storage.project_records([record], {RID: relation}, {}, {})[0]
    assert projected["workspace_bindings"] == []
    assert projected["title"] == record["title"]
    assert not (set(storage.DERIVED_FIELDS) & set(projected))
    assert "lineage_id" not in projected
    assert resident_catalog.default_cwd_path(projected) == ""
    assert record == before


def test_deleted_lookup_does_not_finalize_registration(monkeypatch):
    """查找已删除的 Resident 不会补完注册

    按 ID 查找已删除的 Resident 时返回历史记录，不触发项目引导注册的补完；运行目标解析找不到它。
    """
    record = {"resident_id": RID, "deleted_at": "2026-10-08T00:00:00Z"}
    includes = []
    def get(rid, *, include_deleted=False):
        includes.append(include_deleted)
        if not include_deleted:
            raise KeyError(rid)
        return record
    monkeypatch.setattr(resident_catalog, "get_catalog", lambda: SimpleNamespace(get=get))
    monkeypatch.setattr(resident_lineage, "find", lambda rid: record)
    def unexpected(*args, **kwargs):
        raise AssertionError("Deleted identity lookup must not finalize registration")
    assert resident_record_domain.find_resident(RID) is record
    assert resident_record_domain.find_resident_canonical(RID) is None
    with pytest.raises(KeyError):
        resident_record_domain.resolve_resident_runtime_target(RID, "sample_admin", require_visible=True)
    assert includes == [False]


def test_catalog_historical_lookup_explicitly_forwards_include_deleted(tmp_path):
    """目录默认查不到已删除的 Resident，显式 include_deleted 才返回历史记录的副本

    :design-point:"""
    record = {"resident_id": RID, "deleted_at": "2026-10-08T00:00:00Z"}
    includes = []
    def read(rid, *, include_deleted=False):
        assert rid == RID
        includes.append(include_deleted)
        if not include_deleted:
            raise KeyError(rid)
        return record
    catalog = resident_catalog.ResidentCatalog(tmp_path, SimpleNamespace(resident_record=read))
    with pytest.raises(KeyError):
        catalog.get(RID)
    historical = catalog.get(RID, include_deleted=True)
    historical["deleted_at"] = "altered caller copy"
    assert record["deleted_at"] == "2026-10-08T00:00:00Z"
    assert includes == [False, True]


def test_deleted_workspace_helpers_do_not_create_bindings_or_directories(tmp_path):
    """对已删除的 Resident 调用 Workspace 辅助函数时报错，不建绑定也不建目录

    :design-point:"""
    folder = tmp_path / "must-not-create"
    record = {"resident_id": RID, "deleted_at": "2026-10-08T00:00:00Z",
              "workspace_path": str(folder)}
    before = copy.deepcopy(record)
    for helper in (domain.ensure_resident_workspace,
                   resident_record_domain.set_resident_owned_workspace_binding,
                   resident_record_domain.require_resident_owned_workspace_binding):
        with pytest.raises(KeyError):
            helper(record)
    assert not folder.exists()
    assert record == before


def test_deleted_descriptor_does_not_invent_unknown_lineage_or_backend():
    """已删除 Resident 的资源描述标为 deleted，不编造 Lineage、后端或创建时间

    :design-point:"""
    record = {"resident_id": RID, "deleted_at": "2026-10-08T00:00:00Z"}
    projection = resource_client._resident_resource_projection(record)
    descriptor = projection["descriptor"]
    assert descriptor["status"] == "deleted"
    assert descriptor["relations"] == []
    assert descriptor["attributes"]["backend"] == ""
    assert descriptor["created_at"] == ""
    assert set(projection) == {"descriptor"}
    with pytest.raises(ValueError):
        resource_client._resident_resource_projection({"resident_id": RID})
