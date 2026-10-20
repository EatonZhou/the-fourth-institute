from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from management_portal import society_service
from management_portal.resources.providers import collaboration
from management_portal.resources.providers import install_resolvers
from management_portal.resources.service import ResourceService
from management_portal.resources.registration import ResourceRegistrationStore
from management_portal.resources.collaboration_registry import (
    COLLABORATION_DESCRIPTOR_SCHEMA,
    CollaborationDescriptorStore,
)


def _register_workspace(resource_id, path, *, title="", deleted_at=""):
    """Workspace lifecycle lives only in WorkspaceStore; descriptors are its projection."""
    from management_portal.resources.workspace_store import get_workspace_store

    workspaces = get_workspace_store()
    workspaces.ensure(resource_id, str(path), title=title)
    if deleted_at:
        with workspaces.database.transaction() as connection:
            workspaces.mark_deleted(resource_id, deleted_at=deleted_at, deleted_by="test", connection=connection)


def _resource_service(tmp_path, records, *, descriptors=None):
    by_id = {record["resource_id"]: record for record in records}
    resource_types = frozenset(record["resource_type"] for record in records)
    index = SimpleNamespace(
        get_resource=lambda selected, **_context: by_id.get(selected),
        indexed_types=lambda: resource_types,
        has_indexed_type=lambda selected: selected in resource_types,
        supports_type=lambda selected: selected in resource_types,
    )
    return ResourceService(
        index_provider=index,

        collaboration_store=descriptors or CollaborationDescriptorStore(),
        registration_store=ResourceRegistrationStore(),
        resolver_loader=install_resolvers,
    )


def test_deleted_collaboration_reference_is_not_an_existing_resource(tmp_path, subtests, isolated_portal_case):
    """已删除协作资源引用按不存在处理

    :design: decision_4a9ba5dfada6
    :design-point:
    """
    for resource_type in ["resident", "lineage", "workspace"]:
        for tombstone in [{"status": "deleted"}, {"deleted_at": "2026-09-07T00:00:00Z"}]:
            with subtests.test(resource_type=resource_type, tombstone=tombstone), isolated_portal_case() as case_path:
                tmp_path = case_path
                resource_id = f"{resource_type}_0123456789ab"
                record = {
                    "resource_id": resource_id, "resource_type": resource_type,
                    "title": "Deleted fixture",
                    "attributes": tombstone,
                }
                descriptors = CollaborationDescriptorStore()
                deleted_at = tombstone.get("deleted_at", "2026-09-07T00:00:00Z")
                if resource_type == "workspace":
                    _register_workspace(resource_id, tmp_path / "workspace", title="Deleted fixture",
                                        deleted_at=deleted_at)
                else:
                    descriptors.put({"schema": COLLABORATION_DESCRIPTOR_SCHEMA,
                                     "resource_id": resource_id, "title": "Deleted fixture",
                                     "status": "deleted", "deleted_at": deleted_at})
                service = _resource_service(tmp_path, [record], descriptors=descriptors)
                assert service.resolve(resource_id, actor="collaboration-reader")["exists"] is False
                with pytest.raises(KeyError):
                    service.reference(resource_id, actor="collaboration-reader")
                with pytest.raises(KeyError):
                    service.reference(resource_id, actor="unauthorized")


def test_deleted_collaboration_reference_uses_current_lifecycle_before_index_refresh(tmp_path, subtests, isolated_portal_case):
    """索引尚未刷新时协作引用按当前生命周期过滤

    :design: decision_4a9ba5dfada6
    :design-point:
    """
    for resource_type in ["resident", "lineage", "workspace"]:
        with subtests.test(resource_type=resource_type), isolated_portal_case() as case_path:
            tmp_path = case_path
            resource_id = f"{resource_type}_0123456789ab"
            stale_record = {
                "resource_id": resource_id, "resource_type": resource_type,
                "title": "Lifecycle fixture",
                "attributes": {"status": "active"},
            }
            descriptors = CollaborationDescriptorStore()
            descriptor = {
                "schema": COLLABORATION_DESCRIPTOR_SCHEMA,
                "resource_id": resource_id, "title": "Lifecycle fixture", "status": "active",
            }
            if resource_type == "workspace":
                _register_workspace(resource_id, tmp_path / "workspace", title="Lifecycle fixture")
            else:
                descriptors.put_with_change(descriptor)
            service = _resource_service(tmp_path, [stale_record], descriptors=descriptors)
            assert service.query(q=resource_id, actor="collaboration-reader")["total"] == 1

            if resource_type == "workspace":
                _register_workspace(resource_id, tmp_path / "workspace", deleted_at="2026-09-07T00:00:00Z")
            else:
                descriptors.put_with_change({**descriptor, "status": "deleted", "deleted_at": "2026-09-07T00:00:00Z"})
            assert stale_record["attributes"]["status"] == "active"
            assert service.query(q=resource_id, actor="collaboration-reader")["total"] == 0
            assert service.resolve(resource_id, actor="collaboration-reader")["exists"] is False
            with pytest.raises(KeyError):
                service.reference(resource_id, actor="collaboration-reader")
            with pytest.raises(KeyError):
                service.reference(resource_id, actor="unauthorized")

            if resource_type == "workspace":
                continue  # A deleted Workspace cannot be restored; revival applies to resident/lineage only.
            descriptors.put_with_change(descriptor)
            assert service.query(q=resource_id, actor="collaboration-reader")["total"] == 1


@pytest.mark.parametrize("resource_type", ["resident", "lineage", "workspace"])
def test_registered_collaboration_reference_does_not_wait_for_discovery_index(tmp_path, resource_type):
    """已登记协作资源引用无需等待发现索引"""
    resource_id = f"{resource_type}_0123456789ab"
    descriptors = CollaborationDescriptorStore()
    descriptor = {
        "schema": COLLABORATION_DESCRIPTOR_SCHEMA,
        "resource_id": resource_id, "title": "New branch", "status": "active",
        "project_ids": ["AI员工"],
    }
    if resource_type == "workspace":
        _register_workspace(resource_id, tmp_path / "workspace", title="New branch")
    else:
        descriptors.put(descriptor)
    service = _resource_service(tmp_path, [], descriptors=descriptors)

    def index_unavailable():
        pytest.fail("Direct identity reads must not depend on the discovery index")
    service._index_provider.get_resource = lambda *_args, **_kwargs: index_unavailable()
    reference = service.reference(resource_id, actor="collaboration-reader")
    assert reference["title"] == "New branch"
    assert reference["attributes"]["status"] == "active"
    assert "owner_ids" not in reference["attributes"]
    assert service.get(resource_id, actor="collaboration-reader")["capabilities"]["read"] is True
    assert "search_terms" not in service.get(resource_id, actor="collaboration-reader")
    with pytest.raises(KeyError):
        service.reference(resource_id, actor="")

    if resource_type == "workspace":
        # Workspaces have no rename and cannot be restored: only the deletion step applies.
        _register_workspace(resource_id, tmp_path / "workspace", deleted_at="2026-10-06T00:00:00Z")
        assert service.resolve(resource_id, actor="collaboration-reader")["exists"] is False
        with pytest.raises(KeyError):
            service.reference(resource_id, actor="collaboration-reader")
        return
    descriptors.put({**descriptor, "title": "Renamed branch"})
    assert service.reference(resource_id, actor="collaboration-reader")["title"] == "Renamed branch"
    descriptors.put({**descriptor, "status": "deleted", "deleted_at": "2026-10-06T00:00:00Z"})
    assert service.resolve(resource_id, actor="collaboration-reader")["exists"] is False
    with pytest.raises(KeyError):
        service.reference(resource_id, actor="collaboration-reader")
    descriptors.put(descriptor)
    assert service.reference(resource_id, actor="collaboration-reader")["title"] == "New branch"


def test_collaboration_provider_exposes_only_identity_content(tmp_path, monkeypatch) -> None:
    """协作 Provider 暴露身份投影并读取 Workspace 文件

    Resident 与 Lineage 的正文是身份投影 JSON；Workspace 根没有字节正文，其下文件按地址读取。
    """
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "report.md").write_text("Verified report", encoding="utf-8")
    descriptors = CollaborationDescriptorStore()
    records = []
    for resource_type in ["resident", "lineage", "workspace"]:
        resource_id = f"{resource_type}_0123456789ab"
        if resource_type == "workspace":
            _register_workspace(resource_id, root, title="Content fixture")
        else:
            descriptors.put({
                "schema": COLLABORATION_DESCRIPTOR_SCHEMA,
                "resource_id": resource_id, "title": "Content fixture", "status": "active",
            })
        records.append({
            "resource_id": resource_id, "resource_type": resource_type,
            "title": "Content fixture", "attributes": {"status": "active"},
        })
    fake = SimpleNamespace(
        resident=SimpleNamespace(get_resident_resource_projection=lambda selected: {
            "schema": "resident.v1", "resource_id": selected,
        }),
        lineage=SimpleNamespace(get_lineage_resource_projection=lambda selected: {
            "schema": "lineage.v1", "resource_id": selected,
        }),
    )
    monkeypatch.setattr(society_service, "get_society_service", lambda: fake)

    service = _resource_service(tmp_path, records, descriptors=descriptors)
    for resource_type in ["resident", "lineage"]:
        resource_id = f"{resource_type}_0123456789ab"
        data, content_type, resolved = service.read_content(resource_id, actor="collaboration-reader")
        assert json.loads(data) == {"schema": f"{resource_type}.v1", "resource_id": resource_id}
        assert content_type == "application/json"
        assert resolved["content_schema"] == f"{resource_type}.v1"
    workspace_id = "workspace_0123456789ab"
    assert service.resolve(workspace_id, actor="collaboration-reader")["exists"] is True
    with pytest.raises(ValueError, match="root has no byte content"):
        service.read_content(workspace_id, actor="collaboration-reader")
    assert service.read_content(workspace_id + "/file/report.md", actor="collaboration-reader")[0] == b"Verified report"


def test_collaboration_content_delegates_to_society_service(monkeypatch, subtests, isolated_portal_case):
    """协作资源正文读取委托给 Lineage 服务"""
    for resource_type in ["resident", "lineage"]:
        with subtests.test(resource_type=resource_type), isolated_portal_case() as case_path:
            with monkeypatch.context() as monkeypatch:
                resource_id = f"{resource_type}_0123456789ab"
                fake = SimpleNamespace(
                    resident=SimpleNamespace(
                        get_resident_resource_projection=lambda selected: {
                            "schema": "resident.v1",
                            "resource_id": selected,
                        }
                    ),
                    lineage=SimpleNamespace(
                        get_lineage_resource_projection=lambda selected: {
                            "schema": "lineage.v1",
                            "resource_id": selected,
                        }
                    ),
                )
                monkeypatch.setattr(society_service, "get_society_service", lambda: fake)

                data, content_type, resolved = collaboration._read_content(
                    {"root_id": resource_id, "resource_type": resource_type},
                    max_bytes=4096,
                )

                assert json.loads(data) == {
                    "schema": f"{resource_type}.v1",
                    "resource_id": resource_id,
                }
                assert content_type == "application/json"
                assert resolved["content_schema"] == f"{resource_type}.v1"


def test_collaboration_content_honors_max_bytes(monkeypatch) -> None:
    """协作资源正文超出 max_bytes 时拒绝读取"""
    fake = SimpleNamespace(
        resident=SimpleNamespace(
            get_resident_resource_projection=lambda _selected: {
                "schema": "resident.v1",
                "summary": "x" * 100,
            }
        ),
        lineage=SimpleNamespace(),
    )
    monkeypatch.setattr(society_service, "get_society_service", lambda: fake)

    with pytest.raises(ValueError, match="exceeds max_bytes"):
        collaboration._read_content(
            {
                "root_id": "resident_0123456789ab",
                "resource_type": "resident",
            },
            max_bytes=20,
        )
