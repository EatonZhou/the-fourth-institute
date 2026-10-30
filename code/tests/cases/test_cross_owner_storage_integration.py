from management_portal.society_service import business_fact_views
from management_portal.society_service.lineage_state_domains import definition_projection
import builtins
import hashlib
import io
import json
import socket
from tests.cases.database_test_support import connect
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import knowledge_board_lan.knowledge_backend as knowledge_backend
import knowledge_board_lan.knowledge_details as knowledge_details
import knowledge_board_lan.knowledge_source as knowledge_source
from management_common import site_registry
from management_portal import portal_access_control, resource_index_backend
from management_portal import resource_index_knowledge, resource_index_records, resource_index_registries
from management_portal.execution import agent_data_store, service as execution_service
from management_portal.execution.accounts import ExecutionAccounts
from management_portal.execution.contracts import ExecutionRequest
from management_portal.execution.scheduler import Scheduler
from management_portal.society_service import (
    domain_ports,
    execution_client,
    invariant_locks,
    lineage_state,
    project_domain,
    resident_catalog,
    resident_domain_common,
    resident_hatg_credentials,
    resident_record_domain,
    resource_client,
    state_store,
)
from management_portal.society_service import lineage_aggregate
from management_portal.portal_access_control import PortalAccessStore
from management_portal.resources import ResourceService
from management_portal.resources.collaboration_registry import CollaborationDescriptorStore
from management_portal.resources.providers import collaboration, knowledge, project
from management_portal.resources.registration import ResourceRegistrationStore
import management_portal.resources as resources
from tests.cases.registry_support import ProjectRegistryYaml, write_projects
from management_portal.execution import codex_runtime_settings


ACTOR = "storage-fixture"
PROJECT = "StorageOwnerFixture"
CONTENT_ID = "concept_030303030176"
CONTENT = "RESOURCE_BODY_ONLY_176: an independently owned contract."
POINT = "decision_1b2bdf5c33d6#5029ad110047c56e"

_RESOURCE_CLIENT_METHODS = {
    name: getattr(resource_client, name)
    for name in (
        "sync_lineage",
        "sync_resident",
        "sync_resident_resource_bundles",
        "workspace_id_for_path",
        "project_ids_for_path",
        "project_root",
        "project_roots",
    )
}


class InProcessExecutionClient:
    def __init__(self, server):
        self.server = server

    def request(self, method, params, **kwargs):
        return self.server.dispatch(method, params)


def install_resolvers(service):
    knowledge.install_resolver(service)
    collaboration.install_resolver(service)


@pytest.fixture
def owners(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Network, Provider and subprocess access is forbidden")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    original_open = builtins.open
    original_io_open = io.open

    def check_file(path, mode="r"):
        if isinstance(path, int):
            return
        target = Path(path).expanduser().resolve(strict=False)
        if target.is_relative_to(Path(__file__).resolve().parents[2]) and not any(flag in mode for flag in "wax+"):
            return
        shared_data = any(str(target).startswith(prefix) for prefix in (
            "/share/Projects/", "/share/Agents/", str(Path.home() / ".tfi/secrets") + "/",
        ))
        if not target.is_relative_to(tmp_path) and (shared_data or any(flag in mode for flag in "wax+")):
            raise AssertionError(f"Fixture filesystem access escaped: {target}")

    def safe_open(path, mode="r", *args, **kwargs):
        check_file(path, mode)
        return original_open(path, mode, *args, **kwargs)

    def safe_io_open(path, mode="r", *args, **kwargs):
        check_file(path, mode)
        return original_io_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", safe_open)
    monkeypatch.setattr(io, "open", safe_io_open)
    for method_name, implementation in _RESOURCE_CLIENT_METHODS.items():
        monkeypatch.setattr(resource_client, method_name, implementation)
    monkeypatch.setattr(invariant_locks, "_ROOT_PROVIDER", lambda: tmp_path / "locks")
    registry = ProjectRegistryYaml()
    write_projects({
        "schema_version": 1,
        "projects": [],
        "status_vocab": ["active"],
    })
    project_root = tmp_path / "Projects"
    monkeypatch.setitem(site_registry.PATH_PREFIXES, "projects", {"from": "/Projects", "to": str(project_root)})
    monkeypatch.setattr(project, "SHARE_ROOT", tmp_path)
    monkeypatch.setattr(project, "PROJECTS_ROOT", project_root)
    monkeypatch.setattr(project_domain, "PROJECTS_ROOT", project_root)
    monkeypatch.setattr(lineage_aggregate, "DATA_ROOT", tmp_path / "lineage")
    monkeypatch.setattr(lineage_state, "LINEAGE_FOLDER_ROOT", tmp_path / "lineages")
    monkeypatch.setenv("PORTAL_LINEAGE_FOLDER_ROOT", str(tmp_path / "lineages"))
    for name, value in {"STAGING_ROOT": tmp_path, "AGENTS_ROOT": tmp_path / "agents", "PROJECTS_ROOT": project_root}.items():
        monkeypatch.setattr(resident_domain_common, name, value)
    monkeypatch.setattr(resident_hatg_credentials, "TOKEN_ROOT", tmp_path / "credentials")
    monkeypatch.setattr(
        codex_runtime_settings,
        "codex_runtime_catalog",
        lambda *, account_id="": {
            "account_id": account_id,
            "models": ["gpt-6-astra"],
            "default_model": "gpt-6-astra",
            "default_reasoning_effort": "xhigh",
            "default_efforts_by_model": {"gpt-6-astra": "xhigh"},
            "efforts_by_model": {"gpt-6-astra": ["xhigh"]},
            "service_tiers_by_model": {"gpt-6-astra": []},
        },
    )
    access = PortalAccessStore()
    access.upsert_user(ACTOR, actor=ACTOR, status="active")
    access.upsert_user("storage-outsider", actor=ACTOR, status="active")
    access_factory = portal_access_control.get_portal_access_store
    for module in tuple(sys.modules.values()):
        if module is not None and getattr(module, "get_portal_access_store", None) is access_factory:
            monkeypatch.setattr(module, "get_portal_access_store", lambda: access)
    monkeypatch.setattr(portal_access_control, "get_portal_access_store", lambda: access)
    monkeypatch.setattr(project, "get_portal_access_store", lambda: access)
    descriptors = CollaborationDescriptorStore()
    registrations = ResourceRegistrationStore()
    index_store = resource_index_backend.IndexStore( portal_access=access)
    project_adapter = resource_index_registries.ProjectRegistryAdapter()
    index = resource_index_backend.ResourceIndexService(
        store=index_store,
        adapters=[project_adapter, resource_index_knowledge.ProjectKnowledgeAdapter()],
        start_worker=False,  portal_access=access,
    )
    resource = ResourceService(
        index_provider=index, portal_access=access,
        collaboration_store=descriptors, registration_store=registrations,
        resolver_loader=install_resolvers,
    )
    monkeypatch.setattr(resource_client, "get_resource_service", lambda: resource)
    monkeypatch.setattr(resources, "get_resource_service", lambda: resource)
    monkeypatch.setattr(knowledge_backend, "get_resource_service", lambda: resource)
    monkeypatch.setattr(knowledge_source, "get_resource_service", lambda: resource)
    monkeypatch.setattr(knowledge_details, "get_resource_service", lambda: resource)
    monkeypatch.setattr(resource_index_backend, "get_service", lambda: index)
    for method_name in (
        "sync_lineage", "sync_resident",
        "sync_resident_resource_bundles",
    ):
        monkeypatch.delitem(
            domain_ports.resource_port.__dict__, method_name, raising=False,
        )
    monkeypatch.setattr(domain_ports.resource_port, "_implementation", resource_client)
    project_record = project.register_project(
        {"id": PROJECT, "description": "PROJECT_REGISTRY_ONLY_176", "project_type": "other", "initial_admin_ids": [ACTOR]},
        actor=ACTOR, access_store=access,
    )
    records, source_state = project_adapter.scan()
    index_store.publish_snapshot("project", records, source_state)
    domain_ports.project_domain_port.validate_existing_project_selection({"project_id": PROJECT})
    record = domain_ports.resident_domain_port.register_resident({
        "kind": "project", "project_id": PROJECT, "title": "Owner storage fixture",
        "backend": "codex",
        "mode": "new_session",
    }, ACTOR)
    resident_id = record["resident_id"]
    lineage_id = record["lineage_id"]
    created = knowledge_backend.register_item("concept", CONTENT_ID, {
        "project_id": PROJECT, "title": "Isolated body", "body": CONTENT,
    }, actor=ACTOR)
    content_path = Path(created["path"])
    assert content_path.is_relative_to(tmp_path)
    knowledge_record = resource_index_records.resource_record(
        resource_id=CONTENT_ID, resource_type="concept", title="Isolated body",
        adapter="project_knowledge", path=content_path, facets=["knowledge"],
        attributes={
            "project_ids": [PROJECT], "project_status": "active",
            "type": "concept", "filename": content_path.name,
            "content_root": str(content_path.parent), "file_count": 1,
        },
    )
    index_store.publish_snapshot("project_knowledge", [knowledge_record], {})
    assert index_store.get(CONTENT_ID, actor=ACTOR)["resource_id"] == CONTENT_ID
    data = agent_data_store.AgentDataStore()
    scheduler = Scheduler(quota_reserve_enabled=False)
    publisher = SimpleNamespace(publish=lambda *args, **kwargs: None)
    monkeypatch.setattr(execution_service, "get_notification_publisher", lambda: publisher)
    preparation = execution_service.ExecutionPreparation(accounts=ExecutionAccounts(()))
    server = execution_service.ExecutionServiceServer(
        tmp_path / "fixture-execution.sock", scheduler, preparation=preparation, agent_data=data,
    )
    execution = execution_service.ExecutionService(client=InProcessExecutionClient(server))
    monkeypatch.setattr(execution_client, "service", lambda: execution)
    monkeypatch.delitem(
        domain_ports.execution_port.__dict__, "service", raising=False,
    )
    monkeypatch.setattr(domain_ports.execution_port, "_implementation", execution_client)
    monkeypatch.setattr(agent_data_store, "get_agent_data_store", lambda: data)
    assert domain_ports.resource_port._implementation is resource_client
    assert domain_ports.execution_port._implementation is execution_client
    assert domain_ports.execution_port.service() is execution
    context = SimpleNamespace(
        root=tmp_path, project_root=project_root / PROJECT, registry=registry,
        resource=resource, access=access, descriptors=descriptors,
        index=index, index_store=index_store, registrations=registrations,
        content_path=content_path, resident_id=resident_id, lineage_id=lineage_id,
        record=record, scheduler=scheduler, server=server, execution=execution,
        data=data, project_record=project_record, monkeypatch=monkeypatch,
    )
    try:
        yield context
    finally:
        server.server_close()
        index.adapter_layer.stop()


def attach_and_submit(owners):
    path = owners.project_root / "knowledge_base" / "concept"
    domain_ports.resident_domain_port.add_resident_reference_workspace(
        owners.resident_id, workspace_path=str(path), actor=ACTOR,
    )
    binding = next(item for item in resident_record_domain.find_resident(owners.resident_id)["workspace_bindings"] if item["path"] == str(path))
    body, request, receipt = submit_bound_resource(owners, binding)
    return binding, body, request, receipt


def submit_bound_resource(owners, binding, *, resource_actor=ACTOR):
    record = domain_ports.resource_port.get(CONTENT_ID, actor=resource_actor)
    assert record["resource_id"] == CONTENT_ID
    body, content_type, metadata = owners.resource.read_content(
        CONTENT_ID, actor=resource_actor
    )
    request = ExecutionRequest.new(
        actor=ACTOR, agent_id=owners.resident_id, correlation_id=owners.lineage_id,
        prompt=body.decode(), service_type="chat_completion", provider="fixture-provider", operation="generate",
        metadata={"resource_id": CONTENT_ID, "workspace_id": binding["workspace_id"], "project_id": PROJECT},
    )
    receipt = domain_ports.execution_port.service().submit(request)
    return body, request, receipt


def execution_count(owners):
    with connect(owners.scheduler) as connection:
        return connection.execute("SELECT COUNT(*) FROM executions").fetchone()[0]


def reopen_resident_catalog(owners):
    return resident_catalog.ResidentCatalog(
        owners.root / "lineages",
        state_store.LineageDataStore(),
    )


def assert_separate_owners(owners, binding, body, request):
    aggregate = lineage_aggregate.read_lineage_state(owners.lineage_id)
    serialized = json.dumps(aggregate, ensure_ascii=False)
    assert CONTENT not in serialized
    assert "PROJECT_REGISTRY_ONLY_176" not in serialized
    assert request.execution_id not in json.dumps(definition_projection(aggregate), ensure_ascii=False)
    node = next(node for node in aggregate["nodes"] if node["id"] == owners.resident_id)
    assert node["last_execution"]["execution_id"] == request.execution_id
    assert "workspace_bindings" not in serialized
    assert CONTENT.encode() in body
    assert owners.content_path.read_bytes() == body
    assert any(item["id"] == PROJECT for item in yaml.safe_load(owners.registry.read_text())["projects"])
    assert "memberships" not in owners.access.user(ACTOR)
    canonical = resident_record_domain.find_resident(owners.resident_id)
    assert binding in canonical["workspace_bindings"]
    execution = owners.scheduler.snapshot(request.execution_id)
    assert execution["status"] == "queued"
    assert execution["request"]["prompt"] == body.decode()
    assert execution["request"]["metadata"]["workspace_id"] == binding["workspace_id"]
    assert execution["request"]["actor"] == ACTOR
    assert execution["request"]["agent_id"] == owners.resident_id
    assert execution_count(owners) == 1
    return aggregate


def test_project_binding_resource_and_execution_follow_real_domain_ports(owners, record_property):
    """项目绑定资源与执行经真实领域端口持久化且可重开"""
    binding, body, request, receipt = attach_and_submit(owners)
    aggregate = assert_separate_owners(owners, binding, body, request)
    assert receipt["execution_id"] == request.execution_id
    state_store.reset_lineage_data_store_for_tests()
    assert lineage_aggregate.read_lineage_state(owners.lineage_id) == aggregate
    reopened_catalog = reopen_resident_catalog(owners)
    assert binding in reopened_catalog.get(owners.resident_id)["workspace_bindings"]
    fresh_queue = Scheduler()
    assert fresh_queue.snapshot(request.execution_id)["request"] == request.to_payload()
    fresh_access = PortalAccessStore()
    assert "memberships" not in fresh_access.user(ACTOR)
    fresh_descriptors = CollaborationDescriptorStore()
    fresh_index_store = resource_index_backend.IndexStore( portal_access=owners.access)
    fresh_index = resource_index_backend.ResourceIndexService(store=fresh_index_store, adapters=[], start_worker=False,  portal_access=owners.access)
    fresh_resource = ResourceService(
        index_provider=fresh_index, portal_access=owners.access,
        collaboration_store=fresh_descriptors, registration_store=ResourceRegistrationStore(), resolver_loader=install_resolvers,
    )
    owners.monkeypatch.setattr(knowledge_backend, "get_resource_service", lambda: fresh_resource)
    owners.monkeypatch.setattr(knowledge_source, "get_resource_service", lambda: fresh_resource)
    owners.monkeypatch.setattr(knowledge_details, "get_resource_service", lambda: fresh_resource)
    owners.monkeypatch.setattr(resource_client, "get_resource_service", lambda: fresh_resource)
    assert fresh_resource.read_content(CONTENT_ID, actor=ACTOR)[0] == body
    assert binding in resident_record_domain.find_resident(owners.resident_id)["workspace_bindings"]
    fresh_index.adapter_layer.stop()
    record_property("point_id", POINT)
    record_property("owners", "Project registry -> project port -> Resident/Lineage -> Binding port -> Resource body -> Execution port -> durable queue; no worker or Provider")
    record_property("body_sha256", hashlib.sha256(body).hexdigest())


def test_another_reader_uses_the_same_committed_storage_path(owners, record_property):
    """非创建者读取资源并提交执行时仍经同一持久路径"""
    scoped_actor = "storage-scoped-reader"
    owners.access.upsert_user(
        scoped_actor,
        actor=ACTOR,
        status="active",

    )
    path = owners.content_path.parent
    domain_ports.resident_domain_port.add_resident_reference_workspace(owners.resident_id, workspace_path=str(path), actor=ACTOR)
    before_resident = deepcopy(resident_record_domain.find_resident(owners.resident_id))
    before_lineage = deepcopy(lineage_aggregate.read_lineage_state(owners.lineage_id))
    binding = next(item for item in before_resident["workspace_bindings"] if item["path"] == str(path))
    assert execution_count(owners) == 0
    owners.access.upsert_user(scoped_actor, actor=ACTOR)
    body, request, receipt = submit_bound_resource(
        owners, binding, resource_actor=scoped_actor
    )
    assert execution_count(owners) == 1
    state_store.reset_lineage_data_store_for_tests()
    assert resident_record_domain.find_resident(owners.resident_id)["workspace_bindings"] == before_resident["workspace_bindings"]
    reopened_catalog = reopen_resident_catalog(owners)
    assert reopened_catalog.get(owners.resident_id)["workspace_bindings"] == before_resident["workspace_bindings"]
    after_lineage = lineage_aggregate.read_lineage_state(owners.lineage_id)
    assert after_lineage["nodes"][0]["status"] == "queued"
    assert after_lineage["nodes"][0]["last_execution"]["execution_id"] == request.execution_id
    assert definition_projection(after_lineage) == definition_projection(before_lineage)
    assert CONTENT in owners.content_path.read_text()
    assert_separate_owners(owners, binding, body, request)
    record_property("point_id", POINT)
    record_property("authorization_boundary", "Another active user reads through the same Resource path while Binding, content and Execution remain independently committed")


def test_execution_identity_conflict_keeps_other_owners_and_first_receipt(owners, record_property):
    """执行标识冲突保留首次请求回执与其他领域数据"""
    binding, body, request, receipt = attach_and_submit(owners)
    aggregate = assert_separate_owners(owners, binding, body, request)
    conflicting = request.to_payload()
    conflicting["prompt"] = "UNCOMMITTED_EXECUTION_BODY_176"
    with pytest.raises(ValueError, match="different request"):
        domain_ports.execution_port.service().submit(ExecutionRequest.from_payload(conflicting))
    fresh_queue = Scheduler()
    assert fresh_queue.snapshot(request.execution_id)["request"] == request.to_payload()
    assert execution_count(owners) == 1
    repeated = domain_ports.execution_port.service().submit(request)
    assert repeated["execution_id"] == receipt["execution_id"]
    assert execution_count(owners) == 1
    assert owners.resource.read_content(CONTENT_ID, actor=ACTOR)[0] == body
    assert lineage_aggregate.read_lineage_state(owners.lineage_id) == aggregate
    assert_separate_owners(owners, binding, body, request)
    record_property("point_id", POINT)
    record_property("failure_boundary", "Execution owner rejects conflicting immutable request after prior owner commits; reopen retains first request and no foreign body in Lineage")
