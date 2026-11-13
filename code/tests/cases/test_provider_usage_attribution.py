import json
import sqlite3

import pytest

from scripts.provider_usage_attribution import (Attributor, codex_evidence, claude_evidence,
                                              request_metadata, antigravity_evidence)
from scripts.provider_weekly_usage import report_account, moment
from scripts.provider_usage_attribution_report import run


AT = '2026-11-12T12:00:00Z'
ACCOUNT = {'provider': 'codex', 'account_id': 'codex_test'}
CLAUDE = {'provider': 'claude', 'account_id': 'claude_one'}
ANTIGRAVITY = {'provider': 'antigravity', 'account_id': 'antigravity_one'}


def event(evidence=(), at=AT, model='gpt-6-sol'):
    return {'id': 'req', 'session': 's', 'at': at, 'model': model,
            'usage': {'total_tokens': 35}, 'quota_evidence': list(evidence)}


def snapshot(used=20, **extra):
    return {'limit_id': 'codex', 'primary': {'used_percent': used, 'resets_at': 2000000000}, **extra}


def test_credits_available_and_unlimited_are_not_a_debit_receipt():
    e = codex_evidence(snapshot(credits={'has_credits': True, 'unlimited': True, 'balance': '200'}), AT)
    result = Attributor(ACCOUNT).attribute(event(e))
    assert result['status'] == 'inferred' and result['source'] == 'subscription'
    assert result['ordinary_capacity_eligible'] is False
    assert Attributor(ACCOUNT).attribute(event(codex_evidence({'credits': {'has_credits': True}}, AT)))['status'] == 'unknown'


@pytest.mark.parametrize('sample_at', ['2026-11-12T11:54:59Z', '2026-11-12T12:00:01Z'])
def test_expired_or_future_states_do_not_label_request(sample_at):
    assert Attributor(ACCOUNT).attribute(event(codex_evidence(snapshot(), sample_at)))['status'] == 'unknown'


def test_exhausted_state_never_assigns_request_to_credits():
    result = Attributor(ACCOUNT).attribute(event(codex_evidence(snapshot(100, credits={'has_credits': True}), AT)))
    assert result['source'] == 'unknown'
    assert result['reason'] == 'exhausted_or_transition_no_debit_proof'


def test_codex_extra_quota_is_scoped_to_model_and_keeps_real_id():
    e = codex_evidence({'limit_id': 'codex_bengalfox', 'limit_name': 'gpt-5.3-codex-spark',
                       'secondary': {'used_percent': 30, 'window_minutes': 10080}}, AT)
    assert e[0]['windows'][0]['quota_group'] == 'codex_bengalfox'
    assert Attributor(ACCOUNT).attribute(event(e))['status'] == 'unknown'
    assert Attributor(ACCOUNT).attribute(event(e, model='gpt-5.3-codex-spark'))['status'] == 'inferred'


def test_multiple_constraints_are_not_multiple_token_allocations():
    e = codex_evidence({**snapshot(), 'secondary': {'used_percent': 40}}, AT)
    r = Attributor(ACCOUNT).attribute(event(e))
    assert len(r['constraints']) == 2
    assert 'token_split' not in r


def test_overage_enabled_status_is_not_using_overage():
    e = claude_evidence({'overageStatus': 'allowed'}, AT)
    assert Attributor(CLAUDE).attribute(event(e))['source'] == 'unknown'
    e = claude_evidence({'isUsingOverage': True}, AT)
    r = Attributor(CLAUDE).attribute(event(e))
    assert (r['status'], r['source']) == ('inferred', 'paid_extra')


def test_claude_model_weekly_pool_is_not_automatically_a_free_gift():
    e = claude_evidence({'unifiedWindows': {'seven_day_overage_included': {'utilization': .2}}}, AT)
    assert e[0]['windows'][0]['quota_group'] == 'seven_day_overage_included'
    r = Attributor(CLAUDE).attribute(event(e))
    assert r['source'] == 'unknown'
    assert r['constraints'] == []


def test_claude_model_weekly_pool_constrains_only_fable_requests():
    e = claude_evidence({'unifiedWindows': {'seven_day_overage_included': {'utilization': .2}}}, AT)
    fable = Attributor(CLAUDE).attribute(event(e, model='claude-fable-5-2'))
    assert [c['quota_group'] for c in fable['constraints']] == ['seven_day_overage_included']
    assert Attributor(CLAUDE).attribute(event(e, model='claude-opus-5-5'))['constraints'] == []


def test_claude_fallback_credit_and_kimi_usage_scope_are_not_receipts():
    for provider, raw in [('claude', {'fallback_credit': 1}), ('kimi', {'usageScope': 'turn'})]:
        e = request_metadata(provider, raw, AT)
        assert Attributor(ACCOUNT).attribute(event(e))['status'] == 'unknown'


def varint(v):
    out = bytearray()
    while v > 127:
        out.append((v & 127) | 128)
        v >>= 7
    return bytes(out) + bytes([v])


def pb(n, value):
    if isinstance(value, str):
        value = value.encode()
    if isinstance(value, bytes):
        return varint(n*8+2) + varint(len(value)) + value
    return varint(n*8) + varint(value)


def test_antigravity_positive_request_debit_does_not_invent_token_split_or_credit_origin():
    e = antigravity_evidence({18: pb(1, 1)+pb(2, 42)}, AT, 'req')
    r = Attributor(ANTIGRAVITY).attribute(event(e))
    assert (r['status'], r['source']) == ('confirmed', 'credits_involved')
    assert r['token_split'] is None
    assert r['credit_origin'] == 'unknown_purchased_or_promotional'
    assert not r['ordinary_capacity_eligible']


def test_antigravity_cost_zero_and_unlinked_debit_do_not_confirm_usage():
    for chat in ({13: 50}, {18: pb(1, 1)+pb(2, 0)}, {18: pb(2, (1 << 64)-1)}):
        assert Attributor(ANTIGRAVITY).attribute(event(antigravity_evidence(chat, AT, 'req')))['status'] == 'unknown'
    assert Attributor(ANTIGRAVITY).attribute(event(antigravity_evidence({18: pb(2, 42)}, AT, 'another')))['status'] == 'unknown'


def test_evidence_from_another_provider_cannot_label_request():
    debit = antigravity_evidence({18: pb(2, 42)}, AT, 'req')
    assert Attributor(ACCOUNT).attribute(event(debit))['status'] == 'unknown'
    assert Attributor(ACCOUNT).attribute(event(claude_evidence({'isUsingOverage': True}, AT)))['status'] == 'unknown'


def test_latest_stale_sample_blocks_older_success_but_new_native_sample_can_recover():
    base = {'account_id': ACCOUNT['account_id'], 'id': 'weekly', 'quota_group': 'codex', 'remaining_percent': 80}
    samples = [{**base, 'at': '2026-11-12T11:58:00Z'},
               {**base, 'at': '2026-11-12T11:59:00Z', 'stale': True}]
    classifier = Attributor({**ACCOUNT, 'quota_observations': samples})
    assert classifier.attribute(event())['reason'] == 'latest_state_unavailable'
    assert classifier.attribute(event(codex_evidence(snapshot(), AT)))['status'] == 'inferred'


def test_optional_malformed_limit_containers_are_ignored():
    assert codex_evidence({'by_limit_id': []}, AT)[0]['windows'] == []
    assert claude_evidence({'unifiedWindows': []}, AT)[0]['windows'] == []


def test_export_failure_leaves_no_partial_run_and_preserves_existing_output(tmp_path, monkeypatch):
    from scripts import provider_usage_attribution_report as exporter
    manifest = {'accounts': [ACCOUNT]}
    output = tmp_path/'out'
    def fail(*args, **kwargs):
        raise RuntimeError('simulated failure')
    monkeypatch.setattr(exporter, 'report_account', fail)
    with pytest.raises(RuntimeError):
        run(manifest, moment(AT), moment('2026-11-13T00:00:00Z'), str(output))
    assert not output.exists() and not list(tmp_path.glob('.attribution-run-*'))
    output.mkdir()
    (output/'requests.jsonl').write_text('existing')
    with pytest.raises(ValueError, match='empty'):
        run(manifest, moment(AT), moment('2026-11-13T00:00:00Z'), str(output))
    assert (output/'requests.jsonl').read_text() == 'existing'


def test_export_duplicate_accounts_rejected_before_creating_outputs(tmp_path):
    output = tmp_path/'out'
    with pytest.raises(ValueError, match='Duplicate'):
        run({'accounts': [ACCOUNT, ACCOUNT]}, moment(AT), moment('2026-11-13T00:00:00Z'), str(output))
    assert not output.exists()


def test_account_identity_stale_and_reset_boundaries_are_respected():
    q = {'account_id': 'codex_test', 'at': AT, 'id': 'weekly', 'remaining_percent': 80}
    for raw in ({**q, 'stale': True}, {**q, 'account_id': 'other'},
                {**q, 'identity_epoch': 'new'}, {**q, 'resets_at': AT}):
        r = Attributor({**ACCOUNT, 'quota_observations': [raw]}).attribute(event())
        assert r['status'] == 'unknown'


def test_latest_observation_supersedes_old_value_equal_time_conflict_stays_unknown():
    old = codex_evidence(snapshot(100), '2026-11-12T11:59:00Z')
    new = codex_evidence(snapshot(10), AT)
    assert Attributor(ACCOUNT).attribute(event(old+new))['status'] == 'inferred'
    r = Attributor(ACCOUNT).attribute(event(codex_evidence(snapshot(100), AT)+new))
    assert r['reason'] == 'conflicting_state'


def test_only_explicit_account_applicability_adds_active_policy():
    policy = {'id': 'promo', 'account_id': 'codex_test', 'evidence_source': 'existing-official-notice',
              'effective_from': '2026-11-12T11:00:00Z', 'effective_until': '2026-11-12T13:00:00Z'}
    assert Attributor({**ACCOUNT, 'entitlement_policies': [policy]}).attribute(event())['policy_ids'] == []
    r = Attributor({**ACCOUNT, 'entitlement_policies': [{**policy, 'applicability_confirmed': True}]}).attribute(event())
    assert r['policy_ids'] == ['promo'] and r['source'] == 'unknown'


def test_native_claude_deduplicates_blocks_and_keeps_conflicting_evidence(tmp_path):
    p = tmp_path/'claude.jsonl'
    rows = [{'type': 'rate_limit_event', 'timestamp': AT, 'rate_limit_info': {'isUsingOverage': True}},
            {'type': 'assistant', 'timestamp': AT, 'sessionId': 's',
             'message': {'id': 'req', 'model': 'claude-opus', 'usage': {'input_tokens': 10}}},
            {'type': 'rate_limit_event', 'timestamp': AT, 'rate_limit_info': {'isUsingOverage': False}},
            {'type': 'assistant', 'timestamp': AT, 'sessionId': 's',
             'message': {'id': 'req', 'model': 'claude-opus', 'usage': {'input_tokens': 10, 'output_tokens': 5}}}]
    p.write_text(''.join(json.dumps(row)+'\n' for row in rows))
    account = {'provider': 'claude', 'account_id': 'claude_one', 'sources': [{'format': 'claude', 'path': str(p)}]}
    report = report_account(account, moment('2026-11-12T00:00:00Z'), moment('2026-11-13T00:00:00Z'), 'Asia/Hong_Kong')
    assert report['requests'] == 1 and report['totals']['total_tokens'] == 15
    assert report['quota_attribution']['reasons'] == {'conflicting_state': 1}


def test_native_antigravity_receipt_and_ledger_automatic_link(tmp_path):
    p = tmp_path/'ag.db'
    chat = pb(4, pb(2, 10)+pb(4, 5)+pb(11, 'req')) + pb(9, pb(4, pb(1, 1794484800))) + pb(18, pb(1, 1)+pb(2, 42))
    with sqlite3.connect(p) as c:
        c.execute('CREATE TABLE gen_metadata(idx INTEGER,data BLOB)')
        c.execute('INSERT INTO gen_metadata VALUES(1,?)', (pb(1, chat),))
    manifest = {'accounts': [{'provider': 'antigravity', 'account_id': 'ag',
                             'sources': [{'format': 'antigravity', 'path': str(p)}]}]}
    output = tmp_path/'out'
    report = run(manifest, moment('2026-11-12T00:00:00Z'), moment('2026-11-13T00:00:00Z'), str(output))
    assert report['usage_queried'] is False
    assert report['accounts'][0]['quota_attribution']['requests_by_status_source'] == {'confirmed:credits_involved': 1}
    row = json.loads((output/'requests.jsonl').read_text())
    assert row['usage']['total_tokens'] == 15
    assert row['attribution']['token_split'] is None


def test_metadata_whitelist_drops_credentials_and_prompt_content():
    e = codex_evidence(snapshot(access_token='secret', prompt='private'), AT)
    assert 'secret' not in json.dumps(e) and 'private' not in json.dumps(e)
    q = {'account_id': 'codex_test', 'at': AT, 'remaining_percent': 80, 'secret': 'must-not-copy'}
    result = Attributor({**ACCOUNT, 'quota_observations': [q]}).attribute(event())
    assert 'must-not-copy' not in json.dumps(result)


@pytest.mark.parametrize('provider,group,model,expected', [
    ('kimi', 'kimi-code', 'kimi-for-coding', 'inferred'),
    ('antigravity', 'Gemini Models', 'gemini-4-carbon', 'inferred'),
    ('antigravity', 'Gemini Models', 'claude-opus', 'unknown'),
    ('antigravity', 'Unmapped Group', 'gemini-4-carbon', 'unknown'),
])
def test_kimi_and_antigravity_cache_join_respects_account_and_model_scope(provider, group, model, expected):
    account = {'provider': provider, 'account_id': 'account', 'quota_observations': [
        {'account_id': 'account', 'id': 'weekly', 'quota_group': group, 'at': AT, 'remaining_percent': 80}]}
    result = Attributor(account).attribute(event(model=model))
    assert result['status'] == expected


def test_native_codex_reader_does_not_relabel_spark_as_main_quota(tmp_path):
    p = tmp_path/'codex.jsonl'
    p.write_text(json.dumps({'type': 'event_msg', 'timestamp': AT, 'payload': {
        'type': 'token_count', 'rate_limits': {'limit_id': 'codex_bengalfox',
        'limit_name': 'gpt-5.3-codex-spark', 'secondary': {'used_percent': 10, 'window_minutes': 10080}},
        'info': {'total_token_usage': {'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15},
                 'last_token_usage': {'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15}}}})+'\n')
    from scripts.provider_weekly_usage import CodexSource
    rows = list(CodexSource({'path': str(p), 'session_id': 's'}, set()).read())
    assert rows[0]['quotas'][0]['quota_group'] == 'codex_bengalfox'
    assert rows[0]['quota_evidence'][0]['windows'][0]['models'] == ['gpt-5.3-codex-spark']
