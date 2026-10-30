"""Shared synthetic graph publishers preserve canonical execution facts."""
import copy

from management_portal.execution.agent_data_store import get_agent_data_store
from management_portal.society_service import lineage_lifecycle, lineage_reads, resident_catalog
from management_portal.society_service.lineage_storage import canonical_lineage_folder
from tests.cases.society_service_test_support import (
    create_lineage_with_resident_members, install_resource_boundary,
    install_registered_lineage_members, publish_registered_lineage_members,
)


def test_graph_publisher_keeps_nodes_existing_residents_and_main_agent(monkeypatch):
    """再次发布 Lineage 成员时不改写已登记的 Resident 身份、主 agent 和节点坐标"""
    install_resource_boundary(monkeypatch)
    nodes = [{"id": "resident_111111111111", "title": "First", "x": 12, "y": 34},
             {"id": "resident_222222222222", "title": "Second", "x": 56, "y": 78}]
    lineage = create_lineage_with_resident_members({"title": "Fixture", "nodes": nodes}, "sample_admin")
    catalog = resident_catalog.get_catalog()
    before = [catalog.get(node["id"]) for node in nodes]
    for record in before:
        assert record["lineage_id"] == lineage["lineage_id"]
        assert record["principal_hierarchy"]["scope_ids"] == [lineage["lineage_id"]]
        assert record["default_cwd"] == str(canonical_lineage_folder(lineage["lineage_id"])
            / "residents" / record["resident_id"] / "workspace")
        assert record["owned_workspace"]["workspace_id"] == record["owned_workspace_id"]
        assert record["workspace_bindings"] == []
        agent = get_agent_data_store().agent(record["resident_id"])
        assert agent["parent_agent_id"] is None
        assert agent["sequence_kind"] == "agent_trace"
    aggregate = copy.deepcopy(lineage)
    aggregate["nodes"][0]["title"] = "Do not overwrite existing identity"
    publish_registered_lineage_members(aggregate)
    assert [catalog.get(node["id"]) for node in nodes] == before
    current = lineage_reads.get_lineage(lineage["lineage_id"], "sample_admin")
    assert [(n["id"], n["x"], n["y"]) for n in current["nodes"]] == [
        (n["id"], n["x"], n["y"]) for n in lineage["nodes"]]


def test_registered_member_wrapper_uses_the_v30_publisher(monkeypatch):
    """创建 Lineage 时的成员登记包装经 v30 发布器写入 Resident 目录并保留节点坐标"""
    install_resource_boundary(monkeypatch)
    install_registered_lineage_members(monkeypatch)
    lineage = lineage_lifecycle.create_lineage({"title": "Fixture wrapper", "nodes": [
        {"id": "resident_333333333333", "title": "Member", "x": 3, "y": 4},
    ]}, "sample_admin")
    record = resident_catalog.get_catalog().get("resident_333333333333")
    assert record["lineage_id"] == lineage["lineage_id"]
    assert record["owned_workspace"]["workspace_id"] == record["owned_workspace_id"]
    current = lineage_reads.get_lineage(lineage["lineage_id"], "sample_admin")
    assert current["nodes"][0]["x"] == 3
    assert current["nodes"][0]["y"] == 4
