"""Read-only proof of retained, never-submitted Resident work.

``inspect(api, target, connection=None)`` returns a deterministic, non-sensitive
receipt or raises WorkProofError. It never authorizes service admission itself:
the installer owns stopped services, the database-client barrier and snapshot
locks. Online use is only a candidate diagnostic. With a supplied connection,
only SELECTs are issued and its transaction is neither committed nor closed.
Without one, a repeatable-read, read-only transaction is used.

Seal the receipt before migration and compare it after migration or restore,
before starting Society. The schema marker is deliberately not in the receipt:
v60..v65 migrations change configuration, not these durable work rows. This
narrow proof rejects scheduler records (even completed ones), grouped
inputs, pending delivery/continuation/restart work and unknown evidence. The
existing check_quiescent contract remains unchanged.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone


class WorkProofError(RuntimeError):
    """A public error containing no identities, prompts or database details."""


def require(condition, code):
    if not condition:
        raise WorkProofError('Retained work proof refused: ' + code)


def object_value(value):
    if isinstance(value, str):
        value = json.loads(value)
    require(isinstance(value, dict), 'invalid_object')
    return value


def canonical(value):
    if isinstance(value, datetime):
        require(value.tzinfo is not None, 'ambiguous_timestamp')
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (bytes, memoryview)):
        return {'sha256': hashlib.sha256(value).hexdigest(), 'byte_length': len(value)}
    if isinstance(value, dict):
        return {key: canonical(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [canonical(item) for item in value]
    return value


def fingerprint(rows):
    # Row order, JSONB key order and session timezone must not affect equality.
    # Only SQL columns use the *_json convention. User payload keys do not.
    normalized = [{k: json.loads(v) if k.endswith('_json') and isinstance(v, str) else v
                   for k, v in row.items()} for row in rows]
    digests = sorted(hashlib.sha256(json.dumps(canonical(row), sort_keys=True,
        separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()).digest() for row in normalized)
    return {'count': len(digests), 'sha256': hashlib.sha256(b''.join(digests)).hexdigest()}


EMPTY_TABLES = (
    'resident_execution_release_work', 'resident_delivery_work',
    'resident_lifecycle_operations', 'pending_event_deliveries', 'pending_expiry_notices',
    'effect_work', 'effect_recovery_cleanup', 'resident_creation_operations',
    'lineage_deletion_operations', 'lineage_move_operations', 'resident_workspace_transfers',
)
RETAINED_TABLES = (
    'resident_mailbox', 'resident_mailbox_progress', 'resident_execution_work',
    'conversation_input_states', 'conversation_states', 'input_scopes',
    'family_quota_migrations', 'operator_work', 'resident_sessions', 'residents', 'lineage_nodes',
    'family_residents', 'family_relays', 'family_quota_reviews',
    'trigger_paths', 'trigger_path_steps', 'trigger_path_step_edges',
    'trigger_path_execution_links', 'wire_delivery_receipts',
)
# promote_queue_item writes started_at/dispatched_at when reserving preparation;
# neither is the Provider's start (agent.executions.started_at). Submission is
# bind_execution.submitted_at. Never infer safety from the age of any timestamp.
INERT_PROGRESS = {'actor', 'activation_policy', 'intake_admitted_at', 'updated_at',
                  'started_at', 'dispatched_at'}


def _inspect(cursor):
    from scripts.tfi_release_schema import columns_for, version
    from management_portal.execution.agent_data_tables import AGENT_DATA_COLUMNS
    from management_portal.execution.scheduler_schema import EXECUTION_STORE_COLUMNS
    from management_portal.society_service.resident_execution_storage import PREPARATION, PROGRESS, QUEUE_INPUT_FIELDS

    def rows(schema, table):
        # Names come solely from owner constants, never from target/user input.
        from psycopg import sql
        return cursor.execute(sql.SQL('SELECT * FROM {}.{}').format(
            sql.Identifier(schema), sql.Identifier(table))).fetchall()

    marker = cursor.execute("SELECT value FROM lineage.lineage_meta WHERE key='schema'").fetchone()
    require(marker is not None, 'lineage_schema_missing')
    number = version(marker['value'])
    expected = {'lineage': columns_for(number), 'agent': AGENT_DATA_COLUMNS,
                'execution_runtime': EXECUTION_STORE_COLUMNS}
    actual = {}
    for column in cursor.execute("SELECT table_schema,table_name,column_name FROM information_schema.columns "
                                 "WHERE table_schema IN ('lineage','agent','execution_runtime')").fetchall():
        actual.setdefault(column['table_schema'], {}).setdefault(column['table_name'], set()).add(column['column_name'])
    for schema, tables in expected.items():
        # Execution service_settings is owned separately from Scheduler.
        observed = {k: v for k, v in actual.get(schema, {}).items()
                    if not (schema == 'execution_runtime' and k == 'service_settings')}
        require(observed == {k: set(v) for k, v in tables.items()}, 'unsupported_structure')
    for schema, table, key, value in (
        ('agent', 'agent_store_meta', 'schema', 'tfi.agent-data.v18'),
        ('execution_runtime', 'execution_store_meta', 'schema_version', '19'),
    ):
        from psycopg import sql
        declaration = cursor.execute(sql.SQL('SELECT value FROM {}.{} WHERE key=%s').format(
            sql.Identifier(schema), sql.Identifier(table)), (key,)).fetchone()
        require(declaration is not None and declaration['value'] == value, 'unsupported_schema')

    data = {table: rows('lineage', table) for table in (*EMPTY_TABLES, *RETAINED_TABLES)}
    families = rows('lineage', 'families')
    live_families = {r['family_id']: r for r in families if r['deleted_at'] is None}
    facades = {r['resident_id']: live_families[r['family_id']]['lineage_id'] for r in data['family_residents']
               if r['role'] == 'facade' and r['family_id'] in live_families}
    for table in EMPTY_TABLES:
        require(not data[table], 'pending_' + table)
    require(not rows('execution_runtime', 'executions'), 'scheduler_execution_present')
    require(not rows('execution_runtime', 'execution_input_controls'), 'scheduler_control_present')
    for row in data['operator_work']:
        require(not object_value(row['checkpoint_json']).get('pending_deliveries'), 'operator_delivery_pending')
    for row in data['family_quota_migrations']:
        migration = object_value(row['migration_json'])
        require(migration.get('status') in (None, '', 'waiting') and not migration.get('pending_restart'),
                'quota_migration_in_flight')
    for row in data['resident_sessions']:
        restart = object_value(row['session_restart_json'] or {})
        require(not restart.get('deferred'), 'deferred_restart_pending')

    states = {(r['resident_id'], r['message_id']): r for r in data['conversation_input_states']}
    active = {key: r for key, r in states.items() if r['state'] in ('queued', 'running')}
    require(all(r['state'] in ('queued', 'running', 'completed', 'failed', 'cancelled') for r in states.values()),
            'unknown_input_state')
    residents = {r['resident_id']: r for r in data['residents']}
    nodes = {r['node_id']: r for r in data['lineage_nodes']}
    mail = {(r['resident_id'], r['message_id']): r for r in data['resident_mailbox']}
    work = {r['resident_id']: r for r in data['resident_execution_work']}
    progress = {(r['lineage_id'], r['resident_id']): object_value(r['progress_json'])
                for r in data['resident_mailbox_progress']}
    executions = rows('agent', 'executions')
    preparing = {r['execution_id']: r for r in executions if r['status'] == 'preparing'}
    require(all(r['status'] in ('preparing', 'completed', 'failed', 'cancelled') for r in executions),
            'agent_execution_started_or_unknown')
    identities = list(preparing)
    # Scheduler receipt expiry cannot erase durable admission. This is the
    # same independent ledger used by execution_lifecycle.check_new_submission.
    require(not cursor.execute("SELECT 1 FROM agent.agent_operations WHERE operation_id=ANY(%s) "
        "OR data_json::jsonb->>'execution_id'=ANY(%s) LIMIT 1",
        (['execution_submission:' + eid for eid in identities], identities)).fetchone(), 'durable_execution_operation_present')
    require(not cursor.execute("SELECT 1 FROM execution_runtime.execution_store_meta WHERE key=ANY(%s) LIMIT 1",
        (['request_digest:' + eid for eid in identities],)).fetchone(), 'submission_digest_present')
    require(not cursor.execute("SELECT 1 FROM execution_runtime.execution_token_usage_events "
        "WHERE execution_id=ANY(%s) LIMIT 1", (identities,)).fetchone(), 'execution_usage_present')
    require(not cursor.execute("SELECT 1 FROM agent.segments WHERE execution_id=ANY(%s) "
        "AND segment_type<>'input' LIMIT 1", (identities,)).fetchone(), 'execution_output_present')
    references = rows('agent', 'execution_inputs')
    scopes = {(r['resident_id'], r['message_id']) for r in data['input_scopes']}
    require(len(work) == len(preparing), 'work_preparation_count_mismatch')
    consumed_work, consumed_executions = set(), set()
    facade_messages = []
    classes = {'ordinary_queue': 0, 'held_info': 0, 'facade_input': 0, 'preparing': len(preparing)}

    def message(value, mid):
        value = object_value(value)
        require(value.get('message_id') == mid and isinstance(value.get('content_parts'), list)
                and bool(value['content_parts']), 'input_envelope_missing')

    for (rid, mid), state in active.items():
        require(rid in residents and residents[rid]['deleted_at'] is None and rid in nodes
                and residents[rid]['registration_state'] == 'ready'
                and nodes[rid]['node_type'] == 'resident' and nodes[rid]['deleted_at'] is None,
                'input_resident_missing')
        require(not object_value(state['error_json']), 'input_error_present')
        if state['state'] == 'queued':
            item = mail.get((rid, mid))
            require(not any(r['message_id'] == mid for r in references), 'queued_input_already_admitted')
            require(rid not in work or work[rid]['input_message_id'] != mid, 'queued_input_is_active_work')
            if item is not None and item['place'] == 'info':
                # _hold_info persists a queued projection but no executable
                # queue item. Preserve that projection and its Mailbox owner.
                classes['held_info'] += 1
                continue
            if item is None and rid in facades:
                require(facades[rid] == nodes[rid]['lineage_id'] and rid not in work, 'facade_work_identity_mismatch')
                # accept_for_router/show_request commits the facade request to
                # its own trace. A facade never owns a model execution. Retain
                # the exact trace linkage, envelope and selected payload bytes.
                traced = cursor.execute("SELECT s.*,a.agent_id AS trace_owner,a.trace_sequence_id,"
                    "ss.ordinal AS trace_ordinal,cp.source_id AS position_source,cp.message_id AS position_message,"
                    "p.sha256 AS payload_sha256,p.byte_length AS payload_length,p.body AS payload_body "
                    "FROM agent.agents a JOIN agent.sequence_segments ss ON ss.sequence_id=a.trace_sequence_id "
                    "JOIN agent.segments s ON s.segment_id=ss.segment_id "
                    "JOIN agent.chat_message_positions cp ON cp.sequence_id=ss.sequence_id AND cp.source_id=s.segment_id "
                    "AND cp.ordinal=ss.ordinal AND cp.source_kind='segment' "
                    "JOIN agent.payloads p ON p.payload_ref=s.content_ref "
                    "WHERE a.agent_id=%s AND cp.message_id=%s", (rid, mid)).fetchall()
                require(len(traced) == 1, 'facade_original_trace_missing')
                trace = traced[0]
                facts = object_value(trace['metadata_json']).get('message', {})
                require(trace['segment_type'] == 'input' and not trace['execution_id']
                        and trace['created_by_agent_id'] == rid and facts.get('message_id') == mid
                        and facts.get('role') == 'user' and facts.get('created_at')
                        and isinstance(facts.get('parts'), list) and bool(facts['parts']), 'facade_original_envelope_invalid')
                require(trace['payload_length'] == len(trace['payload_body']) > 0
                        and trace['content_sha256'] == trace['payload_sha256']
                        == hashlib.sha256(trace['payload_body']).hexdigest(), 'facade_original_payload_invalid')
                facade_messages.append(trace)
                classes['facade_input'] += 1
                continue
            require(item is not None and item['place'] == 'queue' and not item['group_id'], 'queued_mailbox_mismatch')
            payload = object_value(item['item_json'])
            require(set(payload) <= QUEUE_INPUT_FIELDS | {'input_message_in_mailbox'}, 'unknown_queue_evidence')
            require(payload.get('user_message_id') == mid and payload.get('actor') and not payload.get('operation'),
                    'queued_identity_mismatch')
            # Queue preparation has no admitted Execution yet.
            message(item['message_json'], mid)
            classes['ordinary_queue'] += 1
            continue
        job = work.get(rid)
        require(job is not None and job['input_message_id'] == mid and job['event_cursor'] == 0,
                'running_work_mismatch')
        require(rid not in consumed_work, 'multiple_running_inputs')
        eid = job['execution_id']
        execution = preparing.get(eid)
        require(execution is not None and execution['agent_id'] == rid and not execution['started_at']
                and not execution['finished_at'] and not execution['provider_turn_id']
                and not execution['final_message_id'] and not object_value(execution['usage_json']),
                'execution_not_pristine_preparing')
        p = object_value(job['progress_json'])
        require(set(p) <= PROGRESS, 'unknown_progress_evidence')
        require(p.get('actor') == execution['actor_ref'] and bool(p.get('actor')), 'actor_mismatch')
        require(all(key in INERT_PROGRESS or value in (None, '', False, {}, []) for key, value in p.items()),
                'submission_continuation_or_projection_evidence')
        require(p.get('activation_policy', 'enqueue_and_trigger') == 'enqueue_and_trigger', 'unsupported_activation')
        refs = [r for r in references if r['execution_id'] == eid]
        require(len(refs) == 1 and refs[0]['message_id'] == mid and refs[0]['role'] == 'primary', 'execution_input_mismatch')
        prep = object_value(job['preparation_json'])
        require(set(prep) <= PREPARATION, 'unknown_preparation_evidence')
        # bootstrap_session is the ordinary first-turn preparation flag, not
        # a submission receipt. Its execution still needs every pristine check
        # above; fork/compact operations remain outside this proof contract.
        require(prep.get('bootstrap_session') is None or isinstance(prep['bootstrap_session'], bool),
                'invalid_bootstrap_flag')
        require(not any(prep.get(k) for k in ('execution_request', 'agent_data_fork',
                    'compact_session_id', 'initial_compaction_operation_id')), 'prepared_operation_present')
        message(prep.get('input_message'), mid)
        require(not prep.get('mailbox_members') and (rid, mid) not in scopes, 'unsupported_scoped_or_group_input')
        checkpoints = progress.get((nodes[rid]['lineage_id'], rid), {}).get('checkpoints', [])
        matches = [c for c in checkpoints if c.get('message_id') == mid]
        require(len(matches) == 1 and matches[0].get('execution_id') == eid
                and isinstance(matches[0].get('item'), dict)
                and matches[0]['item'].get('user_message_id') == mid and not matches[0].get('infos'),
                'checkpoint_mismatch')
        message(matches[0]['item'].get('input_message'), mid)
        consumed_work.add(rid)
        consumed_executions.add(eid)
    require(consumed_work == set(work) and consumed_executions == set(preparing), 'orphan_preparation')
    for key, row in mail.items():
        if row['place'] == 'info':
            # Held info is durable background context, not an executable input.
            # It has no active input state or execution admission of its own.
            require((key not in active or active[key]['state'] == 'queued')
                    and not row['group_id'] and row['resident_id'] in residents
                    and residents[row['resident_id']]['deleted_at'] is None, 'held_info_identity_mismatch')
            require(set(object_value(row['item_json'])) <= {'held_at'}, 'unknown_held_info_evidence')
            require(not any(r['message_id'] == row['message_id'] and r['execution_id'] in preparing
                            for r in references), 'held_info_already_admitted')
            message(row['message_json'], row['message_id'])
            continue
        require(key in active and ((row['place'] == 'queue' and active[key]['state'] == 'queued')
                or (row['place'] == 'pending' and active[key]['state'] == 'running')),
                'unclassified_mailbox_work')
    for row in data['conversation_states']:
        projection = object_value(row['state_json'])
        mid = projection.get('active_message_id')
        if mid:
            require(row['resident_id'] in work and work[row['resident_id']]['input_message_id'] == mid,
                    'active_projection_mismatch')
    # Hash exactly the retained work owners, including the complete checkpoint
    # and message JSON, never return bodies/identities or hash full asset stores.
    retained = {name: fingerprint(data[name]) for name in RETAINED_TABLES}
    retained['agent.executions'] = fingerprint(executions)
    retained['agent.execution_inputs'] = fingerprint(references)
    retained['agent.facade_originals'] = fingerprint(facade_messages)
    for table, introduced in (('family_task_dispatches', 61), ('family_chat_preferences', 65), ('family_chat_inputs', 65)):
        retained[table] = fingerprint(rows('lineage', table) if number >= introduced else [])
    # Migration adds Family configuration columns; the original v60 fields
    # (including identity, routing and membership) must remain byte-equivalent.
    retained['families'] = fingerprint([{k: row[k] for k in columns_for(60)['families']} for row in families])
    return {'schema': 'tfi.application-work-proof.v1',
            'counts': {'queued': sum(r['state'] == 'queued' for r in active.values()),
                       'preparing': len(preparing)}, 'classes': classes, 'retained': retained}


def inspect(api, target, connection=None):
    """Inspect target DSN, or the installer's existing locked transaction."""
    import psycopg
    from psycopg.rows import dict_row
    try:
        if connection is not None:
            with connection.cursor(row_factory=dict_row) as cursor:
                return _inspect(cursor)
        with psycopg.connect(api.clean_environment(target)['PORTAL_DATABASE_URL']) as owned:
            owned.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            with owned.cursor(row_factory=dict_row) as cursor:
                return _inspect(cursor)
    except WorkProofError:
        raise
    except Exception:
        # Never leak DSNs, SQL parameters, malformed JSON or input text into
        # the installer's shareable receipt. Failure always denies admission.
        raise WorkProofError('Retained work proof refused: unreadable_or_unsupported_evidence') from None
