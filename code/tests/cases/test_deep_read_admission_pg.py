"""Concurrent admission against the standard isolated PostgreSQL fixture."""
from concurrent.futures import ThreadPoolExecutor
from management_portal import paper_state_store as store


def test_parallel_admission_real_postgres(isolated_portal_case):
    def admit(n):
        return store.admit_deep_read({
            'job_id': f'job-{n}', 'paper_id': 'synthetic',
            'status': 'queued', 'created_at': n + 1,
        })

    with isolated_portal_case():
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(admit, range(20)))
        assert sum(created for _, created in results) == 1
        assert len({job['job_id'] for job, _ in results}) == 1
        first = results[0][0]
        store.merge('deep_read_jobs', first['job_id'], {
            'status': 'error', 'error': 'synthetic failure',
        })
        retry, created = admit(99)
        assert created and retry['job_id'] == 'job-99'
        assert retry['job_id'] != first['job_id']
        assert store.get('deep_read_jobs', first['job_id'])['status'] == 'error'
