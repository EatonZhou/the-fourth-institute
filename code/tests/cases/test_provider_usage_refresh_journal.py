import json
from datetime import datetime, timezone

import pytest

from scripts.provider_usage_refresh_journal import RefreshJournal
from scripts.provider_usage_cycles import reset_evidence
from scripts.provider_weekly_usage import moment


def raw(at='2026-11-01T01:00:00Z', reset=1794531540):
    return {'timestamp': at, 'quotaLimits': {'rateLimitType': 'seven_day', 'resetsAt': reset}}


def test_file_append_partial_rewrite_and_no_reparse(tmp_path):
    path = tmp_path / 'native.jsonl'
    path.write_text(json.dumps(raw())+'\n')
    journal = RefreshJournal(tmp_path / 'index')
    try:
        journal.sync_file('claude', 'a', path)
        journal.sync_file('claude', 'a', path)
        assert journal.stats['file_cache_hits'] == 1
        with path.open('a') as f:
            f.write(json.dumps(raw('2026-11-01T02:00:00Z')))
        journal.sync_file('claude', 'a', path)
        assert journal.db.execute('SELECT count(*) FROM observations').fetchone()[0] == 1
        with path.open('a') as f:
            f.write('\n')
        journal.sync_file('claude', 'a', path)
        assert journal.db.execute('SELECT count(*) FROM observations').fetchone()[0] == 2
        checkpoint = journal.db.execute('SELECT * FROM files').fetchall()
        with path.open('a') as f:
            f.write('{"quotaLimits":broken}\n')
        with pytest.raises(ValueError):
            journal.sync_file('claude', 'a', path)
        assert journal.db.execute('SELECT * FROM files').fetchall() == checkpoint
        assert journal.db.execute('SELECT count(*) FROM observations').fetchone()[0] == 2
        path.write_text(json.dumps(raw('2026-11-01T03:00:00Z'))+'\n')
        journal.sync_file('claude', 'a', path)
        assert journal.db.execute('SELECT count(*) FROM observations').fetchone()[0] == 1
    finally:
        journal.close()


def home_row(i, at='2026-11-01T01:00:00Z'):
    return {'id': i, 'account_id': 'a', 'finished_at': at, 'sample': {'status': 'ok', 'rate_limits': [
        {'id': 'seven_day', 'quota_group': 'claude', 'observed_at': at,
         'resets_at': 1794531540, 'remaining_percent': 98}]}}


def test_home_persistent_cursor_replay_and_duplicate_idempotence(tmp_path):
    calls = []
    def pages(provider, account, after, replay):
        calls.append((after, replay))
        yield [home_row(20)]
    journal = RefreshJournal(tmp_path / 'index')
    journal.sync_home('claude', 'a', pages)
    journal.close()
    journal = RefreshJournal(tmp_path / 'index')
    try:
        journal.sync_home('claude', 'a', pages)
        assert calls[-1][0] == 20
        assert calls[-1][1] == datetime(2026, 10, 31, 1, tzinfo=timezone.utc)
        assert journal.stats['observations_added'] == 0
        def delayed(provider, account, after, replay):
            yield [home_row(19, '2026-11-01T00:59:00Z')]
        journal.sync_home('claude', 'a', delayed)
        assert journal.db.execute('SELECT after_id FROM home').fetchone()[0] == 20
        assert journal.db.execute('SELECT count(*) FROM observations').fetchone()[0] == 2
        assert journal.observations('claude', 'other', moment('2026-01-01T00:00:00Z'), moment('2027-01-01T00:00:00Z')) == []
    finally:
        journal.close()


def test_sdk_rate_event_ingestion_and_reopen_is_incremental(tmp_path):
    path = tmp_path / 'native.jsonl'
    path.write_text(json.dumps({'timestamp': '2026-11-01T01:00:00Z',
        'type': 'rate_limit_event', 'rate_limit_info': {'unifiedWindows': {
            'seven_day': {'utilization': .25, 'resetsAt': 1794531540}}}}) + '\n')
    journal = RefreshJournal(tmp_path / 'index')
    try:
        journal.sync_file('claude', 'a', path)
        row = json.loads(journal.db.execute('SELECT payload FROM observations').fetchone()[0])
        assert row['remaining_percent'] == 75
    finally:
        journal.close()
    journal = RefreshJournal(tmp_path / 'index')
    try:
        journal.sync_file('claude', 'a', path)
        assert journal.stats['raw_bytes_read'] == 0
        assert journal.stats['file_cache_hits'] == 1
    finally:
        journal.close()


def q(day, remaining, **extra):
    return {'at': f'2026-11-{day:02}T00:00:00Z', 'resets_at': '2026-11-15T00:00:00Z',
            'remaining_percent': remaining, 'quota_group': 'g', 'window_duration_mins': 10080,
            'evidence_source': 'native_quota', **extra}


def test_unverified_or_global_event_cannot_claim_actual_time_or_cause():
    for fields in [{}, {'reset_event_verified': True, 'event_id': 'x', 'account_id': 'a', 'account_specific': False}]:
        evidence = reset_evidence([], [q(1, 20), q(2, 100, evidence_source='provider_reset_event',
            reset_occurred_at='2026-11-01T23:59:00Z', reset_reason='manual_card', **fields)], moment)[0]
        assert evidence['kind'] != 'explicit_provider_reset'
        assert evidence['actual_reset_at'] is None
        assert evidence['reset_cause'] == 'unknown'


def test_missing_percent_is_not_conflict_and_can_be_supplied_by_same_endpoint():
    evidence = reset_evidence([], [q(1, 20), q(2, None), q(2, 100, evidence_source='monitor_quota')], moment)[0]
    assert not evidence['source_conflict']
    assert evidence['remaining_percent_source'] == 'monitor_quota'


def test_conflict_blocks_linking_before_and_after_it():
    evidence = reset_evidence([], [q(1, 20), q(2, 30), q(2, 80), q(3, 100)], moment)
    candidate = next(e for e in evidence if e['kind'] == 'reported_zero_usage')
    assert candidate['reset_between'][0] is None
    assert not candidate['usable_boundary']


def test_failed_home_page_does_not_advance_cursor(tmp_path):
    journal = RefreshJournal(tmp_path / 'index')
    try:
        def pages(provider, account, after, replay):
            yield [home_row(10)]
            yield [{**home_row(20), 'sample': {'rate_limits': [
                {'id': 'seven_day', 'observed_at': 'invalid-date'}]}}]
        with pytest.raises(ValueError):
            journal.sync_home('claude', 'a', pages)
        assert journal.db.execute('SELECT after_id FROM home').fetchone()[0] == 10
        assert journal.db.execute('SELECT count(*) FROM observations').fetchone()[0] == 1
    finally:
        journal.close()


def test_journal_version_mismatch_requires_explicit_backfill(tmp_path):
    journal = RefreshJournal(tmp_path / 'index')
    with journal.db:
        journal.db.execute('UPDATE version SET value=99')
    journal.close()
    with pytest.raises(ValueError, match='backfill'):
        RefreshJournal(tmp_path / 'index')


def test_parser_fingerprint_mismatch_requires_explicit_backfill(tmp_path):
    journal = RefreshJournal(tmp_path / 'index')
    with journal.db:
        journal.db.execute("UPDATE parser_signature SET value='obsolete'")
    journal.close()
    with pytest.raises(ValueError, match='parser changed'):
        RefreshJournal(tmp_path / 'index')


def test_expired_cached_schedule_is_not_a_fresh_reset_candidate():
    assert reset_evidence([], [q(20, 100)], moment) == []


def test_local_cli_automatically_enriches_only_registered_subscriptions(tmp_path, monkeypatch, capsys):
    import scripts.resident_hatg_instruction as isa
    import scripts.provider_usage_local as local
    import scripts.provider_usage_refresh_journal as refresh
    from scripts.provider_weekly_usage import main
    monkeypatch.setattr(isa, 'execute', lambda *a, **k: {'result': {'accounts': [
        {'provider': 'claude', 'account_id': 'a', 'kind': 'subscription_login'},
        {'provider': 'claude', 'account_id': 'api', 'kind': 'api_profile'}]}})
    calls = []
    def manifest(accounts, start, end, **kwargs):
        assert not kwargs['home_evidence']
        assert not kwargs['portal_evidence']
        return {'accounts': [{'provider': 'claude', 'account_id': 'a', 'sources': []},
                             {'provider': 'claude', 'account_id': 'unresolved-account', 'sources': []}]}
    monkeypatch.setattr(local, 'local_manifest', manifest)
    def enrich(data, accounts, start, end, root):
        calls.append([a['account_id'] for a in data['accounts']])
        return {'status': 'available'}
    monkeypatch.setattr(refresh, 'enrich_manifest', enrich)
    assert main(['--resident-id', 'resident_561324ac1c0c', '--native-only', '--subscription-only',
        '--since', '2026-11-01T00:00:00Z', '--until', '2026-11-02T00:00:00Z',
        '--cache-root', str(tmp_path / 'tokens'), '--rebuild', '--workers', '1']) == 0
    result = json.loads(capsys.readouterr().out)
    assert calls == [['a']]
    assert result['refresh_evidence_ingestion']['status'] == 'available'
