from __future__ import annotations

import hashlib
import json
import threading
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from management_common.provider_subagent_runs import PROGRESS_METHOD as SUBAGENT_PROGRESS_METHOD

from .agent_data_store import (
    HARNESS_CONTROL_KIND,
    AgentDataStore,
    get_agent_data_store,
    stable_segment_id,
    utc_now,
)
from .contracts import ExecutionRequest
from .control_messages import HARNESS_CONTROL_EVENT
from .recovery_messages import RECOVERY_INPUT_EVENT, recovery_input_message
from .agent_data_input import input_message
from .provider_reasoning import ProviderReasoning, provider_reasoning
from .session_manager import RuntimeSession


def _input_message_id(request: ExecutionRequest) -> str:
    # Resident API requests identify their input in metadata; native sessions
    # also pass it as a client option for the provider transport. Both refer
    # to the same Portal input occurrence, independently of its current text.
    return str(request.metadata.get("user_message_id")
               or request.runtime_options.get("client_user_message_id") or "").strip()


_TOOL_TYPES = frozenset({
    "commandExecution",
    "fileChange",
    "mcpToolCall",
    "function_call",
    "custom_tool_call",
    "function_call_output",
    "toolCall",
    "toolUse",
    "dynamicToolCall",
    "webSearch",
    "imageView",
    "imageGeneration",
    "collabAgentToolCall",
})
_TOOL_NAMES = {
    "commandExecution": "command",
    "fileChange": "file_change",
    "mcpToolCall": "mcp_tool",
    "function_call": "function_call",
    "custom_tool_call": "custom_tool_call",
    "function_call_output": "function_call",
    "toolCall": "tool_call",
    "toolUse": "tool_use",
    "dynamicToolCall": "dynamic_tool",
    "webSearch": "web_search",
    "imageView": "image_view",
    "imageGeneration": "image_generation",
    "collabAgentToolCall": "collaboration_tool",
}
_TOOL_INPUT_FIELDS = (
    "command",
    "cwd",
    "arguments",
    "input",
    "parameters",
    "patch",
    "diff",
    "changes",
    "path",
    "query",
    "action",
    "prompt",
    "revisedPrompt",
    "url",
)
_TOOL_RESULT_FIELDS = (
    "aggregatedOutput",
    "output",
    "result",
    "stdout",
    "stderr",
    "content",
    "text",
    "message",
    "contentItems",
    "imageUrl",
)


def _json_safe(value: object) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    return str(value)


def _text(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, list):
        return ""
    return "\n\n".join(
        str(
            part.get("text")
            or part.get("input_text")
            or part.get("output_text")
            or ""
        )
        for part in value
        if isinstance(part, Mapping)
    ).strip()


def _timestamp(value: object) -> str:
    try:
        if value in (None, "") or isinstance(value, bool):
            return ""
        return datetime.fromtimestamp(
            int(value) / 1000,
            timezone.utc,
        ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    except (TypeError, ValueError, OSError):
        return ""


def _created_at(event: Mapping[str, Any]) -> str:
    direct = str(event.get("timestamp") or "").strip()
    if direct:
        return direct
    params = event.get("params") if isinstance(event.get("params"), Mapping) else {}
    return (
        _timestamp(
            params.get("completedAtMs")
            or params.get("updatedAtMs")
            or params.get("startedAtMs")
        )
        or utc_now()
    )


def _model_timing(value: object, completed_at: str) -> dict[str, str]:
    """Only adapter-observed request boundaries count as model dispatch times.

    Turn starts, message creation, first-token events and step starts have
    different meanings and must not be substituted for a missing dispatch.
    """
    from .request_timing import observed_timing
    timing = observed_timing(dict(value) if isinstance(value, Mapping) else {})
    start = _utc_moment(timing.get("started_at"))
    finish = _utc_moment(timing.get("finished_at")) or _utc_moment(completed_at)
    if start and finish and start > finish:
        start = None
    return {"started_at": start.isoformat() if start else "",
            "finished_at": finish.isoformat() if finish else ""}


def _turn_id(event: Mapping[str, Any]) -> str:
    params = event.get("params") if isinstance(event.get("params"), Mapping) else {}
    turn = params.get("turn") if isinstance(params.get("turn"), Mapping) else {}
    payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
    return str(
        turn.get("id")
        or params.get("turnId")
        or event.get("turn_id")
        or event.get("turnId")
        or payload.get("turn_id")
        or payload.get("turnId")
        or ""
    ).strip()


def _tool_status(event: Mapping[str, Any], tool: Mapping[str, Any]) -> str:
    raw = str(tool.get("status") or "").strip().casefold()
    method = str(event.get("method") or "").strip().casefold()
    event_type = str(event.get("type") or "").strip().casefold()
    tool_type = str(tool.get("type") or "").strip()
    if raw in {"completed", "complete", "success", "succeeded", "done"}:
        return "completed"
    if raw in {"failed", "failure", "error", "errored"}:
        return "failed"
    if raw in {"cancelled", "canceled"}:
        return "cancelled"
    if method.endswith("/completed") or event_type.endswith(".completed"):
        return "failed" if tool.get("error") or tool.get("errors") else "completed"
    if tool_type == "function_call_output":
        return "failed" if tool.get("error") else "completed"
    if method.endswith("/started") or event_type.endswith(".started"):
        return "running"
    if event_type == "response_item" and tool_type in {
        "function_call",
        "custom_tool_call",
    }:
        return "running"
    return raw or "observed"


def _extract(mapping: Mapping[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {
        field: _json_safe(mapping[field])
        for field in fields
        if field in mapping and mapping[field] is not None
    }


def _utc_moment(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00").replace(" ", "T", 1))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)




def live_tool_segment_id(execution_id: str, call_id: str) -> str:
    """The tool as the Worker saw it in the turn's events."""

    return stable_segment_id("provider-tool", execution_id, call_id)


def persist_provider_compaction(
    store: AgentDataStore,
    *,
    provider_session_ref: str,
    agent_id: str,
    operation_id: str,
    actor_ref: str,
    provider: str,
    content: Mapping[str, Any],
    created_at: str,
    retain_from: str = "",
    provider_compaction_ref: str = "",
    skip_segment_ids: set[str] | frozenset[str] = frozenset(),
    kept_execution_id: str = "",
) -> tuple[str, bool]:
    """Record a Provider compaction where it happened in the Agent trace.

    A session's current context is read from the trace: its transcript from
    its last compaction on (``AgentDataStore.session_context_ids``).  Without
    ``retain_from`` the compaction replaces everything the session held, as
    Codex's replacement history already contains the kept recent items, and
    it goes at the end of the trace.  With it, the members recorded at or
    after that time (the messages the Provider kept verbatim, and later work)
    stay after the compaction: it goes before the first of them.  What
    ``kept_execution_id`` recorded stays after it too (a compaction before
    the Provider took that turn's input).  A compaction already recorded
    under ``provider_compaction_ref`` is not recorded again.  Returns the
    compaction Segment ID and whether this call created it.
    """

    canonical_content = _json_safe(dict(content))
    if provider_compaction_ref and provider_session_ref:
        recorded = store.session_compaction(provider_session_ref, provider_compaction_ref, agent_id=agent_id)
        if recorded:
            return recorded, False
    segment_id = stable_segment_id(
        "provider-compaction",
        operation_id,
        hashlib.sha256(
            json.dumps(
                canonical_content,
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest(),
    )
    if segment_id in skip_segment_ids:
        return segment_id, False
    members = (
        store.segment_members(store.session_context_ids(provider_session_ref, agent_id=agent_id))
        if provider_session_ref
        else []
    )
    retain_after = _utc_moment(retain_from) if retain_from else None
    retained_ids = [
        item["segment_id"]
        for item in members
        if item["segment_type"] != "compaction" and (
            (kept_execution_id and item["execution_id"] == kept_execution_id)
            or (
                retain_after is not None
                and (_utc_moment(item.get("finished_at") or item.get("started_at")) or retain_after) >= retain_after
            )
        )
    ]
    retained = set(retained_ids)
    metadata: dict[str, Any] = {
        "source": "provider_compaction",
        "kind": "compaction",
    }
    if provider_compaction_ref:
        metadata["provider_compaction_ref"] = provider_compaction_ref
    store.put_segment(
        segment_id=segment_id,
        segment_type="compaction",
        content=canonical_content,
        content_type="application/json",
        started_at=created_at,
        finished_at=created_at,
        status="completed",
        created_by_actor_ref=actor_ref,
        performed_by_actor_ref=provider,
        metadata=metadata,
        created_by_agent_id=agent_id,
        execution_id=operation_id,
    )
    store.add_relations([
        {
            "relation_type": "compressed_from",
            "source_segment_id": item["segment_id"],
            "target_segment_id": segment_id,
            "operation_id": operation_id,
            "operation_type": "provider.compact",
            "operation_version": "1",
            "actor_ref": provider,
        }
        for item in members
        if item["segment_id"] not in retained
    ])
    store.append_agent_segments(agent_id, [segment_id], actor_ref=provider)
    if retained:
        store.place_in_trace_before(agent_id, segment_id, retained, actor_ref=provider)
    return segment_id, True


@dataclass
class _Projection:
    request: ExecutionRequest
    session: RuntimeSession
    agent_id: str
    input_segment_id: str
    effective_input_segment_id: str
    system_segment_ids: list[str] = field(default_factory=list)
    provider_session_ref: str = ""
    provider: str = ""
    backend_session_id: str = ""
    backend_route_id: str = ""
    turn_id: str = ""
    final_segment_id: str = ""
    compaction_segment_id: str = ""
    persisted_segments: set[str] = field(default_factory=set)
    pending_compactions: list[tuple[dict[str, Any], str, dict[str, str]]] = field(
        default_factory=list
    )
    provider_input_observed: bool = False
    tools: dict[str, dict[str, Any]] = field(default_factory=dict)
    trace_key: str = ""
    subagent: dict[str, str] = field(default_factory=dict)
    children: dict[str, _Projection] = field(default_factory=dict)


class AgentDataProjector:
    """Turns stable Provider behavior into canonical Segments.

    Streaming deltas and progress stay in memory.  Completed messages, terminal
    tool calls, effective Provider inputs, compactions, and execution summaries are
    committed to the Agent data store.
    """

    def __init__(self, store: AgentDataStore | None = None) -> None:
        self.store = store or get_agent_data_store()
        self._active: dict[str, _Projection] = {}
        self._lock = threading.RLock()

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
        projection = _Projection(
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
            metadata = {
                "role": "user",
                "source": "human_prompt",
                "kind": "provider_input",
                "title": request.actor or "User message",
                "author": request.actor,
            }
            with self.store.database.read() as connection:
                current = connection.execute(
                    "SELECT segment.segment_id FROM agents agent JOIN sequence_segments member ON member.sequence_id=agent.trace_sequence_id "
                    "JOIN segments segment USING(segment_id) WHERE agent.agent_id=%s AND segment.execution_id=%s "
                    "AND segment.segment_type='input' AND segment.metadata_json::jsonb->>'kind' IN ('provider_input','effective_provider_input') "
                    "ORDER BY member.ordinal LIMIT 1", (agent_id, request.execution_id),
                ).fetchone()
            if current:
                projection.effective_input_segment_id = str(current["segment_id"])
                projection.persisted_segments.add(projection.effective_input_segment_id)
                return
            content = (request.metadata.get("fork_context_input") or {}).get("prompt", request.prompt)
            portal_message_id = _input_message_id(request)
            source = self.store.chat_message(agent_id, portal_message_id) if portal_message_id else None
            if source:
                self.store.transform_agent_segments(
                    agent_id, [source["segment_id"]], segment_id=input_segment_id,
                    content=content, metadata=metadata, actor_ref=request.actor,
                    operation_id=request.execution_id, operation_type="provider_input.compose",
                    execution_id=request.execution_id, created_at=accepted_at,
                )
            else:
                self.store.put_segment(
                    segment_id=input_segment_id, segment_type="input", content=content,
                    started_at=accepted_at, finished_at=accepted_at, status="completed",
                    created_by_actor_ref=request.actor, performed_by_actor_ref=request.actor,
                    metadata=metadata, created_by_agent_id=agent_id, execution_id=request.execution_id,
                )
                self.store.append_agent_segments(agent_id, [input_segment_id], actor_ref=request.actor)
            projection.persisted_segments.add(input_segment_id)
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
        user_prompt = str(getattr(invocation, "user_prompt", "") or "")
        prepared_request = getattr(invocation, "request", projection.request)
        input_media = prepared_request.input_media
        if user_prompt and (user_prompt != projection.request.prompt or input_media):
            context_input = projection.request.metadata.get("fork_context_input")
            if context_input:
                # Harness features may wrap the wire input, but the inherited
                # prefix already has canonical Segment identities. Transform
                # only this occurrence of the request into its new-input part.
                # Unknown/lossy transforms must not silently duplicate history.
                if user_prompt.count(projection.request.prompt) != 1:
                    raise ValueError("Fork input transformation lost its canonical PostgreSQL context boundary")
                user_prompt = user_prompt.replace(
                    projection.request.prompt, context_input["prompt"], 1,
                )
            content = (
                input_message(
                    user_prompt, input_media,
                    detail=str(prepared_request.runtime_options.get("input_image_detail") or ""),
                )
                if input_media else user_prompt
            )
            metadata = {
                "role": "user",
                "source": "harness_output",
                "kind": "effective_provider_input",
            }
            content_type = "application/json" if input_media else "text/plain; charset=utf-8"
            target_id = stable_segment_id(
                "harness-preparation", execution_id,
                json.dumps(
                    {"content": content, "content_type": content_type, "metadata": metadata},
                    ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                ),
            )
            if target_id not in projection.persisted_segments:
                at = utc_now()
                self.store.transform_agent_segments(
                    projection.agent_id, [projection.effective_input_segment_id],
                    segment_id=target_id, segment_type="input", content=content,
                    content_type=content_type, metadata=metadata,
                    actor_ref="agent_harness", operation_id=execution_id,
                    operation_type="agent_harness.prepare_invocation",
                    execution_id=execution_id, created_at=at,
                )
                projection.persisted_segments.add(target_id)
            projection.effective_input_segment_id = target_id
        system_context = str(getattr(invocation, "system_context", "") or "")
        attach_system = bool(getattr(invocation, "attach_system_context", False))
        if attach_system and system_context:
            system_id = stable_segment_id(
                "system-context",
                execution_id,
                hashlib.sha256(system_context.encode("utf-8")).hexdigest(),
            )
            if system_id not in projection.persisted_segments:
                at = utc_now()
                self.store.put_segment(
                    segment_id=system_id,
                    segment_type="input",
                    content=system_context,
                    started_at=at,
                    finished_at=at,
                    status="completed",
                    created_by_actor_ref="portal",
                    performed_by_actor_ref="portal",
                    metadata={
                        "role": "system",
                        "source": "system_context",
                        "kind": "system_instructions",
                    },
                    created_by_agent_id=projection.agent_id,
                    execution_id=execution_id,
                )
                self.store.append_agent_segments(projection.agent_id, [system_id], actor_ref='portal')
                projection.system_segment_ids.append(system_id)
                projection.persisted_segments.add(system_id)
        projection.provider_input_observed = True
        initial_backend_session_id = projection.session.backend_session_id
        if (
            not initial_backend_session_id
            and projection.request.session.mode == "stateless"
        ):
            initial_backend_session_id = projection.request.execution_id
        if initial_backend_session_id and not projection.request.metadata.get("fork_context_input"):
            self._bind_provider_session(
                projection,
                provider=projection.request.provider,
                backend_session_id=initial_backend_session_id,
                backend_route_id=projection.session.backend_route_id,
            )

    def _bind_provider_session(
        self,
        projection: _Projection,
        *,
        provider: str,
        backend_session_id: str,
        backend_route_id: str,
        source_backend_session_id: str = "",
        source_backend_route_id: str = "",
    ) -> None:
        clean_session = str(backend_session_id or "").strip()
        if projection.request.session.mode == "stateless":
            clean_session = projection.request.execution_id
        if not clean_session:
            return
        if projection.provider_session_ref:
            if (
                projection.backend_session_id
                and projection.backend_session_id != clean_session
            ):
                raise RuntimeError("Provider Session changed during one execution")
            return
        session = self.store.ensure_provider_session(
            provider=provider,
            backend_route_id=backend_route_id,
            backend_session_id=clean_session,
            agent_id=projection.agent_id,
            actor_ref=projection.request.actor,
            source_backend_session_id=source_backend_session_id,
            source_backend_route_id=source_backend_route_id,
            # A native fork for a Resident (a restart, a Fork) runs under its
            # binding's name; the inherited context is marked in the
            # Resident's own trace.
            context_agent_id=str(projection.request.metadata.get("resident_id") or ""),

        )
        projection.provider_session_ref = str(session["provider_session_ref"])
        projection.provider = provider
        projection.backend_session_id = clean_session
        projection.backend_route_id = backend_route_id
        self.store.bind_execution_provider_session(
            projection.request.execution_id,
            provider_session_ref=projection.provider_session_ref,
        )
        operation = projection.request.operation
        context_input = projection.request.metadata.get("fork_context_input") or {}
        if operation == "compact" and context_input:
            references = context_input["segments"]
            segments = self.store.fork_context_segments(references)
            self._mark_context_received(projection, [item["segment_id"] for item in segments])
        if operation not in {"turn", "generate"}:
            return
        if projection.pending_compactions:
            pending = list(projection.pending_compactions)
            projection.pending_compactions.clear()
            for content, created_at, options in pending:
                # Compacted before the Provider took this turn's input: the
                # turn's records stay after the compaction in the trace.
                self._persist_compaction(
                    projection,
                    content=content,
                    created_at=created_at,
                    kept_execution_id=projection.request.execution_id,
                    **options,
                )
        if context_input:
            # The inherited prefix already belongs to the Agent trace; the
            # mark puts it in the new session's context, before this turn's
            # input.  The new user Segment holds only the new input.
            references = context_input["segments"]
            segments = self.store.fork_context_segments(references)
            self._mark_context_received(
                projection, [item["segment_id"] for item in segments],
            )

    def _mark_context_received(
        self, projection: _Projection, segment_ids: list[str],
    ) -> None:
        """Mark in the trace where the session received a Fork's inherited context.

        The inherited Segments belong to the source's history; this mark is
        what puts them in the session's transcript, so that a Fork from a
        later turn of this session inherits them too.  A turn's inherited
        context comes before its input.
        """

        self.store.record_context_received(
            projection.agent_id,
            projection.provider_session_ref,
            segment_ids,
            actor_ref=projection.request.actor,
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
            self._bind_provider_session(
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
            # A Harness reminder the Provider took into its running turn.
            text = str(details.get("text") or "").strip()
            if not text:
                return
            # A governor that took the turn over after a handover numbers its
            # checkpoints from 1 again: the same control id with other text is another reminder.
            segment_id = stable_segment_id(
                "harness-control", execution_id, str(details.get("control_id") or ""),
                hashlib.sha256(text.encode("utf-8")).hexdigest(),
            )
            if segment_id in projection.persisted_segments:
                return
            at = utc_now()
            self.store.put_segment(
                segment_id=segment_id, segment_type="harness", content=text,
                started_at=at, finished_at=at, status="completed",
                created_by_actor_ref="agent_harness", performed_by_actor_ref="agent_harness",
                metadata={
                    "role": "user", "source": "agent_harness", "kind": HARNESS_CONTROL_KIND,
                    "control_id": str(details.get("control_id") or ""),
                    "reason": str(details.get("reason") or ""),
                    "control_kind": str(details.get("control_kind") or ""),
                },
                created_by_agent_id=projection.agent_id, execution_id=execution_id,
            )
            self.store.append_agent_segments(projection.agent_id, [segment_id], actor_ref='agent_harness')
            projection.persisted_segments.add(segment_id)
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
                self._persist_model_message(
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
        canonical_content = _json_safe(dict(content))
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
        self._bind_provider_session(
            projection,
            provider=provider or projection.request.provider,
            backend_session_id=resolved_session_id,
            backend_route_id=(
                backend_route_id
                or projection.backend_route_id
                or projection.session.backend_route_id
            ),
        )
        self._persist_compaction(
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
                for candidate in projection.children.values():
                    if call and call in candidate.tools:
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
            self._observe_output(child, child_event, _created_at(event), _turn_id(child_event) or projection.turn_id)
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
            self._bind_provider_session(
                projection,
                provider=provider or projection.request.provider,
                backend_session_id=resolved_backend_session_id,
                backend_route_id=backend_route_id or projection.session.backend_route_id,
            )
        turn_id = _turn_id(event) or projection.turn_id
        if turn_id:
            projection.turn_id = turn_id
        created_at = _created_at(event)
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
                    self._persist_compaction(
                        projection,
                        content={"replacement_history": list(replacement)},
                        created_at=created_at,
                    )
            return

        if method == SUBAGENT_PROGRESS_METHOD:
            # A running subagent's card is live activity only; its steps are in its own trace.
            return
        if item.get("type") in {"userInputRequest", "userInputResponse", "turnArtifacts", "subagentRun"}:
            from management_common.provider_user_input import content_part

            if item.get("type") == "turnArtifacts":
                from management_common.provider_turn_artifacts import content_part
            if item.get("type") == "subagentRun":
                from management_common.provider_subagent_runs import content_part

            part = content_part(dict(item), execution_id)
            segment_id = part["segment_id"]
            operational = part["type"] in {"native_turn", "subagent_run"}
            try:
                existing = (self.store.operation_record if operational else self.store.segment)(segment_id)
            except KeyError:
                existing = None
            # A run's card is read again by a Worker that takes the turn
            # over; the first reading stands.
            if existing is not None and existing["content"] != part and part["type"] != "subagent_run":
                raise ValueError("native user input Segment changed during replay")
            if existing is None:
                (self.store.put_operation_record if operational else self.store.put_segment)(
                    segment_id=segment_id,
                    segment_type={"native_turn": "model", "subagent_run": "artifact"}.get(part["type"], "tool"),
                    content=part,
                    content_type="application/json",
                    **({"created_at": created_at} if operational else {"started_at": created_at, "finished_at": created_at}),
                    status="completed", created_by_agent_id=projection.agent_id,
                    created_by_actor_ref=projection.request.identity.principal.id,
                    performed_by_actor_ref=projection.request.identity.principal.id,
                    execution_id=execution_id,
                    metadata={"tool_name": part.get("tool_name", ""), "kind": part["type"],
                              "identity": projection.request.identity.to_dict()},
                )
            if not operational:
                self.store.append_agent_segments(projection.agent_id, [segment_id], actor_ref=provider)
            return

        self._observe_output(projection, event, created_at, turn_id)

        if method == "thread/compacted" or event_type == "compacted":
            replacement = (
                payload.get("replacement_history")
                if isinstance(payload, Mapping)
                else None
            )
            if isinstance(replacement, list):
                self._persist_compaction(
                    projection,
                    content={"replacement_history": list(replacement)},
                    created_at=created_at,
                )

    def _observe_output(
        self, projection: _Projection, event: Mapping[str, Any], created_at: str, turn_id: str,
    ) -> None:
        method = str(event.get("method") or "")
        event_type = str(event.get("type") or "")
        params = event.get("params") if isinstance(event.get("params"), Mapping) else {}
        payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
        item = params.get("item") if isinstance(params.get("item"), Mapping) else {}
        if not item and isinstance(event.get("item"), Mapping):
            item = event["item"]
        if method == "item/completed" and item.get("type") in {"providerInput", "userMessage"}:
            if (projection.trace_key or item.get("type") == "providerInput") and item.get("id"):
                content = item.get("content", item.get("text", ""))
                if content:
                    segment_id = stable_segment_id("provider-input", self._output_scope(projection), item["id"])
                    self._put_output_segment(
                        projection, segment_id=segment_id,
                        segment_type="input", content=content,
                        content_type="application/json" if not isinstance(content, str) else "text/plain",
                        started_at=created_at, created_by_actor_ref=projection.request.identity.principal.id,
                        performed_by_actor_ref=projection.request.identity.principal.id,
                        created_by_agent_id=projection.agent_id, execution_id=projection.request.execution_id,
                        metadata={"role": item.get("role") or "user", "source": f"{projection.request.provider}_input"},
                    )
                    self._append_output(projection, segment_id, projection.request.provider)
            return
        reasoning = provider_reasoning(event)
        request_timing = params.get("request_timing") or event.get("request_timing")
        if reasoning is not None:
            self._persist_reasoning(projection, reasoning, created_at, turn_id, request_timing)
            return

        text = ""
        phase = ""
        item_id = ""
        if method == "item/completed" and str(item.get("type") or "") == "agentMessage":
            text = str(item.get("text") or item.get("message") or "").strip()
            phase = str(item.get("phase") or "message").strip()
            item_id = str(item.get("id") or "").strip()
        elif (
            event_type == "response_item"
            and str(payload.get("type") or "") == "message"
            and str(payload.get("role") or "") == "assistant"
        ):
            text = _text(payload.get("content"))
            phase = str(payload.get("phase") or "message").strip()
            item_id = str(payload.get("id") or "").strip()
        if text:
            self._persist_model_message(
                projection,
                text=text,
                phase=phase,
                item_id=item_id,
                created_at=created_at,
                turn_id=turn_id,
                request_timing=request_timing,
            )

        tool = item if item else payload
        tool_type = str(tool.get("type") or "")
        if tool_type in _TOOL_TYPES:
            self._observe_tool(
                projection,
                event=event,
                tool=tool,
                tool_type=tool_type,
                created_at=created_at,
                turn_id=turn_id,
            )

    def _put_output_segment(
        self,
        projection: _Projection,
        *,
        metadata: Mapping[str, Any],
        **fields: Any,
    ) -> str:
        if projection.trace_key:
            metadata = {**metadata, "subagent": projection.subagent}
        return self.store.put_segment(metadata=metadata, **fields)

    @staticmethod
    def _output_scope(projection: _Projection) -> str:
        execution_id = projection.request.execution_id
        return f"{execution_id}/{projection.trace_key}" if projection.trace_key else execution_id

    def _append_output(self, projection: _Projection, segment_id: str, actor_ref: str) -> None:
        self.store.append_agent_segments(
            self.store.subagent_id(projection.agent_id, projection.trace_key)
            if projection.trace_key else projection.agent_id,
            [segment_id], actor_ref=actor_ref,
        )

    def _persist_reasoning(
        self,
        projection: _Projection,
        reasoning: ProviderReasoning,
        created_at: str,
        turn_id: str,
        request_timing: object = None,
    ) -> str:
        segment_id = stable_segment_id(
            "provider-reasoning",
            self._output_scope(projection),
            turn_id,
            reasoning.item_id or uuid.uuid4().hex,
        )
        self._put_output_segment(
            projection,
            segment_id=segment_id,
            segment_type="model",
            content=reasoning.content,
            content_type="application/json",
            **_model_timing(request_timing, created_at),
            status="completed",
            created_by_actor_ref=projection.request.identity.principal.id,
            performed_by_actor_ref=projection.request.identity.principal.id,
            metadata={
                "role": "assistant",
                "source": f"{projection.request.provider}_output",
                "kind": "reasoning",
                "format": reasoning.formats[0],
                "formats": list(reasoning.formats),
                "title": "Agent reasoning",
                "author": projection.request.identity.principal.id,
                "identity": projection.request.identity.to_dict(),
            },
            created_by_agent_id=projection.agent_id,
            execution_id=projection.request.execution_id,
        )
        self._append_output(projection, segment_id, projection.request.provider)
        projection.persisted_segments.add(segment_id)
        return segment_id

    def _persist_model_message(
        self,
        projection: _Projection,
        *,
        text: str,
        phase: str,
        item_id: str,
        created_at: str,
        turn_id: str,
        request_timing: object = None,
    ) -> str:
        normalized_phase = str(phase or "message").strip().casefold()
        segment_id = stable_segment_id(
            "provider-model",
            self._output_scope(projection),
            item_id or uuid.uuid4().hex,
            normalized_phase,
        )
        from .message_projection import model_message_id
        is_final = normalized_phase in {"final", "final_answer"}
        try:
            recorded = self.store.segment(segment_id)
        except KeyError:
            recorded = None
        if recorded is not None:
            # Preserve the original immutable message's timestamp spelling.
            # SQL TIMESTAMPTZ normalizes offsets/precision when read; using
            # that projection here would change otherwise identical metadata.
            created_at = recorded["metadata"]["message"]["created_at"]
        self._put_output_segment(
            projection,
            segment_id=segment_id,
            segment_type="model",
            content=text,
            **_model_timing(request_timing, created_at),
            status="completed",
            created_by_actor_ref=projection.request.identity.principal.id,
            performed_by_actor_ref=projection.request.identity.principal.id,
            metadata={
                "role": "assistant",
                "message": {
                    "message_id": model_message_id(projection.request.execution_id, item_id, normalized_phase),
                    "role": "agent", "author_id": projection.request.identity.principal.id,
                    "created_at": created_at, "activated_at": created_at,
                    "execution_id": projection.request.execution_id,
                    "provider_turn_id": turn_id,
                    "reply_to_message_id": _input_message_id(projection.request),
                    "parts": [{"type": "text"}],
                },
                "source": (
                    f"{projection.request.provider}_output"
                ),
                "kind": "final_answer" if is_final else normalized_phase,
                "title": "Agent answer" if is_final else "Agent update",
                "author": projection.request.identity.principal.id,
                "identity": projection.request.identity.to_dict(),
            },
            created_by_agent_id=projection.agent_id,
            execution_id=projection.request.execution_id,
        )
        # Resident Chat has one delivery path: its ordered Execution event
        # consumer appends this captured Segment and publishes the Conversation
        # event before processing the next visible message.
        if not projection.agent_id.startswith("resident_") or projection.trace_key:
            self._append_output(projection, segment_id, projection.request.provider)
        projection.persisted_segments.add(segment_id)
        # A provider turn must have one canonical answer.  Runtime steering or
        # transport replay can still surface a second final_answer item before
        # turn/completed.  Capture every item, but only the selected visible
        # answer enters the Resident trace through the Conversation consumer.
        if is_final and not projection.final_segment_id:
            projection.final_segment_id = segment_id
        return segment_id

    def _observe_tool(
        self,
        projection: _Projection,
        *,
        event: Mapping[str, Any],
        tool: Mapping[str, Any],
        tool_type: str,
        created_at: str,
        turn_id: str,
    ) -> None:
        if str(tool.get("status") or "") == "unknown":
            # A polling snapshot can lose a tool without reporting its
            # outcome. Keep that as activity, not a fabricated success.
            return
        call_id = str(
            tool.get("id")
            or tool.get("call_id")
            or tool.get("tool_call_id")
            or ""
        ).strip()
        if not call_id:
            # An anonymous observation has its own identity. Equal tool bodies
            # do not prove it is the same event as another observation.
            call_id = uuid.uuid4().hex
        state = projection.tools.setdefault(call_id, {})
        state.update(_json_safe(tool))
        state["tool_type"] = tool_type
        state["turn_id"] = turn_id
        status = _tool_status(event, tool)
        state["status"] = status
        params = event.get("params") if isinstance(event.get("params"), Mapping) else {}
        if status == "running":
            state.setdefault(
                "started_at",
                _timestamp(params.get("startedAtMs")) or created_at,
            )
            return
        if status not in {"completed", "failed", "cancelled"}:
            return
        state.setdefault("started_at", _timestamp(params.get("startedAtMs")) or created_at)
        state["finished_at"] = _timestamp(params.get("completedAtMs")) or created_at
        self._persist_tool(projection, call_id, state)

    def _persist_tool(
        self,
        projection: _Projection,
        call_id: str,
        state: Mapping[str, Any],
    ) -> str:
        segment_id = live_tool_segment_id(self._output_scope(projection), call_id)
        tool_type = str(state.get("tool_type") or state.get("type") or "")
        tool_name = str(
            state.get("tool_name")
            or state.get("name")
            or state.get("tool")
            or _TOOL_NAMES.get(tool_type)
            or "tool"
        )
        namespace = str(state.get("server") or state.get("server_name") or state.get("namespace") or "")
        error = state.get("error") or state.get("errors")
        content = {
            "tool_name": tool_name,
            "tool_namespace": namespace,
            "call_id": call_id,
            "input": _extract(state, _TOOL_INPUT_FIELDS),
            "result": _extract(state, _TOOL_RESULT_FIELDS),
            "error": _json_safe(error),
            "external_effect_refs": _json_safe(state.get("external_effect_refs") or []),
        }
        status = str(state.get("status") or "completed")
        self._put_output_segment(
            projection,
            segment_id=segment_id,
            segment_type="tool",
            content=content,
            content_type="application/json",
            started_at=str(state.get("started_at") or ""),
            finished_at=str(state.get("finished_at") or utc_now()),
            status=status,
            created_by_actor_ref=projection.request.identity.principal.id,
            performed_by_actor_ref=projection.request.identity.principal.id,
            metadata={
                "tool_name": tool_name,
                "tool_namespace": namespace,
                "source": "provider_tool",
                "kind": "tool",
                "identity": projection.request.identity.to_dict(),
            },
            created_by_agent_id=projection.agent_id,
            execution_id=projection.request.execution_id,
        )
        self._append_output(projection, segment_id, tool_name)
        projection.persisted_segments.add(segment_id)
        return segment_id

    def _persist_compaction(
        self,
        projection: _Projection,
        *,
        content: Mapping[str, Any],
        created_at: str,
        retain_from: str = "",
        provider_compaction_ref: str = "",
        kept_execution_id: str = "",
    ) -> tuple[str, bool]:
        segment_id, created = persist_provider_compaction(
            self.store,
            provider_session_ref=projection.provider_session_ref,
            agent_id=projection.agent_id,
            operation_id=projection.request.execution_id,
            actor_ref=projection.request.actor,
            provider=projection.request.provider,
            content=content,
            created_at=created_at,
            retain_from=retain_from,
            provider_compaction_ref=provider_compaction_ref,
            skip_segment_ids=projection.persisted_segments,
            kept_execution_id=kept_execution_id,
        )
        if created:
            projection.persisted_segments.add(segment_id)
            projection.compaction_segment_id = segment_id
        return segment_id, created

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
            self._bind_provider_session(
                projection,
                provider=projection.request.provider,
                backend_session_id=resolved_backend_session_id,
                backend_route_id=backend_route_id or projection.backend_route_id,
            )
        elif operation == "fork":
            source_backend_session_id = projection.session.backend_session_id
            self._bind_provider_session(
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
            projection.final_segment_id = self._persist_model_message(
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
