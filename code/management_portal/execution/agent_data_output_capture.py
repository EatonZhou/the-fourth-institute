"""Capture of what a Provider produced in a turn: messages, reasoning, tool calls and native records."""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .agent_data_projection_state import ExecutionProjection, input_message_id
from .agent_data_store import AgentDataStore, stable_segment_id, utc_now
from .provider_reasoning import ProviderReasoning, provider_reasoning


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


def json_safe(value: object) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [json_safe(item) for item in value]
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


def event_created_at(event: Mapping[str, Any]) -> str:
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


def model_timing(value: object, completed_at: str) -> dict[str, str]:
    """Only adapter-observed request boundaries count as model dispatch times.

    Turn starts, message creation, first-token events and step starts have
    different meanings and must not be substituted for a missing dispatch.
    """
    from .request_timing import observed_timing
    timing = observed_timing(dict(value) if isinstance(value, Mapping) else {})
    start = utc_moment(timing.get("started_at"))
    finish = utc_moment(timing.get("finished_at")) or utc_moment(completed_at)
    if start and finish and start > finish:
        start = None
    return {"started_at": start.isoformat() if start else "",
            "finished_at": finish.isoformat() if finish else ""}


def event_turn_id(event: Mapping[str, Any]) -> str:
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


def _provider_observation_metadata(recorded, observed):
    # Replays preserve immutable history, including an old record's lack of
    # native evidence. Only first capture records facts from this event.
    if recorded is not None:
        metadata = recorded["metadata"]
        return {"provider_observation": metadata["provider_observation"]} if "provider_observation" in metadata else {}
    return {"provider_observation": observed} if observed else {}


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
        field: json_safe(mapping[field])
        for field in fields
        if field in mapping and mapping[field] is not None
    }


def utc_moment(value: object) -> datetime | None:
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


class OutputCapture:
    """Records a turn's output Segments in the trace of the Agent that produced them."""

    def __init__(self, store: AgentDataStore) -> None:
        self.store = store

    def record_provider_request(
        self, projection: ExecutionProjection, *, execution_id: str, provider: str, params: Mapping[str, Any],
        turn_id: str, created_at: str,
    ) -> None:
        """Keep the receipt of the Provider request that started a Codex turn or review."""
        rpc_method = str(params.get("rpcMethod") or "")
        thread_id = str(params.get("threadId") or "")
        if provider != "codex" or rpc_method not in {"turn/start", "review/start"} or not thread_id or not turn_id:
            return
        receipt_id = stable_segment_id("provider-request", execution_id, thread_id, turn_id, rpc_method)
        try:
            self.store.operation_record(receipt_id)
        except KeyError:
            self.store.put_operation_record(
                segment_id=receipt_id, segment_type="artifact",
                content={"type": "provider_request_receipt", "provider": provider,
                         "method": rpc_method, "backend_session_id": thread_id,
                         "provider_turn_id": turn_id},
                content_type="application/json", created_at=created_at,
                status="completed", created_by_agent_id=projection.agent_id,
                created_by_actor_ref=projection.request.identity.principal.id,
                performed_by_actor_ref=projection.request.identity.principal.id,
                execution_id=execution_id,
                metadata={"kind": "provider_request_receipt"},
            )

    def record_native_item(
        self, projection: ExecutionProjection, item: Mapping[str, Any], *, execution_id: str, provider: str,
        created_at: str,
    ) -> None:
        """Record a native user input, turn artifacts or subagent run card, once."""
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

    def observe_output(
        self, projection: ExecutionProjection, event: Mapping[str, Any], created_at: str, turn_id: str,
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
            self.persist_model_message(
                projection,
                text=text,
                phase=phase,
                item_id=item_id,
                native_item_id=item_id,
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
        projection: ExecutionProjection,
        *,
        metadata: Mapping[str, Any],
        **fields: Any,
    ) -> str:
        if projection.trace_key:
            metadata = {**metadata, "subagent": projection.subagent}
        return self.store.put_segment(metadata=metadata, **fields)

    @staticmethod
    def _output_scope(projection: ExecutionProjection) -> str:
        execution_id = projection.request.execution_id
        return f"{execution_id}/{projection.trace_key}" if projection.trace_key else execution_id

    def _append_output(self, projection: ExecutionProjection, segment_id: str, actor_ref: str) -> None:
        self.store.append_agent_segments(
            self.store.subagent_id(projection.agent_id, projection.trace_key)
            if projection.trace_key else projection.agent_id,
            [segment_id], actor_ref=actor_ref,
        )

    def _persist_reasoning(
        self,
        projection: ExecutionProjection,
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
            **model_timing(request_timing, created_at),
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

    def persist_model_message(
        self,
        projection: ExecutionProjection,
        *,
        text: str,
        phase: str,
        item_id: str,
        created_at: str,
        turn_id: str,
        request_timing: object = None,
        native_item_id: str = "",
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
            **model_timing(request_timing, created_at),
            status="completed",
            created_by_actor_ref=projection.request.identity.principal.id,
            performed_by_actor_ref=projection.request.identity.principal.id,
            metadata={
                "role": "assistant",
                **_provider_observation_metadata(recorded, {"item_id": native_item_id} if native_item_id else {}),
                "message": {
                    "message_id": model_message_id(projection.request.execution_id, item_id, normalized_phase),
                    "role": "agent", "author_id": projection.request.identity.principal.id,
                    "created_at": created_at, "activated_at": created_at,
                    "execution_id": projection.request.execution_id,
                    "provider_turn_id": turn_id,
                    "reply_to_message_id": input_message_id(projection.request),
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
        projection: ExecutionProjection,
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
        if event.get("origin") == "turn_snapshot":
            # A Worker that takes over a running turn replays its snapshot,
            # where long command output is cut short. A tool the live events
            # already recorded keeps that record.
            segment_id = live_tool_segment_id(self._output_scope(projection), call_id)
            if self.store.has_segment(segment_id):
                self._append_output(projection, segment_id, projection.request.provider)
                projection.persisted_segments.add(segment_id)
                return
        state = projection.tools.setdefault(call_id, {})
        state.update(json_safe(tool))
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
        projection: ExecutionProjection,
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
            "error": json_safe(error),
            "external_effect_refs": json_safe(state.get("external_effect_refs") or []),
        }
        status = str(state.get("status") or "completed")
        observation = {}
        if isinstance(state.get("id"), str) and state["id"]:
            observation["item_id"] = state["id"]
        if tool_type == "commandExecution":
            if isinstance(state.get("command"), str):
                observation["command_sha256"] = hashlib.sha256(state["command"].encode()).hexdigest()
            if type(state.get("exitCode")) is int:
                observation["exit_code"] = state["exitCode"]
        try:
            recorded = self.store.segment(segment_id)
        except KeyError:
            recorded = None
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
                **_provider_observation_metadata(recorded, observation),
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
