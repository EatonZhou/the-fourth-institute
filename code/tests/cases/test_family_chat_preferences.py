"""Personal Family routing and projection controls."""
import pytest
from management_portal.society_service import family_facade, resident_record_domain
from tests.cases.test_cross_owner_storage_integration import ACTOR, owners  # noqa
from tests.cases.test_wall04_residual_20260919 import team  # noqa
from tests.cases.family_test_support import set_role

@pytest.fixture
def family_chat(team):
    set_role(team.source['resident_id'], family_id='fbi_chat', actor=ACTOR)
    set_role(team.target['resident_id'], family_id='fbi_chat', role='member', actor=ACTOR)
    return resident_record_domain.find_resident(team.source['resident_id']), team.target['resident_id']

def test_preferences_are_personal_and_solo_restores_mute(family_chat):
    from management_portal.society_service import family_chat_preferences as p
    f, m = family_chat
    p.change(f['resident_id'], ACTOR, m, 'mute', True)
    assert p.read(f, ACTOR)['muted_member_ids'] == [m]
    assert p.read(f, 'someone-else')['muted_member_ids'] == []
    p.change(f['resident_id'], ACTOR, m, 'solo', True)
    assert p.read(f, ACTOR)['solo_member_id'] == m
    assert p.allowed(f, p.read(f, ACTOR)) == [m]
    p.change(f['resident_id'], ACTOR, m, 'solo', False)
    assert p.allowed(f, p.read(f, ACTOR)) == []
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

def test_projection_filters_entire_round_and_nested_replies(family_chat):
    from management_portal.society_service import family_chat_preferences as p
    f, m = family_chat
    rows = [{'message_id':'q','relay':{'resident_id':'child','via':[{'resident_id':m}]}},
            {'message_id':'a','family_reply':{'resident_id':'child','via':[{'resident_id':m}], 'request_message_id':'q'}, 'content_parts':[{'type':'async_questions'}]},
            {'message_id':'unrouted','role':'user'}]
    p.change(f['resident_id'], ACTOR, m, 'mute', True)
    assert [x['message_id'] for x in p.filter_messages(f, ACTOR, rows)] == ['unrouted']
    assert p.filter_messages(f, 'someone-else', rows) == rows

def test_direct_route_and_relay_validation_respect_solo_and_mute(family_chat, monkeypatch):
    from management_portal.society_service import family_chat_preferences as p
    f, m = family_chat
    monkeypatch.setattr(family_facade, 'quoted_member', lambda *a: '')
    p.change(f['resident_id'], ACTOR, m, 'solo', True)
    msg = {'message_id': 'msg_direct', 'author_id': ACTOR}
    assert family_facade.direct_member(f, msg) == m
    p.validate_target(f, msg, m)
    with pytest.raises(ValueError, match='M/S'):
        p.validate_target(f, msg, f['resident_id'])
    # Later M cannot change this input's frozen destination.
    p.change(f['resident_id'], ACTOR, m, 'mute', True)
    p.validate_target(f, msg, m)
    with pytest.raises(ValueError, match='M/S'):
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

def test_schema_upgrade_is_idempotent_and_retains_family(family_chat):
    from management_portal.society_service.state_store import get_lineage_data_store, lineage_data_schema_matches
    from scripts.migrate_family_chat_preferences import upgrade
    db = get_lineage_data_store().database
    def check(c):
        c.execute('DROP TABLE family_chat_preferences, family_chat_inputs')
        c.execute("UPDATE lineage_meta SET value='tfi.lineage-data.v64' WHERE key='schema'")
        assert upgrade(c)['schema'] == 'tfi.lineage-data.v65'
        assert upgrade(c)['schema'] == 'tfi.lineage-data.v65'
        assert lineage_data_schema_matches(c)
    db.write(check)

def test_projection_includes_outward_history_and_queue_ownership(family_chat):
    from management_portal.society_service import family_chat_preferences as p
    f, m = family_chat
    p.change(f['resident_id'], ACTOR, m, 'mute', True)
    rows = [{'message_id':'q', 'relay':{'resident_id':m}},
            {'message_id':'old', 'family_history':{'resident_id':m}},
            {'message_id':'out', 'origin':{'initiated_by':{'id':m}}},
            {'message_id':'new'}]
    snapshot = {'family_facade':{}, 'messages':rows,
                'queue':{'items':[{'message_id': 'q'}, {'message_id':'new'}]},
                'input_states':[{'message_id':'q'}, {'message_id':'new'}]}
    result = p.project(f, ACTOR, snapshot)
    assert result['messages'] == [{'message_id':'new'}]
    assert result['queue']['items'] == result['input_states'] == [{'message_id':'new'}]


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


def test_solo_can_switch_and_clear_after_member_leaves(family_chat, team, monkeypatch):
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
    assert run('UpdateFamilyChatPreferences', {**args, 'actor':'chat-bob'}, actor='chat-alice').status != 'committed'
    permissions.deny('chat-alice', facade_id, '@viewer')
    assert run('UpdateFamilyChatPreferences', args, actor='chat-alice').status != 'committed'
