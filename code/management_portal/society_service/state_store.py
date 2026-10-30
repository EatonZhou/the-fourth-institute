from __future__ import annotations

from management_common.format_tags import schema_matches

import json
import threading
import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, MutableMapping
from contextlib import AbstractContextManager
from typing import Any

from management_common.portal_database import portal_database
from . import trigger_path_storage, family_storage, resident_storage, program_work_storage, domain_work_storage, relation_work_storage, resident_execution_storage
from . import resident_delivery_storage
from . import graph_storage, definition_collections, lineage_metadata_storage
from . import wire_delivery_storage, family_task_dispatch_storage
from . import (
    lineage_data_definitions, lineage_data_effect_cleanup, lineage_data_executions, lineage_data_mailbox,
    lineage_data_notifications, lineage_data_project_graphs, lineage_data_projection_outbox, lineage_data_residents,
    lineage_data_runtime,
)
from management_portal import application_targets
from .runtime_read_models import (
    POSTGRES_TRIGGERS,
    create_runtime_read_models,
)


LINEAGE_DATA_SCHEMA = "tfi.lineage-data.v65"
LINEAGE_TIMER_LEASE_KEY = "lineage_timer_lease"
DEFINITION_COLUMN_FIELDS = frozenset({
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
    **wire_delivery_storage.COLUMNS,
    **family_task_dispatch_storage.COLUMNS,
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
    wire_delivery_storage.create_schema(connection)
    family_task_dispatch_storage.create_schema(connection)
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
    resident_delivery_storage.create_schema(connection)
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


# A queue item's input_message equal to the message the Mailbox keeps is
# stored once, in message_json; the item says so.
STORED_INPUT_MESSAGE = "input_message_in_mailbox"


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
    """Single persistence authority for SocietyService domain data.

    One database and one part per kind of data it keeps. The methods below
    are the parts' operations; schema initialization, the timer lease and
    health read the store's own metadata.
    """

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

    def __init__(self, database: Any | None = None) -> None:
        self.database = database if database is not None else portal_database(schema="lineage")
        self.database_identity = self.database.identity
        self.database.write(self._initialize)
        self.project_graphs = lineage_data_project_graphs.ProjectGraphs(self.database)
        self.runtimes = lineage_data_runtime.LineageRuntimes(self.database)
        self.definitions = lineage_data_definitions.LineageDefinitions(
            self.database, project_graphs=self.project_graphs, deletions=self.lineage_deletion_operations,
        )
        self.notifications = lineage_data_notifications.HumanNotificationRecords(self.database)
        self.effect_cleanup = lineage_data_effect_cleanup.EffectCleanupQueue(self.database)
        self.projection_outbox = lineage_data_projection_outbox.ResidentProjectionOutbox(self.database)
        self.residents = lineage_data_residents.ResidentRecords(
            self.database, project_graphs=self.project_graphs, outbox=self.projection_outbox,
        )
        self.executions = lineage_data_executions.ResidentExecutions(self.database)
        self.mailboxes = lineage_data_mailbox.ResidentMailboxes(self.database)

    def transaction(self, operation: Callable[[], Any]) -> Any:
        return self.database.write(lambda _connection: operation())

    def membership_transaction(self, operation: Callable[[], Any]) -> Any:
        return self.project_graphs.membership_transaction(operation)

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
            or not schema_matches(schema["value"], LINEAGE_DATA_SCHEMA)
            or not lineage_data_schema_matches(connection)
        ):
            raise RuntimeError("Lineage data database does not match its schema")

    # Read inside their own transaction by lineage_state and resident_record_domain.
    @staticmethod
    def _lineage_definition_from_connection(connection: Any, lineage_id: str) -> dict[str, Any]:
        return lineage_data_definitions.definition_from_connection(connection, lineage_id)

    # Held by resident_conversation_stream while it admits a message.
    @staticmethod
    def _lock_active_resident(connection: Any, resident_id: str) -> None:
        lineage_data_mailbox.lock_active_resident(connection, resident_id)

    def lineage_listing_records(self) -> list[dict[str, Any]]:
        return self.definitions.lineage_listing_records()

    def lineage_listing_graph_summaries(self, lineage_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
        return self.definitions.lineage_listing_graph_summaries(lineage_ids)

    def all_lineages(self) -> list[dict[str, Any]]:
        return self.definitions.all_lineages()

    def lineage_definition(self, lineage_id: str) -> dict[str, Any]:
        return self.definitions.lineage_definition(lineage_id)

    def lock_lineage_definition(self, lineage_id: str) -> dict[str, Any] | None:
        return self.definitions.lock_lineage_definition(lineage_id)

    def lineage_id_for_object(self, object_id: str) -> str:
        return self.definitions.lineage_id_for_object(object_id)

    def lineage_revision(self, lineage_id: str) -> int:
        return self.definitions.lineage_revision(lineage_id)

    def lineage_access_records(self, lineage_ids: Iterable[str] | None = None) -> list[dict[str, Any]]:
        return self.definitions.lineage_access_records(lineage_ids)

    def program_resident_observers(self, resident_id: str, event_type: str) -> dict[str, list[str]]:
        return self.definitions.program_resident_observers(resident_id, event_type)

    def upsert_layout_positions(self, lineage_id: str, positions: Iterable[Mapping[str, Any]], *, updated_at: str) -> dict[str, Any]:
        return self.definitions.upsert_layout_positions(lineage_id, positions, updated_at=updated_at)

    def put_lineage_definition(self, lineage_id: str, value: MutableMapping[str, Any], initial_runtime: Mapping[str, Any]) -> None:
        return self.definitions.put_lineage_definition(lineage_id, value, initial_runtime)

    def remove_lineage(self, lineage_id: str, *, deleted_by: str) -> None:
        return self.definitions.remove_lineage(lineage_id, deleted_by=deleted_by)

    def lineage_ids_for_resident(self, resident_id: str) -> list[str]:
        return self.definitions.lineage_ids_for_resident(resident_id)

    def lineage_snapshot(self, lineage_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        return self.definitions.lineage_snapshot(lineage_id)

    def put_lineage_runtime(self, lineage_id: str, value: Mapping[str, Any]) -> None:
        return self.runtimes.put_lineage_runtime(lineage_id, value)

    def put_lineage_runtime_changes(self, lineage_id: str, base: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, Any]:
        return self.runtimes.put_lineage_runtime_changes(lineage_id, base, candidate)

    def put_lineage_runtime_cas(self, lineage_id, expected, candidate):
        return self.runtimes.put_lineage_runtime_cas(lineage_id, expected, candidate)

    def human_notification_records(self, lineage_ids: Iterable[str] | None = None) -> list[dict[str, Any]]:
        return self.notifications.human_notification_records(lineage_ids)

    def portal_human_notifications(self) -> AbstractContextManager[dict[str, Any]]:
        return self.notifications.portal_human_notifications()

    def enqueue_effect_cleanup(self, lineage_id: str, activation_key: str, instructions: dict[str, list[str]]) -> None:
        return self.effect_cleanup.enqueue_effect_cleanup(lineage_id, activation_key, instructions)

    def effect_cleanup_task(self, lineage_id: str, activation_key: str) -> dict[str, Any] | None:
        return self.effect_cleanup.effect_cleanup_task(lineage_id, activation_key)

    def effect_cleanup_tasks(self, *, lineage_id: str | None = None, limit: int = 100, after_key: tuple[str, str, str] | None = None) -> list[dict[str, Any]]:
        return self.effect_cleanup.effect_cleanup_tasks(lineage_id=lineage_id, limit=limit, after_key=after_key)

    def complete_effect_cleanup(self, lineage_id: str, activation_key: str, *, expected_instructions: dict[str, list[str]]) -> bool:
        return self.effect_cleanup.complete_effect_cleanup(lineage_id, activation_key, expected_instructions=expected_instructions)

    def all_residents(self) -> list[dict[str, Any]]:
        return self.residents.all_residents()

    def resident_record(self, resident_id: str, *, include_deleted: bool = False) -> dict[str, Any]:
        return self.residents.resident_record(resident_id, include_deleted=include_deleted)

    def resident_usage_memberships(self, resident_ids: Iterable[str]) -> list[dict[str, str]]:
        return self.residents.resident_usage_memberships(resident_ids)

    def resident_identity_records(self, resident_ids: Iterable[str]) -> list[dict[str, Any]]:
        return self.residents.resident_identity_records(resident_ids)

    def resident_work_ids(self) -> list[str]:
        return self.residents.resident_work_ids()

    def residents_by_id(self, resident_ids: Iterable[str]) -> list[dict[str, Any]]:
        return self.residents.residents_by_id(resident_ids)

    def live_resident_ids(self, resident_ids: Iterable[str]) -> set[str]:
        return self.residents.live_resident_ids(resident_ids)

    def residents_for_lineage(self, lineage_id: str) -> list[dict[str, Any]]:
        return self.residents.residents_for_lineage(lineage_id)

    def put_resident_record(self, resident_id: str, value: Mapping[str, Any], *, commit_configuration: bool = False, commit_intake: bool = False) -> None:
        return self.residents.put_resident_record(resident_id, value, commit_configuration=commit_configuration, commit_intake=commit_intake)

    def read_resident_execution_settings(self, resident_id: str) -> dict[str, Any] | None:
        return self.residents.read_resident_execution_settings(resident_id)

    def write_resident_execution_settings(self, resident_id: str, settings: Mapping[str, Any]) -> dict[str, Any]:
        return self.residents.write_resident_execution_settings(resident_id, settings)

    def remove_residents(self, tombstones: Iterable[Mapping[str, Any]]) -> list[str]:
        return self.residents.remove_residents(tombstones)

    def retire_workspaces_under(self, paths, *, deleted_at: str, deleted_by: str) -> list[str]:
        return self.residents.retire_workspaces_under(paths, deleted_at=deleted_at, deleted_by=deleted_by)

    def resident_deletion(self, resident_id: str) -> dict[str, Any] | None:
        return self.residents.resident_deletion(resident_id)

    def enqueue_resident_resource_projection(self, resident_id: str, value: Mapping[str, Any]) -> int:
        return self.projection_outbox.enqueue_resident_resource_projection(resident_id, value)

    def claim_resident_resource_projections(self, *, limit: int = 50, lease_seconds: float = 90.0, resident_id: str = "") -> list[dict[str, Any]]:
        return self.projection_outbox.claim_resident_resource_projections(limit=limit, lease_seconds=lease_seconds, resident_id=resident_id)

    def resident_resource_projection_pending(self, resident_id: str) -> bool:
        return self.projection_outbox.resident_resource_projection_pending(resident_id)

    def ensure_resident_registration_projection(self, resident_id: str) -> None:
        return self.projection_outbox.ensure_resident_registration_projection(resident_id)

    def complete_resident_resource_projections(self, claims: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
        return self.projection_outbox.complete_resident_resource_projections(claims)

    def fail_resident_resource_projections(self, claims: Iterable[Mapping[str, Any]], error: object) -> int:
        return self.projection_outbox.fail_resident_resource_projections(claims, error)

    def resident_resource_projection_health(self) -> dict[str, Any]:
        return self.projection_outbox.resident_resource_projection_health()

    def resident_execution_records(self, resident_ids):
        return self.executions.resident_execution_records(resident_ids)

    def resident_execution_record_with_revision(self, resident_id):
        return self.executions.resident_execution_record_with_revision(resident_id)

    def resident_execution_control(self, resident_id):
        return self.executions.resident_execution_control(resident_id)

    def resident_execution_revision(self, resident_id):
        return self.executions.resident_execution_revision(resident_id)

    def request_resident_execution_stop(self, resident_id, execution_id, message_id, actor, now):
        return self.executions.request_resident_execution_stop(resident_id, execution_id, message_id, actor, now)

    def advance_resident_execution_cursor(self, resident_id, execution_id, cursor):
        return self.executions.advance_resident_execution_cursor(resident_id, execution_id, cursor)

    def put_resident_execution_record(self, resident_id, value, *, return_state=False, delivery_message_id=""):
        return self.executions.put_resident_execution_record(resident_id, value, return_state=return_state, delivery_message_id=delivery_message_id)

    def terminal_input_receipts(self, inputs: Iterable[tuple[str, str]]) -> dict[tuple[str, str], dict[str, Any]]:
        return self.executions.terminal_input_receipts(inputs)

    def acknowledge_resident_execution(self, resident_id, execution_id):
        return self.executions.acknowledge_resident_execution(resident_id, execution_id)

    def delete_resident_execution_record(self, resident_id):
        return self.executions.delete_resident_execution_record(resident_id)

    def resident_execution_record(self, resident_id: str) -> dict[str, Any]:
        return self.resident_execution_record_with_revision(resident_id)["state"]

    def mailbox_accept(self, resident_id: str, message: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
        return self.mailboxes.mailbox_accept(resident_id, message)

    def mailbox_entries(self, resident_ids: Iterable[str], *, message_ids: Iterable[str] | None = None, places: Iterable[str] | None = None, author: str = "") -> list[dict[str, Any]]:
        return self.mailboxes.mailbox_entries(resident_ids, message_ids=message_ids, places=places, author=author)

    def mailbox_entries_by_author(self, author: str) -> list[dict[str, Any]]:
        return self.mailboxes.mailbox_entries_by_author(author)

    def mailbox_actor_activity(self, author: str, *, excluded_business_ref: str = "") -> dict[str, str]:
        return self.mailboxes.mailbox_actor_activity(author, excluded_business_ref=excluded_business_ref)

    def mailbox_forget(self, resident_id: str, message_ids: Iterable[str], *, places: Iterable[str] = ("pending", "info"), connection: Any | None = None) -> list[str]:
        return self.mailboxes.mailbox_forget(resident_id, message_ids, places=places, connection=connection)

    def mailbox_hold_info(self, resident_id: str, message: Mapping[str, Any], *, held_at: str, limit: int, first: bool = False, item: Mapping[str, Any] | None = None) -> bool:
        return self.mailboxes.mailbox_hold_info(resident_id, message, held_at=held_at, limit=limit, first=first, item=item)

    def mailbox_entry(self, resident_id: str, message_id: str) -> dict[str, Any] | None:
        rows = self.mailbox_entries([resident_id], message_ids=[message_id])
        return rows[0] if rows else None

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
