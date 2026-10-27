"""
:module: resident.conversation
:importance: P1
"""
import pytest

from management_portal import conversation_read_state as reads
from management_portal import conversation_receipt_store as receipts
from management_portal.society_service import resident_conversation_stream as stream
from tests.cases.test_agent_conversation_index_backend import _register_protocol_resident


RESIDENT = 'resident_777777777777'
KEY = 'resident:' + RESIDENT


@pytest.fixture
def chat():
    _register_protocol_resident(RESIDENT)
    for actor in ('sample_admin', 'CASE-A', 'resident_888888888888'):
        reads.delete_actor(actor)
    return stream.get_repository()


def say(chat, message_id, role='user', **fields):
    return chat.append_message(RESIDENT, {
        'message_id': message_id, 'role': role,
        'author_id': 'sample_admin' if role == 'user' else RESIDENT,
        'created_at': '2026-10-15T10:00:00Z',
        'content_parts': [{'type': 'text', 'text': 'hello'}], **fields,
    })


def unread(actor='sample_admin'):
    return reads.unread({'key': KEY}, reads.load(actor))


@pytest.mark.parametrize('outcome', ['completed', 'failed', 'cancelled'])
def test_offline_result_persists_for_sender_and_read_survives_event_replay(chat, outcome):
    """Resident 用户未读布尔记录

    test_offline_result_persists_for_sender_and_read_survives_event_replay

    :design: decision_15f7df737ed5
    :design-point:
    """
    say(chat, 'request')
    assert not unread()
    if outcome == 'completed':
        say(chat, 'reply', 'agent', reply_to_message_id='request')
        assert not unread()
    chat.set_input_state(RESIDENT, 'request', outcome)
    assert unread() and not unread('CASE-A')
    # A fresh process/database handle sees the same persisted flag.
    receipts._database.cache_clear()
    assert unread()
    reads.set_read('sample_admin', KEY, unread_value=False)
    assert not unread()
    chat.set_input_state(RESIDENT, 'request', outcome, updated_at='2026-10-16T10:00:00Z')
    assert not unread()


def test_a_late_outcome_orders_and_pages_the_conversation_by_its_time(chat, monkeypatch):
    """请求的结局时间参与 Mailbox 排序和分页

    用户先给一个 Resident 发请求，再和另一个 Resident 说话；前一个请求后来完成，
    它的对话按完成时间排在前面。服务端分页和条目显示用同一个时间，所以后一页的
    对话不会比已经显示的更新。只有请求结局、用户没有参与的对话不进入列表。
    结局时间在分页 SQL 里参与排序，有结局的对话不另做一次活动读取。

    :design: decision_15f7df737ed5
    """
    from management_portal import agent_conversation_index_backend as backend

    other = 'resident_999999999999'
    _register_protocol_resident(other)
    say(chat, 'request')
    chat.append_message(other, {
        'message_id': 'later', 'role': 'user', 'author_id': 'sample_admin', 'created_at': '2026-10-15T11:00:00Z',
        'content_parts': [{'type': 'text', 'text': 'hello'}],
    })
    say(chat, 'reply', 'agent', reply_to_message_id='request')
    finished = '2026-10-15T12:30:00.250000Z'
    chat.set_input_state(RESIDENT, 'request', 'completed', updated_at=finished)
    with receipts.database().transaction() as connection:
        receipts.alert(connection, ['sample_admin'], 'resident:resident_aaaaaaaaaaaa', 'elsewhere', at='2026-10-15T13:00:00Z')
    expected = [(KEY, finished), ('resident:' + other, '2026-10-15T11:00:00Z')]
    assert list(chat.actor_activity('sample_admin').items()) == [(key.removeprefix('resident:'), at) for key, at in expected]
    monkeypatch.setattr(chat.agent_data, 'chat_actor_activity',
                        lambda *args, **kwargs: pytest.fail('outcomes are paged in SQL'))
    shown, token = [], ''
    for _ in expected:
        page = backend.build_entries_page('sample_admin', page_size=1, page_token=token)
        shown += [(entry['key'], entry['updated_at']) for entry in page['entries'] if entry['key'].startswith('resident:')]
        token = page['next_page_token']
    assert shown == expected and token == ''


def test_control_receipt_does_not_notify_but_late_real_reply_does(chat):
    """Resident 用户未读布尔记录

    test_control_receipt_does_not_notify_but_late_real_reply_does

    :design: decision_15f7df737ed5
    :design-point:
    """
    say(chat, 'request')
    say(chat, 'receipt', 'system', reply_to_message_id='request')
    chat.set_input_state(RESIDENT, 'request', 'completed')
    assert not unread()
    say(chat, 'answer', 'agent', reply_to_message_id='request')
    assert unread()


def test_resident_caller_keeps_its_existing_mailbox_read_contract(chat):
    """Resident 用户未读布尔记录

    test_resident_caller_keeps_its_existing_mailbox_read_contract

    :design: decision_15f7df737ed5
    :design-point:
    """
    sender = 'resident_888888888888'
    say(chat, 'request-from-resident', author_id=sender)
    chat.set_input_state(RESIDENT, 'request-from-resident', 'failed')
    assert unread(sender) and not unread()


def test_addressed_message_notifies_only_recipient_and_clear_removes_flag(chat):
    """Resident 用户未读布尔记录

    test_addressed_message_notifies_only_recipient_and_clear_removes_flag

    :design: decision_15f7df737ed5
    :design-point:
    """
    message = say(chat, 'outward', 'agent', recipients=[{'kind': 'user', 'id': 'CASE-A'}])
    assert unread('CASE-A') and not unread()
    reads.set_read('CASE-A', KEY, unread_value=False)
    chat.append_message(RESIDENT, message)
    assert not unread('CASE-A')
    say(chat, 'outward-next', 'agent', recipients=[{'kind': 'user', 'id': 'CASE-A'}])
    assert unread('CASE-A')
    chat.state_store.database.write(lambda connection: chat._record_event(
        connection, RESIDENT, stream.CONTEXT_CLEARED, {'context_cleared': True},
    ))
    assert not unread('CASE-A')


def test_unread_write_rolls_back_with_conversation_event(chat):
    """Resident 用户未读布尔记录

    test_unread_write_rolls_back_with_conversation_event

    :design: decision_15f7df737ed5
    :design-point:
    """
    say(chat, 'request')
    with pytest.raises(RuntimeError):
        with chat.state_store.database.transaction() as connection:
            chat._record_event(connection, RESIDENT, stream.INPUT_STATE_CHANGED, {
                'input_state': {'message_id': 'request', 'state': 'failed',
                                'updated_at': '2026-10-15T10:00:01Z'},
            })
            raise RuntimeError('rollback')
    assert not unread()


def test_observe_sets_boolean_without_activity_or_completion_scan(chat, monkeypatch):
    """Resident 用户未读布尔记录

    test_observe_sets_boolean_without_activity_or_completion_scan

    :design: decision_15f7df737ed5
    :design-point:
    """
    from management_portal import serve_management_portal as portal
    from management_portal.society_service import resident_conversation_projection_domain

    say(chat, 'request')
    chat.set_input_state(RESIDENT, 'request', 'failed')
    monkeypatch.setattr(resident_conversation_projection_domain, 'conversation_family_summaries',
                        lambda *a: pytest.fail('reading must not scan completed work'))
    portal._set_agent_conversation_read_state('sample_admin', f'resident:{RESIDENT}', unread=False)
    assert not unread()


def test_mailbox_mark_read_persists_through_isa_and_state_refresh(chat, monkeypatch):
    """经 ISA 标记 Mailbox 条目已读或未读后，状态持久保存并反映到 Resident 视图

    :module: resident.identity
    :importance: P2
    """
    from types import SimpleNamespace
    from management_portal import resident_view_projection, serve_management_portal as portal, web_isa

    say(chat, 'request')
    chat.set_input_state(RESIDENT, 'request', 'failed')
    monkeypatch.setattr(portal.resident_api, 'resolve_resident_identity', lambda *a: {})
    monkeypatch.setattr(web_isa, 'web', lambda: portal)
    revision = 0
    for value in (False, True, False):
        call = SimpleNamespace(caller='sample_admin', argument={'key': KEY, 'unread': value}.get,
                               expected_version_for=lambda key: None)
        result = web_isa.mark_inbox_item_read(call)
        assert result['item']['unread'] is value
        assert unread() is value
        projected = resident_view_projection.attach_viewer_state([{'resident_id': RESIDENT}], 'sample_admin')
        assert projected[0]['viewer_state']['unread'] is value
        # Every change carries a newer revision, in the result and in the Resident state alike.
        assert projected[0]['viewer_state']['read_revision'] == result['item']['read_revision'] > revision
        revision = result['item']['read_revision']


def test_marking_read_with_a_seen_revision_keeps_a_newer_alert_unread(chat, monkeypatch):
    """看过的修订号落后时标为已读被拒绝，新提醒保持未读

    浏览器或客户端按看到的修订号标为已读；这之后到达的新回复推进了提醒和修订号，
    修改被拒绝并给出当前修订号，新回复保持未读；用当前修订号再标即可成功。

    :design: decision_15f7df737ed5
    """
    from types import SimpleNamespace
    from management_portal import serve_management_portal as portal, web_isa
    from management_portal.hatg.contracts import InstructionError

    say(chat, 'outward', 'agent', recipients=[{'kind': 'user', 'id': 'sample_admin'}])
    seen = reads.row(reads.load('sample_admin'), KEY)['read_revision']
    say(chat, 'outward-next', 'agent', recipients=[{'kind': 'user', 'id': 'sample_admin'}])
    monkeypatch.setattr(portal.resident_api, 'resolve_resident_identity', lambda *a: {})
    monkeypatch.setattr(web_isa, 'web', lambda: portal)

    def mark(expected):
        return web_isa.mark_inbox_item_read(SimpleNamespace(
            caller='sample_admin', argument={'key': KEY, 'unread': False}.get,
            expected_version_for=lambda key: expected,
        ))

    with pytest.raises(InstructionError) as refused:
        mark(str(seen))
    assert refused.value.code == 'instruction.version_conflict'
    assert unread()
    current = reads.row(reads.load('sample_admin'), KEY)['read_revision']
    assert refused.value.details['current_version'] == str(current)
    assert mark(str(current))['item']['unread'] is False
    assert not unread()
