"""Transient Resident Execution preparation and delivery progress.

Messages belong to the Mailbox and completed execution facts to Agent storage.
"""
from __future__ import annotations

from copy import deepcopy

from management_common.json_columns import json_text, json_value


COLUMNS = ("resident_id", "revision", "execution_id", "input_message_id", "event_cursor", "preparation_json", "progress_json")
PREPARATION = frozenset({
    "input_message", "raw_message",
    "effective_message", "actual_sent_prompt", "prompt_cwd", "context_refs", "input_media",
    "enqueued_at", "additional_skills", "runtime_context", "allow_subagents",
    "agent_data_fork", "compact_session_id", "initialization_recovery", "initial_compaction_operation_id",
    "compact_scope_label", "fork_context_input", "business_ref", "mailbox_members",
    "execution_request", "prompt_builder_version", "prompt_built_at",
    "authorization_prompt", "filesystem_scope",
    "bootstrap_session", "working_directory",
})
PROGRESS = frozenset({
    "actor", "activation_policy", "operation",
    "execution_acknowledged", "submitted_at", "intake_admitted_at", "continuation_id", "continuation_started_at", "started_at",
    "dispatched_at", "updated_at", "finished_at", "outcome", "error", "runtime_error",
    "execution_terminal_status", "stop_requested_at", "stop_requested_by", "stopped_at", "stopped_by",
    "completion_applied_at", "completion_applied_execution_id", "failure_message_id",
    "failure_message_persisted_at", "conversation_projection_intents", "conversation_projection_error",
    "conversation_projection_quarantine", "pending_active_turn_inputs", "pending_active_turn_settings",
    "send_now_inputs", "previous_execution_failure",
    "project_creation_applied_at", "project_creation_applied_execution_id",
})
DERIVED = frozenset({"schema", "schema_version", "resident_id", "lineage_id", "backend", "folder_schema",
                     "intake_state", "message", "queue", "status", "queue_reason"})
IDENTITY = frozenset({"execution_id", "user_message_id", "execution_event_cursor"})
EXECUTION_PREPARATION = frozenset({"execution_request",
                              "authorization_prompt", "filesystem_scope", "fork_context_input"})
QUEUE_INPUT_FIELDS = (PREPARATION - EXECUTION_PREPARATION) | frozenset({
    "user_message_id", "actor", "activation_policy", "operation", "message",
})
TERMINAL = frozenset({"completed", "failed", "cancelled"})
RECOVERY_INDEXES = {
    "resident_failed_execution_idx": "ON resident_execution_work(resident_id) WHERE progress_json::jsonb->>'outcome'='failed'",
}


def queue_input(value):
    """Immutable input preparation shared by queue promotion and requeue."""
    return {key: deepcopy(value[key]) for key in QUEUE_INPUT_FIELDS if key in value}


def encode(value):
    return json_text(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def create_schema(connection):
    connection.execute('CREATE SEQUENCE resident_execution_revision')
    connection.execute("""CREATE TABLE resident_execution_work (
        resident_id TEXT PRIMARY KEY REFERENCES residents(resident_id), revision BIGINT NOT NULL,
        execution_id TEXT NOT NULL UNIQUE, input_message_id TEXT NOT NULL,
        event_cursor BIGINT NOT NULL, preparation_json JSONB NOT NULL, progress_json JSONB NOT NULL)""")

    connection.execute("CREATE TABLE resident_execution_release_work (execution_id TEXT PRIMARY KEY, resident_id TEXT NOT NULL, actor TEXT NOT NULL)")
    for name, definition in RECOVERY_INDEXES.items():
        connection.execute(f"CREATE INDEX {name} {definition}")


def pack(value):
    unknown = set(value) - PREPARATION - PROGRESS - DERIVED - IDENTITY
    if unknown:
        raise ValueError('Unknown Resident execution fields: ' + ', '.join(sorted(unknown)))
    preparation = {key: deepcopy(value[key]) for key in PREPARATION if key in value}
    progress = {key: deepcopy(value[key]) for key in PROGRESS if key in value}
    # An admitted input is not a sent prompt. Preserve its message as the
    # effective input until execution prepares the actual provider prompt.
    if 'actual_sent_prompt' not in preparation and 'effective_message' not in preparation and value.get('message'):
        preparation['effective_message'] = value['message']
    request = preparation.get('execution_request') or {}
    if preparation.get('actual_sent_prompt') == request.get('prompt'):
        preparation.pop('actual_sent_prompt', None)
    if preparation.get('effective_message') == (preparation.get('actual_sent_prompt') or request.get('prompt')):
        preparation.pop('effective_message', None)
    return (str(value.get('execution_id') or ''), str(value.get('user_message_id') or ''),
            int(value.get('execution_event_cursor') or 0), encode(preparation), encode(progress))


def unpack(row, *, content=True):
    value = {key: value for key, value in json_value(row['progress_json']).items() if key in PROGRESS}
    if content:
        value.update({key: value for key, value in json_value(row['preparation_json']).items() if key in PREPARATION})
        prompt = value.get('actual_sent_prompt') or (value.get('execution_request') or {}).get('prompt')
        if prompt is not None:
            value['message'] = value['actual_sent_prompt'] = prompt
            value.setdefault('effective_message', prompt)
        else:
            value['message'] = value.get('effective_message') or value.get('raw_message') or ''
    if row['input_message_id']:
        value['user_message_id'] = row['input_message_id']
    if row['execution_id']:
        value['execution_id'] = row['execution_id']
    if row['event_cursor']:
        value['execution_event_cursor'] = row['event_cursor']
    return value


def pending(value):
    return bool(value.get('execution_id')) and (
        (value.get('operation') == 'context_fork_create' and value.get('outcome') in {'failed', 'cancelled'})
        or not (value.get('outcome') in TERMINAL or value.get('finished_at'))
        or (value.get('submitted_at') and not value.get('execution_acknowledged'))
        or any(value.get(key) for key in ('conversation_projection_intents', 'pending_active_turn_inputs', 'pending_active_turn_settings'))
    )


def read(connection, resident_ids, *, content=True):
    fields = 't.*' if content else ','.join('t.' + key for key in COLUMNS if key != 'preparation_json')
    rows = connection.execute(
        f'SELECT {fields},r.backend,r.intake_open,n.lineage_id FROM resident_execution_work t '
        'JOIN residents r USING(resident_id) LEFT JOIN lineage_nodes n ON n.node_id=t.resident_id '
        'WHERE t.resident_id=ANY(%s)', (list(resident_ids),),
    ).fetchall()
    result = {}
    for row in rows:
        value = unpack(row, content=content)
        value.update(schema='tfi.resident-execution.v1', schema_version=1, resident_id=row['resident_id'],
                     lineage_id=row['lineage_id'] or '', backend=row['backend'] or '', folder_schema='lineage-folder-v3',
                     intake_state='closed' if row['intake_open'] is False else 'open')
        result[row['resident_id']] = (int(row['revision']), value)
    return result


def receipt(value):
    return {key: deepcopy(item) for key,item in value.items()
            if key in PROGRESS | IDENTITY | {'actor','operation','activation_policy','business_ref'}}


def put(connection, resident_id, revision, value):
    values = pack(value)
    row = connection.execute('SELECT * FROM resident_execution_work WHERE resident_id=%s', (resident_id,)).fetchone()
    if row is None:
        connection.execute('INSERT INTO resident_execution_work VALUES (%s,%s,%s,%s,%s,%s,%s)', (resident_id,revision,*values))
        return
    # Event polling writes the cursor and small progress only. An unchanged
    # prompt/request never travels through UPDATE or gets re-toasted.
    changes = {column: item for column,item in zip(COLUMNS[2:],values) if row[column] != item}
    changes['revision'] = revision
    connection.execute('UPDATE resident_execution_work SET '+','.join(f'{column}=%s' for column in changes)+' WHERE resident_id=%s', (*changes.values(),resident_id))
