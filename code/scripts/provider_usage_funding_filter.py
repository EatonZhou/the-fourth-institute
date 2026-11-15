"""Whole-cycle funding eligibility, without allocating mixed requests' tokens."""
from scripts.provider_usage_capacity import _token_prefix, _window_tokens


def funding_class(attribution):
    if (attribution.get('status') == 'confirmed' and attribution.get('source') == 'subscription'
            and attribution.get('ordinary_capacity_eligible') is True):
        return 'eligible_subscription'
    return str(attribution.get('status', 'unknown')) + ':' + str(attribution.get('source', 'unknown'))


def filter_cycles(cycles, events, parse_time, *, sources_failed=False):
    # Reuse time/model scope and prefix-sum semantics of the token accumulator.
    indexes = _token_prefix(({**e, 'service_tier': e.get('capacity_funding_class', 'unknown:unknown')}
                             for e in events), parse_time)
    for cycle in cycles:
        models, _ = _window_tokens(indexes, cycle['models_scope'], set(cycle['excluded_models']),
                                  parse_time(cycle['cycle_start']), parse_time(cycle['final_observed_at']),
                                  include_start=True)
        groups = {}
        for model in models.values():
            for kind, part in model['service_tiers'].items():
                group = groups.setdefault(kind, {'requests': 0, 'recorded_tokens': {}})
                group['requests'] += part['requests']
                for key, value in part['recorded_tokens'].items():
                    group['recorded_tokens'][key] = group['recorded_tokens'].get(key, 0) + value
        eligible = bool(groups) and set(groups) == {'eligible_subscription'} and not sources_failed
        cycle['funding_filter'] = {'applied': True, 'requests_by_evidence': groups,
                                  'sources_failed': sources_failed,
                                  'token_semantics': 'whole_request_groups_not_funding_token_allocation'}
        cycle['ordinary_capacity_eligible'] = eligible
        cycle['capacity_basis'] = 'ordinary_subscription' if eligible else 'observational_only'
        if eligible and 'observational_status' in cycle:
            cycle['status'] = cycle.pop('observational_status')
            cycle['tokens_at_100_percent_same_mix'] = cycle.pop('observational_tokens_at_100_percent_same_mix')
            cycle.pop('ordinary_capacity_exclusion_reason', None)
            for model in cycle['models'].values():
                model['tokens_at_100_percent_same_mix'] = model.pop('observational_tokens_at_100_percent_same_mix')
        if not eligible:
            cycle['ordinary_capacity_exclusion_reason'] = ('source_read_failure' if sources_failed
                                                          else 'funding_not_exclusively_confirmed_subscription')
            if 'observational_status' not in cycle:
                cycle['observational_status'] = cycle['status']
                cycle['observational_tokens_at_100_percent_same_mix'] = cycle['tokens_at_100_percent_same_mix']
            cycle['tokens_at_100_percent_same_mix'] = None
            cycle['weighted_capacity'] = None
            cycle.pop('fit_basis', None)
            cycle.pop('fit_outlier', None)
            for model in cycle['models'].values():
                if 'observational_tokens_at_100_percent_same_mix' not in model:
                    model['observational_tokens_at_100_percent_same_mix'] = model['tokens_at_100_percent_same_mix']
                model['tokens_at_100_percent_same_mix'] = None
            if cycle['status'] == 'measured':
                cycle['status'] = 'funding_unverified'
