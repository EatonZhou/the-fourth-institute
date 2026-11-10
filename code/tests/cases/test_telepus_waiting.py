"""Waiting notices survive refresh/restart and respect channel delivery budgets."""
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from management_portal.telepus import waiting
from management_portal.telepus.telegram_adapter import TelegramAdapter
from management_portal.telepus.weixin_adapter import WeixinAdapter
from management_portal.telepus.weixin_store import chat_key
from tests.cases.telepus_bridge_support import ACTOR, FACADE, telepus_schema  # noqa: F401
from tests.cases.test_telepus_bridge import Chat
from tests.cases.test_telepus_weixin import bridge, incoming  # noqa: F401


@pytest.fixture
def clock(monkeypatch):
    now = [time.time()]
    monkeypatch.setattr(waiting.time, 'time', lambda: now[0])
    monkeypatch.setattr(waiting, 'setting', lambda key, default: 300)
    return now


def test_wait_notice_once_across_restart_and_reset_by_new_message(telepus_schema, clock):
    chat = Chat(telepus_schema)
    chat.send('one')
    sent = []
    send = lambda text, generation: sent.append(text) or True
    clock[0] += 299
    assert not chat.core.waiting.send_if_due('c1', send)
    clock[0] += 2
    assert chat.core.waiting.send_if_due('c1', send)
    restarted = waiting.WaitingNotices(chat.store)
    assert not restarted.send_if_due('c1', send)
    chat.send('two')
    clock[0] += 299
    assert not restarted.send_if_due('c1', send)
    clock[0] += 2
    assert restarted.send_if_due('c1', send)
    assert sent == [waiting.STILL_WORKING] * 2


def test_telegram_refresh_sends_notice_and_reply_suppresses_next_notice(telepus_schema, clock):
    chat = Chat(telepus_schema)
    sent = []

    class Client:
        def send_message(self, **kwargs):
            sent.append(kwargs['text'])
            return {'result': {'message_id': len(sent)}}

    adapter = TelegramAdapter(chat.core, Client())
    chat.send('one')
    clock[0] += 301
    adapter.refresh_routes()
    adapter.refresh_routes()
    assert sent == [waiting.STILL_WORKING]
    chat.send('two')
    mid = chat.portal.delivered[0][2]['message_id']
    chat.portal.answer(FACADE, mid, 'reply_one', 'answer')
    adapter.refresh_routes()
    clock[0] += 301
    adapter.refresh_routes()
    assert sent.count(waiting.STILL_WORKING) == 1
    assert any('answer' in text for text in sent)


def test_weixin_notice_spends_budget_once_and_survives_restart(bridge, clock):
    adapter, server, _, _ = bridge
    incoming(adapter, server)
    chat = chat_key('bot')
    before = adapter.store.budget(chat)[0]
    clock[0] += 301
    adapter.refresh(ACTOR)
    restarted = WeixinAdapter(adapter.router, client_factory=adapter.client_factory)
    restarted.refresh(ACTOR)
    assert sum(message['item_list'][0].get('text_item', {}).get('text') == waiting.STILL_WORKING for message in server.sent) == 1
    assert adapter.store.budget(chat)[0] == before + 1
    assert not adapter.store.pending(chat)


def test_weixin_reserve_and_rejected_notice_are_never_queued(bridge, clock):
    adapter, server, _, _ = bridge
    incoming(adapter, server)
    chat = chat_key('bot')
    while adapter.store.budget(chat)[0] < adapter.budget - 1:
        adapter.store.budget(chat, spent=True)
    before = len(server.sent)
    clock[0] += 301
    adapter.refresh(ACTOR)
    assert len(server.sent) == before
    assert not adapter.store.pending(chat)
    incoming(adapter, server, message_id=2)
    clock[0] += 301
    server.reject = -2
    adapter.refresh(ACTOR)
    assert adapter.store.budget(chat)[1]
    requests = len([x for x in server.requests if x[0].path.endswith('sendmessage')])
    adapter.refresh(ACTOR)
    assert len([x for x in server.requests if x[0].path.endswith('sendmessage')]) == requests
    assert not adapter.store.pending(chat)


def test_configured_delay_concurrent_refresh_and_closed_inputs(telepus_schema, clock, monkeypatch):
    chat = Chat(telepus_schema)
    chat.send('one')
    monkeypatch.setattr(waiting, 'setting', lambda key, default: 10)
    clock[0] += 11
    sent = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(chat.core.waiting.send_if_due, 'c1', lambda text, generation: sent.append(text) or True)
                   for _ in range(2)]
        assert sorted(future.result() for future in futures) == [False, True]
    assert sent == [waiting.STILL_WORKING]
    chat.send('two')
    for row in chat.store.open_inputs('c1'):
        chat.store.close_input(row['message_id'])
    clock[0] += 11
    assert not chat.core.waiting.send_if_due('c1', lambda *args: pytest.fail('closed input'))
