from __future__ import annotations

import os
from pathlib import Path

from management_common.instance_manifest import load, instance_runtime_root


def instance_name() -> str:
    return load().name


def execution_service_socket() -> Path:
    configured = str(os.getenv("PORTAL_EXECUTION_SERVICE_SOCKET") or "").strip()
    return Path(configured) if configured else instance_runtime_root() / "s/execution-service.sock"


def execution_state_root() -> Path:
    # Activity and Harness artifacts are retained application data.
    from management_common.portal_paths import path
    return path('features') / 'execution'
