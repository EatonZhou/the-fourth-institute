"""Script coordination selects work, not retained historical output.

:design: decision_8b510cbd9c58
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
import json
from types import SimpleNamespace

import pytest

from management_portal.society_service.script_runs import store, worker, service, runtime, events


def add(identifier, **values):
    return store.admit({'script_run_id': identifier, 'lineage_id': 'lineage_aaaaaaaaaaaa',
        'started_at': service.now(), 'status': 'running', 'notified': False, **values})[0]


def test_idle_history_is_not_revisited_and_pending_pages_are_fair(monkeypatch):
    """脚本运行 worker 只处理未通知的运行并按页轮转，不翻看已完成历史，非租约持有者不处理"""
    with store.domain().transaction() as c:
        c.execute("""INSERT INTO runs(script_run_id,status,notified,finished_at)
            SELECT 'history_' || n, 'succeeded',true,CURRENT_TIMESTAMP FROM generate_series(1,5000) n""")
        c.execute("""INSERT INTO runs(script_run_id,status,notified)
            SELECT 'pending_' || lpad(n::text,3,'0'), 'running',false FROM generate_series(1,205) n""")
    seen = []
    one, two = worker.Worker(), worker.Worker()
    monkeypatch.setattr(one, 'reconcile', lambda identifier, record: seen.append(identifier))
    monkeypatch.setattr(two, 'reconcile', lambda *_: pytest.fail('non-holder reconciled work'))
    one.tick(); two.tick()
    assert len(seen) == 100
    one.tick(); one.tick()
    assert len(set(seen)) == 205 and not any(x.startswith('history') for x in seen)
    one.tick()
    assert len(seen) == 305
    one.stop()


def test_lease_takeover_fences_old_holder_and_does_not_touch_runs():
    """worker 租约过期被接管后，旧持有者不能续约也不能释放新租约"""
    assert store.acquire_lease('one')
    assert not store.acquire_lease('two')
    with store.domain().transaction() as c:
        c.execute("UPDATE worker_lease SET until_at=CURRENT_TIMESTAMP-INTERVAL '1 second'")
    assert not store.renew_lease('one')
    assert store.acquire_lease('two')
    store.release_lease('one')
    assert not store.renew_lease('one') and store.renew_lease('two')


def test_lease_expiry_prevents_publishing_observed_result(monkeypatch, tmp_path):
    """没有有效租约的 worker 观察到运行结束也不发布结果或回收令牌"""
    monkeypatch.setattr(runtime, 'RUN_ROOT', tmp_path)
    row = add('done', status='succeeded', finished_at=service.now())
    monkeypatch.setattr(events, 'publish', lambda _: pytest.fail('expired worker published'))
    monkeypatch.setattr(service, 'revoke', lambda _: pytest.fail('expired worker revoked'))
    owner = worker.Worker()
    owner.reconcile('done', row)
    assert not store.get('done')['notified']


def test_failed_notification_retries_but_notified_history_does_not(monkeypatch, tmp_path):
    """结果通知失败时下次重试，已通知的运行不再重复通知"""
    monkeypatch.setattr(runtime, 'RUN_ROOT', tmp_path)
    add('done', status='succeeded', finished_at=service.now())
    calls = []
    monkeypatch.setattr(service, 'revoke', lambda _: None)
    def publish(record):
        calls.append(record['script_run_id'])
        if len(calls) == 1:
            raise ConnectionError('temporary service outage')
    monkeypatch.setattr(events, 'publish', publish)
    one = worker.Worker()
    one.tick()
    assert not store.get('done')['notified']
    one.tick(); one.tick()
    assert calls == ['done', 'done'] and store.get('done')['notified']
    one.stop()


def test_filtered_list_and_concurrent_idempotent_admission():
    """相同准入键的并发提交只创建一个运行，运行列表按 Lineage 和状态过滤分页"""
    record = {'script_run_id': 'once', 'lineage_id': 'lineage_aaaaaaaaaaaa',
              'started_at': service.now(), 'status': 'running', 'admission': 'same'}
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda n: store.admit({**record, 'script_run_id': str(n)}), range(6)))
    assert sum(kind == 'created' for _, kind in results) == 1
    for n in range(8):
        add(f'other{n}', lineage_id='lineage_bbbbbbbbbbbb', status='succeeded', notified=True)
    result = store.list_runs({'lineage_id': 'lineage_bbbbbbbbbbbb', 'status': 'succeeded', 'limit': 3})
    assert result['total'] == 8 and len(result['runs']) == 3


def test_cancel_preserves_completed_result_and_run_row_is_unlocked_during_stop(monkeypatch, tmp_path):
    """取消时脚本已写出成功结果则保留该结果，停止期间运行记录不被锁住"""
    monkeypatch.setattr(runtime, 'RUN_ROOT', tmp_path)
    monkeypatch.setattr(service, 'revoke', lambda _: None)
    add('cancel', unit='test')
    root = tmp_path / 'cancel'
    root.mkdir()
    def stop(*args, **kwargs):
        with store.edit('cancel') as row:
            assert row['cancel_requested']
        (root / 'result.json').write_text(json.dumps({'status': 'succeeded', 'finished_at': service.now()}))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(service.process_runtime, 'run_process', stop)
    assert service.cancel('cancel', 'sample_admin', lineage_id='lineage_aaaaaaaaaaaa')['status'] == 'succeeded'


def test_retention_is_bounded_and_keeps_undelivered_results(monkeypatch, tmp_path):
    """超过保留期且已通知的运行才被删除，未送达结果的运行保留"""
    monkeypatch.setattr(runtime, 'RUN_ROOT', tmp_path)
    old = (datetime.now(timezone.utc)-timedelta(days=61)).isoformat()
    add('retained', status='failed', finished_at=old)
    add('expired', status='succeeded', finished_at=old, notified=True)
    assert store.expired() == ['expired']
    store.delete_expired('retained')
    assert store.get('retained') is not None
    store.delete_expired('expired')
    assert store.get('expired') is None
