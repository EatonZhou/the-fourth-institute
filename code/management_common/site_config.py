"""Deployment-owned, non-secret site settings, read afresh by each caller."""
from __future__ import annotations

import hashlib
from management_common.instance_manifest import load

DEFAULT_SITE_NAME = "TFI Portal"


def read_site_config() -> dict:
    """Read feature settings from the explicitly selected instance."""
    data = load().site_settings()
    if "site_name" in data:
        name = data["site_name"]
        if not isinstance(name, str) or not name.strip() or any(ord(c) < 32 or ord(c) == 127 for c in name):
            raise ValueError("site_name must be a nonempty string without control characters")
        data["site_name"] = name.strip()
    return data


def site_name() -> str:
    return read_site_config().get("site_name", DEFAULT_SITE_NAME)


def portal_project_ids() -> list[str]:
    """Projects that own Portal's own development, such as its test cases and WALL entry."""
    deployment = read_site_config().get("deployment", {})
    config = deployment.get("development_projection", {}) if isinstance(deployment, dict) else None
    projects = config.get("project_ids", []) if isinstance(config, dict) else None
    if not isinstance(projects, list) or any(not isinstance(p, str) or not p.strip() for p in projects):
        raise ValueError("deployment.development_projection.project_ids must be a list of nonempty strings")
    return list(projects)


def browser_storage_namespace() -> str:
    """Stable instance identity survives moves, code upgrades and site renames."""
    return hashlib.sha256(("installation:" + load().instance_id).encode("utf-8")).hexdigest()
