from scripts.provider_usage_cycles import reset_evidence, cycle_bounds
from scripts.provider_weekly_usage import moment


def q(day, remaining, source='native_quota', **extra):
    return {'at': f'2026-11-{day:02}T00:00:00Z', 'remaining_percent': remaining,
            'resets_at': '2026-11-15T00:00:00Z', 'window_duration_mins': 10080,
            'evidence_source': source, **extra}


def event(day):
    return {'at': f'2026-11-{day:02}T12:00:00Z', 'model': 'm', 'usage': {'total_tokens': 100}}


def test_zero_then_first_usage_is_ranked_but_not_an_exact_reset():
    result = reset_evidence([event(3)], [q(1, 20), q(2, 100), q(4, 98)], moment)
    zero = result[0]
    assert zero['kind'] == 'first_usage_after_reported_zero'
    assert zero['priority'] == 4
    assert zero['first_usage_at'] == '2026-11-03T12:00:00+00:00'
    assert zero['reset_between'] == ['2026-11-01T00:00:00+00:00', '2026-11-02T00:00:00+00:00']
    assert 'reported_zero_may_be_rounded' in zero['limitations']


def test_first_zero_without_earlier_usage_does_not_invent_a_lower_bound():
    evidence = reset_evidence([event(3)], [q(2, 100)], moment)[0]
    assert evidence['reset_between'][0] is None
    assert not evidence['usable_boundary']
    assert evidence['first_usage_at']


def test_mirrored_observations_do_not_accumulate_confidence():
    quotas = [q(1, 30), q(2, 100)]
    baseline = reset_evidence([event(3)], quotas, moment)
    duplicated = quotas + [q(2, 100, 'daily_snapshot'), q(2, 100, 'monitor_quota')]*20
    assert reset_evidence([event(3)], duplicated, moment) == baseline


def test_source_ranking_retains_conflict_instead_of_averaging_times():
    result = reset_evidence([event(3)], [q(1, 20), q(2, 100), q(2, 60, 'daily_snapshot')], moment)
    assert result[0]['source_conflict']
    assert result[0]['source'] == 'native_quota'
    assert result[0]['source_priority'] > 2
    tied = reset_evidence([], [q(2, 100), q(2, 60)], moment)
    assert tied[0]['kind'] == 'conflicting_equal_priority_observations'
    assert not tied[0]['usable_boundary']


def test_stale_zero_is_not_reset_evidence():
    assert not reset_evidence([event(3)], [q(1, 20), q(2, 100, stale=True)], moment)


def test_explicit_provider_timestamp_outranks_inference():
    observed = q(2, 100, 'provider_reset_event', reset_occurred_at='2026-11-01T23:59:00Z',
                 reset_event_verified=True, account_id='a', event_id='receipt-1')
    result = reset_evidence([event(3)], [q(1, 20), observed], moment)[0]
    assert result['priority'] == 5
    assert result['kind'] == 'explicit_provider_reset'
    assert result['reset_between'][0] == result['reset_between'][1]


def test_cycle_bounds_uses_zero_first_use_evidence_without_endpoint_change():
    quotas = [q(1, 20), q(2, 100), q(5, 20), q(6, 100)]
    result = cycle_bounds([event(3), event(7)], quotas, moment)
    assert result[0]['lower_tokens'] == 100
    assert result[0]['boundary_confidence'][0]['priority'] == 4
    assert result[0]['whole_account_error_bound'] is None


def test_model_scope_and_endpoint_limit_first_use_search():
    quotas = [q(1, 20, models=['different']), q(2, 100, models=['different'])]
    assert reset_evidence([event(3)], quotas, moment)[0]['first_usage_at'] is None
    assert reset_evidence([event(20)], [q(2, 100)], moment)[0]['first_usage_at'] is None


def test_zero_token_placeholders_and_unmapped_quota_groups_are_not_first_use():
    placeholder = event(2)
    placeholder['usage']['total_tokens'] = 0
    result = reset_evidence([placeholder, event(3)], [q(2, 100)], moment)
    assert result[0]['first_usage_at'].startswith('2026-11-03')
    result = reset_evidence([event(3)], [q(2, 100, quota_group='a'), q(2, 100, quota_group='b')], moment)
    assert all(r['first_usage_at'] is None for r in result)
    assert all('model_to_quota_group_unmapped' in r['limitations'] for r in result)


def test_cycle_pair_cannot_bridge_conflict_even_when_both_endpoints_are_candidates():
    quotas = [q(1, 20), q(2, 100), q(3, 50), q(3, 80), q(4, 50), q(5, 100)]
    assert cycle_bounds([event(2), event(4)], quotas, moment) == []
