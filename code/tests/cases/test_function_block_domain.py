"""Function Blocks own selections without changing their executable graph."""
from copy import deepcopy

import pytest

from management_portal.society_service import function_block_domain as blocks


def body():
    return {"nodes": [{"id": "resident_000000000001"}], "edges": [],
            "program": {"components": [{"id": "event_source_000000000001", "kind": "event_source"}],
                        "wires": []}, "function_blocks": []}


def test_wrap_preserves_graph_and_rejects_double_ownership():
    """封装功能块保留运行图并拒绝重复归属"""
    graph = body()
    before = deepcopy(graph["program"])
    block = blocks.functionalize_in_body(graph, ["event_source_000000000001"], "sample_admin", title="Timer",
                                       element_keys={"event_source_000000000001": "timer"})
    assert graph["program"] == before
    assert block["elements"] == [{"id": "event_source_000000000001", "kind": "component", "key": "timer"}]
    with pytest.raises(ValueError, match="already belongs"):
        blocks.functionalize_in_body(graph, ["event_source_000000000001"], "sample_admin")


def test_nested_blocks_cannot_form_cycles_and_unwrap_reparents():
    """嵌套功能块拒绝循环且解除后元素归入父块"""
    graph = body()
    inner = blocks.functionalize_in_body(graph, ["event_source_000000000001"], "sample_admin")
    outer = blocks.functionalize_in_body(graph, [inner["id"]], "sample_admin")
    with pytest.raises(ValueError, match="cycle"):
        blocks.update_in_body(graph, inner["id"], "sample_admin", add_element_ids=[outer["id"]])
    blocks.unfunctionalize_in_body(graph, inner["id"])
    assert graph["function_blocks"][0]["id"] == outer["id"]
    assert graph["function_blocks"][0]["elements"][0]["id"] == "event_source_000000000001"


def test_deleted_elements_are_pruned_and_empty_blocks_removed():
    """删除元素后清除对应归属并移除空功能块"""
    graph = body()
    blocks.functionalize_in_body(graph, ["event_source_000000000001"], "sample_admin")
    graph["program"]["components"] = []
    assert blocks.normalize_blocks(graph, "sample_admin", previous=body()) == []


def test_family_requires_family_operation():
    """Family 功能块仅允许通过 Unfamilize 解除"""
    graph = body()
    block = blocks.functionalize_in_body(graph, ["resident_000000000001"], "sample_admin")
    block["family"] = {"members": ["resident_000000000001"]}
    with pytest.raises(ValueError, match="Unfamilize"):
        blocks.unfunctionalize_in_body(graph, block["id"])
