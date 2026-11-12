import pytest

from scripts.provider_usage_weights import (API_PRICES, apply_price_model, fit_price_model, model_line,
                                            trim_outliers, weight_report)

PRICES = API_PRICES['codex']
# Truth used to generate cycles: astra 1, gpt-6 sol line 2x list, gpt-5.6-sol 0.5x list, fast 2.5x.
TRUTH = {'gpt-6-astra': 1.0, 'gpt-6-sol': 2.0, 'gpt-5-sol': 0.5}  # Keyed by model line.
FAST, SCALE = 2.5, 0.08


def cycle(mix, plan=(), era='e', scale=SCALE):
    """mix: {(model, tier): (uncached M, cached M, output M)}; used% follows TRUTH."""
    models, used = {}, 0.0
    for (model, tier), (unc, cached, out) in mix.items():
        entry = models.setdefault(model, {'recorded_tokens': {}, 'service_tiers': {}})
        entry['service_tiers'][tier] = {'recorded_tokens': {
            'input_tokens': int((unc + cached) * 1e6), 'cached_input_tokens': int(cached * 1e6), 'output_tokens': int(out * 1e6)}}
        price = PRICES[model]
        used += scale * (unc * price[0] + cached * price[1] + out * price[2]) * TRUTH.get(model_line(model), 1.0) * (
            FAST if tier == 'priority' else 1.0)
    return {'status': 'measured', 'used_percentage_points': used, 'models': models,
            'plan_types': list(plan), 'policy_era': era, 'quota_group': 'codex'}


A, S6, S61, S5 = 'gpt-6-astra', 'gpt-6-sol', 'gpt-6.1-sol', 'gpt-5.6-sol'
MIXES = [
    {(A, 'default'): (40, 900, 4)}, {(A, 'priority'): (15, 300, 1.5)},
    {(A, 'default'): (20, 400, 2), (S61, 'default'): (20, 500, 2)},
    {(A, 'default'): (20, 400, 2), (S5, 'default'): (40, 800, 3)},
    {(S61, 'default'): (30, 600, 3), (S5, 'priority'): (10, 200, 1)},
    {(A, 'priority'): (5, 100, 0.5), (S6, 'default'): (10, 300, 1), (S5, 'default'): (20, 300, 2)},
    {(A, 'default'): (10, 200, 1), (S61, 'priority'): (8, 160, 1), (S5, 'default'): (20, 400, 2)},
    {(S5, 'default'): (50, 900, 4)},
    {(A, 'default'): (15, 300, 1), (S6, 'default'): (25, 500, 2)},
]


def test_model_line_drops_the_minor_version_of_sized_models():
    assert [model_line(m) for m in ('gpt-6.1-sol', 'gpt-6-sol', 'gpt-5.6-luna', 'gpt-5.5', 'gpt-6-astra')] == [
        'gpt-6-sol', 'gpt-6-sol', 'gpt-5-luna', 'gpt-5.5', 'gpt-6-astra']


def test_fit_recovers_line_factors_fast_multiplier_and_scale():
    """按标价折算后，估出模型线系数和快速档倍数；每个周期折算成满额的修正后标价。"""
    cycles = [cycle(m) for m in MIXES]
    fit = fit_price_model(cycles, 'codex')
    assert fit['reference_line'] == A
    assert {k: round(v, 2) for k, v in fit['line_factors'].items()} == {A: 1.0, 'gpt-5-sol': 0.5, 'gpt-6-sol': 2.0}
    assert round(fit['fast_multiplier'], 2) == FAST and fit['fast_basis'] == 'fitted'
    assert abs(fit['usd_at_100_percent'] - 100 / SCALE) < 1 and fit['median_relative_error'] < 0.01
    weighted = apply_price_model(cycles[1], 'codex', fit)
    assert abs(weighted['actual_over_predicted'] - 1) < 0.005  # Optimizer step tolerance.
    assert abs(weighted['adjusted_usd_at_100_percent'] - 100 / SCALE) < 1


def test_small_lines_stay_at_list_price_and_unpriced_models_are_reported():
    cycles = [cycle(m) for m in MIXES]
    cycles[0]['models']['gpt-6-luna'] = {'recorded_tokens': {}, 'service_tiers': {'default': {
        'recorded_tokens': {'input_tokens': 1_000_000, 'cached_input_tokens': 0, 'output_tokens': 0}}}}
    cycles[0]['models']['codex-auto-review'] = {'recorded_tokens': {}, 'service_tiers': {'default': {
        'recorded_tokens': {'input_tokens': 500, 'cached_input_tokens': 0, 'output_tokens': 7}}}}
    fit = fit_price_model(cycles, 'codex')
    assert 'gpt-6-luna' not in fit['estimated_lines'] and fit['line_factors'].get('gpt-6-luna', 1.0) == 1.0
    assert fit['unpriced_models'] == {'codex-auto-review': 507}


def test_too_few_cycles_give_no_fit():
    assert fit_price_model([cycle(MIXES[0])], 'codex') is None


def test_sparse_group_keeps_factors_and_fits_only_the_scale():
    """样本少的阶段/套餐沿用数据最多那组的系数，只估整体倍数。"""
    pro = [cycle(m, plan=['pro']) for m in MIXES]
    promax = [cycle(m, plan=['promax'], scale=SCALE / 2.5) for m in MIXES[:2]]
    fits = {f['plan']: f for f in weight_report([{'provider': 'codex', 'weekly_cycles': pro + promax}])}
    assert fits['pro']['factors_from'] == 'own_fit'
    assert fits['promax']['factors_from'] == 'richest_group_rescaled'
    assert abs(fits['promax']['usd_at_100_percent'] - 250 / SCALE) < 2
    assert promax[1]['weighted_capacity']['actual_over_predicted'] == 1.0


def test_external_usage_weeks_fall_out_as_outliers():
    """有外部用量的账号也参与拟合；外部用量大的周期作为离群值剔除，仍折算并标出。"""
    clean = [cycle(m, plan=['pro']) for m in MIXES]
    outside = [cycle(m, plan=['pro']) for m in MIXES[:3]]
    outside[0]['used_percentage_points'] *= 3.0  # Used twice as much again elsewhere that week.
    outside[1]['used_percentage_points'] *= 1.8  # Less than the outlier ratio: stays in.
    accounts = [{'provider': 'codex', 'account_id': 'a', 'weekly_cycles': clean},
                {'provider': 'codex', 'account_id': 'b', 'weekly_cycles': outside}]
    [fit] = weight_report(accounts, external_usage=['b'])
    assert fit['cycles'] == len(MIXES) + 2 and fit['external_usage_cycles'] == 2 and fit['outlier_cycles'] == 1
    assert fit['median_relative_error'] < 0.05
    assert all(c['external_usage_declared'] for c in outside)
    assert outside[0]['fit_outlier'] and outside[0]['weighted_capacity']['actual_over_predicted'] > 2.5
    assert not outside[1]['fit_outlier']


@pytest.mark.parametrize(('ratio', 'outlier'), [
    (0.699, True), (0.7, False), (0.701, False),
    (1.99, False), (2.0, False), (2.001, True),
])
def test_asymmetric_outlier_boundaries(ratio, outlier):
    clean = [cycle(MIXES[0]), cycle(MIXES[0])]
    fit = fit_price_model(clean, 'codex')
    candidate = cycle(MIXES[0])
    predicted = apply_price_model(candidate, 'codex', fit)['predicted_used_percentage_points']
    candidate['used_percentage_points'] = predicted * ratio
    _, dropped = trim_outliers(clean + [candidate], 'codex', lambda kept: fit)
    assert (candidate in dropped) == outlier


def test_low_consumption_outlier_is_removed_and_refitted():
    clean = [cycle(m, plan=['pro']) for _ in range(5) for m in MIXES]
    low = cycle(MIXES[0], plan=['pro'])
    low['used_percentage_points'] *= 0.6
    [fit] = weight_report([{'provider': 'codex', 'weekly_cycles': clean + [low]}])
    assert low['fit_outlier']
    assert fit['cycles'] == len(clean) and fit['outlier_cycles'] == 1
    assert fit['outlier_min_ratio'] == 0.7 and fit['outlier_max_ratio'] == 2.0
    assert fit['median_relative_error'] < 0.01


def test_clean_cut_off_cycles_enter_the_fit():
    """被统一刷新截断的周期，用掉足够多且最低值之后几乎没有用量时参与拟合。"""
    cycles = [cycle(m, plan=['pro']) for m in MIXES]
    partial = [cycle(m, plan=['pro']) for m in MIXES[:3]]  # Each used well over 30 points.
    for c, after in zip(partial, (0, 10, 10_000)):  # The last kept consuming after its lowest sample.
        c.update(status='not_exhausted', recorded_tokens={'total_tokens': 100_000}, tokens_after_final_observation=after)
    small = cycle(MIXES[3], plan=['pro'], scale=SCALE / 20)  # Used under 30 points.
    small.update(status='not_exhausted', recorded_tokens={'total_tokens': 100_000}, tokens_after_final_observation=0)
    [fit] = weight_report([{'provider': 'codex', 'account_id': 'a', 'weekly_cycles': cycles + partial + [small]}])
    assert [c.get('fit_basis') for c in partial + [small]] == ['partial', 'partial', None, None]
    assert fit['partial_cycles'] == 2 and fit['cycles'] == len(MIXES) + 2
    assert 'weighted_capacity' not in small


def test_unpriced_providers_are_skipped():
    assert weight_report([{'provider': 'claude', 'weekly_cycles': [cycle(m) for m in MIXES]}]) == []
