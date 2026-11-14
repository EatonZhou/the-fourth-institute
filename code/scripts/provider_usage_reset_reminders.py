"""Notify current Portal administrators of cached reset cards expiring within 7 days.

No native queries, card redemption or model calls. Also run independently with
--resident-id to check the current saved account snapshot without parsing usage.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def expiring_cards(accounts, *, now):
    end = now + timedelta(days=7)
    found = {}
    for account in accounts:
        snapshot = account.get('refresh_cards') or {}
        if snapshot.get('available_count') == 0:
            continue
        for card in snapshot.get('cards') or []:
            if not isinstance(card, dict):
                continue
            if card.get('status') != 'available':
                continue
            value = card.get('expiresAt')
            try:
                if isinstance(value, bool) or not isinstance(value, (str, int, float)):
                    continue
                expiry = (datetime.fromtimestamp(value, timezone.utc) if isinstance(value, (int, float))
                          else datetime.fromisoformat(value.replace('Z', '+00:00')))
                if expiry.tzinfo is None or not now < expiry <= end:
                    continue
            except (ValueError, TypeError, OverflowError, OSError):
                continue
            row = {'provider': account['provider'], 'account_id': account['account_id'],
                   'card_id': card.get('id'), 'title': ' '.join(str(card.get('title') or card.get('resetType') or '刷新卡').split())[:120],
                   'expires_at': expiry.astimezone(timezone.utc).isoformat(),
                   'stale': bool(snapshot.get('stale')) or account.get('status') in ('unknown', 'unavailable')}
            key = (row['provider'], row['account_id'], row['card_id'] or row['title'], row['expires_at'])
            found[key] = row
    return sorted(found.values(), key=lambda row: (row['expires_at'], row['provider'], row['account_id'], str(row['card_id'])))


def notify_expiring_resets(call, accounts, *, now=None):
    now = now or datetime.now(timezone.utc)
    cards = expiring_cards(accounts, now=now)
    if not cards:
        return {'status': 'empty', 'cards': 0, 'recipients': 0, 'created': 0}
    users = call('ListUsers', status='active').get('users') or []
    admins = sorted({u['username'] for u in users if u.get('category') == 'portal_admin' and u.get('status') == 'active'})
    zone = ZoneInfo('Asia/Hong_Kong')
    message = '\n'.join(f"{r['account_id']} · {r['title']} · {datetime.fromisoformat(r['expires_at']).astimezone(zone):%Y-%m-%d %H:%M %Z}"
                        + ('（上次读数）' if r['stale'] else '') for r in cards)
    # Stable for the same day's same facts; observed_at and changing time-to-live
    # do not turn repeated reports into new messages. Per-user IDs let newly
    # added administrators receive the notice without resending to everyone.
    identity = [{k: r[k] for k in ('provider', 'account_id', 'card_id', 'expires_at')} for r in cards]
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]
    day = now.astimezone(timezone.utc).date().isoformat()
    created = 0
    for username in admins:
        user_key = hashlib.sha256(username.encode()).hexdigest()[:16]
        result = call('CreateNotification', title='刷新卡将在 7 天内到期', message=message,
                      recipients=[username], severity='warning',
                      notification_id=f'reset_expiry_{day}_{digest}_{user_key}')
        created += int(result.get('created') is True)
    return {'status': 'notified' if admins else 'no_administrators', 'cards': len(cards),
            'recipients': len(admins), 'created': created}


def main(argv=None):
    import argparse
    import uuid
    from scripts.resident_hatg_instruction import execute
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resident-id', required=True)
    args = parser.parse_args(argv)
    def call(name, **arguments):
        response = execute(args.resident_id, {'schema': 'tfi.hatg-instruction.v2', 'instruction': name,
                           'arguments': arguments, 'idempotency_key': 'reset-reminder:' + uuid.uuid4().hex},
                           request_timeout=30, max_attempts=1)
        return response['result']
    accounts = call('ListProviderAccounts', refresh=False).get('accounts') or []
    print(json.dumps(notify_expiring_resets(call, accounts), ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
