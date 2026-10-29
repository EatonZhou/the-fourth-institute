"""The execution store schema: its exact table, column and index contract, created once and checked at every start."""
from __future__ import annotations

from typing import Any

from .scheduler_changes import install_change_triggers


EXECUTION_STORE_SCHEMA_VERSION = "19"


EXECUTION_STORE_COLUMNS = {
    "execution_store_meta": {"key", "value"},
    "executions": {
        "execution_id", "capability", "actor",
        "priority", "correlation_id", "request_json", "status",
        "owner_worker_id", "claim_token", "lease_expires_at",
        "available_at",
        "cancel_requested", "attempts", "checkpoint_json",
        "result_json", "error", "usage_accounted",
        "usage_accounted_json",
        "acknowledged_at", "created_at", "updated_at",
    },
    "execution_workers": {
        "worker_id", "build_id", "state",
        "capabilities_json", "pid", "heartbeat_at", "updated_at",
    },
    "capacity_dispatch_state": {
        "capacity_key", "last_dispatched_at", "next_dispatch_at",
        "interval_seconds", "updated_at",
    },
    "capacity_settings": {
        "capacity_key", "limit_value",
        "dispatch_interval_min_seconds",
        "dispatch_interval_max_seconds",
        "updated_by", "updated_at",
    },
    "execution_actor_dispatch_state": {
        "priority", "actor", "last_dispatched_at", "updated_at",
    },
    "execution_account_usage_hourly": {
        "bucket_hour", "provider", "account_id", "resident_id",
        "input_tokens", "cached_input_tokens", "output_tokens",
        "reasoning_output_tokens", "total_tokens", "updated_at",
    },
    "execution_token_usage_events": {
        "execution_id", "event_id", "occurred_at", "usage_json",
    },
    "execution_input_controls": {
        "control_id", "execution_id", "actor", "input_json",
        "expected_turn_id", "status", "error", "created_at",
        "updated_at",
    },
}
EXECUTION_STORE_INDEXES = {
    "executions_schedule_idx",
    "executions_actor_idx",
    "executions_terminal_retention_idx",
    "execution_input_controls_pending_idx",
}


def _transaction_lock(connection: Any, scope: str) -> None:
    """Serialize schema initialization across starting services."""
    connection.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(current_schema() || ':' || %s, 0))",
        (scope,),
    )


def initialize_execution_store(connection: Any) -> None:
    """Check the store an earlier start created, or create it; then install its change triggers."""
    _transaction_lock(connection, "schema-init")
    existing_tables = {
        str(row["table_name"])
        for row in connection.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema=current_schema() AND table_type='BASE TABLE'"
        )
    }
    # ExecutionService settings are owned by their module, outside the
    # scheduler's exact table/column contract.
    existing_tables.discard("service_settings")
    if existing_tables:
        _check_contract(connection, existing_tables)
    else:
        _create_store(connection)
    install_change_triggers(connection)


def _check_contract(connection: Any, existing_tables: set[str]) -> None:
    if "execution_store_meta" not in existing_tables:
        raise RuntimeError(
            "execution store schema does not match current contract"
        )
    actual_columns = {
        table: {
            str(row["column_name"])
            for row in connection.execute(
                'SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=%s',
                (table,),
            ).fetchall()
        }
        for table in EXECUTION_STORE_COLUMNS
        if table in existing_tables
    }
    actual_indexes = {
        str(row["indexname"])
        for row in connection.execute(
            "SELECT indexname FROM pg_indexes "
            "WHERE schemaname=current_schema()"
        )
        if not str(row["indexname"]).endswith("_pkey")
        and not str(row["indexname"]).endswith("_key")
    }
    if (
        existing_tables != set(EXECUTION_STORE_COLUMNS)
        or actual_columns != EXECUTION_STORE_COLUMNS
        or actual_indexes != EXECUTION_STORE_INDEXES
    ):
        raise RuntimeError(
            "execution store schema does not match current contract"
        )
    version = connection.execute(
        "SELECT value FROM execution_store_meta "
        "WHERE key = 'schema_version'"
    ).fetchone()
    if (
        version is None
        or str(version["value"]) != EXECUTION_STORE_SCHEMA_VERSION
    ):
        raise RuntimeError(
            "execution store schema does not match current contract"
        )


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
