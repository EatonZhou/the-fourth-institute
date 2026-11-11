"""Mailbox/iLink simulation; no real accounts, chatrooms or Weixin sends."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from management_portal.telepus.notifications import Notifications, settings
from management_portal.telepus.router_store import RouterStore
from management_portal.telepus.weixin_store import chat_key
from tests.cases.test_telepus_weixin import bridge, incoming, ACCOUNT  # noqa: F401
from tests.cases.telepus_bridge_support import ACTOR, FACADE, MEMBER, telepus_schema  # noqa: F401
from tests.cases.permission_test_support import permissions  # noqa: F401


def mention(message):
    return dict(kind='chatroom', chatroom_id='chatroom_aaaaaaaaaaaa', title='开发群',
                url='/chatroom?id=chatroom_aaaaaaaaaaaa', unread_mentions=[{'message_id': message}],
                text='PRIVATE MESSAGE CONTENT')


def question(message):
    return dict(key='opaque-not-an-identity', source_resident_id=MEMBER, source_message_id=message,
                resident_id=FACADE, message_id='projected-' + message, title='助手',
                kind='question_set', url='/lineage?resident=' + FACADE)


@pytest.fixture
def reminder(bridge, monkeypatch):
    adapter, server, portal, binding = bridge
    incoming(adapter, server, '/help')
    server.sent.clear()
    adapter.store.budget(chat_key('bot'), reset=True)
    now = [1000.0]
    mailbox = {'items': [], 'pending_requests': []}
    watch = {'keys': ['arbitrary-conversation'], 'cursor': 'cursor', 'snapshot_required': False}
    failures = [False]
    calls = []

    def call(actor, instruction, arguments):
        if instruction == 'ListChatroomMessages':
            assert actor == ACTOR
            return {'messages': [dict(message_id=m['message_id'], author_id='alice',
                content_parts=[{'type': 'text', 'text': '请查看 ' + m['message_id']}])
                for item in mailbox['items'] for m in item.get('unread_mentions', [])]}
        assert actor == ACTOR and arguments == {'user_id': ACTOR}
        assert instruction == 'GetMailbox'
        calls.append(instruction)
        if failures[0]:
            raise PermissionError('PRIVATE TOKEN MUST NOT BE LOGGED')
        return deepcopy(mailbox)

    def first(actor, instruction, arguments):
        assert actor == ACTOR and instruction == 'WatchMailbox'
        assert arguments['user_id'] == ACTOR and 'after_cursor' in arguments
        calls.append(instruction)
        return [deepcopy(watch)]

    portal.call, portal.first_batch = call, first
    adapter.notifications.clock = lambda: now[0]
    return SimpleNamespace(adapter=adapter, server=server, portal=portal, binding=binding, now=now,
        box=mailbox, watch=watch, failed=failures, calls=calls, notices=adapter.notifications)


def enable(r, **changes):
    with r.adapter.store.lock(ACTOR):
        r.notices.update(ACTOR, **changes)
    r.notices.scan(ACTOR)


def due(r, *, flush=True):
    r.notices.scan(ACTOR)
    if flush:
        with r.adapter.store.lock(ACTOR):
            r.adapter.flush(ACTOR)


def test_defaults_command_and_self_admin_permissions(reminder, monkeypatch):
    r = reminder
    assert r.notices.preferences(ACTOR) == {'mention': False, 'question': False}
    from management_portal.telepus import portal_client, weixin_binding
    from management_portal.watchboards import telepus
    from management_portal import permission_system
    monkeypatch.setattr(weixin_binding, 'WeixinBinding', lambda: r.binding)
    def client(channel):
        assert channel == 'weixin'
        return r.portal
    monkeypatch.setattr(portal_client, 'PortalClient', client)
    monkeypatch.setattr(telepus, '_contact_user', lambda name, **kw: name)
    monkeypatch.setattr(permission_system, 'user_category', lambda actor: 'portal_admin' if actor == 'admin' else 'user')
    assert settings(ACTOR, changes={'mention': True})['mention']
    with pytest.raises(PermissionError):
        settings('bob', ACTOR, {'question': True})
    assert settings('admin', ACTOR, {'question': True}) == {'mention': True, 'question': True}
    assert settings(ACTOR, changes={'mention': False, 'question': False}) == {'mention': False, 'question': False}
    incoming(r.adapter, r.server, '/notify mention on', message_id=2)
    incoming(r.adapter, r.server, '/notify question on', message_id=3)
    assert r.notices.preferences(ACTOR) == {'mention': True, 'question': True}
    incoming(r.adapter, r.server, '/notify mention off', message_id=4)
    incoming(r.adapter, r.server, '/notify question off', message_id=5)
    assert not any(r.notices.preferences(ACTOR).values())
    assert not r.portal.delivered


@pytest.mark.parametrize('arrival', ['after_success', 'before_commit'])
def test_enable_captures_baseline_before_first_scan(reminder, monkeypatch, arrival):
    r = reminder
    r.box['items'] = [mention('old')]
    snapshot = r.notices.snapshot

    def snapshot_with_arrival(actor):
        current = snapshot(actor)
        r.box['items'].append(mention('new'))
        return current

    if arrival == 'before_commit':
        monkeypatch.setattr(r.notices, 'snapshot', snapshot_with_arrival)
    with r.adapter.store.lock(ACTOR):
        assert r.notices.update(ACTOR, mention=True)['mention']
    monkeypatch.setattr(r.notices, 'snapshot', snapshot)
    with r.notices.router.connect() as c:
        assert c.execute("SELECT baseline_ready FROM notification_preferences WHERE category='mention'").fetchone()[0]
        assert c.execute("SELECT status FROM notification_deliveries WHERE source_message_id='old'").fetchone()[0] == 'baseline'
    assert not r.notices.pending(ACTOR)
    if arrival == 'after_success':
        r.box['items'].append(mention('new'))
    r.notices.scan(ACTOR)
    assert [p['source_message_id'] for p in r.notices.pending(ACTOR)] == ['new']


@pytest.mark.parametrize('failure', ['permission', 'partial', 'baseline_write'])
def test_enable_failure_preserves_switches_and_baseline(reminder, monkeypatch, failure):
    r = reminder
    r.box['items'] = [mention('old')]
    enable(r, mention=True)
    if failure == 'permission':
        r.failed[0] = True
    elif failure == 'partial':
        r.box['incomplete'] = True
    else:
        def fail(*args):
            raise RuntimeError('baseline write failed')
        monkeypatch.setattr(r.notices, '_reconcile', fail)
    with r.adapter.store.lock(ACTOR), pytest.raises((PermissionError, ValueError, RuntimeError)):
        r.notices.update(ACTOR, mention=False, question=True)
    assert r.notices.preferences(ACTOR) == {'mention': True, 'question': False}
    with r.notices.router.connect() as c:
        assert c.execute("SELECT baseline_ready FROM notification_preferences WHERE category='mention'").fetchone()[0]
        assert c.execute("SELECT status FROM notification_deliveries WHERE source_message_id='old'").fetchone()[0] == 'baseline'


def test_baseline_once_restart_and_snapshot_required_offline_new(reminder):
    r = reminder
    r.box['items'] = [mention('old')]
    enable(r, mention=True)
    assert not r.notices.pending(ACTOR)
    r.box['items'].append(mention('new'))
    due(r)
    assert len(r.server.sent) == 1
    restarted = Notifications(RouterStore(r.adapter.router.store.schema), r.portal, clock=lambda: r.now[0])
    r.watch.update(snapshot_required=True, keys=[])
    r.box['items'].append(mention('offline'))
    restarted.scan(ACTOR)
    assert [p['source_message_id'] for p in restarted.pending(ACTOR)] == ['offline']
    r.adapter.notifications = r.notices = restarted
    due(r)
    assert len(r.server.sent) == 2
    due(r)
    assert len(r.server.sent) == 2


def test_missing_question_field_delays_baseline_and_never_clears_pending(reminder):
    r = reminder
    del r.box['pending_requests']
    enable(r, question=True)
    with r.notices.router.connect() as c:
        assert not c.execute('SELECT baseline_ready FROM notification_preferences WHERE category=\'question\'').fetchone()[0]
    r.box['pending_requests'] = [question('old')]
    r.notices.scan(ACTOR)
    assert not r.notices.pending(ACTOR)
    r.box['pending_requests'].append(question('new'))
    due(r, flush=False)
    del r.box['pending_requests']
    r.adapter.flush(ACTOR)
    assert not r.server.sent and len(r.notices.pending(ACTOR)) == 1
    assert r.adapter.store.pending(chat_key('bot'))
    r.box['pending_requests'] = [question('new')]
    r.adapter.flush(ACTOR)
    assert len(r.server.sent) == 1


@pytest.mark.parametrize('failure', ['permission', 'partial', 'off', 'read'])
def test_revalidate_before_send_and_do_not_mutate_on_failed_read(reminder, failure, capsys):
    r = reminder
    enable(r, mention=True)
    r.box['items'] = [mention('new')]
    due(r, flush=False)
    if failure == 'permission':
        r.failed[0] = True
    elif failure == 'partial':
        r.box['next_cursor'] = 'unread-page'
    elif failure == 'off':
        r.notices.update(ACTOR, mention=False)
    else:
        r.box['items'] = []
    r.adapter.flush(ACTOR)
    assert not r.server.sent
    assert len(r.notices.pending(ACTOR)) == (1 if failure in ('permission', 'partial') else 0)
    assert 'PRIVATE' not in str(capsys.readouterr())
    assert not any(name.startswith(('Mark', 'Answer')) for name in r.calls)


def test_immediate_pending_batch_five_items_one_message(reminder):
    r = reminder
    enable(r, mention=True, question=True)
    r.box['items'] = [mention(str(i)) for i in range(7)]
    r.box['pending_requests'] = [question('q')]
    r.notices.scan(ACTOR)
    r.adapter.flush(ACTOR)
    text = r.server.sent[0]['item_list'][0]['text_item']['text']
    assert '8 件事' in text and '另有 3 项' in text
    assert 'http' not in text
    assert text.count('alice') == 5 and text.count('开发群') == 5 and '请查看' in text
    assert 'PRIVATE' not in text and len(r.notices.pending(ACTOR)) == 3
    assert r.adapter.store.pending(chat_key('bot'))
    r.adapter.flush(ACTOR)
    assert len(r.server.sent) == 4 and not r.notices.pending(ACTOR)
    second = '\n'.join(m['item_list'][0]['text_item']['text'] for m in r.server.sent[1:])
    assert '此问题请回 Portal 作答' in second


@pytest.mark.parametrize('gate', ['reserved', 'no_context', 'minus_two'])
def test_budget_wait_and_next_inbound_send_reply_before_summary(reminder, gate):
    r = reminder
    enable(r, mention=True)
    r.box['items'] = [mention('new')]
    due(r, flush=False)
    if gate == 'reserved':
        for _ in range(r.adapter.budget - 1):
            r.adapter.store.budget(chat_key('bot'), spent=True)
    elif gate == 'no_context':
        r.adapter.store.update(ACTOR, context_token='')
    else:
        r.server.reject = -2
    r.adapter.flush(ACTOR)
    assert not r.server.sent and len(r.notices.pending(ACTOR)) == 1
    r.server.reject = 0
    incoming(r.adapter, r.server, '/where', message_id=2)
    texts = [m['item_list'][0]['text_item']['text'] for m in r.server.sent]
    assert len(texts) == 2 and '**alice** 在 **开发群** 里 @了你' in texts[1]
    assert not r.notices.pending(ACTOR)


def test_card_confirmed_source_dedup_projection_and_reminder_does_not_block_card(reminder):
    r = reminder
    enable(r, question=True)
    r.box['pending_requests'] = [question('source-q')]
    due(r, flush=False)
    state = r.adapter.router.store.get_chat_state(chat_key('bot'))
    message = {'message_id': 'projected-q', 'family_reply': {'resident_id': MEMBER, 'message_id': 'source-q'}}
    part = {'answer_message_id': 'answer-q', 'questions': [{'title': 'Approve?', 'options': ['yes', 'no']}]}
    outgoing = r.adapter.router._open_questions(state, FACADE, message, part, member_owned=True)
    r.adapter.enqueue(chat_key('bot'), outgoing)
    r.adapter.flush(ACTOR)
    assert len(r.server.sent) == 1 and '问题 1/1' in r.server.sent[0]['item_list'][0]['text_item']['text']
    assert not r.notices.pending(ACTOR)
    r.box['pending_requests'].append(question('source-q2'))
    due(r)
    assert len(r.server.sent) == 2
    message['message_id'] = 'projection-q2'
    message['family_reply']['message_id'] = 'source-q2'
    part['answer_message_id'] = 'answer-q2'
    r.adapter.enqueue(chat_key('bot'), r.adapter.router._open_questions(state, FACADE, message, part, member_owned=True))
    r.adapter.flush(ACTOR)
    assert len(r.server.sent) == 3


def test_reenable_builds_new_baseline_unbind_clears_and_expiry_stops(reminder):
    r = reminder
    enable(r, mention=True)
    r.box['items'] = [mention('new')]
    due(r, flush=False)
    r.notices.update(ACTOR, mention=False)
    enable(r, mention=True)
    r.adapter.flush(ACTOR)
    assert not r.server.sent
    r.binding.unbind(ACTOR)
    assert r.notices.preferences(ACTOR) == {'mention': False, 'question': False}
    assert not r.adapter.store.pending(chat_key('bot'))
    r.adapter.store.save_account(ACTOR, ACCOUNT)
    assert not any(r.notices.preferences(ACTOR).values())


def test_question_resident_title(reminder):
    r = reminder
    enable(r, question=True)
    q = question('name')
    q.pop('title')
    r.box['pending_requests'] = [q]
    due(r)
    assert '个人助手' in r.server.sent[0]['item_list'][0]['text_item']['text']


def test_network_retry_backoff_and_expired_binding_stops_listening(reminder):
    r = reminder
    enable(r, mention=True)
    r.box['items'] = [mention('new')]
    due(r, flush=False)
    r.server.reject = 500
    r.adapter.flush(ACTOR)
    assert not r.server.sent
    calls = len(r.calls)
    r.adapter.flush(ACTOR)
    assert len(r.calls) == calls
    with r.notices.router.connect() as c:
        c.execute("UPDATE outbound_queue SET item=item-'retry_at' WHERE item->>'notification_actor'=?", (ACTOR,))
    r.server.reject = -14
    r.adapter.flush(ACTOR)
    assert r.adapter.store.account(ACTOR)['status'] == 'expired'
    assert not any(r.notices.preferences(ACTOR).values())
    calls = len(r.calls)
    r.notices.scan(ACTOR)
    assert len(r.calls) == calls


def test_new_question_field_is_discovered_even_without_watch_key(reminder):
    r = reminder
    del r.box['pending_requests']
    enable(r, question=True)
    r.watch['keys'] = []
    r.box['pending_requests'] = [question('existing')]
    r.now[0] += 31
    r.notices.scan(ACTOR)
    assert not r.notices.pending(ACTOR)
    with r.notices.router.connect() as c:
        assert c.execute("SELECT baseline_ready FROM notification_preferences WHERE category='question'").fetchone()[0]


def test_schema_nine_upgrade_preserves_data(telepus_schema):
    store = RouterStore(telepus_schema)
    store.init_schema()
    store.upsert_chat_state('123', telegram_user_id='1', portal_actor=ACTOR)
    with store.connect() as c:
        c.execute('DROP TABLE notification_preferences,notification_deliveries')
        c.execute("UPDATE meta SET value='9' WHERE key='schema_version'")
    upgraded = RouterStore(telepus_schema)
    upgraded.init_schema()
    assert upgraded.get_chat_state('123')['portal_actor'] == ACTOR
    assert upgraded.get_meta('schema_version') == '11'
    assert Notifications(upgraded).preferences(ACTOR) == {'mention': False, 'question': False}


def test_real_kernel_default_account_self_mailbox_permissions(permissions, monkeypatch):
    """Real declarations, default PAR rows and kernel checks; only mailbox data is synthetic."""
    from management_portal import web_isa
    from management_portal.hatg.contracts import InstructionCall, InstructionContext, InstructionError
    from management_portal.hatg.kernel import InstructionKernel

    permissions.users('notify_alice', 'notify_bob')
    reads = []
    monkeypatch.setattr(web_isa, '_inbox_entries', lambda actor, *_: reads.append(actor) or [])
    from management_portal import society_service
    monkeypatch.setattr(society_service, 'get_society_service', lambda: SimpleNamespace(
        resident=SimpleNamespace(list_user_pending_requests=lambda actor: {'requests': []})))
    kernel = InstructionKernel(web_isa.binding(), store=None)
    for actor, other in [('notify_alice', 'notify_bob'), ('notify_bob', 'notify_alice')]:
        context = InstructionContext(caller=actor, entry='web', channel='weixin')
        own = kernel.execute(InstructionCall.from_payload({'instruction': 'GetMailbox', 'arguments': {'user_id': actor}}), context)
        assert own.status == 'committed', own.error
        assert own.result['items'] == []
        refused = kernel.execute(InstructionCall.from_payload({'instruction': 'GetMailbox', 'arguments': {'user_id': other}}), context)
        assert refused.error['code'] in ('instruction.forbidden', 'instruction.not_found')
        stream = kernel.stream(InstructionCall.from_payload({'instruction': 'WatchMailbox',
            'arguments': {'user_id': actor, 'timeout_seconds': 1}}), context)
        try:
            assert next(stream)[0]['snapshot_required']
        finally:
            stream.close()
        with pytest.raises(InstructionError) as denied:
            next(kernel.stream(InstructionCall.from_payload({'instruction': 'WatchMailbox',
                'arguments': {'user_id': other, 'timeout_seconds': 1}}), context))
        assert denied.value.code in ('instruction.forbidden', 'instruction.not_found')
    assert reads == ['notify_alice', 'notify_bob']
