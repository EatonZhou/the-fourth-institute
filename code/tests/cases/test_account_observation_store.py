"""Every actual refresh has durable, content-free evidence without extra IO to providers."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock
import json

import pytest

from management_portal.execution.account_observation_store import AccountObservationStore, evidence
from management_portal.execution.account_monitor import AccountMonitor


def sample(remaining=80, reset=2000):
    return {'status': 'ok', 'checked_at': 1000,
            'rate_limits': [{'id': 'weekly', 'remaining_percent': remaining, 'resets_at': reset,
                             'window_duration_mins': 10080, 'observed_at': 900, 'stale': False}]}


def test_equal_refreshes_are_preserved_across_restarts_and_pages():
    store = AccountObservationStore()
    a = store.append('codex', 'one', sample(), started_at=1000, finished_at=1001)
    b = store.append('codex', 'one', sample(), started_at=1000, finished_at=1001, forced=True)
    restarted = AccountObservationStore()
    rows = restarted.read('codex', 'one', since=1000, until=1002, limit=1)
    assert len(rows) == 1 and rows[0]['id'] == a
    rows = restarted.read('codex', 'one', since=1000, until=1002, after_id=a)
    assert len(rows) == 1 and rows[0]['id'] == b and rows[0]['forced'] is True
    assert rows[0]['sample']['rate_limits'][0]['observed_at'] == 900


def test_reset_transition_does_not_overwrite_previous_observation():
    store = AccountObservationStore()
    for now, remaining, reset in ((1000, 0, 2000), (1100, 100, 3000)):
        store.append('codex', 'one', sample(remaining, reset), started_at=now, finished_at=now)
    rows = store.read('codex', 'one', since=1000, until=1101)
    assert [r['sample']['rate_limits'][0]['resets_at'] for r in rows] == [2000, 3000]
    assert [r['sample']['rate_limits'][0]['remaining_percent'] for r in rows] == [0, 100]
    assert len(store.read('codex', 'one', since=1000, until=1100)) == 1


def test_account_and_provider_scopes_are_independent():
    store = AccountObservationStore()
    for provider, account in (('codex', 'one'), ('codex', 'two'), ('kimi', 'one')):
        store.append(provider, account, sample(), started_at=1000, finished_at=1001)
    assert len(store.read('codex', 'one', since=1000, until=1002)) == 1
    assert store.read('claude', 'one', since=1000, until=1002) == []


def test_history_whitelist_drops_credentials_catalog_and_exception_text():
    original = {**sample(), 'error': 'SECRET', 'secret': 'SECRET',
                'selection_catalog': {'models': ['SECRET']},
                'credits': {'balance': 2.5, 'has_credits': True, 'secret': 'SECRET'},
                'balance': {'amount': 3, 'currency': 'USD', 'updated_at': '2026-11-01T00:00:00Z', 'raw': 'SECRET'}}
    original['rate_limits'][0]['credentials'] = 'SECRET'
    stored = evidence(original)
    assert 'SECRET' not in json.dumps(stored)
    assert stored['credits']['balance'] == 2.5
    assert stored['balance']['amount'] == 3
    assert original['rate_limits'][0]['credentials'] == 'SECRET'


def test_failed_observation_retains_old_source_time_and_marks_retained_values_stale():
    store = AccountObservationStore()
    failed = {**sample(), 'status': 'unavailable', 'error_code': 'account_read_timeout',
              'credits': {'balance': 42, 'observed_at': 800}, 'error': 'SECRET'}
    store.append('codex', 'one', failed, started_at=1000, finished_at=1001)
    saved = store.read('codex', 'one', since=1000, until=1002)[0]['sample']
    assert saved['error_code'] == 'account_read_timeout'
    assert saved['rate_limits'][0]['stale'] and saved['credits']['stale']
    assert saved['rate_limits'][0]['observed_at'] == 900
    assert saved['credits']['observed_at'] == 800


def test_concurrent_writers_append_without_collapsing_equal_values():
    def write(i):
        return AccountObservationStore().append('codex', 'one', sample(), started_at=1000, finished_at=1001)
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(write, range(8)))
    assert len(set(ids)) == 8
    assert len(AccountObservationStore().read('codex', 'one', since=1000, until=1002)) == 8


@pytest.mark.parametrize('kwargs', [{'limit': 0}, {'limit': 1001}, {'after_id': -1}])
def test_invalid_pages_rejected(kwargs):
    with pytest.raises(ValueError):
        AccountObservationStore().read('codex', 'one', since=1000, until=1002, **kwargs)


def make_monitor(history=None):
    account = SimpleNamespace(account_id='one', provider='test', kind='service',
                              billing_account_id='', quota_policy='none', quota_enabled=False)
    accounts = Mock(adapters={'test': SimpleNamespace()})
    accounts.accounts.return_value = [account]
    accounts.observe.return_value = sample()
    accounts.runtime_catalog.return_value = {'models': []}
    history = history if history is not None else Mock()
    return AccountMonitor(accounts, Mock(), usage_store=Mock(), login_state_store=Mock(),
                          observation_store=history), account, accounts, history


def test_monitor_records_each_refresh_without_extra_provider_calls():
    monitor, account, accounts, history = make_monitor()
    monitor.refresh_account(account)
    monitor.refresh_account(account, force=True)
    assert accounts.observe.call_count == 2
    assert history.append.call_count == 2
    assert history.append.call_args.kwargs['forced'] is True
    assert history.append.call_args.args[2]['rate_limits'][0]['observed_at'] == 900


def test_persistence_failure_does_not_turn_fresh_quota_into_failure(caplog):
    monitor, account, accounts, history = make_monitor()
    history.append.side_effect = RuntimeError('SECRET')
    monitor.refresh_account(account)
    assert monitor._samples['one']['status'] == 'ok'
    assert accounts.observe.call_count == 1
    assert 'history write failed' in caplog.text
    assert 'SECRET' not in caplog.text


def test_monitor_retains_failure_attempt_as_history():
    monitor, account, accounts, history = make_monitor()
    monitor.refresh_account(account)
    accounts.observe.side_effect = TimeoutError('SECRET')
    monitor.refresh_account(account)
    assert history.append.call_count == 2
    last = history.append.call_args.args[2]
    assert last['status'] == 'unavailable'
    assert last['error_code'] == 'account_read_timeout'
    assert last['rate_limits'][0]['stale'] is True


def test_quota_reads_happen_only_at_start_reset_failure_or_for_a_close_threshold():
    """Without a Close threshold an account is read once, then only after a reset or a failed read."""
    monitor, account, accounts, history = make_monitor()
    account.capacity_key = 'account:one'
    assert monitor._observation_due(account, 1000, set())
    monitor._samples['one'] = {'status': 'ok', 'checked_at': 1000,
                               'rate_limits': [{'id': 'weekly', 'resets_at': 5000}]}
    assert not monitor._observation_due(account, 4999, set())
    assert monitor._observation_due(account, 5000, set())
    assert monitor._observation_due(account, 1001, {'account:one'})
    assert monitor._observation_due(account, 1001, None)
    monitor._samples['one'] = {'status': 'unavailable', 'checked_at': 1000, 'rate_limits': []}
    assert not monitor._observation_due(account, 1299, set())
    assert monitor._observation_due(account, 1300, set())


def test_fresh_reading_required_only_for_enabled_thresholds():
    monitor, *_ = make_monitor()
    monitor.scheduler.health.return_value = {'capacity_partitions': {
        'codex:four': {'close_threshold': {'value': 2.0}}, 'codex:one': {'close_threshold': {'value': 0.0}},
        'shared-ai': {}}}
    assert monitor._fresh_reading_required() == {'codex:four'}
    monitor.scheduler.health.side_effect = RuntimeError('db')
    assert monitor._fresh_reading_required() is None


def test_shutdown_cancellation_is_not_recorded_as_a_new_observation():
    monitor, account, accounts, history = make_monitor()
    def cancelled(*args, **kwargs):
        monitor.stopped.set()
        return sample()
    accounts.observe.side_effect = cancelled
    monitor.refresh_account(account)
    history.append.assert_not_called()
