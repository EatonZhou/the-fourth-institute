from __future__ import annotations

from management_common.format_tags import schema_matches

import base64
from collections import OrderedDict
from copy import deepcopy
import hashlib
import json
import re
import secrets
import threading
import time
from collections.abc import Iterable, Iterator, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from management_common.agent_data_schema import AGENT_DATA_SCHEMA
from management_common.json_columns import json_text, json_value
from management_common.portal_database import portal_database
from .message_projection import message_facts, project_message, wrap_message
from management_common.database_time import instant, iso_time, decode_times
from .agent_operations import TABLE_SQL, put_operation, next_position
from .conversation_projection import CHAT_PREDICATE, CHAT_SEGMENT, REPLY_SEGMENT
from . import chat_position_index, execution_inputs


SEGMENT_TYPES = frozenset({
    "input",
    "model",
    "tool",
    "compaction",
    "harness",
})
RELATION_TYPES = frozenset({
    "derived_from",
    "transformed_from",
    "compressed_from",
    "extracted_from",
    "merged_from",
})
# Every Agent has one trace, the one record of what happened.  What a
# Provider session holds (its transcript and current context) is read from
# that trace (``session_transcript_ids``, ``session_context_ids``).  A
# subagent the Agent's model starts has a window of its own: its steps are
# a trace of its own, linked by agents.parent_agent_id. Sequences are generic
# ordered Segment references, without Agent identity, purpose or ancestry.
SUBAGENT_TRACE_KIND = "subagent_trace"
AGENT_SEQUENCE_KINDS = frozenset({
    "agent_trace",
    SUBAGENT_TRACE_KIND,
})
# The trace mark of what a Provider session's context began with.
CONTEXT_SEED_KIND = "context_seed"
# A reminder of the Harness loop governor that reached a running turn.
HARNESS_CONTROL_KIND = "harness_control"
# Historical Provider window facts remain readable in existing traces: its
# reminders, attached files, base instructions, settings and the model's own
# tool calls.  A session's context holds them; carried to another Provider
# (a Fork, a seed) they stay behind.
PROVIDER_INJECTED_KIND = "provider_injected"
PROVIDER_TOOL_CALL_KIND = "provider_tool_call"
PROVIDER_ONLY_KINDS = frozenset({PROVIDER_INJECTED_KIND, PROVIDER_TOOL_CALL_KIND})
SESSION_INPUT_ACCEPTANCE_SCHEMA = "tfi.session-input-acceptance.v1"
AGENT_DATA_TABLES = frozenset({
    "agent_operations", "operation_positions",
    "agent_store_meta",
    "payloads",
    "segments",
    "segment_relations",
    "sequences",
    "sequence_segments",
    "agents",
    "provider_sessions",
    "provider_bindings",
    "executions", "execution_inputs",
    # The message projection every Chat read uses (chat_position_index).
    "chat_message_positions", "chat_actor_activity",
})
AGENT_DATA_COLUMNS = {
    "chat_message_positions": chat_position_index.POSITION_COLUMNS,
    "chat_actor_activity": chat_position_index.ACTIVITY_COLUMNS,
    "execution_inputs": execution_inputs.COLUMNS,
    "agent_operations": ("operation_id", "kind", "agent_id", "session_ref", "created_at", "data_json"),
    "operation_positions": ("sequence_id", "operation_id", "ordinal"),
    "agent_store_meta": ("key", "value"),
    "payloads": (
        "payload_ref", "sha256", "byte_length", "content_type", "body",
        "created_at",
    ),
    "segments": (
        "segment_id", "segment_type", "started_at", "finished_at",
        "status", "created_by_actor_ref", "performed_by_actor_ref", "content_ref",
        "content_sha256", "content_type", "metadata_json",
        "created_by_agent_id", "execution_id",
    ),
    "segment_relations": (
        "relation_id", "relation_type", "source_segment_id", "target_segment_id",
        "operation_id", "operation_type", "operation_version", "created_at",
        "actor_ref", "metadata_json",
    ),
    "sequences": (
        "sequence_id", "created_at", "updated_at",
        "created_by_actor_ref", "updated_by_actor_ref",
    ),
    "sequence_segments": ("sequence_id", "ordinal", "segment_id"),
    "agents": ("agent_id", "trace_sequence_id", "updated_at", "sequence_kind", "parent_agent_id"),
    "provider_sessions": (
        "provider_session_ref", "provider", "backend_route_id",
        "backend_session_id", "created_at", "updated_at",
    ),
    "provider_bindings": (
        "binding_id", "provider_session_ref", "context_fingerprint",
        "context_injected_at", "context_injection_count", "harness_tool_names_json",
        "created_at", "updated_at",
    ),
    "executions": (
        "execution_id", "agent_id", "actor_ref", "provider",
        "provider_session_ref", "provider_turn_id",
        "started_at", "finished_at", "status", "usage_json", "final_message_id", "created_at",
    ),
}
# A Chat message is the trace Segment that carries it: its metadata holds the
# message (``{"message": {...}}``).  An Agent's Chat is the messages of its
# trace after the trace's last context reset, in trace order.
# Chat reads read the trace's message projection (chat_position_index); the
# queries that find messages in Segments use this exact predicate, so that the
# partial indexes below apply; the statistics objects size their lookups.
# An input a User typed, by the metadata of its Segment: a Resident's delivery,
# a Mailbox group or a service notice is an input too but names another sender.
# Messages from before sender records name none.
TYPED_BY_USER = "COALESCE(({metadata})::jsonb #>> '{{message,origin,sender,kind}}', 'user') = 'user'"
# The Segments carrying the given messages and those a group or Provider input
# made of them, ending in a SELECT over ``transforms`` (message_id, node).
_GROUPED_INPUTS = (
    "WITH RECURSIVE transforms(message_id,node) AS ("
    "SELECT metadata_json::jsonb#>>'{message,message_id}',segment_id FROM segments "
    f"WHERE {CHAT_PREDICATE} AND metadata_json::jsonb#>>'{{message,message_id}}'=ANY(%s) "
    "UNION SELECT transforms.message_id,relation.target_segment_id FROM transforms "
    "JOIN LATERAL (SELECT target_segment_id FROM segment_relations relation "
    "WHERE relation.source_segment_id=transforms.node "
    "AND relation.operation_type IN ('message.group','provider_input.compose','provider_input.append','agent_harness.prepare_invocation') "
    "OFFSET 0) relation ON TRUE) "
)
# A projected message Chat shows: every one but a final answer Segment
# repeating a message that an earlier Segment of its trace carries.
_SHOWN_POSITION = (
    "NOT (position.source_kind='segment' AND position.message_kind='final_answer' AND EXISTS ("
    "SELECT 1 FROM chat_message_positions earlier WHERE earlier.sequence_id=position.sequence_id "
    "AND earlier.source_kind='segment' AND earlier.message_id=position.message_id "
    "AND earlier.ordinal<position.ordinal))"
)
# A projected message's metadata, read from its Segment or its notice.
_POSITION_METADATA = (
    "CASE WHEN position.source_kind='segment' "
    "THEN (SELECT metadata_json::jsonb FROM segments WHERE segment_id=position.source_id) "
    "ELSE (SELECT data_json::jsonb->'metadata' FROM agent_operations WHERE operation_id=position.source_id) END"
)
# The row Chat reads build a message from (``_chat_from_rows``).
_POSITION_ROW = (
    "position.ordinal, position.source_id AS segment_id, position.segment_type, position.created_at, "
    f"position.actor_ref AS created_by_actor_ref, {_POSITION_METADATA} AS metadata_json"
)
# Whether the message has a body: its Segment's payload, or the notice's text.
_POSITION_HAS_CONTENT = (
    "CASE WHEN position.source_kind='segment' THEN COALESCE((SELECT payload.byte_length "
    "FROM segments segment JOIN payloads payload ON payload.payload_ref=segment.content_ref "
    "WHERE segment.segment_id=position.source_id), 0) > 0 "
    "ELSE COALESCE((SELECT octet_length(convert_to(data_json::jsonb->>'content','UTF8')) "
    "FROM agent_operations WHERE operation_id=position.source_id), 0) > 0 END"
)
SESSION_TURN_INDEX_SQL = (
    "CREATE INDEX executions_session_turn_idx ON executions("
    "provider_session_ref, started_at, execution_id, agent_id, provider_turn_id) "
    "WHERE provider_session_ref IS NOT NULL"
)
# The queries that find messages among the Segments themselves.
CHAT_READ_INDEX_SQL = {
    # Whether a trace member is a Chat message, without reading its row: the
    # projection (chat_position_index) skips a trace's other Segments.
    "segments_chat_idx": (
        "CREATE INDEX segments_chat_idx ON segments(segment_id) "
        f"INCLUDE (segment_type, started_at, finished_at, created_by_actor_ref) WHERE {CHAT_PREDICATE}"
    ),
    # A message by its identity: groups, transformations and repeated finals.
    "segments_message_idx": (
        "CREATE INDEX segments_message_idx ON segments("
        f"(metadata_json::jsonb #>> '{{message,message_id}}')) WHERE {CHAT_PREDICATE}"
    ),
}
CHAT_STATISTICS_SQL = {
    "segments_chat_stats": (
        "CREATE STATISTICS segments_chat_stats (mcv) ON "
        "(jsonb_typeof(metadata_json::jsonb -> 'message')), segment_type FROM segments"
    ),
    # PostgreSQL keeps no statistics for the expressions of partial indexes.
    # Without these, a lookup by message ID is estimated at thousands of rows
    # and the grouped-input recursion sizes its UNION for millions.
    "segments_message_stats": (
        "CREATE STATISTICS segments_message_stats ON "
        "(metadata_json::jsonb #>> '{message,message_id}') FROM segments"
    ),
}
CHAT_COUNT_SQL = (
    "SELECT COUNT(*) AS count FROM chat_message_positions position "
    "WHERE position.sequence_id=%s AND position.source_kind<>'sync' AND position.ordinal<=%s "
    f"AND {_SHOWN_POSITION}"
)
AGENT_DATA_INDEXES = frozenset({
    "agent_operations_session_idx", "operations_context_reset_idx", "operation_positions_order_idx",
    "operation_positions_operation_idx",
    "agents_parent_idx",
    "segments_execution_idx",
    "segment_relations_source_idx",
    "segment_relations_target_idx",
    "provider_sessions_lookup_idx",
    "executions_agent_time_idx", "executions_open_idx", "execution_inputs_message_idx",
    "executions_session_turn_idx",
    *CHAT_READ_INDEX_SQL,
    *chat_position_index.INDEX_SQL,
})


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _later_than(value: str, boundary: str) -> bool:
    """Whether an ISO timestamp is after another, whatever their precision."""

    try:
        return datetime.fromisoformat(value) > datetime.fromisoformat(boundary)
    except (TypeError, ValueError):
        return value > boundary




PROVIDER_TURN_SQL = (
    "CASE WHEN segment.metadata_json::jsonb->>'kind'='appended_provider_input' "
    "THEN segment.metadata_json::jsonb->>'provider_turn_id' ELSE execution.provider_turn_id END"
)

CONTEXT_EVIDENCE_POSITION_LIMIT = 2000


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _document(value: object) -> str:
    """``canonical_json`` text for a JSON column (see json_columns)."""
    return json_text(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _identifier(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(12)}"


def stable_segment_id(*parts: object) -> str:
    digest = hashlib.sha256(
        "\0".join(str(part or "") for part in parts).encode("utf-8")
    ).hexdigest()[:32]
    return f"segment_{digest}"


def chat_segment_id(agent_id: object, message_id: object) -> str:
    return stable_segment_id("chat", agent_id, message_id)


def _content_bytes(content: object, content_type: str) -> bytes:
    if isinstance(content, bytes):
        return content
    if content_type == "application/json" or not isinstance(content, str):
        return canonical_json(content).encode("utf-8")
    return content.encode("utf-8")


def _decode_content(raw: bytes, content_type: str) -> Any:
    text = raw.decode("utf-8", errors="replace")
    if content_type == "application/json":
        return json.loads(text)
    return text


def _message_role_segment_type(role: str) -> str:
    clean = str(role or "").strip().casefold()
    if clean in {"user", "human"}:
        return "input"
    if clean in {"assistant", "agent", "solver", "error"}:
        return "model"
    return "harness"


def _chat_role(value: object) -> str:
    role = str(value or "").strip().casefold()
    if role in {"assistant", "agent"}:
        return "agent"
    if role in {"user", "human"}:
        return "user"
    if role in {"system", "portal", "error"}:
        return "system"
    raise ValueError(f"invalid Resident Message role: {role or '(missing)'}")


def _chat_message_text(message: Mapping[str, Any]) -> str:
    return "\n".join(
        str(part.get("text") or "")
        for part in message.get("content_parts") or []
        if isinstance(part, Mapping) and str(part.get("type") or "") == "text"
    )


def _answer_text(agent_id: str, message: Mapping[str, Any]) -> str:
    """The text of an Agent reply once its ``portal-question`` fences became its question card.

    The delivery decodes the fences; the captured Segment keeps them.
    """
    from management_common.async_questions import decode_question_content

    parts = [dict(part) for part in message.get("content_parts") or [] if isinstance(part, Mapping)]
    return _chat_message_text({
        "content_parts": decode_question_content(parts, agent_id, str(message.get("message_id") or "")),
    })


def _canonical_chat_message(message: Mapping[str, Any]) -> dict[str, Any]:
    from management_common.principal import message_attribution, message_quote, message_recipients

    message_id = str(message.get("message_id") or "").strip()
    if not message_id:
        raise ValueError("Resident Message identity is required")
    role = _chat_role(message.get("role"))
    raw_parts = message.get("content_parts")
    if not isinstance(raw_parts, list):
        raise ValueError("Resident Message content_parts are required")
    result: dict[str, Any] = {
        "message_id": message_id,
        "role": role,
        "content_parts": [
            dict(part) for part in raw_parts if isinstance(part, Mapping)
        ],
        "created_at": str(message.get("created_at") or ""),
    }
    activated_at = str(message.get("activated_at") or "").strip()
    if activated_at:
        result["activated_at"] = activated_at
    result.update(message_attribution(message))
    reply_to = str(message.get("reply_to_message_id") or "").strip()
    if role != "user" and reply_to:
        result["reply_to_message_id"] = reply_to
    recipients = message_recipients(message)
    if recipients:
        result["recipients"] = recipients
    quote = message_quote(message) if role == "user" else {}
    if quote:
        result["quote"] = quote
    usage = message.get("usage")
    if isinstance(usage, Mapping) and usage:
        result["usage"] = dict(usage)
    for key in (
        "provider_response",
        "execution_id",
        "provider_turn_id",
        "business_ref",
        # A held info is delivered with a message; a group member as part of its group.
        "released_with",
        "grouped_into_message_id",
        # An input a Harness gave its Provider on its own (a recovery's re-ask or continue).
        "harness_input",
        "edited_at",
        "retracted_at",
    ):
        value = message.get(key)
        if value not in (None, "", {}):
            result[key] = value
    return result


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


_FIELD_NOT_PROVIDED = object()


def _batches(values: Sequence[Any], size: int = 500) -> Iterable[Sequence[Any]]:
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


def agent_data_schema_matches(connection: Any) -> bool:
    tables = {
        str(row["table_name"])
        for row in connection.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema=current_schema() AND table_type='BASE TABLE'"
        ).fetchall()
    }
    if tables != AGENT_DATA_TABLES:
        return False
    for table, expected in AGENT_DATA_COLUMNS.items():
        actual = tuple(
            str(row["column_name"])
            for row in connection.execute(
                'SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=%s ORDER BY ordinal_position',
                (table,),
            ).fetchall()
        )
        if actual != expected:
            return False
    indexes = {
        str(row["indexname"])
        for row in connection.execute(
            "SELECT indexname FROM pg_indexes WHERE schemaname=current_schema()"
        ).fetchall()
        if not str(row["indexname"]).endswith("_pkey")
        and not str(row["indexname"]).endswith("_key")
    }
    return AGENT_DATA_INDEXES <= indexes


class SegmentIdentityConflict(ValueError):
    def __init__(self, segment_id: str) -> None:
        self.segment_id = segment_id
        super().__init__(f"Segment identity refers to different immutable content: {segment_id}")


class AgentDataStore:
    """Canonical Agent facts and their current ordered projections.

    Segments are immutable facts.  A Sequence is one mutable ordered set of
    Segment references.  Executions own provider-call state, while Chat is a
    current display projection.  Transport progress and Sequence edit history
    are outside this store.
    """

    def __init__(self) -> None:
        self.database = portal_database(schema="agent")
        self.database_identity = self.database.identity
        self.database.write(self._initialize)
        self._conversation_summaries_lock = threading.Lock()
        self._conversation_summaries_cache: OrderedDict[
            tuple[str, str, str], tuple[tuple[str, str], float, dict[str, Any]]
        ] = OrderedDict()

    @staticmethod
    def _initialize(connection: Any) -> None:
        connection.execute(
            "SELECT pg_advisory_xact_lock("
            "hashtextextended(current_schema() || ':schema-init', 0))"
        )
        current = connection.execute(
            "SELECT to_regclass(current_schema() || '.agent_store_meta')"
        ).fetchone()
        current = current if current and current[0] is not None else None
        if current is not None:
            schema = connection.execute(
                "SELECT value FROM agent_store_meta WHERE key = 'schema'"
            ).fetchone()
            if (
                schema is None
                or not schema_matches(schema["value"], "tfi.agent-data.v18")
                or not agent_data_schema_matches(connection)
            ):
                raise RuntimeError("Agent data database does not match its schema")
            return

        connection.executescript(
            """
            CREATE TABLE agent_store_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE payloads (
                payload_ref TEXT PRIMARY KEY,
                sha256 TEXT NOT NULL UNIQUE,
                byte_length BIGINT NOT NULL,
                content_type TEXT NOT NULL,
                body BYTEA NOT NULL,
                created_at TIMESTAMPTZ NOT NULL
            );

            CREATE TABLE segments (
                segment_id TEXT PRIMARY KEY,
                segment_type TEXT NOT NULL CHECK (
                    segment_type IN ('input','model','tool','compaction','harness')
                ),
                started_at TIMESTAMPTZ,
                finished_at TIMESTAMPTZ,
                status TEXT NOT NULL,
                created_by_actor_ref TEXT NOT NULL,
                performed_by_actor_ref TEXT NOT NULL,
                content_ref TEXT NOT NULL,
                content_sha256 TEXT NOT NULL,
                content_type TEXT NOT NULL,
                metadata_json JSONB NOT NULL,
                created_by_agent_id TEXT NOT NULL,
                execution_id TEXT NOT NULL,
                FOREIGN KEY (content_ref) REFERENCES payloads(payload_ref)
            );
            CREATE INDEX segments_execution_idx
                ON segments(execution_id, finished_at, segment_id)
                WHERE execution_id <> '';
            CREATE TABLE segment_relations (
                relation_id TEXT PRIMARY KEY,
                relation_type TEXT NOT NULL CHECK (
                    relation_type IN (
                        'derived_from','transformed_from','compressed_from',
                        'extracted_from','merged_from'
                    )
                ),
                source_segment_id TEXT NOT NULL,
                target_segment_id TEXT NOT NULL,
                operation_id TEXT NOT NULL,
                operation_type TEXT NOT NULL,
                operation_version TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                actor_ref TEXT NOT NULL,
                metadata_json JSONB NOT NULL,
                UNIQUE(relation_type, source_segment_id, target_segment_id, operation_id),
                FOREIGN KEY (source_segment_id) REFERENCES segments(segment_id),
                FOREIGN KEY (target_segment_id) REFERENCES segments(segment_id)
            );
            CREATE INDEX segment_relations_source_idx
                ON segment_relations(source_segment_id, created_at);
            CREATE INDEX segment_relations_target_idx
                ON segment_relations(target_segment_id, created_at);

            CREATE TABLE sequences (
                sequence_id TEXT PRIMARY KEY,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL,
                created_by_actor_ref TEXT NOT NULL,
                updated_by_actor_ref TEXT NOT NULL
            );

            CREATE TABLE sequence_segments (
                sequence_id TEXT NOT NULL,
                ordinal BIGINT NOT NULL CHECK (ordinal > 0),
                segment_id TEXT NOT NULL,
                PRIMARY KEY(sequence_id, ordinal),
                UNIQUE(segment_id, sequence_id),
                FOREIGN KEY (sequence_id) REFERENCES sequences(sequence_id) ON DELETE CASCADE,
                FOREIGN KEY (segment_id) REFERENCES segments(segment_id)
            );

            CREATE TABLE agents (
                agent_id TEXT PRIMARY KEY,
                trace_sequence_id TEXT NOT NULL UNIQUE,
                updated_at TIMESTAMPTZ NOT NULL,
                sequence_kind TEXT NOT NULL CHECK (sequence_kind IN ('agent_trace','subagent_trace')),
                parent_agent_id TEXT REFERENCES agents(agent_id),
                CHECK (parent_agent_id IS NULL OR parent_agent_id <> agent_id),
                CHECK (sequence_kind = 'subagent_trace' OR parent_agent_id IS NULL),
                FOREIGN KEY (trace_sequence_id) REFERENCES sequences(sequence_id)
            );

            CREATE INDEX agents_parent_idx ON agents(parent_agent_id);

            CREATE TABLE provider_sessions (
                provider_session_ref TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                backend_route_id TEXT NOT NULL,
                backend_session_id TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL,
                UNIQUE(provider, backend_route_id, backend_session_id)
            );
            CREATE INDEX provider_sessions_lookup_idx
                ON provider_sessions(provider, backend_session_id, updated_at DESC);

            CREATE TABLE provider_bindings (
                binding_id TEXT PRIMARY KEY,
                provider_session_ref TEXT NOT NULL UNIQUE,
                context_fingerprint TEXT NOT NULL,
                context_injected_at TIMESTAMPTZ,
                context_injection_count BIGINT NOT NULL,
                harness_tool_names_json JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL,
                FOREIGN KEY (provider_session_ref) REFERENCES provider_sessions(provider_session_ref)
            );

            CREATE TABLE executions (
                execution_id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                actor_ref TEXT NOT NULL,
                provider TEXT NOT NULL,
                provider_session_ref TEXT,
                provider_turn_id TEXT NOT NULL,
                started_at TIMESTAMPTZ,
                finished_at TIMESTAMPTZ,
                status TEXT NOT NULL CHECK (
                    status IN ('preparing','queued','running','completed','failed','cancelled')
                ),
                usage_json JSONB NOT NULL,
                final_message_id TEXT,
                created_at TIMESTAMPTZ NOT NULL,
                FOREIGN KEY (provider_session_ref) REFERENCES provider_sessions(provider_session_ref)
            );
            CREATE INDEX executions_agent_time_idx
                ON executions(agent_id, created_at, execution_id);
            -- The few Executions not over yet: Lineage handoffs still pending.
            CREATE INDEX executions_open_idx ON executions(execution_id)
                WHERE status IN ('preparing','queued','running');
            """
        )
        connection.executescript(TABLE_SQL)
        connection.executescript(execution_inputs.DDL)
        chat_position_index.create(connection)
        connection.execute(SESSION_TURN_INDEX_SQL)
        for sql in (*CHAT_READ_INDEX_SQL.values(), *CHAT_STATISTICS_SQL.values()):
            connection.execute(sql)
        connection.execute(
            "INSERT INTO agent_store_meta(key, value) VALUES ('schema', %s)",
            (AGENT_DATA_SCHEMA,),
        )

    @staticmethod
    def _payload(
        connection: Any,
        raw: bytes,
        content_type: str,
        created_at: str,
    ) -> tuple[str, str]:
        from .harness_retention import protect_artifact_references
        protect_artifact_references(connection, raw)
        sha256 = hashlib.sha256(raw).hexdigest()
        payload_ref = f"payload_{sha256}"
        connection.execute(
            '\n            INSERT INTO payloads(\n                payload_ref, sha256, byte_length, content_type, body, created_at\n            ) VALUES (%s, %s, %s, %s, %s, %s)\n            ON CONFLICT DO NOTHING\n            ',
            (payload_ref, sha256, len(raw), content_type, raw, created_at),
        )
        return payload_ref, sha256

    @staticmethod
    def _segment_row_payload(row: Any, content: Any) -> dict[str, Any]:
        return {
            "segment_id": str(row["segment_id"]),
            "segment_type": str(row["segment_type"]),
            "started_at": (iso_time(row["started_at"]) or ""),
            "finished_at": (iso_time(row["finished_at"]) or ""),
            "status": str(row["status"]),
            "created_by_actor_ref": str(row["created_by_actor_ref"]),
            "performed_by_actor_ref": str(row["performed_by_actor_ref"]),
            "content": content,
            "content_ref": str(row["content_ref"]),
            "content_sha256": str(row["content_sha256"]),
            "content_type": str(row["content_type"]),
            "metadata": json_value(row["metadata_json"] or "{}"),
            "created_by_agent_id": str(row["created_by_agent_id"]),
            "execution_id": str(row["execution_id"]),
        }

    @staticmethod
    def _insert_segment(
        connection: Any,
        *,
        segment_id: str,
        segment_type: str,
        content: object,
        started_at: str,
        finished_at: str,
        status: str,
        created_by_actor_ref: str,
        performed_by_actor_ref: str,
        content_type: str,
        metadata: Mapping[str, Any],
        created_by_agent_id: str,
        execution_id: str,
    ) -> str:
        if segment_type not in SEGMENT_TYPES:
            raise ValueError(f"invalid Segment type: {segment_type}")
        clean_id = str(segment_id or "").strip()
        if not clean_id:
            raise ValueError("Segment identity is required")
        at = str(finished_at or started_at or utc_now())
        clean_content_type = str(content_type or "text/plain; charset=utf-8")
        raw = _content_bytes(content, clean_content_type)
        payload_ref, sha256 = AgentDataStore._payload(
            connection, raw, clean_content_type, at
        )
        forbidden = {"hidden_from_chat", "show_in_chat", "send_to_ai", "native_id"}
        if forbidden.intersection(metadata):
            raise ValueError("Segment metadata cannot store consumer eligibility or transport aliases")
        metadata_json = _document(dict(metadata))
        existing = connection.execute(
            'SELECT * FROM segments WHERE segment_id = %s', (clean_id,)
        ).fetchone()
        if existing is not None:
            content_identity = (
                str(existing["segment_type"]),
                str(existing["content_ref"]),
                str(existing["content_type"]),
                json_value(existing["metadata_json"]),
            )
            if content_identity != (
                segment_type,
                payload_ref,
                clean_content_type,
                json_value(metadata_json),
            ):
                raise SegmentIdentityConflict(clean_id)
            return clean_id
        connection.execute(
            '\n            INSERT INTO segments(\n                segment_id, segment_type, started_at, finished_at,\n                status, created_by_actor_ref, performed_by_actor_ref,\n                content_ref, content_sha256, content_type,\n                metadata_json, created_by_agent_id, execution_id\n            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)\n            ',
            (
                clean_id,
                segment_type,
                instant(str(started_at or "")),
                instant(str(finished_at or "")),
                str(status or "completed"),
                str(created_by_actor_ref or ""),
                str(performed_by_actor_ref or ""),
                payload_ref,
                sha256,
                clean_content_type,
                metadata_json,
                str(created_by_agent_id or ""),
                str(execution_id or ""),
            ),
        )
        return clean_id

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
        resolved_id = str(segment_id or _identifier("segment"))
        return self.database.write(
            lambda connection: self._insert_segment(
                connection,
                segment_id=resolved_id,
                segment_type=segment_type,
                content=content,
                started_at=started_at,
                finished_at=finished_at,
                status=status,
                created_by_actor_ref=created_by_actor_ref,
                performed_by_actor_ref=performed_by_actor_ref,
                content_type=content_type,
                metadata=metadata or {},
                created_by_agent_id=created_by_agent_id,
                execution_id=execution_id,
            )
        )

    @staticmethod
    def _read_segment_row(
        connection: Any,
        segment_id: str,
    ) -> dict[str, Any]:
        row = connection.execute(
            '\n            SELECT segment.*, payload.body AS payload_body\n            FROM segments AS segment\n            JOIN payloads AS payload ON payload.payload_ref = segment.content_ref\n            WHERE segment.segment_id = %s\n            ',
            (segment_id,),
        ).fetchone()
        if row is None:
            raise KeyError(segment_id)
        raw = bytes(row["payload_body"])
        return AgentDataStore._segment_row_payload(
            row, _decode_content(raw, str(row["content_type"]))
        )

    @staticmethod
    def _read_segment_rows(
        connection: Any,
        segment_ids: Sequence[str],
    ) -> list[dict[str, Any]]:
        requested = list(dict.fromkeys(
            str(item or "").strip()
            for item in segment_ids
            if str(item or "").strip()
        ))
        if not requested:
            return []
        found: dict[str, dict[str, Any]] = {}
        for chunk in _batches(requested):
            placeholders = ",".join("%s" for _ in chunk)
            for row in connection.execute(
                f"SELECT segment.*, payload.body AS payload_body "
                f"FROM segments AS segment JOIN payloads AS payload "
                f"ON payload.payload_ref = segment.content_ref "
                f"WHERE segment.segment_id IN ({placeholders})",
                chunk,
            ).fetchall():
                raw = bytes(row["payload_body"])
                found[str(row["segment_id"])] = AgentDataStore._segment_row_payload(
                    row,
                    _decode_content(raw, str(row["content_type"])),
                )
        missing = next((item for item in requested if item not in found), "")
        if missing:
            raise KeyError(missing)
        return [found[item] for item in requested]

    @staticmethod
    def _existing_segment_ids(
        connection: Any,
        segment_ids: Sequence[str],
    ) -> set[str]:
        found: set[str] = set()
        for chunk in _batches(segment_ids):
            placeholders = ",".join("%s" for _ in chunk)
            found.update(
                str(row["segment_id"])
                for row in connection.execute(
                    f"SELECT segment_id FROM segments "
                    f"WHERE segment_id IN ({placeholders})",
                    chunk,
                ).fetchall()
            )
        return found

    def has_segment(self, segment_id: str) -> bool:
        """Whether an identity names captured content, rather than an operation."""
        with self.database.read() as connection:
            return bool(connection.execute(
                "SELECT 1 FROM segments WHERE segment_id=%s", (segment_id,),
            ).fetchone())

    def segment(self, segment_id: str) -> dict[str, Any]:
        with self.database.read() as connection:
            return self._read_segment_row(connection, str(segment_id or ""))


    def add_relations(
        self,
        relations: Sequence[Mapping[str, Any]],
    ) -> list[str]:
        normalized: list[dict[str, Any]] = []
        for relation in relations:
            relation_type = str(relation.get("relation_type") or "")
            if relation_type not in RELATION_TYPES:
                raise ValueError(f"invalid Segment relation: {relation_type}")
            source = str(relation.get("source_segment_id") or "")
            target = str(relation.get("target_segment_id") or "")
            operation_id = str(relation.get("operation_id") or "")
            if not source or not target:
                raise ValueError("Segment relation endpoints are required")
            digest = hashlib.sha256(
                f"{relation_type}\0{source}\0{target}\0{operation_id}".encode("utf-8")
            ).hexdigest()[:32]
            normalized.append({
                "relation_id": f"relation_{digest}",
                "relation_type": relation_type,
                "source_segment_id": source,
                "target_segment_id": target,
                "operation_id": operation_id,
                "operation_type": str(relation.get("operation_type") or ""),
                "operation_version": str(relation.get("operation_version") or ""),
                "created_at": str(relation.get("created_at") or utc_now()),
                "actor_ref": str(relation.get("actor_ref") or ""),
                "metadata_json": _document(dict(relation.get("metadata") or {})),
            })

        def write(connection: Any) -> list[str]:
            connection.executemany(
                '\n                INSERT INTO segment_relations(\n                    relation_id, relation_type, source_segment_id,\n                    target_segment_id, operation_id, operation_type,\n                    operation_version, created_at, actor_ref, metadata_json\n                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)\n                ON CONFLICT DO NOTHING\n                ',
                [tuple(item[key] for key in (
                    "relation_id", "relation_type", "source_segment_id",
                    "target_segment_id", "operation_id", "operation_type",
                    "operation_version", "created_at", "actor_ref", "metadata_json",
                )) for item in normalized],
            )
            return [str(item["relation_id"]) for item in normalized]

        return self.database.write(write) if normalized else []

    @staticmethod
    def _sequence_row(connection: Any, sequence_id: str, *, lock: bool = False) -> Any | None:
        return connection.execute(
            'SELECT * FROM sequences WHERE sequence_id = %s' + (' FOR UPDATE' if lock else ''),
            (sequence_id,),
        ).fetchone()

    @classmethod
    def _create_sequence(cls, connection: Any, actor_ref: str) -> Any:
        sequence_id, at = _identifier("sequence"), utc_now()
        connection.execute(
            'INSERT INTO sequences(sequence_id, created_at, updated_at, created_by_actor_ref, updated_by_actor_ref) '
            'VALUES (%s, %s, %s, %s, %s)',
            (sequence_id, at, at, actor_ref, actor_ref),
        )
        # An empty Sequence is fully projected; its appends keep it current.
        chat_position_index.mark(connection, sequence_id)
        return cls._sequence_row(connection, sequence_id)


    @classmethod
    def _agent_trace(cls, connection: Any, agent_id: str) -> Any | None:
        return cls._sequence_row(connection, cls._trace_id(connection, agent_id))

    @staticmethod
    def _validated_agent_id(agent_id: str, sequence_kind: str, parent_agent_id: str | None) -> str:
        agent_id = str(agent_id or "").strip()
        if not agent_id or sequence_kind not in AGENT_SEQUENCE_KINDS:
            raise ValueError("Agent requires an identity and a valid trace purpose")
        if parent_agent_id == agent_id or (parent_agent_id and sequence_kind != SUBAGENT_TRACE_KIND):
            raise ValueError("Invalid Agent parent")
        return agent_id

    @staticmethod
    def _needs_agent_parent_update(row: Any, sequence_kind: str, parent_agent_id: str | None) -> bool:
        if row["sequence_kind"] != sequence_kind:
            raise ValueError("Agent identity refers to different trace purpose or parent")
        if row["parent_agent_id"] == parent_agent_id:
            return False
        # Child traces may lack evidence of their direct parent. Only fill an
        # unknown edge; never replace a known one.
        if row["parent_agent_id"] is not None or parent_agent_id is None:
            raise ValueError("Agent identity refers to different trace purpose or parent")
        return True

    @classmethod
    def _ensure_agent(cls, connection: Any, agent_id: str, *, actor_ref: str,
                      sequence_kind: str = "agent_trace", parent_agent_id: str | None = None) -> Any:
        agent_id = cls._validated_agent_id(agent_id, sequence_kind, parent_agent_id)
        connection.execute("SELECT pg_advisory_xact_lock(hashtextextended('agent:' || %s, 0))", (agent_id,))
        row = connection.execute('SELECT * FROM agents WHERE agent_id = %s', (agent_id,)).fetchone()
        if row is not None:
            if cls._needs_agent_parent_update(row, sequence_kind, parent_agent_id):
                connection.execute("SELECT pg_advisory_xact_lock(hashtextextended('agent-parent-edges', 0))")
                cycle = connection.execute(
                    'WITH RECURSIVE ancestors AS ('
                    'SELECT agent_id, parent_agent_id FROM agents WHERE agent_id=%s UNION '
                    'SELECT a.agent_id, a.parent_agent_id FROM agents a JOIN ancestors p ON a.agent_id=p.parent_agent_id) '
                    'SELECT 1 FROM ancestors WHERE agent_id=%s', (parent_agent_id, agent_id),
                ).fetchone()
                if cycle:
                    raise ValueError("Agent parent would form a cycle")
                connection.execute('UPDATE agents SET parent_agent_id=%s, updated_at=%s WHERE agent_id=%s',
                                   (parent_agent_id, utc_now(), agent_id))
                row = connection.execute('SELECT * FROM agents WHERE agent_id=%s', (agent_id,)).fetchone()
            return row
        trace = cls._create_sequence(connection, actor_ref)
        connection.execute(
            'INSERT INTO agents(agent_id, trace_sequence_id, updated_at, sequence_kind, parent_agent_id) '
            'VALUES (%s, %s, %s, %s, %s)',
            (agent_id, trace["sequence_id"], trace["updated_at"], sequence_kind, parent_agent_id),
        )
        return connection.execute('SELECT * FROM agents WHERE agent_id = %s', (agent_id,)).fetchone()

    def ensure_agent(self, agent_id: str, *, actor_ref: str = "", sequence_kind: str = "agent_trace",
                     parent_agent_id: str | None = None) -> dict[str, Any]:
        agent_id = self._validated_agent_id(agent_id, sequence_kind, parent_agent_id)
        with self.database.read() as connection:
            row = connection.execute('SELECT * FROM agents WHERE agent_id = %s', (agent_id,)).fetchone()
        if row is not None:
            if not self._needs_agent_parent_update(row, sequence_kind, parent_agent_id):
                return decode_times(row, ("created_at", "updated_at"))
            # A newly learned parent edge must be checked and stored atomically.
        return decode_times(self.database.write(lambda c: self._ensure_agent(
            c, agent_id, actor_ref=actor_ref, sequence_kind=sequence_kind, parent_agent_id=parent_agent_id,
        )), ("updated_at",))

    def agent(self, agent_id: str) -> dict[str, Any]:
        with self.database.read() as c:
            row = c.execute('SELECT * FROM agents WHERE agent_id = %s', (agent_id,)).fetchone()
            return decode_times(row, ("created_at", "updated_at")) if row is not None else {}

    def trace_sequence_id(self, agent_id: str) -> str:
        with self.database.read() as c:
            return self._trace_id(c, agent_id)

    @classmethod
    def _ensure_agent_trace(cls, connection: Any, agent_id: str, *, actor_ref: str) -> Any:
        row = cls._ensure_agent(connection, agent_id, actor_ref=actor_ref)
        return cls._sequence_row(connection, row["trace_sequence_id"])

    def append_agent_segments(self, agent_id: str, segment_ids: Iterable[str], *, actor_ref: str) -> dict[str, Any]:
        requested = list(segment_ids)
        def write(c: Any) -> dict[str, Any]:
            agent = c.execute('SELECT * FROM agents WHERE agent_id = %s', (agent_id,)).fetchone()
            if agent is None:
                agent = self._ensure_agent(c, agent_id, actor_ref=actor_ref)
            sequence_id = agent["trace_sequence_id"]
            c.execute("SELECT 1 FROM sequences WHERE sequence_id=%s FOR UPDATE", (sequence_id,))
            # A UI snapshot can arrive before the completed provider event.
            # Replace that projection position, never append the text twice.
            notices = c.execute("""SELECT position.operation_id,position.ordinal,segment.segment_id
                FROM operation_positions position JOIN agent_operations operation USING(operation_id)
                JOIN segments segment ON segment.segment_id=ANY(%s)
                  AND segment.metadata_json::jsonb#>>'{message,message_id}'=operation.data_json::jsonb#>>'{metadata,message,message_id}'
                WHERE position.sequence_id=%s AND operation.kind='notice'""", (requested,sequence_id)).fetchall()
            result = self._append_segments(c, sequence_id, requested, actor_ref)
            for notice in notices:
                c.execute("""INSERT INTO agent_operations
                    SELECT %s,kind,agent_id,session_ref,created_at,data_json FROM agent_operations
                    WHERE operation_id=%s ON CONFLICT DO NOTHING""",
                    ("message_artifact:" + notice["segment_id"], "message_artifact:" + notice["operation_id"]))
                c.execute("DELETE FROM operation_positions WHERE sequence_id=%s AND operation_id=%s", (sequence_id,notice["operation_id"]))
                c.execute("UPDATE sequence_segments SET ordinal=%s WHERE sequence_id=%s AND segment_id=%s", (notice["ordinal"],sequence_id,notice["segment_id"]))
            if notices:
                chat_position_index.refresh_sequence(c, sequence_id)
            if requested:
                ended = c.execute(
                    "SELECT DISTINCT segment.execution_id FROM segments segment "
                    "JOIN executions execution USING(execution_id) "
                    "WHERE segment.segment_id=ANY(%s) AND execution.status IN ('completed','failed','cancelled')",
                    (requested,),
                ).fetchall()
                for row in ended:
                    self._refresh_finished_execution_pointer(c, str(row["execution_id"]))
            return result
        return self.database.write(write)

    @staticmethod
    def _sequence_ids(
        connection: Any,
        sequence_id: str,
    ) -> list[str]:
        return [
            str(row["segment_id"])
            for row in connection.execute(
                'SELECT segment_id FROM sequence_segments WHERE sequence_id = %s ORDER BY ordinal',
                (sequence_id,),
            ).fetchall()
        ]

    @staticmethod
    def _sequence_count(
        connection: Any,
        sequence_id: str,
    ) -> int:
        row = connection.execute(
            'SELECT COUNT(*) AS count FROM sequence_segments WHERE sequence_id = %s',
            (sequence_id,),
        ).fetchone()
        return int(row["count"] if row is not None else 0)

    @staticmethod
    def _touch_sequence(
        connection: Any,
        sequence_id: str,
        actor_ref: str,
    ) -> str:
        # A writer that did not keep the projection leaves its mark behind. A
        # later append must not make that projection look current merely by
        # advancing the mark: rebuild it from the locked trace instead.
        was_current = chat_position_index.current(connection, sequence_id)
        at = utc_now()
        connection.execute(
            'UPDATE sequences SET updated_at = %s, updated_by_actor_ref = %s WHERE sequence_id = %s',
            (at, str(actor_ref or ""), sequence_id),
        )
        if was_current:
            chat_position_index.mark(connection, sequence_id)
        else:
            chat_position_index.refresh_sequence(connection, sequence_id)
        return at

    @classmethod
    def _place_operation(cls, connection: Any, sequence_id: str, operation_id: str,
                         position: int, actor_ref: str) -> None:
        connection.execute("INSERT INTO operation_positions VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                           (sequence_id, operation_id, position))
        chat_position_index.upsert_notices(connection, sequence_id, [operation_id])
        cls._touch_sequence(connection, sequence_id, actor_ref)

    @classmethod
    def _append_sequence_ids(
        cls,
        connection: Any,
        *,
        sequence: Any,
        segment_ids: Iterable[str],
        actor_ref: str,
    ) -> list[str]:
        requested = list(dict.fromkeys(
            str(value or "").strip()
            for value in segment_ids
            if str(value or "").strip()
        ))
        if not requested:
            return []
        found = cls._existing_segment_ids(connection, requested)
        missing = [item for item in requested if item not in found]
        if missing:
            raise KeyError(missing[0])
        sequence_id = str(sequence["sequence_id"])
        # MAX(ordinal) + 1 is safe only while this Sequence's parent row is
        # locked. Unrelated Sequences still append in parallel.
        locked = connection.execute(
            'SELECT sequence_id FROM sequences WHERE sequence_id = %s FOR UPDATE',
            (sequence_id,),
        ).fetchone()
        if locked is None:
            raise RuntimeError("Sequence disappeared during append")
        selected: set[str] = set()
        for chunk in _batches(requested):
            placeholders = ",".join("%s" for _ in chunk)
            selected.update(
                str(row["segment_id"])
                for row in connection.execute(
                    f"SELECT segment_id FROM sequence_segments "
                    f"WHERE sequence_id = %s AND segment_id IN ({placeholders})",
                    [sequence_id, *chunk],
                ).fetchall()
            )
        additions = [item for item in requested if item not in selected]
        if not additions:
            return []
        base = next_position(connection, sequence_id) - 1
        connection.executemany(
            'INSERT INTO sequence_segments(sequence_id, ordinal, segment_id) VALUES (%s, %s, %s)',
            [
                (sequence_id, base + index, segment_id)
                for index, segment_id in enumerate(additions, 1)
            ],
        )
        chat_position_index.upsert_segments(connection, sequence_id, additions)
        cls._touch_sequence(connection, sequence_id, actor_ref)
        return additions


    @classmethod
    def _append_segments(cls, connection: Any, sequence_id: str, segment_ids: Iterable[str], actor_ref: str) -> dict[str, Any]:
        sequence = cls._sequence_row(connection, sequence_id, lock=True)
        if sequence is None:
            raise KeyError(sequence_id)
        additions = cls._append_sequence_ids(connection, sequence=sequence, segment_ids=segment_ids, actor_ref=actor_ref)
        current = cls._sequence_row(connection, sequence_id)
        return {"sequence_id": sequence_id, "appended_segment_ids": additions,
                "segment_count": cls._sequence_count(connection, sequence_id), "updated_at": (iso_time(current["updated_at"]) or "")}


    def transform_agent_segments(
        self, agent_id: str, source_ids: Sequence[str], *, segment_id: str,
        content: Any, actor_ref: str, operation_id: str, operation_type: str,
        segment_type: str = "input", content_type: str = "text/plain; charset=utf-8",
        metadata: Mapping[str, Any] | None = None, execution_id: str = "",
        created_at: str = "",
    ) -> str:
        """Atomically replace consumed occurrences, retaining immutable ancestry.

        Only this Agent's sequence changes. A Fork already holding a source keeps
        it. Identical text in another occurrence has no bearing on replacement.
        """
        sources = list(dict.fromkeys(source_ids))
        if not sources:
            raise ValueError("A transformation needs source Segment identities")

        def write(connection: Any) -> str:
            sequence_id = self._locked_trace(connection, agent_id, actor_ref)
            members = connection.execute(
                "SELECT member.ordinal, member.segment_id, segment.content_sha256, "
                "segment.metadata_json::jsonb#>>'{message,message_id}' AS message_id "
                "FROM sequence_segments member JOIN segments segment USING(segment_id) "
                "WHERE member.sequence_id=%s AND member.segment_id=ANY(%s) ORDER BY member.ordinal",
                (sequence_id, sources),
            ).fetchall()
            if not members:
                replay = connection.execute(
                    "SELECT 1 FROM sequence_segments WHERE sequence_id=%s AND segment_id=%s",
                    (sequence_id, segment_id),
                ).fetchone()
                if replay:
                    return segment_id
                raise ValueError("Transformation sources are outside the current trace")
            if len(members) != len(sources):
                raise ValueError("Transformation must consume all its current sources")
            first = self._read_segment_row(connection, members[0]["segment_id"])
            facts = {**first["metadata"], **dict(metadata or {})}
            # Message identity is attached to the occurrence, not its old text.
            at = str(created_at or utc_now())
            self._insert_segment(
                connection, segment_id=segment_id, segment_type=segment_type,
                content=content, content_type=content_type, metadata=facts,
                 started_at=at, finished_at=at,
                status="completed", created_by_actor_ref=first["created_by_actor_ref"],
                performed_by_actor_ref=actor_ref, created_by_agent_id=agent_id,
                execution_id=execution_id or first["execution_id"],
            )
            for source_id in sources:
                relation_id = stable_segment_id("transform", operation_id, source_id, segment_id).replace("segment_", "relation_", 1)
                connection.execute(
                    "INSERT INTO segment_relations VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                    (relation_id, "transformed_from", source_id, segment_id,
                     operation_id, operation_type, "1", at, actor_ref, "{}"),
                )
            # Preserve the first position; gaps are harmless and retain cursors.
            connection.execute(
                "DELETE FROM sequence_segments WHERE sequence_id=%s AND segment_id=ANY(%s)",
                (sequence_id, sources),
            )
            connection.execute(
                "INSERT INTO sequence_segments(sequence_id,ordinal,segment_id) VALUES (%s,%s,%s)",
                (sequence_id, int(members[0]["ordinal"]), segment_id),
            )
            # Capture consumed positions in this same locked transaction. A
            # pre-click snapshot can precede input admission; polling can miss
            # preparation entirely. Neither can reconstruct these facts later.
            put_operation(
                connection,
                operation_id=stable_segment_id("trace-transformation", sequence_id, operation_id, segment_id),
                kind="trace_transformation", agent_id=agent_id, created_at=at,
                data={"schema": "tfi.trace-transformation.v1", "trace_id": sequence_id,
                      "operation_id": operation_id, "operation_type": operation_type,
                      "recorded_at": at, "execution_id": execution_id or first["execution_id"],
                      "sources": [{"ordinal": int(member["ordinal"]), "segment_id": member["segment_id"],
                                   "message_id": member["message_id"] or "",
                                   "content_sha256": member["content_sha256"]} for member in members],
                      "target_segment_id": segment_id, "target_ordinal": int(members[0]["ordinal"])},
            )
            self._touch_sequence(connection, sequence_id, actor_ref)
            chat_position_index.refresh_sequence(connection, sequence_id)
            return segment_id

        return self.database.write(write)


    def fork_agent(
        self, *, source_agent_id: str, target_agent_id: str,
        segment_ids: Sequence[str], actor_ref: str,
    ) -> dict[str, Any]:
        """Materialize the selected immutable references, never a parent's prefix.

        The creation owner freezes selection before publishing the child. Retries
        verify the same selection even after the child has started working.
        """
        source_id, target_id = str(source_agent_id).strip(), str(target_agent_id).strip()
        identities = list(segment_ids)
        if not source_id or not target_id or source_id == target_id:
            raise ValueError("Agent fork requires distinct source and target identities")
        if len(identities) != len(set(identities)):
            raise ValueError("Fork selection contains repeated Segment identities")

        def write(connection: Any) -> dict[str, Any]:
            if self._agent_trace(connection, source_id) is None:
                raise ValueError("Fork source Agent is not registered")
            target_sequence_id = self._locked_trace(connection, target_id, actor_ref)
            if len(self._existing_segment_ids(connection, identities)) != len(identities):
                raise ValueError("Fork selection contains unknown Segments")
            operation_id = stable_segment_id("fork", target_id)
            facts = {"source_agent_id": source_id, "segment_count": len(identities),
                     "selection_sha256": hashlib.sha256(canonical_json(identities).encode()).hexdigest()}
            receipt = connection.execute("SELECT operation_id FROM agent_operations WHERE operation_id=%s", (operation_id,)).fetchone()
            if not receipt:
                existing = self._sequence_ids(connection, target_sequence_id)
                if existing and existing != identities:
                    raise ValueError("Fork target already contains different work")
                if not existing:
                    self._append_sequence_ids(connection, sequence={"sequence_id": target_sequence_id},
                                              segment_ids=identities, actor_ref=actor_ref)
            put_operation(connection, operation_id=operation_id, kind="forked", agent_id=target_id,
                          created_at=utc_now(), data=facts)
            return {"source_agent_id": source_id, "target_agent_id": target_id,
                    "trace_sequence_id": target_sequence_id, "segment_count": len(identities)}

        return self.database.write(write)

    def frozen_fork_segments(self, agent_id: str) -> list[str]:
        """Verify the original Fork prefix, never reconstruct it from today's context."""
        with self.database.snapshot() as c:
            row = c.execute("SELECT data_json FROM agent_operations WHERE operation_id=%s AND kind='forked'",
                            (stable_segment_id('fork', agent_id),)).fetchone()
            if row is None:
                raise ValueError('初始化恢复缺少原 Fork 冻结凭据')
            facts = json_value(row['data_json'])
            ids = [str(r['segment_id']) for r in c.execute(
                'SELECT segment_id FROM sequence_segments WHERE sequence_id=%s ORDER BY ordinal LIMIT %s',
                (self._trace_id(c, agent_id), int(facts.get('segment_count') or 0)),).fetchall()]
            if hashlib.sha256(canonical_json(ids).encode()).hexdigest() != facts.get('selection_sha256'):
                raise ValueError('原 Fork 冻结片段已变化，不能猜测恢复上下文')
            return ids

    def frozen_fork_references(self, agent_id: str) -> list[dict[str, str]]:
        with self.database.snapshot() as c:
            ids = self.frozen_fork_segments(agent_id)
            turns = {str(r['segment_id']): str(r['provider_turn_id'] or '') for r in c.execute(
                'SELECT segment_id,provider_turn_id FROM segments LEFT JOIN executions USING(execution_id) WHERE segment_id=ANY(%s)',
                (ids,),).fetchall()}
            return [{'segment_id': sid, 'provider_turn_id': turns[sid]} for sid in ids]

    def initialization_session_fact(self, agent_id: str, binding_id: str) -> dict[str, Any]:
        """Canonical facts only; a failed/expired Execution is not a native exit."""
        binding = self.provider_binding(binding_id)
        head = self.session_context_head(str(binding.get('provider_session_ref') or ''), agent_id=agent_id) if binding else None
        execution = self.execution(str((head or {}).get('execution_id') or '')) if head else None
        with self.database.read() as c:
            active = [str(r['execution_id']) for r in c.execute(
                "SELECT execution_id FROM executions WHERE agent_id=%s AND status IN ('queued','running')",
                (agent_id,),).fetchall()]
            receipt = c.execute("SELECT data_json FROM agent_operations WHERE agent_id=%s AND kind='initialization_promoted' ORDER BY created_at DESC LIMIT 1",
                                (agent_id,)).fetchone()
        return {'binding': binding, 'active_execution_ids': active,
                'compaction_segment_id': str(head['segment_id']) if head and execution and execution['status'] == 'completed'
                    and execution['agent_id'] == agent_id and execution['provider_session_ref'] == binding.get('provider_session_ref') else '',
                'receipt': json_value(receipt['data_json']) if receipt else {}}

    def promote_initialization_session(self, *, agent_id: str, candidate_agent_id: str,
                                      binding_id: str, prepared_binding_id: str,
                                      source_binding: Mapping[str, Any], segment_ids: list[str],
                                      compaction_execution_id: str, initialization_id: str,
                                      actor_ref: str) -> dict[str, Any]:
        """Promote the binding and canonical compaction together; keep the old Session audit."""
        operation_id = 'initialization_promoted:' + initialization_id
        def write(c: Any) -> dict[str, Any]:
            trace_id = self._locked_trace(c, agent_id, actor_ref)
            replay = c.execute('SELECT data_json FROM agent_operations WHERE operation_id=%s', (operation_id,)).fetchone()
            if replay:
                return json_value(replay['data_json'])
            c.execute('SELECT binding_id FROM provider_bindings WHERE binding_id=ANY(%s) ORDER BY binding_id FOR UPDATE',
                      ([binding_id, prepared_binding_id],)).fetchall()
            candidate = self.provider_binding(prepared_binding_id)
            execution = self.execution(compaction_execution_id)
            head = self.session_context_head(str(candidate.get('provider_session_ref') or ''), agent_id=candidate_agent_id)
            if not execution or execution['status'] != 'completed' or execution['agent_id'] != candidate_agent_id or execution['provider_session_ref'] != candidate.get('provider_session_ref') or not head or head.get('execution_id') != compaction_execution_id:
                raise ValueError('候选会话尚无已完成的原生压缩事实')
            current = [str(r['segment_id']) for r in c.execute(
                'SELECT segment_id FROM sequence_segments WHERE sequence_id=%s ORDER BY ordinal', (trace_id,)).fetchall()]
            if current[:len(segment_ids)] != segment_ids:
                raise ValueError('恢复期间继承上下文发生变化')
            replacement = self.replace_provider_binding(binding_id, prepared_binding_id,
                expected_provider_session_ref=str(source_binding.get('provider_session_ref') or ''),
                expected_absent=not source_binding)
            compact_id = str(head['segment_id'])
            self.append_agent_segments(agent_id, [compact_id], actor_ref=actor_ref)
            self.place_in_trace_before(agent_id, compact_id, current[len(segment_ids):], actor_ref=actor_ref)
            facts = {'initialization_id': initialization_id, 'candidate_agent_id': candidate_agent_id,
                     'source_session': dict(source_binding), 'target_session': replacement['current_binding'],
                     'compaction_execution_id': compaction_execution_id, 'compaction_segment_id': compact_id}
            put_operation(c, operation_id=operation_id, kind='initialization_promoted', agent_id=agent_id,
                          created_at=utc_now(), data=facts)
            return facts
        return self.database.write(write)

    def fork_inheritance(self, agent_id: str, *, include_compaction: bool = False) -> dict[str, Any]:
        """What a Fork's trace took from its source when it was created.

        The forked receipt fixes how many leading trace members were inherited.
        The result names the opening compaction (its content only on request),
        the inherited Chat messages, and whether a later context reset hides
        them. An Agent that no Fork created has no receipt and gets ``{}``.
        """
        agent_id = str(agent_id or "").strip()
        self._chat_traces([agent_id])
        with self.database.snapshot() as connection:
            receipt = connection.execute(
                "SELECT data_json FROM agent_operations WHERE operation_id=%s AND kind='forked'",
                (stable_segment_id("fork", agent_id),),
            ).fetchone()
            sequence_id = self._trace_id(connection, agent_id)
            if receipt is None or not sequence_id:
                return {}
            facts = json_value(receipt["data_json"])
            count = int(facts.get("segment_count") or 0)
            result: dict[str, Any] = {
                "source_agent_id": str(facts.get("source_agent_id") or ""), "segment_count": count,
                "opening_compaction": None, "message_count": 0, "last_message_id": "", "context_cleared": False,
            }
            if not count:
                return result
            first = connection.execute(
                "SELECT m.ordinal, s.segment_id, s.segment_type, COALESCE(s.finished_at,s.started_at) AS created_at FROM sequence_segments m "
                "JOIN segments s USING(segment_id) WHERE m.sequence_id=%s ORDER BY m.ordinal LIMIT 1",
                (sequence_id,),
            ).fetchone()
            last = connection.execute(
                "SELECT ordinal FROM sequence_segments WHERE sequence_id=%s ORDER BY ordinal OFFSET %s LIMIT 1",
                (sequence_id, count - 1),
            ).fetchone()
            if first is None or last is None:
                return result
            boundary = int(last["ordinal"])
            if first["segment_type"] == "compaction":
                compaction = {"segment_id": str(first["segment_id"]), "created_at": (iso_time(first["created_at"]) or "")}
                if include_compaction:
                    compaction["content"] = self._read_segment_rows(connection, [compaction["segment_id"]])[0]["content"]
                result["opening_compaction"] = compaction
            counted = connection.execute(CHAT_COUNT_SQL, (sequence_id, boundary)).fetchone()
            result["message_count"] = int(counted["count"])
            latest = self._chat_rows(connection, sequence_id, before=boundary + 1, limit=1)
            if latest:
                message = json_value(latest[-1]["metadata_json"]).get("message") or {}
                result["last_message_id"] = str(message.get("message_id") or "")
            # The Chat shows only what follows its last context reset.
            result["context_cleared"] = self._chat_floor(connection, sequence_id) >= boundary
            return result

    def agent_context_snapshot(self, agent_id: str, *, before_message_id: str = "", through_message_id: str = "", through_turn_id: str = "", provider_session_ref: str = "") -> dict[str, Any]:
        """The Agent's own effective window, including a Fork with no session yet.

        Walk backwards to the last compaction/reset. Read payloads only for the
        retained window. Execution receipts do not determine trace membership.
        Delivery excludes its current input with before_message_id.
        """
        if before_message_id or through_message_id:
            # The boundary messages are found by the projection (_chat_member).
            self._chat_traces([agent_id])
        with self.database.snapshot() as connection:
            return self._agent_context_snapshot(connection, agent_id, before_message_id=before_message_id, through_message_id=through_message_id, through_turn_id=through_turn_id, provider_session_ref=provider_session_ref)

    def _agent_context_snapshot(self, connection: Any, agent_id: str, *, before_message_id: str = "", through_message_id: str = "", through_turn_id: str = "", provider_session_ref: str = "") -> dict[str, Any]:
        trace = self._agent_trace(connection, agent_id)
        if trace is None:
            raise KeyError(f"Agent trace not found: {agent_id}")
        sequence_id = str(trace["sequence_id"])
        first = 1
        before = None
        if provider_session_ref:
            session_owner = connection.execute(
                "SELECT 1 FROM executions WHERE provider_session_ref=%s AND agent_id=%s "
                "UNION ALL SELECT 1 FROM agent_operations WHERE session_ref=%s AND agent_id=%s "
                "AND kind='context_received' LIMIT 1",
                (provider_session_ref, agent_id, provider_session_ref, agent_id),
            ).fetchone()
            # Inherited messages retain their original session provenance. Its
            # delivery receipts belong to the source, not this independent trace.
            window = self._session_window(connection, provider_session_ref, agent_id) if (
                session_owner is not None
            ) else None
            if window and window[0] == sequence_id:
                first = max(first, window[1])
                before = window[2] + 1
        if before_message_id:
            member = self._chat_member(connection, sequence_id, before_message_id)
            if member is None:
                raise ValueError("Context input boundary is not in the Agent trace")
            before = int(member["ordinal"])
        if through_message_id:
            member = self._chat_member(connection, sequence_id, through_message_id)
            if member is None:
                raise ValueError("Fork cutoff message is not in the Agent trace")
            # The selected input and following responses form a trace interval.
            # A recorded Provider turn can group appended inputs, but old traces
            # remain usable without any Execution or Provider session record.
            target = self.trace_rows(connection, sequence_id, first=int(member["ordinal"]), limit=1)[0]
            target_turn = str(target["provider_turn_id"] or "")
            # Use the same turn boundary as recent Fork selection. Held-info
            # Segments carry released_with and stay with the carrier's answers;
            # an absent Provider receipt alone still starts a genuine user turn.
            from .context_seed import is_context_turn_input

            next_ordinal = int(member["ordinal"]) + 1
            before = None
            while True:
                page = self.trace_rows(connection, sequence_id, first=next_ordinal, limit=256)
                for row in page:
                    item = {"segment_type": row["segment_type"],
                            "metadata": json_value(row["metadata_json"])}
                    if row["segment_type"] == "compaction" or (
                        is_context_turn_input(item)
                        and (not target_turn or str(row["provider_turn_id"] or "") != target_turn)
                    ):
                        before = int(row["ordinal"])
                        break
                if before is not None or len(page) < 256:
                    break
                next_ordinal = int(page[-1]["ordinal"]) + 1
            reset = connection.execute(
                "SELECT MIN(p.ordinal) AS ordinal FROM operation_positions p JOIN agent_operations o USING(operation_id) "
                "WHERE p.sequence_id=%s AND p.ordinal>%s AND o.kind IN ('context_reset','provider_context_reset')",
                (sequence_id, member["ordinal"]),
            ).fetchone()
            if reset["ordinal"] is not None:
                before = min(before, int(reset["ordinal"])) if before is not None else int(reset["ordinal"])
        if through_turn_id:
            cutoff = connection.execute(
                "SELECT MAX(m.ordinal) AS ordinal FROM sequence_segments m JOIN segments segment USING(segment_id) "
                "LEFT JOIN executions execution USING(execution_id) WHERE m.sequence_id=%s "
                f"AND {PROVIDER_TURN_SQL}=%s",
                (sequence_id, through_turn_id),
            ).fetchone()
            if cutoff["ordinal"] is None:
                raise ValueError("Fork cutoff has no recorded context in this Agent trace")
            before = int(cutoff["ordinal"]) + 1
        boundary = connection.execute(
            "SELECT COALESCE(MAX(p.ordinal),0) AS ordinal FROM operation_positions p "
            "JOIN agent_operations o USING(operation_id) WHERE p.sequence_id=%s "
            "AND o.kind IN ('context_reset','provider_context_reset') "
            "AND (%s::bigint IS NULL OR p.ordinal < %s)", (sequence_id, before, before),
        ).fetchone()
        first = max(first, int(boundary["ordinal"]) + 1)
        rows = []
        while True:
            page = self.trace_rows(connection, sequence_id, first=first, before=before,
                                   newest_first=True, limit=256)
            stop = False
            for row in page:
                rows.append(row)
                if row["segment_type"] == "compaction":
                    stop = True
                    break
            if stop or len(page) < 256:
                break
            before = int(page[-1]["ordinal"])
        rows.reverse()
        selected = []
        for row in rows:
            metadata = json_value(row["metadata_json"])
            if metadata.get("kind") in PROVIDER_ONLY_KINDS | {"system_instructions"}:
                continue
            selected.append(row)
        segments = self._read_segment_rows(connection, [str(row["segment_id"]) for row in selected])
        for item, row in zip(segments, selected):
            item["provider_turn_id"] = str(row["provider_turn_id"] or "")
        return {"segments": segments, "empty_context": not segments}

    def context_evidence_snapshot(self, agent_id: str) -> dict[str, Any]:
        """Read audit counts, effective context and bounded import facts atomically.

        This is an explicit diagnostic read. Normal conversation reads do not
        load model context. Its owner must authorize the Agent before calling.
        """
        with self.database.snapshot() as connection:
            observation = connection.execute(
                "SELECT clock_timestamp() AS sampled_at, "
                "current_setting('transaction_isolation') AS isolation, "
                "current_setting('transaction_read_only') AS read_only"
            ).fetchone()
            if observation["isolation"] != "repeatable read" or observation["read_only"] != "on":
                raise RuntimeError("Context evidence requires a read-only repeatable-read snapshot")
            trace = self._agent_trace(connection, agent_id)
            positions = {"schema": "tfi.trace-position-evidence.v1",
                         "sampled_at": iso_time(observation["sampled_at"]),
                         "consistency": "repeatable_read_read_only",
                         "trace_id": trace["sequence_id"] if trace else "",
                         "trace_updated_at": iso_time(trace["updated_at"]) if trace else "",
                         "entries": [], "truncated": False}
            if trace is None:
                return {"trace_counts": {}, "segments": [], "imports": [], "imports_truncated": False,
                        "trace_positions": positions, "transformations": [], "transformations_truncated": False}
            rows = connection.execute(
                "SELECT member.ordinal, member.segment_id, segment.segment_type, segment.content_sha256, "
                "segment.execution_id, segment.started_at AS segment_started_at, "
                "segment.finished_at AS segment_finished_at, "
                "segment.metadata_json::jsonb#>>'{message,message_id}' AS message_id, "
                "segment.metadata_json::jsonb#>>'{provider_observation,item_id}' AS provider_item_id, "
                f"{PROVIDER_TURN_SQL} AS provider_turn_id, execution.provider_session_ref, "
                "session.provider, session.backend_route_id, session.backend_session_id "
                "FROM sequence_segments member JOIN segments segment USING(segment_id) "
                "LEFT JOIN executions execution ON execution.execution_id=segment.execution_id "
                "LEFT JOIN provider_sessions session USING(provider_session_ref) "
                "WHERE member.sequence_id=%s ORDER BY member.ordinal LIMIT %s",
                (trace["sequence_id"], CONTEXT_EVIDENCE_POSITION_LIMIT + 1),
            ).fetchall()
            positions["entries"] = [
                {key: (int(value) if key == "ordinal" else iso_time(value) if key in
                       {"segment_started_at", "segment_finished_at"} else value or "")
                 for key, value in dict(row).items()} for row in rows[:CONTEXT_EVIDENCE_POSITION_LIMIT]]
            positions["truncated"] = len(rows) > CONTEXT_EVIDENCE_POSITION_LIMIT
            transformations = connection.execute(
                "SELECT operation_id, data_json FROM agent_operations "
                "WHERE session_ref='' AND agent_id=%s AND kind='trace_transformation' "
                "ORDER BY created_at DESC, operation_id DESC LIMIT %s",
                (agent_id, CONTEXT_EVIDENCE_POSITION_LIMIT + 1),
            ).fetchall()
            counts = connection.execute(
                "SELECT s.segment_type, COUNT(*) AS count FROM sequence_segments m "
                "JOIN segments s USING(segment_id) WHERE m.sequence_id=%s GROUP BY s.segment_type",
                (trace["sequence_id"],),
            ).fetchall()
            context = self._agent_context_snapshot(connection, agent_id)
            compaction_ids = [item["segment_id"] for item in context["segments"]
                              if item["segment_type"] == "compaction"]
            compressed_from = [dict(row) for row in connection.execute(
                "SELECT source_segment_id,target_segment_id FROM segment_relations "
                "WHERE relation_type='compressed_from' AND target_segment_id=ANY(%s) "
                "ORDER BY target_segment_id,source_segment_id LIMIT 2001", (compaction_ids,),
            ).fetchall()] if compaction_ids else []
            rows = connection.execute(
                "SELECT operation_id, data_json FROM agent_operations WHERE agent_id=%s "
                "AND kind='artifact' AND starts_with(operation_id, 'segment_fork_import_') "
                "ORDER BY created_at DESC, operation_id DESC LIMIT 21", (agent_id,),
            ).fetchall()
            imports = []
            for row in rows[:20]:
                record = json_value(row["data_json"])
                if record.get("metadata", {}).get("kind") != "fork_history_delivery":
                    continue
                imports.append({"operation_id": row["operation_id"], "content": record["content"]})
            identities = list(dict.fromkeys(
                str(ref["segment_id"]) for item in imports for ref in item["content"]["segments"]
            ))
            segments = {item["segment_id"]: item for item in self._read_segment_rows(connection, identities)}
            for item in imports:
                item["segments"] = [
                    {**segments[ref["segment_id"]], "provider_turn_id": str(ref.get("provider_turn_id") or "")}
                    for ref in item["content"]["segments"]
                ]
            return {"trace_counts": {row["segment_type"]: int(row["count"]) for row in counts},
                    "segments": context["segments"], "imports": imports,
                    "compressed_from": compressed_from[:2000],
                    "compressed_from_truncated": len(compressed_from) > 2000,
                    "trace_positions": positions,
                    "transformations": [{"receipt_id": row["operation_id"], **json_value(row["data_json"])}
                                        for row in transformations[:CONTEXT_EVIDENCE_POSITION_LIMIT]],
                    "transformations_truncated": len(transformations) > CONTEXT_EVIDENCE_POSITION_LIMIT,
                    "imports_truncated": len(rows) > 20}

    def read_sequence(
        self,
        sequence_id: str,
        *,
        offset: int = 0,
        limit: int = 200,
    ) -> dict[str, Any]:
        with self.database.read() as connection:
            sequence = self._sequence_row(connection, sequence_id)
            if sequence is None:
                return {
                    "sequence_id": "",
                    "total": 0,
                    "segments": [],
                }
            sequence_id = str(sequence["sequence_id"])
            total = self._sequence_count(connection, sequence_id)
            start = max(0, int(offset))
            bounded = max(1, min(2_000, int(limit)))
            rows = connection.execute(
                'SELECT segment_id FROM sequence_segments WHERE sequence_id = %s ORDER BY ordinal LIMIT %s OFFSET %s',
                (sequence_id, bounded, start),
            ).fetchall()
            return {
                "sequence_id": sequence_id,
                "updated_at": iso_time(sequence["updated_at"]),
                "offset": start,
                "total": total,
                "segments": self._read_segment_rows(
                    connection,
                    [str(row["segment_id"]) for row in rows],
                ),
            }

    def sequence_segment_ids(self, sequence_id: str) -> list[str]:
        with self.database.read() as connection:
            return self._sequence_ids(connection, sequence_id)

    def latest_provider_session_ref(self, agent_id: str, *, since: str = "") -> str:
        """The Provider session of the agent's most recent Execution, if any.

        A session exists here as soon as the Provider accepted input, even when
        the turn failed before the session was bound.  With ``since`` (the time
        the agent's context was cleared) only an Execution started after it
        counts: the sessions before it hold the context that was cleared.
        """

        clean_agent_id = str(agent_id or "").strip()
        if not clean_agent_id:
            return ""
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT provider_session_ref, started_at FROM executions "
                "WHERE agent_id = %s AND provider_session_ref <> '' "
                "ORDER BY started_at DESC LIMIT 1",
                (clean_agent_id,),
            ).fetchone()
        if row is None:
            return ""
        if since and not _later_than((iso_time(row["started_at"]) or ""), since):
            return ""
        return str(row["provider_session_ref"])

    def latest_context_compaction(self, agent_id: str, *, since: str = "") -> dict[str, Any] | None:
        """The compaction that heads an agent's newest Provider context.

        The newest context is that of the Provider session of the agent's most
        recent Execution (after ``since``, as for ``latest_provider_session_ref``),
        read from the trace.  Returns that compaction Segment, or ``None`` when
        the context has not been compacted.
        """

        provider_session_ref = self.latest_provider_session_ref(agent_id, since=since)
        if not provider_session_ref:
            return None
        return self.session_context_head(provider_session_ref, agent_id=agent_id)

    def message_in_context(self, agent_id: str, message_id: str, *, since: str = "") -> bool:
        """Whether a Chat message the Provider took is still in the agent's newest context.

        The newest context is the one ``latest_context_compaction`` reads: the
        Provider session of the agent's most recent Execution after ``since``,
        from its last compaction on.  A message no Provider took, one a clear
        hid, one an earlier session took and one a compaction has since
        summarised are not in it.
        """

        try:
            source = self.chat_message_provenance(agent_id, message_id)
        except KeyError:
            return False
        # A fresh restart clears the canonical context before the next lazy
        # Provider session exists. The latest execution still names the old
        # session in that interval, so its context window alone is stale.
        with self.database.read() as connection:
            trace = self._agent_trace(connection, agent_id)
            if trace is None:
                return False
            reset = connection.execute(
                "SELECT 1 FROM sequence_segments member JOIN operation_positions boundary "
                "ON boundary.sequence_id=member.sequence_id AND boundary.ordinal>member.ordinal "
                "JOIN agent_operations operation USING(operation_id) "
                "WHERE member.sequence_id=%s AND member.segment_id=%s "
                "AND operation.kind IN ('context_reset','provider_context_reset') LIMIT 1",
                (trace["sequence_id"], source["segment_id"]),
            ).fetchone()
            if reset is not None:
                return False
        provider_session_ref = self.latest_provider_session_ref(agent_id, since=since)
        if not provider_session_ref or source["provider_session_ref"] != provider_session_ref:
            return False
        return source["segment_id"] in self.session_context_ids(provider_session_ref, agent_id=agent_id)

    def session_context_head(self, provider_session_ref: str, *, agent_id: str = "") -> dict[str, Any] | None:
        """The compaction at the head of this Provider session, if any."""
        context = self.session_context_ids(provider_session_ref, agent_id=agent_id)
        members = self.segment_members(context[:1])
        if members and members[0]["segment_type"] == "compaction":
            return self.segment(members[0]["segment_id"])
        return None

    def segment_members(self, segment_ids: Sequence[str]) -> list[dict[str, Any]]:
        """Segments in the given order, without their content bodies."""

        with self.database.read() as connection:
            return self._segment_members(connection, [str(item) for item in segment_ids])

    @staticmethod
    def _segment_members(connection: Any, ids: list[str]) -> list[dict[str, Any]]:
        found: dict[str, dict[str, Any]] = {}
        for chunk in _batches(ids):
            placeholders = ",".join("%s" for _ in chunk)
            for row in connection.execute(
                f"SELECT segment_id, segment_type, started_at, finished_at, metadata_json, execution_id "
                f"FROM segments WHERE segment_id IN ({placeholders})",
                chunk,
            ).fetchall():
                found[str(row["segment_id"])] = {
                    "segment_id": str(row["segment_id"]),
                    "segment_type": str(row["segment_type"]),
                    "started_at": (iso_time(row["started_at"]) or ""),
                    "finished_at": (iso_time(row["finished_at"]) or ""),
                    "metadata": json_value(row["metadata_json"] or "{}"),
                    "execution_id": str(row["execution_id"] or ""),
                }
        return [found[item] for item in ids if item in found]



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
        """Record a Provider session, the first time it is seen.

        A native fork (``source_backend_session_id``) starts with its source's
        current context; the trace of ``context_agent_id`` (the Agent that will
        work in it; by default ``agent_id``) marks that inherited context, so
        that the session's context read from the trace begins with it.  The
        context_received operation marks the first inherited trace member;
        the content must already belong to that Agent's trace.
        """

        clean_provider = str(provider or "").strip()
        clean_route = str(backend_route_id or "").strip()
        clean_session = str(backend_session_id or "").strip()
        if not clean_provider or not clean_session:
            raise ValueError("Provider Session identity is required")
        digest = hashlib.sha256(
            f"{clean_provider}\0{clean_route}\0{clean_session}".encode("utf-8")
        ).hexdigest()[:32]
        session_ref = f"provider_session_{digest}"

        def write(connection: Any) -> dict[str, Any]:
            existing = connection.execute(
                'SELECT 1 FROM provider_sessions WHERE provider_session_ref = %s',
                (session_ref,),
            ).fetchone()
            at = utc_now()
            if existing is None:
                clean_source = str(source_backend_session_id or "").strip()
                inherited: list[str] = []
                if clean_source:
                    source = connection.execute(
                        'SELECT provider_session_ref FROM provider_sessions '
                        'WHERE provider = %s AND backend_route_id = %s AND backend_session_id = %s',
                        (clean_provider, str(source_backend_route_id or clean_route), clean_source),
                    ).fetchone()
                    if source is None:
                        raise KeyError(
                            f"Provider Session source is not recorded: {clean_source}"
                        )
                    inherited = self._session_context_ids(
                        connection, str(source["provider_session_ref"]),
                        agent_id=str(context_agent_id or agent_id or ""),
                    )
                connection.execute(
                    'INSERT INTO provider_sessions(provider_session_ref, provider, backend_route_id, '
                    'backend_session_id, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s)',
                    (session_ref, clean_provider, clean_route, clean_session, at, at),
                )
                marking_agent = str(context_agent_id or agent_id or "").strip()
                if inherited and marking_agent:
                    self._record_context_received(
                        connection, marking_agent, session_ref, inherited, actor_ref=actor_ref,
                    )
            else:
                connection.execute(
                    'UPDATE provider_sessions SET updated_at = %s WHERE provider_session_ref = %s',
                    (at, session_ref),
                )
            return {
                "provider_session_ref": session_ref,
                "provider": clean_provider,
                "backend_route_id": clean_route,
                "backend_session_id": clean_session,
                "agent_id": str(agent_id or ""),
            }

        return self.database.write(write)

    @staticmethod
    def _provider_binding_record(row: Any) -> dict[str, Any]:
        return {
            "binding_id": str(row["binding_id"]),
            "provider_session_ref": str(row["provider_session_ref"]),
            "provider": str(row["provider"]),
            "backend_session_id": str(row["backend_session_id"]),
            "backend_route_id": str(row["backend_route_id"]),
            "agent_id": str(row["agent_id"] or ""),
            "context_fingerprint": str(row["context_fingerprint"]),
            "context_injected_at": (iso_time(row["context_injected_at"]) or ""),
            "context_injection_count": int(row["context_injection_count"] or 0),
            "harness_tool_names": clean_string_tuple(row["harness_tool_names_json"]),
            "created_at": (iso_time(row["created_at"]) or ""),
            "updated_at": (iso_time(row["updated_at"]) or ""),
        }

    @staticmethod
    def _provider_binding_row(
        connection: Any,
        binding_id: str,
    ) -> Any | None:
        return connection.execute(
            "\n            SELECT binding.*, session.provider, session.backend_route_id,\n                   session.backend_session_id,\n                   COALESCE((\n                       SELECT execution.agent_id\n                       FROM executions AS execution\n                       WHERE execution.provider_session_ref =\n                             session.provider_session_ref\n                       ORDER BY execution.started_at, execution.execution_id\n                       LIMIT 1\n                   ), '') AS agent_id\n            FROM provider_bindings AS binding\n            JOIN provider_sessions AS session\n              ON session.provider_session_ref = binding.provider_session_ref\n            WHERE binding.binding_id = %s\n            ",
            (binding_id,),
        ).fetchone()

    def provider_binding(self, binding_id: str) -> dict[str, Any]:
        clean_id = str(binding_id or "").strip()
        if not clean_id:
            return {}
        with self.database.read() as connection:
            row = self._provider_binding_row(connection, clean_id)
        return self._provider_binding_record(row) if row is not None else {}

    def provider_binding_routes(
        self,
        binding_ids: Iterable[object],
    ) -> dict[str, dict[str, Any]]:
        requested = tuple(dict.fromkeys(
            str(item or "").strip()
            for item in binding_ids
            if str(item or "").strip()
        ))
        if not requested:
            return {}
        records: dict[str, dict[str, Any]] = {}
        with self.database.read() as connection:
            for chunk in _batches(requested):
                placeholders = ",".join("%s" for _ in chunk)
                rows = connection.execute(
                    "SELECT binding.binding_id, session.provider_session_ref, "
                    "session.provider, session.backend_route_id, session.backend_session_id "
                    "FROM provider_bindings AS binding "
                    "JOIN provider_sessions AS session "
                    "ON session.provider_session_ref = binding.provider_session_ref "
                    f"WHERE binding.binding_id IN ({placeholders})",
                    chunk,
                ).fetchall()
                records.update((str(row["binding_id"]), dict(row)) for row in rows)
        return records


    def bind_provider_session(
        self,
        binding_id: str,
        provider_session_ref: str,
        *,
        context_fingerprint: object = _FIELD_NOT_PROVIDED,
        context_injected_at: object = _FIELD_NOT_PROVIDED,
        context_injection_count: object = _FIELD_NOT_PROVIDED,
        harness_tool_names: object = _FIELD_NOT_PROVIDED,
    ) -> dict[str, Any]:
        clean_binding = str(binding_id or "").strip()
        clean_session_ref = str(provider_session_ref or "").strip()
        if not clean_binding or not clean_session_ref:
            raise ValueError("Provider binding and Session identities are required")

        def write(connection: Any) -> dict[str, Any]:
            if connection.execute(
                'SELECT 1 FROM provider_sessions WHERE provider_session_ref = %s',
                (clean_session_ref,),
            ).fetchone() is None:
                raise KeyError(clean_session_ref)
            existing = self._provider_binding_row(connection, clean_binding)
            at = utc_now()
            fingerprint = (
                str(existing["context_fingerprint"] or "")
                if existing is not None and context_fingerprint is _FIELD_NOT_PROVIDED
                else str("" if context_fingerprint is _FIELD_NOT_PROVIDED else context_fingerprint or "")
            )
            injected_at = (
                (iso_time(existing["context_injected_at"]) or "")
                if existing is not None and context_injected_at is _FIELD_NOT_PROVIDED
                else str("" if context_injected_at is _FIELD_NOT_PROVIDED else context_injected_at or "")
            )
            injection_count = (
                int(existing["context_injection_count"] or 0)
                if existing is not None and context_injection_count is _FIELD_NOT_PROVIDED
                else max(0, int(0 if context_injection_count is _FIELD_NOT_PROVIDED else context_injection_count or 0))
            )
            tools_json = _document(clean_string_tuple(
                existing["harness_tool_names_json"]
                if existing is not None and harness_tool_names is _FIELD_NOT_PROVIDED
                else () if harness_tool_names is _FIELD_NOT_PROVIDED else harness_tool_names
            ))
            if existing is None:
                connection.execute(
                    '\n                    INSERT INTO provider_bindings(\n                        binding_id, provider_session_ref, context_fingerprint,\n                        context_injected_at, context_injection_count,\n                        harness_tool_names_json, created_at, updated_at\n                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)\n                    ',
                    (
                        clean_binding,
                        clean_session_ref,
                        fingerprint,
                        instant(injected_at),
                        injection_count,
                        tools_json,
                        at,
                        at,
                    ),
                )
            else:
                if str(existing["provider_session_ref"]) != clean_session_ref:
                    raise ValueError("Provider binding identity is immutable")
                connection.execute(
                    '\n                    UPDATE provider_bindings\n                    SET context_fingerprint = %s, context_injected_at = %s,\n                        context_injection_count = %s, harness_tool_names_json = %s,\n                        updated_at = %s\n                    WHERE binding_id = %s\n                    ',
                    (
                        fingerprint,
                        instant(injected_at),
                        injection_count,
                        tools_json,
                        at,
                        clean_binding,
                    ),
                )
            row = self._provider_binding_row(connection, clean_binding)
            if row is None:
                raise RuntimeError("Provider binding write did not produce a record")
            return self._provider_binding_record(row)

        return self.database.write(write)


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
        canonical = str(binding_id or "").strip()
        prepared = str(prepared_binding_id or "").strip()
        if not canonical or not prepared or canonical == prepared:
            raise ValueError("Provider binding replacement requires distinct identities")
        if expected_absent and (
            expected_provider or expected_backend_session_id or expected_provider_session_ref
        ):
            raise ValueError("An expected absent binding cannot also have an expected identity")

        def write(connection: Any) -> dict[str, Any]:
            previous_row = self._provider_binding_row(connection, canonical)
            prepared_row = self._provider_binding_row(connection, prepared)
            if expected_absent and previous_row is not None:
                raise RuntimeError("Provider binding changed before replacement")
            if previous_row is None and not expected_absent:
                raise KeyError(f"Provider binding not found: {canonical}")
            if prepared_row is None:
                raise KeyError(f"Prepared Provider binding not found: {prepared}")
            if (
                expected_provider_session_ref
                and str(previous_row["provider_session_ref"]) != expected_provider_session_ref
            ):
                raise RuntimeError("Provider binding changed before replacement")
            if expected_provider and str(previous_row["provider"]) != expected_provider:
                raise RuntimeError("Provider binding changed before replacement")
            if (
                expected_backend_session_id
                and str(previous_row["backend_session_id"]) != expected_backend_session_id
            ):
                raise RuntimeError("Provider binding changed before replacement")
            previous = self._provider_binding_record(previous_row) if previous_row is not None else {}
            connection.execute(
                'DELETE FROM provider_bindings WHERE binding_id = %s', (canonical,)
            )
            connection.execute(
                'UPDATE provider_bindings SET binding_id = %s, updated_at = %s WHERE binding_id = %s',
                (canonical, utc_now(), prepared),
            )
            current_row = self._provider_binding_row(connection, canonical)
            if current_row is None:
                raise RuntimeError("Provider binding replacement did not produce a record")
            return {
                "previous_binding": previous,
                "current_binding": self._provider_binding_record(current_row),
            }

        return self.database.write(write)

    def restore_provider_binding(
        self,
        binding_id: str,
        *,
        expected_provider_session_ref: str,
        binding: Mapping[str, Any],
        expected_absent: bool = False,
    ) -> dict[str, Any]:
        canonical = str(binding_id or "").strip()
        expected = str(expected_provider_session_ref or "").strip()
        previous_ref = str(binding.get("provider_session_ref") or "").strip()
        if not canonical or (not expected and not expected_absent) or not previous_ref:
            raise ValueError("Provider binding restoration requires explicit current and prior state")

        def write(connection: Any) -> dict[str, Any]:
            current = self._provider_binding_row(connection, canonical)
            if expected_absent:
                if current is not None:
                    raise RuntimeError("Provider binding changed after detachment")
                connection.execute(
                    "INSERT INTO provider_bindings (binding_id, provider_session_ref, created_at, updated_at, "
                    "context_fingerprint, context_injected_at, context_injection_count, harness_tool_names_json) "
                    "VALUES (%s,%s,%s,%s,'',NULL,0,'[]')",
                    (canonical, previous_ref, utc_now(), utc_now()),
                )
                current = self._provider_binding_row(connection, canonical)
            if current is None or str(current["provider_session_ref"]) != (previous_ref if expected_absent else expected):
                raise RuntimeError("Provider binding changed after replacement")
            if connection.execute(
                'SELECT 1 FROM provider_sessions WHERE provider_session_ref = %s',
                (previous_ref,),
            ).fetchone() is None:
                raise KeyError(previous_ref)
            at = utc_now()
            connection.execute(
                '\n                UPDATE provider_bindings\n                SET provider_session_ref = %s, context_fingerprint = %s,\n                    context_injected_at = %s, context_injection_count = %s,\n                    harness_tool_names_json = %s, updated_at = %s\n                WHERE binding_id = %s\n                ',
                (
                    previous_ref,
                    str(binding.get("context_fingerprint") or ""),
                    instant(str(binding.get("context_injected_at") or "")),
                    max(0, int(binding.get("context_injection_count") or 0)),
                    _document(clean_string_tuple(binding.get("harness_tool_names"))),
                    at,
                    canonical,
                ),
            )
            row = self._provider_binding_row(connection, canonical)
            if row is None:
                raise RuntimeError("Provider binding restoration did not produce a record")
            return self._provider_binding_record(row)

        return self.database.write(write)

    def delete_provider_binding(
        self, binding_id: str, *, expected_provider_session_ref: str = "",
    ) -> None:
        clean_id = str(binding_id or "").strip()
        if not clean_id:
            return

        def write(connection: Any) -> None:
            current = self._provider_binding_row(connection, clean_id)
            if current is None:
                return
            if (
                expected_provider_session_ref
                and str(current["provider_session_ref"]) != expected_provider_session_ref
            ):
                raise RuntimeError("Provider binding changed before release")
            connection.execute(
                'DELETE FROM provider_bindings WHERE binding_id = %s', (clean_id,)
            )

        self.database.write(write)

    def reserve_execution(
        self, *, execution_id: str, agent_id: str, actor_ref: str, provider: str,
        created_at: str = "",
    ) -> dict[str, Any]:
        """Create the single execution identity before preparing its request.

        Preparation and pre-start cancellation are part of execution history.
        The queue and worker use this identity; they never allocate a second
        Resident job or turn identity.
        """
        if not all((execution_id, agent_id, actor_ref, provider)):
            raise ValueError("Execution reservation requires identity, agent, actor and provider")

        def write(connection: Any) -> dict[str, Any]:
            connection.execute(
                "INSERT INTO executions(execution_id,agent_id,actor_ref,provider,provider_session_ref,"
                "provider_turn_id,created_at,started_at,finished_at,status,usage_json) "
                "VALUES(%s,%s,%s,%s,NULL,'',%s,NULL,NULL,'preparing','{}') "
                "ON CONFLICT(execution_id) DO NOTHING",
                (execution_id, agent_id, actor_ref, provider, created_at or utc_now()),
            )
            row = connection.execute("SELECT * FROM executions WHERE execution_id=%s FOR UPDATE", (execution_id,)).fetchone()
            if (row["agent_id"], row["actor_ref"], row["provider"]) != (agent_id, actor_ref, provider):
                raise ValueError("Execution identity refers to different immutable provenance")
            return self._execution_record(row)

        return self.database.write(write)

    def queue_execution(self, execution_id: str) -> None:
        """Mark request admission; retries cannot rewind a started execution."""
        def write(connection: Any) -> None:
            row = connection.execute("SELECT status FROM executions WHERE execution_id=%s FOR UPDATE", (execution_id,)).fetchone()
            if row is None:
                raise KeyError(execution_id)
            if row["status"] == "preparing":
                connection.execute("UPDATE executions SET status='queued' WHERE execution_id=%s", (execution_id,))
        self.database.write(write)

    @staticmethod
    def record_execution_submission_in(connection: Any, request: Any, *, request_digest: str, submitted_at: str) -> None:
        """Persist validated request semantics in the admission transaction, without its body."""
        request.validate()
        if not re.fullmatch(r"[0-9a-f]{64}", request_digest):
            raise ValueError("Execution submission requires its original request digest")
        row = connection.execute("SELECT agent_id,actor_ref,provider FROM executions WHERE execution_id=%s FOR UPDATE",
                                 (request.execution_id,)).fetchone()
        if row is None or (row["agent_id"], row["actor_ref"], row["provider"]) != (request.agent_id, request.actor, request.provider):
            raise ValueError("Execution submission does not match its immutable provenance")
        put_operation(connection, operation_id="execution_submission:" + request.execution_id,
                      kind="execution_submission", agent_id=request.agent_id, created_at=submitted_at,
                      data={"schema": "tfi.execution-submission.v1", "execution_id": request.execution_id,
                            "service_type": request.service_type, "operation": request.operation,
                            "request_digest": request_digest})

    def begin_execution(
        self,
        *,
        execution_id: str,
        agent_id: str,
        actor_ref: str,
        provider: str,
        started_at: str,
    ) -> dict[str, Any]:
        clean_execution_id = str(execution_id or "").strip()
        if not clean_execution_id:
            raise ValueError("Execution identity is required")
        identity = (
            str(agent_id or ""),
            str(actor_ref or ""),
            str(provider or ""),
            instant(started_at or utc_now()),
        )

        def write(connection: Any) -> dict[str, Any]:
            existing = connection.execute(
                'SELECT * FROM executions WHERE execution_id = %s',
                (clean_execution_id,),
            ).fetchone()
            if existing is not None:
                existing_identity = (
                    str(existing["agent_id"]),
                    str(existing["actor_ref"]),
                    str(existing["provider"]),
                )
                if existing_identity != identity[:3]:
                    raise ValueError(
                        "Execution identity refers to different immutable provenance"
                    )
                if existing["status"] in {"preparing", "queued"}:
                    connection.execute(
                        "UPDATE executions SET status='running',started_at=%s WHERE execution_id=%s",
                        (identity[3], clean_execution_id),
                    )
                    existing = connection.execute("SELECT * FROM executions WHERE execution_id=%s", (clean_execution_id,)).fetchone()
                elif existing["started_at"] != identity[3]:
                    raise ValueError("Execution identity refers to different immutable provenance")
                return self._execution_record(existing)
            connection.execute(
                "INSERT INTO executions(execution_id,agent_id,actor_ref,provider,provider_session_ref,"
                "provider_turn_id,created_at,started_at,finished_at,status,usage_json) "
                "VALUES(%s,%s,%s,%s,NULL,'',%s,%s,NULL,'running','{}')",
                (
                    clean_execution_id,
                    identity[0],
                    identity[1],
                    identity[2],
                    identity[3],
                    identity[3],
                ),
            )
            created = connection.execute(
                'SELECT * FROM executions WHERE execution_id = %s',
                (clean_execution_id,),
            ).fetchone()
            if created is None:
                raise RuntimeError("Execution creation did not produce a record")
            return self._execution_record(created)

        return self.database.write(write)


    def bind_execution_provider_session(
        self,
        execution_id: str,
        *,
        provider_session_ref: str,
    ) -> None:
        def write(connection: Any) -> None:
            execution = connection.execute(
                'SELECT * FROM executions WHERE execution_id = %s FOR UPDATE',
                (str(execution_id or ""),),
            ).fetchone()
            if execution is None:
                raise KeyError(execution_id)
            session = connection.execute(
                'SELECT provider FROM provider_sessions WHERE provider_session_ref = %s',
                (str(provider_session_ref or ""),),
            ).fetchone()
            if session is None:
                raise KeyError(provider_session_ref)
            if str(session["provider"]) != str(execution["provider"]):
                raise ValueError("Execution and Provider Session use different providers")
            current = str(execution["provider_session_ref"] or "")
            requested = str(provider_session_ref or "")
            if current and current != requested:
                raise ValueError("Execution is already bound to a different Provider Session")
            if not current:
                connection.execute(
                    'UPDATE executions SET provider_session_ref = %s WHERE execution_id = %s',
                    (requested, str(execution_id or "")),
                )
            self._record_session_input_acceptance(
                connection, {**dict(execution), "provider_session_ref": requested},
            )

        self.database.write(write)

    @staticmethod
    def _session_input_acceptance_id(session_ref: str, agent_id: str) -> str:
        digest = hashlib.sha256(canonical_json([session_ref, agent_id]).encode()).hexdigest()
        return f"segment_session_input_acceptance_{digest}"

    @classmethod
    def _session_input_acceptance(
        cls, connection: Any, session_ref: str, agent_id: str,
    ) -> dict[str, Any] | None:
        row = connection.execute(
            "SELECT data_json FROM agent_operations WHERE kind='input_accepted' AND session_ref=%s AND agent_id=%s ORDER BY created_at LIMIT 1",
            (session_ref, agent_id),
        ).fetchone()
        return json_value(row["data_json"]) if row else None

    @classmethod
    def _record_session_input_acceptance(
        cls, connection: Any, execution: Mapping[str, Any],
    ) -> bool:
        """Persist the first accepted turn independently of Execution retention."""
        session_ref = str(execution["provider_session_ref"] or "")
        turn_id = str(execution["provider_turn_id"] or "").strip()
        agent_id = str(execution["agent_id"])
        if not session_ref or not turn_id:
            return False
        if cls._session_input_acceptance(connection, session_ref, agent_id) is not None:
            return False
        session = connection.execute(
            'SELECT provider FROM provider_sessions WHERE provider_session_ref = %s',
            (session_ref,),
        ).fetchone()
        if session is None or session["provider"] != execution["provider"]:
            raise ValueError("Session input acceptance requires the matching Provider Session")
        at = utc_now()
        put_operation(connection, operation_id=cls._session_input_acceptance_id(session_ref, agent_id),
            kind="input_accepted", agent_id=agent_id, session_ref=session_ref, created_at=at,
            data={"schema": SESSION_INPUT_ACCEPTANCE_SCHEMA, "provider_session_ref": session_ref,
                  "agent_id": agent_id, "provider_turn_id": turn_id,
                  "evidence_execution_id": str(execution["execution_id"]), "recorded_at": at})
        return True

    def trace_length(self, agent_id: str) -> int:
        """How many Segments the Agent's trace holds now."""

        with self.database.read() as connection:
            trace = self._agent_trace(connection, str(agent_id or ''))
            return self._sequence_count(connection, str(trace["sequence_id"])) if trace else 0

    def place_in_trace_before(
        self, agent_id: str, segment_id: str, following: Iterable[str], *, actor_ref: str,
    ) -> bool:
        """Move a trace member before the first of ``following`` that precedes it.

        A compaction recorded after the fact goes before the first message it
        kept, where it happened.  Returns whether it moved.
        """

        later = [str(item) for item in following if str(item)]
        clean_agent = str(agent_id or "").strip()
        if not later or not clean_agent:
            return False

        def write(connection: Any) -> bool:
            trace = self._agent_trace(connection, clean_agent)
            if trace is None:
                return False
            sequence_id = self._locked_trace(connection, clean_agent, actor_ref)
            current = connection.execute(
                "SELECT ordinal FROM sequence_segments WHERE sequence_id = %s AND segment_id = %s",
                (sequence_id, segment_id),
            ).fetchone()
            first = connection.execute(
                "SELECT MIN(ordinal) AS ordinal FROM sequence_segments WHERE sequence_id = %s AND segment_id = ANY(%s)",
                (sequence_id, later),
            ).fetchone()
            if current is None or first is None or first["ordinal"] is None:
                return False
            if int(first["ordinal"]) > int(current["ordinal"]):
                return False
            self._remove_trace_member(connection, sequence_id, segment_id)
            self._insert_trace_member(connection, sequence_id, int(first["ordinal"]), segment_id)
            self._touch_sequence(connection, sequence_id, str(actor_ref or ""))
            return True

        return self.database.write(write)


    def reset_provider_context(self, agent_id: str, *, reset_at: str, actor_ref: str) -> str:
        """Discard model context at a fresh restart while retaining chat history."""
        operation_id = stable_segment_id("provider_context_reset", agent_id, reset_at)
        def write(connection: Any) -> str:
            sequence_id = self._locked_trace(connection, agent_id, actor_ref)
            if connection.execute("SELECT 1 FROM agent_operations WHERE operation_id=%s", (operation_id,)).fetchone():
                return operation_id
            put_operation(connection, operation_id=operation_id, kind="provider_context_reset", agent_id=agent_id,
                          created_at=reset_at, data={"actor_ref": actor_ref})
            self._place_operation(connection, sequence_id, operation_id, next_position(connection, sequence_id), actor_ref)
            return operation_id
        return self.database.write(write)

    def record_context_reset(
        self, agent_id: str, *, reset_at: str, actor_ref: str,
        trace_position: int,
    ) -> str:
        """Record a clear action without adding model content to the trace."""
        if not agent_id or not reset_at:
            raise ValueError("A context reset needs its Agent and time")
        operation_id = stable_segment_id("context_reset", agent_id, reset_at)
        def write(connection: Any) -> str:
            sequence_id = self._locked_trace(connection, agent_id, actor_ref)
            if connection.execute("SELECT 1 FROM agent_operations WHERE operation_id=%s", (operation_id,)).fetchone():
                return operation_id
            # Public cutoffs count content occurrences; operations and
            # many-to-one transformations can leave gaps in ordinals.
            following = connection.execute(
                "SELECT ordinal FROM sequence_segments WHERE sequence_id=%s ORDER BY ordinal OFFSET %s LIMIT 1",
                (sequence_id, max(int(trace_position), 0)),
            ).fetchone()
            position = int(following["ordinal"]) if following else next_position(connection, sequence_id)
            connection.execute("UPDATE sequence_segments SET ordinal=ordinal+1000000000 WHERE sequence_id=%s AND ordinal>=%s", (sequence_id, position))
            connection.execute("UPDATE sequence_segments SET ordinal=ordinal-999999999 WHERE sequence_id=%s AND ordinal>1000000000", (sequence_id,))
            connection.execute("UPDATE operation_positions SET ordinal=ordinal+1 WHERE sequence_id=%s AND ordinal>=%s", (sequence_id, position))
            put_operation(connection, operation_id=operation_id, kind="context_reset", agent_id=agent_id,
                created_at=reset_at, data={"actor_ref": actor_ref})
            self._place_operation(connection, sequence_id, operation_id, position, actor_ref)
            chat_position_index.refresh_sequence(connection, sequence_id)
            return operation_id
        return self.database.write(write)

    def clear_chat(self, agent_id: str, *, actor_ref: str = "") -> str:
        """End an Agent's Chat here (a deleted Chatroom): a context reset after all it holds."""

        return self.record_context_reset(
            agent_id, reset_at=utc_now(), actor_ref=actor_ref,
            trace_position=2**31 - 1,
        )

    def _locked_trace(self, connection: Any, agent_id: str, actor_ref: str) -> str:
        trace = self._ensure_agent_trace(connection, agent_id, actor_ref=actor_ref)
        sequence_id = str(trace["sequence_id"])
        connection.execute("SELECT 1 FROM sequences WHERE sequence_id = %s FOR UPDATE", (sequence_id,))
        return sequence_id


    @classmethod
    def _insert_trace_member(cls, connection: Any, sequence_id: str, position: int, segment_id: str) -> None:
        # Two steps keep (sequence, ordinal) unique while later members move up by one.
        connection.execute(
            "UPDATE sequence_segments SET ordinal = ordinal + 1000000000 WHERE sequence_id = %s AND ordinal >= %s",
            (sequence_id, position),
        )
        connection.execute(
            "UPDATE sequence_segments SET ordinal = ordinal - 999999999 WHERE sequence_id = %s AND ordinal > 1000000000",
            (sequence_id,),
        )
        connection.execute(
            "INSERT INTO sequence_segments(sequence_id, ordinal, segment_id) VALUES (%s, %s, %s)",
            (sequence_id, position, segment_id),
        )
        connection.execute("UPDATE operation_positions SET ordinal=ordinal+1 WHERE sequence_id=%s AND ordinal>=%s", (sequence_id, position))
        cls._touch_sequence(connection, sequence_id, "portal")
        chat_position_index.refresh_sequence(connection, sequence_id)

    @classmethod
    def _remove_trace_member(cls, connection: Any, sequence_id: str, segment_id: str) -> None:
        row = connection.execute(
            "DELETE FROM sequence_segments WHERE sequence_id = %s AND segment_id = %s RETURNING ordinal",
            (sequence_id, segment_id),
        ).fetchone()
        if row is None:
            return
        connection.execute(
            "UPDATE sequence_segments SET ordinal = ordinal + 1000000000 WHERE sequence_id = %s AND ordinal > %s",
            (sequence_id, int(row["ordinal"])),
        )
        connection.execute(
            "UPDATE sequence_segments SET ordinal = ordinal - 1000000001 WHERE sequence_id = %s AND ordinal > 1000000000",
            (sequence_id,),
        )
        connection.execute("UPDATE operation_positions SET ordinal=ordinal-1 WHERE sequence_id=%s AND ordinal>%s", (sequence_id, int(row["ordinal"])))
        cls._touch_sequence(connection, sequence_id, "portal")
        chat_position_index.refresh_sequence(connection, sequence_id)

    def record_input_acceptance(
        self, execution_id: str, *, agent_id: str, provider_turn_id: str,
    ) -> None:
        turn_id = str(provider_turn_id or "").strip()
        if not turn_id:
            raise ValueError("Provider input acceptance requires a turn identity")

        def write(connection: Any) -> None:
            execution = connection.execute(
                'SELECT * FROM executions WHERE execution_id = %s FOR UPDATE',
                (execution_id,),
            ).fetchone()
            if execution is None or execution["agent_id"] != agent_id:
                raise KeyError(execution_id)
            if execution["provider_turn_id"] == turn_id:
                self._record_session_input_acceptance(connection, execution)
                return
            if execution["status"] != "running":
                raise ValueError("Execution terminal fact cannot be changed")
            connection.execute(
                'UPDATE executions SET provider_turn_id = %s WHERE execution_id = %s',
                (turn_id, execution_id),
            )
            self._record_session_input_acceptance(
                connection, {**dict(execution), "provider_turn_id": turn_id},
            )

        self.database.write(write)

    def finish_execution(self, execution_id: str, *, status: str, finished_at: str = "",
                         usage: Mapping[str, Any] | None = None, provider_turn_id: str = "",
                         only_if_running: bool = False) -> bool:
        return self.database.write(lambda connection: self.finish_execution_in(
            connection, execution_id, status=status, finished_at=finished_at, usage=usage,
            provider_turn_id=provider_turn_id, only_if_running=only_if_running))

    def finish_execution_in(self, connection: Any, execution_id: str, *, status: str,
                            finished_at: str = "", usage: Mapping[str, Any] | None = None,
                            provider_turn_id: str = "", only_if_running: bool = False) -> bool:
        """Finalize in a transaction whose search path addresses Agent tables."""
        clean_status = str(status or "").strip()
        if clean_status not in {"completed", "failed", "cancelled"}:
            raise ValueError(f"invalid terminal Execution status: {clean_status}")
        row = connection.execute(
            'SELECT * FROM executions WHERE execution_id = %s FOR UPDATE',
            (str(execution_id or ""),),
        ).fetchone()
        if row is None:
            raise KeyError(execution_id)
        desired_finished_at = instant(finished_at or row["finished_at"] or utc_now())
        desired_turn_id = str(provider_turn_id or row["provider_turn_id"] or "")
        desired_usage = _document(dict(usage or {}))
        if only_if_running and row["status"] != "running":
            return False
        if str(row["status"]) in {"completed", "failed", "cancelled"}:
            # Maintenance enumerates running rows before reading Scheduler.
            # A Worker may have finalized this row in that interval.
            if only_if_running:
                return False
            reported = json_value(row["usage_json"] or "{}")
            # The Scheduler's count is added after the Worker's terminal fact.
            reported.pop("accounted", None)
            current = (
                str(row["status"]),
                row["finished_at"],
                str(row["provider_turn_id"]),
                reported,
            )
            desired = (
                clean_status,
                desired_finished_at,
                desired_turn_id,
                json_value(desired_usage),
            )
            if current != desired:
                raise ValueError("Execution terminal fact cannot be changed")
            self._record_session_input_acceptance(connection, row)
            if row["final_message_id"] is None:
                self._refresh_finished_execution_pointer(connection, str(execution_id or ""))
            return False
        connection.execute(
            '\n                UPDATE executions\n                SET finished_at = %s, status = %s, usage_json = %s,\n                    provider_turn_id = %s\n                WHERE execution_id = %s\n                ',
            (
                desired_finished_at,
                clean_status,
                desired_usage,
                desired_turn_id,
                str(execution_id or ""),
            ),
        )
        self._record_session_input_acceptance(
            connection, {**dict(row), "provider_turn_id": desired_turn_id},
        )
        self._refresh_finished_execution_pointer(connection, str(execution_id or ""))
        return True

    def record_execution_output(self, execution_id: str, *, usage: Mapping[str, Any] | None = None,
                                provider_turn_id: str = "") -> None:
        """Persist Provider output metadata; the scheduler owns the terminal outcome."""
        def write(connection):
            row = connection.execute("SELECT * FROM executions WHERE execution_id=%s FOR UPDATE", (execution_id,)).fetchone()
            if row is None:
                raise KeyError(execution_id)
            if row["status"] in {"completed", "failed", "cancelled"}:
                return
            turn = provider_turn_id or row["provider_turn_id"]
            connection.execute("UPDATE executions SET usage_json=%s,provider_turn_id=%s WHERE execution_id=%s",
                               (_document(dict(usage or {})),turn,execution_id))
            self._record_session_input_acceptance(connection,{**dict(row),"provider_turn_id":turn})
        self.database.write(write)

    def record_accounted_usage(self, execution_id: str, usage: Mapping[str, Any]) -> bool:
        """Keep what the Scheduler counted for an ended Execution's Provider requests.

        One Execution can run several Provider turns (a message sent into the
        running turn, a continuation, a Worker handover) and the Provider's own
        report covers only the last one; the Scheduler counted every request.
        """

        counted = {key: value for key, value in dict(usage or {}).items() if isinstance(value, int) and value > 0}
        if not counted:
            return False

        def write(connection: Any) -> bool:
            row = connection.execute(
                'SELECT status, usage_json FROM executions WHERE execution_id = %s FOR UPDATE',
                (str(execution_id or ""),),
            ).fetchone()
            if row is None or str(row["status"]) == "running":
                return False
            usage_payload = json_value(row["usage_json"] or "{}")
            if usage_payload.get("accounted") == counted:
                return False
            usage_payload["accounted"] = counted
            connection.execute(
                'UPDATE executions SET usage_json = %s WHERE execution_id = %s',
                (_document(usage_payload), str(execution_id or "")),
            )
            return True

        return self.database.write(write)

    def provider_session_executions(self, provider: str, *, active_since: str) -> list[dict[str, Any]]:
        """The Provider sessions with an Execution running or ended since ``active_since``.

        Each session lists its route, when Portal recorded it, the bindings
        that use it and the time span of each of those Executions.
        """

        with self.database.read() as connection:
            rows = connection.execute(
                "SELECT session.backend_session_id, session.backend_route_id, session.created_at AS session_created_at, "
                "execution.agent_id, execution.started_at, execution.finished_at, "
                "ARRAY(SELECT binding.binding_id FROM provider_bindings binding "
                "WHERE binding.provider_session_ref = session.provider_session_ref ORDER BY binding.binding_id) AS binding_ids "
                "FROM executions execution JOIN provider_sessions session USING (provider_session_ref) "
                "WHERE execution.provider = %s AND session.backend_session_id <> '' "
                "AND (execution.finished_at IS NULL OR execution.finished_at >= %s) "
                "ORDER BY session.backend_session_id, execution.started_at",
                (provider, active_since),
            ).fetchall()
        sessions: dict[str, dict[str, Any]] = {}
        for row in rows:
            session = sessions.setdefault(str(row["backend_session_id"]), {
                "backend_session_id": str(row["backend_session_id"]),
                "backend_route_id": str(row["backend_route_id"] or ""),
                "agent_id": str(row["agent_id"]),
                "created_at": (iso_time(row["session_created_at"]) or ""),
                "binding_ids": [str(binding) for binding in row["binding_ids"] or ()],
                "executions": [],
            })
            session["executions"].append(((iso_time(row["started_at"]) or ""), (iso_time(row["finished_at"]) or "")))
        return list(sessions.values())

    @staticmethod
    def _execution_record(row: Any) -> dict[str, Any]:
        return {
            "execution_id": str(row["execution_id"]),
            "agent_id": str(row["agent_id"]),
            "actor_ref": str(row["actor_ref"]),
            "provider": str(row["provider"]),
            "provider_session_ref": str(row["provider_session_ref"] or ""),
            "provider_turn_id": str(row["provider_turn_id"]),
            "created_at": (iso_time(row["created_at"]) or ""),
            "started_at": (iso_time(row["started_at"]) or ""),
            "finished_at": (iso_time(row["finished_at"]) or ""),
            "status": str(row["status"]),
            "usage": json_value(row["usage_json"] or "{}"),
        }

    def execution(self, execution_id: str) -> dict[str, Any] | None:
        with self.database.snapshot() as connection:
            row = connection.execute(
                'SELECT * FROM executions WHERE execution_id = %s',
                (str(execution_id or ""),),
            ).fetchone()
            if row is None:
                return None
            submission = connection.execute(
                "SELECT created_at,data_json FROM agent_operations WHERE operation_id=%s "
                "AND kind='execution_submission' AND agent_id=%s",
                ("execution_submission:" + str(execution_id), row["agent_id"]),
            ).fetchone()
            facts = json_value(submission["data_json"]) if submission is not None else {}
            return {**self._execution_record(row),
                    "service_type": facts.get("service_type"), "operation": facts.get("operation"),
                    "submitted_at": iso_time(submission["created_at"]) if submission is not None else None,
                    "inputs": execution_inputs.read_in(connection, str(execution_id))}

    def binding_has_accepted_input(self, binding_id: str, *, agent_id: str) -> bool:
        with self.database.snapshot() as connection:
            row = connection.execute(
                'SELECT provider_session_ref FROM provider_bindings WHERE binding_id = %s',
                (binding_id,),
            ).fetchone()
            return row is not None and self._session_input_acceptance(
                connection, str(row["provider_session_ref"]), agent_id,
            ) is not None

    def running_executions(self) -> list[dict[str, Any]]:
        with self.database.read() as connection:
            rows = connection.execute(
                """
                SELECT * FROM executions
                WHERE status = 'running'
                ORDER BY started_at, execution_id
                """
            ).fetchall()
        return [self._execution_record(row) for row in rows]


    # -- Chat: the messages of an Agent's trace ------------------------------
    #
    # An Agent's Chat is not stored apart from its trace: it is the trace's
    # message Segments (``CHAT_SEGMENT``) and notices after its last context
    # reset, in trace order, as the trace's message projection places them
    # (chat_position_index).  A message's position (``ordinal``) is its trace position.
    # A message that waits in a Resident's Mailbox is not here yet: it enters
    # the trace when it is delivered.

    @staticmethod
    def _chat_payload(segment_id: object, metadata_json: object, content: Any = "") -> dict[str, Any]:
        """The message a Chat Segment carries, with that Segment's identity."""

        metadata = json_value(metadata_json or "{}")
        return project_message(segment_id, metadata.get("message") or {}, content)

    @staticmethod
    def _trace_id(connection: Any, agent_id: object) -> str:
        row = connection.execute(
            "SELECT trace_sequence_id FROM agents WHERE agent_id = %s", (str(agent_id or ""),),
        ).fetchone()
        return str(row["trace_sequence_id"]) if row is not None else ""

    @staticmethod
    def _trace_ids(connection: Any, agent_ids: Sequence[str]) -> dict[str, str]:
        """Each Agent's trace; an Agent that never had one is left out."""

        traces: dict[str, str] = {}
        for chunk in _batches(list(agent_ids)):
            for row in connection.execute(
                "SELECT agent_id, trace_sequence_id FROM agents WHERE agent_id = ANY(%s)", (list(chunk),),
            ).fetchall():
                traces[str(row["agent_id"])] = str(row["trace_sequence_id"])
        return traces

    def _chat_traces(self, agent_ids: Sequence[str]) -> dict[str, str]:
        """Each Agent's trace, its message projection current; an Agent that never had one is left out.

        Chat reads only the projection (chat_position_index).  A trace whose
        mark is behind its Sequence row is rebuilt from the locked trace
        first.  Called in a read, it uses that read's connection, and a
        rebuild joins its transaction: the read's next statements see it.  A
        snapshot must open after it.
        """
        requested = list(dict.fromkeys(str(item) for item in agent_ids if str(item)))
        traces: dict[str, str] = {}
        behind: list[str] = []
        with self.database.read() as connection:
            for chunk in _batches(requested):
                for row in connection.execute(
                    "SELECT agent.agent_id, agent.trace_sequence_id, "
                    f"{chat_position_index.behind('agent.trace_sequence_id')} AS behind "
                    "FROM agents agent WHERE agent.agent_id = ANY(%s)", (list(chunk),),
                ).fetchall():
                    traces[str(row["agent_id"])] = str(row["trace_sequence_id"])
                    if row["behind"]:
                        behind.append(str(row["trace_sequence_id"]))
        self._bring_current(behind)
        return traces

    def _chat_trace(self, agent_id: str) -> str:
        return self._chat_traces([agent_id]).get(str(agent_id), "")

    def _bring_current(self, sequence_ids: Iterable[str]) -> None:
        for sequence_id in sequence_ids:
            self.database.write(lambda connection, sequence_id=sequence_id: (
                chat_position_index.bring_current(connection, sequence_id)
            ))

    def bring_chat_current(self) -> None:
        """Bring every Agent trace's message projection current, before a listing reads them all."""
        with self.database.read() as connection:
            behind = chat_position_index.lagging(connection)
        self._bring_current(behind)

    @staticmethod
    def _chat_floor(connection: Any, sequence_id: str) -> int:
        row = connection.execute(
            "SELECT COALESCE(MAX(position.ordinal),0) AS ordinal FROM operation_positions position "
            "JOIN agent_operations operation USING(operation_id) WHERE position.sequence_id=%s AND operation.kind='context_reset'",
            (sequence_id,),
        ).fetchone()
        return int(row["ordinal"])

    @staticmethod
    def _chat_floors(connection: Any, sequence_ids: Iterable[str]) -> dict[str, int]:
        rows = connection.execute(
            "SELECT position.sequence_id, MAX(position.ordinal) AS ordinal FROM operation_positions position "
            "JOIN agent_operations operation USING(operation_id) WHERE position.sequence_id=ANY(%s) "
            "AND operation.kind='context_reset' GROUP BY position.sequence_id", (list(sequence_ids),),
        ).fetchall()
        return {str(row["sequence_id"]): int(row["ordinal"]) for row in rows}

    @staticmethod
    def _chat_rows(
        connection: Any,
        sequence_id: str,
        *,
        after: int = 0,
        before: int | None = None,
        limit: int = 0,
        user_only: bool = False,
        sender: str = "",
    ) -> list[Any]:
        """Chat members between two trace positions, oldest first: the newest ``limit`` of them.

        ``sender`` keeps only the inputs that User typed.  The trace's
        projection must be current (``_chat_traces``).
        """

        if not sequence_id:
            return []
        clauses = ["position.sequence_id=%s", "position.source_kind<>'sync'", "position.ordinal>%s"]
        parameters: list[Any] = [sequence_id, int(after)]
        if before is not None:
            clauses.append("position.ordinal<%s")
            parameters.append(int(before))
        if user_only or sender:
            clauses.append("position.segment_type='input'")
        if sender:
            clauses.append("lower(position.actor_ref)=lower(%s)")
            parameters.append(sender)
            clauses.append(TYPED_BY_USER.format(metadata=_POSITION_METADATA))
        clauses.append(_SHOWN_POSITION)
        limit_clause = "LIMIT %s" if limit else ""
        if limit:
            parameters.append(int(limit))
        return list(connection.execute(
            f"SELECT {_POSITION_ROW} FROM (SELECT * FROM chat_message_positions position "
            f"WHERE {' AND '.join(clauses)} ORDER BY position.ordinal DESC {limit_clause}) position "
            "ORDER BY position.ordinal",
            parameters,
        ).fetchall())

    @staticmethod
    def _chat_member(connection: Any, sequence_id: str, message_id: str) -> Any | None:
        """A message's newest shown position in a trace, wherever it stands there.

        The trace's projection must be current: readers bring it current
        first (``_chat_traces``), writers under the trace lock (``_current_member``).
        """

        if not sequence_id or not message_id:
            return None
        return connection.execute(
            f"SELECT {_POSITION_ROW} FROM chat_message_positions position "
            "WHERE position.message_id=%s AND position.sequence_id=%s AND position.source_kind<>'sync' "
            f"AND {_SHOWN_POSITION} ORDER BY position.ordinal DESC LIMIT 1",
            (message_id, sequence_id),
        ).fetchone()

    @classmethod
    def _current_member(cls, connection: Any, sequence_id: str, message_id: str) -> Any | None:
        """``_chat_member`` in a write, the trace locked and its projection brought current first."""

        if sequence_id:
            chat_position_index.bring_current(connection, sequence_id)
        return cls._chat_member(connection, sequence_id, message_id)

    @classmethod
    def _shown_member(cls, connection: Any, sequence_id: str, message_id: str) -> Any | None:
        """A message's position while it is in the trace's Chat (after the last reset)."""

        row = cls._chat_member(connection, sequence_id, str(message_id or "").strip())
        if row is None or int(row["ordinal"]) <= cls._chat_floor(connection, sequence_id):
            return None
        return row

    @staticmethod
    def _same_message(stored: Mapping[str, Any], requested: Mapping[str, Any]) -> None:
        if (
            _chat_message_text(stored) != _chat_message_text(requested)
            or str(stored.get("role") or "") != str(requested.get("role") or "")
            or str(stored.get("author_id") or "") != str(requested.get("author_id") or "")
        ):
            raise ValueError("Chat message identity refers to different immutable content")
        # How it was delivered (with a request, in a group) is the delivery's to say.
        ignored = {"activated_at", "segment_id", "released_with", "grouped_into_message_id", "business_ref"}
        if (
            {key: value for key, value in stored.items() if key not in ignored}
            != {key: value for key, value in requested.items() if key not in ignored}
        ):
            raise ValueError("Chat message metadata changed without an explicit update")

    @classmethod
    def _check_tool_source(cls, connection: Any, agent_id: str, message: Mapping[str, Any]) -> None:
        """A tool or turn card shows a Segment of the Agent's own work, exactly as recorded."""

        tool_parts = [
            part for part in message["content_parts"]
            if part.get("type") in {"tool_call", "tool_result", "native_turn", "resource_changes", "subagent_run"}
            and part.get("segment_id")
        ]
        if message["role"] != "agent" or len(tool_parts) != 1:
            return
        part = tool_parts[0]
        source = (cls._operation_record if part["type"] in {"native_turn", "resource_changes", "subagent_run"} else cls._read_segment_row)(connection, str(part["segment_id"]))
        expected_type = {
            "native_turn": "model", "resource_changes": "artifact", "subagent_run": "artifact",
        }.get(part.get("type"), "tool")
        if (
            source.get("segment_type") != expected_type
            or source.get("created_by_agent_id") != agent_id
            or source.get("content") != part
        ):
            raise ValueError("tool message must reference its own canonical Segment")

    @classmethod
    def _append_chat_message(
        cls,
        connection: Any,
        agent_id: str,
        message: Mapping[str, Any],
        *,
        activated_at: str = "",
    ) -> tuple[dict[str, Any], bool]:
        """Put one message at the end of the Agent's trace; a message already there is returned as it is."""

        canonical = _canonical_chat_message(message)
        message_id = str(canonical["message_id"])
        role = str(canonical["role"])
        actor_ref = str(canonical.get("author_id") or "")
        canonical["created_at"] = str(canonical.get("created_at") or utc_now())
        sequence = cls._ensure_agent_trace(connection, agent_id, actor_ref=actor_ref)
        sequence_id = str(sequence["sequence_id"])
        # Appends of one message serialize on its trace.
        connection.execute("SELECT 1 FROM sequences WHERE sequence_id = %s FOR UPDATE", (sequence_id,))
        existing = cls._current_member(connection, sequence_id, message_id)
        captured = None
        if existing is None and agent_id.startswith("resident_") and role == "agent" and canonical.get("execution_id"):
            candidates = connection.execute(
                "SELECT segment_id,metadata_json FROM segments WHERE execution_id=%s "
                "AND segment_type='model' AND metadata_json::jsonb #>> '{message,message_id}'=%s "
                "AND NOT (metadata_json::jsonb ? 'subagent') ORDER BY finished_at,segment_id",
                (canonical["execution_id"], message_id),
            ).fetchall()
            if candidates:
                contents = cls._message_contents(connection, [row["segment_id"] for row in candidates])
                # Several captured Provider finals can share the canonical final
                # message ID. Deliver the selected content, never an arbitrary row.
                selected = _answer_text(agent_id, canonical)
                captured = next((row for row in candidates if _answer_text(agent_id, cls._chat_payload(
                    row["segment_id"], row["metadata_json"], contents[row["segment_id"]],
                )) == selected), None)
                if captured is None:
                    raise ValueError("Captured message does not match the selected content")
        extras_id = str(existing["segment_id"]) if existing else (
            str(captured["segment_id"]) if captured else chat_segment_id(agent_id, message_id)
        )
        if canonical.get("provider_response") and captured is None:
            put_operation(connection, operation_id="message_artifact:" + extras_id, kind="message_artifact", agent_id=agent_id,
                created_at=canonical["created_at"], data={"provider_response": canonical["provider_response"]})
        if existing is not None:
            stored = cls._chat_payload(existing["segment_id"], existing["metadata_json"], cls._message_contents(connection, [existing["segment_id"]])[existing["segment_id"]])
            if role == "user" and _chat_message_text(stored) != _chat_message_text(canonical):
                transformed = connection.execute("SELECT 1 FROM segment_relations WHERE target_segment_id=%s LIMIT 1", (existing["segment_id"],)).fetchone()
                if not transformed:
                    cls._same_message(stored, canonical)
            return stored, False
        consumed = cls._group_source_members(connection, sequence_id, [message_id]) if role == "user" else []
        if consumed:
            # Grouping removed this occurrence from the trace, not history.
            # A delivery replay validates and reuses the accepted source; it
            # cannot rewrite it or resurrect it in the current Chat.
            source = consumed[-1]
            stored = cls._chat_payload(source["segment_id"], source["metadata_json"],
                                       cls._message_contents(connection, [source["segment_id"]])[source["segment_id"]])
            cls._same_message(stored, canonical)
            return stored, False
        cls._check_tool_source(connection, agent_id, canonical)
        canonical["activated_at"] = str(activated_at or canonical.get("activated_at") or utc_now())
        if captured is not None:
            # The Execution collector saved immutable content, but did not
            # deliver it to this Resident's Chat. This consumer now places the
            # captured Segment in the trace, then its caller publishes it.
            segment_id = str(captured["segment_id"])
            display = {
                key: canonical[key]
                for key in ("content_parts", "origin", "usage", "activated_at", "provider_response")
                if key in canonical
            }
            put_operation(
                connection, operation_id="message_artifact:" + segment_id,
                kind="message_artifact", agent_id=agent_id,
                created_at=canonical["created_at"], data=display,
            )
            cls._append_sequence_ids(
                connection, sequence=sequence, segment_ids=[segment_id], actor_ref=actor_ref,
            )
            stored = cls._chat_payload(
                segment_id, captured["metadata_json"],
                cls._message_contents(connection, [segment_id])[segment_id],
            )
            return {**stored, **display}, True
        segment_type = _message_role_segment_type(role)
        fields = {
            "segment_type": segment_type,
            "content": wrap_message(_chat_message_text(canonical)) if role == "user" else _chat_message_text(canonical),
            "started_at": canonical["created_at"] if segment_type != "model" else "",
            "finished_at": canonical["created_at"],
            "status": "completed",
            "created_by_actor_ref": actor_ref,
            "performed_by_actor_ref": actor_ref if segment_type != "model" else "portal",
            "content_type": "text/plain; charset=utf-8",
            "metadata": {"role": role, "kind": "user_message" if role == "user" else "assistant_message" if role == "agent" else "portal_notice", "message": message_facts(canonical)},
            "created_by_agent_id": agent_id,
            "execution_id": str(canonical.get("execution_id") or ""),
        }
        segment_id = chat_segment_id(agent_id, message_id)
        parts = canonical["content_parts"]
        # Visible model text enters the trace here, through the Conversation
        # consumer. Structured notices without text remain operations.
        operational = (role == "system" and not canonical.get("harness_input")) or (role == "agent" and not _chat_message_text(canonical)) or any(
            part.get("type") in {"native_turn", "resource_changes", "subagent_run"} for part in parts)
        if operational:
            put_operation(connection, operation_id=segment_id, kind="notice", agent_id=agent_id,
                created_at=canonical["created_at"], data=fields)
            cls._place_operation(connection, sequence_id, segment_id, next_position(connection, sequence_id), actor_ref)
            return {**canonical, "segment_id": segment_id}, True
        cls._insert_segment(connection, segment_id=segment_id, **fields)
        cls._append_sequence_ids(connection, sequence=sequence, segment_ids=[segment_id], actor_ref=actor_ref)
        return {**canonical, "segment_id": segment_id}, True

    def display_chat_message(
        self,
        agent_id: str,
        message: Mapping[str, Any],
    ) -> dict[str, Any]:
        projected, _changed = self.project_chat_message(agent_id, message)
        return projected

    def put_operation_record(self, *, segment_id: str, created_by_agent_id: str,
                             created_at: str = "", **fields: Any) -> str:
        """Store a UI artifact as an operational fact, outside Segments."""
        data = {**fields, "segment_id": segment_id, "created_by_agent_id": created_by_agent_id,
                "created_at": created_at or utc_now()}
        return self.database.write(lambda connection: put_operation(
            connection, operation_id=segment_id, kind="artifact", agent_id=created_by_agent_id,
            created_at=data["created_at"], data=data))

    def operation_record(self, operation_id: str) -> dict[str, Any]:
        with self.database.read() as connection:
            return self._operation_record(connection, operation_id)

    @staticmethod
    def _operation_record(connection: Any, operation_id: str) -> dict[str, Any]:
        row = connection.execute("SELECT data_json FROM agent_operations WHERE operation_id=%s", (operation_id,)).fetchone()
        if row is None:
            raise KeyError(operation_id)
        return json_value(row["data_json"])

    def project_chat_message(
        self,
        agent_id: str,
        message: Mapping[str, Any],
        *,
        activated_at: str = "",
    ) -> tuple[dict[str, Any], bool]:
        """Append a message to the Agent's Chat; returns it and whether it was new."""

        def write(connection: Any) -> tuple[dict[str, Any], bool]:
            projected, changed = self._append_chat_message(
                connection, agent_id, message, activated_at=activated_at,
            )
            if changed:
                self._refresh_finished_execution_pointer(connection, str(projected.get("execution_id") or ""))
            return projected, changed

        return self.database.write(write)

    def merge_message_group(self, agent_id: str, message_ids: Sequence[str], target_id: str) -> None:
        """A composed message consumes its members in the current trace."""
        def write(connection: Any) -> None:
            sequence_id = self._locked_trace(connection, agent_id, "portal")
            consumed = {row["member_id"]: row for row in self._group_source_members(connection, sequence_id, message_ids)}
            sources = [self._current_member(connection, sequence_id, identity) or consumed.get(identity) for identity in message_ids]
            sources = [row for row in sources if row is not None and row["segment_id"] != target_id]
            target = connection.execute("SELECT ordinal FROM sequence_segments WHERE sequence_id=%s AND segment_id=%s", (sequence_id, target_id)).fetchone()
            if not sources or target is None:
                return
            at = utc_now()
            for source in sources:
                source_id = str(source["segment_id"])
                relation_id = stable_segment_id("group", source_id, target_id).replace("segment_", "relation_", 1)
                connection.execute("INSERT INTO segment_relations VALUES (%s,'merged_from',%s,%s,%s,'message.group','1',%s,'portal','{}') ON CONFLICT DO NOTHING",
                                   (relation_id, source_id, target_id, target_id, at))
            position = min([int(target["ordinal"]), *[int(row["ordinal"]) for row in sources if "ordinal" in row.keys()]])
            connection.execute("DELETE FROM sequence_segments WHERE sequence_id=%s AND segment_id=ANY(%s)",
                               (sequence_id, [target_id, *[row["segment_id"] for row in sources]]))
            connection.execute("INSERT INTO sequence_segments VALUES (%s,%s,%s)", (sequence_id, position, target_id))
            self._touch_sequence(connection, sequence_id, "portal")
            chat_position_index.refresh_sequence(connection, sequence_id)
        self.database.write(write)

    def update_chat_message(
        self,
        agent_id: str,
        message_id: str,
        message: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Replace a message where it stands in the trace by a new version of it."""

        def write(connection: Any) -> dict[str, Any]:
            sequence_id = self._trace_id(connection, agent_id)
            row = self._current_member(connection, sequence_id, message_id)
            if row is None:
                raise KeyError(message_id)
            previous = self._chat_payload(row["segment_id"], row["metadata_json"], self._message_contents(connection, [row["segment_id"]])[row["segment_id"]])
            canonical = _canonical_chat_message(message)
            if str(canonical["message_id"]) != message_id:
                raise ValueError("Chat message update changed identity")
            source_segment_id = str(row["segment_id"])
            canonical["created_at"] = str(canonical.get("created_at") or utc_now())
            if not canonical.get("activated_at") and previous.get("activated_at"):
                canonical["activated_at"] = previous["activated_at"]
            content = _chat_message_text(canonical)
            if canonical["role"] == "user":
                content = wrap_message(content)
            metadata = {"role": str(canonical["role"]), "kind": "user_message" if canonical["role"] == "user" else "assistant_message" if canonical["role"] == "agent" else "portal_notice", "message": message_facts(canonical)}
            digest = hashlib.sha256(
                (content + "\0" + canonical_json(canonical)).encode("utf-8")
            ).hexdigest()[:24]
            target_segment_id = stable_segment_id(
                "chat-transform", agent_id, message_id, digest
            )
            role = str(canonical["role"])
            actor_ref = str(canonical.get("author_id") or "")
            created_at = str(canonical["created_at"])
            if target_segment_id == source_segment_id:
                return {**canonical, "segment_id": source_segment_id}
            notice = connection.execute("SELECT 1 FROM agent_operations WHERE operation_id=%s AND kind='notice'", (source_segment_id,)).fetchone()
            if notice:
                fields = {"segment_type": _message_role_segment_type(role), "content": content,
                          "metadata": metadata, "created_by_actor_ref": actor_ref,
                          "execution_id": str(canonical.get("execution_id") or "")}
                put_operation(connection, operation_id=target_segment_id, kind="notice", agent_id=agent_id,
                              created_at=created_at, data=fields)
                connection.execute("UPDATE operation_positions SET operation_id=%s WHERE sequence_id=%s AND operation_id=%s",
                                   (target_segment_id, sequence_id, source_segment_id))
                self._touch_sequence(connection, sequence_id, actor_ref)
                chat_position_index.refresh_sequence(connection, sequence_id)
                return {**canonical, "segment_id": target_segment_id}
            self._insert_segment(
                connection,
                segment_id=target_segment_id,
                segment_type=_message_role_segment_type(role),
                content=content,
                started_at="",
                finished_at=created_at,
                status="completed",
                created_by_actor_ref=actor_ref,
                performed_by_actor_ref="portal",
                content_type="text/plain; charset=utf-8",
                metadata=metadata,
                created_by_agent_id=agent_id,
                execution_id=str(canonical.get("execution_id") or ""),
            )
            operation_id = _identifier("operation")
            relation_id = hashlib.sha256(
                f"transformed_from\0{source_segment_id}\0{target_segment_id}\0{operation_id}".encode()
            ).hexdigest()[:32]
            connection.execute(
                "\n                INSERT INTO segment_relations(\n                    relation_id, relation_type, source_segment_id,\n                    target_segment_id, operation_id, operation_type,\n                    operation_version, created_at, actor_ref, metadata_json\n                ) VALUES (%s, 'transformed_from', %s, %s, %s, 'chat.message.update',\n                          '1', %s, %s, '{}')\n                ",
                (
                    f"relation_{relation_id}",
                    source_segment_id,
                    target_segment_id,
                    operation_id,
                    utc_now(),
                    actor_ref,
                ),
            )
            connection.execute("SELECT 1 FROM sequences WHERE sequence_id = %s FOR UPDATE", (sequence_id,))
            connection.execute(
                'UPDATE sequence_segments SET segment_id = %s WHERE sequence_id = %s AND segment_id = %s',
                (target_segment_id, sequence_id, source_segment_id),
            )
            self._touch_sequence(connection, sequence_id, actor_ref)
            chat_position_index.refresh_sequence(connection, sequence_id)
            return {**canonical, "segment_id": target_segment_id}

        return self.database.write(write)

    def displayed_chat_message_ids(self, agent_id: str) -> list[str]:
        """The messages a clear now would end, oldest first, without their bodies."""

        with self.database.read() as connection:
            sequence_id = self._chat_trace(agent_id)
            if not sequence_id:
                return []
            floor = self._chat_floor(connection, sequence_id)
            return [
                str(row["message_id"])
                for row in connection.execute(
                    "SELECT position.message_id FROM chat_message_positions position "
                    "WHERE position.sequence_id = %s AND position.source_kind <> 'sync' AND position.ordinal > %s "
                    f"AND {_SHOWN_POSITION} ORDER BY position.ordinal",
                    (sequence_id, floor),
                ).fetchall()
            ]

    @classmethod
    def _message_contents(cls, connection: Any, identities: Sequence[str]) -> dict[str, Any]:
        real_ids = cls._existing_segment_ids(connection, identities)
        contents = {item["segment_id"]: item["content"] for item in cls._read_segment_rows(connection, list(real_ids))}
        remaining = [item for item in identities if item not in real_ids]
        if remaining:
            for row in connection.execute("SELECT operation_id, data_json FROM agent_operations WHERE operation_id=ANY(%s)", (remaining,)).fetchall():
                contents[str(row["operation_id"])] = json_value(row["data_json"]).get("content", "")
        return contents

    def _chat_from_rows(self, rows: Sequence[Any]) -> list[dict[str, Any]]:
        if not rows:
            return []
        from .scheduler import normalize_token_usage
        metadata = {str(row["segment_id"]): json_value(row["metadata_json"]) for row in rows}
        with self.database.read() as connection:
            contents = self._message_contents(connection, [row["segment_id"] for row in rows])
            executions = list({item.get("message", {}).get("execution_id") for item in metadata.values()
                               if item.get("message", {}).get("execution_id")})
            endings = connection.execute(
                "SELECT execution_id,usage_json,final_message_id AS message_id "
                "FROM executions WHERE execution_id=ANY(%s) AND status IN ('completed','failed','cancelled')",
                (executions,),
            ).fetchall() if executions else []
            usage = {}
            for ending in endings:
                if not ending["message_id"]:
                    continue
                raw_usage = json_value(ending["usage_json"])
                usage[(str(ending["execution_id"]), str(ending["message_id"]))] = {
                    **normalize_token_usage(raw_usage.get("accounted") or raw_usage),
                    **{key: raw_usage[key] for key in ("api_id", "model")
                       if isinstance(raw_usage.get(key), str) and raw_usage[key]},
                }
            extras = {str(row["operation_id"]).removeprefix("message_artifact:"): json_value(row["data_json"]) for row in connection.execute(
                "SELECT operation_id,data_json FROM agent_operations WHERE operation_id=ANY(%s)",
                (["message_artifact:" + str(row["segment_id"]) for row in rows],),
            ).fetchall()}
        messages = [self._chat_payload(row["segment_id"], row["metadata_json"], contents[row["segment_id"]]) for row in rows]
        for message in messages:
            message.update(extras.get(message["segment_id"], {}))
            counted = usage.get((message.get("execution_id"), message.get("message_id")))
            if counted:
                message["usage"] = counted
        return messages

    @staticmethod
    def _execution_final_reply_rows(connection: Any, executions: Sequence[str]) -> list[Any]:
        """The final reply of each terminal Execution, which its writes record (executions.final_message_id).

        It reads the trace itself, not its message projection: the write that
        ends an Execution holds the Execution and not its trace.
        """
        if not executions:
            return []
        # Resolve against the Execution's complete current trace, never the
        # requested page. A partial fork must not turn an inherited progress
        # message into the parent Execution's final reply.  A final answer
        # repeating a message earlier in the trace is not shown (_SHOWN_POSITION).
        return connection.execute(
                "WITH selected_executions AS MATERIALIZED ("
                "SELECT execution_id,usage_json,status,trace_sequence_id FROM executions "
                "JOIN agents USING(agent_id) WHERE execution_id=ANY(%s) AND status IN ('completed','failed','cancelled')), "
                "replies AS ("
                "SELECT execution.*,segment.metadata_json,member.ordinal FROM selected_executions execution "
                "JOIN LATERAL (SELECT segment_id,metadata_json::jsonb AS metadata_json FROM segments segment "
                f"WHERE segment.execution_id=execution.execution_id AND segment.execution_id<>'' AND {CHAT_SEGMENT} OFFSET 0) segment ON TRUE "
                "JOIN LATERAL (SELECT ordinal FROM sequence_segments member "
                "WHERE member.sequence_id=execution.trace_sequence_id AND member.segment_id=segment.segment_id OFFSET 0) member ON TRUE "
                "WHERE segment.metadata_json->>'kind' IS DISTINCT FROM 'final_answer' OR NOT EXISTS ("
                "SELECT 1 FROM LATERAL (SELECT segment_id FROM segments candidate "
                f"WHERE {CHAT_PREDICATE} AND metadata_json::jsonb#>>'{{message,message_id}}'=segment.metadata_json#>>'{{message,message_id}}' "
                "OFFSET 0) previous JOIN sequence_segments earlier ON earlier.segment_id=previous.segment_id "
                "AND earlier.sequence_id=execution.trace_sequence_id AND earlier.ordinal<member.ordinal) "
                "UNION ALL SELECT execution.*, operation.data_json::jsonb->'metadata',position.ordinal "
                "FROM selected_executions execution JOIN operation_positions position "
                "ON position.sequence_id=execution.trace_sequence_id "
                "JOIN agent_operations operation USING(operation_id) "
                "WHERE operation.kind='notice' AND operation.data_json::jsonb->>'execution_id'=execution.execution_id) "
                "SELECT DISTINCT ON (execution_id) execution_id,usage_json, "
                "segment.metadata_json::jsonb#>>'{message,message_id}' AS message_id FROM replies segment "
                f"WHERE {REPLY_SEGMENT} "
                "AND (segment.metadata_json::jsonb#>>'{message,role}'='agent' OR "
                "segment.metadata_json::jsonb->>'kind'='portal_notice') "
                "ORDER BY execution_id, CASE WHEN status='completed' AND "
                "segment.metadata_json::jsonb#>>'{message,role}'='agent' THEN 0 "
                "WHEN status='completed' THEN 1 ELSE 0 END, ordinal DESC",
                (list(executions),),
            ).fetchall()

    def _refresh_finished_execution_pointer(self, connection: Any, execution_id: str) -> None:
        if not execution_id:
            return
        terminal = connection.execute(
            "SELECT 1 FROM executions WHERE execution_id=%s AND status IN ('completed','failed','cancelled')",
            (execution_id,),
        ).fetchone()
        if terminal is None:
            return
        rows = self._execution_final_reply_rows(connection, [execution_id])
        if not rows:
            # Running Executions have no final reply. A terminal Execution may
            # legitimately have no displayed answer; the empty string records
            # that the projection was checked, unlike NULL (not backfilled).
            connection.execute(
                "UPDATE executions SET final_message_id='' WHERE execution_id=%s "
                "AND status IN ('completed','failed','cancelled') AND final_message_id IS DISTINCT FROM ''",
                (execution_id,),
            )
            return
        connection.execute(
            "UPDATE executions SET final_message_id=%s WHERE execution_id=%s "
            "AND status IN ('completed','failed','cancelled') AND final_message_id IS DISTINCT FROM %s",
            (str(rows[0]["message_id"] or ""), execution_id, str(rows[0]["message_id"] or "")),
        )

    def read_chat_messages(
        self,
        agent_id: str,
        *,
        limit: int = 0,
    ) -> list[dict[str, Any]]:
        """The Agent's Chat, oldest first; the newest ``limit`` messages when given."""

        with self.database.read() as connection:
            sequence_id = self._chat_trace(agent_id)
            if not sequence_id:
                return []
            return self._chat_from_rows(self._chat_rows(
                connection, sequence_id, after=self._chat_floor(connection, sequence_id),
                limit=max(0, int(limit)),
            ))

    def read_chat_messages_many(self, agent_ids: Sequence[str]) -> dict[str, list[dict[str, Any]]]:
        """Read current Chats together, retaining each trace's occurrence order.

        Search consumes bounded batches through this owner, using the same Chat
        policy and content projection as a single conversation. Database reads
        for traces, reset positions, content and usage are shared by the batch.
        """
        requested = list(dict.fromkeys(agent_ids))
        messages: dict[str, list[dict[str, Any]]] = {agent_id: [] for agent_id in requested}
        if not requested:
            return messages
        with self.database.read() as connection:
            traces = self._chat_traces(requested)
            if not traces:
                return messages
            floors = self._chat_floors(connection, traces.values())
            rows = connection.execute(
                "WITH requested AS (SELECT * FROM unnest(%s::text[], %s::bigint[]) "
                "AS wanted(sequence_id, floor)) "
                f"SELECT requested.sequence_id, {_POSITION_ROW} "
                "FROM requested CROSS JOIN LATERAL ("
                "SELECT * FROM chat_message_positions position "
                "WHERE position.sequence_id=requested.sequence_id AND position.source_kind<>'sync' "
                f"AND position.ordinal>requested.floor AND {_SHOWN_POSITION} OFFSET 0"
                ") position ORDER BY requested.sequence_id, position.ordinal",
                (list(traces.values()), [floors.get(trace, 0) for trace in traces.values()]),
            ).fetchall()
            projected = self._chat_from_rows(rows)
        by_trace: dict[str, list[dict[str, Any]]] = {}
        for row, message in zip(rows, projected):
            by_trace.setdefault(str(row["sequence_id"]), []).append(message)
        messages.update({agent_id: by_trace.get(trace, []) for agent_id, trace in traces.items()})
        return messages

    def read_chat_messages_through_turn(
        self,
        agent_id: str,
        message_ids: Sequence[object],
        *,
        limit: int,
        include_turn: bool = True,
    ) -> list[dict[str, Any]]:
        """The last ``limit`` messages up to the turn of the newest of ``message_ids``.

        The turn ends where the next user message begins. With include_turn=False,
        stop strictly before the requested input (for stateless API history).
        """

        requested = list(dict.fromkeys(
            str(value or "").strip()
            for value in message_ids
            if str(value or "").strip()
        ))
        if not requested:
            return []
        with self.database.read() as connection:
            sequence_id = self._chat_trace(agent_id)
            if not sequence_id:
                return []
            floor = self._chat_floor(connection, sequence_id)
            target = connection.execute(
                "SELECT MAX(position.ordinal) AS ordinal FROM chat_message_positions position "
                "WHERE position.message_id = ANY(%s) AND position.sequence_id = %s "
                f"AND position.source_kind <> 'sync' AND {_SHOWN_POSITION}",
                (requested, sequence_id),
            ).fetchone()
            target_ordinal = int(target["ordinal"] or 0) if target else 0
            if target_ordinal <= floor:
                return []
            if not include_turn:
                reset = connection.execute(
                    "SELECT COALESCE(MAX(p.ordinal),0) AS ordinal FROM operation_positions p "
                    "JOIN agent_operations o USING(operation_id) WHERE p.sequence_id=%s "
                    "AND o.kind='provider_context_reset' AND p.ordinal < %s",
                    (sequence_id, target_ordinal),
                ).fetchone()
                floor = max(floor, int(reset["ordinal"]))
                return self._chat_from_rows(self._chat_rows(
                    connection, sequence_id, after=floor, before=target_ordinal,
                    limit=max(1, int(limit)),
                ))
            following = connection.execute(
                "SELECT position.ordinal FROM chat_message_positions position "
                "WHERE position.sequence_id = %s AND position.segment_type = 'input' AND position.ordinal > %s "
                f"AND position.source_kind <> 'sync' AND {_SHOWN_POSITION} ORDER BY position.ordinal LIMIT 1",
                (sequence_id, target_ordinal),
            ).fetchone()
            return self._chat_from_rows(self._chat_rows(
                connection, sequence_id, after=floor,
                before=int(following["ordinal"]) if following else None,
                limit=max(1, int(limit)),
            ))

    def read_chat_message_page(
        self,
        agent_id: str,
        *,
        before_ordinal: int | None = None,
        after_ordinal: int = 0,
        limit: int,
    ) -> dict[str, Any]:
        """The newest Chat messages between two trace positions.

        ``ordinals`` lists each returned message's trace position, in the
        same order, for streams that keep their own read or delivery positions.
        """
        safe_limit = max(1, int(limit))
        boundary = int(before_ordinal) if before_ordinal is not None else None
        if boundary is not None and boundary <= 0:
            raise ValueError("Chat message page boundary must be positive")
        lower = int(after_ordinal or 0)
        if lower < 0:
            raise ValueError("Chat message page lower boundary must not be negative")
        with self.database.read() as connection:
            sequence_id = self._chat_trace(agent_id)
            rows = self._chat_rows(
                connection, sequence_id, after=max(lower, self._chat_floor(connection, sequence_id)),
                before=boundary, limit=safe_limit + 1,
            ) if sequence_id else []
        has_more = len(rows) > safe_limit
        selected_rows = rows[1:] if has_more else rows
        return {
            "messages": self._chat_from_rows(selected_rows),
            "ordinals": [int(row["ordinal"]) for row in selected_rows],
            "next_before_ordinal": (
                int(selected_rows[0]["ordinal"])
                if has_more and selected_rows
                else None
            ),
        }

    def read_input_history(
        self,
        agent_id: str,
        sender: str,
        *,
        before_ordinal: int | None = None,
        limit: int,
    ) -> dict[str, Any]:
        """What ``sender`` typed in the Agent's trace, newest first: the composer's input history.

        The whole trace counts, before the last context reset too, and only that User's own inputs.  A
        retracted message gives its version before the retraction, so it can
        be sent again.  ``before_ordinal`` continues from
        an earlier page; ``ordinals`` lists each message's trace position.
        """
        safe_limit = max(1, int(limit))
        if before_ordinal is not None and int(before_ordinal) <= 0:
            raise ValueError("Input page boundary must be positive")
        clean_sender = str(sender or "").strip()
        with self.database.read() as connection:
            sequence_id = self._chat_trace(agent_id)
            rows = self._chat_rows(
                connection, sequence_id, before=before_ordinal, limit=safe_limit + 1, sender=clean_sender,
            ) if sequence_id and clean_sender else []
            has_more = len(rows) > safe_limit
            selected_rows = self._before_retraction(connection, rows[1:] if has_more else rows)
        selected_rows.reverse()
        return {
            "messages": self._chat_from_rows(selected_rows),
            "ordinals": [int(row["ordinal"]) for row in selected_rows],
            "next_before_ordinal": (
                int(selected_rows[-1]["ordinal"]) if has_more and selected_rows else None
            ),
        }

    @staticmethod
    def _before_retraction(connection: Any, rows: Sequence[Any]) -> list[dict[str, Any]]:
        """Chat rows, a retracted message's row carrying the version it replaced."""

        rows = [dict(row) for row in rows]
        retracted = [
            str(row["segment_id"]) for row in rows
            if (json_value(row["metadata_json"] or "{}").get("message") or {}).get("retracted_at")
        ]
        if not retracted:
            return rows
        previous = {str(row["target_segment_id"]): row for row in connection.execute(
            "SELECT relation.target_segment_id, segment.segment_id, segment.metadata_json::jsonb AS metadata_json "
            "FROM segment_relations relation JOIN segments segment ON segment.segment_id=relation.source_segment_id "
            "WHERE relation.relation_type='transformed_from' AND relation.target_segment_id=ANY(%s)",
            (retracted,),
        ).fetchall()}
        for row in rows:
            source = previous.get(str(row["segment_id"]))
            if source is not None:
                row.update(segment_id=source["segment_id"], metadata_json=source["metadata_json"])
        return rows

    def chat_message_ordinal(self, agent_id: str, message_id: str) -> int | None:
        """Trace position of one message in the Agent's Chat; None when it is not there."""

        with self.database.read() as connection:
            row = self._shown_member(connection, self._chat_trace(agent_id), message_id)
        return int(row["ordinal"]) if row is not None else None


    def chat_message(
        self,
        agent_id: str,
        message_id: str,
    ) -> dict[str, Any] | None:
        """One message of the Agent's Chat; None when it is not there (or was cleared)."""

        with self.database.read() as connection:
            row = self._shown_member(connection, self._chat_trace(agent_id), message_id)
            return self._chat_from_rows([row])[0] if row is not None else None

    def chat_message_ancestors(self, segment_id: str, message_id: str) -> list[dict[str, Any]]:
        """Immutable input versions used to validate a delivery replay.

        These are provenance records, never extra members of the current Chat.
        """
        with self.database.read() as connection:
            ids = [row["segment_id"] for row in connection.execute("""
                WITH RECURSIVE ancestors(segment_id) AS (
                    SELECT source_segment_id FROM segment_relations WHERE target_segment_id=%s
                    UNION
                    SELECT relation.source_segment_id FROM segment_relations relation
                    JOIN ancestors ON relation.target_segment_id=ancestors.segment_id
                )
                SELECT segment.segment_id FROM ancestors JOIN segments segment USING(segment_id)
                WHERE segment.metadata_json::jsonb#>>'{message,message_id}'=%s
            """, (segment_id, message_id)).fetchall()]
            return [self._chat_payload(row["segment_id"], canonical_json(row["metadata"]), row["content"])
                    for row in self._read_segment_rows(connection, ids)]

    def chat_message_provenance(self, agent_id: str, message_id: str) -> dict[str, str]:
        """The current occurrence directly names the Execution that delivered it."""
        with self.database.read() as connection:
            message = self._shown_member(connection, self._chat_trace(agent_id), message_id)
            if message is None:
                raise KeyError(message_id)
            segment_id = str(message["segment_id"])
            turn = connection.execute(
                f"SELECT execution.provider_session_ref, {PROVIDER_TURN_SQL} AS provider_turn_id "
                "FROM segments segment JOIN executions execution USING(execution_id) WHERE segment.segment_id=%s",
                (segment_id,),
            ).fetchone()
            return {"segment_id": segment_id,
                    "provider_session_ref": str(turn["provider_session_ref"] or "") if turn else "",
                    "provider_turn_id": str(turn["provider_turn_id"] or "") if turn else ""}

    def latest_chat_message(
        self,
        agent_id: str,
        *,
        role: str = "",
    ) -> dict[str, Any] | None:
        """The newest message of the Agent's Chat, of ``role`` when given."""

        wanted = str(role or "").strip().casefold()
        wanted = _chat_role(wanted) if wanted else ""
        with self.database.read() as connection:
            sequence_id = self._chat_trace(agent_id)
            if not sequence_id:
                return None
            floor = self._chat_floor(connection, sequence_id)
            before = None
            while True:
                rows = self._chat_rows(
                    connection, sequence_id, after=floor, before=before, limit=50,
                    user_only=wanted == "user",
                )
                for message in reversed(self._chat_from_rows(rows)):
                    if not wanted or message["role"] == wanted:
                        return message
                if len(rows) < 50:
                    return None
                before = int(rows[0]["ordinal"])

    def trace_chat_messages(self, agent_id: str, message_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
        """Messages of the Agent's trace by identity, wherever they stand in it (a clear does not hide them)."""

        requested = list(dict.fromkeys(str(item or "").strip() for item in message_ids if str(item or "").strip()))
        if not requested:
            return {}
        found: dict[str, dict[str, Any]] = {}
        with self.database.read() as connection:
            sequence_id = self._chat_trace(agent_id)
            if not sequence_id:
                return {}
            for chunk in _batches(requested):
                # Each message's newest shown position.
                rows = connection.execute(
                    f"SELECT DISTINCT ON (position.message_id) {_POSITION_ROW} FROM chat_message_positions position "
                    "WHERE position.message_id = ANY(%s) AND position.sequence_id = %s "
                    f"AND position.source_kind <> 'sync' AND {_SHOWN_POSITION} "
                    "ORDER BY position.message_id, position.ordinal DESC",
                    (list(chunk), sequence_id),
                ).fetchall()
                for message in self._chat_from_rows(rows):
                    found[str(message["message_id"])] = message
        return found

    def group_source_message(self, agent_id: str, message_id: str) -> dict[str, Any] | None:
        """Read a consumed member for audit/requeue, never as a current Chat entry."""
        with self.database.read() as connection:
            rows = self._group_source_members(connection, self._trace_id(connection, agent_id), [message_id])
            if rows:
                row = rows[-1]
                return self._chat_payload(row["segment_id"], row["metadata_json"], self._message_contents(connection, [row["segment_id"]])[row["segment_id"]])
        return None

    @staticmethod
    def _group_source_members(connection: Any, sequence_id: str, message_ids: Sequence[str]) -> list[Any]:
        """Accepted sources of groups in this trace, oldest grouping operation first.

        A consumed source has no current ordinal. Its content stays immutable;
        the latest recorded grouping owns the current grouping. Start from the
        indexed message identities, never a scan of the rendered trace.
        """
        if not message_ids or not sequence_id:
            return []
        return list(connection.execute(
                "SELECT source.segment_id,source.metadata_json,member.ordinal AS group_ordinal, "
                "source.metadata_json::jsonb#>>'{message,message_id}' AS member_id, "
                "target.metadata_json::jsonb#>>'{message,message_id}' AS group_id "
                "FROM (SELECT segment_id,metadata_json FROM segments "
                f"WHERE {CHAT_PREDICATE} AND metadata_json::jsonb#>>'{{message,message_id}}'=ANY(%s) OFFSET 0) source "
                "JOIN LATERAL (SELECT target_segment_id,created_at FROM segment_relations relation "
                "WHERE relation.source_segment_id=source.segment_id AND relation.operation_type='message.group' OFFSET 0) relation ON TRUE "
                "JOIN LATERAL (SELECT metadata_json FROM segments WHERE segment_id=relation.target_segment_id OFFSET 0) target ON TRUE "
                "JOIN LATERAL (WITH RECURSIVE versions(segment_id) AS ("
                "SELECT relation.target_segment_id UNION SELECT revision.target_segment_id "
                "FROM versions JOIN segment_relations revision ON revision.source_segment_id=versions.segment_id "
                "WHERE revision.relation_type IN ('transformed_from','merged_from')) "
                "SELECT member.ordinal FROM versions JOIN sequence_segments member USING(segment_id) "
                "JOIN segments current USING(segment_id) WHERE member.sequence_id=%s "
                "AND current.metadata_json::jsonb#>>'{message,message_id}'=target.metadata_json::jsonb#>>'{message,message_id}') member ON TRUE "
                "ORDER BY relation.created_at,member.ordinal,source.segment_id",
                (list(message_ids), sequence_id),
            ).fetchall())

    def message_groups(self, agent_id: str, message_ids: Sequence[str]) -> dict[str, str]:
        """Recover the latest grouping in this trace, including consumed members."""
        with self.database.read() as connection:
            rows = self._group_source_members(connection, self._trace_id(connection, agent_id), message_ids)
        return {str(row["member_id"]): str(row["group_id"]) for row in rows}


    def chat_final_replies_many(
        self, requests: Mapping[str, Sequence[str]],
    ) -> dict[str, dict[str, tuple[str, str]]]:
        """For each input: (the final Agent answer, the last reply of any kind).

        Agent messages and Portal notices (failure, stop, compaction, command
        receipts) name the input they answer in ``reply_to_message_id``.  A
        Mailbox group's members are answered by their group.  An input that
        nothing answers is left out. Provider phases do not end a Portal input:
        after continuation its last current reply is the answer. The last reply
        of any kind supplies failure/stop notices.
        """

        wanted = {
            str(agent_id): list(dict.fromkeys(str(item or "").strip() for item in message_ids if str(item or "").strip()))
            for agent_id, message_ids in requests.items()
        }
        wanted = {agent_id: ids for agent_id, ids in wanted.items() if ids}
        if not wanted:
            return {}
        groups: dict[tuple[str, str], str] = {}
        last: dict[tuple[str, str], tuple[int, str]] = {}
        last_agent: dict[tuple[str, str], tuple[int, str]] = {}
        with self.database.read() as connection:
            traces = self._chat_traces(list(wanted))
            sequences = list(traces.values())
            if not sequences:
                return {}
            asked = list(dict.fromkeys(item for agent_id in traces for item in wanted[agent_id]))
            for chunk in _batches(asked):
                for row in connection.execute(
                    _GROUPED_INPUTS
                    # Each node's few memberships first: the planner overestimates the
                    # transforms and would otherwise sort every position of the traces.
                    + "SELECT position.sequence_id,transforms.message_id,position.message_id AS group_id "
                    "FROM transforms JOIN LATERAL (SELECT sequence_id FROM sequence_segments member "
                    "WHERE member.segment_id=transforms.node AND member.sequence_id=ANY(%s) OFFSET 0) member ON TRUE "
                    "JOIN LATERAL (SELECT position.sequence_id,position.message_id FROM chat_message_positions position "
                    "WHERE position.sequence_id=member.sequence_id AND position.source_kind='segment' "
                    f"AND position.source_id=transforms.node AND {_SHOWN_POSITION} OFFSET 0) position ON TRUE "
                    # A notice is never transformed; it stands for itself.
                    "UNION ALL SELECT position.sequence_id,position.message_id,position.message_id "
                    "FROM chat_message_positions position WHERE position.sequence_id=ANY(%s) "
                    "AND position.source_kind='notice' AND position.message_id=ANY(%s)",
                    (list(chunk), sequences, sequences, list(chunk)),
                ).fetchall():
                    if row["group_id"]:
                        groups[(str(row["sequence_id"]), str(row["message_id"]))] = str(row["group_id"])
            # An input is answered in its own trace: look each one up there, not
            # in every requested trace (fifty Residents made 5000 lookups).
            pairs = list(dict.fromkeys(
                (sequence_id, identity)
                for agent_id, sequence_id in traces.items()
                for message_id in wanted[agent_id]
                for identity in (message_id, groups.get((sequence_id, message_id), ""))
                if identity
            ))
            rows = []
            for chunk in _batches(pairs):
                rows.extend(connection.execute(
                    "SELECT requested.sequence_id,reply.* "
                    "FROM unnest(%s::text[],%s::text[]) requested(sequence_id,reply_to) "
                    "CROSS JOIN (VALUES ('model'),('harness')) kind(segment_type) "
                    "CROSS JOIN LATERAL (SELECT position.ordinal,position.segment_type,"
                    "position.reply_to,position.message_id FROM chat_message_positions position "
                    "WHERE position.sequence_id=requested.sequence_id AND position.reply_to IS NOT NULL "
                    "AND position.reply_to=requested.reply_to AND position.source_kind<>'sync' AND position.reply_candidate "
                    f"AND position.segment_type=kind.segment_type AND {_SHOWN_POSITION} "
                    "ORDER BY position.ordinal DESC LIMIT 1) reply",
                    ([sequence_id for sequence_id, _identity in chunk], [identity for _sequence_id, identity in chunk]),
                ).fetchall())
            for row in rows:
                key = (str(row["sequence_id"]), str(row["reply_to"]))
                found = (int(row["ordinal"]), str(row["message_id"]))
                for held in (last, last_agent) if row["segment_type"] == "model" else (last,):
                    if key not in held or found[0] > held[key][0]:
                        held[key] = found
        replies: dict[str, dict[str, tuple[str, str]]] = {}
        for agent_id, sequence_id in traces.items():
            for message_id in wanted[agent_id]:
                key = (sequence_id, message_id)
                if key not in last:
                    key = (sequence_id, groups.get(key, ""))
                if key in last:
                    answer = last_agent.get(key, (0, ""))[1]
                    replies.setdefault(agent_id, {})[message_id] = (answer, last[key][1])
        return replies

    def chat_replies(
        self,
        agent_id: str,
        message_id: str,
        *,
        until_message_id: str = "",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """The shown Agent messages that reply to ``message_id``, oldest first: the last ``limit``.

        Replies come after the message they answer; ``until_message_id`` (its
        final reply) ends the search, so a finished turn reads only its own
        messages.  Without it the search runs to the end of the Conversation.
        """

        return self.chat_replies_many(
            {agent_id: {message_id: until_message_id}}, limit=limit,
        ).get(agent_id, {}).get(message_id, [])

    def chat_replies_many(
        self, requests: Mapping[str, Mapping[str, str]], *, limit: int = 20,
    ) -> dict[str, dict[str, list[dict[str, Any]]]]:
        """Read bounded reply windows together; each input keeps its own limit and order.

        Requests map Agent and input identity to an optional final reply identity.
        Positions and payloads are read in batches, never one round trip per input.
        """
        wanted = {agent: dict(inputs) for agent, inputs in requests.items() if inputs}
        result = {agent: {message: [] for message in inputs} for agent, inputs in wanted.items()}
        if not wanted:
            return result
        with self.database.read() as connection:
            traces = self._chat_traces(list(wanted))
            if not traces:
                return result
            floors = self._chat_floors(connection, traces.values())
            identities = list(dict.fromkeys(
                identity for agent in traces for message, until in wanted[agent].items()
                for identity in (message, until) if identity
            ))
            positions: dict[tuple[str, str], int] = {}
            for chunk in _batches(identities):
                rows = connection.execute(
                    "SELECT position.sequence_id, MAX(position.ordinal) AS ordinal, position.message_id "
                    "FROM chat_message_positions position WHERE position.message_id = ANY(%s) "
                    "AND position.sequence_id = ANY(%s) AND position.source_kind <> 'sync' "
                    f"AND {_SHOWN_POSITION} GROUP BY position.sequence_id, position.message_id",
                    (list(chunk), list(traces.values())),
                ).fetchall()
                for row in rows:
                    key = (str(row["sequence_id"]), str(row["message_id"]))
                    positions[key] = max(positions.get(key, 0), int(row["ordinal"]))
            windows = [
                (agent, message, sequence, max(positions[(sequence, message)], floors.get(sequence, 0)),
                 positions.get((sequence, until), 2147483647))
                for agent, sequence in traces.items() for message, until in wanted[agent].items()
                if (sequence, message) in positions
            ]
            for chunk in _batches(windows):
                # Each input's newest replies by chat_message_positions_reply_idx.
                rows = connection.execute(
                    f"""
                    SELECT requested.agent_id,requested.message_id,reply.*
                    FROM unnest(%s::text[],%s::text[],%s::text[],%s::integer[],%s::integer[])
                         requested(agent_id,message_id,sequence_id,after_ordinal,until_ordinal)
                    CROSS JOIN LATERAL (
                        SELECT position.ordinal,position.source_id AS segment_id,
                               {_POSITION_METADATA} AS metadata_json
                        FROM chat_message_positions position
                        WHERE position.sequence_id=requested.sequence_id AND position.reply_to=requested.message_id
                          AND position.segment_type='model'
                          AND (position.reply_candidate OR EXISTS (
                              SELECT 1 FROM jsonb_array_elements(
                                  COALESCE(({_POSITION_METADATA})#>'{{message,parts}}', '[]'::jsonb)
                              ) part WHERE part->>'type' IN ('tool_call','tool_result')
                                AND part->>'tool_name' IN ('request_user_input','request_approval','mcp_elicitation')
                          ))
                          AND position.reply_to IS NOT NULL AND position.source_kind<>'sync'
                          AND position.ordinal > requested.after_ordinal AND position.ordinal <= requested.until_ordinal
                          AND {_SHOWN_POSITION}
                        ORDER BY position.ordinal DESC LIMIT %s
                    ) reply
                    ORDER BY requested.agent_id,requested.message_id,reply.ordinal
                    """,
                    (*[list(column) for column in zip(*chunk)], max(1, int(limit))),
                ).fetchall()
                unique = {row["segment_id"]: row for row in rows}
                messages = {message["segment_id"]: message for message in self._chat_from_rows(list(unique.values()))}
                for row in rows:
                    message = messages[row["segment_id"]]
                    if message["role"] == "agent":
                        result[row["agent_id"]][row["message_id"]].append(dict(message))
        return result

    def chat_user_counts(self, agent_ids: Sequence[str]) -> dict[str, int]:
        """How many user messages each Agent's current Chat holds."""
        return {agent: facts["count"] for agent, facts in self._user_message_summaries(agent_ids).items()}

    @staticmethod
    def _user_message_rows(connection, sequences, floors, *, excluded_business_ref="", actor_spellings=()):
        """Each trace's user-message count, newest message and the actor's requests after its last clear."""
        excluded, parameters = "", ()
        if excluded_business_ref:
            excluded = " AND NOT (position.business_ref IS NOT NULL AND position.business_ref ~ %s)"
            parameters = (excluded_business_ref,)
        return list(connection.execute(
            "SELECT position.sequence_id, COUNT(*) AS count, MAX(position.created_at) AS last_at, "
            "(ARRAY_AGG(position.actor_ref ORDER BY position.created_at DESC, position.ordinal DESC))[1] "
            "AS last_actor, ARRAY_AGG(ARRAY[position.created_at::text, position.message_id] "
            "ORDER BY position.created_at, position.ordinal) "
            "FILTER (WHERE lower(position.actor_ref) = ANY(%s)) AS actor_requests "
            "FROM chat_message_positions position WHERE position.sequence_id = ANY(%s) "
            "AND position.segment_type = 'input' AND position.source_kind <> 'sync' "
            "AND position.ordinal > COALESCE((%s::jsonb ->> position.sequence_id)::int, 0)"
            f"{excluded} GROUP BY position.sequence_id",
            (list(actor_spellings), list(sequences),
             canonical_json({key: floors[key] for key in sequences if key in floors}), *parameters),
        ).fetchall())

    @staticmethod
    def _addressed_rows(connection, sequences, floor_json: str, actor: str) -> list[Any]:
        """Messages naming the actor as a recipient, oldest first per trace."""
        return list(connection.execute(
            "SELECT position.sequence_id, position.created_at, position.message_id "
            "FROM chat_message_positions position WHERE position.sequence_id = ANY(%s) "
            "AND position.segment_type = 'model' AND position.source_kind <> 'sync' "
            "AND position.recipient_users @> ARRAY[lower(%s)] "
            "AND position.ordinal > COALESCE((%s::jsonb ->> position.sequence_id)::int, 0) "
            "ORDER BY position.sequence_id, position.created_at, position.ordinal",
            (list(sequences), actor, floor_json),
        ).fetchall())


    @staticmethod
    def _activity_rows(
        connection: Any,
        actor: str,
        sequence_ids: Sequence[str] | None,
        *,
        after: tuple[str, str] | None = None,
        limit: int | None = None,
        exclude_agent_ids: Sequence[str] = (),
        known_times: Mapping[str, Any] | None = None,
    ) -> list[Any]:
        """Each trace's latest Chat activity with the actor: user messages it sent, messages to it.

        Read from the activity kept for each trace and person, already counted
        from the trace's last context reset: no grouping of all their messages
       .  The traces' projections must be current.
        """
        scope = " AND activity.sequence_id = ANY(%s)" if sequence_ids is not None else ""
        exclusions = " AND agent.agent_id <> ALL(%s)" if exclude_agent_ids else ""
        # Times the caller knows beyond the messages raise a listed agent's
        # activity, ordering and bounding pages in SQL; they list no agent.
        known = (" LEFT JOIN unnest(%s::text[], %s::timestamptz[]) AS known(agent_id, at)"
                 " ON known.agent_id = agent.agent_id") if known_times else ""
        latest = "GREATEST(MAX(activity.latest_at), MAX(known.at))" if known_times else "MAX(activity.latest_at)"
        boundary = (
            f" HAVING {latest} < %s OR ({latest} = %s AND agent.agent_id > %s)"
        ) if after else ""
        paging = " ORDER BY latest_at DESC, agent.agent_id ASC LIMIT %s" if limit is not None else ""
        return list(connection.execute(
            f"SELECT agent.agent_id, {latest} AS latest_at FROM chat_actor_activity activity "
            "JOIN agents agent ON agent.trace_sequence_id = activity.sequence_id" + known
            + " WHERE activity.actor = lower(%s)" + scope + exclusions
            + " GROUP BY agent.agent_id" + boundary + paging,
            [*([list(known_times), list(known_times.values())] if known_times else []),
             actor, *([list(sequence_ids)] if sequence_ids is not None else []),
             *([list(exclude_agent_ids)] if exclude_agent_ids else []),
             *([after[0], after[0], after[1]] if after else []),
             *([limit] if limit is not None else [])],
        ).fetchall())

    def chat_actor_activity_page(
        self, actor_ref: str, *, after: tuple[str, str] | None = None,
        limit: int = 50, exclude_agent_ids: Sequence[str] = (),
        known_times: Mapping[str, Any] | None = None,
    ) -> list[tuple[str, str]]:
        """Return one ordered activity page; grouping and keyset bounds stay in SQL.

        ``known_times`` maps agents to later times the caller knows of, such
        as outcomes; an agent's activity is the latest of them and its messages.
        A listing brings every trace current once (``bring_chat_current``)
        before it reads its pages.
        """
        actor = str(actor_ref or "").strip()
        if not actor:
            return []
        with self.database.read() as connection:
            rows = self._activity_rows(
                connection, actor, None, after=after, limit=limit,
                exclude_agent_ids=exclude_agent_ids, known_times=known_times,
            )
        return [(str(row["agent_id"]), (iso_time(row["latest_at"]) or "")) for row in rows]

    def chat_actor_activity(
        self, actor_ref: str, agent_ids: Iterable[str] | None = None,
    ) -> dict[str, str]:
        """Latest activity of each agent with the actor: user messages it sent there, or messages to it.

        The copies a Family facade relays to its members do not count: they
        keep their sender, but the sender's conversation is with the facade
        (chat_position_index.RELAYED_COPY_PATTERN, decision_44cf587c1721).  A message a Resident
        sends to the actor counts where it is written: in the sender's
        Conversation, or a Family facade's (decision_c524c775e0be).  Newest first.
        """
        actor = str(actor_ref or "").strip()
        requested = None if agent_ids is None else tuple(dict.fromkeys(
            str(item or "").strip() for item in agent_ids if str(item or "").strip()
        ))
        if not actor or requested == ():
            return {}
        rows: list[Any] = []
        if requested is None:
            self.bring_chat_current()
            with self.database.read() as connection:
                rows = self._activity_rows(connection, actor, None)
        else:
            with self.database.read() as connection:
                traces = self._chat_traces(list(requested))
                for chunk in _batches(list(traces.values())):
                    rows.extend(self._activity_rows(connection, actor, list(chunk)))
        ordered = sorted(((str(row["agent_id"]), (iso_time(row["latest_at"]) or "")) for row in rows), key=lambda item: item[0])
        ordered.sort(key=lambda item: item[1], reverse=True)
        return dict(ordered)

    @staticmethod
    def chat_activity_page_token(agent_id: str, latest_at: str) -> str:
        return base64.urlsafe_b64encode(json.dumps(
            [1, latest_at, agent_id], separators=(",", ":"),
        ).encode("utf-8")).decode("ascii").rstrip("=")


    @staticmethod
    def chat_activity_page_boundary(page_token: str) -> tuple[str, str] | None:
        """The (latest_at, agent_id) a page token continues after; None for the first page."""

        if not page_token:
            return None
        try:
            if len(page_token) > 2048:
                raise ValueError("oversized cursor")
            boundary = json.loads(base64.b64decode(
                page_token + "=" * (-len(page_token) % 4),
                altchars=b"-_", validate=True,
            ))
            if (
                not isinstance(boundary, list) or len(boundary) != 3
                or boundary[0] != 1
                or not all(isinstance(item, str) and item for item in boundary[1:])
            ):
                raise ValueError("invalid cursor")
            return (boundary[1], boundary[2])
        except (ValueError, TypeError) as exc:
            raise ValueError("Invalid conversation page token") from exc


    def chat_signatures(self, agent_ids: Sequence[str]) -> dict[str, str]:
        """A value that changes whenever an Agent's trace, and so its Chat, changes."""

        requested = list(dict.fromkeys(str(item) for item in agent_ids if str(item)))
        if not requested:
            return {}
        facts: dict[str, list[object]] = {}
        with self.database.read() as connection:
            for chunk in _batches(requested):
                # The length is the trace's last position, a Segment's or a notice's.
                for row in connection.execute(
                    "SELECT agent.agent_id, sequence.sequence_id, sequence.updated_at, GREATEST("
                    "(SELECT MAX(member.ordinal) FROM sequence_segments member "
                    " WHERE member.sequence_id = sequence.sequence_id), "
                    "(SELECT MAX(position.ordinal) FROM operation_positions position JOIN LATERAL ("
                    " SELECT 1 FROM agent_operations operation WHERE operation.operation_id=position.operation_id "
                    " AND operation.kind='notice' OFFSET 0) notice ON TRUE "
                    " WHERE position.sequence_id = sequence.sequence_id)) AS length "
                    "FROM agents agent JOIN sequences sequence ON sequence.sequence_id = agent.trace_sequence_id "
                    "WHERE agent.agent_id = ANY(%s)",
                    (list(chunk),),
                ).fetchall():
                    if row["length"]:
                        facts[str(row["agent_id"])] = [
                            str(row["sequence_id"]), int(row["length"]), (iso_time(row["updated_at"]) or ""),
                        ]
        return {
            agent_id: hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
            for agent_id, value in facts.items()
        }

    def _user_message_summaries(
        self, agent_ids: Sequence[str], *, excluded_business_ref: str = "",
    ) -> dict[str, dict[str, Any]]:
        """Count, newest time and sender of each Agent's user messages, in one pass over its trace."""

        summaries = {agent_id: {"count": 0, "last_at": "", "last_actor": ""} for agent_id in agent_ids}
        with self.database.read() as connection:
            traces = self._chat_traces(list(agent_ids))
            owners = {sequence_id: agent_id for agent_id, sequence_id in traces.items()}
            floors = self._chat_floors(connection, traces.values())
            for chunk in _batches(list(traces.values())):
                for row in self._user_message_rows(
                    connection, chunk, floors, excluded_business_ref=excluded_business_ref,
                ):
                    summaries[owners[str(row["sequence_id"])]] = {
                        "count": int(row["count"]), "last_at": (iso_time(row["last_at"]) or ""),
                        "last_actor": str(row["last_actor"]),
                    }
        return summaries

    def chat_conversation_summaries(
        self, agent_ids: Sequence[str], actor_ref: str, *, excluded_business_ref: str = "",
        finish: bool = True,
    ) -> dict[str, dict[str, Any]]:
        """User-message facts of each conversation, the actor's requests in it and the latest message to the actor.

        Messages whose ``business_ref`` matches ``excluded_business_ref`` (a
        regular expression) do not count, such as the copies a Family facade
        relays to its members: the sender's conversation is with the facade.
        With ``finish=False`` the actor's requests stay as ``actor_requests``
        (time, message id) pairs, for a caller that adds the messages waiting
        in a Mailbox before ``finish_conversation_summary``.
        """
        requested = list(dict.fromkeys(str(item) for item in agent_ids if str(item)))
        if not requested:
            return {}
        actor = str(actor_ref or "").strip().lstrip("@＠").strip()
        summaries = {agent_id: {
            "user_message_count": 0, "last_user_actor": "", "last_user_message_at": "",
            "actor_requests": [], "actor_addressed_message_id": "", "actor_addressed_at": "",
        } for agent_id in requested}
        actor_spellings = [prefix + actor.casefold() for prefix in ("", "@", "＠")] if actor else []
        # Counts and this actor's requests describe the same user messages.
        # Read them in one pass, with trace/reset/recipient facts from the same
        # committed snapshot, instead of scanning every Chat twice.
        self._chat_traces(requested)
        with self.database.snapshot() as connection:
            traces = self._trace_ids(connection, requested)
            owners = {sequence_id: agent_id for agent_id, sequence_id in traces.items()}
            floors = self._chat_floors(connection, traces.values())
            # Sequence rows change for every append, replacement, notice and
            # reset. Their MVCC identity also distinguishes equal timestamps
            # and replacements which keep the same trace length. Read it in
            # the same committed snapshot as the facts it guards.
            versions = {}
            if connection.execute("SHOW transaction_read_only").fetchone()[0] == "on":
                versions = {
                    str(row["sequence_id"]): (str(row["version"]), str(row["position"]))
                    for row in connection.execute(
                        "SELECT sequence_id, xmin::text AS version, ctid::text AS position "
                        "FROM sequences WHERE sequence_id = ANY(%s)",
                        (list(traces.values()),),
                    ).fetchall()
                }
            pending = []
            for sequence_id in traces.values():
                key = (actor.casefold(), excluded_business_ref, sequence_id)
                with self._conversation_summaries_lock:
                    cached = self._conversation_summaries_cache.get(key)
                    if cached and cached[0] == versions.get(sequence_id) and cached[1] > time.monotonic():
                        self._conversation_summaries_cache.move_to_end(key)
                    else:
                        cached = None
                if cached is None:
                    pending.append(sequence_id)
                else:
                    summaries[owners[sequence_id]] = deepcopy(cached[2])
            for chunk in _batches(pending):
                floor_json = canonical_json({key: floors[key] for key in chunk if key in floors})
                for row in self._user_message_rows(
                    connection, chunk, floors, excluded_business_ref=excluded_business_ref, actor_spellings=actor_spellings,
                ):
                    summaries[owners[str(row["sequence_id"])]].update({
                        "user_message_count": int(row["count"]),
                        "last_user_actor": str(row["last_actor"]),
                        "last_user_message_at": (iso_time(row["last_at"]) or ""),
                        "actor_requests": [(iso_time(instant(at)), str(message_id))
                                           for at, message_id in row["actor_requests"] or []],
                    })
                if not actor:
                    continue
                for row in self._addressed_rows(connection, chunk, floor_json, actor):
                    summary = summaries[owners[str(row["sequence_id"])]]
                    summary["actor_addressed_message_id"] = str(row["message_id"])
                    summary["actor_addressed_at"] = (iso_time(row["created_at"]) or "")
            for sequence_id in pending:
                summary = summaries[owners[sequence_id]]
                if sequence_id not in versions or len(summary["actor_requests"]) > 4096:
                    continue
                key = (actor.casefold(), excluded_business_ref, sequence_id)
                value = (versions[sequence_id], time.monotonic() + 60.0, deepcopy(summary))
                with self._conversation_summaries_lock:
                    self._conversation_summaries_cache[key] = value
                    self._conversation_summaries_cache.move_to_end(key)
                    while (
                        len(self._conversation_summaries_cache) > 2048
                        or sum(len(item[2]["actor_requests"]) for item in self._conversation_summaries_cache.values()) > 65536
                    ):
                        self._conversation_summaries_cache.popitem(last=False)
        if finish:
            for summary in summaries.values():
                finish_conversation_summary(summary)
        return summaries

    def chat_message_headers(
        self,
        agent_ids: Sequence[str],
        *,
        message_ids: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        """User messages and replies of many Agents' Chats, without their bodies."""

        requested = list(dict.fromkeys(str(item) for item in agent_ids if str(item)))
        if not requested:
            return []
        selected_ids = (
            list(dict.fromkeys(str(item) for item in message_ids if str(item)))
            if message_ids is not None else None
        )
        if selected_ids == []:
            return []
        rows: list[dict[str, Any]] = []
        with self.database.read() as connection:
            traces = self._chat_traces(requested)
            owners = {sequence_id: agent_id for agent_id, sequence_id in traces.items()}
            floors = self._chat_floors(connection, traces.values())
            for chunk in _batches(list(traces.values())):
                floor_json = canonical_json({key: floors[key] for key in chunk if key in floors})
                # User messages and the replies naming the input they answer,
                # after each trace's last clear.
                clauses = (
                    "position.sequence_id = ANY(%s) AND position.source_kind <> 'sync' "
                    "AND position.ordinal > COALESCE((%s::jsonb ->> position.sequence_id)::int, 0) "
                    "AND (position.segment_type = 'input' OR (position.segment_type = 'model' "
                    f"AND position.reply_to IS NOT NULL)) AND position.reply_candidate AND {_SHOWN_POSITION}"
                )
                parameters: tuple[Any, ...] = (list(chunk), floor_json)
                if selected_ids is not None:
                    # The named messages by their identity (chat_message_positions_identity_idx).
                    clauses = "position.message_id = ANY(%s) AND " + clauses
                    parameters = (selected_ids, *parameters)
                query = (
                    f"SELECT position.sequence_id, position.source_id AS segment_id, {_POSITION_METADATA} AS metadata_json, "
                    f"{_POSITION_HAS_CONTENT} AS has_content FROM (SELECT * FROM chat_message_positions position "
                    f"WHERE {clauses} OFFSET 0) position"
                )
                for row in connection.execute(query, parameters).fetchall():
                    message = self._chat_payload(row["segment_id"], row["metadata_json"], "")
                    reply_to = str(message.get("reply_to_message_id") or "")
                    if message["role"] != "user" and not (message["role"] == "agent" and reply_to):
                        continue
                    rows.append({
                        "agent_id": owners[str(row["sequence_id"])],
                        "message_id": str(message["message_id"]),
                        "role": str(message["role"]),
                        "actor_ref": str(message.get("author_id") or ""),
                        "accepted_at": str(message.get("created_at") or ""),
                        "display_state": "displayed",
                        "reply_to_message_id": reply_to,
                        "author_id": str(message.get("author_id") or ""),
                        "has_content": bool(row["has_content"]),
                    })
        rows.sort(key=lambda item: (item["agent_id"], item["accepted_at"], item["message_id"]))
        return rows

    @staticmethod
    def _timeline_segment(
        segment: Mapping[str, Any],
        *,
        turn_id: str = "",
    ) -> dict[str, Any]:
        metadata = dict(segment.get("metadata") or {})
        content = segment.get("content")
        source = str(metadata.get("source") or "") or {
            "input": "human_prompt",
            "model": "agent_output",
            "harness": "harness_output",
            "compaction": "context_compaction",
        }.get(str(segment.get("segment_type") or ""), "")
        return {
            "id": str(segment.get("segment_id") or ""),
            "segment_id": str(segment.get("segment_id") or ""),
            "created_at": str(segment.get("finished_at") or segment.get("started_at") or ""),
            "started_at": str(segment.get("started_at") or ""),
            "finished_at": str(segment.get("finished_at") or ""),
            "source": source,
            "kind": str(metadata.get("kind") or "message"),
            "title": str(metadata.get("title") or ""),
            "content": (
                str(content or "")
                if not isinstance(content, Mapping)
                else canonical_json(content)
            ),
            "content_length": len(str(content or "")),
            "line_count": str(content or "").count("\n") + 1,
            "role": str(metadata.get("role") or ""),
            "author": str(
                metadata.get("author") or segment.get("created_by_actor_ref") or ""
            ),
            "turn_id": str(turn_id or ""),
            "execution_id": str(segment.get("execution_id") or ""),
        }

    @staticmethod
    def _tool_resource(
        segment: Mapping[str, Any],
        *,
        turn_id: str = "",
    ) -> dict[str, Any]:
        content = (
            segment.get("content")
            if isinstance(segment.get("content"), Mapping)
            else {}
        )
        metadata = dict(segment.get("metadata") or {})
        return {
            "id": str(content.get("call_id") or segment.get("segment_id") or ""),
            "segment_id": str(segment.get("segment_id") or ""),
            "kind": "tool",
            "tool_name": str(content.get("tool_name") or metadata.get("tool_name") or ""),
            "server": str(
                content.get("tool_namespace") or metadata.get("tool_namespace") or ""
            ),
            "namespace": str(content.get("tool_namespace") or ""),
            "call_id": str(content.get("call_id") or ""),
            "created_at": str(segment.get("finished_at") or segment.get("started_at") or ""),
            "started_at": str(segment.get("started_at") or ""),
            "completed_at": str(segment.get("finished_at") or ""),
            "status": str(segment.get("status") or ""),
            "turn_id": str(turn_id or ""),
            "input": content.get("input"),
            "result": content.get("result"),
            "error": content.get("error"),
            "external_effect_refs": list(content.get("external_effect_refs") or []),
        }

    def _session_trace_owners(self, connection: Any, session_ref: str, agent_id: str = "") -> Iterator[str]:
        """Agents whose trace may hold a Provider session's work, most likely first.

        The given Agent, then the Agents that recorded the session's work,
        then the Agents whose traces hold it (a session restart's work was
        merged into its Resident's trace; a Fork's trace holds a copy, the
        original holds the most of it and is the older one), then the
        Residents bound to it (a session nothing worked in yet holds only the
        context its Resident's trace marks).  Each is read only when the ones
        before it did not do.
        """

        seen: set[str] = set()

        def fresh(owners: Iterable[str]) -> Iterator[str]:
            for owner in owners:
                if owner and owner not in seen:
                    seen.add(owner)
                    yield owner

        yield from fresh([str(agent_id or "").strip()])
        yield from fresh(
            str(row["created_by_agent_id"])
            for row in connection.execute(
                "SELECT segment.created_by_agent_id, COUNT(*) AS members FROM executions execution "
                "JOIN segments segment ON segment.execution_id = execution.execution_id "
                "WHERE execution.provider_session_ref = %s AND segment.execution_id <> '' "
                "GROUP BY 1 ORDER BY 2 DESC",
                (session_ref,),
            ).fetchall()
        )
        yield from fresh(
            str(row["agent_id"])
            for row in connection.execute(
                "SELECT agent.agent_id, COUNT(*) AS members, MIN(sequence.created_at) AS created_at "
                "FROM (SELECT segment.segment_id FROM executions execution "
                "JOIN segments segment ON segment.execution_id = execution.execution_id "
                "WHERE execution.provider_session_ref = %s AND segment.execution_id <> '' LIMIT 500) own "
                "JOIN sequence_segments member ON member.segment_id = own.segment_id "
                "JOIN sequences sequence ON sequence.sequence_id = member.sequence_id "
                "JOIN agents agent ON agent.trace_sequence_id = sequence.sequence_id AND agent.sequence_kind = 'agent_trace' "
                "GROUP BY agent.agent_id ORDER BY members DESC, created_at",
                (session_ref,),
            ).fetchall()
        )
        yield from fresh(
            str(row["binding_id"]).split(":", 1)[1]
            for row in connection.execute(
                "SELECT binding_id FROM provider_bindings WHERE provider_session_ref = %s "
                "AND binding_id LIKE 'resident:%%'",
                (session_ref,),
            ).fetchall()
        )

    @staticmethod
    def trace_rows(
        connection: Any, sequence_id: str, *, first: int = 1, before: int | None = None,
        newest_first: bool = False, limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """A trace's members in order, with their Execution's session and turn."""

        bounds = " AND member.ordinal >= %s"
        arguments: list[Any] = [sequence_id, first]
        if before is not None:
            bounds += " AND member.ordinal < %s"
            arguments.append(before)
        ordering = " DESC" if newest_first else " ASC"
        if limit is not None:
            ordering += " LIMIT %s"
            arguments.append(limit)
        return [decode_times(row, ("started_at", "finished_at")) for row in connection.execute(
            "SELECT member.ordinal, member.segment_id, segment.segment_type, segment.started_at, segment.finished_at, "
            "segment.metadata_json, segment.execution_id, segment.created_by_agent_id, segment.content_sha256, segment.content_type, "
            f"execution.provider_session_ref AS execution_session, {PROVIDER_TURN_SQL} AS provider_turn_id "
            "FROM sequence_segments member JOIN segments segment ON segment.segment_id = member.segment_id "
            "LEFT JOIN executions execution ON execution.execution_id = segment.execution_id "
            "WHERE member.sequence_id = %s" + bounds + " ORDER BY member.ordinal" + ordering,
            tuple(arguments),
        ).fetchall()]

    def _session_window(self, connection: Any, session_ref: str, agent_id: str) -> tuple[str, int, int] | None:
        for owner in self._session_trace_owners(connection, session_ref, agent_id):
            sequence_id = self._trace_id(connection, owner)
            if not sequence_id:
                continue
            own = connection.execute(
                "WITH session_executions AS MATERIALIZED ("
                "SELECT execution_id FROM executions WHERE provider_session_ref=%s) "
                "SELECT MIN(member.ordinal) AS first, MAX(member.ordinal) AS last "
                "FROM session_executions execution JOIN segments segment "
                "ON segment.execution_id=execution.execution_id AND segment.execution_id<>'' "
                "JOIN sequence_segments member ON member.segment_id=segment.segment_id "
                "WHERE member.sequence_id=%s", (session_ref, sequence_id),
            ).fetchone()
            delivery = connection.execute(
                "SELECT MIN(position.ordinal) AS first, MAX(last_member.ordinal) AS last "
                "FROM operation_positions position JOIN agent_operations operation USING(operation_id) "
                "LEFT JOIN sequence_segments last_member ON last_member.sequence_id=position.sequence_id "
                "AND last_member.segment_id=operation.data_json::jsonb->>'last_segment_id' "
                "WHERE position.sequence_id=%s AND operation.session_ref=%s AND operation.kind='context_received'",
                (sequence_id, session_ref),
            ).fetchone()
            starts = [int(row["first"]) for row in (own, delivery) if row["first"] is not None]
            ends = [int(row["last"]) for row in (own, delivery) if row["last"] is not None]
            if starts and ends:
                return sequence_id, min(starts), max(ends)
        return None

    def _session_transcript_ids(self, connection: Any, session_ref: str, *, agent_id: str = "") -> list[str]:
        window = self._session_window(connection, session_ref, agent_id)
        if not window:
            return []
        sequence_id, first, last = window
        return [str(row["segment_id"]) for row in connection.execute(
            "SELECT segment_id FROM sequence_segments WHERE sequence_id=%s AND ordinal BETWEEN %s AND %s ORDER BY ordinal",
            (sequence_id, first, last),
        ).fetchall()]


    def _session_context_ids(self, connection: Any, session_ref: str, *, agent_id: str = "") -> list[str]:
        window = self._session_window(connection, session_ref, agent_id)
        if not window:
            return []
        sequence_id, first, last = window
        ids: list[str] = []
        before = last + 1
        while True:
            page = self.trace_rows(connection, sequence_id, first=first, before=before, newest_first=True, limit=256)
            for row in page:
                ids.append(str(row["segment_id"]))
                if row["segment_type"] == "compaction":
                    return list(reversed(ids))
            if len(page) < 256:
                return list(reversed(ids))
            before = int(page[-1]["ordinal"])

    def session_context_ids(self, provider_session_ref: str, *, agent_id: str = "") -> list[str]:
        """A Provider session's current context as read from its Agent trace."""

        with self.database.read() as connection:
            return self._session_context_ids(
                connection, str(provider_session_ref or ""), agent_id=agent_id,
            )

    def session_compaction(
        self, provider_session_ref: str, provider_compaction_ref: str, *, agent_id: str = "",
    ) -> str:
        """The compaction recorded for a session under the Provider's own identity, if any.

        Recorded by one of the session's Executions, or (recorded by a
        backfill) held in the session's transcript.
        """

        session_ref = str(provider_session_ref or "")
        reference = str(provider_compaction_ref or "")
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT segment.segment_id FROM executions execution "
                "JOIN segments segment ON segment.execution_id = execution.execution_id "
                # segments_execution_idx covers only Segments of an Execution.
                "WHERE execution.provider_session_ref = %s AND segment.execution_id <> '' "
                "AND segment.segment_type = 'compaction' "
                "AND segment.metadata_json::jsonb ->> 'provider_compaction_ref' = %s LIMIT 1",
                (session_ref, reference),
            ).fetchone()
            if row is not None:
                return str(row["segment_id"])
            held = self._session_transcript_ids(connection, session_ref, agent_id=agent_id)
            return next(
                (
                    item["segment_id"] for item in self._segment_members(connection, held)
                    if item["segment_type"] == "compaction"
                    and item["metadata"].get("provider_compaction_ref") == reference
                ),
                "",
            )


    @staticmethod
    def subagent_id(root_agent_id: str, trace_key: str) -> str:
        """Backend-local child identity scoped to the Portal root Agent."""
        if not root_agent_id or not trace_key:
            raise ValueError("Subagent requires its root and backend trace identity")
        return f"{root_agent_id}/{trace_key}"


    def record_context_received(
        self,
        agent_id: str,
        provider_session_ref: str,
        segment_ids: Sequence[str],
        *,
        actor_ref: str,
    ) -> str:
        """Record a context_received operation at the inherited trace members.

        The selected Segments must already belong to this Agent's trace.
        The operation records their delivery to the Provider session without
        inserting or reordering content. Repeated delivery is idempotent.
        """

        def write(connection: Any) -> str:
            return self._record_context_received(
                connection, str(agent_id), str(provider_session_ref), segment_ids,
                actor_ref=actor_ref,
            )

        return self.database.write(write)

    @staticmethod
    def _context_seed_id(session_ref: str, agent_id: str, segment_ids: Sequence[str]) -> str:
        listed = hashlib.sha256("\0".join(str(item) for item in segment_ids).encode("utf-8")).hexdigest()
        return stable_segment_id(CONTEXT_SEED_KIND, session_ref, agent_id, listed)

    def _record_context_received(
        self, connection: Any, agent_id: str, session_ref: str, segment_ids: Sequence[str],
        *, actor_ref: str,
    ) -> str:
        if not agent_id or not session_ref:
            raise ValueError("Context delivery needs an Agent and Provider session")
        if not segment_ids:
            return ""
        sequence_id = self._locked_trace(connection, agent_id, actor_ref)
        rows = connection.execute(
            "SELECT segment_id, ordinal FROM sequence_segments WHERE sequence_id=%s AND segment_id=ANY(%s) ORDER BY ordinal",
            (sequence_id, list(segment_ids)),
        ).fetchall()
        present = {str(row["segment_id"]) for row in rows}
        missing = [item for item in segment_ids if item not in present]
        if missing:
            raise ValueError("Received context is not present in the Agent trace")
        operation_id = self._context_seed_id(session_ref, agent_id, segment_ids)
        put_operation(connection, operation_id=operation_id, kind="context_received", agent_id=agent_id,
            session_ref=session_ref, created_at=utc_now(),
            data={"first_segment_id": str(rows[0]["segment_id"]), "last_segment_id": str(rows[-1]["segment_id"])})
        self._place_operation(connection, sequence_id, operation_id, int(rows[0]["ordinal"]), actor_ref)
        return operation_id

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
        """Freeze model context, not the audit transcript, in one read snapshot.

        A historical cutoff is resolved in what the session held, read from
        the Agent trace (``_session_transcript_ids``), before applying its
        last compaction there. A later compaction must never leak future
        facts into a Fork-from-here snapshot.  An agent without a session is
        empty when it never ran, or when its context was cleared
        (``context_cleared``): the audit trace then holds only cleared work.
        """
        with self.database.snapshot() as connection:
            exact_session_ref = str(provider_session_ref or "").strip()
            if exact_session_ref:
                session = connection.execute(
                    'SELECT * FROM provider_sessions WHERE provider_session_ref=%s',
                    (exact_session_ref,),
                ).fetchone()
            else:
                session = connection.execute(
                    'SELECT * FROM provider_sessions WHERE provider=%s AND backend_route_id=%s AND backend_session_id=%s',
                    (provider, backend_route_id, backend_session_id),
                ).fetchone()
            context_ids = self._session_context_ids(
                connection, str(session["provider_session_ref"]), agent_id=agent_id,
            ) if session is not None and not through_turn_id else []
            if not through_turn_id and (session is None or not context_ids):
                if context_cleared or session is not None:
                    return {"segments": [], "empty_context": True}
                if agent_id and not (backend_session_id or provider_session_ref):
                    return self._agent_context_snapshot(connection, agent_id)
                return None
            if session is None:
                return None
            if through_turn_id:
                # A historical cutoff reads what the session held from its
                # Agent trace, the one record of everything that happened.
                member_ids = self._session_transcript_ids(
                    connection, str(session["provider_session_ref"]), agent_id=agent_id,
                )
            else:
                member_ids = context_ids
            segments = self._read_segment_rows(connection, member_ids)
            self._attach_provider_turns(connection, segments)
            if through_turn_id:
                positions = [i for i, item in enumerate(segments)
                             if item["provider_turn_id"] == through_turn_id]
                if not positions:
                    raise ValueError("Fork cutoff has no recorded Provider context")
                segments = segments[:positions[-1] + 1]
                compactions = [i for i, item in enumerate(segments)
                               if item["segment_type"] == "compaction"]
                if compactions:
                    segments = segments[compactions[-1]:]
            # What the Provider's own process put in its window stays with it.
            segments = [
                item for item in segments
                if (item.get("metadata") or {}).get("kind") not in PROVIDER_ONLY_KINDS
            ]
            return {
                "provider_session_ref": str(session["provider_session_ref"]),
                "provider": str(session["provider"]),
                "backend_session_id": str(session["backend_session_id"]),
                "backend_route_id": str(session["backend_route_id"]),
                "through_turn_id": through_turn_id,
                "segments": segments,
                "empty_context": not segments,
            }

    def fork_context_segments(self, references: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        ids = [str(item["segment_id"]) for item in references]
        with self.database.read() as connection:
            segments = self._read_segment_rows(connection, ids)
            if [item["segment_id"] for item in segments] != ids:
                raise ValueError("Fork context snapshot is incomplete")
            for segment, reference in zip(segments, references):
                segment["provider_turn_id"] = str(reference.get("provider_turn_id") or "")
            return segments

    def _sessions_by_backend(
        self, connection: Any, provider: str, backend_session_id: str, backend_route_id: str = "",
    ) -> list[Any]:
        if backend_route_id:
            return connection.execute(
                'SELECT * FROM provider_sessions WHERE provider = %s AND backend_route_id = %s '
                'AND backend_session_id = %s',
                (provider, backend_route_id, backend_session_id),
            ).fetchall()
        return connection.execute(
            'SELECT * FROM provider_sessions WHERE provider = %s AND backend_session_id = %s '
            'ORDER BY updated_at DESC',
            (provider, backend_session_id),
        ).fetchall()

    @staticmethod
    def _attach_provider_turns(connection, segments):
        identities = [item["segment_id"] for item in segments]
        turns = {}
        for chunk in _batches(identities):
            turns.update({str(row["segment_id"]): str(row["provider_turn_id"] or "")
                for row in connection.execute(
                    f"SELECT segment.segment_id, {PROVIDER_TURN_SQL} AS provider_turn_id "
                    "FROM segments segment LEFT JOIN executions execution USING(execution_id) "
                    "WHERE segment.segment_id = ANY(%s)", (chunk,),
                ).fetchall()})
        for item in segments:
            item["provider_turn_id"] = turns[item["segment_id"]]

    def session_transcript(
        self,
        *,
        provider: str,
        backend_session_id: str,
        backend_route_id: str = "",
    ) -> dict[str, Any] | None:
        """A Provider session's transcript, read from its Agent trace, as timeline items."""

        with self.database.read() as connection:
            sessions = self._sessions_by_backend(connection, provider, backend_session_id, backend_route_id)
            if not sessions:
                return None
            session = sessions[0]
            session_ref = str(session["provider_session_ref"])
            segments = self._read_segment_rows(
                connection, self._session_transcript_ids(connection, session_ref),
            )
            self._attach_provider_turns(connection, segments)
            return {
                "provider_session_ref": session_ref,
                "provider": str(session["provider"]),
                "backend_route_id": str(session["backend_route_id"]),
                "backend_session_id": str(session["backend_session_id"]),
                "updated_at": (iso_time(session["updated_at"]) or ""),
                "items": [
                    self._timeline_segment(
                        segment,
                        turn_id=segment["provider_turn_id"],
                    )
                    for segment in segments
                    if str(segment.get("segment_type")) != "tool"
                ],
                "resource_events": [
                    self._tool_resource(
                        segment,
                        turn_id=segment["provider_turn_id"],
                    )
                    for segment in segments
                    if str(segment.get("segment_type")) == "tool"
                ],
                "embedded_images": [],
            }


    def provider_user_prompts(
        self,
        *,
        provider: str,
        backend_session_id: str,
    ) -> list[dict[str, Any]]:
        transcript = self.session_transcript(
            provider=provider,
            backend_session_id=backend_session_id,
        )
        if transcript is None:
            return []
        return [
            item
            for item in transcript.get("items") or []
            if str(item.get("role") or "") in {"user", "human"}
            or str(item.get("source") or "") == "human_prompt"
        ]

    def message_provider_prompts(self, agent_id: str, message_id: str) -> list[dict[str, Any]]:
        """The user prompts of the Provider session whose execution took this message.

        A ``provider_api`` Resident runs each input in a session of its own,
        and a context reset starts a new session, so the Resident's current
        session may not be the one that took an earlier message.
        """

        try:
            source = self.chat_message_provenance(agent_id, message_id)
        except KeyError:
            return []
        if not source["provider_session_ref"]:
            return []
        with self.database.read() as connection:
            session = connection.execute(
                "SELECT provider, backend_session_id FROM provider_sessions WHERE provider_session_ref = %s",
                (source["provider_session_ref"],),
            ).fetchone()
        if session is None:
            return []
        return self.provider_user_prompts(
            provider=str(session["provider"]), backend_session_id=str(session["backend_session_id"]),
        )

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
