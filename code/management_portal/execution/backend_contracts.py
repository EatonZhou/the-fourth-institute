"""What ExecutionRuntime and every Backend share.

``BackendInvocation`` is one call of a Backend: the request, its session,
the turn's prompt and the system context due; ``BackendResult`` is what the
call returns.  ``BackendContext`` carries the callbacks through which a
Backend reports the call's events, interrupt target, recovery state and
context usage while it runs.  ``Backend`` is the protocol a Provider's
Adapter implements; the Provider catalog names each Provider's Adapter
(``AgentProvider.adapter``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol

from .contracts import ExecutionRequest, ProviderInputBinding
from .harness import (
    AgentLoopProviderEventProcessor,
    AgentLoopToolBatchProcessor,
    HarnessTool,
    ProviderErrorRecoveryPolicy,
)
from .provider_activity_events import _compact_provider_activity_event
from .session_manager import RuntimeSession


def _positive_int(value: object) -> int:
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0


_CONTEXT_USAGE_ALIASES = {
    "input_tokens": "inputTokens",
    "cached_input_tokens": "cachedInputTokens",
    "output_tokens": "outputTokens",
    "reasoning_output_tokens": "reasoningOutputTokens",
    "total_tokens": "totalTokens",
}


def _live_context_usage(
    value: object,
) -> tuple[dict[str, int], int]:
    """Normalize the current context sample of one ``thread/tokenUsage/updated`` payload."""

    payload = value if isinstance(value, dict) else {}
    current = (
        payload.get("context")
        if isinstance(payload.get("context"), dict)
        else payload.get("last")
        if isinstance(payload.get("last"), dict)
        else {}
    )
    usage = {
        target: number
        for target, source in _CONTEXT_USAGE_ALIASES.items()
        if (number := _positive_int(current.get(source))) > 0
    }
    return usage, _positive_int(payload.get("modelContextWindow"))


@dataclass(frozen=True)
class BackendResult:
    output: dict[str, Any]
    backend_session_id: str = ""
    backend_route_id: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    available_harness_tool_names: frozenset[str] = frozenset()


@dataclass(frozen=True)
class BackendInvocation:
    request: ExecutionRequest
    session: RuntimeSession
    user_prompt: str
    # The system parts due this turn (ExecutionService's injection plan).
    system_context: str
    attach_system_context: bool
    is_new_backend_session: bool
    recovery_checkpoint: dict[str, Any] = field(default_factory=dict)
    harness_tools: tuple[HarnessTool, ...] = ()
    available_harness_tool_names: frozenset[str] = frozenset()
    tool_batch_processor: AgentLoopToolBatchProcessor | None = None
    provider_event_processor: AgentLoopProviderEventProcessor | None = None
    provider_error_recovery: ProviderErrorRecoveryPolicy | None = None
    # The capabilities of the Adapter that runs this invocation, attached by
    # AgentHarness when it plans the turn (None before planning).
    harness_capabilities: frozenset[str] | None = None
    # Every system part of the session, due or not: where a Provider session
    # starts (a new Codex thread) or its system prompt is given anew at every
    # process start (Claude Code).
    session_system_context: str = ""


def full_system_context(invocation: object) -> str:
    """Every system part of the invocation's session (see BackendInvocation)."""
    return str(
        getattr(invocation, "session_system_context", "")
        or getattr(invocation, "system_context", "")
        or ""
    )


class BackendContext:
    def __init__(
        self,
        *,
        emit_event: Callable[[str, dict[str, Any]], None],
        cancellation_requested: Callable[[], bool],
        set_interrupt_target: Callable[[str, str, str], None],
        reassign_backend_route: Callable[[str], dict[str, Any]] | None = None,
        update_context_usage: (
            Callable[[dict[str, int], int], None] | None
        ) = None,
        set_provider_recovery_state: (
            Callable[[dict[str, Any]], None] | None
        ) = None,
        turn_control_provider: (
            Callable[[], dict[str, Any] | None] | None
        ) = None,
        turn_control_result_callback: (
            Callable[[dict[str, Any], bool, str], None] | None
        ) = None,
        provider_event_observer: (
            Callable[[str, str, str, Mapping[str, Any]], None] | None
        ) = None,
        invocation_observer: (
            Callable[[BackendInvocation, list[dict[str, Any]]], None] | None
        ) = None,
        compaction_observer: (
            Callable[..., None] | None
        ) = None,
        harness_plan_observer: (
            Callable[[dict[str, Any]], None] | None
        ) = None,
        capability_assembly_observer: Callable[[dict[str, Any]], None] | None = None,
        turn_start_reason: str = "initial",
        turn_start_reason_changed: Callable[[str], None] | None = None,
        execution_started_at: str = "",
        handover_requested: Callable[[], bool] | None = None,
    ) -> None:
        # When the Execution was accepted.  A recovered attempt keeps it, so
        # adapters can count the usage of every attempt of the turn.
        self.execution_started_at = str(execution_started_at or "")
        self._emit_event = emit_event
        self._cancellation_requested = cancellation_requested
        self._handover_requested = handover_requested
        self._set_interrupt_target = set_interrupt_target
        self._reassign_backend_route = reassign_backend_route
        self._update_context_usage = update_context_usage
        self._set_provider_recovery_state = set_provider_recovery_state
        # What this attempt last checkpointed; the invocation holds the state
        # the attempt started from.
        self._provider_recovery_state: dict[str, Any] | None = None
        self._turn_control_provider = turn_control_provider
        self._turn_control_result_callback = turn_control_result_callback
        self._provider_event_observer = provider_event_observer
        self._invocation_observer = invocation_observer
        self._compaction_observer = compaction_observer
        self._harness_plan_observer = harness_plan_observer
        self._capability_assembly_observer = capability_assembly_observer
        self._turn_start_reason = turn_start_reason or "initial"
        self._turn_start_reason_changed = turn_start_reason_changed
        self._started_turn_reasons: dict[str, str] = {}

    def set_turn_start_reason(self, reason: str, *, replace_pending: bool = False) -> None:
        """Name why the next turn starts.

        A Worker handover or a compaction names the start it causes; a
        recovery on the way does not rename it.  ``replace_pending`` renames
        it anyway, for a start that has a cause of its own.
        """

        if not replace_pending and reason == "provider_recovery" and self._turn_start_reason in {
            "backend_service_update", "context_compaction",
        }:
            return
        if reason == self._turn_start_reason:
            return
        self._turn_start_reason = reason
        if self._turn_start_reason_changed is not None:
            self._turn_start_reason_changed(reason)

    def emit(self, event_type: str, details: dict[str, Any] | None = None) -> None:
        self._emit_event(str(event_type or ""), dict(details or {}))

    def observe_provider_event(
        self,
        event: Mapping[str, Any],
        *,
        provider: str = "",
        backend_session_id: str = "",
        backend_route_id: str = "",
    ) -> None:
        """Project one Provider event into canonical and activity streams."""

        from .native_tool_events import normalize_native_tool_event
        event = normalize_native_tool_event(event)

        if self._provider_event_observer is not None:
            self._provider_event_observer(
                str(provider or ""),
                str(backend_session_id or ""),
                str(backend_route_id or ""),
                event,
            )

        activity = _compact_provider_activity_event(event)
        if activity is not None:
            new_turn_started = False
            params = activity.get("params") or {}
            if activity.get("method") == "turn/started":
                turn = params.get("turn") or {}
                turn_id = str(turn.get("id") or params.get("turnId") or "")
                new_turn_started = not turn_id or turn_id not in self._started_turn_reasons
                reason = self._started_turn_reasons.setdefault(
                    turn_id, self._turn_start_reason,
                ) if turn_id else self._turn_start_reason
                activity["turn_start_reason"] = reason
            self.emit("provider_event", activity)
            if new_turn_started:
                self.set_turn_start_reason("continuation")

    def observe_invocation(
        self,
        invocation: BackendInvocation,
        reports: list[dict[str, Any]],
    ) -> None:
        if self._invocation_observer is not None:
            self._invocation_observer(invocation, reports)

    def record_harness_plan(self, plan: Mapping[str, Any]) -> None:
        """Keep this Execution's Harness plan with its recovery checkpoint."""

        if self._harness_plan_observer is not None:
            self._harness_plan_observer(dict(plan))

    def record_capability_assembly(self, assembly: Mapping[str, Any]) -> None:
        if self._capability_assembly_observer is not None:
            self._capability_assembly_observer(dict(assembly))

    def record_compaction(
        self,
        *,
        provider: str,
        backend_session_id: str,
        backend_route_id: str = "",
        content: Mapping[str, Any],
        created_at: str = "",
        retain_from: str = "",
        provider_compaction_ref: str = "",
    ) -> None:
        if self._compaction_observer is not None:
            self._compaction_observer(
                str(provider or ""),
                str(backend_session_id or ""),
                str(backend_route_id or ""),
                dict(content),
                str(created_at or ""),
                retain_from=str(retain_from or ""),
                provider_compaction_ref=str(provider_compaction_ref or ""),
            )


    def is_cancelled(self) -> bool:
        return bool(self._cancellation_requested())

    def is_handover(self) -> bool:
        """Whether this Worker is handing the Execution to the next Worker."""

        return bool(self._handover_requested and self._handover_requested())

    def set_interrupt_target(
        self,
        backend_session_id: str,
        turn_id: str = "",
        backend_route_id: str = "",
    ) -> None:
        self._set_interrupt_target(
            str(backend_session_id or ""),
            str(turn_id or ""),
            str(backend_route_id or ""),
        )

    def set_provider_recovery_state(
        self,
        state: Mapping[str, Any] | None,
    ) -> None:
        self._provider_recovery_state = dict(state or {})
        if self._set_provider_recovery_state is not None:
            self._set_provider_recovery_state(dict(state or {}))

    def current_provider_recovery_state(self) -> dict[str, Any] | None:
        """The state this attempt last checkpointed; None before it set one."""

        return None if self._provider_recovery_state is None else dict(self._provider_recovery_state)

    def bind_input(
        self,
        *,
        provider: str,
        backend_session_id: str,
        turn_id: str,
        provider_message_id: str = "",
        backend_route_id: str = "",
    ) -> None:
        if not str(turn_id or "").strip():
            return
        binding = ProviderInputBinding(
            provider=str(provider or ""),
            backend_session_id=str(backend_session_id or ""),
            backend_route_id=str(backend_route_id or ""),
            turn_id=str(turn_id).strip(),
            provider_message_id=str(provider_message_id or ""),
        ).validate()
        self.set_interrupt_target(
            binding.backend_session_id,
            binding.turn_id,
            binding.backend_route_id,
        )
        self.emit(
            "provider_input_bound",
            binding.to_payload(),
        )

    def reassign_backend_route(self, backend_route_id: str) -> bool:
        if self._reassign_backend_route is None:
            return True
        return self._reassign_backend_route(str(backend_route_id or ""))["accepted"]

    def observe_context_usage(
        self,
        usage: dict[str, int],
        model_context_window: int = 0,
    ) -> None:
        if self._update_context_usage is not None:
            self._update_context_usage(
                dict(usage or {}),
                _positive_int(model_context_window),
            )

    def has_turn_control_provider(self) -> bool:
        return self._turn_control_provider is not None

    def next_turn_control(self) -> dict[str, Any] | None:
        if self._turn_control_provider is None:
            return None
        control = self._turn_control_provider()
        return dict(control) if isinstance(control, dict) else None

    def record_turn_control_result(
        self,
        control: dict[str, Any],
        delivered: bool,
        error: str,
    ) -> None:
        if self._turn_control_result_callback is not None:
            self._turn_control_result_callback(
                dict(control),
                bool(delivered),
                str(error or ""),
            )


class Backend(Protocol):
    provider_id: str

    def prepare_context_seed(
        self,
        invocation: BackendInvocation,
    ) -> BackendInvocation:
        ...

    def execute(
        self,
        invocation: BackendInvocation,
        context: BackendContext,
    ) -> BackendResult:
        ...

    def interrupt(
        self,
        *,
        backend_session_id: str,
        turn_id: str,
        backend_route_id: str,
        runtime_options: dict[str, Any],
    ) -> bool:
        ...
