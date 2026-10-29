"""Initialization owns its work and waiting inputs, even after a native failure."""
import pytest

from management_portal.society_service import (
    resident_domain,
    resident_execution_work,
    resident_execution_domain,
    resident_intake_domain,
    resident_session_backend_domain,
    resident_catalog,
    resident_protocol,
    resident_record_domain,
    resident_input_queue_domain,
)
from management_portal.execution.agent_data_store import get_agent_data_store
from tests.cases.agent_data_test_support import register_session
from tests.cases.test_resident_folder_creation_contract import (
    future_creation_store, _new_session_payload, _activate_created_resident,
)
from tests.cases.test_resident_fork_context_delivery import _record_provider_turn
from management_portal.society_service import resident_initialization, resident_backend
from management_portal.society_service.domain_ports import execution_port
from management_portal.execution.agent_data_session_capture import persist_provider_compaction
from tests.cases.isa_test_support import isolated_instruction_store, run
import hashlib
from tests.cases.permission_test_support import permissions, Fact


@pytest.fixture
def compacted_child(future_creation_store, monkeypatch):
    source = resident_domain.register_resident(_new_session_payload('Initialization source'), 'sample_admin')
    source = _activate_created_resident(source['resident_id'])
    _record_provider_turn(get_agent_data_store(), source, 0)
    child = resident_domain.create_resident_compacted_context_fork(source['resident_id'], 'sample_admin', backend_route_id='one')
    record = resident_record_domain.find_resident(child['resident_id'])
    register_session(record, '01a00000-0000-7000-8000-000000000918', backend_route_id='one')
    monkeypatch.setattr(resident_input_queue_domain, 'acknowledge_execution', lambda _: True)
    return record


def fail_initialization(record, monkeypatch):
    def fail(**kwargs):
        raise RuntimeError('execution worker lease expired after backend execution started')
    monkeypatch.setattr(resident_session_backend_domain, 'run_backend_session_operation', fail)
    prepared = resident_execution_domain._prepare_resident_continuation(record)
    assert prepared
    resident_execution_domain.run_resident_execution(*prepared)
    return resident_record_domain.find_resident(record['resident_id'])


def test_failed_compaction_retains_creation_work_and_incoming_input(compacted_child, monkeypatch):
    record = fail_initialization(compacted_child, monkeypatch)
    assert record['session_state'] == 'failed'
    job = resident_execution_work.read(record)
    assert job['operation'] == 'context_fork_create'
    assert job['outcome'] == 'failed'
    identity = job['execution_id']
    # Acknowledgement is allowed to clean Execution payloads, not the owner intent.
    resident_catalog.get_catalog().state_store.acknowledge_resident_execution(record['resident_id'], identity)
    assert resident_execution_work.read(record)['execution_id'] == identity
    for mid in ['msg_' + 'a1' * 16, 'msg_' + 'a2' * 16]:
        resident_intake_domain.persist_resident_input(record['resident_id'], 'sample_admin', 'keep original input', message_id=mid)
    after = resident_execution_work.read(record)
    assert after['execution_id'] == identity
    assert after['outcome'] == 'failed'
    assert [i['user_message_id'] for i in after['queue']] == ['msg_'+'a1'*16, 'msg_'+'a2'*16]
    assert record['context_compaction_state'] != 'completed'


def prepare_retry(record, monkeypatch):
    monkeypatch.setattr(resident_execution_domain, 'resume_resident_execution', lambda _: {})
    return resident_initialization.retry(record['resident_id'], 'sample_admin')


def candidate_backend(calls, *, fail_compact=False, lost_create=False):
    """Only replace native I/O; production owner, requests and AgentData stay real."""
    def operation(**kw):
        calls.append(kw)
        store = get_agent_data_store()
        eid = 'execution-' + hashlib.sha256(kw['operation_id'].encode()).hexdigest()[:24]
        if kw['operation'] == 'create':
            execution_port.sessions().bind_session(kw['binding_id'], provider='codex', backend_route_id='one',
                                                  backend_session_id='01a00000-0000-7000-8000-000000000919')
        binding = execution_port.sessions().provider_binding(kw['binding_id'])
        store.begin_execution(execution_id=eid, agent_id=kw['agent_id'], actor_ref=kw['actor'], provider='codex', started_at='2026-10-19T00:00:00Z')
        store.bind_execution_provider_session(eid, provider_session_ref=binding['provider_session_ref'])
        if kw['operation'] == 'compact' and fail_compact:
            store.finish_execution(eid, status='failed')
            raise RuntimeError('native outcome unknown after lost Worker')
        if kw['operation'] == 'compact':
            persist_provider_compaction(store, provider_session_ref=binding['provider_session_ref'], agent_id=kw['agent_id'],
                operation_id=eid, actor_ref=kw['actor'], provider='codex', created_at='2026-10-19T00:00:00Z',
                content={'replacement_history': [{'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': 'frozen summary'}]}]})
        store.finish_execution(eid, status='completed')
        if kw['operation'] == 'create' and lost_create:
            raise ConnectionError('completed create reply lost')
        return {'backend_session_id': binding['backend_session_id'], 'usage': {}}
    return operation


def execute(record):
    prepared = resident_execution_domain._prepare_resident_continuation(resident_record_domain.find_resident(record['resident_id']))
    assert prepared
    resident_execution_domain.run_resident_execution(*prepared)
    return resident_record_domain.find_resident(record['resident_id'])


def test_legacy_pending_visible_and_restored_with_original_identity(compacted_child, monkeypatch):
    record = fail_initialization(compacted_child, monkeypatch)
    mid = 'msg_' + 'b1' * 16
    resident_intake_domain.persist_resident_input(record['resident_id'], 'sample_admin', 'original translation', message_id=mid)
    saved = resident_execution_work.read(record)
    saved['queue'] = []
    resident_execution_work.write(record, saved)  # the historical demotion, not a new input
    store = resident_catalog.get_catalog().state_store
    store.delete_resident_execution_record(record['resident_id'])
    assert store.mailbox_entry(record['resident_id'], mid)['place'] == 'pending'
    mailbox = resident_protocol.get_resident_mailbox(record['resident_id'], 'sample_admin')
    assert mid in str(mailbox)
    receipt = prepare_retry(record, monkeypatch)
    assert receipt['queued_count'] == 1
    assert resident_execution_work.read(record)['queue'][0]['user_message_id'] == mid
    again = resident_initialization.retry(record['resident_id'], 'sample_admin')
    assert again['initialization_id'] == receipt['initialization_id']


def test_candidate_promotes_only_after_canonical_compaction_and_releases_one_input(compacted_child, monkeypatch):
    record = fail_initialization(compacted_child, monkeypatch)
    old = resident_backend.session_record(record)
    mid = 'msg_' + 'c1' * 16
    resident_intake_domain.persist_resident_input(record['resident_id'], 'sample_admin', 'waiting business', message_id=mid)
    receipt = prepare_retry(record, monkeypatch)
    calls = []
    monkeypatch.setattr(resident_session_backend_domain, 'run_backend_session_operation', candidate_backend(calls))
    finalize = resident_execution_domain._finalize_applied_execution
    def finalize_without_starting_business(*args):
        assert resident_record_domain.find_resident(record['resident_id'])['context_compaction_state'] == 'completed'
        finalize(*args)
        return False
    monkeypatch.setattr(resident_execution_domain, '_finalize_applied_execution', finalize_without_starting_business)
    current = execute(record)
    assert current['context_compaction_state'] == 'completed', current.get('last_error')
    assert resident_backend.session_record(current)['provider_session_ref'] != old['provider_session_ref']
    assert [i['operation'] for i in calls] == ['create', 'compact']
    assert all(i['binding_id'] != resident_backend.execution_binding_id(record) for i in calls)
    assert all(i['agent_id'] == receipt['candidate_agent_id'] for i in calls)
    job = resident_execution_work.read(current)
    assert job['user_message_id'] == mid
    assert job['queue'] == []
    assert not job.get('submitted_at')  # never ran the user's business in this test
    assert get_agent_data_store().agent_context_snapshot(record['resident_id'])['segments'][0]['segment_type'] == 'compaction'
    session_ref = resident_backend.session_record(current)['provider_session_ref']
    head = get_agent_data_store().session_context_head(session_ref, agent_id=record['resident_id'])
    assert head and head['segment_type'] == 'compaction'


def test_failed_candidate_keeps_original_binding_and_frozen_prefix(compacted_child, monkeypatch):
    record = fail_initialization(compacted_child, monkeypatch)
    store = get_agent_data_store()
    old = resident_backend.session_record(record)
    prefix = store.frozen_fork_segments(record['resident_id'])
    first = prepare_retry(record, monkeypatch)
    calls = []
    monkeypatch.setattr(resident_session_backend_domain, 'run_backend_session_operation', candidate_backend(calls, fail_compact=True))
    current = execute(record)
    assert current['session_state'] == 'failed'
    assert current['context_compaction_state'] != 'completed'
    assert resident_backend.session_record(current) == old
    assert store.frozen_fork_segments(record['resident_id']) == prefix
    second = resident_initialization.retry(record['resident_id'], 'sample_admin')
    assert second['initialization_id'] != first['initialization_id']
    assert second['candidate_binding_id'] != first['candidate_binding_id']


@pytest.mark.parametrize('lost_at', ['create', 'promotion'])
def test_reply_loss_resumes_same_attempt_without_repeating_completed_operations(compacted_child, monkeypatch, lost_at):
    record = fail_initialization(compacted_child, monkeypatch)
    first = prepare_retry(record, monkeypatch)
    calls = []
    monkeypatch.setattr(resident_session_backend_domain, 'run_backend_session_operation', candidate_backend(calls, lost_create=lost_at == 'create'))
    promote = execution_port.promote_initialization_session
    if lost_at == 'promotion':
        def lost_promotion(**kwargs):
            promote(**kwargs)
            raise ConnectionError('committed promotion reply lost')
        monkeypatch.setattr(execution_port, 'promote_initialization_session', lost_promotion)
    current = execute(record)
    assert current['context_compaction_state'] != 'completed'
    second = resident_initialization.retry(record['resident_id'], 'sample_admin')
    assert second['initialization_id'] == first['initialization_id']
    monkeypatch.setattr(execution_port, 'promote_initialization_session', promote)
    monkeypatch.setattr(resident_session_backend_domain, 'run_backend_session_operation', candidate_backend(calls))
    current = execute(record)
    assert current['context_compaction_state'] == 'completed', current.get('last_error')
    assert [c['operation'] for c in calls] == ['create', 'compact']
    assert resident_initialization.view(record['resident_id'], 'sample_admin')['initialization_id'] == first['initialization_id']


def test_binding_change_fences_candidate_promotion(compacted_child, monkeypatch):
    record = fail_initialization(compacted_child, monkeypatch)
    prepare_retry(record, monkeypatch)
    calls = []
    backend = candidate_backend(calls)
    def change_binding(**kwargs):
        result = backend(**kwargs)
        if kwargs['operation'] == 'compact':
            execution_port.sessions().release_session(resident_backend.execution_binding_id(record))
            register_session(record, '01a00000-0000-7000-8000-000000000920', backend_route_id='one')
        return result
    monkeypatch.setattr(resident_session_backend_domain, 'run_backend_session_operation', change_binding)
    prefix = get_agent_data_store().sequence_segment_ids(get_agent_data_store().trace_sequence_id(record['resident_id']))
    current = execute(record)
    assert current['context_compaction_state'] != 'completed'
    assert get_agent_data_store().sequence_segment_ids(get_agent_data_store().trace_sequence_id(record['resident_id'])) == prefix
    assert resident_backend.record_session_id(record).endswith('920')


def test_promotion_rolls_back_binding_and_trace_together(compacted_child, monkeypatch):
    record = fail_initialization(compacted_child, monkeypatch)
    first = prepare_retry(record, monkeypatch)
    store = get_agent_data_store()
    old_ref = resident_backend.session_record(record)['provider_session_ref']
    prefix = store.frozen_fork_segments(record['resident_id'])
    calls = []
    monkeypatch.setattr(resident_session_backend_domain, 'run_backend_session_operation', candidate_backend(calls))
    place = store.trace_edits.place_in_trace_before
    def fail_after_placement(*args, **kwargs):
        place(*args, **kwargs)
        raise RuntimeError('injected failure before promotion commit')
    monkeypatch.setattr(store.trace_edits, 'place_in_trace_before', fail_after_placement)
    current = execute(record)
    assert current['context_compaction_state'] != 'completed'
    assert resident_backend.session_record(record)['provider_session_ref'] == old_ref
    assert store.frozen_fork_segments(record['resident_id']) == prefix
    assert not store.initialization_session_fact(record['resident_id'], resident_backend.execution_binding_id(record))['receipt']
    monkeypatch.setattr(store.trace_edits, 'place_in_trace_before', place)
    assert resident_initialization.retry(record['resident_id'], 'sample_admin')['initialization_id'] == first['initialization_id']
    assert execute(record)['context_compaction_state'] == 'completed'
    assert [c['operation'] for c in calls] == ['create', 'compact']


def test_formal_retry_is_idempotent_and_read_is_not_a_retry(isolated_instruction_store, compacted_child, monkeypatch):
    record = fail_initialization(compacted_child, monkeypatch)
    monkeypatch.setattr(resident_execution_domain, 'resume_resident_execution', lambda _: {})
    before = run('GetResidentInitialization', {'resident_id': record['resident_id']}).raise_for_error()
    assert before['status'] == 'failed'
    first = run('RetryResidentInitialization', {'resident_id': record['resident_id']}, key='initialization-test')
    first.raise_for_error()
    replay = run('RetryResidentInitialization', {'resident_id': record['resident_id']}, key='initialization-test')
    assert replay.replayed
    assert replay.result == first.result
    assert run('GetResidentInitialization', {'resident_id': record['resident_id']}).raise_for_error()['initialization_id'] == first.result['initialization_id']


def test_formal_initialization_authorization(isolated_instruction_store, compacted_child, permissions, monkeypatch):
    record = compacted_child
    permissions.users('viewer_init', category='intern')
    permissions.place(Fact('resident', record['resident_id']))
    monkeypatch.setattr(resident_execution_domain, 'resume_resident_execution', lambda _: {})
    args = {'resident_id': record['resident_id']}
    assert run('GetResidentInitialization', args, actor='viewer_init').status == 'committed'
    refused = run('RetryResidentInitialization', args, actor='viewer_init', key='viewer-denied')
    assert refused.error['code'] == 'instruction.forbidden'
    permissions.grant('viewer_init', record['resident_id'], '@contributor')
    assert run('RetryResidentInitialization', args, actor='viewer_init', key='contributor-allowed').status == 'committed'
    permissions.deny('viewer_init', record['resident_id'])
    permissions.deny('viewer_init', record['resident_id'], '@contributor')
    for instruction in ('GetResidentInitialization', 'RetryResidentInitialization'):
        refused = run(instruction, args, actor='viewer_init')
        assert refused.error['code'] == ('instruction.not_found' if instruction.startswith('Get') else 'instruction.forbidden')
        assert refused.error['details']['reason'] == 'denied'


def test_changed_frozen_prefix_refuses_recovery(compacted_child, monkeypatch):
    record = fail_initialization(compacted_child, monkeypatch)
    store = get_agent_data_store()
    ids = store.frozen_fork_segments(record['resident_id'])
    store.transform_agent_segments(record['resident_id'], ids, segment_id='segment_' + 'd1'*16,
        content='changed source', actor_ref='sample_admin', operation_id='fixture-transform', operation_type='fixture')
    with pytest.raises(ValueError, match='冻结片段已变化'):
        prepare_retry(record, monkeypatch)


def test_already_completed_original_compaction_is_reconciled_without_native_work(compacted_child, monkeypatch):
    record = fail_initialization(compacted_child, monkeypatch)
    store = get_agent_data_store()
    binding = resident_backend.session_record(record)
    eid = 'execution-original-completed'
    store.begin_execution(execution_id=eid, agent_id=record['resident_id'], actor_ref='sample_admin', provider='codex', started_at='2026-10-19T00:00:00Z')
    store.bind_execution_provider_session(eid, provider_session_ref=binding['provider_session_ref'])
    persist_provider_compaction(store, provider_session_ref=binding['provider_session_ref'], agent_id=record['resident_id'],
        operation_id=eid, actor_ref='sample_admin', provider='codex', created_at='2026-10-19T00:00:00Z',
        content={'replacement_history': [{'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': 'original compaction'}]}]})
    store.finish_execution(eid, status='completed')
    prepare_retry(record, monkeypatch)
    monkeypatch.setattr(resident_session_backend_domain, 'run_backend_session_operation', lambda **_: pytest.fail('must not resubmit confirmed operation'))
    current = execute(record)
    assert current['context_compaction_state'] == 'completed'
    assert resident_backend.session_record(record)['provider_session_ref'] == binding['provider_session_ref']


def test_active_original_execution_blocks_retry(compacted_child, monkeypatch):
    record = fail_initialization(compacted_child, monkeypatch)
    get_agent_data_store().begin_execution(execution_id='execution-original-active', agent_id=record['resident_id'],
        actor_ref='sample_admin', provider='codex', started_at='2026-10-19T00:00:00Z')
    with pytest.raises(ValueError, match='仍在执行'):
        prepare_retry(record, monkeypatch)


def test_closed_intake_is_preserved_while_initialization_completes(compacted_child, monkeypatch):
    record = fail_initialization(compacted_child, monkeypatch)
    resident_protocol.change_resident_state(record['resident_id'], 'sample_admin', state_domain='intake', transition='close')
    mid = 'msg_' + 'e1' * 16
    resident_intake_domain.persist_resident_input(record['resident_id'], 'sample_admin', 'held original', message_id=mid)
    prepare_retry(record, monkeypatch)
    calls = []
    monkeypatch.setattr(resident_session_backend_domain, 'run_backend_session_operation', candidate_backend(calls))
    current = execute(record)
    assert current['context_compaction_state'] == 'completed', current.get('last_error')
    job = resident_execution_work.read(current)
    assert resident_execution_work.is_execution_held(job)
    assert job['user_message_id'] == mid
    assert not job.get('submitted_at')
    assert [i['operation'] for i in calls] == ['create', 'compact']


def test_retry_and_input_during_candidate_io_keep_one_attempt_and_all_inputs(compacted_child, monkeypatch):
    record = fail_initialization(compacted_child, monkeypatch)
    resident_protocol.change_resident_state(record['resident_id'], 'sample_admin', state_domain='intake', transition='close')
    first = prepare_retry(record, monkeypatch)
    calls = []
    backend = candidate_backend(calls)
    mids = ['msg_' + 'e2' * 16, 'msg_' + 'e3' * 16]
    def interleaved(**kwargs):
        again = resident_initialization.retry(record['resident_id'], 'sample_admin')
        assert again['initialization_id'] == first['initialization_id']
        mid = mids[0 if kwargs['operation'] == 'create' else 1]
        resident_intake_domain.persist_resident_input(record['resident_id'], 'sample_admin', 'arrived during initialization', message_id=mid)
        return backend(**kwargs)
    monkeypatch.setattr(resident_session_backend_domain, 'run_backend_session_operation', interleaved)
    current = execute(record)
    job = resident_execution_work.read(current)
    assert current['context_compaction_state'] == 'completed', current.get('last_error')
    assert [job['user_message_id'], *[i['user_message_id'] for i in job['queue']]] == mids
    assert resident_execution_work.is_execution_held(job)
    assert [i['operation'] for i in calls] == ['create', 'compact']
    assert all(i['metadata']['fork_context_input']['segments'][0]['provider_turn_id']
               for i in calls if i['operation'] == 'compact')

