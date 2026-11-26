"""The knowledge-tag singleton replaces its two retired ISA instructions.

:module: isa.instructions
:design: decision_6591e7793116
"""
import copy
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from management_portal import permission_system, project_registry
from management_portal.resources import ResourceService
from management_portal.resources.errors import ResourceOperationError, ResourceVersionConflict
from management_portal.resources.providers import knowledge_tags
from management_portal.resources.registry_store import get_registry_store
from tests.cases.registry_support import put_registry_document

IDENTIFIER = "knowledge_tags_7d4e9a2c6f10"
TAGS = [
    {"id": "todo", "folder": "todo", "description": "待办"},
    {"id": "event", "folder": "event", "description": "事件", "param": "who, ddl"},
]
PROJECT = {"id": "Demo", "status": "active"}


@pytest.fixture
def legacy():
    raw = {"schema_version": 999, "status_vocab": ["active"], "tag_vocab": TAGS, "projects": [PROJECT]}
    put_registry_document(project_registry.COLLECTION, project_registry.DOCUMENT_ID, raw)
    project_registry.reset_cache_for_tests()
    return copy.deepcopy(raw)


@pytest.fixture
def service(monkeypatch):
    # Verify exact real actor and shared role at the provider seam. The
    # permission owner's tests exercise the concrete current-scheme engine.
    def allowed(actor, requirements):
        assert requirements in [[("@viewer", "@shared")], [("@admin", "@shared")]]
        return actor == "administrator" or (actor == "reader" and requirements[0][0] == "@viewer")
    monkeypatch.setattr(permission_system, "allows_roles", allowed, raising=False)
    result = ResourceService()
    knowledge_tags.install_resolver(result)
    knowledge_tags.install_mutation_provider(result)
    return result


def update(service, tags, version=None, actor="administrator"):
    payload = {"content": json.dumps(tags, ensure_ascii=False)}
    if version is not None:
        payload["expected_version"] = version
    return service._mutation_providers.provider_for("knowledge_tags").update(IDENTIFIER, payload, actor=actor)


def test_legacy_list_migrates_once_preserving_content_version_and_metadata(legacy):
    store = get_registry_store()
    assert project_registry.load() == legacy
    migrated = store.get(project_registry.COLLECTION, IDENTIFIER)
    assert migrated == {"resource_id": IDENTIFIER, "tags": TAGS, "version": project_registry.tags_version(TAGS)}
    assert store.get(project_registry.COLLECTION, "projects") == {k: v for k, v in legacy.items() if k != "tag_vocab"}
    revision = store.revision(project_registry.COLLECTION)
    project_registry.reset_cache_for_tests()
    assert project_registry.load() == legacy
    assert project_registry.knowledge_tags_configuration() == migrated
    assert store.revision(project_registry.COLLECTION) == revision


@pytest.mark.parametrize("corruption", ["tags", "version", "identity"])
def test_conflicting_existing_singleton_aborts_without_writes(legacy, corruption):
    current = {"resource_id": IDENTIFIER, "tags": TAGS, "version": project_registry.tags_version(TAGS)}
    if corruption == "tags":
        current.update(tags=[], version=project_registry.tags_version([]))
    elif corruption == "version":
        current["version"] = "wrong"
    else:
        current["resource_id"] = "knowledge_tags_000000000000"
    put_registry_document(project_registry.COLLECTION, IDENTIFIER, current)
    store = get_registry_store()
    before = store.snapshot(project_registry.COLLECTION)
    with pytest.raises(ResourceOperationError) as error:
        project_registry.load()
    assert error.value.code == "instruction.conflict"
    assert store.snapshot(project_registry.COLLECTION) == before


def test_read_and_replace_keep_project_on_same_configuration(legacy, service):
    data, mime, resolved = service.read_content(IDENTIFIER, actor="reader")
    assert mime == "application/json" and json.loads(data) == TAGS
    version = resolved["record"]["version"]
    result = update(service, [{"id": "risk", "description": "风险"}], version)
    assert result["version"] != version
    assert project_registry.tags() == [{"id": "risk", "folder": "risk", "description": "风险"}]
    assert project_registry.tag_folder_map() == {"risk": "risk"}
    assert project_registry.projects() == [PROJECT]
    assert project_registry.load()["status_vocab"] == ["active"]
    assert "tag_vocab" not in get_registry_store().get(project_registry.COLLECTION, "projects")


def test_required_and_stale_cas_leave_pending_migration_untouched(legacy, service):
    store = get_registry_store()
    before = store.snapshot(project_registry.COLLECTION)
    with pytest.raises(ValueError, match="expected_version"):
        update(service, [])
    with pytest.raises(ResourceVersionConflict) as error:
        update(service, [], "0" * 16)
    assert error.value.current_version == project_registry.tags_version(TAGS)
    assert store.snapshot(project_registry.COLLECTION) == before


@pytest.mark.parametrize("tags", [
    [{"id": "Todo", "description": "x"}],
    [{"id": "a", "description": "x"}, {"id": "a", "description": "y"}],
    [{"id": "a", "folder": "x", "description": "x"}, {"id": "b", "folder": "x", "description": "y"}],
    [{"id": "a", "folder": "../x", "description": "x"}],
    [{"id": "a"}], [{"id": "a", "description": "x", "color": "red"}], "todo",
])
def test_invalid_tags_rejected_without_write(legacy, service, tags):
    store = get_registry_store()
    before = store.snapshot(project_registry.COLLECTION)
    with pytest.raises(ValueError):
        update(service, tags, project_registry.tags_version(TAGS))
    assert store.snapshot(project_registry.COLLECTION) == before


def test_shared_roles_precede_read_write_and_migration(legacy, service):
    store = get_registry_store()
    before = store.snapshot(project_registry.COLLECTION)
    with pytest.raises(PermissionError):
        service.read_content(IDENTIFIER, actor="outsider")
    with pytest.raises(PermissionError):
        update(service, [], project_registry.tags_version(TAGS), actor="reader")
    assert store.snapshot(project_registry.COLLECTION) == before
    for address in (IDENTIFIER + "/file/tags.json", "knowledge_tags_000000000000"):
        with pytest.raises(KeyError):
            service.read_content(address, actor="administrator")


def test_concurrent_same_version_updates_cannot_both_commit(legacy):
    from threading import Barrier
    gate = Barrier(2)
    version = project_registry.tags_version(TAGS)
    def write(tag):
        gate.wait(timeout=5)
        try:
            return project_registry.replace_tags([{"id": tag, "description": tag}], expected_version=version)
        except project_registry.TagsChanged:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(write, ["alpha", "beta"]))
    assert sum(value is not None for value in outcomes) == 1
    assert project_registry.tags() == next(value for value in outcomes if value is not None)


def test_project_creation_uses_updated_singleton_folders(legacy, service, monkeypatch, tmp_path):
    from management_portal.resources.providers import project
    monkeypatch.setattr(project, "PROJECTS_ROOT", tmp_path / "Projects")
    update(service, [{"id": "finding", "folder": "findings", "description": "研究结论"}], project_registry.tags_version(TAGS))
    project.create_project({"id": "New", "project_type": "company"}, actor="resident_0123456789ab")
    assert (tmp_path / "Projects/New/knowledge_base/findings").is_dir()
    assert not (tmp_path / "Projects/New/knowledge_base/todo").exists()


def test_read_body_and_version_match_during_update(legacy, service):
    resolved = service.resolve(IDENTIFIER, actor="reader")
    update(service, [], project_registry.tags_version(TAGS))
    resolver = service._resolvers.resolver_for("knowledge_tags")
    data, _, actual = resolver.read_content(resolved, actor="reader")
    assert json.loads(data) == []
    assert actual["record"]["version"] == project_registry.tags_version([])


def test_retired_handlers_and_singleton_capabilities(service):
    from management_portal.society_service.isa import organization
    assert not {"GetKnowledgeTags", "UpdateKnowledgeTags"} & set(organization.HANDLERS)
    capabilities = service.capabilities("knowledge_tags")
    assert capabilities["readable"] and capabilities["updatable"] and capabilities["content_updatable"]
    assert not capabilities["registerable"] and not capabilities["unregisterable"]


def test_actual_resource_service_update_projects_the_saved_configuration(legacy):
    from management_portal.resources.runtime import create_local_resource_service
    service = create_local_resource_service()
    data, _, resolved = service.read_content(IDENTIFIER, actor="test-actor")
    assert json.loads(data) == TAGS
    record = service.update(IDENTIFIER, {"content": "[]", "expected_version": resolved["record"]["version"]},
                            actor="test-actor")
    assert record["version"] == project_registry.tags_version([])
    indexed = service._index().store.internal_get(IDENTIFIER)
    assert indexed["resource_type"] == "knowledge_tags"
    assert indexed["attributes"]["version"] == record["version"]
    assert project_registry.tags() == []


def test_update_resource_envelope_cas_reaches_singleton(legacy, monkeypatch):
    from types import SimpleNamespace
    from management_portal.resources import isa
    from management_portal.resources.runtime import create_local_resource_service
    service = create_local_resource_service()
    monkeypatch.setattr(isa, "_service", lambda: service)
    values = {"resource_id": IDENTIFIER, "changes": {"content": "[]"}}
    inv = SimpleNamespace(caller="test-actor", argument=values.get,
                          expected_version_for=lambda identifier: project_registry.tags_version(TAGS))
    result = isa.update_resource(inv)
    assert result["version"] == project_registry.tags_version([])
    assert project_registry.tags() == []


def test_tag_change_notification_refreshes_both_registry_projections(legacy, monkeypatch):
    from management_portal.resource_index_registries import ProjectRegistryAdapter, KnowledgeTagsRegistryAdapter
    from management_portal.resources.registry_store import registry_change_key
    notifications = []
    monkeypatch.setattr(project_registry, "announce_registry_changes",
                        lambda collection, changes: notifications.extend(
                            registry_change_key(collection, identifier) for identifier in changes))
    project_registry.replace_tags([], expected_version=project_registry.tags_version(TAGS))
    project_changes = ProjectRegistryAdapter().scan_changes(notifications, [], {})
    tag_changes = KnowledgeTagsRegistryAdapter().scan_changes(notifications, [], {})
    assert IDENTIFIER not in project_changes.deletes
    assert {record["resource_id"] for record in project_changes.upserts} == {"Demo"}
    assert {record["resource_id"] for record in tag_changes.upserts} == {IDENTIFIER}
    assert tag_changes.upserts[0]["attributes"]["version"] == project_registry.tags_version([])


def test_real_permissions_preserve_shared_viewer_admin_and_cas(legacy, monkeypatch):
    from tests.cases.permission_test_support import enable
    from management_portal.permission_system.facts import fact_from_record
    from management_portal.resources.runtime import create_local_resource_service
    permissions = enable(monkeypatch)
    permissions.users("reader", "writer", "outsider", "object_admin")
    permissions.administrator("admin")
    permissions.place(fact_from_record({"resource_id": IDENTIFIER, "resource_type": "knowledge_tags",
                                        "attributes": {"singleton": True}}, "knowledge_tags"))
    permissions.grant("writer", "@shared", a="@contributor")
    permissions.grant("object_admin", IDENTIFIER, a="@admin")
    permissions.deny("outsider", "@shared", a="@viewer")
    service = create_local_resource_service()
    store = get_registry_store()
    original = store.snapshot(project_registry.COLLECTION)
    with pytest.raises(PermissionError):
        service.read_content(IDENTIFIER, actor="outsider")
    for actor in ("reader", "writer", "object_admin"):
        with pytest.raises(PermissionError):
            service.update(IDENTIFIER, {"content": "[]", "expected_version": project_registry.tags_version(TAGS)}, actor=actor)
    assert store.snapshot(project_registry.COLLECTION) == original
    data, _, resolved = service.read_content(IDENTIFIER, actor="reader")
    assert json.loads(data) == TAGS
    version = resolved["record"]["version"]
    record = service.update(IDENTIFIER, {"content": "[]", "expected_version": version}, actor="admin")
    assert record["version"] == project_registry.tags_version([])
    with pytest.raises(ResourceVersionConflict):
        service.update(IDENTIFIER, {"content": json.dumps(TAGS), "expected_version": version}, actor="admin")
    with pytest.raises(ValueError, match="expected_version"):
        service.update(IDENTIFIER, {"content": json.dumps(TAGS)}, actor="admin")
    assert project_registry.tags() == []
