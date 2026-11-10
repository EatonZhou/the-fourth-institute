"""Mobile membership and direct delivery use real isolated Family lifecycle facts."""
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest

from tests.cases.test_personal_assistant import personal, future_creation_store, charters  # noqa: F401
from management_portal.society_service import (
    personal_phone_assistant as phone, resident_record_domain as records,
    family_facade, family_chat_preferences as prefs, resident_runtime_domain,
    resident_protocol, resident_intake_domain, resident_conversation_stream, family_quota,
)
REAL_DELIVER = resident_protocol.deliver_resident_input
REAL_INTAKE = resident_intake_domain.deliver_resident_message_content


def test_first_mobile_messages_share_one_member_and_execution_settings(personal):
    ama, entry = personal
    facade = records.find_resident(entry['resident_id'])
    def route(channel):
        return family_facade.direct_member(facade, {'message_id': 'msg_' + channel, 'author_id': 'newcomer',
                                                   'origin': {'channel': {'kind': channel}}})
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(route, ['weixin', 'telegram']))
    assert ids[0] == ids[1] and ids[0] != ama['resident_id']
    assert phone.ensure(facade, 'newcomer') == ids[0]
    member = records.find_resident(ids[0])
    assert member['personal_owner_id'] == 'newcomer'
    assert member['family']['role'] == 'member'
    original = resident_runtime_domain.read_resident_runtime_settings(records.find_resident(ama['resident_id']))
    copied = resident_runtime_domain.read_resident_runtime_settings(member)
    for key in ['backend', 'model', 'reasoning_effort']:
        assert copied.get(key) == original.get(key)
    from management_portal.society_service import lineage_aggregate
    graph = lineage_aggregate.read_lineage(facade['lineage_id'], actor='newcomer')
    family = next(b['family'] for b in graph['function_blocks'] if b.get('family'))
    assert family['phone_assistant_id'] == ids[0]
    assert set(family['members']) == {ids[0], ama['resident_id']}


def test_mobile_ignores_web_solo_mute_but_keeps_quotes_and_answers(personal, monkeypatch):
    ama, entry = personal
    facade = records.find_resident(entry['resident_id'])
    rid = phone.ensure(facade, 'newcomer')
    prefs.change(facade['resident_id'], 'newcomer', ama['resident_id'], 'solo', True)
    prefs.change(facade['resident_id'], 'newcomer', rid, 'mute', True)
    message = {'message_id': 'msg_mobile', 'author_id': 'newcomer', 'origin': {'channel': {'kind': 'weixin'}}}
    assert family_facade.direct_member(facade, message) == rid
    prefs.validate_target(facade, message, rid)
    assert family_facade.direct_member(facade, {**message, 'origin': {}}) == ama['resident_id']
    monkeypatch.setattr(family_facade, 'quoted_member', lambda *a: ama['resident_id'])
    assert family_facade.direct_member(facade, message) == ama['resident_id']
    assert family_facade.direct_member(facade, {**message, 'content_parts': [
        {'type': 'async_question_answer', 'source_message_id': 'question', 'answers': []}]}) == ama['resident_id']


def test_mobile_delivery_is_relayed_before_router_and_reply_is_projected(personal, monkeypatch):
    _, entry = personal
    facade = records.find_resident(entry['resident_id'])
    monkeypatch.setattr(resident_protocol, 'deliver_resident_input', REAL_DELIVER)
    monkeypatch.setattr(resident_intake_domain, 'deliver_resident_message_content', REAL_INTAKE)
    monkeypatch.setattr(family_quota.execution_port, 'service', lambda: Mock(accounts=lambda: {'accounts': []}))
    result = resident_protocol.deliver_resident_input({'resident_id': facade['resident_id'],
        'message': {'message_id': 'msg_' + 'c' * 32, 'content_parts': [{'type': 'text', 'text': 'hello'}]}},
        'newcomer', delivery_context=resident_protocol.ResidentDeliveryContext(channel='weixin'))
    link = family_facade.relay_links(facade)[result['message_id']]
    assert records.find_resident(link['resident_id'])['title'] == '手机助手'
    repository = resident_conversation_stream.get_repository()
    delivered = repository.message(link['resident_id'], link['message_id'])
    assert delivered['origin']['channel'] == {'kind': 'weixin'}
    assert repository.input_state(facade['resident_id'], result['message_id'])['state'] == 'completed'
    repository.append_message(link['resident_id'], {'message_id': 'msg_' + 'd' * 32, 'role': 'agent',
        'author_id': link['resident_id'], 'reply_to_message_id': link['message_id'],
        'content_parts': [{'type': 'text', 'text': 'phone answer'}]})
    repository.set_input_state(link['resident_id'], link['message_id'], 'completed')
    shown = family_facade.project_messages(facade, [repository.message(facade['resident_id'], result['message_id'])])
    assert shown[-1]['family_reply']['title'] == '手机助手'
    assert shown[-1]['family_reply']['request_channel'] == {'kind': 'weixin'}
    prefs.change(facade['resident_id'], 'newcomer', link['resident_id'], 'mute', True)
    assert prefs.filter_messages(facade, 'newcomer', shown) == shown


def test_solo_generation_changes_only_on_solo_transition(personal):
    ama, entry = personal
    fid = entry['resident_id']
    def token():
        return prefs.get(fid, 'newcomer')['solo_notice_token']
    prefs.change(fid, 'newcomer', ama['resident_id'], 'solo', True)
    first = token()
    prefs.change(fid, 'newcomer', ama['resident_id'], 'mute', True)
    assert token() == first
    prefs.change(fid, 'newcomer', ama['resident_id'], 'solo', False)
    prefs.change(fid, 'newcomer', ama['resident_id'], 'solo', True)
    assert token() != first
