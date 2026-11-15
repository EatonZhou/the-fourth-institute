from datetime import datetime, timezone
import json

from scripts import provider_usage_official_news as module

NOW = datetime(2026, 11, 15, tzinfo=timezone.utc)


def setup(monkeypatch):
    monkeypatch.setattr(module, 'source_list', lambda: [
        {'id': 'a', 'events_url': 'https://official.example/a', 'providers': ['antigravity'],
         'check_daily': True, 'expected_text': 'quota'},
        {'id': 'k', 'events_url': 'https://official.example/k', 'providers': ['kimi'],
         'check_daily': True, 'expected_text': 'quota'}])


def page(text):
    return '<html><body>' + ('Official quota notice. ' * 10) + text + '</body></html>'


def test_daily_per_source_cache_detects_changes_without_creating_reset_events(tmp_path, monkeypatch):
    setup(monkeypatch)
    calls = []
    def fetch(url):
        calls.append(url)
        return page('We are resetting quota now. <script>private fake reset</script>')
    first = module.check_official_news(tmp_path, now=NOW, fetch=fetch)
    assert len(calls) == 2 and first['status'] == 'available'
    assert all(c['finding'] == 'baseline_recorded' and c['reset_mentions'] for c in first['checks'])
    module.check_official_news(tmp_path, now=NOW, fetch=fetch)
    assert len(calls) == 2
    second = module.check_official_news(tmp_path, now=NOW.replace(day=16), fetch=lambda _: page('New quota reset notice'))
    assert all(c['finding'] == 'changed_review_needed' for c in second['checks'])
    assert all(c['actual_reset_at'] is None for c in second['checks'])
    assert json.loads((tmp_path / 'latest.json').read_text())['automatic_reset_events_created'] == 0
    assert 'private fake reset' not in (tmp_path / 'a.json').read_text()


def test_failure_keeps_old_check_time_and_does_not_become_no_news(tmp_path, monkeypatch):
    setup(monkeypatch)
    module.check_official_news(tmp_path, now=NOW, fetch=lambda _: page('No update'))
    def fetch(url):
        if url.endswith('/a'):
            raise OSError('private details')
        return page('No update')
    result = module.check_official_news(tmp_path, now=NOW.replace(day=16), fetch=fetch)
    assert result['status'] == 'partial'
    assert result['checks'][0]['status'] == 'failed'
    assert result['checks'][0]['checked_at'] == NOW.isoformat()
    assert result['checks'][1]['finding'] == 'no_change'
    assert 'private details' not in json.dumps(result)


def test_empty_html_is_failed_not_successful_check(tmp_path, monkeypatch):
    setup(monkeypatch)
    result = module.check_official_news(tmp_path, now=NOW, fetch=lambda _: '<script>Quota reset</script>')
    assert all(c['status'] == 'failed' for c in result['checks'])
