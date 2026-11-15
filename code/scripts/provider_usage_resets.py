"""Classify retained subscription reset observations without upstream queries."""
import math
import re
from urllib.parse import urlparse
from scripts.provider_usage_announcement_sources import DISCOVERY_SOURCE, trusted_source


def official_announcement(event, provider):
    hosts = {'codex': {'openai.com', 'help.openai.com', 'developers.openai.com', 'learn.chatgpt.com'},
             'claude': {'anthropic.com', 'www.anthropic.com', 'claude.com', 'support.claude.com', 'code.claude.com'}}
    url = urlparse(str(event.get('official_url') or ''))
    return (event.get('official_verified') is True and url.scheme == 'https'
            and url.hostname in hosts.get(provider, set()))


def announcement_evidence(event, provider):
    """Distinguish a reviewed official source from linked third-party evidence."""
    if official_announcement(event, provider):
        return {'grade': 'official_verified', 'source': event['official_url'],
                'original_urls': [event['official_url']]}
    tools = {'codex': 'codex', 'claude': 'claude-code'}
    source = trusted_source(event.get('discovery_source'), provider)
    if (not source or provider not in tools
            or event.get('tool') != tools[provider]):
        return None
    references = [p.get('url') for name in ('posts', 'sources')
                  for p in (event.get(name) if isinstance(event.get(name), list) else []) if isinstance(p, dict)]
    references += event.get('original_urls') if isinstance(event.get('original_urls'), list) else []
    links = []
    for reference in references:
        if not isinstance(reference, str):
            continue
        try:
            url = urlparse(reference)
        except ValueError:
            continue
        if url.scheme != 'https' or url.username or url.password or url.netloc != url.hostname:
            continue
        # Keep actual post/article links, not profile pages or aggregator mirrors.
        if ((url.hostname in {'x.com', 'twitter.com', 'www.x.com', 'www.twitter.com'}
             and re.fullmatch(r'/[A-Za-z0-9_]{1,15}/status/[0-9]+/?', url.path))
                or (url.hostname in {'openai.com', 'help.openai.com', 'learn.chatgpt.com',
                                     'anthropic.com', 'www.anthropic.com', 'claude.com', 'support.claude.com'}
                    and len(url.path.strip('/').split('/')) >= 2)
                or (url.hostname == 'community.openai.com' and url.path.startswith('/t/'))):
            links.append(reference)
    if not links:
        digest = event.get('evidence_digest')
        if (not source.get('allow_unannounced_reports') or not event.get('id')
                or not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest)):
            return None
        return {'grade': 'trusted_third_party_report', 'source': source['events_url'],
                'source_id': source['id'], 'source_record_id': event['id'], 'evidence_digest': digest,
                'report_url': source['home_url'], 'original_urls': [], 'scope_text': event.get('scope')}
    return {'grade': 'third_party_with_original_links', 'source': source['events_url'], 'source_id': source['id'],
            'original_urls': sorted(set(links)), 'scope_text': event.get('scope')}


def classify_resets(records, provider, account_id, parse_time, announcements=()):
    if provider not in {'codex', 'claude', 'kimi', 'antigravity'}:
        return []
    announcements = sorted(announcements, key=lambda event: not official_announcement(event, provider))
    def moment(value):
        try:
            result = parse_time(value)
            return result if result.tzinfo is not None else None
        except (ValueError, TypeError, OverflowError, OSError):
            return None
    def number(value):
        return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 100
    def cards(snapshot, at):
        observed = moment(snapshot.get('observed_at'))
        count = snapshot.get('available_count')
        if (snapshot.get('stale') or snapshot.get('status') != 'observed'
                or type(count) is not int or count < 0 or not observed or abs((observed-at).total_seconds()) > 60):
            return None
        return {**snapshot, 'time': observed}
    samples = {}
    for record in records:
        if record.get('account_id') != account_id or record.get('provider', provider) != provider:
            continue
        sample = record.get('sample') or {}
        if sample.get('status') in {'unavailable', 'unknown'}:
            continue
        for window in sample.get('rate_limits') or []:
            at, reset = moment(window.get('observed_at')), moment(window.get('resets_at'))
            remaining = window.get('remaining_percent')
            if window.get('stale') or not at or not reset or reset <= at or not number(remaining):
                continue
            key = (str(window.get('quota_group') or ''), str(window.get('id') or ''))
            samples.setdefault(key, {}).setdefault(at, []).append((window, reset, remaining,
                cards(sample.get('refresh_cards') or {}, at), record.get('id')))
    result = []
    for (group, window_id), timeline in sorted(samples.items()):
        previous = None
        for at, copies in sorted(timeline.items()):
            if len({(v[1], v[2]) for v in copies}) != 1:
                previous = None  # conflicting observations break the chain
                continue
            w, reset, remaining, card, row_id = copies[0]
            if len({str(v[3]) for v in copies}) != 1:
                card = None
            if previous:
                before_at, before_reset, before_remaining, before_card, before_id = previous
                # Any positive recovery is evidence; consuming immediately may hide 100%.
                recovered = remaining > before_remaining
                if recovered:
                    cause, reasons, announcement, evidence = 'unknown', [], None, None
                    precision = max(1, w.get('reset_time_precision_seconds') or 60)
                    scheduled = before_at < before_reset <= at and reset > before_reset
                    delta = None
                    card_evidence = 'unknown'
                    if card and before_card and card['time'] > before_card['time']:
                        delta = card['available_count'] - before_card['available_count']
                        card_evidence = 'count_only'
                        old = before_card.get('cards')
                        new = card.get('cards')
                        if before_card['available_count'] == card['available_count'] == 0:
                            card_evidence = 'same_available_cards'
                        if isinstance(old, list) and isinstance(new, list):
                            old = {c['id']: c for c in old if c.get('id') and c.get('status') == 'available'}
                            new = {c['id']: c for c in new if c.get('id') and c.get('status') == 'available'}
                            if len(old) == before_card['available_count'] and len(new) == card['available_count']:
                                lost = set(old) - set(new)
                                if delta == -1 and len(lost) == 1 and not (set(new) - set(old)):
                                    expiry = moment(old[next(iter(lost))].get('expiresAt'))
                                    if expiry and expiry > at:
                                        card_evidence = 'one_card_removed_before_expiry'
                                    else:
                                        reasons.append('card_expiry_not_excluded')
                                elif delta == 0 and set(old) == set(new):
                                    card_evidence = 'same_available_cards'
                    non_scheduled = at < before_reset and (before_reset-at).total_seconds() > precision
                    if non_scheduled and delta == -1 and card_evidence == 'one_card_removed_before_expiry':
                        cause = 'manual_card'
                    elif non_scheduled and delta == 0 and card_evidence == 'same_available_cards':
                        for event in announcements:
                            event_at = moment(event.get('at'))
                            candidate_evidence = announcement_evidence(event, provider)
                            if (event.get('kind') == 'global_reset' and candidate_evidence
                                    and event_at and before_at < event_at <= at
                                    and (not event.get('account_ids') or account_id in event['account_ids'])
                                    and (not event.get('window_ids') or window_id in event['window_ids'])):
                                cause, announcement = 'provider_global', event.get('id')
                                evidence = candidate_evidence
                                break
                    elif scheduled and delta in (None, 0) and (reset-before_reset).total_seconds() >= 60:
                        cause = 'scheduled'
                    if cause == 'unknown':
                        reasons.append('insufficient_or_conflicting_reset_cause_evidence')
                        if delta is None:
                            reasons.append('refresh_card_observations_unavailable')
                        elif delta == 0 and card_evidence == 'same_available_cards':
                            reasons.append('matching_eligible_announcement_unavailable')
                    window_name = window_id.lower()
                    minutes = w.get('window_duration_mins')
                    if minutes is None:
                        if window_name in {'limit7d', 'seven_day', 'seven_day_sonnet', 'seven_day_opus'} or window_name.endswith('/weekly limit remaining'):
                            minutes = 10080
                        elif window_name in {'limit5h', 'five_hour'} or window_name.endswith('/five hour limit remaining'):
                            minutes = 300
                    result.append({'provider': provider, 'account_id': account_id,
                        'quota_group': group, 'window_id': window_id, 'reset_cause': cause,
                        'window_duration_mins': minutes,
                        'classification': 'inferred_from_logs', 'actual_reset_at': None,
                        'observed_change_between': [before_at.isoformat(), at.isoformat()],
                        'planned_reset_at': before_reset.isoformat(),
                        'remaining_before': before_remaining, 'remaining_after': remaining,
                        'card_count_delta': delta, 'card_evidence': card_evidence,
                        'announcement_id': announcement, 'observation_ids': [before_id, row_id],
                        'announcement_evidence': evidence,
                        'limitations': ['observation_interval_not_exact_reset_time', *reasons] +
                            (['announcement_original_not_independently_verified', 'announcement_scope_text_not_machine_verified'] if evidence and
                             evidence['grade'] != 'official_verified' else [])})
            previous = at, reset, remaining, card, row_id
    return result
