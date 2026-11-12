"""Offline usage report: existing native records, no remote model calls."""
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.provider_weekly_usage import build_report, collect_portal


START = '2026-11-01T00:00:00Z'
END = '2026-11-08T00:00:00Z'
AT = '2026-11-02T00:00:00Z'
MS = 1793577600000


def source(tmp_path, fmt, records, name='records', session='session'):
    path = tmp_path / name
    path.write_text(''.join(json.dumps(r) + '\n' for r in records))
    return {'format': fmt, 'path': str(path), 'session_id': session}


def report(sources, provider='test', **extra):
    return build_report({'accounts': [{'provider': provider, 'account_id': 'one',
                                      'sources': sources, **extra}]}, START, END)['accounts'][0]


def test_claude_recovers_model_and_deduplicates_content_blocks(tmp_path):
    records = [{'type': 'assistant', 'timestamp': AT, 'message': {'id': 'request', 'model': 'opus',
                'usage': {'input_tokens': 10, 'cache_read_input_tokens': 20, 'output_tokens': output}}}
               for output in (2, 5)]
    result = report([source(tmp_path, 'claude', records)])
    assert result['requests'] == 1
    assert result['totals']['total_tokens'] == 35
    assert result['buckets'][0]['model'] == 'opus'
    assert 'SECRET' not in json.dumps(result)


def test_kimi_reuses_wire_model_and_cache(tmp_path):
    record = {'type': 'usage.record', 'time': MS, 'agentId': 'main', 'model': 'kimi',
              'usage': {'inputOther': 10, 'inputCacheRead': 20, 'output': 5}}
    result = report([source(tmp_path, 'kimi', [record, record])])
    assert result['requests'] == 2  # Distinct raw occurrences without an upstream ID must not collapse.
    assert result['totals']['total_tokens'] == 70
    assert result['buckets'][0]['model'] == 'kimi'


def test_dsh_uses_preceding_context_not_current_resident_model(tmp_path):
    records = [{'type': 'request/context', 'time': MS, 'data': {'model': 'flash'}},
               {'type': 'assistant/message', 'time': MS + 1, 'seq': 0,
                'data': {'usage': {'inputTokens': 10, 'cacheReadTokens': 20, 'outputTokens': 5}}}]
    result = report([source(tmp_path, 'dsh', records)])
    assert result['requests'] == 1
    assert result['buckets'][0]['model'] == 'flash'
    assert result['totals']['total_tokens'] == 35


def test_opencode_keeps_each_model(tmp_path):
    path = tmp_path / 'requests.json'
    path.write_text(json.dumps({'requests': [{'id': str(i), 'at_ms': MS, 'model': model,
                                              'usage': {'input_tokens': 10, 'output_tokens': 5}}
                                             for i, model in enumerate(('model-a', 'model-b'))]}))
    result = report([{'format': 'opencode', 'path': str(path), 'session_id': 's'}])
    assert result['totals']['total_tokens'] == 30
    assert {b['model'] for b in result['buckets']} == {'model-a', 'model-b'}


def codex_records(fork=False):
    return [{'timestamp': START, 'type': 'session_meta', 'payload': {'id': 's', **({'forked_from_id': 'parent'} if fork else {})}},
            {'timestamp': START, 'type': 'turn_context', 'payload': {'model': 'gpt'}},
            {'timestamp': AT, 'type': 'event_msg', 'payload': {'type': 'token_count',
             'info': {'last_token_usage': {'input_tokens': 10, 'output_tokens': 5}, 'total_token_usage': {'total_tokens': 15}},
             'rate_limits': {'primary': {'window_minutes': 10080, 'resets_at': 1794096000}}}}]


def test_codex_keeps_usage_without_weekly_limits_and_deduplicates(tmp_path):
    rows = codex_records()
    rows[-1]['payload'].pop('rate_limits')
    result = report([source(tmp_path, 'codex', rows + [rows[-1]])])
    assert result['totals']['total_tokens'] == 15
    assert result['requests'] == 1
    assert result['buckets'][0]['period_kind'] == 'calendar_week'


def test_codex_records_service_tier_from_thread_settings(tmp_path):
    """快速模式（priority）由 thread_settings_applied 记录，之后的请求带上档位，直到再次修改。"""
    from scripts.provider_weekly_usage import CodexSource
    rows = codex_records()
    later = json.loads(json.dumps(rows[-1]))
    later['payload']['info']['total_token_usage']['total_tokens'] = 30
    settings = lambda tier: {'timestamp': AT, 'type': 'event_msg', 'payload': {
        'type': 'thread_settings_applied', 'thread_settings': {'model': 'gpt', 'service_tier': tier}}}
    rows = rows[:2] + [settings('priority'), rows[-1], settings(None), later]
    events = [e for e in CodexSource(source(tmp_path, 'codex', rows), set()).read() if e and not e.get('quota_only')]
    assert [e.get('service_tier') for e in events] == ['priority', None]


def test_codex_uses_observed_reset_without_claiming_known_start(tmp_path):
    result = report([source(tmp_path, 'codex', codex_records())])
    assert result['buckets'][0]['period_kind'] == 'observed_reset'
    assert 'cycle_start_unknown_not_a_complete_cycle_total' in result['gaps']


def test_codex_fork_import_is_not_counted(tmp_path):
    rows = codex_records(True)
    rows[-1]['timestamp'] = START
    result = report([source(tmp_path, 'codex', rows)])
    assert result['requests'] == 0
    assert 'fork_import_records_excluded' in result['gaps']


def test_codex_imported_parent_metadata_cannot_replace_physical_session(tmp_path):
    rows = codex_records(True)
    rows[-1]['timestamp'] = START
    rows.insert(1, {'type': 'session_meta', 'timestamp': START, 'payload': {'id': 'parent'}})
    after = json.loads(json.dumps(rows[-1]))
    after['timestamp'] = AT
    rows.append(after)
    result = report([source(tmp_path, 'codex', rows)], resident_records=[{
        'execution_id': 'e', 'session_id': 'session', 'started_at': START,
        'finished_at': END, 'usage': {'total_tokens': 15}}])
    assert result['requests'] == 1
    assert result['resident_comparisons'][0]['difference'] == 0


def test_codex_quota_only_observation_is_reused_without_counting_tokens(tmp_path):
    rows = codex_records()
    normal = rows[-1]
    rows.insert(2, {'type': 'event_msg', 'timestamp': START, 'payload': {'type': 'token_count',
        'info': None, 'rate_limits': {'secondary': {'window_minutes': 10080, 'resets_at': END, 'used_percent': 10}}}})
    normal['payload']['rate_limits'] = {}
    result = report([source(tmp_path, 'codex', rows)])
    assert result['totals']['total_tokens'] == 15
    assert result['requests'] == 1
    assert result['buckets'][0]['period_kind'] == 'observed_reset'


def test_resident_total_is_compared_never_added(tmp_path):
    src = source(tmp_path, 'requests', [{'request_id': 'r', 'occurred_at': AT,
                  'model': 'model', 'usage': {'input_tokens': 10, 'output_tokens': 5}}])
    result = report([src], resident_records=[{'execution_id': 'e', 'session_id': 'session',
                    'started_at': START, 'finished_at': END, 'usage': {'total_tokens': 20}}])
    assert result['totals']['total_tokens'] == 15
    assert result['resident_comparisons'][0]['difference'] == 5
    assert 'resident_total_mismatch_not_added' in result['gaps']


@pytest.mark.parametrize('provider', ['deepseek', 'openrouter', 'gemini', 'kimi_api', 'jev'])
def test_api_exports_share_adapter_and_do_not_invent_quota(tmp_path, provider):
    src = source(tmp_path, 'requests', [{'request_id': 'r', 'occurred_at': AT,
                  'usage': {'input_tokens': 10, 'output_tokens': 5}, 'content': 'SECRET'}])
    result = report([src], provider=provider)
    assert result['totals']['total_tokens'] == 15
    assert result['buckets'][0]['model'] == 'unknown'
    assert result['buckets'][0]['period_kind'] == 'calendar_week'
    assert 'SECRET' not in json.dumps(result)


def test_half_open_window_and_local_week(tmp_path):
    records = [{'request_id': str(i), 'occurred_at': at, 'usage': {'total_tokens': 1}}
               for i, at in enumerate((START, AT, END))]
    result = report([source(tmp_path, 'requests', records)])
    assert result['requests'] == 2


def test_multiple_quota_groups_are_not_arbitrarily_assigned(tmp_path):
    src = source(tmp_path, 'requests', [{'request_id': 'r', 'occurred_at': AT,
                  'usage': {'total_tokens': 10}, 'model': 'm'}])
    quotas = [{'at': START, 'resets_at': END, 'window_duration_mins': 10080, 'quota_group': g}
              for g in ('gemini', 'claude')]
    result = report([src], quota_observations=quotas)
    assert result['buckets'][0]['period_kind'] == 'calendar_week'
    quotas[0]['models'] = ['m']; quotas[1]['models'] = ['other']
    result = report([src], quota_observations=quotas)
    assert result['buckets'][0]['quota_group'] == 'gemini'


def test_bad_source_isolated_and_missing_not_zero(tmp_path):
    good = source(tmp_path, 'claude', [{'type':'assistant','timestamp':AT,
        'message':{'id':'r','model':'opus','usage':{'input_tokens':10}}}])
    bad = {'format': 'claude', 'path': str(tmp_path / 'missing')}
    result = report([good, bad])
    assert result['totals']['total_tokens'] == 10
    assert 'source_unreadable_or_invalid:claude' in result['gaps']
    assert report([bad])['requests'] == 0
    assert 'no_attributable_requests_not_proof_of_zero_usage' in report([bad])['gaps']


def test_antigravity_never_creates_database_or_fabricates_model(tmp_path):
    path = tmp_path / 'missing.db'
    result = report([{'format': 'antigravity', 'path': str(path)}])
    assert not path.exists()
    assert result['requests'] == 0
    path = tmp_path / 'existing.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE gen_metadata(idx INTEGER,data BLOB)')
    result = report([{'format': 'antigravity', 'path': str(path)}])
    assert result['requests'] == 0
    assert not any(g.startswith('source_unreadable') for g in result['gaps'])


def test_portal_uses_cached_accounts_only_and_exposes_truncation():
    calls = []
    def call(name, **args):
        calls.append((name, args))
        if name == 'ListProviderAccounts':
            assert args == {'refresh': False}
            return {'accounts': [{'provider': 'codex', 'account_id': 'c', 'kind': 'subscription_login'},
                                 {'provider': 'claude', 'account_id': 'a'}]}
        assert name == 'GetProviderUsageEvents'
        return {'records': [{'source': 'session', 'record': r} for r in codex_records()], 'next_cursor': 'more'}
    result = collect_portal(call, START, END, 1)
    assert len(calls) == 2
    assert result['accounts'][0]['totals']['total_tokens'] == 15
    assert 'history_pagination_truncated' in result['accounts'][0]['gaps']
    assert 'native_history_ISA_not_available_for_this_account' in result['accounts'][1]['gaps']


def test_naive_times_and_duplicate_account_entries_rejected():
    with pytest.raises(ValueError):
        build_report({}, '2026-11-01', END)
    with pytest.raises(ValueError):
        build_report({'accounts': [{'provider': 'p', 'account_id': 'a'}] * 2}, START, END)


def test_resident_accounted_takes_priority_and_crossing_turn_is_not_compared(tmp_path):
    src = source(tmp_path, 'requests', [{'request_id': 'r', 'occurred_at': AT, 'usage': {'total_tokens': 15}}])
    records = [{'session_id': 'session', 'started_at': START, 'finished_at': END,
                'usage': {'last': {'total_tokens': 3}, 'accounted': {'total_tokens': 15}}}]
    result = report([src], resident_records=records)
    assert result['resident_comparisons'][0]['difference'] == 0
    records[0]['started_at'] = '2026-10-31T00:00:00Z'
    assert report([src], resident_records=records)['resident_comparisons'] == []


def test_mixed_formats_do_not_double_count_same_session(tmp_path):
    raw = source(tmp_path, 'claude', [{'type': 'assistant', 'timestamp': AT,
                 'message': {'id': 'r', 'model': 'opus', 'usage': {'input_tokens': 10}}}], name='raw')
    exported = source(tmp_path, 'requests', [{'request_id': 'different-id', 'occurred_at': AT,
                                             'usage': {'input_tokens': 10}}], name='export')
    result = report([raw, exported])
    assert result['totals']['total_tokens'] == 10
    assert 'non_native_export_excluded_from_native_totals' in result['gaps']


def test_cli_reports_existing_export_without_contacting_provider(tmp_path):
    src = source(tmp_path, 'requests', [{'request_id': 'r', 'occurred_at': AT,
                                        'model': 'm', 'usage': {'total_tokens': 15}}])
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({'accounts': [{'provider': 'api', 'account_id': 'one', 'sources': [src]}]}))
    command = [sys.executable, str(Path(__file__).resolve().parents[2] / 'scripts/provider_weekly_usage.py'),
               '--manifest', str(manifest), '--since', START, '--until', END, '--rebuild']
    run = subprocess.run(command, capture_output=True, text=True, timeout=15, check=True)
    result = json.loads(run.stdout)
    assert result['accounts'][0]['totals']['total_tokens'] == 15
    assert not run.stderr


def test_future_and_stale_quota_observations_do_not_rewrite_history(tmp_path):
    src = source(tmp_path, 'requests', [{'request_id': 'r', 'occurred_at': AT,
                                        'usage': {'total_tokens': 10}}])
    quotas = [{'at': END, 'resets_at': '2026-11-15T00:00:00Z', 'window_duration_mins': 10080},
              {'at': START, 'resets_at': END, 'window_duration_mins': 10080, 'stale': True}]
    result = report([src], quota_observations=quotas)
    assert result['buckets'][0]['period_kind'] == 'calendar_week'


def test_codex_raw_and_sanitized_export_keep_identity_and_skip_context_estimates(tmp_path):
    from management_portal.execution.provider_usage_events import public_record
    from scripts.provider_weekly_usage import moment
    raw = codex_records()
    raw[-1]['ordinal'] = 17
    raw[-1]['payload']['info']['model_context_window'] = 200000
    raw[-1]['payload']['info']['private'] = 'SECRET'
    context = json.loads(json.dumps(raw[-1]))
    context['timestamp'] = '2026-11-02T00:00:01Z'
    context['payload']['info']['last_token_usage'] = {'total_tokens': 150000}
    raw.append(context)
    exported = [public_record(row, moment(START), moment(END)) for row in raw]
    assert exported[2]['ordinal'] == 17
    assert exported[2]['payload']['info']['total_token_usage'] == {'total_tokens': 15}
    assert 'SECRET' not in json.dumps(exported)
    a = report([source(tmp_path, 'codex', raw, name='raw')])
    b = report([source(tmp_path, 'codex', exported, name='export')])
    assert a['totals'] == b['totals'] == {'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15}
    assert a['requests'] == b['requests'] == 1
    assert 'codex_identity_fallback_timestamp_usage' not in b['gaps']
