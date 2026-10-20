"""Pure planning for atomic cross-Lineage object moves (MoveLineageObjects).

A move never asks the caller to tidy the selection first: relations between
the selection and the rest of the source (wires, node relations, operator
executor bindings and Function Block slot bindings) are detached and listed in
the result (decision_6fc653d55c79, decision_6591e7793116).
"""
from __future__ import annotations

import secrets
from copy import deepcopy
from typing import Any



def successor_coordinator(
    current: str, removed_ids: set[str], remaining: list[dict[str, Any]], *, member_ids: set[str] | None = None,
) -> str:
    """Keep a coordinator, or pick a random remaining Resident when it leaves.

    Removing or moving away the Leader picks a
    random successor from the remaining Residents (decision_d19b9909a674);
    with no Resident left the Lineage has none for now.
    """

    if current not in removed_ids:
        return current
    candidates = [
        str(node.get("id") or "")
        for node in remaining
        if str(node.get("id") or "").startswith("resident_")
        and (member_ids is None or node["id"] in member_ids)
        and not (
            isinstance(node.get("operator_executor"), dict)
            and str(node["operator_executor"].get("component_id") or "")
        )
    ]
    return secrets.choice(candidates) if candidates else ""


def _executor_id(component: dict[str, Any]) -> str:
    return str((component.get("config") or {}).get("resident_executor_id") or "")


def _without_executor(component: dict[str, Any]) -> dict[str, Any]:
    item = deepcopy(component)
    item["config"] = {key: value for key, value in (item.get("config") or {}).items() if key != "resident_executor_id"}
    return item


def plan(
    source: dict, target: dict, object_ids: list[str], *,
    source_member_ids: set[str] | None = None,
) -> dict:
    if not isinstance(object_ids, list) or not object_ids or any(
        not isinstance(item, str) or not item for item in object_ids
    ) or len(set(object_ids)) != len(object_ids):
        raise ValueError("object_ids must be a nonempty array of unique object IDs")
    program = source.get("program") or {}
    nodes = {item["id"]: item for item in source.get("nodes", [])}
    components = {item["id"]: item for item in program.get("components", [])}
    instances = {item["id"]: item for item in source.get("function_blocks", [])}
    selected = set(object_ids)
    unknown = selected - nodes.keys() - components.keys() - instances.keys()
    if unknown:
        raise ValueError("选区包含不存在的对象：" + ", ".join(sorted(unknown)))

    # A selected boundary or owned element moves with its whole enclosing block.
    owners = {e["id"]: b["id"] for b in instances.values() for e in b.get("elements") or []}
    moved = set(selected)
    while True:
        expanded = moved | {owners[eid] for eid in moved if eid in owners}
        expanded.update(e["id"] for bid in moved & instances.keys() for e in instances[bid].get("elements") or [])
        if expanded == moved:
            break
        moved = expanded
    moved_instances = moved & instances.keys()
    moved -= moved_instances
    moved -= {item["id"] for item in program.get("wires", [])}

    # Wires and node relations follow their endpoints; the ones that cross the
    # boundary are detached. Watch wires may observe other wires or relations,
    # so this settles over the attachment graph.
    attachments = {item["id"]: item for item in source.get("edges", []) + program.get("wires", [])}
    inside = set(moved)
    pending = dict(attachments)
    while pending:
        progressed = False
        for item_id, item in list(pending.items()):
            ends = {str(item.get("source") or ""), str(item.get("target") or "")} - {""}
            if any(end in pending for end in ends):
                continue
            if ends <= inside:
                inside.add(item_id)
            del pending[item_id]
            progressed = True
        if not progressed:  # a cycle of watch wires: treat what is left as external
            break
    moving_attachments = {item_id for item_id in attachments if item_id in inside}
    detached: list[dict[str, str]] = []
    source_edge_ids = {edge["id"] for edge in source.get("edges", [])}
    for item_id, item in attachments.items():
        ends = {str(item.get("source") or ""), str(item.get("target") or "")} - {""}
        if item_id not in moving_attachments and ends & inside:
            is_wire = item_id not in source_edge_ids
            detached.append({"id": item_id, "source": str(item.get("source") or ""), "target": str(item.get("target") or ""),
                             "type": "wire" if is_wire else str(item.get("type") or "relation")})
    detached_ids = {item["id"] for item in detached}

    # Operator executor bindings and Function Block slot bindings that cross
    # the boundary are cleared on both sides.
    left_components, right_components = [], []
    cleared_executors: set[str] = set()
    for component_id, component in components.items():
        executor = _executor_id(component)
        crosses = bool(executor) and (component_id in moved) != (executor in moved)
        if crosses:
            detached.append({"id": f"{component_id}:executor", "source": component_id, "target": executor,
                             "type": "operator_executor"})
            cleared_executors.add(executor)
        item = _without_executor(component) if crosses else deepcopy(component)
        (right_components if component_id in moved else left_components).append(item)
    left_instances, right_instances = [], []
    for instance_id, instance in instances.items():
        item = deepcopy(instance)
        stays = instance_id not in moved_instances
        item["elements"] = [e for e in item.get("elements") or [] if e["id"] not in detached_ids]
        (left_instances if stays else right_instances).append(item)

    def node_copy(node: dict[str, Any]) -> dict[str, Any]:
        item = deepcopy(node)
        if node["id"] in cleared_executors:
            item.pop("operator_executor", None)
        return item

    target_program = target.get("program") or {}
    target_ids = {item["id"] for item in target.get("nodes", []) + target.get("edges", [])
                  + target_program.get("components", []) + target_program.get("wires", [])
                  + target.get("function_blocks", [])}
    clashes = (moved | moved_instances | moving_attachments) & target_ids
    if clashes:
        raise ValueError("目标 Lineage 已存在相同 ID 的对象：" + ", ".join(sorted(clashes)))

    left, right = deepcopy(source), deepcopy(target)
    residents = moved & set(nodes)
    left["nodes"] = [node_copy(node) for key, node in nodes.items() if key not in moved]
    right["nodes"] = deepcopy(target.get("nodes", [])) + [node_copy(node) for key, node in nodes.items() if key in moved]
    edge_ids = [edge["id"] for edge in source.get("edges", []) if edge["id"] in moving_attachments]
    left["edges"] = [deepcopy(edge) for edge in source.get("edges", []) if edge["id"] not in moving_attachments | detached_ids]
    right["edges"] = deepcopy(target.get("edges", [])) + [deepcopy(edge) for edge in source.get("edges", []) if edge["id"] in edge_ids]
    wire_ids = [wire["id"] for wire in program.get("wires", []) if wire["id"] in moving_attachments]
    left.setdefault("program", {})["wires"] = [deepcopy(wire) for wire in program.get("wires", [])
                                               if wire["id"] not in moving_attachments | detached_ids]
    right.setdefault("program", {})["wires"] = deepcopy(target_program.get("wires", [])) + [
        deepcopy(wire) for wire in program.get("wires", []) if wire["id"] in wire_ids]
    left["program"]["components"] = left_components
    right["program"]["components"] = deepcopy(target_program.get("components", [])) + right_components
    left["function_blocks"] = left_instances
    right["function_blocks"] = deepcopy(target.get("function_blocks", [])) + right_instances

    # Coordinators that leave are replaced from the remaining Residents; an
    # empty target takes over the selection's own coordinators.
    target_empty = not target.get("nodes")
    current = str(source.get("leader_resident_id") or "")
    left["leader_resident_id"] = successor_coordinator(current, residents, left["nodes"], member_ids=source_member_ids)
    if target_empty and current in residents:
        right["leader_resident_id"] = current
    if (source.get("principal_hierarchy") or {}).get("level") == "project" and not left.get("leader_resident_id"):
        raise ValueError("Project leader must remain in its own Lineage")
    return {"source": left, "target": right, "object_ids": sorted(selected),
            "resident_ids": sorted(residents), "moved_ids": sorted(moved | moved_instances | moving_attachments),
            "wire_ids": wire_ids, "edge_ids": edge_ids,
            "detached_edges": detached,
            "detached_edge_ids": [item["id"] for item in detached],
            "detached_wire_ids": sorted(item["id"] for item in detached if item["type"] == "wire"),
            "target_empty": target_empty}
