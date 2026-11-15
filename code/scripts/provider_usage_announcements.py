"""Daily announcement discovery. Third-party events require a matching review.

reviewed.json stores events with id, evidence_digest, official_url and
official_verified=true. Editing upstream content invalidates that review.
"""
from datetime import datetime, timezone
import hashlib
import json
from urllib.request import urlopen
from zoneinfo import ZoneInfo

from management_common.file_permissions import atomic_write_private_bytes, ensure_private_directory
from scripts.provider_usage_native import record_path
from scripts.provider_usage_resets import official_announcement

SOURCE = 'https://divin.cc/api/events'


def download():
    with urlopen(SOURCE, timeout=20) as response:
        raw = response.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError('announcement response too large')
    return json.loads(raw)


def collect_announcements(root='features:provider_usage/announcements', *, now=None, fetch=download):
    root = record_path(root)
    ensure_private_directory(root)
    now = now or datetime.now(timezone.utc)
    day = now.astimezone(ZoneInfo('Asia/Hong_Kong')).date().isoformat()
    cache = root / 'candidates.json'
    previous = json.loads(cache.read_text()) if cache.exists() else None
    status, error = 'available', None
    if previous and previous.get('day') == day:
        discovered = previous
    else:
        try:
            payload = fetch()
            if not isinstance(payload, dict) or not isinstance(payload.get('events'), list):
                raise ValueError('invalid events export')
            events = []
            seen = set()
            for event in payload['events']:
                if not isinstance(event, dict) or not isinstance(event.get('id'), str) or event['id'] in seen:
                    raise ValueError('invalid or duplicate event identity')
                seen.add(event['id'])
                if event.get('tool') not in {'codex', 'claude-code'}:
                    continue
                # An aggregator cannot certify itself, even if its payload claims to.
                clean = {k: v for k, v in event.items() if k not in {'official_verified', 'official_url', 'evidence_digest'}}
                clean['evidence_digest'] = hashlib.sha256(json.dumps(clean, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                events.append(clean)
            discovered = {'day': day, 'source': SOURCE, 'fetched_at': now.isoformat(), 'events': events}
            atomic_write_private_bytes(cache, json.dumps(discovered, ensure_ascii=False).encode())
        except (OSError, ValueError, TimeoutError) as exc:
            status, error = ('partial' if previous else 'failed'), type(exc).__name__
            discovered = previous or {'events': []}
    review_path = root / 'reviewed.json'
    review_document = json.loads(review_path.read_text()) if review_path.exists() else {}
    if not isinstance(review_document, dict):
        raise ValueError('invalid announcement review document')
    reviews = review_document.get('events', [])
    if not isinstance(reviews, list) or any(not isinstance(r, dict) or not isinstance(r.get('id'), str) for r in reviews):
        raise ValueError('invalid announcement review records')
    reviews = {r['id']: r for r in reviews}
    verified, pending = [], []
    for event in discovered['events']:
        provider = 'claude' if event['tool'] == 'claude-code' else 'codex'
        review = reviews.get(event['id'], {})
        if (review.get('evidence_digest') == event['evidence_digest']
                and official_announcement(review, provider)):
            verified.append({**event, **{k: review[k] for k in ('official_verified', 'official_url', 'account_ids', 'window_ids') if k in review}})
        else:
            pending.append(event['id'])
    target = root / 'latest.json'
    output = {'events': verified, 'pending_review': pending, 'source': SOURCE,
              'fetched_at': discovered.get('fetched_at'), 'status': status}
    atomic_write_private_bytes(target, json.dumps(output, ensure_ascii=False).encode())
    return {'status': status, 'path': str(target), 'candidates': len(discovered['events']),
            'verified': len(verified), 'pending_review': len(pending), 'error_code': error}
