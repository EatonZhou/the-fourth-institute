"""Classify retained subscription reset observations without upstream queries."""
import math
from urllib.parse import urlparse


def official_announcement(event, provider):
    hosts = {'codex': {'openai.com', 'help.openai.com', 'developers.openai.com', 'learn.chatgpt.com'},
             'claude': {'anthropic.com', 'www.anthropic.com', 'claude.com', 'support.claude.com', 'code.claude.com'}}
    url = urlparse(str(event.get('official_url') or ''))
    return (event.get('official_verified') is True and url.scheme == 'https'
            and url.hostname in hosts.get(provider, set()))


def classify_resets(records, provider, account_id, parse_time, announcements=()):
    if provider not in {'codex', 'claude', 'kimi', 'antigravity'}:
        return []
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
                    cause, reasons, announcement = 'unknown', [], None
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
                            if (event.get('kind') == 'global_reset' and official_announcement(event, provider)
                                    and event_at and before_at < event_at <= at
                                    and (not event.get('account_ids') or account_id in event['account_ids'])
                                    and (not event.get('window_ids') or window_id in event['window_ids'])):
                                cause, announcement = 'provider_global', event.get('id')
                                break
                    elif scheduled and delta in (None, 0) and (reset-before_reset).total_seconds() >= 60:
                        cause = 'scheduled'
                    if cause == 'unknown':
                        reasons.append('insufficient_or_conflicting_reset_cause_evidence')
                        if delta is None:
                            reasons.append('refresh_card_observations_unavailable')
                        elif delta == 0 and card_evidence == 'same_available_cards':
                            reasons.append('matching_verified_announcement_unavailable')
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
                        'limitations': ['observation_interval_not_exact_reset_time', *reasons]})
            previous = at, reset, remaining, card, row_id
    return result
