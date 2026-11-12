"""脚本历史与产物独立到期，租约协调器按分钟有界维护。

:design: decision_8b510cbd9c58
"""
from datetime import timedelta
from threading import Event, Thread

import pytest

from management_portal.society_service.script_runs import runtime, store, worker
from management_common.directory_cleanup_store import get_directory_cleanup_store


def add(identifier, end, *, status='succeeded', notified=True):
    return store.admit({'script_run_id': identifier, 'status': status, 'notified': notified,
                       'started_at': end.isoformat(), 'finished_at': end.isoformat()})[0]


def test_records_require_notified_terminal_and_strict_sixty_day_cutoff():
    """仅完成通知且严格超过六十天的终态记录到期，边界与未通知记录保留。"""
    with store.domain().transaction() as c:
        now = c.execute('SELECT CURRENT_TIMESTAMP').fetchone()[0]
        add('old', now-timedelta(days=60,microseconds=1))
        add('exact', now-timedelta(days=60))
        add('young', now-timedelta(days=8))
        add('undelivered', now-timedelta(days=100), notified=False)
        add('unknown_delivery', now-timedelta(days=100), notified=None)
        add('running', now-timedelta(days=100), status='running')
        store.admit({'script_run_id':'no_finished_at','status':'succeeded','notified':True})
        assert store.expired() == ['old']


def test_record_deletion_rechecks_age_delivery_and_owned_lease(tmp_path, monkeypatch):
    """删除重新核对期限、通知与真实租约，不能绕过选择阶段删新记录。"""
    monkeypatch.setattr(runtime,'RUN_ROOT',tmp_path)
    with store.domain().transaction() as c:
        now = c.execute('SELECT CURRENT_TIMESTAMP').fetchone()[0]
        add('old',now-timedelta(days=61))
        add('young',now-timedelta(days=8))
        add('undelivered',now-timedelta(days=61),notified=False)
        add('running',now-timedelta(days=61),status='running')
        assert store.acquire_lease('owner')
        for identifier in ['young','undelivered','running']:
            assert not store.delete_expired(identifier,holder='owner')
            assert store.get(identifier) is not None
        assert not store.delete_expired('old',holder='other')
        assert store.get('old') is not None
        assert store.delete_expired('old',holder='owner')
        assert store.get('old') is None


def test_background_files_expire_at_seven_days_without_removing_history(tmp_path, monkeypatch):
    """七天产物独立清理，未通知与已通知的六十天内记录都继续可读。"""
    monkeypatch.setattr(runtime,'RUN_ROOT',tmp_path)
    with store.domain().transaction() as c:
        now = c.execute('SELECT CURRENT_TIMESTAMP').fetchone()[0]
        for identifier,age,status,notified in [
            ('delivered',7,'succeeded',True),('undelivered',8,'failed',False),
            ('young',6,'succeeded',True),('running',100,'running',False)]:
            add(identifier,now-timedelta(days=age),status=status,notified=notified)
            (tmp_path/identifier).mkdir()
        assert store.expired_artifacts() == ['undelivered','delivered']
        for identifier in store.expired_artifacts():
            store.expire_artifacts(identifier)
        assert not store.expired_artifacts()
    get_directory_cleanup_store().run_pending()
    assert not (tmp_path/'delivered').exists() and not (tmp_path/'undelivered').exists()
    assert (tmp_path/'young').exists() and (tmp_path/'running').exists()
    assert store.get('delivered') is not None and store.get('undelivered') is not None


def test_record_delete_rolls_back_when_cleanup_intent_fails(tmp_path, monkeypatch):
    """目录清理意图不能持久化时，业务历史删除必须回滚。"""
    from management_common import directory_retention
    monkeypatch.setattr(runtime,'RUN_ROOT',tmp_path)
    with store.domain().read() as c:
        now=c.execute('SELECT CURRENT_TIMESTAMP').fetchone()[0]
    add('old',now-timedelta(days=61))
    assert store.acquire_lease('owner')
    def fail(*a,**kw):
        raise RuntimeError('cleanup intent unavailable')
    monkeypatch.setattr(directory_retention,'enqueue_directory_cleanup',fail)
    with pytest.raises(RuntimeError,match='cleanup intent unavailable'):
        store.delete_expired('old',holder='owner')
    assert store.get('old') is not None


def test_minute_sweep_caps_batch_and_preserves_pending_results(tmp_path, monkeypatch):
    """协调器每分钟最多回收一百条已通知历史，不由每小时目录任务驱动。"""
    monkeypatch.setattr(runtime,'RUN_ROOT',tmp_path)
    with store.domain().transaction() as c:
        c.execute("""INSERT INTO runs(script_run_id,status,notified,finished_at)
            SELECT 'old_' || lpad(n::text,3,'0'),'succeeded',true,
              CURRENT_TIMESTAMP-INTERVAL '61 days' FROM generate_series(1,205) n""")
    assert len(store.expired(limit=1000)) == 100
    assert len(store.expired_artifacts(limit=1000)) == 100
    clock=[100.0]
    monkeypatch.setattr(worker.time,'monotonic',lambda:clock[0])
    owner=worker.Worker()
    owner.tick()
    assert len(store.expired()) == 100
    with store.domain().read() as c:
        assert c.execute('SELECT count(*) FROM runs').fetchone()[0] == 105
    clock[0]=159.999
    owner.tick()
    with store.domain().read() as c:
        assert c.execute('SELECT count(*) FROM runs').fetchone()[0] == 105
    clock[0]=160.0
    owner.tick()
    assert store.expired() == ['old_201','old_202','old_203','old_204','old_205']
    owner.stop()


def test_lease_is_thirty_seconds_and_takeover_fences_record_delete(tmp_path, monkeypatch):
    """三十秒租期配十秒独立续租；接管后旧持有者不能删除或释放新租约。"""
    monkeypatch.setattr(runtime,'RUN_ROOT',tmp_path)
    assert store.LEASE_SECONDS == 30 and worker.HEARTBEAT_SECONDS == 10
    assert store.acquire_lease('one')
    with store.domain().transaction() as c:
        remaining=c.execute('SELECT EXTRACT(EPOCH FROM until_at-clock_timestamp()) FROM worker_lease').fetchone()[0]
        assert 28 < remaining <= 30
        now=c.execute('SELECT CURRENT_TIMESTAMP').fetchone()[0]
        add('old',now-timedelta(days=61))
        c.execute("UPDATE worker_lease SET until_at=clock_timestamp()-INTERVAL '1 second'")
    assert store.acquire_lease('two')
    assert not store.renew_lease('one')
    assert not store.delete_expired('old',holder='one')
    store.release_lease('one')
    assert store.renew_lease('two') and store.get('old') is not None
    assert store.delete_expired('old',holder='two')


def test_independent_heartbeat_continues_during_slow_work_and_fences_loss(monkeypatch):
    """批次工作阻塞时续租线程仍运行，续租失败后不再处理下一记录或维护。"""
    with store.domain().read() as c:
        now=c.execute('SELECT CURRENT_TIMESTAMP').fetchone()[0]
    add('a',now,status='running',notified=False)
    add('b',now,status='running',notified=False)
    entered,renewed,release,done=Event(),Event(),Event(),Event()
    real_renew=store.renew_lease
    calls=[]
    def renew(holder):
        result=real_renew(holder)
        renewed.set()
        return result
    monkeypatch.setattr(store,'renew_lease',renew)
    monkeypatch.setattr(worker,'HEARTBEAT_SECONDS',.01)
    owner=worker.Worker()
    def reconcile(identifier,record):
        calls.append(identifier)
        entered.set()
        assert release.wait(5)
    monkeypatch.setattr(owner,'reconcile',reconcile)
    errors=[]
    def tick():
        try: owner.tick()
        except BaseException as e: errors.append(e)
        finally: done.set()
    thread=Thread(target=tick)
    thread.start()
    try:
        assert entered.wait(5) and renewed.wait(5)
        with store.domain().transaction() as c:
            c.execute("UPDATE worker_lease SET until_at=clock_timestamp()-INTERVAL '1 second'")
        assert store.acquire_lease('new')
        assert owner.lease_lost.wait(5)
    finally:
        release.set()
        thread.join(5)
        owner.stop()
    assert done.is_set() and not errors and calls == ['a']
    assert store.renew_lease('new')
