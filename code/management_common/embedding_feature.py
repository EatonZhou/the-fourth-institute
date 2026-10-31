"""Portal-wide switch and model location for optional Resident embeddings."""

from __future__ import annotations

import json
import os
from pathlib import Path

from management_common.portal_paths import path as portal_path


def settings_file() -> Path:
    return portal_path("instances_root") / "settings" / "resident_embeddings.json"


def model_directory() -> Path:
    configured = os.getenv("PORTAL_RESIDENT_EMBEDDING_MODEL_DIR")
    return Path(configured).expanduser() if configured else portal_path("instances_root") / "models" / "resident-embeddings"


def platform_issue() -> str | None:
    """Wheel availability is determined by installation, not a historical release."""
    return None


def enabled() -> bool:
    """Fresh installations are lexical-only, even if packages happen to exist."""
    try:
        payload = json.loads(settings_file().read_text(encoding="utf-8"))
        return payload.get("enabled") is True
    except (OSError, ValueError, TypeError):
        return False
