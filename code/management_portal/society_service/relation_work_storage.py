"""Workspace handoff and context absorption progress; Resident state is a view."""
from __future__ import annotations

from management_common.database_time import decode_times
from management_common.json_columns import json_text

TABLES = {"handoff": "workspace_handoff_work", "absorption": "context_absorption_work"}
COLUMNS = {table: ("lineage_id", "edge_id", "progress_json") for table in TABLES.values()}
RECOVERY_INDEXES = {
    "absorption_pending_recovery_idx": "ON context_absorption_work(lineage_id,edge_id) WHERE COALESCE(progress_json::jsonb->>'target_message_id','')<>''",
}


def recovery_lineage_ids(connection):
    return {row["lineage_id"] for row in connection.execute("""
        SELECT w.lineage_id FROM context_absorption_work w
        JOIN lineage_edges e ON e.edge_id=w.edge_id AND e.lineage_id=w.lineage_id
        WHERE COALESCE(w.progress_json::jsonb->>'target_message_id','')<>''
            AND e.edge_type='absorption' AND e.deleted_at IS NULL
    """).fetchall()}


def create_schema(connection):
    for table in TABLES.values():
        connection.execute(f"CREATE TABLE {table}(lineage_id TEXT NOT NULL REFERENCES lineages(lineage_id), "
                           "edge_id TEXT NOT NULL, progress_json JSONB NOT NULL, PRIMARY KEY(lineage_id,edge_id))")
    for name, definition in RECOVERY_INDEXES.items():
        connection.execute(f"CREATE INDEX {name} {definition}")


def read(connection, lineage_id):
    # Both tables return JSONB while one of them may still store JSON text.
    rows = connection.execute(" UNION ALL ".join(
        f"SELECT '{kind}' kind,edge_id,progress_json::jsonb progress_json FROM {table} WHERE lineage_id=%s"
        for kind, table in TABLES.items()), (lineage_id,) * len(TABLES)).fetchall()
    result = {}
    for row in rows:
        result.setdefault(row['edge_id'], {})[row['kind']] = row['progress_json']
    return result


def put(connection, lineage_id, before, after):
    for kind, table in TABLES.items():
        old = {key: value[kind] for key, value in before.items() if kind in value}
        new = {key: value[kind] for key, value in after.items() if kind in value}
        removed = list(old.keys() - new.keys())
        if removed:
            connection.execute(f"DELETE FROM {table} WHERE lineage_id=%s AND edge_id=ANY(%s)", (lineage_id, removed))
        changed = [(lineage_id, key, json_text(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False))
                   for key, value in new.items() if old.get(key) != value]
        if changed:
            connection.executemany(f"INSERT INTO {table} VALUES(%s,%s,%s) ON CONFLICT(lineage_id,edge_id) "
                                   "DO UPDATE SET progress_json=excluded.progress_json", changed)


def resident_observations(connection, lineage_id):
    from management_common.portal_database import _database_schema
    from management_portal.execution.agent_data_store import get_agent_data_store
    get_agent_data_store()
    table = '"' + _database_schema("agent") + '".executions'
    rows = connection.execute(f"""
        SELECT n.node_id,e.execution_id,e.status,e.created_at,e.started_at,e.finished_at,i.message_id,
            CASE WHEN e.status='failed' AND s.state='failed' AND s.updated_at>=e.created_at
                 THEN LEFT(s.error_json::jsonb->>'message',2000) ELSE '' END AS error,
            CASE WHEN e.status='failed' AND s.state='failed' AND s.updated_at>=e.created_at
                 THEN LEFT(s.error_json::jsonb->>'code',120) ELSE '' END AS error_code
        FROM lineage_nodes n JOIN residents r ON r.resident_id=n.node_id AND r.deleted_at IS NULL
        CROSS JOIN LATERAL (
            SELECT execution_id,status,created_at,started_at,finished_at FROM {table} e
            WHERE e.agent_id=n.node_id ORDER BY created_at DESC,execution_id DESC LIMIT 1
        ) e LEFT JOIN "{_database_schema("agent")}".execution_inputs i
            ON i.execution_id=e.execution_id AND i.role='primary'
        LEFT JOIN conversation_input_states s ON s.resident_id=n.node_id AND s.message_id=i.message_id
        WHERE n.lineage_id=%s
    """, (lineage_id,)).fetchall()
    return {row["node_id"]: {
        "status": row["status"] if row["status"] in {"preparing", "queued", "running"} else "idle",
        "last_execution": decode_times({key: row[key] for key in ("execution_id", "status", "created_at", "started_at", "finished_at", "message_id", "error", "error_code")}, {"created_at", "started_at", "finished_at"}),
    } for row in rows}
