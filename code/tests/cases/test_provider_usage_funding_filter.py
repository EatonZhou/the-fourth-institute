import json
import pytest
from scripts.provider_usage_capacity import full_cycle_capacity
from scripts.provider_usage_funding_filter import funding_class, filter_cycles
from scripts.provider_usage_weights import fit_basis
from scripts.provider_weekly_usage import moment, report_account


START, END = '2026-11-01T00:00:00Z', '2026-11-09T00:00:00Z'


def events(kind='eligible_subscription'):
    return [{'id': str(i), 'model': 'gpt-5.5', 'at': at, 'capacity_funding_class': k,
             'usage': {'input_tokens': 10, 'cached_input_tokens': 3, 'output_tokens': 2, 'total_tokens': 12}}
            for i, (at, k) in enumerate([('2026-11-01T00:00:00Z', 'eligible_subscription'),
                                       ('2026-11-02T00:00:00Z', kind)])]


def cycle(rows):
    return full_cycle_capacity(rows, [{'at': '2026-11-03T00:00:00Z', 'resets_at': '2026-11-08T00:00:00Z',
        'remaining_percent': 0, 'window_duration_mins': 10080}], moment, moment(START), moment(END))


@pytest.mark.parametrize('kind', ['unknown:unknown', 'inferred:subscription', 'inferred:paid_extra', 'confirmed:credits_involved'])
def test_one_unverified_request_excludes_entire_cycle_not_just_its_tokens(kind):
    rows = events(kind)
    cycles = cycle(rows)
    filter_cycles(cycles, rows, moment)
    [c] = cycles
    assert c['status'] == 'funding_unverified'
    assert c['recorded_tokens']['total_tokens'] == 24
    assert c['tokens_at_100_percent_same_mix'] is None
    assert c['observational_tokens_at_100_percent_same_mix']['total_tokens'] == 24
    assert fit_basis(c) is None
    assert sum(v['requests'] for v in c['funding_filter']['requests_by_evidence'].values()) == 2
    filter_cycles(cycles, rows, moment)
    assert c['observational_tokens_at_100_percent_same_mix']['total_tokens'] == 24
    assert c['weighted_capacity'] is None


def test_confirmed_subscription_contract_can_pass_but_source_failure_blocks():
    rows = events()
    cycles = cycle(rows)
    filter_cycles(cycles, rows, moment)
    assert cycles[0]['ordinary_capacity_eligible'] is True
    assert fit_basis(cycles[0]) == 'exhausted'
    cycles = cycle(rows)
    filter_cycles(cycles, rows, moment, sources_failed=True)
    assert fit_basis(cycles[0]) is None
    filter_cycles(cycles, rows, moment)
    assert fit_basis(cycles[0]) == 'exhausted'
    assert cycles[0]['tokens_at_100_percent_same_mix']['total_tokens'] == 24


def test_eligibility_flag_alone_is_not_a_subscription_receipt():
    assert funding_class({'status': 'inferred', 'source': 'subscription', 'ordinary_capacity_eligible': True}) != 'eligible_subscription'


def test_real_report_wires_unknown_funding_into_capacity_filter(tmp_path):
    p = tmp_path / 'requests.jsonl'
    p.write_text('\n'.join(json.dumps({'request_id': e['id'], 'occurred_at': e['at'], 'model': e['model'],
                                     'usage': e['usage']}) for e in events())+'\n')
    account = {'provider': 'codex', 'account_id': 'a', 'sources': [{'format': 'requests', 'path': str(p), 'session_id': 's'}],
        'quota_observations': [{'at': '2026-11-03T00:00:00Z', 'resets_at': '2026-11-08T00:00:00Z',
                               'remaining_percent': 0, 'window_duration_mins': 10080}]}
    r = report_account(account, moment(START), moment(END), 'UTC')
    assert r['totals']['total_tokens'] == 24
    assert r['quota_attribution']['ordinary_capacity_filter_applied'] is True
    assert r['weekly_capacity_status'] == 'insufficient_funding_evidence'
    assert r['weekly_cycles'][0]['tokens_at_100_percent_same_mix'] is None
    from scripts.provider_usage_report import weekly_cycle_diagnostics
    d = weekly_cycle_diagnostics(r)
    assert d['observed_weekly_windows'] == 1 and d['measured_weekly_cycles'] == 0
    assert d['funding_unverified_cycles'] == 1
    assert 'ordinary_subscription_funding_not_verified' in d['evidence_gaps']
