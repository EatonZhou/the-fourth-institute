"""Fresh-context Resident inputs retain their identity across account migration."""
from dataclasses import replace
import secrets

import pytest

from management_portal.execution.contracts import ExecutionRequest, SessionOptions
from tests.cases.test_execution_layer import _stack, _close
from tests.cases.test_quota_wait_restart_handoff import (
    resident, RID, MID,
    test_original_deferred_request_advances_and_preserves_original_input as _roundtrip,
    test_withdraw_receipt_survives_ack_retention_and_keeps_actor_scope as _replay,
    test_withdraw_and_real_worker_claim_have_only_one_winner as _claim_race,
)

pytestmark = pytest.mark.usefixtures('registered_execution_defaults')


@pytest.fixture
def execution(tmp_path):
    scheduler, server, thread, client, service = _stack(tmp_path)
    request = ExecutionRequest.new(
        actor='alice', agent_id=RID, prompt='old provider preparation',
        service_type='agent_session', provider='claude', operation='turn',
        session=SessionOptions(mode='stateless'),
        metadata={'business_type': 'resident', 'resident_id': RID},
        runtime_options={'client_user_message_id': MID},
    )
    scheduler.submit(request)
    args = dict(actor='alice', binding_id='resident:' + RID,
                operation_id='restart_original', replacement_execution_id='execution-replacement',
                performed_by='steward')
    try:
        yield scheduler, service, request, args
    finally:
        _close(server, thread)


@pytest.mark.parametrize('finish', ['completed', 'failed', 'cancelled'])
def test_stateless_original_input_and_queue_survive_migration(resident, execution, finish):
    _roundtrip(resident, execution, finish)


def test_stateless_receipt_replay_retains_actor_and_operation_scope(execution):
    _replay(execution)


def test_stateless_withdraw_and_claim_have_only_one_winner(execution):
    _claim_race(execution)


@pytest.mark.parametrize('change', [
    "status='claimed',owner_worker_id='worker'", "attempts=1",
    "checkpoint_json='{\"backend_started\":true}'", "cancel_requested=true",
])
def test_stateless_claim_checkpoint_or_stop_is_never_withdrawn(execution, change):
    scheduler, service, request, args = execution
    with scheduler.changes.connect() as connection:
        connection.execute('UPDATE executions SET ' + change + ' WHERE execution_id=%s', (request.execution_id,))
        connection.commit()
    before = scheduler.snapshot(request.execution_id)
    assert service.withdraw_unstarted(request.execution_id, **args)['withdrawn'] is False
    assert scheduler.snapshot(request.execution_id) == before


@pytest.mark.parametrize('change', [
    {'metadata': {}},
    {'metadata': {'business_type': 'resident', 'resident_id': 'resident_bbbbbbbbbbbb'}},
    {'runtime_options': {}},
    {'metadata': {'business_type': 'other', 'resident_id': RID}},
])
def test_unattributed_stateless_execution_is_not_a_resident_handoff(execution, change):
    scheduler, service, request, args = execution
    candidate = replace(request, execution_id='execution-' + secrets.token_hex(12), **change)
    scheduler.submit(candidate)
    before = scheduler.snapshot(candidate.execution_id)
    assert service.withdraw_unstarted(candidate.execution_id, **args)['withdrawn'] is False
    assert scheduler.snapshot(candidate.execution_id) == before


def test_stateless_cannot_be_handed_to_another_resident(execution):
    scheduler, service, request, args = execution
    before = scheduler.snapshot(request.execution_id)
    with pytest.raises(ValueError, match='binding changed'):
        service.withdraw_unstarted(request.execution_id, **{**args, 'binding_id': 'resident:resident_bbbbbbbbbbbb'})
    assert scheduler.snapshot(request.execution_id) == before
