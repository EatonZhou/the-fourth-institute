"""Addressed facade sends deliver before arrival/Router automation runs."""
from unittest.mock import Mock

import pytest

from tests.cases.test_family_chat_preferences import family_chat, lineage, charters, future_creation_store  # noqa: F401
from tests.cases.isa_test_support import ACTOR
from management_portal.society_service import (
    family_chat_preferences, family_facade, family_quota, lineage_message_events,
    resident_protocol, resident_conversation_stream, family_direct_delivery, resident_intake_domain,
)
REAL_DELIVER_INPUT = resident_protocol.deliver_resident_input
REAL_INTAKE = resident_intake_domain.deliver_resident_message_content


@pytest.mark.parametrize('nested', [False, True])
def test_solo_delivers_before_any_arrival_automation_and_replays_once(family_chat, monkeypatch, nested):
    monkeypatch.setattr(resident_protocol, 'deliver_resident_input', REAL_DELIVER_INPUT)
    monkeypatch.setattr(resident_intake_domain, 'deliver_resident_message_content', REAL_INTAKE)
    facade, member = family_chat
    target = member
    if nested:
        from tests.cases.isa_test_support import ok
        inner = ok('Familize', {'lineage_id': facade['lineage_id'], 'resident_ids': [member]})
        target = inner['facade_resident_id']
        family_chat_preferences.change(target, ACTOR, member, 'solo', True)
    family_chat_preferences.change(facade['resident_id'], ACTOR, target, 'solo', True)
    monkeypatch.setattr(family_quota.execution_port, 'service', lambda: Mock(accounts=lambda: {'accounts': []}))
    repository = resident_conversation_stream.get_repository()
    observed = []

    def wake(record, mid=''):
        if record['resident_id'] == facade['resident_id']:
            link = family_facade.relay_links(facade)[mid]
            assert repository.message(target, link['message_id'])
            observed.append(mid)
        return True

    monkeypatch.setattr(lineage_message_events, 'wake_watchers', wake)
    payload = {'resident_id': facade['resident_id'], 'message': {
        'message_id': 'msg_' + 'a' * 32, 'content_parts': [{'type': 'text', 'text': 'direct request'}]}}
    first = resident_protocol.deliver_resident_input(payload, ACTOR)
    assert first['accepted']
    link = family_facade.relay_links(facade)[first['message_id']]
    assert link['resident_id'] == target
    if nested:
        from management_portal.society_service import resident_record_domain
        assert family_facade.relay_links(resident_record_domain.find_resident_for_admission(target))[link['message_id']]['resident_id'] == member
    assert repository.input_state(facade['resident_id'], first['message_id'])['state'] == 'completed'
    again = resident_protocol.deliver_resident_input(payload, ACTOR)
    assert again['message_id'] == first['message_id']
    assert len(repository.input_states(member)) == 1
    assert observed


@pytest.mark.parametrize('state', ['cancelled', 'completed', 'failed'])
def test_terminal_source_never_direct_delivers(monkeypatch, state):
    from types import SimpleNamespace
    monkeypatch.setattr(resident_conversation_stream, 'get_repository', lambda: SimpleNamespace(
        input_state=lambda *a: {'state': state}))
    monkeypatch.setattr(family_quota, 'relay_message', lambda *a: pytest.fail('terminal input relayed'))
    assert family_direct_delivery.deliver({'resident_id': 'resident_a'}, 'msg_a', ACTOR) is None


def test_failed_direct_attempt_does_not_reject_committed_send(monkeypatch):
    monkeypatch.setattr(family_direct_delivery, 'deliver', Mock(side_effect=PermissionError('revoked')))
    assert family_direct_delivery.resume({'resident_id': 'resident_a'}, 'msg_a', ACTOR) is None
