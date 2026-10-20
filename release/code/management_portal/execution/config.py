from __future__ import annotations
from management_common.portal_paths import path as portal_path

import os
from pathlib import Path

from management_common.instance_paths import instance_root

DEFAULT_INSTANCE_ROOT = instance_root()


def portal_instance() -> str:
    return (
        str(os.getenv("PORTAL_INSTANCE") or "dev").strip()
        or "dev"
    )


def execution_service_socket(instance: str | None = None) -> Path:
    target_instance = str(instance or portal_instance()).strip() or "dev"
    configured = str(os.getenv("PORTAL_EXECUTION_SERVICE_SOCKET") or "").strip()
    if configured and target_instance == portal_instance():
        return Path(configured)
    if (
        len(target_instance) > 120
        or target_instance in {".", ".."}
        or "/" in target_instance
        or "\\" in target_instance
        or not all(
            character.isalnum() or character in "._-"
            for character in target_instance
        )
    ):
        raise ValueError("invalid Portal instance")
    return DEFAULT_INSTANCE_ROOT / target_instance / "execution-service.sock"


def execution_state_root() -> Path:
    configured = str(os.getenv("PORTAL_EXECUTION_STATE_ROOT") or "").strip()
    return Path(configured) if configured else (portal_path("staging_root") / "portal_execution")
