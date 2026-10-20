"""Family lifecycle through the public ISA and typed element membership."""
import pytest
from tests.cases.isa_test_support import ok, run
from tests.cases.test_isa_lineage_lifecycle import future_creation_store, graph, lineage, resident
from tests.cases.test_family_familize import charters


def test_delete_family_removes_nested_contents_but_keeps_outside_objects(lineage, charters):
    """删除 Family 递归清除嵌套内容并保留外部对象

    :module: lineage.hatg"""
    from management_portal.society_service.resident_catalog import get_catalog

    first, second, outside = (resident(lineage, title) for title in ("First", "Second", "Outside"))
    ok("UpdateLineage", {"lineage_id": lineage, "changes": {"leader_resident_id": first}})
    inner = ok("Familize", {"lineage_id": lineage, "resident_ids": [first]})
    outer = ok("Familize", {"lineage_id": lineage, "resident_ids": [inner["facade_resident_id"], second]})
    source = ok("CreateComponent", {"lineage_id": lineage, "kind": "event_source",
        "config": {"event_type": "manual.triggered"}})["component_id"]
    owned = ok("CreateComponent", {"lineage_id": lineage, "kind": "event_source",
        "config": {"event_type": "manual.triggered"}})["component_id"]
    ok("UpdateFamily", {"lineage_id": lineage, "family_id": inner["family_id"], "add_element_ids": [owned]})
    external_wire = ok("CreateWire", {"lineage_id": lineage, "kind": "route", "source": source,
        "target": outer["facade_resident_id"]})["wire_id"]
    result = ok("DeleteFamily", {"lineage_id": lineage, "family_id": outer["resource_id"]})
    view = graph(lineage)
    expected = {first, second} | {f[key] for f in (inner, outer)
        for key in ("facade_resident_id", "steward_resident_id", "router_resident_id")}
    assert set(result["deleted_resident_ids"]) == expected
    assert all(get_catalog().get_historical(rid)["deleted_at"] for rid in expected)
    assert {n["id"] for n in view["nodes"]} == {outside}
    assert view["leader_resident_id"] == outside
    assert not view["function_blocks"]
    assert {c["id"] for c in view["program"]["components"]} == {source}
    assert not any(w["id"] == external_wire for w in view["program"]["wires"])


def test_delete_nested_family_keeps_parent_and_siblings(lineage, charters):
    """删除嵌套 Family 保留父 Family 与其他成员

    :module: lineage.hatg"""
    first, second = resident(lineage, "First"), resident(lineage, "Second")
    inner = ok("Familize", {"lineage_id": lineage, "resident_ids": [first]})
    outer = ok("Familize", {"lineage_id": lineage, "resident_ids": [inner["facade_resident_id"], second]})
    ok("DeleteFamily", {"lineage_id": lineage, "family_id": inner["family_id"]})
    view = graph(lineage)
    assert [b["id"] for b in view["function_blocks"]] == [outer["family_id"]]
    assert view["function_blocks"][0]["family"]["members"] == [second]
    assert {n["id"] for n in view["nodes"]} == {second, *(outer[k] for k in
        ("facade_resident_id", "steward_resident_id", "router_resident_id"))}


def test_delete_family_waits_for_members_and_retries_without_partial_deletion(lineage, charters, monkeypatch):
    """删除 Family 等待成员停止且冲突时不部分删除

    :module: lineage.hatg"""
    from management_portal.society_service import lineage_domain, resident_lifecycle_domain
    from management_portal.society_service.templates import familize

    member = resident(lineage, "Busy member")
    family = ok("Familize", {"lineage_id": lineage, "resident_ids": [member]})
    before = graph(lineage)
    busy, stopped = {member}, set()
    monkeypatch.setattr(lineage_domain, "quiesce_lineage_resident", lambda _l, p, _a: stopped.add(p["target"]))
    monkeypatch.setattr(resident_lifecycle_domain, "_resident_has_pending_work", lambda rid: rid in busy)
    monkeypatch.setattr(familize, "ROLE_STOP_WAIT_SECONDS", 0)
    result = run("DeleteFamily", {"lineage_id": lineage, "family_id": family["family_id"]})
    assert result.error["code"] == "instruction.conflict" and result.error["retryable"]
    assert graph(lineage)["function_blocks"] == before["function_blocks"]
    assert {n["id"] for n in graph(lineage)["nodes"]} == stopped
    busy.clear()
    ok("DeleteFamily", {"lineage_id": lineage, "family_id": family["family_id"]})
    assert not graph(lineage)["nodes"]


def test_delete_family_aborts_if_contents_change_while_stopping(lineage, charters, monkeypatch):
    """删除 Family 在停止期间内容变化时返回冲突

    :module: lineage.hatg"""
    from management_portal.society_service import lineage_domain

    first, added = resident(lineage, "First"), resident(lineage, "Added while stopping")
    family = ok("Familize", {"lineage_id": lineage, "resident_ids": [first]})
    changed = []

    def quiesce(*_args):
        if not changed:
            changed.append(True)
            ok("UpdateFamily", {"lineage_id": lineage, "family_id": family["family_id"], "add_member_ids": [added]})
    monkeypatch.setattr(lineage_domain, "quiesce_lineage_resident", quiesce)
    result = run("DeleteFamily", {"lineage_id": lineage, "family_id": family["family_id"]})
    assert result.error["code"] == "instruction.conflict"
    view = graph(lineage)
    assert set(view["function_blocks"][0]["family"]["members"]) == {first, added}
    assert len(view["nodes"]) == 5


def test_family_changes_roster_and_preserves_nested_block(lineage, charters):
    """Family 成员变更和解散保留嵌套功能块"""
    first, second = resident(lineage, "First"), resident(lineage, "Second")
    inner = ok("Familize", {"lineage_id": lineage, "resident_ids": [first]})
    outer = ok("Familize", {"lineage_id": lineage, "resident_ids": [inner["facade_resident_id"], second]})
    block = next(b for b in graph(lineage)["function_blocks"] if b["id"] == outer["family_id"])
    assert {"id": inner["family_id"], "kind": "function_block", "key": "member:" + inner["facade_resident_id"]} in block["elements"]
    ok("UpdateFamily", {"lineage_id": lineage, "family_id": outer["family_id"], "remove_member_ids": [second], "route_threshold": 75})
    view = graph(lineage)
    block = next(b for b in view["function_blocks"] if b["id"] == outer["family_id"])
    assert block["family"]["members"] == [inner["facade_resident_id"]]
    assert block["family"]["route_threshold"] == 75
    assert second in {n["id"] for n in view["nodes"]}
    assert not any(e.get("key", "").endswith(":" + second) for e in block["elements"])
    ok("Unfamilize", {"lineage_id": lineage, "family_id": outer["family_id"]})
    assert [b["id"] for b in graph(lineage)["function_blocks"]] == [inner["family_id"]]


def test_external_wire_in_another_block_rewires_and_new_member_joins(lineage, charters):
    """Family 重接外部连线并为新成员补齐监听连线"""
    first, second = resident(lineage, "First"), resident(lineage, "Second")
    source = ok("CreateComponent", {"lineage_id": lineage, "kind": "event_source", "config": {"event_type": "manual.triggered"}})["component_id"]
    wire = ok("CreateWire", {"lineage_id": lineage, "kind": "route", "source": source, "target": first})["wire_id"]
    other = ok("Functionalize", {"lineage_id": lineage, "element_ids": [source, wire]})
    family = ok("Familize", {"lineage_id": lineage, "resident_ids": [first, second]})
    assert next(w for w in graph(lineage)["program"]["wires"] if w["id"] == wire)["target"] == family["facade_resident_id"]
    added = ok("CreateResident", {"lineage_id": lineage, "family_id": family["family_id"], "title": "New member"})["resident_id"]
    block = next(b for b in graph(lineage)["function_blocks"] if b["id"] == family["family_id"])
    assert added in block["family"]["members"]
    def member_listeners(resident_id):
        return {e["key"].split(":")[0] for e in block["elements"]
                if e.get("key", "").endswith(":" + resident_id) and e["kind"] == "wire"}
    # New members receive the same monitoring as existing members, including
    # recovery-arrival listeners added to the current Family template.
    assert {"watch_member_queue", "watch_member_done", "watch_member_failed",
            "watch_recovery_finished", "watch_recovery_arrival"} <= member_listeners(first)
    assert member_listeners(added) == member_listeners(first)
    from management_portal.society_service.resident_record_domain import _resident_owned_workspace_path
    from management_portal.society_service.resident_workspace_domain import _find_resident
    guide_path = _resident_owned_workspace_path(_find_resident(charters[0][0])) / "family-work-guide.md"
    assert str(guide_path) in charters[0][1]
    guide = guide_path.read_text(encoding="utf-8")
    assert "UpdateFamily" in guide and "UpdateFunctionBlockInstance" not in guide
    ok("Unfamilize", {"lineage_id": lineage, "family_id": family["family_id"], "rewire_to_resident_id": first})
    view = graph(lineage)
    assert next(w for w in view["program"]["wires"] if w["id"] == wire)["target"] == first
    assert any(e["id"] == wire for b in view["function_blocks"] if b["id"] == other["function_block_id"] for e in b["elements"])


def test_familize_template_and_update_keep_generated_identity(lineage, charters):
    """Family 模板更新保留生成元素标识"""
    first, second = resident(lineage, "First"), resident(lineage, "Second")
    template = next(t for t in ok("ListTemplates", {})["templates"] if t.get("builtin_key") == "family")
    applied = ok("ApplyTemplate", {"lineage_id": lineage, "template_id": template["template_id"],
                                   "target": {"kind": "resident", "resident_ids": [first]}})
    family_id = applied["family_id"]
    before = next(b for b in graph(lineage)["function_blocks"] if b["id"] == family_id)
    updated = ok("UpdateFamily", {"lineage_id": lineage, "family_id": family_id,
                                  "add_member_ids": [second], "review_interval_seconds": 600})["function_block"]
    keys = {e["key"]: e["id"] for e in before["elements"]}
    assert all(e["id"] == keys[e["key"]] for e in updated["elements"] if e["key"] in keys)
    timer = next(c for c in graph(lineage)["program"]["components"] if c["id"] == keys["periodic_review"])
    assert timer["config"]["interval_seconds"] == 600
    count = len(graph(lineage)["nodes"])
    refused = run("UpdateFamily", {"lineage_id": lineage, "family_id": family_id,
                                   "add_member_ids": [applied["facade_resident_id"]]})
    assert refused.error["code"] == "instruction.invalid_arguments"
    assert len(graph(lineage)["nodes"]) == count


def test_familize_template_accepts_mixed_selection(lineage, charters):
    """Family 模板接受成员、嵌套块和组件混合选择"""
    first, second = resident(lineage, "First"), resident(lineage, "Second")
    inner = ok("Familize", {"lineage_id": lineage, "resident_ids": [first]})
    source = ok("CreateComponent", {"lineage_id": lineage, "kind": "event_source",
                                    "config": {"event_type": "manual.triggered"}})["component_id"]
    wire = ok("CreateWire", {"lineage_id": lineage, "kind": "route", "source": source,
                             "target": second})["wire_id"]
    template = next(t for t in ok("ListTemplates", {})["templates"] if t.get("builtin_key") == "family")
    applied = ok("ApplyTemplate", {"lineage_id": lineage, "template_id": template["template_id"],
        "target": {"kind": "selection", "resident_ids": [second],
                   "element_ids": [second, inner["family_id"], source, wire]}})
    view = graph(lineage)
    outer = next(b for b in view["function_blocks"] if b["id"] == applied["family_id"])
    assert set(outer["family"]["members"]) == {second, inner["facade_resident_id"]}
    elements = {e["id"] for e in outer["elements"]}
    assert inner["family_id"] in elements
    assert {source, wire} <= elements
    assert next(w for w in view["program"]["wires"] if w["id"] == wire)["target"] == second


def test_unfamilize_nested_family_restores_outer_leader_successor(lineage, charters):
    """解散嵌套 Family 后外层保留原 leader 继任者"""
    first, second = resident(lineage, "First"), resident(lineage, "Second")
    inner = ok("Familize", {"lineage_id": lineage, "resident_ids": [first]})
    outer = ok("Familize", {"lineage_id": lineage, "resident_ids": [inner["facade_resident_id"], second]})
    ok("Unfamilize", {"lineage_id": lineage, "family_id": inner["family_id"], "rewire_to_resident_id": first})
    remaining = next(b for b in graph(lineage)["function_blocks"] if b["id"] == outer["family_id"])
    assert remaining["family"]["members"] == sorted([first, second])
    assert remaining["family"]["previous_leader_id"] == first
    assert any(e["id"] == first and e["kind"] == "resident" for e in remaining["elements"])
    ok("Unfamilize", {"lineage_id": lineage, "family_id": outer["family_id"]})
    assert graph(lineage)["leader_resident_id"] == first
