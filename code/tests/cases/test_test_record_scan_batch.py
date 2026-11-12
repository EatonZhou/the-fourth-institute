"""Test恢复单次真值扫描与清理回执批量提交保持崩溃恢复语义。

:design: decision_8273271b67b1
"""
import os
from collections import Counter

import pytest

from management_portal.test_framework import records as module
from management_portal.test_framework.records import RunRecords, RunCoordinator


def make(store,status='queued',tau=False,cleanup=None):
    plan={'executable':True,'selected':[{}],**({'campaign':'tau'} if tau else {})}
    key=store.create(plan,'alice')['run_id']
    store._mutate(key,lambda r:r.update(status=status,executor={**module.process_identity(),'pid':999999999},
        **({'resource_cleanup':cleanup} if cleanup else {})))
    return key


def test_recovery_reads_each_truth_once_and_heals_stale_index(tmp_path,monkeypatch):
    """两次持久写之间崩溃的终态索引不能跳过详情，统一扫描只读每条一次。"""
    store=RunRecords(tmp_path/'runs.json')
    local=make(store,'running'); tau=make(store,'running',tau=True); queued=make(store)
    with store._locked():
        index=store._read_index()
        for row in index: row['status']='passed'
        store._write_index(index)
    seen=Counter();read=store._read_record
    def counted(key): seen[key]+=1;return read(key)
    monkeypatch.setattr(store,'_read_record',counted)
    launched=[]
    owner=RunCoordinator(store,tmp_path/'artifacts',launch=launched.append)
    assert set(owner.recover())=={tau,queued}
    assert seen==Counter({local:1,tau:1,queued:1})
    assert read(local)['status']=='error'
    assert {r['run_id']:r['status'] for r in store._read_index()}=={local:'error',tau:'running',queued:'queued'}


def test_recovery_keeps_live_legacy_pid_and_selected_scope(tmp_path,monkeypatch):
    """旧PID仍存活时不重放；指定Run只读取指定详情。"""
    store=RunRecords(tmp_path/'runs.json')
    live=make(store,'running');other=make(store,'running')
    def legacy(r):r.pop('executor',None);r.update(executor_pid=os.getpid(),recovery_pending=True)
    store._mutate(live,legacy)
    seen=[];read=store._read_record
    def counted(k):seen.append(k);return read(k)
    monkeypatch.setattr(store,'_read_record',counted)
    owner=RunCoordinator(store,tmp_path/'artifacts',launch=lambda _:pytest.fail('live legacy re-launched'))
    assert owner.recover({live})==[] and seen==[live]
    assert read(live)['status']=='running' and read(live)['recovery_pending']
    assert read(other)['status']=='running'


def test_recovery_preserves_unverifiable_executor(tmp_path):
    """不完整执行身份属于未知，不得将未知当作进程死亡重放。"""
    store=RunRecords(tmp_path/'runs.json');key=make(store,'running')
    store._mutate(key,lambda r:r.update(executor={'pid':999999999}))
    owner=RunCoordinator(store,tmp_path/'artifacts',launch=lambda _:pytest.fail('unknown owner replayed'))
    assert owner.recover()==[] and store.get(key)['status']=='running'


def test_pending_cleanup_flushes_one_index_and_skips_complete_and_active(tmp_path,monkeypatch):
    """维护只重试未完整收尾的终态，批次只保存一次索引。"""
    from management_portal.test_framework import run_cleanup
    store=RunRecords(tmp_path/'runs.json')
    first=make(store,'passed');second=make(store,'error');active=make(store,'running')
    complete=make(store,'passed',cleanup={'status':'complete','errors':[]})
    calls=[]
    def finish(k):calls.append(k);return {'status':'complete','errors':[]}
    monkeypatch.setattr(run_cleanup,'finish_run',finish)
    writes=[];write=store._write_index
    def indexed(x):writes.append(len(x));write(x)
    monkeypatch.setattr(store,'_write_index',indexed)
    result=store.finish_pending_cleanup()
    assert set(calls)=={first,second} and len(writes)==1 and result['errors']==[]
    assert store.get(active)['status']=='running' and store.get(complete)['resource_cleanup']['status']=='complete'
    store.finish_pending_cleanup()
    assert len(calls)==2 and len(writes)==1


def test_cleanup_commit_merges_current_record_and_ignores_deleted_or_active(tmp_path,monkeypatch):
    """域清理不持有文件锁；回执提交时重新核对真值，保留并行更新。"""
    from management_portal.test_framework import run_cleanup
    store=RunRecords(tmp_path/'runs.json')
    keep=make(store,'passed');gone=make(store,'passed');active=make(store,'passed')
    def finish(key):
        # Re-enter through another owner while domain cleanup is outside the lock.
        peer=RunRecords(store.path)
        if key==keep:peer._mutate(key,lambda r:r.update(note='parallel write'))
        if key==gone:peer.delete_many([key])
        if key==active:peer._mutate(key,lambda r:r.update(status='running'))
        return {'status':'complete','errors':[]}
    monkeypatch.setattr(run_cleanup,'finish_run',finish)
    result=store.finish_pending_cleanup()
    assert result['updated']==[keep]
    assert store.get(keep)['note']=='parallel write'
    assert store.get(active)['status']=='running' and 'resource_cleanup' not in store.get(active)
    with pytest.raises(KeyError):store.get(gone)


def test_cleanup_record_commit_survives_index_write_failure(tmp_path,monkeypatch):
    """回执详情已持久化而索引提交失败后，下一次不重复已完成的域清理。"""
    from management_portal.test_framework import run_cleanup
    store=RunRecords(tmp_path/'runs.json');key=make(store,'passed');calls=[]
    def finish(k):calls.append(k);return {'status':'error','errors':['fixture failure']}
    monkeypatch.setattr(run_cleanup,'finish_run',finish)
    write=store._write_index
    def fail(_):raise OSError('index crash')
    monkeypatch.setattr(store,'_write_index',fail)
    with pytest.raises(OSError,match='index crash'):store.finish_pending_cleanup()
    assert store._read_record(key)['status']=='error'
    monkeypatch.setattr(store,'_write_index',write)
    monkeypatch.setattr(run_cleanup,'finish_run',lambda k:{'status':'complete','errors':[]})
    store.finish_pending_cleanup()
    assert store.get(key)['status']=='error' and store.get(key)['resource_cleanup']['status']=='complete'
    assert store._read_index()[0]['status']=='error'


def test_pending_cleanup_persists_domain_exception_and_continues(tmp_path,monkeypatch):
    """单个域清理失败记录原错误并继续其余收尾，已完成回执不再执行。"""
    from management_portal.test_framework import run_cleanup
    store=RunRecords(tmp_path/'runs.json');bad=make(store,'passed');good=make(store,'passed')
    def finish(k):
        if k==bad:raise RuntimeError('cleanup fixture error')
        return {'status':'complete','errors':[]}
    monkeypatch.setattr(run_cleanup,'finish_run',finish)
    result=store.finish_pending_cleanup()
    assert result['errors']==['cleanup fixture error']
    assert store.get(bad)['status']=='error' and store.get(good)['resource_cleanup']['status']=='complete'
