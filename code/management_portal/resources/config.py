from __future__ import annotations

import os
from pathlib import Path

from management_common.instance_manifest import instance_runtime_root


def resource_service_mode() -> str:
    """How this process reaches ResourceService.

    Only the instance's resource service runs it locally (``local``/``server``,
    set in its unit); every other process is a ``client`` of that service, so
    no process keeps a second resource index of its own.
    """
    return str(os.getenv("PORTAL_RESOURCE_SERVICE_MODE") or "client").strip()


def resource_service_socket() -> Path:
    configured = str(os.getenv("PORTAL_RESOURCE_SERVICE_SOCKET", "") or "").strip()
    return Path(configured) if configured else instance_runtime_root() / "s/resource-service.sock"


def resource_database_schema() -> str:
    """The configured schema of the one resource index shared by both deployments.

    A process that keeps the index locally must name it; only an isolated test
    database namespace may fall back to ``resources``.
    """
    configured = str(os.getenv("PORTAL_RESOURCE_DATABASE_SCHEMA") or "").strip()
    if configured:
        return configured
    if str(os.getenv("PORTAL_DATABASE_NAMESPACE") or "").strip():
        return "resources"
    raise RuntimeError(
        "PORTAL_RESOURCE_DATABASE_SCHEMA must name the resource index schema "
        "of a process that runs ResourceService locally"
    )


from management_common.rpc_auth import rpc_signing_key_file as resource_rpc_signing_key_file
