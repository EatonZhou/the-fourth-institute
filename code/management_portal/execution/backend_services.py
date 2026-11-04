"""The execution services ExecutionRuntime hands an invocation to.

``AgentSessionService`` runs ``agent_session`` invocations on the Backend of
the request's Provider (``AGENT_PROVIDERS``), through the Agent Harness;
``default_execution_services`` adds the ``chat_completion`` service.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from .backend_contracts import Backend, BackendContext, BackendInvocation, BackendResult
from .chat_completion_service import ChatCompletionService
from .context_seed import prepare_backend_context_seed
from .contracts import ExecutionRecoveryDeferred
from .harness import AgentHarness, default_agent_harness
from .provider_catalog import AGENT_PROVIDERS

LOGGER = logging.getLogger(__name__)


class ExecutionServiceAdapter(Protocol):
    service_type: str

    def execute(
        self,
        invocation: BackendInvocation,
        context: BackendContext,
    ) -> BackendResult:
        ...

    def interrupt(
        self,
        *,
        provider: str,
        backend_session_id: str,
        turn_id: str,
        backend_route_id: str,
        runtime_options: dict[str, Any],
    ) -> bool:
        ...


class AgentSessionService:
    service_type = "agent_session"

    def __init__(
        self,
        providers: dict[str, Backend] | None = None,
        harness: AgentHarness | None = None,
    ) -> None:
        self.providers = providers or {
            definition.provider: definition.build_backend() for definition in AGENT_PROVIDERS.values()
        }
        self.harness = harness if harness is not None else default_agent_harness()

    def execute(
        self,
        invocation: BackendInvocation,
        context: BackendContext,
    ) -> BackendResult:
        try:
            provider = self.providers[invocation.request.provider]
        except KeyError as exc:
            raise ValueError(
                "unsupported Agent Backend Provider: "
                f"{invocation.request.provider}"
            ) from exc
        from .native_options import prepare_invocation
        invocation = prepare_invocation(invocation)
        invocation = self.harness.prepare_invocation(invocation, provider)
        def execute_with_context_seed(
            prepared: BackendInvocation,
            backend_context: BackendContext,
        ) -> BackendResult:
            from .resident_capability_assembly import prepare, observe_delivery
            prepared = prepare(prepared, backend_context)
            prepared = prepare_backend_context_seed(provider, prepared)
            def observe(**values: Any) -> None:
                # Delivery evidence never fails a turn: a completed reply would be lost.
                try:
                    observe_delivery(prepared, backend_context, **values)
                except Exception:  # noqa: BLE001
                    LOGGER.warning("Resident capability delivery of %s could not be observed",
                                   prepared.request.execution_id, exc_info=True)
            try:
                result = provider.execute(prepared, backend_context)
            except ExecutionRecoveryDeferred:
                # The turn goes on in a later attempt, which observes its delivery.
                raise
            except Exception:
                observe(completed=False)
                raise
            observe(completed=True, available_names=result.available_harness_tool_names)
            return result
        if not self.harness.should_run(invocation):
            return execute_with_context_seed(invocation, context)
        return self.harness.execute(
            provider=provider,
            invocation=invocation,
            context=context,
            execute=execute_with_context_seed,
        )

    def interrupt(
        self,
        *,
        provider: str,
        backend_session_id: str,
        turn_id: str,
        backend_route_id: str,
        runtime_options: dict[str, Any],
    ) -> bool:
        selected = self.providers.get(provider)
        if selected is None:
            return False
        return selected.interrupt(
            backend_session_id=backend_session_id,
            turn_id=turn_id,
            backend_route_id=backend_route_id,
            runtime_options=runtime_options,
        )


def default_execution_services(
    *,
    agent_harness: AgentHarness | None = None,
) -> dict[str, ExecutionServiceAdapter]:
    services: tuple[ExecutionServiceAdapter, ...] = (
        AgentSessionService(harness=agent_harness),
        ChatCompletionService(),
    )
    return {service.service_type: service for service in services}
