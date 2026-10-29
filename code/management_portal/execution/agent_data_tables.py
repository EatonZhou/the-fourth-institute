"""The AgentData schema: its tables, columns and indexes, created once and checked at every start."""
from __future__ import annotations

from typing import Any

from management_common.agent_data_schema import AGENT_DATA_SCHEMA
from management_common.format_tags import schema_matches

from .agent_operations import TABLE_SQL
from .conversation_projection import CHAT_PREDICATE
from . import chat_position_index, execution_inputs


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


def initialize_agent_data(connection: Any) -> None:
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
