"""All native pools are displayed without changing account-wide admission."""
from management_portal.execution.codex_quota import rate_limit_windows, quota_sample
from management_portal.execution.account_observation_store import evidence, merge_windows


def test_all_pools_survive_while_extra_exhaustion_does_not_pause_account():
    payload = {'rateLimitsByLimitId': {
        'codex': {'primary': {'usedPercent': 20, 'windowDurationMins': 300, 'resetsAt': 200},
                  'secondary': {'usedPercent': 30, 'windowDurationMins': 10080, 'resetsAt': 300}},
        'codex_bengalfox': {'limitName': 'GPT-5.3-Codex-Spark',
                           'primary': {'usedPercent': 100, 'windowDurationMins': 10080, 'resetsAt': 400}}}}
    windows = rate_limit_windows(payload, observed_at=100)
    assert [w['id'] for w in windows] == ['primary', 'secondary', 'codex_bengalfox/primary']
    assert windows[-1]['remaining_percent'] == 0
    assert windows[-1]['label'] == 'GPT-5.3-Codex-Spark · 每周'
    assert quota_sample(payload, observed_at=100)['remaining_percent'] == 70
    saved = evidence({'status': 'ok', 'rate_limits': windows})
    assert saved['rate_limits'] == windows
    assert len(merge_windows({'rate_limits': []}, saved, now=101)['rate_limits']) == 3


def test_absent_window_is_not_fabricated_and_invalid_percentage_is_unknown():
    windows = rate_limit_windows({'rateLimits': {'primary': {'usedPercent': float('nan')},
                                                 'secondary': None}}, observed_at=100)
    assert len(windows) == 1 and windows[0]['remaining_percent'] is None


def test_by_id_is_authoritative_and_top_level_fallback_is_not_duplicated():
    payload = {'rateLimits': {'primary': {'usedPercent': 90}},
               'rateLimitsByLimitId': {'codex': {'primary': {'usedPercent': 10}}}}
    assert [w['remaining_percent'] for w in rate_limit_windows(payload, observed_at=100)] == [90]
