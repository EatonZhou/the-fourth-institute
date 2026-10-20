"""The closed persistence contract for Resident identity and operational facts.

The API record is assembled from its domain owners. It is not a document to
persist wholesale: every accepted field has a named, typed storage location.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from management_common.database_time import instant, iso_time
from management_common.json_columns import json_text, json_value


# field -> (column, type). Nullable columns preserve absence without a second
# list of present fields. JSON is reserved for an explicitly named structure.
IDENTITY_FIELDS = {
    "resident_id": ("resident_id", "text"),
    "title": ("title", "text"),
    "backend": ("backend", "text"),
    "created_at": ("created_at", "instant"),
    "updated_at": ("updated_at", "instant"),
    "created_by": ("created_by", "text"),
    "description": ("description", "text"),
    "updated_by": ("updated_by", "text"),
    "title_origin": ("title_origin", "text"),
    "principal_hierarchy": ("principal_hierarchy_json", "object"),
    "personal_owner_id": ("personal_owner_id", "text"),
    "deleted_at": ("deleted_at", "instant"),
    "deleted_by": ("deleted_by", "text"),
    # A historical deletion can outlive its Lineage. This retains the known
    # Project at deletion; current membership still derives from the Lineage.
    "historical_project_id": ("historical_project_id", "text"),
    "owned_workspace_id": ("owned_workspace_id", "text"),
}

# Session maintenance and rebuildable observations have separate owners.
SESSION_FIELDS = {
    field: (field, "instant" if field in {"context_compacted_at", "context_reset_at", "last_token_usage_at", "last_turn_usage_at", "last_interaction_at", "forked_from_at"} else "text") for field in (
        "session_state", "context_compacted_at", "context_compaction_policy",
        "context_compaction_state", "context_reset_at", "session_created_via",
    )
}
SESSION_FIELDS.update({field: (field + "_json", "object") for field in ("session_restart", "session_tools_refresh")})
OBSERVATION_FIELDS = {
    field: (field, "instant" if field in {"context_compacted_at", "context_reset_at", "last_token_usage_at", "last_turn_usage_at", "last_interaction_at", "forked_from_at"} else "text") for field in ("last_error", "last_token_usage_at", "last_turn_usage_at", "last_interaction_at")
}
OBSERVATION_FIELDS.update({"last_model_context_window": ("last_model_context_window", "integer"),
    **{field: (field + "_json", "object") for field in ("last_runtime_error", "last_token_usage", "last_turn_usage")}})
RECOVERY_FIELDS = {field: (field + "_json", "object") for field in ("work_recovery", "project_bootstrap")}
# Registration publishes its resources before the identity is ready; a derived
# Resident is ready when it is created.
REGISTRATION_STATES = ("initializing", "resource_pending", "ready")
IDENTITY_FIELDS.update({"registration_state": ("registration_state", "text"),
                        "intake_open": ("intake_open", "boolean"),
                        "intake_transition_id": ("intake_transition_id", "text")})
# Writes name every column, so an omitted field takes the column default here.
COLUMN_DEFAULTS = {"intake_open": True, "registration_state": "ready"}

CONFIGURATION_FIELDS = {
    "backend_route_id": ("backend_route_id", "text"),
    "execution_settings": ("execution_settings_json", "object"),
    "harness_config": ("harness_config_json", "object"),
    "quota_reserve_override": ("quota_reserve_override_json", "object"),
}
PROVENANCE_FIELDS = {
    field: (field, "instant" if field in {"context_compacted_at", "context_reset_at", "last_token_usage_at", "last_turn_usage_at", "last_interaction_at", "forked_from_at"} else "text") for field in (
        "source_resident_id", "context_source_resident_id", "forked_from_at", "fork_mode", "source_message_id",
    )
}
DOMAIN_TABLES = {
    "residents": IDENTITY_FIELDS,
    "resident_execution_configuration": CONFIGURATION_FIELDS,
    "resident_derivations": PROVENANCE_FIELDS,
    "resident_sessions": SESSION_FIELDS,
    "resident_observation_cache": OBSERVATION_FIELDS,
    "resident_recovery": RECOVERY_FIELDS,
}
RECOVERY_INDEXES = {
    "resident_failed_work_idx": "ON resident_observation_cache(resident_id) WHERE last_error IS NOT NULL AND last_error<>''",
    "resident_deferred_restart_idx": "ON resident_sessions(resident_id) WHERE session_restart_json::jsonb ? 'deferred'",
    "resident_quota_override_idx": "ON resident_execution_configuration(resident_id) WHERE quota_reserve_override_json IS NOT NULL AND quota_reserve_override_json<>'{}'",
}


def deferred_restart_resident_ids(connection):
    return [row["resident_id"] for row in connection.execute(
        "SELECT resident_id FROM resident_sessions WHERE session_restart_json::jsonb ? 'deferred' ORDER BY resident_id"
    ).fetchall()]


def quota_override_resident_ids(connection):
    return [row["resident_id"] for row in connection.execute(
        "SELECT resident_id FROM resident_execution_configuration "
        "WHERE quota_reserve_override_json IS NOT NULL AND quota_reserve_override_json<>'{}' ORDER BY resident_id"
    ).fetchall()]


def failed_work_resident_ids(connection, *, checked_before: float):
    """Only retained failed inputs whose automatic retry is ready for review."""
    return [row["resident_id"] for row in connection.execute("""
        WITH failed AS (
            SELECT resident_id FROM resident_observation_cache WHERE last_error IS NOT NULL AND last_error<>''
            UNION SELECT resident_id FROM resident_execution_work WHERE progress_json::jsonb->>'outcome'='failed'
        )
        SELECT o.resident_id FROM failed o
        JOIN resident_mailbox_progress p USING(resident_id)
        JOIN conversation_input_states s ON s.resident_id=o.resident_id
            AND s.message_id=p.progress_json::jsonb->'checkpoints'->-1->>'message_id'
        JOIN residents r ON r.resident_id=o.resident_id
        LEFT JOIN resident_recovery recovery ON recovery.resident_id=o.resident_id
        WHERE s.state='failed'
            AND r.deleted_at IS NULL AND r.intake_open IS NOT FALSE
            AND COALESCE(recovery.work_recovery_json::jsonb->s.message_id->>'disabled','false')<>'true'
            AND COALESCE(recovery.work_recovery_json::jsonb->s.message_id->'checked_at','0'::jsonb)<=to_jsonb(%s::double precision)
        ORDER BY o.resident_id
    """, (checked_before,)).fetchall()]


PERSISTED_FIELDS = frozenset(field for fields in DOMAIN_TABLES.values() for field in fields) | {"workspace_bindings"}

# Service projections accepted on a full-record update but never serialized.
# They are rebuilt from the authoritative fields during every read.
PROJECTION_FIELDS = frozenset({
    "schema", "folder_schema",
    "lineage_id", "project_id", "lineage_title", "lineage_project_id", "workspace_path",
    "workspace_root", "owned_workspace", "default_cwd",
    "workspace_paths", "workspace_roots", "family",
})


def validate_record_fields(record: Mapping[str, Any]) -> None:
    unknown = set(record) - PERSISTED_FIELDS - PROJECTION_FIELDS
    if unknown:
        raise ValueError("Unknown Resident fields: " + ", ".join(sorted(unknown)))
    for fields in DOMAIN_TABLES.values():
        for field, (_column, kind) in fields.items():
            if field in record:
                encode(field, record[field], kind)
    if "principal_hierarchy" in record:
        from .principal_hierarchy import resident_assignment
        resident_assignment(record["principal_hierarchy"])
    if "execution_settings" in record:
        from .resident_settings_contract import runtime_settings_from_parts
        runtime_settings_from_parts({**record["execution_settings"], "backend": record.get("backend", "")}, record.get("harness_config") or {})
    if record.get("quota_reserve_override"):
        from management_portal.execution.account_quota import normalize_quota_override
        normalize_quota_override(record["quota_reserve_override"])



def encode(field: str, value: Any, kind: str) -> Any:
    if value is None:
        return None
    if kind == 'instant':
        return instant(value)
    expected = {"text": str, "object": dict, "array": list, "integer": int, "boolean": bool}[kind]
    if not isinstance(value, expected) or (kind == "integer" and isinstance(value, bool)):
        raise ValueError(f"Resident {field} must be {kind}")
    if kind in {"object", "array"}:
        return json_text(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return value


def decode(row: Mapping[str, Any], fields: Mapping[str, tuple[str, str]]) -> dict[str, Any]:
    return {
        field: json_value(row[column]) if kind in {"object", "array"} else iso_time(row[column]) if kind == "instant" else row[column]
        for field, (column, kind) in fields.items() if row.get(column) is not None
    }


def columns(fields: Mapping[str, tuple[str, str]]) -> tuple[str, ...]:
    return tuple(column for column, _kind in fields.values())


def create_table(connection: Any, table: str, fields: Mapping[str, tuple[str, str]]) -> None:
    types = {"instant": "TIMESTAMPTZ", "text": "TEXT", "object": "JSONB", "array": "JSONB", "integer": "BIGINT", "boolean": "BOOLEAN"}
    declarations = ["resident_id TEXT PRIMARY KEY"]
    for field, (column, kind) in fields.items():
        if field != "resident_id":
            declarations.append(f"{column} {types[kind]}")
    if table != "residents":
        declarations.append("FOREIGN KEY(resident_id) REFERENCES residents(resident_id) ON DELETE CASCADE")
    connection.execute(f"CREATE TABLE {table} ({', '.join(declarations)})")


def put_fields(connection: Any, table: str, fields: Mapping[str, tuple[str, str]], resident_id: str, record: Mapping[str, Any]) -> None:
    selected = {field: descriptor for field, descriptor in fields.items() if field != "resident_id"}
    names = ["resident_id", *columns(selected)]
    values = [resident_id, *(encode(field, record.get(field, COLUMN_DEFAULTS.get(field)), kind) for field, (_column, kind) in selected.items())]
    assignments = ", ".join(f"{column}=excluded.{column}" for column in names[1:])
    # Preserve row locks across domain writes, while avoiding a rewrite when
    # the caller changed only another domain's fields.
    excluded = ", ".join(f"excluded.{column}" for column in names[1:])
    connection.execute(
        f"INSERT INTO {table} ({', '.join(names)}) VALUES ({', '.join('%s' for _ in names)}) "
        f"ON CONFLICT(resident_id) DO UPDATE SET {assignments} "
        f"WHERE ({', '.join(f'{table}.{column}' for column in names[1:])}) IS DISTINCT FROM ({excluded})",
        values,
    )


def create_schema(connection: Any) -> None:
    from .resident_workspace_storage import WORKSPACE_SCHEMA_SQL
    for table, fields in DOMAIN_TABLES.items():
        create_table(connection, table, fields)
    for name, definition in RECOVERY_INDEXES.items():
        connection.execute(f"CREATE INDEX {name} {definition}")
    connection.execute("ALTER TABLE lineage_resident_nodes ADD CONSTRAINT lineage_resident_identity_fk FOREIGN KEY (node_id) REFERENCES residents(resident_id) DEFERRABLE INITIALLY DEFERRED")
    from . import family_storage
    family_storage.create_schema(connection)
    for statement in WORKSPACE_SCHEMA_SQL:
        connection.execute(statement)
    from management_portal.resources.workspace_store import get_workspace_store
    table = get_workspace_store().table
    connection.execute(f"ALTER TABLE residents ADD CONSTRAINT residents_owned_workspace_fk FOREIGN KEY (owned_workspace_id) REFERENCES {table}(workspace_id)")
    connection.execute(f"ALTER TABLE resident_workspace_bindings ADD CONSTRAINT resident_workspace_target_fk FOREIGN KEY (workspace_id) REFERENCES {table}(workspace_id)")
    connection.execute("ALTER TABLE residents ADD CONSTRAINT residents_live_workspace CHECK (deleted_at IS NOT NULL OR owned_workspace_id IS NOT NULL)")
    connection.execute(f"ALTER TABLE residents ADD CONSTRAINT residents_registration_state CHECK (registration_state IN {REGISTRATION_STATES})")
    connection.execute("ALTER TABLE residents ALTER COLUMN registration_state SET DEFAULT 'ready'")
    connection.execute("ALTER TABLE residents ALTER COLUMN registration_state SET NOT NULL")


def read_execution_settings(connection: Any, resident_id: str) -> dict[str, Any] | None:
    from .resident_settings_contract import runtime_settings_from_parts
    row = connection.execute(
        "SELECT r.backend,c.execution_settings_json,c.harness_config_json FROM residents r "
        "JOIN resident_execution_configuration c USING(resident_id) WHERE r.resident_id=%s AND r.deleted_at IS NULL", (resident_id,),
    ).fetchone()
    if row is None:
        return None
    return runtime_settings_from_parts(
        {**json_value(row["execution_settings_json"]), "backend": row["backend"]},
        json_value(row["harness_config_json"]),
    )
