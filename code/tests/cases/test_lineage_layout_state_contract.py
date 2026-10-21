from management_portal.society_service import lineage_placement, lineage_program
from management_portal.society_service.lineage_placement import node_position, place_new_components
from copy import deepcopy
import math
import pytest


@pytest.mark.parametrize("nested", [False, True])
def test_family_icon_stays_clear_of_its_facade_and_visible_members(nested):
    """Family 图标避开门面与可见成员"""
    facade = {"id": "facade", "x": 50, "y": 50}
    member = {"id": "member", "x": 50, "y": 70}
    previous = {"nodes": [facade, member], "function_blocks": []}
    elements = [{"id": "facade", "kind": "resident"}, {"id": "member", "kind": "resident"}]
    if nested:
        previous["function_blocks"] = [{"id": "inner", "x": 70, "y": 70,
            "family": {"facade": "member", "members": []},
            "elements": [{"id": "member", "kind": "resident"}]}]
        elements[-1] = {"id": "inner", "kind": "function_block"}
    current = deepcopy(previous)
    block = {"id": "family", "x": 50, "y": 50, "elements": elements,
             "family": {"facade": "facade", "members": ["member"]}}
    current["function_blocks"].append(block)
    place_new_components(previous, current)
    _assert_clear((block["x"], block["y"]), [facade, member],
                  lineage_placement._default_size({"kind": "function_block"}))
    assert current["nodes"] == previous["nodes"]


def test_moved_group_avoids_destination_and_preserves_relative_positions():
    """移动组避开目标并保留成员相对位置"""
    previous = {"nodes": [], "function_blocks": [{"id": "target", "x": 50, "y": 50}]}
    lineage = deepcopy(previous)
    lineage["nodes"] = [{"id": "member", "x": 50, "y": 50}, {"id": "inner", "x": 50, "y": 50}]
    lineage["program"] = {"components": [{"id": "event", "kind": "event_source", "x": 80, "y": 50}]}
    lineage["function_blocks"].append({"id": "moved_block", "x": 110, "y": 50, "elements": [{"id": "inner"}]})
    lineage_placement.place_moved_objects(previous, lineage, {"member", "inner", "event", "moved_block"})
    member = lineage["nodes"][0]
    event = lineage["program"]["components"][0]
    block = lineage["function_blocks"][1]
    assert (event["x"] - member["x"], event["y"] - member["y"]) == (30, 0)
    assert (block["x"] - member["x"], block["y"] - member["y"]) == (60, 0)
    for node in (member, event, block):
        kind = "function_block" if node is block else node.get("kind")
        _assert_clear((node["x"], node["y"]), previous["function_blocks"],
                      lineage_placement._default_size({"kind": kind}), {"target": (176 / 12.8, 84 / 9)})
    assert lineage["nodes"][1] == {"id": "inner", "x": 50, "y": 50}
    assert lineage["function_blocks"][0] == previous["function_blocks"][0]
    snapshot = deepcopy(lineage)
    lineage_placement.place_moved_objects(previous, lineage, {"member", "inner", "event", "moved_block"})
    assert lineage == snapshot


def test_program_normalization_preserves_unbounded_layout_coordinates_contracts(subtests, isolated_portal_case):
    """Function Block 实例中的 Seed 显示在来源 Resident 旁

    实例的 Seed 分配位置并占位，重新展开保留位置与采纳/放弃状态，读取视图保留 Program 诊断。

    :module: resident.fork-handoff
    :importance: P1
    """
    with subtests.test(contract='test_program_normalization_preserves_unbounded_layout_coordinates'), isolated_portal_case() as case_path:
        normalized = lineage_program.normalize_program(
            {
                "components": [{
                    "id": "event_source_111111111111",
                    "kind": "event_source",
                    "title": "Unbounded Event",
                    "x": -17.25,
                    "y": 124.5,
                    "config": {"event_type": "manual.triggered"},
                }],
                "wires": [],
            },
            lineage_nodes=[],
        )

        component = normalized["components"][0]
        assert component["x"] == -17.25
        assert component["y"] == 124.5
    with subtests.test(contract='test_fork_uses_measured_node_and_child_sizes_and_program_obstacles'), isolated_portal_case() as case_path:
        source = {"id": "source", "x": 310, "y": -250}
        effect = {"id": "effect", "kind": "effect", "x": 310, "y": -230}
        block = {"id": "block", "kind": "function_block", "x": 335, "y": -250}
        lineage = {"nodes": [source], "function_blocks": [block], "program": {"components": [effect]}}
        sizes = {"source": (22, 20), "effect": (40, 32), "block": (45, 30)}
        payload = {"placement": {
            "node_sizes": {node_id: {"width": width, "height": height} for node_id, (width, height) in sizes.items()},
            "child_size": {"width": 30, "height": 25},
        }}
        position = node_position(lineage, source, payload)
        _assert_clear(position, [source, effect, block], (30, 25), sizes)
        assert position[0] > 250 and position[1] < -200
    with subtests.test(contract='test_fork_selects_nearest_free_rectangle_boundary'), isolated_portal_case() as case_path:
        source = {"id": "source", "x": 300, "y": 250}
        below = {"id": "below", "x": 300, "y": 264}
        right = {"id": "right", "x": 314, "y": 250}
        position = node_position({"nodes": [source, below, right]}, source, {})
        assert position == (300, 236.88)
        _assert_clear(position, [source, below, right])
    with subtests.test(contract='test_fork_respects_free_explicit_position_and_avoids_stale_occupied_position'), isolated_portal_case() as case_path:
        source = {"id": "source", "x": 300, "y": 250}
        lineage = {"nodes": [source]}
        assert node_position(lineage, source, {"x": 330, "y": 275}) == (330, 275)
        position = node_position(lineage, source, {"x": 300, "y": 250})
        _assert_clear(position, [source])
    with subtests.test(contract='test_fork_avoids_hidden_block_internals'), isolated_portal_case() as case_path:
        source = {"id": "source", "x": 300, "y": 250}
        expected = node_position({"nodes": [source]}, source, {})
        lineage = {"nodes": [source, {"id": "remote", "x": -9999, "y": 9999}], "function_blocks": [{"id": "block", "x": 900, "y": 900, "elements": [{"id": "hidden", "kind": "component"}]}], "program": {
            "components": [{"id": "hidden", "x": expected[0], "y": expected[1]}],
        }}
        position = node_position(lineage, source, {})
        _assert_clear(position, [source, lineage["program"]["components"][0]])
    with subtests.test(contract='test_new_primitives_reserve_measured_rectangles_in_one_batch'), isolated_portal_case() as case_path:
        source = {"id": "resident", "x": 310, "y": -250}
        existing = {"id": "existing", "kind": "event_source", "x": 310, "y": -230}
        previous = {"nodes": [source], "program": {"components": [existing]}}
        lineage = deepcopy(previous)
        sizes = {"resident": (32, 28), "existing": (25, 20)}
        for index in range(18):
            kind = ["event_source", "effect", "filter", "selector", "reducer", "seed"][index % 6]
            component = {"id": f"{kind}_{index}", "kind": kind, "x": 310, "y": -250}
            lineage["program"]["components"].append(component)
            sizes[component["id"]] = (28, 24)
        place_new_components(previous, lineage, {"placement": {"node_sizes": {
            key: {"width": size[0], "height": size[1]} for key, size in sizes.items()
        }}})
        occupied = [source, existing]
        for component in lineage["program"]["components"][1:]:
            _assert_clear((component["x"], component["y"]), occupied, (28, 24), sizes)
            assert math.dist((component["x"], component["y"]), (310, -250)) < 110
            occupied.append(component)
        assert lineage["nodes"] == previous["nodes"]
        assert lineage["program"]["components"][0] == existing
        snapshot = deepcopy(lineage)
        place_new_components(snapshot, lineage)
        assert lineage == snapshot


def _assert_clear(position, nodes, child_size=(154 / 12.8, 94 / 9), sizes=None):
    for node in nodes:
        width, height = (sizes or {}).get(node["id"], (154 / 12.8, 94 / 9))
        assert (
            abs(position[0] - node["x"]) + 1e-9 >= (width + child_size[0]) / 2 + 24 / 12.8
            or abs(position[1] - node["y"]) + 1e-9 >= (height + child_size[1]) / 2 + 24 / 9
        )


def test_forks_expand_near_source_without_bounds_or_occupied_fallback(subtests, isolated_portal_case):
    """分支在来源附近扩展并避开已有节点"""
    for origin in [(300, 250), (-300, -250), (0, 0), (90, 88)]:
        with subtests.test(origin=origin), isolated_portal_case() as case_path:
            source = {"id": "source", "x": origin[0], "y": origin[1]}
            lineage = {"nodes": [source]}
            for index in range(48):
                position = node_position(lineage, source, {})
                _assert_clear(position, lineage["nodes"])
                assert math.dist(position, origin) < 100
                lineage["nodes"].append({"id": str(index), "x": position[0], "y": position[1]})
            assert (source["x"], source["y"]) == origin


def test_fork_rejects_invalid_geometry(subtests, isolated_portal_case):
    """创建分支拒绝无效布局尺寸"""
    for geometry in [None, {"node_sizes": []}, {"child_size": {"width": 0, "height": 20}}, {"child_size": {"width": float("nan"), "height": 20}}]:
        with subtests.test(geometry=geometry), isolated_portal_case() as case_path:
            with pytest.raises(ValueError):
                node_position({"nodes": []}, {"x": 0, "y": 0}, {"placement": geometry})


def test_new_node_stays_outside_nested_block_expansion():
    """新节点避开嵌套块的展开区域"""
    members = [{"id": "left", "x": 20, "y": 40}, {"id": "right", "x": 60, "y": 40}]
    inner = {"id": "inner", "x": 20, "y": 20, "elements": [{"id": item["id"]} for item in members]}
    outer = {"id": "outer", "x": 20, "y": 10, "elements": [{"id": "inner"}]}
    lineage = {"nodes": members, "function_blocks": [inner, outer]}
    original = deepcopy(lineage)
    position = node_position(lineage, {}, {"x": 40, "y": 40}, child={"kind": "filter"})
    # The empty space between members still belongs to their expanded block.
    assert position[0] < 10 or position[0] > 70 or position[1] < 20 or position[1] > 55
    assert lineage == original
    # Wrapping those members does not displace the new block away from itself.
    assert node_position(lineage, {}, {"x": 40, "y": 40},
                         child={"kind": "function_block", "elements": [{"id": "outer"}]}) == (40, 40)


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
def test_family_whitespace_is_available_but_each_measured_card_stays_reserved(nested, reverse):
    """Family 空白可放置，图标、门面、成员与组件仍按实测尺寸避让。"""
    facade = {"id": "facade", "x": -100, "y": 0}
    member = {"id": "member", "x": 100, "y": 0}
    effect = {"id": "effect", "kind": "effect", "x": 0, "y": 100}
    family = {"id": "family", "x": 0, "y": -100,
              "family": {"facade": "facade", "members": ["member"]},
              "elements": [{"id": item["id"]} for item in (facade, member, effect)]}
    blocks = [family]
    if nested:
        inner = {"id": "inner", "x": 100, "y": 100,
                 "family": {"facade": "member", "members": []},
                 "elements": [{"id": "member"}, {"id": "effect"}]}
        family["elements"] = [{"id": "facade"}, {"id": "inner"}]
        blocks.append(inner)
    if reverse:
        blocks.reverse()
    lineage = {"nodes": [facade, member], "program": {"components": [effect]},
               "function_blocks": blocks}
    cards = [facade, member, effect, *blocks]
    sizes = {card["id"]: (30, 26) for card in cards}
    payload = {"placement": {"node_sizes": {
        key: {"width": width, "height": height} for key, (width, height) in sizes.items()
    }, "child_size": {"width": 22, "height": 20}}}
    original = deepcopy((lineage, payload))
    assert node_position(lineage, {"x": 0, "y": 0}, payload) == (0, 0)
    for card in cards:
        position = node_position(lineage, card, payload)
        _assert_clear(position, cards, (22, 20), sizes)
    assert (lineage, payload) == original


@pytest.mark.parametrize("family_outside", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
def test_mixed_family_and_ordinary_blocks_preserve_only_ordinary_expansion(family_outside, reverse):
    """混合嵌套保留普通块边界，不给 Family 单独填实空白。"""
    members = [{"id": "left", "x": -100, "y": 0}, {"id": "right", "x": 100, "y": 0}]
    ordinary = {"id": "ordinary", "x": 0, "y": -100,
                "elements": [{"id": item["id"]} for item in members]}
    family = {"id": "family", "x": 0, "y": 100,
              "family": {"facade": "left", "members": ["right"]}, "elements": []}
    if family_outside:
        family["elements"] = [{"id": "ordinary"}]
    else:
        family["elements"] = ordinary["elements"]
        ordinary["elements"] = [{"id": "family"}]
    blocks = [ordinary, family]
    if reverse:
        blocks.reverse()
    lineage = {"nodes": members, "function_blocks": blocks}
    original = deepcopy(lineage)
    # The ordinary enclosure spans the distant member cards, even when a
    # Family lies between them and the enclosing ordinary block.
    left = -100 - 154 / 12.8 / 2 - 46 / 12.8
    right = 100 + 154 / 12.8 / 2 + 46 / 12.8
    top = -94 / 9 / 2 - 54 / 9
    bottom = (94 / 9 / 2 if family_outside else 100 + 84 / 9 / 2) + 28 / 9
    envelope = {"id": "envelope", "x": (left + right) / 2, "y": (top + bottom) / 2}
    position = node_position(lineage, {"x": 80, "y": 0}, {})
    _assert_clear(position, [envelope], sizes={"envelope": (right - left, bottom - top)})
    # A Family around a normal block does not fill the gap to its own icon;
    # a normal block around a Family does reserve that same gap.
    gap = node_position(lineage, {}, {"x": 0, "y": 50})
    assert (gap == (0, 50)) is family_outside
    assert lineage == original


def test_new_primitives_use_family_gaps_and_preserve_existing_coordinates():
    """批量组件共用 Family 空白中的位置权威，已存在成员不移动。"""
    previous = {"nodes": [{"id": "left", "x": -100, "y": 0},
                          {"id": "right", "x": 100, "y": 0}],
                "function_blocks": [{"id": "family", "x": 0, "y": -100,
                    "family": {"facade": "left", "members": ["right"]},
                    "elements": [{"id": "left"}, {"id": "right"}]}],
                "program": {"components": []}}
    lineage = deepcopy(previous)
    lineage["program"]["components"] = [
        {"id": name, "kind": "effect", "x": 0, "y": 0} for name in ("a", "b")
    ]
    place_new_components(previous, lineage)
    first, second = lineage["program"]["components"]
    assert (first["x"], first["y"]) == (0, 0)
    _assert_clear((second["x"], second["y"]), [first], (142 / 12.8, 76 / 9),
                  {"a": (142 / 12.8, 76 / 9)})
    assert lineage["nodes"] == previous["nodes"]
    assert lineage["function_blocks"] == previous["function_blocks"]
    snapshot = deepcopy(lineage)
    place_new_components(snapshot, lineage)
    assert lineage == snapshot


def test_unpositioned_primitives_follow_connected_nodes_and_preserve_hidden_internals():
    """未定位组件跟随连接节点并保留隐藏组件位置"""
    root = {"id": "leader", "x": 0, "y": 0}
    target = {"id": "target", "x": -300, "y": 250}
    hidden = {"id": "hidden", "kind": "effect", "x": 0, "y": 0}
    lineage = {"nodes": [root, target], "leader_resident_id": "leader",
               "function_blocks": [{"id": "block", "x": 0, "y": 0, "elements": [{"id": "hidden", "kind": "component"}]}], "program": {
        "components": [
            {"id": "effect", "kind": "effect"},
            {"id": "event", "kind": "event_source"},
            hidden,
        ],
        "wires": [{"source": "event", "target": "target"}, {"source": "event", "target": "effect"}],
    }}
    place_new_components(None, lineage)
    assert hidden == {"id": "hidden", "kind": "effect", "x": 0, "y": 0}
    for component in lineage["program"]["components"][:2]:
        assert math.dist((component["x"], component["y"]), (-300, 250)) < 40
    assert "x" not in lineage_program.normalize_program({
        "components": [{"id": "event_source_new", "kind": "event_source", "x": None, "config": {"event_type": "manual.triggered"}}],
        "wires": [],
    }, lineage_nodes=[]) ["components"][0]


def test_candidate_seeds_are_placed_beside_their_source():
    """候选 Seed 放置在各自来源 Resident 旁"""
    left = {"id": "resident_aaaaaaaaaaaa", "x": 20, "y": 20}
    right = {"id": "resident_bbbbbbbbbbbb", "x": 80, "y": 60}
    seeds = [{"id": f"seed_{index}", "kind": "seed", "config": {}} for index in range(2)]
    forks = [{"source": source["id"], "target": seed["id"], "type": "candidate_fork"}
             for seed, source in zip(seeds, (left, right))]
    lineage = {"nodes": [left, right], "edges": forks, "leader_resident_id": left["id"],
               "program": {"components": seeds, "wires": []}}
    place_new_components(None, lineage)
    for seed, source in zip(seeds, (left, right)):
        assert math.dist((seed["x"], seed["y"]), (source["x"], source["y"])) < 20


def test_unwrapping_places_revealed_objects_without_moving_visible_objects():
    """展开块时安置显露对象并保留可见对象位置"""
    resident = {"id": "resident", "x": 0, "y": 0}
    visible_seed = {"id": "seed", "kind": "seed", "x": 75, "y": 70}
    hidden = [{"id": name, "kind": "event_source", "x": 0, "y": 0} for name in ("manual", "timer")]
    previous = {"nodes": [resident], "program": {"components": [visible_seed, *hidden]},
                "function_blocks": [{"id": "block", "x": 50, "y": 50,
                    "elements": [{"id": "manual", "kind": "component"}, {"id": "timer", "kind": "component"},
                                 {"id": "nested", "kind": "function_block"}]},
                                    {"id": "nested", "x": 0, "y": 0, "elements": []}]}
    current = deepcopy(previous)
    nested = current["function_blocks"][1]
    current["function_blocks"] = [nested]
    place_new_components(previous, current)
    assert current["nodes"] == previous["nodes"]
    assert (current["program"]["components"][0]["x"], current["program"]["components"][0]["y"]) == (75, 70)
    visible = [resident, *current["program"]["components"], {**nested, "kind": "function_block"}]
    sizes = {item["id"]: lineage_placement._default_size(item) for item in visible}
    for item in visible:
        _assert_clear((item["x"], item["y"]), [other for other in visible if other != item], sizes[item["id"]], sizes)
    snapshot = deepcopy(current)
    place_new_components(snapshot, current)
    assert current == snapshot
