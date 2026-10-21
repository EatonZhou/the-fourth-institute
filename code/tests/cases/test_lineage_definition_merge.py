"""A Lineage write applies its own changes onto the definition current at commit.

:importance: P1
"""
from __future__ import annotations

import copy

import pytest

from management_portal.society_service.lineage_definition_merge import (
    ConcurrentChangeConflict,
    merge_definition,
)


def definition(*node_ids, edges=(), leader="", title="Team", description="", wires=(), components=()):
    return {
        "lineage_id": "lineage_111111111111",
        "title": title,
        "description": description,
        "leader_resident_id": leader,
        "revision": 4,
        "updated_at": "2026-10-01T00:00:00Z",
        "nodes": [{"id": node_id, "title": node_id, "x": 1.0, "y": 2.0} for node_id in node_ids],
        "edges": [{"id": f"edge_{source}_{target}", "source": source, "target": target, "type": "inheritance"}
                  for source, target in edges],
        "program": {
            "components": [{"id": component_id, "kind": "event_source", "config": {}} for component_id in components],
            "wires": [{"id": f"wire_{source}_{target}", "kind": "invoke", "source": source, "target": target}
                      for source, target in wires],
        },
    }


def first_other(leader, removed, nodes):
    return next((node["id"] for node in nodes if node["id"] not in removed), "")


def merge(base, ours, current):
    return merge_definition(base, ours, current, successor=first_other)


def ids(value, key="nodes"):
    return [item["id"] for item in value.get(key) or []]


def test_writes_that_add_different_objects_both_land():
    """并发新增不同对象的写入都保留"""
    base = definition("resident_a", leader="resident_a")
    ours = definition("resident_a", "resident_b", leader="resident_a")
    current = definition("resident_a", "resident_c", leader="resident_a")
    assert ids(merge(base, ours, current)) == ["resident_a", "resident_c", "resident_b"]


def test_fields_merge_independently_and_the_later_value_wins_on_the_same_field():
    """不同字段独立合并，同一字段后提交者生效"""
    base = definition("resident_a", title="Team", description="old")
    ours = definition("resident_a", title="Ours", description="old")
    current = definition("resident_a", title="Team", description="theirs")
    merged = merge(base, ours, current)
    assert (merged["title"], merged["description"]) == ("Ours", "theirs")
    current["title"] = "Theirs"
    assert merge(base, ours, current)["title"] == "Ours"


def test_changing_an_object_another_write_deleted_fails():
    """修改已被并发删除的对象时合并失败"""
    base = definition("resident_a", "resident_b")
    ours = copy.deepcopy(base)
    ours["nodes"][1]["title"] = "renamed"
    current = definition("resident_a")
    with pytest.raises(ConcurrentChangeConflict, match="resident_b"):
        merge(base, ours, current)


def test_referring_to_an_object_another_write_deleted_fails():
    """引用已被并发删除的对象时合并失败"""
    base = definition("resident_a", "resident_b")
    ours = definition("resident_a", "resident_b", edges=[("resident_a", "resident_b")])
    current = definition("resident_a")
    with pytest.raises(ConcurrentChangeConflict, match="edge_resident_a_resident_b"):
        merge(base, ours, current)


def test_a_wire_to_a_component_another_write_deleted_fails():
    """连到已被并发删除组件的连线使写入失败"""
    base = definition("resident_a", components=["component_1"])
    ours = definition("resident_a", components=["component_1"], wires=[("component_1", "resident_a")])
    current = definition("resident_a")
    with pytest.raises(ConcurrentChangeConflict, match="wire_component_1_resident_a"):
        merge(base, ours, current)


def test_grouping_an_element_another_write_deleted_fails():
    """把已被并发删除的元素编入功能块时报 function_blocks 冲突

    :importance: P2"""
    base = definition('resident_a')
    ours = copy.deepcopy(base)
    ours['function_blocks'] = [{'id':'fbi_000000000001', 'elements':[
        {'id':'resident_a', 'kind':'resident', 'key':None}]}]
    current = definition()
    with pytest.raises(ConcurrentChangeConflict, match='function_blocks'):
        merge(base, ours, current)


def test_deleting_an_element_prunes_a_concurrently_created_boundary():
    """删除元素时也把它从并发新建的功能块中剔除

    :importance: P2"""
    base = definition('resident_a', 'resident_b')
    ours = definition('resident_b')
    current = copy.deepcopy(base)
    current['function_blocks'] = [{'id':'fbi_000000000001', 'elements':[
        {'id':'resident_a', 'kind':'resident', 'key':None},
        {'id':'resident_b', 'kind':'resident', 'key':None}]}]
    assert merge(base, ours, current)['function_blocks'][0]['elements'] == [
        {'id':'resident_b', 'kind':'resident', 'key':None}]


def test_deleting_an_object_removes_references_another_write_added():
    """删除对象时一并移除并发写入新增的引用"""
    base = definition("resident_a", "resident_b")
    ours = definition("resident_a")
    current = definition("resident_a", "resident_b", edges=[("resident_a", "resident_b")],
                         components=["component_1"], wires=[("component_1", "resident_b")])
    merged = merge(base, ours, current)
    assert ids(merged) == ["resident_a"]
    assert merged.get("edges", []) == []
    assert merged["program"]["wires"] == []


def test_a_deleted_successor_is_replaced_without_failing_the_deletion():
    """删除 Leader 时选中的继任者被并发删除后重新选择"""
    base = definition("resident_a", "resident_b", "resident_c", leader="resident_a")
    ours = definition("resident_b", "resident_c", leader="resident_b")  # deleted the Leader, chose b
    current = definition("resident_a", "resident_c", leader="resident_a")  # another write deleted b
    merged = merge(base, ours, current)
    assert ids(merged) == ["resident_c"]
    assert merged["leader_resident_id"] == "resident_c"


def test_choosing_a_leader_another_write_deleted_fails():
    """选择已被并发删除的 Resident 作为 Leader 时合并失败"""
    base = definition("resident_a", "resident_b", leader="resident_a")
    ours = definition("resident_a", "resident_b", leader="resident_b")
    current = definition("resident_a", leader="resident_a")
    with pytest.raises(ConcurrentChangeConflict, match="leader_resident_id"):
        merge(base, ours, current)


def test_canvas_coordinates_do_not_count_as_changes_and_new_objects_keep_theirs():
    """画布坐标不参与合并，新对象保留自己的坐标"""
    base = definition("resident_a")
    ours = definition("resident_a", "resident_b")
    ours["nodes"][1].update({"x": 40.0, "y": 50.0})
    current = definition("resident_a")
    current["nodes"][0].update({"x": 9.0, "y": 9.0})  # moved on the canvas meanwhile
    merged = merge(base, ours, current)
    assert merged["nodes"][1]["x"] == 40.0
    assert merged["nodes"][0]["title"] == "resident_a"
    assert (merged["nodes"][0]["x"], merged["nodes"][0]["y"]) == (9.0, 9.0)  # the move made meanwhile stays


def test_objects_another_write_added_keep_their_coordinates():
    """并发新增的对象保留自己的画布坐标"""
    base = definition("resident_a")
    ours = definition("resident_a")
    ours["title"] = "Renamed"
    current = definition("resident_a", "resident_c")
    current["nodes"][1].update({"x": 70.0, "y": 80.0})
    merged = merge(base, ours, current)
    assert (merged["nodes"][1]["x"], merged["nodes"][1]["y"]) == (70.0, 80.0)


def test_restamped_objects_do_not_count_as_changes():
    """只被重新打时间戳的对象不算作修改

    Deleting one member restamps the others; another write deleted one of them meanwhile."""

    base = definition("resident_a", "resident_b", "resident_c", leader="resident_a")
    for node in base["nodes"]:
        node["updated_at"] = "2026-10-01T00:00:00Z"
    ours = copy.deepcopy(base)
    ours["nodes"] = [node for node in ours["nodes"] if node["id"] != "resident_b"]
    for node in ours["nodes"]:
        node["updated_at"] = "2026-10-01T00:00:05Z"
    current = copy.deepcopy(base)
    current["nodes"] = [node for node in current["nodes"] if node["id"] != "resident_c"]
    merged = merge(base, ours, current)
    assert ids(merged) == ["resident_a"]
    assert merged["nodes"][0]["updated_at"] == "2026-10-01T00:00:05Z"


def test_versions_and_timestamps_are_left_to_the_commit():
    """变化计数与时间戳由提交赋值，不参与合并"""
    base = definition("resident_a")
    ours = definition("resident_a", "resident_b")
    ours["revision"] = 5
    current = definition("resident_a")
    current["revision"] = 9
    assert "revision" not in merge(base, ours, current)
