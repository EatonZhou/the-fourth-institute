"""Family structure belongs to its Function Block, including nested Families."""
from management_portal.society_service import family_familize


def test_component_identity_comes_from_elements():
    """内部组件依据 Function Block 元素确定所属 Family 门面"""
    block = {"id": "fbi_111111111111", "elements": [{"id": "effect_111111111111", "kind": "component", "key": "router"}],
             "family": {"facade": "resident_111111111111", "router": "resident_222222222222", "members": []}}
    lineage = {"function_blocks": [block], "program": {"components": [{"id": "effect_111111111111", "kind": "effect"}]}}
    assert family_familize.facade_for_internal_note(lineage, "effect_111111111111", "resident_222222222222") == "resident_111111111111"
    assert family_familize.facade_for_internal_note(lineage, "effect_333333333333", "resident_222222222222") == ""


def test_reconcile_replaces_generated_wires_that_lost_their_keys():
    """协调 Family 时替换丢失局部键的生成连线

    Migrated Families may own watch wires without a local key; a reconcile must not leave them behind.

    :module: lineage.hatg
    """
    from management_portal.society_service.templates.familize import reconcile_in_body

    members = ["resident_333333333333", "resident_444444444444"]
    block = {"id": "fbi_111111111111", "elements": [],
             "family": {"facade": "resident_111111111111", "steward": "resident_555555555555",
                        "router": "resident_222222222222", "members": members, "goal": "", "title": "F",
                        "route_threshold": 60, "review_interval_seconds": 86400}}
    body = {"lineage_id": "lineage_000000000001", "program": {}, "function_blocks": [block]}
    reconcile_in_body(body, block, "CASE-A")
    member_watches = sum(w["kind"] == "watch" and w["target"] in members for w in body["program"]["wires"])
    assert member_watches >= len(members)
    for element in block["elements"]:
        if element["key"].startswith("watch_"):
            element["key"] = None
    keyless = {e["id"] for e in block["elements"] if e["key"] is None}
    assert keyless and all(w["kind"] == "watch" for w in body["program"]["wires"] if w["id"] in keyless)
    reconcile_in_body(body, block, "CASE-A")
    wire_ids = {w["id"] for w in body["program"]["wires"]}
    assert not keyless & wire_ids
    assert sum(w["kind"] == "watch" and w["target"] in members for w in body["program"]["wires"]) == member_watches
    assert {e["id"] for e in block["elements"] if e["kind"] == "wire"} == wire_ids


def test_nested_facade_is_a_member_but_internal_roles_are_not():
    """Family 门面可解析到对应 Router"""
    block = {"id": "fbi_111111111111", "elements": [],
             "family": {"facade": "resident_111111111111", "router": "resident_222222222222", "members": ["resident_333333333333"]}}
    assert family_familize.router_of_facade({"function_blocks": [block]}, "resident_111111111111") == "resident_222222222222"
