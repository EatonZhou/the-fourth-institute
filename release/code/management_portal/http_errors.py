"""Consistent JSON error semantics for Portal HTTP endpoints."""

from __future__ import annotations

import logging
import traceback
from typing import Any, cast

from management_common.operation_errors import OperationError, failure_payload


_STATUS_CODES = {
    400: "operation.invalid",
    401: "operation.unauthenticated",
    403: "operation.forbidden",
    404: "operation.not_found",
    405: "operation.method_not_allowed",
    409: "operation.conflict",
    413: "operation.too_large",
    422: "operation.unprocessable",
    429: "operation.rate_limited",
    500: "operation.failed",
    502: "operation.bad_gateway",
    503: "operation.unavailable",
    504: "operation.timeout",
}


def normalize_json_error(status: int, payload: dict[str, Any] | list[Any]) -> dict[str, Any] | list[Any]:
    """Add the shared error envelope without changing a route's public message."""

    if status < 400 or not isinstance(payload, dict):
        return payload
    message = payload.get("error")
    if not isinstance(message, str):
        message = payload.get("message")
    if not isinstance(message, str):
        return payload
    result = dict(payload)
    result.setdefault("error", message)
    result.setdefault("code", _STATUS_CODES.get(status, "operation.failed"))
    result.setdefault("message", message)
    result["http_status"] = status
    result.setdefault("retryable", False)
    result.setdefault("commit_state", "not_committed" if status in {400, 401, 403, 404, 405, 413, 422} else "unknown")
    result.setdefault("details", {})
    return result


def exception_http_error(error: Exception) -> tuple[int, dict[str, Any]]:
    """Map an uncaught endpoint error while keeping unknown server details private."""

    payload = failure_payload(error)
    status = int(payload["http_status"])
    if status >= 500 and not isinstance(error, OperationError):
        # Keep enough provenance to diagnose a masked failure without recording
        # exception messages, source lines, locals or request/credential content.
        frames = traceback.extract_tb(error.__traceback__)
        logging.getLogger(__name__).error(
            "Unhandled Portal HTTP exception: %s; frames=%s",
            type(error).__name__,
            " -> ".join(f"{frame.filename}:{frame.lineno} {frame.name}" for frame in frames),
        )
        payload["message"] = "Portal request failed"
        payload["details"] = {}
    return status, cast(dict[str, Any], normalize_json_error(status, {"error": payload["message"], **payload}))


def instruction_error_payload(error: object) -> dict:
    """JSON body for an operation or instruction failure: message plus its stable code and details."""

    from management_common.operation_errors import failure_payload

    failure = failure_payload(error)
    return {"error": failure.pop("message"), **failure}
