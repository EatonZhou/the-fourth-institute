"""Simulated iLink server, persistence and channel regressions."""
import json
from urllib.parse import parse_qs, urlsplit
import pytest
from management_portal.telepus.router_core import RouterCore
from management_portal.telepus.router_store import RouterStore
from management_portal.telepus.weixin_adapter import WeixinAdapter
from management_portal.telepus.weixin_binding import WeixinBinding
from management_portal.telepus.weixin_client import WeixinClient, WeixinError, crypt
from management_portal.telepus.weixin_store import chat_key
from tests.cases.telepus_bridge_support import ACTOR, FACADE, FakePortal, telepus_schema  # noqa: F401

ACCOUNT = dict(portal_actor=ACTOR, ilink_bot_id='bot', ilink_user_id='scanner',
               bot_token='test-private-token', baseurl='https://ilinkai.weixin.qq.com')


class ILinkServer:
    def __init__(self):
        self.messages, self.sent, self.requests, self.statuses = [], [], [], []
        self.reject, self.server_id, self.encrypted = 0, 2**64 - 1, b''

    def __call__(self, url, method, headers, body, timeout):
        uri = urlsplit(url)
        payload = json.loads(body) if body and headers.get('Content-Type') == 'application/json' else {}
        self.requests.append((uri, method, headers, payload))
        operation = uri.path.rsplit('/', 1)[-1]
        if operation == 'get_bot_qrcode':
            assert payload == {'local_token_list': []}
            result = dict(qrcode='test-private-qr', qrcode_img_content='https://weixin.qq.com/bind/test')
        elif operation == 'get_qrcode_status':
            result = self.statuses.pop(0)
        elif operation == 'getupdates':
            result = dict(msgs=self.messages, get_updates_buf='cursor')
            self.messages = []
        elif operation == 'sendmessage':
            assert headers['Authorization'] == 'Bearer test-private-token'
            assert payload['base_info']['channel_version'].startswith('3.0.0/tfi-telepus-')
            if self.reject:
                return json.dumps({'ret': self.reject}).encode(), {}
            self.sent.append(payload['msg'])
            result = {'message_id': self.server_id}
            self.server_id -= 1
        elif operation == 'getconfig':
            result = {'typing_ticket': 'ticket'}
        elif operation == 'getuploadurl':
            self.key = bytes.fromhex(payload['aeskey'])
            result = {'upload_param': 'test-upload'}
        elif operation == 'upload':
            self.encrypted = body
            return b'', {'x-encrypted-param': 'test-download'}
        elif operation == 'download':
            return self.encrypted, {}
        else:
            result = {}
        return json.dumps({'ret': 0, **result}).encode(), {}


@pytest.fixture
def bridge(telepus_schema):
    store = RouterStore(telepus_schema)
    store.init_schema()
    server, portal = ILinkServer(), FakePortal()
    factory = lambda account=None: WeixinClient(account, transport=server)
    adapter = WeixinAdapter(RouterCore(store=store, portal=portal), client_factory=factory)
    adapter.store.save_account(ACTOR, ACCOUNT)
    return adapter, server, portal, WeixinBinding(store, factory)


def incoming(adapter, server, text='hello', message_id=1, sender='scanner', reference=None):
    item = {'type': 1, 'text_item': {'text': text}}
    if reference:
        item['ref_msg'] = {'svr_id': reference}
    server.messages = [dict(message_id=message_id, from_user_id=sender, message_type=1,
                            context_token='test-context', item_list=[item])]
    adapter.poll_once(ACTOR)


def test_binding_pin_redirect_expiry_and_private_result(bridge):
    adapter, server, _, binding = bridge
    first = binding.start(ACTOR)
    assert 'test-private' not in json.dumps(first)
    session = first['session_id']
    assert binding.get('bob', session)['status'] == 'expired'
    server.statuses = [dict(status='need_verifycode'), dict(status='scaned_but_redirect', redirect_host='alt.weixin.qq.com'),
                       dict(status='scaned'), dict(status='expired'), dict(status='confirmed', **ACCOUNT)]
    assert binding.get(ACTOR, session)['status'] == 'need_verifycode'
    binding.get(ACTOR, session, '1234')
    assert parse_qs(server.requests[-1][0].query)['verify_code'] == ['1234']
    binding.get(ACTOR, session)
    assert server.requests[-1][0].hostname == 'alt.weixin.qq.com'
    assert 'verify_code' not in parse_qs(server.requests[-1][0].query)
    assert binding.get(ACTOR, session)['attempt'] == 2
    assert binding.get(ACTOR, session) == {'status': 'active'}
    assert adapter.store.binding(ACTOR, session) is None
    assert binding.unbind(ACTOR)['status'] == 'unbound'
    assert not adapter.store.account(ACTOR)


def test_binding_stops_after_three_codes(bridge):
    _, server, _, binding = bridge
    session = binding.start(ACTOR)['session_id']
    server.statuses = [dict(status='expired')] * 3
    binding.get(ACTOR, session)
    binding.get(ACTOR, session)
    assert binding.get(ACTOR, session) == {'status': 'expired'}


def test_two_users_bind_on_same_watchboard_and_reach_their_own_amas(telepus_schema, monkeypatch):
    """Two signed-in Users have separate QR sessions, bots, AMAs and revocation."""
    from types import SimpleNamespace
    from management_portal.telepus import weixin_binding
    from management_portal.watchboards import telepus

    store = RouterStore(telepus_schema)
    store.init_schema()
    actors = ('alice', 'bob')
    facades = dict(zip(actors, (FACADE, 'resident_bbbbbbbbbbbb')))
    accounts = {actor: {**ACCOUNT, 'portal_actor': actor, 'ilink_bot_id': f'bot-{actor}',
                       'ilink_user_id': f'scanner-{actor}'} for actor in actors}
    portals = {actor: FakePortal(actor, facades[actor]) for actor in actors}
    servers = {actor: ILinkServer() for actor in actors}
    login = ILinkServer()

    class UsersPortal:
        def __getattr__(self, method):
            return lambda actor, *args, **kwargs: getattr(portals[actor], method)(actor, *args, **kwargs)

    def factory(account=None):
        return WeixinClient(account, transport=servers[account['portal_actor']] if account else login)

    binding = WeixinBinding(store, factory)
    adapter = WeixinAdapter(RouterCore(store=store, portal=UsersPortal()), client_factory=factory)
    monkeypatch.setattr(weixin_binding, 'WeixinBinding', lambda: binding)
    monkeypatch.setattr(telepus, '_read_telepus_telegram_user_contacts', lambda: {})
    monkeypatch.setattr(telepus, 'get_portal_access_store', lambda: SimpleNamespace(
        user=lambda name: {'username': name, 'configured': name in actors, 'status': 'active'}))
    contexts = {actor: SimpleNamespace(actor=actor, resident_id='resident_4d7f422f024f') for actor in actors}
    sessions = {actor: telepus.act(contexts[actor], 'weixin-start', {})['binding']['session_id'] for actor in actors}
    assert sessions['alice'] != sessions['bob']
    # A caller cannot confirm or replace someone else's QR session.
    assert telepus.act(contexts['bob'], 'weixin-get', {'session_id': sessions['alice']})['binding']['status'] == 'expired'
    with pytest.raises(ValueError, match='Unsupported'):
        telepus.act(contexts['bob'], 'weixin-unbind', {'user_id': 'alice'})
    for actor in actors:
        login.statuses.append(dict(status='confirmed', **accounts[actor]))
        assert telepus.act(contexts[actor], 'weixin-get', {'session_id': sessions[actor]})['binding']['status'] == 'active'
        if actor == 'alice':
            other = telepus.project(contexts['bob'])
            assert other['weixin_binding']['status'] == 'unbound'
            assert other['telegram_users']['current_user'] == 'bob'
            assert other['telegram_users']['permissions']['can_manage']
    assert {account['portal_actor'] for account in adapter.store.accounts()} == set(actors)

    def receive(actor, message_id):
        servers[actor].messages = [dict(message_id=message_id, from_user_id=accounts[actor]['ilink_user_id'],
            message_type=1, context_token=f'context-{actor}',
            item_list=[{'type': 1, 'text_item': {'text': f'hello from {actor}'}}])]
        adapter.poll_once(actor)

    for actor in actors:
        receive(actor, 1)  # Same upstream ID in separate bots must not deduplicate.
        resident, _, message, _ = portals[actor].delivered[0]
        assert resident == facades[actor]
        assert message['content_parts'][0]['text'] == f'hello from {actor}'
        portals[actor].answer(resident, message['message_id'], f'reply-{actor}', f'answer for {actor}')
        adapter.refresh(actor)
        assert servers[actor].sent[-1]['to_user_id'] == accounts[actor]['ilink_user_id']
        assert f'answer for {actor}' in servers[actor].sent[-1]['item_list'][0]['text_item']['text']
        assert adapter.store.account(actor)['context_token'] == f'context-{actor}'
    assert portals['alice'].delivered[0][2]['message_id'] != portals['bob'].delivered[0][2]['message_id']
    assert telepus.act(contexts['alice'], 'weixin-unbind', {})['binding']['status'] == 'unbound'
    assert telepus.project(contexts['bob'])['weixin_binding']['status'] == 'active'
    receive('bob', 2)
    assert len(portals['bob'].delivered) == 2
    assert len(portals['alice'].delivered) == 1


def test_binding_quiet_long_poll_retries_before_web_deadline_and_recovers_confirmation(bridge):
    adapter, server, _, binding = bridge
    session = binding.start(ACTOR)['session_id']
    attempts = []

    def transport(url, method, headers, body, timeout):
        attempts.append(timeout)
        if len(attempts) == 1:
            raise WeixinError('network', 'http')
        return server(url, method, headers, body, timeout)

    binding.client_factory = lambda account=None: WeixinClient(account, transport=transport)
    waiting = binding.get(ACTOR, session)
    assert waiting['status'] == 'wait' and waiting['retryable']
    assert waiting['session_id'] == session and attempts == [5]
    server.statuses = [dict(status='confirmed', **ACCOUNT)]
    assert binding.get(ACTOR, session) == {'status': 'active'}
    # The server committed, but the browser lost the confirmation response.
    assert binding.get(ACTOR, session)['status'] == 'active'
    incoming(adapter, server, '/help')
    assert any('/help' in msg['item_list'][0]['text_item']['text'] for msg in server.sent)


def test_sender_dedup_uint64_reference_and_channel_isolation(bridge):
    adapter, server, portal, _ = bridge
    incoming(adapter, server, sender='stranger')
    assert not portal.delivered
    incoming(adapter, server)
    incoming(adapter, server)
    assert len(portal.delivered) == 1 and not server.sent
    request = portal.delivered[0][2]['message_id']
    portal.answer(FACADE, request, 'reply', 'answer', member='Helper')
    adapter.refresh(ACTOR)
    assert server.sent[0]['item_list'][0]['text_item']['text'] == '**Helper**\nanswer'
    incoming(adapter, server, 'follow up', 2, reference=2**64 - 1)
    assert portal.delivered[-1][2]['quote'] == {'message_id': 'reply'}
    assert not adapter.router.store.watched_chats()


def test_quota_pause_reopen_and_ordered_resume(bridge):
    adapter, server, portal, _ = bridge
    incoming(adapter, server)
    request = portal.delivered[0][2]['message_id']
    portal.answer(FACADE, request, 'reply', 'answer')
    server.reject = -2
    adapter.refresh(ACTOR)
    attempts = len([r for r in server.requests if r[0].path.endswith('sendmessage')])
    adapter.refresh(ACTOR)
    assert len([r for r in server.requests if r[0].path.endswith('sendmessage')]) == attempts
    assert adapter.router.store.input(request)['open']
    server.reject = 0
    incoming(adapter, server, '/where', 2)
    assert server.sent[0]['item_list'][0]['text_item']['text'] == 'answer'
    assert not adapter.store.pending(chat_key('bot'))
    assert not adapter.router.store.input(request)['open']


def test_long_response_more_and_notification_reservation(bridge):
    adapter, server, portal, _ = bridge
    incoming(adapter, server)
    portal.answer(FACADE, portal.delivered[0][2]['message_id'], 'long', 'x' * 8500)
    adapter.refresh(ACTOR)
    assert len(server.sent) == 1 and '/more' in server.sent[0]['item_list'][0]['text_item']['text']
    incoming(adapter, server, '/more', 2)
    assert len(server.sent) == 2
    adapter.budget = 3
    adapter.respond(ACTOR, {'messages': [{'text': str(i)} for i in range(6)]})
    assert '回复任意消息' in server.sent[-1]['item_list'][0]['text_item']['text']
    assert adapter.store.pending(chat_key('bot'))


def test_numbered_question_back_and_answer(bridge):
    adapter, server, portal, _ = bridge
    incoming(adapter, server)
    portal.answer(FACADE, portal.delivered[0][2]['message_id'], 'questions', 'choose', parts=[{
        'type': 'async_questions', 'answer_message_id': 'answer', 'questions': [
            {'title': 'First', 'options': ['A', 'B']}, {'title': 'Second'}]}])
    adapter.refresh(ACTOR)
    incoming(adapter, server, '2', 2)
    incoming(adapter, server, '/where', 3)
    assert adapter.router.store.active_question(chat_key('bot'))['step'] == 1
    incoming(adapter, server, '/back', 4)
    incoming(adapter, server, '1', 5)
    incoming(adapter, server, 'custom', 6)
    assert portal.delivered[-1][2]['content_parts'][1]['answers'][0]['answer'] == 'A'
    assert not adapter.router.store.active_question(chat_key('bot'))


def test_expired_stops_delivery_and_media_round_trip(bridge):
    adapter, server, _, _ = bridge
    incoming(adapter, server)
    server.reject = -14
    adapter.respond(ACTOR, {'messages': [{'text': 'failed'}]})
    assert adapter.store.account(ACTOR)['status'] == 'expired'
    assert 'test-private' not in adapter.store.account(ACTOR)['last_error']
    client = adapter.client_factory(ACCOUNT)
    item = client.upload(b'hello file', 'sample.txt', 'text/plain')
    assert client.download(item['file_item']) == b'hello file'
    assert crypt(server.encrypted, server.key, decrypt=True) == b'hello file'


def test_untrusted_redirect_rejected_without_secret(bridge):
    _, server, _, binding = bridge
    session = binding.start(ACTOR)['session_id']
    server.statuses = [dict(status='scaned_but_redirect', redirect_host='evil.example')]
    with pytest.raises(WeixinError, match='invalid_host'):
        binding.get(ACTOR, session)


def test_pending_delivery_survives_adapter_restart_and_network_retry(bridge):
    adapter, server, portal, _ = bridge
    incoming(adapter, server)
    portal.answer(FACADE, portal.delivered[0][2]['message_id'], 'retry', 'retry content')
    original = adapter.client_factory
    def unavailable(account=None):
        client = original(account)
        client.send = lambda *a, **k: (_ for _ in ()).throw(WeixinError('network', 'http'))
        return client
    adapter.client_factory = unavailable
    adapter.refresh(ACTOR)
    assert adapter.store.budget(chat_key('bot')) == (0, False)
    restarted = WeixinAdapter(RouterCore(store=RouterStore(adapter.router.store.schema), portal=portal), client_factory=original)
    restarted.refresh(ACTOR)
    assert server.sent[0]['item_list'][0]['text_item']['text'] == 'retry content'
    assert not restarted.store.pending(chat_key('bot'))


def test_history_full_file_and_skip(bridge):
    adapter, server, portal, _ = bridge
    incoming(adapter, server)
    portal.answer(FACADE, portal.delivered[0][2]['message_id'], 'answer', 'history ' * 180)
    incoming(adapter, server, '/history', 2)
    assert '/full 1' in server.sent[-1]['item_list'][0]['text_item']['text']
    incoming(adapter, server, '/full 1', 3)
    assert any(('history ' * 180).strip() in i['item_list'][0].get('text_item', {}).get('text', '') for i in server.sent)
    token = adapter.router.store.create_attachment_delivery(chat_id=chat_key('bot'), telegram_user_id='scanner',
        portal_actor=ACTOR, source_message_id='answer', attachment={'filename': 'a.txt'})
    adapter.router.store.set_meta('weixin-control:' + chat_key('bot') + ':/file:1', 'att:' + token)
    incoming(adapter, server, '/file 1', 4)
    assert server.sent[-1]['item_list'][0]['type'] == 4
    adapter.router.store.open_question_session(chat_id=chat_key('bot'), resident_id=FACADE, source_message_id='q',
        answer_message_id='qa', questions=[{'title': 'Q'}], lendable=False)
    incoming(adapter, server, '/skip', 5)
    assert portal.closed == [(FACADE, 'q')]


def test_member_credentials_cannot_be_approved_by_number_or_free_text(bridge):
    adapter, server, portal, _ = bridge
    incoming(adapter, server)
    adapter.router.store.open_question_session(chat_id=chat_key('bot'), resident_id=FACADE, source_message_id='q',
        answer_message_id='qa', questions=[{'title': 'Approve', 'credentials': [{'a': '@admin', 'resource_id': FACADE}]}], lendable=False)
    incoming(adapter, server, '同意交出这些权限', 2)
    assert len(portal.delivered) == 1
    incoming(adapter, server, '1', 3)
    assert portal.delivered[-1][3]['credentials'] is None
    assert portal.delivered[-1][2]['content_parts'][1]['answers'][0]['answer'] == '不交出'


def test_rebinding_preserves_pending_but_does_not_reuse_external_id_mapping(bridge):
    adapter, server, portal, binding = bridge
    incoming(adapter, server)
    adapter.store.enqueue(chat_key('bot'), {'text': 'pending'})
    session = binding.start(ACTOR)['session_id']
    server.statuses = [{'status': 'confirmed', **ACCOUNT, 'ilink_bot_id': 'newbot'}]
    assert binding.get(ACTOR, session)['status'] == 'active'
    assert adapter.store.pending(chat_key('newbot'))
    incoming(adapter, server, 'new message', 1)
    assert len(portal.delivered) == 2
    assert server.sent[0]['item_list'][0]['text_item']['text'] == 'pending'


def test_additive_upgrade_preserves_existing_chat_and_input(telepus_schema):
    from concurrent.futures import ThreadPoolExecutor
    store = RouterStore(telepus_schema)
    store.init_schema()
    store.upsert_chat_state('123', portal_actor=ACTOR, telegram_user_id='12345')
    store.record_input('123', FACADE, 'input', 7)
    with store.connect() as c:
        c.execute("UPDATE meta SET value='7' WHERE key='schema_version'")
        for table in ('chat_state', 'telegram_inputs', 'delivered_messages', 'message_links', 'question_sessions',
                      'callback_tokens', 'attachment_deliveries', 'audit_events', 'processed_updates'):
            c.execute(f'ALTER TABLE {table} DROP COLUMN channel')
        c.execute('ALTER TABLE chat_state DROP COLUMN send_count, DROP COLUMN send_blocked')
        c.execute('DROP TABLE weixin_accounts, weixin_bindings, outbound_queue, platform_message_ids')
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(lambda _: RouterStore(telepus_schema).init_schema(), range(3)))
    upgraded = RouterStore(telepus_schema)
    assert upgraded.get_chat_state('123')['portal_actor'] == ACTOR
    assert upgraded.input('input')['telegram_message_id'] == 7
    with upgraded.connect() as c:
        assert c.execute("SELECT channel FROM chat_state WHERE chat_id='123'").fetchone()[0] == 'telegram'
        assert c.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == '8'
        assert c.execute('SELECT count(*) FROM weixin_accounts').fetchone()[0] == 0


def test_reply_images_are_sent_as_media_and_inbound_media_is_decrypted(bridge, monkeypatch, tmp_path):
    import management_portal.telepus.weixin_adapter as implementation
    adapter, server, portal, _ = bridge
    incoming(adapter, server)
    monkeypatch.setattr(portal, 'attachments', lambda *args: [{'filename': 'a.png', 'content_type': 'image/png'}])
    monkeypatch.setattr(portal, 'read_attachment', lambda *args: {'filename': 'a.png', 'content_type': 'image/png', 'data': b'image bytes'})
    portal.answer(FACADE, portal.delivered[0][2]['message_id'], 'image', '![image](workspace_111111111111/file/a.png)')
    adapter.refresh(ACTOR)
    assert server.sent[-1]['item_list'][0]['type'] == 2
    media = server.sent[-1]['item_list'][0]
    monkeypatch.setattr(implementation, 'portal_path', lambda area: tmp_path)
    server.messages = [dict(message_id=2, from_user_id='scanner', message_type=1,
                           context_token='test-context', item_list=[media])]
    adapter.poll_once(ACTOR)
    assert '[image:' in portal.delivered[-1][2]['content_parts'][0]['text']
    assert next(tmp_path.rglob('*.jpg')).read_bytes() == b'image bytes'
