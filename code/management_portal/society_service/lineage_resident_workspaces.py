"""The Workspaces of a Lineage node's Resident: binding, unbinding, listing and mounting reference Workspaces."""

from __future__ import annotations

from typing import Any

from .lineage_values import _clone_json, _safe_actor, _text
from . import lineage_workspace_binding, lineage_node_workspaces
from .lineage_view_projection import node_resident_id as _node_resident_id
from . import lineage_aggregate
from .domain_ports import resident_domain_port as resident_domain
from . import lineage_state


def _resident_workspace_roots(resident_id: str, actor: str) -> dict[str, Any]:
    return resident_domain.get_resident_workspace_roots(resident_id, actor)


def _mount_resident_reference_workspace(
    resident_id: str,
    actor: str,
    workspace_path: str,
    *,
    workspace_id: str = "",
    purpose: str = "",
) -> dict[str, Any]:
    return resident_domain.add_resident_reference_workspace(
        resident_id,
        workspace_path,
        actor,
        workspace_id=workspace_id,
        purpose=purpose,
    )


def _node_workspace_context(lineage_id: str, resident_id: str, actor: str) -> tuple[dict[str, Any], dict[str, Any]]:
    lineage = lineage_aggregate.read_lineage(lineage_id, actor=actor)
    lineage_state._ensure_visible(lineage, actor)
    node = next(
        (
            item for item in lineage.get("nodes") or []
            if isinstance(item, dict) and str(item.get("id") or "") == str(resident_id or "")
        ),
        None,
    )
    if not node:
        raise KeyError("Lineage node not found")
    return _clone_json(lineage), _clone_json(node)


def bind_node_workspace(lineage_id: str, payload: dict[str, Any], actor: str) -> dict[str, Any]:
    """Add another Workspace without replacing the node cwd."""
    actor_id = _safe_actor(actor)
    resident_id = _text(payload.get("resident_id"), 80).lower()
    mounted = add_node_reference_workspace(
        lineage_id, resident_id, actor_id, str(payload.get("path") or ""), purpose=str(payload.get("purpose") or ""),
    )
    lineage = lineage_aggregate.read_lineage(lineage_id, actor=actor_id)
    return {
        "lineage": lineage_state._verified_lineage_view(lineage, actor_id),
        "workspace": mounted["workspace"],
        "mount": mounted["mount"],
    }


def unbind_node_workspace(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    """Remove one Resident Workspace binding without deleting its files."""

    actor_id = _safe_actor(actor)
    resident_id, workspace_id, workspace_path = (
        lineage_workspace_binding.unbind_selector(payload)
    )
    _lineage, node = _node_workspace_context(
        lineage_id,
        resident_id,
        actor_id,
    )
    node_resident_id = _node_resident_id(node)
    if not node_resident_id:
        raise ValueError("节点尚未绑定 Resident")
    removed = resident_domain.remove_resident_reference_workspace(
        node_resident_id, actor_id,
        workspace_id=workspace_id,
        workspace_path=workspace_path,
    )
    lineage = lineage_aggregate.read_lineage(lineage_id, actor=actor_id)
    return {
        "lineage": lineage_state._verified_lineage_view(lineage, actor_id),
        "workspace_binding": removed,
    }


def get_node_workspaces(lineage_id: str, resident_id: str, actor: str) -> dict[str, Any]:
    """The node Resident's authorized Workspace bindings."""
    _, node = _node_workspace_context(lineage_id, resident_id, actor)
    resident_id = _node_resident_id(node)
    return lineage_node_workspaces.build_node_workspaces(
        lineage_id, node, resident_id, _resident_workspace_roots(resident_id, actor),
    )


def add_node_reference_workspace(
    lineage_id: str,
    resident_id: str,
    actor: str,
    workspace_path: str,
    *,
    workspace_id: str = "",
    purpose: str = "",
) -> dict[str, Any]:
    """Mount a reference directory through the selected node's Resident binding."""
    lineage, node = _node_workspace_context(lineage_id, resident_id, actor)
    return lineage_workspace_binding.mount_reference(
        lineage_id=lineage_id,
        node=node,
        lineage_nodes=lineage.get("nodes") or [],
        node_resident_id=_node_resident_id(node),
        actor=actor,
        workspace_path=workspace_path,
        workspace_id=workspace_id,
        purpose=purpose,
        mount=_mount_resident_reference_workspace,
        workspaces=get_node_workspaces,
        node_resident=_node_resident_id,
    )
