"""Conservative token bounds for reset boundaries observed between two samples."""
from bisect import bisect_left, bisect_right


SOURCE_PRIORITY = {'provider_reset_event': 5, 'native_quota': 4, 'monitor_quota': 3,
                   'daily_snapshot': 2, 'execution_usage': 1, 'unspecified': 0}


def _residual_exclusions(keys, group, models):
    """Models a group without a model list must not count, or None when unmapped.

    With one quota group it covers every model. With several, a group without
    a model list is the account's main limit only when every other group names
    its models (e.g. a separate Spark limit); it then covers the remaining models.
    Otherwise the allocation is ambiguous and the group is skipped.
    """
    if models or len({key[0] for key in keys}) <= 1:
        return set()
    others = [scope for name, scope in keys if name != group]
    if others and all(others):
        return {model for scope in others for model in scope}
    return None


def _weekly_quota_keys(quotas):
    """Every weekly (group, models) scope seen for the account, usable or not."""
    return {(q.get('quota_group', ''), tuple(sorted(q.get('models') or [])))
            for q in quotas if isinstance(q, dict) and q.get('window_duration_mins') == 10080}


def _verified_account_event(q):
    return (q.get('evidence_source') == 'provider_reset_event' and q.get('reset_event_verified') is True
            and bool(q.get('event_id')) and bool(q.get('account_id')) and q.get('account_specific') is not False)


def reset_evidence(events, quotas, parse_time, prepared=None):
    """Rank evidence; preserve intervals rather than averaging inferred times.

    Priorities are ordinal policy, not calibrated probabilities. A copied native
    observation is one observation regardless of how many stores contain it.
    """
    prepared = prepared if prepared is not None else _token_index(events, parse_time)
    samples, conflicts = {}, []
    for q in quotas:
        try:
            if q.get('stale') or q.get('window_duration_mins') != 10080:
                continue
            at, reset = parse_time(q['at']), parse_time(q['resets_at'])
            if reset <= at and not _verified_account_event(q):
                continue
            remaining = q.get('remaining_percent')
            if remaining is not None and (isinstance(remaining, bool) or not isinstance(remaining, (int, float)) or not 0 <= remaining <= 100):
                continue
            key = (q.get('quota_group', ''), tuple(sorted(q.get('models') or [])), at)
            samples.setdefault(key, []).append((reset, remaining, q))
        except (KeyError, ValueError, TypeError, OverflowError):
            continue
    groups, blocked = {}, {}
    for (group, models, at), candidates in samples.items():
        best = max(SOURCE_PRIORITY.get(q.get('evidence_source', 'unspecified'), 0) for _, _, q in candidates)
        top = [(reset, remaining, q) for reset, remaining, q in candidates
               if SOURCE_PRIORITY.get(q.get('evidence_source', 'unspecified'), 0) == best]
        resets = {reset for reset, _, _ in top}
        known = {remaining for _, remaining, _ in top if remaining is not None}
        conflict = (len({reset for reset, _, _ in candidates}) > 1 or
                    len({remaining for _, remaining, _ in candidates if remaining is not None}) > 1)
        if len(resets) > 1 or len(known) > 1:
            conflicts.append({'quota_group': group, 'models': list(models), 'observed_at': at.isoformat(),
                              'kind': 'conflicting_equal_priority_observations', 'usable_boundary': False})
            blocked.setdefault((group, models), []).append(at)
            continue
        reset, remaining, q = top[0]
        compatible = [(v, item) for endpoint, v, item in candidates if endpoint == reset and v is not None]
        if remaining is None and compatible:
            rank = max(SOURCE_PRIORITY.get(item.get('evidence_source', 'unspecified'), 0) for _, item in compatible)
            compatible = [(v, item) for v, item in compatible
                          if SOURCE_PRIORITY.get(item.get('evidence_source', 'unspecified'), 0) == rank]
            if len({v for v, _ in compatible}) == 1:
                remaining, percent_source = compatible[0]
                q = {**q, 'remaining_percent_source': percent_source.get('evidence_source', 'unspecified')}
        groups.setdefault((group, models), []).append((at, reset, remaining, q, conflict))
    result = list(conflicts)
    for (group, models), observations in groups.items():
        excluded = _residual_exclusions(_weekly_quota_keys(quotas), group, models)
        unmapped = excluded is None
        times = [] if unmapped else sorted({at for model, (ts, prefix) in prepared.items()
                    if (not models or model in models) and model not in excluded
                    for i, at in enumerate(ts) if prefix[i+1] > prefix[i]})
        observations.sort(key=lambda x: x[0])
        next_nonzero, upcoming = {}, None
        for o in reversed(observations):
            next_nonzero[o[0]] = upcoming
            if o[2] is not None and o[2] != 100:
                upcoming = o[0]
        previous = None
        barriers = sorted(blocked.get((group, models), []))
        for at, reset, remaining, q, conflict in observations:
            if previous and bisect_right(barriers, at) > bisect_right(barriers, previous[0]):
                previous = None
            lower, upper, kind, priority = None, at, '', 0
            if remaining == 100 and (previous is None or previous[2] != 100):
                lower = previous[0] if previous and previous[2] is not None and previous[2] < 100 else None
                kind, priority = 'reported_zero_usage', 3
            elif previous:
                before = previous[2]
                jump = before is not None and remaining is not None and remaining - before >= 20
                changed = abs((reset - previous[1]).total_seconds()) > 60
                if changed or jump:
                    lower = previous[0]
                    kind, priority = ('remaining_quota_jump', 2) if jump else ('reset_endpoint_change', 1)
            first_use = None
            if kind == 'reported_zero_usage':
                i = bisect_left(times, at)
                # Stop at the next nonzero quota observation's time, if supplied.
                next_change = next_nonzero[at]
                barrier_index = bisect_right(barriers, at)
                next_barrier = barriers[barrier_index] if barrier_index < len(barriers) else None
                if (i < len(times) and times[i] < reset and (next_change is None or times[i] <= next_change)
                        and (next_barrier is None or times[i] < next_barrier)):
                    first_use = times[i]
                    kind, priority = 'first_usage_after_reported_zero', 4
            explicit = q.get('reset_occurred_at')
            verified = _verified_account_event(q)
            if explicit and q.get('evidence_source') == 'provider_reset_event' and verified:
                try:
                    occurred = parse_time(explicit)
                    if occurred <= at:
                        lower = upper = occurred
                        kind, priority = 'explicit_provider_reset', 5
                except (TypeError, ValueError, OverflowError):
                    pass
            if kind:
                result.append({'quota_group': group, 'models': list(models), 'kind': kind,
                    'priority': priority, 'source_priority': SOURCE_PRIORITY.get(q.get('evidence_source', 'unspecified'), 0),
                    'source': q.get('evidence_source', 'unspecified'), 'source_conflict': conflict,
                    'remaining_percent_source': q.get('remaining_percent_source', q.get('evidence_source', 'unspecified')),
                    'timing_confidence': 'verified_account_event' if priority == 5 else 'observation_candidate',
                    'reset_cause': q.get('reset_reason') if priority == 5 and q.get('reset_reason') in
                        {'manual_card', 'scheduled', 'provider_global', 'plan_upgrade'} else 'unknown',
                    'actual_reset_at': lower.isoformat() if priority == 5 else None,
                    'timing_error_bound_seconds': None,
                    'reset_between': [lower.isoformat() if lower else None, upper.isoformat()],
                    'first_usage_at': first_use.isoformat() if first_use else None,
                    'usable_boundary': lower is not None and kind != 'reset_endpoint_change' and not conflict,
                    'limitations': [] if priority == 5 else ['inferred_not_exact', 'reported_zero_may_be_rounded',
                        'first_recorded_usage_may_not_be_first_account_usage'] +
                        (['schedule_movement_without_reset_corroboration'] if kind == 'reset_endpoint_change' else []) +
                        (['unbounded_reporting_lag_and_unknown_reset_cause'] if priority != 5 else []) +
                        (['model_to_quota_group_unmapped'] if unmapped else [])})
            previous = (at, reset, remaining)
    return sorted(result, key=lambda e: (e.get('reset_between', [None, e.get('observed_at', '')])[1],
                                         -e.get('priority', 0), -e.get('source_priority', 0)))


def normalize_quota_endpoints(quotas, parse_time, tolerance_seconds=60):
    """Cluster at most 60 seconds of endpoint jitter, without transitive drift."""
    scopes = {}
    result = [dict(q) for q in quotas]
    for q in result:
        try:
            reset = parse_time(q['resets_at'])
            key = (q.get('quota_group', ''), tuple(sorted(q.get('models') or [])), q.get('window_duration_mins'))
            scopes.setdefault(key, []).append((reset, q))
        except (KeyError, ValueError, TypeError, OverflowError):
            continue
    for observations in scopes.values():
        anchor = None
        for reset, q in sorted(observations, key=lambda pair: pair[0]):
            if anchor is None or (reset - anchor).total_seconds() > tolerance_seconds:
                anchor = reset
            q['resets_at'] = anchor.isoformat()
    return result


def _token_index(events, parse_time):
    models = {}
    for e in events:
        models.setdefault(e['model'], []).append((parse_time(e['at']), e['usage'].get('total_tokens', 0)))
    result = {}
    for model, values in models.items():
        values.sort()
        prefix = [0]
        for _, tokens in values:
            prefix.append(prefix[-1] + tokens)
        result[model] = ([at for at, _ in values], prefix)
    return result


def cycle_bounds(events, quotas, parse_time, tolerance=0.10):
    prepared = _token_index(events, parse_time)
    groups, barriers = {}, {}
    all_keys = _weekly_quota_keys(quotas)
    for evidence in reset_evidence(events, quotas, parse_time, prepared):
        key = (evidence['quota_group'], tuple(evidence['models']))
        if evidence.get('source_conflict') or evidence['kind'] == 'conflicting_equal_priority_observations':
            at = evidence.get('observed_at') or evidence['reset_between'][1]
            barriers.setdefault(key, []).append(parse_time(at))
        if not evidence['usable_boundary']:
            continue
        left, right = evidence['reset_between']
        groups.setdefault(key, []).append((parse_time(left), parse_time(right), evidence['models'], evidence['kind'], evidence))
    result = []
    for (group, scope), boundaries in groups.items():
        blocked = sorted(barriers.get((group, scope), []))
        boundaries.sort(key=lambda v: (v[1], -v[4]['priority']))
        for start, end in zip(boundaries, boundaries[1:]):
            if start[1] > end[0]:
                continue
            if bisect_right(blocked, end[1]) > bisect_left(blocked, start[0]):
                continue
            allowed = end[2]
            # Do not allocate one model to several groups without an explicit map.
            excluded = _residual_exclusions(all_keys, group, scope)
            if excluded is None:
                continue
            models = {}
            for model, (times, prefix) in prepared.items():
                if (allowed and model not in allowed) or model in excluded:
                    continue
                upper = prefix[bisect_left(times, end[1])] - prefix[bisect_left(times, start[0])]
                lower = prefix[bisect_left(times, end[0])] - prefix[bisect_left(times, start[1])]
                if upper:
                    models[model] = {'lower_tokens': lower, 'upper_tokens': upper}
            lower = sum(v['lower_tokens'] for v in models.values())
            upper = sum(v['upper_tokens'] for v in models.values())
            result.append({'quota_group': group,
                           'start_between': [v.isoformat() for v in start[:2]],
                           'end_between': [v.isoformat() for v in end[:2]],
                           'lower_tokens': lower, 'upper_tokens': upper,
                           'within_10_percent': upper - lower <= tolerance * lower,
                           'models': models, 'boundary_evidence': [start[3], end[3]],
                           'boundary_confidence': [start[4], end[4]],
                           'uncertainty_scope': 'inferred_boundary_timing_only',
                           'conditional_on_candidate_boundaries': any(v[4]['priority'] != 5 for v in (start, end)),
                           'whole_account_error_bound': None,
                           'basis': 'observed_boundary_brackets_within_supplied_records'})
    return result
