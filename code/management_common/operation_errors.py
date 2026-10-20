"""Transport-neutral failure semantics shared by application boundaries.

Commit state is explicit: an interrupted exchange is unknown, never a promise
that retrying a mutation is safe. Python exception names are not wire protocol.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping


class OperationError(RuntimeError):
    code = "operation.failed"
    http_status = 500
    retryable = False
    commit_state = "unknown"

    def __init__(self, message: str, *, code: str | None = None,
                 http_status: int | None = None, retryable: bool | None = None,
                 commit_state: str | None = None,
                 details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code
        if http_status is not None:
            self.http_status = http_status
        if retryable is not None:
            self.retryable = retryable
        if commit_state is not None:
            self.commit_state = commit_state
        self.details = deepcopy(dict(details or {}))

    def failure_payload(self) -> dict[str, Any]:
        # Domain commit receipts can be added by an outer transaction owner.
        committed = self.details.get("domain_committed")
        state = ("committed" if committed else "not_committed") if isinstance(committed, bool) else self.commit_state
        retryable = bool(self.details.get("retryable", self.retryable))
        if state == "committed":
            # An Instruction owner can retry only its persisted projection receipt,
            # never the committed mutation, using the same idempotency key.
            retryable = retryable and self.details.get("retry") == "same_instruction_same_idempotency_key"
        return {
            "code": self.code, "message": str(self),
            "http_status": self.http_status,
            "retryable": retryable,
            "commit_state": state, "details": deepcopy(self.details),
        }


def failure_payload(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, OperationError):
        return exc.failure_payload()
    code, status, state = "operation.failed", 500, "unknown"
    if isinstance(exc, PermissionError):
        code, status, state = "operation.forbidden", 403, "not_committed"
    elif isinstance(exc, FileNotFoundError):
        code, status, state = "operation.file_not_found", 404, "not_committed"
    elif isinstance(exc, KeyError):
        code, status, state = "operation.not_found", 404, "not_committed"
    elif isinstance(exc, FileExistsError):
        code, status, state = "operation.conflict", 409, "not_committed"
    elif isinstance(exc, ValueError):
        code, status, state = "operation.invalid", 400, "not_committed"
    elif isinstance(exc, TimeoutError):
        code, status = "operation.timeout", 504
    message = str(exc).strip("'") if isinstance(exc, KeyError) else str(exc)
    return OperationError(message, code=code, http_status=status, commit_state=state).failure_payload()


def remote_failure(payload: object) -> OperationError:
    if (not isinstance(payload, dict)
            or not isinstance(payload.get("code"), str) or not payload["code"]
            or not isinstance(payload.get("message"), str)
            or type(payload.get("http_status")) is not int
            or not 400 <= payload["http_status"] <= 599
            or type(payload.get("retryable")) is not bool
            or not isinstance(payload.get("commit_state"), str)
            or payload.get("commit_state") not in {"unknown", "not_committed", "committed"}
            or not isinstance(payload.get("details"), dict)):
        return OperationError("Invalid operation error envelope", code="contract.invalid_error")
    return OperationError(payload["message"], code=payload["code"],
                          http_status=payload["http_status"], retryable=payload["retryable"],
                          commit_state=payload["commit_state"], details=payload["details"])
