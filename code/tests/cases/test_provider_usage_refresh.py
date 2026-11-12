import pytest

from scripts.provider_usage_refresh import home_quota_records, claude_quota_records
from scripts.provider_usage_cycles import reset_evidence, normalize_quota_endpoints
from scripts.provider_weekly_usage import moment


@pytest.mark.parametrize('window', ['seven_day', 'seven_day_sonnet', 'seven_day_opus',
                                  'limit7d', 'Gemini Models/Weekly Limit Remaining'])
def test_declared_weekly_windows_preserve_source_time_and_account(window):
    row = {'account_id': 'a', 'finished_at': '2026-11-01T12:00:00Z', 'sample': {
        'status': 'ok', 'rate_limits': [{'id': window, 'used_percent': 10,
            'observed_at': '2026-11-01T10:00:00Z', 'resets_at': '2026-11-08T10:00:00Z'}]}}
    q = list(home_quota_records([row]))[0]
    assert q['at'] == '2026-11-01T10:00:00Z'
    assert q['account_id'] == 'a'
    assert q['remaining_percent'] == 90
    assert q['window_duration_mins'] == 10080


@pytest.mark.parametrize('window,duration', [('five_hour', None), ('limit5h', 300),
    ('Gemini Models/Five Hour Limit Remaining', None), ('weekly', 300), ('unknown', None)])
def test_never_infers_weekly_from_labels_or_overrides_duration(window, duration):
    row = {'rate_limits': [{'id': window, 'window_duration_mins': duration, 'label': 'Weekly'}]}
    assert list(home_quota_records([row])) == []


def test_failed_home_sample_marks_old_values_stale():
    q = list(home_quota_records([{'sample': {'status': 'unavailable', 'rate_limits': [
        {'id': 'seven_day', 'remaining_percent': 100, 'observed_at': '2026-11-01T01:00:00Z'}]}}]))[0]
    assert q['stale'] is True


def test_first_use_after_zero_is_supporting_time_not_an_exact_reset():
    quotas = [{'at': at, 'resets_at': '2026-11-08T00:00:00Z', 'remaining_percent': value,
               'window_duration_mins': 10080, 'quota_group': 'a', 'evidence_source': 'monitor_quota'}
              for at, value in [('2026-11-01T01:00:00Z', 80), ('2026-11-01T02:00:00Z', 100)]]
    events = [{'at': '2026-11-01T03:00:00Z', 'model': 'm', 'usage': {'total_tokens': 10}}]
    r = reset_evidence(events, quotas, moment)[-1]
    assert r['kind'] == 'first_usage_after_reported_zero'
    assert r['reset_between'] == ['2026-11-01T01:00:00+00:00', '2026-11-01T02:00:00+00:00']
    assert r['first_usage_at'] == '2026-11-01T03:00:00+00:00'
    assert 'inferred_not_exact' in r['limitations']


def test_conflicting_equal_priority_samples_do_not_form_boundary():
    quotas = [{'at': '2026-11-01T01:00:00Z', 'resets_at': '2026-11-08T00:00:00Z',
        'remaining_percent': value, 'window_duration_mins': 10080, 'evidence_source': 'monitor_quota'}
        for value in [10, 100]]
    r = reset_evidence([], normalize_quota_endpoints(quotas, moment), moment)
    assert r[0]['kind'] == 'conflicting_equal_priority_observations'
    assert r[0]['usable_boundary'] is False


def test_claude_raw_metadata_is_deduplicated_and_not_a_percentage():
    info = {'rateLimitType': 'seven_day', 'resetsAt': 1794531540, 'status': 'rejected'}
    row = {'timestamp': '2026-11-11T08:00:00Z', 'quotaLimits': info,
           'apiErrorParams': {'rate_limit_info': info}}
    result = list(claude_quota_records(row))
    assert len(result) == 1
    assert result[0]['remaining_percent'] is None
    assert result[0]['resets_at'] == info['resetsAt']
    assert list(claude_quota_records({**row, 'timestamp': None})) == []
    assert list(claude_quota_records({'timestamp': row['timestamp'], 'quotaLimits': {
        **info, 'rateLimitType': 'five_hour'}})) == []


def test_rolling_reset_prediction_does_not_create_a_cycle_boundary():
    quotas = [{'at': f'2026-11-01T0{i}:00:00Z', 'resets_at': f'2026-11-08T0{i}:00:00Z',
               'remaining_percent': 100, 'window_duration_mins': 10080, 'quota_group': 'unused'}
              for i in (1, 2, 3)]
    evidence = reset_evidence([], quotas, moment)
    movements = [e for e in evidence if e['kind'] == 'reset_endpoint_change']
    assert len(movements) == 2
    assert all(not e['usable_boundary'] for e in movements)


def test_home_collection_time_does_not_replace_missing_provider_time():
    assert list(home_quota_records([{'finished_at': '2026-11-01T00:00:00Z',
        'sample': {'rate_limits': [{'id': 'seven_day', 'remaining_percent': 100}]}}])) == []


@pytest.mark.parametrize('utilization,remaining', [(0, 100), (.25, 75), (1, 0),
    (True, None), (-1, None), (101, None), (float('nan'), None), ('0.5', None)])
def test_claude_native_numeric_utilization_is_not_discarded(utilization, remaining):
    row = {'timestamp': '2026-11-11T08:00:00Z', 'quotaLimits': {
        'rateLimitType': 'seven_day', 'resetsAt': 1794531540, 'utilization': utilization}}
    assert list(claude_quota_records(row))[0]['remaining_percent'] == remaining


def test_claude_sdk_unified_windows_and_scope():
    row = {'type': 'rate_limit_event', 'timestamp': '2026-11-11T08:00:00Z',
        'rate_limit_info': {'unifiedWindows': {
            kind: {'resetsAt': 1794531540, 'utilization': .2}
            for kind in ['seven_day', 'seven_day_opus', 'five_hour']}}}
    records = list(claude_quota_records(row))
    assert [r['quota_group'] for r in records] == ['claude', 'seven_day_opus']
    assert all(r['remaining_percent'] == 80 for r in records)
    assert list(claude_quota_records({**row, 'type': 'assistant'})) == []
