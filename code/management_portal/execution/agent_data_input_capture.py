"""Capture of what a Provider was given in a turn: its input, system context and Harness reminders."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from .agent_data_contexts import HARNESS_CONTROL_KIND
from .agent_data_input import input_message
from .agent_data_projection_state import ExecutionProjection, input_message_id
from .agent_data_store import AgentDataStore, stable_segment_id, utc_now
from .contracts import ExecutionRequest


class InputCapture:
    """Records the inputs of a turn in the trace of the Agent that runs it."""

    def __init__(self, store: AgentDataStore) -> None:
        self.store = store

    def record_provider_input(
        self, projection: ExecutionProjection, request: ExecutionRequest, *, agent_id: str,
        input_segment_id: str, accepted_at: str,
    ) -> None:
        """Record the input the request gives its Provider, once: composed from its Chat message or as given."""
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
        portal_message_id = input_message_id(request)
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

    def record_effective_input(self, projection: ExecutionProjection, execution_id: str, invocation: object) -> None:
        """Record the input as the Harness prepared it for the Provider, when it differs from the request."""
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

    def record_system_context(self, projection: ExecutionProjection, execution_id: str, invocation: object) -> None:
        """Record the system context the invocation attaches, once."""
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

    def record_harness_control(
        self, projection: ExecutionProjection, execution_id: str, details: Mapping[str, Any],
    ) -> None:
        """Record a Harness reminder the Provider took into its running turn, once."""
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
