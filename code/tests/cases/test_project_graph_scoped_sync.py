"""A Lineage write re-projects only the Project graphs it can change.

A Project's principal graph shows its own members and the Leaders of the
Project's ordinary Lineages.  Writes used to re-project every Project graph in
the Portal inside their transaction; they now pass the Projects whose members
or Leaders they may have changed.  After each structural operation below, a
synchronization of every Project graph must find nothing left to change.

:module: lineage.team
"""
from __future__ import annotations

import pytest

from management_portal.society_service import lineage_domain as graphs
from management_portal.society_service import resident_catalog
from management_portal.society_service.state_store import PROJECT_GRAPH_FILTER, LineageDataStore
from tests.cases.isa_test_support import ACTOR
from tests.cases.test_principal_hierarchy import family, make_project_graph
from tests.cases.test_resident_folder_creation_contract import (
    future_creation_store, _new_session_payload,  # noqa: F401
)


def store():
    return resident_catalog.get_catalog().state_store


def assert_every_project_graph_is_current():
    def synchronize_all(connection):
        def revisions():
            return {
                str(row["lineage_id"]): (str(row["project_id"]), int(row["revision"]))
                for row in connection.execute(
                    f"SELECT lineage_id, project_id, revision FROM lineages WHERE {PROJECT_GRAPH_FILTER}"
                ).fetchall()
            }

        before = revisions()
        LineageDataStore._synchronize_project_graphs(connection, {project for project, _ in before.values()})
        return before, revisions()

    before, after = store().database.write(synchronize_all)
    assert before, "the scenario needs a Project graph"
    assert after == before, "a full synchronization still changed a Project graph"


def node_ids(lineage_id):
    return {node["id"] for node in graphs.get_lineage(lineage_id, ACTOR)["nodes"]}


def test_structural_operations_leave_no_project_graph_behind(future_creation_store):
    """结构操作后所有 Project 组织图无需再同步"""
    project, child = family("Project"), family("Child")
    member = graphs.create_lineage_resident(
        project["lineage_id"], _new_session_payload("Member"), ACTOR,
    )["resident"]
    make_project_graph(project["lineage_id"])
    assert_every_project_graph_is_current()

    # A new ordinary Lineage contributes its Leader.
    later = family("Later")
    assert later["resident_id"] in node_ids(project["lineage_id"])
    assert_every_project_graph_is_current()

    # Changing a Leader replaces the contributed node.
    successor = graphs.create_lineage_resident(
        child["lineage_id"], _new_session_payload("Successor"), ACTOR,
    )["resident"]
    graphs.update_lineage_metadata(child["lineage_id"], {"leader_resident_id": successor["resident_id"]}, ACTOR)
    assert {successor["resident_id"]} <= node_ids(project["lineage_id"])
    assert child["resident_id"] not in node_ids(project["lineage_id"])
    assert_every_project_graph_is_current()

    # A Resident that moves into the Project Lineage becomes a direct member.
    graphs.move_lineage_subgraph(child["lineage_id"], {
        "object_ids": [child["resident_id"]], "target_lineage_id": project["lineage_id"],
    }, ACTOR)
    assert child["resident_id"] in node_ids(project["lineage_id"])
    assert_every_project_graph_is_current()

    # Deleting a direct member and a contributing Leader.
    assert member["resident_id"] in node_ids(project["lineage_id"])
    graphs.delete_lineage_graph_objects(project["lineage_id"], {"resident_ids": [member["resident_id"]]}, ACTOR)
    graphs.delete_lineage_graph_objects(later["lineage_id"], {"resident_ids": [later["resident_id"]]}, ACTOR)
    assert not {member["resident_id"], later["resident_id"]} & node_ids(project["lineage_id"])
    assert_every_project_graph_is_current()

    # Deleting a whole ordinary Lineage removes its Leader.
    doomed = family("Doomed")
    assert doomed["resident_id"] in node_ids(project["lineage_id"])
    graphs.delete_lineage(doomed["lineage_id"], ACTOR)
    assert doomed["resident_id"] not in node_ids(project["lineage_id"])
    assert_every_project_graph_is_current()


def test_an_ordinary_edit_does_not_read_project_graphs(future_creation_store, monkeypatch):
    """普通 Lineage 修改不读取 Project 组织图"""
    project, child = family("Project"), family("Child")
    make_project_graph(project["lineage_id"])
    materialized = []
    original = LineageDataStore._materialize_lineage_members

    def observe(connection, lineage_id):
        materialized.append(lineage_id)
        return original(connection, lineage_id)

    monkeypatch.setattr(LineageDataStore, "_materialize_lineage_members", staticmethod(observe))
    graphs.update_lineage_metadata(child["lineage_id"], {"description": "only the text changes"}, ACTOR)
    assert materialized == []
    graphs.update_lineage_metadata(project["lineage_id"], {"description": "the Project graph itself"}, ACTOR)
    assert materialized == [project["lineage_id"]]


def test_a_second_project_graph_is_still_rejected(future_creation_store):
    """同一 Project 的第二个组织图仍被拒绝"""
    one, two = family("One"), family("Two")
    make_project_graph(one["lineage_id"])
    with pytest.raises(ValueError, match="only one"):
        make_project_graph(two["lineage_id"])
