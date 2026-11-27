"""Connect a Worker to the supervised private host for one registered account."""
from __future__ import annotations

import hashlib
import time

from management_common import portal_paths
from management_common.local_ipc import connect_local
from management_common.provider_account_paths import validate_identity
from management_portal.antigravity_client import AccountAcpClient


def socket_path(account_id, portal_only=False):
    # Keep the Unix socket name short even for long registration IDs.
    digest = hashlib.sha256(validate_identity(account_id).encode()).hexdigest()[:20]
    return portal_paths.path("runtime") / "s" / ("agy-" + digest + ("-restricted" if portal_only else "") + ".sock")


def connect_account(account_id, *, portal_only=False, **callbacks):
    from scripts.portal_service_units import antigravity_account_service

    path = socket_path(account_id, portal_only)
    deadline = time.monotonic() + 20
    next_start = 0.0
    while True:
        try:
            connection = connect_local(path)
            return AccountAcpClient(connection, **callbacks)
        except (FileNotFoundError, ConnectionRefusedError):
            now = time.monotonic()
            if now >= deadline:
                raise TimeoutError("Antigravity account service did not become ready")
            if now >= next_start:
                try:
                    antigravity_account_service(account_id, action="start")
                    next_start = deadline
                except BlockingIOError:
                    # Another first caller or instance update owns the lifecycle lock.
                    # Keep trying the socket while it registers and starts the service.
                    next_start = now + 1
            time.sleep(0.1)
