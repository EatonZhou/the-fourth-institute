from __future__ import annotations

from management_portal import article_registry

from unittest import mock

import pytest

from management_portal import resource_index_backend as backend
from management_portal import resource_index_records, resource_index_registries
from management_portal.resources import ResourceService
from management_portal.resources.registry_store import registry_change_key


@pytest.mark.parametrize(
    ("resource_type", "adapter_class", "record"),
    [
        ("repo", resource_index_registries.ResourceRegistryAdapter, {
            "resource_id": "repo_0123456789ab", "name": "Immediate repo",
            "project_ids": [], "checkouts": [], "remotes": [],
        }),
        ("article", resource_index_registries.ResourceRegistryAdapter, {
            "resource_id": "article_0123456789ab", "title": "Immediate article",
            "project_ids": [], "roots": [],
        }),
        ("device", resource_index_registries.DeviceRegistryAdapter, {
            "resource_id": "device_0123456789ab", "name": "Immediate device",
            "device_kind": "device", "lifecycle": "active", "network": {},
            "access_methods": {}, "monitor": {},
        }),
        ("project", resource_index_registries.ProjectRegistryAdapter, {
            "resource_id": "ImmediateProject", "name": "Immediate Project",
            "status": "active", "tags": [],
        }),
        ("workbench", resource_index_registries.WorkbenchAdapter, {
            "resource_id": "workbench_0123456789ab", "title": "Immediate workbench",
            "created_by": "test-actor", "bindings": {},
        }),
    ],
)
def test_registration_is_queryable_before_return_without_adapter_scan(
    isolated_portal_case, resource_type, adapter_class, record,
):
    """仓库、文章、设备、Project 和工作台登记在返回前即可查询，无需适配器全量扫描"""
    with isolated_portal_case():
        adapter = adapter_class()
        store = backend.IndexStore()
        store.publish_snapshot(adapter.name, [], {})
        index = backend.ResourceIndexService(store=store, adapters=[adapter], start_worker=False)
        index.adapter_layer._on_projection_changed = None
        service = ResourceService(index_provider=index)
        def register(payload, **context):
            if resource_type == "workbench":
                from management_portal.workbench_projections.store import Store

                Store().create(record["resource_id"], {"title": record["title"]}, context["actor"])
            return dict(record)

        service.register_mutation_provider(
            resource_type, register=register,
        )
        with mock.patch.object(adapter, "scan", side_effect=AssertionError("full scan")):
            created = service.register(
                resource_type, {}, actor="test-actor", wait_for_index=True,
                index_timeout=0,
            )
        assert "index_projection" not in created
        assert store.internal_get(record["resource_id"])["resource_id"] == record["resource_id"]
        assert any(item["resource_id"] == record["resource_id"] for item in
                   service.query(actor="test-actor", resource_types=[resource_type], q="Immediate")["resources"])
        assert adapter.name not in index.adapter_layer._pending


def test_older_full_and_incremental_scans_cannot_erase_direct_projection(
    isolated_portal_case,
):
    """登记前开始的全量或增量扫描不会抹掉登记时直接写入的索引记录"""
    with isolated_portal_case():
        store = backend.IndexStore()
        adapter = resource_index_registries.ResourceRegistryAdapter()
        store.publish_snapshot(adapter.name, [], {})
        scan_generation = store.generation()
        summary = adapter.project_record("repo", {
            "resource_id": "repo_0123456789ab", "name": "Immediate repo",
            "project_ids": [], "checkouts": [], "remotes": [],
        })
        store.apply(resource_index_records.ChangeSet(adapter.name, upserts=(summary,)), direct=True)
        store.publish_snapshot(adapter.name, [], {}, scan_started_generation=scan_generation)
        assert store.internal_get(summary["resource_id"])["title"] == "Immediate repo"
        store.apply(
            resource_index_records.ChangeSet(adapter.name, deletes=(summary["resource_id"],)),
            scan_started_generation=scan_generation,
        )
        assert store.internal_get(summary["resource_id"])["title"] == "Immediate repo"


def test_project_registry_document_change_projects_only_changed_projects():
    """Project 登记文档变更后只投影新增或变化的 Project"""
    adapter = resource_index_registries.ProjectRegistryAdapter()
    before = adapter.project_record("project", {
        "id": "Existing", "status": "active", "tags": [],
    })
    with mock.patch.object(resource_index_registries.project_provider, "load_projects_payload", return_value={
        "projects": [
            {"id": "Existing", "status": "active", "tags": []},
            {"id": "New", "status": "active", "tags": []},
        ],
    }):
        changes = adapter.scan_changes(
            [resource_index_registries.project_registry.CHANGE_PATH], [before], {},
        )
    assert [record["resource_id"] for record in changes.upserts] == ["New"]
    assert changes.deletes == ()


@pytest.mark.parametrize("resource_type", ["repo", "article", "device"])
def test_registry_change_reads_only_changed_document(resource_type):
    """仓库、文章或设备登记变更时只读取发生变化的那份文档"""
    adapter = resource_index_registries.DeviceRegistryAdapter() if resource_type == "device" else resource_index_registries.ResourceRegistryAdapter()
    resource_id = f"{resource_type}_0123456789ab"
    document = {
        "repo": {"id": resource_id, "name": "Immediate repo", "project_ids": [], "checkouts": [], "remotes": []},
        "article": {"id": resource_id, "title": "Immediate article", "project_ids": [], "roots": []},
        "device": {"device_id": resource_id, "name": "Immediate device", "device_kind": "device",
                   "network": {}, "access_methods": {}, "monitor": {}},
    }[resource_type]
    collection = {
        "repo": resource_index_registries.resource_identity_backend.REPO_COLLECTION,
        "article": article_registry.ARTICLE_COLLECTION,
        "device": resource_index_registries.device_identity_backend.DEVICE_COLLECTION,
    }[resource_type]
    registry = mock.Mock()
    registry.get.return_value = dict(document)
    validator = {
        "repo": (resource_index_registries.resource_identity_backend, "_validated_repo"),
        "article": (article_registry, "_validated_article"),
        "device": (resource_index_registries.device_identity_backend, "_normalize_record"),
    }[resource_type]
    with mock.patch.object(resource_index_registries, "get_registry_store", return_value=registry), \
         mock.patch.object(*validator, return_value=dict(document)), \
         mock.patch.object(resource_index_registries.device_identity_backend, "load_device_observations", return_value={}):
        changes = adapter.scan_changes([registry_change_key(collection, resource_id)], [], {})
    registry.get.assert_called_once_with(collection, resource_id)
    assert [record["resource_id"] for record in changes.upserts] == [resource_id]
