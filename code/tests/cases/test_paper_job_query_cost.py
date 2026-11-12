"""Latest-job reads must stay bounded and preserve the persisted chronology.

:importance: P1
:aspects: functional, performance
"""
import pytest

from management_portal import paper_reading_jobs as jobs, paper_state_store as store, permission_system


def add(kind, identity, paper='visible', created=100.0, status='done'):
    return store.append(kind, {'job_id': identity, 'paper_id': paper, 'created_at': created, 'status': status})


@pytest.mark.parametrize('kind', ['deep_read_jobs', 'exp_jobs'])
def test_latest_preserves_fractional_time_and_stable_id(kind, isolated_portal_case):
    with isolated_portal_case():
        add(kind, 'older', created=100.1)
        add(kind, 'newer', created=100.9)
        assert jobs._latest_job(kind, paper_id='visible')['job_id'] == 'newer'
        add(kind, 'tie-z', created=100.9)
        add(kind, 'tie-a', created=100.9)
        assert jobs._latest_job(kind, paper_id='visible')['job_id'] == 'tie-z'
        assert jobs._latest_job(kind, job_id='older')['job_id'] == 'older'
        assert jobs._latest_job(kind, paper_id='missing') is None


@pytest.mark.parametrize('kind', ['deep_read_jobs', 'exp_jobs'])
def test_latest_does_not_materialize_old_jobs(kind, isolated_portal_case, monkeypatch):
    with isolated_portal_case():
        for i in range(120):
            add(kind, f'history-{i:03d}', created=i+1)
        add(kind, 'other-paper', paper='other', created=1000)
        decoded = []
        original = store.decode
        monkeypatch.setattr(store, 'decode', lambda k, row: decoded.append(row['job_id']) or original(k, row))
        assert jobs._latest_job(kind, paper_id='visible')['job_id'] == 'history-119'
        assert decoded == ['history-119'], 'Latest lookup must not load historical rows into Python'


def test_provenance_selects_latest_before_done_filter_and_respects_visibility(isolated_portal_case, monkeypatch):
    with isolated_portal_case():
        add('deep_read_jobs', 'success-old', created=100.1)
        add('deep_read_jobs', 'failure-new', created=100.9, status='error')
        add('deep_read_jobs', 'private-success', paper='private', created=200)
        monkeypatch.setattr(jobs, '_all_papers', lambda: [{'id': x} for x in ('visible', 'private', 'empty')])
        monkeypatch.setattr(permission_system, 'permitted_ids', lambda *a: ['visible', 'empty'])
        assert jobs.provenance_candidates('reader') == {'visible_papers': 2, 'without_completed_job': 2, 'candidates': []}


def test_provenance_materializes_only_visible_latest_jobs(isolated_portal_case, monkeypatch):
    with isolated_portal_case():
        for paper in ('visible', 'private'):
            for i in range(60):
                add('deep_read_jobs', f'{paper}-{i:03d}', paper=paper, created=i+1)
        monkeypatch.setattr(jobs, '_all_papers', lambda: [{'id': 'visible'}, {'id': 'private'}])
        monkeypatch.setattr(permission_system, 'permitted_ids', lambda *a: ['visible'])
        decoded = []
        original = store.decode
        monkeypatch.setattr(store, 'decode', lambda k, row: decoded.append(row['job_id']) or original(k, row))
        result = jobs.provenance_candidates('reader')
        assert result['candidates'] == [{'paper_id': 'visible', 'job_id': 'visible-059'}]
        assert decoded == ['visible-059']
        monkeypatch.setattr(permission_system, 'permitted_ids', lambda *a: [])
        decoded.clear()
        assert jobs.provenance_candidates('reader')['candidates'] == []
        assert decoded == []


def test_exp_admission_uses_single_paper_lookup(isolated_portal_case, monkeypatch):
    with isolated_portal_case():
        def no_scan():
            pytest.fail('One Exp admission must not scan and register the entire library')
        monkeypatch.setattr(jobs, '_all_papers', no_scan)
        monkeypatch.setattr(jobs.PAPER_STORE, 'paper_by_id', lambda pid: {'id': pid, 'title': 'Fixture'} if pid == 'visible' else None)
        monkeypatch.setattr(jobs, '_start_exp_task', lambda job: job)
        result = jobs.start_exp({'paper_id': 'visible'}, 'reader')
        assert result['paper_id'] == 'visible' and result['created_by'] == 'reader'
        assert jobs.start_exp({'paper_id': 'visible'}, 'reader')['job_id'] == result['job_id']
        with pytest.raises(KeyError):
            jobs.start_exp({'paper_id': 'missing'}, 'reader')


def test_exp_worker_only_loads_metadata(monkeypatch, tmp_path):
    state = {'job_id': 'exp-fixture', 'paper_id': 'visible', 'created_by': 'reader'}
    monkeypatch.setattr(jobs, '_set_exp_job', lambda identity, patch: state.update(patch) or dict(state))
    def metadata(pid, *, sections=None):
        assert sections == [], 'Worker must not load unrelated artifacts or recover figures'
        return {'paper': {'id': pid, 'title': 'Fixture'}}
    monkeypatch.setattr(jobs, 'get_paper_detail', metadata)
    monkeypatch.setattr(jobs, '_generate_exp_extraction', lambda *a, **kw: ('\n'.join('## ' + s for s in jobs.EXP_SECTIONS), {}))
    monkeypatch.setattr(jobs, '_write_exp_markdown', lambda *a, **kw: tmp_path / 'exp.md')
    monkeypatch.setattr(jobs.PAPER_STORE, 'update_paper', lambda *a: {})
    jobs._run_exp_job(state['job_id'])
    assert state['status'] == 'done', state.get('error')


def test_library_review_count_is_scoped_and_does_not_load_bodies(isolated_portal_case, monkeypatch):
    from management_portal import paper_library
    with isolated_portal_case():
        store.append('reviews', {'review_id': 'visible-review', 'paper_id': 'visible', 'raw_response': 'body' * 1000})
        store.append('reviews', {'review_id': 'private-review', 'paper_id': 'private', 'raw_response': 'private' * 1000})
        monkeypatch.setattr(paper_library, 'resource_index_papers', lambda *, actor: [{'id': 'visible', 'title': 'Fixture'}])
        monkeypatch.setattr(paper_library.paper_favorites, 'favorite_ids', lambda actor: [])
        original = store.decode
        decoded = []
        monkeypatch.setattr(store, 'decode', lambda kind, row: decoded.append(kind) or original(kind, row))
        assert paper_library.list_library(actor='reader')['summary']['review_count'] == 1
        assert decoded == [], 'A count must not materialize review bodies'
        monkeypatch.setattr(paper_library, 'resource_index_papers', lambda *, actor: [])
        assert paper_library.list_library(actor='reader')['summary']['review_count'] == 0
