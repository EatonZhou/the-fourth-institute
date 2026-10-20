from .contracts import (
    ExecutionRequest,
    ExecutionResult,
    InputMedia,
    SessionOptions,
    SystemInstructions,
)

__all__ = [
    "ExecutionRequest",
    "ExecutionResult",
    "InputMedia",
    "SessionOptions",
    "SystemInstructions",
    "ExecutionClient",
    "ExecutionSubmission",
    "ExecutionService",
    "get_execution_service",
]


def __getattr__(name: str):
    if name in {"ExecutionClient", "ExecutionSubmission"}:
        from .client import ExecutionClient, ExecutionSubmission

        return {
            "ExecutionClient": ExecutionClient,
            "ExecutionSubmission": ExecutionSubmission,
        }[name]
    if name in {"ExecutionService", "get_execution_service"}:
        from .service import ExecutionService, get_execution_service

        return {
            "ExecutionService": ExecutionService,
            "get_execution_service": get_execution_service,
        }[name]
    raise AttributeError(name)
