"""Historical conversation reads remain available before site Backend setup."""
from types import SimpleNamespace

import pytest

from management_portal.execution import account_registrations
from management_portal.society_service import resident_backend, resident_catalog, resident_conversation_stream, resident_protocol
from management_portal.society_service.domain_ports import execution_port
from management_portal.society_service.isa import conversation
from tests.cases.test_conversation_account_resolution_batch import ACTOR, catalog, populate


@pytest.mark.parametrize('route', ['', 'one'])
def test_get_conversation_retains_history_without_default_or_selected_account(catalog, monkeypatch, route):
    rid = populate(1, routes=[('codex', route)])[0]
    for row in account_registrations.registrations('codex'):
        account_registrations.delete_registration(row['account_id'])
    record = resident_catalog.get_catalog().get(rid)
    with pytest.raises(ValueError, match='No enabled default Codex account|unknown Codex account route'):
        resident_backend.execution_account(record)
    repository = resident_conversation_stream.get_repository()
    before = repository.snapshot(rid)
    assert before['messages']

    def no_execution():
        raise AssertionError('History read must not request execution/admission for a missing account')

    monkeypatch.setattr(execution_port, 'service', no_execution)
    monkeypatch.setattr(conversation, 'protocol', lambda: resident_protocol)
    args = {'resident_id': rid, 'history_limit': 50}
    result = conversation.get_conversation(SimpleNamespace(caller=ACTOR, argument=args.get))
    assert result['messages'] == before['messages']
    assert result['resident']['provider'] == 'codex'
    assert result['resident']['account'] == ''
    assert result['state']['execution_gate'] == {'state': 'unknown', 'reason': 'account_unavailable'}
    assert result['controls']['send_hint']
    # The browser uses this additional native settings projection before it
    # renders history. It must have the same read-only missing-account policy.
    from management_portal.society_service.resident_directory_domain import get_resident_session_projection
    from management_portal.resident_chat_projection import project_session
    projection = get_resident_session_projection(rid, ACTOR)
    assert projection['execution_account'] == {}
    assert project_session(projection, ACTOR)['resident_id'] == rid
    assert repository.snapshot(rid) == before
    assert resident_catalog.get_catalog().get(rid) == record
    # Reading did not install a route or weaken the execution-side requirement.
    assert not account_registrations.registrations('codex')
    with pytest.raises(ValueError):
        resident_backend.execution_account(record)
