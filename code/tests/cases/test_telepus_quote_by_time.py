"""Weixin quotes that identify the quoted message only by time (ET's client, 2026-11-28).

The quote has ``message_item`` with ``msg_id="0"``, ``type=0`` and the quoted
message's ``create_time_ms``/``update_time_ms``; no ``svr_id``, title or text.
"""
import pytest

from management_portal.telepus import weixin_quote
from management_portal.telepus.router_core import RouterCore
from management_portal.telepus.weixin_adapter import WeixinAdapter
from management_portal.telepus.weixin_quote import UNREADABLE, quote_id
from tests.cases.test_telepus_weixin import bridge  # noqa: F401
from tests.cases.telepus_bridge_support import ACTOR, FACADE, telepus_schema  # noqa: F401

T0 = 1_795_000_000_000


@pytest.fixture
def clock(monkeypatch):
    now = [T0]
    monkeypatch.setattr(weixin_quote, 'now_ms', lambda: now[0], raising=False)
    return now


def send(adapter, server, text, message_id, at_ms, quote=None):
    item = {'type': 1, 'text_item': {'text': text}}
    if quote is not None:
        item['ref_msg'] = quote
    server.messages = [dict(message_id=message_id, from_user_id='scanner', message_type=1, create_time_ms=at_ms,
                            context_token='test-context', item_list=[item])]
    adapter.poll_once(ACTOR)


def time_only(at_ms):
    """The shape recorded for ET's quote: no usable ID and no text."""
    return {'message_item': {'at_bot_username_list': [], 'button_item_list': [], 'create_time_ms': at_ms,
                             'is_completed': True, 'msg_id': '0', 'type': 0, 'update_time_ms': at_ms}}


def last_text(portal):
    return portal.delivered[-1][2]['content_parts'][0]['text']


def last_quote_audit(adapter):
    with adapter.router.store.connect() as c:
        return c.execute("SELECT payload_json FROM audit_events WHERE event_type='quote_received' "
                         "ORDER BY event_id DESC LIMIT 1").fetchone()[0]


def test_zero_ids_are_not_ids(bridge):
    assert quote_id({'message_item': {'msg_id': '0'}}) == ''
    assert quote_id({'svr_id': 0, 'message_item': {'msg_id': ''}}) == ''
    assert quote_id({'svr_id': ' 12 '}) == '12'
    adapter, server, portal, _ = bridge
    send(adapter, server, 'question', 1, T0)
    send(adapter, server, 'follow up', 2, T0 + 60_000, time_only(T0 + 30_000))
    with adapter.router.store.connect() as c:
        assert not c.execute("SELECT 1 FROM platform_message_ids WHERE external_id='0'").fetchone()
    audit = last_quote_audit(adapter)
    assert audit['has_id'] is False and audit['id_valid'] is False and audit['matched_by'] == 'none'


def test_time_matches_portal_linked_reply(bridge, clock):
    adapter, server, portal, _ = bridge
    send(adapter, server, 'hello', 1, T0)
    portal.answer(FACADE, portal.delivered[0][2]['message_id'], 'reply', 'Portal reply text')
    clock[0] = T0 + 5_000
    adapter.refresh(ACTOR)
    send(adapter, server, '这是什么', 2, T0 + 60_000, time_only(T0 + 5_800))
    assert portal.delivered[-1][2]['quote'] == {'message_id': 'reply'}
    assert last_text(portal) == '这是什么'
    audit = last_quote_audit(adapter)
    assert audit['matched_by'] == 'time' and audit['time_delta_ms'] == 800 and audit['quote_time_ms'] == T0 + 5_800


def test_time_matches_local_command_reply_and_own_text_after_restart(bridge, clock):
    adapter, server, portal, _ = bridge
    clock[0] = T0 + 1_000
    send(adapter, server, '/help', 1, T0)  # local command and local reply; no Portal copy
    # A publish restarts the router: a new adapter over the same bridge database.
    restarted = WeixinAdapter(RouterCore(store=adapter.router.store, portal=portal), client_factory=getattr(adapter, '_client_factory', adapter.client_factory))
    send(restarted, server, '这是什么', 2, T0 + 60_000, time_only(T0 + 1_200))
    text = last_text(portal)
    assert 'quote' not in portal.delivered[-1][2]
    assert text.startswith('> ') and '/notify' in text and text.endswith('\n\n这是什么')
    send(restarted, server, '那这个呢', 3, T0 + 90_000, time_only(T0 - 400))
    assert last_text(portal) == '> /help\n\n那这个呢'
    audit = last_quote_audit(restarted)
    assert audit['matched_by'] == 'time' and audit['text_source'] == 'record' and '/help' not in str(audit)


def test_time_outside_tolerance_or_tied_gives_placeholder(bridge, clock):
    adapter, server, portal, _ = bridge
    send(adapter, server, 'first', 1, T0)
    send(adapter, server, 'second', 2, T0 + 2_000)
    send(adapter, server, '这是什么', 3, T0 + 60_000, time_only(T0 + 1_000))  # equally close to both
    assert last_text(portal) == '> ' + UNREADABLE + '\n\n这是什么'
    send(adapter, server, '这是什么', 4, T0 + 90_000, time_only(T0 + 5_500))  # 3.5 s from the nearest
    assert last_text(portal) == '> ' + UNREADABLE + '\n\n这是什么'
    assert last_quote_audit(adapter)['matched_by'] == 'none'


def test_record_is_pruned_after_seven_days(bridge):
    adapter, server, portal, _ = bridge
    send(adapter, server, '/where', 1, T0)
    with adapter.router.store.connect() as c:
        assert c.execute('SELECT count(*) FROM weixin_messages').fetchone()[0] >= 2
        c.execute("UPDATE weixin_messages SET created_at=CURRENT_TIMESTAMP - INTERVAL '8 days'")
    assert adapter.quotes.prune() >= 2
    with adapter.router.store.connect() as c:
        assert c.execute('SELECT count(*) FROM weixin_messages').fetchone()[0] == 0
