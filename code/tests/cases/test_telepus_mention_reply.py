"""Isolated Mailbox, Chatroom and iLink simulations; never send to live users."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from management_portal.telepus.mention_reply import enrich
from management_portal.telepus.router_store import RouterStore
from management_portal.telepus.weixin_store import chat_key
from tests.cases.test_telepus_notifications import reminder, mention, enable, due  # noqa: F401
from tests.cases.test_telepus_weixin import bridge, incoming  # noqa: F401
from tests.cases.telepus_bridge_support import ACTOR, MEMBER, telepus_schema  # noqa: F401
from tests.cases.permission_test_support import permissions  # noqa: F401

ROOM = 'chatroom_aaaaaaaaaaaa'


@pytest.fixture
def mentions(reminder, monkeypatch):
    r = reminder
    original = r.portal.call
    r.sources, r.posts, r.source_calls = {}, [], []
    r.read_error = r.post_error = None

    def call(actor, instruction, arguments, **kwargs):
        if instruction == 'ListChatroomMessages':
            r.source_calls.append((actor, arguments))
            if r.read_error:
                raise r.read_error
            return {'messages': deepcopy(list(r.sources.values()))}
        if instruction == 'GetChatroom':
            return {'chatroom': {'title': '开发群'}}
        if instruction == 'CreateChatroomMessage':
            if r.post_error:
                raise r.post_error
            r.posts.append((actor, deepcopy(arguments), kwargs))
            return {'message': {'message_id': 'reply'}}
        return original(actor, instruction, arguments)

    r.portal.call = call
    r.portal.residents[MEMBER] = {'title': '提醒成员'}
    monkeypatch.setattr('management_portal.telepus.notifications.public_link', lambda url: 'https://portal.example' + url)
    return r


def add(r, key, text='SOURCE PRIVATE TEXT', author='alice', attachments=()):
    r.box['items'].append(mention(key))
    r.sources[key] = {'message_id': key, 'author_id': author,
        'content_parts': [{'type': 'text', 'text': text}, *attachments]}


def deliver(r, reference, text='REPLY PRIVATE TEXT', images=False, message_id=900):
    item = {'type': 1, 'text_item': {'text': text}, 'ref_msg': reference}
    r.server.messages = [{'message_id': message_id, 'message_type': 1, 'from_user_id': 'scanner',
        'context_token': 'context', 'item_list': [item] + ([{'type': 2, 'image_item': {}}] if images else [])}]
    r.adapter.poll_once(ACTOR)


def sent_text(r):
    return r.server.sent[-1]['item_list'][0]['text_item']['text']


def test_excerpt_author_truncation_and_attachment_placeholders(mentions):
    r = mentions
    enable(r, mention=True)
    add(r, 'long', '**' + '长' * 130 + '**', author=MEMBER)
    add(r, 'media', '请看', attachments=[{'type': 'image'}, {'type': 'file', 'name': 'report.pdf'}])
    due(r)
    texts = [m['item_list'][0]['text_item']['text'] for m in r.server.sent]
    assert len(texts) == 2
    assert any('长' * 120 + '…' in t and '长' * 121 not in t for t in texts)
    assert any('alice' in t and '[图片]' in t and '[文件 report.pdf]' in t for t in texts)
    assert all('引用这条消息回复，会以你的名义发到聊天室' in t for t in texts)
    assert any(r.portal.resident(ACTOR, MEMBER)['title'] in t for t in texts)


@pytest.mark.parametrize('failure', ['denied', 'missing', 'retracted', 'network'])
def test_read_failure_preserves_pending_and_never_sends(mentions, failure, capsys):
    r = mentions
    enable(r, mention=True)
    add(r, 'one')
    if failure == 'missing':
        r.sources.clear()
    elif failure == 'retracted':
        r.sources['one']['retracted_at'] = 'now'
    else:
        r.read_error = PermissionError('SOURCE PRIVATE TEXT') if failure == 'denied' else TimeoutError('SOURCE PRIVATE TEXT')
    due(r)
    assert not r.server.sent and len(r.notices.pending(ACTOR)) == 1
    assert 'PRIVATE' not in str(capsys.readouterr())


@pytest.mark.parametrize('kind', ['svr_id', 'msg_id', 'full_text', 'excerpt'])
def test_quote_routes_as_bound_user_with_original_quote_and_safe_audit(mentions, kind, capsys):
    r = mentions
    enable(r, mention=True)
    add(r, 'one')
    due(r)
    external = r.server.server_id + 1
    ref = ({'svr_id': str(external)} if kind == 'svr_id' else
           {'message_item': {'msg_id': str(external)}} if kind == 'msg_id' else
           {'svr_id': 'unknown', 'title': sent_text(r) if kind == 'full_text' else 'SOURCE PRIVATE TEXT'})
    deliver(r, ref)
    actor, arguments, key = r.posts[0]
    assert actor == ACTOR and arguments == {'chatroom_id': ROOM, 'content_parts': [
        {'type': 'text', 'text': 'REPLY PRIVATE TEXT'}], 'quote': {'message_id': 'one'}}
    assert key['key'].startswith('weixin-chatroom:')
    assert '✅ 已发到' in sent_text(r) and not r.portal.delivered
    with r.notices.router.connect() as c:
        for table in ('notification_deliveries', 'audit_events', 'outbound_queue'):
            assert 'PRIVATE TEXT' not in str(c.execute('SELECT * FROM ' + table).fetchall())
    assert 'PRIVATE TEXT' not in str(capsys.readouterr())


def test_multiple_excerpt_matches_not_adopted_and_other_user_isolated(mentions):
    r = mentions
    enable(r, mention=True)
    add(r, 'one')
    add(r, 'two')
    due(r)
    assert r.notices.quoted('another-user', {'svr_id': str(r.server.server_id + 1)}) is None
    assert r.notices.quoted(ACTOR, {'title': 'SOURCE PRIVATE TEXT'}) is None
    deliver(r, {'title': 'SOURCE PRIVATE TEXT'})
    assert not r.posts and r.portal.delivered


@pytest.mark.parametrize('count,budget', [(4, 8), (2, 2)])
def test_summary_quote_blocked_when_large_batch_or_quota_low(mentions, count, budget):
    r = mentions
    r.adapter.budget = budget
    enable(r, mention=True)
    for n in range(count):
        add(r, str(n))
    due(r)
    assert len(r.server.sent) == 1 and '汇总提醒' in sent_text(r)
    deliver(r, {'svr_id': str(r.server.server_id + 1)})
    assert '汇总提醒' in sent_text(r) and not r.posts and not r.portal.delivered


@pytest.mark.parametrize('failure', ['denied', 'deleted', 'network'])
def test_reply_failure_does_not_fall_back_to_ama(mentions, failure):
    r = mentions
    enable(r, mention=True)
    add(r, 'one')
    due(r)
    if failure == 'deleted':
        r.sources.clear()
    else:
        r.post_error = PermissionError('secret') if failure == 'denied' else TimeoutError('secret')
    deliver(r, {'svr_id': str(r.server.server_id + 1)})
    assert '未发送到聊天室' in sent_text(r) and 'secret' not in sent_text(r)
    assert not r.posts and not r.portal.delivered


@pytest.mark.parametrize('text', ['image reply', ''])
def test_images_without_authenticated_upload_fall_back_explicitly(mentions, text):
    r = mentions
    enable(r, mention=True)
    add(r, 'one')
    due(r)
    deliver(r, {'svr_id': str(r.server.server_id + 1)}, text=text, images=True)
    assert bool(r.posts) == bool(text)
    assert '附件' in sent_text(r) and not r.portal.delivered


def test_non_reminder_quote_keeps_original_routing(mentions):
    deliver(mentions, {'svr_id': 'unknown', 'title': 'not a reminder'})
    assert not mentions.posts and mentions.portal.delivered


def test_partial_send_only_marks_delivered_items_and_retry_keeps_identity(mentions, monkeypatch):
    from management_portal.telepus import weixin_adapter
    r = mentions
    enable(r, mention=True)
    for key in ('one', 'two', 'three'):
        add(r, key, text=key)
    factory = r.adapter.client_factory
    calls = []
    broken = [True]
    def client(account):
        result = factory(account)
        send = result.send
        def send_with_failure(*args, **kwargs):
            calls.append(kwargs['client_id'])
            if broken[0] and len(calls) == 2:
                raise TimeoutError('private response')
            return send(*args, **kwargs)
        result.send = send_with_failure
        return result
    r.adapter.client_factory = client
    due(r)
    assert len(r.server.sent) == 1 and len(r.notices.pending(ACTOR)) == 2
    broken[0] = False
    original_time = weixin_adapter.time.time
    stamp = original_time() + 310
    monkeypatch.setattr(weixin_adapter.time, 'time', lambda: stamp)
    r.adapter.flush(ACTOR)
    assert len(r.server.sent) == 3 and not r.notices.pending(ACTOR)
    assert calls[1] == calls[2]


def test_reply_after_reminder_disabled_and_after_adapter_restart(mentions):
    r = mentions
    enable(r, mention=True)
    add(r, 'one')
    due(r)
    external = r.server.server_id + 1
    r.notices.update(ACTOR, mention=False)
    from management_portal.telepus.weixin_adapter import WeixinAdapter
    r.adapter = WeixinAdapter(r.adapter.router, client_factory=r.adapter.client_factory)
    deliver(r, {'svr_id': str(external)})
    assert r.posts and not r.portal.delivered


def test_source_pagination_finds_old_original(mentions):
    r = mentions
    pages = []
    def call(actor, instruction, args):
        pages.append(args.get('cursor', ''))
        if not args.get('cursor'):
            return {'messages': [], 'next_cursor': 'older'}
        return {'messages': [{'message_id': 'old', 'author_id': 'alice',
            'content_parts': [{'type': 'text', 'text': 'original'}]}]}
    r.portal.call = call
    item = enrich(r.portal, ACTOR, {'chatroom_id': ROOM, 'source_message_id': 'old'})
    assert item['excerpt'] == 'original' and pages == ['', 'older']


def test_schema_ten_upgrade_adds_only_columns_and_keeps_sent_record(mentions):
    r = mentions
    enable(r, mention=True)
    add(r, 'one')
    due(r)
    with r.notices.router.connect() as c:
        c.execute('ALTER TABLE notification_deliveries DROP COLUMN external_message_id, DROP COLUMN quote_fingerprints, DROP COLUMN is_summary')
        c.execute("UPDATE meta SET value='10' WHERE key='schema_version'")
    upgraded = RouterStore(r.notices.router.schema)
    upgraded.init_schema()
    with upgraded.connect() as c:
        row = c.execute("SELECT * FROM notification_deliveries WHERE source_message_id='one'").fetchone()
        assert row['status'] == 'sent' and row['external_message_id'] == '' and row['quote_fingerprints'] == []
    assert upgraded.get_meta('schema_version') == '11'


def test_real_kernel_default_chatroom_member_boundary(permissions, monkeypatch):
    """Default grants shared reads; membership grants posting, explicit denials still win."""
    from management_portal import web_isa, chatroom_web_isa
    from management_portal.permission_system.facts import Fact
    from management_portal.hatg.contracts import InstructionCall, InstructionContext
    from management_portal.hatg.kernel import InstructionKernel
    permissions.users('mention_alice', 'mention_bob')
    rooms = {'mention_alice': ROOM, 'mention_bob': 'chatroom_bbbbbbbbbbbb'}
    permissions.place(*(Fact('chatroom', room, creator=user, members=(user,)) for user, room in rooms.items()))
    calls = []
    def read(actor, room, **kwargs):
        calls.append((actor, room))
        return {'messages': []}
    def post(actor, room, content, **kwargs):
        calls.append((actor, room))
        return {'message': {'message_id': 'msg_aaaaaaaaaaaa'}}
    monkeypatch.setattr(chatroom_web_isa, '_chatrooms', lambda: SimpleNamespace(list_messages=read, post_message=post))
    kernel = InstructionKernel(web_isa.binding(), store=None)
    for actor, own in rooms.items():
        for instruction in ['ListChatroomMessages', 'CreateChatroomMessage']:
            for room in rooms.values():
                args = {'chatroom_id': room}
                if instruction == 'CreateChatroomMessage':
                    args['content_parts'] = [{'type': 'text', 'text': 'synthetic'}]
                result = kernel.execute(InstructionCall.from_payload({'instruction': instruction, 'arguments': args}),
                    InstructionContext(caller=actor, entry='web', channel='weixin'))
                if room == own or instruction == 'ListChatroomMessages':
                    assert result.status == 'committed', result.error
                else:
                    assert result.error['code'] in ('instruction.forbidden', 'instruction.not_found')
    assert len(calls) == 6
    permissions.deny('mention_bob', ROOM, '@viewer')
    denied = kernel.execute(InstructionCall.from_payload({'instruction': 'ListChatroomMessages',
        'arguments': {'chatroom_id': ROOM}}), InstructionContext(caller='mention_bob', entry='web', channel='weixin'))
    assert denied.error['code'] in ('instruction.forbidden', 'instruction.not_found')
    assert len(calls) == 6
