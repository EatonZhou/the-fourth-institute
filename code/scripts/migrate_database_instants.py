"""One-time, transactional conversion of declared absolute times.

The manifest is explicit: durations, provider payloads and sequence counters are
excluded. Numeric source values are seconds (including identity/Passkey and
search integers); no unit is guessed from magnitude. Optional zero sentinels
are declared separately. Run only during a coordinated writer cutover.
"""
from __future__ import annotations

from psycopg import sql

# Owned table -> absolute-time columns. All persisted numeric times in these
# owners were verified as Unix seconds against the writers and existing data.
COLUMNS = {
    "agent.agent_operations": "created_at",
    "agent.agents": "updated_at",
    "agent.chat_message_positions": "created_at",
    "agent.execution_inputs": "accepted_at",
    "agent.executions": "started_at finished_at created_at",
    "agent.payloads": "created_at",
    "agent.provider_bindings": "context_injected_at created_at updated_at",
    "agent.provider_sessions": "created_at updated_at",
    "agent.segment_relations": "created_at",
    "agent.segments": "created_at started_at finished_at",
    "agent.sequences": "created_at updated_at",
    "article.build_settings": "updated_at",
    "chatrooms.rooms": "created_at updated_at",
    "execution_account_usage.accounts": "observed_at",
    "execution_account_usage.daily_usage": "observed_at",
    "execution_runtime.capacity_dispatch_state": "last_dispatched_at next_dispatch_at updated_at",
    "execution_runtime.capacity_settings": "updated_at",
    "execution_runtime.execution_account_usage_hourly": "bucket_hour updated_at",
    "execution_runtime.execution_actor_dispatch_state": "last_dispatched_at updated_at",
    "execution_runtime.execution_input_controls": "created_at updated_at",
    "execution_runtime.execution_token_usage_events": "occurred_at",
    "execution_runtime.execution_workers": "heartbeat_at updated_at",
    "execution_runtime.executions": "lease_expires_at acknowledged_at created_at updated_at available_at",
    "harness_auto_policy.harness_auto_policy_state": "updated_at",
    "harness_effectiveness.harness_quality_labels": "updated_at",
    "harness_effectiveness.harness_strategy_observations": "recorded_at",
    "harness_evolution.harness_policy_cells": "challenger_since updated_at",
    "harness_evolution.harness_policy_events": "recorded_at",
    "harness_evolution.harness_turn_calls": "recorded_at",
    "harness_evolution.maintenance_runs": "created_at started_at finished_at",
    "lineage.conversation_events": "occurred_at",
    "lineage.conversation_input_states": "updated_at",
    "lineage.effect_recovery_cleanup": "created_at",
    "lineage.families": "deleted_at",
    "lineage.function_blocks": "created_at updated_at",
    "lineage.lineage_edges": "deleted_at",
    "lineage.lineage_fragments": "created_at updated_at",
    "lineage.lineage_nodes": "deleted_at",
    "lineage.lineages": "created_at updated_at graph_updated_at layout_updated_at deleted_at runtime_updated_at",
    "lineage.resident_derivations": "forked_from_at",
    "lineage.resident_lifecycle_operations": "available_at",
    "lineage.resident_mailbox": "accepted_at updated_at",
    "lineage.resident_observation_cache": "last_token_usage_at last_turn_usage_at last_interaction_at",
    "lineage.resident_resource_projection_outbox": "enqueued_at available_at lease_until",
    "lineage.resident_sessions": "context_compacted_at context_reset_at",
    "lineage.residents": "created_at updated_at deleted_at",
    "lineage.trigger_path_steps": "created_at started_at finished_at",
    "lineage.trigger_paths": "occurred_at finished_at",
    "model_apis.apis": "updated_at",
    "model_apis.relay_resources": "updated_at",
    "portal_identity.agent_tokens": "expires_at",
    "portal_identity.login_attempts": "expires_at",
    "portal_identity.passkey_credentials": "created_at last_used_at",
    "portal_identity.password_reset_tokens": "created_at expires_at",
    "portal_identity.two_factor_users": "enabled_at",
    "portal_identity.user_access_tokens": "created_at expires_at",
    "portal_identity.user_accounts": "created_at updated_at",
    "portal_identity.user_invitations": "created_at expires_at accepted_at",
    "portal_notice.featured_posts": "featured_at",
    "portal_notice.posts": "created_at updated_at edited_at pinned_at",
    "portal_notice.read_receipts": "read_at",
    "portal_notice.replies": "created_at",
    "portal_pulse.publications": "published_at",
    "portal_ui.associations": "updated_at",
    "portal_ui.mailbox_todos": "created_at updated_at",
    "portal_wall.wall_jobs": "created_at completed_at updated_at",
    "portal_wall.wall_resident_cleanup": "retry_at",
    "resident_context.embedding_jobs": "available_at created_at updated_at",
    "resident_context.resident_embeddings": "updated_at",
    "resident_context.residents": "discovered_at updated_at last_checked_at",
    "resident_context.routing_eval_cases": "created_at",
    "resident_context.routing_eval_submissions": "created_at",
    "resident_context.service_state": "updated_at",
    "resident_context.snapshots": "rebuilt_at updated_at",
    "resident_context.summary_jobs": "available_at created_at updated_at lease_until",
    "resident_context.work_summaries": "updated_at",
    "resource_dev.resource_registrations": "created_at",
    "resource_release.resource_registrations": "created_at",
    "script_runs.runs": "started_at finished_at",
    "script_runs.worker_lease": "until_at",
    "semantic_search.jobs": "created_at updated_at refreshed_at",
    "templates.installed_builtins": "installed_at",
    "templates.templates": "created_at updated_at",
    "workbenches.apps": "created_at updated_at",
    "workbenches.generations": "created_at input_updated_at applied_at",
    "workbenches.versions": "updated_at",
    "workspace.workspaces": "created_at updated_at deleted_at",
    "telepus.attachment_deliveries": "created_at updated_at expires_at",
    "telepus.audit_events": "created_at",
    "telepus.callback_tokens": "created_at expires_at",
    "telepus.channel_state": "updated_at",
    "telepus.chat_state": "created_at updated_at",
    "telepus.conversations": "created_at updated_at",
    "telepus.exclusive_lease": "acquired_at renewed_at expires_at",
    "telepus.jobs": "created_at started_at finished_at delivered_at",
    "telepus.processed_updates": "processed_at",
    "telepus.recommendation_requests": "created_at updated_at",
    "telepus.session_history": "rotated_at",
}
for owner in ("execution_instructions", "lineage", "resource_instructions", "web_instructions"):
    COLUMNS[owner + ".instruction_executions"] = "created_at updated_at"
    COLUMNS[owner + ".instruction_retry_guards"] = "expired_at"

ZERO_IS_ABSENT = {
    "portal_identity.passkey_credentials.last_used_at",
    "harness_evolution.harness_policy_cells.challenger_since",
    "execution_runtime.executions.lease_expires_at",
    "lineage.resident_resource_projection_outbox.lease_until",
}
# NULL means not known/not yet occurred. Other columns keep their NOT NULL
# constraint, so an unexpected empty historical value aborts the transaction.
NULLABLE = {
    "agent.executions": "started_at finished_at",
    "agent.segments": "started_at finished_at",
    "agent.provider_bindings": "context_injected_at",
    "article.build_settings": "updated_at",
    "execution_runtime.executions": "lease_expires_at acknowledged_at",
    "harness_evolution.harness_policy_cells": "challenger_since",
    "lineage.lineages": COLUMNS["lineage.lineages"],
    "lineage.residents": COLUMNS["lineage.residents"],
    "lineage.resident_derivations": "forked_from_at",
    "lineage.resident_observation_cache": COLUMNS["lineage.resident_observation_cache"],
    "lineage.resident_sessions": COLUMNS["lineage.resident_sessions"],
    "lineage.resident_resource_projection_outbox": "enqueued_at lease_until",
    "portal_identity.passkey_credentials": "last_used_at",
    "resident_context.residents": "last_checked_at",
    "resident_context.snapshots": "rebuilt_at updated_at",
    "resident_context.summary_jobs": "lease_until",
    "resident_context.work_summaries": "updated_at",
    "templates.templates": "created_at updated_at",
    "telepus.jobs": "started_at finished_at delivered_at",
}
EPOCH_DEFAULTS = {
    "execution_runtime.capacity_dispatch_state.last_dispatched_at",
    "execution_runtime.capacity_dispatch_state.next_dispatch_at",
    "execution_runtime.executions.available_at",
    "lineage.resident_lifecycle_operations.available_at",
    "lineage.resident_resource_projection_outbox.available_at",
}
CURRENT_DEFAULTS = {
    "resource_dev.resource_registrations.created_at",
    "resource_release.resource_registrations.created_at",
}

# These registration rows were copied from SQLite CURRENT_TIMESTAMP, whose
# unzoned output is UTC (registration.py at cd731f73). No other naive source
# is accepted. New PostgreSQL writers always store a zoned instant.
LEGACY_UTC = CURRENT_DEFAULTS


def migrate(connection, *, namespace="", tables=None):
    """Alter each table once, preserve row counts and recreate the exact view.

    A missing optional owner table is reported as absent, never manufactured.
    A missing column in an existing owner is an incompatible schema and fails.
    The caller owns commit/rollback and keeps writers quiescent.
    """
    from management_portal.execution.harness_effectiveness import create_observation_view

    selected = COLUMNS if tables is None else {name: COLUMNS[name] for name in tables}
    prefix = namespace + "_" if namespace else ""
    connection.execute("SET LOCAL lock_timeout='10s'")
    connection.execute("SET LOCAL timezone='UTC'")
    view_schema = prefix + "harness_effectiveness"
    view = sql.Identifier(view_schema, "harness_observations")
    view_present = False
    if "harness_effectiveness.harness_strategy_observations" in selected:
        row = connection.execute("SELECT relkind FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                                 "WHERE n.nspname=%s AND c.relname='harness_observations'", (view_schema,)).fetchone()
        if row:
            if row[0] != "v":
                raise ValueError("Harness observations must remain a view")
            connection.execute(sql.SQL("DROP VIEW {}").format(view))
            view_present = True
    report = {}
    for name, fields in selected.items():
        owner, table = name.split(".")
        schema = prefix + owner
        target = sql.Identifier(schema, table)
        catalog = {r[0]: r[1:] for r in connection.execute(
            "SELECT column_name,data_type,is_nullable,column_default FROM information_schema.columns "
            "WHERE table_schema=%s AND table_name=%s", (schema, table))}
        if not catalog:
            report[name] = {"absent": True}
            continue
        connection.execute(sql.SQL("LOCK TABLE {} IN ACCESS EXCLUSIVE MODE").format(target))
        # A physical time-column rewrite changes xmin without changing trace
        # membership. Preserve only projections already verified as current;
        # stale projections must still be rebuilt from their source rows.
        projection = sql.Identifier(schema, 'chat_message_positions')
        current_traces = []
        if name == 'agent.sequences' and connection.execute(
            'SELECT to_regclass(%s)', (schema + '.chat_message_positions',),
        ).fetchone()[0]:
            current_traces = [r[0] for r in connection.execute(sql.SQL(
                "SELECT s.sequence_id FROM {} s JOIN {} p ON p.sequence_id=s.sequence_id "
                "WHERE p.source_kind='sync' AND p.source_id='__sync__' AND p.message_id=s.xmin::text"
            ).format(target, projection))]
        before = connection.execute(sql.SQL("SELECT count(*) FROM {}").format(target)).fetchone()[0]
        actions = []
        defaults = []
        for field in fields.split():
            if field not in catalog:
                raise ValueError(f"Missing declared time column: {name}.{field}")
            kind, nullable, default = catalog[field]
            column = sql.Identifier(field)
            full = name + "." + field
            if kind == "timestamp with time zone":
                continue
            if default is not None:
                actions.append(sql.SQL("ALTER COLUMN {} DROP DEFAULT").format(column))
            if field in NULLABLE.get(name, "").split():
                actions.append(sql.SQL("ALTER COLUMN {} DROP NOT NULL").format(column))
            value = column
            if full in ZERO_IS_ABSENT:
                value = sql.SQL("NULLIF({},0)").format(column)
            if kind in {"bigint", "integer", "double precision", "real", "numeric"}:
                value = sql.SQL("to_timestamp({}::double precision)").format(value)
            elif kind == "text":
                # Reject naive timestamps rather than depending on the server's
                # timezone. Blank is the old representation of an absent time.
                invalid = connection.execute(sql.SQL(
                    "SELECT count(*) FROM {} WHERE NULLIF(btrim({}), '') IS NOT NULL "
                    "AND btrim({}) !~ '(Z|[+-][0-9]{{2}}(:?[0-9]{{2}})?)$'"
                ).format(target, column, column)).fetchone()[0]
                if invalid and full not in LEGACY_UTC:
                    raise ValueError(f"{name}.{field}: {invalid} times lack a timezone")
                value = sql.SQL("NULLIF(btrim({}), '')::timestamptz").format(column)
            else:
                raise ValueError(f"{name}.{field}: undeclared time encoding {kind}")
            actions.append(sql.SQL("ALTER COLUMN {} TYPE TIMESTAMPTZ USING {}").format(column, value))
            if full in EPOCH_DEFAULTS:
                defaults.append(sql.SQL("ALTER COLUMN {} SET DEFAULT TIMESTAMPTZ 'epoch'").format(column))
            elif full in CURRENT_DEFAULTS:
                defaults.append(sql.SQL("ALTER COLUMN {} SET DEFAULT CURRENT_TIMESTAMP").format(column))
            elif default is not None and default not in {"''::text", "0", "0.0"}:
                raise ValueError(f"{name}.{field}: unrecognized source default")
        if actions:
            connection.execute(sql.SQL("ALTER TABLE {} ").format(target) + sql.SQL(", ").join(actions))
            if current_traces:
                connection.execute(sql.SQL(
                    "UPDATE {} p SET message_id=s.xmin::text FROM {} s "
                    "WHERE p.sequence_id=s.sequence_id AND p.source_kind='sync' "
                    "AND p.source_id='__sync__' AND s.sequence_id=ANY(%s)"
                ).format(projection, target), (current_traces,))
        if defaults:
            connection.execute(sql.SQL("ALTER TABLE {} ").format(target) + sql.SQL(", ").join(defaults))
        after = connection.execute(sql.SQL("SELECT count(*) FROM {}").format(target)).fetchone()[0]
        if before != after:
            raise RuntimeError(f"Row count changed in {name}")
        report[name] = {"rows": after, "columns": fields.split()}
    if view_present:
        connection.execute(sql.SQL("SET LOCAL search_path TO {},public").format(sql.Identifier(view_schema)))
        create_observation_view(connection)
    return report

