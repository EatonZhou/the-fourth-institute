"""Offline waiting-work evidence using only the disposable pytest PostgreSQL.

``seed_waiting(connection)`` is also the synthetic VM fixture constructor:
initialize the real three domain schemas first, then invoke it in a write
transaction. It creates no provider credentials and calls no model. It is a
storage proof fixture; real recovery semantics are tested separately.
"""
import json
import hashlib
import os
from types import SimpleNamespace

import psycopg
from psycopg.types.json import Jsonb
import pytest

from scripts import tfi_application_work as work

STAMP = '2026-10-01T00:00:00Z'


class SchemaConnection:
    def __init__(self, connection):
        from management_common.postgres_database import _portal_row_factory
        self.cursor = connection.cursor(row_factory=_portal_row_factory)

    def execute(self, *args):
        return self.cursor.execute(*args)

    def executescript(self, sql):
        self.cursor.execute(sql)


def initialize(connection, number=60):
    from management_portal.society_service.state_store import create_lineage_data_schema
    from management_portal.execution.agent_data_tables import initialize_agent_data
    from management_portal.execution.scheduler_schema import initialize_execution_store
    from tests.cases.test_release_schema import origin
    c = SchemaConnection(connection)
    for schema, create in (('lineage', create_lineage_data_schema), ('agent', initialize_agent_data),
                           ('execution_runtime', initialize_execution_store)):
        c.execute('CREATE SCHEMA ' + schema)
        c.execute('SET LOCAL search_path TO ' + schema)
        create(c)
    c.execute('SET LOCAL search_path TO lineage')
    origin(c, number)


def seed_waiting(connection):
    """Synthetic four queued/two pre-submit inputs, stable IDs and checkpoints."""
    c = connection.cursor()
    from psycopg import sql
    workspace_table = c.execute("SELECT n.nspname,t.relname FROM pg_constraint k "
        "JOIN pg_class t ON t.oid=k.confrelid JOIN pg_namespace n ON n.oid=t.relnamespace "
        "WHERE k.conrelid='lineage.residents'::regclass AND k.conname='residents_owned_workspace_fk'").fetchone()
    for i in range(6):
        rid, mid, eid = f'resident_{i:012x}', f'msg_{i:032x}', f'execution-{i:024x}'
        envelope = {'message_id': mid, 'role': 'user',
                    'content_parts': [{'type': 'text', 'text': 'synthetic input ' + str(i)}]}
        item = {'user_message_id': mid, 'actor': 'fixture_user', 'input_message': envelope,
                'raw_message': 'synthetic input ' + str(i), 'enqueued_at': STAMP}
        c.execute(sql.SQL("INSERT INTO {}(workspace_id,path,title,created_at,updated_at) VALUES(%s,%s,'fixture',%s,%s)")
                  .format(sql.Identifier(*workspace_table)),
                  (f'workspace_{i:012x}', f'/synthetic/application-proof/{i}', STAMP, STAMP))
        c.execute("INSERT INTO lineage.residents(resident_id,registration_state,intake_open,owned_workspace_id) VALUES(%s,'ready',true,%s)",
                  (rid, f'workspace_{i:012x}'))
        c.execute("INSERT INTO lineage.lineage_nodes(node_id,lineage_id,node_type) VALUES(%s,'kept','resident')", (rid,))
        c.execute("INSERT INTO lineage.conversation_states(resident_id) VALUES(%s)", (rid,))
        state = 'queued' if i < 4 else 'running'
        c.execute("INSERT INTO lineage.conversation_input_states VALUES(%s,%s,%s,'{}',%s)", (rid, mid, state, STAMP))
        if i < 4:
            c.execute("INSERT INTO lineage.resident_mailbox(resident_id,message_id,place,accepted_at,updated_at,message_json,item_json) "
                      "VALUES(%s,%s,'queue',%s,%s,%s,%s)", (rid, mid, STAMP, STAMP, Jsonb(envelope), Jsonb(item)))
        else:
            c.execute("INSERT INTO agent.executions(execution_id,agent_id,actor_ref,provider,provider_turn_id,status,usage_json,created_at) "
                      "VALUES(%s,%s,'fixture_user','codex','','preparing','{}',%s)", (eid, rid, STAMP))
            c.execute("INSERT INTO agent.execution_inputs VALUES(%s,%s,'primary',%s)", (eid, mid, STAMP))
            c.execute("INSERT INTO lineage.resident_execution_work VALUES(%s,1,%s,%s,0,%s,%s)",
                      (rid, eid, mid, Jsonb({'input_message': envelope, 'raw_message': item['raw_message']}),
                       Jsonb({'actor': 'fixture_user', 'activation_policy': 'enqueue_and_trigger',
                              'started_at': STAMP, 'dispatched_at': STAMP, 'intake_admitted_at': STAMP})))
            checkpoint = {'message_id': mid, 'execution_id': eid, 'started_at': STAMP, 'item': item, 'infos': []}
            c.execute("INSERT INTO lineage.resident_mailbox_progress VALUES('kept',%s,%s)",
                      (rid, Jsonb({'checkpoints': [checkpoint]})))
            c.execute("INSERT INTO lineage.family_quota_migrations VALUES('kept',%s,%s)",
                      (rid, Jsonb({'status': 'waiting', 'reason': 'arbitrary text, not evidence', 'attempts': 0})))


@pytest.fixture
def database():
    dsn = os.environ['PORTAL_POSTGRES_TEST_DSN']
    with psycopg.connect(dsn) as c:
        initialize(c)
        seed_waiting(c)
    yield dsn, SimpleNamespace(clean_environment=lambda _: {'PORTAL_DATABASE_URL': dsn})
    with psycopg.connect(dsn) as c:
        if c.execute("SELECT to_regclass('tfi_migration_archive.lineage_run_as')").fetchone()[0]:
            c.execute("DELETE FROM tfi_migration_archive.lineage_run_as WHERE source_schema='lineage'")
        c.execute('DROP SCHEMA lineage,agent,execution_runtime CASCADE')


def test_waiting_proof_is_read_only_private_and_equal_across_real_migration(database):
    from scripts.tfi_release_schema import upgrade
    dsn, api = database
    before = work.inspect(api, {})
    assert before['counts'] == {'queued': 4, 'preparing': 2}
    public = json.dumps(before)
    assert not any(secret in public for secret in ('synthetic input', 'fixture_user', 'arbitrary text', 'msg_0000', 'resident_0000'))
    with psycopg.connect(dsn) as c:
        c.execute('SET TRANSACTION READ ONLY')
        assert work.inspect(None, None, connection=c) == before
    with psycopg.connect(dsn) as c:
        c.execute('SET LOCAL search_path TO lineage')
        upgrade(SchemaConnection(c))
    assert work.inspect(api, {}) == before
    with psycopg.connect(dsn) as c:
        c.execute("SET LOCAL timezone TO 'Asia/Hong_Kong'")
        assert work.inspect(None, None, connection=c) == before


def creation_receipt(connection, payload=None):
    connection.execute("INSERT INTO lineage.resident_creation_operations VALUES('kept','fixture-create',%s)",
        (Jsonb({'resident_id': 'resident_000000000000'} if payload is None else payload),))


def test_completed_creation_receipt_is_retained_across_migration(database):
    from scripts.tfi_release_schema import upgrade
    dsn, api = database
    empty = work.inspect(api, {})
    with psycopg.connect(dsn) as c:
        creation_receipt(c)
    before = work.inspect(api, {})
    assert before['retained']['resident_creation_operations']['count'] == 1
    assert before != empty
    assert 'fixture-create' not in json.dumps(before)
    with psycopg.connect(dsn) as c:
        c.execute('SET LOCAL search_path TO lineage')
        upgrade(SchemaConnection(c))
    assert work.inspect(api, {}) == before
    with psycopg.connect(dsn) as c:
        c.execute("UPDATE lineage.resident_creation_operations SET creation_key='another-request'")
    changed_key = work.inspect(api, {})
    assert changed_key != before
    with psycopg.connect(dsn) as c:
        c.execute("UPDATE lineage.resident_creation_operations SET operation_json=%s",
                  (Jsonb({'resident_id': 'resident_000000000001'}),))
    assert work.inspect(api, {}) != changed_key


@pytest.mark.parametrize('payload', [
    {}, {'resident_id': ''}, {'resident_id': 7}, {'resident_id': 'missing'},
    {'resident_id': 'resident_000000000000', 'status': 'completed'},
])
def test_unproven_creation_receipt_refuses(database, payload):
    dsn, api = database
    with psycopg.connect(dsn) as c:
        creation_receipt(c, payload)
    with pytest.raises(work.WorkProofError, match='creation_'):
        work.inspect(api, {})


@pytest.mark.parametrize('mutation', [
    "UPDATE lineage.residents SET registration_state='resource_pending' WHERE resident_id='resident_000000000000'",
    "UPDATE lineage.residents SET deleted_at=now() WHERE resident_id='resident_000000000000'",
    "UPDATE lineage.lineage_nodes SET node_type=NULL,deleted_at=now() WHERE node_id='resident_000000000000'",
    "UPDATE lineage.lineage_nodes SET lineage_id='other' WHERE node_id='resident_000000000000'",
    "INSERT INTO lineage.resident_resource_projection_outbox(resident_id,revision,payload_json,enqueued_at,attempt_count,available_at,lease_token,last_error) VALUES('resident_000000000000',1,'{}',now(),0,now(),'','')",
])
def test_creation_identity_and_publication_must_be_complete(database, mutation):
    dsn, api = database
    with psycopg.connect(dsn) as c:
        creation_receipt(c)
        c.execute("INSERT INTO lineage.lineages(lineage_id,title) VALUES('other','Other fixture')")
        c.execute(mutation)
    with pytest.raises(work.WorkProofError, match='creation_'):
        work.inspect(api, {})


@pytest.mark.parametrize('key,value', [
    ('submitted_at', STAMP), ('execution_acknowledged', True), ('continuation_id', 'waiting-thread'),
    ('continuation_started_at', STAMP), ('outcome', 'failed'),
    ('conversation_projection_intents', {'pending': {}}), ('conversation_projection_error', 'failed'),
    ('conversation_projection_quarantine', {'unknown': True}), ('pending_active_turn_inputs', [{}]),
    ('future_submission', False),
])
def test_submission_continuation_projection_and_unknown_progress_refuse(database, key, value):
    dsn, api = database
    with psycopg.connect(dsn) as c:
        c.execute("UPDATE lineage.resident_execution_work SET progress_json=progress_json || %s", (Jsonb({key: value}),))
    with pytest.raises(work.WorkProofError):
        work.inspect(api, {})


@pytest.mark.parametrize('sql', [
    "UPDATE lineage.resident_execution_work SET event_cursor=1",
    "UPDATE agent.executions SET started_at=now()",
    "UPDATE agent.executions SET status='queued'",
    "UPDATE agent.executions SET provider_turn_id='confirmed'",
    "UPDATE lineage.resident_mailbox_progress SET progress_json='{}'",
    "UPDATE lineage.resident_execution_work SET input_message_id='missing'",
    "DELETE FROM agent.execution_inputs",
    "DELETE FROM lineage.resident_mailbox",
    "UPDATE lineage.resident_mailbox SET place='info'",
    "UPDATE lineage.family_quota_migrations SET migration_json='{\"status\":\"migrating\"}'",
    "UPDATE lineage.family_quota_migrations SET migration_json='{\"status\":\"waiting\",\"pending_restart\":{\"resume\":true}}'",
    "ALTER TABLE lineage.resident_execution_work ADD COLUMN unknown_evidence text",
    "UPDATE lineage.resident_execution_work SET preparation_json=preparation_json || '{\"bootstrap_session\":\"running\"}'",
])
def test_incomplete_or_ambiguous_evidence_refuses(database, sql):
    dsn, api = database
    with psycopg.connect(dsn) as c:
        c.execute(sql)
    with pytest.raises(work.WorkProofError):
        work.inspect(api, {})


@pytest.mark.parametrize('mutation,code', [
    ({'status': 'migrating', 'pending_restart': None}, 'quota_migration_in_flight'),
    ({'status': 'future'}, 'quota_migration_in_flight'),
    ({'pending_restart': {'idempotency_key': 'kept-intent'}}, 'quota_migration_in_flight'),
    ({'pending_restart': {}}, 'quota_migration_in_flight'),
    ({'pending_restart': False}, 'quota_migration_in_flight'),
    ({'future_intent': False}, 'unknown_quota_evidence'),
    ({'attempts': True}, 'invalid_quota_evidence'),
])
def test_unavailable_cannot_hide_restart_or_unknown_evidence(database, mutation, code):
    dsn, api = database
    incident = {'status': 'unavailable', 'epoch': 'fixture-epoch', 'attempts': 0,
                'tried_accounts': [], 'checked_at': 123, 'owner_notified': True,
                'reason': 'arbitrary text, not evidence'}
    incident.update(mutation)
    with psycopg.connect(dsn) as c:
        c.execute('UPDATE lineage.family_quota_migrations SET migration_json=%s', (Jsonb(incident),))
    with pytest.raises(work.WorkProofError, match=code):
        work.inspect(api, {})


@pytest.mark.parametrize('mutation,code', [
    ("INSERT INTO lineage.resident_lifecycle_operations VALUES('unfinished','resident_000000000004',"
     "'{\"status\":\"failed\",\"next_step\":1,\"steps\":[{\"kind\":\"session_restart\"},{\"kind\":\"execution_ack\"}]}',now(),'')",
     'pending_resident_lifecycle_operations'),
    ("INSERT INTO lineage.resident_sessions(resident_id,session_restart_json) VALUES('resident_000000000004',"
     "'{\"status\":\"accepted\",\"phase\":\"creating_session\"}')", 'session_restart_unsettled'),
    ("INSERT INTO lineage.resident_sessions(resident_id,session_restart_json) VALUES('resident_000000000004',"
     "'{\"status\":\"completed\",\"deferred\":{\"phase\":\"waiting\"}}')", 'deferred_restart_pending'),
    ("UPDATE lineage.resident_execution_work SET progress_json=progress_json || '{\"submitted_at\":\"recorded\"}'",
     'submission_continuation_or_projection_evidence'),
    ("UPDATE lineage.family_quota_migrations SET resident_id='missing' WHERE resident_id='resident_000000000004'",
     'quota_resident_identity_mismatch'),
])
def test_unavailable_still_requires_independent_work_proof(database, mutation, code):
    dsn, api = database
    with psycopg.connect(dsn) as c:
        c.execute('UPDATE lineage.family_quota_migrations SET migration_json=%s', (Jsonb({
            'status': 'unavailable', 'epoch': 'fixture-epoch', 'attempts': 0,
            'tried_accounts': [], 'checked_at': 123}),))
        c.execute(mutation)
    with pytest.raises(work.WorkProofError, match=code):
        work.inspect(api, {})


def previous_failure(connection, *, status='failed', finished=True, same_agent=True, record=None):
    eid, rid = connection.execute("SELECT execution_id,agent_id FROM agent.executions "
                                  "WHERE status='preparing' ORDER BY execution_id LIMIT 1").fetchone()
    other = connection.execute("SELECT agent_id FROM agent.executions WHERE agent_id<>%s LIMIT 1", (rid,)).fetchone()[0]
    connection.execute("INSERT INTO agent.executions(execution_id,agent_id,actor_ref,provider,provider_turn_id,status,"
                       "usage_json,created_at,started_at,finished_at) VALUES('execution-failed-before',%s,'fixture_user',"
                       "'codex','',%s,'{}',now(),now(),%s)",
                       (rid if same_agent else other, status, 'now' if finished else None))
    value = {'execution_id': 'execution-failed-before', 'error': 'Codex app-server socket unavailable',
             'runtime_error': {'code': 'unknown', 'message': 'x', 'raw_error': 'x', 'source': 'execution_result'},
             'failed_at': STAMP} if record is None else record
    connection.execute("UPDATE lineage.resident_execution_work SET progress_json=progress_json || %s "
                       "WHERE execution_id=%s", (Jsonb({'previous_execution_failure': value, 'error': ''}), eid))
    return eid


def test_preparing_retry_after_terminal_failure_is_retained(database):
    """Preparing work promoted after a failed predecessor keeps its inert failure record.

    :purpose: ET 10-31 kept a steward's next input preparing after its Codex turn failed.
    """
    dsn, api = database
    with psycopg.connect(dsn) as c:
        previous_failure(c)
    before = work.inspect(api, {})
    assert before['classes']['preparing'] >= 1
    with psycopg.connect(dsn) as c:
        c.execute("UPDATE lineage.resident_execution_work SET progress_json=jsonb_set(progress_json,"
                  "'{previous_execution_failure,error}','\"changed\"') WHERE progress_json ? 'previous_execution_failure'")
    assert work.inspect(api, {}) != before


def test_owner_failure_promotion_of_an_unsubmitted_turn_is_retained(database, monkeypatch):
    """The real owner records the failure, then releases the unsubmitted predecessor as cancelled.

    :purpose: ET 10-31 steward: previous_execution_failure at 13:17:34, its
      never-submitted Execution ended cancelled 1 s later by the same write.
    """
    from management_common.postgres_database import PostgresDatabase
    from management_portal.society_service import resident_conversation_stream, resident_input_queue_domain
    from management_portal.society_service.lineage_data_executions import ResidentExecutions
    dsn, api = database
    monkeypatch.setenv('PORTAL_DATABASE_URL', dsn)
    monkeypatch.delenv('PORTAL_DATABASE_NAMESPACE', raising=False)
    store = ResidentExecutions(PostgresDatabase(dsn, schema='lineage', min_size=1, max_size=2))
    rid, old, nxt = 'resident_000000000004', 'execution-' + '0' * 23 + '4', 'msg_next'
    envelope = {'message_id': nxt, 'role': 'user', 'content_parts': [{'type': 'text', 'text': 'synthetic next'}]}
    with psycopg.connect(dsn) as c:
        c.execute("INSERT INTO lineage.conversation_input_states VALUES(%s,%s,'queued','{}',%s)", (rid, nxt, STAMP))
        c.execute("UPDATE lineage.residents SET backend='codex' WHERE resident_id=%s", (rid,))
    job =store.resident_execution_record_with_revision(rid)['state']
    job['queue'] = [{'user_message_id': nxt, 'actor': 'fixture_user', 'input_message': envelope,
                     'raw_message': 'synthetic next', 'enqueued_at': STAMP}]
    store.put_resident_execution_record(rid, job)
    failed_inputs = []
    monkeypatch.setattr(resident_conversation_stream, 'get_repository', lambda: SimpleNamespace(
        set_input_state=lambda *args, **kwargs: failed_inputs.append(args[:3])))
    job = store.resident_execution_record_with_revision(rid)['state']
    job['failure_message_persisted_at'] = '2026-10-31T13:17:34Z'
    assert resident_input_queue_domain.promote_queue_after_failed_execution(
        job, rid, '2026-10-31T13:17:34Z', 'Codex app-server socket unavailable',
        {'code': 'unknown', 'message': 'x', 'raw_error': 'x', 'source': 'execution_result'})
    store.put_resident_execution_record(rid, job)
    with psycopg.connect(dsn) as c:
        # Input-state projection is outside this storage owner: the failed
        # input as set_input_state recorded it, the promoted one as admitted.
        c.execute("UPDATE lineage.conversation_input_states SET state='failed' WHERE resident_id=%s AND message_id=%s",
                  failed_inputs[0][:2])
        c.execute("UPDATE lineage.conversation_input_states SET state='running' WHERE resident_id=%s AND message_id=%s",
                  (rid, nxt))
        # retain_input_checkpoint's entry for the promoted input, as seed_waiting models it.
        c.execute("UPDATE lineage.resident_mailbox_progress SET progress_json=%s WHERE resident_id=%s",
                  (Jsonb({'checkpoints': [{'message_id': nxt, 'execution_id': job['execution_id'],
                                           'started_at': STAMP, 'infos': [],
                                           'item': {'user_message_id': nxt, 'input_message': envelope}}]}), rid))
        released = c.execute("SELECT status,started_at,finished_at FROM agent.executions WHERE execution_id=%s",
                             (old,)).fetchone()
        current = c.execute("SELECT execution_id,progress_json FROM lineage.resident_execution_work "
                            "WHERE resident_id=%s", (rid,)).fetchone()
    assert released[0] == 'cancelled' and released[1] is None and released[2] is not None
    assert current[0] != old and current[1]['previous_execution_failure']['execution_id'] == old
    before = work.inspect(api, {})
    assert before['counts'] == {'queued': 4, 'preparing': 2}
    with psycopg.connect(dsn) as c:
        c.execute("UPDATE lineage.resident_execution_work SET progress_json=jsonb_set(progress_json,"
                  "'{previous_execution_failure,error}','\"changed\"') WHERE resident_id=%s", (rid,))
    assert work.inspect(api, {}) != before
    with psycopg.connect(dsn) as c:
        c.execute("UPDATE agent.executions SET status='completed' WHERE execution_id=%s", (old,))
    with pytest.raises(work.WorkProofError, match='previous_failure_not_terminal'):
        work.inspect(api, {})


@pytest.mark.parametrize('variant,code', [
    ({'status': 'completed'}, 'previous_failure_not_terminal'),
    ({'status': 'preparing', 'finished': False}, 'work_preparation_count_mismatch'),
    ({'finished': False}, 'previous_failure_not_terminal'),
    ({'same_agent': False}, 'previous_failure_not_terminal'),
    ({'record': {'execution_id': 'execution-missing', 'error': '', 'runtime_error': {}, 'failed_at': STAMP}},
     'previous_failure_not_terminal'),
    ({'record': {'execution_id': 'execution-failed-before', 'error': '', 'runtime_error': {}}},
     'previous_failure_evidence_invalid'),
    ({'record': {'execution_id': 'execution-failed-before', 'error': '', 'runtime_error': {},
                 'failed_at': STAMP, 'continuation_id': 'c'}}, 'previous_failure_evidence_invalid'),
    ({'record': 'failed'}, 'previous_failure_evidence_invalid'),
])
def test_previous_failure_must_name_a_terminal_failed_execution(database, variant, code):
    dsn, api = database
    with psycopg.connect(dsn) as c:
        previous_failure(c, **variant)
    with pytest.raises(work.WorkProofError, match=code):
        work.inspect(api, {})


def test_previous_failure_cannot_name_the_preparing_execution(database):
    dsn, api = database
    with psycopg.connect(dsn) as c:
        eid = previous_failure(c)
        c.execute("UPDATE lineage.resident_execution_work SET progress_json=jsonb_set(progress_json,"
                  "'{previous_execution_failure,execution_id}',%s) WHERE execution_id=%s", (Jsonb(eid), eid))
    with pytest.raises(work.WorkProofError, match='previous_failure_not_terminal'):
        work.inspect(api, {})


def test_previous_failure_does_not_hide_other_progress_evidence(database):
    dsn, api = database
    with psycopg.connect(dsn) as c:
        eid = previous_failure(c)
        c.execute("UPDATE lineage.resident_execution_work SET progress_json=progress_json || "
                  "'{\"submitted_at\":\"recorded\"}' WHERE execution_id=%s", (eid,))
    with pytest.raises(work.WorkProofError, match='submission_continuation_or_projection_evidence'):
        work.inspect(api, {})


@pytest.mark.parametrize('status', ['queued', 'running', 'completed', 'future'])
def test_scheduler_record_never_classified_by_reason_or_terminal_label(database, status):
    dsn, api = database
    with psycopg.connect(dsn) as c:
        c.execute("INSERT INTO execution_runtime.executions(execution_id,capability,actor,priority,correlation_id,request_json,status,created_at,updated_at) "
                  "VALUES('unknown','codex','fixture',0,'x','{}',%s,now(),now())", (status,))
    with pytest.raises(work.WorkProofError, match='scheduler_execution_present'):
        work.inspect(api, {})


def scheduler_execution(dsn, monkeypatch, *, metadata, status):
    """Submit and end one execution through the real Scheduler owner."""
    from management_portal.execution.contracts import ExecutionRequest, ExecutionResult, SessionOptions
    from management_portal.execution.scheduler import Scheduler
    monkeypatch.setenv('PORTAL_DATABASE_URL', dsn)
    monkeypatch.delenv('PORTAL_DATABASE_NAMESPACE', raising=False)
    scheduler = Scheduler(dispatch_interval_min_seconds=0, dispatch_interval_max_seconds=0)
    # The shape account_setup.check submits for a model API account test.
    request = ExecutionRequest.new(
        actor='fixture_user', prompt='Reply with a short greeting.', service_type='chat_completion',
        provider='test-api', operation='generate', session=SessionOptions(), metadata=metadata)
    scheduler.submit(request)
    if status == 'queued':
        return request.execution_id
    scheduler.register_worker(worker_id='fixture-worker', build_id='fixture-build',
                              capabilities=[request.capability], state='standby')
    scheduler.activate_worker('fixture-worker')
    claimed = scheduler.claim(worker_id='fixture-worker', capabilities=[request.capability])
    assert claimed['claimed'] and claimed['execution']['execution_id'] == request.execution_id
    token = claimed['claim_token']
    scheduler.start(request.execution_id, worker_id='fixture-worker', claim_token=token)
    scheduler.finish(ExecutionResult(execution_id=request.execution_id, status=status, output='',
                                     error='kimi service unreachable' if status == 'failed' else ''),
                     worker_id='fixture-worker', claim_token=token)
    return request.execution_id


ACCOUNT_CHECK = {'provider_account_check': {'account_id': 'kimi_service', 'model': 'kimi-for-coding'}}


@pytest.mark.parametrize('status', ['failed', 'completed'])
def test_ended_provider_account_check_is_retained_and_hashed(database, monkeypatch, status):
    """已结束且未确认的账号接入测试调度行不对应 Resident 工作，证明保留它并计入指纹"""
    dsn, api = database
    before = work.inspect(api, {})
    eid = scheduler_execution(dsn, monkeypatch, metadata=ACCOUNT_CHECK, status=status)
    proof = work.inspect(api, {})
    assert proof['counts'] == before['counts'] and proof['classes'] == before['classes']
    assert proof['retained']['execution_runtime.executions']['count'] == 1
    with psycopg.connect(dsn) as c:
        c.execute("UPDATE execution_runtime.executions SET error='changed' WHERE execution_id=%s", (eid,))
    assert work.inspect(api, {}) != proof


@pytest.mark.parametrize('metadata, status', [
    (ACCOUNT_CHECK, 'queued'),
    ({'message_id': 'msg_turn'}, 'failed'),
    ({'provider_account_check': 'not an object'}, 'completed'),
])
def test_other_scheduler_records_still_refuse(database, monkeypatch, metadata, status):
    dsn, api = database
    scheduler_execution(dsn, monkeypatch, metadata=metadata, status=status)
    with pytest.raises(work.WorkProofError, match='scheduler_execution_present'):
        work.inspect(api, {})


def test_work_named_account_check_refuses_even_when_acknowledged(database, monkeypatch):
    dsn, api = database
    eid = scheduler_execution(dsn, monkeypatch, metadata=ACCOUNT_CHECK, status='failed')
    with psycopg.connect(dsn) as c:
        work_eid = c.execute('SELECT execution_id FROM lineage.resident_execution_work LIMIT 1').fetchone()[0]
        c.execute('UPDATE execution_runtime.executions SET execution_id=%s WHERE execution_id=%s', (work_eid, eid))
    with pytest.raises(work.WorkProofError, match='scheduler_execution_present'):
        work.inspect(api, {})
    with psycopg.connect(dsn) as c:
        c.execute('UPDATE execution_runtime.executions SET acknowledged_at=now()')
    with pytest.raises(work.WorkProofError, match='scheduler_execution_present'):
        work.inspect(api, {})


@pytest.mark.parametrize('status', ['completed', 'failed', 'cancelled'])
def test_acknowledged_terminal_receipt_is_retained_and_hashed(database, monkeypatch, status):
    dsn, api = database
    eid = scheduler_execution(dsn, monkeypatch, metadata={'message_id': 'consumed'}, status=status)
    from management_portal.execution.scheduler import Scheduler
    Scheduler().acknowledge(eid, actor='fixture_user')
    proof = work.inspect(api, {})
    assert proof['retained']['execution_runtime.executions']['count'] == 1
    with psycopg.connect(dsn) as c:
        c.execute("UPDATE execution_runtime.executions SET error='changed receipt' WHERE execution_id=%s", (eid,))
    assert work.inspect(api, {}) != proof


def test_acknowledged_nonterminal_receipt_still_refuses(database, monkeypatch):
    dsn, api = database
    eid = scheduler_execution(dsn, monkeypatch, metadata={}, status='queued')
    with psycopg.connect(dsn) as c:
        c.execute('UPDATE execution_runtime.executions SET acknowledged_at=now() WHERE execution_id=%s', (eid,))
    with pytest.raises(work.WorkProofError, match='scheduler_execution_present'):
        work.inspect(api, {})


def test_receipt_detects_payload_checkpoint_and_input_state_changes(database):
    dsn, api = database
    before = work.inspect(api, {})
    with psycopg.connect(dsn) as c:
        c.execute("UPDATE lineage.resident_mailbox SET message_json=jsonb_set(message_json,'{content_parts,0,text}', '\"changed\"')")
    assert work.inspect(api, {}) != before
    before = work.inspect(api, {})
    with psycopg.connect(dsn) as c:
        c.execute("UPDATE lineage.resident_mailbox_progress SET progress_json=progress_json || '{\"retained_extra\":true}'")
    assert work.inspect(api, {}) != before


def test_supplied_transaction_is_not_committed_closed_or_written(database):
    dsn, api = database
    before = work.inspect(api, {})
    with psycopg.connect(dsn) as c:
        c.execute("UPDATE lineage.resident_mailbox SET position=7")
        assert work.inspect(None, None, connection=c) != before
        assert not c.closed
        c.rollback()
    assert work.inspect(api, {}) == before


def test_public_errors_never_expose_connection_or_input(database):
    _, api = database
    bad = SimpleNamespace(clean_environment=lambda _: {'PORTAL_DATABASE_URL': 'secret-invalid-dsn'})
    with pytest.raises(work.WorkProofError) as raised:
        work.inspect(bad, {})
    assert 'secret-invalid-dsn' not in str(raised.value)


def test_held_info_is_preserved_without_fabricating_an_active_input(database):
    dsn, api = database
    before = work.inspect(api, {})
    with psycopg.connect(dsn) as c:
        c.execute("INSERT INTO lineage.resident_mailbox SELECT resident_id,'msg_held_info','info',9,'',author_id,"
                  "accepted_at,updated_at,jsonb_set(message_json,'{message_id}','\"msg_held_info\"'),"
                  "'{\"held_at\":\"2026-10-01T00:00:00Z\"}' FROM lineage.resident_mailbox LIMIT 1")
    proof = work.inspect(api, {})
    assert proof['counts'] == before['counts']
    assert proof != before
    assert proof['retained']['resident_mailbox']['count'] == 5
    with psycopg.connect(dsn) as c:
        c.execute("UPDATE lineage.resident_mailbox SET message_json=jsonb_set(message_json,'{content_parts,0,text}','\"changed info\"') "
                  "WHERE place='info'")
    assert work.inspect(api, {}) != proof


def test_deferred_session_restart_cannot_be_mistaken_for_untouched_preparation(database):
    dsn, api = database
    with psycopg.connect(dsn) as c:
        c.execute("INSERT INTO lineage.resident_sessions(resident_id,session_restart_json) "
                  "SELECT resident_id,'{\"deferred\":{\"handoff\":{\"phase\":\"withdrawn\"}}}' "
                  "FROM lineage.residents LIMIT 1")
    with pytest.raises(work.WorkProofError, match='deferred_restart_pending'):
        work.inspect(api, {})


def test_fingerprint_handles_legacy_text_json_without_interpreting_user_keys():
    payload = {'user_json': 'ordinary text, not JSON', 'parts': [1, None]}
    assert work.fingerprint([{'payload_json': json.dumps(payload)}]) == work.fingerprint([{'payload_json': payload}])
    assert work.fingerprint([{'value': 1}, {'value': 2}]) == work.fingerprint([{'value': 2}, {'value': 1}])


@pytest.mark.parametrize('kind', ['submission', 'unknown_operation', 'request_digest'])
def test_expired_scheduler_receipt_cannot_hide_durable_submission(database, kind):
    dsn, api = database
    with psycopg.connect(dsn) as c:
        eid, rid = c.execute('SELECT execution_id,agent_id FROM agent.executions LIMIT 1').fetchone()
        if kind == 'request_digest':
            c.execute('INSERT INTO execution_runtime.execution_store_meta VALUES(%s,%s)', ('request_digest:' + eid, 'synthetic'))
        else:
            c.execute("INSERT INTO agent.agent_operations(operation_id,kind,agent_id,created_at,data_json) VALUES(%s,%s,%s,now(),%s)",
                ('execution_submission:' + eid if kind == 'submission' else 'unknown', kind, rid,
                 Jsonb({'execution_id': eid})))
    with pytest.raises(work.WorkProofError, match='(operation|digest)_present'):
        work.inspect(api, {})


def shape_et(connection):
    """Only synthetic data: queued projections of two infos and two facades."""
    c = connection.cursor()
    for i in range(4):
        rid, mid = f'resident_{i:012x}', f'msg_{i:032x}'
        if i < 2:
            c.execute("UPDATE lineage.resident_mailbox SET place='info',item_json=%s WHERE resident_id=%s",
                      (Jsonb({'held_at': STAMP}), rid))
            continue
        c.execute('DELETE FROM lineage.resident_mailbox WHERE resident_id=%s', (rid,))
        c.execute("INSERT INTO lineage.families(family_id,lineage_id,title,goal,resource_id,created_at,updated_at) "
                  "VALUES(%s,'kept','fixture','fixture',%s,%s,%s)", (f'fbi_{i}', f'family_{i:012x}', STAMP, STAMP))
        c.execute("INSERT INTO lineage.family_residents VALUES(%s,%s,'facade')", (f'fbi_{i}', rid))
        seq, segment, payload = f'seq_{i}', f'segment_{i}', f'payload_{i}'
        body = b'Synthetic original facade input'
        digest = hashlib.sha256(body).hexdigest()
        c.execute("INSERT INTO agent.sequences VALUES(%s,%s,%s,'fixture','fixture')", (seq, STAMP, STAMP))
        c.execute("INSERT INTO agent.agents VALUES(%s,%s,%s,'agent_trace',NULL)", (rid, seq, STAMP))
        c.execute("INSERT INTO agent.payloads VALUES(%s,%s,%s,'text/plain',%s,%s) ON CONFLICT(sha256) DO NOTHING",
                  (payload, digest, len(body), body, STAMP))
        payload = c.execute('SELECT payload_ref FROM agent.payloads WHERE sha256=%s', (digest,)).fetchone()[0]
        metadata = {'message': {'message_id': mid, 'role': 'user', 'created_at': STAMP,
                                'parts': [{'type': 'text', 'length': len(body)}]}}
        c.execute("INSERT INTO agent.segments(segment_id,segment_type,status,created_by_actor_ref,performed_by_actor_ref,"
                  "content_ref,content_sha256,content_type,metadata_json,created_by_agent_id,execution_id) "
                  "VALUES(%s,'input','completed','fixture','fixture',%s,%s,'text/plain',%s,%s,'')",
                  (segment, payload, digest, Jsonb(metadata), rid))
        c.execute('INSERT INTO agent.sequence_segments VALUES(%s,1,%s)', (seq, segment))
        c.execute("INSERT INTO agent.chat_message_positions(sequence_id,source_kind,source_id,ordinal,segment_type,message_id,message_kind) "
                  "VALUES(%s,'segment',%s,1,'input',%s,'user')", (seq, segment, mid))


def test_et_shaped_info_facade_and_preparation_survive_v60_migration_without_replay(database):
    from scripts.tfi_release_schema import upgrade
    dsn, api = database
    with psycopg.connect(dsn) as c:
        shape_et(c)
    before = work.inspect(api, {})
    assert before['counts'] == {'queued': 4, 'preparing': 2}
    assert before['classes'] == {'ordinary_queue': 0, 'held_info': 2, 'facade_input': 2, 'preparing': 2}
    assert before['retained']['agent.facade_originals']['count'] == 2
    assert 'Synthetic original' not in json.dumps(before)
    with psycopg.connect(dsn) as c:
        c.execute('SET LOCAL search_path TO lineage')
        upgrade(SchemaConnection(c))
    assert work.inspect(api, {}) == before
    with psycopg.connect(dsn) as c:
        c.execute("UPDATE agent.payloads SET body='corrupted'")
    with pytest.raises(work.WorkProofError, match='facade_original_payload_invalid'):
        work.inspect(api, {})


@pytest.mark.parametrize('sql', [
    'DELETE FROM agent.sequence_segments',
    "UPDATE lineage.family_residents SET role='member'",
    "UPDATE lineage.families SET deleted_at=now()",
    "UPDATE agent.segments SET execution_id='unexpected'",
])
def test_queued_facade_projection_requires_its_original_owner_and_no_execution(database, sql):
    dsn, api = database
    with psycopg.connect(dsn) as c:
        shape_et(c)
        c.execute(sql)
    with pytest.raises(work.WorkProofError):
        work.inspect(api, {})
