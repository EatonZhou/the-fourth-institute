from __future__ import annotations

from management_common.format_tags import schema_matches

from copy import deepcopy
from typing import Any, Callable, Mapping


GRAPH = "graph"
LAYOUT = "layout"
RUNTIME = "runtime"
DOMAINS = {GRAPH, LAYOUT, RUNTIME}

HANDOFF_RUNTIME_FIELDS = {
    "status",
    "error",
    "ready_at",
}
RUNTIME_STATE_SCHEMA = "tfi.lineage-runtime-state.v1"
RUNTIME_STATE_FIELDS = frozenset({
    "schema",
    "lineage_id",
    "runtime_revision",
    "updated_at",
    "nodes",
    "edges",
})
OPTIONAL_DEFINITION_COLLECTION_FIELDS = {
    "edges",
    "function_blocks",
    "fragments",
}

def _non_negative_integer(value: object) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def domain_versions(lineage: object) -> dict[str, int]:
    item = lineage if isinstance(lineage, dict) else {}
    return {
        GRAPH: _non_negative_integer(item.get("revision")),
        LAYOUT: _non_negative_integer(item.get("layout_revision")),
        RUNTIME: _non_negative_integer(item.get("runtime_revision")),
    }


def ensure_metadata(
    lineage: dict[str, Any],
    previous: object = None,
) -> dict[str, Any]:
    """Materialize the three independent HATG state clocks.

    ``revision`` is the graph-authoring clock. Layout and runtime changes have
    their own clocks and never advance it.
    """

    before = previous if isinstance(previous, dict) else {}
    lineage["revision"] = _non_negative_integer(
        lineage.get("revision", before.get("revision"))
    )
    lineage["layout_revision"] = _non_negative_integer(
        lineage.get("layout_revision", before.get("layout_revision"))
    )
    lineage["runtime_revision"] = _non_negative_integer(
        lineage.get("runtime_revision", before.get("runtime_revision"))
    )
    lineage["graph_updated_at"] = str(
        lineage.get("graph_updated_at")
        or before.get("graph_updated_at")
        or lineage.get("updated_at")
        or before.get("updated_at")
        or ""
    )
    lineage["layout_updated_at"] = str(
        lineage.get("layout_updated_at")
        or before.get("layout_updated_at")
        or ""
    )
    lineage["runtime_updated_at"] = str(
        lineage.get("runtime_updated_at")
        or before.get("runtime_updated_at")
        or ""
    )
    return lineage


def stamp(
    lineage: dict[str, Any],
    previous: object,
    domain: str,
    *,
    now: str,
) -> dict[str, Any]:
    """Advance exactly one HATG state domain."""

    if domain not in DOMAINS:
        raise ValueError(f"unknown Lineage state domain: {domain}")
    before = previous if isinstance(previous, dict) else {}
    ensure_metadata(lineage, before)
    versions = domain_versions(before)
    if domain == GRAPH:
        lineage["revision"] = versions[GRAPH] + 1
        lineage["graph_updated_at"] = now
        # ``updated_at`` remains the graph-authoring timestamp so existing
        # Lineage lists do not jump around because an Agent changed state.
        lineage["updated_at"] = now
        lineage["layout_revision"] = versions[LAYOUT]
        lineage["layout_updated_at"] = str(
            before.get("layout_updated_at") or ""
        )
        lineage["runtime_revision"] = versions[RUNTIME]
        lineage["runtime_updated_at"] = str(
            before.get("runtime_updated_at") or ""
        )
    elif domain == LAYOUT:
        lineage["revision"] = versions[GRAPH]
        lineage["graph_updated_at"] = str(
            before.get("graph_updated_at")
            or before.get("updated_at")
            or ""
        )
        lineage["updated_at"] = str(before.get("updated_at") or "")
        lineage["layout_revision"] = versions[LAYOUT] + 1
        lineage["layout_updated_at"] = now
        lineage["runtime_revision"] = versions[RUNTIME]
        lineage["runtime_updated_at"] = str(
            before.get("runtime_updated_at") or ""
        )
    else:
        lineage["revision"] = versions[GRAPH]
        lineage["graph_updated_at"] = str(
            before.get("graph_updated_at")
            or before.get("updated_at")
            or ""
        )
        lineage["updated_at"] = str(before.get("updated_at") or "")
        lineage["layout_revision"] = versions[LAYOUT]
        lineage["layout_updated_at"] = str(
            before.get("layout_updated_at") or ""
        )
        lineage["runtime_revision"] = versions[RUNTIME] + 1
        lineage["runtime_updated_at"] = now
    return lineage


def stamp_additional_runtime_change(
    lineage: dict[str, Any],
    previous: object,
    *,
    now: str,
) -> dict[str, Any]:
    """Advance runtime alongside a graph edit that fenced executable work."""

    before = previous if isinstance(previous, dict) else {}
    lineage["runtime_revision"] = (
        domain_versions(before)[RUNTIME] + 1
    )
    lineage["runtime_updated_at"] = now
    return lineage


def _by_id(items: object) -> dict[str, dict[str, Any]]:
    if not isinstance(items, list):
        return {}
    return {
        str(item.get("id") or ""): item
        for item in items
        if isinstance(item, dict) and str(item.get("id") or "")
    }


def preserve_layout(
    previous: object,
    candidate: dict[str, Any],
) -> dict[str, Any]:
    """Keep existing positions out of a stale graph-authoring payload.

    New objects retain the position supplied by their create command.
    """

    before = previous if isinstance(previous, dict) else {}
    old_nodes = _by_id(before.get("nodes"))
    for node in candidate.get("nodes") or []:
        old = old_nodes.get(str(node.get("id") or ""))
        if old:
            node["x"] = old.get("x", node.get("x"))
            node["y"] = old.get("y", node.get("y"))

    old_program = (
        before.get("program")
        if isinstance(before.get("program"), dict)
        else {}
    )
    new_program = (
        candidate.get("program")
        if isinstance(candidate.get("program"), dict)
        else {}
    )
    old_components = _by_id(old_program.get("components"))
    for component in new_program.get("components") or []:
        old = old_components.get(str(component.get("id") or ""))
        if not old:
            continue
        if "x" in old:
            component["x"] = old["x"]
        else:
            component.pop("x", None)
        if "y" in old:
            component["y"] = old["y"]
        else:
            component.pop("y", None)
    old_blocks = _by_id(
        before.get("function_blocks")
    )
    for block in candidate.get("function_blocks") or []:
        old = old_blocks.get(str(block.get("id") or ""))
        if not old:
            continue
        block["x"] = old.get("x", block.get("x"))
        block["y"] = old.get("y", block.get("y"))
    return candidate


def preserve_runtime(
    previous: object,
    candidate: dict[str, Any],
) -> dict[str, Any]:
    """Prevent a graph form snapshot from overwriting live execution state."""

    before = previous if isinstance(previous, dict) else {}
    old_nodes = _by_id(before.get("nodes"))
    for node in candidate.get("nodes") or []:
        old = old_nodes.get(str(node.get("id") or ""))
        if not old:
            continue
        node["status"] = old.get("status", node.get("status"))
        node["last_execution"] = deepcopy(old.get("last_execution") or {})

    old_edges = _by_id(before.get("edges"))
    candidate_edges = [
        edge
        for edge in candidate.get("edges") or []
        if (
            isinstance(edge, dict)
            and str(edge.get("type") or "") != "absorption"
        )
    ]
    candidate_resident_ids = {
        str(node.get("id") or "")
        for node in candidate.get("nodes") or []
        if isinstance(node, dict) and str(node.get("id") or "")
    }
    # Absorption is a short-lived execution projection. A graph-authoring
    # form must neither resurrect its stale copy nor erase the current copy.
    # If an endpoint was structurally deleted, the projection is discarded.
    candidate_edges.extend(
        deepcopy(edge)
        for edge in before.get("edges") or []
        if (
            isinstance(edge, dict)
            and str(edge.get("type") or "") == "absorption"
            and str(edge.get("source") or "") in candidate_resident_ids
            and str(edge.get("target") or "") in candidate_resident_ids
        )
    )
    candidate["edges"] = candidate_edges
    for edge in candidate_edges:
        old = old_edges.get(str(edge.get("id") or ""))
        if not old:
            continue
        if str(old.get("type") or "") == "handoff":
            for field in HANDOFF_RUNTIME_FIELDS:
                if field in old:
                    edge[field] = deepcopy(old[field])
            old_items = {
                str(item.get("item_id") or ""): item
                for item in old.get("items") or []
                if isinstance(item, dict)
                and str(item.get("item_id") or "")
            }
            for item in edge.get("items") or []:
                if not isinstance(item, dict):
                    continue
                old_item = old_items.get(
                    str(item.get("item_id") or "")
                )
                if not old_item:
                    continue
                item["attempts"] = deepcopy(
                    old_item.get("attempts") or []
                )
                item["result"] = deepcopy(old_item.get("result") or {})
                if old_item.get("result"):
                    old_target = (
                        old_item.get("target")
                        if isinstance(old_item.get("target"), dict)
                        else {}
                    )
                    target = (
                        item.get("target")
                        if isinstance(item.get("target"), dict)
                        else {}
                    )
                    for field in ("workspace_id", "path", "scope"):
                        if field in old_target:
                            target[field] = deepcopy(old_target[field])
                    item["target"] = target
    return candidate


def prepare_graph_candidate(
    previous: object,
    candidate: dict[str, Any],
) -> dict[str, Any]:
    """Apply the graph-write ownership boundary to one normalized candidate."""

    preserve_layout(previous, candidate)
    preserve_runtime(previous, candidate)
    ensure_metadata(candidate, previous)
    return candidate


def runtime_projection(lineage: object) -> dict[str, Any]:
    """Return only state owned by HATG execution protocols."""

    item = lineage if isinstance(lineage, dict) else {}
    nodes: dict[str, dict[str, Any]] = {}
    for node in item.get("nodes") or []:
        if not isinstance(node, dict) or not str(node.get("id") or ""):
            continue
        projection = {
            "status": deepcopy(node.get("status")),
            "last_execution": deepcopy(node.get("last_execution") or {}),
        }
        projection = {
            key: value
            for key, value in projection.items()
            if value not in (None, "", [], {})
        }
        if projection:
            nodes[str(node["id"])] = projection

    edges: dict[str, dict[str, Any]] = {}
    for edge in item.get("edges") or []:
        if not isinstance(edge, dict) or not str(edge.get("id") or ""):
            continue
        edge_type = str(edge.get("type") or "")
        projection: dict[str, Any] = {}
        if edge_type == "absorption":
            projection["absorption"] = deepcopy(edge)
        if edge_type == "handoff":
            projection["handoff"] = {
                **{
                    field: deepcopy(edge[field])
                    for field in HANDOFF_RUNTIME_FIELDS
                    if field in edge
                },
                "items": [
                    {
                        "item_id": str(entry.get("item_id") or ""),
                        "attempts": deepcopy(entry.get("attempts") or []),
                        "result": deepcopy(entry.get("result") or {}),
                        "target": {
                            field: deepcopy(entry.get("target", {}).get(field))
                            for field in ("workspace_id", "path", "scope")
                            if isinstance(entry.get("target"), dict)
                            and entry.get("target", {}).get(field) not in (None, "")
                        },
                    }
                    for entry in edge.get("items") or []
                    if isinstance(entry, dict)
                    and (
                        entry.get("attempts")
                        or entry.get("result")
                        or (
                            isinstance(entry.get("target"), dict)
                            and any(
                                entry["target"].get(field) not in (None, "")
                                for field in ("workspace_id", "path", "scope")
                            )
                        )
                    )
                ],
            }
        projection = {
            key: value
            for key, value in projection.items()
            if value not in (None, "", [], {})
        }
        if projection:
            edges[str(edge["id"])] = projection

    output = {
        "nodes": nodes,
        "edges": edges,
    }
    return {
        key: value
        for key, value in output.items()
        if value not in (None, "", [], {})
    }


def definition_projection(lineage: object) -> dict[str, Any]:
    """Return the HATG definition without mutable execution state."""

    item = deepcopy(lineage) if isinstance(lineage, dict) else {}
    for field in ("runtime_revision", "runtime_updated_at"):
        item.pop(field, None)

    static_nodes: list[dict[str, Any]] = []
    for raw in item.get("nodes") or []:
        if not isinstance(raw, dict):
            continue
        node = deepcopy(raw)
        node.pop("status", None)
        node.pop("last_execution", None)
        for field in ("home_lineage_id", "hierarchy_reference", "accessible"):
            node.pop(field, None)
        static_nodes.append(node)
    item["nodes"] = static_nodes

    static_edges: list[dict[str, Any]] = []
    for raw in item.get("edges") or []:
        if not isinstance(raw, dict) or str(raw.get("type") or "") == "absorption":
            continue
        edge = deepcopy(raw)
        edge.pop("absorption", None)
        for field in HANDOFF_RUNTIME_FIELDS:
            edge.pop(field, None)
        for entry in edge.get("items") or []:
            if isinstance(entry, dict):
                entry.pop("attempts", None)
                entry.pop("result", None)
        static_edges.append(edge)
    item["edges"] = static_edges

    for field in OPTIONAL_DEFINITION_COLLECTION_FIELDS:
        if item.get(field) in (None, [], {}):
            item.pop(field, None)
    return item


def persisted_runtime_state(lineage: object) -> dict[str, Any]:
    item = lineage if isinstance(lineage, dict) else {}
    state = {
        "schema": RUNTIME_STATE_SCHEMA,
        "lineage_id": str(item.get("lineage_id") or ""),
        "runtime_revision": _non_negative_integer(item.get("runtime_revision")),
        "updated_at": str(item.get("runtime_updated_at") or ""),
        **runtime_projection(item),
    }
    return {
        key: value
        for key, value in state.items()
        if key in {"schema", "lineage_id", "runtime_revision"}
        or value not in (None, "", [], {})
    }


def validate_runtime_state(
    runtime_state: object,
    *,
    definition: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate the only persisted HATG runtime-state representation."""

    if not isinstance(runtime_state, Mapping):
        raise ValueError("Lineage runtime state must be an object")
    state = deepcopy(dict(runtime_state))
    unknown = set(state) - RUNTIME_STATE_FIELDS
    if unknown:
        raise ValueError(
            "Lineage runtime state contains unsupported fields: "
            + ", ".join(sorted(unknown))
        )
    if not schema_matches(state.get("schema"), "tfi.lineage-runtime-state.v1"):
        raise ValueError("Lineage runtime state schema is invalid")
    lineage_id = str(state.get("lineage_id") or "")
    if not lineage_id:
        raise ValueError("Lineage runtime state requires lineage_id")
    revision = state.get("runtime_revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise ValueError("Lineage runtime state revision must be non-negative")
    if "updated_at" in state and not isinstance(state["updated_at"], str):
        raise ValueError("Lineage runtime state updated_at must be a string")
    for field in ("nodes", "edges"):
        values = state.get(field, {})
        if not isinstance(values, Mapping) or not all(
            isinstance(key, str) and key and isinstance(value, Mapping)
            for key, value in values.items()
        ):
            raise ValueError(f"Lineage runtime state {field} must be an object map")

    if definition is None:
        return state
    definition_id = str(definition.get("lineage_id") or "")
    if lineage_id != definition_id:
        raise ValueError("Lineage runtime state identity does not match")
    node_ids = set(_by_id(definition.get("nodes")))
    stale_nodes = set(state.get("nodes") or {}) - node_ids
    if stale_nodes:
        raise ValueError(
            "Lineage runtime state references unknown nodes: "
            + ", ".join(sorted(stale_nodes))
        )
    edge_ids = set(_by_id(definition.get("edges")))
    stale_edges = {
        edge_id
        for edge_id, projection in (state.get("edges") or {}).items()
        if edge_id not in edge_ids
        and not isinstance(projection.get("absorption"), Mapping)
    }
    if stale_edges:
        raise ValueError(
            "Lineage runtime state references unknown edges: "
            + ", ".join(sorted(stale_edges))
        )
    return state


def merge_runtime_state(
    definition: dict[str, Any],
    runtime_state: object,
) -> dict[str, Any]:
    """Build the in-memory Lineage aggregate from its two persisted facts."""

    state = validate_runtime_state(runtime_state, definition=definition)
    return merge_validated_runtime_state(deepcopy(definition), state)


def merge_validated_runtime_state(
    merged: dict[str, Any],
    state: Mapping[str, Any],
) -> dict[str, Any]:
    """``merge_runtime_state`` into a definition the caller owns, with a runtime state it already validated."""

    for field in OPTIONAL_DEFINITION_COLLECTION_FIELDS:
        merged.setdefault(field, [])
    merged["runtime_revision"] = _non_negative_integer(
        state.get("runtime_revision")
    )
    merged["runtime_updated_at"] = str(state.get("updated_at") or "")

    nodes = _by_id(merged.get("nodes"))
    for node in nodes.values():
        node.setdefault("status", "idle")
        node.setdefault("last_execution", {})
    for node_id, projection in (state.get("nodes") or {}).items():
        node = nodes.get(str(node_id))
        if node is None or not isinstance(projection, dict):
            continue
        if "status" in projection:
            node["status"] = deepcopy(projection["status"])
        if projection.get("last_execution"):
            node["last_execution"] = deepcopy(projection["last_execution"])
    edges = _by_id(merged.get("edges"))
    for edge_id, projection in (state.get("edges") or {}).items():
        if not isinstance(projection, dict):
            continue
        absorption = projection.get("absorption")
        if isinstance(absorption, dict):
            absorption_id = str(absorption.get("id") or edge_id)
            if absorption_id not in edges:
                copied = deepcopy(absorption)
                merged.setdefault("edges", []).append(copied)
                edges[absorption_id] = copied
            continue
        edge = edges.get(str(edge_id))
        if edge is None:
            continue
        handoff = projection.get("handoff")
        if isinstance(handoff, dict):
            for field in HANDOFF_RUNTIME_FIELDS:
                if field in handoff:
                    edge[field] = deepcopy(handoff[field])
            runtime_items = {
                str(entry.get("item_id") or ""): entry
                for entry in handoff.get("items") or []
                if isinstance(entry, dict) and str(entry.get("item_id") or "")
            }
            for entry in edge.get("items") or []:
                if not isinstance(entry, dict):
                    continue
                runtime_item = runtime_items.get(str(entry.get("item_id") or ""))
                if runtime_item is None:
                    continue
                for field in ("attempts", "result"):
                    if runtime_item.get(field):
                        entry[field] = deepcopy(runtime_item[field])
                if runtime_item.get("target"):
                    target = (
                        entry.get("target")
                        if isinstance(entry.get("target"), dict)
                        else {}
                    )
                    target.update(deepcopy(runtime_item["target"]))
                    entry["target"] = target
    return merged


def runtime_projection_changed(
    previous: object,
    candidate: object,
) -> bool:
    return runtime_projection(previous) != runtime_projection(candidate)


def apply_layout_patch(
    lineage: dict[str, Any],
    positions: object,
    *,
    normalize_position: Callable[[object, float], float],
) -> dict[str, Any]:
    """Merge positions by canonical HATG object id.

    Missing objects are ignored because deleting an object in another page is
    not a layout conflict. The response tells the caller which ids were
    applied and which were already gone.
    """

    if not isinstance(positions, list):
        raise ValueError("positions must be a list")
    if len(positions) > 512:
        raise ValueError("too many HATG layout positions")

    targets: dict[str, tuple[str, dict[str, Any]]] = {}
    for kind, values in (("resident", lineage.get("nodes")),):
        for value in values or []:
            if isinstance(value, dict) and str(value.get("id") or ""):
                targets[str(value["id"])] = (kind, value)
    program = (
        lineage.get("program")
        if isinstance(lineage.get("program"), dict)
        else {}
    )
    for value in program.get("components") or []:
        if isinstance(value, dict) and str(value.get("id") or ""):
            targets[str(value["id"])] = ("component", value)
    for value in lineage.get("function_blocks") or []:
        if isinstance(value, dict) and str(value.get("id") or ""):
            targets[str(value["id"])] = (
                "function_block",
                value,
            )

    requested: dict[str, dict[str, Any]] = {}
    for raw in positions:
        if not isinstance(raw, dict):
            raise ValueError("each layout position must be an object")
        object_id = str(
            raw.get("object_id") or raw.get("id") or ""
        ).strip()
        if not object_id:
            raise ValueError("layout position object_id required")
        requested[object_id] = raw

    applied: list[dict[str, Any]] = []
    missing: list[str] = []
    for object_id, raw in requested.items():
        target_entry = targets.get(object_id)
        if not target_entry:
            missing.append(object_id)
            continue
        kind, target = target_entry
        fallback_x = float(target.get("x") or 0)
        fallback_y = float(target.get("y") or 0)
        target["x"] = normalize_position(raw.get("x"), fallback_x)
        target["y"] = normalize_position(raw.get("y"), fallback_y)
        applied.append(
            {
                "object_id": object_id,
                "object_kind": kind,
                "x": target["x"],
                "y": target["y"],
            }
        )
    return {
        "applied": applied,
        "missing_object_ids": missing,
    }
