from concurrent.futures import ThreadPoolExecutor
import json

from management_portal.execution.account_observation_store import AccountObservationStore
from management_portal.execution.subscription_profile import kimi_profile, reset_cards


def test_kimi_profile_uses_native_level_without_account_identifiers():
    result = kimi_profile({'kind': 'ok', 'userInfo': {'userLevel': 20, 'userLevelName': 'Moderato', 'region': 'REGION_CN',
                                                  'phone': 'SECRET', 'userId': 'SECRET'}})
    assert result['tier'] == {'id': '20', 'name': 'Moderato'}
    assert result['region'] == 'REGION_CN'
    assert 'SECRET' not in json.dumps(result)
    assert kimi_profile({'kind': 'error'})['status'] == 'unknown'


def test_reset_card_count_is_authoritative_and_null_details_stay_unknown():
    result = reset_cards({'rateLimitResetCredits': {'availableCount': 3, 'credits': [
        {'id': 'c', 'expiresAt': 2000, 'title': 'Weekly', 'token': 'SECRET'}]}}, observed_at=1000)
    assert result['available_count'] == 3 and result['cards'][0]['expiresAt'] == 2000
    assert 'SECRET' not in json.dumps(result)
    assert reset_cards({}, observed_at=1000)['available_count'] is None
    assert reset_cards({'rateLimitResetCredits': {'availableCount': 0, 'credits': []}}, observed_at=1000)['available_count'] == 0
    assert reset_cards({'rateLimitResetCredits': {'availableCount': 2, 'credits': None}}, observed_at=1000)['cards'] is None


def test_query_admission_survives_restart_and_coalesces_processes():
    def claim(_):
        return AccountObservationStore().claim('codex', 'one', now=1000, interval=600)
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(claim, range(8))) == 1
    restarted = AccountObservationStore()
    assert not restarted.claim('codex', 'one', now=1599, interval=600)
    assert restarted.claim('codex', 'one', now=1600, interval=600)
    restarted.save_success('codex', 'one', {'status': 'ok', 'rate_limits': []}, now=1601)
    assert AccountObservationStore().state('codex', 'one')['success_at'] == 1601


def test_daily_profile_claim_is_separate_from_usage_and_retains_history():
    store = AccountObservationStore()
    assert store.claim('kimi', 'one', now=1000, interval=86400, profile=True)
    value = kimi_profile({'kind': 'ok', 'userInfo': {'userLevel': 20, 'userLevelName': 'Moderato'}})
    store.save_profile('kimi', 'one', value)
    assert not AccountObservationStore().claim('kimi', 'one', now=2000, interval=86400, profile=True)
    assert store.claim('kimi', 'one', now=2000, interval=600)
    assert store.state('kimi', 'one')['profile']['tier']['name'] == 'Moderato'
    with store.connection() as c:
        assert c.execute('SELECT count(*) AS n FROM subscription_observations').fetchone()['n'] == 1


def test_daily_profile_does_not_skip_next_day_due_to_runtime_jitter():
    from datetime import datetime
    store = AccountObservationStore()
    first = datetime.fromisoformat('2026-11-14T09:00:30+08:00').timestamp()
    tomorrow = datetime.fromisoformat('2026-11-15T09:00:00+08:00').timestamp()
    assert store.claim('kimi', 'daily', now=first, interval=86400, profile=True)
    assert not store.claim('kimi', 'daily', now=first+3600, interval=86400, profile=True)
    assert store.claim('kimi', 'daily', now=tomorrow, interval=86400, profile=True)


def test_monitor_restores_attempt_success_and_tier_without_new_provider_reads(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from management_portal.execution.account_monitor import AccountMonitor
    from management_portal.execution import subscription_profile
    account = SimpleNamespace(provider='kimi', account_id='one', route_id='one', kind='subscription_login')
    accounts = Mock(adapters={'kimi': SimpleNamespace()})
    accounts.accounts.return_value = [account]
    store = AccountObservationStore()
    assert store.claim('kimi', 'one', now=1000, interval=600)
    store.save_success('kimi', 'one', {'status': 'ok', 'rate_limits': []}, now=1001)
    monitor = AccountMonitor(accounts, Mock(), observation_store=store, login_state_store=Mock())
    assert monitor._last_read_attempt['one'] == 1000 and monitor._last_good_read['one'] == 1001
    reader = Mock(return_value=kimi_profile({'kind': 'ok', 'userInfo': {'userLevel': 20, 'userLevelName': 'Moderato'}}))
    monkeypatch.setattr(subscription_profile, 'read_profile', reader)
    for _ in range(2):
        subscription_profile.collect_profiles(accounts, store=store)
    assert reader.call_count == 1
    accounts.observe.assert_not_called()
    restarted = AccountMonitor(accounts, Mock(), observation_store=AccountObservationStore(), login_state_store=Mock())
    assert store.profiles(['one'])['one']['tier']['name'] == 'Moderato'


def test_failed_tier_read_retries_on_the_next_hourly_check_not_the_next_day():
    store = AccountObservationStore()
    assert store.claim('kimi', 'one', now=1000, interval=86400, profile=True)
    store.save_profile('kimi', 'one', {'stale': True, 'error_code': 'KimiCodeUnavailable'})
    assert store.claim('kimi', 'one', now=4600, interval=86400, profile=True)
    store.save_profile('kimi', 'one', kimi_profile({'kind': 'ok', 'userInfo': {'userLevelName': 'Moderato'}}))
    assert not store.claim('kimi', 'one', now=8200, interval=86400, profile=True)


def test_kimi_daily_reader_uses_existing_server_with_bounded_readiness_probe(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from management_portal import kimi_account_runtime
    from management_portal.execution.subscription_profile import read_profile
    client = Mock()
    client._request.return_value = {'kind': 'ok', 'userInfo': {'userLevelName': 'Moderato'}}
    factory = Mock(return_value=client)
    monkeypatch.setattr(kimi_account_runtime, 'client_for_route', factory)
    result = read_profile(SimpleNamespace(provider='kimi', route_id='kimi-code'), None)
    assert result['tier']['name'] == 'Moderato'
    factory.assert_called_once_with('kimi-code', for_control=True, probe_timeout=15)
    client._request.assert_called_once_with('GET', 'oauth/userinfo')
