from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .contracts import ExecutionRequest


@dataclass(frozen=True)
class ExecutionSubmission:
    """Provider-neutral identity returned after durable submission."""

    execution_id: str
    actor: str
    agent_id: str
    service_type: str
    provider: str

    def to_payload(self) -> dict[str, str]:
        return {
            "execution_id": self.execution_id,
            "actor": self.actor,
            "agent_id": self.agent_id,
            "service_type": self.service_type,
            "provider": self.provider,
        }


class ExecutionClient:
    """Single client contract for every Portal AI execution caller.

    Callers construct an ``ExecutionRequest`` and never depend on a Provider
    SDK.  Scheduling, retries, Provider adapters, observation and AgentData
    persistence remain owned by ExecutionService and ExecutionRuntime.
    """

    def __init__(self, service: Any | None = None) -> None:
        if service is None:
            from .service import ExecutionService

            service = ExecutionService()
        self._service = service

    def submit(
        self,
        request: ExecutionRequest,
        *,
        on_submitted: Callable[[dict[str, str]], None] | None = None,
    ) -> ExecutionSubmission:
        request.validate()
        receipt = self._service.submit(request) or {}
        if not isinstance(receipt, dict):
            raise RuntimeError("ExecutionService returned an invalid submission receipt")
        execution_id = str(receipt.get("execution_id") or request.execution_id)
        if execution_id != request.execution_id:
            raise RuntimeError("ExecutionService changed the submitted execution identity")
        submission = ExecutionSubmission(
            execution_id=execution_id,
            actor=request.actor,
            agent_id=request.agent_id,
            service_type=request.service_type,
            provider=request.provider,
        )
        if on_submitted is not None:
            on_submitted(submission.to_payload())
        return submission

    def wait(
        self,
        submission: ExecutionSubmission,
        *,
        timeout_seconds: float | None = None,
        cancellation_requested: Callable[[], bool] | None = None,
        on_events: Callable[[list[dict[str, Any]]], None] | None = None,
        on_snapshot: Callable[[dict[str, Any]], bool | None] | None = None,
    ) -> dict[str, Any]:
        return self._service.wait(
            submission.execution_id,
            actor=submission.actor,
            timeout_seconds=timeout_seconds,
            cancellation_requested=cancellation_requested,
            on_events=on_events,
            on_snapshot=on_snapshot,
        )

    def status(
        self,
        execution_id: str,
        *,
        actor: str,
        include_events: bool = False,
    ) -> dict[str, Any]:
        return self._service.status(
            str(execution_id or ""),
            actor=str(actor or ""),
            include_events=include_events,
        )

    def resume(
        self,
        execution_id: str,
        *,
        actor: str,
        agent_id: str = "",
        service_type: str = "",
        provider: str = "",
        timeout_seconds: float | None = None,
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        return self.wait(
            ExecutionSubmission(
                execution_id=str(execution_id or ""),
                actor=str(actor or ""),
                agent_id=str(agent_id or ""),
                service_type=str(service_type or ""),
                provider=str(provider or ""),
            ),
            timeout_seconds=timeout_seconds,
            cancellation_requested=cancellation_requested,
        )

    def execute(
        self,
        request: ExecutionRequest,
        *,
        timeout_seconds: float | None = None,
        cancellation_requested: Callable[[], bool] | None = None,
        on_submitted: Callable[[dict[str, str]], None] | None = None,
        on_events: Callable[[list[dict[str, Any]]], None] | None = None,
        on_snapshot: Callable[[dict[str, Any]], bool | None] | None = None,
    ) -> dict[str, Any]:
        submission = self.submit(request, on_submitted=on_submitted)
        return self.wait(
            submission,
            timeout_seconds=timeout_seconds,
            cancellation_requested=cancellation_requested,
            on_events=on_events,
            on_snapshot=on_snapshot,
        )
