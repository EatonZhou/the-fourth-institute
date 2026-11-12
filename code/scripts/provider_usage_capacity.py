"""Weekly capacity from whole quota cycles, without billing weights.

A cycle is one weekly window, identified by its scheduled reset endpoint. It
starts full (100% remaining) at ``resets_at - window duration`` and its
consumption is the largest decline seen in the window: 100 minus the lowest
remaining percentage. Partial segments between two samples are not used,
because a sample can lag the usage that produced it.
"""
from bisect import bisect_right
from datetime import timedelta

from scripts.provider_usage_cycles import _residual_exclusions, _weekly_quota_keys


FIELDS = ('input_tokens', 'cached_input_tokens', 'output_tokens',
          'reasoning_output_tokens', 'total_tokens')
# A window whose lowest remaining value is at most this is treated as used up: the last
# percent or so is rounding and display lag, often cut off by a global reset.
EXHAUSTED_REMAINING = 5
# A provider-wide reset is announced at one time and reaches accounts within about an hour.
GLOBAL_RESET_TOLERANCE = timedelta(hours=2)
# Native Codex samples report the same window's endpoint with minutes of drift.
ENDPOINT_TOLERANCE = timedelta(minutes=15)


def _usable_percent(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and 0 <= value <= 100


def service_tier(event):
    """Speed tier of a request; requests without a recorded tier ran at the default tier."""
    return event.get('service_tier') or 'default'


def _token_prefix(events, parse_time):
    """Per (model, service tier) sorted times with prefix sums of each token field and of field presence."""
    grouped = {}
    for event in events:
        grouped.setdefault((event['model'], service_tier(event)), []).append((parse_time(event['at']), event['usage']))
    indexes = {}
    for model, rows in grouped.items():
        rows.sort(key=lambda row: row[0])
        times, prefix, reported = [], [(0,) * len(FIELDS)], [(0,) * len(FIELDS)]
        for at, usage in rows:
            times.append(at)
            prefix.append(tuple(prefix[-1][i] + usage.get(key, 0) for i, key in enumerate(FIELDS)))
            reported.append(tuple(reported[-1][i] + int(key in usage) for i, key in enumerate(FIELDS)))
        indexes[model] = times, prefix, reported
    return indexes


def _window_tokens(indexes, models, excluded, start, end):
    """Tokens in (start, end] per model (split by service tier) and in total."""
    by_model, total = {}, dict.fromkeys(FIELDS, 0)
    for (model, tier), (times, prefix, reported) in sorted(indexes.items()):
        if (models and model not in models) or model in excluded:
            continue
        left, right = bisect_right(times, start), bisect_right(times, end)
        if left == right:
            continue
        usage = {key: prefix[right][i] - prefix[left][i] for i, key in enumerate(FIELDS)}
        fields = {key: reported[right][i] - reported[left][i] for i, key in enumerate(FIELDS)}
        entry = by_model.setdefault(model, {'requests': 0, 'recorded_tokens': dict.fromkeys(FIELDS, 0),
                                            'requests_reporting_field': dict.fromkeys(FIELDS, 0),
                                            'service_tiers': {}})
        entry['requests'] += right - left
        entry['service_tiers'][tier] = {'requests': right - left, 'recorded_tokens': usage}
        for key in FIELDS:
            entry['recorded_tokens'][key] += usage[key]
            entry['requests_reporting_field'][key] += fields[key]
            total[key] += usage[key]
    return by_model, total


def _cluster_endpoints(by_reset):
    """Merge endpoints that drift by minutes into one window, anchored on the earliest.

    The anchor is not moved by later members, so a slow drift cannot chain two
    genuinely different windows together.
    """
    merged, anchor = {}, None
    for reset in sorted(by_reset):
        if anchor is None or reset - anchor > ENDPOINT_TOLERANCE:
            anchor = reset
        merged.setdefault(anchor, []).extend(by_reset[reset])
    return merged


def _end_reason(next_start, finished, global_resets):
    if next_start is None:
        return 'scheduled_end' if finished else 'ongoing'
    if any(abs(next_start - at) <= GLOBAL_RESET_TOLERANCE for at in global_resets):
        return 'global_reset'
    return 'early_reset_unattributed'  # e.g. a reset card; the records do not say which.


def _policy_era(policy_changes, start):
    earlier = [p for p in policy_changes if p['at'] <= start]
    return earlier[-1]['id'] if earlier else None


def full_cycle_capacity(events, quotas, parse_time, since, until, provider_events=(), plan_periods=()):
    """One record per observed weekly window; capacity only for exhausted, covered windows.

    ``status`` is ``measured`` when the window was used up (0% remaining) and its
    capacity is usable. Other values explain why it is not: ``ongoing`` (not ended),
    ``start_before_query`` (records before the query start are not loaded),
    ``plan_changed`` (plan differs within the window), ``policy_changed`` (a provider
    limit change took effect within it), ``not_exhausted`` (it ended before reaching
    0%, see ``end_reason``) or ``no_tokens_recorded``.

    ``provider_events`` are dated provider-wide events from an explicit import:
    ``{'kind': 'global_reset' | 'policy', 'at': datetime, 'id': str}``. A global
    reset explains why a window ended early; a policy change (e.g. doubled limits)
    starts a new ``policy_era``, and capacities of different eras are not comparable.

    ``plan_periods`` (``{'plan', 'from', 'until'}`` with datetimes) are the account's
    declared plans; they apply only when the quota samples of a cycle carry no plan.
    """
    global_resets = sorted(e['at'] for e in provider_events if e['kind'] == 'global_reset')
    policy_changes = sorted((e for e in provider_events if e['kind'] == 'policy'), key=lambda e: e['at'])
    events = list(events)
    indexes = _token_prefix(events, parse_time)
    all_keys = _weekly_quota_keys(quotas)
    windows = {}
    for q in quotas:
        try:
            if q.get('stale') or q.get('window_duration_mins') != 10080:
                continue
            remaining = q.get('remaining_percent')
            if not _usable_percent(remaining):
                continue
            at, reset = parse_time(q['at']), parse_time(q['resets_at'])
            if reset <= at:
                continue
            scope = (q.get('quota_group', ''), tuple(sorted(q.get('models') or [])))
            windows.setdefault(scope, {}).setdefault(reset, []).append(
                (at, remaining, str(q.get('plan_type') or 'unknown'), q.get('evidence_source', 'unspecified')))
        except (ValueError, TypeError, KeyError, OverflowError):
            continue
    result = []
    for (group, models), raw_by_reset in sorted(windows.items()):
        excluded = _residual_exclusions(all_keys, group, models)
        if excluded is None:
            continue  # Several groups and no model mapping: allocation would be a guess.
        by_reset = _cluster_endpoints(raw_by_reset)
        # Before a window's first request Codex reports a floating endpoint (now + 7 days) at
        # 100% remaining. Such a window has not started and must not end the previous one.
        by_reset = {reset: s for reset, s in by_reset.items()
                    if any(remaining < 100 for _, remaining, _, _ in s)}
        resets = sorted(by_reset)
        for number, reset in enumerate(resets):
            start = reset - timedelta(minutes=10080)
            next_start = resets[number + 1] - timedelta(minutes=10080) if number + 1 < len(resets) else None
            # After the next window starts, lagging sessions still report this endpoint: ignore them.
            samples = sorted(s for s in by_reset[reset] if next_start is None or s[0] < next_start)
            if not samples:
                continue
            lowest = min(remaining for _, remaining, _, _ in samples)
            # First time the lowest value is shown: once the weekly limit is exhausted, later
            # requests run on credits and do not consume this window.
            final_at = min(at for at, remaining, _, _ in samples if remaining == lowest)
            used = round(100 - lowest, 6)
            plans = sorted({plan for _, _, plan, _ in samples} - {'unknown'})
            plan_basis = 'quota_samples' if plans else None
            if not plans and plan_periods:
                # Native Codex samples carry no plan; use declared periods overlapping the cycle.
                plans = sorted({p['plan'] for p in plan_periods if p['from'] < final_at and start < p['until']})
                plan_basis = 'declared_periods' if plans else None
            finished = reset <= until or number + 1 < len(resets)
            by_model, total = _window_tokens(indexes, models, excluded, start, final_at)
            window_end = min(value for value in (reset, until, next_start) if value is not None)
            _, after_final = _window_tokens(indexes, models, excluded, final_at, window_end)
            end_reason = _end_reason(next_start, finished, global_resets)
            policies = [p for p in policy_changes if start < p['at'] <= final_at]
            if not finished:
                status = 'ongoing'
            elif start < since:
                status = 'start_before_query'
            elif len(plans) > 1:
                status = 'plan_changed'
            elif policies:
                status = 'policy_changed'
            elif lowest > EXHAUSTED_REMAINING:
                status = 'not_exhausted'  # Ended early (e.g. a global reset): not a full consumption.
            elif not total['total_tokens']:
                status = 'no_tokens_recorded'
            else:
                status = 'measured'
            measured = status == 'measured'
            for value in by_model.values():
                value['tokens_at_100_percent_same_mix'] = (
                    {key: round(v * 100 / used) for key, v in value['recorded_tokens'].items()} if measured else None)
            result.append({
                'quota_group': group, 'models_scope': list(models), 'excluded_models': sorted(excluded),
                'resets_at': reset.isoformat(), 'cycle_start': start.isoformat(),
                'cycle_start_basis': 'resets_at_minus_window_duration',
                'final_observed_at': final_at.isoformat(), 'lowest_remaining_percent': lowest,
                'exhausted': lowest <= EXHAUSTED_REMAINING, 'end_reason': end_reason,
                'policy_era': _policy_era(policy_changes, start),
                'policy_changes_in_cycle': [p['id'] for p in policies],
                'used_percentage_points': used, 'samples': len(samples),
                'evidence_sources': sorted({source for _, _, _, source in samples}),
                'plan_types': plans, 'plan_basis': plan_basis, 'status': status,
                'recorded_tokens': total, 'models': by_model,
                'tokens_after_final_observation': after_final['total_tokens'],
                'tokens_at_100_percent_same_mix': (
                    {key: round(v * 100 / used) for key, v in total.items()} if measured else None),
                'limitations': ['conditional_on_observed_model_and_token_mix', 'provider_billing_weights_not_identified',
                                'unseen_account_usage_not_bounded', 'quota_percentage_rounding',
                                'reasoning_is_a_subset_of_output_do_not_add',
                                'cache_reads_are_a_subset_of_input_do_not_add',
                                'unreported_fields_are_not_proven_zero',
                                'cache_writes_not_separately_preserved_in_current_index']})
    return result
