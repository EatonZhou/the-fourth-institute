"""A deferred migration must not stop another Resident's recovery scan."""
from dataclasses import replace
import secrets

import pytest

from management_portal.execution.contracts import SessionOptions
from management_portal.society_service import resident_deferred_restart
from tests.cases.test_quota_wait_restart_handoff import execution, resident, RID


def test_stateless_input_is_not_a_session_handoff(execution):
    scheduler, service, request, args = execution
    stateless = replace(request, execution_id='execution-' + secrets.token_hex(12),
                        session=SessionOptions(mode='stateless'))
    scheduler.submit(stateless)
    before = scheduler.snapshot(stateless.execution_id)
    result = service.withdraw_unstarted(stateless.execution_id, **args)
    assert result['withdrawn'] is False
    assert scheduler.snapshot(stateless.execution_id) == before


def test_another_persistent_binding_remains_rejected(execution):
    scheduler, service, request, args = execution
    other = replace(request, execution_id='execution-' + secrets.token_hex(12),
                    session=SessionOptions(mode='persistent', binding_id='resident:other'))
    scheduler.submit(other)
    before = scheduler.snapshot(other.execution_id)
    with pytest.raises(ValueError, match='execution binding changed'):
        service.withdraw_unstarted(other.execution_id, **args)
    assert scheduler.snapshot(other.execution_id) == before


def test_one_failed_handoff_does_not_starve_the_next_resident(resident, monkeypatch, caplog):
    store, repo, operations, original, native = resident
    advance = resident_deferred_restart.advance
    first = {'resident_id': 'resident_000000000000',
             'session_restart': {'deferred': {'phase': 'waiting'}}}
    def unavailable(record):
        if record['resident_id'] == first['resident_id']:
            raise ConnectionError('withdrawal receipt unavailable')
        return advance(record)
    monkeypatch.setattr(resident_deferred_restart, 'advance', unavailable)
    resident_deferred_restart.reconcile([first, store.resident_record(RID)])
    current = store.resident_record(RID)['session_restart']
    assert current['restart_id'] == original['restart_id']
    assert current['deferred']['phase'] == 'migrating'
    assert len(operations) == 1
    assert first['session_restart']['deferred'] == {'phase': 'waiting'}
    assert 'resident_000000000000' in caplog.text
    assert 'withdrawal receipt unavailable' in caplog.text
