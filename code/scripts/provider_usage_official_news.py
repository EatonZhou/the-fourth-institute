"""Daily bounded official-page checks, independent of reset-event inference."""
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
import re
from urllib.request import urlopen
from zoneinfo import ZoneInfo

from management_common.file_permissions import atomic_write_private_bytes, ensure_private_directory
from scripts.provider_usage_announcement_sources import source_list
from scripts.provider_usage_native import record_path


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hidden = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'noscript'):
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript'):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def download_page(url):
    with urlopen(url, timeout=20) as response:
        raw = response.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError('Official page too large')
    return raw.decode('utf-8')


def check_official_news(root='features:provider_usage/official-reset-news', *, now=None, fetch=download_page):
    root = record_path(root)
    ensure_private_directory(root)
    now = now or datetime.now(timezone.utc)
    day = now.astimezone(ZoneInfo('Asia/Hong_Kong')).date().isoformat()
    checks = []
    for source in source_list():
        if not source.get('check_daily'):
            continue
        cache = root / (source['id'] + '.json')
        previous = json.loads(cache.read_text()) if cache.exists() else None
        status, error = 'available', None
        if previous and previous.get('day') == day:
            current = previous
        else:
            try:
                parser = Page()
                parser.feed(fetch(source['events_url']))
                body = re.sub(r'\s+', ' ', ' '.join(parser.parts)).strip()
                if len(body) < 150 or source['expected_text'] not in body.lower():
                    raise ValueError('Official page unreadable')
                digest = hashlib.sha256(body.encode()).hexdigest()
                mentions = []
                for match in re.finditer(r'reset|重置|清零', body, re.I):
                    excerpt = body[max(0, match.start()-100):match.end()+180]
                    if re.search(r'quota|credit|usage|limit|额度|用量|限额', excerpt, re.I):
                        mentions.append(excerpt)
                current = {'day': day, 'source_id': source['id'], 'url': source['events_url'],
                    'providers': source['providers'], 'checked_at': now.isoformat(),
                    'content_digest': digest, 'text': body, 'reset_mentions': list(dict.fromkeys(mentions)),
                    'finding': ('baseline_recorded' if not previous else 'no_change' if digest == previous.get('content_digest')
                                else 'changed_review_needed'), 'actual_reset_at': None}
                atomic_write_private_bytes(cache, json.dumps(current, ensure_ascii=False).encode())
            except (OSError, ValueError, TimeoutError) as exc:
                status, error = 'failed', type(exc).__name__
                current = previous or {'source_id': source['id'], 'url': source['events_url'],
                                       'providers': source['providers']}
        checks.append({k: v for k, v in {**current, 'status': status, 'error_code': error}.items() if k != 'text'})
    status = 'partial' if any(c['status'] == 'failed' for c in checks) else 'available'
    target = root / 'latest.json'
    output = {'status': status, 'checked_day': day, 'checks': checks,
              'scope': 'listed_official_pages_only', 'automatic_reset_events_created': 0}
    atomic_write_private_bytes(target, json.dumps(output, ensure_ascii=False).encode())
    return {'status': status, 'path': str(target), 'checks': checks}
