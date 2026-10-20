"""Archive source control states and make copied history non-consumable."""
from copy import deepcopy
import json
from datetime import datetime, timezone

CLEAR_TABLES = {
    "resident_execution_work", "resident_execution_release_work", "input_scopes", "worker_lease",
    "operator_work", "pending_event_deliveries", "pending_expiry_notices", "resident_delivery_profiles",
    "resident_recovery", "resident_lifecycle_operations", "effect_recovery_cleanup", "resident_delivery_work",
    "effect_work", "lineage_move_operations", "resident_creation_operations", "family_relays", "family_quota_reviews",
    "family_quota_migrations", "resident_mailbox_progress", "lineage_deletion_operations", "lineage_resource_projection",
    "resident_mailbox",
    "context_absorption_work", "workspace_handoff_work", "resident_observation_cache",
    "embedding_jobs", "summary_jobs",
}


def freeze(tables):
    tables = deepcopy(tables)
    archive = []
    counts = {}
    frozen_at = datetime.now(timezone.utc)
    # Snapshots/session bindings are source provider control data, not chat
    # history; the independent instance must establish new provider sessions.
    for key in [('resident_context', 'snapshots'), ('lineage', 'resident_sessions')]:
        if key in tables:
            for row in tables[key]:
                archive.append({'source_schema': key[0], 'source_table': key[1], 'row': row})
            counts['.'.join(key)] = len(tables[key])
            tables[key] = []
    for (schema, name), rows in tables.items():
        if name in CLEAR_TABLES:
            for row in rows:
                archive.append({"source_schema": schema, "source_table": name, "row": row})
            counts[schema + "." + name] = len(rows)
            rows.clear()
        elif name == "conversation_input_states":
            for row in rows:
                if row.get("state") in {"queued", "running"}:
                    archive.append({"source_schema": schema, "source_table": name, "row": deepcopy(row)})
                    row["state"] = "cancelled"
                    row["error_json"] = {"code": "distribution.history_frozen", "message": "Source input retained as history; never replayed on installation"}
        elif name == "conversation_states":
            for row in rows:
                archive.append({"source_schema": schema, "source_table": name, "row": deepcopy(row)})
                row["state_json"] = {"working": False, "active_message_id": "", "queue": {"items": []}}
                row["activity_json"] = []
        elif name == "event_source_progress":
            for row in rows:
                archive.append({"source_schema": schema, "source_table": name, "row": deepcopy(row)})
                row["checkpoint_json"] = {"paused": True}
        elif schema == "agent" and name == "executions":
            for row in rows:
                if row.get("status") in {"preparing", "queued", "running"}:
                    archive.append({"source_schema": schema, "source_table": name, "row": deepcopy(row)})
                    row["status"] = "cancelled"
        elif name in {'trigger_paths', 'trigger_path_steps'}:
            for row in rows:
                if row.get('status') in {'active', 'pending', 'running', 'waiting'}:
                    archive.append({'source_schema': schema, 'source_table': name, 'row': deepcopy(row)})
                    row['status'] = 'cancelled'
                    row['finished_at'] = frozen_at
                    if name == 'trigger_path_steps':
                        row['checkpoint_json'] = {}
    # Every event source is paused, even when source progress had no row.
    existing = {row["source_id"] for row in tables.get(("lineage", "event_source_progress"), [])}
    progress = tables.setdefault(("lineage", "event_source_progress"), [])
    for node in tables.get(("lineage", "lineage_nodes"), []):
        if node.get("node_type") == "event_source" and node["node_id"] not in existing:
            progress.append({"lineage_id": node["lineage_id"], "source_id": node["node_id"], "checkpoint_json": {"paused": True}})
    for item in archive:
        item['row'] = {key: json.loads(value) if key.endswith('_json') and isinstance(value, str) else value
                       for key, value in item['row'].items()}
    return tables, archive, {"cleared_control_tables": counts, "archived_control_rows": len(archive), "event_sources_paused": True, "pending_inputs_cancelled": True, "active_trigger_paths_cancelled": True}
