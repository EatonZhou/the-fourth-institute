"""Signed transport for Society RPCs, independent of the Society owner."""
from __future__ import annotations

import json
import os
import secrets
import socket
from contextlib import closing
from pathlib import Path
from typing import Any

from .instance_manifest import instance_runtime_root
from .operation_errors import OperationError, remote_failure
from .rpc_auth import request_auth
from .service_socket import SERVICE_RESTART_WAIT_SECONDS, connect_service


def society_service_socket() -> Path:
    configured = str(os.getenv("PORTAL_SOCIETY_SERVICE_SOCKET", "") or "").strip()
    return Path(configured) if configured else instance_runtime_root() / "s/society-service.sock"


def request_society(
    socket_path: Path,
    method: str,
    params: dict[str, Any],
    *,
    timeout: float,
    restart_wait: float,
) -> dict[str, Any]:
    """Send exactly one signed request, retaining its response envelope."""
    request_id = f"society-service-{secrets.token_hex(10)}"
    try:
        connection, _ = connect_service(socket_path, timeout=timeout, restart_wait=restart_wait)
    except OSError as exc:
        raise OperationError(
            f"SocietyService unavailable at {socket_path}: {exc}",
            code="lineage.unavailable", http_status=503, commit_state="not_committed", retryable=True,
        ) from exc

    try:
        with closing(connection):
            payload = {"id": request_id, "method": method, "params": params,
                       "auth": request_auth(method, params, socket_path=socket_path)}
            connection.sendall(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n")
            with closing(connection.makefile("rb")) as stream:
                raw = stream.readline()
        if not raw:
            raise OperationError("SocietyService closed the connection without a response",
                                 code="lineage.unavailable", http_status=503)
        response = json.loads(raw)
    except socket.timeout as exc:
        raise OperationError(f"SocietyService busy at {socket_path}: {exc}",
                             code="lineage.busy", http_status=503) from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OperationError(f"SocietyService unavailable at {socket_path}: {exc}",
                             code="lineage.unavailable", http_status=503) from exc
    if not isinstance(response, dict) or response.get("id") != request_id:
        raise OperationError("SocietyService returned an invalid response envelope",
                             code="lineage.unavailable", http_status=503)
    return response


def _resident_call(name: str, kwargs: dict[str, str], *, timeout: float, restart_wait: float) -> dict[str, Any]:
    response = request_society(
        society_service_socket(), "domain.call", {"domain": "resident", "name": name, "args": [], "kwargs": kwargs},
        timeout=timeout, restart_wait=restart_wait,
    )
    if not response.get("ok"):
        error = remote_failure(response.get("error"))
        if error.code == "operation.forbidden":
            raise PermissionError(str(error)) from error
        if error.code == "operation.not_found":
            raise KeyError(str(error)) from error
        if error.code == "operation.invalid":
            raise ValueError(str(error)) from error
        raise error
    result = response.get("result")
    if not isinstance(result, dict):
        raise OperationError("SocietyService returned an invalid domain result", code="contract.invalid_result")
    return result


def resolve_input_execution(*, actor: str, resident_id: str, message_id: str) -> dict[str, Any]:
    return _resident_call(
        "resolve_input_execution", {"actor": actor, "resident_id": resident_id, "message_id": message_id},
        timeout=10, restart_wait=0,
    )


def prepare_provider_runtime_update(*, provider: str, update_id: str) -> dict[str, Any]:
    result = _resident_call(
        "prepare_provider_runtime_update", {"provider": provider, "update_id": update_id},
        timeout=30, restart_wait=SERVICE_RESTART_WAIT_SECONDS,
    )
    count = result.get("prepared")
    if type(count) is not int or count < 0:
        raise OperationError("SocietyService returned an invalid maintenance receipt", code="contract.invalid_result")
    return result
