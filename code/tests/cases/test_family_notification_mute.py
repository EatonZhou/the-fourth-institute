"""Family Mute only controls new personal Mailbox alerts.

:module: resident.conversation
:importance: P1
:design: decision_9ad68dd077d4
"""
import hashlib

import pytest

from management_common.principal import message_origin
from management_portal import conversation_read_state as reads
from management_portal import conversation_receipt_store as receipts
from management_portal.society_service import (
    family_facade, outward_messages, resident_conversation_records as events,
    resident_conversation_stream, resident_record_domain, resident_unread,
)
from management_portal.society_service.state_store import get_lineage_data_store
from tests.cases.family_test_support import set_role
from tests.cases.test_agent_conversation_index_backend import _register_protocol_resident


OUTER = 'resident_f10000000001'
INNER = 'resident_f10000000002'
LEAF = 'resident_f10000000003'
SIBLING = 'resident_f10000000004'
STEWARD = 'resident_f10000000005'
RESIDENT_VIEWER = 'resident_f10000000006'
ALICE, BOB = 'sample_admin', 'CASE-A'


@pytest.fixture
def chat():
    for rid in (OUTER, INNER, LEAF, SIBLING, STEWARD, RESIDENT_VIEWER):
        _register_protocol_resident(rid)
    set_role(OUTER, family_id='fbi_mute_outer')
    set_role(INNER, family_id='fbi_mute_outer', role='member')
    set_role(SIBLING, family_id='fbi_mute_outer', role='member')
    set_role(STEWARD, family_id='fbi_mute_outer', role='steward')
    set_role(INNER, family_id='fbi_mute_inner')
    set_role(LEAF, family_id='fbi_mute_inner', role='member')
    for actor in (ALICE, BOB, RESIDENT_VIEWER):
        reads.delete_actor(actor)
    return resident_conversation_stream.get_repository()


def policy(facade, actor=ALICE, *, muted=(), excluded=(), solo=''):
    # Isolated fixture state; preference API authorization is owned/tested separately.
    get_lineage_data_store().database.write(lambda c: c.execute(
        'INSERT INTO family_chat_preferences(facade_id,actor,muted_member_ids,excluded_member_ids,solo_member_id) '
        'VALUES (%s,%s,%s,%s,%s) ON CONFLICT(facade_id,actor) DO UPDATE SET '
        'muted_member_ids=EXCLUDED.muted_member_ids,excluded_member_ids=EXCLUDED.excluded_member_ids,'
        'solo_member_id=EXCLUDED.solo_member_id',
        (facade, actor, list(muted), list(excluded), solo),
    ))


def state(facade=OUTER, actor=ALICE):
    return reads.row(reads.load(actor), 'resident:' + facade)


def append(chat, rid, mid, *, author=ALICE, role='user', **fields):
    return chat.append_message(rid, {
        'message_id': mid, 'role': role, 'author_id': author,
        'created_at': '2026-11-02T09:00:00Z',
        'content_parts': [{'type': 'text', 'text': mid}], **fields,
    })


def request(chat, name, *, actor=ALICE, chain=(OUTER, INNER, LEAF)):
    """Persist the same input and accepted relay links used by real terminal events."""
    previous = None
    for rid in chain:
        mid = 'msg_' + hashlib.sha256((name + rid).encode()).hexdigest()[:32]
        fields = {'business_ref': family_facade.relay_mark(*previous)} if previous else {}
        append(chat, rid, mid, author=actor, **fields)
        chat.set_input_state(rid, mid, 'queued')
        if previous:
            facade, root = previous
            record = resident_record_domain.find_resident(facade)
            with get_lineage_data_store().family_relays.transaction(record['lineage_id'], facade) as value:
                value.setdefault('links', {})[root] = {'resident_id': rid, 'message_id': mid}
        previous = (rid, mid)
    assert [rid for rid, _ in family_facade.relay_sources_of(*previous)] == list(reversed(chain[:-1]))
    return previous


def finish(chat, target, outcome='completed'):
    rid, mid = target
    if outcome == 'completed':
        append(chat, rid, mid + '_reply', author=rid, role='agent', reply_to_message_id=mid)
    chat.set_input_state(rid, mid, outcome)


def notify(sender, actor, mid):
    return outward_messages.deliver_to_user(sender, actor, [{'type': 'text', 'text': mid}], message_id=mid)


@pytest.mark.parametrize('outcome', ['completed', 'failed', 'cancelled'])
@pytest.mark.parametrize('chain', [(OUTER, SIBLING), (OUTER, INNER, LEAF)])
def test_persisted_terminal_events_respect_viewer_and_direct_member(chat, outcome, chain):
    policy(OUTER, muted=[chain[1]])
    mine = request(chat, 'mine', chain=chain)
    theirs = request(chat, 'theirs', actor=BOB, chain=chain)
    finish(chat, mine, outcome)
    assert not state()['unread']
    finish(chat, theirs, outcome)
    # Read back through a fresh receipt-store handle, not an in-memory alert spy.
    receipts._database.cache_clear()
    assert not state()['unread'] and state(actor=BOB)['unread']
    assert chat.input_state(*mine)['state'] == outcome
    assert chat.stored_message(*mine)['content_parts']
    assert not state(chain[-1])['unread']  # relay copies never create a private alert
    finish(chat, request(chat, 'other-member', chain=(OUTER, SIBLING if chain[1] == INNER else INNER)), outcome)
    assert state()['unread']


def test_nested_mute_is_scoped_to_each_facade_and_never_a_leaf_guess(chat):
    policy(OUTER, muted=[LEAF])  # stale/non-direct preference cannot mute the outer member
    policy(INNER, muted=[LEAF])
    finish(chat, request(chat, 'outer-request'))
    assert state()['unread']
    finish(chat, request(chat, 'inner-request', chain=(INNER, LEAF)))
    assert not state(INNER)['unread']
    # An outer setting does not suppress a user's independent inner conversation.
    policy(INNER)
    policy(OUTER, muted=[INNER])
    finish(chat, request(chat, 'inner-unmuted', chain=(INNER, LEAF)))
    assert state(INNER)['unread']


def test_mute_preserves_old_unread_and_unmute_allows_subsequent_results(chat):
    finish(chat, request(chat, 'before'))
    before = state()
    policy(OUTER, muted=[INNER], solo=INNER)
    finish(chat, request(chat, 'during'))
    assert state() == before  # Solo does not override notification Mute
    reads.set_read(ALICE, 'resident:' + OUTER, unread_value=False)
    finish(chat, request(chat, 'during-after-read'), 'failed')
    assert not state()['unread']
    policy(OUTER)
    assert not state()['unread']
    finish(chat, request(chat, 'after'), 'cancelled')
    assert state()['unread'] and state()['read_revision'] > before['read_revision']


def test_late_final_reply_checks_current_mute_without_changing_direct_chat(chat):
    rid, mid = request(chat, 'late')
    chat.set_input_state(rid, mid, 'completed')
    assert not state()['unread']
    policy(OUTER, muted=[INNER])
    append(chat, rid, mid + '_late_reply', author=rid, role='agent', reply_to_message_id=mid)
    assert not state()['unread']
    # Personal direct conversations have their own notification contract.
    finish(chat, request(chat, 'direct', chain=(LEAF,)))
    assert state(LEAF)['unread']


def test_outward_notifications_keep_messages_and_recipients_and_use_nested_member(chat):
    policy(OUTER, muted=[INNER])
    mine = notify(LEAF, ALICE, 'outward-muted')
    theirs = notify(LEAF, BOB, 'outward-other-user')
    assert mine['resident_id'] == theirs['resident_id'] == OUTER
    assert not state()['unread'] and state(actor=BOB)['unread']
    stored = chat.stored_message(OUTER, mine['message_id'])
    assert stored['recipients'] == [{'kind': 'user', 'id': ALICE}]
    assert stored['origin']['initiated_by']['id'] == LEAF
    assert stored['content_parts'] == [{'type': 'text', 'text': 'outward-muted'}]
    # An update of an addressed message also passes the same mute gate.
    chat.update_message(OUTER, mine['message_id'], message={
        **stored, 'content_parts': [{'type': 'text', 'text': 'edited'}],
    })
    assert not state()['unread']
    notify(SIBLING, ALICE, 'outward-sibling')
    before = state()
    assert before['unread']
    notify(LEAF, ALICE, 'outward-muted-again')
    assert state() == before
    reads.set_read(ALICE, 'resident:' + OUTER, unread_value=False)
    policy(OUTER)
    notify(LEAF, ALICE, 'outward-unmuted')
    assert state()['unread']


def test_unknown_provenance_non_business_and_resident_recipients_still_alert(chat):
    policy(OUTER, muted=[INNER, STEWARD])
    notify(STEWARD, ALICE, 'steward')
    assert state()['unread']
    reads.set_read(ALICE, 'resident:' + OUTER, unread_value=False)
    append(chat, OUTER, 'unknown', role='agent', author=OUTER,
           recipients=[{'kind': 'user', 'id': ALICE}])
    assert state()['unread']
    policy(OUTER, RESIDENT_VIEWER, muted=[INNER])
    append(chat, OUTER, 'resident-recipient', role='agent', author=OUTER,
           origin=message_origin(OUTER, initiated_by=LEAF),
           recipients=[{'kind': 'resident', 'id': RESIDENT_VIEWER}])
    assert state(actor=RESIDENT_VIEWER)['unread']
    reads.set_read(RESIDENT_VIEWER, 'resident:' + OUTER, unread_value=False)
    finish(chat, request(chat, 'resident-request', actor=RESIDENT_VIEWER), 'failed')
    assert state(actor=RESIDENT_VIEWER)['unread']


def test_exclude_and_solo_do_not_suppress_new_notifications(chat):
    policy(OUTER, excluded=[INNER], solo=SIBLING)
    finish(chat, request(chat, 'already-routed'))
    assert state()['unread']
    reads.set_read(ALICE, 'resident:' + OUTER, unread_value=False)
    notify(LEAF, ALICE, 'excluded-outward')
    assert state()['unread']


def test_projected_via_provenance_is_direct_and_unknown_is_not_muted(chat):
    with get_lineage_data_store().database.read() as c:
        for field in ('family_reply', 'relay', 'family_history'):
            assert resident_unread._message_member(c, OUTER, {
                field: {'resident_id': LEAF, 'via': [{'resident_id': INNER}]},
            }) == INNER
        assert resident_unread._message_member(c, OUTER, {
            'family_reply': {'resident_id': LEAF, 'via': [{'resident_id': RESIDENT_VIEWER}]},
        }) == ''


def test_muted_event_rolls_back_with_its_conversation_transaction(chat):
    policy(OUTER, muted=[INNER])
    rid, mid = request(chat, 'rollback', actor=BOB)
    with pytest.raises(RuntimeError, match='rollback'):
        with chat.state_store.database.transaction() as c:
            chat.events.record_event(c, rid, events.INPUT_STATE_CHANGED, {
                'input_state': {'message_id': mid, 'state': 'failed', 'updated_at': '2026-11-02T10:00:00Z'},
            })
            raise RuntimeError('rollback')
    assert not state(actor=BOB)['unread']
