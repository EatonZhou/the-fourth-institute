"""Registered Projects: the registry record and the folder of a Project ID."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from management_common.instance_locations import current_instance
from management_portal import project_registry


def project_root_from_registry(project_id: str) -> Path | None:
    try:
        return project_record_for_id(project_id)[1]
    except KeyError:
        return None


def project_root_for_id(project_id: str) -> Path:
    root = project_root_from_registry(project_id)
    if root is None or not root.is_dir():
        raise ValueError(f"项目不存在: {project_id}")
    return root


def project_record_for_id(project_id: str) -> tuple[dict[str, Any], Path]:
    """Resolve only registered identity/path pairs through the Project registry."""
    return project_record_from_document(project_registry.load(), project_id)


def project_record_from_document(
    document: dict[str, Any], project_id: str
) -> tuple[dict[str, Any], Path]:
    target = str(project_id or "").strip()
    for item in document["projects"]:
        if not isinstance(item, dict):
            continue
        registry_id = str(item.get("id") or "").strip()
        if target != registry_id:
            continue
        raw_path = str(item.get("path") or "").strip()
        project_path = (
            current_instance().resolve(raw_path)
            if raw_path
            else None
        )
        if project_path is None:
            raise ValueError(f"project path missing: {target}")
        return dict(item), project_path
    raise KeyError("project not found")
