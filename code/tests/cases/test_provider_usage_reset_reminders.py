from datetime import datetime, timedelta, timezone
from scripts.provider_usage_reset_reminders import expiring_cards, notify_expiring_resets

NOW = datetime(2026, 11, 14, tzinfo=timezone.utc)


def account(cards, **extra):
    return {'provider': 'codex', 'account_id': 'codex_one',
            'refresh_cards': {'available_count': len(cards), 'cards': cards}, **extra}


def card(days=1, **extra):
    return {'id': 'c', 'status': 'available', 'title': 'Full reset',
            'expiresAt': (NOW + timedelta(days=days)).timestamp(), **extra}


def test_seven_day_window_excludes_used_expired_unknown_and_bad_expiries():
    rows = [card(0, id='expired'), card(7, id='edge'), card(7.01, id='late'),
            card(1, status='redeemed'), card(1, expiresAt=None), card(1, expiresAt=True),
            card(1, expiresAt=[]), card(1, expiresAt='2026-11-15T00:00:00'),
            card(1, expiresAt='2026-11-15T00:00:00Z', id='iso')]
    result = expiring_cards([account(rows)], now=NOW)
    assert [r['card_id'] for r in result] == ['iso', 'edge']


def test_no_cards_makes_no_user_lookup_or_notification():
    def forbidden(*args, **kwargs):
        raise AssertionError('Empty expiry checks must make no calls')
    assert notify_expiring_resets(forbidden, [account([card(8)])], now=NOW)['status'] == 'empty'


def test_only_active_administrators_receive_deduplicated_line_items():
    calls, existing = [], set()
    def call(name, **args):
        calls.append((name, args))
        if name == 'ListUsers':
            assert args == {'status': 'active'}
            return {'users': [{'username': 'a', 'category': 'portal_admin', 'status': 'active'},
                              {'username': 'b', 'category': 'portal_admin', 'status': 'active'},
                              {'username': 'disabled', 'category': 'portal_admin', 'status': 'disabled'},
                              {'username': 'member', 'category': 'member', 'status': 'active'}]}
        assert name == 'CreateNotification'
        key = args['notification_id']
        created = key not in existing
        existing.add(key)
        return {'created': created}
    data = [account([card(2, title='Weekly\nreset'), card(1, id='other')])]
    assert notify_expiring_resets(call, data, now=NOW)['created'] == 2
    notices = [a for n, a in calls if n == 'CreateNotification']
    assert [a['recipients'] for a in notices] == [['a'], ['b']]
    assert len(notices[0]['message'].splitlines()) == 2
    assert '2026-11-15 08:00 HKT' in notices[0]['message']
    assert 'Weekly reset' in notices[0]['message']
    assert notify_expiring_resets(call, data, now=NOW + timedelta(hours=1))['created'] == 0
    assert notify_expiring_resets(call, data, now=NOW + timedelta(days=1))['created'] == 2


def test_duplicate_cards_and_retained_readings_are_explicit():
    data = [account([card(), card()], status='unavailable')]
    result = expiring_cards(data, now=NOW)
    assert len(result) == 1 and result[0]['stale'] is True


def test_portal_usage_entry_reuses_snapshot_and_runs_reminders(monkeypatch, capsys):
    from scripts import provider_weekly_usage, resident_hatg_instruction
    calls = []
    accounts = [account([card(expiresAt=(datetime.now(timezone.utc) + timedelta(days=3)).timestamp())])]
    def execute(resident_id, request, **kwargs):
        assert resident_id == 'resident-test'
        name, args = request['instruction'], request['arguments']
        calls.append(name)
        if name == 'ListProviderAccounts':
            assert args == {'refresh': False}
            return {'result': {'accounts': accounts}}
        if name == 'ListUsers':
            return {'result': {'users': [{'username': 'admin', 'category': 'portal_admin', 'status': 'active'}]}}
        assert name == 'CreateNotification'
        return {'result': {'created': True}}
    def collect(call, *args, **kwargs):
        assert kwargs['accounts'] == accounts
        return {'accounts': []}
    monkeypatch.setattr(resident_hatg_instruction, 'execute', execute)
    monkeypatch.setattr(provider_weekly_usage, 'collect_portal', collect)
    assert provider_weekly_usage.main(['--resident-id', 'resident-test', '--isa-export',
                                      '--since', '2026-11-01T00:00:00Z', '--until', '2026-11-02T00:00:00Z']) == 0
    assert calls == ['ListProviderAccounts', 'ListUsers', 'CreateNotification']
    assert '"created": 1' in capsys.readouterr().out
