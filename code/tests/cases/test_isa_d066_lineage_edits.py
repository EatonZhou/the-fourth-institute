"""Operation-specific authorization at the atomic Lineage edit boundary."""

import pytest

from management_portal.society_service import (
    function_block_domain,
    lineage_aggregate,
    lineage_graph_edits,
    lineage_lifecycle,
    lineage_moves,
    lineage_program_resident_inputs,
    lineage_resident_nodes,
    resident_catalog,
)
from tests.cases.isa_test_support import ACTOR
from tests.cases.test_resident_folder_creation_contract import future_creation_store  # noqa: F401


@pytest.fixture
def lineage(future_creation_store):
    return lineage_lifecycle.create_lineage({"title": "Team", "project_id": "project_test"}, ACTOR)["lineage_id"]


def graph(lineage_id):
    return lineage_aggregate.read_lineage(lineage_id, actor=ACTOR)


def resident(lineage_id, title):
    return lineage_resident_nodes.create_lineage_resident(
        lineage_id, {"title": title, "backend": "codex", "creation_key": f"test-{title}"}, ACTOR,
    )["resident"]["resident_id"]


@pytest.mark.parametrize("dry_run", [False, True])
def test_denied_resident_delete_rolls_back_other_graph_operations(lineage, dry_run):
    worker = resident(lineage, "Worker")
    before = graph(lineage)
    event = {
        "id": "event_source_ffaabb001122",
        "kind": "event_source",
        "config": {"event_type": "manual.triggered"},
    }

    def authorize(requirements):
        assert requirements == (
            ("create_component", lineage),
            ("delete_resident", lineage),
            ("delete_resident", worker),
        )
        raise PermissionError("caller lacks Resident admin")

    with pytest.raises(PermissionError, match="Resident admin"):
        lineage_graph_edits.apply_lineage_changes(lineage, {
            "program_operations": [{"op": "add", "kind": "component", "value": event}],
            "resident_ids": [worker],
            "dry_run": dry_run,
        }, ACTOR, include_lineage=False, authorize=authorize)
    assert graph(lineage) == before
    assert resident_catalog.get_catalog().get(worker)["lineage_id"] == lineage


def test_lineage_edit_checks_each_graph_action_and_commits_one_revision(lineage):
    worker = resident(lineage, "Worker")
    original = "event_source_ffaabb001122"
    clone = "event_source_ffaabb001123"
    wire = "wire_ffaabb001122"
    before = graph(lineage)
    operations = [
        {"op": "add", "kind": "component", "value": {
            "id": original, "kind": "event_source", "config": {"event_type": "manual.triggered"},
        }},
        {"op": "clone", "kind": "component", "id": original, "new_id": clone},
        {"op": "set", "kind": "component", "id": clone, "field": "title", "value": "Cloned event"},
        {"op": "add", "kind": "wire", "value": {
            "id": wire, "kind": "route", "source": clone, "target": worker,
        }},
        {"op": "set", "kind": "wire", "id": wire, "field": "label", "value": "Notify"},
        {"op": "remove", "kind": "wire", "id": wire},
        {"op": "remove", "kind": "component", "id": original},
    ]
    requested = []
    result = lineage_graph_edits.apply_lineage_changes(
        lineage, {"program_operations": operations}, ACTOR, include_lineage=False, authorize=requested.extend,
    )
    assert requested == [
        (action, lineage) for action in (
            "create_component", "update_component", "create_wire", "update_wire", "delete_wire", "delete_component",
        )
    ]
    assert result["revision"] == before["revision"] + 1
    saved = graph(lineage)["program"]
    assert [item["id"] for item in saved["components"]] == [clone]
    assert saved["components"][0]["title"] == "Cloned event"
    assert saved["wires"] == []


def test_dry_run_rejects_a_missing_resident_instead_of_previewing_a_delete(lineage):
    before = graph(lineage)
    missing = "resident_ffaabb001125"
    with pytest.raises(ValueError, match="Resident 不属于这个 Lineage"):
        lineage_graph_edits.apply_lineage_changes(lineage, {
            "resident_ids": [missing],
            "dry_run": True,
        }, ACTOR, include_lineage=False)
    assert graph(lineage) == before


def test_resident_deletion_can_reconcile_its_original_tombstone_without_a_graph_write(lineage, monkeypatch):
    from management_portal.society_service import resident_workspace_domain

    resident(lineage, "Retained")
    removed = resident(lineage, "Removed")
    plan = {"resident_ids": [removed]}
    expected = (("delete_resident", lineage), ("delete_resident", removed))
    calls = []
    authorize = lambda requirements: calls.append(requirements)
    deleted = lineage_graph_edits.apply_lineage_changes(lineage, plan, ACTOR, authorize=authorize)
    assert deleted["deleted_ids"] == [removed]
    baseline = graph(lineage)
    repairs = []
    monkeypatch.setattr(resident_workspace_domain, "retry_deleted_resident", lambda *args: repairs.append(args))
    preview = lineage_graph_edits.apply_lineage_changes(lineage, {**plan, "dry_run": True}, ACTOR, authorize=authorize)
    assert preview["revision"] == baseline["revision"] and not repairs
    retry = lineage_graph_edits.apply_lineage_changes(lineage, plan, ACTOR, authorize=authorize)
    assert calls == [expected, expected, expected]
    assert repairs == [(removed, ACTOR)]
    assert retry["deleted_ids"] == [removed] and graph(lineage) == baseline


def test_duty_edit_retains_four_levels_and_the_same_authority(lineage):
    worker = resident(lineage, "Worker")
    event_id = "event_source_ffaabb001122"
    wire_id = "wire_ffaabb001122"
    lineage_graph_edits.apply_lineage_changes(lineage, {"program_operations": [
        {"op": "add", "kind": "component", "value": {
            "id": event_id, "kind": "event_source", "config": {"event_type": "manual.triggered"},
        }},
        {"op": "add", "kind": "wire", "value": {
            "id": wire_id, "kind": "route", "source": event_id, "target": worker,
        }},
    ]}, ACTOR, include_lineage=False)
    duty = function_block_domain.functionalize(
        lineage, [event_id, wire_id], ACTOR, title="Duty",
        duty={"holder_id": worker, "description": "An editable duty"},
    )

    def authorize(requirements):
        assert requirements == (("update_duty", lineage),)

    for level in ("trigger", "turn", "session", "on_demand"):
        result = lineage_graph_edits.apply_lineage_changes(lineage, {
            "function_block_id": duty["id"],
            "duty_changes": {"memory_level": level, "prompt": "The duty prompt"},
        }, ACTOR, include_lineage=False, authorize=authorize)
        assert result["function_block"]["duty"]["memory"] == {
            "level": level, "prompt": "The duty prompt",
        }
    before = graph(lineage)
    with pytest.raises(ValueError, match="Duty memory level"):
        lineage_graph_edits.apply_lineage_changes(lineage, {
            "function_block_id": duty["id"],
            "duty_changes": {"memory_level": ""},
        }, ACTOR, include_lineage=False, authorize=authorize)
    assert graph(lineage) == before


@pytest.mark.parametrize("blocker", ["flows", "pending HATG work"])
def test_explicit_empty_source_move_keeps_single_resident_guards(lineage, monkeypatch, blocker):
    worker = resident(lineage, "Worker")
    target = lineage_lifecycle.create_lineage({"title": "Destination", "project_id": "project_test"}, ACTOR)["lineage_id"]
    if blocker == "flows":
        lineage_graph_edits.apply_lineage_changes(lineage, {"program_operations": [
            {"op": "add", "kind": "component", "value": {
                "id": "event_source_ffaabb001122", "kind": "event_source", "config": {"event_type": "manual.triggered"},
            }},
        ]}, ACTOR, include_lineage=False)
    else:
        monkeypatch.setattr(
            lineage_program_resident_inputs, "pending_resident_handoffs",
            lambda _lineage_id: [{"status": "queued", "resident_id": worker}],
        )
    before = [graph(lineage), graph(target)]
    with pytest.raises(ValueError, match=blocker):
        lineage_moves.move_lineage_subgraph(lineage, {
            "object_ids": [worker], "target_lineage_id": target, "allow_empty_source": True,
        }, ACTOR)
    assert [graph(lineage), graph(target)] == before
    assert resident_catalog.get_catalog().get(worker)["lineage_id"] == lineage


@pytest.mark.parametrize("existing_target", [False, True])
def test_explicit_empty_source_move_keeps_identity_and_standard_result(lineage, existing_target):
    worker = resident(lineage, "Worker")
    before = resident_catalog.get_catalog().get(worker)
    args = {"object_ids": [worker], "allow_empty_source": True}
    if existing_target:
        args["target_lineage_id"] = lineage_lifecycle.create_lineage({
            "title": "Destination", "project_id": "project_test",
        }, ACTOR)["lineage_id"]
    result = lineage_moves.move_lineage_subgraph(lineage, args, ACTOR)
    assert result["operation_id"]
    assert result["source_lineage_id"] == lineage
    assert result["target_lineage_id"] != lineage
    if existing_target:
        assert result["target_lineage_id"] == args["target_lineage_id"]
    assert result["detached_edges"] == []
    assert graph(lineage)["nodes"] == []
    assert graph(result["target_lineage_id"])["leader_resident_id"] == worker
    after = resident_catalog.get_catalog().get(worker)
    assert after["owned_workspace_id"] == before["owned_workspace_id"]
    assert after["lineage_id"] == result["target_lineage_id"]
