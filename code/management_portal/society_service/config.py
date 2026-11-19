from __future__ import annotations

import os

from management_common.society_rpc_client import society_service_socket as society_service_socket


def society_service_mode() -> str:
    return str(os.getenv("PORTAL_SOCIETY_SERVICE_MODE", "local") or "local").strip()
