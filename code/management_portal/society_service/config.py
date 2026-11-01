from __future__ import annotations

import os
from pathlib import Path

from management_common.instance_manifest import instance_runtime_root


def society_service_mode() -> str:
    return str(os.getenv("PORTAL_SOCIETY_SERVICE_MODE", "local") or "local").strip()


def society_service_socket() -> Path:
    configured = str(os.getenv("PORTAL_SOCIETY_SERVICE_SOCKET", "") or "").strip()
    return Path(configured) if configured else instance_runtime_root() / "s/society-service.sock"
