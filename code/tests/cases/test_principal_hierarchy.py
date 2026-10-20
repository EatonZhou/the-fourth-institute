"""
:module: lineage.team
:importance: P1
:aspects: behavior, architecture-conformance
:design: decision_3e07c6d35cac
"""
from __future__ import annotations
from management_portal.society_service import resident_catalog
from management_portal.society_service import resident_conversation_stream

import copy

import pytest

from management_portal.society_service import lineage_domain as graphs
from management_portal.society_service import resident_project_domain
from management_portal.society_service import principal_hierarchy as hierarchy
from tests.cases.test_resident_folder_creation_contract import future_creation_store, _new_session_payload


def family(title):
    graph = graphs.create_lineage({"title": title, "project_id": "project_test"}, "sample_admin")
    root = graphs.create_lineage_resident(graph["lineage_id"], _new_session_payload(title), "sample_admin")["resident"]
    graph = graphs.get_lineage(graph["lineage_id"], "sample_admin")
    if graph["leader_resident_id"] != root["resident_id"]:
        graphs.update_lineage_metadata(graph["lineage_id"], {"leader_resident_id": root["resident_id"], "expected_revision": graph["revision"]}, "sample_admin")
    return root


def make_project_graph(lineage_id, project_id="project_test"):
    """Store an existing Lineage as its Project's principal graph.

    Production creates the Project Lineage with its Project (CreateProject); a
    scenario that needs members and child Lineages first writes the same scope
    through the Lineage state transaction, where the store keeps one principal
    graph per Project.
    """
    with graphs.catalog_mutation_lock("principal-hierarchy", operation="test"), graphs._lineage_scope(lineage_id):
        current = graphs._read_lineage(lineage_id, actor="sample_admin")
        candidate = copy.deepcopy(current)
        candidate[hierarchy.FIELD] = hierarchy.lineage_scope(
            {"level": "project", "scope_id": project_id}, project_id=current["project_id"],
        )
        resident_catalog.get_catalog().state_store.transaction(
            lambda: graphs._persist_lineage_state_transaction(current, candidate, "principal_hierarchy"),
        )


def test_project_graph_reuses_identity_and_tracks_child_leaders(future_creation_store):
    """项目图复用现有身份并动态跟踪子 Lineage 领导者"""
    project, child = family("Project"), family("Child")
    catalog = resident_catalog.get_catalog()
    original = copy.deepcopy(catalog.get(child["resident_id"]))
    make_project_graph(project["lineage_id"])
    projected = graphs.get_lineage(project["lineage_id"], "sample_admin")
    assert {node["id"] for node in projected["nodes"]} == {project["resident_id"], child["resident_id"]}
    node = next(node for node in projected["nodes"] if node["id"] == child["resident_id"])
    assert node["home_lineage_id"] == child["lineage_id"]
    assert node["accessible"] is True
    assert catalog.get(child["resident_id"]) == original
    assert catalog.state_store.lineage_ids_for_resident(child["resident_id"]) == [child["lineage_id"]]
    later = family("Later")
    assert later["resident_id"] in {node["id"] for node in graphs.get_lineage(project["lineage_id"], "sample_admin")["nodes"]}
    listing = next(row for row in graphs.list_lineages("sample_admin")["lineages"] if row["lineage_id"] == project["lineage_id"])
    assert listing["principal_hierarchy"]["level"] == "project"
    with pytest.raises(ValueError, match="Project references"):
        graphs.delete_lineage_graph_objects(project["lineage_id"], {"resident_ids": [child["resident_id"]],
            "expected_revision": graphs.get_lineage(project["lineage_id"], "sample_admin")["revision"]}, "sample_admin")


def test_project_scope_is_unique_and_rejected_change_rolls_back(future_creation_store):
    """项目范围保持唯一且被拒绝的修改回滚"""
    one, two = family("One"), family("Two")
    make_project_graph(one["lineage_id"])
    with pytest.raises(ValueError, match="only one"):
        make_project_graph(two["lineage_id"])
    assert graphs.get_lineage(two["lineage_id"], "sample_admin")["principal_hierarchy"]["level"] == "lineage"


def test_denied_child_exposes_only_topology():
    """无权访问的子节点只暴露拓扑信息"""
    view = {"lineage_id": "project_graph", "principal_hierarchy": {"level": "project"}, "nodes": [
        {"id": "child", "title": "Private title", "role": {"prompt": "secret"}, "status": "running", "x": 2, "y": 3},
    ]}
    hierarchy.project_node_access(view, [{"resident_id": "child", "lineage_id": "child_graph"}], lambda _: False)
    assert view["nodes"] == [{"id": "child", "title": "无权访问的 Lineage", "role": {}, "x": 2, "y": 3,
                              "hierarchy_reference": True, "accessible": False}]


def test_shared_user_scope_and_leader_levels_do_not_change_permissions():
    """共享用户范围和领导层级保留归属约束"""
    record = {"resident_id": "worker", "principal_hierarchy": {
        "level": "user", "category": "shared_service", "scope_ids": ["CASE-A", "sample_admin"],
    }}
    assert hierarchy.level_for_resident(record, {"leader_resident_id": "worker"}) == "user"
    for level in ("project", "lineage"):
        former = {"resident_id": "former", "principal_hierarchy": {"level": level, "category": "leader", "scope_ids": []}}
        assert hierarchy.level_for_resident(former, {"leader_resident_id": "new"}) == "resident"
    with pytest.raises(ValueError):
        hierarchy.resident_assignment({"level": "portal_admin", "category": "admin", "scope_ids": []})
    assert hierarchy.scope_after_project_change({"level": "lineage", "scope_id": "A"}, "B") == {"level": "lineage", "scope_id": "B"}
    with pytest.raises(ValueError, match="cannot move"):
        hierarchy.scope_after_project_change({"level": "project", "scope_id": "A"}, "B")


def test_ensure_project_principal_creates_once_without_starting_execution(future_creation_store, monkeypatch):
    """项目主体仅创建一次且不启动执行"""
    from management_portal.society_service import principal_hierarchy_domain, resource_client
    monkeypatch.setattr(resident_project_domain, "_project_root_for_id", lambda _: future_creation_store)
    monkeypatch.setattr(resource_client, "_query_all", lambda *args, **kwargs: [{"resource_id": "project_test", "title": "Test project", "attributes": {"path": str(future_creation_store)}}])
    child = family("Child")
    first = principal_hierarchy_domain.ensure_project("project_test", "sample_admin")
    again = principal_hierarchy_domain.ensure_project("project_test", "sample_admin")
    assert first["created"] is True and again["created"] is False
    assert first["resident_id"] == again["resident_id"]
    graph = graphs.get_lineage(first["lineage_id"], "sample_admin")
    assert graph["leader_resident_id"] == first["resident_id"]
    assert child["resident_id"] in {node["id"] for node in graph["nodes"]}
    assert resident_conversation_stream.get_repository().read_messages(first["resident_id"]) == []


def test_new_residents_receive_their_home_scope_without_copying_leadership(future_creation_store):
    """验证五层组织身份、导航与生命周期约定。"""
    root = family("Shared service")
    catalog = resident_catalog.get_catalog()
    assert catalog.get(root["resident_id"])["principal_hierarchy"]["level"] == "lineage"
    member = graphs.create_lineage_resident(root["lineage_id"], _new_session_payload("New shared worker"), "sample_admin")["resident"]
    assert catalog.get(member["resident_id"])["principal_hierarchy"]["level"] == "resident"
    assert catalog.get(member["resident_id"])["lineage_id"] == root["lineage_id"]
