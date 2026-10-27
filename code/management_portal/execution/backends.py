from __future__ import annotations

from management_common.format_tags import schema_matches
from management_common.product_environment import mirror_product_env

from .harness_capabilities import SUBAGENTS
from .provider_catalog import AGENT_PROVIDERS

import json
import logging
import os
import secrets
import threading
import time
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from management_portal.ai_provider_registry import (
    AIProviderRegistry,
    shared_ai_provider_registry,
)
from management_portal.claude_code_client import shared_client as shared_claude_client
from management_portal.resident_shell_environment import may_inherit_portal_database, portal_database_environment
from .codex_history_delivery import CodexHistoryDelivery
from management_portal.codex_app_server_client import (
    DEFAULT_COMPACT_NO_PROGRESS_TIMEOUT_SECONDS,
    DEFAULT_COMPACT_TOTAL_TIMEOUT_SECONDS,
    CodexAppServerUnavailable,
    CodexCompactionInProgress,
    CodexAppServerError,
    CodexThreadWriterBusy,
    CodexTurnContinuationRequired,
    CodexThreadNotFound,
    thread_sandbox_from_policy,
    CodexTurnConnectionLost,
    CodexTurnDetached,
    CodexTurnInterrupted,
    CodexTurnRetryableFailure,
    _terminal_turn_error,
    client_for_socket,
    codex_final_agent_response,
    codex_token_usage_delta,
    codex_thread_execution_lock,
    is_context_compaction_item,
)
from management_portal import kimi_portal_tools
from management_portal import codex_subagent_cards
from management_portal.kimi_code_client import KimiCodeDetached, KimiCodeTurnFailed, KimiCodeUnavailable
from management_portal.native_agent_transport import NativeAgentDetached, NativeAgentUnavailable
from management_portal.kimi_code_client import shared_client as shared_kimi_client
from management_common.provider_turn_artifacts import ToolEditArtifacts

from management_common.provider_subagent_runs import PROGRESS_METHOD as SUBAGENT_PROGRESS_METHOD

from .activity_redaction import redact_sensitive_mapping
from .contracts import (
    EXECUTION_INPUT_CONTROL_SCHEMA,
    EXECUTION_TURN_SETTINGS_CONTROL_SCHEMA,
    ExecutionControlSignal,
    ExecutionRecoveryDeferred,
    ExecutionRequest,
    ProviderInputBinding,
)
from .context_seed import (
    prepare_backend_context_seed,
    preserve_structured_seed,
    realize_portable_seed,
)
from .claude_transcript_compaction import (
    claude_transcript_path,
    read_claude_latest_compaction,
)
from .kimi_tool_gateway import KimiToolGateway, publish_catalog
from .native_tool_events import NativeToolEventTranslator, provider_activity
from .native_session_compaction import (
    NativeCompaction,
    dsh_session_path,
    kimi_wire_path,
    read_dsh_latest_compaction,
    read_dsh_request_config,
    read_kimi_latest_compaction,
)
from .provider_egress import egress_state, location_restricted, route_upstream
from .provider_failures import is_egress_block_failure, is_network_failure, is_usage_limit_failure
from .turn_input_pump import NO_TURN_INPUT as _NO_TURN_INPUT, TurnInputPump, text_input
from .control_messages import report_harness_control
from .provider_turn_usage import (
    ObservedTurnUsage,
    claude_turn_usage,
    epoch_ms,
    kimi_turn_usage,
    observed_turn_usage,
    read_claude_turn_usage,
    read_dsh_context_window,
    read_dsh_turn_usage,
    read_kimi_turn_usage,
)
from .account_adapters import CodexRoutes, CodexAccountRoute
from .codex_recovery import CodexRecoveryBudget
from .codex_rollout_history import (
    CodexCompaction,
    codex_subagent_rollouts,
    read_codex_request_usage,
    read_codex_context_window,
    read_codex_turn_messages,
    read_codex_turn_error,
    read_codex_turn_input_recorded,
    read_codex_latest_compaction,
)
from .harness import (
    AgentLoopProviderEventProcessor,
    AgentLoopToolBatch,
    AgentLoopToolBatchProcessor,
    AgentLoopToolResult,
    AgentHarness,
    ContextWindowObservation,
    HarnessTool,
    InvocationTransformResult,
    ProviderErrorRecoveryPolicy,
    default_agent_harness,
    tool_call_scope,
)
from .session_manager import RuntimeSession


def _positive_int(value: object) -> int:
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0


def _bounded_wait_seconds(name: str, default: float) -> float:
    try:
        configured = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        configured = default
    return max(1.0, configured)


def _codex_compaction_wait_options() -> dict[str, float]:
    return {
        "timeout_seconds": _bounded_wait_seconds(
            "PORTAL_CODEX_COMPACTION_TIMEOUT_SECONDS",
            DEFAULT_COMPACT_TOTAL_TIMEOUT_SECONDS,
        ),
        "no_progress_timeout_seconds": _bounded_wait_seconds(
            "PORTAL_CODEX_COMPACTION_NO_PROGRESS_TIMEOUT_SECONDS",
            DEFAULT_COMPACT_NO_PROGRESS_TIMEOUT_SECONDS,
        ),
    }


LOGGER = logging.getLogger(__name__)

_CODEX_COMPACTION_PROMPT = (
    "Summarize the current task state using only established facts. Include the active "
    "request and completion criteria, constraints, verified progress and results, "
    "remaining work, confirmed blockers, and essential resource references. Preserve "
    "the distinction between completed actions and planned actions. Keep the summary "
    "concise and factual."
)




_CONTEXT_USAGE_ALIASES = {
    "input_tokens": "inputTokens",
    "cached_input_tokens": "cachedInputTokens",
    "output_tokens": "outputTokens",
    "reasoning_output_tokens": "reasoningOutputTokens",
    "total_tokens": "totalTokens",
}


def _codex_live_context_usage(
    value: object,
) -> tuple[dict[str, int], int]:
    """Normalize one Codex token notification's current context sample."""

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


PROVIDER_ACTIVITY_TEXT_LIMIT = 16_000
PROVIDER_ACTIVITY_METHODS = frozenset({
    SUBAGENT_PROGRESS_METHOD,
    "error",
    "guardianWarning",
    "item/autoApprovalReview/started",
    "item/autoApprovalReview/completed",
    "item/started",
    "item/completed",
    "turn/plan/updated",
    "item/plan/delta",
    "turn/started",
    "turn/completed",
    "thread/attached",
})


def _activity_text(value: object, limit: int = PROVIDER_ACTIVITY_TEXT_LIMIT) -> str:
    text = str(value or "")
    encoded = text.encode("utf-8", errors="replace")
    return text if len(encoded) <= limit else encoded[:limit].decode("utf-8", errors="ignore")


def _activity_item_text(item: dict[str, Any]) -> str:
    direct = str(item.get("text") or "")
    if direct:
        return _activity_text(direct)
    for key in ("content", "summary"):
        value = item.get(key)
        if isinstance(value, str) and value:
            return _activity_text(value)
        if not isinstance(value, list):
            continue
        chunks = [
            str(part.get("text") or part.get("output_text") or "")
            for part in value
            if isinstance(part, dict)
        ]
        text = "\n\n".join(chunk for chunk in chunks if chunk)
        if text:
            return _activity_text(text)
    return ""


def _activity_arguments(value: object) -> dict[str, Any]:
    arguments = value if isinstance(value, dict) else {}
    result: dict[str, Any] = {}
    for key, item in list(arguments.items())[:32]:
        if str(key).casefold() in {"content", "prompt", "system_prompt"}:
            continue
        if isinstance(item, (str, int, float, bool)) or item is None:
            result[str(key)] = _activity_text(item, 2_000)
        elif isinstance(item, list):
            result[str(key)] = [
                _activity_text(entry, 500)
                for entry in item[:10]
                if isinstance(entry, (str, int, float, bool))
            ]
    return result


def _compact_activity_item(value: object) -> dict[str, Any]:
    item = value if isinstance(value, dict) else {}
    if item.get("type") == "turnArtifacts":
        return {key: item[key] for key in ("id", "type", "artifacts") if key in item}
    if item.get("type") == "subagentRun":
        return {key: item[key] for key in ("id", "type", "run") if key in item}
    if item.get("type") in {"userInputRequest", "userInputResponse"}:
        return {key: item[key] for key in ("id", "type", "user_input") if key in item}
    compact = {
        key: item[key]
        for key in (
            "id",
            "type",
            "status",
            "phase",
            "exitCode",
            "exit_code",
            "durationMs",
            "percent",
        )
        if item.get(key) not in (None, "")
    }
    for key in (
        "command",
        "cwd",
        "name",
        "server",
        "tool",
        "description",
        "path",
        "query",
        "revisedPrompt",
    ):
        if item.get(key) not in (None, ""):
            compact[key] = _activity_text(item[key], 4_000)
    text = _activity_item_text(item)
    if text:
        compact["text"] = text
    progress = item.get("progress") if isinstance(item.get("progress"), dict) else {}
    if progress:
        compact["progress"] = {
            key: (
                _activity_text(progress[key], 2_000)
                if key == "text"
                else progress[key]
            )
            for key in ("text", "percent")
            if progress.get(key) not in (None, "")
        }
    display = item.get("display") if isinstance(item.get("display"), dict) else {}
    if display:
        compact["display"] = {
            key: _activity_text(display[key], 2_000)
            for key in ("command", "path", "url", "skill_name")
            if display.get(key) not in (None, "")
        }
    arguments = _activity_arguments(item.get("arguments"))
    if arguments:
        compact["arguments"] = arguments
    changes = item.get("changes") if isinstance(item.get("changes"), list) else []
    if changes:
        compact["changes"] = [
            {
                key: _activity_text(change[key], 2_000)
                for key in ("kind", "path")
                if change.get(key) not in (None, "")
            }
            for change in changes[:5]
            if isinstance(change, dict)
        ]
    action = item.get("action") if isinstance(item.get("action"), dict) else {}
    queries = action.get("queries") if isinstance(action.get("queries"), list) else []
    if queries:
        compact["action"] = {
            "queries": [_activity_text(query, 2_000) for query in queries[:5]]
        }
    return compact


def _compact_provider_activity_event_unredacted(
    value: object,
) -> dict[str, Any] | None:
    event = value if isinstance(value, dict) else {}
    method = str(event.get("method") or "")
    if method:
        if method not in PROVIDER_ACTIVITY_METHODS:
            return None
        params = event.get("params") if isinstance(event.get("params"), dict) else {}
        compact_params: dict[str, Any] = {
            key: params[key]
            for key in (
                "threadId",
                "turnId",
                "startedAtMs",
                "completedAtMs",
            )
            if params.get(key) not in (None, "")
        }
        if (method.startswith("item/") or method == SUBAGENT_PROGRESS_METHOD) and isinstance(params.get("item"), dict):
            item = params["item"]
            item_type = str(item.get("type") or "")
            if item_type == "userMessage":
                return None
            if method == "item/started" and item_type in {"agentMessage", "reasoning"}:
                return None
            compact_params["item"] = _compact_activity_item(item)
        elif method == "item/plan/delta":
            compact_params.update(itemId=params.get("itemId"), delta=_activity_text(params.get("delta")))
        elif method == "turn/plan/updated":
            compact_params["explanation"] = _activity_text(
                params.get("explanation"),
                4_000,
            )
            compact_params["plan"] = [
                {
                    "step": _activity_text(item.get("step"), 2_000),
                    "status": _activity_text(item.get("status"), 100),
                }
                for item in (params.get("plan") or [])[:100]
                if isinstance(item, dict) and str(item.get("step") or "")
            ]
        elif method in {"error", "guardianWarning"}:
            error = params.get("error") if isinstance(params.get("error"), dict) else {}
            compact_params.update({
                "willRetry": bool(params.get("willRetry")),
                "message": _activity_text(
                    params.get("message") or params.get("warning"),
                    4_000,
                ),
                "error": {
                    "message": _activity_text(error.get("message"), 4_000),
                    "additionalDetails": _activity_text(
                        error.get("additionalDetails"),
                        4_000,
                    ),
                },
            })
        elif method.startswith("turn/"):
            turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
            compact_params["turn"] = {
                key: _activity_text(turn[key], 2_000)
                for key in ("id", "status")
                if turn.get(key) not in (None, "")
            }
            if turn.get("error"):
                error = turn["error"]
                compact_params["turn"]["error"] = (
                    {"message": _activity_text(error.get("message"), 2_000)}
                    if isinstance(error, Mapping) else _activity_text(error, 2_000)
                )
            if isinstance(params.get("usage"), dict):
                compact_params["usage"] = dict(params["usage"])
        return {"method": method, "params": compact_params}

    event_type = str(event.get("type") or "")
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    payload_type = str(payload.get("type") or "")
    if event_type == "response_item":
        if payload_type not in {
            "function_call",
            "custom_tool_call",
            "function_call_output",
        }:
            return None
        compact_payload = {
            key: _activity_text(payload[key], 2_000)
            for key in ("type", "name", "call_id")
            if payload.get(key) not in (None, "")
        }
        arguments = _activity_arguments(payload.get("arguments"))
        if arguments:
            compact_payload["arguments"] = arguments
        if payload.get("output") not in (None, ""):
            compact_payload["output"] = _activity_text(payload["output"], 2_000)
        return {"type": event_type, "payload": compact_payload}
    return None


def _compact_provider_activity_event(value: object) -> dict[str, Any] | None:
    compact = _compact_provider_activity_event_unredacted(value)
    if compact is None:
        return None
    return redact_sensitive_mapping(compact)


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


def _model_api_entry(api_id: str, model: str) -> dict[str, Any]:
    from management_portal.ai_provider_registry import AIProviderRegistry, shared_ai_provider_registry
    try:
        entry = shared_ai_provider_registry.get(api_id)
    except ValueError:
        return {}
    return dict(AIProviderRegistry.model_entry(entry, model))


class CodexBackend:

    provider_id = "codex"

    def prepare_context_seed(
        self,
        invocation: BackendInvocation,
    ) -> BackendInvocation:
        return preserve_structured_seed(invocation)

    def _canonical_replay_turn(
        self, account_id: str, thread_id: str, turn: dict[str, Any],
    ) -> dict[str, Any]:
        """Resolve snapshot messages at the Provider boundary, before fan-out."""
        # A message without text or questions, such as an empty final answer,
        # has no rollout identity (read_codex_turn_messages) and nothing to replay.
        if not isinstance(turn.get("items"), list):
            return turn
        items = [item for item in turn["items"] if not (
            isinstance(item, dict) and item.get("type") == "agentMessage"
            and not str(item.get("text") or "").strip() and not item.get("questions")
        )]
        expected = sum(isinstance(item, dict) and item.get("type") == "agentMessage"
                       for item in items)
        if not expected:
            return {**turn, "items": items}
        path = self.account_registry.find_thread_rollout(
            account_id, thread_id, include_archived=True,
        )
        native = (read_codex_turn_messages(path, str(turn.get("id") or ""), thread_id=thread_id)
                  if isinstance(path, (str, Path)) else None)
        if str(turn.get("status") or "").casefold() in {"inprogress", "in_progress"}:
            messages = iter(native or ())
            canonical = []
            for item in items:
                if isinstance(item, dict) and item.get("type") == "agentMessage":
                    completed_message = next(messages, None)
                    if completed_message is not None:
                        canonical.append({**completed_message, "status": "completed"})
                else:
                    canonical.append(item)
            canonical.extend({**message, "status": "completed"} for message in messages)
            return {**turn, "items": canonical}
        if native is None or len(native) < expected:
            # Do not manufacture a second message ID or re-send the input.
            # Scheduler retains the accepted-turn checkpoint and retries it.
            raise ExecutionRecoveryDeferred(
                "Codex native message identities are not yet available for replay",
                retry_after_seconds=1,
            )
        messages = iter(native)
        canonical = [next(messages) if isinstance(item, dict) and item.get("type") == "agentMessage"
                     else item for item in items]
        canonical.extend(messages)
        return {**turn, "items": canonical}

    def __init__(
        self,
        *,
        account_registry: CodexRoutes | None = None,
        client_factory: Callable[[Path | str], Any] = client_for_socket,
    ) -> None:
        self.account_registry = account_registry or CodexRoutes()
        self.client_factory = client_factory

    def _latest_compaction(
        self,
        route: CodexAccountRoute,
        thread_id: str,
    ) -> CodexCompaction | None:
        rollout_path = self.account_registry.find_thread_rollout(
            route.account_id,
            thread_id,
            include_archived=True,
        )
        if not isinstance(rollout_path, (str, Path)):
            return None
        return read_codex_latest_compaction(rollout_path).compaction


    @contextmanager
    def _compaction_recorded_on_failure(
        self,
        *,
        context: BackendContext,
        route: CodexAccountRoute,
        thread_id: Callable[[], str],
        previous_compaction: Callable[[], CodexCompaction | None],
    ):
        """Record a compaction committed by a turn that did not complete.

        Codex compacts a thread automatically in the middle of a long turn.  A
        completed turn records that compaction afterwards; a turn that fails,
        is cancelled or is handed to another Worker records it here, otherwise
        AgentData keeps the uncompacted context and a recovered attempt takes
        the compacted rollout as its new baseline.
        """

        try:
            yield
        except BaseException:
            try:
                previous = previous_compaction()
                self._record_new_compaction(
                    context=context,
                    route=route,
                    thread_id=thread_id(),
                    previous_fingerprint=previous.fingerprint if previous is not None else "",
                    wait_seconds=0.0,
                    required=False,
                )
            except Exception:  # noqa: BLE001 - the turn's own failure is what the caller reports
                LOGGER.warning(
                    "Codex compaction of an unfinished turn could not be recorded for %s",
                    thread_id(),
                    exc_info=True,
                )
            raise

    def _record_new_compaction(
        self,
        *,
        context: BackendContext,
        route: CodexAccountRoute,
        thread_id: str,
        previous_fingerprint: str,
        wait_seconds: float,
        required: bool,
    ) -> CodexCompaction | None:
        deadline = time.monotonic() + max(0.0, wait_seconds)
        while True:
            compaction = self._latest_compaction(route, thread_id)
            if (
                compaction is not None
                and compaction.fingerprint != previous_fingerprint
            ):
                # Codex compacts in the middle of a turn; what the turn did
                # after the compaction stays after it in the context.
                context.record_compaction(
                    provider=self.provider_id,
                    backend_session_id=thread_id,
                    backend_route_id=route.account_id,
                    content={
                        "replacement_history": [
                            dict(item)
                            for item in compaction.replacement_history
                        ]
                    },
                    created_at=compaction.created_at,
                    retain_from=compaction.created_at,
                    provider_compaction_ref=compaction.fingerprint,
                )
                return compaction
            if time.monotonic() >= deadline:
                break
            time.sleep(0.05)
        if required:
            raise RuntimeError(
                "Codex compact completed without a committed replacement context"
            )
        return None

    @staticmethod
    def _thread_config(options: dict[str, Any]) -> dict[str, Any]:
        raw_overrides = options.get("config_overrides")
        if raw_overrides is not None and not isinstance(
            raw_overrides,
            dict,
        ):
            raise ValueError("Codex config_overrides must be an object")
        config = dict(raw_overrides or {})
        # Ask for readable information through supported Provider controls.
        # These do not decrypt opaque reasoning or override explicit choices.
        config.setdefault("show_raw_agent_reasoning", True)
        config.setdefault("model_reasoning_summary", "auto")
        # Codex no longer registers update_plan by default. Portal projects
        # its native plan events, so enable it unless this session overrides
        # the choice (Codex accepts nested and dotted config keys).
        if "tools.update_plan.enabled" not in config and "tools.update_plan" not in config:
            raw_tools = config.get("tools")
            if raw_tools is not None and not isinstance(raw_tools, dict):
                raise ValueError("Codex config_overrides.tools must be an object")
            tools = dict(raw_tools or {})
            raw_plan = tools.get("update_plan")
            if raw_plan is not None and not isinstance(raw_plan, dict):
                raise ValueError("Codex config_overrides.tools.update_plan must be an object")
            plan = dict(raw_plan or {})
            plan.setdefault("enabled", True)
            tools["update_plan"] = plan
            config["tools"] = tools
        raw_agents = config.get("agents")
        if raw_agents is not None and not isinstance(raw_agents, dict):
            raise ValueError("Codex config_overrides.agents must be an object")
        codex = AGENT_PROVIDERS["codex"]
        if not isinstance(options.get("allow_subagents", codex.subagents_by_default), bool):
            raise ValueError("Codex allow_subagents must be a boolean")
        agents = dict(raw_agents or {})
        agents["enabled"] = codex.allows_subagents(options)
        agents["interrupt_message"] = False
        config["agents"] = agents
        config["compact_prompt"] = _CODEX_COMPACTION_PROMPT
        if isinstance(options.get("goal"), dict):
            raw_features = config.get("features")
            if raw_features is not None and not isinstance(raw_features, dict):
                raise ValueError(
                    "Codex config_overrides.features must be an object"
                )
            features = dict(raw_features or {})
            features["goals"] = True
            config["features"] = features
        config["project_doc_max_bytes"] = 0
        return config

    @classmethod
    def _runtime_thread_config(cls, request: ExecutionRequest) -> dict[str, Any]:
        config = cls._thread_config(request.runtime_options)
        database = portal_database_environment(may_inherit_portal_database(request))
        environment = {**config.get("shell_environment_policy.set", {}), **database}
        environment.pop("PORTAL_EXECUTION_ID", None)
        mirror_product_env(environment, "PORTAL_EXECUTION_CONTEXT_FILE", str(cls._execution_context_path(request)))
        config["shell_environment_policy.set"] = environment
        return config

    @staticmethod
    def _execution_context_path(request: ExecutionRequest) -> Path:
        import hashlib
        identity = request.session.binding_id or request.execution_id
        digest = hashlib.sha256(identity.encode()).hexdigest()
        from .config import execution_state_root
        root = execution_state_root() / "codex_context"
        return root / (digest + ".json")

    @classmethod
    def _write_execution_context(cls, request: ExecutionRequest) -> None:
        from management_portal.shared_state import atomic_replace_json

        # The turn's tools report which execution they belong to; permissions
        # are the Resident's own, so nothing else travels with them.
        path = cls._execution_context_path(request)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        atomic_replace_json(path, {"execution_id": request.execution_id})

    @staticmethod
    def _harness_tool_schemas(
        harness_tools: tuple[HarnessTool, ...] = (),
    ) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        names: set[str] = set()
        for harness_tool in harness_tools:
            if not bool(getattr(harness_tool, "expose_schema", True)):
                continue
            name = str(harness_tool.name or "").strip()
            if (
                not name
                or not isinstance(harness_tool.description, str)
                or not isinstance(harness_tool.input_schema, dict)
                or not callable(harness_tool.handler)
            ):
                raise ValueError("Codex Harness tools require a valid contract")
            if name in names:
                raise ValueError(f"Duplicate Codex dynamic tool: {name}")
            names.add(name)
            tools.append({
                "type": "function",
                "name": name,
                "description": harness_tool.description,
                "inputSchema": dict(harness_tool.input_schema),
            })
        return tools

    @staticmethod
    def _harness_tool_dispatcher(
        harness_tools: tuple[HarnessTool, ...] = (),
        tool_batch_processor: AgentLoopToolBatchProcessor | None = None,
    ) -> Callable[[dict[str, Any]], dict[str, Any]] | None:
        harness_handlers = {
            str(tool.name): tool.handler
            for tool in harness_tools
        }
        if not harness_handlers:
            return None

        def process_result(
            params: dict[str, Any],
            *,
            tool_name: str,
            arguments: dict[str, Any],
            result: dict[str, Any],
            source: str,
        ) -> dict[str, Any]:
            if tool_batch_processor is None:
                return result
            try:
                processed = tool_batch_processor.process(
                    AgentLoopToolBatch(items=(AgentLoopToolResult(
                        call_id=str(params.get("callId") or ""),
                        tool_name=tool_name,
                        arguments=dict(arguments),
                        result=dict(result),
                        source=source,
                    ),))
                )
                if (
                    not isinstance(processed, AgentLoopToolBatch)
                    or len(processed.items) != 1
                    or not isinstance(processed.items[0].result, dict)
                ):
                    raise TypeError(
                        "tool batch processor must preserve one result"
                    )
                return dict(processed.items[0].result)
            except InterruptedError:
                raise
            except Exception:
                # Result governance is optional. Never hide an otherwise valid
                # Provider tool result when a Harness lifecycle hook fails.
                logging.getLogger(__name__).exception("Codex Harness tool result processing failed")
                return result

        def dispatch(params: dict[str, Any]) -> dict[str, Any]:
            tool_name = str(params.get("tool") or "").strip()
            arguments = params.get("arguments")
            if not tool_name or not isinstance(arguments, dict):
                raise ValueError(
                    "Dynamic tool call requires tool and object arguments"
                )
            harness_handler = harness_handlers.get(tool_name)
            if harness_handler is not None:
                try:
                    with tool_call_scope(params.get("callId")):
                        content = str(harness_handler(arguments) or "")
                except Exception as exc:
                    return {
                        "success": False,
                        "contentItems": [{
                            "type": "inputText",
                            "text": (
                                "Harness tool failed: "
                                f"{type(exc).__name__}: {str(exc)[:1000]}"
                            ),
                        }],
                    }
                result = {
                    "success": True,
                    "contentItems": [{
                        "type": "inputText",
                        "text": content,
                    }],
                }
                return process_result(
                    params,
                    tool_name=tool_name,
                    arguments=arguments,
                    result=result,
                    source="harness",
                )
            return {
                "success": False,
                "contentItems": [{"type": "inputText", "text": f"Unknown Harness tool: {tool_name}"}],
            }

        return dispatch

    @staticmethod
    def _sandbox_options(
        options: dict[str, Any],
    ) -> tuple[str, dict[str, Any] | None]:
        sandbox = str(options.get("sandbox") or "").strip()
        raw_policy = options.get("sandbox_policy")
        sandbox_policy = (
            dict(raw_policy)
            if isinstance(raw_policy, dict)
            else None
        )
        if raw_policy is not None and not isinstance(raw_policy, dict):
            raise ValueError("Codex sandbox_policy must be an object")
        permissions = str(options.get("permissions") or "").strip()
        if permissions:
            # A named Codex permission profile is a complete, higher-level
            # policy. Never send either low-level sandbox representation with
            # it to app-server.
            return "", None
        if sandbox and sandbox_policy is not None:
            raise ValueError(
                "Codex runtime options cannot combine sandbox and sandbox_policy"
            )
        return sandbox, sandbox_policy


    @classmethod
    def _start_thread(
        cls,
        client: Any,
        request: ExecutionRequest,
        cancellation_requested: Callable[[], bool] | None = None,
        harness_tools: tuple[HarnessTool, ...] = (),
        developer_instructions: str = "",
    ) -> str:
        options = request.runtime_options
        params = {
            "cwd": request.cwd,
            "ephemeral": bool(options.get("ephemeral", False)),
            "threadSource": str(options.get("thread_source") or "user"),
        }
        thread_config = cls._runtime_thread_config(request)
        params["config"] = thread_config
        if str(developer_instructions or "").strip():
            params["developerInstructions"] = str(developer_instructions)
        dynamic_tools = cls._harness_tool_schemas(harness_tools)
        if dynamic_tools:
            params["dynamicTools"] = dynamic_tools
        permissions = str(options.get("permissions") or "")
        sandbox, sandbox_policy = cls._sandbox_options(options)
        if not sandbox:
            sandbox = thread_sandbox_from_policy(sandbox_policy)
        if permissions:
            params["permissions"] = permissions
        elif sandbox:
            params["sandbox"] = sandbox
        for source, target in (
            ("model", "model"),
            ("service_tier", "serviceTier"),
            ("approvals_reviewer", "approvalsReviewer"),
            ("approval_policy", "approvalPolicy"),
        ):
            value = options.get(source)
            if value not in (None, ""):
                params[target] = value
        result = client.start_thread(
            params,
            cancellation_requested=cancellation_requested,
        )
        thread = result.get("thread") if isinstance(result, dict) else {}
        thread_id = str(
            thread.get("id") if isinstance(thread, dict) else ""
        ).strip()
        if not thread_id:
            raise RuntimeError("Codex thread/start returned no thread id")
        return thread_id


    def _resolve_client(
        self,
        invocation: BackendInvocation,
        context: BackendContext,
    ) -> tuple[CodexAccountRoute, Any, str, bool]:
        request = invocation.request
        session = invocation.session
        if session.backend_session_id:
            if not session.backend_route_id:
                raise ValueError(
                    "existing Codex session requires backend_route_id"
                )
            route = self.account_registry.get(
                session.backend_route_id
            )
            client = self.client_factory(route.socket_path)
            thread_id = session.backend_session_id
            return (
                route,
                client,
                thread_id,
                False,
            )
        candidates = self.account_registry.candidates(
            requested_account_id=session.backend_route_id,
            allow_fallback=bool(
                request.runtime_options.get(
                    "allow_backend_route_fallback",
                    True,
                )
            ),
        )
        current = candidates[0]
        unavailable = None
        for index, route in enumerate(candidates):
            if index:
                if not context.reassign_backend_route(route.account_id):
                    continue
                context.emit(
                    "backend_route_fallback",
                    {
                        "provider": self.provider_id,
                        "from_route_id": current.account_id,
                        "to_route_id": route.account_id,
                        "reason": type(unavailable).__name__,
                    },
                )
                current = route
            client = self.client_factory(route.socket_path)
            try:
                return (
                    route,
                    client,
                    self._start_thread(
                        client,
                        request,
                        context.is_cancelled,
                        invocation.harness_tools,
                        invocation.system_context if invocation.attach_system_context else "",
                    ),
                    bool(invocation.attach_system_context and invocation.system_context),
                )
            except CodexAppServerUnavailable as exc:
                unavailable = exc
        raise unavailable

    def configure_auto_compact_threshold(
        self,
        *,
        invocation: BackendInvocation,
        threshold_percent: int,
        max_context_tokens: int = 0,
    ) -> InvocationTransformResult:
        """Configure Codex's native compactor from model catalog metadata.

        ``max_context_tokens`` bounds the limit that the share of the window
        gives (0: no bound).
        """

        if invocation.request.operation not in {"create", "turn"}:
            return InvocationTransformResult(
                invocation,
                False,
                "operation_not_supported",
            )
        observation = self.observe_context_window(invocation=invocation)
        if not observation.tokens:
            return InvocationTransformResult(
                invocation,
                False,
                observation.reason or "model_context_window_unavailable",
                dict(observation.metrics),
            )
        options = dict(invocation.request.runtime_options)
        raw_overrides = options.get("config_overrides")
        if raw_overrides is not None and not isinstance(
            raw_overrides,
            dict,
        ):
            raise ValueError("Codex config_overrides must be an object")
        config_overrides = dict(raw_overrides or {})
        effective_context_window = observation.tokens
        requested_percent = max(1, min(99, int(threshold_percent)))
        requested_limit = max(
            1,
            (effective_context_window * requested_percent) // 100,
        )
        if _positive_int(max_context_tokens):
            requested_limit = min(requested_limit, _positive_int(max_context_tokens))
        previous_limit = _positive_int(
            config_overrides.get("model_auto_compact_token_limit")
        )
        # Enabling a supposedly earlier policy must not accidentally relax an
        # already more aggressive explicit limit.
        applied_limit = (
            min(previous_limit, requested_limit)
            if previous_limit
            else requested_limit
        )
        previous_scope = str(
            config_overrides.get("model_auto_compact_token_limit_scope")
            or ""
        ).strip()
        config_overrides["model_auto_compact_token_limit"] = applied_limit
        config_overrides["model_auto_compact_token_limit_scope"] = "total"
        options["config_overrides"] = config_overrides
        request = replace(
            invocation.request,
            runtime_options=options,
        )
        metrics: dict[str, Any] = {
            **dict(observation.metrics),
            "requested_threshold_percent": requested_percent,
            "auto_compact_token_limit": applied_limit,
            "effective_threshold_percent": round(
                (applied_limit / effective_context_window) * 100,
                2,
            ),
        }
        if previous_limit:
            metrics["previous_auto_compact_token_limit"] = previous_limit
        if previous_scope:
            metrics["previous_auto_compact_scope"] = previous_scope
        return InvocationTransformResult(
            replace(invocation, request=request),
            True,
            "native_auto_compact_threshold_configured",
            metrics,
        )


    def observe_context_window(
        self,
        *,
        invocation: BackendInvocation,
    ) -> ContextWindowObservation:
        """Resolve the effective model context window without a model call."""

        options = dict(invocation.request.runtime_options)
        model = str(options.get("model") or "").strip()
        if not model:
            return ContextWindowObservation(0, "model_unspecified")
        route_id, model_info = self._model_catalog_entry(
            model=model,
            preferred_route_id=invocation.session.backend_route_id,
        )
        raw_overrides = options.get("config_overrides")
        if raw_overrides is not None and not isinstance(
            raw_overrides,
            dict,
        ):
            raise ValueError("Codex config_overrides must be an object")
        config_overrides = dict(raw_overrides or {})
        configured_context_window = _positive_int(
            config_overrides.get("model_context_window")
        )
        catalog_context_window = _positive_int(
            model_info.get("context_window")
        ) or _positive_int(model_info.get("max_context_window"))
        context_window = configured_context_window or catalog_context_window
        if not context_window:
            session = invocation.session
            if session.backend_session_id and session.backend_route_id and not invocation.is_new_backend_session:
                rollout = self.account_registry.find_thread_rollout(
                    session.backend_route_id, session.backend_session_id,
                    include_archived=True,
                )
                observed_window = read_codex_context_window(rollout, model=model) if rollout else 0
                if observed_window:
                    return ContextWindowObservation(
                        observed_window,
                        metrics={
                            "model": model,
                            "effective_context_window": observed_window,
                            "context_window_source": "provider_thread_observation",
                            "model_catalog_route_id": session.backend_route_id,
                        },
                    )
            return ContextWindowObservation(
                0,
                "model_context_window_unavailable",
                {"model": model},
            )
        effective_percent = _positive_int(
            model_info.get("effective_context_window_percent")
        ) or 95
        effective_percent = max(1, min(100, effective_percent))
        effective_context_window = (
            context_window * effective_percent
        ) // 100
        metrics: dict[str, Any] = {
            "model": model,
            "model_catalog_route_id": route_id,
            "catalog_context_window": catalog_context_window,
            "effective_context_window": effective_context_window,
            "effective_context_window_percent": effective_percent,
        }
        if configured_context_window:
            metrics["configured_context_window"] = configured_context_window
        return ContextWindowObservation(
            effective_context_window,
            metrics=metrics,
        )

    def _model_catalog_entry(
        self,
        *,
        model: str,
        preferred_route_id: str,
    ) -> tuple[str, dict[str, Any]]:
        routes = list(self.account_registry.routes())
        clean_route_id = str(preferred_route_id or "").strip()
        if not clean_route_id:
            try:
                clean_route_id = str(
                    self.account_registry.default_for_new_session().account_id
                    or ""
                ).strip()
            except Exception:
                clean_route_id = ""
        if clean_route_id:
            routes.sort(
                key=lambda route: route.account_id != clean_route_id
            )
        for route in routes:
            if getattr(route, "kind", "") == "api_profile":
                # A Model API declares its models' context windows itself.
                declared = _model_api_entry(route.api_id, model)
                if declared:
                    return route.account_id, declared
                continue
            try:
                payload = json.loads(
                    (route.codex_home / "models_cache.json").read_text(
                        encoding="utf-8"
                    )
                )
            except Exception:
                continue
            entries = (
                payload.get("models")
                if isinstance(payload, dict)
                and isinstance(payload.get("models"), list)
                else []
            )
            for raw_entry in entries:
                if not isinstance(raw_entry, dict):
                    continue
                slug = str(
                    raw_entry.get("slug")
                    or raw_entry.get("model")
                    or raw_entry.get("id")
                    or ""
                ).strip()
                if slug == model:
                    return route.account_id, dict(raw_entry)
        return "", {}

    def execute(
        self,
        invocation: BackendInvocation,
        context: BackendContext,
    ) -> BackendResult:
        # The attempt's thread and the requests it reported live.
        usage_scope: dict[str, Any] = {}
        try:
            return self._execute(invocation, context, usage_scope)
        except CodexTurnDetached as exc:
            raise ExecutionRecoveryDeferred(str(exc)) from exc
        finally:
            self._report_rollout_usage(context, usage_scope)

    def _report_rollout_usage(self, context: BackendContext, usage_scope: Mapping[str, Any]) -> None:
        """Report the requests of the Execution that no live notification reported.

        A request that ends while one Worker hands the turn to the next, or a
        subagent's before Portal joins its thread, reaches no Worker; Codex
        keeps every request in the rollouts.  This runs after each attempt,
        also when it fails, is cancelled or hands over.
        """

        route = usage_scope.get("route")
        thread = usage_scope.get("thread")
        thread_id = str(thread() if callable(thread) else "")
        since = str(getattr(context, "execution_started_at", "") or "")
        if route is None or not thread_id or not since:
            return
        reported = set(usage_scope.get("reported") or ())
        try:
            rollout = self.account_registry.find_thread_rollout(route.account_id, thread_id)
            sources = [("", rollout, 0)] if rollout else []
            sources.extend(
                (f"{child}:", path, start)
                for child, path, start in codex_subagent_rollouts(route.codex_home, thread_id, since=since)
            )
            for prefix, path, start in sources:
                for event_id, delta, written_at in read_codex_request_usage(
                    path, since=since, history_start=start,
                ) or ():
                    if prefix + event_id not in reported:
                        reported.add(prefix + event_id)
                        context.emit("token_usage_delta", {
                            "event_id": prefix + event_id, "usage": delta, "occurred_at": written_at,
                        })
        except Exception:  # noqa: BLE001 - usage reporting never fails a turn
            LOGGER.warning("Codex rollout usage of thread %s could not be reported", thread_id, exc_info=True)

    def _execute(
        self,
        invocation: BackendInvocation,
        context: BackendContext,
        usage_scope: dict[str, Any],
    ) -> BackendResult:
        # Planned detachment is separate from user cancellation. In-flight native
        # commands keep running and the next owner follows this exact turn.
        def provider_cancelled() -> bool:
            return context.is_cancelled() and not (invocation.request.operation == "turn" and context.is_handover())

        request = invocation.request
        options = request.runtime_options
        goal_command = (
            dict(options.get("goal"))
            if isinstance(options.get("goal"), dict)
            else {}
        )
        if goal_command and bool(options.get("ephemeral", False)):
            raise ValueError("Codex native goals require a persistent thread")
        (
            route,
            client,
            thread_id,
            system_context_bound_to_thread,
        ) = (
            self._resolve_client(
                invocation,
                context,
            )
        )
        usage_scope.update(route=route, thread=lambda: thread_id)
        available_harness_tool_names = set(
            invocation.available_harness_tool_names
        )
        if invocation.is_new_backend_session:
            available_harness_tool_names.update(
                str(tool.name or "").strip()
                for tool in invocation.harness_tools
                if bool(getattr(tool, "expose_schema", True))
                and str(tool.name or "").strip()
            )
        context.set_interrupt_target(
            thread_id,
            backend_route_id=route.account_id,
        )
        operation = request.operation
        if operation == "release":
            client.unsubscribe_thread(thread_id)
            return BackendResult(
                output={
                    "message": "",
                    "provider": self.provider_id,
                    "operation": operation,
                },
                backend_route_id=route.account_id,
            )
        def history_delivery_for(references, *, agent_id=None, store=None):
            return CodexHistoryDelivery(
                references=references, route_id=route.account_id,
                agent_id=agent_id or request.agent_id, actor=request.actor,
                execution_id=request.execution_id, store=store,
                send=lambda target, items: client.request(
                    "thread/inject_items", {"threadId": target, "items": items},
                    cancellation_requested=provider_cancelled,
                ),
            )

        if operation == "create":
            seed = request.metadata.get("fork_context_input") or {}
            if seed:
                if not request.metadata.get("preserve_session_context"):
                    raise ValueError("Session creation history requires an explicit preservation operation")
                from .agent_data_store import get_agent_data_store
                from .session_history_import import validate_codex_preservation
                store = get_agent_data_store()
                validate_codex_preservation(store.fork_context_segments(seed["segments"]))
                history_delivery_for(seed["segments"], store=store,
                                     agent_id=seed["source_agent_id"]).initialize(thread_id)
                # create has no turn for the projector to mark. Keep the
                # acknowledged window attached to this session for later forks.
                imported_session = store.ensure_provider_session(
                    provider=self.provider_id, backend_route_id=route.account_id,
                    backend_session_id=thread_id, agent_id=request.agent_id, actor_ref=request.actor,
                )
                store.record_context_received(seed["source_agent_id"], imported_session["provider_session_ref"],
                    [item["segment_id"] for item in seed["segments"]], actor_ref=request.actor)
            return BackendResult(
                output={
                    "message": "",
                    "provider": self.provider_id,
                    "operation": operation,
                    "context_result": "preserved_import" if seed else "fresh",
                },
                backend_session_id=thread_id,
                backend_route_id=route.account_id,
                available_harness_tool_names=frozenset(
                    available_harness_tool_names
                ),
            )
        if operation == "fork":
            sandbox, sandbox_policy = self._sandbox_options(options)
            if not sandbox:
                sandbox = thread_sandbox_from_policy(sandbox_policy)
            params = {
                "threadId": thread_id,
                "ephemeral": bool(options.get("ephemeral", False)),
                "excludeTurns": bool(options.get("exclude_turns", True)),
                "threadSource": str(options.get("thread_source") or "user"),
                "config": self._runtime_thread_config(request),
            }
            for source, target in (
                ("cwd", "cwd"),
                ("approval_policy", "approvalPolicy"),
            ):
                value = options.get(source)
                if value not in (None, ""):
                    params[target] = value
            if sandbox:
                params["sandbox"] = sandbox
            try:
                forked = client.fork_thread(
                    params,
                    cancellation_requested=provider_cancelled,
                )
            except CodexAppServerError as exc:
                if not isinstance(exc, CodexThreadNotFound):
                    raise
                from .agent_data_store import get_agent_data_store

                store = get_agent_data_store()
                agent_id = str(request.metadata.get("resident_id") or request.agent_id)
                snapshot = store.provider_context_snapshot(
                    provider=self.provider_id, backend_route_id=route.account_id,
                    backend_session_id=thread_id, agent_id=agent_id,
                )
                if not snapshot or not snapshot.get("segments"):
                    raise
                references = snapshot["segments"]
                if request.metadata.get("preserve_session_context"):
                    from .session_history_import import validate_codex_preservation
                    validate_codex_preservation(references)
                replacement = self._start_thread(
                    client, request, context.is_cancelled, invocation.harness_tools,
                )
                delivery = history_delivery_for(references, agent_id=agent_id, store=store)
                delivery.initialize(replacement)
                context.emit("fork_source_restored", {
                    "provider": self.provider_id, "source_thread_id": thread_id,
                    "thread_id": replacement, "source": "agent_trace",
                })
                forked = {"thread": {"id": replacement}}
            thread = (
                forked.get("thread")
                if isinstance(forked.get("thread"), dict)
                else {}
            )
            forked_id = str(thread.get("id") or "")
            if not forked_id:
                raise RuntimeError("Codex thread/fork returned no thread id")
            return BackendResult(
                output={
                    "message": "",
                    "provider": self.provider_id,
                    "operation": operation,
                    "forked_from_session_id": thread_id,
                },
                backend_session_id=forked_id,
                backend_route_id=route.account_id,
            )
        if operation == "compact":
            self._write_execution_context(request)
            fork_history = request.metadata.get("fork_context_input") or {}
            if fork_history:
                if (
                    str(request.metadata.get("initial_context_policy") or "")
                    != "compact_before_first_turn"
                ):
                    raise ValueError(
                        "Fork context may only accompany compact_before_first_turn"
                    )

                history_delivery_for(fork_history["segments"]).initialize(thread_id)
            previous_compaction = self._latest_compaction(route, thread_id)
            try:
                usage = client.compact_thread(
                    thread_id,
                    config=self._runtime_thread_config(request),
                    cancellation_requested=provider_cancelled,
                    **_codex_compaction_wait_options(),
                )
            except (TimeoutError, CodexAppServerUnavailable) as exc:
                context.set_provider_recovery_state({"kind": "thread_writer_busy", "thread_id": thread_id})
                raise ExecutionRecoveryDeferred(
                    "Codex compaction recovery wait deadline reached"
                ) from exc
            self._record_new_compaction(
                context=context,
                route=route,
                thread_id=thread_id,
                previous_fingerprint=(
                    previous_compaction.fingerprint
                    if previous_compaction is not None
                    else ""
                ),
                wait_seconds=1.0,
                required=True,
            )
            return BackendResult(
                output={
                    "message": "",
                    "provider": self.provider_id,
                    "operation": operation,
                },
                backend_session_id=thread_id,
                backend_route_id=route.account_id,
                usage=dict(usage) if isinstance(usage, dict) else {},
            )
        if operation != "turn":
            raise ValueError(f"unsupported Codex backend operation: {operation}")
        sandbox, sandbox_policy = self._sandbox_options(options)
        turn_options = {
            key: options[key]
            for key in (
                "model",
                "effort",
                "summary",
                "service_tier",
                "collaboration_mode",
                "permissions",
                "approval_policy",
                "approvals_reviewer",
            )
            if key in options and options[key] not in (None, "")
        }
        if sandbox:
            turn_options["sandbox"] = sandbox
        if sandbox_policy is not None:
            turn_options["sandbox_policy"] = sandbox_policy
        turn_options["cwd"] = request.cwd
        turn_options["config"] = self._runtime_thread_config(request)
        portal_message_id = str(
            options.get("client_user_message_id") or ""
        )
        if portal_message_id:
            turn_options["client_user_message_id"] = portal_message_id
        dynamic_tool_handler = self._harness_tool_dispatcher(
            invocation.harness_tools,
            invocation.tool_batch_processor,
        )
        if dynamic_tool_handler is not None:
            turn_options["dynamic_tool_handler"] = dynamic_tool_handler
        event_processor = invocation.provider_event_processor
        if event_processor is not None or context.has_turn_control_provider():
            def next_turn_control() -> dict[str, Any] | None:
                external = context.next_turn_control()
                if external is not None:
                    return external
                if event_processor is not None:
                    return event_processor.next_control()
                return None

            def record_turn_control_result(
                control: dict[str, Any],
                delivered: bool,
                error: str,
            ) -> None:
                if (
                    schema_matches(control.get("schema"), "tfi.execution-input-control.v1")
                    or schema_matches(control.get("schema"), "tfi.execution-turn-settings-control.v1")
                ):
                    context.record_turn_control_result(
                        control,
                        delivered,
                        error,
                    )
                elif event_processor is not None:
                    event_processor.record_control_result(
                        control,
                        delivered,
                        error,
                    )
                    if delivered:
                        report_harness_control(context, control)

            turn_options["turn_control_provider"] = next_turn_control
            turn_options["turn_control_result_callback"] = (
                record_turn_control_result
            )

        provider_recovery_state = dict(
            invocation.recovery_checkpoint.get("provider_recovery_state")
            if isinstance(
                invocation.recovery_checkpoint.get("provider_recovery_state"),
                dict,
            )
            else {}
        )
        recovery_budget = CodexRecoveryBudget(provider_recovery_state)
        original_input_recorded = provider_recovery_state.get("original_input_recorded") is True
        provider_error_continue_count = int(
            provider_recovery_state.get("provider_error_continue_count") or 0
        )
        continuation_count = 0

        def persist_provider_recovery_state(
            state: Mapping[str, Any] | None,
        ) -> None:
            nonlocal provider_recovery_state
            provider_recovery_state = dict(state or {})
            provider_recovery_state["recovery_budget"] = recovery_budget.snapshot()
            provider_recovery_state["provider_error_continue_count"] = provider_error_continue_count
            provider_recovery_state["original_input_recorded"] = original_input_recorded
            context.set_provider_recovery_state(provider_recovery_state)

        def reserve_recovery_attempt(reason: object) -> None:
            recovery_budget.reserve_retry(reason)
            persist_provider_recovery_state(provider_recovery_state)


        def turn_started_callback(turn_id: str) -> None:
            recovery_budget.turn_started(turn_id)
            context.bind_input(
                provider=self.provider_id,
                backend_session_id=thread_id,
                backend_route_id=route.account_id,
                turn_id=turn_id,
            )
            original_turn_id = str(
                provider_recovery_state.get("original_turn_id") or ""
            )
            if original_turn_id and str(turn_id or "") != original_turn_id:
                persist_provider_recovery_state({})
            else:
                persist_provider_recovery_state(provider_recovery_state)

        recovered_terminal = (
            provider_recovery_state
            if provider_recovery_state.get("kind") == "completed_turn"
            else {}
        )
        recovered_turn_id = str(
            recovered_terminal.get("turn_id") or ""
        ).strip()
        checkpoint_turn_id = str(
            invocation.recovery_checkpoint.get("turn_id") or ""
        ).strip()
        recovered_response = str(
            recovered_terminal.get("response") or ""
        ).strip()
        if (
            recovered_turn_id
            and recovered_turn_id == checkpoint_turn_id
        ):
            return BackendResult(
                output={
                    "message": recovered_response,
                    "provider": self.provider_id,
                    "turn_id": recovered_turn_id,
                },
                backend_session_id=thread_id,
                backend_route_id=route.account_id,
                usage=dict(
                    recovered_terminal.get("usage")
                    if isinstance(recovered_terminal.get("usage"), dict)
                    else {}
                ),
                available_harness_tool_names=frozenset(
                    available_harness_tool_names
                ),
            )

        reported_usage_events: set[str] = set()
        usage_scope["reported"] = reported_usage_events
        compaction_event_observed = False
        latest_token_usage: dict[str, Any] = {}
        subagent_cards = codex_subagent_cards.for_thread(thread_id)
        def event_callback(event: dict[str, Any]) -> None:
            nonlocal compaction_event_observed
            nonlocal latest_token_usage
            nonlocal provider_error_continue_count
            if recovery_budget.observe(event):
                provider_error_continue_count = 0
                persist_provider_recovery_state(provider_recovery_state)
            method = str(event.get("method") or "")
            params = event.get("params") if isinstance(event.get("params"), dict) else {}
            item = params.get("item") if isinstance(params.get("item"), dict) else {}
            if method == "thread/compacted" or (
                method == "item/completed" and is_context_compaction_item(item)
            ):
                compaction_event_observed = True
            turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
            turn_id = str(turn.get("id") or params.get("turnId") or "")
            if turn_id:
                context.set_interrupt_target(
                    thread_id,
                    turn_id,
                    route.account_id,
                )
            if event_processor is not None:
                try:
                    event_processor.process_event(event)
                except Exception:
                    logging.getLogger(__name__).exception("Codex Harness event observation failed")
            if method == "thread/tokenUsage/updated":
                if isinstance(params.get("tokenUsage"), dict):
                    latest_token_usage = dict(params["tokenUsage"])
                usage_event_id, usage_delta = codex_token_usage_delta(
                    params.get("tokenUsage")
                )
                if (
                    usage_event_id
                    and usage_event_id not in reported_usage_events
                ):
                    reported_usage_events.add(usage_event_id)
                    context.emit(
                        "token_usage_delta",
                        {
                            "event_id": usage_event_id,
                            "usage": usage_delta,
                        },
                    )
                live_usage, context_window = _codex_live_context_usage(
                    params.get("tokenUsage")
                )
                if live_usage or context_window:
                    context.observe_context_usage(
                        live_usage,
                        context_window,
                    )
            child_event = params.get("event") if method == "subagent/event" else None
            if isinstance(child_event, dict) and child_event.get("method") == "thread/tokenUsage/updated":
                # A subagent's requests run in its own thread: they are the
                # turn's usage too, though not the size of the turn's context.
                child_params = child_event.get("params") if isinstance(child_event.get("params"), dict) else {}
                child_event_id, child_delta = codex_token_usage_delta(child_params.get("tokenUsage"))
                child_event_id = f"{params.get('traceKey') or ''}:{child_event_id}" if child_event_id else ""
                if child_event_id and child_event_id not in reported_usage_events:
                    reported_usage_events.add(child_event_id)
                    context.emit("token_usage_delta", {"event_id": child_event_id, "usage": child_delta})
            # Persist the provider notification itself. Reconstructed snapshots
            # are display state, not another source of immutable reasoning.
            if not (item.get("type") == "reasoning" and event.get("origin") == "turn_snapshot"):
                context.observe_provider_event(event, provider=self.provider_id,
                                               backend_session_id=thread_id, backend_route_id=route.account_id)
            for card_event in subagent_cards.observe(event):
                context.observe_provider_event(card_event, provider=self.provider_id,
                                               backend_session_id=thread_id, backend_route_id=route.account_id)
            if method == "turn/completed" and not goal_command:
                completed_turn = (
                    params.get("turn")
                    if isinstance(params.get("turn"), dict)
                    else {}
                )
                completed_turn_id = str(
                    completed_turn.get("id") or turn_id
                ).strip()
                response = codex_final_agent_response(completed_turn, [])
                if (
                    completed_turn_id
                    and str(completed_turn.get("status") or "")
                    .strip()
                    .casefold()
                    == "completed"
                    and response
                ):
                    # The app-server can emit its terminal event and still
                    # leave the transport request blocked. Persist the
                    # provider result at the event boundary so a replacement
                    # worker can finish without reopening the whole thread.
                    persist_provider_recovery_state({
                        "kind": "completed_turn",
                        "turn_id": completed_turn_id,
                        "response": response,
                        "usage": dict(latest_token_usage),
                    })

        def canonical_replay_turn(turn: dict[str, Any]) -> dict[str, Any]:
            try:
                return self._canonical_replay_turn(route.account_id, thread_id, turn)
            except ExecutionRecoveryDeferred as exc:
                # A replacement worker must not replenish this wait budget.
                # Keep observing the accepted turn; never send a continuation
                # just because its durable message identities are unavailable.
                reserve_recovery_attempt(exc)
                raise

        turn_options["turn_snapshot_transform"] = canonical_replay_turn
        turn_options["detach_requested"] = context.is_handover
        previous_compaction = self._latest_compaction(route, thread_id)


        def recovery_input(turn_id: str, continuation_prompt: str) -> str:
            nonlocal original_input_recorded
            if not original_input_recorded:
                rollout = self.account_registry.find_thread_rollout(
                    route.account_id, thread_id, include_archived=True,
                )
                recorded = read_codex_turn_input_recorded(rollout, turn_id, expected_prompt=invocation.user_prompt)
                if recorded is None:
                    reserve_recovery_attempt("native input delivery is not yet verifiable")
                    raise ExecutionRecoveryDeferred(
                        "Codex input delivery is not yet verifiable; original input retained",
                        retry_after_seconds=5,
                    )
                original_input_recorded = recorded
            if original_input_recorded:
                return continuation_prompt
            return (f"{invocation.system_context}\n\n{invocation.user_prompt}"
                    if invocation.attach_system_context and not system_context_bound_to_thread
                    else invocation.user_prompt)

        def apply_turn_continuation(
            continuation: CodexTurnContinuationRequired,
            continuation_count: int,
        ) -> str:
            nonlocal provider_error_continue_count
            rollout_path = self.account_registry.find_thread_rollout(
                route.account_id, continuation.thread_id, include_archived=True,
            )
            terminal_error = (
                read_codex_turn_error(rollout_path, continuation.turn_id)
                if isinstance(rollout_path, (str, Path)) else None
            )
            if terminal_error:
                failure = _terminal_turn_error(continuation.thread_id, {
                    "id": continuation.turn_id,
                    "status": "failed",
                    "error": terminal_error,
                })
                if not isinstance(failure, CodexTurnRetryableFailure):
                    raise failure from continuation
            provider_error = continuation.reason == "provider_system_error"
            if provider_error:
                reserve_recovery_attempt(continuation)
            policy = invocation.provider_error_recovery
            harness_continue = bool(
                provider_error
                and policy is not None
                and provider_error_continue_count
                < policy.max_continue_attempts
            )
            if harness_continue:
                continuation_prompt, provider_error_continue_count = provider_error_continuation(
                    continuation.__cause__ or continuation,
                    invocation=invocation, context=context,
                    provider_id=self.provider_id, turn_started=True,
                )
            else:
                continuation_prompt = INTERRUPTED_TURN_CONTINUATION
            continuation_prompt = recovery_input(continuation.turn_id, continuation_prompt)
            continuation_state = {
                "kind": "turn_and_continue",
                "phase": "ready_to_continue",
                "original_turn_id": continuation.turn_id,
                "reason": continuation.reason,
                "continuation_prompt": continuation_prompt,
                "input_delivery": "continued" if original_input_recorded else "resent",
                "transition_count": continuation_count,
                "previous_compaction_fingerprint": (
                    previous_compaction.fingerprint
                    if previous_compaction is not None
                    else ""
                ),
                "effort": str(
                    turn_options.get("effort") or ""
                ).strip(),
                "provider_error_continue_count": (
                    provider_error_continue_count
                ),
            }
            if continuation_count > 2:
                if provider_error:
                    # Requeue with the same outage budget. Only newly observed
                    # work can replenish it, not another scheduler claim.
                    persist_provider_recovery_state(continuation_state)
                    raise ExecutionRecoveryDeferred(
                        "Codex provider system error recovery deferred",
                        retry_after_seconds=5,
                    )
                persist_provider_recovery_state({})
                raise RuntimeError(
                    "Codex provider continuation limit exceeded"
                )
            persist_provider_recovery_state(continuation_state)
            context.set_turn_start_reason("provider_recovery")
            return continuation_prompt

        def recover_provider_failure(
            failure: CodexTurnInterrupted | CodexTurnRetryableFailure,
        ) -> CodexTurnContinuationRequired:
            if provider_cancelled():
                raise InterruptedError(
                    f"Codex execution cancelled for {thread_id}"
                ) from failure
            policy = invocation.provider_error_recovery
            if policy is None or policy.max_continue_attempts < 1:
                raise failure
            if provider_error_continue_count >= policy.max_continue_attempts:
                reserve_recovery_attempt(failure)
                persist_provider_recovery_state({
                    "kind": "turn_and_continue",
                    "phase": "ready_to_continue",
                    "original_turn_id": failure.turn_id,
                    "reason": "provider_system_error",
                    "continuation_prompt": policy.continuation_prompt,
                    "transition_count": continuation_count,
                    "previous_compaction_fingerprint": (
                        previous_compaction.fingerprint
                        if previous_compaction is not None
                        else ""
                    ),
                    "effort": str(
                        turn_options.get("effort") or ""
                    ).strip(),
                    "provider_error_continue_count": provider_error_continue_count,
                })
                raise ExecutionRecoveryDeferred(
                    "Codex provider recovery deferred after bounded "
                    "automatic continuation",
                    retry_after_seconds=5,
                ) from failure
            return CodexTurnContinuationRequired(
                failure.thread_id or thread_id,
                failure.turn_id,
                "provider_system_error",
            )

        def resume_accepted_turn(turn_id: str):
            current_turn_id = str(turn_id or "").strip()
            if not current_turn_id:
                raise CodexAppServerUnavailable(
                    "Codex accepted a turn but did not return its identity"
                )
            retry_delay = 0.25
            reconnect_deadline = time.monotonic() + 540.0
            while True:
                if provider_cancelled():
                    raise InterruptedError(
                        f"Codex execution cancelled while reconnecting {thread_id}"
                    )
                try:
                    return client.resume_turn(
                        thread_id,
                        current_turn_id,
                        cwd=request.cwd,
                        config=turn_options["config"],
                        event_callback=event_callback,
                        turn_started_callback=turn_started_callback,
                        turn_snapshot_transform=turn_options["turn_snapshot_transform"],
                        detach_requested=context.is_handover,
                        cancellation_requested=provider_cancelled,
                        dynamic_tool_handler=dynamic_tool_handler,
                        turn_control_provider=turn_options.get(
                            "turn_control_provider"
                        ),
                        turn_control_result_callback=turn_options.get(
                            "turn_control_result_callback"
                        ),
                        timeout_seconds=None,
                    )
                except CodexTurnConnectionLost as exc:
                    current_turn_id = str(
                        exc.turn_id or current_turn_id
                    ).strip()
                except (CodexTurnInterrupted, CodexTurnRetryableFailure) as exc:
                    raise recover_provider_failure(exc) from exc
                except (CodexAppServerUnavailable, TimeoutError):
                    pass
                if time.monotonic() >= reconnect_deadline:
                    persist_provider_recovery_state({"kind": "provider_reconnect", "turn_id": current_turn_id})
                    raise ExecutionRecoveryDeferred("Codex app-server reconnect deadline reached", retry_after_seconds=5)
                if context.is_handover():
                    raise CodexTurnDetached(thread_id, current_turn_id)
                time.sleep(retry_delay)
                retry_delay = min(5.0, retry_delay * 2.0)

        fork_history = request.metadata.get("fork_context_input") or {}
        history_delivery = None
        if fork_history and not invocation.recovery_checkpoint.get("turn_id"):
            history_delivery = history_delivery_for(fork_history["segments"])

        def run_turn_recovering(
            prompt: str,
            *,
            images: list[str] | None = None,
            image_urls: list[str] | None = None,
        ):
            if provider_recovery_state.get("continuation_prompt") == prompt:
                report_recovery_input(
                    context, provider="codex", prompt=prompt,
                    reason="provider_error" if provider_recovery_state.get("reason") == "provider_system_error" else "missing_final_response",
                    input_delivery=str(provider_recovery_state.get("input_delivery") or "continued"),
                    turn_id=str(provider_recovery_state.get("original_turn_id") or ""),
                    image_count=len(images or []) + len(image_urls or []),
                )
            try:
                return client.run_turn(
                    thread_id,
                    prompt,
                    **({"review_target": options["review_target"]} if options.get("review_target") else {}),
                    event_callback=event_callback,
                    turn_started_callback=turn_started_callback,
                    images=images,
                    image_urls=image_urls,
                    image_detail=str(options.get("input_image_detail") or "").strip().casefold() or None,
                    cancellation_requested=provider_cancelled,
                    **({"history_initializer": history_delivery.initialize} if history_delivery else {}),
                    **turn_options,
                )
            except CodexCompactionInProgress as exc:
                persist_provider_recovery_state({
                    "kind": "thread_writer_busy",
                    "thread_id": thread_id,
                })
                raise ExecutionRecoveryDeferred(
                    str(exc),
                    retry_after_seconds=exc.retry_after_seconds,
                ) from exc
            except CodexThreadWriterBusy as exc:
                # The Provider rejected this input before accepting a turn.
                # Reclaims must share the durable no-progress budget, otherwise
                # a writer that never releases its thread loops forever.
                reserve_recovery_attempt(exc)
                persist_provider_recovery_state({
                    "kind": "thread_writer_busy",
                    "thread_id": thread_id,
                })
                raise ExecutionRecoveryDeferred(
                    str(exc),
                    retry_after_seconds=30,
                ) from exc
            except CodexTurnConnectionLost as exc:
                reserve_recovery_attempt(exc)
                return resume_accepted_turn(exc.turn_id)
            except (CodexTurnInterrupted, CodexTurnRetryableFailure) as exc:
                raise recover_provider_failure(exc) from exc

        with self._compaction_recorded_on_failure(
            context=context,
            route=route,
            thread_id=lambda: thread_id,
            previous_compaction=lambda: previous_compaction,
        ), codex_thread_execution_lock(
            f"{route.account_id}:{thread_id}",
            cancellation_requested=provider_cancelled,
        ):
            self._write_execution_context(request)
            if provider_cancelled():
                raise InterruptedError(
                    f"Codex execution cancelled before turn start for {thread_id}"
                )
            recovery_turn_id = str(
                invocation.recovery_checkpoint.get("turn_id") or ""
            )
            if goal_command:
                goal_action = "observe" if recovery_turn_id else str(goal_command.get("action") or "")
                result = client.run_goal(
                    thread_id,
                    action=goal_action,
                    objective=str(goal_command.get("objective") or ""),
                    token_budget=(
                        int(goal_command["token_budget"])
                        if goal_command.get("token_budget") is not None
                        else None
                    ),
                    cwd=request.cwd,
                    config=turn_options["config"],
                    dynamic_tool_handler=dynamic_tool_handler,
                    event_callback=event_callback,
                    cancellation_requested=provider_cancelled,
                    detach_requested=context.is_handover,
                )
            else:
                continuation_count = int(provider_recovery_state.get("transition_count") or 0)
                turn_prompt = (
                    f"{invocation.system_context}\n\n{invocation.user_prompt}"
                    if invocation.attach_system_context and not system_context_bound_to_thread
                    else invocation.user_prompt
                )
                turn_images = [item.source for item in request.input_media
                               if item.media_type == "image" and item.source_type == "local_path"]
                turn_image_urls = [item.source for item in request.input_media
                                   if item.media_type == "image" and item.source_type == "url"]
                if (recovery_turn_id
                    and provider_recovery_state.get("original_turn_id") == recovery_turn_id
                    and provider_recovery_state.get("phase") == "ready_to_continue"):
                    turn_prompt = recovery_input(recovery_turn_id, str(provider_recovery_state["continuation_prompt"]))
                    persist_provider_recovery_state({**provider_recovery_state, "continuation_prompt": turn_prompt,
                                                     "input_delivery": "continued" if original_input_recorded else "resent"})
                    recovery_turn_id = ""
                    if original_input_recorded:
                        turn_images = []
                        turn_image_urls = []
                    context.set_turn_start_reason("provider_recovery")
                fork_thread_replaceable = history_delivery is not None
                while True:
                    try:
                        if recovery_turn_id:
                            result = resume_accepted_turn(recovery_turn_id)
                        else:
                            result = run_turn_recovering(turn_prompt, images=turn_images, image_urls=turn_image_urls)
                        break
                    except CodexTurnContinuationRequired as exc:
                        continuation_count += 1
                        turn_prompt = apply_turn_continuation(exc, continuation_count)
                        recovery_turn_id = ""
                        if original_input_recorded:
                            turn_images = []
                            turn_image_urls = []
                    except CodexAppServerError as exc:
                        if not (
                            fork_thread_replaceable
                            and isinstance(exc, CodexThreadNotFound)
                        ):
                            raise
                        # A Fork child's thread has no turn yet, so it exists only in the
                        # app-server's memory and a normal worker restart loses it. The Fork
                        # window is immutable in PostgreSQL: import it into a new thread.
                        from .session_manager import SessionManager
                        sessions = SessionManager(agent_data=history_delivery.store)
                        if sessions.has_accepted_input(invocation.session.binding_id, agent_id=request.agent_id):
                            raise
                        fork_thread_replaceable = False
                        lost_thread_id = thread_id
                        thread_id = self._start_thread(
                            client, request, context.is_cancelled, invocation.harness_tools,
                        )
                        sessions.replace_unaccepted_session(
                            request, invocation.session, backend_session_id=thread_id,
                            backend_route_id=route.account_id,
                        )
                        context.emit("fork_thread_replaced", {
                            "provider": self.provider_id, "lost_thread_id": lost_thread_id,
                            "thread_id": thread_id,
                        })
        # A resumed provider turn can replay an older contextCompaction item.
        # That notification is useful as a hint to reconcile the durable
        # rollout, but it does not prove that this Execution created a new
        # compaction.  Compaction projection is secondary to an otherwise
        # completed user turn, so a stale replay must not replace the
        # Provider's successful result with a Portal system failure.  Explicit
        # compact operations keep their
        # strict required=True checks above.
        self._record_new_compaction(
            context=context,
            route=route,
            thread_id=thread_id,
            previous_fingerprint=(
                previous_compaction.fingerprint
                if previous_compaction is not None
                else ""
            ),
            wait_seconds=1.0 if compaction_event_observed else 0.0,
            required=False,
        )
        usage = (
            dict(result.token_usage)
            if isinstance(result.token_usage, dict)
            else {}
        )
        return BackendResult(
            output={
                "message": str(result.response or ""),
                "provider": self.provider_id,
                "turn_id": str(result.turn_id or ""),
                **(
                    {"goal": dict(result.goal) if result.goal else None}
                    if goal_command
                    else {}
                ),
            },
            backend_session_id=str(result.thread_id or thread_id),
            backend_route_id=route.account_id,
            usage=usage,
            available_harness_tool_names=frozenset(
                available_harness_tool_names
            ),
        )

    def interrupt(
        self,
        *,
        backend_session_id: str,
        turn_id: str,
        backend_route_id: str,
        runtime_options: dict[str, Any],
    ) -> bool:
        # A lost Worker may not have learned the turn id yet; the thread's
        # turn in progress is then the one it started.
        find_active_turn = bool(runtime_options.get("interrupt_active_turn"))
        if not backend_session_id or not turn_id or not backend_route_id:
            if not (
                backend_session_id
                and backend_route_id
                and (isinstance(runtime_options.get("goal"), dict) or find_active_turn)
            ):
                return False
        route = self.account_registry.get(backend_route_id)
        client = self.client_factory(route.socket_path)
        goal_command = runtime_options.get("goal")
        paused = False
        try:
            if not turn_id and find_active_turn:
                turn_id = client.active_turn_id(backend_session_id)
            if turn_id:
                client.interrupt_turn(backend_session_id, turn_id)
        finally:
            if isinstance(goal_command, dict):
                try:
                    client.request(
                        "thread/goal/set",
                        {"threadId": backend_session_id, "status": "paused"},
                        timeout=30.0,
                    )
                    paused = True
                except Exception:
                    paused = False
        return bool(turn_id) or paused


def record_native_compaction(
    context: BackendContext,
    *,
    provider: str,
    backend_session_id: str,
    backend_route_id: str,
    compaction: NativeCompaction,
) -> None:
    """Record a compaction that a Provider committed in its own session files."""

    context.record_compaction(
        provider=provider,
        backend_session_id=backend_session_id,
        backend_route_id=backend_route_id,
        content={"replacement_history": [dict(item) for item in compaction.replacement_history]},
        created_at=compaction.created_at,
        retain_from=compaction.retain_from,
        provider_compaction_ref=compaction.ref,
    )


def report_request_usage(context: BackendContext, observed: ObservedTurnUsage | None) -> None:
    """Record each Provider request in the usage ledger under its own identity.

    This runs whether the turn completed, failed or was cancelled; a recovered
    attempt reports the same requests again and the ledger counts each once.
    """

    for request in observed.requests if observed is not None else ():
        try:
            context.emit("token_usage_delta", {
                "event_id": request.request_id,
                "usage": dict(request.usage),
                # Read after the turn: the ledger hour is when the request was made.
                "occurred_at": request.occurred_at,
            })
        except Exception:  # noqa: BLE001 - usage reporting never fails a turn
            LOGGER.warning("Token usage of request %s could not be recorded", request.request_id, exc_info=True)


def _kimi_home() -> str:
    from management_portal import kimi_code_server

    return os.getenv("PORTAL_KIMI_CODE_HOME") or str(kimi_code_server.DEFAULT_HOME)


def _kimi_wire(session_id: str) -> Path | None:
    return kimi_wire_path(_kimi_home(), session_id)


def kimi_latest_compaction(session_id: str) -> NativeCompaction | None:
    """The newest compaction of a Kimi Code session, read from its wire file."""

    path = _kimi_wire(session_id)
    return read_kimi_latest_compaction(path) if path else None


def request_scoped_item_event(
    event: dict[str, Any],
    turn_id: str,
    request_key: str,
) -> dict[str, Any]:
    """Make item IDs that a native Provider counts per request unique.

    The Claude daemon names text and thinking items ``<turn>:<message>:...``,
    counting from the start of each Provider request.  A recovered Execution
    can send a second request under the same turn; without a request key its
    items would reuse the first request's IDs for different content, which
    AgentData rightly rejects as a conflicting replay.
    """

    params = event.get("params")
    if not isinstance(params, dict):
        return event
    if event.get("method") == "subagent/event" and isinstance(params.get("event"), dict):
        return {**event, "params": {**params, "event": request_scoped_item_event(
            params["event"], turn_id, request_key,
        )}}
    item = params.get("item")
    if not isinstance(item, dict):
        return event
    item_id = str(item.get("id") or "")
    prefix = f"{turn_id}:"
    if not turn_id or not request_key or not item_id.startswith(prefix):
        return event
    scoped_id = f"{turn_id}:{request_key}:{item_id[len(prefix):]}"
    return {**event, "params": {**params, "item": {**item, "id": scoped_id}}}


def _unphased_agent_message(event: Mapping[str, Any]) -> bool:
    params = event.get("params")
    item = params.get("item") if isinstance(params, Mapping) else None
    return (
        event.get("method") == "item/completed"
        and isinstance(item, Mapping)
        and item.get("type") == "agentMessage"
        and not str(item.get("phase") or "").strip()
    )


def _with_answer_phase(event: dict[str, Any], phase: str) -> dict[str, Any]:
    params = event["params"]
    return {**event, "params": {**params, "item": {**params["item"], "phase": phase}}}


# A Provider error with one of these in its text will fail again the same way.
_UNCONTINUABLE_ERROR_MARKERS = (
    "authenticat", "unauthorized", "api key", "api_key", "forbidden", "permission denied",
    "safety", "content policy", "policy violation", "insufficient", "balance", "billing", "credit",
    "context length", "context window", "prompt is too long", "maximum context", "too many tokens",
)
PROVIDER_CONTINUATION_WAIT_SECONDS = 540.0
# A turn that could not reach Anthropic from this host (refused for the
# location, or the VPS tunnel down while the host holds its default traffic
# closed) waits for the egress to come back: how long, how often it looks,
# and how many such continuations one Execution sends.  The outages seen on
# 10-07 lasted 30 to 150 seconds.
EGRESS_BLOCK_WAIT_SECONDS = 1800.0
EGRESS_PROBE_INTERVAL_SECONDS = 10.0
EGRESS_BLOCK_CONTINUE_ATTEMPTS = 5
# Runtime option: tool results DSH replaces at the first step of the turn.
# How long a Kimi turn waits for Kimi Code to register Portal tools that are new to it.
PORTAL_TOOL_REGISTRATION_WAIT_SECONDS = 36.0




# What continues a turn that is no longer running (its Provider process
# restarted, or a Worker left it): the Provider gets no input twice.
INTERRUPTED_TURN_CONTINUATION = "continue"


def report_recovery_input(
    context: BackendContext,
    *,
    provider: str,
    reason: str,
    prompt: str,
    input_delivery: str,
    turn_id: str,
    image_count: int = 0,
) -> None:
    """Record the input Portal hands a Provider to recover a turn, as it goes out.

    ``reason``: ``worker_handover`` (a handover interrupted a Codex turn),
    ``turn_interrupted`` (the Provider's process no longer ran the turn),
    ``provider_error`` or ``provider_transition``.  ``input_delivery``:
    ``continued`` (the Provider had the turn's input) or ``first_delivery``
    (it never had it; ``prompt`` is the turn's own input).  A Worker that
    attaches to a running turn sends nothing and records nothing here.
    """

    context.emit("recovery_input_sent", {
        "provider": provider, "reason": reason, "prompt": prompt,
        "input_delivery": input_delivery, "turn_id": turn_id,
        **({"image_count": image_count} if image_count else {}),
    })


def continuation_prompt_of(invocation: BackendInvocation) -> str:
    """The prompt of a continuation after a Provider error, per the Harness policy."""

    policy = getattr(invocation, "provider_error_recovery", None)
    return str(getattr(policy, "continuation_prompt", "") or INTERRUPTED_TURN_CONTINUATION)


def provider_error_continuation(
    error: BaseException,
    *,
    invocation: BackendInvocation,
    context: BackendContext,
    provider_id: str,
    turn_started: bool,
) -> tuple[str, int]:
    """The prompt and number of the continuation Portal sends now, or re-raise.

    The Harness attaches the recovery policy (``provider_error_continue``).
    An Adapter that declares ``error_continuation`` asks here after its turn
    failed.  The turn is continued in the same Provider session when the
    Provider had already taken the turn's input, the failure is one a second
    request can get past, Portal is not cancelling the Execution, and this
    Execution has an attempt left; the used attempts are kept with the
    recovery checkpoint, so another Worker does not start counting again.

    A turn whose route reaches a location-restricted service
    (``provider_egress``: Anthropic, through the VPS egress) and could not
    reach it from this host is not the Provider failing: refused for the
    location (``blocked``), or unable to connect while the probe cannot reach
    the service either (``unreachable``; the VPS tunnel is down).  The
    continuation waits until the service serves this host again and has its
    own attempts (``EGRESS_BLOCK_CONTINUE_ATTEMPTS``).  A connection failure
    while the probe gets through is an ordinary Provider error, and so is any
    failure on a route whose service serves every location.
    ``provider_error_continue_count`` counts every continuation, which names
    the continuation turn; ``egress_block_continue_count`` the ones after
    the egress was unavailable.
    """

    policy = getattr(invocation, "provider_error_recovery", None)
    current = getattr(context, "current_provider_recovery_state", None)
    state = current() if callable(current) else None
    if not isinstance(state, dict):
        # This attempt has not continued yet: the counts it started with.
        checkpoint = getattr(invocation, "recovery_checkpoint", None)
        state = checkpoint.get("provider_recovery_state") if isinstance(checkpoint, dict) else None
    state = dict(state) if isinstance(state, dict) else {}
    used = _positive_int(state.get("provider_error_continue_count"))
    egress_used = _positive_int(state.get("egress_block_continue_count"))
    text = str(error).casefold()
    session = getattr(invocation, "session", None)
    upstream = route_upstream(provider_id, str(getattr(session, "backend_route_id", "") or ""))
    restricted = location_restricted(upstream)
    egress = "blocked" if restricted and is_egress_block_failure(text) else ""
    if (
        not egress
        and restricted
        and policy is not None
        and turn_started
        and is_network_failure(text)
        and not context.is_cancelled()
        and egress_state(upstream) != "allowed"
    ):
        egress = "unreachable"
    egress_block = bool(egress)
    if (
        policy is None
        or (
            egress_used >= EGRESS_BLOCK_CONTINUE_ATTEMPTS
            if egress_block
            else used - egress_used >= policy.max_continue_attempts
        )
        or not turn_started
        or isinstance(error, (InterruptedError, ExecutionControlSignal, ValueError, TypeError))
        or context.is_cancelled()
        or is_usage_limit_failure(text)
        or (not egress_block and any(marker in text for marker in _UNCONTINUABLE_ERROR_MARKERS))
    ):
        raise error
    if egress_block:
        context.emit("provider_egress_unavailable", {
            "provider": provider_id, "upstream": upstream, "state": egress, "error": str(error)[:300],
        })
        if not wait_for_provider_egress(context, upstream):
            raise error
    elif not wait_before_continuation(policy, context):
        raise error
    attempt = used + 1
    context.set_provider_recovery_state({
        **state, "provider_error_continue_count": attempt,
        **({"egress_block_continue_count": egress_used + 1} if egress_block else {}),
    })
    context.emit("agent_harness_provider_error_continue", {
        "attempt": attempt,
        "max_attempts": EGRESS_BLOCK_CONTINUE_ATTEMPTS if egress_block else policy.max_continue_attempts,
        "provider": provider_id,
        "reason": f"egress_{egress}" if egress_block else "provider_error",
        "error": str(error)[:300],
    })
    # The continuation starts because the Provider failed.  A Worker handover
    # whose turn kept running started no turn, and its pending reason must
    # not name this start.
    context.set_turn_start_reason("provider_recovery", replace_pending=True)
    return policy.continuation_prompt, attempt


def wait_before_continuation(policy: ProviderErrorRecoveryPolicy, context: BackendContext) -> bool:
    """Wait the policy's pause before the continuation; False when the Execution is cancelled meanwhile."""

    remaining = max(0.0, float(policy.continue_delay_seconds))
    while remaining > 0:
        if context.is_cancelled():
            return False
        step = min(0.5, remaining)
        time.sleep(step)
        remaining -= step
    return not context.is_cancelled()


def wait_for_provider_egress(
    context: BackendContext,
    upstream: str,
    *,
    probe: Callable[[], str] | None = None,
) -> bool:
    """Wait until the service ``upstream`` serves this host's requests again.

    False when the Execution is cancelled or the egress does not come back
    within ``EGRESS_BLOCK_WAIT_SECONDS``.  A Worker handing the Execution
    over stops waiting; the next Worker continues the turn.
    """

    probe = probe or (lambda: egress_state(upstream))
    deadline = time.monotonic() + EGRESS_BLOCK_WAIT_SECONDS
    while True:
        if context.is_cancelled():
            return False
        if context.is_handover():
            raise ExecutionRecoveryDeferred(
                "Worker handover: the turn waits for the Provider egress on the next Worker",
                retry_after_seconds=EGRESS_PROBE_INTERVAL_SECONDS,
            )
        if probe() == "allowed":
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        pause = min(EGRESS_PROBE_INTERVAL_SECONDS, remaining)
        while pause > 0:
            if context.is_cancelled() or context.is_handover():
                break
            step = min(0.5, pause)
            time.sleep(step)
            pause -= step


def run_provider_continuation(
    run: Callable[[], Any],
    *,
    context: BackendContext,
    unavailable: tuple[type[BaseException], ...],
) -> Any:
    """Send the continuation, waiting for a Provider process that is restarting."""

    deadline = time.monotonic() + PROVIDER_CONTINUATION_WAIT_SECONDS
    while True:
        try:
            return run()
        except unavailable:
            if context.is_cancelled() or time.monotonic() >= deadline:
                raise
            time.sleep(5)


def _report_tool_events(
    invocation: BackendInvocation,
    events: list[dict[str, Any]],
) -> None:
    """Hand common tool events to the Harness observer of this turn."""

    processor = getattr(invocation, "provider_event_processor", None)
    if processor is None:
        return
    for event in events:
        try:
            processor.process_event(event)
        except Exception:  # noqa: BLE001 - observation never fails a turn
            LOGGER.debug("Harness event observer failed", exc_info=True)


def _observe_tool_edit_artifacts(
    context: BackendContext, tool_edits: ToolEditArtifacts, *,
    provider: str, session_id: str, turn_id: str, status: str,
) -> None:
    """Record the turn's 本轮修改 before its turn/completed, as Codex's artifacts arrive."""

    artifacts = tool_edits.finish(thread_id=session_id, turn_id=turn_id, status=status)
    if artifacts is not None:
        context.observe_provider_event(
            {"method": "item/completed", "params": artifacts},
            provider=provider, backend_session_id=session_id,
        )


def _report_context_usage(invocation: BackendInvocation, usage: Mapping[str, Any]) -> None:
    """Tell the Harness observer how full the context was after the turn."""

    context_usage = usage.get("context")
    tokens = _positive_int(context_usage.get("inputTokens")) if isinstance(context_usage, Mapping) else 0
    window = _positive_int(usage.get("modelContextWindow"))
    if tokens and window:
        _report_tool_events(invocation, [{
            "method": "thread/tokenUsage/updated",
            "params": {"tokenUsage": {"last": {"inputTokens": tokens}, "modelContextWindow": window}},
        }])


def nothing_to_compact(outcome: Mapping[str, Any]) -> bool:
    """Whether a compaction did not run because the history is too short."""

    reason = str(outcome.get("reason") or "")
    return reason in {"nothing_to_compact", "summary_not_smaller", "no_session"} or "not enough messages" in reason.casefold()


def compaction_note(outcome: Mapping[str, Any]) -> str:
    """What an on-demand compaction did, for the person who asked for it."""

    if not outcome.get("compacted"):
        return "没有足够的历史可以压缩，会话未改变。"
    before = _positive_int(outcome.get("pre_tokens"))
    after = _positive_int(outcome.get("context_tokens"))
    if before and after:
        return f"上下文从约 {before:,} token 压缩到约 {after:,} token。"
    items = _positive_int(outcome.get("shadowed_items"))
    removed = _positive_int(outcome.get("shadowed_tokens"))
    if items:
        note = f"较早的 {items} 条历史已换成一份摘要（约 {removed:,} token）"
        return note + (f"，压缩后上下文约 {after:,} token。" if after else "。")
    return "压缩已完成。"


class NativeSocketBackend:
    provider_id: str
    client_factory: Any
    # Whether the Provider marks its agent messages as commentary or as the
    # turn's final answer.  Without that mark the adapter decides: the last
    # agent message of a completed turn is the answer, every other one is
    # commentary.
    marks_answer_phases: bool = True
    scopes_item_ids: bool = True

    def prepare_turn_tools(self, invocation, context):
        from .native_harness_tools import prepare_tools
        return prepare_tools(invocation, context)

    def turn_scope(self, session_id, catalog, dispatch):
        return nullcontext()

    def client_turn_options(self, invocation, context, catalog):
        return self.turn_options(invocation.request.runtime_options)

    def media_options(self, invocation: BackendInvocation) -> dict[str, Any]:
        if invocation.request.input_media:
            raise ValueError("Native adapter does not support direct input media")
        return {}

    def compaction_options(self, options: Mapping[str, Any], backend_session_id: str = "") -> dict[str, Any]:
        """What the Adapter's client needs to set the Provider's compaction threshold."""

        return {}

    def turn_options(self, options: Mapping[str, Any]) -> dict[str, Any]:
        """What else the Adapter's client is given for this turn.

        A Provider whose model can start subagents is given the Resident's
        "允许开启 sub agent": off takes its subagent tool away.
        """

        definition = AGENT_PROVIDERS[self.provider_id]
        return {"allow_subagents": definition.allows_subagents(dict(options))} if SUBAGENTS in definition.harness else {}

    def compaction_control(self, context):
        return {}

    def handover_options(self, context: BackendContext) -> dict[str, Any]:
        """Let a retiring Worker leave the turn running (``handover_keeps_turn``)."""

        if AGENT_PROVIDERS[self.provider_id].handover_keeps_turn:
            return {"detach_requested": context.is_handover}
        return {}

    def prepare_context_seed(
        self,
        invocation: BackendInvocation,
    ) -> BackendInvocation:
        return realize_portable_seed(invocation)

    def execute(
        self,
        invocation: BackendInvocation,
        context: BackendContext,
    ) -> BackendResult:
        request = invocation.request
        session = invocation.session
        options = request.runtime_options
        operation = request.operation
        media_options = self.media_options(invocation)
        route_id = str(options.get("provider_profile") or session.backend_route_id or AGENT_PROVIDERS[self.provider_id].default_route)
        if session.backend_route_id and session.backend_route_id != route_id:
            raise ValueError("Provider route changes require a session migration")
        session_key = str(
            options.get("session_key")
            or request.session.binding_id
            or request.execution_id
        )
        context.set_interrupt_target(session.backend_session_id)
        if operation == "release":
            closed = self.client_factory().close_session(
                session_key,
                session.backend_session_id,
            ) or {}
            if "active turn" in str(closed.get("reason") or ""):
                # The live session would carry on into the key's next turn
                # (its daemon reuses it), so it is not released yet; the
                # release is retried once that turn has ended.
                raise RuntimeError(f"{self.provider_id} session has an active turn; release it when the turn ends")
            return BackendResult(
                output={
                    "message": "",
                    "provider": self.provider_id,
                    "operation": operation,
                },
            )
        if operation == "fork":
            forked = self.client_factory().fork_session(
                source_session_id=session.backend_session_id,
                cwd=request.cwd,
                config_dir=str(options.get("config_dir") or ""),
                title=str(options.get("title") or ""),
                provider_profile=route_id,
            )
            forked_id = str(forked.get("session_id") or "")
            if not forked_id:
                raise RuntimeError("Native session fork returned no session id")
            return BackendResult(
                output={
                    "message": "",
                    "provider": self.provider_id,
                    "operation": operation,
                    "forked_from_session_id": session.backend_session_id,
                },
                backend_session_id=forked_id,
                backend_route_id=route_id,
            )
        if operation == "compact":
            return self.compact_native_session(invocation, context, session_key=session_key, route_id=route_id)
        if operation not in {"turn"}:
            raise ValueError(
                f"unsupported native backend operation: {operation}"
            )

        request_key = secrets.token_hex(6)
        observed_session = {"id": session.backend_session_id}
        pending_message: dict[str, dict[str, Any] | None] = {"event": None}
        turn_started = {"value": False}
        # The Harness reads the Provider's own tool calls in the common shape.
        tool_events = NativeToolEventTranslator(request.cwd)
        # Without a native turn diff, 本轮修改 comes from the file tools' calls.
        tool_edits = ToolEditArtifacts(request.cwd)

        def flush_pending_message(phase: str) -> None:
            held = pending_message["event"]
            pending_message["event"] = None
            if held is not None:
                deliver(_with_answer_phase(held, phase))

        def event_callback(event: dict[str, Any]) -> None:
            if self.scopes_item_ids:
                event = request_scoped_item_event(event, turn_id, request_key)
            if self.marks_answer_phases:
                deliver(event)
                return
            # Hold each agent message until the next item or the end of the
            # turn shows whether it was the answer.
            method = str(event.get("method") or "")
            if _unphased_agent_message(event):
                flush_pending_message("commentary")
                pending_message["event"] = event
                return
            if method in {"item/started", "item/completed"}:
                flush_pending_message("commentary")
            elif method == "turn/completed":
                params = event.get("params") if isinstance(event.get("params"), dict) else {}
                turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
                completed = str(turn.get("status") or "completed") == "completed"
                flush_pending_message("final_answer" if completed else "commentary")
            deliver(event)

        def deliver(event: dict[str, Any]) -> None:
            method = str(event.get("method") or "")
            params = event.get("params") if isinstance(event.get("params"), dict) else {}
            thread = params.get("thread") if isinstance(params.get("thread"), dict) else {}
            session_id = str(
                params.get("threadId")
                or params.get("sessionId")
                or thread.get("id")
                or session.backend_session_id
                or ""
            )
            if session_id:
                observed_session["id"] = session_id
            turn = (
                params.get("turn")
                if isinstance(params.get("turn"), dict)
                else {}
            )
            event_turn_id = str(
                turn.get("id") or params.get("turnId") or ""
            )
            if method == "turn/started":
                turn_started["value"] = True
                if event_turn_id:
                    observed_session["turn_id"] = event_turn_id
                context.bind_input(
                    provider=self.provider_id,
                    backend_session_id=session_id,
                    turn_id=event_turn_id,
                    provider_message_id=str(params.get("userMessageId") or event_turn_id),
                )
                input_pump.turn_started(event_turn_id or turn_id, turn_id, request.execution_id)
            elif method == "turn/completed":
                input_pump.turn_ended()
            elif method == "turn/recovered":
                # The daemon no longer had the turn and sent this input instead.
                report_recovery_input(
                    context, provider=self.provider_id, reason="turn_interrupted",
                    prompt=str(params.get("prompt") or ""),
                    input_delivery=str(params.get("delivery") or "continued"),
                    turn_id=str(params.get("turnId") or turn_id),
                    image_count=int(params.get("imageCount") or 0),
                )
                return
            if session_id:
                context.set_interrupt_target(session_id, observed_session.get("turn_id") or running_turn_id)
            if method == "thread/tokenUsage/updated":
                from .harness_capabilities import CONTEXT_USAGE_EVENTS
                if CONTEXT_USAGE_EVENTS in AGENT_PROVIDERS[self.provider_id].harness:
                    usage, window = _codex_live_context_usage(params.get("tokenUsage"))
                    context.observe_context_usage(usage, window)
                    _report_tool_events(invocation, [event])
            translated = tool_events.translate(event)
            for tool_event in translated:
                tool_edits.observe(tool_event)
            if method == "turn/completed":
                _observe_tool_edit_artifacts(
                    context, tool_edits, provider=self.provider_id, session_id=session_id,
                    turn_id=event_turn_id or turn_id, status=str(turn.get("status") or "completed"),
                )
            context.observe_provider_event(
                event,
                provider=self.provider_id,
                backend_session_id=session_id,
            )
            if getattr(invocation, "provider_event_processor", None) is not None:
                # Text and reasoning between tool calls are progress too.
                _report_tool_events(invocation, translated or [provider_activity()])

        turn_id = str(options.get("turn_id") or request.execution_id)
        if not self.scopes_item_ids and invocation.recovery_checkpoint.get("turn_id"):
            turn_id = str(invocation.recovery_checkpoint["turn_id"])

        def deliver_turn_input(run_turn_id: str, items: list[dict[str, Any]], control_id: str) -> tuple[bool, str]:
            return self.deliver_turn_input(
                session_key=session_key,
                session_id=str(observed_session["id"] or session.backend_session_id or ""),
                turn_id=run_turn_id,
                items=items,
                control_id=control_id,
            )

        # Send now and the loop governor's reminders reach a running turn
        # only where the Provider takes input between the steps of a turn.
        input_pump = TurnInputPump(
            context=context,
            event_processor=getattr(invocation, "provider_event_processor", None),
            deliver=deliver_turn_input,
        ) if AGENT_PROVIDERS[self.provider_id].active_turn_input else _NO_TURN_INPUT
        tool_catalog, tool_callback = self.prepare_turn_tools(invocation, context)
        client_options = self.client_turn_options(invocation, context, tool_catalog)
        context.set_interrupt_target(
            session.backend_session_id,
            turn_id,
        )
        self.prepare_native_session(
            session_key=session_key,
            backend_session_id=session.backend_session_id,
            options=options,
        )
        observed_usage: ObservedTurnUsage | None = None

        def run_turn(
            prompt: str, run_turn_id: str, session_id: str, media: dict[str, Any], continue_prompt: str = "",
        ) -> Any:
            return self.client_factory().run_turn(
                session_key=session_key,
                session_id=session_id,
                prompt=prompt,
                cwd=request.cwd,
                config_dir=str(options.get("config_dir") or ""),
                provider_profile=route_id,
                model=str(options.get("model") or ""),
                effort=str(options.get("effort") or ""),
                is_owner=bool(options.get("is_owner", False)),
                permission_profile=request.permission_profile,
                permission_mode=str(options.get("permission_mode") or ""),
                system_prompt_append=(
                    full_system_context(invocation)
                    if AGENT_PROVIDERS[self.provider_id].system_context_every_turn
                    else invocation.system_context
                    if invocation.attach_system_context
                    else ""
                ),
                add_dirs=[
                    str(item)
                    for item in options.get("add_dirs") or []
                    if str(item)
                ],
                turn_id=run_turn_id,
                event_callback=event_callback,
                tools=tool_catalog,
                tool_callback=tool_callback,
                resident_environment=dict(options.get("resident_environment") or {}),
                inherit_portal_database=may_inherit_portal_database(request),
                **media,
                **self.compaction_options(options, session.backend_session_id),
                **client_options,
                **self.handover_options(context),
                **({"continue_prompt": continue_prompt} if continue_prompt else {}),
            )

        # A turn an earlier attempt started stays that turn.  Under the same
        # turn id the Provider's process attaches to it while it runs (a
        # Worker handover left it running); a turn that is no longer running
        # is continued in the same session with "continue" once the Provider
        # had taken its input.  Only an input it never took is sent.
        recovery_checkpoint = getattr(invocation, "recovery_checkpoint", None) or {}
        handover = recovery_checkpoint.get("worker_handover")
        reattaching = bool(
            AGENT_PROVIDERS[self.provider_id].handover_keeps_turn
            and isinstance(handover, dict)
            and handover.get("turn_kept_running")
            and handover.get("turn_id") == turn_id
        )
        running_turn_id = turn_id
        running_prompt, running_media = invocation.user_prompt, media_options
        continue_prompt = INTERRUPTED_TURN_CONTINUATION if recovery_checkpoint.get("turn_id") else ""
        if continue_prompt:
            recovery_state = recovery_checkpoint.get("provider_recovery_state")
            continued = int((recovery_state or {}).get("provider_error_continue_count") or 0) if isinstance(recovery_state, dict) else 0
            if continued:
                # The turn running then was a continuation after a Provider
                # error, whose input was a continuation too.
                if self.scopes_item_ids:
                    running_turn_id = f"{turn_id}:continue{continued}"
                running_prompt, running_media = continuation_prompt_of(invocation), {}
        try:
            with input_pump, self.turn_scope(session.backend_session_id, tool_catalog, tool_callback):
                if recovery_checkpoint.get("turn_id") and context.is_cancelled():
                    raise InterruptedError("execution cancelled before attaching to its turn")
                if reattaching:
                    turn_started["value"] = True
                    input_pump.turn_started(running_turn_id, turn_id, request.execution_id)
                    context.emit("worker_handover_continuing", {
                        "provider": self.provider_id, "previous_turn_id": running_turn_id,
                        "input_delivery": "kept_running",
                    })
                try:
                    result = run_turn(
                        running_prompt, running_turn_id, session.backend_session_id, running_media, continue_prompt,
                    )
                except NativeAgentDetached:
                    raise
                except Exception as exc:  # noqa: BLE001 - re-raised unless the turn may be continued
                    # The Provider failed after it took the turn: carry on in the
                    # same session, as the Harness recovery policy allows.
                    input_pump.turn_ended()
                    failure: Exception = exc
                    while True:
                        prompt, attempt = provider_error_continuation(
                            failure, invocation=invocation, context=context,
                            provider_id=self.provider_id, turn_started=turn_started["value"],
                        )
                        flush_pending_message("commentary")
                        client = self.client_factory()
                        report_recovery_input(
                            context, provider=self.provider_id, reason="provider_error", prompt=prompt,
                            input_delivery="continued", turn_id=f"{turn_id}:continue{attempt}",
                        )
                        try:
                            result = run_provider_continuation(
                                lambda: run_turn(
                                    prompt, f"{turn_id}:continue{attempt}",
                                    str(observed_session["id"] or session.backend_session_id), {},
                                ),
                                context=context,
                                unavailable=(getattr(client, "unavailable_type", NativeAgentUnavailable),),
                            )
                            break
                        except Exception as again:  # noqa: BLE001 - only an unavailable egress goes on
                            # The egress can go away again before the
                            # continuation is answered; the recovery policy
                            # decides whether another one goes out.
                            if not (is_egress_block_failure(again) or is_network_failure(again)):
                                raise
                            failure = again
            observed_session["id"] = str(result.session_id or observed_session["id"] or "")
            flush_pending_message("final_answer")
        except NativeAgentDetached as exc:
            # The Scheduler requeues the Execution; the next Worker attaches.
            raise ExecutionRecoveryDeferred(
                f"Worker handover: the {self.provider_id} turn keeps running for the next Worker"
            ) from exc
        finally:
            # A message still held when the turn failed was not an answer.
            flush_pending_message("commentary")
            # A long turn can compact several times before it fails; the
            # Provider session keeps those compactions either way.
            self.record_provider_compaction(
                context=context,
                backend_session_id=str(observed_session["id"] or ""),
                backend_route_id=route_id,
                config_dir=str(options.get("config_dir") or ""),
            )
            # What the Provider's own record of the turn holds beyond the
            # events Portal received.
            # Every request the turn made is used, also when the turn failed.
            observed_usage = self.observe_turn_usage(
                context=context,
                backend_session_id=str(observed_session["id"] or ""),
                config_dir=str(options.get("config_dir") or ""),
            )
            report_request_usage(context, observed_usage)
        usage = self.turn_usage(result, observed_usage)
        _report_context_usage(invocation, usage)
        return BackendResult(
            output={
                "message": str(result.response or ""),
                "provider": self.provider_id,
                "turn_id": str(result.turn_id or ""),
            },
            backend_session_id=str(
                result.session_id or session.backend_session_id
            ),
            backend_route_id=route_id,
            usage=usage,
            available_harness_tool_names=frozenset(tool["name"] for tool in tool_catalog),
        )

    def compact_native_session(
        self,
        invocation: BackendInvocation,
        context: BackendContext,
        *,
        session_key: str,
        route_id: str,
    ) -> BackendResult:
        """Have the Provider compact the session's history now (``/compact``).

        Runs between turns.  The Provider replaces older history with a
        summary written by the session's model and keeps the newest messages;
        the compaction is recorded from the Provider's own session file, as
        after a turn.  The summary's own request is not reported by either
        Provider, so the operation records no token use; the size of the
        context afterwards updates the session's context meter.
        """

        request, session = invocation.request, invocation.session
        options = request.runtime_options
        if not session.backend_session_id:
            raise ValueError("The session has no Provider history to compact yet")
        if context.is_cancelled():
            raise InterruptedError("Compaction cancelled before it started")
        self.prepare_native_session(
            session_key=session_key, backend_session_id=session.backend_session_id, options=options,
        )
        outcome = self.client_factory().compact_session(
            session_key=session_key,
            session_id=session.backend_session_id,
            cwd=request.cwd,
            config_dir=str(options.get("config_dir") or ""),
            provider_profile=route_id,
            model=str(options.get("model") or ""),
            effort=str(options.get("effort") or ""),
            is_owner=bool(options.get("is_owner", False)),
            permission_profile=request.permission_profile,
            permission_mode=str(options.get("permission_mode") or ""),
            add_dirs=[str(item) for item in options.get("add_dirs") or [] if str(item)],
            resident_environment=dict(options.get("resident_environment") or {}),
            inherit_portal_database=may_inherit_portal_database(request),
            request_id=request.execution_id,
            **self.compaction_control(context),
            **self.turn_options(options),
            **self.compaction_options(options, session.backend_session_id),
        )
        session_id = str(outcome.get("session_id") or session.backend_session_id)
        compacted = bool(outcome.get("compacted"))
        if not compacted and not nothing_to_compact(outcome):
            raise RuntimeError(f"{AGENT_PROVIDERS[self.provider_id].label} did not compact: {outcome.get('reason') or 'unknown'}")
        if compacted:
            self.record_provider_compaction(
                context=context,
                backend_session_id=session_id,
                backend_route_id=route_id,
                config_dir=str(options.get("config_dir") or ""),
            )
        context_tokens = _positive_int(outcome.get("context_tokens"))
        return BackendResult(
            output={
                "message": compaction_note(outcome),
                "provider": self.provider_id,
                "operation": "compact",
                "compacted": compacted,
            },
            backend_session_id=session_id,
            backend_route_id=route_id,
            usage=(
                {"context": {"inputTokens": context_tokens, "totalTokens": context_tokens}}
                if context_tokens else {}
            ),
        )

    def deliver_turn_input(
        self,
        *,
        session_key: str,
        session_id: str,
        turn_id: str,
        items: list[dict[str, Any]],
        control_id: str,
    ) -> tuple[bool, str]:
        """Hand input to the running turn ``turn_id``; whether the Provider took it.

        Called beside the blocking turn call, from the turn's input pump.
        A ``subagent_stop`` item stops one subagent run instead.
        """

        if len(items) == 1 and items[0].get("type") == "subagent_stop":
            client = self.client_factory()
            if not hasattr(client, "stop_subagent"):
                return False, "subagent_stop_unsupported"
            result = client.stop_subagent(session_key, session_id, tool_use_id=str(items[0].get("tool_use_id") or ""))
            return bool(result.get("stopped")), str(result.get("reason") or "")
        text = text_input(items)
        if not text:
            return False, "turn_input_not_text"
        result = self.client_factory().steer(
            session_key, session_id, turn_id=turn_id, text=text, control_id=control_id,
        )
        return bool(result.get("steered")), str(result.get("reason") or "")

    def interrupt(
        self,
        *,
        backend_session_id: str,
        turn_id: str,
        backend_route_id: str,
        runtime_options: dict[str, Any],
    ) -> bool:
        session_key = str(runtime_options.get("session_key") or "")
        if not session_key and not backend_session_id:
            return False
        result = self.client_factory().interrupt(session_key, backend_session_id, turn_id=turn_id)
        return bool(result.get("interrupted"))

    def observe_turn_usage(self, *, context, backend_session_id, config_dir):
        since = str(getattr(context, "execution_started_at", "") or "")
        if not backend_session_id or not since:
            return None
        try:
            return self.read_turn_usage(backend_session_id, config_dir, since)
        except Exception:
            LOGGER.warning("%s usage could not be read for session %s", self.provider_id, backend_session_id, exc_info=True)
            return None

    def read_turn_usage(self, session_id, config_dir, since):
        return None

    def turn_usage(self, result: Any, observed: ObservedTurnUsage | None) -> dict[str, Any]:
        """The turn's usage as Portal records it (see ``provider_turn_usage``)."""

        return dict(result.usage) if isinstance(result.usage, dict) else {}

    def record_provider_compaction(self, *, context, backend_session_id, backend_route_id="", config_dir=""):
        if not backend_session_id:
            return
        try:
            compaction = self.latest_compaction(backend_session_id, config_dir)
            if compaction is not None:
                record_native_compaction(context, provider=self.provider_id, backend_session_id=backend_session_id,
                                         backend_route_id=backend_route_id, compaction=compaction)
        except Exception:
            LOGGER.warning("%s compaction could not be recorded for session %s", self.provider_id, backend_session_id, exc_info=True)

    def latest_compaction(self, session_id, config_dir):
        return None

    def prepare_native_session(
        self, *, session_key: str, backend_session_id: str, options: Mapping[str, Any] | None = None,
    ) -> None:
        """Adapters whose daemon keeps a live session per key align it here."""


class ClaudeBackend(NativeSocketBackend):
    provider_id = "claude"

    def prepare_native_session(
        self, *, session_key: str, backend_session_id: str, options: Mapping[str, Any] | None = None,
    ) -> None:
        """Run the turn in the requested Claude session, not a stale live one.

        The daemon keeps one live Claude session per session key and reuses it
        while the session options stay the same.  After a session restart the
        key's binding names a new session; an idle live session that still
        holds the old one is closed so that the turn resumes the requested one.
        """

        if not backend_session_id:
            return
        try:
            client = self.client_factory()
            status = client.session_status(session_key, backend_session_id)
            loaded = str(status.get("session_id") or "")
            if (
                status.get("loaded")
                and not status.get("active")
                and loaded
                and loaded != backend_session_id
            ):
                client.close_session(session_key, loaded)
        except Exception:  # noqa: BLE001 - the daemon then keeps its previous behaviour
            LOGGER.warning(
                "Claude session %s could not be aligned for key %s",
                backend_session_id,
                session_key,
                exc_info=True,
            )

    def latest_compaction(self, session_id, config_dir):
        path = claude_transcript_path(config_dir, session_id) if config_dir else None
        value = read_claude_latest_compaction(path) if path else None
        return (NativeCompaction(value.boundary_uuid or value.fingerprint, value.created_at,
                                 value.retain_from, value.replacement_history) if value else None)

    def read_turn_usage(self, session_id, config_dir, since):
        path = claude_transcript_path(config_dir, session_id) if config_dir else None
        return read_claude_turn_usage(path, since=since) if path else None

    def turn_usage(self, result: Any, observed: ObservedTurnUsage | None) -> dict[str, Any]:
        return claude_turn_usage(result.usage, observed, getattr(result, "model_usage", None))

    def configure_auto_compact_threshold(
        self,
        *,
        invocation: BackendInvocation,
        threshold_percent: int,
        max_context_tokens: int = 0,
    ) -> InvocationTransformResult:
        """Set the context size from which Claude Code compacts by itself.

        Claude Code takes the size in tokens when its CLI starts.  The Adapter
        learns a model's context window only from a finished turn, so a share
        of the window alone cannot be turned into tokens; the policy's token
        bound can.
        """

        del threshold_percent
        tokens = _positive_int(max_context_tokens)
        if invocation.request.operation != "turn":
            return InvocationTransformResult(invocation, False, "operation_not_supported")
        if not tokens:
            return InvocationTransformResult(invocation, False, "model_context_window_unavailable")
        options = dict(invocation.request.runtime_options)
        options["auto_compact_window_tokens"] = tokens
        return InvocationTransformResult(
            replace(invocation, request=replace(invocation.request, runtime_options=options)),
            True,
            "native_auto_compact_threshold_configured",
            {"auto_compact_token_limit": tokens},
        )

    def compaction_options(self, options: Mapping[str, Any], backend_session_id: str = "") -> dict[str, Any]:
        tokens = _positive_int(options.get("auto_compact_window_tokens"))
        return {"auto_compact_window_tokens": tokens} if tokens else {}


    def media_options(self, invocation: BackendInvocation) -> dict[str, Any]:
        if not invocation.request.input_media:
            return {}
        route = invocation.session.backend_route_id or invocation.request.runtime_options.get("provider_profile")
        if not AGENT_PROVIDERS[self.provider_id].catalog(route)["capabilities"]["media"]:
            raise ValueError("Selected Claude route does not support images")
        return {"input_media": [item.to_payload() for item in invocation.request.input_media]}

    @staticmethod
    def client_factory():
        return shared_claude_client()


class DshBackend(NativeSocketBackend):
    provider_id = "dsh"
    marks_answer_phases = False

    def interrupt(
        self,
        *,
        backend_session_id: str,
        turn_id: str,
        backend_route_id: str,
        runtime_options: dict[str, Any],
    ) -> bool:
        if not turn_id and runtime_options.get("interrupt_active_turn"):
            # A lost Worker may not have seen its turn start. Portal names a
            # DSH turn itself, and a new session after that turn.
            from management_portal.dsh_client import session_id_for

            turn_id = str(runtime_options.get("turn_id") or runtime_options.get("abandoned_execution_id") or "")
            if turn_id and not backend_session_id:
                backend_session_id = session_id_for(turn_id)
        return super().interrupt(
            backend_session_id=backend_session_id, turn_id=turn_id,
            backend_route_id=backend_route_id, runtime_options=runtime_options,
        )

    def prepare_native_session(
        self, *, session_key: str, backend_session_id: str, options: Mapping[str, Any] | None = None,
    ) -> None:
        """Start a new DSH agent when the requested model or effort changed.

        After its first request a DSH agent derives every request from the
        request header it logged, so a changed model or reasoning effort only
        takes effect in a new agent instance that resumes the session.  The
        compaction threshold and the subagent tool are fixed when the DSH
        process starts: an earlier threshold than the launched one, or a
        changed "允许开启 sub agent", starts a new process too.
        """

        if not backend_session_id:
            return
        requested = dict(options or {})
        model = str(requested.get("model") or "")
        effort = str(requested.get("effort") or "high")
        try:
            from management_portal.dsh_client import STATE, launched_compaction_ratio, launched_with_subagents

            wanted = self._compaction_ratio(requested, backend_session_id)
            launched = launched_compaction_ratio(backend_session_id)
            subagents = AGENT_PROVIDERS[self.provider_id].allows_subagents(requested)
            if (wanted and launched and wanted < launched - 0.001) or (
                launched_with_subagents(backend_session_id) is not subagents
            ):
                self.client_factory().close_session(session_key, backend_session_id)
                return
            path = dsh_session_path(STATE, backend_session_id)
            config = read_dsh_request_config(path) if path else {}
            if not config or not model:
                return
            logged_effort = (
                "" if config["_adapter_defaults"].get("reasoningEffort") is True
                else str(config.get("reasoningEffort") or "")
            )
            if config.get("model") != model or (logged_effort and logged_effort != effort):
                self.client_factory().close_session(session_key, backend_session_id)
        except Exception:  # noqa: BLE001 - the turn then keeps the previous settings
            LOGGER.warning(
                "DSH session %s could not apply its new runtime settings",
                backend_session_id,
                exc_info=True,
            )

    def configure_auto_compact_threshold(
        self,
        *,
        invocation: BackendInvocation,
        threshold_percent: int,
        max_context_tokens: int = 0,
    ) -> InvocationTransformResult:
        """Ask DSH to condense history at an earlier point than its default.

        DSH takes the threshold as a share of the model's window when its
        process starts; ``_compaction_ratio`` turns the request into that share.
        """

        if invocation.request.operation != "turn":
            return InvocationTransformResult(invocation, False, "operation_not_supported")
        options = dict(invocation.request.runtime_options)
        options["auto_compact_percent"] = max(1, min(99, int(threshold_percent)))
        options["auto_compact_max_tokens"] = _positive_int(max_context_tokens)
        return InvocationTransformResult(
            replace(invocation, request=replace(invocation.request, runtime_options=options)),
            True,
            "native_auto_compact_threshold_configured",
            {
                "requested_threshold_percent": options["auto_compact_percent"],
                "max_context_tokens": options["auto_compact_max_tokens"],
            },
        )

    @staticmethod
    def _compaction_ratio(options: Mapping[str, Any], backend_session_id: str) -> float:
        """The share of the window this turn asks DSH to compact at (0: none asked).

        A token bound becomes a share once the session's log tells the
        model's window; a new session has no log yet and uses the share alone.
        """

        from management_portal.dsh_client import STATE, compaction_ratio

        percent = _positive_int(options.get("auto_compact_percent"))
        bound = _positive_int(options.get("auto_compact_max_tokens"))
        if not percent and not bound:
            return 0.0
        ratio = (percent or 80) / 100
        path = dsh_session_path(STATE, backend_session_id) if backend_session_id else None
        window = read_dsh_context_window(path) if path else 0
        if bound and window:
            ratio = min(ratio, bound / window)
        return compaction_ratio(ratio)


    def compaction_options(self, options: Mapping[str, Any], backend_session_id: str = "") -> dict[str, Any]:
        from management_portal.dsh_client import launched_compaction_ratio

        wanted = self._compaction_ratio(options, backend_session_id)
        launched = launched_compaction_ratio(backend_session_id) if backend_session_id else 0.0
        # A session keeps the earliest threshold it was given.
        ratio = min(value for value in (wanted, launched) if value) if (wanted or launched) else 0.0
        return {"auto_compact_ratio": ratio} if ratio else {}

    def latest_compaction(self, session_id, config_dir):
        from management_portal.dsh_client import STATE
        path = dsh_session_path(STATE, session_id)
        return read_dsh_latest_compaction(path) if path else None

    def read_turn_usage(self, session_id, config_dir, since):
        from management_portal.dsh_client import STATE
        path = dsh_session_path(STATE, session_id)
        return read_dsh_turn_usage(path, session_id=session_id, since_ms=epoch_ms(since)) if path else None

    def turn_usage(self, result: Any, observed: ObservedTurnUsage | None) -> dict[str, Any]:
        return observed_turn_usage(result.usage, observed)

    @staticmethod
    def client_factory():
        from management_portal.dsh_client import shared_client
        return shared_client()


class KimiBackend(NativeSocketBackend):
    provider_id = "kimi"
    scopes_item_ids = False

    def __init__(self, client=None):
        self._account_client = client
        if client is not None:
            self.client_factory = lambda: client

    def account_home(self):
        return self._account_client.home if self._account_client is not None else _kimi_home()

    @staticmethod
    def client_factory():
        return shared_kimi_client()

    def media_options(self, invocation):
        # Kimi reads the paths already attached to the prompt.
        return {}

    def execute(self, invocation, context):
        request, session = invocation.request, invocation.session
        route = str(request.runtime_options.get("provider_profile") or session.backend_route_id or "")
        if session.backend_route_id and session.backend_route_id != route:
            raise ValueError("Provider route changes require a session migration")
        if route and self._account_client is None:
            from management_portal.kimi_account_runtime import client_for_route, remember_session
            result = KimiBackend(client_for_route(route)).execute(invocation, context)
            remember_session(result.backend_session_id, route)
            return result
        if not session.backend_session_id and request.operation in {"create", "turn"}:
            options = request.runtime_options
            created = self.client_factory().create_session(
                cwd=request.cwd, title=str(options.get("title") or "Portal execution"),
                system_prompt=invocation.system_context if invocation.attach_system_context else "",
                tools=list(options.get("tools") or []),
                permission_mode=str(options.get("permission_mode") or request.permission_profile),
                model=str(options.get("model") or ""),
            )
            session_id = str(created.get("id") or "")
            if not session_id:
                raise RuntimeError("Kimi session creation returned no session id")
            if route:
                from management_portal.kimi_account_runtime import remember_session
                remember_session(session_id, route)
            session = replace(session, backend_session_id=session_id)
            invocation = replace(invocation, session=session)
        if request.operation == "create":
            context.set_interrupt_target(session.backend_session_id)
            return BackendResult(output={"message": "", "provider": self.provider_id, "operation": "create"},
                                 backend_session_id=session.backend_session_id, backend_route_id=route)
        return super().execute(invocation, context)

    def deliver_turn_input(self, **kwargs):
        if self._account_client is None:
            from management_portal.kimi_account_runtime import client_for_route, session_route
            route = session_route(kwargs["session_id"])
            if route:
                return KimiBackend(client_for_route(route, for_control=True)).deliver_turn_input(**kwargs)
        return super().deliver_turn_input(**kwargs)

    def interrupt(self, **kwargs):
        route = kwargs["backend_route_id"] or kwargs["runtime_options"].get("provider_profile")
        if route and self._account_client is None:
            from management_portal.kimi_account_runtime import client_for_route
            return KimiBackend(client_for_route(route, for_control=True)).interrupt(**kwargs)
        return super().interrupt(**kwargs)

    def prepare_turn_tools(self, invocation, context):
        return self.offer_portal_tools(invocation, context, home=self.account_home())

    def turn_scope(self, session_id, catalog, dispatch):
        return KimiToolGateway(session_id, dispatch if catalog else None)

    def client_turn_options(self, invocation, context, catalog):
        from .native_options import KIMI_SUBAGENT_TOOLS
        options = invocation.request.runtime_options
        tools = list(options.get("tools") or [])
        profile = {"tools": tools, "permission_mode": str(options.get("permission_mode") or invocation.request.permission_profile),
                   "model": str(options.get("model") or "")}
        disabled = self.settle_portal_tools(invocation.session.backend_session_id, catalog,
                                           profile=profile, cancelled=context.is_cancelled, client=self.client_factory())
        if not AGENT_PROVIDERS[self.provider_id].allows_subagents(dict(options)):
            disabled += sorted(KIMI_SUBAGENT_TOOLS)
        return {"native_tools": tools, "disabled_tools": disabled, "cancelled": context.is_cancelled,
                "prepend_system_prompt": invocation.attach_system_context and not invocation.is_new_backend_session}

    def compaction_options(self, options, backend_session_id=""):
        return {}

    def compaction_control(self, context):
        return {"cancelled": context.is_cancelled, "detach_requested": context.is_handover}

    def compact_native_session(self, invocation, context, **kwargs):
        # The HTTP operation keeps running after this worker detaches.
        try:
            return super().compact_native_session(invocation, context, **kwargs)
        except KimiCodeDetached as exc:
            raise ExecutionRecoveryDeferred("Worker handover: Kimi compaction keeps running") from exc

    def latest_compaction(self, session_id, config_dir):
        path = kimi_wire_path(self.account_home(), session_id)
        return read_kimi_latest_compaction(path) if path else None

    def read_turn_usage(self, session_id, config_dir, since):
        path = kimi_wire_path(self.account_home(), session_id)
        observed = read_kimi_turn_usage(path, since_ms=epoch_ms(since)) if path else None
        if observed is None:
            return None
        status = self.client_factory().get_status(session_id)
        return replace(observed, context_window=_positive_int(status.get("max_context_tokens")))

    def turn_usage(self, result, observed):
        return kimi_turn_usage(result.usage, observed)

    @staticmethod
    def offer_portal_tools(
        invocation: BackendInvocation,
        context: BackendContext,
        *, home=None,
    ) -> tuple[list[dict[str, Any]], Callable[[dict[str, Any]], dict[str, Any]] | None]:
        """List this turn's Portal tools in the catalog Kimi Code reads.

        Returns the turn's catalog and the dispatcher the tool gateway
        serves. An optional tool failure leaves the turn without Portal tools;
        a required capability failure stops before the model turn starts.
        """

        from .native_harness_tools import prepare_tools
        from .resident_capability_assembly import require_offered_tools

        if not getattr(invocation, "harness_tools", ()):
            require_offered_tools(invocation, ())
            return [], None
        home = home if home is not None else _kimi_home()
        if not kimi_portal_tools.service_ready(home):
            # Without the hook Portal cannot tell which session makes a call.
            LOGGER.warning("The Kimi Code service runs without Portal's tool server and hook; restart it to offer Portal tools")
            require_offered_tools(invocation, ())
            return [], None
        try:
            catalog, dispatch = prepare_tools(invocation, context)
            # Kimi Code connects again, and lists the tools again, when the catalog grew.
            publish_catalog(catalog, home)
        except Exception:  # noqa: BLE001 - Portal tools are an addition to the turn
            LOGGER.warning("Portal tools could not be offered to Kimi Code", exc_info=True)
            require_offered_tools(invocation, ())
            return [], None
        require_offered_tools(invocation, {tool["name"] for tool in catalog})
        return catalog, dispatch

    @staticmethod
    def settle_portal_tools(
        session_id: str,
        catalog: list[dict[str, Any]],
        *,
        profile: dict[str, Any],
        cancelled: Callable[[], bool],
        client=None,
    ) -> list[str]:
        """The tools to switch off for the session's next prompt.

        Kimi Code lists Portal's tools once per workspace, for every session
        there, so a turn switches off the ones it does not have.  When the
        catalog has just grown, Kimi Code is still connecting again: the turn
        waits a moment for its own tools to be registered.
        """

        prefix = kimi_portal_tools.TOOL_NAME_PREFIX
        everything = [f"{prefix}*"]
        wanted = {f"{prefix}{tool['name']}" for tool in catalog}
        client = client if client is not None else shared_kimi_client()
        try:
            # A session is listed with its tools only once Kimi Code has loaded it.
            client.update_session_profile(session_id, **profile)
            deadline = time.monotonic() + (PORTAL_TOOL_REGISTRATION_WAIT_SECONDS if wanted else 0.0)
            while True:
                registered = {
                    str(tool.get("name") or "") for tool in client.session_tools(session_id)
                    if str(tool.get("name") or "").startswith(prefix)
                }
                if wanted <= registered or time.monotonic() >= deadline or cancelled():
                    break
                time.sleep(0.25)
        except Exception:  # noqa: BLE001 - the turn runs; tools Portal cannot vouch for stay off
            LOGGER.warning("Portal tools of Kimi session %s could not be settled", session_id, exc_info=True)
            return [] if wanted else everything
        if wanted - registered:
            LOGGER.warning(
                "Kimi Code has not registered Portal tools %s for session %s",
                sorted(wanted - registered), session_id,
            )
        return sorted(registered - wanted)


def _until_cancelled(call: Callable[[], Any], cancelled: Callable[[], bool]) -> Any:
    """Wait for one blocking Provider request; a stop ends the wait at once.

    The request runs on in its own daemon thread until its response or its own
    timeout, and what it returns after the stop is dropped: the Provider may
    still bill that response, but the Resident stops now.
    """

    outcome: dict[str, Any] = {}

    def run() -> None:
        try:
            outcome["response"] = call()
        except BaseException as exc:  # noqa: BLE001 - handed to the waiting caller
            outcome["error"] = exc

    worker = threading.Thread(target=run, name="chat-completion-request", daemon=True)
    worker.start()
    while worker.is_alive():
        worker.join(0.25)
        if worker.is_alive() and cancelled():
            raise InterruptedError("execution was cancelled during provider call")
    if "error" in outcome:
        raise outcome["error"]
    return outcome["response"]


class ChatCompletionService:
    service_type = "chat_completion"

    def __init__(
        self,
        provider_registry: AIProviderRegistry | None = None,
    ) -> None:
        self.provider_registry = (
            provider_registry or shared_ai_provider_registry
        )

    def execute(
        self,
        invocation: BackendInvocation,
        context: BackendContext,
    ) -> BackendResult:
        from .resident_capability_assembly import prepare
        invocation = prepare(invocation, context)
        transport: dict[str, Any] = {}
        try:
            return self._execute(invocation, context, transport)
        except Exception as exc:
            if transport.get("schema"):
                existing = getattr(exc, "error_details", {})
                exc.error_details = {**(existing if isinstance(existing, dict) else {}), "provider_transport": transport}
            raise

    def _execute(
        self, invocation: BackendInvocation, context: BackendContext, transport: dict[str, Any],
    ) -> BackendResult:
        request = invocation.request
        if context.is_cancelled():
            raise InterruptedError("execution was cancelled before provider call")
        options = request.runtime_options
        provider_id = request.provider
        item = self.provider_registry.get(provider_id)
        raw_messages = options.get("messages")
        messages = [
            dict(message)
            for message in raw_messages
            if isinstance(message, dict)
        ] if isinstance(raw_messages, list) else []
        if invocation.attach_system_context:
            messages.insert(
                0,
                {
                    "role": "system",
                    "content": invocation.system_context,
                },
            )
        business_system_prompt = str(options.get("system_prompt") or "").strip()
        if business_system_prompt:
            insert_at = 1 if invocation.attach_system_context else 0
            messages.insert(
                insert_at,
                {"role": "system", "content": business_system_prompt},
            )
        if messages and messages[-1].get("role") == "user":
            messages[-1]["content"] = invocation.user_prompt
        else:
            messages.append({"role": "user", "content": invocation.user_prompt})
        parameters = (
            dict(options.get("parameters"))
            if isinstance(options.get("parameters"), dict)
            else {}
        )
        from .model_api_protocols import protocol_adapter
        adapter = protocol_adapter(str(item.get("kind") or "openai_compatible_chat"))
        adapter.validate(parameters, require_questions=True)
        model = str(options.get("model") or item.get("default_model") or "").strip()
        if not model:
            raise ValueError(f"LLM provider model is missing: {provider_id}")
        if options.get("context_history"):
            context.emit("context_history", options["context_history"])
        result = adapter.execute(
            self.provider_registry, {**item, "id": provider_id}, model, messages, parameters,
            float(options.get("timeout_seconds") or 360), max(0, int(options.get("max_retries") or 0)),
            context.is_cancelled, transport.update, lambda call: _until_cancelled(call, context.is_cancelled),
        )
        return self._result(context, provider_id, model, result.response, result.content, transport,
                            structured=result.structured)

    def _result(self, context, provider_id, model, response, content, transport, *, structured=None):
        usage = (
            dict(response.get("usage"))
            if isinstance(response.get("usage"), dict)
            else {}
        )
        usage.update(
            {
                "api_id": provider_id,
                "model": str(model),
            }
        )
        context.emit(
            "provider_output",
            {"text": content, "final": True,
             "request_timing": {key: transport.get(key) for key in ("started_at", "finished_at")}},
        )
        return BackendResult(
            output={
                "message": content,
                "service_type": self.service_type,
                "provider": provider_id,
                "response": response,
                **({"structured": structured} if structured is not None else {}),
                **({"provider_transport": transport} if transport.get("schema") else {}),
            },
            usage=usage,
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
        return False


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
            try:
                result = provider.execute(prepared, backend_context)
            except Exception:
                observe_delivery(prepared, backend_context, completed=False)
                raise
            observe_delivery(prepared, backend_context, completed=True,
                             available_names=result.available_harness_tool_names)
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
