"""Personal Family Mute and Solo controls.

:module: lineage.team
:importance: P1
"""
import pytest
from management_portal.society_service import family_facade, resident_record_domain
from tests.cases.isa_test_support import ACTOR, ok
from tests.cases.test_family_familize import lineage, charters, future_creation_store, resident  # noqa: F401

@pytest.fixture
def family_chat(lineage, charters):
    member = resident(lineage, 'Member')
    result = ok('Familize', {'lineage_id':lineage, 'resident_ids':[member]})
    return resident_record_domain.find_resident(result['facade_resident_id']), member

def test_preferences_are_personal_and_controls_are_independent(family_chat):
    from management_portal.society_service import family_chat_preferences as p
    f, m = family_chat
    p.change(f['resident_id'], ACTOR, m, 'mute', True)
    assert p.read(f, ACTOR)['muted_member_ids'] == [m]
    assert p.allowed(f, p.read(f, ACTOR)) == []
    assert p.read(f, 'someone-else')['muted_member_ids'] == []
    p.change(f['resident_id'], ACTOR, m, 'mute', True)
    assert p.allowed(f, p.read(f, ACTOR)) == []
    p.change(f['resident_id'], ACTOR, m, 'solo', True)
    assert p.read(f, ACTOR)['solo_member_id'] == m
    assert p.allowed(f, p.read(f, ACTOR)) == [m]
    p.change(f['resident_id'], ACTOR, m, 'mute', True)
    assert p.read(f, ACTOR)['solo_member_id'] == m
    p.change(f['resident_id'], ACTOR, m, 'solo', False)
    assert p.allowed(f, p.read(f, ACTOR)) == []
    assert p.read(f, ACTOR)['muted_member_ids'] == [m]
    with pytest.raises(ValueError):
        p.change(f['resident_id'], ACTOR, f['resident_id'], 'solo', True)

def test_admission_snapshot_survives_later_toggle(family_chat):
    from management_portal.society_service import family_chat_preferences as p
    f, m = family_chat
    msg = {'message_id': 'msg_chat_1', 'author_id': ACTOR}
    family_facade.routing_snapshot(f, msg)
    p.change(f['resident_id'], ACTOR, m, 'mute', True)
    assert p.allowed(f, family_facade.routing_snapshot(f, msg)) == [m]
    assert p.allowed(f, family_facade.routing_snapshot(f, {**msg, 'message_id': 'msg_chat_2'})) == []

def test_mute_hides_entire_round_and_nested_replies(family_chat):
    from management_portal.society_service import family_chat_preferences as p
    f, m = family_chat
    rows = [{'message_id':'q','relay':{'resident_id':'child','via':[{'resident_id':m}]}},
            {'message_id':'a','family_reply':{'resident_id':'child','via':[{'resident_id':m}], 'request_message_id':'q'}, 'content_parts':[{'type':'async_questions'}]},
            {'message_id':'unrouted','role':'user'}]
    p.change(f['resident_id'], ACTOR, m, 'mute', True)
    p.change(f['resident_id'], ACTOR, m, 'mute', True)
    assert p.filter_messages(f, ACTOR, rows) == rows[-1:]
    assert p.filter_messages(f, 'someone-else', rows) == rows
    p.change(f['resident_id'], ACTOR, m, 'mute', False)
    assert p.filter_messages(f, ACTOR, rows) == rows

def test_direct_route_and_relay_validation_respect_solo_and_mute(family_chat, monkeypatch):
    from management_portal.society_service import family_chat_preferences as p
    f, m = family_chat
    monkeypatch.setattr(family_facade, 'quoted_member', lambda *a: '')
    p.change(f['resident_id'], ACTOR, m, 'solo', True)
    msg = {'message_id': 'msg_direct', 'author_id': ACTOR}
    assert family_facade.direct_member(f, msg) == m
    p.validate_target(f, msg, m)
    with pytest.raises(ValueError, match='Solo/Mute'):
        p.validate_target(f, msg, f['resident_id'])
    # Later Mute cannot change this input's frozen destination, nor override Solo.
    p.change(f['resident_id'], ACTOR, m, 'mute', True)
    p.validate_target(f, msg, m)
    p.validate_target(f, {**msg, 'message_id':'msg_new_solo'}, m)
    p.change(f['resident_id'], ACTOR, m, 'solo', False)
    with pytest.raises(ValueError, match='Solo/Mute'):
        p.validate_target(f, {**msg, 'message_id':'msg_new'}, m)

def test_real_admission_freezes_routing_and_projection_uses_viewer(family_chat):
    from management_portal.society_service import family_chat_preferences as p, resident_domain, resident_conversation_stream, resident_protocol
    f, m = family_chat
    mid = 'msg_0000000000000111'
    resident_domain.deliver_resident_message_content(f['resident_id'], ACTOR, 'before mute', message_id=mid)
    p.change(f['resident_id'], ACTOR, m, 'mute', True)
    message = resident_conversation_stream.get_repository().message(f['resident_id'], mid)
    assert family_facade.routing_snapshot(f, message)['muted_member_ids'] == []
    snap = resident_protocol.get_resident_conversation_snapshot(f['resident_id'], ACTOR)
    assert snap['family_facade']['chat_preferences']['muted_member_ids'] == [m]

def test_projection_includes_outward_history_and_queue_ownership(family_chat):
    from management_portal.society_service import family_chat_preferences as p
    f, m = family_chat
    p.change(f['resident_id'], ACTOR, m, 'solo', True)
    rows = [{'message_id':'q', 'relay':{'resident_id':m}},
            {'message_id':'old', 'family_history':{'resident_id':m}},
            {'message_id':'out', 'origin':{'initiated_by':{'id':m}}},
            {'message_id':'new', 'relay':{'resident_id': 'resident_other_member'}},
            {'message_id':'global'}]
    # A different current direct member's round is outside the Solo view.
    from unittest.mock import patch
    snapshot = {'family_facade':{}, 'messages':rows,
                'queue':{'items':[{'message_id': 'q'}, {'message_id':'new'}]},
                'input_states':[{'message_id':'q'}, {'message_id':'new'}]}
    with patch.object(family_facade, 'family_member_ids', return_value={m, 'resident_other_member'}):
        result = p.project(f, ACTOR, snapshot)
    assert [row['message_id'] for row in result['messages']] == ['q','old','out','global']
    assert result['queue']['items'] == result['input_states'] == [{'message_id':'q'}]


def test_mute_allows_explicit_quote_but_blocks_automatic_continuation(family_chat, monkeypatch):
    from management_portal.society_service import family_chat_preferences as p
    f, m = family_chat
    p.change(f['resident_id'], ACTOR, m, 'mute', True)
    monkeypatch.setattr(family_facade, 'quoted_member', lambda *_: '')
    monkeypatch.setattr(family_facade, 'continued_member', lambda *_: m)
    msg = {'message_id':'msg_auto', 'author_id':ACTOR}
    assert family_facade.direct_member(f, msg) == ''
    with pytest.raises(ValueError, match='Mute'):
        p.validate_target(f, msg, m)
    monkeypatch.setattr(family_facade, 'quoted_member', lambda *_: m)
    explicit = {**msg, 'message_id':'msg_explicit'}
    assert family_facade.direct_member(f, explicit) == m
    p.validate_target(f, explicit, m)


def test_question_answer_keeps_original_owner_despite_solo(family_chat, monkeypatch):
    from management_common.async_questions import ANSWER_PART
    from management_portal.society_service import family_chat_preferences as p
    f, m = family_chat
    p.change(f['resident_id'], ACTOR, m, 'solo', True)
    original = 'resident_original_question_owner'
    monkeypatch.setattr(family_facade, 'quoted_member', lambda *a: original)
    message = {'message_id':'msg_answer', 'author_id':ACTOR,
               'content_parts':[{'type':ANSWER_PART, 'source_message_id':'msg_questions'}]}
    assert family_facade.direct_member(f, message) == original
    p.validate_target(f, message, original)


def test_solo_can_switch_and_clear_after_member_leaves(family_chat, monkeypatch):
    from management_portal.society_service import family_chat_preferences as p
    f, m = family_chat
    second = 'resident_other_member'
    real_members = family_facade.family_member_ids
    monkeypatch.setattr(family_facade, 'family_member_ids', lambda *_: {m, second})
    p.change(f['resident_id'], ACTOR, m, 'solo', True)
    assert p.change(f['resident_id'], ACTOR, second, 'solo', True)['solo_member_id'] == second
    monkeypatch.setattr(family_facade, 'family_member_ids', real_members)
    assert p.allowed(f, p.read(f, ACTOR)) == []
    assert p.change(f['resident_id'], ACTOR, second, 'solo', False)['solo_member_id'] == ''


def test_explicit_schema_migration_preserves_legacy_routing_and_rolls_back_preview(family_chat):
    from scripts.migrate_family_chat_controls import migrate
    from management_portal.society_service import family_chat_preferences as p
    from management_portal.society_service.state_store import get_lineage_data_store, LineageDataStore
    f, m = family_chat
    p.change(f['resident_id'], ACTOR, m, 'mute', True)
    p.change(f['resident_id'], ACTOR, m, 'solo', True)
    request = {'message_id':'msg_migration', 'author_id':ACTOR}
    before = family_facade.routing_snapshot(f, request)
    database = get_lineage_data_store().database
    def legacy(c):
        c.execute('UPDATE family_chat_preferences SET excluded_member_ids=muted_member_ids')
        c.execute('ALTER TABLE family_chat_preferences DROP COLUMN muted_member_ids')
        for table in ('family_chat_preferences', 'family_chat_inputs'):
            c.execute(f'ALTER TABLE {table} RENAME COLUMN excluded_member_ids TO muted_member_ids')
        c.execute("UPDATE lineage_meta SET value='tfi.lineage-data.v65' WHERE key='schema'")
    database.write(legacy)
    try:
        preview = migrate(database)
        assert preview['changed'] and not preview['committed']
        with database.read() as c:
            assert c.execute("SELECT value FROM lineage_meta WHERE key='schema'").fetchone()[0] == 'tfi.lineage-data.v65'
        assert migrate(database, apply=True)['changed']
        database.write(LineageDataStore._initialize)
        assert p.read(f, ACTOR) == {'muted_member_ids':[m], 'solo_member_id':m}
        assert family_facade.routing_snapshot(f, request) == before
        assert not migrate(database, apply=True)['changed']
    finally:
        # A failed assertion must not leave other isolated cases with old DDL.
        migrate(database, apply=True)

from tests.cases.permission_test_support import Fact, permissions  # noqa: E402,F401


def test_viewers_change_only_their_own_preferences_through_isa(family_chat, permissions):
    from tests.cases.isa_test_support import ok, run
    f, m = family_chat
    facade_id = f['resident_id']
    permissions.place(Fact('resident', facade_id))
    for user in ['chat-alice', 'chat-bob']:
        permissions.grant(user, facade_id, '@viewer')
    args = dict(resident_id=facade_id, member_id=m, control='mute', enabled=True)
    ok('UpdateFamilyChatPreferences', args, actor='chat-alice')
    assert ok('GetFamilyChatPreferences', {'resident_id':facade_id}, actor='chat-alice')['muted_member_ids'] == [m]
    assert ok('GetFamilyChatPreferences', {'resident_id':facade_id}, actor='chat-bob')['muted_member_ids'] == []
    assert run('UpdateFamilyChatPreferences', {**args, 'control':'exclude'}, actor='chat-alice').status != 'committed'
    ok('UpdateFamilyChatPreferences', {**args, 'control':'solo'}, actor='chat-alice')
    assert ok('GetFamilyChatPreferences', {'resident_id':facade_id}, actor='chat-alice') == {
        'muted_member_ids':[m], 'solo_member_id':m}
    assert run('UpdateFamilyChatPreferences', {**args, 'actor':'chat-bob'}, actor='chat-alice').status != 'committed'
    permissions.deny('chat-alice', facade_id, '@viewer')
    assert run('UpdateFamilyChatPreferences', args, actor='chat-alice').status != 'committed'


def test_old_exclude_merges_into_mute_and_can_be_cleared(family_chat):
    """Legacy excludes survive as Mute; unmute clears both stored sources."""
    from management_portal.society_service import family_chat_preferences as p
    from management_portal.society_service.state_store import get_lineage_data_store
    f, m = family_chat
    db = get_lineage_data_store().database
    p.change(f['resident_id'], ACTOR, m, 'mute', False)
    db.write(lambda c: c.execute('UPDATE family_chat_preferences SET excluded_member_ids=%s WHERE facade_id=%s', ([m], f['resident_id'])))
    assert p.read(f, ACTOR) == {'muted_member_ids':[m], 'solo_member_id':''}
    assert p.allowed(f, p.read(f, ACTOR)) == []
    assert p.change(f['resident_id'], ACTOR, m, 'mute', False)['muted_member_ids'] == []
    assert p.allowed(f, p.read(f, ACTOR)) == [m]
