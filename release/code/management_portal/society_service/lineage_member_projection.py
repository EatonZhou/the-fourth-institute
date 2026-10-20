from __future__ import annotations

from typing import Any, Iterable, Mapping

from . import lineage_placement


def project_resident_nodes(
    definition: dict[str, Any],
    residents: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Materialize the authorized HATG participant set with graph-owned attributes.

    The caller supplies home members plus derived child leaders for a Project
    HATG. Participation is not a second home: every Resident keeps its unique
    canonical lineage_id. A new identity may already have a node row while its
    initial layout still needs to be allocated in the same transaction.
    """
    attributes = {
        str(node["id"]): node
        for node in definition["nodes"]
    }
    members = list(residents)
    member_ids = {str(member["resident_id"]) for member in members}
    foreign_ids = attributes.keys() - member_ids
    if foreign_ids:
        raise ValueError("HATG nodes lack canonical membership or a derived Project reference: " + ", ".join(sorted(foreign_ids)))
    for member in members:
        resident_id = str(member["resident_id"])
        node = attributes.get(resident_id)
        if node is not None and "x" in node and "y" in node:
            continue
        anchor = attributes.get(str(member.get("source_resident_id") or ""), {"x": 20, "y": 35})
        occupied = {**definition, "nodes": [item for item in definition["nodes"] if item["id"] != resident_id]}
        x, y = lineage_placement.node_position(occupied, anchor, {})
        if node is None:
            node = {"id": resident_id, "title": str(member.get("title") or resident_id)}
            definition["nodes"].append(node)
            attributes[resident_id] = node
        node.update(x=x, y=y)
    return definition
