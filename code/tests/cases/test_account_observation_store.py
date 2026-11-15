"""Every actual refresh has durable, content-free evidence without extra IO to providers."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock
import json

import pytest

from management_portal.execution.account_observation_store import AccountObservationStore, evidence, merge_windows
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


def test_replay_model_pool_after_later_logs_omit_it_and_monitor_restarts():
    store = AccountObservationStore()
    fable = {'id': 'seven_day_overage_included', 'label': 'Fable 每周', 'remaining_percent': 0,
             'resets_at': 2000, 'observed_at': 900, 'stale': False}
    original = sample()
    original['rate_limits'].append(fable)
    store.append('claude', 'one', original, started_at=1000, finished_at=1001)
    store.append('claude', 'one', sample(), started_at=1100, finished_at=1101)
    restored = AccountObservationStore().restore_sample('claude', 'one', now=1200)
    assert fable in restored['rate_limits']
    assert AccountObservationStore().restore_sample('claude', 'two', now=1200)['rate_limits'] == []
    accounts = Mock(adapters={'claude': SimpleNamespace()})
    account = SimpleNamespace(provider='claude', account_id='one', quota_policy='reserve')
    accounts.accounts.return_value = [account]
    # Actual monitor restore is tested with an epoch-independent fake clock.
    from unittest.mock import patch
    with patch('management_portal.execution.account_monitor.time.time', return_value=1200):
        monitor = AccountMonitor(accounts, Mock(), usage_store=Mock(), login_state_store=Mock(), observation_store=store)
    assert fable in monitor._samples['one']['rate_limits']
    next_sample = merge_windows(sample(), restored, now=1300)
    store.append('claude', 'one', next_sample, started_at=1300, finished_at=1301)
    assert fable in AccountObservationStore().restore_sample('claude', 'one', now=1400)['rate_limits']
    assert all(w['id'] != fable['id'] for w in store.restore_sample('claude', 'one', now=2000)['rate_limits'])


def test_new_model_window_supersedes_old_and_failed_refresh_does_not_freshen_it():
    previous = {'rate_limits': [{'id': 'fable', 'resets_at': 2000, 'remaining_percent': 0,
                                'observed_at': 900, 'stale': False}]}
    failed = merge_windows({'status': 'unavailable'}, previous, now=1000)['rate_limits'][0]
    assert failed['stale'] and failed['observed_at'] == 900
    new = {'id': 'fable', 'resets_at': 3000, 'remaining_percent': 90, 'observed_at': 1200}
    assert merge_windows({'rate_limits': [new]}, previous, now=1200)['rate_limits'] == [new]
    store = AccountObservationStore()
    store.append('claude', 'one', previous, started_at=1000, finished_at=1000)
    store.append('claude', 'one', {'rate_limits': [new]}, started_at=1200, finished_at=1200)
    assert store.restore_sample('claude', 'one', now=2500)['rate_limits'] == [{**new, 'stale': False}]


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
    durable = AccountObservationStore()
    history.claim.side_effect = durable.claim
    history.state.side_effect = durable.state
    history.save_success.side_effect = durable.save_success
    history.release_unsent.side_effect = durable.release_unsent
    return AccountMonitor(accounts, Mock(), usage_store=Mock(), login_state_store=Mock(),
                          observation_store=history), account, accounts, history


def test_monitor_records_each_refresh_without_extra_provider_calls(monkeypatch):
    from management_portal.execution import account_monitor as module
    monkeypatch.setattr(module.time, 'time', lambda: 1000)
    monitor, account, accounts, history = make_monitor()
    monitor.refresh_account(account)
    monkeypatch.setattr(module.time, 'time', lambda: 1600)
    monitor.refresh_account(account, force=True)
    assert accounts.observe.call_count == 2
    assert history.append.call_count == 2
    assert history.append.call_args.kwargs['forced'] is True
    assert history.append.call_args.args[2]['rate_limits'][0]['observed_at'] == 900


def test_persistence_failure_does_not_turn_fresh_quota_into_failure(caplog, monkeypatch):
    monkeypatch.setattr('management_portal.execution.account_monitor.time.time', lambda: 1000)
    monitor, account, accounts, history = make_monitor()
    history.append.side_effect = RuntimeError('SECRET')
    monitor.refresh_account(account)
    assert monitor._samples['one']['status'] == 'ok'
    assert accounts.observe.call_count == 1
    assert 'history write failed' in caplog.text
    assert 'SECRET' not in caplog.text


def test_monitor_retains_failure_attempt_as_history(monkeypatch):
    from management_portal.execution import account_monitor as module
    monitor, account, accounts, history = make_monitor()
    monkeypatch.setattr(module.time, 'time', lambda: 1000)
    monitor.refresh_account(account)
    monkeypatch.setattr(module.time, 'time', lambda: 1600)
    accounts.observe.side_effect = TimeoutError('SECRET')
    monitor.refresh_account(account)
    assert history.append.call_count == 2
    last = history.append.call_args.args[2]
    assert last['status'] == 'unavailable'
    assert last['error_code'] == 'account_read_timeout'
    assert last['error_kind'] == 'TimeoutError'
    assert last['rate_limits'][0]['stale'] is True


def test_local_failure_releases_only_its_claim_and_retry_can_send(monkeypatch):
    from management_portal.execution import account_monitor as module
    from management_portal.execution.account_observation import quota_preflight
    monitor, account, accounts, history = make_monitor()
    monkeypatch.setattr(module.time, 'time', lambda: 1000)
    def local_failure(*args, **kwargs):
        with quota_preflight():
            raise ConnectionRefusedError('SECRET')
    accounts.observe.side_effect = local_failure
    monitor.refresh_account(account)
    assert history.state(account.provider, account.account_id)['attempted_at'] is None
    assert history.append.call_args.args[2]['quota_request_sent'] is False
    assert history.append.call_args.args[2]['error_kind'] == 'ConnectionRefusedError'
    accounts.observe.side_effect = None
    monitor.refresh_account(account)
    assert accounts.observe.call_count == 2
    # Successful dispatch keeps its slot; the third request is refused.
    monitor.refresh_account(account)
    assert accounts.observe.call_count == 2


def test_unsent_release_cannot_erase_newer_reservation_or_prior_cooldown():
    store = AccountObservationStore()
    assert store.claim('kimi', 'one', now=1000, interval=600)
    assert store.claim('kimi', 'one', now=1600, interval=600)
    assert not store.release_unsent('kimi', 'one', attempted_at=1000, previous_attempt=None)
    assert store.release_unsent('kimi', 'one', attempted_at=1600, previous_attempt=1000)
    assert store.state('kimi', 'one')['attempted_at'] == 1000
    assert not store.claim('kimi', 'one', now=1500, interval=600)


def test_account_deadlines_are_independent_of_hourly_checks():
    from management_portal.execution import account_monitor as m
    monitor, account, accounts, history = make_monitor()
    due = lambda now, requested=False: monitor._real_read_due(account, now, requested=requested)
    assert due(0)                                       # no successful observation yet
    monitor._last_read_attempt['one'] = monitor._last_good_read['one'] = 1000
    monitor._samples['one'] = {'rate_limits': [{'id': 'weekly', 'resets_at': 20000}]}
    # Scheduling chooses intent only; admission owns the 10-minute rule.
    assert due(1000 + m.MIN_READ_INTERVAL_SECONDS - 1, requested=True)
    assert due(1000 + m.MIN_READ_INTERVAL_SECONDS, requested=True)
    assert not due(19999)                              # before its reset
    assert due(20000)                                  # reset is due without hourly gating
    monitor._samples['one'] = {'rate_limits': []}
    deadline = 1000 + m.READING_EXPIRES_SECONDS - m.READING_REFRESH_LEAD_SECONDS
    assert not due(deadline - 1)
    assert due(deadline)
    assert due(deadline + 600)                          # failure remains due for admitted retry


def test_direct_and_concurrent_refreshes_share_one_admission_gate(monkeypatch):
    from management_portal.execution import account_monitor as module
    monitor, account, accounts, history = make_monitor()
    monkeypatch.setattr(module.time, 'time', lambda: 1000)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: monitor.refresh_account(account, force=True), range(8)))
    assert accounts.observe.call_count == history.append.call_count == 1
    monkeypatch.setattr(module.time, 'time', lambda: 1599)
    monitor.refresh_account(account, force=True)
    assert accounts.observe.call_count == 1
    monitor.refresh_account(account, passive=True)
    assert monitor._last_read_attempt['one'] == 1000
    monkeypatch.setattr(module.time, 'time', lambda: 1600)
    monitor.refresh_account(account)
    assert accounts.observe.call_count == 3


def test_rationed_quota_stays_valid_until_expiry_or_first_reset():
    from management_portal.execution.account_monitor import _rationed_quota, READING_EXPIRES_SECONDS
    sample = {'quota': {'status': 'available', 'observed_at': 1000, 'max_age_seconds': 60, 'valid_until': 1060},
              'rate_limits': [{'resets_at': 5000}, {'resets_at': 9000}, {'resets_at': 900}]}
    quota = _rationed_quota(sample)
    assert (quota['max_age_seconds'], quota['valid_until']) == (READING_EXPIRES_SECONDS, 900)
    assert _rationed_quota({'quota': {'status': 'unknown'}}) == {'status': 'unknown'}


def test_claude_cache_is_read_between_real_reads_without_a_usage_query(monkeypatch):
    monkeypatch.setattr('management_portal.execution.account_monitor.time.time', lambda: 1000)
    monitor, account, accounts, history = make_monitor()
    accounts.observes_passively = lambda a: True
    monitor.refresh_account(account, passive=True)
    assert accounts.observe.call_args.kwargs == {'force': False, 'passive': True}
    assert 'one' not in monitor._last_read_attempt and 'one' not in monitor._last_good_read
    monitor.refresh_account(account)
    assert accounts.observe.call_args.kwargs == {'force': False}
    assert 'one' in monitor._last_good_read


def test_shutdown_cancellation_is_not_recorded_as_a_new_observation():
    monitor, account, accounts, history = make_monitor()
    def cancelled(*args, **kwargs):
        monitor.stopped.set()
        return sample()
    accounts.observe.side_effect = cancelled
    monitor.refresh_account(account)
    history.append.assert_not_called()
