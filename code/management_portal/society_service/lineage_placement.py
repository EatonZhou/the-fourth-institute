from __future__ import annotations

import math
from typing import Any


WORLD_WIDTH = 1280
WORLD_HEIGHT = 900
NODE_GAP = 24


def _number(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError("节点布局需要有限数值") from None
    if isinstance(value, bool) or not math.isfinite(result):
        raise ValueError("节点布局需要有限数值")
    return result


def _size(value: Any) -> tuple[float, float]:
    if not isinstance(value, dict) or set(value) != {"width", "height"}:
        raise ValueError("节点尺寸需要 width 和 height")
    width, height = _number(value["width"]), _number(value["height"])
    if width <= 0 or height <= 0:
        raise ValueError("节点尺寸必须大于零")
    return width, height


def _default_size(node: dict[str, Any]) -> tuple[float, float]:
    kind = node.get("kind")
    if kind == "seed":
        width, height = 74, 74
    elif kind in {"event_source", "effect", "filter", "selector", "reducer"}:
        width, height = 142, 76
    elif kind == "function_block":
        width, height = 176, 84
    else:
        width, height = 154, 94
    return width / WORLD_WIDTH * 100, height / WORLD_HEIGHT * 100


def _on_canvas(item: dict[str, Any], owned_ids: set[str]) -> bool:
    return item.get("id") not in owned_ids


def _owned_ids(lineage):
    return {e["id"] for b in lineage.get("function_blocks") or [] for e in b.get("elements") or []}


def _candidate_sources(lineage: dict[str, Any]) -> dict[str, str]:
    """Seed ID -> the Resident its candidate fork starts from."""
    return {
        str(edge.get("target") or ""): str(edge.get("source") or "")
        for edge in lineage.get("edges") or []
        if isinstance(edge, dict) and edge.get("type") == "candidate_fork"
    }


def node_position(
    lineage: dict[str, Any],
    source: dict[str, Any],
    payload: dict[str, Any],
    *,
    child: dict[str, Any] | None = None,
) -> tuple[float, float]:
    """Place a new graph object nearest its anchor without bounds or overlap."""
    geometry = payload.get("placement", {})
    if not isinstance(geometry, dict) or set(geometry) - {"node_sizes", "child_size"}:
        raise ValueError("placement 只接受 node_sizes 和 child_size")
    raw_sizes = geometry.get("node_sizes", {})
    if not isinstance(raw_sizes, dict):
        raise ValueError("node_sizes 必须按节点 ID 提供尺寸")
    sizes = {node_id: _size(value) for node_id, value in raw_sizes.items()}
    default_width, default_height = _default_size(child or {})
    child_size = (
        _size(geometry["child_size"])
        if "child_size" in geometry
        else sizes.get((child or {}).get("id"), (default_width, default_height))
    )
    child_width = max(child_size[0], default_width)
    child_height = max(child_size[1], default_height)
    origin_x = _number(payload.get("x", source.get("x", 50)))
    origin_y = _number(payload.get("y", source.get("y", 50)))
    program = lineage.get("program") or {}
    instances = lineage.get("function_blocks") or []
    nodes = [
        *(lineage.get("nodes") or []),
        *(program.get("components") or []),
        *({**instance, "kind": "function_block"} for instance in instances),
    ]
    by_id = {node["id"]: node for node in nodes}
    # An ordinary block replaces its contents on the folded canvas. A Family
    # icon remains visible beside its facade and expanded members.
    # Other blocks reserve their expanded bounds even while folded.
    enclosed = set()
    def enclose(block):
        for element in block.get("elements") or []:
            if element["id"] in enclosed:
                continue
            enclosed.add(element["id"])
            member = by_id.get(element["id"])
            if member and member.get("kind") == "function_block":
                enclose(member)
    if (child or {}).get("kind") == "function_block" and not (child or {}).get("family"):
        enclose(child)
    bounds = {}
    for node in nodes:
        if node["id"] in enclosed:
            continue
        if (node.get("operator_executor") or {}).get("component_id"):
            continue
        default_width, default_height = _default_size(node)
        measured_width, measured_height = sizes.get(node.get("id"), (default_width, default_height))
        width, height = max(default_width, measured_width), max(default_height, measured_height)
        center_x = _number(node.get("x") if node.get("x") is not None else 50)
        center_y = _number(node.get("y") if node.get("y") is not None else 50)
        bounds[node["id"]] = (center_x - width / 2, center_x + width / 2,
                              center_y - height / 2, center_y + height / 2)
    expanded = {}
    footprints = {}
    def expansion(block, visiting):
        bid = block["id"]
        if bid in footprints:
            return footprints[bid]
        if bid in visiting or bid in enclosed:
            return None
        boxes = []
        for element in block.get("elements") or []:
            member = by_id.get(element["id"])
            if not member or member["id"] in enclosed:
                continue
            box = (expansion(member, visiting | {bid}) if member.get("kind") == "function_block"
                   else bounds.get(member["id"]))
            if box:
                boxes.append(box)
        if block.get("family"):
            # A Family has no enclosing frame. Its content extent matters to
            # an enclosing ordinary block, but is not itself an obstacle.
            if bounds.get(bid):
                boxes.append(bounds[bid])
            footprint = ((min(b[0] for b in boxes), max(b[1] for b in boxes),
                          min(b[2] for b in boxes), max(b[3] for b in boxes))
                         if boxes else None)
        elif boxes:
            # Ordinary blocks reserve their expanded header, ports and border.
            footprint = (min(b[0] for b in boxes) - 4600 / WORLD_WIDTH,
                         max(b[1] for b in boxes) + 4600 / WORLD_WIDTH,
                         min(b[2] for b in boxes) - 5400 / WORLD_HEIGHT,
                         max(b[3] for b in boxes) + 2800 / WORLD_HEIGHT)
            expanded[bid] = footprint
        else:
            footprint = bounds.get(bid)
        footprints[bid] = footprint
        return footprint
    for instance in instances:
        expansion({**instance, "kind": "function_block"}, set())
    horizontal = child_width / 2 + NODE_GAP / WORLD_WIDTH * 100
    vertical = child_height / 2 + NODE_GAP / WORLD_HEIGHT * 100
    rectangles = [
        (math.floor((left - horizontal) * 100) / 100,
         math.ceil((right + horizontal) * 100) / 100,
         math.floor((top - vertical) * 100) / 100,
         math.ceil((bottom + vertical) * 100) / 100)
        for left, right, top, bottom in [*bounds.values(), *expanded.values()]
    ]
    origin_x, origin_y = round(origin_x, 2), round(origin_y, 2)
    columns = {origin_x}
    for left, right, _top, _bottom in rectangles:
        columns.update((left, right))
    best = None
    best_score = None
    for candidate_x in sorted(columns, key=lambda value: (abs(value - origin_x), value < origin_x)):
        horizontal_distance = ((candidate_x - origin_x) * WORLD_WIDTH / 100) ** 2
        if best_score is not None and horizontal_distance > best_score[0]:
            break
        intervals = sorted(
            (top, bottom) for left, right, top, bottom in rectangles
            if left < candidate_x < right
        )
        merged = []
        for top, bottom in intervals:
            if merged and top < merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], bottom))
            else:
                merged.append((top, bottom))
        rows = [origin_y]
        for top, bottom in merged:
            if top < origin_y < bottom:
                rows = [top, bottom]
                break
        for candidate_y in rows:
            vertical_distance = ((candidate_y - origin_y) * WORLD_HEIGHT / 100) ** 2
            score = (
                horizontal_distance + vertical_distance,
                abs(candidate_y - origin_y),
                candidate_x < origin_x,
                candidate_y < origin_y,
            )
            if best_score is None or score < best_score:
                best_score = score
                best = (candidate_x, candidate_y)
    if best is None:
        raise ValueError("无法计算可用的节点位置")
    return best


def place_moved_objects(previous: dict[str, Any], lineage: dict[str, Any], moved_ids: set[str]) -> None:
    """Translate a moved visible group together, preserving its internal layout."""
    owned_ids = _owned_ids(lineage)
    objects = [
        *((item, item) for item in lineage.get("nodes") or []),
        *((item, item) for item in (lineage.get("program") or {}).get("components") or []),
        *((item, {**item, "kind": "function_block"}) for item in lineage.get("function_blocks") or []),
    ]
    visible = [(item, geometry) for item, geometry in objects
               if item["id"] in moved_ids and _on_canvas(item, owned_ids)
               and not (item.get("operator_executor") or {}).get("component_id")]
    if not visible:
        return
    rectangles = [(float(item.get("x", 50)), float(item.get("y", 50)), *_default_size(geometry))
                  for item, geometry in visible]
    left = min(x - width / 2 for x, y, width, height in rectangles)
    right = max(x + width / 2 for x, y, width, height in rectangles)
    top = min(y - height / 2 for x, y, width, height in rectangles)
    bottom = max(y + height / 2 for x, y, width, height in rectangles)
    center = ((left + right) / 2, (top + bottom) / 2)
    x, y = node_position(previous, {}, {
        "x": center[0], "y": center[1],
        "placement": {"child_size": {"width": right - left, "height": bottom - top}},
    })
    dx, dy = x - round(center[0], 2), y - round(center[1], 2)
    for item, _geometry in visible:
        item["x"] = round(float(item.get("x", 50)) + dx, 2)
        item["y"] = round(float(item.get("y", 50)) + dy, 2)


def place_new_components(
    previous: dict[str, Any] | None,
    lineage: dict[str, Any],
    request: dict[str, Any] | None = None,
) -> None:
    """Allocate newly visible primitives, reserving each slot within the batch.

    Runs at the graph commit boundary, covering creation, cloning and graph
    projections without changing already visible objects or still-hidden inner objects
    of Function Block instances (their Seeds are visible and are placed).
    New top-level Function Block instances are placed the same way, so a
    second instance of a definition never lands on the first.
    """
    request = request or {}
    _place_components(previous, lineage, request)
    _place_instances(previous, lineage, request)


def _place_instances(previous: dict[str, Any] | None, lineage: dict[str, Any], request: dict[str, Any]) -> None:
    program = lineage.get("program") or {}
    instances = lineage.get("function_blocks") or []
    old_ids = {item["id"] for item in (previous or {}).get("function_blocks", []) if item["id"] not in _owned_ids(previous or {})}
    pending = [item for item in instances if item["id"] not in old_ids and item["id"] not in _owned_ids(lineage)]
    if not pending:
        return
    pending_ids = {item["id"] for item in pending}
    occupied = {
        **lineage,
        "function_blocks": [item for item in instances if item["id"] not in pending_ids],
    }
    residents = {item["id"]: item for item in lineage.get("nodes", [])}
    anchor = (
        residents.get(request.get("near_resident_id"))
        or residents.get(lineage.get("leader_resident_id"))
        or next(iter(residents.values()), {"x": 50, "y": 50})
    )
    geometry = {"placement": request.get("placement", {})}
    for instance in pending:
        coordinates = {axis: instance[axis] for axis in ("x", "y") if instance.get(axis) is not None}
        instance["x"], instance["y"] = node_position(
            occupied, anchor, {**geometry, **coordinates}, child={**instance, "kind": "function_block"},
        )
        occupied["function_blocks"].append(instance)


def _place_components(previous: dict[str, Any] | None, lineage: dict[str, Any], request: dict[str, Any]) -> None:
    program = lineage.get("program") or {}
    previous_program = (previous or {}).get("program") or {}
    previous_instances = _owned_ids(previous or {})
    old_ids = {item["id"] for item in previous_program.get("components", []) if _on_canvas(item, previous_instances)}
    instance_ids = _owned_ids(lineage)
    pending = [
        item for item in program.get("components", [])
        if item["id"] not in old_ids and _on_canvas(item, instance_ids)
    ]
    if not pending:
        return
    pending_ids = {item["id"] for item in pending}
    occupied = {
        **lineage,
        "program": {
            **program,
            "components": [item for item in program.get("components", []) if item["id"] not in pending_ids],
        },
    }
    residents = {item["id"]: item for item in lineage.get("nodes", [])}
    near_id = request.get("near_resident_id")
    if near_id and near_id not in residents:
        raise ValueError("空间锚点必须是当前图中的 Resident")
    positioned = {
        **residents,
        **{item["id"]: item for item in occupied["program"]["components"]},
    }
    candidate_sources = _candidate_sources(lineage)
    adjacency: dict[str, list[str]] = {}
    for wire in program.get("wires", []):
        source, target = wire.get("source"), wire.get("target")
        adjacency.setdefault(source, []).append(target)
        adjacency.setdefault(target, []).append(source)
    default_anchor = (
        residents.get(lineage.get("leader_resident_id"))
        or next(iter(residents.values()), {"x": 20, "y": 35})
    )
    geometry = {"placement": request.get("placement", {})}
    while pending:
        component = next(
            (item for item in pending if any(ref in positioned for ref in adjacency.get(item["id"], []))),
            pending[0],
        )
        neighbors = [positioned[ref] for ref in adjacency.get(component["id"], []) if ref in positioned]
        anchor = (
            # A Seed stands beside the Resident it would be forked from.
            residents.get(candidate_sources.get(component["id"], ""))
            or residents.get(near_id)
            or (neighbors[0] if neighbors else None)
            or residents.get(component.get("created_by_principal_id"))
            or default_anchor
        )
        coordinates = {axis: component[axis] for axis in ("x", "y") if component.get(axis) is not None}
        component["x"], component["y"] = node_position(
            occupied, anchor, {**geometry, **coordinates}, child=component,
        )
        occupied["program"]["components"].append(component)
        positioned[component["id"]] = component
        pending.remove(component)
