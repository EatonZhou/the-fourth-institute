""":importance: P2"""
from management_portal.society_service.domain_ports import execution_port
from management_portal.society_service import resident_restart_domain
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from management_portal.execution.accounts import ExecutionAccount, ExecutionAccounts
from management_portal.execution.account_monitor import AccountMonitor
from management_portal.execution.account_quota import quota_state
from management_portal.execution.contracts import ExecutionRequest, SessionOptions
from management_portal.execution.scheduler import Scheduler
from management_portal.execution.service import ExecutionService, ExecutionServiceServer


class Adapter:
    provider = 'claude'

    def accounts(self):
        return tuple(ExecutionAccount(name, self.provider, name, 'account:' + name,
            quota_policy='reserve' if name.startswith('pct') else 'none',
            billing_account_id='' if name.startswith('pct') else 'billing_' + name)
            for name in ['pct_a', 'pct_b', 'cny', 'usd'])

    def resolve(self, route_id, options, **kwargs):
        return next(a for a in self.accounts() if a.route_id == route_id)

    def observe(self, account, **kwargs):
        return {'status': 'ok', 'rate_limits': []}


@pytest.fixture
def scheduler():
    result = Scheduler(accounts=ExecutionAccounts((Adapter(),)), quota_reserve_enabled=True,
                       dispatch_interval_min_seconds=0, dispatch_interval_max_seconds=0)
    with result._connect() as connection:
        for account in result.accounts.accounts():
            for prefix in ('quota', 'quota_threshold'):
                connection.execute('DELETE FROM execution_store_meta WHERE key=%s', (f'{prefix}:{account.capacity_key}',))
        connection.commit()
    result.register_worker(worker_id='close-test', build_id='test', capabilities=['agent_session:claude'])
    return result


def update(scheduler, account, remaining):
    balance = account in ('cny', 'usd')
    scheduler.update_account_quota(account, {'status': 'available', 'complete': True,
        'observed_at': time.time(), 'remaining_balance' if balance else 'remaining_percent': remaining,
        **({'currency': account.upper(), 'max_age_seconds': 120} if balance else {})})


def state(scheduler, account):
    return scheduler.health()['capacity_partitions']['account:' + account]['quota_reserve']


def configure(scheduler, account, value, unit):
    server = object.__new__(ExecutionServiceServer)
    server.accounts, server.scheduler = scheduler.accounts, scheduler
    return server.dispatch('scheduler.capacity.configure', {'account_id': account, 'actor': 'admin',
        'max_concurrency': 8, 'dispatch_interval_min_seconds': 0, 'dispatch_interval_max_seconds': 0,
        'close_threshold': {'value': value, 'unit': unit}})


def submit(scheduler, account):
    request = ExecutionRequest.new(actor='test', prompt='Close threshold', service_type='agent_session',
        provider='claude', operation='turn', session=SessionOptions(mode='stateless', backend_route_id=account))
    scheduler.submit(request)
    return request


def claim(scheduler):
    return scheduler.claim(worker_id='close-test', capabilities=['agent_session:claude'])


def test_per_account_threshold_persists_and_does_not_change_other_accounts(scheduler):
    """关闭阈值按账号持久保存且不影响其他账号，旧客户端只保存并发时也不覆盖"""
    for name in ['pct_a', 'pct_b']:
        update(scheduler, name, 5)
    result = configure(scheduler, 'pct_a', 5, '%')
    assert result['close_threshold']['value'] == 5
    assert state(scheduler, 'pct_a')['status'] == 'reserved'
    assert state(scheduler, 'pct_b')['status'] == 'available'
    restored = Scheduler(accounts=scheduler.accounts, quota_reserve_enabled=True)
    assert state(restored, 'pct_a')['close_threshold']['value'] == 5
    assert state(restored, 'pct_b')['close_threshold']['value'] == 2
    # Old clients saving concurrency must preserve the new threshold.
    restored.configure_capacity('account:pct_a', 12, 1, 2, actor='legacy-client')
    assert state(restored, 'pct_a')['close_threshold']['value'] == 5


def test_saving_unchanged_threshold_does_not_exempt_running_work(scheduler):
    """重新保存未改变的关闭阈值不会让运行中的执行免于取消"""
    update(scheduler, 'pct_a', 10)
    item = submit(scheduler, 'pct_a')
    assert claim(scheduler)['claimed']
    scheduler.configure_capacity('account:pct_a', 12, 0, 0, actor='test',
                                 close_threshold={'value': 2, 'unit': '%'})
    update(scheduler, 'pct_a', 1)
    assert scheduler.snapshot(item.execution_id)['scheduler']['cancel_requested']


def test_threshold_change_lets_running_execution_finish(scheduler):
    """修改关闭阈值时已在运行的执行可以跑完，新阈值只拦下未开始的执行"""
    update(scheduler, 'pct_a', 5)
    request = submit(scheduler, 'pct_a')
    assert claim(scheduler)['claimed']
    configure(scheduler, 'pct_a', 5, '%')
    assert state(scheduler, 'pct_a')['status'] == 'reserved'
    # Work running when the threshold changes finishes, also across later quota samples.
    update(scheduler, 'pct_a', 4)
    snapshot = scheduler.snapshot(request.execution_id)
    assert not snapshot['scheduler']['cancel_requested']
    # The new threshold holds back what has not started yet.
    submit(scheduler, 'pct_a')
    assert claim(scheduler)['reason'] == 'quota_reserve'


def test_work_started_after_a_threshold_change_is_still_protected(scheduler):
    """阈值修改后才开始的执行在余量低于新阈值时被请求取消"""
    update(scheduler, 'pct_a', 50)
    configure(scheduler, 'pct_a', 5, '%')
    request = submit(scheduler, 'pct_a')
    assert claim(scheduler)['claimed']
    update(scheduler, 'pct_a', 4)
    snapshot = scheduler.snapshot(request.execution_id)
    assert snapshot['scheduler']['cancel_requested']
    event = next(e for e in snapshot['events'] if e['type'] == 'cancellation_requested')
    assert event['details']['close_threshold']['value'] == 5


def test_lower_threshold_releases_original_queue_on_fresh_observation(scheduler):
    """调低关闭阈值后，按新鲜额度样本立即放行原先被保留的排队执行"""
    update(scheduler, 'pct_a', 1)
    request = submit(scheduler, 'pct_a')
    assert claim(scheduler)['reason'] == 'quota_reserve'
    configure(scheduler, 'pct_a', 0.5, '%')
    assert claim(scheduler)['execution']['execution_id'] == request.execution_id


def test_balance_defaults_zero_and_compares_original_currency(scheduler):
    """余额账号的关闭阈值默认为 0，并按原币种 CNY、USD 与余额比较"""
    update(scheduler, 'cny', 5)
    update(scheduler, 'usd', 5)
    assert state(scheduler, 'cny')['close_threshold']['value'] == 0
    assert state(scheduler, 'usd')['close_threshold']['value'] == 0
    configure(scheduler, 'cny', 5, 'CNY')
    configure(scheduler, 'usd', 4.99, 'USD')
    assert state(scheduler, 'cny')['status'] == 'reserved'
    assert state(scheduler, 'usd')['status'] == 'available'
    update(scheduler, 'usd', 0)
    assert state(scheduler, 'usd')['status'] == 'reserved'
    update(scheduler, 'usd', 4.991)
    assert state(scheduler, 'usd')['status'] == 'available'


@pytest.mark.parametrize('value,unit', [(-1, '%'), (101, '%'), (float('nan'), '%'),
    (float('inf'), '%'), (True, '%'), ('5', '%'), (5, 'CNY')])
def test_invalid_threshold_is_atomic(scheduler, value, unit):
    """非法关闭阈值（负数、超过 100%、NaN、布尔、字符串、单位不符）被拒绝且原阈值不变"""
    update(scheduler, 'pct_a', 10)
    with pytest.raises(ValueError):
        configure(scheduler, 'pct_a', value, unit)
    assert state(scheduler, 'pct_a')['close_threshold']['value'] == 2


def test_unavailable_balance_does_not_become_zero_or_reopen_reserved(scheduler):
    """余额不可用时不按 0 处理，也不重新放开已保留的账号"""
    scheduler.update_account_quota('cny', {'status': 'unknown'})
    assert not state(scheduler, 'cny')['automatic_paused']
    assert not state(scheduler, 'cny')['close_threshold']['supported']
    update(scheduler, 'cny', 5)
    configure(scheduler, 'cny', 1, 'CNY')
    update(scheduler, 'cny', 0)
    scheduler.update_account_quota('cny', {'status': 'unknown'})
    assert state(scheduler, 'cny')['status'] == 'reserved'


def test_stale_balance_cannot_trigger_new_close_or_reopen():
    """过期余额样本既不触发新的关闭，也不解除已有的关闭"""
    threshold = {'metric': 'balance', 'unit': 'CNY', 'value': 5, 'supported': True}
    sample = {'status': 'available', 'remaining_balance': 1, 'observed_at': 1}
    assert quota_state(sample, now=1000, threshold=threshold)['status'] == 'unknown'
    sample['status'] = 'reserved'
    assert quota_state(sample, now=1000, threshold=threshold)['status'] == 'reserved'


def test_monitor_applies_balance_close_without_home_requests(scheduler):
    """AccountMonitor 刷新计费余额后直接按阈值关闭账号，不依赖 Home 页面请求"""
    update(scheduler, 'cny', 5)
    configure(scheduler, 'cny', 1, 'CNY')
    monitor = AccountMonitor(scheduler.accounts, scheduler)
    monitor.billing.observe = lambda account_id: {'id': account_id, 'status': 'ok', 'metric_kind': 'balance',
        'amount': 0, 'currency': 'CNY', 'display': '0 CNY', 'updated_at': datetime.now(timezone.utc).isoformat()}
    monitor.refresh_account(scheduler.accounts.get('cny'))
    row = next(a for a in monitor.snapshot()['accounts'] if a['account_id'] == 'cny')
    assert row['quota_reserve']['status'] == 'reserved'
    assert row['close_threshold']['unit'] == 'CNY'
    assert row['balance']['amount'] == 0


def test_rpc_client_preserves_zero_and_unit():
    """ExecutionService RPC 客户端原样传递值为 0 的关闭阈值及其单位"""
    calls = []
    service = object.__new__(ExecutionService)
    service.client = SimpleNamespace(request=lambda method, params: calls.append(params) or params)
    service.configure_account_scheduler('usd', 8, 2, 6, actor='admin', close_threshold={'value': 0, 'unit': 'USD'})
    assert calls[0]['close_threshold'] == {'value': 0, 'unit': 'USD'}


def test_shared_billing_routes_reuse_one_sample(monkeypatch):
    """同一计费条目的余额只查询一次，后续观测复用该样本"""
    from management_portal.execution import provider_balance_adapters as native
    calls = []
    monkeypatch.setattr(native, 'entries', lambda: [{'id': 'shared'}])
    monkeypatch.setattr(native, '_fetch_provider_balance', lambda entry: calls.append(entry['id']) or {
        'id': 'shared', 'status': 'ok', 'metric_kind': 'balance', 'amount': 0, 'currency': 'CNY'})
    billing = native.BillingObservations()
    assert billing.observe('shared')['amount'] == 0
    assert billing.observe('shared')['amount'] == 0
    assert calls == ['shared']


@pytest.mark.parametrize('remaining,fresh,allowed', [(5, True, True), (0, True, False), (5, False, False)])
def test_session_open_uses_the_common_remaining_metric(monkeypatch, remaining, fresh, allowed):
    """打开会话按通用剩余量判断额度，余量为 0 或样本不新鲜时拒绝"""
    monkeypatch.setattr(execution_port, 'health', lambda: {'scheduler': {'capacity_partitions': {
        'account:usd': {'quota_reserve': {'status': 'reserved', 'remaining': remaining, 'fresh': fresh,
                                        'remaining_balance': remaining, 'currency': 'USD'}}}}})
    if allowed:
        assert resident_restart_domain._account_session_open_quota({'capacity_key': 'account:usd'})['remaining'] == remaining
    else:
        with pytest.raises(ValueError):resident_restart_domain._account_session_open_quota({'capacity_key': 'account:usd'})


def test_monitor_keeps_successful_balance_current_without_time_expiry(scheduler, monkeypatch):
    """成功读取的余额不随时间过期，阈值为 0 时账号保持可用"""
    monitor = AccountMonitor(scheduler.accounts, scheduler)
    now = time.time()
    monitor.billing.observe = lambda account_id: {'id': account_id, 'status': 'ok', 'metric_kind': 'balance',
        'amount': 5, 'currency': 'CNY', 'display': '5 CNY', 'updated_at': datetime.fromtimestamp(now, timezone.utc).isoformat()}
    monitor.refresh_account(scheduler.accounts.get('cny'))
    monkeypatch.setattr(time, 'time', lambda: now + 121)
    row = next(a for a in monitor.snapshot()['accounts'] if a['account_id'] == 'cny')
    assert not row['balance'].get('stale', False)
    assert row['balance']['amount'] == 5
    assert row['quota_reserve']['status'] == 'available'
    assert not row['quota_reserve']['threshold_enabled']
    assert not row['quota_reserve']['fresh']
    assert not row['quota_reserve']['automatic_paused']


@pytest.mark.parametrize('metric,unit', [('percent', '%'), ('balance', 'CNY'), ('balance', 'USD')])
@pytest.mark.parametrize('remaining,age,status', [(0, 0, 'reserved'), (-0.1, 0, 'reserved'),
    (0, 1000, 'reserved'), (None, 1000, 'unknown'), (1, 1000, 'reserved')])
def test_zero_disables_threshold_even_when_empty_stale_or_unknown(metric, unit, remaining, age, status):
    """关闭阈值为 0 即停用阈值，余额账号余量为空、过期或未知时仍可用，额度账号只在额度用完时暂停

    A percent account whose quota is used up stays paused, also on a stale
    reading that already held it; a hold the threshold set earlier is released.
    """
    sample = {'status': status, 'observed_at': 2000 - age,
              'remaining_percent' if metric == 'percent' else 'remaining_balance': remaining}
    quota = quota_state(sample, now=2000, threshold={'metric': metric, 'unit': unit, 'value': 0, 'supported': True})
    used_up = metric == 'percent' and remaining is not None and remaining <= 0
    assert quota['status'] == ('reserved' if used_up else 'available')
    assert not quota['threshold_enabled']
    assert quota['automatic_paused'] is used_up
    assert quota['used_up'] is used_up
    assert quota['remaining'] == remaining
    assert quota['fresh'] == (age == 0)


@pytest.mark.parametrize('threshold', [0, 2])
def test_exhausted_quota_pauses_until_a_fresh_reading_shows_quota(threshold):
    """额度账号报告额度用完时暂停自动工作，不论 Close 阈值；新鲜的有额度读数才放行

    Codex keeps a used-up account working on paid credits and still reports
    window percentages, so its "exhausted" flag alone must hold the account.
    """
    limit = {'metric': 'percent', 'unit': '%', 'value': threshold, 'supported': True}
    spent = quota_state({'status': 'available', 'complete': True, 'observed_at': 1000,
                         'remaining_percent': 40, 'exhausted': True}, now=1000, threshold=limit)
    assert spent['status'] == 'reserved' and spent['used_up'] and spent['automatic_paused']
    held = quota_state(spent, now=5000, threshold=limit)
    assert held['status'] == 'reserved' and held['used_up'] and not held['fresh']
    restored = quota_state({'status': 'available', 'complete': True, 'observed_at': 5000,
                            'remaining_percent': 40, 'exhausted': False}, now=5000, threshold=limit)
    assert restored['status'] == 'available' and not restored['used_up'] and not restored['automatic_paused']


def test_used_up_account_cancels_running_work_and_unreadable_samples_keep_the_hold(scheduler):
    """阈值为 0 的额度账号用完时取消运行中的执行，读不到额度时不解除暂停"""
    configure(scheduler, 'pct_a', 0, '%')
    update(scheduler, 'pct_a', 50)
    running = submit(scheduler, 'pct_a')
    assert claim(scheduler)['execution']['execution_id'] == running.execution_id
    # Work kept through a threshold change stops too once the quota is used up.
    configure(scheduler, 'pct_a', 1, '%')
    configure(scheduler, 'pct_a', 0, '%')
    scheduler.update_account_quota('pct_a', {'status': 'available', 'complete': True, 'observed_at': time.time(),
                                             'remaining_percent': 30, 'exhausted': True})
    assert scheduler.snapshot(running.execution_id)['scheduler']['cancel_requested']
    queued = submit(scheduler, 'pct_a')
    assert claim(scheduler)['reason'] == 'quota_reserve'
    scheduler.update_account_quota('pct_a', {'status': 'unknown'})
    assert state(scheduler, 'pct_a')['used_up']
    assert claim(scheduler)['reason'] == 'quota_reserve'
    update(scheduler, 'pct_a', 30)
    assert claim(scheduler)['execution']['execution_id'] == queued.execution_id


@pytest.mark.parametrize('account,unit', [('pct_a', '%'), ('cny', 'CNY'), ('usd', 'USD')])
def test_zero_releases_queued_work_and_does_not_cancel_on_empty_balance(scheduler, account, unit):
    """阈值改为 0 立即放行被保留的排队执行，余量归零也不取消运行中的执行"""
    update(scheduler, account, 1)
    configure(scheduler, account, 2, unit)
    pending = submit(scheduler, account)
    assert claim(scheduler)['reason'] == 'quota_reserve'
    # Disabling the threshold releases an old hold without another observation.
    with scheduler._connect() as connection:
        import json
        row = connection.execute('SELECT value FROM execution_store_meta WHERE key=%s',
                                 (f'quota:account:{account}',)).fetchone()
        sample = json.loads(row['value'])
        sample['observed_at'] = 1
        connection.execute('UPDATE execution_store_meta SET value=%s WHERE key=%s',
                           (json.dumps(sample), f'quota:account:{account}'))
        connection.commit()
    configure(scheduler, account, 0, unit)
    assert claim(scheduler)['execution']['execution_id'] == pending.execution_id
    # A balance account runs out naturally; a used-up percent account pauses
    # (test_used_up_account_cancels_running_work_and_unreadable_samples_keep_the_hold).
    update(scheduler, account, 1 if unit == '%' else 0)
    assert not scheduler.snapshot(pending.execution_id)['scheduler']['cancel_requested']
    following = submit(scheduler, account)
    assert claim(scheduler)['execution']['execution_id'] == following.execution_id
    restored = Scheduler(accounts=scheduler.accounts, quota_reserve_enabled=True)
    assert not state(restored, account)['automatic_paused']
    # A positive threshold still restores automatic protection.
    configure(scheduler, account, 1, unit)
    assert state(scheduler, account)['automatic_paused']


@pytest.mark.parametrize('metric,unit', [('percent', '%'), ('balance', 'USD')])
def test_session_open_respects_disabled_threshold_without_fresh_positive_quota(monkeypatch, metric, unit):
    """关闭阈值为 0 时，打开会话不要求新鲜的正余量"""
    quota = quota_state({'status': 'reserved', 'observed_at': 1}, now=1000,
        threshold={'metric': metric, 'unit': unit, 'value': 0, 'supported': True})
    monkeypatch.setattr(execution_port, 'health', lambda: {'scheduler': {'capacity_partitions': {
        'test': {'quota_reserve': quota}}}})
    assert resident_restart_domain._account_session_open_quota({'capacity_key': 'test'}) == quota


def test_session_open_refuses_a_used_up_account_with_zero_threshold(monkeypatch):
    """额度已用完的账号即使 Close 阈值为 0 也不能创建新 Session"""
    quota = quota_state({'status': 'available', 'complete': True, 'observed_at': 1000, 'remaining_percent': 20,
                         'exhausted': True}, now=1000,
                        threshold={'metric': 'percent', 'unit': '%', 'value': 0, 'supported': True})
    monkeypatch.setattr(execution_port, 'health', lambda: {'scheduler': {'capacity_partitions': {
        'test': {'quota_reserve': quota}}}})
    with pytest.raises(ValueError, match='额度已用完'):
        resident_restart_domain._account_session_open_quota({'capacity_key': 'test'})
