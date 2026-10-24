"""Deployment-owned, non-secret site settings, read afresh by each caller."""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path

DEFAULT_SITE_NAME = "TFI Portal"
PORTAL_ROOT = Path(__file__).resolve().parents[1]


def read_site_config() -> dict:
    configured = os.environ.get("PORTAL_SITE_CONFIG", "").strip()
    path = Path(configured).expanduser() if configured else PORTAL_ROOT / "config" / "site.json"
    if not path.is_absolute():
        path = PORTAL_ROOT / path
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict):
        raise ValueError("Site configuration must be a JSON object")
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
    """Project stable installation identity, never a build or display name.

    Installers persist installation_id. Existing/source deployments without it
    retain a scope derived from their configured source root across upgrades.
    Only the digest, not the path or any credentials, reaches the browser.
    """
    deployment = read_site_config().get("deployment", {})
    identity = deployment.get("installation_id")
    if identity is not None:
        if not isinstance(identity, str) or not identity.strip():
            raise ValueError("deployment.installation_id must be a nonempty string")
        identity = "installation:" + identity.strip()
    else:
        root = Path(deployment.get("source_root", str(PORTAL_ROOT))).expanduser()
        if not root.is_absolute():
            root = PORTAL_ROOT / root
        identity = "source:" + str(root.resolve())
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()
