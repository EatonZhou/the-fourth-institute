"""Create an empty execution store; consume existing storage without migration."""
from __future__ import annotations

from typing import Any

from .scheduler_changes import install_change_triggers


EXECUTION_STORE_SCHEMA_VERSION = "19"


def _transaction_lock(connection: Any, scope: str) -> None:
    """Serialize schema initialization across starting services."""
    connection.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(current_schema() || ':' || %s, 0))",
        (scope,),
    )


def initialize_execution_store(connection: Any) -> None:
    """Create tables and notifications only for an empty store."""
    _transaction_lock(connection, "schema-init")
    if connection.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_schema=current_schema() LIMIT 1"
    ).fetchone() is not None:
        return
    _create_store(connection)
    install_change_triggers(connection)


def _create_store(connection: Any) -> None:
    # Older schemas keep cancel_requested and usage_accounted as BIGINT
    # until their conversion: SQL writes and compares '1'/'0', which
    # both BIGINT and BOOLEAN accept.
    connection.executescript(
        """
                CREATE TABLE IF NOT EXISTS execution_store_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS executions (
                    execution_id TEXT PRIMARY KEY,
                    capability TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    priority BIGINT NOT NULL,
                    correlation_id TEXT NOT NULL,
                    request_json JSONB NOT NULL,
                    status TEXT NOT NULL,
                    owner_worker_id TEXT NOT NULL DEFAULT '',
                    claim_token TEXT NOT NULL DEFAULT '',
                    lease_expires_at TIMESTAMPTZ,
                    available_at TIMESTAMPTZ NOT NULL DEFAULT 'epoch',
                    cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
                    attempts BIGINT NOT NULL DEFAULT 0,
                    checkpoint_json JSONB NOT NULL DEFAULT '{}',
                    result_json JSONB NOT NULL DEFAULT '{}',
                    error TEXT NOT NULL DEFAULT '',
                    usage_accounted BOOLEAN NOT NULL DEFAULT FALSE,
                    usage_accounted_json JSONB NOT NULL DEFAULT '{}',
                    acknowledged_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL
                );
                CREATE INDEX IF NOT EXISTS executions_schedule_idx
                    ON executions(status, priority DESC, created_at ASC);
                CREATE INDEX IF NOT EXISTS executions_actor_idx
                    ON executions(actor, created_at DESC);
                CREATE INDEX IF NOT EXISTS executions_terminal_retention_idx
                    ON executions(status, updated_at);
                CREATE TABLE IF NOT EXISTS execution_workers (
                    worker_id TEXT PRIMARY KEY,
                    build_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    capabilities_json JSONB NOT NULL,
                    pid BIGINT NOT NULL DEFAULT 0,
                    heartbeat_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL
                );
                CREATE TABLE IF NOT EXISTS capacity_dispatch_state (
                    capacity_key TEXT PRIMARY KEY,
                    last_dispatched_at TIMESTAMPTZ NOT NULL DEFAULT 'epoch',
                    next_dispatch_at TIMESTAMPTZ NOT NULL DEFAULT 'epoch',
                    interval_seconds DOUBLE PRECISION NOT NULL DEFAULT 0,
                    updated_at TIMESTAMPTZ NOT NULL
                );
                CREATE TABLE IF NOT EXISTS capacity_settings (
                    capacity_key TEXT PRIMARY KEY,
                    limit_value BIGINT NOT NULL,
                    dispatch_interval_min_seconds DOUBLE PRECISION,
                    dispatch_interval_max_seconds DOUBLE PRECISION,
                    updated_by TEXT NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL
                );
                CREATE TABLE IF NOT EXISTS execution_actor_dispatch_state (
                    priority BIGINT NOT NULL,
                    actor TEXT NOT NULL,
                    last_dispatched_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL,
                    PRIMARY KEY (priority, actor)
                );
                CREATE TABLE IF NOT EXISTS execution_account_usage_hourly (
                    bucket_hour TIMESTAMPTZ NOT NULL, provider TEXT NOT NULL,
                    account_id TEXT NOT NULL, resident_id TEXT NOT NULL,
                    input_tokens BIGINT NOT NULL DEFAULT 0,
                    cached_input_tokens BIGINT NOT NULL DEFAULT 0,
                    output_tokens BIGINT NOT NULL DEFAULT 0,
                    reasoning_output_tokens BIGINT NOT NULL DEFAULT 0,
                    total_tokens BIGINT NOT NULL DEFAULT 0, updated_at TIMESTAMPTZ NOT NULL,
                    PRIMARY KEY (bucket_hour, provider, account_id, resident_id)
                );
                CREATE TABLE IF NOT EXISTS execution_token_usage_events (
                    execution_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    occurred_at TIMESTAMPTZ NOT NULL,
                    usage_json JSONB NOT NULL,
                    PRIMARY KEY (execution_id, event_id),
                    FOREIGN KEY (execution_id)
                        REFERENCES executions(execution_id)
                        ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS execution_input_controls (
                    control_id TEXT PRIMARY KEY,
                    execution_id TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    input_json JSONB NOT NULL,
                    expected_turn_id TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT '',
                    created_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL,
                    FOREIGN KEY (execution_id)
                        REFERENCES executions(execution_id)
                        ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS execution_input_controls_pending_idx
                    ON execution_input_controls(
                        execution_id, status, created_at
                    );
                    """
    )
    connection.execute(
        "\n                    INSERT INTO execution_store_meta (key, value)\n                    VALUES ('schema_version', %s)\n                    ",
        (EXECUTION_STORE_SCHEMA_VERSION,),
    )
