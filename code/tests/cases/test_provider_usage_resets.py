from copy import deepcopy

import pytest

from scripts.provider_usage_resets import classify_resets
from scripts.provider_weekly_usage import moment, report_account
from scripts.provider_usage_refresh_journal import RefreshJournal


def row(hour, remaining, count=1, expiry='2026-11-20T00:00:00Z', reset='2026-11-10T00:00:00Z'):
    at = f'2026-11-01T{hour:02}:00:00Z'
    cards = [{'id': 'card', 'status': 'available', 'expiresAt': expiry}] if count else []
    return {'id': hour, 'provider': 'codex', 'account_id': 'a', 'finished_at': at, 'sample': {
        'status': 'ok', 'rate_limits': [{'id': 'primary', 'quota_group': 'codex',
        'observed_at': at, 'remaining_percent': remaining, 'resets_at': reset, 'window_duration_mins': 10080}],
        'refresh_cards': {'status': 'observed', 'observed_at': at, 'available_count': count, 'cards': cards}}}


def classify(rows, announcements=()):
    return classify_resets(rows, 'codex', 'a', moment, announcements)


def test_manual_card_with_immediate_consumption_and_duplicates():
    rows = [row(1, 20), row(2, 98, 0)]
    found = classify(rows)
    assert found[0]['reset_cause'] == 'manual_card'
    assert found[0]['actual_reset_at'] is None
    assert classify(rows * 2) == found


@pytest.mark.parametrize('change', ['expired', 'missing_details', 'unknown_count', 'stale_cards', 'old_card_clock'])
def test_card_decrease_does_not_prove_use(change):
    rows = [row(1, 20), row(2, 100, 0)]
    if change == 'expired': rows[0]['sample']['refresh_cards']['cards'][0]['expiresAt'] = '2026-11-01T01:30:00Z'
    if change == 'missing_details': rows[0]['sample']['refresh_cards']['cards'] = None
    if change == 'unknown_count': rows[1]['sample']['refresh_cards']['available_count'] = None
    if change == 'stale_cards': rows[1]['sample']['refresh_cards']['stale'] = True
    if change == 'old_card_clock': rows[1]['sample']['refresh_cards']['observed_at'] = '2026-11-01T01:00:00Z'
    assert classify(rows)[0]['reset_cause'] == 'unknown'


def test_scheduled_and_endpoint_only_and_conflict():
    before = row(1, 20, reset='2026-11-01T01:30:00Z')
    after = row(2, 99, reset='2026-11-08T01:30:00Z')
    assert classify([before, after])[0]['reset_cause'] == 'scheduled'
    after['sample']['rate_limits'][0]['remaining_percent'] = 19
    assert classify([before, after]) == []
    conflict = deepcopy(before)
    conflict['sample']['rate_limits'][0]['remaining_percent'] = 10
    assert classify([before, conflict, row(2, 100)]) == []


def test_global_needs_verified_applicable_announcement_and_known_cards():
    rows = [row(1, 20, 0), row(2, 100, 0)]
    event = {'kind': 'global_reset', 'id': 'official', 'at': '2026-11-01T01:30:00Z',
             'official_verified': True, 'official_url': 'https://openai.com/news/reset'}
    assert classify(rows, [event])[0]['reset_cause'] == 'provider_global'
    for changes in ({'official_verified': False}, {'official_url': 'https://openai.com.evil/reset'},
                    {'account_ids': ['other']}, {'window_ids': ['secondary']}, {'at': '2026-10-01T00:00:00Z'}):
        assert classify(rows, [{**event, **changes}])[0]['reset_cause'] == 'unknown'


def test_claude_and_incremental_journal_and_report(tmp_path):
    rows = [row(1, 20), row(2, 100, 0)]
    for r in rows:
        r['provider'] = 'claude'
        r['sample']['rate_limits'][0].update(id='seven_day', quota_group='claude')
    assert classify_resets(rows, 'claude', 'a', moment)[0]['reset_cause'] == 'manual_card'
    def pages(*_args): yield rows
    journal = RefreshJournal(tmp_path / 'cache')
    journal.sync_home('claude', 'a', pages)
    journal.close()
    journal = RefreshJournal(tmp_path / 'cache')
    journal.sync_home('claude', 'a', pages)
    assert journal.db.execute('SELECT count(*) FROM reset_snapshots').fetchone()[0] == 2
    snapshots = journal.reset_snapshots('claude', 'a', moment('2026-11-01T00:00:00Z'), moment('2026-11-03T00:00:00Z'))
    journal.close()
    report = report_account({'provider': 'claude', 'account_id': 'a', 'sources': [], 'reset_snapshots': snapshots},
                            moment('2026-11-01T00:00:00Z'), moment('2026-11-03T00:00:00Z'), 'UTC')
    assert report['reset_classifications'][0]['reset_cause'] == 'manual_card'


def test_unscheduled_refill_excludes_whole_week_capacity_fit():
    rows = [row(1, 20, reset='2026-11-08T00:00:00Z'), row(2, 100, 0, reset='2026-11-08T00:00:00Z')]
    quotas = [{**r['sample']['rate_limits'][0], 'at': r['finished_at'], 'evidence_source': 'monitor_quota'} for r in rows]
    report = report_account({'provider': 'codex', 'account_id': 'a', 'sources': [],
                            'reset_snapshots': rows, 'quota_observations': quotas},
                           moment('2026-10-31T00:00:00Z'), moment('2026-11-09T00:00:00Z'), 'UTC')
    assert report['weekly_cycles']
    assert all(c['status'] == 'reset_interrupted' and c['tokens_at_100_percent_same_mix'] is None
               for c in report['weekly_cycles'])


def test_unverified_import_does_not_become_official_reset():
    from scripts.provider_usage_events_import import provider_events
    event = {'tool': 'codex', 'type': 'usage_reset', 'global': True, 'stage': 'completed',
             'completedAt': '2026-11-01T01:30:00Z', 'id': 'third-party'}
    assert provider_events({'events': [event]}, 'codex') == []


@pytest.mark.parametrize('provider,window_id', [('kimi', 'limit7d'), ('antigravity', 'gemini/Weekly limit remaining')])
def test_other_providers_use_native_endpoints_without_assuming_weekly_period(provider, window_id):
    rows = [row(1, 20, reset='2026-11-01T01:30:00Z'), row(2, 99, reset='2026-11-08T01:30:00Z')]
    for r in rows:
        r['provider'] = provider
        r['sample'].pop('refresh_cards')
        w = r['sample']['rate_limits'][0]
        w.update(id=window_id, quota_group=provider)
        w.pop('window_duration_mins')
    found = classify_resets(rows, provider, 'a', moment)
    assert found[0]['reset_cause'] == 'scheduled'
    assert found[0]['window_duration_mins'] == 10080
    assert found[0]['card_count_delta'] is None
