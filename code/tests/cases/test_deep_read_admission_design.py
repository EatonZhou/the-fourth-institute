"""Direct admission contract: no Paper scan, PDF or model work in POST."""
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from types import SimpleNamespace
from management_portal import paper_reading_jobs as jobs, paper_state_store as store


def test_start_reuses_job_without_scan_or_second_launch(monkeypatch):
    monkeypatch.setattr(jobs, 'get_resource_service', lambda: SimpleNamespace(get=lambda *a, **kw: {'id':'paper_test'}))
    monkeypatch.setattr(jobs, '_all_papers', lambda: (_ for _ in ()).throw(AssertionError('full scan')))
    monkeypatch.setattr(jobs, '_paper_public', lambda *a: (_ for _ in ()).throw(AssertionError('PDF probe')))
    saved=[]; launched=[]
    def admit(job):
        if saved:return saved[0],False
        saved.append(job);return job,True
    monkeypatch.setattr(store,'admit_deep_read',admit)
    monkeypatch.setattr(jobs,'_launch_deep_read_business_task',lambda job:launched.append(job['job_id']))
    a=jobs.start_deep_read({'paper_id':'paper_test'},'actor')
    b=jobs.start_deep_read({'paper_id':'paper_test'},'actor')
    assert a==b and a['status']=='queued' and len(launched)==1


def test_admission_transaction_serializes_concurrent_requests_and_allows_retry(monkeypatch):
    lock=Lock(); rows=[]
    class Connection:
        def execute(self,sql,args):
            if sql.startswith('SELECT'):
                found=next((r for r in rows if r['paper_id']==args[0] and r['status'] in ('queued','running')),None)
            else:
                names=sql.split('(',1)[1].split(')',1)[0].split(',')
                found=dict(zip(names,args));rows.append(found)
            return SimpleNamespace(fetchone=lambda:found)
    @contextmanager
    def transaction(key):
        with lock:yield Connection()
    monkeypatch.setattr(store,'database',lambda:SimpleNamespace(transaction=transaction))
    def admit(n):return store.admit_deep_read({'job_id':str(n),'paper_id':'p','status':'queued','created_at':1})
    with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(admit,range(16)))
    assert len(rows)==1 and sum(created for _,created in results)==1
    rows[0]['status']='error'
    assert admit(20)[1] and len(rows)==2


def test_launch_returns_admission_snapshot_not_background_read(monkeypatch):
    calls=[]
    monkeypatch.setattr(jobs.business_tasks,'start',lambda *a,**kw:calls.append(a))
    monkeypatch.setattr(jobs,'_latest_deep_read_job',lambda **kw:(_ for _ in ()).throw(AssertionError('sync readback')))
    job={'job_id':'new','status':'queued'}
    assert jobs._launch_deep_read_business_task(job) is job and len(calls)==1


def test_reader_permission_uses_resource_owner_not_contributor(monkeypatch):
    from management_portal import permission_system
    seen=[]
    monkeypatch.setattr(permission_system,'permitted_ids',lambda *a: (_ for _ in ()).throw(AssertionError('new ACL')))
    monkeypatch.setattr(jobs,'get_resource_service',lambda:SimpleNamespace(get=lambda id,**kw:seen.append((id,kw))))
    monkeypatch.setattr(store,'admit_deep_read',lambda job:(job,False))
    assert jobs.start_deep_read({'paper_id':'paper_visible'},'viewer')['status']=='queued'
    assert seen==[('paper_visible',{'actor':'viewer'})]


def test_missing_paper_fails_before_registration(monkeypatch):
    import pytest
    def missing(*a,**kw):raise KeyError('Paper not found')
    monkeypatch.setattr(jobs,'get_resource_service',lambda:SimpleNamespace(get=missing))
    monkeypatch.setattr(store,'admit_deep_read',lambda job: (_ for _ in ()).throw(AssertionError('must not admit')))
    with pytest.raises(KeyError,match='Paper not found'):
        jobs.start_deep_read({'paper_id':'paper_missing'},'viewer')


def test_no_pdf_is_visible_background_failure_before_extraction(monkeypatch):
    patches=[]
    monkeypatch.setattr(jobs,'_latest_deep_read_job',lambda **kw:{'paper_id':'p','status':'queued'})
    monkeypatch.setattr(jobs,'get_paper_detail',lambda id, **kw:{'paper':{'id':id}})
    monkeypatch.setattr(jobs,'_paper_public',lambda p:{'has_pdf':False})
    monkeypatch.setattr(jobs,'_extract_pdf_text',lambda *a,**kw:(_ for _ in ()).throw(AssertionError('no PDF')))
    monkeypatch.setattr(jobs,'_set_deep_read_job',lambda id,patch:patches.append(patch))
    jobs._run_deep_read_job('job-no-pdf')
    assert patches[-1]['status']=='error' and '没有 PDF' in patches[-1]['error']
