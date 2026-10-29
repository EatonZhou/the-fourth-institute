"""The AgentData projection of running executions into canonical Segments."""
from __future__ import annotations

import threading
from dataclasses import replace
from typing import Any, Mapping

from management_common.provider_subagent_runs import PROGRESS_METHOD as SUBAGENT_PROGRESS_METHOD

from .agent_data_input_capture import InputCapture
from .agent_data_output_capture import OutputCapture, event_created_at, event_turn_id, json_safe
from .agent_data_projection_state import ExecutionProjection
from .agent_data_session_capture import SessionCapture
from .agent_data_store import AgentDataStore, get_agent_data_store, stable_segment_id, utc_now
from .contracts import ExecutionRequest
from .control_messages import HARNESS_CONTROL_EVENT
from .recovery_messages import RECOVERY_INPUT_EVENT, recovery_input_message
from .session_manager import RuntimeSession


class AgentDataProjector:
    """Turns stable Provider behavior into canonical Segments.

    Streaming deltas and progress stay in memory.  Completed messages, terminal
    tool calls, effective Provider inputs, compactions, and execution summaries are
    committed to the Agent data store.

    The projector keeps each running execution's state and routes what it
    observes; capturing the Provider's input and output and following its
    session are components of their own.
    """

    def __init__(self, store: AgentDataStore | None = None) -> None:
        self.store = store or get_agent_data_store()
        self._active: dict[str, ExecutionProjection] = {}
        self._lock = threading.RLock()
        self.inputs = InputCapture(self.store)
        self.sessions = SessionCapture(self.store)
        self.output = OutputCapture(self.store)

    def begin(
        self,
        request: ExecutionRequest,
        session: RuntimeSession,
        *,
        created_at: str = "",
        recovery: bool = False,
    ) -> None:
        agent_id = request.agent_id
        has_provider_input = request.operation in {"turn", "generate"}
        accepted_at = str(created_at or utc_now())
        input_segment_id = (
            stable_segment_id("execution-input", request.execution_id)
            if has_provider_input
            else ""
        )
        projection = ExecutionProjection(
            request=request,
            session=session,
            agent_id=agent_id,
            input_segment_id=input_segment_id,
            effective_input_segment_id=input_segment_id,
            provider=request.provider,
            backend_session_id=session.backend_session_id,
            backend_route_id=session.backend_route_id,
        )
        with self._lock:
            if request.execution_id in self._active:
                raise RuntimeError(
                    f"Agent data projection already active: {request.execution_id}"
                )
            self._active[request.execution_id] = projection
        execution_started = False
        try:
            self.store.begin_execution(
                execution_id=request.execution_id,
                agent_id=agent_id,
                actor_ref=request.actor,
                provider=request.provider,
                started_at=accepted_at,
            )
            execution_started = True
            if not has_provider_input:
                return
            self.inputs.record_provider_input(projection, request, agent_id=agent_id,
                                              input_segment_id=input_segment_id, accepted_at=accepted_at)
        except BaseException:
            with self._lock:
                if self._active.get(request.execution_id) is projection:
                    self._active.pop(request.execution_id, None)
            if execution_started:
                self.store.finish_execution(request.execution_id, status="failed")
            raise

    def observe_invocation(
        self,
        execution_id: str,
        invocation: object,
        reports: list[dict[str, Any]] | None = None,
    ) -> None:
        with self._lock:
            projection = self._active.get(execution_id)
        if projection is None:
            return
        operation = projection.request.operation
        if operation not in {"turn", "generate"}:
            return
        self.inputs.record_effective_input(projection, execution_id, invocation)
        self.inputs.record_system_context(projection, execution_id, invocation)
        projection.provider_input_observed = True
        initial_backend_session_id = projection.session.backend_session_id
        if (
            not initial_backend_session_id
            and projection.request.session.mode == "stateless"
        ):
            initial_backend_session_id = projection.request.execution_id
        if initial_backend_session_id and not projection.request.metadata.get("fork_context_input"):
            self.sessions.bind_provider_session(
                projection,
                provider=projection.request.provider,
                backend_session_id=initial_backend_session_id,
                backend_route_id=projection.session.backend_route_id,
            )

    def observe_runtime_event(
        self,
        execution_id: str,
        event_type: str,
        details: Mapping[str, Any],
    ) -> None:
        with self._lock:
            projection = self._active.get(execution_id)
        if projection is None:
            return
        if event_type == "provider_input_bound":
            if projection.request.operation not in {"turn", "generate"}:
                return
            projection.turn_id = str(details.get("turn_id") or "")
            if not projection.turn_id.strip():
                raise ValueError("Provider input acceptance requires a turn identity")
            resolved_backend_session_id = str(
                details.get("backend_session_id")
                or projection.session.backend_session_id
                or ""
            )
            if (
                not resolved_backend_session_id
                and projection.request.session.mode == "stateless"
            ):
                resolved_backend_session_id = projection.request.execution_id
            if not resolved_backend_session_id:
                return
            self.sessions.bind_provider_session(
                projection,
                provider=str(details.get("provider") or projection.request.provider),
                backend_session_id=resolved_backend_session_id,
                backend_route_id=str(
                    details.get("backend_route_id")
                    or projection.session.backend_route_id
                ),
            )
            self.store.record_input_acceptance(
                execution_id, agent_id=projection.agent_id,
                provider_turn_id=projection.turn_id,
            )
        elif event_type == HARNESS_CONTROL_EVENT:
            self.inputs.record_harness_control(projection, execution_id, details)
        elif event_type == RECOVERY_INPUT_EVENT:
            # Resident recovery input reaches the trace through the same
            # ordered Conversation consumer as other visible output.
            message = recovery_input_message(details, execution_id=execution_id)
            if message is not None and (not projection.agent_id.startswith("resident_") or projection.trace_key):
                self.store.project_chat_message(projection.agent_id, message)
        elif event_type == "provider_output":
            if projection.request.operation not in {"turn", "generate"}:
                return
            text = str(details.get("text") or "").strip()
            if text:
                self.output.persist_model_message(
                    projection,
                    text=text,
                    phase="final" if details.get("final") else "message",
                    item_id=str(details.get("id") or ""),
                    created_at=utc_now(),
                    turn_id=projection.turn_id,
                    request_timing=details.get("request_timing"),
                )

    def observe_compaction(
        self,
        *,
        execution_id: str,
        provider: str,
        backend_session_id: str,
        backend_route_id: str,
        content: Mapping[str, Any],
        created_at: str = "",
        retain_from: str = "",
        provider_compaction_ref: str = "",
    ) -> None:
        """Record a Provider compaction as the new head of its context.

        ``retain_from`` keeps the context members recorded at or after that
        time after the compaction; Providers such as Claude Code keep recent
        messages verbatim.  ``provider_compaction_ref`` names the Provider's own
        compaction so that observing it again does not record it twice.
        """
        with self._lock:
            projection = self._active.get(execution_id)
        if projection is None:
            return
        options = {
            "retain_from": str(retain_from or ""),
            "provider_compaction_ref": str(provider_compaction_ref or ""),
        }
        canonical_content = json_safe(dict(content))
        if not isinstance(canonical_content, dict) or not canonical_content:
            raise ValueError("Compaction content is required")
        replacement = canonical_content.get("replacement_history")
        if replacement is not None and not isinstance(replacement, list):
            raise ValueError("Compaction replacement_history must be an array")
        observed_at = str(created_at or utc_now())
        if (
            projection.request.operation in {"turn", "generate"}
            and not projection.provider_input_observed
        ):
            projection.pending_compactions.append(
                (canonical_content, observed_at, options)
            )
            return
        resolved_session_id = str(
            backend_session_id
            or projection.backend_session_id
            or projection.session.backend_session_id
            or ""
        )
        if not resolved_session_id:
            raise RuntimeError("Compaction Provider Session identity is missing")
        self.sessions.bind_provider_session(
            projection,
            provider=provider or projection.request.provider,
            backend_session_id=resolved_session_id,
            backend_route_id=(
                backend_route_id
                or projection.backend_route_id
                or projection.session.backend_route_id
            ),
        )
        self.sessions.persist_compaction(
            projection,
            content=canonical_content,
            created_at=observed_at,
            **options,
        )

    def observe(
        self,
        *,
        provider: str,
        backend_route_id: str,
        backend_session_id: str,
        execution_id: str,
        event: Mapping[str, Any],
    ) -> None:
        with self._lock:
            projection = self._active.get(execution_id)
        if projection is None:
            return
        if event.get("method") == "subagent/event":
            self._observe_subagent_event(projection, provider=provider, event=event)
            return
        operation = projection.request.operation
        if operation not in {"turn", "generate", "compact"}:
            return
        resolved_backend_session_id = (
            backend_session_id or projection.session.backend_session_id
        )
        if (
            not resolved_backend_session_id
            and projection.request.session.mode == "stateless"
        ):
            resolved_backend_session_id = projection.request.execution_id
        if resolved_backend_session_id:
            self.sessions.bind_provider_session(
                projection,
                provider=provider or projection.request.provider,
                backend_session_id=resolved_backend_session_id,
                backend_route_id=backend_route_id or projection.session.backend_route_id,
            )
        turn_id = event_turn_id(event) or projection.turn_id
        if turn_id:
            projection.turn_id = turn_id
        created_at = event_created_at(event)
        method = str(event.get("method") or "")
        event_type = str(event.get("type") or "")
        params = event.get("params") if isinstance(event.get("params"), Mapping) else {}
        payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
        item = params.get("item") if isinstance(params.get("item"), Mapping) else {}
        if not item and isinstance(event.get("item"), Mapping):
            item = event["item"]

        if operation == "compact":
            if method == "thread/compacted" or event_type == "compacted":
                replacement = (
                    payload.get("replacement_history")
                    if isinstance(payload, Mapping)
                    else None
                )
                if isinstance(replacement, list):
                    self.sessions.persist_compaction(
                        projection,
                        content={"replacement_history": list(replacement)},
                        created_at=created_at,
                    )
            return

        if method == "portal/provider_request_accepted":
            self.output.record_provider_request(projection, execution_id=execution_id, provider=provider,
                                                params=params, turn_id=turn_id, created_at=created_at)
            return

        if method == SUBAGENT_PROGRESS_METHOD:
            # A running subagent's card is live activity only; its steps are in its own trace.
            return
        if item.get("type") in {"userInputRequest", "userInputResponse", "turnArtifacts", "subagentRun"}:
            self.output.record_native_item(projection, item, execution_id=execution_id, provider=provider,
                                           created_at=created_at)
            return

        self.output.observe_output(projection, event, created_at, turn_id)

        if method == "thread/compacted" or event_type == "compacted":
            replacement = (
                payload.get("replacement_history")
                if isinstance(payload, Mapping)
                else None
            )
            if isinstance(replacement, list):
                self.sessions.persist_compaction(
                    projection,
                    content={"replacement_history": list(replacement)},
                    created_at=created_at,
                )

    def _observe_subagent_event(
        self, projection: ExecutionProjection, *, provider: str, event: Mapping[str, Any],
    ) -> None:
        """Record a subagent's event in its own trace, under the parent the event establishes."""
        params = event.get("params") or {}
        key = str(params.get("traceKey") or params.get("parentToolUseId") or "")
        child_event = params.get("event")
        if not key or not isinstance(child_event, Mapping):
            return
        # Agent ownership is independent of sequence storage. Adapters that
        # know a full ancestry supply it even if a parent has not emitted yet.
        parent_id = None
        ancestors = params.get("ancestorTraceKeys")
        if isinstance(ancestors, list):
            # An explicit empty chain proves that the root is the parent.
            parent_id = projection.agent_id
            for ancestor in ancestors:
                ancestor_id = self.store.subagent_id(projection.agent_id, str(ancestor))
                self.store.ensure_agent(ancestor_id, sequence_kind="subagent_trace",
                                        parent_agent_id=parent_id, actor_ref=provider)
                parent_id = ancestor_id
        elif params.get("parentTraceKey"):
            # A direct edge does not establish the parent's own ancestry.
            parent_id = self.store.subagent_id(projection.agent_id, str(params["parentTraceKey"]))
            if not self.store.agent(parent_id):
                self.store.ensure_agent(parent_id, sequence_kind="subagent_trace", actor_ref=provider)
        else:
            call = str(params.get("parentToolUseId") or "")
            if call and call in projection.tools:
                parent_id = projection.agent_id
            else:
                for candidate in projection.children.values():
                    # A fork may echo its spawning call in its own trace.
                    # That copy cannot prove self-parentage or override a
                    # call observed in the root's own stream.
                    if candidate.trace_key != key and call and call in candidate.tools:
                        parent_id = self.store.subagent_id(projection.agent_id, candidate.trace_key)
                        break
        child_id = self.store.subagent_id(projection.agent_id, key)
        # Missing evidence on reconnect cannot erase an established edge.
        existing = self.store.agent(child_id)
        if existing and parent_id is None:
            parent_id = existing["parent_agent_id"]
        self.store.ensure_agent(child_id, sequence_kind="subagent_trace",
                                parent_agent_id=parent_id, actor_ref=provider)
        child = projection.children.get(key)
        if child is None:
            child = replace(
                projection, trace_key=key,
                subagent={"parent_tool_use_id": str(params.get("parentToolUseId") or key),
                          "subagent_id": str(params.get("agentId") or "")},
                children={}, tools={}, persisted_segments=set(), final_segment_id="",
            )
            projection.children[key] = child
        self.output.observe_output(child, child_event, event_created_at(event), event_turn_id(child_event) or projection.turn_id)

    def complete(
        self,
        *,
        execution_id: str,
        backend_session_id: str,
        backend_route_id: str,
        turn_id: str,
        response: str,
        usage: Mapping[str, Any] | None = None,
    ) -> str:
        with self._lock:
            projection = self._active.get(execution_id)
        if projection is None:
            return ""
        projection.turn_id = str(turn_id or projection.turn_id)
        operation = projection.request.operation
        if operation in {"turn", "generate", "create", "compact"}:
            resolved_backend_session_id = (
                backend_session_id or projection.backend_session_id
            )
            if (
                not resolved_backend_session_id
                and projection.request.session.mode == "stateless"
            ):
                resolved_backend_session_id = projection.request.execution_id
            if not resolved_backend_session_id:
                raise RuntimeError(
                    "Provider Session identity is missing at execution completion"
                )
            self.sessions.bind_provider_session(
                projection,
                provider=projection.request.provider,
                backend_session_id=resolved_backend_session_id,
                backend_route_id=backend_route_id or projection.backend_route_id,
            )
        elif operation == "fork":
            source_backend_session_id = projection.session.backend_session_id
            self.sessions.bind_provider_session(
                projection,
                provider=projection.request.provider,
                backend_session_id=backend_session_id,
                backend_route_id=backend_route_id or projection.backend_route_id,
                source_backend_session_id=source_backend_session_id,
                source_backend_route_id=projection.session.backend_route_id,
            )
        if operation == "compact" and not projection.compaction_segment_id:
            raise RuntimeError("Compact execution produced no canonical Segment")
        if operation not in {"turn", "generate"}:
            self.store.record_execution_output(
                execution_id,
                usage=usage,
                provider_turn_id=projection.turn_id,
            )
            return projection.compaction_segment_id
        clean_response = str(response or "").strip()
        if clean_response and not projection.final_segment_id:
            projection.final_segment_id = self.output.persist_model_message(
                projection,
                text=clean_response,
                phase="final",
                item_id="final",
                created_at=utc_now(),
                turn_id=projection.turn_id,
            )
        self.store.record_execution_output(
            execution_id,
            usage=usage,
            provider_turn_id=projection.turn_id,
        )
        return projection.final_segment_id

    def discard(self, execution_id: str) -> None:
        with self._lock:
            self._active.pop(str(execution_id or ""), None)
