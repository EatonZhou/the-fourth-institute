"""Requires disposable PG URL supplied by the isolated runner, never production."""
from concurrent.futures import ThreadPoolExecutor
import os
import pytest
from management_portal import paper_state_store as store


def test_parallel_admission_real_postgres(monkeypatch):
    dsn=os.environ.get('DEEP_READ_TEST_PG')
    if not dsn:pytest.skip('disposable database only')
    monkeypatch.setenv('PORTAL_DATABASE_URL',dsn)
    monkeypatch.setenv('PORTAL_DATABASE_NAMESPACE','deep_read_isolated')
    def admit(n):return store.admit_deep_read({'job_id':f'job-{n}','paper_id':'synthetic','status':'queued','created_at':n+1})
    with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(admit,range(20)))
    assert sum(created for _,created in results)==1
    assert len({job['job_id'] for job,_ in results})==1
    first=results[0][0]
    store.merge('deep_read_jobs',first['job_id'],{'status':'error','error':'synthetic failure'})
    retry,created=admit(99)
    assert created and retry['job_id']!='first' and retry['job_id']!=first['job_id']
