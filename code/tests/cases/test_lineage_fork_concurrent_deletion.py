"""Fork preparation must not resurrect or depend on unrelated deleted members.

:module: spec.design-governance
"""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from management_portal.society_service import (
    lineage_branches,
    lineage_graph_edits,
    lineage_lifecycle,
    lineage_reads,
    lineage_resident_nodes,
    lineage_state,
)
from management_portal.society_service.isa import common
from tests.cases.test_principal_hierarchy import family
from tests.cases.test_resident_folder_creation_contract import (
    future_creation_store, _new_session_payload, _append_conversation_turn, CANONICAL_RESIDENT_TARGETS,
)


@pytest.mark.parametrize("delete_source", [False, True])
def test_fork_attaches_against_committed_membership(future_creation_store, monkeypatch, delete_source):
    """Fork 准备期间其他成员被删时仍按已提交成员接入，来源被删则报冲突并删除新 Resident"""
    root = family("Team")
    lineage_id = root["lineage_id"]
    source = lineage_resident_nodes.create_lineage_resident(lineage_id, _new_session_payload("Source"), "sample_admin")["resident"]
    other = lineage_resident_nodes.create_lineage_resident(lineage_id, _new_session_payload("Other"), "sample_admin")["resident"]
    _append_conversation_turn(source["resident_id"], 0)
    monkeypatch.setattr(lineage_state, "_resident_targets", CANONICAL_RESIDENT_TARGETS)
    create = lineage_branches._create_derived_resident
    made = []

    def prepare(*args, **kwargs):
        detail = create(*args, **kwargs)
        made.append(detail["resident_id"])
        selected = source if delete_source else other
        # A separate transaction must finish while Fork preparation is in progress.
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(lineage_graph_edits.delete_lineage_graph_objects, lineage_id,
                {"resident_ids": [selected["resident_id"]]}, "sample_admin").result(timeout=15)
        return detail

    monkeypatch.setattr(lineage_branches, "_create_derived_resident", prepare)
    call = lambda: lineage_branches.fork_lineage_resident(lineage_id, {
        "source": source["resident_id"], "fork_mode": "full", "child_title": "Child",
    }, "sample_admin")
    if delete_source:
        with pytest.raises(lineage_state.LineageConflictError, match="source"):
            call()
    else:
        call()
    current = lineage_reads.get_lineage(lineage_id, "sample_admin")
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


def test_versioned_fork_does_not_conflict_with_its_own_child(future_creation_store, monkeypatch):
    """Fork 的首次成员提交检查版本，随后挂接不把自身提交误判成并发修改"""
    root = family("Versioned Fork")
    lineage_id = root["lineage_id"]
    source = lineage_resident_nodes.create_lineage_resident(lineage_id, _new_session_payload("Source"), "sample_admin")["resident"]
    _append_conversation_turn(source["resident_id"], 0)
    monkeypatch.setattr(lineage_state, "_resident_targets", CANONICAL_RESIDENT_TARGETS)
    revision = lineage_reads.get_lineage(lineage_id, "sample_admin")["revision"]
    invocation = SimpleNamespace(arguments={"lineage_id": lineage_id}, expected_version=revision,
                                 expected_version_for=lambda _: revision)
    result = common.guard_definition_handler("ForkResident", lambda _: lineage_branches.fork_lineage_resident(
        lineage_id, {"source": source["resident_id"], "fork_mode": "full", "child_title": "Child"},
        "sample_admin",
    ))(invocation)
    child = result["created_resident"]["id"]
    current = lineage_reads.get_lineage(lineage_id, "sample_admin")
    assert any(e["source"] == source["resident_id"] and e["target"] == child for e in current["edges"])


def test_stale_fork_precondition_leaves_no_child(future_creation_store, monkeypatch):
    """过期版本的 Fork 不得遗留已注册的子 Resident 或改变已提交的图"""
    from management_portal.hatg.contracts import InstructionError
    root = family("Stale Fork")
    lineage_id = root["lineage_id"]
    source = lineage_resident_nodes.create_lineage_resident(lineage_id, _new_session_payload("Source"), "sample_admin")["resident"]
    _append_conversation_turn(source["resident_id"], 0)
    monkeypatch.setattr(lineage_state, "_resident_targets", CANONICAL_RESIDENT_TARGETS)
    revision = lineage_reads.get_lineage(lineage_id, "sample_admin")["revision"]
    lineage_lifecycle.update_lineage_metadata(lineage_id, {"description": "concurrent edit"}, "sample_admin")
    invocation = SimpleNamespace(arguments={"lineage_id": lineage_id}, expected_version=revision,
                                 expected_version_for=lambda _: revision)
    with pytest.raises(InstructionError) as error:
        common.guard_definition_handler("ForkResident", lambda _: lineage_branches.fork_lineage_resident(
            lineage_id, {"source": source["resident_id"], "fork_mode": "full", "child_title": "Rejected"},
            "sample_admin",
        ))(invocation)
    assert error.value.code == "instruction.conflict"
    current = lineage_reads.get_lineage(lineage_id, "sample_admin")
    assert current["description"] == "concurrent edit"
    assert {node["id"] for node in current["nodes"]} == {root["resident_id"], source["resident_id"]}
