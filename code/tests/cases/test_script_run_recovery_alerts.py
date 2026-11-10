"""Receipt loss releases exclusivity; alerts follow distinct failure episodes.

:design: decision_8b510cbd9c58
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from scripts import portal_background_jobs as jobs
from management_portal.society_service.script_runs import events, runtime, service, store, worker


@pytest.mark.parametrize('code,output,expected', [
    (0, 'LoadState=not-found\nActiveState=inactive\nSubState=dead', 'stopped'),
    (0, 'LoadState=loaded\nActiveState=failed\nSubState=failed', 'stopped'),
    (0, 'LoadState=loaded\nActiveState=active\nSubState=running', 'running'),
    (0, 'LoadState=loaded\nActiveState=deactivating\nSubState=stop-sigterm', 'running'),
    (1, '', 'unknown'), (0, '', 'unknown'),
])
def test_native_owner_state_without_receipts(monkeypatch, tmp_path, code, output, expected):
    monkeypatch.setattr(jobs.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=code, stdout=output))
    monkeypatch.setattr(service, 'sys', SimpleNamespace(platform='linux'))
    monkeypatch.setattr(runtime, 'RUN_ROOT', tmp_path)
    assert service.running_state({'script_run_id': 'gone', 'unit': 'portal-script-gone'}) == expected


def test_missing_result_releases_exclusive_key_but_unknown_does_not(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime, 'RUN_ROOT', tmp_path)
    monkeypatch.setattr(service, 'revoke', lambda r: None)
    monkeypatch.setattr(events, 'publish', lambda r: None)
    record = {'script_run_id': 'gone', 'lineage_id': 'lineage_a', 'status': 'running',
              'unit': 'gone', 'exclusive_key': 'maintenance',
              'started_at': (datetime.now(timezone.utc)-timedelta(days=3)).isoformat()}
    store.admit(record)
    owner = worker.Worker()
    monkeypatch.setattr(owner, 'owns_lease', lambda: True)
    monkeypatch.setattr(service, 'running_state', lambda r: 'unknown')
    owner.reconcile('gone', store.get('gone'))
    assert store.admit({**record, 'script_run_id': 'new'})[1] == 'already_running'
    monkeypatch.setattr(service, 'running_state', lambda r: 'stopped')
    owner.reconcile('gone', store.get('gone'))
    assert store.get('gone')['status'] == 'lost'
    assert store.admit({**record, 'script_run_id': 'new'})[1] == 'created'


def test_failure_episodes_survive_retries_change_and_recovery():
    record = {'script_run_id': 'first', 'lineage_id': 'lineage_a', 'effect_id': 'effect_a',
              'path': '/job.py', 'status': 'failed', 'exit_code': 1, 'stderr_tail': 'denied'}
    assert events.first_failure(record)
    assert events.first_failure(record)  # A retry uses the same dispatch cursor.
    assert not events.first_failure({**record, 'script_run_id': 'repeat'})
    assert events.first_failure({**record, 'script_run_id': 'changed', 'stderr_tail': 'disk full'})
    assert not events.first_failure({**record, 'script_run_id': 'cancel', 'status': 'cancelled'})
    assert not events.first_failure({**record, 'script_run_id': 'admin', 'status': 'lost',
                                     'error_code': 'script.orphan_recovered'})
    assert not events.first_failure({**record, 'script_run_id': 'repeat2', 'stderr_tail': 'disk full'})
    assert not events.first_failure({**record, 'status': 'succeeded'})
    assert events.first_failure({**record, 'script_run_id': 'regression', 'stderr_tail': 'disk full'})


def test_cancelling_collected_unit_is_safe_only_when_stopped(monkeypatch, tmp_path):
    monkeypatch.setattr(service, 'sys', SimpleNamespace(platform='linux'))
    monkeypatch.setattr(runtime, 'RUN_ROOT', tmp_path)
    monkeypatch.setattr(service.process_runtime, 'run_process',
                        lambda *a, **k: SimpleNamespace(returncode=5, stderr='unit not loaded'))
    monkeypatch.setattr(jobs, 'job_state', lambda *a: 'stopped')
    assert service._stop_run({'script_run_id': 'gone', 'unit': 'gone'}, tmp_path/'result.json')


def test_delayed_failure_cannot_reopen_recovered_episode():
    row = {'script_run_id': 'one', 'lineage_id': 'lineage_a', 'effect_id': 'effect_a',
           'path': '/job.py', 'status': 'failed', 'finished_at': '2026-11-10T01:00:00Z'}
    assert events.first_failure(row)
    assert not events.first_failure({**row, 'script_run_id': 'two', 'finished_at': '2026-11-10T02:00:00Z'})
    assert events.first_failure(row)  # Publishing the first failure may still need retrying.
    assert not events.first_failure({**row, 'status': 'succeeded', 'finished_at': '2026-11-10T03:00:00Z'})
    assert not events.first_failure(row)
    assert events.first_failure({**row, 'script_run_id': 'regression', 'finished_at': '2026-11-10T04:00:00Z'})


def test_completion_observers_keep_cancelled_events_without_failure_alerts(monkeypatch):
    monkeypatch.setattr(events.lineage_aggregate, 'read_lineage_state', lambda _: {})
    monkeypatch.setattr(events.lineage_program_queries, '_compiled_program', lambda _: {'event_sources': {
        name: {'event_type': 'script.finished', 'outcome': name, 'subject_effect_ids': ['effect_a']}
        for name in ('any', 'failed', 'succeeded')}})
    deliveries, notices = [], []
    monkeypatch.setattr(events.lineage_domain, 'dispatch_lineage_program_event', lambda *a, **k: deliveries.append(a[1]))
    monkeypatch.setattr(events.lineage_failure_notifications, 'notify', lambda *a, **k: notices.append(k))
    row = {'script_run_id': 'cancel', 'lineage_id': 'lineage_a', 'effect_id': 'effect_a',
           'trigger_path_id': '', 'caller_id': 'sample_admin', 'path': '/job.py',
           'finished_at': service.now(), 'status': 'cancelled'}
    events.publish(row)
    events.publish({**row, 'script_run_id': 'admin', 'status': 'lost', 'error_code': 'script.orphan_recovered'})
    assert deliveries == ['any', 'any'] and not notices
    deliveries.clear()
    events.publish({**row, 'script_run_id': 'failure', 'status': 'failed'})
    events.publish({**row, 'script_run_id': 'repeat', 'status': 'failed'})
    assert deliveries.count('failed') == 1 and deliveries.count('any') == 2
    assert len(notices) == 1
