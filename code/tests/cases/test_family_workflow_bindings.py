"""Family boundaries preserve formal workflows and their durable migration binding."""

from copy import deepcopy

import pytest

from management_portal import isa_client
from management_portal.lineage_workflows import WorkflowBindings
from management_portal.workflow_bindings_store import WorkflowBindingConflict
from tests.cases.isa_test_support import ACTOR, ok
from tests.cases.test_family_familize import charters  # noqa: F401 - fixture
from tests.cases.test_isa_lineage_lifecycle import (  # noqa: F401 - fixtures
    future_creation_store,
    graph,
    lineage,
    resident,
)


NAME = "tau:module"
PROMPT = "Verify the assigned module without starting another Campaign."


@pytest.fixture
def workflows(lineage, charters, monkeypatch):
    """Use real ISA handlers, graph storage and PostgreSQL binding storage."""
    calls = []
    original = isa_client.call

    def tracked(actor, instruction, arguments=None, **options):
        assert instruction != "TriggerEvent", "install/follow must not execute a workflow"
        calls.append(instruction)
        return original(actor, instruction, arguments, **options)

    monkeypatch.setattr(isa_client, "call", tracked)
    return WorkflowBindings(), calls


def install(bindings, lineage_id, resident_id):
    return bindings.install(actor=ACTOR, lineage_id=lineage_id, resident_id=resident_id,
                            name=NAME, title="Module verification", prompt=PROMPT)


def flow_objects(view, state):
    """Compare business identity and contracts, independent of layout/runtime views."""
    components = {c["id"]: {k: c[k] for k in ("id", "kind", "config")}
                  for c in view["program"]["components"] if c["id"] in state["steps"].values()}
    wires = {w["id"]: w for w in view["program"]["wires"]
             if w["id"] in state["steps"].values()}
    return deepcopy({"components": components, "wires": wires})


def target_of(view, wire_id):
    return next(w["target"] for w in view["program"]["wires"] if w["id"] == wire_id)


def move_flow(source, target, member, state):
    result = ok("MoveLineageObjects", {
        "lineage_id": source, "target_lineage_id": target,
        "object_ids": [member, state["steps"]["event"], state["steps"]["effect"]],
    })
    assert result["detached"] == []
    assert member not in {n["id"] for n in graph(source)["nodes"]}
    assert member in {n["id"] for n in graph(target)["nodes"]}


def new_target():
    return ok("CreateLineage", {"project_id": "project_test", "title": "Migrated team"})["lineage_id"]


def test_lineage_family_creation_preserves_workflow_and_direct_route(lineage, workflows):
    """整范围 Family 保留内部工作流与直接成员路由"""
    bindings, calls = workflows
    member = resident(lineage, "Module")
    binding = install(bindings, lineage, member)
    state = bindings.store.get(lineage, member, NAME)
    direct = ok("CreateWire", {"lineage_id": lineage, "kind": "route",
                              "source": state["steps"]["event"], "target": member})["wire_id"]
    before = flow_objects(graph(lineage), state)
    family = ok("Familize", {"lineage_id": lineage, "scope": "lineage"})
    current = graph(lineage)
    assert family["rewired_wire_ids"] == []
    assert member in family["member_resident_ids"]
    assert flow_objects(current, state) == before
    assert target_of(current, direct) == member
    calls.clear()
    assert install(bindings, lineage, member) == binding
    assert calls == ["GetLineage"]
    assert bindings.store.get(lineage, member, NAME) == state


@pytest.mark.parametrize("follow_directly", [False, True], ids=["install", "follow-store"])
@pytest.mark.parametrize("existing_family", [False, True], ids=["family-after-move", "join-family"])
def test_moved_workflow_joins_lineage_family_and_only_rebinds_cache(
    lineage, workflows, follow_directly, existing_family,
):
    """迁移工作流加入 Family 后只更新绑定归属"""
    bindings, calls = workflows
    member = resident(lineage, "Moved module")
    binding = install(bindings, lineage, member)
    state = bindings.store.get(lineage, member, NAME)
    direct = ok("CreateWire", {"lineage_id": lineage, "kind": "route",
                              "source": state["steps"]["event"], "target": member})["wire_id"]
    before = flow_objects(graph(lineage), state)
    target = new_target()
    if existing_family:
        family = ok("Familize", {"lineage_id": target, "scope": "lineage"})
    move_flow(lineage, target, member, state)
    if existing_family:
        ok("UpdateFamily", {"lineage_id": target, "family_id": family["family_id"],
                            "add_member_ids": [member]})
    else:
        family = ok("Familize", {"lineage_id": target, "scope": "lineage"})
    current = graph(target)
    block = next(b for b in current["function_blocks"] if b["id"] == family["family_id"])
    assert member in block["family"]["members"]
    assert target_of(current, direct) == member
    assert flow_objects(current, state) == before
    assert not graph(lineage)["program"]["components"]
    assert not graph(lineage)["program"]["wires"]
    assert bindings.store.get(lineage, member, NAME) == state
    calls.clear()
    if follow_directly:
        bindings.store.follow_moved_resident(target, member, NAME, current)
        assert calls == []
    assert install(bindings, target, member) == {**binding, "lineage_id": target}
    assert set(calls) == {"GetLineage"}
    assert bindings.store.get(lineage, member, NAME) is None
    moved = bindings.store.get(target, member, NAME)
    assert moved == {**state, "lineage_id": target, "version": state["version"] + 1}
    assert install(bindings, target, member) == {**binding, "lineage_id": target}
    assert bindings.store.get(target, member, NAME) == moved
    assert graph(target)["revision"] == current["revision"]
    assert flow_objects(graph(target), moved) == before


@pytest.mark.parametrize("moved", [False, True], ids=["same-team", "moved-team"])
@pytest.mark.parametrize("damage", ["missing-target", "changed-target", "missing-invoke", "changed-invoke"])
def test_invalid_workflow_is_refused_without_recreation_or_cache_move(lineage, workflows, moved, damage):
    """工作流缺失或改接时拒绝重建和缓存迁移"""
    bindings, calls = workflows
    member = resident(lineage, "Module")
    install(bindings, lineage, member)
    state = bindings.store.get(lineage, member, NAME)
    target = new_target() if moved else lineage
    if moved:
        move_flow(lineage, target, member, state)
    other = resident(target, "Another module")
    label = damage.split("-")[1]
    wire_id = state["steps"][label]
    if damage.startswith("missing"):
        ok("DeleteWire", {"lineage_id": target, "wire_id": wire_id})
    else:
        ok("UpdateWire", {"lineage_id": target, "wire_id": wire_id,
                          "changes": {"target": other}})
    before = graph(target)
    calls.clear()
    error = WorkflowBindingConflict if moved else ValueError
    with pytest.raises(error, match="complete workflow|连接"):
        install(bindings, target, member)
    assert set(calls) == {"GetLineage"}
    assert bindings.store.get(lineage, member, NAME) == state
    if moved:
        assert bindings.store.get(target, member, NAME) is None
    assert graph(target)["revision"] == before["revision"]
    assert flow_objects(graph(target), state) == flow_objects(before, state)


def test_local_family_preserves_recursive_internal_source_and_redirects_external_workflow(lineage, workflows):
    """局部 Family 保留嵌套内部路由并重接外部入口"""
    bindings, _calls = workflows
    first, second = resident(lineage, "Inner member"), resident(lineage, "Outer member")
    inner = ok("Familize", {"lineage_id": lineage, "resident_ids": [first]})
    view = graph(lineage)
    inner_block = next(b for b in view["function_blocks"] if b["id"] == inner["family_id"])
    owned = {e["id"] for e in inner_block["elements"]}
    internal_event = next(c["id"] for c in view["program"]["components"]
                          if c["id"] in owned and c["kind"] == "event_source")
    route = ok("CreateWire", {"lineage_id": lineage, "kind": "route",
                             "source": internal_event, "target": second})["wire_id"]
    install(bindings, lineage, second)
    state = bindings.store.get(lineage, second, NAME)
    outsider = resident(lineage, "Shared workflow observer")
    ok("CreateWire", {"lineage_id": lineage, "kind": "route",
                      "source": state["steps"]["event"], "target": outsider})
    outer = ok("Familize", {"lineage_id": lineage,
                            "resident_ids": [inner["facade_resident_id"], second]})
    view = graph(lineage)
    assert target_of(view, route) == second
    assert target_of(view, state["steps"]["target"]) == outer["facade_resident_id"]
    assert target_of(view, state["steps"]["invoke"]) == state["steps"]["effect"]
    assert state["steps"]["target"] in outer["rewired_wire_ids"]
    assert route not in outer["rewired_wire_ids"]
    third = resident(lineage, "Joining member")
    install(bindings, lineage, third)
    joined = bindings.store.get(lineage, third, NAME)
    ok("CreateWire", {"lineage_id": lineage, "kind": "route",
                      "source": joined["steps"]["event"], "target": outsider})
    joined_route = ok("CreateWire", {"lineage_id": lineage, "kind": "route",
                                    "source": internal_event, "target": third})["wire_id"]
    ok("UpdateFamily", {"lineage_id": lineage, "family_id": outer["family_id"],
                        "add_member_ids": [third]})
    view = graph(lineage)
    assert target_of(view, joined_route) == third
    assert target_of(view, joined["steps"]["target"]) == outer["facade_resident_id"]


def test_unfamilize_retargets_internal_facade_reference_before_removal(lineage, workflows):
    """解除 Family 前改接保留的门面引用"""
    bindings, _calls = workflows
    member = resident(lineage, "Retained member")
    install(bindings, lineage, member)
    state = bindings.store.get(lineage, member, NAME)
    before = flow_objects(graph(lineage), state)
    family = ok("Familize", {"lineage_id": lineage, "scope": "lineage"})
    facade = family["facade_resident_id"]
    install(bindings, lineage, facade)
    facade_state = bindings.store.get(lineage, facade, NAME)
    direct = ok("CreateWire", {"lineage_id": lineage, "kind": "route",
                              "source": facade_state["steps"]["event"], "target": facade})["wire_id"]
    ok("Unfamilize", {"lineage_id": lineage, "family_id": family["family_id"],
                      "rewire_to_resident_id": member})
    view = graph(lineage)
    assert {n["id"] for n in view["nodes"]} == {member}
    assert flow_objects(view, state) == before
    assert target_of(view, facade_state["steps"]["target"]) == member
    assert target_of(view, direct) == member
    assert not any(w["source"] == facade or w["target"] == facade for w in view["program"]["wires"])
