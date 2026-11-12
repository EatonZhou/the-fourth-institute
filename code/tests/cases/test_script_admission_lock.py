"""RunScript distinguishes pre-admission lock failure from unknown launch outcome."""
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from psycopg.errors import LockNotAvailable
from management_portal.hatg.contracts import normalize_error
from management_portal.society_service import directory_execution_admission, lineage_aggregate
from management_portal.society_service.script_runs import service


@pytest.mark.parametrize('phase', ['authority', 'directory', 'launch'])
def test_script_lock_commit_boundary(monkeypatch, phase):
    error = LockNotAvailable('canceling statement due to lock timeout')
    released = []

    @contextmanager
    def authority(_):
        if phase == 'authority': raise error
        try: yield
        finally: released.append('authority')

    @contextmanager
    def directory(_):
        if phase == 'directory': raise error
        yield

    monkeypatch.setattr(service, 'owning_lineage', lambda *a: 'lineage_fixture')
    monkeypatch.setattr(service, '_validate', lambda a: {'cwd': '/fixture', 'path': '/fixture/job.py'})
    monkeypatch.setattr(lineage_aggregate, 'execution_authority', authority)
    monkeypatch.setattr(lineage_aggregate, 'lineage_catalog_store', lambda: Mock())
    monkeypatch.setattr(directory_execution_admission, 'admit', directory)
    start = Mock(side_effect=error)
    monkeypatch.setattr(service, '_start', start)
    with pytest.raises(Exception) as raised:
        service.start({}, 'resident_fixture')
    failure = normalize_error(raised.value)
    assert failure.commit_state == ('unknown' if phase == 'launch' else 'not_committed')
    if phase != 'launch':
        assert failure.retryable is True
        start.assert_not_called()
    else:
        start.assert_called_once()
    assert released == ([] if phase == 'authority' else ['authority'])
    from management_portal.society_service.lineage_program_effect_execution import _CallAttempt
    entry = _CallAttempt(context=None, started_at='').executed_entry(SimpleNamespace(
        instruction_id='inst_fixture', status='failed', error=failure.to_dict(), replayed=True))
    assert entry['status'] == ('unresolved' if phase == 'launch' else 'failed')
