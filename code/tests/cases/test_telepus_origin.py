"""Trusted channel provenance changes presentation, never the authenticated sender."""
from copy import deepcopy

import pytest

from management_common.principal import message_origin, validate_message_origin
from management_portal.hatg.contracts import InstructionCall, InstructionContext, InstructionError
from management_portal.society_service import resident_input_queue_domain, resident_message_contract, resident_prompt_domain
from management_portal.telepus.portal_client import PortalClient
from tests.cases.test_resident_conversation_boundaries import repository  # noqa: F401
from tests.cases.resident_execution_test_support import resident_record, input_delivery  # noqa: F401
from tests.cases.test_family_facade import facade, _register_test_member, MEMBER  # noqa: F401

RESIDENT = 'resident_111111111111'


@pytest.mark.parametrize('channel', ['weixin', 'telegram'])
def test_channel_is_signed_and_guides_recipient(repository, channel):
    mid = 'msg_' + 'a' * 32
    message = resident_input_queue_domain.resident_user_message(RESIDENT, 'hello', 'alice', message_id=mid, channel=channel)
    saved = repository.commit_user_message(RESIDENT, message)
    assert saved['origin']['sender'] == {'kind': 'user', 'id': 'alice'}
    assert saved['origin']['channel'] == {'kind': channel}
    assert validate_message_origin(saved['origin']) == saved['origin']
    prompt = resident_prompt_domain._sender_line({'actor': 'alice', 'input_message': saved})
    assert '当前发送者是用户 alice' in prompt
    assert ('手机微信' if channel == 'weixin' else 'Telegram') in prompt
    assert '长回复会被分段' in prompt
    forwarded = resident_prompt_domain._sender_line({'actor': RESIDENT, 'input_message': saved})
    assert '长回复会被分段' in forwarded
    changed = deepcopy(saved)
    changed['origin']['channel']['kind'] = 'telegram' if channel == 'weixin' else 'weixin'
    with pytest.raises(resident_message_contract.MessageIdentityConflict):
        repository.commit_user_message(RESIDENT, changed)


@pytest.mark.parametrize('protocol', [True, False])
def test_legacy_replay_does_not_backfill_channel(repository, protocol):
    mid = 'msg_' + 'b' * 32
    body = {'message_id': mid, 'role': 'user', 'content_parts': [{'type': 'text', 'text': 'old'}]}
    original = resident_input_queue_domain.resident_user_message(RESIDENT, 'old', 'alice', message_id=mid, protocol_input=body)
    repository.commit_user_message(RESIDENT, original)
    replay = resident_input_queue_domain.resident_user_message(RESIDENT, 'old', 'alice', message_id=mid, protocol_input=body if protocol else None, channel='weixin')
    assert 'channel' not in replay['origin']
    assert repository.commit_user_message(RESIDENT, replay) == repository.message(RESIDENT, mid)


def test_channel_rejects_forgery_and_unknown_kind():
    from management_portal.society_service import resident_protocol
    with pytest.raises(ValueError, match='unsupported'):
        resident_protocol.deliver_resident_input({'channel': 'weixin'}, 'alice')
    with pytest.raises(InstructionError):
        InstructionCall.from_payload({'instruction': 'DeliverMessage', 'arguments': {}, 'channel': 'weixin'})
    for channel in ({'kind': 'email'}, {'kind': 'weixin', 'actor': 'alice'}, 'weixin', None):
        with pytest.raises(ValueError):
            validate_message_origin({**message_origin('alice'), 'channel': channel})
    for field in ('origin', 'channel'):
        with pytest.raises(ValueError, match='unsupported'):
            resident_message_contract.validate_delivery_message({
                'role': 'user', 'content_parts': [{'type': 'text', 'text': 'hi'}], field: {'kind': 'weixin'}})
    for entry, actor, channel in [('harness', RESIDENT, 'weixin'), ('web', 'portal', 'weixin'), ('web', 'alice', 'email')]:
        with pytest.raises(InstructionError):
            InstructionContext(caller=actor, entry=entry, channel=channel)
    context = InstructionContext(caller='alice', entry='web', channel='weixin')
    assert InstructionContext.from_payload(context.to_payload()) == context


@pytest.mark.parametrize('channel', ['weixin', 'telegram'])
def test_telepus_client_sets_context_without_adding_body_fields(monkeypatch, channel):
    from management_portal import isa_client
    from management_portal.telepus import portal_client
    captured = []
    monkeypatch.setattr(portal_client, '_prepare_portal_runtime', lambda: None)
    monkeypatch.setattr(isa_client.routing, 'execute_or_raise', lambda payload, context: captured.append((payload, context)) or {})
    body = {'message_id': 'msg_' + 'c' * 32, 'content_parts': [{'type': 'text', 'text': 'hello'}]}
    PortalClient(channel).deliver('alice', RESIDENT, body)
    payload, context = captured[0]
    assert context.caller == 'alice' and context.channel == channel
    assert payload['arguments']['message'] == body
    assert 'channel' not in payload['arguments']


def test_isa_delivery_uses_only_trusted_context(facade):
    from types import SimpleNamespace
    from management_portal.society_service.isa import conversation
    from management_portal.society_service import resident_conversation_stream
    mid = 'msg_' + 'd' * 32
    inv = SimpleNamespace(caller='CASE-A', context=InstructionContext(caller='CASE-A', entry='web', channel='weixin'),
                          argument=lambda name: None, idempotency_key='')
    conversation.deliver(inv, facade['resident_id'], {'message_id': mid, 'content_parts': [{'type': 'text', 'text': 'hello'}]})
    saved = resident_conversation_stream.get_repository().message(facade['resident_id'], mid)
    assert saved['origin']['channel'] == {'kind': 'weixin'}
    assert saved['author_id'] == 'CASE-A'


def test_family_relay_to_member_keeps_source_channel(facade):
    """A routed facade message reaches the chosen member with the source message's channel.

    Routing is a Lineage program since d066: the Router only picks a member, and
    ``RelayMessage`` delivers the facade message with the relay mark
    ``<facade>/message/<id>``. That relay copies the channel, so the member
    answers with the phone channel's formatting hint.
    """
    from management_portal.society_service import family_facade, relay_domain
    from management_portal.society_service import resident_conversation_stream, resident_intake_domain
    facade_id = facade['resident_id']
    _register_test_member(facade, '成员')
    relay_domain.update_facade_members(facade_id, [MEMBER], 'CASE-A')
    original = 'msg_' + 'e' * 32
    resident_intake_domain.deliver_resident_message_content(facade_id, 'CASE-A', 'hello', message_id=original, channel='weixin')
    routed = 'msg_' + 'f' * 32
    resident_intake_domain.deliver_resident_message_content(
        MEMBER, facade_id, 'hello', business_ref=family_facade.relay_mark(facade_id, original), message_id=routed)
    member_input = resident_conversation_stream.get_repository().message(MEMBER, routed)
    assert member_input['origin']['channel'] == {'kind': 'weixin'}
    assert member_input['author_id'] == facade_id
    assert '长回复会被分段' in resident_prompt_domain._sender_line({'actor': facade_id, 'input_message': member_input})
