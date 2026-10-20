"""Resident-owned Workspace binding operations exposed in Lineage context.

The Lineage graph is only consulted for membership and affected-node views;
Workspace Binding writes are made exclusively by the Resident service.
"""
from __future__ import annotations

from typing import Any, Callable

from .lineage_values import _text






def unbind_selector(payload: dict[str, Any]) -> tuple[str, str, str]:
    resident_id = _text(payload.get("resident_id"), 80).lower()
    workspace_id = _text(payload.get("workspace_id"), 100)
    workspace_path = str(payload.get("path") or "").strip()
    if not resident_id:
        raise ValueError("解除 Workspace 绑定需要 Resident")
    if not any((workspace_id, workspace_path)):
        raise ValueError("解除 Workspace 绑定需要 workspace_id 或 path")
    return resident_id, workspace_id, workspace_path


def mount_reference(
    *,
    lineage_id: str,
    node: dict[str, Any],
    lineage_nodes: list[dict[str, Any]],
    node_resident_id: str,
    actor: str,
    workspace_path: str,
    workspace_id: str,
    purpose: str = "",
    mount: Callable[..., dict[str, Any]],
    workspaces: Callable[[str, str, str], dict[str, Any]],
    node_resident: Callable[[dict[str, Any]], str],
) -> dict[str, Any]:
    if not node_resident_id:
        raise ValueError("节点尚未绑定 Resident")
    mounted = mount(
        node_resident_id, actor, workspace_path,
        workspace_id=workspace_id, purpose=purpose,
    )
    workspace = workspaces(lineage_id, str(node.get("id") or ""), actor)
    affected_nodes = [
        {"id": str(item.get("id") or ""), "title": str(item.get("title") or item.get("id") or "")}
        for item in lineage_nodes
        if isinstance(item, dict) and node_resident(item) == node_resident_id
    ]
    return {
        "lineage_id": lineage_id, "node_id": str(node.get("id") or ""),
        "resident_id": node_resident_id, "path": mounted["path"],
        "added": bool(mounted.get("added")), "mount": mounted,
        "binding_scope": "node", "affected_nodes": affected_nodes,
        "workspace": workspace,
    }
