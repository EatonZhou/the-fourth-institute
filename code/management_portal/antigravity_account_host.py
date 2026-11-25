"""Connect a Worker to the supervised private host for one registered account."""
from __future__ import annotations

import hashlib
import time

from management_common import portal_paths
from management_common.local_ipc import connect_local
from management_common.provider_account_paths import validate_identity
from management_portal.antigravity_client import AccountAcpClient


def socket_path(account_id):
    # Keep the Unix socket name short even for long registration IDs.
    digest = hashlib.sha256(validate_identity(account_id).encode()).hexdigest()[:20]
    return portal_paths.path("runtime") / "s" / ("agy-" + digest + ".sock")


def connect_account(account_id, **callbacks):
    from scripts.portal_service_units import antigravity_account_service

    path = socket_path(account_id)
    try:
        connection = connect_local(path)
    except (FileNotFoundError, ConnectionRefusedError):
        antigravity_account_service(account_id, action="start")
        deadline = time.monotonic() + 20
        while True:
            try:
                connection = connect_local(path)
                break
            except (FileNotFoundError, ConnectionRefusedError):
                if time.monotonic() >= deadline:
                    raise TimeoutError("Antigravity account service did not become ready")
                time.sleep(0.1)
    return AccountAcpClient(connection, **callbacks)
