"""Durable unstarted handoffs, not terminal status, authorize graph succession."""
import pytest

from management_portal.execution import execution_inputs, execution_lifecycle
from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.society_service import (
    resident_deferred_restart, resident_execution_work, resident_restart_domain,
    trigger_paths, trigger_path_storage,
)
from tests.cases.test_quota_wait_restart_handoff import RID, MID, execution, resident

pytestmark = pytest.mark.usefixtures('registered_execution_defaults')


@pytest.fixture
def handoff(resident, execution):
    store, repo, operations, original, native = resident
    _, _, request, _ = execution
    lineage = store.resident_record(RID)['lineage_id']
    event = dict(id='guard-event', type='time.reached', occurred_at='2026-10-19T00:00:00Z',
                 payload={'handoff_test': 'guards'})
    path = trigger_paths.begin_event(lineage, 'source', event, cause_kind='timer', cause_id='source')
    trigger_paths.admit_resident(lineage, resident_id=RID, message_id=MID,
        trigger_path_id=path, source_wire_id='wire', event=event)
    trigger_paths.event_settled(path, 'source', event['id'])
    assert resident_deferred_restart.advance(store.resident_record(RID))
    record = store.resident_record(RID)
    receipt = record['session_restart']['deferred']['handoff']
    replacement = receipt['replacement_execution_id']
    leaf = next(s['step_id'] for s in trigger_paths.get(lineage, path)['steps'] if s['kind'] == 'resident')
    return store, repo, operations, request.execution_id, replacement, lineage, path, leaf


def reserve(identity, *, resident_id=RID, actor='alice', message_id=MID):
    agent = AgentDataStore()
    with agent.database.transaction() as c:
        execution_lifecycle.reserve(c, execution_id=identity, agent_id=resident_id, actor=actor,
                                    provider='codex', created_at='2026-10-19T00:10:00Z')
        execution_inputs.attach_in(c, identity, [{'message_id': message_id, 'role': 'primary'}])


@pytest.mark.parametrize('bad', ['resident', 'actor', 'input', 'replacement', 'started', 'receipt', 'path', 'step', 'replacement_started', 'replacement_terminal'])
def test_only_exact_unstarted_handoff_can_extend_active_path(handoff, bad):
    store, _, _, old, new, lineage, path, leaf = handoff
    target = 'execution-not-designated' if bad == 'replacement' else new
    reserve(target, resident_id='resident_other' if bad == 'resident' else RID,
            actor='other' if bad == 'actor' else 'alice', message_id='msg_other' if bad == 'input' else MID)
    if bad in {'started', 'receipt'}:
        with AgentDataStore().database.transaction() as c:
            if bad == 'started':
                c.execute('UPDATE executions SET started_at=created_at WHERE execution_id=%s', (old,))
            else:
                c.execute('DELETE FROM agent_operations WHERE operation_id=%s', ('execution_handoff:' + old,))
    if bad in {'replacement_started', 'replacement_terminal'}:
        with AgentDataStore().database.transaction() as c:
            if bad == 'replacement_started':
                c.execute("UPDATE executions SET status='running',started_at=created_at WHERE execution_id=%s", (target,))
            else:
                c.execute("UPDATE executions SET status='completed',finished_at=created_at WHERE execution_id=%s", (target,))
    if bad in {'path', 'step'}:
        with store.database.transaction() as c:
            if bad == 'path':
                trigger_path_storage.cancel(c, path, at=trigger_paths.now())
            else:
                # Corrupt-state guard, not a permitted production transition.
                c.execute("UPDATE trigger_path_steps SET status='failed' WHERE step_id=%s", (leaf,))
    with pytest.raises(ValueError):
        trigger_paths.link_input(lineage, RID, MID, target)
    assert {r['execution_id'] for r in trigger_paths.get(lineage, path)['execution_links']} == {old}


@pytest.mark.parametrize('status', ['completed', 'failed', 'cancelled'])
def test_business_outcome_and_failure_query_follow_designated_successor(handoff, status):
    store, _, operations, old, new, lineage, path, _ = handoff
    assert trigger_paths.settle(path) is False
    pending = trigger_paths.resident_inputs(lineage, pending=True)
    assert pending[0]['status'] == 'queued' and pending[0]['completed_at'] == ''
    assert trigger_paths.find_event_paths(lineage, {'handoff_test': 'guards'})[0]['failures'] == []
    resident_restart_domain.resume_resident_session_restart(operations[-1]['step'])
    resident_deferred_restart.advance(store.resident_record(RID))
    agent = AgentDataStore()
    agent.finish_execution(new, status=status)
    assert trigger_paths.settle(path)
    assert trigger_paths.get(lineage, path)['status'] == status
    assert bool(trigger_paths.find_event_paths(lineage, {'handoff_test': 'guards'})[0]['failures']) == (status != 'completed')
    assert trigger_paths.resident_inputs(lineage)[0]['status'] == status
    assert agent.execution(old)['status'] == 'cancelled'
    assert not trigger_paths.settle(path)
    trigger_paths.link_input(lineage, RID, MID, new)  # exact replay is harmless even after terminal


def test_failed_release_rolls_back_links_reservation_and_reenters_same_handoff(handoff, monkeypatch):
    store, repo, operations, old, new, lineage, path, _ = handoff
    resident_restart_domain.resume_resident_session_restart(operations[-1]['step'])
    link = trigger_path_storage.link_execution
    def fail_after_link(c, step, execution_id):
        link(c, step, execution_id)
        if execution_id == new:
            raise ConnectionError('interrupted after association')
    with monkeypatch.context() as patch:
        patch.setattr(trigger_path_storage, 'link_execution', fail_after_link)
        with pytest.raises(ConnectionError, match='interrupted'):
            resident_deferred_restart.advance(store.resident_record(RID))
    assert resident_execution_work.read(store.resident_record(RID))['execution_id'] == old
    with AgentDataStore().database.read() as c:
        assert c.execute('SELECT 1 FROM executions WHERE execution_id=%s', (new,)).fetchone() is None
    assert {r['execution_id'] for r in trigger_paths.get(lineage, path)['execution_links']} == {old}
    assert store.resident_record(RID)['session_restart']['deferred']
    assert repo.input_state(RID, MID)['state'] == 'queued'
    resident_deferred_restart.advance(store.resident_record(RID))
    assert resident_execution_work.read(store.resident_record(RID))['execution_id'] == new
    assert not store.resident_record(RID)['session_restart'].get('deferred')
    assert len(operations) == 1
