"""Resident domain responsibilities: project."""
from __future__ import annotations
from management_common.portal_paths import path as portal_path

from pathlib import Path
from typing import Any

from management_portal import project_registry

from .project_domain import project_record_for_id
from . import resident_domain_common


CODES_ROOT = (portal_path("share_root") / "Codes")


def load_projects_registry() -> list[dict[str, Any]]:
    return project_registry.projects()


def project_info_for_workspace(record: dict, workspace_path: Path) -> dict[str, str]:
    project_id = str(record.get("project_id") or "").strip()
    project_record: dict[str, Any] = {}
    project_root: Path | None = None
    if project_id:
        try:
            project_record, project_root = project_record_for_id(project_id)
        except KeyError:
            pass
    project_name = project_root.name if project_root else ""
    return {
        "project_id": project_id,
        "project_name": project_name,
        "project_path": str(project_root or ""),
        "project_registry_id": str(project_record.get("id") or "") if project_record else "",
    }


def safe_absolute_path(raw: str) -> Path:
    if not raw.strip():
        raise ValueError("workspace_root 不能为空")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise ValueError("workspace_root 必须是绝对路径")
    resolved = path.resolve(strict=False)
    allowed = [
        resident_domain_common.AGENTS_ROOT.resolve(strict=False),
        resident_domain_common.PROJECTS_ROOT.resolve(strict=False),
        CODES_ROOT.resolve(strict=False),
        resident_domain_common.STAGING_ROOT.resolve(strict=False),
    ]
    if not any(resolved == root or root in resolved.parents for root in allowed):
        raise ValueError("workspace_root 只能位于当前实例的目录：" + "、".join(map(str, allowed)))
    return resolved


def optional_absolute_path(raw: str) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError("开发地址必须是绝对路径")
    return str(path.resolve(strict=False))
