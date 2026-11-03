"""Paid Fork preparation keeps its frozen history, original input and consent scope."""
import copy

import pytest

from management_portal.society_service import (
    resident_backend, resident_domain, resident_execution_domain, resident_execution_work,
    resident_initialization, resident_initialization_credits, resident_input_queue_domain,
    resident_intake_domain, resident_record_domain, resident_session_backend_domain,
)
from management_portal.society_service.domain_ports import execution_port
from tests.cases.agent_data_test_support import register_session
from tests.cases.isa_test_support import isolated_instruction_store, run
from tests.cases.test_resident_folder_creation_contract import (
    future_creation_store, _new_session_payload, _activate_created_resident,
)
from tests.cases.test_resident_fork_context_delivery import _record_provider_turn
from tests.cases.test_resident_initialization_recovery import (
    compacted_child, fail_initialization, candidate_backend, execute,
)
from tests.cases.test_session_credits_consent import paid_quota
from management_portal.execution.agent_data_store import get_agent_data_store


@pytest.fixture
def pending_child(future_creation_store, monkeypatch):
    source = resident_domain.register_resident(_new_session_payload('Paid Fork source'), 'sample_admin')
    source = _activate_created_resident(source['resident_id'])
    _record_provider_turn(get_agent_data_store(), source, 0)
    child = resident_domain.create_resident_full_context_fork(source['resident_id'], 'sample_admin', backend_route_id='one')
    monkeypatch.setattr(resident_execution_domain, 'resume_resident_execution', lambda _: {})
    return resident_record_domain.find_resident(child['resident_id'])


@pytest.fixture
def consent_io(monkeypatch):
    policies = []
    monkeypatch.setattr(execution_port, 'configure_binding_quota_overrides',
                        lambda values: policies.extend(copy.deepcopy(values)))
    monkeypatch.setattr(execution_port, 'health', lambda: {'scheduler': {'capacity_partitions': {
        'codex:one': {'quota_reserve': paid_quota()}}}})
    return policies


def test_runtime_settings_exposes_current_initialization(pending_child):
    from management_portal.society_service import resident_restart_domain
    rid = pending_child['resident_id']
    expected = resident_initialization.view(rid, 'sample_admin')
    options = resident_restart_domain.get_resident_restart_options(rid, 'sample_admin')
    assert options['initialization'] == expected
    assert options['initialization']['initialization_id']
    assert options['initialization']['status'] in {'pending', 'running', 'failed'}


def consent_args(record):
    return {'resident_id': record['resident_id'],
            'expected_initialization_id': resident_initialization.view(record['resident_id'], 'sample_admin')['initialization_id'],
            'credits_consent': {'account_id': 'codex_one', 'confirmed': True}}


def confirm(record, **changes):
    args = {**consent_args(record), **changes}
    return resident_initialization.retry(args.pop('resident_id'), 'sample_admin', request_id='paid-fork', **args)


def test_human_resumes_same_initialization_and_keeps_original_queued_input(
        isolated_instruction_store, pending_child, consent_io, monkeypatch):
    rid = pending_child['resident_id']
    mid = 'msg_' + 'd1' * 16
    resident_intake_domain.persist_resident_input(rid, 'sample_admin', 'first real question', message_id=mid)
    before = resident_execution_work.read(pending_child)
    args = consent_args(pending_child)
    result = run('RetryResidentInitialization', args, key='paid-fork')
    assert result.status == 'committed', result
    assert result.result['initialization_id'] == before['execution_id']
    assert run('RetryResidentInitialization', args, key='paid-fork').result == result.result
    saved = resident_execution_work.read(resident_record_domain.find_resident(rid))
    assert saved['execution_id'] == before['execution_id']
    assert [i['user_message_id'] for i in saved['queue']] == [mid]
    assert consent_io[-1]['credits_scope'] == {
        'account_id': 'codex_one', 'operation_id': 'initialization-credits:paid-fork',
        'execution_id': before['execution_id'],
    }
    assert not saved.get('initialization_recovery')  # no replacement or compact
    assert saved['credits_authorization']['confirmed_by'] == 'sample_admin'


@pytest.mark.parametrize('changes', [
    {'expected_initialization_id': ''}, {'expected_initialization_id': 'stale'},
    {'credits_consent': {'account_id': 'codex_two', 'confirmed': True}},
    {'credits_consent': {'account_id': 'codex_one', 'confirmed': False}},
])
def test_bad_confirmation_never_changes_initialization(pending_child, consent_io, changes):
    before = resident_execution_work.read(pending_child)
    with pytest.raises(ValueError):
        confirm(pending_child, **changes)
    assert resident_execution_work.read(pending_child) == before
    assert not consent_io


def test_resident_cannot_confirm_initialization(pending_child, consent_io):
    args = consent_args(pending_child)
    with pytest.raises(PermissionError, match='人类'):
        resident_initialization.retry(args.pop('resident_id'), pending_child['resident_id'],
                                      request_id='agent-consent', **args)
    assert not consent_io


def test_unavailable_credits_does_not_wake_or_change_work(pending_child, consent_io, monkeypatch):
    before = resident_execution_work.read(pending_child)
    monkeypatch.setattr(execution_port, 'health', lambda: {'scheduler': {'capacity_partitions': {
        'codex:one': {'quota_reserve': paid_quota(balance=0)}}}})
    with pytest.raises(ValueError, match='可用 credits'):
        confirm(pending_child)
    assert resident_execution_work.read(pending_child) == before
    assert not consent_io


def test_created_session_promotes_consent_before_original_queue(pending_child, consent_io, monkeypatch):
    rid = pending_child['resident_id']
    mid = 'msg_' + 'd2' * 16
    resident_intake_domain.persist_resident_input(rid, 'sample_admin', 'first real question', message_id=mid)
    confirm(pending_child)
    calls = []
    def create(**kw):
        calls.append(kw['operation'])
        assert kw['operation'] == 'create'
        assert consent_io[-1]['credits_scope']['execution_id'] == kw['execution_work']['execution_id']
        register_session(pending_child, '01a00000-0000-7000-8000-000000000931', backend_route_id='one')
        return {'backend_session_id': '01a00000-0000-7000-8000-000000000931', 'backend_route_id': 'one'}
    monkeypatch.setattr(resident_session_backend_domain, 'run_backend_session_operation', create)
    monkeypatch.setattr(resident_input_queue_domain, 'acknowledge_execution', lambda _: True)
    prepared = resident_execution_domain._prepare_resident_continuation(pending_child)
    record = resident_record_domain.find_resident(rid)
    job = resident_execution_work.read(record)
    assert resident_execution_domain._create_resident_session(rid, prepared[1], record, job)
    current = resident_record_domain.find_resident(rid)
    policy = current['quota_reserve_override']
    assert policy['credits_scope']['provider_session_ref'] == resident_backend.session_record(current)['provider_session_ref']
    assert 'execution_id' not in policy['credits_scope']
    assert resident_execution_work.read(current)['user_message_id'] == mid
    assert calls == ['create']


def test_candidate_recovery_authorizes_each_step_and_then_resulting_session(
        compacted_child, consent_io, monkeypatch):
    record = fail_initialization(compacted_child, monkeypatch)
    monkeypatch.setattr(resident_execution_domain, 'resume_resident_execution', lambda _: {})
    result = confirm(record)
    current = resident_record_domain.find_resident(record['resident_id'])
    attempt = resident_execution_work.read(current)['initialization_recovery']
    assert result['initialization_id'] == attempt['id']
    calls = []
    backend = candidate_backend(calls)
    def checked(**kw):
        policy = consent_io[-1]
        assert policy['binding_id'] == attempt['candidate_binding_id']
        assert policy['credits_scope']['execution_id'] == attempt['execution_ids'][kw['operation']]
        return backend(**kw)
    monkeypatch.setattr(resident_session_backend_domain, 'run_backend_session_operation', checked)
    deleted = []
    monkeypatch.setattr(execution_port, 'forget_binding_quota_overrides', lambda ids: deleted.extend(ids))
    current = execute(current)
    assert current['session_state'] == 'active'
    assert current['quota_reserve_override']['credits_scope']['provider_session_ref'] == resident_backend.session_record(current)['provider_session_ref']
    assert deleted == [attempt['candidate_binding_id']]
    assert [call['operation'] for call in calls] == ['create', 'compact']


def test_completion_sync_retry_does_not_create_or_compact_again(pending_child, consent_io, monkeypatch):
    confirm(pending_child)
    rid = pending_child['resident_id']
    register_session(pending_child, '01a00000-0000-7000-8000-000000000932', backend_route_id='one')
    prepared = resident_execution_domain._prepare_resident_continuation(pending_child)
    record = resident_record_domain.find_resident(rid)
    job = resident_execution_work.read(record)
    resident_initialization_credits.promote(record, job)
    resident_record_domain.save_resident_record(record, commit_configuration=True)
    job['submitted_at'] = '2026-11-03T00:00:00Z'
    job['completion_applied_at'] = '2026-11-03T00:00:00Z'
    job['completion_applied_execution_id'] = job['execution_id']
    resident_execution_work.write(record, job)
    monkeypatch.setattr(resident_input_queue_domain, 'acknowledge_execution', lambda _: True)
    monkeypatch.setattr(resident_input_queue_domain, 'sync_resident_quota_overrides', lambda _: False)
    monkeypatch.setattr(resident_execution_domain, '_created_session_thread', lambda *a: pytest.fail('must not recreate'))
    assert not resident_execution_domain._create_resident_session(rid, prepared[1], record, job)
    assert resident_execution_work.read(record)['execution_id'] == job['execution_id']
    monkeypatch.setattr(resident_input_queue_domain, 'sync_resident_quota_overrides', lambda _: True)
    assert not resident_execution_domain._create_resident_session(rid, prepared[1], record, job)
    assert not resident_execution_work.read(record)


def test_initialization_consent_is_not_an_input_preference():
    from management_portal.society_service.resident_execution_storage import queue_input
    item = {'user_message_id': 'msg_original', 'raw_message': 'hello',
            'credits_authorization': {'account_id': 'codex_one'},
            'credits_preparation_policy': {'enabled': True}}
    assert queue_input(item) == {'user_message_id': 'msg_original', 'raw_message': 'hello'}
