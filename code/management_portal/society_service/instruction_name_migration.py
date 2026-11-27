"""Migrate current Society definitions when ISA contracts change.

``instruction_normalization`` and ``isa_definition_normalization`` own the
rewrite rules; this module applies them to stored definitions and templates,
stopping with locations while Effect work or pending deliveries still depend on
what would change. Execution journals, approved drafts and published template
history are never inputs to a rewrite.
"""

from copy import deepcopy
import json

from . import isa_migration_scope
from .instruction_normalization import (  # noqa: F401  (the migration's public rewrite entries)
    DefinitionMigrationRequired, normalize_component_config, normalize_prompt, normalize_script,
    normalize_wire_config,
)
from .isa_definition_normalization import (  # noqa: F401
    _normalize_definition, normalize_definition, normalize_template,
)


def _blockers(connection, lineage_id, effects, wires):
    from . import domain_work_storage, program_work_storage, trigger_path_storage

    if not effects and not wires:
        return []
    blocked = []
    for key, record in domain_work_storage.list_work(connection, "effect_work", lineage_id).items():
        effect_id = record.get("effect_id")
        source_wire_id = record.get("source_wire_id")
        if (effect_id in effects or source_wire_id in wires or not effect_id
                or not source_wire_id and effect_id in wires.values()):
            step = trigger_path_storage.effect_step_status(connection, lineage_id, key)
            calls = [
                {"position": position, "status": call.get("status") or "unknown",
                 "instruction_id": call.get("instruction_id") or ""}
                for position, call in (record.get("calls") or {}).items() if isinstance(call, dict)
            ]
            blocked.append({
                "kind": "effect_activation", "lineage_id": lineage_id,
                "effect_id": effect_id, "activation_key": key, "wire_id": source_wire_id or "",
                "journal_status": record.get("status") or "unknown",
                "trigger_path_id": step.get("trigger_path_id") or record.get("trigger_path_id") or "",
                "step_status": step.get("status") or "unknown",
                "calls": calls,
            })
    if not wires:
        return blocked
    runtime = program_work_storage.read(connection, lineage_id)
    for wire_id, effect_id in wires.items():
        deliveries = runtime.get("event_wires", {}).get(wire_id, {}).get("pending_events", ())
        if deliveries:
            blocked.append({
                "kind": "pending_event_deliveries", "count": len(deliveries),
                "lineage_id": lineage_id, "wire_id": wire_id, **_target_identity(effect_id),
                "deliveries": [_delivery_status(delivery) for delivery in deliveries],
            })
    for operator_id, state in runtime.get("operators", {}).items():
        for pending in state.get("pending_deliveries", {}).values():
            for wire_id in sorted(wires.keys() & set(pending.get("output_wire_ids", ()))):
                blocked.append({"kind": "pending_operator_delivery", "lineage_id": lineage_id,
                                "operator_id": operator_id, "wire_id": wire_id, **_target_identity(wires[wire_id])})
    return blocked


def _target_identity(target):
    return {"effect_id" if target.startswith("effect_") else "target_id": target}


def _delivery_status(delivery):
    hold = delivery.get("recovery_blocked") or {}
    event = delivery.get("event") or {}
    return {
        "delivery_id": delivery.get("claim_token") or "",
        "trigger_path_id": hold.get("trigger_path_id") or (event.get("payload") or {}).get("trigger_path_id") or "",
        "activation_key": hold.get("activation_key") or "",
        "instruction_id": hold.get("instruction_id") or "",
        "status": "blocked" if hold else "dispatching" if delivery.get("dispatch_token") else "waiting",
        "requires_resolution": hold.get("requires_resolution") is True,
    }


def _report(blocked):
    return {"blocked_count": sum(item.get("count", 1) for item in blocked), "blockers": blocked}


def _current_lineage_ids(connection):
    # Read identity indexes only. A deferred object's metadata, graph and work
    # must never be loaded merely to decide that its migration is out of scope.
    return [row["lineage_id"] for row in connection.execute(
        "SELECT lineage_id FROM lineages WHERE deleted_at IS NULL ORDER BY lineage_id"
    ).fetchall()]


def check():
    """Read owner checkpoints and report IDs only, before stopping the old owner."""
    from psycopg import IsolationLevel, connect, sql
    from psycopg.rows import dict_row

    from management_common.portal_database import portal_database
    from management_common.postgres_database import PostgresConnection

    from . import family_strategy_state, lineage_data_definitions

    target = portal_database(schema="lineage")
    blocked = []
    deferred = []
    # An ordinary owner store initializes its schema on connection. Use an
    # unpooled read-only snapshot so preflight is safe with the old owner live.
    with connect(target.dsn, row_factory=dict_row) as raw:
        raw.read_only = True
        raw.isolation_level = IsolationLevel.REPEATABLE_READ
        raw.execute(sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(target.schema)))
        connection = PostgresConnection(raw)
        blocked.extend(family_strategy_state.check_legacy_dispatches(connection))
        for lineage_id in _current_lineage_ids(connection):
            if isa_migration_scope.is_deferred(lineage_id):
                deferred.extend(isa_migration_scope.deferred_report())
                continue
            definition = lineage_data_definitions.definition_from_connection(connection, lineage_id)
            migrate_family = _has_original_family_script(definition)
            diagnostics = []
            effects, wires = _normalize_definition(definition, diagnostics=diagnostics)
            blocked.extend({"lineage_id": lineage_id, **issue} for issue in diagnostics)
            blocked.extend(_blockers(connection, lineage_id, effects, wires))
            if migrate_family:
                blocked.extend(family_strategy_state.check_family_state(connection, lineage_id))
        # Current, editable templates participate in the same preflight.
        # The template store's published snapshots and versions are evidence,
        # and are normalized only when installed into a current definition.
        template_table = sql.Identifier(portal_database(schema="templates").schema, "templates")
        if raw.execute("SELECT to_regclass(%s) AS relation", (template_table.as_string(raw),)).fetchone()["relation"]:
            for row in raw.execute(sql.SQL("SELECT template_id, body FROM {}").format(template_table)):
                try:
                    normalize_template(row)
                except DefinitionMigrationRequired as error:
                    blocked.extend({"kind": "template_contract", "template_id": row["template_id"], **issue}
                                   for issue in error.issues)
    return {**_report(blocked), "deferred_count": len(deferred), "deferred": deferred}


def _require_clear(report):
    if report["blocked_count"]:
        raise RuntimeError("ISA definition migration blocked by unresolved contracts or Effect work: " + json.dumps(report))


def _has_original_family_script(definition):
    from . import family_definition
    return any(isinstance(source := (component.get("config") or {}).get("script"), str)
               and family_definition.is_original_family_script(source)
               for component in definition["program"]["components"])


def _migrate_lineage(lineage_id):
    if isa_migration_scope.is_deferred(lineage_id):
        return False
    from . import family_strategy_state, lineage_aggregate, lineage_state

    catalog = lineage_aggregate.lineage_catalog_store()

    def migrate(connection):
        if catalog.state_store.lock_lineage_definition(lineage_id) is None:
            return
        before = lineage_aggregate.read_lineage_state(lineage_id)
        after = deepcopy(before)
        effects, wires = _normalize_definition(after)
        if after != before:
            _require_clear(_report(_blockers(connection, lineage_id, effects, wires)))
            # Copy facts only during the exact canonical strategy cutover, on
            # the same locked transaction as the graph CAS. Subsequent starts
            # must not replay old facts over an evolved SharedState cell.
            if _has_original_family_script(before):
                family_strategy_state.migrate_family_state(connection, lineage_id)
            lineage_state._persist_lineage_state_transaction(
                before, after, "Migrate current ISA instruction contracts",
                synchronize_resource_identities=False,
                synchronize_resident_identities=False,
            )
            return True
        return False

    with lineage_aggregate.lineage_scope(lineage_id, operation="normalize_isa_instruction_names"):
        if catalog.state_store.database.write(migrate):
            lineage_state.invalidate_lineage_read_cache(lineage_id)


def migrate():
    """Complete before any Society runtime worker can read the definitions."""
    from . import family_strategy_state, lineage_aggregate, resident_runtime_domain
    from .templates import store

    _require_clear(check())
    catalog = lineage_aggregate.lineage_catalog_store()
    with catalog.state_store.database.read() as connection:
        lineage_ids = _current_lineage_ids(connection)
    for lineage_id in lineage_ids:
        if not isa_migration_scope.is_deferred(lineage_id):
            _migrate_lineage(lineage_id)
    resident_runtime_domain.migrate_instruction_names()
    store.migrate_instruction_names()
    family_strategy_state.retire_legacy_dispatches()
