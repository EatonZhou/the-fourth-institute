"""Daily discovery: linked third-party global resets may support log inference.

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
from scripts.provider_usage_resets import official_announcement, announcement_evidence, DISCOVERY_SOURCE
from scripts.provider_usage_announcement_sources import source_list

SOURCE = DISCOVERY_SOURCE


def download(url=SOURCE):
    with urlopen(url, timeout=20) as response:
        raw = response.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError('announcement response too large')
    return json.loads(raw)


def normalize_export(payload, source):
    """Map reviewed feed semantics; retain unknown scope outside inference."""
    if source.get('format') != 'agy_resets_v1':
        if not isinstance(payload, dict) or not isinstance(payload.get('events'), list):
            raise ValueError('invalid events export')
        raw_events = payload['events']
    else:
        if (not isinstance(payload, dict) or not isinstance(payload.get('data'), list)
                or not isinstance(payload.get('pagination'), dict)
                or payload['pagination'].get('has_more') is not False):
            raise ValueError('invalid or truncated reset export')
        raw_events = []
        for record in payload['data']:
            if not isinstance(record, dict) or not isinstance(record.get('id'), str):
                raise ValueError('invalid reset record')
            models, plans = record.get('models'), record.get('plans')
            if (not isinstance(models, list) or not isinstance(plans, list)
                    or plans != ['all'] or record.get('limit_change')
                    or record.get('kind') not in {'weekly', 'unspecified'}):
                continue
            groups = (['Gemini Models', 'Claude and GPT models'] if models == ['all']
                      else ['Gemini Models'] if 'gemini' in models else [])
            if not groups:
                continue
            when = datetime.fromisoformat(str(record.get('announced_at')).replace('Z', '+00:00'))
            if when.tzinfo is None:
                raise ValueError('reset announcement requires timezone')
            if not isinstance(record.get('sources', []), list):
                raise ValueError('invalid reset references')
            references = [r for r in record.get('sources', []) if isinstance(r, dict)]
            if isinstance(record.get('source'), dict):
                references.append(record['source'])
            event = {'id': 'agy:' + record['id'], 'tool': 'antigravity',
                     'type': 'usage_reset', 'stage': 'completed', 'global': True,
                     'completedAt': when.isoformat(), 'time_basis': 'announcement_time',
                     'quota_groups': groups, 'source_models': models, 'source_plans': plans,
                     'scope': record.get('text'), 'sources': references,
                     'title': 'Antigravity reset report'}
            if record['kind'] == 'weekly':
                event['window_ids'] = [g + '/Weekly Limit Remaining' for g in groups]
            raw_events.append(event)
    events, seen = [], set()
    tools = {'codex': 'codex', 'claude': 'claude-code', 'kimi': 'kimi', 'antigravity': 'antigravity'}
    allowed = {tools[p] for p in source['providers'] if p in tools}
    for event in raw_events:
        if not isinstance(event, dict) or not isinstance(event.get('id'), str) or event['id'] in seen:
            raise ValueError('invalid or duplicate event identity')
        seen.add(event['id'])
        if event.get('tool') not in allowed:
            continue
        clean = {k: v for k, v in event.items() if k not in {'official_verified', 'official_url', 'evidence_digest'}}
        clean['evidence_digest'] = hashlib.sha256(json.dumps(clean, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        events.append(clean)
    return events


def collect_announcements(root='features:provider_usage/announcements', *, now=None, fetch=download):
    root = record_path(root)
    ensure_private_directory(root)
    now = now or datetime.now(timezone.utc)
    day = now.astimezone(ZoneInfo('Asia/Hong_Kong')).date().isoformat()
    discovered, source_statuses = [], []
    for source in source_list():
        if not source.get('collector_enabled'):
            continue
        url = source['events_url']
        cache = root / ('candidates.json' if url == SOURCE else source['id'] + '-candidates.json')
        previous = json.loads(cache.read_text()) if cache.exists() else None
        status, error = 'available', None
        if previous and previous.get('day') == day:
            current = previous
        else:
            try:
                events = normalize_export(fetch(url), source)
                current = {'day': day, 'source': url, 'fetched_at': now.isoformat(), 'events': events}
                atomic_write_private_bytes(cache, json.dumps(current, ensure_ascii=False).encode())
            except (OSError, ValueError, TimeoutError) as exc:
                status, error = ('partial' if previous else 'failed'), type(exc).__name__
                current = previous or {'events': []}
        discovered.extend({**event, 'discovery_source': url} for event in current['events'])
        source_statuses.append({'id': source['id'], 'source': url, 'status': status,
                                'fetched_at': current.get('fetched_at'), 'candidates': len(current['events']),
                                'error_code': error})
    failures = [s for s in source_statuses if s['status'] != 'available']
    status = ('failed' if failures and all(s['status'] == 'failed' for s in source_statuses)
              else 'partial' if failures else 'available')
    error = ','.join(s['id'] + ':' + s['error_code'] for s in failures) or None
    review_path = root / 'reviewed.json'
    review_document = json.loads(review_path.read_text()) if review_path.exists() else {}
    if not isinstance(review_document, dict):
        raise ValueError('invalid announcement review document')
    reviews = review_document.get('events', [])
    if not isinstance(reviews, list) or any(not isinstance(r, dict) or not isinstance(r.get('id'), str) for r in reviews):
        raise ValueError('invalid announcement review records')
    reviews = {r['id']: r for r in reviews}
    verified, inferred, pending = [], [], []
    for event in discovered:
        provider = {'claude-code': 'claude', 'codex': 'codex', 'kimi': 'kimi', 'antigravity': 'antigravity'}[event['tool']]
        review = reviews.get(event['id'], {})
        if (review.get('evidence_digest') == event['evidence_digest']
                and official_announcement(review, provider)):
            verified.append({**event, **{k: review[k] for k in ('official_verified', 'official_url', 'account_ids', 'window_ids') if k in review}})
        else:
            pending.append(event['id'])
            linked = {**event, 'official_verified': False}
            evidence = announcement_evidence(linked, provider)
            if (evidence and event.get('type') == 'usage_reset' and event.get('global') is True
                    and event.get('stage') == 'completed' and event.get('completedAt')):
                inferred.append({**linked, 'original_urls': evidence['original_urls'],
                                 'announcement_evidence_grade': evidence['grade']})
    target = root / 'latest.json'
    output = {'events': verified + inferred, 'pending_review': pending, 'source': SOURCE,
              'inferred_candidates': len(inferred), 'source_registry': source_list(),
              'sources': source_statuses,
              'fetched_at': min((s['fetched_at'] for s in source_statuses if s['fetched_at']), default=None), 'status': status}
    atomic_write_private_bytes(target, json.dumps(output, ensure_ascii=False).encode())
    return {'status': status, 'path': str(target), 'candidates': len(discovered), 'sources': source_statuses,
            'verified': len(verified), 'inferred_candidates': len(inferred),
            'pending_review': len(pending), 'error_code': error}
