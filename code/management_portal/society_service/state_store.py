from __future__ import annotations

from management_common.format_tags import schema_matches

import json
import secrets
import threading
import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, MutableMapping
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Iterator

from management_common.portal_database import portal_database
from management_common.database_time import instant, iso_time
from management_common.json_columns import json_text, json_value
from .read_snapshot import request_cached
from . import trigger_path_storage, family_storage, resident_storage, program_work_storage, domain_work_storage, relation_work_storage, resident_execution_storage
from . import graph_storage, definition_collections, lineage_metadata_storage, lineage_state_domains
from . import wire_delivery_budget, family_task_dispatches
from management_portal import application_targets
from .runtime_read_models import (
    POSTGRES_TRIGGERS,
    create_runtime_read_models,
)


LINEAGE_DATA_SCHEMA = "tfi.lineage-data.v61"
LINEAGE_TIMER_LEASE_KEY = "lineage_timer_lease"
# Projects whose principal graph a membership transaction must re-project
# when it finishes; None outside such a transaction.
_HIERARCHY_SYNC_PENDING: ContextVar[set[str] | None] = ContextVar(
    "lineage_hierarchy_sync_pending", default=None,
)
PROJECT_GRAPH_FILTER = "lineage_id IN (SELECT lineage_id FROM project_lineage_entries)"

DEFINITION_COLLECTION_FIELDS = frozenset({
    "function_blocks",
    "fragments",
})
DEFINITION_STRUCTURAL_FIELDS = frozenset({
    "nodes",
    "edges",
    "program",
    *DEFINITION_COLLECTION_FIELDS,
})
DEFINITION_COLUMN_FIELDS = frozenset({
    "run_as_principal",
    "schema",
    "schema_version",
    "lineage_id",
    "lineage_membership",
    "project_id",
    "title",
    "description",
    "leader_resident_id",
    "revision",
    "layout_revision",
    "created_at",
    "updated_at",
    "graph_updated_at",
    "layout_updated_at",
    "created_by",
})
RUNTIME_STRUCTURAL_FIELDS = frozenset({
    "nodes",
    "edges",
})
RUNTIME_COLUMN_FIELDS = frozenset({
    "schema",
    "lineage_id",
    "runtime_revision",
    "updated_at",
})

FORBIDDEN_FORK_IDENTITY_FIELDS = frozenset({
    "fork_id",
    "fork_type",
    "fork_status",
    "forks",
    "is_fork",
    "source_fork_id",
    "view",
})

LINEAGE_DATA_COLUMNS = {
    "instruction_executions": (
        "instruction_id", "instruction", "scope_kind", "scope_id", "caller", "entry",
        "request_key", "fingerprint", "status", "created_at", "updated_at",
        "references_json", "error_json", "recovery_key",
    ),
    "instruction_retry_guards": ("request_digest", "expired_at"),
    "effect_recovery_cleanup": ("lineage_id", "activation_key", "instructions_json", "created_at"),
    "resident_workspace_transfers": ("resident_id", "operation_id", "source_lineage_id", "target_lineage_id", "instruction_id"),
    "lineage_meta": ("key", "value"),
    **program_work_storage.COLUMNS,
    **wire_delivery_budget.COLUMNS,
    **family_task_dispatches.COLUMNS,
    **domain_work_storage.COLUMNS,
    **relation_work_storage.COLUMNS,
    **lineage_metadata_storage.COLUMNS,
    **graph_storage.COLUMNS,
    **definition_collections.COLUMNS,
    **application_targets.COLUMNS,
    "portal_human_notifications": ("singleton", "notifications_json"),
    "lineage_layout_positions": ("lineage_id", "object_id", "x", "y"),
    **{table: ("resident_id", *[column for column in resident_storage.columns(fields) if column != "resident_id"])
       for table, fields in resident_storage.DOMAIN_TABLES.items()},
    "resident_workspace_bindings": ("resident_id", "workspace_id", "purpose"),
    **family_storage.COLUMNS,
    **trigger_path_storage.COLUMNS,
    "resident_execution_work": resident_execution_storage.COLUMNS,
    "resident_execution_release_work": ("execution_id","resident_id","actor"),
    "resident_delivery_work": ("resident_id", "message_id", "revision"),
    "resident_mailbox": (
        "resident_id", "message_id", "place", "position", "group_id",
        "author_id", "accepted_at", "updated_at", "message_json", "item_json",
    ),
    "resident_resource_projection_outbox": (
        "resident_id", "revision", "payload_json", "enqueued_at",
        "attempt_count", "available_at", "lease_token", "lease_until",
        "last_error",
    ),
    "resident_lifecycle_operations": (
        "operation_id", "resident_id", "payload_json", "available_at",
        "last_error",
    ),
    "conversation_states": (
        "resident_id", "cursor", "state_json", "activity_json",
    ),
    "conversation_input_states": (
        "resident_id", "message_id", "state", "error_json", "updated_at",
    ),
    "conversation_events": (
        "resident_id", "cursor", "event_type", "subject_id",
        "occurred_at", "payload_json",
    ),
    "conversation_event_delivery": ("sequence", "resident_id", "cursor"),
}
POSTGRES_LINEAGE_DATA_COLUMNS = {
    **LINEAGE_DATA_COLUMNS,
    "input_scopes": (
        "resident_id", "message_id", "actor", "descriptor",
    ),
}

LINEAGE_DATA_INDEXES = frozenset({
    "trigger_paths_lineage_idx", "trigger_paths_active_idx", "trigger_path_steps_path_idx",
    "trigger_path_steps_input_idx", "trigger_path_execution_links_execution_idx",
    "trigger_path_steps_effect_latest_idx", "trigger_path_steps_effect_activation_idx",
    "trigger_path_steps_unblocked_idx", "trigger_path_step_edges_target_idx", "trigger_paths_finished_idx",
    "trigger_paths_cancelled_idx", "trigger_paths_payload_idx",
    "instruction_executions_request_idx",
    "instruction_executions_scope_idx",
    "instruction_executions_expiry_idx",
    "effect_recovery_cleanup_created_idx",
    "lineage_nodes_lineage_idx",
    "lineage_edges_lineage_idx",
    "lineage_edges_source_idx",
    "lineage_edges_target_idx",
    "residents_owned_workspace_idx",
    "resident_workspace_id_idx",
    "resident_mailbox_place_idx",
    "conversation_input_states_state_idx",
    "conversation_events_type_idx",
    "resident_resource_projection_outbox_available_idx",
    "conversation_event_delivery_event_idx",
})


def _json(value: object) -> str:
    return json_text(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _object(raw: object, identity: str) -> dict[str, Any]:
    value = json_value(raw or "{}")
    if not isinstance(value, dict):
        raise RuntimeError(f"Stored {identity} is not an object")
    return value


def _now_iso_millis() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class RuntimeCASConflict(RuntimeError):
    """A runtime object changed after the caller read its expected state."""


def _graph_objects(value: Mapping[str, Any]) -> dict[str, str]:
    objects: dict[str, str] = {}
    for kind, collection in (
        ("resident", value.get("nodes")),
        ("edge", value.get("edges")),
    ):
        for raw in collection or []:
            if isinstance(raw, Mapping) and str(raw.get("id") or "").strip():
                objects[str(raw["id"])] = kind
    program = value.get("program") if isinstance(value.get("program"), Mapping) else {}
    for kind, collection in (
        ("component", program.get("components")),
        ("wire", program.get("wires")),
        ("function_block", value.get("function_blocks")),
    ):
        for raw in collection or []:
            if isinstance(raw, Mapping) and str(raw.get("id") or "").strip():
                objects[str(raw["id"])] = kind
    return objects


def _layout_positions(value: Mapping[str, Any]) -> dict[str, tuple[str, float, float]]:
    positions: dict[str, tuple[str, float, float]] = {}
    graph_objects = _graph_objects(value)
    collections: list[object] = [
        value.get("nodes"),
    ]
    program = value.get("program") if isinstance(value.get("program"), Mapping) else {}
    collections.append(program.get("components"))
    for collection in collections:
        for raw in collection or []:
            if not isinstance(raw, Mapping):
                continue
            object_id = str(raw.get("id") or "").strip()
            if not object_id or object_id not in graph_objects:
                continue
            positions[object_id] = (
                graph_objects[object_id],
                float(raw.get("x") or 0),
                float(raw.get("y") or 0),
            )
    return positions


def _runtime_objects(value: Mapping[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    objects: dict[tuple[str, str], dict[str, Any]] = {}
    meta = {
        key: deepcopy(item)
        for key, item in value.items()
        if key not in RUNTIME_STRUCTURAL_FIELDS and key not in RUNTIME_COLUMN_FIELDS
    }
    if meta:
        objects[("meta", "root")] = meta
    for namespace in ("edges",):
        for object_id, state in (value.get(namespace) or {}).items():
            objects[(namespace[:-1], str(object_id))] = deepcopy(dict(state))
    return objects


def _runtime_from_objects(
    base: Mapping[str, Any],
    objects: Mapping[tuple[str, str], Mapping[str, Any]],
) -> dict[str, Any]:
    value = {
        "schema": str(base.get("schema") or ""),
        "lineage_id": str(base.get("lineage_id") or ""),
        "runtime_revision": int(base.get("runtime_revision") or 0),
    }
    if str(base.get("updated_at") or ""):
        value["updated_at"] = str(base["updated_at"])
    value.update(deepcopy(dict(objects.get(("meta", "root"), {}))))
    for namespace in ("node", "edge"):
        rows = {
            object_id: deepcopy(dict(state))
            for (kind, object_id), state in objects.items()
            if kind == namespace
        }
        if rows:
            value[f"{namespace}s"] = rows
    return value


def _postgres_lineage_data_schema_matches(connection: Any) -> bool:
    tables = {
        str(row["table_name"])
        for row in connection.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema=current_schema() AND table_type='BASE TABLE'"
        ).fetchall()
    }
    if tables != set(POSTGRES_LINEAGE_DATA_COLUMNS):
        return False
    rows = connection.execute(
        "SELECT table_name,column_name FROM information_schema.columns "
        "WHERE table_schema=current_schema() ORDER BY table_name,ordinal_position"
    ).fetchall()
    actual_columns: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        actual_columns[str(row["table_name"])].append(str(row["column_name"]))
    if any(
        tuple(actual_columns.get(table, ())) != expected
        for table, expected in POSTGRES_LINEAGE_DATA_COLUMNS.items()
    ):
        return False
    indexes = {
        str(row["indexname"])
        for row in connection.execute(
            "SELECT indexname FROM pg_indexes WHERE schemaname=current_schema()"
        ).fetchall()
    }
    if not LINEAGE_DATA_INDEXES.issubset(indexes):
        return False
    triggers = {
        str(row["trigger_name"])
        for row in connection.execute(
            "SELECT DISTINCT trigger_name FROM information_schema.triggers "
            "WHERE trigger_schema=current_schema()"
        ).fetchall()
    }
    return triggers == POSTGRES_TRIGGERS


def lineage_data_schema_matches(connection: Any) -> bool:
    return _postgres_lineage_data_schema_matches(connection)


def create_lineage_data_schema(connection: Any) -> None:
    lineage_metadata_storage.create_schema(connection)
    graph_storage.create_schema(connection)
    definition_collections.create_schema(connection)
    connection.executescript('\n        CREATE TABLE lineage_meta (\n            key TEXT PRIMARY KEY,\n            value TEXT NOT NULL\n        );\n\n        CREATE TABLE lineage_layout_positions (\n            lineage_id TEXT NOT NULL REFERENCES lineages(lineage_id),\n            object_id TEXT NOT NULL, x DOUBLE PRECISION NOT NULL, y DOUBLE PRECISION NOT NULL,\n            PRIMARY KEY(lineage_id,object_id)\n        );\n\n        ')
    program_work_storage.create_schema(connection)
    wire_delivery_budget.create_schema(connection)
    family_task_dispatches.create_schema(connection)
    domain_work_storage.create_schema(connection)
    relation_work_storage.create_schema(connection)
    resident_storage.create_schema(connection)
    application_targets.create_schema(connection)
    # Human notifications that belong to no Lineage (lineage_human_notifications.py).
    connection.execute(
        "CREATE TABLE portal_human_notifications (singleton INTEGER PRIMARY KEY CHECK(singleton=1), "
        "notifications_json JSONB NOT NULL)"
    )
    resident_execution_storage.create_schema(connection)
    from . import resident_delivery_work
    resident_delivery_work.create_schema(connection)
    connection.executescript(
        """
        CREATE TABLE resident_resource_projection_outbox (
            resident_id TEXT PRIMARY KEY,
            revision BIGINT NOT NULL,
            payload_json JSONB NOT NULL,
            enqueued_at TIMESTAMPTZ,
            attempt_count BIGINT NOT NULL DEFAULT 0,
            available_at TIMESTAMPTZ NOT NULL DEFAULT 'epoch',
            lease_token TEXT NOT NULL DEFAULT '',
            lease_until TIMESTAMPTZ,
            last_error TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX resident_resource_projection_outbox_available_idx
            ON resident_resource_projection_outbox(
                available_at, lease_until, resident_id
            );

        CREATE TABLE conversation_states (
            resident_id TEXT PRIMARY KEY,
            cursor BIGINT NOT NULL DEFAULT 0,
            state_json JSONB NOT NULL DEFAULT '{}',
            activity_json JSONB NOT NULL DEFAULT '[]',
            FOREIGN KEY (resident_id) REFERENCES residents(resident_id)
                ON DELETE CASCADE
        );

        CREATE TABLE conversation_input_states (
            resident_id TEXT NOT NULL,
            message_id TEXT NOT NULL,
            state TEXT NOT NULL CHECK (
                state IN ('queued','running','completed','failed','cancelled')
            ),
            error_json JSONB NOT NULL DEFAULT '{}',
            updated_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (resident_id, message_id),
            FOREIGN KEY (resident_id) REFERENCES conversation_states(resident_id)
                ON DELETE CASCADE
        );
        CREATE INDEX conversation_input_states_state_idx
            ON conversation_input_states(resident_id, state, updated_at);

        CREATE TABLE conversation_events (
            resident_id TEXT NOT NULL,
            cursor BIGINT NOT NULL,
            event_type TEXT NOT NULL,
            subject_id TEXT NOT NULL,
            occurred_at TIMESTAMPTZ NOT NULL,
            payload_json JSONB NOT NULL,
            PRIMARY KEY (resident_id, cursor),
            FOREIGN KEY (resident_id) REFERENCES conversation_states(resident_id)
                ON DELETE CASCADE
        );
        CREATE INDEX conversation_events_type_idx
            ON conversation_events(resident_id, event_type, subject_id, cursor);
        """
    )
    _create_resident_lifecycle_schema(connection)
    trigger_path_storage.create_schema(connection)
    for statement in MAILBOX_DDL:
        connection.execute(statement)
    create_instruction_operation_schema(connection)
    create_runtime_read_models(connection)
    connection.execute(
        "CREATE TABLE input_scopes ("
        "resident_id TEXT NOT NULL, message_id TEXT NOT NULL, "
        "actor TEXT NOT NULL, descriptor JSONB NOT NULL, "
        "PRIMARY KEY(resident_id,message_id), "
        "FOREIGN KEY (resident_id) REFERENCES residents(resident_id) ON DELETE CASCADE)"
    )
    connection.execute(
        "INSERT INTO lineage_meta(key, value) VALUES ('schema', %s)",
        (LINEAGE_DATA_SCHEMA,),
    )


# Records of what Lineage runs and Resident Mailboxes did. Rows are only
# appended: never updated, never deleted, not even with their Lineage or
# Resident. Current work is stored by its owning domain.



# A Resident's Mailbox: every message it accepted that is not in its trace
# yet (decision_b5d349190743).  ``place`` says where it waits: ``queue`` (a
# top-level queue item, at ``position``), ``group`` (a member of the waiting
# group ``group_id``, at ``position`` in it), ``info`` (a held info, at
# ``position``) or ``pending`` (accepted, in no queue: a facade request the
# Router has not shown yet, a round that left the queue and is not delivered
# yet).  ``message_json`` is the message; ``item_json`` the queue item without
# its members (and without ``input_message`` when that is the message itself),
# or for an info when it was held.  A message leaves when it enters the trace,
# when it is withdrawn, or when its input ends.
MAILBOX_DDL = (
    """
    CREATE TABLE resident_mailbox (
        resident_id TEXT NOT NULL,
        message_id TEXT NOT NULL,
        place TEXT NOT NULL CHECK (place IN ('pending','queue','group','info')),
        position BIGINT NOT NULL DEFAULT 0,
        group_id TEXT NOT NULL DEFAULT '',
        author_id TEXT NOT NULL DEFAULT '',
        accepted_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        message_json JSONB NOT NULL,
        item_json JSONB NOT NULL DEFAULT '{}',
        PRIMARY KEY (resident_id, message_id),
        FOREIGN KEY (resident_id) REFERENCES residents(resident_id)
            ON DELETE CASCADE
    )
    """,
    "CREATE INDEX resident_mailbox_place_idx ON resident_mailbox(resident_id, place, group_id, position)",
)


def _real_message(value: object) -> bool:
    return isinstance(value, Mapping) and isinstance(value.get("content_parts"), list)


# A queue item's input_message equal to the message the Mailbox keeps is
# stored once, in message_json; the item says so.
STORED_INPUT_MESSAGE = "input_message_in_mailbox"


def _encode_mailbox_message(message: Mapping[str, Any]) -> dict[str, Any]:
    """Pending input uses the same lossless envelope before entering a trace."""
    from management_portal.execution.message_projection import wrap_message
    stored = deepcopy(dict(message))
    for part in stored.get("content_parts", []):
        if part.get("type") == "text":
            part["text"] = wrap_message(str(part.get("text") or ""))
    return stored


def _decode_mailbox_message(raw: object, identity: str) -> dict[str, Any]:
    from management_portal.execution.message_projection import unwrap_message
    message = _object(raw, identity)
    for part in message.get("content_parts", []):
        if part.get("type") == "text":
            part["text"] = unwrap_message(part.get("text"))
    return message


def _mailbox_item(row: Any) -> dict[str, Any]:
    item = _object(row["item_json"], str(row["message_id"]))
    if item.pop(STORED_INPUT_MESSAGE, False):
        item["input_message"] = _decode_mailbox_message(row["message_json"], str(row["message_id"]))
    return item


def _mailbox_row(row: Any) -> dict[str, Any]:
    return {
        "resident_id": str(row["resident_id"]),
        "message_id": str(row["message_id"]),
        "place": str(row["place"]),
        "position": int(row["position"]),
        "group_id": str(row["group_id"]),
        "author_id": str(row["author_id"]),
        "accepted_at": iso_time(row["accepted_at"]),
        "updated_at": iso_time(row["updated_at"]),
        "message": _decode_mailbox_message(row["message_json"], str(row["message_id"])),
        "item": _object(row["item_json"], str(row["message_id"])),
    }


def _create_resident_lifecycle_schema(connection: Any) -> None:
    connection.execute("""
        CREATE TABLE IF NOT EXISTS resident_lifecycle_operations (
            operation_id TEXT PRIMARY KEY,
            resident_id TEXT NOT NULL UNIQUE,
            payload_json JSONB NOT NULL,
            available_at TIMESTAMPTZ NOT NULL DEFAULT 'epoch',
            last_error TEXT NOT NULL DEFAULT ''
        )
    """)


def create_effect_cleanup_schema(connection: Any) -> None:
    """Recovery acknowledgements outlive the graph that scheduled them."""
    connection.execute("CREATE TABLE IF NOT EXISTS effect_recovery_cleanup ("
                       "lineage_id TEXT NOT NULL, activation_key TEXT NOT NULL, "
                       "instructions_json JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL, "
                       "PRIMARY KEY(lineage_id,activation_key))")
    connection.execute("CREATE INDEX IF NOT EXISTS effect_recovery_cleanup_created_idx "
                       "ON effect_recovery_cleanup(created_at,lineage_id,activation_key)")


def create_instruction_operation_schema(connection: Any) -> None:
    """Instruction recovery, Effect cleanup and Workspace transfer ownership."""

    from management_portal.hatg.store import create_schema as create_execution_schema

    create_execution_schema(connection)
    create_effect_cleanup_schema(connection)

    connection.execute("""
        CREATE TABLE resident_workspace_transfers (
            resident_id TEXT PRIMARY KEY,
            operation_id TEXT NOT NULL UNIQUE,
            source_lineage_id TEXT NOT NULL,
            target_lineage_id TEXT NOT NULL,
            instruction_id TEXT NOT NULL
        )
    """)


class LineageDataStore:
    effect_work = domain_work_storage.repository("effect_work")
    lineage_drafts = domain_work_storage.repository("lineage_drafts")
    lineage_move_operations = domain_work_storage.repository("lineage_move_operations")
    resident_creation_operations = domain_work_storage.repository("resident_creation_operations")
    family_relays = domain_work_storage.repository("family_relays")
    family_quota_reviews = domain_work_storage.repository("family_quota_reviews")
    family_quota_migrations = domain_work_storage.repository("family_quota_migrations")
    resident_mailbox_progress = domain_work_storage.repository("resident_mailbox_progress")
    lineage_shared_state = domain_work_storage.repository("lineage_shared_state")
    lineage_human_notifications = domain_work_storage.repository("lineage_human_notifications")
    lineage_deletion_operations = domain_work_storage.repository("lineage_deletion_operations")
    lineage_resource_projection = domain_work_storage.repository("lineage_resource_projection")

    """Single persistence authority for SocietyService domain data."""

    def __init__(self) -> None:
        self.database = portal_database(schema="lineage")
        self.database_identity = self.database.identity
        self.database.write(self._initialize)

    def transaction(self, operation: Callable[[], Any]) -> Any:
        return self.database.write(lambda _connection: operation())

    def membership_transaction(self, operation: Callable[[], Any]) -> Any:
        """Validate derived Project references against the aggregate's final state."""
        def commit(connection: Any) -> Any:
            if _HIERARCHY_SYNC_PENDING.get() is not None:
                # The outermost membership transaction synchronizes.
                return operation()
            projects: set[str] = set()
            token = _HIERARCHY_SYNC_PENDING.set(projects)
            try:
                result = operation()
            finally:
                _HIERARCHY_SYNC_PENDING.reset(token)
            self._synchronize_project_graphs(connection, projects)
            return result

        return self.database.write(commit)














    @staticmethod
    def _initialize(connection: Any) -> None:
        connection.execute(
            "SELECT pg_advisory_xact_lock("
            "hashtextextended(current_schema() || ':schema-init', 0))"
        )
        current = connection.execute(
            "SELECT to_regclass(current_schema() || '.lineage_meta') AS table_name"
        ).fetchone()
        has_schema = current is not None and current["table_name"] is not None
        has_any_table = connection.execute(
            "SELECT 1 AS present FROM information_schema.tables "
            "WHERE table_schema=current_schema() LIMIT 1"
        ).fetchone() is not None
        if not has_schema:
            if has_any_table:
                raise RuntimeError("Lineage data database does not match its schema")
            create_lineage_data_schema(connection)
            return
        schema = connection.execute(
            "SELECT value FROM lineage_meta WHERE key='schema'"
        ).fetchone()
        if (
            schema is None
            or not schema_matches(schema["value"], "tfi.lineage-data.v61")
            or not lineage_data_schema_matches(connection)
        ):
            raise RuntimeError("Lineage data database does not match its schema")

    @staticmethod
    def _definitions_from_connection(connection: Any, lineage_ids: list[str] | None = None) -> list[dict[str, Any]]:
        headers = lineage_metadata_storage.read_many(connection, lineage_ids)
        ids = [row['lineage_id'] for row in headers]
        graphs = graph_storage.read_many(connection, ids)
        collections = definition_collections.read_many(connection, ids)
        family_storage.hydrate_programs(connection, graphs)
        positions = {(r['lineage_id'],r['object_id']): (r['x'],r['y']) for r in connection.execute(
            'SELECT * FROM lineage_layout_positions WHERE lineage_id=ANY(%s)', (ids,)).fetchall()}
        result = []
        for header in headers:
            lid = header['lineage_id']
            value = {**header, **graphs[lid], **collections[lid]}
            for items in (value['nodes'],value['program']['components']):
                for item in items:
                    position = positions.get((lid,item['id']))
                    if position is not None:
                        item['x'],item['y'] = position
            result.append(lineage_state_domains.definition_projection(value))
        return result


    def lineage_listing_records(self) -> list[dict[str, Any]]:
        with self.database.read() as connection:
            return lineage_metadata_storage.read_many(connection)

    def lineage_listing_graph_summaries(self, lineage_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
        ids = list(dict.fromkeys(lineage_ids))
        result = {lid: {'lineage_node_ids': [], 'node_count': 0, 'edge_count': 0, 'active_seed_count': 0, 'statuses': {}} for lid in ids}
        if not ids:
            return result
        with self.database.read_transaction() as connection:
            rows = connection.execute("""SELECT lineage_id,node_id FROM lineage_nodes WHERE lineage_id=ANY(%s) AND node_type='resident'
                AND EXISTS (SELECT 1 FROM residents r WHERE r.resident_id=node_id AND r.deleted_at IS NULL)
                UNION SELECT lineage_id,node_id FROM lineage_node_references WHERE lineage_id=ANY(%s) ORDER BY lineage_id,node_id""", (ids,ids)).fetchall()
            for row in rows:
                result[row['lineage_id']]['lineage_node_ids'].append(row['node_id'])
            for lid, summary in result.items():
                summary['node_count'] = len(summary['lineage_node_ids'])
                summary['statuses'] = {'idle': summary['node_count']} if summary['node_count'] else {}
            for row in connection.execute("""SELECT e.lineage_id,COUNT(*) n FROM lineage_edges e
                WHERE e.deleted_at IS NULL AND e.lineage_id=ANY(%s) AND e.edge_type=ANY(%s) GROUP BY e.lineage_id""", (ids,list(graph_storage.RELATION_TYPES))).fetchall():
                result[row['lineage_id']]['edge_count'] = int(row['n'])
            for row in connection.execute("""SELECT n.lineage_id,COUNT(*) n FROM lineage_nodes n JOIN lineage_components c USING(node_id)
                WHERE n.deleted_at IS NULL AND n.lineage_id=ANY(%s) AND n.node_type='seed' AND COALESCE(c.config_json::jsonb->>'status','') IN ('active','','0') GROUP BY n.lineage_id""", (ids,)).fetchall():
                result[row['lineage_id']]['active_seed_count'] = int(row['n'])
        return result

    def all_lineages(self) -> list[dict[str, Any]]:
        with self.database.read_transaction() as connection:
            return self._definitions_from_connection(connection)

    @staticmethod
    def _lineage_definition_from_connection(connection: Any, lineage_id: str) -> dict[str, Any]:
        values = LineageDataStore._definitions_from_connection(connection, [lineage_id])
        if not values:
            raise KeyError(lineage_id)
        return values[0]


    @staticmethod
    def _hierarchy_graph_members(connection: Any, definition: Mapping[str, Any]) -> list[dict[str, Any]]:
        from .principal_hierarchy import project_leaders

        lineage_id = str(definition["lineage_id"])
        members = connection.execute(LineageDataStore._resident_select(
            'WHERE r.lineage_id=%s ORDER BY r.created_at, r.resident_id'
        ), (lineage_id,)).fetchall()
        result = [LineageDataStore._resident_from_row(row) for row in members]
        if (definition.get("principal_hierarchy") or {}).get("level") == "project":
            if definition.get("leader_resident_id") not in {row["resident_id"] for row in result}:
                raise ValueError("Project leader must belong to its own principal graph")
            children = lineage_metadata_storage.read_many(connection)
            leaders = list(project_leaders(definition, children))
            rows = {
                str(row["resident_id"]): row
                for row in connection.execute(LineageDataStore._resident_select(
                    'WHERE r.resident_id = ANY(%s)'
                ), (leaders,)).fetchall()
            } if leaders else {}
            result.extend(
                LineageDataStore._resident_from_row(rows[resident_id])
                for resident_id in leaders
                if resident_id in rows
            )
        return result

    @staticmethod
    def _materialize_lineage_members(connection: Any, lineage_id: str) -> None:
        """Publish membership and its graph projection in the same transaction."""
        from .lineage_member_projection import project_resident_nodes

        # A write of the Project graph itself merges with this one at commit.
        connection.execute('SELECT 1 FROM lineages WHERE lineage_id=%s FOR NO KEY UPDATE', (lineage_id,))
        definition = LineageDataStore._lineage_definition_from_connection(connection, lineage_id)
        old_ids = {str(node["id"]) for node in definition["nodes"]}
        members = LineageDataStore._hierarchy_graph_members(connection, definition)
        if old_ids == {str(row["resident_id"]) for row in members}:
            if any("x" not in node or "y" not in node for node in definition["nodes"]):
                project_resident_nodes(definition, members)
                LineageDataStore._initialize_layout_positions(connection, lineage_id, definition)
            return
        if (definition.get("principal_hierarchy") or {}).get("level") == "project":
            removed = old_ids - {str(row["resident_id"]) for row in members}
            def references(value: object) -> bool:
                if isinstance(value, str):
                    return value in removed
                if isinstance(value, dict):
                    return any(references(item) for item in value.values())
                if isinstance(value, list):
                    return any(references(item) for item in value)
                return False
            if removed and any(references(definition.get(field)) for field in ("edges", "program")):
                raise ValueError("Project graph references a departing Lineage leader; detach its wires before changing hierarchy")
            definition["nodes"] = [node for node in definition["nodes"] if node["id"] not in removed]
        project_resident_nodes(definition, members)
        definition["revision"] += 1
        definition["graph_updated_at"] = definition["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        LineageDataStore._write_definition(connection, lineage_id, definition)

    @staticmethod
    def _synchronize_project_graphs(connection: Any, project_ids: Iterable[str]) -> None:
        """Re-project the principal graphs of the given Projects in this transaction.

        A Project's principal graph shows its own members and the Leaders of
        the Project's ordinary Lineages.  Writers pass the Projects whose
        members or Leaders they may have changed; every other Project graph
        is unaffected and is not read.
        """
        projects = {str(item) for item in project_ids if str(item or "")}
        if not projects:
            return
        pending = _HIERARCHY_SYNC_PENDING.get()
        if pending is not None:
            pending.update(projects)
            return
        seen: set[str] = set()
        for row in connection.execute(
            "SELECT lineage_id, project_id, leader_resident_id FROM lineages "
            f"WHERE {PROJECT_GRAPH_FILTER} AND project_id = ANY(%s) ORDER BY lineage_id",
            (sorted(projects),),
        ).fetchall():
            if row["project_id"] in seen:
                raise ValueError("A Project can have only one principal graph")
            seen.add(row["project_id"])
            leader_exists = connection.execute(
                'SELECT 1 FROM residents WHERE resident_id=%s AND deleted_at IS NULL', (row["leader_resident_id"],),
            ).fetchone()
            if not leader_exists:
                continue  # Registration commits the new leader immediately after its graph.
            LineageDataStore._materialize_lineage_members(connection, row["lineage_id"])

    @staticmethod
    def _lineage_projects(connection: Any, lineage_ids: Iterable[str]) -> set[str]:
        requested = sorted({str(item) for item in lineage_ids if str(item or "")})
        if not requested:
            return set()
        return {
            str(row["project_id"])
            for row in connection.execute(
                'SELECT project_id FROM lineages WHERE lineage_id = ANY(%s)', (requested,),
            ).fetchall()
        }

    @staticmethod
    def _hierarchy_projects(previous: Any | None, value: Mapping[str, Any]) -> set[str]:
        """Projects whose principal graph a Lineage definition write can change.

        A write changes a Project graph when it writes the graph itself or
        changes what makes the Lineage contribute to one: its Project, Leader,
        membership or hierarchy level, including creation.
        """
        after = (
            str(value.get("project_id") or ""),
            str(value.get("leader_resident_id") or ""),
            str(value.get("lineage_membership") or ""),
            str((value.get("principal_hierarchy") or {}).get("level") or ""),
        )
        before = None
        if previous is not None:
            hierarchy = previous.get("principal_hierarchy")
            before = (
                str(previous["project_id"]),
                str(previous["leader_resident_id"]),
                str(previous["lineage_membership"]),
                str((hierarchy if isinstance(hierarchy, dict) else {}).get("level") or ""),
            )
        if before == after and after[3] != "project":
            return set()
        return {after[0], *(() if before is None else (before[0],))}

    def lineage_definition(self, lineage_id: str) -> dict[str, Any]:
        with self.database.read() as connection:
            return self._lineage_definition_from_connection(connection, lineage_id)

    def lock_lineage_definition(self, lineage_id: str) -> dict[str, Any] | None:
        """The committed definition, its Lineage row held until this transaction ends.

        Called inside the transaction that commits a definition write, so
        writes on one Lineage merge one after another for the duration of
        their commit only; None when the Lineage does not exist yet.
        """

        def read(connection: Any) -> dict[str, Any] | None:
            if connection.execute(
                'SELECT 1 FROM lineages WHERE lineage_id=%s FOR NO KEY UPDATE', (lineage_id,),
            ).fetchone() is None:
                return None
            return self._lineage_definition_from_connection(connection, lineage_id)

        return self.database.write(read)

    @request_cached
    def lineage_id_for_object(self, object_id: str) -> str:
        """Lineage that currently holds a graph object (component, wire, edge or block)."""

        with self.database.read() as connection:
            row = connection.execute(
                "SELECT n.lineage_id FROM lineage_nodes n LEFT JOIN residents r ON r.resident_id=n.node_id WHERE n.node_id=%s AND n.deleted_at IS NULL AND (n.node_type<>'resident' OR r.deleted_at IS NULL) UNION ALL SELECT lineage_id FROM lineage_edges WHERE edge_id=%s AND deleted_at IS NULL",
                (object_id,object_id),
            ).fetchone()
        return str(row["lineage_id"]) if row is not None else ""

    def lineage_revision(self, lineage_id: str) -> int:
        """Current definition version of one Lineage (its revision)."""

        with self.database.read() as connection:
            row = connection.execute(
                'SELECT revision FROM lineages WHERE lineage_id=%s AND deleted_at IS NULL', (lineage_id,),
            ).fetchone()
        if row is None:
            raise KeyError(lineage_id)
        return int(row["revision"])



    @request_cached
    def lineage_access_records(self, lineage_ids: Iterable[str] | None = None) -> list[dict[str, Any]]:
        with self.database.read() as connection:
            records = lineage_metadata_storage.read_many(connection, list(lineage_ids) if lineage_ids is not None else None)
            ids = [row['lineage_id'] for row in records]
            members = defaultdict(list)
            for row in connection.execute("""
                SELECT n.lineage_id,n.node_id FROM lineage_nodes n
                JOIN residents r ON r.resident_id=n.node_id AND r.deleted_at IS NULL
                WHERE n.node_type='resident' AND n.lineage_id=ANY(%s)
                UNION SELECT ref.lineage_id,ref.node_id FROM lineage_node_references ref
                JOIN residents r ON r.resident_id=ref.node_id AND r.deleted_at IS NULL
                WHERE ref.lineage_id=ANY(%s)
            """, (ids,ids)).fetchall():
                members[row['lineage_id']].append(row['node_id'])
            return [{**row, 'lineage_node_ids': sorted(members[row['lineage_id']])} for row in records]


    def program_resident_observers(self, resident_id: str, event_type: str) -> dict[str, list[str]]:
        """Locate observers using graph relations, without loading definitions."""
        with self.database.read() as connection:
            rows = connection.execute("""
                SELECT n.lineage_id, n.node_id
                FROM lineage_nodes n JOIN lineage_components c ON c.node_id=n.node_id
                JOIN lineages l ON l.lineage_id=n.lineage_id AND l.deleted_at IS NULL
                WHERE n.node_type='event_source' AND n.deleted_at IS NULL
                  AND lower(c.config_json::jsonb->>'event_type')=%s
                  AND (c.config_json::jsonb->>'resident_scope'='system'
                    OR EXISTS (SELECT 1 FROM lineage_edges e WHERE e.lineage_id=n.lineage_id
                        AND e.edge_type='watch' AND e.deleted_at IS NULL
                        AND e.target_node_id=%s AND e.source_node_id=n.node_id)
                    OR (c.config_json::jsonb->>'resident_scope'='lineage' AND (
                        EXISTS (SELECT 1 FROM lineage_nodes m WHERE m.lineage_id=n.lineage_id
                            AND m.node_id=%s AND m.node_type='resident' AND m.deleted_at IS NULL)
                        OR EXISTS (SELECT 1 FROM lineage_node_references r
                            WHERE r.lineage_id=n.lineage_id AND r.node_id=%s))))
                ORDER BY n.lineage_id,n.node_id
            """, (str(event_type or '').strip().lower(), *([str(resident_id or '').strip().lower()]*3))).fetchall()
        result: dict[str, list[str]] = {}
        for row in rows:
            result.setdefault(row['lineage_id'], []).append(row['node_id'])
        return result

    @staticmethod
    def _write_definition(
        connection: Any,
        lineage_id: str,
        value: MutableMapping[str, Any],
    ) -> set[str]:
        """Write one Lineage definition; return the Projects whose graph it may change."""
        now = str(value.get("updated_at") or _now_iso_millis())
        LineageDataStore._lock_runtime(connection, lineage_id)
        LineageDataStore._appoint_missing_leader(connection, value)
        desired = _graph_objects(value)
        current = graph_storage.active_objects(connection, lineage_id)
        from management_portal.resources.retired_identity_boundary import require_live_identity
        require_live_identity(connection, lineage_id)
        previous_rows = lineage_metadata_storage.read_many(connection, [lineage_id], include_deleted=True)
        previous_row = previous_rows[0] if previous_rows else None
        lineage_metadata_storage.put(connection, lineage_id, dict(value))
        family_storage.store_program(connection, lineage_id, value, now)
        graph_storage.put(connection, lineage_id, value)
        graph_storage.retire_missing(connection, lineage_id, set(current), set(desired), now)
        definition_collections.put(connection, lineage_id, value)
        connection.execute('DELETE FROM lineage_layout_positions WHERE lineage_id=%s AND NOT (object_id=ANY(%s))', (lineage_id,list(desired)))
        connection.execute("DELETE FROM workspace_handoff_work WHERE lineage_id=%s AND NOT (edge_id=ANY(%s))", (lineage_id,list(desired)))
        LineageDataStore._initialize_layout_positions(connection, lineage_id, value)
        return LineageDataStore._hierarchy_projects(previous_row, value)

    @staticmethod
    def _appoint_missing_leader(connection: Any, value: MutableMapping[str, Any]) -> None:
        """A Lineage with a Resident always has a Leader (decision_6fc653d55c79).

        An empty seat goes to the earliest-created member; Function Block slot
        Residents only when no ordinary member exists, operator executors never.
        A Project-level Lineage keeps its own stricter Leader rule.
        """
        if str(value.get("leader_resident_id") or "") or (
            (value.get("principal_hierarchy") or {}).get("level") == "project"
        ):
            return
        nodes = {
            str(node.get("id") or ""): node
            for node in value.get("nodes") or []
            if isinstance(node, Mapping)
        }
        if not nodes:
            return
        # The nodes being written are the members: a Resident moving in is
        # still registered in its old Lineage, and one being created has no
        # record yet; it is the newest member.
        rows = connection.execute(
            'SELECT resident_id, deleted_at FROM residents WHERE resident_id = ANY(%s) '
            'ORDER BY created_at, resident_id',
            (sorted(nodes),),
        ).fetchall()
        registered = [str(row["resident_id"]) for row in rows]
        deleted = {str(row["resident_id"]) for row in rows if row["deleted_at"]}
        members = [
            resident_id
            for resident_id in [*registered, *(item for item in nodes if item not in registered)]
            if resident_id not in deleted
            and not isinstance(nodes[resident_id].get("operator_executor"), dict)
        ]
        owned = {e["id"] for b in value.get("function_blocks") or [] for e in b.get("elements") or []}
        ordinary = [item for item in members if item not in owned]
        if ordinary or members:
            value["leader_resident_id"] = (ordinary or members)[0]

    @staticmethod
    def _initialize_layout_positions(connection: Any, lineage_id: str, value: Mapping[str, Any]) -> None:
        positions = [(lineage_id, object_id, x, y)
                     for object_id, (_kind, x, y) in _layout_positions(value).items()]
        if positions:
            connection.executemany(
                'INSERT INTO lineage_layout_positions(lineage_id,object_id,x,y) VALUES (%s,%s,%s,%s) '
                'ON CONFLICT(lineage_id,object_id) DO NOTHING', positions,
            )

    def upsert_layout_positions(
        self,
        lineage_id: str,
        positions: Iterable[Mapping[str, Any]],
        *,
        updated_at: str,
    ) -> dict[str, Any]:
        requested = [dict(item) for item in positions]

        def write(connection: Any) -> dict[str, Any]:
            # Definition commits lock the Lineage before its layout rows.
            # Keep that order and serialize the layout revision read/increment.
            lineage = connection.execute(
                'SELECT revision, layout_revision, layout_updated_at FROM lineages WHERE lineage_id=%s FOR NO KEY UPDATE',
                (lineage_id,),
            ).fetchone()
            if lineage is None:
                raise KeyError(lineage_id)
            active = graph_storage.active_objects(connection, lineage_id)
            applied: list[dict[str, Any]] = []
            missing: list[str] = []
            for item in requested:
                object_id = str(item["object_id"])
                kind = active.get(object_id)
                if kind is None:
                    missing.append(object_id)
                    continue
                x, y = float(item["x"]), float(item["y"])
                if kind == "function_block":
                    connection.execute(
                        'UPDATE function_blocks SET x=%s,y=%s WHERE function_block_id=%s AND lineage_id=%s',
                        (x,y,object_id,lineage_id),
                    )
                    applied.append({"object_id":object_id,"object_kind":kind,"x":x,"y":y})
                    continue
                connection.execute(
                    'INSERT INTO lineage_layout_positions(lineage_id,object_id,x,y) VALUES (%s,%s,%s,%s) ON CONFLICT(lineage_id,object_id) DO UPDATE SET x=excluded.x,y=excluded.y WHERE (lineage_layout_positions.x,lineage_layout_positions.y) IS DISTINCT FROM (excluded.x,excluded.y)',
                    (lineage_id, object_id, x, y),
                )
                applied.append({
                    "object_id": object_id,
                    "object_kind": kind,
                    "x": x,
                    "y": y,
                })
            layout_revision = int(lineage["layout_revision"])
            if applied:
                layout_revision += 1
                connection.execute(
                    'UPDATE lineages SET layout_revision=%s, layout_updated_at=%s WHERE lineage_id=%s',
                    (layout_revision, updated_at, lineage_id),
                )
            return {
                "lineage_id": lineage_id,
                "revision": int(lineage["revision"]),
                "layout_revision": layout_revision,
                "layout_updated_at": (
                    updated_at if applied else (iso_time(lineage["layout_updated_at"]) or "")
                ),
                "applied": applied,
                "missing_object_ids": missing,
            }

        return self.database.write(write)

    def put_lineage_definition(
        self,
        lineage_id: str,
        value: MutableMapping[str, Any],
        initial_runtime: Mapping[str, Any],
    ) -> None:
        def write(connection: Any) -> None:
            deleting = connection.execute(
                "SELECT 1 FROM lineage_deletion_operations WHERE lineage_id=%s",
                (lineage_id,),
            ).fetchone()
            if deleting is not None:
                raise RuntimeCASConflict("Lineage deletion is in progress")
            self._synchronize_project_graphs(
                connection, self._write_definition(connection, lineage_id, value),
            )
            exists = connection.execute(
                'SELECT 1 FROM lineages WHERE lineage_id=%s AND runtime_revision IS NOT NULL', (lineage_id,)
            ).fetchone()
            if exists is None:
                self._write_runtime(connection, lineage_id, initial_runtime)

        self.database.write(write)

    def remove_lineage(self, lineage_id: str, *, deleted_by: str) -> None:
        def write(connection: Any) -> None:
            self._lock_runtime(connection, lineage_id)
            from .lineage_definition_versions import check_definition_version
            check_definition_version(connection, lineage_id)
            projects = self._lineage_projects(connection, [lineage_id])
            deleted_at = _now_iso_millis()
            connection.execute('UPDATE lineages SET deleted_at=%s,deleted_by=%s WHERE lineage_id=%s AND deleted_at IS NULL', (deleted_at,deleted_by,lineage_id))
            # The Lineage's Families go with it, as when their Function Blocks are removed.
            family_storage.store_program(connection, lineage_id, {"function_blocks": []}, deleted_at)
            for path in connection.execute(
                "SELECT trigger_path_id FROM trigger_paths WHERE lineage_id=%s AND status='active' ORDER BY trigger_path_id",
                (lineage_id,),
            ).fetchall():
                trigger_path_storage.cancel(connection, path["trigger_path_id"], at=deleted_at)
            for table in (*program_work_storage.COLUMNS, *wire_delivery_budget.COLUMNS, "lineage_shared_state", "lineage_drafts"):
                connection.execute(f'DELETE FROM {table} WHERE lineage_id=%s', (lineage_id,))
            self.lineage_deletion_operations.delete(lineage_id)
            connection.execute('DELETE FROM project_lineage_entries WHERE lineage_id=%s', (lineage_id,))
            self._synchronize_project_graphs(connection, projects)
        self.database.write(write)

    def lineage_ids_for_resident(self, resident_id: str) -> list[str]:
        with self.database.read() as connection:
            rows = connection.execute(
                '\n                SELECT n.lineage_id FROM residents r JOIN lineage_nodes n ON n.node_id=r.resident_id WHERE r.resident_id=%s AND r.deleted_at IS NULL\n                ORDER BY lineage_id\n                ',
                (resident_id,),
            ).fetchall()
        return [str(row["lineage_id"]) for row in rows]


    def _lineage_runtime_from_connection(self, connection, lineage_id):
        header = connection.execute('SELECT runtime_revision,runtime_updated_at FROM lineages WHERE lineage_id=%s', (lineage_id,)).fetchone()
        if header is None:
            return {}
        return {'schema': lineage_state_domains.RUNTIME_STATE_SCHEMA, 'lineage_id': lineage_id,
                'runtime_revision': int(header['runtime_revision'] or 0), 'updated_at': iso_time(header['runtime_updated_at']) or '',
                'nodes': relation_work_storage.resident_observations(connection, lineage_id),
                'edges': relation_work_storage.read(connection, lineage_id)}


    def lineage_snapshot(
        self,
        lineage_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Read definition and runtime from one PostgreSQL snapshot."""

        with self.database.snapshot() as connection:
            definition = self._lineage_definition_from_connection(
                connection,
                lineage_id,
            )
            runtime = self._lineage_runtime_from_connection(
                connection,
                lineage_id,
            )
        return definition, runtime

    def human_notification_records(
        self, lineage_ids: Iterable[str] | None = None,
    ) -> list[dict[str, Any]]:
        requested = None if lineage_ids is None else list(dict.fromkeys(lineage_ids))
        if requested == []:
            return []
        batches = [None] if requested is None else [
            requested[offset:offset + 400] for offset in range(0, len(requested), 400)
        ]
        records = []
        with self.database.read() as connection:
            for batch in batches:
                restriction = "" if batch is None else (
                    " AND notice.lineage_id IN (" + ",".join("%s" for _ in batch) + ")"
                )
                rows = connection.execute(
                    "SELECT notice.lineage_id, lineage.title, notice.notifications_json AS state_json "
                    "FROM lineage_human_notifications AS notice "
                    "JOIN lineages AS lineage ON lineage.lineage_id = notice.lineage_id "
                    "WHERE TRUE"
                    + restriction + " ORDER BY notice.lineage_id", batch or [],
                ).fetchall()
                for row in rows:
                    records.append({
                        "lineage_id": str(row["lineage_id"]),
                        "title": str(row["title"]),
                        "runtime": _object(row["state_json"], str(row["lineage_id"])),
                    })
            if requested is None:
                row = connection.execute(
                    "SELECT notifications_json FROM portal_human_notifications WHERE singleton=1"
                ).fetchone()
                if row:
                    records.append({"lineage_id": "", "title": "",
                                    "runtime": _object(row["notifications_json"], "portal_human_notifications")})
        return records

    @contextmanager
    def portal_human_notifications(self) -> Iterator[dict[str, Any]]:
        """The one record of human notifications that belong to no Lineage."""
        with self.database.transaction() as connection:
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(current_schema() || ':portal_human_notifications', 0))"
            )
            row = connection.execute(
                "SELECT notifications_json FROM portal_human_notifications WHERE singleton=1"
            ).fetchone()
            original = _object(row["notifications_json"], "portal_human_notifications") if row else {}
            state = deepcopy(original)
            yield state
            if state != original:
                connection.execute(
                    "INSERT INTO portal_human_notifications(singleton,notifications_json) VALUES (1,%s) "
                    "ON CONFLICT(singleton) DO UPDATE SET notifications_json=excluded.notifications_json",
                    (json_text(state, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False),),
                )




    def enqueue_effect_cleanup(self, lineage_id: str, activation_key: str, instructions: dict[str, list[str]]) -> None:
        if not lineage_id or not activation_key or not isinstance(instructions, dict):
            raise ValueError("Effect cleanup requires its activation identity and caller mapping")
        for caller, ids in instructions.items():
            if not isinstance(caller, str) or not caller or not isinstance(ids, list) or not all(isinstance(i, str) and i for i in ids):
                raise ValueError("Effect cleanup instructions must map callers to instruction IDs")
        def enqueue(connection: Any) -> None:
            connection.execute("INSERT INTO effect_recovery_cleanup(lineage_id,activation_key,instructions_json,created_at) "
                               "VALUES (%s,%s,'{}',%s) ON CONFLICT DO NOTHING",
                               (lineage_id, activation_key, datetime.now(timezone.utc).isoformat()))
            row = connection.execute("SELECT instructions_json FROM effect_recovery_cleanup "
                                     "WHERE lineage_id=%s AND activation_key=%s FOR UPDATE", (lineage_id, activation_key)).fetchone()
            stored = json_value(row["instructions_json"])
            merged = deepcopy(stored)
            for caller, ids in instructions.items():
                merged[caller] = sorted(set(merged.get(caller, [])) | set(ids))
            if merged != stored:
                value = _json(merged)
                connection.execute("UPDATE effect_recovery_cleanup SET instructions_json=%s WHERE lineage_id=%s AND activation_key=%s",
                                   (value, lineage_id, activation_key))
        self.database.write(enqueue)

    @staticmethod
    def _effect_cleanup_row(row: Any) -> dict[str, Any]:
        return {"lineage_id": row["lineage_id"], "activation_key": row["activation_key"],
                "instructions": json_value(row["instructions_json"]), "created_at": iso_time(row["created_at"])}

    def effect_cleanup_task(self, lineage_id: str, activation_key: str) -> dict[str, Any] | None:
        with self.database.read() as connection:
            row = connection.execute("SELECT * FROM effect_recovery_cleanup WHERE lineage_id=%s AND activation_key=%s",
                                     (lineage_id, activation_key)).fetchone()
        return self._effect_cleanup_row(row) if row else None

    def effect_cleanup_tasks(self, *, lineage_id: str | None = None, limit: int = 100,
                             after_key: tuple[str, str, str] | None = None) -> list[dict[str, Any]]:
        predicates, values = [], []
        if lineage_id is not None:
            predicates.append("lineage_id=%s")
            values.append(lineage_id)
        if after_key is not None:
            if len(after_key) != 3:
                raise ValueError("Effect cleanup cursor requires created_at, Lineage and activation")
            predicates.append("(created_at,lineage_id,activation_key)>(%s,%s,%s)")
            values.extend(after_key)
        with self.database.read() as connection:
            rows = connection.execute("SELECT * FROM effect_recovery_cleanup " +
                                      ("WHERE " + " AND ".join(predicates) + " " if predicates else "") +
                                      "ORDER BY created_at,lineage_id,activation_key LIMIT %s",
                                      (*values, max(1, min(int(limit), 1000))),).fetchall()
        return [self._effect_cleanup_row(row) for row in rows]

    def complete_effect_cleanup(self, lineage_id: str, activation_key: str, *, expected_instructions: dict[str, list[str]]) -> bool:
        expected = _json(expected_instructions)
        return bool(self.database.write(lambda connection: connection.execute(
            "DELETE FROM effect_recovery_cleanup WHERE lineage_id=%s AND activation_key=%s AND instructions_json=%s",
            (lineage_id, activation_key, expected),
        ).rowcount))











    @staticmethod
    def _write_runtime_header(connection, lineage_id, value):
        unknown = set(value) - RUNTIME_STRUCTURAL_FIELDS - RUNTIME_COLUMN_FIELDS
        if unknown:
            raise ValueError('Unknown runtime projection fields: ' + ', '.join(sorted(unknown)))
        connection.execute('UPDATE lineages SET runtime_revision=%s,runtime_updated_at=%s WHERE lineage_id=%s',
                           (int(value.get('runtime_revision') or 0), instant(value.get('updated_at')), lineage_id))

    @staticmethod
    def _replace_runtime_objects(connection, lineage_id, value):
        LineageDataStore._lock_runtime(connection, lineage_id)
        active = graph_storage.active_objects(connection, lineage_id)
        after = {key: state for key,state in (value.get('edges') or {}).items()
                 if key in active or isinstance(state.get('absorption'), dict)}
        relation_work_storage.put(connection, lineage_id, relation_work_storage.read(connection,lineage_id), after)

    @classmethod
    def _write_runtime(
        cls,
        connection: Any,
        lineage_id: str,
        value: Mapping[str, Any],
    ) -> None:
        cls._write_runtime_header(connection, lineage_id, value)
        cls._replace_runtime_objects(connection, lineage_id, value)

    def put_lineage_runtime(self, lineage_id: str, value: Mapping[str, Any]) -> None:
        self.database.write(
            lambda connection: self._write_runtime(connection, lineage_id, value)
        )

    @staticmethod
    def _lock_runtime(connection, lineage_id):
        row = connection.execute('SELECT deleted_at FROM lineages WHERE lineage_id=%s FOR NO KEY UPDATE', (lineage_id,)).fetchone()
        if row is not None and row['deleted_at'] is not None:
            raise RuntimeCASConflict('Lineage has been deleted')

    def put_lineage_runtime_changes(
        self,
        lineage_id: str,
        base: Mapping[str, Any],
        candidate: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Apply the runtime objects a definition write changed onto the committed runtime.

        The write's other runtime objects keep their committed state; an
        object whose graph node no longer exists is not written.
        """

        base_objects = _runtime_objects(base)
        candidate_objects = _runtime_objects(candidate)
        changed = {
            key
            for key in set(base_objects) | set(candidate_objects)
            if base_objects.get(key) != candidate_objects.get(key)
        }

        def write(connection: Any) -> dict[str, Any]:
            self._lock_runtime(connection, lineage_id)
            current = self._lineage_runtime_from_connection(connection, lineage_id)
            if not changed:
                return current
            merged_objects = _runtime_objects(current)
            for key in changed:
                if key in candidate_objects:
                    merged_objects[key] = deepcopy(candidate_objects[key])
                else:
                    merged_objects.pop(key, None)
            merged = _runtime_from_objects(current, merged_objects)
            merged["schema"] = str(candidate.get("schema") or current.get("schema") or "")
            merged["lineage_id"] = lineage_id
            merged["runtime_revision"] = int(current.get("runtime_revision") or 0) + 1
            merged["updated_at"] = str(candidate.get("updated_at") or current.get("updated_at") or "")
            self._write_runtime(connection, lineage_id, merged)
            return merged

        return self.database.write(write)

    def put_lineage_runtime_cas(self, lineage_id, expected, candidate):
        before = expected.get('edges') or {}
        after = candidate.get('edges') or {}
        changed = {key for key in set(before) | set(after) if before.get(key) != after.get(key)}
        def write(connection):
            self._lock_runtime(connection, lineage_id)
            current = self._lineage_runtime_from_connection(connection, lineage_id)
            if not changed:
                return current
            edges = current['edges']
            active = graph_storage.active_objects(connection, lineage_id)
            for key in changed:
                if key in after and key not in active and not isinstance(after[key].get('absorption'),dict):
                    raise RuntimeCASConflict('graph object is deleted or moved: edge/' + key)
                if edges.get(key) != before.get(key) and not (key in active and key not in edges):
                    raise RuntimeCASConflict('runtime object changed: edge/' + key)
            merged = deepcopy(edges)
            for key in changed:
                if key in after:
                    merged[key] = deepcopy(after[key])
                else:
                    merged.pop(key,None)
            current.update(edges=merged, runtime_revision=current['runtime_revision']+1, updated_at=str(candidate.get('updated_at') or ''))
            relation_work_storage.put(connection,lineage_id,edges,merged)
            self._write_runtime_header(connection,lineage_id,current)
            return current
        return self.database.write(write)

    @staticmethod
    def _resident_from_row(row: Any) -> dict[str, Any]:
        row = dict(row)
        from .resident_catalog import RESIDENT_RECORD_SCHEMA
        value: dict[str, Any] = {"schema": RESIDENT_RECORD_SCHEMA, "folder_schema": "lineage-folder-v3"}
        for fields in resident_storage.DOMAIN_TABLES.values():
            value.update(resident_storage.decode(row, fields))
        value.update(lineage_id=str(row.get('lineage_id') or ''),
                     project_id=str(row.get('lineage_project_id') or ''),
                     lineage_title=str(row.get('lineage_title') or ''),
                     lineage_project_id=str(row.get('lineage_project_id') or ''))
        if value.get('deleted_at'):
            value['project_id'] = str(value.get('historical_project_id') or value['project_id'])
            if not row.get('lineage_id'):
                value.pop('lineage_id', None)
        if "principal_hierarchy" in value:
            from .principal_hierarchy import level_for_resident
            home = {"leader_resident_id": row.get("home_leader"), "lineage_membership": row.get("home_membership"),
                    "principal_hierarchy": {"level": "project" if row.get("home_is_project") else "lineage"}}
            value["principal_hierarchy"]["level"] = level_for_resident(value, home)
        return value

    @staticmethod
    def _resident_select(where: str = "", *, include_deleted: bool = False) -> str:
        selections = ['r.*', 'l.title AS lineage_title', 'l.project_id AS lineage_project_id', 'l.leader_resident_id AS home_leader', 'l.lineage_membership AS home_membership', 'EXISTS(SELECT 1 FROM project_lineage_entries p WHERE p.lineage_id=l.lineage_id) AS home_is_project']
        joins = []
        for index, (table, fields) in enumerate(resident_storage.DOMAIN_TABLES.items()):
            if table == 'residents':
                continue
            alias = f'd{index}'
            selections.extend(f'{alias}.{column}' for column in resident_storage.columns(fields))
            joins.append(f'LEFT JOIN {table} AS {alias} USING(resident_id)')
        if not include_deleted:
            where = 'WHERE r.deleted_at IS NULL ' + (
                'AND ' + where[6:] if where.startswith('WHERE ') else where
            )
        membership_join = 'LEFT JOIN' if include_deleted else 'JOIN'
        return ('SELECT ' + ', '.join(selections) + f' FROM (SELECT resident.*,node.lineage_id FROM residents resident {membership_join} lineage_nodes node ON node.node_id=resident.resident_id) AS r '
                + 'LEFT JOIN lineages AS l USING(lineage_id) ' + ' '.join(joins) + ' ' + where)

    @classmethod
    def _residents_from_rows(cls, connection: Any, rows: Iterable[Any]) -> list[dict[str, Any]]:
        from . import resident_workspace_storage as workspaces, resource_client, lineage_storage
        records = [cls._resident_from_row(row) for row in rows]
        if not records:
            return []
        # Historical identity is valid even when its Lineage/folder no longer
        # exists. Never project or recreate runtime Workspaces for it.
        active = [record for record in records if not record.get('deleted_at')]
        ids = [record['resident_id'] for record in active]
        all_ids = [record['resident_id'] for record in records]
        bindings = workspaces.bindings_for_residents(connection, all_ids)
        paths = resource_client.workspace_paths_many(
            [record['owned_workspace_id'] for record in active] +
            [binding['workspace_id'] for rid in ids for binding in bindings.get(rid, [])], connection=connection,
        )
        folders = {lineage_id: str(lineage_storage.canonical_lineage_folder(lineage_id).resolve(strict=False))
                   for lineage_id in dict.fromkeys(record['lineage_id'] for record in active)}
        projected = {record['resident_id']: record for record in workspaces.project_records(active, bindings, paths, folders)}
        records = [projected.get(record['resident_id'], record) for record in records]
        roles = family_storage.resident_roles(connection, all_ids)
        for record in records:
            rid = record['resident_id']
            record['family'] = roles.get(rid, {})
            if record.get('deleted_at'):
                record['workspace_bindings'] = bindings.get(rid, [])
        return records

    def all_residents(self) -> list[dict[str, Any]]:
        with self.database.snapshot() as connection:
            rows = connection.execute(
                self._resident_select("ORDER BY r.resident_id")
            ).fetchall()
            return self._residents_from_rows(connection, rows)

    @request_cached
    def resident_record(self, resident_id: str, *, include_deleted: bool = False) -> dict[str, Any]:
        with self.database.snapshot() as connection:
            row = connection.execute(
                self._resident_select('WHERE r.resident_id=%s', include_deleted=include_deleted), (resident_id,)
            ).fetchone()
            if row is None:
                raise KeyError(resident_id)
            return self._residents_from_rows(connection, [row])[0]

    def resident_usage_memberships(self, resident_ids: Iterable[str]) -> list[dict[str, str]]:
        """Return only resident_id/lineage_id for the requested live subset.

        Preserve request order and omit repeated IDs, without loading Resident
        domain subrecords, Workspace projections, graph or permission state.
        """
        requested = list(dict.fromkeys(str(rid) for rid in resident_ids if str(rid)))
        if not requested:
            return []
        values: dict[str, dict[str, str]] = {}
        with self.database.snapshot() as connection:
            for start in range(0, len(requested), 500):
                for row in connection.execute(
                    'SELECT r.resident_id,n.lineage_id FROM residents r JOIN lineage_nodes n ON n.node_id=r.resident_id '
                    'WHERE r.resident_id=ANY(%s) AND r.deleted_at IS NULL',
                    (requested[start:start + 500],),
                ).fetchall():
                    values[row['resident_id']] = {'resident_id': row['resident_id'], 'lineage_id': row['lineage_id']}
        return [values[rid] for rid in requested if rid in values]

    def resident_identity_records(self, resident_ids: Iterable[str]) -> list[dict[str, Any]]:
        """Current identity and Family roles for authorization and activity reads.

        Workspace paths and Watchboards belong to their own views. Reading a
        status must not resolve every member's filesystem bindings.
        """
        requested = list(dict.fromkeys(str(item) for item in resident_ids if str(item)))
        if not requested:
            return []
        with self.database.snapshot() as connection:
            rows = connection.execute(
                self._resident_select("WHERE r.resident_id = ANY(%s)"), (requested,),
            ).fetchall()
            records = {row['resident_id']: self._resident_from_row(row) for row in rows}
            roles = family_storage.resident_roles(connection, list(records))
        return [{**records[rid], 'family': roles.get(rid, {})} for rid in requested if rid in records]

    def resident_work_ids(self) -> list[str]:
        """Select identities with a turn or queued input without Workspace reads."""
        with self.database.read() as connection:
            rows = connection.execute(
                "SELECT resident_id FROM residents WHERE deleted_at IS NULL AND resident_id IN ("
                "SELECT resident_id FROM resident_execution_work UNION "
                "SELECT resident_id FROM resident_mailbox WHERE place='queue') ORDER BY resident_id"
            ).fetchall()
        return [row['resident_id'] for row in rows]

    def residents_by_id(self, resident_ids: Iterable[str]) -> list[dict[str, Any]]:
        requested = list(dict.fromkeys(str(item) for item in resident_ids if str(item)))
        if not requested:
            return []
        placeholders = ",".join("%s" for _ in requested)
        with self.database.snapshot() as connection:
            rows = connection.execute(
                self._resident_select(f"WHERE r.resident_id IN ({placeholders})"),
                requested,
            ).fetchall()
            values = {row["resident_id"]: row for row in self._residents_from_rows(connection, rows)}
        return [values[item] for item in requested if item in values]

    def live_resident_ids(self, resident_ids: Iterable[str]) -> set[str]:
        """Residents not deleted that are still a node of a Lineage that is not deleted."""
        requested = list(dict.fromkeys(str(item) for item in resident_ids if str(item)))
        if not requested:
            return set()
        with self.database.read() as connection:
            rows = connection.execute(
                "SELECT r.resident_id FROM residents r "
                "JOIN lineage_nodes n ON n.node_id=r.resident_id AND n.node_type='resident' AND n.deleted_at IS NULL "
                "JOIN lineages l ON l.lineage_id=n.lineage_id AND l.deleted_at IS NULL "
                "WHERE r.resident_id = ANY(%s) AND r.deleted_at IS NULL",
                (requested,),
            ).fetchall()
        return {row['resident_id'] for row in rows}

    def residents_for_lineage(self, lineage_id: str) -> list[dict[str, Any]]:
        with self.database.snapshot() as connection:
            rows = connection.execute(
                self._resident_select(
                    'WHERE r.lineage_id=%s ORDER BY r.resident_id'
                ),
                (lineage_id,),
            ).fetchall()
            return self._residents_from_rows(connection, rows)


    def put_resident_record(
        self, resident_id: str, value: Mapping[str, Any], *, commit_configuration: bool = False, commit_intake: bool = False,
    ) -> None:
        from . import resident_workspace_storage, resource_client
        from .resident_settings_contract import split_runtime_settings
        record = deepcopy(dict(value))
        resident_storage.validate_record_fields(record)
        if record.get('resident_id') != resident_id:
            raise ValueError('Resident record identity must match its storage key')
        record['execution_settings'], record['harness_config'] = split_runtime_settings({
            **(record.get('execution_settings') or {}),
            'backend': record.get('backend') or '',
            'agent_harness': record.get('harness_config') or {},
        })
        configured_backend = record['execution_settings'].pop('backend', record.get('backend'))
        if configured_backend != record.get('backend'):
            raise ValueError('Resident backend and execution configuration differ')

        def write(connection: Any) -> None:
            from management_portal.resources.retired_identity_boundary import require_live_identity
            require_live_identity(connection, resident_id, parents=[record.get('lineage_id')])
            prior = connection.execute('SELECT n.lineage_id,r.backend,r.deleted_at,r.intake_open,r.intake_transition_id,r.registration_state FROM residents r LEFT JOIN lineage_nodes n ON n.node_id=r.resident_id WHERE r.resident_id=%s FOR UPDATE OF r', (resident_id,)).fetchone()
            if record.get('deleted_at') or (prior and prior['deleted_at'] is not None):
                raise ValueError('Deleted Resident identity cannot be updated or restored')
            for field in ('deleted_at', 'deleted_by', 'historical_project_id'):
                if record.get(field) is not None:
                    raise ValueError('Resident deletion facts require the deletion owner')
            if prior is not None and not commit_configuration:
                # Runtime/identity saves may carry an older configuration snapshot.
                # Only the configuration owner may replace it or switch backend.
                record['backend'] = prior['backend']
            if prior is not None and not commit_intake:
                for field in ('intake_open', 'intake_transition_id'):
                    record[field] = prior[field]
            if record.get('registration_state') is None:
                # Only registration starts a Resident before its resources are published.
                record['registration_state'] = prior['registration_state'] if prior is not None else 'ready'
            if record.get('registration_state') not in resident_storage.REGISTRATION_STATES:
                raise ValueError('Resident registration_state must be one of ' + ', '.join(resident_storage.REGISTRATION_STATES))
            if record['registration_state'] == 'resource_pending' and prior is not None and prior['registration_state'] == 'ready':
                record['registration_state'] = 'ready'
            if prior is None:
                # Registering a Resident publishes Lineage membership. Check
                # the caller's version in this transaction, before publishing
                # the child, rather than at the later Fork-edge commit. A
                # stale Fork must not leave a child which the same stale
                # precondition then prevents its compensation from deleting.
                from .lineage_definition_versions import check_definition_version
                check_definition_version(connection, str(record['lineage_id']))
            resource_client.ensure_workspaces(record, connection=connection)
            graph_storage.upsert(connection, 'lineage_nodes', {'node_id': resident_id}, {'lineage_id': record['lineage_id'], 'node_type': 'resident'})
            for table, fields in resident_storage.DOMAIN_TABLES.items():
                if table == 'resident_execution_configuration' and prior is not None and not commit_configuration:
                    continue
                resident_storage.put_fields(connection, table, fields, resident_id, record)
            resident_workspace_storage.replace_bindings(connection, resident_id, record['workspace_bindings'])
            lineage_id = str(record.get('lineage_id') or '')
            if lineage_id and (prior is None or prior['lineage_id'] != lineage_id):
                self._materialize_lineage_members(connection, lineage_id)
                self._synchronize_project_graphs(connection, self._lineage_projects(
                    connection, [lineage_id, *(() if prior is None else (prior['lineage_id'],))],
                ))
            if connection.execute(
                'SELECT 1 FROM resident_resource_projection_outbox WHERE resident_id=%s',
                (resident_id,),
            ).fetchone():
                # Session/configuration updates can advance identity metadata
                # while its publication is pending. Fence the old receipt and
                # coalesce the outstanding work with the committed identity.
                row = connection.execute(self._resident_select('WHERE r.resident_id=%s'), (resident_id,)).fetchone()
                self._enqueue_resident_resource_projection(
                    connection, resident_id, self._residents_from_rows(connection, [row])[0],
                )
        self.database.write(write)

    def read_resident_execution_settings(self, resident_id: str) -> dict[str, Any] | None:
        with self.database.read() as connection:
            return resident_storage.read_execution_settings(connection, resident_id)

    def write_resident_execution_settings(self, resident_id: str, settings: Mapping[str, Any]) -> dict[str, Any]:
        from .resident_settings_contract import split_runtime_settings
        execution, harness = split_runtime_settings(settings)
        def write(connection: Any) -> dict[str, Any]:
            row = connection.execute('SELECT backend FROM residents WHERE resident_id=%s AND deleted_at IS NULL FOR UPDATE', (resident_id,)).fetchone()
            if row is None:
                raise KeyError(resident_id)
            backend = execution.pop('backend', row['backend'])
            if backend != row['backend']:
                raise ValueError('Backend changes require the Resident provider transition')
            connection.execute('UPDATE resident_execution_configuration SET execution_settings_json=%s,harness_config_json=%s WHERE resident_id=%s',
                               (_json(execution), _json(harness), resident_id))
            return resident_storage.read_execution_settings(connection, resident_id)
        return self.database.write(write)

    @staticmethod
    def _enqueue_resident_resource_projection(
        connection: Any,
        resident_id: str,
        value: Mapping[str, Any],
    ) -> int:
        clean_id = str(resident_id or "").strip()
        payload = _json(dict(value))
        enqueued_at = str(value.get("updated_at") or value.get("deleted_at") or "")
        connection.execute(
            "\n            INSERT INTO resident_resource_projection_outbox(\n                resident_id, revision, payload_json, enqueued_at,\n                attempt_count, available_at, lease_token, lease_until,\n                last_error\n            ) VALUES (\n                %s, 1, %s, %s, 0, 'epoch', '', NULL, ''\n            )\n            ON CONFLICT(resident_id) DO UPDATE SET\n                revision=resident_resource_projection_outbox.revision + 1,\n                payload_json=excluded.payload_json,\n                enqueued_at=excluded.enqueued_at,\n                attempt_count=0,\n                available_at='epoch',\n                lease_token='',\n                lease_until=NULL,\n                last_error=''\n            ",
            (clean_id, payload, instant(enqueued_at)),
        )
        row = connection.execute(
            'SELECT revision FROM resident_resource_projection_outbox WHERE resident_id=%s',
            (clean_id,),
        ).fetchone()
        return int(row["revision"])

    def enqueue_resident_resource_projection(
        self,
        resident_id: str,
        value: Mapping[str, Any],
    ) -> int:
        def write(connection: Any) -> int:
            return self._enqueue_resident_resource_projection(
                connection,
                resident_id,
                value,
            )

        return self.database.write(write)

    def hold_lineage_timer_lease(self, holder: str, ttl_seconds: float) -> bool:
        """Acquire or renew the lease to run Lineage timers; True while ``holder`` owns it.

        Dev and Release share Lineage data, so one coordinator at a time wakes
        Lineages for due time sources.  A lease that is not renewed expires,
        and then another coordinator takes over.
        """

        clean_holder = str(holder or "").strip()
        if not clean_holder:
            return False
        now = time.time()

        def write(connection: Any) -> bool:
            row = connection.execute(
                "SELECT value FROM lineage_meta WHERE key=%s FOR UPDATE", (LINEAGE_TIMER_LEASE_KEY,),
            ).fetchone()
            try:
                current = json.loads(str(row["value"])) if row else {}
            except ValueError:
                current = {}
            owner = str(current.get("holder") or "")
            if owner and owner != clean_holder and float(current.get("expires_at") or 0) > now:
                return False
            connection.execute(
                "INSERT INTO lineage_meta(key, value) VALUES (%s, %s) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (LINEAGE_TIMER_LEASE_KEY, json.dumps({"holder": clean_holder, "expires_at": now + float(ttl_seconds)})),
            )
            return True

        return bool(self.database.write(write))

    def claim_resident_resource_projections(
        self,
        *,
        limit: int = 50,
        lease_seconds: float = 90.0,
        resident_id: str = "",
    ) -> list[dict[str, Any]]:
        now = time.time()
        lease_token = secrets.token_hex(16)
        batch_limit = max(1, min(int(limit), 500))
        target_resident_id = str(resident_id or "").strip()

        def write(connection: Any) -> list[dict[str, Any]]:
            selection = "resident_id = %s" if target_resident_id else "available_at <= %s"
            rows = connection.execute(
                "SELECT resident_id, revision, payload_json, attempt_count "
                "FROM resident_resource_projection_outbox "
                f"WHERE {selection} AND (lease_token='' OR lease_until <= %s) "
                "ORDER BY available_at, resident_id LIMIT %s",
                (target_resident_id or instant(now), instant(now), batch_limit),
            ).fetchall()
            if not rows:
                return []
            ids = [str(row['resident_id']) for row in rows]
            # Keep the identity -> outbox lock order used by saves and ACKs.
            # Rebuild old outstanding intents from canonical facts on retry;
            # the Resource boundary still rejects stale publication payloads.
            connection.execute(
                'SELECT resident_id FROM residents WHERE resident_id=ANY(%s) ORDER BY resident_id FOR SHARE',
                (ids,),
            ).fetchall()
            current = {record['resident_id']: record for record in self._residents_from_rows(
                connection, connection.execute(
                    self._resident_select('WHERE r.resident_id=ANY(%s)', include_deleted=True), (ids,),
                ).fetchall(),
            )}
            claimed: list[dict[str, Any]] = []
            for row in rows:
                resident_id = str(row["resident_id"])
                revision = int(row["revision"])
                payload = current.get(resident_id, _object(row['payload_json'], resident_id))
                changed = connection.execute(
                    "UPDATE resident_resource_projection_outbox SET payload_json=%s, lease_token=%s, lease_until=%s, attempt_count=attempt_count + 1 WHERE resident_id=%s AND revision=%s AND (lease_token='' OR lease_until <= %s)",
                    (
                        _json(payload),
                        lease_token,
                        instant(now + max(1.0, float(lease_seconds))),
                        resident_id,
                        revision,
                        instant(now),
                    ),
                ).rowcount
                if not changed:
                    continue
                claimed.append({
                    "resident_id": resident_id,
                    "revision": revision,
                    "payload": payload,
                    "attempt_count": int(row["attempt_count"] or 0) + 1,
                    "lease_token": lease_token,
                })
            return claimed

        return self.database.write(write)

    def resident_resource_projection_pending(self, resident_id: str) -> bool:
        with self.database.read() as connection:
            return connection.execute(
                'SELECT 1 FROM resident_resource_projection_outbox WHERE resident_id=%s',
                (resident_id,),
            ).fetchone() is not None

    def ensure_resident_registration_projection(self, resident_id: str) -> None:
        """Repair missing publication work, never manufacture an acknowledgement."""
        def write(connection: Any) -> None:
            row = connection.execute(
                self._resident_select('WHERE r.resident_id=%s'), (resident_id,),
            ).fetchone()
            if row is None:
                raise KeyError(resident_id)
            record = self._resident_from_row(row)
            if record.get("deleted_at"):
                raise KeyError(resident_id)
            if record.get("registration_state") != "resource_pending":
                return
            existing = connection.execute(
                'SELECT 1 FROM resident_resource_projection_outbox WHERE resident_id=%s',
                (resident_id,),
            ).fetchone()
            if existing is None:
                # Use the current identity in the same transaction as the
                # absence check; preserve any existing publisher's CAS lease.
                self._enqueue_resident_resource_projection(connection, resident_id, record)

        self.database.write(write)

    def complete_resident_resource_projections(
        self,
        claims: Iterable[Mapping[str, Any]],
    ) -> dict[str, Any]:
        values = [dict(item) for item in claims]

        def write(connection: Any) -> dict[str, Any]:
            completed = 0
            ready_resident_ids: list[str] = []
            for item in values:
                resident_id = str(item.get("resident_id") or "")
                # Match the identity -> outbox lock order of registration and
                # deletion. A late publication receipt must not make a deleted
                # identity ready, even while its outbox claim is still valid.
                identity = connection.execute(
                    'SELECT deleted_at FROM residents WHERE resident_id=%s FOR SHARE',
                    (resident_id,),
                ).fetchone()
                acknowledged = connection.execute(
                    'DELETE FROM resident_resource_projection_outbox WHERE resident_id=%s AND revision=%s AND lease_token=%s',
                    (
                        resident_id,
                        int(item.get("revision") or 0),
                        str(item.get("lease_token") or ""),
                    ),
                ).rowcount
                completed += acknowledged
                if not acknowledged or identity is None or identity['deleted_at'] is not None:
                    continue
                row = connection.execute(
                    "UPDATE residents SET registration_state='ready' WHERE resident_id=%s "
                    "AND registration_state='resource_pending' RETURNING resident_id", (resident_id,),
                ).fetchone()
                if row:
                    ready_resident_ids.append(resident_id)
            return {"completed": completed, "ready_resident_ids": ready_resident_ids}

        return self.database.write(write)

    def fail_resident_resource_projections(
        self,
        claims: Iterable[Mapping[str, Any]],
        error: object,
    ) -> int:
        values = [dict(item) for item in claims]
        available_at = time.time() + min(
            60.0,
            max(1.0, max((int(item.get("attempt_count") or 1) for item in values), default=1) * 2.0),
        )
        message = f"{error.__class__.__name__}: {error}"[:1000] if isinstance(error, BaseException) else str(error)[:1000]

        def write(connection: Any) -> int:
            failed = 0
            for item in values:
                failed += connection.execute(
                    "UPDATE resident_resource_projection_outbox SET available_at=%s, lease_token='', lease_until=NULL, last_error=%s WHERE resident_id=%s AND revision=%s AND lease_token=%s",
                    (
                        instant(available_at),
                        message,
                        str(item.get("resident_id") or ""),
                        int(item.get("revision") or 0),
                        str(item.get("lease_token") or ""),
                    ),
                ).rowcount
            return failed

        return self.database.write(write)

    def resident_resource_projection_health(self) -> dict[str, Any]:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS pending_count, "
                "COALESCE(MAX(attempt_count), 0) AS max_attempt_count, "
                "COALESCE(MAX(last_error), '') AS last_error "
                "FROM resident_resource_projection_outbox"
            ).fetchone()
        return {
            "pending_count": int(row["pending_count"] or 0),
            "max_attempt_count": int(row["max_attempt_count"] or 0),
            "last_error": str(row["last_error"] or ""),
        }

    def remove_residents(self, tombstones: Iterable[Mapping[str, Any]]) -> list[str]:
        """Retain identity permanently; repeat deletion preserves the first facts.

        The lifecycle service owns quiescing work and detaching graph edges in
        the same enclosing transaction. Agent and historical records remain.
        """
        values = [dict(record) for record in tombstones]
        if any(not record.get('resident_id') or not record.get('deleted_at') for record in values):
            raise ValueError('Resident deletion requires identity and deleted_at')

        def write(connection: Any) -> list[str]:
            workspace_ids = set()
            for record in sorted(values, key=lambda value: value['resident_id']):
                rid = record['resident_id']
                prior = connection.execute(
                    'SELECT deleted_at,owned_workspace_id FROM residents WHERE resident_id=%s FOR UPDATE', (rid,),
                ).fetchone()
                if prior is None:
                    raise KeyError(rid)
                if prior['deleted_at'] is not None:
                    continue
                if prior['owned_workspace_id']:
                    workspace_ids.add(prior['owned_workspace_id'])
                workspace_ids.update(row['workspace_id'] for row in connection.execute(
                    'SELECT workspace_id FROM resident_workspace_bindings WHERE resident_id=%s', (rid,),
                ).fetchall())
                connection.execute(
                    'UPDATE residents SET deleted_at=%s,deleted_by=%s,historical_project_id='
                    'COALESCE((SELECT project_id FROM lineages WHERE lineages.lineage_id=(SELECT lineage_id FROM lineage_nodes WHERE node_id=residents.resident_id)),%s) '
                    'WHERE resident_id=%s',
                    (record['deleted_at'], record.get('deleted_by') or None, record.get('project_id') or None, rid),
                )
                connection.execute('DELETE FROM resident_workspace_bindings WHERE resident_id=%s', (rid,))
                connection.execute('DELETE FROM resident_workspace_transfers WHERE resident_id=%s', (rid,))
            if not workspace_ids:
                return []
            from .resident_workspace_retirement import retire_unreferenced
            return retire_unreferenced(connection, workspace_ids=workspace_ids,
                                       deleted_at=values[0]['deleted_at'],
                                       deleted_by=values[0].get('deleted_by') or '')
        return self.database.write(write)

    def retire_workspaces_under(self, paths, *, deleted_at: str, deleted_by: str) -> list[str]:
        """Retire unused registrations within folders being deleted."""
        from .resident_workspace_retirement import retire_unreferenced
        return self.database.write(lambda connection: retire_unreferenced(
            connection, paths=paths, deleted_at=deleted_at, deleted_by=deleted_by,
        ))

    def resident_deletion(self, resident_id: str) -> dict[str, Any] | None:
        """Historical projection of the same retained identity, never a second row."""
        with self.database.snapshot() as connection:
            row = connection.execute(self._resident_select(
                'WHERE r.resident_id=%s AND r.deleted_at IS NOT NULL', include_deleted=True,
            ), (resident_id,)).fetchone()
            if row is None:
                return None
            return self._residents_from_rows(connection, [row])[0]

    def resident_execution_record(self, resident_id: str) -> dict[str, Any]:
        return self.resident_execution_record_with_revision(resident_id)["state"]

    @staticmethod
    def _mailbox_queues(connection: Any, resident_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        """Each Resident's queue, as its Job carries it, from its Mailbox."""

        queues: dict[str, list[dict[str, Any]]] = {}
        members: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for offset in range(0, len(resident_ids), 500):
            for row in connection.execute(
                "SELECT resident_id, message_id, place, group_id, message_json, item_json "
                "FROM resident_mailbox WHERE resident_id = ANY(%s) AND place IN ('queue', 'group') "
                "ORDER BY resident_id, place, group_id, position",
                (resident_ids[offset:offset + 500],),
            ).fetchall():
                resident_id = str(row["resident_id"])
                if row["place"] == "group":
                    members[(resident_id, str(row["group_id"]))].append(_mailbox_item(row))
                else:
                    queues.setdefault(resident_id, []).append(_mailbox_item(row))
        for resident_id, items in queues.items():
            for item in items:
                group = members.get((resident_id, str(item.get("user_message_id") or "")))
                if group:
                    item["mailbox_members"] = group
        return queues

    def _with_queues(self, connection: Any, states: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        queues = self._mailbox_queues(connection, list(states)) if states else {}
        for resident_id, state in states.items():
            if state or resident_id in queues:
                state["queue"] = queues.get(resident_id, [])
        return states

    def resident_execution_records(self, resident_ids):
        requested = list(dict.fromkeys(resident_ids))
        with self.database.read_transaction() as connection:
            values = resident_execution_storage.read(connection, requested) if requested else {}
            return self._with_queues(connection, {rid: values.get(rid,(0,{}))[1] for rid in requested})

    def resident_execution_record_with_revision(self, resident_id):
        with self.database.read_transaction() as connection:
            revision,state = resident_execution_storage.read(connection,[resident_id]).get(resident_id,(0,{}))
            self._with_queues(connection,{resident_id:state})
        return {'revision':revision,'state':state}

    def resident_execution_control(self, resident_id):
        from .runtime_read_models import execution_control_state
        with self.database.read_transaction() as connection:
            revision,state = resident_execution_storage.read(connection,[resident_id],content=False).get(resident_id,(0,{}))
            self._with_queues(connection,{resident_id:state})
        return {'revision':revision,'state':execution_control_state(state)}

    def resident_execution_revision(self, resident_id):
        with self.database.read_current() as connection:
            row = connection.execute('SELECT revision FROM resident_execution_work WHERE resident_id=%s',(resident_id,)).fetchone()
        return int(row['revision']) if row else 0

    def request_resident_execution_stop(self, resident_id, execution_id, message_id, actor, now):
        """Commit exact-input control without loading prompt, Workspace or Conversation.

        The parent Resident row is the same serialization boundary as normal Job
        writes. Their merge rule preserves this intent on stale progress writes.
        """
        def write(connection):
            resident = connection.execute(
                'SELECT resident_id FROM residents WHERE resident_id=%s AND deleted_at IS NULL FOR UPDATE',
                (resident_id,),
            ).fetchone()
            if resident is None:
                raise KeyError("resident not found")
            row = connection.execute(
                'SELECT execution_id,input_message_id,progress_json FROM resident_execution_work WHERE resident_id=%s FOR UPDATE',
                (resident_id,),
            ).fetchone()
            if row is None or row['execution_id'] != execution_id or row['input_message_id'] != message_id:
                raise ValueError("message is not the active Resident input")
            progress = json_value(row['progress_json'])
            if progress.get('outcome') in resident_execution_storage.TERMINAL or progress.get('finished_at'):
                return False
            progress.setdefault('stop_requested_at', now)
            progress.setdefault('stop_requested_by', actor)
            progress['updated_at'] = now
            revision = int(connection.execute("SELECT nextval('resident_execution_revision')").fetchone()[0])
            connection.execute(
                'UPDATE resident_execution_work SET progress_json=%s,revision=%s WHERE resident_id=%s',
                (resident_execution_storage.encode(progress),revision,resident_id),
            )
            from .resident_delivery_work import enqueue_state
            enqueue_state(connection, resident_id, revision)
            return True
        return self.database.write(write)

    def advance_resident_execution_cursor(self, resident_id, execution_id, cursor):
        self.database.write(lambda connection: connection.execute(
            "UPDATE resident_execution_work SET event_cursor=%s,revision=nextval('resident_execution_revision') "
            "WHERE resident_id=%s AND execution_id=%s AND event_cursor < %s",
            (cursor,resident_id,execution_id,cursor),
        ))

    @staticmethod
    def _sync_mailbox_queue(connection: Any, resident_id: str, queue: object) -> None:
        """Make the Mailbox's queue and group rows the Job's queue.

        A message that leaves the queue stays in the Mailbox, ``pending``,
        until it enters the trace or its input ends.
        """

        now = _now_iso_millis()
        desired: list[tuple[str, str, int, str, dict[str, Any]]] = []
        for index, raw in enumerate(queue if isinstance(queue, list) else []):
            if not isinstance(raw, Mapping):
                raise ValueError("Resident Queue items must be objects")
            item = deepcopy(dict(raw))
            members = item.pop("mailbox_members", None) or []
            message_id = str(item.get("user_message_id") or "").strip()
            if not message_id:
                raise ValueError("Resident Queue item requires user_message_id")
            desired.append((message_id, "queue", index, "", item))
            for position, member in enumerate(members):
                member_item = deepcopy(dict(member))
                member_id = str(member_item.get("user_message_id") or "").strip()
                if not member_id:
                    raise ValueError("Mailbox group member requires user_message_id")
                desired.append((member_id, "group", position, message_id, member_item))
        existing = {
            str(row["message_id"]): row
            for row in connection.execute(
                "SELECT message_id, place, message_json FROM resident_mailbox WHERE resident_id = %s FOR UPDATE",
                (resident_id,),
            ).fetchall()
        }
        values = []
        for message_id, place, position, group_id, item in desired:
            row = existing.get(message_id)
            stored = _decode_mailbox_message(row["message_json"], message_id) if row is not None else None
            message = item.get("input_message")
            if not _real_message(stored):
                stored = deepcopy(dict(message)) if _real_message(message) else {}
            if stored and message == stored:
                item.pop("input_message", None)
                item[STORED_INPUT_MESSAGE] = True
            values.append((
                resident_id, message_id, place, position, group_id,
                str(stored.get("author_id") or item.get("actor") or ""),
                str(stored.get("created_at") or item.get("enqueued_at") or now),
                now, _json(_encode_mailbox_message(stored)), _json(item),
            ))
        if values:
            connection.executemany(
                "INSERT INTO resident_mailbox(resident_id, message_id, place, position, group_id, author_id, "
                "accepted_at, updated_at, message_json, item_json) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (resident_id, message_id) DO UPDATE SET place = excluded.place, "
                "position = excluded.position, group_id = excluded.group_id, updated_at = excluded.updated_at, "
                "item_json = excluded.item_json, message_json = CASE WHEN resident_mailbox.message_json = '{}' "
                "THEN excluded.message_json ELSE resident_mailbox.message_json END",
                values,
            )
        kept = {item[0] for item in desired}
        left = [
            message_id for message_id, row in existing.items()
            if row["place"] in ("queue", "group") and message_id not in kept
        ]
        if left:
            # An input that already ended (a withdrawn item) leaves the Mailbox.
            connection.execute(
                "DELETE FROM resident_mailbox mailbox USING conversation_input_states input "
                "WHERE mailbox.resident_id = %s AND mailbox.message_id = ANY(%s) "
                "AND input.resident_id = mailbox.resident_id AND input.message_id = mailbox.message_id "
                "AND input.state IN ('completed', 'failed', 'cancelled')",
                (resident_id, left),
            )
            connection.execute(
                "UPDATE resident_mailbox SET place = 'pending', updated_at = %s "
                "WHERE resident_id = %s AND message_id = ANY(%s)",
                (now, resident_id, left),
            )

    def put_resident_execution_record(self, resident_id, value, *, return_state=False, delivery_message_id=""):
        payload = deepcopy(dict(value))
        queue = payload.pop('queue', [])
        def write(connection):
            resident = connection.execute('SELECT r.resident_id,r.backend,c.backend_route_id FROM residents r LEFT JOIN resident_execution_configuration c USING(resident_id) WHERE r.resident_id=%s AND r.deleted_at IS NULL FOR UPDATE OF r', (resident_id,)).fetchone()
            if resident is None:
                raise KeyError(resident_id)
            if payload.get("execution_id"):
                from management_portal.execution import execution_lifecycle
                from . import resident_backend, resident_execution_links
                execution_lifecycle.reserve(connection, execution_id=payload["execution_id"], agent_id=resident_id,
                    actor=str(payload.get("actor") or ""), provider=resident_backend.execution_provider(dict(resident)),
                    created_at=str(payload.get("enqueued_at") or payload.get("started_at") or _now_iso_millis()))
                resident_execution_links.link_inputs(resident_id, payload, connection=connection)
            revision,previous = resident_execution_storage.read(connection,[resident_id]).get(resident_id,(0,{}))
            same_execution = previous.get('execution_id') and previous.get('execution_id') == payload.get('execution_id')
            same_input = previous.get('user_message_id') and previous.get('user_message_id') == payload.get('user_message_id')
            if (same_execution or same_input) and previous.get('intake_admitted_at'):
                # Progress-only writes cannot undo admission of the same input.
                payload['intake_admitted_at'] = previous['intake_admitted_at']
            if (same_execution or same_input) and previous.get('stop_requested_at'):
                payload['stop_requested_at'] = previous['stop_requested_at']
                if previous.get('stop_requested_by'):
                    payload['stop_requested_by'] = previous['stop_requested_by']
            if same_execution:
                if previous.get('execution_acknowledged'):
                    payload['execution_acknowledged'] = True
                payload['execution_event_cursor'] = max(int(previous.get('execution_event_cursor') or 0), int(payload.get('execution_event_cursor') or 0))
            if previous.get('execution_id') and not same_execution:
                self._release_execution(resident_id, previous)
            revision = int(connection.execute("SELECT nextval('resident_execution_revision')").fetchone()[0])
            if resident_execution_storage.pending(payload):
                resident_execution_storage.put(connection,resident_id,revision,payload)
            else:
                if payload.get('execution_id'):
                    self._release_execution(resident_id, payload)
                connection.execute('DELETE FROM resident_execution_work WHERE resident_id=%s',(resident_id,))
            self._sync_mailbox_queue(connection,resident_id,queue)
            if delivery_message_id:
                from .resident_delivery_work import enqueue
                enqueue(connection, resident_id, delivery_message_id, revision)
            return revision,{**deepcopy(payload),'queue':deepcopy(queue)}
        revision,committed = self.database.write(write)
        return (revision,committed) if return_state else revision

    def _release_execution(self, resident_id, value):
        identity = str(value.get('execution_id') or '')
        if not identity:
            return
        def write(connection):
            if value.get("outcome") == "failed" and value.get("error") and value.get("user_message_id"):
                error = dict(value.get("runtime_error") or {"code": "execution_failed", "message": str(value["error"])})
                connection.execute(
                    "UPDATE conversation_input_states SET error_json=%s WHERE resident_id=%s AND message_id=%s "
                    "AND state IN ('queued','running')",
                    (json_text(error, ensure_ascii=False), resident_id, value["user_message_id"]),
                )
            if value.get('submitted_at'):
                if not value.get('execution_acknowledged'):
                    connection.execute(
                        "INSERT INTO resident_execution_release_work VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
                        (identity,resident_id,str(value['actor'])),
                    )
            else:
                from management_common.portal_database import _database_schema
                table = '"' + _database_schema('agent') + '".executions'
                status = value.get("outcome") if value.get("outcome") in resident_execution_storage.TERMINAL else "cancelled"
                connection.execute(f"UPDATE {table} SET status=%s,finished_at=%s "
                                   "WHERE execution_id=%s AND status IN ('preparing','queued')",
                                   (status,str(value.get("finished_at") or _now_iso_millis()),identity))
        self.database.write(write)

    def terminal_input_receipts(self, inputs: Iterable[tuple[str,str]]) -> dict[tuple[str,str],dict[str,Any]]:
        pairs = list(dict.fromkeys(inputs))
        if not pairs:
            return {}
        from management_common.portal_database import _database_schema
        agent = '"' + _database_schema('agent') + '"'
        with self.database.read() as connection:
            rows = connection.execute(
                "WITH requested AS (SELECT * FROM unnest(%s::text[],%s::text[]) AS p(resident_id,message_id)) "
                "SELECT input.resident_id,input.message_id,e.execution_id,e.status,e.finished_at,e.actor_ref "
                "FROM requested JOIN conversation_input_states input USING(resident_id,message_id) "
                f"JOIN LATERAL (SELECT e.* FROM {agent}.execution_inputs i "
                f"JOIN {agent}.executions e USING(execution_id) "
                "WHERE i.message_id=input.message_id AND e.agent_id=input.resident_id "
                "ORDER BY e.created_at DESC,e.execution_id DESC LIMIT 1) e ON TRUE "
                "WHERE input.state IN ('queued','running') AND e.status IN ('completed','failed','cancelled') "
                "AND e.finished_at>=input.updated_at "
                "AND NOT EXISTS (SELECT 1 FROM resident_execution_work active WHERE active.resident_id=input.resident_id "
                "AND active.input_message_id=input.message_id) "
                "AND NOT EXISTS (SELECT 1 FROM resident_mailbox mailbox WHERE mailbox.resident_id=input.resident_id "
                "AND mailbox.message_id=input.message_id)",
                ([rid for rid,_ in pairs],[mid for _,mid in pairs]),
            ).fetchall()
        return {(row['resident_id'],row['message_id']): {
            'execution_id':row['execution_id'],'outcome':row['status'],
            'finished_at':iso_time(row['finished_at']),'actor':row['actor_ref'],
        } for row in rows}

    def acknowledge_resident_execution(self,resident_id,execution_id):
        def write(connection):
            connection.execute('SELECT resident_id FROM residents WHERE resident_id=%s FOR UPDATE', (resident_id,))
            revision,value = resident_execution_storage.read(connection,[resident_id]).get(resident_id,(0,{}))
            if value.get('execution_id') != execution_id:
                return
            value['execution_acknowledged'] = True
            if resident_execution_storage.pending(value):
                revision = int(connection.execute("SELECT nextval('resident_execution_revision')").fetchone()[0])
                resident_execution_storage.put(connection,resident_id,revision,value)
            else:
                self._release_execution(resident_id,value)
                connection.execute('DELETE FROM resident_execution_work WHERE resident_id=%s',(resident_id,))
        self.database.write(write)

    def delete_resident_execution_record(self, resident_id):
        def write(connection):
            connection.execute('SELECT resident_id FROM residents WHERE resident_id=%s FOR UPDATE', (resident_id,))
            _revision,value = resident_execution_storage.read(connection,[resident_id]).get(resident_id,(0,{}))
            if value.get('execution_id'):
                self._release_execution(resident_id,value)
            connection.execute('DELETE FROM resident_execution_work WHERE resident_id=%s',(resident_id,))
            self._sync_mailbox_queue(connection,resident_id,[])
        self.database.write(write)

    # -- Mailbox ----------------------------------------------------------

    @staticmethod
    def _lock_active_resident(connection: Any, resident_id: str) -> None:
        # Holding this lock through admission serializes with deletion. An
        # earlier service lookup alone cannot protect the retained identity.
        row = connection.execute(
            'SELECT 1 FROM residents WHERE resident_id=%s AND deleted_at IS NULL FOR SHARE',
            (resident_id,),
        ).fetchone()
        if row is None:
            raise KeyError(resident_id)

    def mailbox_accept(self, resident_id: str, message: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
        """Keep one accepted message in the Mailbox, ``pending`` until it is queued or delivered.

        Returns the message the Mailbox holds and whether it was new; one
        already there is returned as it is.
        """

        stored = deepcopy(dict(message))
        message_id = str(stored.get("message_id") or "").strip()
        if not message_id or not _real_message(stored):
            raise ValueError("Mailbox message requires message_id and content_parts")
        now = _now_iso_millis()

        def write(connection: Any) -> tuple[dict[str, Any], bool]:
            self._lock_active_resident(connection, resident_id)
            inserted = connection.execute(
                "INSERT INTO resident_mailbox(resident_id, message_id, place, position, group_id, author_id, "
                "accepted_at, updated_at, message_json, item_json) VALUES (%s, %s, 'pending', 0, '', %s, %s, %s, %s, '{}') "
                "ON CONFLICT (resident_id, message_id) DO NOTHING RETURNING message_id",
                (resident_id, message_id, str(stored.get("author_id") or ""),
                 str(stored.get("created_at") or now), now, _json(_encode_mailbox_message(stored))),
            ).fetchone()
            if inserted is not None:
                return stored, True
            row = connection.execute(
                "SELECT message_json FROM resident_mailbox WHERE resident_id = %s AND message_id = %s FOR UPDATE",
                (resident_id, message_id),
            ).fetchone()
            existing = _decode_mailbox_message(row["message_json"], message_id)
            if not _real_message(existing):
                connection.execute(
                    "UPDATE resident_mailbox SET message_json = %s, author_id = %s, updated_at = %s "
                    "WHERE resident_id = %s AND message_id = %s",
                    (_json(_encode_mailbox_message(stored)), str(stored.get("author_id") or ""), now, resident_id, message_id),
                )
                return stored, False
            return existing, False

        return self.database.write(write)

    def mailbox_entries(
        self,
        resident_ids: Iterable[str],
        *,
        message_ids: Iterable[str] | None = None,
        places: Iterable[str] | None = None,
        author: str = "",
    ) -> list[dict[str, Any]]:
        """Mailbox rows, by Resident and place order."""

        requested = list(dict.fromkeys(str(item) for item in resident_ids if str(item)))
        clauses = ["resident_id = ANY(%s)"]
        parameters: list[Any] = [requested]
        if message_ids is not None:
            clauses.append("message_id = ANY(%s)")
            parameters.append(list(dict.fromkeys(str(item) for item in message_ids)))
        if places is not None:
            clauses.append("place = ANY(%s)")
            parameters.append(list(places))
        if author:
            clauses.append("lower(trim(ltrim(author_id, '@＠'))) = lower(%s)")
            parameters.append(str(author).strip().lstrip("@＠").strip())
        if not requested:
            return []
        with self.database.read() as connection:
            rows = connection.execute(
                "SELECT * FROM resident_mailbox WHERE " + " AND ".join(clauses)
                + " ORDER BY resident_id, place, group_id, position, accepted_at, message_id",
                parameters,
            ).fetchall()
        return [_mailbox_row(row) for row in rows]

    def mailbox_entries_by_author(self, author: str) -> list[dict[str, Any]]:
        """Every Resident's waiting messages from one sender."""

        clean = str(author or "").strip().lstrip("@＠").strip()
        if not clean:
            return []
        with self.database.read() as connection:
            rows = connection.execute(
                "SELECT * FROM resident_mailbox WHERE lower(trim(ltrim(author_id, '@＠'))) = lower(%s)",
                (clean,),
            ).fetchall()
        return [_mailbox_row(row) for row in rows]

    def mailbox_actor_activity(self, author: str, *, excluded_business_ref: str = "") -> dict[str, str]:
        """Latest valid waiting input per Resident, without decoding every message."""
        clean = str(author or "").strip().lstrip("@＠").strip()
        if not clean:
            return {}
        exclusion = (
            " AND coalesce(message_json::jsonb ->> 'business_ref', '') !~ %s"
            if excluded_business_ref else ""
        )
        with self.database.read() as connection:
            rows = connection.execute(
                "SELECT resident_id, MAX(coalesce(nullif(message_json::jsonb ->> 'created_at', '')::timestamptz, "
                "accepted_at)) AS latest_at FROM resident_mailbox "
                "WHERE lower(trim(ltrim(author_id, '@＠'))) = lower(%s) "
                "AND jsonb_typeof(message_json::jsonb -> 'content_parts') = 'array'"
                + exclusion + " GROUP BY resident_id",
                (clean, *([excluded_business_ref] if excluded_business_ref else [])),
            ).fetchall()
        return {str(row["resident_id"]): iso_time(row["latest_at"]) for row in rows}

    def mailbox_entry(self, resident_id: str, message_id: str) -> dict[str, Any] | None:
        rows = self.mailbox_entries([resident_id], message_ids=[message_id])
        return rows[0] if rows else None

    def mailbox_forget(
        self,
        resident_id: str,
        message_ids: Iterable[str],
        *,
        places: Iterable[str] = ("pending", "info"),
        connection: Any | None = None,
    ) -> list[str]:
        """Take messages out of the Mailbox (delivered, withdrawn or ended); queued ones stay."""

        ids = list(dict.fromkeys(str(item) for item in message_ids if str(item)))
        if not ids:
            return []

        def write(current: Any) -> list[str]:
            return [
                str(row["message_id"])
                for row in current.execute(
                    "DELETE FROM resident_mailbox WHERE resident_id = %s AND message_id = ANY(%s) "
                    "AND place = ANY(%s) RETURNING message_id",
                    (resident_id, ids, list(places)),
                ).fetchall()
            ]

        if connection is not None:
            return write(connection)
        return self.database.write(write)

    def mailbox_hold_info(
        self,
        resident_id: str,
        message: Mapping[str, Any],
        *,
        held_at: str,
        limit: int,
        first: bool = False,
        item: Mapping[str, Any] | None = None,
    ) -> bool:
        """Hold one info message (at the end, or ``first``); False when it was already held."""

        stored = deepcopy(dict(message))
        message_id = str(stored.get("message_id") or "").strip()
        if not message_id or not _real_message(stored):
            raise ValueError("info message requires message_id and content_parts")
        now = _now_iso_millis()

        def write(connection: Any) -> bool:
            self._lock_active_resident(connection, resident_id)
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended('resident_mailbox_info:' || %s, 0))", (resident_id,),
            )
            row = connection.execute(
                "SELECT place FROM resident_mailbox WHERE resident_id = %s AND message_id = %s FOR UPDATE",
                (resident_id, message_id),
            ).fetchone()
            if row is not None and row["place"] == "info":
                return False
            bounds = connection.execute(
                "SELECT COUNT(*) AS count, COALESCE(MIN(position), 0) AS first, COALESCE(MAX(position), 0) AS last "
                "FROM resident_mailbox WHERE resident_id = %s AND place = 'info'",
                (resident_id,),
            ).fetchone()
            if int(bounds["count"]) >= limit:
                raise ValueError(f"Resident 已有 {limit} 条未送达的 info 消息，请先让它处理一条请求")
            position = int(bounds["first"]) - 1 if first else int(bounds["last"]) + 1
            details = _json({**dict(item or {}), "held_at": held_at})
            connection.execute(
                "INSERT INTO resident_mailbox(resident_id, message_id, place, position, group_id, author_id, "
                "accepted_at, updated_at, message_json, item_json) VALUES (%s, %s, 'info', %s, '', %s, %s, %s, %s, %s) "
                "ON CONFLICT (resident_id, message_id) DO UPDATE SET place = 'info', position = excluded.position, "
                "group_id = '', updated_at = excluded.updated_at, item_json = excluded.item_json, "
                "message_json = CASE WHEN resident_mailbox.message_json = '{}' THEN excluded.message_json "
                "ELSE resident_mailbox.message_json END",
                (resident_id, message_id, position, str(stored.get("author_id") or ""),
                 str(stored.get("created_at") or now), now, _json(_encode_mailbox_message(stored)), details),
            )
            return True

        return bool(self.database.write(write))

    def health(self) -> dict[str, Any]:
        with self.database.read() as connection:
            schema = connection.execute(
                "SELECT value FROM lineage_meta WHERE key='schema'"
            ).fetchone()
        return {
            "schema": str(schema["value"] if schema else ""),
            "database": self.database.display_name,
        }


_STORE: LineageDataStore | None = None
_STORE_LOCK = threading.Lock()


def get_lineage_data_store() -> LineageDataStore:
    global _STORE
    identity = portal_database(schema="lineage").identity
    with _STORE_LOCK:
        if _STORE is None or _STORE.database_identity != identity:
            _STORE = LineageDataStore()
        return _STORE


def reset_lineage_data_store_for_tests() -> None:
    global _STORE
    with _STORE_LOCK:
        _STORE = None
