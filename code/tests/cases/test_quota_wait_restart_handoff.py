"""Queued, never-claimed input migrates without cancelling its business identity."""
from __future__ import annotations

import copy
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.execution.contracts import ExecutionRequest, SessionOptions, SystemInstructions
from management_portal.society_service import (
    resident_backend,
    resident_catalog,
    resident_conversation_stream,
    resident_deferred_restart,
    resident_execution_domain,
    resident_execution_engine_domain,
    resident_execution_work,
    resident_input_queue_domain,
    resident_record_domain,
    resident_restart_domain,
)
from management_portal.society_service.domain_ports import execution_port
from management_portal.society_service.state_store import LineageDataStore
from tests.cases.test_execution_layer import _stack, _close
from tests.cases.test_lineage_catalogs import _lineage_definition, _record
from tests.cases.test_session_restart import _restart_domain

pytestmark = pytest.mark.usefixtures("registered_execution_defaults")
RID = "resident_aaaaaaaaaaaa"
MID = "msg_original"


@pytest.fixture
def execution(tmp_path):
    scheduler, server, thread, client, service = _stack(tmp_path)
    request = ExecutionRequest.new(
        actor="alice", agent_id=RID, prompt="old provider preparation",
        service_type="agent_session", provider="claude", operation="turn",
        session=SessionOptions(mode="persistent", binding_id="resident:" + RID),
    )
    scheduler.submit(request)
    args = dict(actor="alice", binding_id="resident:" + RID,
                operation_id="restart_original", replacement_execution_id="execution-replacement",
                performed_by="steward")
    try:
        yield scheduler, service, request, args
    finally:
        _close(server, thread)


def test_withdraw_receipt_survives_ack_retention_and_keeps_actor_scope(execution):
    scheduler, service, request, args = execution
    receipt = service.withdraw_unstarted(request.execution_id, **args)
    assert receipt['withdrawn'] is True
    snapshot = scheduler.snapshot(request.execution_id)
    assert snapshot['status'] == 'cancelled'
    assert snapshot['result']['unstarted_handoff']['operation_id'] == args['operation_id']
    service.acknowledge(request.execution_id, actor='alice')
    scheduler.prune_terminal(older_than_epoch=time.time() + 1)
    assert service.withdraw_unstarted(request.execution_id, **args) == receipt
    with pytest.raises(PermissionError):
        scheduler.withdraw_unstarted(request.execution_id, **{**args, 'actor': 'intruder'})
    with pytest.raises(ValueError, match='another operation'):
        scheduler.withdraw_unstarted(request.execution_id, **{**args, 'operation_id': 'another'})


@pytest.mark.parametrize('change', [
    "status='claimed',owner_worker_id='worker'", "attempts=1",
    "checkpoint_json='{\"backend_started\":true}'", "cancel_requested=true",
])
def test_withdraw_refuses_claim_history_checkpoint_and_stop(execution, change):
    scheduler, service, request, args = execution
    with scheduler._connect() as connection:
        connection.execute('UPDATE executions SET ' + change + ' WHERE execution_id=%s', (request.execution_id,))
        connection.commit()
    assert service.withdraw_unstarted(request.execution_id, **args)['withdrawn'] is False
    assert not scheduler.snapshot(request.execution_id)['result'].get('unstarted_handoff')


def test_withdraw_and_real_worker_claim_have_only_one_winner(execution):
    scheduler, service, request, args = execution
    scheduler.register_worker(worker_id='test-worker', build_id='test', capabilities=['agent_session:claude'])
    barrier = threading.Barrier(2)
    def withdraw():
        barrier.wait()
        return service.withdraw_unstarted(request.execution_id, **args)
    def claim():
        barrier.wait()
        return scheduler.claim(worker_id='test-worker', capabilities=['agent_session:claude'])
    with ThreadPoolExecutor(max_workers=2) as pool:
        withdrawal, claimed = pool.submit(withdraw), pool.submit(claim)
        withdrawal, claimed = withdrawal.result(), claimed.result()
    assert withdrawal['withdrawn'] != claimed['claimed'], (withdrawal, claimed)
    assert scheduler.snapshot(request.execution_id)['status'] == ('cancelled' if withdrawal['withdrawn'] else 'claimed')


@pytest.fixture
def resident(monkeypatch, execution):
    scheduler, service, request, args = execution
    store = LineageDataStore()
    transaction = store.transaction
    real_read, real_active = resident_execution_work.read, resident_execution_work.is_active
    _, operations, _ = _restart_domain(monkeypatch, source_provider='claude', source_route='deepseek_primary')
    monkeypatch.setattr(resident_execution_domain, 'resident_system_instructions',
                        lambda *a, **kw: SystemInstructions())
    # The existing fixture replaces only native/provider work here. Restore the
    # actual durable Job, restart and Mailbox repositories for the whole handoff.
    monkeypatch.setattr(store, 'transaction', transaction)
    monkeypatch.setattr(resident_catalog, 'get_catalog', lambda: SimpleNamespace(state_store=store))
    monkeypatch.setattr(resident_execution_work, 'read', real_read)
    monkeypatch.setattr(resident_execution_work, 'is_active', real_active)
    monkeypatch.setattr(resident_execution_work, '_post_execution_commit', lambda *a, **k: None)
    lineage = _lineage_definition()
    store.put_lineage_definition(lineage['lineage_id'], lineage, {})
    record = _record(RID)
    record.update(backend='claude', backend_route_id='deepseek_primary', intake_open=True)
    store.put_resident_record(RID, record, commit_configuration=True)
    monkeypatch.setattr(resident_record_domain, 'find_resident', store.resident_record)
    monkeypatch.setattr(resident_record_domain, 'find_resident_canonical', store.resident_record)
    monkeypatch.setattr(resident_record_domain, 'save_resident_record',
                        lambda value, **kw: store.put_resident_record(RID, value, **kw))
    monkeypatch.setattr(resident_record_domain, 'resolve_resident_runtime_target',
                        lambda *a, **kw: SimpleNamespace(record=store.resident_record(RID), resident_id=RID))
    native = execution_port.service()
    # Native preparation is the only simulated execution; withdrawal and its
    # receipt travel through the real ExecutionService socket and Scheduler.
    native.withdraw_unstarted = lambda *a, **kw: service.withdraw_unstarted(*a, **kw)
    monkeypatch.setattr(execution_port, 'service', lambda: native)
    repo = resident_conversation_stream.get_repository()
    message = dict(message_id=MID, role='user', author_id='alice',
                   content_parts=[{'type': 'text', 'text': 'original input'}])
    repo.accept_user_message(RID, message)
    repo.set_input_state(RID, MID, 'queued')
    queue = []
    for mid in ('msg_handoff_one', 'msg_handoff_two'):
        item = {**message, 'message_id': mid}
        repo.accept_user_message(RID, item)
        repo.set_input_state(RID, mid, 'queued')
        queue.append({'user_message_id': mid, 'input_message': item, 'actor': 'alice',
                      'effective_message': 'queued handoff', 'activation_policy': 'enqueue_and_trigger'})
    job = dict(execution_id=request.execution_id, user_message_id=MID, actor='alice',
               input_message=message, effective_message='original input', raw_message='original input',
               actual_sent_prompt='old provider preparation', runtime_context={'old': 'context'},
               submitted_at='2026-10-19T00:00:00Z', started_at='2026-10-19T00:00:00Z',
               activation_policy='enqueue_and_trigger', queue=queue)
    store.put_resident_execution_record(RID, job)
    receipt = resident_deferred_restart.schedule(
        store.resident_record(RID), 'alice',
        {'target_provider': 'codex', 'target_account': 'one', 'context_strategy': 'compact',
         'active_execution_policy': 'wait', 'idempotency_key': 'original-migration'},
        {'provider': 'claude', 'account_id': 'deepseek_primary'},
        {'target_provider': 'codex', 'target_account': 'one', 'context_strategy': 'compact'},
    )
    return store, repo, operations, receipt, native


@pytest.mark.parametrize('finish', ['completed', 'failed', 'cancelled'])
def test_original_deferred_request_advances_and_preserves_original_input(resident, execution, finish):
    store, repo, operations, original, native = resident
    scheduler, _, request, _ = execution
    advanced = resident_deferred_restart.advance(store.resident_record(RID))
    assert advanced is True, store.resident_record(RID)['session_restart']
    pending = store.resident_record(RID)
    assert pending['session_restart']['restart_id'] == original['restart_id']
    assert pending['session_restart']['request_id'] == original['request_id']
    assert pending['session_restart']['deferred']['handoff']['phase'] == 'withdrawn'
    assert resident_execution_work.read(pending)['execution_id'] == request.execution_id
    assert repo.input_state(RID, MID)['state'] == 'queued'
    assert pending['intake_open'] is False
    if finish == 'failed':
        native.resume = lambda *a, **k: {'status': 'failed', 'result': {'status': 'failed'}, 'error': 'native preparation failed'}
    elif finish == 'cancelled':
        pending['session_restart']['status'] = 'cancelling'
        store.put_resident_record(RID, pending)
    resident_restart_domain.resume_resident_session_restart(operations[-1]['step'])
    resident_deferred_restart.advance(store.resident_record(RID))
    current = store.resident_record(RID)
    job = resident_execution_work.read(current)
    assert current['session_restart']['status'] == finish
    assert current['session_restart']['restart_id'] == original['restart_id']
    assert current['session_restart']['request_id'] == original['request_id']
    assert current['intake_open'] is True
    assert not current['session_restart'].get('deferred')
    assert job['user_message_id'] == MID
    assert [item['user_message_id'] for item in job['queue']] == ['msg_handoff_one', 'msg_handoff_two']
    assert job['execution_id'] != request.execution_id
    assert not job.get('submitted_at')
    assert not job.get('runtime_context')
    assert job['effective_message'] == 'original input'
    assert 'old provider preparation' not in str(job)
    assert repo.input_state(RID, MID)['state'] == 'queued'
    expected_provider = 'codex' if finish == 'completed' else 'claude'
    with AgentDataStore().database.read() as connection:
        records = {r['execution_id']: dict(r) for r in connection.execute(
            'SELECT execution_id,provider,status FROM executions WHERE execution_id=ANY(%s)',
            ([request.execution_id, job['execution_id']],),
        ).fetchall()}
    assert records[request.execution_id]['provider'] == 'claude'
    assert records[request.execution_id]['status'] == 'cancelled'
    assert records[job['execution_id']]['provider'] == expected_provider
    assert records[job['execution_id']]['status'] == 'preparing'


def test_lost_withdraw_reply_is_reconciled_without_terminal_input(resident, execution, monkeypatch):
    store, repo, operations, original, native = resident
    original_withdraw = native.withdraw_unstarted
    def lost(*a, **kw):
        original_withdraw(*a, **kw)
        raise ConnectionError('lost after commit')
    monkeypatch.setattr(native, 'withdraw_unstarted', lost)
    with pytest.raises(ConnectionError, match='lost after commit'):
        resident_deferred_restart.advance(store.resident_record(RID))
    assert repo.input_state(RID, MID)['state'] == 'queued'
    assert not operations
    monkeypatch.setattr(native, 'withdraw_unstarted', original_withdraw)
    assert resident_deferred_restart.advance(store.resident_record(RID)) is True
    assert len(operations) == 1
    assert resident_deferred_restart.advance(store.resident_record(RID)) is True
    assert len(operations) == 1


def test_receipt_save_failure_keeps_exact_intent_for_recovery(resident, monkeypatch):
    store, repo, operations, original, native = resident
    save = resident_record_domain.save_resident_record
    def fail_receipt(value, **kw):
        if value.get('session_restart', {}).get('deferred', {}).get('handoff', {}).get('phase') == 'withdrawn':
            raise RuntimeError('receipt persistence failed')
        return save(value, **kw)
    monkeypatch.setattr(resident_record_domain, 'save_resident_record', fail_receipt)
    with pytest.raises(RuntimeError, match='receipt persistence failed'):
        resident_deferred_restart.advance(store.resident_record(RID))
    intent = copy.deepcopy(store.resident_record(RID)['session_restart']['deferred']['handoff'])
    assert not intent.get('phase')
    assert repo.input_state(RID, MID)['state'] == 'queued'
    monkeypatch.setattr(resident_record_domain, 'save_resident_record', save)
    assert resident_deferred_restart.advance(store.resident_record(RID)) is True
    assert store.resident_record(RID)['session_restart']['deferred']['handoff'] == {**intent, 'phase': 'withdrawn'}


@pytest.mark.parametrize('manual_close', [False, True])
def test_cancel_after_withdraw_restores_source_and_only_own_intake_transition(resident, manual_close):
    from management_portal.society_service import resident_restart_handoff
    store, repo, operations, original, native = resident
    assert resident_restart_handoff.advance(store.resident_record(RID)) is True
    record = store.resident_record(RID)
    if manual_close:
        resident_input_queue_domain.commit_resident_intake_state(record, 'closed')
    resident_deferred_restart.cancel(store.resident_record(RID), original['restart_id'])
    record = store.resident_record(RID)
    job = resident_execution_work.read(record)
    assert record['intake_open'] is (not manual_close)
    assert record['backend'] == 'claude'
    assert job['user_message_id'] == MID and not job.get('submitted_at')
    assert repo.input_state(RID, MID)['state'] == 'queued'
    assert not operations


def test_internal_handoff_terminal_does_not_project_user_cancellation(execution, monkeypatch):
    from management_portal.society_service import resident_restart_handoff
    scheduler, service, request, args = execution
    service.withdraw_unstarted(request.execution_id, **args)
    snapshot = scheduler.snapshot(request.execution_id)
    monkeypatch.setattr(resident_execution_engine_domain, 'resident_record_for_execution',
                        lambda *a: pytest.fail('handoff must not project a user terminal'))
    resident_execution_engine_domain._project_resident_execution_snapshot(RID, request.execution_id, snapshot)
    monkeypatch.setattr(resident_execution_engine_domain, '_resident_execution_event_cursor', lambda *a: 0)
    with pytest.raises(resident_restart_handoff.ExecutionHandedOff):
        resident_execution_engine_domain.wait_for_resident_execution(
            SimpleNamespace(wait=lambda *a, **kw: snapshot), execution_id=request.execution_id,
            actor='alice', resident_id=RID, user_message_id=MID,
        )
