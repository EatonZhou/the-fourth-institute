"""How the weekly limit prices requests: API list price with fitted corrections.

Every measured cycle (see ``provider_usage_capacity``) says: these tokens used
``used_percentage_points`` of the weekly limit. Each request is first priced at the
API list price of its model (Standard tier, short context): uncached input, cached
input and output each at their own rate. Measured Codex cycles follow that price
closely, with two corrections fitted from the cycles of one provider, policy era
and plan:

- a factor per model line (model size within a generation: ``gpt-6-astra``,
  ``gpt-6-sol`` covering gpt-6-sol and gpt-6.1-sol, ``gpt-5.6-sol`` ...), relative to
  the line with the largest list cost. Only lines with enough data get a factor;
  the others stay at list price (factor 1).
- the fast-mode multiplier (``service_tier: priority``), in place of the list
  multiplier, when fast mode carries enough of the cost.

    used% ~= scale * sum(list_price * line_factor * fast_factor * tokens)

Cycles cut off before exhaustion (by a global reset, say) also count when they used
at least ``PARTIAL_MIN_USED`` points and almost no tokens followed their lowest
sample. Cycles below ``OUTLIER_MIN_RATIO`` or above ``OUTLIER_MAX_RATIO`` are
left out and the fit repeated.

Errors are relative to the cycle's used percentage. ``adjusted_usd_at_100_percent``
restates a cycle as the corrected list-price value that the whole weekly limit
buys, which makes accounts with different model and tier mixes comparable.
Groups with too few cycles keep the richest group's factors and fit only the scale.
"""
import math
import re
import statistics

PRICE_SOURCE = 'https://developers.openai.com/api/docs/pricing, Standard short context, read 2026-11-12'
# USD per 1M tokens: (uncached input, cached input, output); fast = list multiplier.
API_PRICES = {
    'codex': {
        'gpt-6-astra': (10.00, 1.00, 50.00), 'gpt-6.1-sol': (2.00, 0.10, 10.00), 'gpt-6-sol': (2.00, 0.20, 10.00),
        'gpt-6.1-terra': (1.00, 0.05, 5.00), 'gpt-6-luna': (0.10, 0.01, 0.50),
        'gpt-5.6-sol': (4.00, 0.40, 20.00), 'gpt-5.6-terra': (2.00, 0.20, 12.00), 'gpt-5.6-luna': (0.20, 0.02, 1.20),
        'gpt-5.5': (5.00, 0.50, 30.00), 'gpt-5.4': (2.50, 0.25, 15.00), 'gpt-5.4-mini': (0.75, 0.075, 4.50),
        'gpt-5.3-codex': (1.75, 0.175, 14.00),
    },
}
LIST_FAST_MULTIPLIER = {'gpt-5.5': 2.5}  # Others: 2x.
MIN_LINE_SHARE = 0.03   # Share of list cost a line needs for its own factor...
MIN_LINE_CYCLES = 5     # ...and cycles in which it is at least 5% of the cost.
MIN_FAST_SHARE = 0.05
PARTIAL_MIN_USED = 30                   # A cut-off cycle enters the fit when it used this much...
PARTIAL_MAX_TOKENS_AFTER_LOWEST = 0.05  # ...and almost nothing was recorded after its lowest sample.
OUTLIER_MIN_RATIO = 0.7  # Actual consumption more than 30% below predicted.
OUTLIER_MAX_RATIO = 2.0  # Actual consumption more than twice predicted.
KINDS = ('uncached', 'cached', 'output')


def fit_basis(cycle):
    """'exhausted', 'partial' (cut off with a clean lowest sample) or None (not usable for pricing)."""
    used = cycle.get('used_percentage_points')
    if not used:
        return None
    if cycle.get('status') == 'measured':
        return 'exhausted'
    total = (cycle.get('recorded_tokens') or {}).get('total_tokens') or 0
    if cycle.get('status') == 'not_exhausted' and used >= PARTIAL_MIN_USED and total and \
            (cycle.get('tokens_after_final_observation') or 0) <= PARTIAL_MAX_TOKENS_AFTER_LOWEST * total:
        return 'partial'
    return None


def model_line(model):
    """Model size within a generation: drop the minor version of sized models (gpt-6.1-sol -> gpt-6-sol)."""
    return re.sub(r'^(gpt-\d+)\.\d+-(?=[a-z])', r'\1-', str(model).lower())


def _tokens(recorded):
    uncached = max(0, recorded.get('input_tokens', 0) - recorded.get('cached_input_tokens', 0))
    return uncached / 1e6, recorded.get('cached_input_tokens', 0) / 1e6, recorded.get('output_tokens', 0) / 1e6


def cycle_costs(cycle, provider):
    """List cost (USD, Standard rate) per (line, tier) and unpriced token counts per model."""
    prices = API_PRICES.get(provider, {})
    costs, unpriced = {}, {}
    for model, entry in (cycle.get('models') or {}).items():
        tiers = entry.get('service_tiers') or {'default': {'recorded_tokens': entry['recorded_tokens']}}
        for tier, value in tiers.items():
            amounts = _tokens(value['recorded_tokens'])
            price = prices.get(model)
            if price is None:
                unpriced[model] = unpriced.get(model, 0) + round(sum(amounts) * 1e6)
                continue
            fast = tier == 'priority'
            key = (model_line(model), fast)
            costs[key] = costs.get(key, 0) + sum(a * p for a, p in zip(amounts, price))
            if fast:  # Remember the list multiplier for models whose fast factor is not fitted.
                costs[('list_fast', model_line(model))] = costs.get(('list_fast', model_line(model)), 0) + \
                    sum(a * p for a, p in zip(amounts, price)) * LIST_FAST_MULTIPLIER.get(model, 2.0)
    return costs, unpriced


def _adjusted(costs, factors, fast):
    total = 0.0
    for key, value in costs.items():
        if key[0] == 'list_fast':
            if fast is None:
                total += value * factors.get(key[1], 1.0)
            continue
        line, is_fast = key
        if is_fast and fast is None:
            continue  # Counted above at the list multiplier.
        total += value * factors.get(line, 1.0) * (fast if is_fast else 1.0)
    return total


def _scale(samples, factors, fast):
    ratios = [_adjusted(costs, factors, fast) / used for costs, used in samples]
    denominator = sum(r * r for r in ratios)
    if not denominator:
        return None, float('inf')
    scale = sum(ratios) / denominator
    return scale, sum((scale * r - 1) ** 2 for r in ratios)


def _optimize(samples, free, fast_free, factors=None, fast=None):
    factors = dict(factors or {})
    fast = fast if fast is not None else (2.0 if fast_free else None)
    _, best = _scale(samples, factors, fast)
    step = 0.5
    while step > 0.003:
        improved = False
        for name in list(free) + (['fast'] if fast_free else []):
            for delta in (step, -step):
                trial_factors, trial_fast = dict(factors), fast
                if name == 'fast':
                    trial_fast = fast * math.exp(delta)
                else:
                    trial_factors[name] = factors.get(name, 1.0) * math.exp(delta)
                _, loss = _scale(samples, trial_factors, trial_fast)
                if loss < best - 1e-12:
                    best, factors, fast, improved = loss, trial_factors, trial_fast, True
        if not improved:
            step /= 2
    return factors, fast


def _samples(cycles, provider):
    samples, unpriced = [], {}
    for cycle in cycles:
        if not fit_basis(cycle):
            continue
        costs, missing = cycle_costs(cycle, provider)
        for model, tokens in missing.items():
            unpriced[model] = unpriced.get(model, 0) + tokens
        if any(k[0] != 'list_fast' for k in costs):
            samples.append((costs, cycle['used_percentage_points']))
    return samples, unpriced


def fit_price_model(cycles, provider):
    """Corrected list-price model for usable cycles (see ``fit_basis``) of one provider, era and plan."""
    samples, unpriced = _samples(cycles, provider)
    if len(samples) < 2:
        return None
    line_cost, line_cycles, fast_cost = {}, {}, 0.0
    for costs, _ in samples:
        cycle_total = sum(v for k, v in costs.items() if k[0] != 'list_fast')
        per_line = {}
        for (line, is_fast), value in ((k, v) for k, v in costs.items() if k[0] != 'list_fast'):
            per_line[line] = per_line.get(line, 0) + value
            fast_cost += value if is_fast else 0.0
        for line, value in per_line.items():
            line_cost[line] = line_cost.get(line, 0) + value
            if value >= 0.05 * cycle_total:
                line_cycles[line] = line_cycles.get(line, 0) + 1
    total = sum(line_cost.values())
    reference = max(line_cost, key=line_cost.get)
    estimated = sorted(line for line in line_cost if line != reference and line_cost[line] / total >= MIN_LINE_SHARE
                       and line_cycles.get(line, 0) >= MIN_LINE_CYCLES)
    fast_free = fast_cost / total >= MIN_FAST_SHARE
    if len(samples) < len(estimated) + int(fast_free) + 3:
        estimated, fast_free = [], False
    factors, fast = _optimize(samples, estimated, fast_free)
    scale, _ = _scale(samples, factors, fast)
    return _summary(samples, factors, fast, scale, reference, estimated, unpriced)


def _summary(samples, factors, fast, scale, reference, estimated, unpriced, basis='own_fit'):
    errors = [abs(used / (scale * _adjusted(costs, factors, fast)) - 1) for costs, used in samples]
    return {'basis': 'api_list_price', 'price_source': PRICE_SOURCE, 'factors_from': basis,
            'reference_line': reference, 'estimated_lines': list(estimated),
            'line_factors': {reference: 1.0, **{k: round(v, 3) for k, v in sorted(factors.items())}},
            'fast_multiplier': round(fast, 3) if fast is not None else None,
            'fast_basis': 'fitted' if fast is not None else 'list_price',
            'scale_percent_per_usd': round(scale, 6), 'usd_at_100_percent': round(100 / scale, 2),
            'unpriced_models': dict(sorted(unpriced.items())), 'cycles': len(samples),
            'median_relative_error': round(statistics.median(errors), 3)}


def rescale_fit(cycles, provider, base):
    """Keep ``base``'s line and fast factors; fit only the scale (another plan or era)."""
    factors = {k: v for k, v in base['line_factors'].items() if k != base['reference_line']}
    fast = base['fast_multiplier']
    samples, unpriced = _samples(cycles, provider)
    if not samples:
        return None
    scale, _ = _scale(samples, factors, fast)
    if not scale:
        return None
    return _summary(samples, factors, fast, scale, base['reference_line'], base['estimated_lines'],
                    unpriced, 'richest_group_rescaled')


def apply_price_model(cycle, provider, fit):
    factors = {k: v for k, v in fit['line_factors'].items() if k != fit['reference_line']}
    costs, unpriced = cycle_costs(cycle, provider)
    adjusted = _adjusted(costs, factors, fit['fast_multiplier'])
    used = cycle.get('used_percentage_points')
    if not adjusted or not used:
        return None
    predicted = fit['scale_percent_per_usd'] * adjusted
    return {'adjusted_list_cost_usd': round(adjusted, 2), 'predicted_used_percentage_points': round(predicted, 2),
            'actual_over_predicted': round(used / predicted, 3),
            'adjusted_usd_at_100_percent': round(adjusted * 100 / used, 2),
            'unpriced_tokens': sum(unpriced.values())}


def weight_report(accounts, external_usage=()):
    """Fit per (provider, quota group, policy era, plan); annotate usable cycles in place.

    Usable cycles are exhausted ones and cut-off ones with a clean lowest sample
    (``fit_basis``). Cycles without a known plan join the era's most common known
    plan: accounts are mostly on one plan, and native Codex samples carry no plan.
    Cycles whose actual/predicted ratio is outside the inclusive bounds are
    dropped and the fit repeated until the kept set settles; they are still priced
    and marked ``fit_outlier``. Accounts in ``external_usage`` (also used where no
    records reach this report) take part like any other: weeks with much unrecorded
    use fall out as outliers. Their cycles are marked ``external_usage_declared``.
    """
    external_usage = set(external_usage)
    groups = {}
    for account in accounts:
        if account['provider'] not in API_PRICES:
            continue
        for cycle in account.get('weekly_cycles') or []:
            basis = fit_basis(cycle)
            if not basis:
                continue
            cycle['fit_basis'] = basis
            if account.get('account_id') in external_usage:
                cycle['external_usage_declared'] = True
            groups.setdefault((account['provider'], cycle.get('quota_group'), cycle.get('policy_era')), []).append(cycle)
    selections = []
    for (provider, group, era), cycles in sorted(groups.items(), key=lambda item: tuple(map(str, item[0]))):
        plans = {}
        for cycle in cycles:
            plan = cycle['plan_types'][0] if len(cycle.get('plan_types') or []) == 1 else None
            plans.setdefault(plan, []).append(cycle)
        known = sorted((p for p in plans if p), key=lambda p: (-len(plans[p]), p))
        for plan in sorted(set(known or [None]), key=str):
            members = plans.get(plan, []) + (plans.get(None, []) if plan and known and plan == known[0] else [])
            fit, outliers = trim_outliers(members, provider, lambda kept: fit_price_model(kept, provider))
            selections.append(((provider, group), era, plan, members, fit, outliers))
    bases = {}
    for scope, _, _, _, fit, _ in selections:
        if fit and (scope not in bases or fit['cycles'] > bases[scope]['cycles']):
            bases[scope] = fit
    fits = []
    for scope, era, plan, members, fit, outliers in selections:
        base = bases.get(scope)
        if base and base is not fit and (not fit or len(fit['estimated_lines']) < len(base['estimated_lines'])
                                         or (fit['fast_basis'] != 'fitted' and base['fast_basis'] == 'fitted')):
            rescaled, rescaled_outliers = trim_outliers(members, scope[0], lambda kept: rescale_fit(kept, scope[0], base))
            if rescaled:
                fit, outliers = rescaled, rescaled_outliers
        if not fit:
            continue
        dropped = {id(c) for c in outliers}
        kept = [c for c in members if id(c) not in dropped]
        fits.append({'provider': scope[0], 'quota_group': scope[1], 'policy_era': era, 'plan': plan or 'unknown',
                     **fit, 'partial_cycles': sum(c['fit_basis'] == 'partial' for c in kept),
                     'external_usage_cycles': sum(bool(c.get('external_usage_declared')) for c in kept),
                     'outlier_cycles': len(outliers),
                     'outlier_min_ratio': OUTLIER_MIN_RATIO, 'outlier_max_ratio': OUTLIER_MAX_RATIO})
        for cycle in members:
            cycle['weighted_capacity'] = apply_price_model(cycle, scope[0], fit)
            cycle['fit_outlier'] = id(cycle) in dropped
    return fits


def trim_outliers(cycles, provider, fit_cycles):
    """Fit, drop cycles outside the ratio bounds, refit from all; repeat until stable.

    Every round judges all cycles against the latest fit, so a cycle dropped early
    can come back. Returns the fit and the dropped cycles.
    """
    kept, fit = list(cycles), fit_cycles(cycles)
    for _ in range(20):
        if not fit:
            break
        inside = []
        for cycle in cycles:
            weighted = apply_price_model(cycle, provider, fit)
            if not weighted or OUTLIER_MIN_RATIO <= weighted['actual_over_predicted'] <= OUTLIER_MAX_RATIO:
                inside.append(cycle)
        if {id(c) for c in inside} == {id(c) for c in kept}:
            break
        refit = fit_cycles(inside)
        if not refit:
            break
        kept, fit = inside, refit
    if not fit:
        return None, []
    kept_ids = {id(c) for c in kept}
    return fit, [c for c in cycles if id(c) not in kept_ids]
