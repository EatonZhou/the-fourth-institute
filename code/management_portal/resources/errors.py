from management_common.operation_errors import OperationError

from pathlib import Path
from typing import Any


class ResourceOperationError(OperationError, ValueError):
    """A domain-owned rejection with explicit public failure semantics."""

    def __init__(
        self,
        code: str,
        message: str,
        status: int = 400,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        if not isinstance(code, str) or not code.strip():
            raise ValueError("Resource operation error code must be non-empty")
        if type(status) is not int or not 400 <= status <= 599:
            raise ValueError("Resource operation error status must be 400–599")
        if details is not None and not isinstance(details, dict):
            raise ValueError("Resource operation error details must be an object")
        super().__init__(message)
        self.code = code
        self.http_status = status
        self.details = dict(details or {})


class ResourceServiceUnavailable(OperationError):
    code = "resource.unavailable"
    http_status = 503

    """ResourceService cannot provide the requested operation."""



class GitCheckoutRequired(ValueError):
    def __init__(self, path: str | Path, *, message: str = "") -> None:
        self.path = str(Path(path).expanduser().resolve(strict=False))
        super().__init__(message or f"directory is not a Git checkout: {self.path}")


class ResourceVersionConflict(RuntimeError):
    """A resource changed after a caller loaded the version being edited."""

    def __init__(
        self,
        message: str = "Resource version conflict",
        *,
        expected_version: object = None,
        current_version: object = None,
    ) -> None:
        super().__init__(message)
        self.expected_version = expected_version
        self.current_version = current_version

    def as_dict(self) -> dict[str, object]:
        return {
            "code": "resource_version_conflict",
            "expected_version": self.expected_version,
            "current_version": self.current_version,
        }


_ERROR_CONTRACTS = (
    (ResourceOperationError, "resource_operation_error", 500),
    (GitCheckoutRequired, "git_checkout_required", 409),
    (ResourceVersionConflict, "resource_version_conflict", 409),
    (ResourceServiceUnavailable, "resource_service_unavailable", 503),
    (FileNotFoundError, "file_not_found", 404),
    (FileExistsError, "file_exists", 409),
    (PermissionError, "permission_denied", 403),
    (KeyError, "resource_not_found", 404),
    (ValueError, "invalid_argument", 400),
    (RuntimeError, "internal_error", 500),
)


def resource_error_payload(error: Exception) -> dict[str, Any]:
    """Encode public failure semantics independently of Python class names."""
    code = next(
        (code for error_type, code, _status in _ERROR_CONTRACTS
         if isinstance(error, error_type)),
        "internal_error",
    )
    details: dict[str, Any] = {}
    message = str(error)
    if isinstance(error, ResourceOperationError):
        details = {
            "code": error.code,
            "http_status": error.http_status,
            "details": dict(error.details),
        }
    elif isinstance(error, GitCheckoutRequired):
        details = {"path": error.path, "can_initialize_git": True}
    elif isinstance(error, ResourceVersionConflict):
        details = {
            "expected_version": error.expected_version,
            "current_version": error.current_version,
        }
    elif (
        code in {"file_not_found", "file_exists", "permission_denied"}
        and isinstance(error, OSError)
        and error.errno is not None
    ):
        details = {
            "errno": error.errno,
            "strerror": error.strerror,
            "filename": error.filename,
            "filename2": error.filename2,
        }
    elif isinstance(error, KeyError) and len(error.args) == 1:
        message = str(error.args[0])
    return {"code": code, "message": message, "details": details}


def resource_error_from_payload(raw: object) -> Exception:
    """Restore only explicitly exported failures; unknown codes stay internal."""
    payload = raw if isinstance(raw, dict) else {}
    message = str(payload.get("message") or "ResourceService request failed")
    details = payload.get("details")
    details = details if isinstance(details, dict) else {}
    error_type = next(
        (error_type for error_type, code, _status in _ERROR_CONTRACTS
         if code == payload.get("code")),
        RuntimeError,
    )
    if error_type is ResourceOperationError:
        try:
            if not isinstance(details["details"], dict):
                raise ValueError("Domain error details must be an object")
            return ResourceOperationError(
                details["code"], message, details["http_status"],
                details=details["details"],
            )
        except (KeyError, TypeError, ValueError):
            return RuntimeError("Invalid resource_operation_error payload")
    if error_type is GitCheckoutRequired:
        path = details.get("path")
        if not isinstance(path, str) or not path:
            return RuntimeError("Invalid git_checkout_required error: path required")
        return GitCheckoutRequired(path, message=message)
    if error_type is ResourceVersionConflict:
        return ResourceVersionConflict(
            message,
            expected_version=details.get("expected_version"),
            current_version=details.get("current_version"),
        )
    if issubclass(error_type, OSError) and isinstance(details.get("errno"), int):
        return error_type(
            details["errno"], details.get("strerror"),
            details.get("filename"), None, details.get("filename2"),
        )
    return error_type(message)


def resource_error_http_response(error: Exception) -> tuple[int, dict[str, Any]]:
    """Project the Resource error contract onto the public HTTP envelope."""
    if isinstance(error, ResourceOperationError):
        return error.http_status, {
            "error": str(error),
            "code": error.code,
            "details": dict(error.details),
        }
    failure = resource_error_payload(error)
    status = next(
        status for _error_type, code, status in _ERROR_CONTRACTS
        if code == failure["code"]
    )
    payload = {
        "error": str(error),
        "code": failure["code"],
        "details": failure["details"],
    }
    if isinstance(error, (GitCheckoutRequired, ResourceVersionConflict)):
        payload.update(failure["details"])
    if isinstance(error, ResourceServiceUnavailable):
        payload.update({
            "error": "ResourceService 暂时不可用",
            "detail": str(error),
            "retryable": True,
        })
    return status, payload
