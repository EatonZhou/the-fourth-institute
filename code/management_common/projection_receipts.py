from __future__ import annotations

from management_common.operation_errors import OperationError

import copy
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Iterator


_FAILURES: ContextVar[list[dict[str, Any]] | None] = ContextVar(
    "committed_projection_failures", default=None
)


class CommittedProjectionError(OperationError):
    code = "projection.pending"
    http_status = 503
    commit_state = "committed"

    def __init__(self, failures: list[dict[str, Any]]) -> None:
        super().__init__("领域已提交，但投影尚未确认")
        self.failures = copy.deepcopy(failures)
        self.details: dict[str, Any] = {
            "domain_committed": True,
            "projection_failures": copy.deepcopy(failures),
            "recovery": "projection_only",
        }
        if len(failures) == 1:
            self.details["committed_revision"] = failures[0]["committed_revision"]


@contextmanager
def collect_projection_failures() -> Iterator[list[dict[str, Any]]]:
    failures: list[dict[str, Any]] = []
    token = _FAILURES.set(failures)
    try:
        yield failures
    finally:
        _FAILURES.reset(token)


def project_committed(
    receipt: dict[str, Any], projection: Callable[[], Any]
) -> None:
    try:
        projection()
    except Exception as exc:
        failure = {
            **copy.deepcopy(receipt),
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        failures = _FAILURES.get()
        if failures is None:
            raise CommittedProjectionError([failure]) from exc
        failures.append(failure)
