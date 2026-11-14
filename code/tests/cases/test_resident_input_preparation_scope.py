"""Send-now prepares immutable input outside the state lock and fences its commit."""
from copy import deepcopy
import pytest
from management_portal.society_service import (
    invariant_locks, resident_execution_work as work,
    resident_input_queue_domain as queue, resident_conversation_stream,
)
from tests.cases.resident_execution_test_support import resident_record  # noqa: F401

EID = 'execution-' + 'a' * 24
MID = 'msg_' + 'b' * 32


def held(resident_id):
    return invariant_locks._depths().get('resident:' + resident_id, 0) > 0


@pytest.fixture
def delivered(resident_record, monkeypatch):
    rid = resident_record['resident_id']
    item = dict(execution_id='job-supplement', user_message_id=MID, actor='CASE-A',
        input_message=dict(message_id=MID, role='user', author_id='CASE-A',
            content_parts=[dict(type='text', text='immutable supplement')], created_at='2026-10-10T00:00:00Z'),
        active_turn_input=dict(control_id='control-supplement', actor='CASE-A'))
    work.write(resident_record, dict(execution_id=EID, actor='CASE-A',
        submitted_at='2026-10-14T00:00:00Z', send_now_inputs=[item]))
    class Service:
        def input_control(self, *_a, **_kw):
            assert not held(rid)
            return {'status': 'delivered'}
    monkeypatch.setattr(queue.execution_port, 'service', lambda: Service())
    return rid, resident_record, resident_conversation_stream.get_repository()


def test_immutable_and_group_reads_are_outside_state_lock(delivered, monkeypatch):
    rid, record, repo = delivered
    observations = []
    for name in ['_group_members', '_validate_stored_message_identity']:
        original = getattr(repo.messages, name)
        def observed(*a, _name=name, _original=original, **kw):
            observations.append((_name, held(rid)))
            assert not held(rid), 'immutable/group read blocks the Resident state lock'
            return _original(*a, **kw)
        monkeypatch.setattr(repo.messages, name, observed)
    assert queue.reconcile_queued_turn_inputs(rid, EID)
    assert observations and all(not flag for _, flag in observations)
    assert not work.send_now_inputs(work.read(record))
    assert repo.message(rid, MID)['message_id'] == MID
    observations.clear()
    assert queue.reconcile_queued_turn_inputs(rid, EID)
    assert observations == [], 'settled receipts do not prepare again'


@pytest.mark.parametrize('race', ['withdraw', 'execution', 'control', 'group'])
def test_preparation_rechecks_exact_queued_input(delivered, monkeypatch, race):
    rid, record, repo = delivered
    original = repo.messages._group_members
    def race_read(*a, **kw):
        assert not held(rid)
        value = original(*a, **kw)
        with queue.resident_record_domain.state_lock(rid):
            current = deepcopy(work.read(record))
            if race == 'withdraw':
                current.pop('send_now_inputs')
            elif race == 'execution':
                current['execution_id'] = 'execution-' + 'c' * 24
            elif race == 'control':
                current['send_now_inputs'][0]['active_turn_input']['control_id'] = 'new-control'
            else:
                current['send_now_inputs'][0]['input_message']['content_parts'].append(
                    dict(type='text', text='regrouped after read'))
            work.write(record, current)
        return value
    monkeypatch.setattr(repo.messages, '_group_members', race_read)
    queue.reconcile_queued_turn_inputs(rid, EID)
    assert repo.message(rid, MID) is None, 'obsolete prepared contents were not committed'


def test_occurrence_change_defers_then_retries_idempotently(delivered, monkeypatch):
    rid, record, repo = delivered
    original = repo.messages.prepare_user_message
    def prepare(*a, **kw):
        value = original(*a, **kw)
        repo.commit_user_message(rid, value.message)
        return value
    monkeypatch.setattr(repo.messages, 'prepare_user_message', prepare)
    assert not queue.reconcile_queued_turn_inputs(rid, EID)
    first = repo.agent_data.chat_message(rid, MID)['segment_id']
    assert work.send_now_inputs(work.read(record))
    monkeypatch.setattr(repo.messages, 'prepare_user_message', original)
    assert queue.reconcile_queued_turn_inputs(rid, EID)
    assert not work.send_now_inputs(work.read(record))
    assert repo.agent_data.chat_message(rid, MID)['segment_id'] == first


@pytest.mark.parametrize('forged', [False, True])
def test_harness_ancestry_is_checked_outside_lock_without_accepting_forgery(delivered, monkeypatch, forged):
    rid, record, repo = delivered
    original_input = deepcopy(work.read(record)['send_now_inputs'][0]['input_message'])
    repo.commit_user_message(rid, original_input)
    repo.agent_data.update_chat_message(rid, MID, {
        **original_input, 'content_parts': [{'type':'text', 'text':'Harness wrapped input'}]})
    if forged:
        current = deepcopy(work.read(record))
        current['send_now_inputs'][0]['input_message']['content_parts'] = [{'type':'text', 'text':'forged original'}]
        work.write(record, current)
    observed = []
    original = repo.agent_data.chat_message_ancestors
    def ancestors(*a, **kw):
        observed.append(held(rid))
        assert not held(rid), 'provenance SQL blocked the Resident lock'
        return original(*a, **kw)
    monkeypatch.setattr(repo.agent_data, 'chat_message_ancestors', ancestors)
    if forged:
        with pytest.raises(ValueError, match='different immutable content'):
            queue.reconcile_queued_turn_inputs(rid, EID)
        assert work.send_now_inputs(work.read(record))
    else:
        assert queue.reconcile_queued_turn_inputs(rid, EID)
        assert not work.send_now_inputs(work.read(record))
        assert repo.message(rid, MID)['content_parts'][0]['text'] == 'Harness wrapped input'
    assert observed == [False], 'one provenance lookup outside the state scope'
