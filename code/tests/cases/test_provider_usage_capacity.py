from datetime import datetime

from scripts.provider_usage_capacity import full_cycle_capacity
from scripts.provider_usage_events_import import provider_events
from scripts.provider_weekly_usage import moment


RESET = '2026-11-08T00:00:00Z'  # Cycle starts 2026-11-01T00:00:00Z.
SINCE, UNTIL = moment('2026-10-25T00:00:00Z'), moment('2026-11-20T00:00:00Z')


def event(at, model='a', **usage):
    return {'at': at, 'model': model, 'usage': usage}


def sample(at, remaining, reset=RESET, **extra):
    return {'at': at, 'resets_at': reset, 'remaining_percent': remaining, 'window_duration_mins': 10080, **extra}


def cycles(events, quotas, since=SINCE, until=UNTIL, provider=()):
    return full_cycle_capacity(events, quotas, moment, since, until, provider)


def test_cycle_counts_from_full_start_to_first_exhaustion():
    """整周期从 100% 起算，到第一次显示 0% 为止；采样之间的分段不参与计算。"""
    events = [event('2026-11-01T01:00:00Z', total_tokens=300),   # Before the first sample: still counted.
              event('2026-11-03T00:00:00Z', total_tokens=500),
              event('2026-11-05T00:00:00Z', total_tokens=200)]   # After exhaustion: credits.
    quotas = [sample('2026-11-02T00:00:00Z', 90), sample('2026-11-04T00:00:00Z', 0),
              sample('2026-11-06T00:00:00Z', 3)]  # A lagging later sample does not undo exhaustion.
    [c] = cycles(events, quotas)
    assert (c['status'], c['exhausted'], c['end_reason']) == ('measured', True, 'scheduled_end')
    assert c['cycle_start'] == '2026-11-01T00:00:00+00:00'
    assert c['final_observed_at'] == '2026-11-04T00:00:00+00:00'
    assert c['recorded_tokens']['total_tokens'] == 800
    assert c['tokens_after_final_observation'] == 200
    assert c['tokens_at_100_percent_same_mix']['total_tokens'] == 800


def test_model_tokens_are_split_by_service_tier():
    events = [event('2026-11-02T00:00:00Z', input_tokens=10, total_tokens=10),
              {**event('2026-11-02T01:00:00Z', input_tokens=30, total_tokens=30), 'service_tier': 'priority'}]
    [c] = cycles(events, [sample('2026-11-03T00:00:00Z', 0)])
    tiers = c['models']['a']['service_tiers']
    assert {t: v['recorded_tokens']['input_tokens'] for t, v in tiers.items()} == {'default': 10, 'priority': 30}
    assert c['models']['a']['recorded_tokens']['input_tokens'] == 40 and c['models']['a']['requests'] == 2


def test_typed_fields_overlap_and_are_reported_together():
    e = event('2026-11-02T00:00:00Z', input_tokens=80, cached_input_tokens=60, output_tokens=20,
              reasoning_output_tokens=5, total_tokens=100)
    [c] = cycles([e], [sample('2026-11-03T00:00:00Z', 0)])
    assert c['tokens_at_100_percent_same_mix'] == {
        'input_tokens': 80, 'cached_input_tokens': 60, 'output_tokens': 20,
        'reasoning_output_tokens': 5, 'total_tokens': 100}
    assert c['models']['a']['requests_reporting_field']['total_tokens'] == 1
    assert 'reasoning_is_a_subset_of_output_do_not_add' in c['limitations']


def test_global_reset_explains_unexhausted_cycle_and_is_not_capacity():
    """统一刷新截断的周期标为未用完，不参与容量计算。"""
    events = [event('2026-11-02T00:00:00Z', total_tokens=100), event('2026-11-04T00:00:00Z', total_tokens=50)]
    nxt = '2026-11-10T12:10:00Z'  # Next window starts 11-03 12:10.
    quotas = [sample('2026-11-03T00:00:00Z', 75), sample('2026-11-04T06:00:00Z', 90, reset=nxt)]
    reset = [{'kind': 'global_reset', 'at': moment('2026-11-03T12:00:00Z'), 'id': 'r'}]
    first, second = cycles(events, quotas, until=moment('2026-11-05T00:00:00Z'), provider=reset)
    assert (first['status'], first['end_reason']) == ('not_exhausted', 'global_reset')
    assert first['tokens_at_100_percent_same_mix'] is None
    assert second['status'] == 'ongoing' and second['cycle_start'] == '2026-11-03T12:10:00+00:00'
    first, _ = cycles(events, quotas, until=moment('2026-11-05T00:00:00Z'))
    assert first['end_reason'] == 'early_reset_unattributed'


def test_policy_change_splits_eras_and_blocks_a_spanning_cycle():
    events = [event('2026-11-02T00:00:00Z', total_tokens=100)]
    policy = [{'kind': 'policy', 'at': moment('2026-11-02T12:00:00Z'), 'id': 'double'}]
    [c] = cycles(events, [sample('2026-11-03T00:00:00Z', 0)], provider=policy)
    assert c['status'] == 'policy_changed' and c['policy_changes_in_cycle'] == ['double']
    early = [{'kind': 'policy', 'at': moment('2026-10-30T00:00:00Z'), 'id': 'double'}]
    [c] = cycles(events, [sample('2026-11-03T00:00:00Z', 0)], provider=early)
    assert c['status'] == 'measured' and c['policy_era'] == 'double'


def test_declared_plan_periods_fill_cycles_without_sampled_plans():
    events = [event('2026-11-02T00:00:00Z', total_tokens=100)]
    plus = [{'plan': 'plus', 'from': moment('2026-10-01T00:00:00Z'), 'until': moment('2026-11-02T12:00:00Z')}]
    [c] = full_cycle_capacity(events, [sample('2026-11-03T00:00:00Z', 0)], moment, SINCE, UNTIL, (), plus)
    assert (c['plan_types'], c['plan_basis'], c['status']) == (['plus'], 'declared_periods', 'measured')
    both = plus + [{'plan': 'pro', 'from': moment('2026-11-02T12:00:00Z'), 'until': moment('2026-12-01T00:00:00Z')}]
    [c] = full_cycle_capacity(events, [sample('2026-11-03T00:00:00Z', 0)], moment, SINCE, UNTIL, (), both)
    assert c['status'] == 'plan_changed'
    sampled = [sample('2026-11-03T00:00:00Z', 0, plan_type='pro')]  # Samples win over declarations.
    [c] = full_cycle_capacity(events, sampled, moment, SINCE, UNTIL, (), plus)
    assert (c['plan_types'], c['plan_basis']) == (['pro'], 'quota_samples')


def test_drifting_endpoints_merge_and_lagging_old_samples_stop_at_next_window():
    """重置时间漂移几分钟仍是同一周期；下一周期开始后旧周期的迟到采样和 token 不再计入。"""
    events = [event('2026-11-02T00:00:00Z', total_tokens=100), event('2026-11-04T00:00:00Z', total_tokens=400)]
    nxt = '2026-11-10T12:00:00Z'  # Next window starts 11-03 12:00.
    quotas = [sample('2026-11-02T06:00:00Z', 90), sample('2026-11-03T00:00:00Z', 0, reset='2026-11-08T00:04:00Z'),
              sample('2026-11-05T00:00:00Z', 10),  # An old session still reporting the old endpoint.
              sample('2026-11-05T00:00:00Z', 70, reset=nxt)]
    first, second = cycles(events, quotas, until=moment('2026-11-06T00:00:00Z'))
    assert first['samples'] == 2 and first['status'] == 'measured'
    assert first['recorded_tokens']['total_tokens'] == 100 and first['tokens_after_final_observation'] == 0
    assert second['cycle_start'] == '2026-11-03T12:00:00+00:00'


def test_floating_unstarted_endpoint_does_not_cut_the_running_cycle():
    events = [event('2026-11-02T00:00:00Z', total_tokens=100), event('2026-11-05T00:00:00Z', total_tokens=100)]
    quotas = [sample('2026-11-02T06:00:00Z', 90), sample('2026-11-06T00:00:00Z', 0),
              sample('2026-11-04T00:00:00Z', 100, reset='2026-11-11T00:00:00Z')]  # Not started yet.
    [c] = cycles(events, quotas)
    assert c['status'] == 'measured' and c['recorded_tokens']['total_tokens'] == 200


def test_unusable_cycles_are_reported_with_reasons_not_capacity():
    events = [event('2026-11-02T00:00:00Z', total_tokens=100)]
    assert cycles(events, [sample('2026-11-03T00:00:00Z', 0)], since=moment('2026-11-02T00:00:00Z'))[0]['status'] == 'start_before_query'
    assert cycles(events, [sample('2026-11-03T00:00:00Z', 40)])[0]['status'] == 'not_exhausted'
    near = cycles(events, [sample('2026-11-03T00:00:00Z', 5)])[0]  # Last points: rounding and lag.
    assert near['status'] == 'measured' and near['tokens_at_100_percent_same_mix']['total_tokens'] == 105
    assert cycles([], [sample('2026-11-03T00:00:00Z', 0)])[0]['status'] == 'no_tokens_recorded'
    assert cycles(events, [sample('2026-11-03T00:00:00Z', 0)], until=moment('2026-11-04T00:00:00Z'))[0]['status'] == 'ongoing'
    plan = [sample('2026-11-02T12:00:00Z', 95, plan_type='plus'), sample('2026-11-03T00:00:00Z', 0, plan_type='pro')]
    [c] = cycles(events, plan)
    assert c['status'] == 'plan_changed' and c['tokens_at_100_percent_same_mix'] is None


def test_stale_invalid_and_non_weekly_samples_are_ignored():
    events = [event('2026-11-02T00:00:00Z', total_tokens=100)]
    quotas = [sample('2026-11-03T00:00:00Z', 0, stale=True), sample('2026-11-03T00:00:00Z', True),
              {**sample('2026-11-03T00:00:00Z', 0), 'window_duration_mins': 300},
              sample('2026-11-09T00:00:00Z', 0)]  # Observed after its own reset endpoint.
    assert cycles(events, quotas) == []


def test_main_limit_excludes_models_of_named_limits_and_unmapped_groups_are_skipped():
    events = [event('2026-11-02T00:00:00Z', total_tokens=100),
              event('2026-11-02T00:00:00Z', model='gpt-5.3-codex-spark', total_tokens=900)]
    quotas = [sample('2026-11-03T00:00:00Z', 0, quota_group='codex'),
              sample('2026-11-03T00:00:00Z', 50, quota_group='codex_bengalfox', models=['gpt-5.3-codex-spark'])]
    main = next(c for c in cycles(events, quotas) if c['quota_group'] == 'codex')
    assert main['recorded_tokens']['total_tokens'] == 100
    assert main['excluded_models'] == ['gpt-5.3-codex-spark']
    quotas.append(sample('2026-11-03T00:00:00Z', 50, quota_group='other'))
    assert all(c['quota_group'] != 'codex' for c in cycles(events, quotas))


def test_provider_events_keep_completed_global_resets_and_policies_only():
    payload = {'events': [
        {'tool': 'codex', 'type': 'usage_reset', 'global': True, 'stage': 'completed', 'completedAt': '2026-10-20T06:33:00Z', 'id': 'g'},
        {'tool': 'codex', 'type': 'policy', 'global': False, 'stage': 'completed', 'completedAt': '2026-10-29T16:56:00Z', 'id': 'p'},
        {'tool': 'codex', 'type': 'reset_card', 'global': False, 'stage': 'completed', 'completedAt': '2026-10-08T03:44:00Z', 'id': 'c'},
        {'tool': 'codex', 'type': 'usage_reset', 'global': True, 'stage': 'scheduled', 'completedAt': None, 'id': 's'},
        {'tool': 'claude-code', 'type': 'usage_reset', 'global': True, 'stage': 'completed', 'completedAt': '2026-10-21T00:00:00Z', 'id': 'x'}]}
    result = provider_events(payload, 'codex')
    assert [(e['kind'], e['id']) for e in result] == [('global_reset', 'g'), ('policy', 'p')]
    assert isinstance(result[0]['at'], datetime)
    assert [e['id'] for e in provider_events(payload, 'claude')] == ['x']
    assert [e['id'] for e in provider_events(payload, 'codex', ignored=['p'])] == ['g']
