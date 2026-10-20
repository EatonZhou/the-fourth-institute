"""Fork preparation must not resurrect or depend on unrelated deleted members.

:module: spec.design-governance
"""
from concurrent.futures import ThreadPoolExecutor

import pytest
from management_portal.society_service import lineage_domain as graphs
from tests.cases.test_principal_hierarchy import family
from tests.cases.test_resident_folder_creation_contract import (
    future_creation_store, _new_session_payload, _append_conversation_turn, CANONICAL_RESIDENT_TARGETS,
)


@pytest.mark.parametrize("delete_source", [False, True])
def test_fork_attaches_against_committed_membership(future_creation_store, monkeypatch, delete_source):
    """Fork 准备期间其他成员被删时仍按已提交成员接入，来源被删则报冲突并删除新 Resident"""
    root = family("Team")
    lineage_id = root["lineage_id"]
    source = graphs.create_lineage_resident(lineage_id, _new_session_payload("Source"), "sample_admin")["resident"]
    other = graphs.create_lineage_resident(lineage_id, _new_session_payload("Other"), "sample_admin")["resident"]
    _append_conversation_turn(source["resident_id"], 0)
    monkeypatch.setattr(graphs, "_resident_targets", CANONICAL_RESIDENT_TARGETS)
    create = graphs._create_derived_resident
    made = []

    def prepare(*args, **kwargs):
        detail = create(*args, **kwargs)
        made.append(detail["resident_id"])
        selected = source if delete_source else other
        # A separate transaction must finish while Fork preparation is in progress.
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(graphs.delete_lineage_graph_objects, lineage_id,
                {"resident_ids": [selected["resident_id"]]}, "sample_admin").result(timeout=15)
        return detail

    monkeypatch.setattr(graphs, "_create_derived_resident", prepare)
    call = lambda: graphs.fork_lineage_resident(lineage_id, {
        "source": source["resident_id"], "fork_mode": "full", "child_title": "Child",
    }, "sample_admin")
    if delete_source:
        with pytest.raises(graphs.LineageConflictError, match="source"):
            call()
    else:
        call()
    current = graphs.get_lineage(lineage_id, "sample_admin")
    ids = {n["id"] for n in current["nodes"]}
    assert made
    if delete_source:
        assert made[0] not in ids
        from management_portal.society_service import resident_catalog
        assert resident_catalog.get_catalog().get_historical(made[0])["deleted_at"]
    else:
        assert ids == {root["resident_id"], source["resident_id"], made[0]}
        assert any(e["source"] == source["resident_id"] and e["target"] == made[0]
                   for e in current["edges"])
