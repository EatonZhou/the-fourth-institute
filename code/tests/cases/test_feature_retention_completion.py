"""Feature entry points keep lifecycle leases and durable end-of-work cleanup.

:design: decision_4aa13bc21e8e
:profiles: local.fixture
:risk: no-external-side-effects
"""
from datetime import timedelta
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

from management_common.database_time import instant
from management_common.directory_retention import RetentionStore
from management_common.resource_lifecycle import LifecycleStore, current_lifecycle, lifecycle_scope
from management_portal import business_tasks, paper_collection_jobs as jobs, paper_state_store
from tests.cases.test_retention_consumers import artifact


def test_deep_read_real_thread_holds_test_lease_and_tracks_files(tmp_path, monkeypatch):
    from management_portal import paper_reading_jobs as reading
    from management_common.resource_identity import get_resource_type_registry
    from management_portal.test_framework.run_cleanup import finish_run
    store = LifecycleStore()
    run_id = 'testrun_feature_reading'
    scope = store.open(str(tmp_path), actor='alice', run_id=run_id, case_id='deep-read')
    entered, release, done = Event(), Event(), Event()
    seen = []
    path = tmp_path / 'reading-work'
    job = {'job_id': 'deep-read-feature', 'status': 'queued'}
    paper_state_store.append('deep_read_jobs', job)
    monkeypatch.setattr(reading, '_latest_deep_read_job', lambda **kw: job)

    def work(job_id):
        try:
            path.mkdir()
            (path / 'trace').write_text('isolated background output')
            RetentionStore().declare(path, category='background', owner_id=job_id)
            seen.append((current_lifecycle(), get_resource_type_registry().new_id('paper')))
            entered.set()
            assert release.wait(10)
        finally:
            done.set()

    monkeypatch.setattr(reading, '_run_deep_read_job', work)
    try:
        with lifecycle_scope(scope, actor='alice'):
            reading._launch_deep_read_business_task(job)
        assert entered.wait(5)
        assert seen[0][0] == scope
        assert artifact(path)['test_run_id'] == run_id
        assert store.pending(scope) == [seen[0][1]]
        with pytest.raises(TimeoutError, match='active requests'):
            store.seal(scope, timeout=0.1)
        reading._launch_deep_read_business_task(job)
        assert len(seen) == 1
    finally:
        release.set()
        assert done.wait(5)
        store.seal(scope, timeout=5)
        for identifier in store.pending(scope):
            store.released(scope, identifier)  # Allocated identity only; no Paper object exists.
        finish_run(run_id)
    assert not path.exists()


@pytest.mark.parametrize('conversion_fails', [False, True])
def test_presentation_occupied_work_is_durable_and_retryable(tmp_path, monkeypatch, conversion_fails):
    import os
    from management_portal import presentation_preview as preview
    from management_common.directory_cleanup_store import DirectoryCleanupStore
    monkeypatch.setattr(preview, 'instance_runtime_root', lambda: tmp_path)
    monkeypatch.setattr(preview, 'libreoffice', lambda: 'fixture-soffice')
    work = []
    declared = []
    def convert(command, **options):
        directory = Path(options['cwd'])
        work.append(directory)
        retention = RetentionStore()
        with retention.database.read() as connection:
            declared.extend(connection.execute(f'SELECT category FROM {retention.table}').fetchall())
        (directory / 'slides.pdf').write_bytes(b'%PDF-fixture')
        return SimpleNamespace(returncode=1 if conversion_fails else 0)
    monkeypatch.setattr(preview, 'run_process', convert)
    real_unlink = os.unlink
    def occupied(path, *args, **kwargs):
        if Path(path).name == 'slides.pptx':
            raise PermissionError('fixture: occupied conversion directory')
        return real_unlink(path, *args, **kwargs)
    with monkeypatch.context() as failure:
        failure.setattr(os, 'unlink', occupied)
        if conversion_fails:
            with pytest.raises(preview.PresentationPreviewError, match='损坏'):
                preview.presentation_pdf(b'fixture', 'slides.pptx')
        else:
            pdf = preview.presentation_pdf(b'fixture', 'slides.pptx')
            assert pdf.read_bytes().startswith(b'%PDF-')
        assert any(row['category'] == 'runtime_tmp' for row in declared)
        queue = DirectoryCleanupStore()
        receipts = queue.status()
        assert len(receipts) == 1 and receipts[0]['status'] == 'retry'
        assert 'occupied conversion directory' in receipts[0]['last_error']
        assert list(tmp_path.rglob('slides.pptx'))
    # Reopen the durable queue and invoke its allocation owner, as maintenance
    # does. Reserved cleanup retains its inode claim until that owner retries.
    from management_common.file_retention import recover_temporary_directories
    queue = DirectoryCleanupStore()
    with queue.database.transaction() as connection:
        connection.execute(f'UPDATE {queue.table} SET available_at=clock_timestamp()')
    assert not recover_temporary_directories()['errors']
    assert queue.status()[0]['status'] == 'completed'
    assert not work[0].exists()
    assert not list(tmp_path.rglob('slides.pptx'))


@pytest.fixture
def collection(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, '_notify_paper_projection', lambda *a, **kw: None)
    monkeypatch.setattr(jobs, 'PAPER_COLLECTOR_WORK_ROOT', tmp_path)
    def create(*, status='running', owner=None, updated_at=100, deleted=False):
        job_id = 'collector-retention'
        job = {'job_id': job_id, 'status': status, 'created_by': 'alice',
               'created_at': 1, 'updated_at': updated_at, 'deleted': deleted,
               'process_owner': owner or {}}
        paper_state_store.append('collector_jobs', job)
        path = tmp_path / job_id
        path.mkdir()
        (path / 'stdout.json').write_text('fixture')
        RetentionStore().declare(path, category='background', owner_id=job_id, created_at=instant(1))
        return job, path
    return create


@pytest.mark.parametrize('transition', ['stale', 'error', 'done', 'cancelled', 'delete'])
def test_collector_terminal_entry_persists_deadline_atomically(collection, monkeypatch, transition):
    job, path = collection()
    monkeypatch.setattr(jobs, '_now', lambda: 200000)
    if transition == 'stale':
        assert jobs.list_paper_collection_jobs()['jobs'][0]['status'] == 'error'
    elif transition == 'delete':
        jobs.delete_paper_collection_job({'job_id': job['job_id']})
    else:
        jobs._set_paper_collection_job(job['job_id'], {'status': transition})
    assert artifact(path)['eligible_at'] == instant(200000) + timedelta(days=7)
    retention = RetentionStore()
    assert retention.sweep(now=instant(200000) + timedelta(days=7, seconds=-1))['queued'] == 0
    assert path.exists()
    assert retention.sweep(now=instant(200000) + timedelta(days=7))['queued'] == 1
    retention.queue.run_pending()
    assert not path.exists()


def test_retention_failure_rolls_back_terminal_transition(collection, monkeypatch):
    job, path = collection()
    def fail(*args, **kwargs):
        raise RuntimeError('fixture retention unavailable')
    monkeypatch.setattr(RetentionStore, 'finish', fail)
    with pytest.raises(RuntimeError, match='retention unavailable'):
        jobs._set_paper_collection_job(job['job_id'], {'status': 'error'})
    assert paper_state_store.get('collector_jobs', job['job_id'])['status'] == 'running'
    assert artifact(path)['eligible_at'] is None


@pytest.mark.parametrize('status', ['done', 'error', 'cancelled', 'deleted'])
def test_terminal_read_reconciles_missing_deadline_without_mtime(collection, monkeypatch, status):
    import os
    job, path = collection(status=status, updated_at=200000, deleted=status == 'deleted')
    os.utime(path, (1, 1))
    monkeypatch.setattr(jobs, '_now', lambda: 900000)
    jobs.list_paper_collection_jobs()
    assert artifact(path)['eligible_at'] == instant(200000) + timedelta(days=7)
    jobs.list_paper_collection_jobs()
    assert paper_state_store.get('collector_jobs', job['job_id'])['updated_at'] == 200000
    assert artifact(path)['eligible_at'] == instant(200000) + timedelta(days=7)


def test_delete_preserves_live_collector_until_writer_finishes(collection, monkeypatch):
    job, path = collection(owner=business_tasks.process_identity())
    jobs.delete_paper_collection_job({'job_id': job['job_id']})
    jobs.list_paper_collection_jobs()
    retention = RetentionStore()
    assert artifact(path)['eligible_at'] is None
    assert retention.sweep(now=instant(9000000000))['queued'] == 0
    assert path.exists()
    monkeypatch.setattr(jobs, '_now', lambda: 300000)
    jobs._finish_paper_collection_job(job['job_id'])
    assert artifact(path)['eligible_at'] == instant(300000) + timedelta(days=7)
    assert paper_state_store.get('collector_jobs', job['job_id'])['status'] == 'deleted'
    monkeypatch.setattr(jobs, '_now', lambda: 400000)
    jobs._finish_paper_collection_job(job['job_id'])
    assert artifact(path)['eligible_at'] == instant(300000) + timedelta(days=7)
    assert paper_state_store.get('collector_jobs', job['job_id'])['updated_at'] == 300000


def test_terminal_missing_file_is_not_expiry(collection):
    job, path = collection(status='done', updated_at=200000)
    (path / 'stdout.json').unlink()
    path.rmdir()
    jobs.list_paper_collection_jobs()
    assert artifact(path)['eligible_at'] == instant(200000) + timedelta(days=7)
    assert RetentionStore().queue.status(owner_id=job['job_id']) == []


def test_maintenance_reconciles_hidden_terminal_writer_without_list_read(collection):
    from management_portal import directory_maintenance
    job, path = collection(status='deleted', deleted=True, updated_at=200000)
    report = directory_maintenance.run(test_roots=[], now=instant(200001))
    assert report['paper_collections'] == {'checked': 1, 'errors': []}
    assert report['paper_figures']['errors'] == []
    assert artifact(path)['eligible_at'] == instant(200000) + timedelta(days=7)
    assert path.exists()


def test_maintenance_keeps_unfinished_and_unobservable_writers(collection):
    job, path = collection(status='running', updated_at=1)
    result = jobs.reconcile_collection_retention()
    assert result == {'checked': 0, 'errors': []}
    assert artifact(path)['eligible_at'] is None
    paper_state_store.merge('collector_jobs', job['job_id'], {
        'deleted': True, 'status': 'deleted', 'process_owner': {'host': 'unobservable-fixture-host'},
    })
    assert jobs.reconcile_collection_retention() == {'checked': 1, 'errors': []}
    assert artifact(path)['eligible_at'] is None


def test_maintenance_isolates_unknown_terminal_clock(collection):
    job, path = collection(status='done', updated_at=200000)
    paper_state_store.append('collector_jobs', {
        'job_id': 'collector-unknown-clock', 'status': 'deleted', 'deleted': True,
        'process_owner': {}, 'updated_at': None, 'created_at': None,
    })
    result = jobs.reconcile_collection_retention()
    assert result['checked'] == 1
    assert len(result['errors']) == 1 and 'collector-unknown-clock' in result['errors'][0]
    assert artifact(path)['eligible_at'] == instant(200000) + timedelta(days=7)


def test_dead_process_terminal_reconciliation_persists_observed_end(collection, monkeypatch):
    import json
    import subprocess
    import sys
    result = subprocess.run([sys.executable, '-c',
        'import json, os; from management_portal.business_tasks import process_identity; '
        'print(json.dumps(process_identity()), flush=True); os._exit(0)'],
        check=True, capture_output=True, text=True)
    owner = json.loads(result.stdout)
    assert not business_tasks.process_is_alive(owner)
    job, path = collection(status='deleted', deleted=True, owner=owner)
    monkeypatch.setattr(jobs, '_now', lambda: 400000)
    jobs.list_paper_collection_jobs()
    assert artifact(path)['eligible_at'] == instant(400000) + timedelta(days=7)
    current = paper_state_store.get('collector_jobs', job['job_id'])
    assert current['process_owner'] == {} and current['updated_at'] == 400000
    monkeypatch.setattr(jobs, '_now', lambda: 500000)
    jobs.list_paper_collection_jobs()
    assert artifact(path)['eligible_at'] == instant(400000) + timedelta(days=7)


def test_collector_process_exit_skips_finally_then_stale_owner_finishes(collection, monkeypatch):
    import subprocess
    import sys
    from management_portal import paper_collector
    job, path = collection()
    paper_state_store.merge('collector_jobs', job['job_id'], {
        'collector_contract': paper_collector.CONTRACT, 'direction': 'fixture',
        'max_papers': 1, 'requested_message_id': 'msg_fixture',
    })
    program = '''
import os, sys
from pathlib import Path
from management_portal import business_tasks, paper_collection_jobs as jobs, paper_collector, paper_projection
job_id, work_root = sys.argv[1:]
jobs.PAPER_COLLECTOR_WORK_ROOT = Path(work_root)
jobs._notify_paper_projection = lambda *a, **k: None
jobs.resource_index_papers = lambda **k: []
jobs._set_paper_collection_job(job_id, {'process_owner': business_tasks.process_identity()})
def crash(**options):
    (Path(work_root) / job_id / 'interrupted.marker').write_text('writer reached turn')
    os._exit(17)
paper_projection.turn = crash
with paper_collector.authenticated_request({'type': 'human', 'id': 'alice'}):
    jobs._run_paper_collection_job(job_id)
'''
    result = subprocess.run([sys.executable, '-c', program, job['job_id'], str(path.parent)],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 17, result.stderr
    assert (path / 'interrupted.marker').read_text() == 'writer reached turn'
    current = paper_state_store.get('collector_jobs', job['job_id'])
    assert current['status'] == 'running'
    assert not business_tasks.process_is_alive(current['process_owner'])
    assert artifact(path)['eligible_at'] is None
    confirmed_at = current['updated_at'] + jobs.COLLECTOR_STALE_SECONDS + 1
    monkeypatch.setattr(jobs, '_now', lambda: confirmed_at)
    assert jobs.list_paper_collection_jobs()['jobs'][0]['status'] == 'error'
    assert artifact(path)['eligible_at'] == instant(confirmed_at) + timedelta(days=7)


def test_terminal_without_time_requires_owner_confirmation(collection):
    job, path = collection(status='done', updated_at=None)
    with pytest.raises(ValueError, match='no confirmed terminal time'):
        jobs.list_paper_collection_jobs()
    assert artifact(path)['eligible_at'] is None
    assert RetentionStore().queue.status(owner_id=job['job_id']) == []


def test_explicit_writer_confirmation_supplies_missing_terminal_clock(collection, monkeypatch):
    job, path = collection(status='done', updated_at=None)
    monkeypatch.setattr(jobs, '_now', lambda: 500000)
    jobs._finish_paper_collection_job(job['job_id'])
    assert artifact(path)['eligible_at'] == instant(500000) + timedelta(days=7)
    assert paper_state_store.get('collector_jobs', job['job_id'])['updated_at'] == 500000


def test_unknown_remote_writer_is_not_expired(collection):
    job, path = collection(status='deleted', deleted=True, owner={'host': 'unobservable-fixture-host'})
    jobs.list_paper_collection_jobs()
    assert artifact(path)['eligible_at'] is None
    assert RetentionStore().sweep(now=instant(9000000000))['queued'] == 0
    assert path.exists()


def test_real_collector_deleted_during_turn_finishes_only_after_last_file_write(collection, monkeypatch):
    from management_portal import paper_collector, paper_projection
    from time import monotonic, sleep
    job, path = collection(owner=business_tasks.process_identity())
    job = paper_state_store.merge('collector_jobs', job['job_id'], {
        'collector_contract': paper_collector.CONTRACT, 'direction': 'fixture',
        'max_papers': 1, 'requested_message_id': 'msg_fixture',
    })
    entered, release = Event(), Event()
    monkeypatch.setattr(jobs, 'resource_index_papers', lambda **kw: [])
    def turn(**options):
        entered.set()
        assert release.wait(5)
        assert options['cancellation_requested']()
        raise RuntimeError('fixture turn stopped after deletion')
    monkeypatch.setattr(paper_projection, 'turn', turn)
    task_id = 'paper-collection:' + job['job_id']
    try:
        with paper_collector.authenticated_request({'type': 'human', 'id': 'alice'}):
            jobs._start_paper_collection_task(job)
        assert entered.wait(5)
        jobs.delete_paper_collection_job({'job_id': job['job_id']})
        jobs._set_paper_collection_job(job['job_id'], {'status': 'running', 'step': 'late progress'})
        assert paper_state_store.get('collector_jobs', job['job_id'])['status'] == 'deleted'
        assert artifact(path)['eligible_at'] is None
        assert RetentionStore().sweep(now=instant(9000000000))['queued'] == 0
        monkeypatch.setattr(jobs, '_now', lambda: 600000)
    finally:
        release.set()
        deadline = monotonic() + 5
        while business_tasks.is_active(task_id) and monotonic() < deadline:
            sleep(0.01)
        assert not business_tasks.is_active(task_id)
    assert (path / 'stderr.log').read_text() == 'fixture turn stopped after deletion'
    assert artifact(path)['eligible_at'] == instant(600000) + timedelta(days=7)
    assert paper_state_store.get('collector_jobs', job['job_id'])['status'] == 'deleted'


@pytest.mark.parametrize('failure', ['launch', 'before_run', 'deleted_before_run', 'deleted_launch'])
def test_collector_early_exit_releases_claimed_work(collection, monkeypatch, failure):
    from management_portal import paper_collector
    from time import monotonic, sleep
    job, path = collection(owner=business_tasks.process_identity())
    job = paper_state_store.merge('collector_jobs', job['job_id'], {
        'collector_contract': paper_collector.CONTRACT,
    })
    monkeypatch.setattr(jobs, '_now', lambda: 700000)
    def fail(*a, **kw):
        raise RuntimeError('fixture early failure')
    if failure.startswith('deleted'):
        jobs.delete_paper_collection_job({'job_id': job['job_id']})
    if failure in {'launch', 'deleted_launch'}:
        monkeypatch.setattr(business_tasks, 'start', fail)
    elif failure == 'before_run':
        monkeypatch.setattr(jobs, '_run_paper_collection_job', fail)
    with paper_collector.authenticated_request({'type': 'human', 'id': 'alice'}):
        jobs._start_paper_collection_task(job)
    task_id = 'paper-collection:' + job['job_id']
    deadline = monotonic() + 5
    while business_tasks.is_active(task_id) and monotonic() < deadline:
        sleep(0.01)
    assert not business_tasks.is_active(task_id)
    assert artifact(path)['eligible_at'] == instant(700000) + timedelta(days=7)
    saved = paper_state_store.get('collector_jobs', job['job_id'])
    assert saved['status'] == ('deleted' if failure.startswith('deleted') else 'error')
    assert saved['process_owner'] == {}


def test_terminal_write_failure_does_not_expire_running_collector(collection, monkeypatch):
    from management_portal import paper_collector, paper_projection
    job, path = collection()
    paper_state_store.merge('collector_jobs', job['job_id'], {
        'collector_contract': paper_collector.CONTRACT, 'direction': 'fixture',
        'max_papers': 1, 'requested_message_id': 'msg_fixture'})
    monkeypatch.setattr(jobs, 'resource_index_papers', lambda **k: [])
    original = jobs._set_paper_collection_job
    def set_job(identifier, patch):
        if patch.get('status') == 'error':
            raise RuntimeError('terminal write unavailable')
        return original(identifier, patch)
    monkeypatch.setattr(jobs, '_set_paper_collection_job', set_job)
    monkeypatch.setattr(jobs, '_now', lambda: 800000)
    def turn(**kw):
        raise RuntimeError('business failed')
    monkeypatch.setattr(paper_projection, 'turn', turn)
    with paper_collector.authenticated_request({'type': 'human', 'id': 'alice'}):
        with pytest.raises(RuntimeError, match='terminal write unavailable'):
            jobs._run_paper_collection_job(job['job_id'])
    current = paper_state_store.get('collector_jobs', job['job_id'])
    row = artifact(path)
    print({'status': current['status'], 'process_owner': current['process_owner'], 'eligible_at': str(row['eligible_at'])})
    assert current['status'] == 'running'
    assert row['eligible_at'] is None, 'nonterminal job received durable expiry'
