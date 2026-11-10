"""Platform quotes resolve to one Portal message without content in quote audit."""
from concurrent.futures import ThreadPoolExecutor
import pytest

from management_portal.telepus.router_store import RouterStore
from management_portal.telepus.quote_text import quote_text
from management_portal.telepus.weixin_store import chat_key
from tests.cases.test_telepus_weixin import bridge, incoming  # noqa: F401
from tests.cases.test_telepus_bridge import chat  # noqa: F401
from tests.cases.telepus_bridge_support import ACTOR, FACADE, telepus_schema  # noqa: F401


def quoted_incoming(adapter, server, reference, message_id=2):
    server.messages = [dict(message_id=message_id, from_user_id='scanner', message_type=1,
        context_token='test-context', item_list=[{'type': 1, 'text_item': {'text': 'follow up'},
                                                'ref_msg': reference}])]
    adapter.poll_once(ACTOR)


@pytest.mark.parametrize('reference', [{'svr_id': 1}, {'message_item': {'msg_id': '1'}}])
def test_weixin_quotes_own_input_by_both_identifiers(bridge, reference):
    adapter, server, portal, _ = bridge
    incoming(adapter, server, 'my first message')
    original = portal.delivered[0][2]['message_id']
    quoted_incoming(adapter, server, reference)
    assert portal.delivered[-1][2]['quote'] == {'message_id': original}
    with adapter.router.store.connect() as c:
        row = c.execute("SELECT payload_json FROM audit_events WHERE event_type='quote_received'").fetchone()
    assert row[0] == {'has_id': True, 'matched_by': 'id'}


@pytest.mark.parametrize('reference', [
    {'title': 'important words', 'partial_text': True},
    {'svr_id': 999, 'message_item': {'text_item': {'text': '**important words**'}}},
])
def test_unique_text_match_deduplicates_same_portal_reply(bridge, reference):
    adapter, server, portal, _ = bridge
    incoming(adapter, server)
    portal.answer(FACADE, portal.delivered[0][2]['message_id'], 'reply', 'The **important words** are here.')
    adapter.refresh(ACTOR)
    # The same reply shown again by /history is still one possible Portal source.
    incoming(adapter, server, '/history', 2)
    quoted_incoming(adapter, server, reference, 3)
    assert portal.delivered[-1][2]['quote'] == {'message_id': 'reply'}
    assert portal.delivered[-1][2]['content_parts'][0]['text'] == 'follow up'
    with adapter.router.store.connect() as c:
        row = c.execute("SELECT payload_json FROM audit_events WHERE event_type='quote_received'").fetchone()
    assert row[0] == {'has_id': bool(reference.get('svr_id')), 'matched_by': 'text'}


@pytest.mark.parametrize('ambiguous', [False, True])
def test_unresolved_or_ambiguous_quote_keeps_excerpt(bridge, ambiguous):
    adapter, server, portal, _ = bridge
    incoming(adapter, server, 'same words' if ambiguous else 'unrelated')
    if ambiguous:
        incoming(adapter, server, 'same words again', 2)
    quoted_incoming(adapter, server, {'svr_id': 999, 'title': 'same words'}, 3)
    assert 'quote' not in portal.delivered[-1][2]
    assert portal.delivered[-1][2]['content_parts'][0]['text'] == '> same words\n\nfollow up'
    with adapter.router.store.connect() as c:
        row = c.execute("SELECT payload_json FROM audit_events WHERE event_type='quote_received'").fetchone()
    assert row[0] == {'has_id': True, 'matched_by': 'none'}


def test_text_match_of_old_input_and_chat_isolation(bridge):
    adapter, server, portal, _ = bridge
    incoming(adapter, server, 'before **migration**')
    original = portal.delivered[0][2]['message_id']
    with adapter.router.store.connect() as c:
        c.execute('DELETE FROM message_links')
    assert adapter.router.store.linked_message_by_text('another-chat', 'migration') is None
    quoted_incoming(adapter, server, {'title': 'before migration'})
    assert portal.delivered[-1][2]['quote'] == {'message_id': original}


def test_every_segment_more_question_and_history_has_a_link(bridge):
    adapter, server, portal, _ = bridge
    incoming(adapter, server)
    original = portal.delivered[0][2]['message_id']
    portal.answer(FACADE, original, 'long-reply', ('Paragraph with content.\n\n' * 350))
    adapter.refresh(ACTOR)
    incoming(adapter, server, '/more', 2)
    incoming(adapter, server, '/history', 3)
    store, chat_id = adapter.router.store, chat_key('bot')
    with store.connect() as c:
        rows = c.execute('SELECT * FROM message_links WHERE chat_id=? AND message_id=?', (chat_id, 'long-reply')).fetchall()
    assert len(rows) >= 3
    assert all(row['excerpt'] for row in rows)
    assert any('/more' in row['excerpt'] for row in rows)
    history_id = rows[-1]['telegram_message_id']
    assert store.linked_message(chat_id, history_id)['message_id'] == 'long-reply'
    session = store.open_question_session(chat_id=chat_id, resident_id=FACADE,
        source_message_id='question-message', answer_message_id='answer-message',
        questions=[{'title': 'Which option?', 'options': ['A', 'B']}], lendable=True)
    adapter.respond(ACTOR, {'messages': [adapter.router._question_prompt(session)]})
    local = store.active_question(chat_id)['telegram_message_id']
    assert store.linked_message(chat_id, local)['message_id'] == 'question-message'


def test_telegram_can_reply_to_own_message(chat):
    chat.send('first')
    source, local = chat.portal.delivered[-1][2]['message_id'], chat.telegram_id
    chat.send('second', reply_to=local)
    assert chat.portal.delivered[-1][2]['quote'] == {'message_id': source}


def test_schema_8_upgrade_is_locked_additive_and_preserves_input(telepus_schema):
    store = RouterStore(telepus_schema)
    store.init_schema()
    store.record_input('chat', FACADE, 'old-input', 1, excerpt='kept')
    store.link_message('chat', 2, FACADE, 'old-reply')
    with store.connect() as c:
        c.execute('ALTER TABLE message_links DROP COLUMN excerpt')
        c.execute("UPDATE meta SET value='8' WHERE key='schema_version'")
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(lambda _: RouterStore(telepus_schema).init_schema(), range(3)))
    upgraded = RouterStore(telepus_schema)
    assert upgraded.input('old-input')['excerpt'] == 'kept'
    assert upgraded.linked_message('chat', 2)['message_id'] == 'old-reply'
    assert upgraded.linked_message_by_text('chat', 'kept')['message_id'] == 'old-input'
    with upgraded.connect() as c:
        assert c.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == '9'
        c.execute("UPDATE meta SET value='6' WHERE key='schema_version'")
    with pytest.raises(RuntimeError, match='migration'):
        RouterStore(telepus_schema).init_schema()


def test_markdown_matching_retains_code_and_strips_formatting():
    assert quote_text(' # Title\n\n**bold** _italic_ ~~old~~ [link](https://example.org) `x_y` ') == 'Title bold italic old link x_y'
    assert quote_text('```python\nx = 1\n```') == 'x = 1'
    assert not quote_text('** **')
