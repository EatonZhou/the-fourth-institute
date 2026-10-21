""":importance: P2"""
from tests.cases.agent_data_test_support import register_session
from pathlib import Path
from types import SimpleNamespace
import json
import socket
import subprocess

import pytest

from management_common.json_columns import json_value
from management_portal import resource_index_backend
from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.execution.session_manager import SessionManager
from management_portal.society_service import (
    domain_ports, execution_client, invariant_locks, lineage_domain,
    lineage_storage, resident_backend, resource_client,
)
from management_portal.society_service import resident_lifecycle_domain
from management_portal.society_service import resident_runtime_domain
from management_portal.society_service.resident_lifecycle import (
    LifecycleAttemptFailed, ResidentLifecycleOperations,
)
from management_portal.society_service.resident_lineage_host import ResidentLineageHost
from management_portal.society_service.state_store import LineageDataStore
from management_portal.portal_access_control import PortalAccessStore
from management_portal.resources import ResourceService
from management_portal.resources.collaboration_registry import CollaborationDescriptorStore
from management_portal.resources.providers import collaboration
from management_portal.resources.registration import ResourceRegistrationStore


ACTOR = "fixture-lifecycle-owner"
LINEAGE = "lineage_040404040401"
NEIGHBOR = "lineage_040404040402"
RESIDENT = "resident_040404040401"
DELETED_AT = "2026-09-12T12:30:00Z"


from management_portal.society_service import resident_session_backend_domain
from tests.cases.registry_support import write_projects

@pytest.fixture
def integration(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("isolated lifecycle test forbids network and real Provider processes")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(invariant_locks, "_ROOT_PROVIDER", lambda: tmp_path / "locks")
    monkeypatch.setenv("PORTAL_RESOURCE_SERVICE_PROCESS", "0")
    descriptors = CollaborationDescriptorStore()
    access = PortalAccessStore()
    write_projects("schema_version: 1\nprojects:\n  - id: FixtureLifecycle\n")
    access.upsert_user(ACTOR, actor=ACTOR)
    index_store = resource_index_backend.IndexStore( portal_access=access)
    adapter = resource_index_backend.CollaborationRegistryAdapter()
    index_store.publish_snapshot(adapter.name, [], {"fixture": True})
    index = resource_index_backend.ResourceIndexService(store=index_store, adapters=[adapter], start_worker=False)
    index.adapter_layer._on_projection_changed = None
    resource = ResourceService(index_provider=index,  portal_access=access, collaboration_store=descriptors, registration_store=ResourceRegistrationStore(), resolver_loader=collaboration.install_resolver)
    monkeypatch.setattr(resource_client, "get_resource_service", lambda: resource)
    monkeypatch.setattr(domain_ports.resource_port, "_implementation", resource_client)
    monkeypatch.setattr(domain_ports.resident_lineage_host_port, "_implementation", ResidentLineageHost())
    session_manager = SessionManager(agent_data=AgentDataStore())
    monkeypatch.setattr(execution_client, "sessions", lambda: session_manager)
    canonical_root = tmp_path / "registered-folders"
    folder = tmp_path / "actual-folder"
    owned = folder / "residents" / RESIDENT / "workspace"
    owned.mkdir(parents=True)
    marker = folder / "retained.txt"
    marker.write_text("original bytes survive release failure")
    canonical = lineage_storage.bind_lineage_folder(LINEAGE, folder, folder_root=canonical_root)
    neighbor_folder = tmp_path / "neighbor"
    neighbor_folder.mkdir()
    (neighbor_folder / "untouched.txt").write_text("neighbor original")
    neighbor_link = lineage_storage.bind_lineage_folder(NEIGHBOR, neighbor_folder, folder_root=canonical_root)
    monkeypatch.setattr(lineage_domain, "LINEAGE_FOLDER_ROOT", canonical_root)
    monkeypatch.setenv("PORTAL_LINEAGE_FOLDER_ROOT", str(canonical_root))
    definition = {"lineage_id": LINEAGE, "title": "Tombstone integration target", "nodes": [], "project_id": "FixtureLifecycle", "created_at": "2026-09-11T00:00:00Z", "updated_at": "2026-09-11T00:00:00Z"}
    neighbor_definition = {"lineage_id": NEIGHBOR, "title": "Neighbor retained", "nodes": [], "project_id": "FixtureLifecycle", "created_at": "2026-09-11T00:00:00Z", "updated_at": "2026-09-11T00:00:00Z"}
    state = LineageDataStore()
    state.put_lineage_definition(LINEAGE, definition, {})
    state.put_lineage_definition(NEIGHBOR, neighbor_definition, {})
    resource_client.sync_lineage(definition, updated_by=ACTOR)
    resource_client.sync_lineage(neighbor_definition, updated_by=ACTOR)
    index.adapter_layer.refresh_now("collaboration", force=True)
    record = {"resident_id": RESIDENT, "lineage_id": LINEAGE, "backend": "codex", "folder_schema": "lineage-folder-v3", "workspace_path": str(folder), "owned_workspace_id": "workspace_040404040401", "default_cwd": str(owned), "owned_workspace": {"workspace_id": "workspace_040404040401", "path": str(owned)}, "workspace_bindings": []}
    assert resident_runtime_domain._resident_working_directory(record) == owned
    register_session(record, "synthetic-release-session", backend_route_id="synthetic-route")
    operations = ResidentLifecycleOperations(LineageDataStore())
    target = SimpleNamespace(root=tmp_path, resource=resource, descriptors=descriptors, index=index, record=record, definition=definition, operations=operations, folder=folder, owned=owned, marker=marker, canonical=canonical, neighbor_folder=neighbor_folder, neighbor_link=neighbor_link, archive=tmp_path / "archived-folder")
    assert LINEAGE in query_ids(target)
    assert resource.reference(LINEAGE, actor=ACTOR)["resource_id"] == LINEAGE
    assert LINEAGE in query_ids(target, actor="fixture-stranger")
    assert resource.reference(LINEAGE, actor="fixture-stranger")["resource_id"] == LINEAGE
    yield target
    index.adapter_layer.stop()


def query_ids(target, *, actor=ACTOR):
    return [item["resource_id"] for item in target.resource.query(actor=actor, resource_types=["lineage"])["resources"]]


def deletion_step(target, kind):
    return {"kind": kind, "lineage_id": LINEAGE, "folder": str(target.folder), "definition": target.definition, "actor": ACTOR, "deleted_at": DELETED_AT}


def assert_deleted_reference(target):
    assert target.descriptors.get(LINEAGE)["status"] == "deleted"
    assert target.descriptors.get(LINEAGE)["deleted_at"] == DELETED_AT
    assert target.resource.resolve(LINEAGE, actor=ACTOR)["exists"] is False
    with pytest.raises(KeyError):
        target.resource.reference(LINEAGE, actor=ACTOR)
    assert target.resource.query(q=LINEAGE, actor=ACTOR)["resources"] == []
    assert target.resource.resolve(LINEAGE, actor="fixture-stranger")["exists"] is False


def pending_cursor(target, operation_id):
    with target.operations.store.database.read() as connection:
        row = connection.execute('SELECT payload_json,last_error FROM resident_lifecycle_operations WHERE operation_id=%s', (operation_id,)).fetchone()
    return json_value(row["payload_json"]), row["last_error"]


def recover_operations(target, operation_id):
    target.operations.store.database.write(lambda connection: connection.execute('UPDATE resident_lifecycle_operations SET available_at=to_timestamp(0) WHERE operation_id=%s', (operation_id,)))
    return ResidentLifecycleOperations(LineageDataStore())


def assert_archived_without_neighbor_damage(target):
    assert not target.folder.exists()
    assert not target.canonical.exists() and not target.canonical.is_symlink()
    assert (target.archive / "retained.txt").read_text() == "original bytes survive release failure"
    assert target.neighbor_link.is_symlink()
    assert (target.neighbor_folder / "untouched.txt").read_text() == "neighbor original"
    assert target.resource.reference(NEIGHBOR, actor=ACTOR)["resource_id"] == NEIGHBOR
    assert_deleted_reference(target)
    assert LINEAGE not in query_ids(target)


@pytest.mark.parametrize("failure_kind", ["unknown", "confirmed"])
def test_resource_tombstone_release_failure_recovery_and_archive(integration, monkeypatch, record_property, failure_kind):
    """资源墓碑先行发布且会话释放失败可恢复并安全归档"""
    target = integration
    releases = []

    def safe_release(**request):
        assert request["operation"] == "release"
        assert request["binding_id"] == resident_backend.execution_binding_id(target.record)
        assert Path(request["cwd"]) == target.owned
        assert_deleted_reference(target)
        assert target.marker.read_text() == "original bytes survive release failure"
        assert target.canonical.is_symlink()
        releases.append({"operation_id": request["operation_id"], "ordinary_query_ids": query_ids(target)})
        if len(releases) == 1:
            if failure_kind == "confirmed":
                raise LifecycleAttemptFailed("synthetic confirmed release failure")
            raise OSError("synthetic unknown release outcome")
        return {"status": "completed"}

    monkeypatch.setattr(resident_session_backend_domain, "_run_backend_session_operation", safe_release)
    steps = [deletion_step(target, "lineage_tombstone"), resident_lifecycle_domain._resident_release_step(target.record, ACTOR), deletion_step(target, "lineage_folder_unbind"), {"kind": "archive", "source": str(target.folder), "target": str(target.archive)}]
    operation_id = target.operations.enqueue(RESIDENT, steps)
    assert not target.operations.run(operation_id, resident_lifecycle_domain._apply_resident_lifecycle_step)
    cursor, error = pending_cursor(target, operation_id)
    assert cursor["next_step"] == 1
    assert "synthetic" in error
    assert resident_backend.session_record(target.record)
    assert not target.archive.exists()
    assert_deleted_reference(target)
    target.index.adapter_layer.refresh_now("collaboration", force=True)
    assert LINEAGE not in query_ids(target)
    recovered = recover_operations(target, operation_id)
    assert recovered.ready() == [operation_id]
    assert recovered.run(operation_id, resident_lifecycle_domain._apply_resident_lifecycle_step) == RESIDENT
    assert releases[1]["operation_id"] == releases[0]["operation_id"] + (":retry-1" if failure_kind == "confirmed" else "")
    assert not resident_backend.session_record(target.record)
    assert not recovered.pending(RESIDENT)
    assert not recovered.run(operation_id, lambda *args: pytest.fail("completed deletion replayed"))
    assert_archived_without_neighbor_damage(target)
    record_property("release_observations", json.dumps(releases))
    record_property("index_refresh_boundary", "real collaboration adapter catches up after first release failure, before recovery")


def test_tombstone_hides_ordinary_query_before_delayed_index_refresh(integration, record_property):
    """索引延迟刷新时权威墓碑仍立即隐藏普通查询结果"""
    target = integration
    resident_lifecycle_domain._apply_resident_lifecycle_step("fixture-query:0", deletion_step(target, "lineage_tombstone"))
    assert_deleted_reference(target)
    result = query_ids(target)
    record_property("descriptor", json.dumps(target.descriptors.get(LINEAGE)))
    record_property("ordinary_query_before_refresh", json.dumps(result))
    assert LINEAGE not in result, "authoritative tombstone still appears in ordinary ResourceService.query while index refresh is delayed"
