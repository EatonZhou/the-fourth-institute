from __future__ import annotations

from .errors import LineageConflictError, SocietyServiceBusy, SocietyServiceUnavailable

import json
import secrets
import socket
from collections.abc import Mapping as MappingABC
from pathlib import Path
from typing import Any

from management_common.service_socket import SERVICE_RESTART_WAIT_SECONDS, connect_service

from .config import society_service_socket
from .contracts import DOMAIN_METHODS


class _DomainClient:
    def __init__(self, client: "SocietyServiceClient", domain: str) -> None:
        self._client = client
        self._domain = domain

    def __getattr__(self, method: str):
        if method not in DOMAIN_METHODS[self._domain]:
            raise AttributeError(f"unsupported {self._domain} method: {method}")

        def invoke(*args: Any, **kwargs: Any) -> Any:
            return self._client.call(self._domain, method, *args, **kwargs)

        return invoke


class SocietyServiceClient:
    """Thin client for all Lineage-owned domain and runtime operations."""

    def __init__(
        self,
        socket_path: Path | str | None = None,
        *,
        timeout: float = 120.0,
    ) -> None:
        self.socket_path = Path(socket_path or society_service_socket())
        self.timeout = max(0.1, float(timeout))
        self.resident = _DomainClient(self, "resident")
        self.workspace = _DomainClient(self, "workspace")
        self.project = _DomainClient(self, "project")
        self.lineage = _DomainClient(self, "lineage")
        self.projection = _DomainClient(self, "projection")
        self.service_invocation = _DomainClient(self, "service_invocation")

    @classmethod
    def _json_value(cls, value: Any) -> Any:
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, MappingABC):
            return {str(key): cls._json_value(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set, frozenset)):
            return [cls._json_value(item) for item in value]
        return value

    def _request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
        restart_wait: float = SERVICE_RESTART_WAIT_SECONDS,
    ) -> Any:
        request_id = f"society-service-{secrets.token_hex(10)}"
        payload = {
            "id": request_id,
            "method": str(method or ""),
            "params": self._json_value(params or {}),
        }
        connection: socket.socket | None = None
        stream = None
        try:
            try:
                connection, _path = connect_service(
                    self.socket_path,
                    timeout=self.timeout if timeout is None else max(0.1, float(timeout)),
                    restart_wait=restart_wait,
                )
            except OSError as exc:
                raise SocietyServiceUnavailable(
                    f"SocietyService unavailable at {self.socket_path}: {exc}",
                    commit_state="not_committed", retryable=True,
                ) from exc
            from management_common.rpc_auth import request_auth

            payload["auth"] = request_auth(payload["method"], payload["params"], socket_path=self.socket_path)
            connection.sendall(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
                    "utf-8"
                )
                + b"\n"
            )
            stream = connection.makefile("rb")
            raw = stream.readline()
            if not raw:
                raise SocietyServiceUnavailable(
                    "SocietyService closed the connection without a response"
                )
            response = json.loads(raw)
        except socket.timeout as exc:
            raise SocietyServiceBusy(
                f"SocietyService busy at {self.socket_path}: {exc}"
            ) from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise SocietyServiceUnavailable(
                f"SocietyService unavailable at {self.socket_path}: {exc}"
            ) from exc
        finally:
            if stream is not None:
                stream.close()
            if connection is not None:
                connection.close()
        if not isinstance(response, dict) or response.get("id") != request_id:
            raise SocietyServiceUnavailable(
                "SocietyService returned an invalid response envelope"
            )
        if not response.get("ok"):
            self._raise_remote_error(response.get("error"))
        return response.get("result")

    @staticmethod
    def _raise_remote_error(raw: object) -> None:
        from management_common.operation_errors import OperationError, remote_failure
        from management_portal.hatg.contracts import InstructionError
        from .result_contracts import ResultContractError

        failure = remote_failure(raw)
        code, details, message = failure.code, failure.details, str(failure)
        error: Exception = failure
        if code == "contract.invalid_result":
            if not all(isinstance(details.get(key), str) for key in ("contract", "path", "reason")):
                raise OperationError("Invalid result-contract error details", code="contract.invalid_error")
            error = ResultContractError(details["contract"], details["path"], details["reason"])
        elif code == "instruction.conflict" and details.get("conflict") is True:
            error = LineageConflictError(message, resource=str(details.get("resource") or "lineage"))
        elif code == "service_invocation.conflict":
            from management_portal.service_invocation_contract import ServiceInvocationConflict
            error = ServiceInvocationConflict(message)
        elif code == "lineage.busy":
            error = SocietyServiceBusy(message)
        elif code in {"lineage.unavailable", "resource.unavailable"}:
            error = SocietyServiceUnavailable(message)
        elif code == "projection.pending":
            from management_common.projection_receipts import CommittedProjectionError
            failures = details.get("projection_failures")
            if (not isinstance(failures, list)
                    or not all(isinstance(item, dict) and "committed_revision" in item for item in failures)):
                raise OperationError("Invalid projection error details", code="contract.invalid_error")
            error = CommittedProjectionError(failures)
        elif code.startswith("instruction."):
            error = InstructionError(code, message, http_status=failure.http_status, details=details)
        elif code in {
            "operation.not_found", "operation.file_not_found", "operation.forbidden",
            "operation.invalid", "operation.conflict",
        }:
            error = {
                "operation.not_found": KeyError, "operation.file_not_found": FileNotFoundError,
                "operation.forbidden": PermissionError, "operation.invalid": ValueError,
                "operation.conflict": FileExistsError,
            }[code](message)
        if isinstance(error, OperationError):
            error.code = code
            error.http_status = failure.http_status
            error.retryable = failure.retryable
            error.commit_state = failure.commit_state
            error.details = details
        raise error

    def call(self, domain: str, method: str, *args: Any, **kwargs: Any) -> Any:
        if domain not in DOMAIN_METHODS or method not in DOMAIN_METHODS[domain]:
            raise ValueError(f"unsupported SocietyService call: {domain}.{method}")
        return self._request(
            "domain.call",
            {
                "domain": domain,
                "name": method,
                "args": list(args),
                "kwargs": kwargs,
            },
        )

    def health(self, *, timeout: float = 5) -> dict[str, Any]:
        result = self._request("health", timeout=timeout, restart_wait=0)
        return dict(result or {})

    def wake_resident_execution(self, resident_id: str) -> None:
        from .runtime.contracts import ResidentWakeRequest

        request = ResidentWakeRequest(resident_id=resident_id)
        self._request("runtime.resident.wake", request.to_payload(), timeout=1.0, restart_wait=0)
