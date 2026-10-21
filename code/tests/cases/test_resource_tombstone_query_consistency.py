import socket
import subprocess
from types import SimpleNamespace

import pytest

from management_portal import resource_index_backend as backend
from management_portal.portal_access_control import PortalAccessStore
from management_portal.resources import ResourceService
from management_portal.resources.collaboration_registry import (
    COLLABORATION_DESCRIPTOR_SCHEMA, CollaborationDescriptorStore,
)
from management_portal.resources.providers import collaboration
from management_portal.resources.registration import ResourceRegistrationStore
from tests.cases.registry_support import write_projects


ACTOR = "fixture-query-owner"
OTHER = "fixture-query-outsider"
PROJECT = "FixtureQuery"
QUERY_SCOPE = "0303185000"  # ID fragment shared only by this module's subjects.
OTHER_PROJECT = "FixtureOther"
POINT = "decision_1b2bdf5c33d6#dbbefb8a37de798d"
DELETED_AT = "2026-09-12T14:00:00Z"


@pytest.fixture
def query_owner(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("query fixture forbids network and real Provider processes")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setenv("PORTAL_RESOURCE_SERVICE_PROCESS", "0")
    descriptors = CollaborationDescriptorStore()
    access = PortalAccessStore()
    write_projects(f"schema_version: 1\nprojects:\n  - id: {PROJECT}\n  - id: {OTHER_PROJECT}\n")
    access.upsert_user(ACTOR, actor=ACTOR)
    access.upsert_user(OTHER, actor=ACTOR)
    store = backend.IndexStore( portal_access=access)
    adapter = backend.CollaborationRegistryAdapter()
    store.publish_snapshot(adapter.name, [], {})
    index = backend.ResourceIndexService(store=store, adapters=[adapter], start_worker=False)
    index.adapter_layer._on_projection_changed = None
    resource = ResourceService(
        index_provider=index,  portal_access=access,
        collaboration_store=descriptors,
        registration_store=ResourceRegistrationStore(),
        resolver_loader=collaboration.install_resolver,
    )
    target = SimpleNamespace(root=tmp_path, descriptors=descriptors, access=access, store=store, index=index, resource=resource)
    # Keep unrelated canonical data present even in an otherwise clean database.
    publish(target, "resident_040418500001", "Unrelated Resident")
    yield target
    index.adapter_layer.stop()


def publish(target, resource_id, title, *, project_id=PROJECT):
    if resource_id.startswith(("resident_", "lineage_")):
        from tests.cases.canonical_domain_fixture import canonical_projection
        projection = canonical_projection(resource_id, title=title, project_id=project_id)
        target.resource.sync_collaboration_resource(projection["descriptor"])
        return
    if resource_id.startswith("workspace_"):
        # WorkspaceStore owns Workspace identity; Workspaces carry no Project.
        from management_portal.resources.workspace_store import get_workspace_store
        get_workspace_store().ensure(resource_id, str(target.root / resource_id), title=title)
        return
    target.resource.sync_collaboration_resource(
        {"schema": COLLABORATION_DESCRIPTOR_SCHEMA, "resource_id": resource_id, "title": title, "status": "active", "project_ids": [project_id]},
    )


def set_status(target, resource_id, status):
    if resource_id.startswith("workspace_") and status == "deleted":
        from management_portal.resources.workspace_store import get_workspace_store
        workspaces = get_workspace_store()
        with workspaces.database.transaction() as connection:
            workspaces.mark_deleted(resource_id, deleted_at=DELETED_AT, deleted_by=ACTOR, connection=connection)
        return
    descriptor = target.descriptors.get(resource_id)
    descriptor.update(status=status, deleted_at=DELETED_AT if status == "deleted" else "")
    target.descriptors.put(descriptor)


def ids(result):
    return [record["resource_id"] for record in result["resources"]]


@pytest.mark.parametrize("resource_type", ["lineage", "resident", "workspace"])
def test_authoritative_status_precedes_pagination_and_facet_filters(query_owner, resource_type, record_property):
    """已删除的 Lineage、Resident 或 Workspace 在分页和分面过滤前就被排除，索引快照不被改写"""
    target = query_owner
    deleted, first, second = [f"{resource_type}_03031850000{number}" for number in range(1, 4)]
    for resource_id, title in [(deleted, "A target"), (first, "B neighbor"), (second, "C neighbor")]:
        publish(target, resource_id, title)
    target.index.adapter_layer.refresh_now("collaboration", force=True)
    before = target.store.internal_get(deleted)
    set_status(target, deleted, "deleted")
    for offset, expected in [(0, [first]), (1, [second]), (2, [])]:
        result = target.resource.query(q=QUERY_SCOPE, actor=ACTOR, resource_types=[resource_type], facets=["collaboration", resource_type], limit=1, offset=offset, include_attributes=True)
        assert result["total"] == 2
        assert ids(result) == expected
        assert all(record["attributes"]["status"] == "active" for record in result["resources"])
    assert target.resource.query(q=f"{QUERY_SCOPE}01", actor=ACTOR, resource_types=[resource_type], facets=["collaboration"])["total"] == 0
    assert target.resource.query(q=QUERY_SCOPE, actor=ACTOR, facets=["knowledge"])["total"] == 0
    assert target.resource.query(q=QUERY_SCOPE, actor=ACTOR, resource_types=["unregistered-type"])["resources"] == []
    assert target.store.internal_get(deleted) == before
    assert target.resource.get(deleted, actor=ACTOR)["attributes"]["status"] == "deleted"
    record_property("point_id", POINT)
    record_property("assertion", "lifecycle overlay precedes facet filtering, total and every page; active neighbors stay stable; index snapshot is not rewritten")


@pytest.mark.parametrize("scoped", [True, False], ids=["project-access", "no-project-access"])
def test_explicit_deleted_query_keeps_authorization_and_project_filters(query_owner, scoped, record_property):
    """显式查询已删除资源时仍按状态、Project 和属性条件过滤"""
    target = query_owner
    deleted = "lineage_030318500011"
    hidden = "lineage_030318500012"
    publish(target, deleted, "Visible deleted")
    publish(target, hidden, "Other project deleted", project_id=OTHER_PROJECT)
    target.index.adapter_layer.refresh_now("collaboration", force=True)
    set_status(target, deleted, "deleted")
    set_status(target, hidden, "deleted")
    if not scoped:
        target.store._portal_access = None
        target.resource = ResourceService(index_provider=target.index,  collaboration_store=target.descriptors, registration_store=ResourceRegistrationStore(), resolver_loader=collaboration.install_resolver)
    # In open mode every valid User reads; the status, Project and attribute
    # filters still select exactly the requested deleted records.
    result = target.resource.query(q=QUERY_SCOPE, actor=ACTOR, statuses=["deleted"], include_attributes=True)
    assert sorted(ids(result)) == sorted([deleted, hidden])
    assert {record["attributes"]["deleted_at"] for record in result["resources"]} == {DELETED_AT}
    assert target.resource.query(q=QUERY_SCOPE, actor=ACTOR, statuses=["active"])["total"] == 0
    assert ids(target.resource.query(q=QUERY_SCOPE, actor=ACTOR, statuses=["deleted"], projects=[OTHER_PROJECT])) == [hidden]
    assert ids(target.resource.query(q=QUERY_SCOPE, actor=OTHER, statuses=["deleted"], projects=[PROJECT])) == [deleted]
    assert all("owner_ids" not in record["attributes"] for record in result["resources"])
    record_property("point_id", POINT)
    record_property("authorization_mode", "open mode with Project store" if scoped else "open mode, no Project store")


def test_restore_overrides_a_stale_deleted_index_without_refresh(query_owner, record_property):
    """资源恢复后无需刷新索引即可在默认查询中出现"""
    target = query_owner
    resource_id = "lineage_030318500021"
    publish(target, resource_id, "Restored")
    set_status(target, resource_id, "deleted")
    target.index.adapter_layer.refresh_now("collaboration", force=True)
    assert target.resource.query(q=QUERY_SCOPE, actor=ACTOR)["total"] == 0
    before = target.store.internal_get(resource_id)
    set_status(target, resource_id, "active")
    assert ids(target.resource.query(q=QUERY_SCOPE, actor=ACTOR)) == [resource_id]
    assert target.resource.query(q=QUERY_SCOPE, actor=ACTOR, statuses=["deleted"])["total"] == 0
    assert target.resource.get(resource_id, actor=ACTOR)["attributes"]["status"] == "active"
    assert target.store.internal_get(resource_id) == before
    record_property("point_id", POINT)
    record_property("assertion", "authoritative restoration is applied before default deleted exclusion; no query mutates the lagged index")


def test_index_only_and_other_resource_types_retain_existing_query_behavior(query_owner, record_property):
    """没有权威描述符的仅索引记录和其他资源类型保持原有查询过滤"""
    target = query_owner
    legacy = "resident_030318500031"
    concept = "concept_030318500032"
    records = [backend.resource_record(resource_id=resource_id, resource_type=resource_type, title=title, adapter="fixture", attributes={"project_ids": [PROJECT], "status": "active"}, facets=facets) for resource_id, resource_type, title, facets in [(legacy, "resident", "Legacy", ["collaboration"]), (concept, "concept", "Known non-collaboration", ["knowledge"])]]
    target.store.publish_snapshot("fixture", records, {})
    assert ids(target.resource.query(q=QUERY_SCOPE, actor=ACTOR, resource_types=["resident"])) == [legacy]
    assert ids(target.resource.query(q=QUERY_SCOPE, actor=ACTOR, facets=["knowledge"])) == [concept]
    assert target.resource.query(q=QUERY_SCOPE, actor=OTHER)["total"] == 2  # open mode: every valid User reads
    assert target.resource.query(q=QUERY_SCOPE, actor=ACTOR, resource_types=["not-a-resource-type"])["total"] == 0
    record_property("point_id", POINT)
    record_property("assertion", "missing authoritative descriptors preserve legacy projection; unrelated types and unknown requested types retain filtering semantics")
