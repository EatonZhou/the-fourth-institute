import json
import socket
import subprocess

import pytest

from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.society_service import invariant_locks, lineage_lifecycle
from management_portal.society_service import lineage_aggregate
from management_portal.resources.collaboration_registry import CollaborationDescriptorStore
from tests.cases.isa_test_support import isolated_instruction_store  # noqa: F401 - fixture


@pytest.fixture(autouse=True)
def no_external_actions(tmp_path, monkeypatch, isolate_portal_databases):
    monkeypatch.setenv("PORTAL_SOCIETY_SERVICE_PROCESS", "0")
    monkeypatch.setattr(invariant_locks, "_ROOT_PROVIDER", lambda: tmp_path / "locks")

    def reject(*args, **kwargs):
        raise AssertionError("round6 prohibits network, Provider and subprocess effects")

    monkeypatch.setattr(socket.socket, "connect", reject)
    monkeypatch.setattr(subprocess, "Popen", reject)


def test_domain_owned_descriptor_and_execution_survive_reopen_without_aggregate_copy(isolated_instruction_store, tmp_path, record_property):
    """协作描述与 Execution 记录由各自存储持久化，重开后可读且不复制进 Lineage 状态"""
    created = lineage_lifecycle.create_lineage({"title": "round6 adjacent stores", "nodes": [], "edges": []}, "sample_admin")
    before = lineage_aggregate.read_lineage_state(created["lineage_id"])
    descriptors = CollaborationDescriptorStore()
    record = descriptors.put({"schema": "tfi.collaboration-descriptor.v2", "resource_id": created["lineage_id"],
                              "title": "external descriptor v1", "summary": "descriptor-only", "status": "active",
                              "project_ids": [], "relations": [], "attributes": {}, "created_at": "", "updated_at": "", "deleted_at": ""})
    execution = AgentDataStore()
    execution.begin_execution(execution_id="round6-isolated-execution", agent_id="resident_666666666666",
                              actor_ref="fixture-owner", provider="codex", started_at="2026-09-12T00:00:00Z")
    descriptors.put({**record, "title": "external descriptor v2"})
    fresh_descriptors = CollaborationDescriptorStore()
    fresh_execution = AgentDataStore()
    assert fresh_descriptors.get(created["lineage_id"])["title"] == "external descriptor v2"
    assert fresh_execution.execution("round6-isolated-execution")["agent_id"] == "resident_666666666666"
    assert lineage_aggregate.read_lineage_state(created["lineage_id"]) == before
    serialized = json.dumps(before, ensure_ascii=False)
    assert "external descriptor v2" not in serialized and "round6-isolated-execution" not in serialized
    record_property("point", "176")
    record_property("scope", "actual descriptor filesystem and Execution PostgreSQL stores/reopen; not Resource body, Project registry, Workspace binding or full workflow")
