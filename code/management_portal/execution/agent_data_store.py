"""Canonical Agent facts: the vocabulary of their records and the store that composes them."""
from __future__ import annotations

import hashlib
import json
import secrets
import threading
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from management_common.json_columns import json_text
from management_common.portal_database import portal_database


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def json_document(value: object) -> str:
    """``canonical_json`` text for a JSON column (see json_columns)."""
    return json_text(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def new_identifier(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(12)}"


def stable_segment_id(*parts: object) -> str:
    digest = hashlib.sha256(
        "\0".join(str(part or "") for part in parts).encode("utf-8")
    ).hexdigest()[:32]
    return f"segment_{digest}"


def chat_segment_id(agent_id: object, message_id: object) -> str:
    return stable_segment_id("chat", agent_id, message_id)


def clean_string_tuple(value: object) -> tuple[str, ...]:
    raw: object = value
    if isinstance(value, str):
        try:
            raw = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            raw = ()
    if not isinstance(raw, (list, tuple, set, frozenset)):
        return ()
    return tuple(sorted({
        str(item or "").strip()
        for item in raw
        if str(item or "").strip()
    }))


FIELD_NOT_PROVIDED = object()


def in_batches(values: Sequence[Any], size: int = 500) -> Iterable[Sequence[Any]]:
    for offset in range(0, len(values), size):
        yield values[offset : offset + size]


def finish_conversation_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """Turn a summary's ``actor_requests`` into its request ids and conversation signature."""

    requests = sorted(summary.pop("actor_requests", []) or [])
    summary["actor_request_ids"] = [message_id for _at, message_id in requests]
    summary["actor_last_user_message_at"] = requests[-1][0] if requests else ""
    signature_facts = [
        summary["user_message_count"], summary["last_user_actor"],
        summary["last_user_message_at"], summary["actor_last_user_message_at"],
        summary["actor_request_ids"],
    ]
    if summary["actor_addressed_message_id"]:
        signature_facts += [summary["actor_addressed_message_id"], summary["actor_addressed_at"]]
    summary["conversation_signature"] = (
        hashlib.sha256(canonical_json(signature_facts).encode("utf-8")).hexdigest()
        if any(signature_facts) else ""
    )
    return summary


class AgentDataStore:
    """Canonical Agent facts and their current ordered projections.

    Segments are immutable facts.  A Sequence is one mutable ordered set of
    Segment references.  Executions own provider-call state, while Chat is a
    current display projection.  Transport progress and Sequence edit history
    are outside this store.

    Each record family is a component of its own module; the store composes
    them over one database and is their published interface.
    """

    def __init__(self) -> None:
        from .agent_data_chat import ChatReads
        from .agent_data_chat_activity import ChatActivity
        from .agent_data_chat_replies import ChatReplies
        from .agent_data_chat_writes import ChatWrites
        from .agent_data_contexts import AgentContexts
        from .agent_data_executions import ExecutionRecords
        from .agent_data_forks import AgentForks
        from .agent_data_segments import SegmentRecords
        from .agent_data_sessions import ProviderSessions
        from .agent_data_tables import initialize_agent_data
        from .agent_data_trace_edits import TraceEdits
        from .agent_data_traces import AgentTraces
        from .agent_data_transcripts import SessionTranscripts

        self.database = portal_database(schema="agent")
        self.database_identity = self.database.identity
        self.database.write(initialize_agent_data)
        self.segments = SegmentRecords(self.database)
        self.traces = AgentTraces(self.database)
        self.trace_edits = TraceEdits(self.database)
        self.executions = ExecutionRecords(self.database)
        self.sessions = ProviderSessions(self.database)
        self.chat = ChatReads(self.database)
        self.contexts = AgentContexts(self.database, segments=self.segments, executions=self.executions,
                                      chat=self.chat)
        self.transcripts = SessionTranscripts(self.database, chat=self.chat)
        self.forks = AgentForks(self.database, executions=self.executions, sessions=self.sessions,
                                contexts=self.contexts, trace_edits=self.trace_edits, chat=self.chat)
        self.replies = ChatReplies(self.database, chat=self.chat)
        self.chat_writes = ChatWrites(self.database)
        self.chat_activity = ChatActivity(self.database, chat=self.chat)

    # Segments

    def put_segment(
        self,
        *,
        segment_id: str = "",
        segment_type: str,
        content: object,
        started_at: str = "",
        finished_at: str = "",
        status: str = "completed",
        created_by_actor_ref: str = "",
        performed_by_actor_ref: str = "",
        content_type: str = "text/plain; charset=utf-8",
        metadata: Mapping[str, Any] | None = None,
        created_by_agent_id: str = "",
        execution_id: str = "",
    ) -> str:
        return self.segments.put_segment(
            segment_id=segment_id, segment_type=segment_type, content=content,
            started_at=started_at, finished_at=finished_at, status=status,
            created_by_actor_ref=created_by_actor_ref,
            performed_by_actor_ref=performed_by_actor_ref, content_type=content_type,
            metadata=metadata, created_by_agent_id=created_by_agent_id, execution_id=execution_id,
        )

    def has_segment(self, segment_id: str) -> bool:
        return self.segments.has_segment(segment_id)

    def segment(self, segment_id: str) -> dict[str, Any]:
        return self.segments.segment(segment_id)

    def add_relations(
        self,
        relations: Sequence[Mapping[str, Any]],
    ) -> list[str]:
        return self.segments.add_relations(relations)

    def segment_members(self, segment_ids: Sequence[str]) -> list[dict[str, Any]]:
        return self.segments.segment_members(segment_ids)

    # Agents and traces

    def ensure_agent(self, agent_id: str, *, actor_ref: str = "", sequence_kind: str = "agent_trace",
                     parent_agent_id: str | None = None) -> dict[str, Any]:
        return self.traces.ensure_agent(
            agent_id, actor_ref=actor_ref, sequence_kind=sequence_kind,
            parent_agent_id=parent_agent_id,
        )

    def agent(self, agent_id: str) -> dict[str, Any]:
        return self.traces.agent(agent_id)

    def trace_sequence_id(self, agent_id: str) -> str:
        return self.traces.trace_sequence_id(agent_id)

    def trace_length(self, agent_id: str) -> int:
        return self.traces.trace_length(agent_id)

    def read_sequence(
        self,
        sequence_id: str,
        *,
        offset: int = 0,
        limit: int = 200,
    ) -> dict[str, Any]:
        return self.traces.read_sequence(sequence_id, offset=offset, limit=limit)

    def sequence_segment_ids(self, sequence_id: str) -> list[str]:
        return self.traces.sequence_segment_ids(sequence_id)

    def subagent_id(self, root_agent_id: str, trace_key: str) -> str:
        return self.traces.subagent_id(root_agent_id, trace_key)

    # Trace edits

    def append_agent_segments(self, agent_id: str, segment_ids: Iterable[str], *, actor_ref: str) -> dict[str, Any]:
        return self.trace_edits.append_agent_segments(agent_id, segment_ids, actor_ref=actor_ref)

    def transform_agent_segments(
        self, agent_id: str, source_ids: Sequence[str], *, segment_id: str,
        content: Any, actor_ref: str, operation_id: str, operation_type: str,
        segment_type: str = "input", content_type: str = "text/plain; charset=utf-8",
        metadata: Mapping[str, Any] | None = None, execution_id: str = "",
        created_at: str = "",
    ) -> str:
        return self.trace_edits.transform_agent_segments(
            agent_id, source_ids, segment_id=segment_id, content=content, actor_ref=actor_ref,
            operation_id=operation_id, operation_type=operation_type, segment_type=segment_type,
            content_type=content_type, metadata=metadata, execution_id=execution_id,
            created_at=created_at,
        )

    def place_in_trace_before(
        self, agent_id: str, segment_id: str, following: Iterable[str], *, actor_ref: str,
    ) -> bool:
        return self.trace_edits.place_in_trace_before(
            agent_id, segment_id, following, actor_ref=actor_ref,
        )

    def reset_provider_context(self, agent_id: str, *, reset_at: str, actor_ref: str) -> str:
        return self.trace_edits.reset_provider_context(
            agent_id, reset_at=reset_at, actor_ref=actor_ref,
        )

    def record_context_reset(
        self, agent_id: str, *, reset_at: str, actor_ref: str,
        trace_position: int,
    ) -> str:
        return self.trace_edits.record_context_reset(
            agent_id, reset_at=reset_at, actor_ref=actor_ref, trace_position=trace_position,
        )

    def clear_chat(self, agent_id: str, *, actor_ref: str = "") -> str:
        return self.trace_edits.clear_chat(agent_id, actor_ref=actor_ref)

    # Executions

    def reserve_execution(
        self, *, execution_id: str, agent_id: str, actor_ref: str, provider: str,
        created_at: str = "",
    ) -> dict[str, Any]:
        return self.executions.reserve_execution(
            execution_id=execution_id, agent_id=agent_id, actor_ref=actor_ref, provider=provider,
            created_at=created_at,
        )

    def queue_execution(self, execution_id: str) -> None:
        self.executions.queue_execution(execution_id)

    def begin_execution(
        self,
        *,
        execution_id: str,
        agent_id: str,
        actor_ref: str,
        provider: str,
        started_at: str,
    ) -> dict[str, Any]:
        return self.executions.begin_execution(
            execution_id=execution_id, agent_id=agent_id, actor_ref=actor_ref, provider=provider,
            started_at=started_at,
        )

    def bind_execution_provider_session(
        self,
        execution_id: str,
        *,
        provider_session_ref: str,
    ) -> None:
        self.executions.bind_execution_provider_session(
            execution_id, provider_session_ref=provider_session_ref,
        )

    def record_input_acceptance(
        self, execution_id: str, *, agent_id: str, provider_turn_id: str,
    ) -> None:
        self.executions.record_input_acceptance(
            execution_id, agent_id=agent_id, provider_turn_id=provider_turn_id,
        )

    def finish_execution(self, execution_id: str, *, status: str, finished_at: str = "",
                         usage: Mapping[str, Any] | None = None, provider_turn_id: str = "",
                         only_if_running: bool = False) -> bool:
        return self.executions.finish_execution(
            execution_id, status=status, finished_at=finished_at, usage=usage,
            provider_turn_id=provider_turn_id, only_if_running=only_if_running,
        )

    def finish_execution_in(self, connection: Any, execution_id: str, *, status: str,
                            finished_at: str = "", usage: Mapping[str, Any] | None = None,
                            provider_turn_id: str = "", only_if_running: bool = False) -> bool:
        return self.executions.finish_execution_in(
            connection, execution_id, status=status, finished_at=finished_at, usage=usage,
            provider_turn_id=provider_turn_id, only_if_running=only_if_running,
        )

    def record_execution_output(self, execution_id: str, *, usage: Mapping[str, Any] | None = None,
                                provider_turn_id: str = "") -> None:
        self.executions.record_execution_output(
            execution_id, usage=usage, provider_turn_id=provider_turn_id,
        )

    def record_accounted_usage(self, execution_id: str, usage: Mapping[str, Any]) -> bool:
        return self.executions.record_accounted_usage(execution_id, usage)

    def provider_session_executions(self, provider: str, *, active_since: str) -> list[dict[str, Any]]:
        return self.executions.provider_session_executions(provider, active_since=active_since)

    def execution(self, execution_id: str) -> dict[str, Any] | None:
        return self.executions.execution(execution_id)

    def binding_has_accepted_input(self, binding_id: str, *, agent_id: str) -> bool:
        return self.executions.binding_has_accepted_input(binding_id, agent_id=agent_id)

    def running_executions(self) -> list[dict[str, Any]]:
        return self.executions.running_executions()

    def latest_provider_session_ref(self, agent_id: str, *, since: str = "") -> str:
        return self.executions.latest_provider_session_ref(agent_id, since=since)

    # Provider sessions and bindings

    def ensure_provider_session(
        self,
        *,
        provider: str,
        backend_route_id: str,
        backend_session_id: str,
        agent_id: str,
        actor_ref: str,
        source_backend_session_id: str = "",
        source_backend_route_id: str = "",
        context_agent_id: str = "",
    ) -> dict[str, Any]:
        return self.sessions.ensure_provider_session(
            provider=provider, backend_route_id=backend_route_id,
            backend_session_id=backend_session_id, agent_id=agent_id, actor_ref=actor_ref,
            source_backend_session_id=source_backend_session_id,
            source_backend_route_id=source_backend_route_id, context_agent_id=context_agent_id,
        )

    def provider_binding(self, binding_id: str) -> dict[str, Any]:
        return self.sessions.provider_binding(binding_id)

    def provider_binding_routes(
        self,
        binding_ids: Iterable[object],
    ) -> dict[str, dict[str, Any]]:
        return self.sessions.provider_binding_routes(binding_ids)

    def bind_provider_session(
        self,
        binding_id: str,
        provider_session_ref: str,
        *,
        context_fingerprint: object = FIELD_NOT_PROVIDED,
        context_injected_at: object = FIELD_NOT_PROVIDED,
        context_injection_count: object = FIELD_NOT_PROVIDED,
        harness_tool_names: object = FIELD_NOT_PROVIDED,
    ) -> dict[str, Any]:
        return self.sessions.bind_provider_session(
            binding_id, provider_session_ref, context_fingerprint=context_fingerprint,
            context_injected_at=context_injected_at,
            context_injection_count=context_injection_count, harness_tool_names=harness_tool_names,
        )

    def replace_provider_binding(
        self,
        binding_id: str,
        prepared_binding_id: str,
        *,
        expected_provider: str = "",
        expected_backend_session_id: str = "",
        expected_provider_session_ref: str = "",
        expected_absent: bool = False,
    ) -> dict[str, Any]:
        return self.sessions.replace_provider_binding(
            binding_id, prepared_binding_id, expected_provider=expected_provider,
            expected_backend_session_id=expected_backend_session_id,
            expected_provider_session_ref=expected_provider_session_ref,
            expected_absent=expected_absent,
        )

    def restore_provider_binding(
        self,
        binding_id: str,
        *,
        expected_provider_session_ref: str,
        binding: Mapping[str, Any],
        expected_absent: bool = False,
    ) -> dict[str, Any]:
        return self.sessions.restore_provider_binding(
            binding_id, expected_provider_session_ref=expected_provider_session_ref,
            binding=binding, expected_absent=expected_absent,
        )

    def delete_provider_binding(
        self, binding_id: str, *, expected_provider_session_ref: str = "",
    ) -> None:
        self.sessions.delete_provider_binding(
            binding_id, expected_provider_session_ref=expected_provider_session_ref,
        )

    # Contexts

    def agent_context_snapshot(self, agent_id: str, *, before_message_id: str = "", through_message_id: str = "", through_turn_id: str = "", provider_session_ref: str = "") -> dict[str, Any]:
        return self.contexts.agent_context_snapshot(
            agent_id, before_message_id=before_message_id, through_message_id=through_message_id,
            through_turn_id=through_turn_id, provider_session_ref=provider_session_ref,
        )

    def context_evidence_snapshot(self, agent_id: str) -> dict[str, Any]:
        return self.contexts.context_evidence_snapshot(agent_id)

    def latest_context_compaction(self, agent_id: str, *, since: str = "") -> dict[str, Any] | None:
        return self.contexts.latest_context_compaction(agent_id, since=since)

    def message_in_context(self, agent_id: str, message_id: str, *, since: str = "") -> bool:
        return self.contexts.message_in_context(agent_id, message_id, since=since)

    def session_context_head(self, provider_session_ref: str, *, agent_id: str = "") -> dict[str, Any] | None:
        return self.contexts.session_context_head(provider_session_ref, agent_id=agent_id)

    def session_context_ids(self, provider_session_ref: str, *, agent_id: str = "") -> list[str]:
        return self.contexts.session_context_ids(provider_session_ref, agent_id=agent_id)

    def session_compaction(
        self, provider_session_ref: str, provider_compaction_ref: str, *, agent_id: str = "",
    ) -> str:
        return self.contexts.session_compaction(
            provider_session_ref, provider_compaction_ref, agent_id=agent_id,
        )

    def record_context_received(
        self,
        agent_id: str,
        provider_session_ref: str,
        segment_ids: Sequence[str],
        *,
        actor_ref: str,
    ) -> str:
        return self.contexts.record_context_received(
            agent_id, provider_session_ref, segment_ids, actor_ref=actor_ref,
        )

    def provider_context_snapshot(
        self,
        *,
        provider: str = "",
        backend_session_id: str = "",
        backend_route_id: str = "",
        provider_session_ref: str = "",
        through_turn_id: str = "",
        agent_id: str = "",
        context_cleared: bool = False,
    ) -> dict[str, Any] | None:
        return self.contexts.provider_context_snapshot(
            provider=provider, backend_session_id=backend_session_id,
            backend_route_id=backend_route_id, provider_session_ref=provider_session_ref,
            through_turn_id=through_turn_id, agent_id=agent_id, context_cleared=context_cleared,
        )

    def fork_context_segments(self, references: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        return self.contexts.fork_context_segments(references)

    # Session transcripts

    def session_transcript(
        self,
        *,
        provider: str,
        backend_session_id: str,
        backend_route_id: str = "",
    ) -> dict[str, Any] | None:
        return self.transcripts.session_transcript(
            provider=provider, backend_session_id=backend_session_id,
            backend_route_id=backend_route_id,
        )

    def provider_user_prompts(
        self,
        *,
        provider: str,
        backend_session_id: str,
    ) -> list[dict[str, Any]]:
        return self.transcripts.provider_user_prompts(
            provider=provider, backend_session_id=backend_session_id,
        )

    def message_provider_prompts(self, agent_id: str, message_id: str) -> list[dict[str, Any]]:
        return self.transcripts.message_provider_prompts(agent_id, message_id)

    # Forks

    def fork_agent(
        self, *, source_agent_id: str, target_agent_id: str,
        segment_ids: Sequence[str], actor_ref: str,
    ) -> dict[str, Any]:
        return self.forks.fork_agent(
            source_agent_id=source_agent_id, target_agent_id=target_agent_id,
            segment_ids=segment_ids, actor_ref=actor_ref,
        )

    def frozen_fork_segments(self, agent_id: str) -> list[str]:
        return self.forks.frozen_fork_segments(agent_id)

    def frozen_fork_references(self, agent_id: str) -> list[dict[str, str]]:
        return self.forks.frozen_fork_references(agent_id)

    def initialization_session_fact(self, agent_id: str, binding_id: str) -> dict[str, Any]:
        return self.forks.initialization_session_fact(agent_id, binding_id)

    def promote_initialization_session(self, *, agent_id: str, candidate_agent_id: str,
                                      binding_id: str, prepared_binding_id: str,
                                      source_binding: Mapping[str, Any], segment_ids: list[str],
                                      compaction_execution_id: str, initialization_id: str,
                                      actor_ref: str) -> dict[str, Any]:
        return self.forks.promote_initialization_session(
            agent_id=agent_id, candidate_agent_id=candidate_agent_id, binding_id=binding_id,
            prepared_binding_id=prepared_binding_id, source_binding=source_binding,
            segment_ids=segment_ids, compaction_execution_id=compaction_execution_id,
            initialization_id=initialization_id, actor_ref=actor_ref,
        )

    def fork_inheritance(self, agent_id: str, *, include_compaction: bool = False) -> dict[str, Any]:
        return self.forks.fork_inheritance(agent_id, include_compaction=include_compaction)

    # Chat reads

    def bring_chat_current(self) -> None:
        self.chat.bring_chat_current()

    def displayed_chat_message_ids(self, agent_id: str) -> list[str]:
        return self.chat.displayed_chat_message_ids(agent_id)

    def read_chat_messages(
        self,
        agent_id: str,
        *,
        limit: int = 0,
    ) -> list[dict[str, Any]]:
        return self.chat.read_chat_messages(agent_id, limit=limit)

    def read_chat_messages_many(self, agent_ids: Sequence[str]) -> dict[str, list[dict[str, Any]]]:
        return self.chat.read_chat_messages_many(agent_ids)

    def read_chat_messages_through_turn(
        self,
        agent_id: str,
        message_ids: Sequence[object],
        *,
        limit: int,
        include_turn: bool = True,
    ) -> list[dict[str, Any]]:
        return self.chat.read_chat_messages_through_turn(
            agent_id, message_ids, limit=limit, include_turn=include_turn,
        )

    def read_chat_message_page(
        self,
        agent_id: str,
        *,
        before_ordinal: int | None = None,
        after_ordinal: int = 0,
        limit: int,
    ) -> dict[str, Any]:
        return self.chat.read_chat_message_page(
            agent_id, before_ordinal=before_ordinal, after_ordinal=after_ordinal, limit=limit,
        )

    def read_input_history(
        self,
        agent_id: str,
        sender: str,
        *,
        before_ordinal: int | None = None,
        limit: int,
    ) -> dict[str, Any]:
        return self.chat.read_input_history(
            agent_id, sender, before_ordinal=before_ordinal, limit=limit,
        )

    def chat_message_ordinal(self, agent_id: str, message_id: str) -> int | None:
        return self.chat.chat_message_ordinal(agent_id, message_id)

    def chat_message(
        self,
        agent_id: str,
        message_id: str,
    ) -> dict[str, Any] | None:
        return self.chat.chat_message(agent_id, message_id)

    def chat_message_ancestors(self, segment_id: str, message_id: str) -> list[dict[str, Any]]:
        return self.chat.chat_message_ancestors(segment_id, message_id)

    def chat_message_provenance(self, agent_id: str, message_id: str) -> dict[str, str]:
        return self.chat.chat_message_provenance(agent_id, message_id)

    def latest_chat_message(
        self,
        agent_id: str,
        *,
        role: str = "",
    ) -> dict[str, Any] | None:
        return self.chat.latest_chat_message(agent_id, role=role)

    def trace_chat_messages(self, agent_id: str, message_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
        return self.chat.trace_chat_messages(agent_id, message_ids)

    def group_source_message(self, agent_id: str, message_id: str) -> dict[str, Any] | None:
        return self.chat.group_source_message(agent_id, message_id)

    def message_groups(self, agent_id: str, message_ids: Sequence[str]) -> dict[str, str]:
        return self.chat.message_groups(agent_id, message_ids)

    # Chat replies

    def chat_final_replies_many(
        self, requests: Mapping[str, Sequence[str]],
    ) -> dict[str, dict[str, tuple[str, str]]]:
        return self.replies.chat_final_replies_many(requests)

    def chat_replies(
        self,
        agent_id: str,
        message_id: str,
        *,
        until_message_id: str = "",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        return self.replies.chat_replies(
            agent_id, message_id, until_message_id=until_message_id, limit=limit,
        )

    def chat_replies_many(
        self, requests: Mapping[str, Mapping[str, str]], *, limit: int = 20,
    ) -> dict[str, dict[str, list[dict[str, Any]]]]:
        return self.replies.chat_replies_many(requests, limit=limit)

    # Chat writes

    def display_chat_message(
        self,
        agent_id: str,
        message: Mapping[str, Any],
    ) -> dict[str, Any]:
        return self.chat_writes.display_chat_message(agent_id, message)

    def put_operation_record(self, *, segment_id: str, created_by_agent_id: str,
                             created_at: str = "", **fields: Any) -> str:
        return self.chat_writes.put_operation_record(
            segment_id=segment_id, created_by_agent_id=created_by_agent_id, created_at=created_at,
            **fields,
        )

    def operation_record(self, operation_id: str) -> dict[str, Any]:
        return self.chat_writes.operation_record(operation_id)

    def project_chat_message(
        self,
        agent_id: str,
        message: Mapping[str, Any],
        *,
        activated_at: str = "",
    ) -> tuple[dict[str, Any], bool]:
        return self.chat_writes.project_chat_message(agent_id, message, activated_at=activated_at)

    def merge_message_group(self, agent_id: str, message_ids: Sequence[str], target_id: str) -> None:
        self.chat_writes.merge_message_group(agent_id, message_ids, target_id)

    def update_chat_message(
        self,
        agent_id: str,
        message_id: str,
        message: Mapping[str, Any],
    ) -> dict[str, Any]:
        return self.chat_writes.update_chat_message(agent_id, message_id, message)

    # Chat activity

    def chat_user_counts(self, agent_ids: Sequence[str]) -> dict[str, int]:
        return self.chat_activity.chat_user_counts(agent_ids)

    def chat_actor_activity_page(
        self, actor_ref: str, *, after: tuple[str, str] | None = None,
        limit: int = 50, exclude_agent_ids: Sequence[str] = (),
        known_times: Mapping[str, Any] | None = None,
    ) -> list[tuple[str, str]]:
        return self.chat_activity.chat_actor_activity_page(
            actor_ref, after=after, limit=limit, exclude_agent_ids=exclude_agent_ids,
            known_times=known_times,
        )

    def chat_actor_activity(
        self, actor_ref: str, agent_ids: Iterable[str] | None = None,
    ) -> dict[str, str]:
        return self.chat_activity.chat_actor_activity(actor_ref, agent_ids)

    def chat_activity_page_token(self, agent_id: str, latest_at: str) -> str:
        return self.chat_activity.chat_activity_page_token(agent_id, latest_at)

    def chat_activity_page_boundary(self, page_token: str) -> tuple[str, str] | None:
        return self.chat_activity.chat_activity_page_boundary(page_token)

    def chat_signatures(self, agent_ids: Sequence[str]) -> dict[str, str]:
        return self.chat_activity.chat_signatures(agent_ids)

    def chat_conversation_summaries(
        self, agent_ids: Sequence[str], actor_ref: str, *, excluded_business_ref: str = "",
        finish: bool = True,
    ) -> dict[str, dict[str, Any]]:
        return self.chat_activity.chat_conversation_summaries(
            agent_ids, actor_ref, excluded_business_ref=excluded_business_ref, finish=finish,
        )

    def chat_message_headers(
        self,
        agent_ids: Sequence[str],
        *,
        message_ids: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        return self.chat_activity.chat_message_headers(agent_ids, message_ids=message_ids)

    def health(self) -> dict[str, Any]:
        with self.database.read() as connection:
            schema = connection.execute(
                "SELECT value FROM agent_store_meta WHERE key = 'schema'"
            ).fetchone()
        return {
            "enabled": True,
            "schema": str(schema["value"] if schema else ""),
            "database": self.database.display_name,
        }


_STORE: AgentDataStore | None = None
_STORE_LOCK = threading.Lock()


def get_agent_data_store() -> AgentDataStore:
    global _STORE
    identity = portal_database(schema="agent").identity
    with _STORE_LOCK:
        if _STORE is None or _STORE.database_identity != identity:
            _STORE = AgentDataStore()
        return _STORE


def reset_agent_data_store_for_tests() -> None:
    global _STORE
    with _STORE_LOCK:
        _STORE = None
