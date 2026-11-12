"""Telepus durable records exclude polling diagnostics and expired callback payloads.

:design: decision_c41e1310c463
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from tests.cases.test_telepus_notifications import reminder, mention, enable, due  # noqa: F401
from tests.cases.test_telepus_weixin import bridge  # noqa: F401
from tests.cases.telepus_bridge_support import ACTOR, telepus_schema  # noqa: F401


def test_scan_and_send_do_not_persist_diagnostics_or_rewrite_ready_baseline(reminder):
    r = reminder
    enable(r, mention=True)
    with r.notices.router.connect() as c:
        baseline = c.execute('SELECT xmin::text FROM notification_preferences WHERE portal_actor=?', (ACTOR,)).fetchone()[0]
    for _ in range(3):
        r.notices.scan(ACTOR)
    r.box['items'] = [mention('new')]
    due(r)
    assert len(r.server.sent) == 1
    with r.notices.router.connect() as c:
        assert c.execute("SELECT count(*) FROM audit_events WHERE event_type IN ('notification_scan','notification_latency')").fetchone()[0] == 0
        assert c.execute('SELECT xmin::text FROM notification_preferences WHERE portal_actor=?', (ACTOR,)).fetchone()[0] == baseline
        assert c.execute("SELECT count(*) FROM notification_deliveries WHERE status='sent'").fetchone()[0] == 1
    due(r)
    assert len(r.server.sent) == 1


def test_unchanged_cursor_does_not_rewrite_meta(reminder):
    store = reminder.notices.router
    store.set_meta('test-cursor', 'cursor-one')
    with store.connect() as c:
        before = c.execute("SELECT xmin::text FROM meta WHERE key='test-cursor'").fetchone()[0]
    store.set_meta('test-cursor', 'cursor-one')
    with store.connect() as c:
        assert c.execute("SELECT xmin::text FROM meta WHERE key='test-cursor'").fetchone()[0] == before
    store.set_meta('test-cursor', 'cursor-two')
    assert store.get_meta('test-cursor') == 'cursor-two'


def test_full_reads_keep_cadence_in_memory_and_restart_revalidates(reminder):
    from management_portal.telepus.notifications import Notifications
    r = reminder
    r.box['items'] = [mention('baseline')]
    enable(r, mention=True)
    store = r.notices.router
    key = 'notification-watch:' + ACTOR
    with store.connect() as c:
        before = c.execute('SELECT xmin::text FROM meta WHERE key=?', (key,)).fetchone()[0]
    for _ in range(3):
        r.now[0] += 31
        r.notices.scan(ACTOR)
    with store.connect() as c:
        assert c.execute('SELECT xmin::text FROM meta WHERE key=?', (key,)).fetchone()[0] == before
    assert 'read_at' not in store.get_meta(key)
    restarted = Notifications(store, r.portal, clock=lambda: r.now[0])
    r.calls.clear()
    restarted.scan(ACTOR)
    assert r.calls == ['GetMailbox']
    assert not restarted.pending(ACTOR)


def test_callback_expiry_is_bounded_and_keeps_live_payloads_and_receipts(reminder):
    store = reminder.notices.router
    past = datetime.now(timezone.utc) - timedelta(days=1)
    expired = [store.create_callback_token('chat', {'kind': 'expired'}) for _ in range(3)]
    live = store.create_callback_token('chat', {'kind': 'live'})
    store.append_audit('question_answer', payload={'status': 'sent'})
    with store.connect() as c:
        for token in expired:
            c.execute('UPDATE callback_tokens SET expires_at=? WHERE token=?', (past, token))
    assert store.prune_expired_callback_tokens(limit=2) == 2
    assert store.prune_expired_callback_tokens(limit=2) == 1
    assert store.prune_expired_callback_tokens(limit=2) == 0
    assert store.get_callback_payload('chat', live) == {'kind': 'live'}
    assert store.get_callback_payload('other', live) is None
    assert all(store.get_callback_payload('chat', token) is None for token in expired)
    with store.connect() as c:
        assert c.execute("SELECT count(*) FROM audit_events WHERE event_type='question_answer'").fetchone()[0] == 1


@pytest.mark.parametrize('fail', [False, True])
def test_refresh_maintains_expiry_once_per_minute_without_blocking_replies(monkeypatch, fail):
    from management_portal.telepus import cli, refresh_service, weixin_adapter
    now = [0]
    ticks = iter([3, 60, 63])
    cleanups, replies, diagnostics = [], [], []

    def cleanup():
        cleanups.append(now[0])
        if fail:
            raise OSError('synthetic failure')

    def refresh(**kwargs):
        replies.append(now[0])
        return {}

    monkeypatch.setattr(cli, 'time', SimpleNamespace(monotonic=lambda: now[0], sleep=lambda _: now.__setitem__(0, next(ticks))))
    monkeypatch.setattr(cli, 'TelegramClient', lambda: object())
    monkeypatch.setattr(cli, 'telegram_adapter', lambda _: SimpleNamespace(router=SimpleNamespace(store=SimpleNamespace(prune_expired_callback_tokens=cleanup)), refresh_routes=refresh))
    monkeypatch.setattr(weixin_adapter, 'WeixinAdapter', lambda: SimpleNamespace(store=SimpleNamespace(accounts=lambda: []), notifications=SimpleNamespace(diagnostic=lambda *args: diagnostics.append(args))))
    monkeypatch.setattr(refresh_service, 'NotificationScheduler', lambda _: SimpleNamespace(start=lambda: None, close=lambda: None))
    assert cli.run_telegram_refresh_daemon(['--max-iterations', '4']) == 0
    assert cleanups == [0, 60]
    assert replies == [0, 3, 60, 63]
    assert len(diagnostics) == (2 if fail else 0)
