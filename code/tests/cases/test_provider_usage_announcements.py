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
