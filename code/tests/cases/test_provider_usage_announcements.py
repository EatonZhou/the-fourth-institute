from datetime import datetime, timezone
import json

import pytest

from scripts.provider_usage_announcements import collect_announcements


NOW = datetime(2026, 11, 15, tzinfo=timezone.utc)


def candidate():
    return {'id': 'a', 'tool': 'codex', 'type': 'usage_reset', 'global': True,
            'stage': 'completed', 'completedAt': '2026-11-14T23:00:00Z',
            'official_verified': True, 'official_url': 'https://openai.com/news/'}


def test_daily_cache_review_and_content_change_invalidates_review(tmp_path):
    calls = []
    def fetch():
        calls.append(True)
        return {'events': [candidate()]}
    result = collect_announcements(tmp_path, now=NOW, fetch=fetch)
    assert result['pending_review'] == 1 and result['verified'] == 0
    event = json.loads((tmp_path / 'candidates.json').read_text())['events'][0]
    assert 'official_verified' not in event
    (tmp_path / 'reviewed.json').write_text(json.dumps({'events': [{**event,
        'official_verified': True, 'official_url': 'https://openai.com/news/'}]}))
    assert collect_announcements(tmp_path, now=NOW, fetch=fetch)['verified'] == 1
    assert len(calls) == 1
    tomorrow = NOW.replace(day=16)
    changed = {**candidate(), 'scope': 'different accounts'}
    assert collect_announcements(tmp_path, now=tomorrow, fetch=lambda: {'events': [changed]})['verified'] == 0


def test_network_failure_retains_original_observation_time(tmp_path):
    collect_announcements(tmp_path, now=NOW, fetch=lambda: {'events': [candidate()]})
    def fail(): raise OSError('offline')
    result = collect_announcements(tmp_path, now=NOW.replace(day=16), fetch=fail)
    assert result['status'] == 'partial'
    assert json.loads((tmp_path / 'latest.json').read_text())['fetched_at'] == NOW.isoformat()


@pytest.mark.parametrize('payload', [[], {}, {'events': [candidate(), candidate()]}, {'events': [None]}])
def test_invalid_export_does_not_certify_events(tmp_path, payload):
    result = collect_announcements(tmp_path, now=NOW, fetch=lambda: payload)
    assert result['status'] == 'failed' and result['verified'] == 0


def test_linked_third_party_event_flows_to_log_inference_without_official_upgrade(tmp_path):
    from scripts.provider_usage_events_import import provider_events
    from scripts.provider_usage_resets import classify_resets
    from scripts.provider_weekly_usage import moment, build_report
    from scripts.provider_usage_report import markdown
    from tests.cases.test_provider_usage_resets import row
    event = {**candidate(), 'completedAt': '2026-11-01T01:30:00Z',
             'sources': [{'url': 'https://x.com/ClaudeDevs/status/1234567890'}],
             'scope': 'paid accounts'}
    result = collect_announcements(tmp_path, now=NOW, fetch=lambda: {'events': [event]})
    assert result['inferred_candidates'] == 1 and result['verified'] == 0
    exported = json.loads((tmp_path / 'latest.json').read_text())
    assert exported['events'][0]['official_verified'] is False
    events = provider_events(exported, 'codex')
    snapshots = [row(1, 20, 0), row(2, 98, 0)]
    classified = classify_resets(snapshots, 'codex', 'a', moment, events)[0]
    assert classified['reset_cause'] == 'provider_global'
    assert classified['actual_reset_at'] is None
    assert classified['announcement_evidence']['grade'] == 'third_party_with_original_links'
    assert 'announcement_original_not_independently_verified' in classified['limitations']
    reviewed = {**events[0], 'id': 'reviewed', 'official_verified': True,
                'official_url': 'https://openai.com/news/reset'}
    strongest = classify_resets(snapshots, 'codex', 'a', moment, [*events, reviewed])[0]
    assert strongest['announcement_id'] == 'reviewed'
    assert strongest['announcement_evidence']['grade'] == 'official_verified'
    snapshots[1]['sample']['refresh_cards']['available_count'] = None
    assert classify_resets(snapshots, 'codex', 'a', moment, events)[0]['reset_cause'] == 'unknown'
    snapshots[1]['sample']['refresh_cards']['available_count'] = 0
    report = build_report({'provider_events': exported, 'accounts': [
        {'provider': 'codex', 'account_id': 'a', 'sources': [], 'reset_snapshots': snapshots}]},
        '2026-11-01T00:00:00Z', '2026-11-03T00:00:00Z')
    rendered = markdown(report)
    assert 'third_party_with_original_links' in rendered
    assert 'https://x.com/ClaudeDevs/status/1234567890' in rendered


@pytest.mark.parametrize('link', [None, 'https://x.com/ClaudeDevs', 'https://x.com.evil/a/status/123',
                               'http://x.com/a/status/123', 'https://[', 'javascript:alert(1)'])
def test_missing_or_invalid_link_uses_only_trusted_report_grade(tmp_path, link):
    event = {**candidate(), 'posts': [{'url': link}]}
    result = collect_announcements(tmp_path, now=NOW, fetch=lambda: {'events': [event]})
    assert result['inferred_candidates'] == 1 and result['pending_review'] == 1
    emitted = json.loads((tmp_path / 'latest.json').read_text())['events'][0]
    assert emitted['announcement_evidence_grade'] == 'trusted_third_party_report'
    assert emitted['original_urls'] == [] and emitted['official_verified'] is False


@pytest.mark.parametrize('changes', [{'global': False}, {'type': 'reset_card'}, {'type': 'policy'},
                                    {'stage': 'scheduled'}, {'completedAt': None}])
def test_card_grants_policies_and_incomplete_events_do_not_enter_reset_inference(tmp_path, changes):
    event = {**candidate(), 'posts': [{'url': 'https://x.com/thsottiaux/status/123'}], **changes}
    result = collect_announcements(tmp_path, now=NOW, fetch=lambda: {'events': [event]})
    assert result['inferred_candidates'] == 0


def test_only_listed_trusted_sources_can_support_unannounced_reset(tmp_path):
    from scripts.provider_usage_resets import announcement_evidence, classify_resets, DISCOVERY_SOURCE
    from scripts.provider_usage_events_import import provider_events
    from scripts.provider_weekly_usage import moment
    from tests.cases.test_provider_usage_resets import row
    event = {**candidate(), 'completedAt': '2026-11-01T01:30:00Z'}
    collect_announcements(tmp_path, now=NOW, fetch=lambda: {'events': [event]})
    emitted = json.loads((tmp_path / 'latest.json').read_text())['events'][0]
    assert announcement_evidence(emitted, 'codex')['grade'] == 'trusted_third_party_report'
    assert announcement_evidence({**emitted, 'discovery_source': 'https://unlisted.example/feed'}, 'codex') is None
    assert announcement_evidence({**emitted, 'evidence_digest': None}, 'codex') is None
    assert announcement_evidence({**emitted, 'tool': 'claude-code'}, 'codex') is None
    events = provider_events({'events': [emitted]}, 'codex')
    snapshots = [row(1, 20, 0), row(2, 98, 0)]
    found = classify_resets(snapshots, 'codex', 'a', moment, events)[0]
    assert found['reset_cause'] == 'provider_global'
    assert found['announcement_evidence']['grade'] == 'trusted_third_party_report'
    snapshots[1]['sample']['refresh_cards']['available_count'] = None
    assert classify_resets(snapshots, 'codex', 'a', moment, events)[0]['reset_cause'] == 'unknown'
