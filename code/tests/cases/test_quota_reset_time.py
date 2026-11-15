import pytest

from management_portal.execution.quota_reset_time import reset_epoch_seconds, normalize_reset_windows
from management_portal.execution.account_monitor import _rationed_quota
from management_portal.execution.account_observation_store import evidence


@pytest.mark.parametrize('value', [None, True, False, -1, 0, float('nan'), float('inf'),
                                  1794734856000, '2030-01-01T01:00:00', 'not-a-date'])
def test_invalid_or_timezone_free_reset_is_unknown(value):
    assert reset_epoch_seconds(value) is None


@pytest.mark.parametrize('value', [1893456000, '2030-01-01T00:00:00Z',
                                  '2030-01-01T01:00:00+01:00', 'Jan 01, 2030, 00:00 UTC'])
def test_all_provider_formats_agree_in_utc(value):
    assert reset_epoch_seconds(value) == 1893456000


def test_expired_native_reset_cannot_freshen_quota_and_semantics_survive_log():
    s = normalize_reset_windows({'status': 'ok', 'rate_limits': [
        {'id': 'weekly', 'resets_at': 900, 'remaining_percent': 80}]}, 1000)
    assert s['status'] == 'unavailable' and s['error_code'] == 'quota_reset_not_advanced'
    window = evidence(s)['rate_limits'][0]
    assert window['stale'] and window['remaining_percent'] == 80
    assert window['reset_time_semantics'] == 'scheduled_endpoint_not_proven_reset_event'


def test_expired_extra_pool_does_not_invalidate_main_quota():
    s = normalize_reset_windows({'status': 'ok', 'quota': {
        'status': 'available', 'observed_at': 1000, 'windows': [{'resets_at': 2000}]},
        'rate_limits': [{'id': 'primary', 'resets_at': 2000},
                        {'id': 'extra', 'resets_at': 900}]}, 1000)
    assert s['status'] == 'ok' and s['rate_limits'][1]['stale']
    assert _rationed_quota(s)['valid_until'] == 2000


def test_past_reset_still_expires_scheduler_reading():
    assert _rationed_quota({'quota': {'status': 'available', 'observed_at': 1000},
                           'rate_limits': [{'resets_at': 900}]})['valid_until'] == 900


def test_expired_response_does_not_advance_success_clock(monkeypatch):
    from tests.cases.test_account_observation_store import make_monitor, sample
    monkeypatch.setattr('management_portal.execution.account_monitor.time.time', lambda: 1000)
    monitor, account, accounts, history = make_monitor()
    accounts.observe.return_value = sample(reset=900)
    monitor.refresh_account(account)
    history.save_success.assert_not_called()
    assert 'one' not in monitor._last_good_read
    assert monitor._samples['one']['error_code'] == 'quota_reset_not_advanced'


def test_missing_error_windows_do_not_erase_previous_observations():
    assert 'rate_limits' not in normalize_reset_windows({'status': 'unavailable'}, 1000)


def test_antigravity_offset_and_minute_precision():
    from management_portal.antigravity_accounts import quota_windows
    prefix = '### Quota Limits\n- **Gemini Models**\n  - **Weekly Limit Remaining:** 80% remaining (resets '
    assert quota_windows(prefix + '2030-01-01T01:00:00+01:00)\n', 1000)[0]['resets_at'] == 1893456000
    minute = quota_windows(prefix + 'Jan 01, 2030, 00:00 UTC)\n', 1000)[0]
    assert evidence(normalize_reset_windows({'rate_limits': [minute]}, 1000))['rate_limits'][0]['reset_time_precision_seconds'] == 60
