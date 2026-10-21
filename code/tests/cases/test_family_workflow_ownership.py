"""Family workflow ownership through real ISA handlers and isolated PostgreSQL.

:design: decision_44cf587c1721, decision_5b5653c3eaa5
"""

import copy

import pytest

from management_portal.society_service import family_familize
from tests.cases.isa_test_support import ok, run
from tests.cases.test_family_familize import charters  # noqa: F401 - fixture
from tests.cases.test_family_workflow_bindings import (
    NAME, flow_objects, install, target_of, workflows,  # noqa: F401 - fixture
)
from tests.cases.test_isa_lineage_lifecycle import (  # noqa: F401 - fixtures
    future_creation_store, graph, lineage, resident,
)


def _family(lineage_id, *members):
    return ok("Familize", {"lineage_id": lineage_id, "resident_ids": list(members)})


def _update(lineage_id, family, **changes):
    return ok("UpdateFamily", {"lineage_id": lineage_id, "family_id": family["family_id"], **changes})


def _block(view, family):
    return next(b for b in view["function_blocks"] if b["id"] == family["family_id"])


def _assert_owner(view, ids, family=None):
    for eid in ids:
        owners = [(b["id"], e) for b in view["function_blocks"] for e in b["elements"] if e["id"] == eid]
        if family is None:
            assert owners == [], (eid, owners)
        else:
            assert len(owners) == 1, (eid, owners)
            owner, element = owners[0]
            assert owner == family["family_id"]
            assert element == {"id": eid, "kind": "wire" if eid.startswith("wire_") else "component",
                               "key": "workflow:" + eid}


def _event(lineage_id, event_type="manual.triggered"):
    return ok("CreateComponent", {"lineage_id": lineage_id, "kind": "event_source",
                                  "config": {"event_type": event_type}})["component_id"]


def _wire(lineage_id, source, target, kind="route"):
    return ok("CreateWire", {"lineage_id": lineage_id, "kind": kind,
                             "source": source, "target": target})["wire_id"]


def _refused_without_changes(lineage_id, family, **changes):
    before = graph(lineage_id)
    result = run("UpdateFamily", {"lineage_id": lineage_id, "family_id": family["family_id"], **changes})
    assert result.error["code"] == "instruction.invalid_arguments", result
    after = graph(lineage_id)
    assert after["revision"] == before["revision"]
    assert after["program"] == before["program"]
    assert after["function_blocks"] == before["function_blocks"]


@pytest.mark.parametrize("nested", [False, True], ids=["root", "parent-family"])
def test_explicit_workflow_roundtrip_keeps_objects_and_binding(lineage, workflows, nested):
    """Family工作流显式收纳移出保留对象与绑定"""
    bindings, calls = workflows
    member = resident(lineage, "Module")
    family = _family(lineage, member)
    parent = _family(lineage, family["facade_resident_id"]) if nested else None
    binding = install(bindings, lineage, member)
    state = bindings.store.get(lineage, member, NAME)
    steps = state["steps"]
    before = flow_objects(graph(lineage), state)
    # Component selection includes ordinary internal wires without recreating them.
    _update(lineage, family, add_element_ids=[steps["event"], steps["effect"]])
    _assert_owner(graph(lineage), steps.values(), family)
    assert flow_objects(graph(lineage), state) == before
    # Removing a component also releases its directly owned incident wires.
    _update(lineage, family, remove_element_ids=[steps["event"]])
    view = graph(lineage)
    _assert_owner(view, [steps["event"], steps["invoke"]], parent)
    _assert_owner(view, [steps["effect"], steps["target"]], family)
    _update(lineage, family, remove_element_ids=[steps["effect"]])
    _assert_owner(graph(lineage), steps.values(), parent)
    assert flow_objects(graph(lineage), state) == before
    calls.clear()
    assert install(bindings, lineage, member) == binding
    assert calls == ["GetLineage"]
    assert bindings.store.get(lineage, member, NAME) == state


def test_child_can_collect_ancestor_workflow_without_duplicate_ownership(lineage, workflows):
    """子Family从祖先收纳工作流保持唯一归属"""
    bindings, _calls = workflows
    member = resident(lineage, "Descendant module")
    child = _family(lineage, member)
    parent = _family(lineage, child["facade_resident_id"])
    install(bindings, lineage, member)
    state = bindings.store.get(lineage, member, NAME)
    steps = state["steps"]
    before = flow_objects(graph(lineage), state)
    _update(lineage, parent, add_element_ids=list(steps.values()))
    _assert_owner(graph(lineage), steps.values(), parent)
    _update(lineage, child, add_element_ids=[steps["event"], steps["effect"]])
    _assert_owner(graph(lineage), steps.values(), child)
    assert flow_objects(graph(lineage), state) == before
    _update(lineage, child, remove_element_ids=list(steps.values()))
    _assert_owner(graph(lineage), steps.values(), parent)
    assert flow_objects(graph(lineage), state) == before


def test_ownership_only_update_preserves_generated_route_configuration(lineage, charters):
    """纯Family归属更新保留生成路由配置

    只增删工作流归属时，原Program配置与Router成员顺序均保持不变。
    """
    members = [resident(lineage, "First"), resident(lineage, "Second")]
    family = _family(lineage, *members)
    route = next(w for w in graph(lineage)["program"]["wires"]
                 if "members" in w.get("config", {}).get("invoke_profiles", {}).get("effect", {}).get("arguments", {}))
    config = copy.deepcopy(route["config"])
    # Existing generated configuration may differ from the normalized roster
    # order. Changing only workflow ownership must not regenerate that route.
    config["invoke_profiles"]["effect"]["arguments"]["members"]["value"] = sorted(members, reverse=True)
    ok("UpdateWire", {"lineage_id": lineage, "wire_id": route["id"], "changes": {"config": config}})
    event = _event(lineage)
    before = graph(lineage)["program"]
    for changes in ({"add_element_ids": [event]}, {"remove_element_ids": [event]}):
        _update(lineage, family, **changes)
        assert graph(lineage)["program"] == before


@pytest.mark.parametrize("operation", ["add_element_ids", "remove_element_ids"])
@pytest.mark.parametrize("selection", ["generated-component", "generated-wire", "member", "family", "missing"])
def test_generated_and_non_workflow_elements_cannot_be_edited(lineage, charters, operation, selection):
    """Family拒绝编辑生成结构和非工作流元素"""
    member = resident(lineage, "Member")
    family = _family(lineage, member)
    elements = _block(graph(lineage), family)["elements"]
    candidates = {
        "generated-component": next(e["id"] for e in elements if e["kind"] == "component"),
        "generated-wire": next(e["id"] for e in elements if e["kind"] == "wire"),
        "member": member, "family": family["family_id"], "missing": "event_source_000000000000",
    }
    _refused_without_changes(lineage, family, **{operation: [candidates[selection]]})


@pytest.mark.parametrize("owner_kind", ["sibling-family", "ordinary-block"])
def test_workflow_cannot_be_taken_from_another_container(lineage, charters, owner_kind):
    """Family拒绝抢占其他容器的工作流"""
    member, sibling_member = resident(lineage, "Member"), resident(lineage, "Sibling")
    family = _family(lineage, member)
    sibling = _family(lineage, sibling_member) if owner_kind == "sibling-family" else None
    source = _event(lineage)
    if sibling:
        _update(lineage, sibling, add_element_ids=[source])
    else:
        ok("Functionalize", {"lineage_id": lineage, "element_ids": [source]})
    _refused_without_changes(lineage, family, add_element_ids=[source])
    _refused_without_changes(lineage, family, remove_element_ids=[source])


def test_explicit_wire_requires_internal_endpoints_and_failed_batch_is_atomic(lineage, charters):
    """Family收纳连线校验内部端点并原子拒绝失败批次"""
    member, outsider = resident(lineage, "Member"), resident(lineage, "Outsider")
    family = _family(lineage, member)
    source = _event(lineage)
    internal = _wire(lineage, source, member)
    external = _wire(lineage, source, outsider)
    _refused_without_changes(lineage, family, add_element_ids=[internal])
    _refused_without_changes(lineage, family, add_element_ids=[source, external])
    _assert_owner(graph(lineage), [source, internal, external])
    _update(lineage, family, add_element_ids=[source, internal])
    _assert_owner(graph(lineage), [source, internal], family)
    _assert_owner(graph(lineage), [external])
    _refused_without_changes(lineage, family, add_element_ids=[source, source])
    _refused_without_changes(lineage, family, add_element_ids=[source], remove_element_ids=[source])


@pytest.mark.parametrize("joining", [False, True], ids=["familize", "add-member"])
def test_auto_collection_selects_exclusive_groups_and_keeps_shared_branch_outside(lineage, workflows, joining):
    """Family自动收纳专属流程并保留共享入口"""
    bindings, _calls = workflows
    member, outsider = resident(lineage, "Module"), resident(lineage, "Outside observer")
    family = _family(lineage, resident(lineage, "Existing member")) if joining else None
    install(bindings, lineage, member)
    state = bindings.store.get(lineage, member, NAME)
    steps = state["steps"]
    # A completion watcher is an ordinary component, even though its target is observed.
    watcher = _event(lineage, "resident.work_finished")
    watched = _wire(lineage, watcher, member, "watch")
    followup = _wire(lineage, watcher, steps["effect"])
    shared = _event(lineage)
    inside_branch = _wire(lineage, shared, member)
    outside_branch = _wire(lineage, shared, outsider)
    unattached = _event(lineage)
    before = flow_objects(graph(lineage), state)
    if joining:
        _update(lineage, family, add_member_ids=[member])
    else:
        family = _family(lineage, member)
    view = graph(lineage)
    _assert_owner(view, [*steps.values(), watcher, watched, followup], family)
    _assert_owner(view, [shared, inside_branch, outside_branch, unattached])
    assert flow_objects(view, state) == before
    assert target_of(view, inside_branch) == family["facade_resident_id"]
    assert target_of(view, outside_branch) == outsider
    assert target_of(view, watched) == member
    if not joining:
        created = set(family["created"]["component_ids"] + family["created"]["wire_ids"])
        assert not created.intersection([*steps.values(), watcher, watched, followup])
        generated = {e["id"] for e in _block(view, family)["elements"]
                     if e["kind"] in {"component", "wire"} and not e["key"].startswith("workflow:")}
        assert created == generated


def test_recursive_resident_boundary_collects_workflow_without_stealing_child_structure(lineage, workflows):
    """递归成员范围收纳工作流且不抢子Family结构"""
    bindings, _calls = workflows
    member = resident(lineage, "Nested member")
    child = _family(lineage, member)
    child_before = _block(graph(lineage), child)
    install(bindings, lineage, member)
    state = bindings.store.get(lineage, member, NAME)
    before = flow_objects(graph(lineage), state)
    parent = _family(lineage, child["facade_resident_id"])
    view = graph(lineage)
    _assert_owner(view, state["steps"].values(), parent)
    assert _block(view, child)["elements"] == child_before["elements"]
    assert flow_objects(view, state) == before


def test_auto_collection_respects_wire_owned_by_another_block(lineage, workflows):
    """Family工作流所有权冲突边界"""
    bindings, _calls = workflows
    member = resident(lineage, "Module with externally owned invoke")
    install(bindings, lineage, member)
    state = bindings.store.get(lineage, member, NAME)
    steps = state["steps"]
    other = ok("Functionalize", {"lineage_id": lineage, "element_ids": [steps["invoke"]]})
    before = graph(lineage)
    other_block = next(b for b in before["function_blocks"] if b["id"] == other["function_block_id"])
    family = _family(lineage, member)
    view = graph(lineage)
    # An external owner of only the connecting wire still owns the group boundary.
    _assert_owner(view, [steps["event"], steps["effect"], steps["target"]])
    assert next(b for b in view["function_blocks"] if b["id"] == other_block["id"])["elements"] == other_block["elements"]
    assert target_of(view, steps["invoke"]) == steps["effect"]
    assert target_of(view, steps["target"]) == family["facade_resident_id"]
    family_ids = {e["id"] for e in _block(view, family)["elements"]}
    assert not family_ids.intersection(steps.values())


def test_member_reconciliation_keeps_business_ids_and_retires_only_generated_watch(lineage, workflows):
    """名册更新保留业务身份并仅清理生成监听"""
    bindings, _calls = workflows
    member, joining = resident(lineage, "Module"), resident(lineage, "Joining")
    install(bindings, lineage, member)
    state = bindings.store.get(lineage, member, NAME)
    before = flow_objects(graph(lineage), state)
    family = _family(lineage, member)
    _update(lineage, family, add_member_ids=[joining], goal="Keep current work")
    added = _block(graph(lineage), family)
    added_keys = {e["key"]: e["id"] for e in added["elements"]}
    watched = added_keys["watch_member_done:" + joining]
    _update(lineage, family, remove_member_ids=[joining], route_threshold=75)
    view = graph(lineage)
    final_keys = {e["key"]: e["id"] for e in _block(view, family)["elements"]}
    assert watched not in {w["id"] for w in view["program"]["wires"]}
    assert final_keys["watch_member_done:" + member] == added_keys["watch_member_done:" + member]
    assert final_keys["router"] == added_keys["router"]
    _assert_owner(view, state["steps"].values(), family)
    assert flow_objects(view, state) == before


def test_removal_is_not_undone_by_parameter_update_or_existing_member_add(lineage, charters):
    """Family参数更新不收回已显式移出的流程"""
    member = resident(lineage, "Member")
    source = _event(lineage)
    wire = _wire(lineage, source, member)
    family = _family(lineage, member)
    _update(lineage, family, remove_element_ids=[wire])
    _assert_owner(graph(lineage), [source], family)
    _assert_owner(graph(lineage), [wire])
    _update(lineage, family, goal="Only update metadata", add_member_ids=[member])
    _assert_owner(graph(lineage), [wire])
    _update(lineage, family, remove_element_ids=[source])
    _update(lineage, family, review_interval_seconds=600)
    view = graph(lineage)
    _assert_owner(view, [source, wire])
    assert target_of(view, wire) == member


@pytest.mark.parametrize("nested", [False, True], ids=["ungroup", "return-to-parent"])
def test_unfamilize_preserves_workflows_and_moves_ownership_to_parent(lineage, workflows, nested):
    """解除Family保留普通工作流并提升至父容器"""
    bindings, calls = workflows
    member = resident(lineage, "Retained executor")
    binding = install(bindings, lineage, member)
    state = bindings.store.get(lineage, member, NAME)
    before = flow_objects(graph(lineage), state)
    family = _family(lineage, member)
    parent = _family(lineage, family["facade_resident_id"]) if nested else None
    generated = {e["id"] for e in _block(graph(lineage), family)["elements"]
                 if e["kind"] in {"component", "wire"} and not e["key"].startswith("workflow:")}
    ok("Unfamilize", {"lineage_id": lineage, "family_id": family["family_id"],
                      "rewire_to_resident_id": member})
    view = graph(lineage)
    _assert_owner(view, state["steps"].values(), parent)
    assert flow_objects(view, state) == before
    assert not generated.intersection(c["id"] for c in view["program"]["components"])
    assert not generated.intersection(w["id"] for w in view["program"]["wires"])
    assert family["family_id"] not in {b["id"] for b in view["function_blocks"]}
    calls.clear()
    assert install(bindings, lineage, member) == binding
    assert calls == ["GetLineage"]


def test_adopted_effect_does_not_acquire_generated_family_identity(lineage, workflows):
    """收纳Effect不赋予Family内部身份"""
    bindings, _calls = workflows
    member = resident(lineage, "Module")
    install(bindings, lineage, member)
    state = bindings.store.get(lineage, member, NAME)
    family = _family(lineage, member)
    view = graph(lineage)
    generated_router = next(e["id"] for e in _block(view, family)["elements"] if e["key"] == "router")
    ordinary_effect = state["steps"]["effect"]
    _assert_owner(view, [ordinary_effect], family)
    facade, router = family["facade_resident_id"], family["router_resident_id"]
    assert family_familize.facade_for_internal_note(view, ordinary_effect, router) == ""
    assert family_familize.facade_for_family_state(view, ordinary_effect, "family_corrections_" + facade) == ""
    assert family_familize.facade_for_internal_note(view, generated_router, router) == facade
    assert family_familize.facade_for_family_state(view, generated_router, "family_corrections_" + facade) == facade
