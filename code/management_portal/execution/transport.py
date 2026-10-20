from __future__ import annotations

import json
import secrets
import socket
from pathlib import Path
from typing import Any

from management_common.service_socket import SERVICE_RESTART_WAIT_SECONDS, connect_service

from .config import execution_service_socket, portal_instance


DEFAULT_REQUEST_TIMEOUT_SECONDS = 90.0
CONTROL_REQUEST_TIMEOUT_SECONDS = 15.0


class ExecutionServiceUnavailable(RuntimeError):
    pass


class ExecutionResponseUnknown(RuntimeError):
    """The connection accepted a request but its result was not observed."""


class ExecutionClaimOwnershipLost(RuntimeError):
    """The Scheduler fenced this Worker out of an Execution claim."""


class ExecutionServiceClient:
    def __init__(
        self,
        socket_path: Path | str | None = None,
        *,
        timeout: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
        restart_wait: float = SERVICE_RESTART_WAIT_SECONDS,
    ) -> None:
        self.restart_wait = max(0.0, float(restart_wait))
        if socket_path is not None:
            self.socket_paths = (Path(socket_path),)
        else:
            current = portal_instance()
            candidates = [execution_service_socket(current)]
            if current in {"dev", "release"}:
                peer = "release" if current == "dev" else "dev"
                candidates.append(execution_service_socket(peer))
            self.socket_paths = tuple(dict.fromkeys(candidates))
        self.socket_path = self.socket_paths[0]
        self.timeout = max(0.1, float(timeout))

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
        retry_on_unavailable: bool = True,
        restart_wait: float | None = None,
    ) -> Any:
        # This instance's service first, then its peer's; while neither
        # accepts, the request waits for one to come back.  Without
        # ``retry_on_unavailable`` it stays on this instance's service.
        socket_paths = self.socket_paths if retry_on_unavailable else self.socket_paths[:1]
        if not socket_paths:
            raise ExecutionServiceUnavailable("ExecutionService has no endpoint")
        return self._request_at(
            socket_paths,
            method,
            params,
            timeout=timeout,
            restart_wait=self.restart_wait if restart_wait is None else restart_wait,
        )

    def _request_at(
        self,
        socket_paths: tuple[Path, ...],
        method: str,
        params: dict[str, Any] | None,
        *,
        timeout: float | None,
        restart_wait: float,
    ) -> Any:
        request_id = f"execution-service-{secrets.token_hex(10)}"
        payload = {
            "id": request_id,
            "method": str(method or ""),
            "params": params or {},
        }
        connection: socket.socket | None = None
        connected = False
        socket_path = socket_paths[0]
        try:
            response_timeout = (
                self.timeout if timeout is None else max(0.1, float(timeout))
            )
            connection, socket_path = connect_service(
                socket_paths,
                timeout=min(response_timeout, CONTROL_REQUEST_TIMEOUT_SECONDS),
                restart_wait=restart_wait,
            )
            connected = True
            connection.settimeout(response_timeout)
            from management_common.rpc_auth import request_auth

            payload["auth"] = request_auth(
                payload["method"], payload["params"], socket_path=socket_path,
                actor=str(payload["params"].get("actor") or ""),
            )
            connection.sendall(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                + b"\n"
            )
            stream = connection.makefile("rb")
            raw = stream.readline()
        except (OSError, TimeoutError) as exc:
            error = ExecutionResponseUnknown if connected else ExecutionServiceUnavailable
            raise error(f"ExecutionService request failed at {socket_path}: {type(exc).__name__}") from exc
        finally:
            if connection is not None:
                connection.close()
        if not raw:
            raise ExecutionResponseUnknown(
                "ExecutionService closed the connection without a response"
            )
        try:
            response = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ExecutionResponseUnknown(
                "ExecutionService returned invalid JSON"
            ) from exc
        if not isinstance(response, dict) or response.get("id") != request_id:
            raise ExecutionResponseUnknown(
                "ExecutionService returned an invalid response envelope"
            )
        if not response.get("ok"):
            error = response.get("error")
            details = error if isinstance(error, dict) else {}
            message = str(details.get("message") or "ExecutionService request failed")
            error_class = str(details.get("class") or "RuntimeError")
            if error_class == "KeyError":
                raise KeyError(message)
            if error_class == "ValueError":
                raise ValueError(message)
            if (
                error_class == "PermissionError"
                and message == "execution claim ownership changed"
            ):
                raise ExecutionClaimOwnershipLost(message)
            if error_class == "PermissionError":
                raise PermissionError(message)
            raise RuntimeError(message)
        return response.get("result")

    def health(self) -> dict[str, Any]:
        result = self.request(
            "health", timeout=min(self.timeout, CONTROL_REQUEST_TIMEOUT_SECONDS),
            restart_wait=0,
        )
        return result if isinstance(result, dict) else {}
