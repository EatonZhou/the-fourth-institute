"""Authorized node Workspace bindings."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .lineage_values import _text


def root_path(value: object) -> str:
    raw = str(value or "").strip()
    return str(Path(raw).expanduser().resolve(strict=False)) if raw else ""


def build_node_workspaces(
    lineage_id: str, node: dict[str, Any], resident_id: str, folder: dict[str, Any],
) -> dict[str, Any]:
    """The node's bound Workspaces, its own first; files inside them are read by ReadResource."""
    owned_id = folder.get("workspace_id")
    workspaces = [
        {
            "workspace_id": _text(item.get("workspace_id"), 80),
            **({"purpose": item["purpose"]} if "purpose" in item else {}),
            "label": str(item.get("label") or item.get("path") or item.get("workspace_id") or ""),
            "path": root_path(item.get("path")),
            "is_default_cwd": item.get("workspace_id") == owned_id,
            "exists": bool(item.get("exists", True)),
            "is_dir": bool(item.get("is_dir", True)),
        }
        for item in folder.get("roots") or [] if isinstance(item, dict)
    ]
    workspaces.sort(key=lambda item: (not item["is_default_cwd"], item["label"].casefold()))
    default_workspace = next((item for item in workspaces if item["is_default_cwd"]), None)
    return {
        "lineage_id": lineage_id,
        "node_id": str(node.get("id") or ""),
        "node_title": str(node.get("title") or ""),
        "resident_id": resident_id,
        "workspace_id": str((default_workspace or {}).get("workspace_id") or ""),
        "default_cwd": root_path(folder.get("default_cwd")) or str((default_workspace or {}).get("path") or ""),
        "default_workspace": default_workspace,
        "workspaces": workspaces,
    }
