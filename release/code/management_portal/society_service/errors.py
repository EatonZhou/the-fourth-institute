"""Public Lineage failures, shared by local and remote consumers."""
from __future__ import annotations

from typing import Any

from management_common.operation_errors import OperationError


class SocietyServiceUnavailable(OperationError):
    code = "lineage.unavailable"
    http_status = 503


class SocietyServiceBusy(SocietyServiceUnavailable):
    """Response timed out; a sent mutation may already have committed."""
    code = "lineage.busy"


class LineageConflictError(OperationError):
    """The current state of a Lineage object does not allow this operation.

    For example the object was deleted by an instruction that committed
    first, or a deletion of the whole Lineage is in progress.
    """

    code = "instruction.conflict"
    http_status = 409
    commit_state = "not_committed"

    def __init__(self, message: str, *, resource: str = "lineage") -> None:
        self.resource = resource
        details: dict[str, Any] = {"conflict": True, "resource": resource}
        super().__init__(message, details=details)

    def conflict_details(self) -> dict[str, Any]:
        return dict(self.details)

    @property
    def payload(self) -> dict[str, Any]:
        return self.conflict_details()
