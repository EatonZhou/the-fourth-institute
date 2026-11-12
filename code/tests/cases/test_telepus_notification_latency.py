"""Timed isolated Mailbox/iLink fixtures; no live messages or user credentials."""
import time
from contextlib import nullcontext
from copy import deepcopy
from threading import Event, Lock
from types import SimpleNamespace

import pytest

from management_portal.telepus.refresh_service import NotificationScheduler
from management_portal.telepus.weixin_format import notification_text
from tests.cases.test_telepus_notifications import reminder, mention, enable  # noqa: F401
from tests.cases.test_telepus_weixin import bridge  # noqa: F401
from tests.cases.telepus_bridge_support import ACTOR, telepus_schema  # noqa: F401


@pytest.mark.parametrize('summary', [True, False])
def test_reminders_have_no_links_even_in_source_excerpt(summary):
    text = notification_text([dict(category='mention', title='群 https://portal.example',
        author='[成员](https://example.com)', excerpt='请看 [报告](/report) https://report.example/x',
        url='https://portal.example/lineage')], summary=summary)
    assert 'http' not in text.lower() and '/report' not in text
    assert '成员' in text and '报告' in text and '请看' in text


def test_new_mention_long_watch_wakes_sender_within_five_seconds(reminder, monkeypatch):
    r = reminder
    r.notices.clock = time.time
    enable(r, mention=True)
    listening, changed, sent = Event(), Event(), Event()
    timeouts, mailbox_reads = [], []
    original_call = r.portal.call
    created = [0.0]

    def watch(actor, instruction, arguments):
        timeouts.append(arguments['timeout_seconds'])
        listening.set()
        if changed.wait(2):
            changed.clear()
            return [dict(cursor='new-event', keys=['changed'])]
        return [dict(cursor='new-event', keys=[])]

    def read(actor, instruction, arguments):
        time.sleep(.02)  # Explicit, repeatable simulated API latency.
        result = original_call(actor, instruction, arguments)
        if instruction == 'GetMailbox':
            mailbox_reads.append(time.time())
        return result

    from management_portal.telepus.weixin_client import WeixinClient
    def transport(url, method, headers, body, timeout):
        if url.endswith('/sendmessage'):
            time.sleep(.02)
        return r.server(url, method, headers, body, timeout)
    monkeypatch.setattr(r.adapter, 'client_factory', lambda account: WeixinClient(account, transport=transport))

    monkeypatch.setattr(r.portal, 'first_batch', watch)
    monkeypatch.setattr(r.portal, 'call', read)
    # Observe the real delivery boundary in the fixture, not a production metric write.
    original_sent = r.notices.sent
    def delivered(group, external_id):
        original_sent(group, external_id)
        sent.set()
    monkeypatch.setattr(r.notices, 'sent', delivered)
    scheduler = NotificationScheduler(r.adapter)
    scheduler.start()
    try:
        assert listening.wait(2)
        created[0] = time.time()
        r.box['items'] = [mention('new')]
        changed.set()
        assert sent.wait(5), 'long-watch notification did not reach the simulated sender'
        assert time.time() - created[0] < 5
        assert timeouts[0] == 25
        assert len(r.server.sent) == 1
        assert len(mailbox_reads) == 1, 'discovery snapshot must be reused by the immediate sender'
        with r.notices.router.connect() as c:
            assert c.execute("SELECT count(*) FROM audit_events WHERE event_type IN ('notification_scan','notification_latency')").fetchone()[0] == 0
    finally:
        scheduler.close()
        changed.set()
        scheduler.manager.join(2)
        for worker in scheduler.workers.values():
            worker.join(2)


def test_more_than_eight_users_watch_concurrently_and_relisten():
    entered, release, done = Event(), Event(), Event()
    mutex = Lock()
    calls, flushed = {}, []
    users = [f'user{i}' for i in range(12)]
    def scan(actor, *, on_pending):
        with mutex:
            calls[actor] = calls.get(actor, 0) + 1
            if len(calls) == len(users):
                entered.set()
            if all(calls.get(user, 0) >= 2 for user in users):
                done.set()
        release.wait(2)
        on_pending(actor)
        return .01
    adapter = SimpleNamespace(store=SimpleNamespace(
        active_actors=lambda: users,
        lock=lambda actor: nullcontext()), flush=lambda actor: flushed.append(actor),
        notifications=SimpleNamespace(preferences=lambda actor: {'mention': True}, scan=scan))
    scheduler = NotificationScheduler(adapter)
    scheduler.start()
    try:
        assert entered.wait(2), 'users beyond the eighth must not queue behind a long poll'
        release.set()
        assert done.wait(2)
        assert set(flushed) == set(users)
    finally:
        scheduler.close()
        release.set()
        scheduler.manager.join(2)
        for worker in scheduler.workers.values():
            worker.join(2)


def test_watch_backoff_cursor_snapshot_required_and_thirty_second_fallback(reminder, monkeypatch):
    r = reminder
    r.box['items'] = [mention('baseline')]
    enable(r, mention=True)
    requests = []
    fail = [True]
    def watch(actor, instruction, arguments):
        requests.append(deepcopy(arguments))
        if fail[0]:
            raise ConnectionError('synthetic disconnect')
        return [dict(cursor='reconnected', keys=[], snapshot_required=True)]
    monkeypatch.setattr(r.portal, 'first_batch', watch)
    assert r.notices.scan(ACTOR) == 2
    assert r.notices.scan(ACTOR) == 2 and len(requests) == 1
    r.now[0] += 2
    fail[0] = False
    r.box['items'].append(mention('offline-new'))
    r.notices.scan(ACTOR)
    assert [row['source_message_id'] for row in r.notices.pending(ACTOR)] == ['offline-new']
    def quiet(actor, instruction, arguments):
        requests.append(deepcopy(arguments))
        r.now[0] += arguments['timeout_seconds']
        return [dict(cursor='reconnected', keys=[])]
    monkeypatch.setattr(r.portal, 'first_batch', quiet)
    r.notices.scan(ACTOR)
    assert requests[-1]['after_cursor'] == 'reconnected' and requests[-1]['timeout_seconds'] == 25
    r.box['items'].append(mention('missed-event'))
    r.notices.scan(ACTOR)
    assert requests[-1]['timeout_seconds'] == 5
    assert {row['source_message_id'] for row in r.notices.pending(ACTOR)} == {'offline-new', 'missed-event'}
