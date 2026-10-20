"""Read-only projections that combine Lineage data with live Resident data."""

from __future__ import annotations

from management_common.principal import is_user_or_resident

import json
from typing import Any, Callable

from . import lineage_program, lineage_state_domains, resident_catalog, resident_protocol
from .domain_ports import resident_domain_port
from .lineage_values import _clone_json, _text


def node_resident_id(node: dict[str, Any]) -> str:
    return _text(node.get("id"), 80).lower()


def node_status(value: object) -> str:
    state = _text(value, 40).lower()
    if state in {"running", "stopping", "working"}:
        return "running"
    if state in {"creating", "queued", "pending", "starting"}:
        return "queued"
    if state in {"blocked", "failed", "error"}:
        return "blocked"
    if state in {"done", "completed", "succeeded"}:
        return "done"
    return "idle"


def project_resident_runtime_state(view: dict[str, Any], actor: str) -> None:
    nodes = [
        node for node in view.get("nodes") or []
        if isinstance(node, dict) and node_resident_id(node) and node.get("accessible") is not False
    ]
    states = resident_protocol.read_resident_states([
        node_resident_id(node) for node in nodes
    ])
    for node in nodes:
        resident_id = node_resident_id(node)
        if resident_id not in states:
            continue
        node["status"] = node_status((states[resident_id].get("family_activity") or states[resident_id]).get("work_state"))


def project_handoff_execution(view: dict[str, Any], actor: str) -> None:
    """Overlay each launch record with the state of its exact Resident input."""
    nodes_by_id = {
        str(node.get("id") or ""): node
        for node in view.get("nodes") or []
        if isinstance(node, dict)
    }
    for edge in view.get("edges") or []:
        if not isinstance(edge, dict) or str(edge.get("type") or "") != "handoff":
            continue
        execution = edge.get("execution") if isinstance(edge.get("execution"), dict) else {}
        message_id = str(execution.get("message_id") or "").strip()
        if not message_id or str(execution.get("status") or "") not in {"queued", "running", "sent"}:
            continue
        target = nodes_by_id.get(str(edge.get("target") or ""))
        if not target:
            continue
        if str(target.get("status") or "") == "running":
            execution["status"] = "running"
        try:
            input_state = resident_protocol.get_resident_input_state(
                node_resident_id(target), actor, message_id,
            )
        except (KeyError, PermissionError, OSError, ValueError):
            input_state = {}
        input_status = str(input_state.get("state") or "")
        if input_status in {"completed", "failed", "cancelled"}:
            execution["status"] = input_status
            execution["completed_at"] = str(input_state.get("updated_at") or "")
        edge["execution"] = execution


def verified_lineage_view(
    lineage: dict[str, Any],
    actor: str,
    *,
    seed_title: Callable[[str, dict[str, Any], dict[str, Any]], tuple[str, bool, str]],
) -> dict[str, Any]:
    """Build the authorized read model without changing aggregate state."""
    view = json.loads(json.dumps(lineage, ensure_ascii=False))
    lineage_state_domains.ensure_metadata(view, lineage)
    from .principal_hierarchy import project_node_access
    if (view.get("principal_hierarchy") or {}).get("level") == "project":
        project_node_access(view, resident_catalog.get_catalog().get_many(
            [node["id"] for node in view["nodes"]],
        ), lambda _identifier: is_user_or_resident(actor))
    project_resident_runtime_state(view, actor)
    project_handoff_execution(view, actor)
    try:
        view["program_diagnostics"] = lineage_program.program_diagnostics(
            view.get("program"), view.get("nodes") or [],
        )
    except (TypeError, ValueError) as exc:
        # Invalid authoring data must leave the identity and repair UI readable.
        view["program_diagnostics"] = []
        view["program_integrity"] = {"valid": False, "error": str(exc)}
    # Display fields are not authoring data: added after the Program is checked.
    lineage_id = str(view.get("lineage_id") or "")
    program = view.get("program") if isinstance(view.get("program"), dict) else {}
    for seed in program.get("components") or []:
        if not isinstance(seed, dict) or str(seed.get("kind") or "") != "seed":
            continue
        config = seed.get("config") if isinstance(seed.get("config"), dict) else {}
        seed_projection = {**seed, **config}
        display_title, artifact_exists, artifact_version = seed_title(lineage_id, view, seed_projection)
        seed["display_title"] = display_title
        seed["artifact_exists"] = artifact_exists
        seed["artifact_version"] = artifact_version
    return view


RESOURCE_PROJECTION_SCHEMA = "tfi.lineage-resource-projection.v1"
RESOURCE_MEMBER_LIMIT = 256


def resource_projection(
    clean_id: str, lineage: dict[str, Any], membership: str
) -> dict[str, Any]:
    """Build the bounded Resource read model from committed Lineage state."""
    nodes = [
        node
        for node in lineage.get("nodes") or []
        if isinstance(node, dict) and node_resident_id(node)
    ]
    resident_ids = [node_resident_id(node) for node in nodes]
    records = {
        str(record.get("resident_id") or ""): record
        for record in resident_catalog.get_catalog().get_many(resident_ids)
        if isinstance(record, dict)
    }
    resident_states = resident_protocol.read_resident_states(resident_ids)
    projected_members: list[dict[str, Any]] = []
    for node in nodes[:RESOURCE_MEMBER_LIMIT]:
        resident_id = node_resident_id(node)
        record = records.get(resident_id) or {}
        work_state = str(
            (resident_states.get(resident_id) or {}).get("work_state")
            or "idle"
        )
        status = node_status(work_state)
        error = str(
            record.get("last_error")
            or ""
        )
        projected = {
            "resident_id": resident_id,
            "title": _text(
                record.get("title")
                or node.get("title")
                or resident_id,
                500,
            ),
            "status": status,
            "state": work_state,
            "updated_at": _text(
                record.get("interaction_updated_at")
                or record.get("updated_at")
                or "",
                80,
            ),
            "is_leader": resident_id
            == str(lineage.get("leader_resident_id") or ""),
        }
        if error:
            projected["error"] = _text(error, 4000)
        projected_members.append(projected)
    statuses: dict[str, int] = {}
    for node in nodes:
        resident_id = node_resident_id(node)
        status = node_status(
            (resident_states.get(resident_id) or {}).get("work_state")
        )
        statuses[status] = statuses.get(status, 0) + 1
    return {
        "schema": RESOURCE_PROJECTION_SCHEMA,
        "resource_id": clean_id,
        "resource_type": "lineage",
        "lineage_id": clean_id,
        "title": _text(lineage.get("title") or clean_id, 500),
        "summary": _text(lineage.get("description"), 4000),
        "status": "deleted" if lineage.get("deleted_at") else "active",
        "lineage_membership": membership,
        "project_id": _text(lineage.get("project_id"), 120),
        "leader_resident_id": str(
            lineage.get("leader_resident_id") or ""
        ),
        "revision": max(0, int(lineage.get("revision") or 0)),
        "layout_revision": max(
            0,
            int(lineage.get("layout_revision") or 0),
        ),
        "runtime_revision": max(
            0,
            int(lineage.get("runtime_revision") or 0),
        ),
        "created_at": str(lineage.get("created_at") or ""),
        "updated_at": str(lineage.get("updated_at") or ""),
        "runtime_updated_at": str(
            lineage.get("runtime_updated_at") or ""
        ),
        "member_count": len(nodes),
        "edge_count": len(lineage.get("edges") or []),
        "statuses": statuses,
        "members": projected_members,
        "members_truncated": len(nodes) > len(projected_members),
    }

