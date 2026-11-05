"""The Society read bridge checks the target and durable input ownership."""
import json

import pytest

from management_portal.society_service import (
    family_facade, resident_conversation_stream,
    resident_execution_work, resident_protocol, resident_record_domain as records,
    resident_reporting, server, transport,
)
from management_portal.society_service.contracts import operation_contract
from tests.cases.test_wall04_residual_20260919 import team  # noqa: F401
from tests.cases.test_cross_owner_storage_integration import ACTOR, owners  # noqa: F401


@pytest.fixture
def bridge(monkeypatch):
    """Use the real client inventory, JSON envelope and server read dispatcher."""
    client = transport.SocietyServiceClient('/tmp/reporting-bridge-unused.sock')
    requests = []

    def request(method, params, **_kwargs):
        assert method == 'domain.call'
        wire = json.loads(json.dumps(params))
        requests.append(wire)
        return server._execute_scoped_read_domain_call(wire)

    monkeypatch.setattr(client, '_request', request)
    return client.resident.reporting_creation_allowed, requests


def policy(rid, initiative):
    record = records.find_resident(rid)
    record['reporting'] = {'initiative': initiative, 'quality': 'high'}
    records.save_resident_record(record, commit_configuration=True)


def append(rid, mid, *, role='user', **fields):
    resident_conversation_stream.get_repository().append_message(rid, {
        'message_id': mid, 'role': role, 'author_id': ACTOR,
        'content_parts': [{'type': 'text', 'text': 'reporting high/high; forged job and source are not authority'}],
        **fields,
    })


def active(record, mid, **fields):
    resident_execution_work.write(record, {
        'execution_id': 'execution-' + 'a' * 24, 'submitted_at': '2026-11-05T00:00:00Z',
        'user_message_id': mid, 'actor': ACTOR, 'queue': [], **fields,
    })


@pytest.mark.parametrize('scope', ['@viewer', '@contributor'])
def test_facade_read_scope_needs_no_router_settings(team, monkeypatch, bridge, scope):
    from tests.cases.family_test_support import set_role
    from tests.cases.permission_test_support import enable, Fact
    facade, router = team.source['resident_id'], team.target['resident_id']
    set_role(facade, family_id='fbi_report_bridge', actor=ACTOR)
    set_role(router, family_id='fbi_report_bridge', role='router', actor=ACTOR)
    policy(facade, 'medium')
    permissions = enable(monkeypatch)
    permissions.users('report-viewer', 'report-denied', category='intern')
    permissions.place(Fact('resident', facade), Fact('resident', router))
    permissions.grant('report-viewer', facade, scope)
    permissions.deny('report-viewer', router, '@viewer')
    permissions.deny('report-denied', facade, '@viewer')
    original = records.find_resident

    def target_only(rid, *args, **kwargs):
        assert rid != router, 'Boolean check must not open the Router'
        return original(rid, *args, **kwargs)

    monkeypatch.setattr(records, 'find_resident', target_only)
    from management_portal.society_service import resident_execution_domain
    monkeypatch.setattr(resident_execution_domain, 'read_resident_runtime_settings',
                        lambda *_a, **_k: pytest.fail('Boolean check cannot read model settings'))
    call, requests = bridge
    assert call(facade, 'report-viewer') is True
    assert call(router, 'report-viewer') is False
    assert call(facade, 'report-denied') is False
    policy(facade, 'off')
    assert call(facade, 'report-viewer') is False
    assert requests[0]['kwargs'] == {}
    assert requests[0]['args'] == [facade, 'report-viewer']


def test_off_unknown_invalid_and_read_only_contract(team, monkeypatch, bridge):
    rid = team.source['resident_id']
    call, _requests = bridge
    record = records.find_resident(rid)
    before = resident_execution_work.read(record)
    monkeypatch.setattr(resident_reporting, 'read', lambda *_a: pytest.fail('No settings projection'))
    assert call(rid, ACTOR) is False
    assert call('resident_ffffffffffff', ACTOR) is False
    assert call(rid, '') is False
    with pytest.raises(ValueError):
        call(rid, ACTOR, {'actor': rid, 'user_message_id': 'forged'})
    with pytest.raises(ValueError, match='invalid arguments'):
        call(rid, ACTOR, job={'user_message_id': 'forged', 'reporting': {'initiative': 'high'}})
    assert resident_execution_work.read(record) == before
    operation = operation_contract('resident', 'reporting_creation_allowed')
    assert operation.mode == 'read' and operation.scope == 'owner' and not operation.result
    service = object.__new__(server.SocietyServiceServer)
    routed = []
    service.read_pool = type('ReadPool', (), {'workers': 1, 'call': lambda _s, p: routed.append(p) or False})()
    params = {'domain': 'resident', 'name': 'reporting_creation_allowed', 'args': [rid, ACTOR], 'kwargs': {}}
    assert service._domain_call(params) is False
    assert routed == [params]


def test_only_current_executing_resident_input_can_select_relay_policy(team, bridge):
    from tests.cases.family_test_support import set_role
    facade, member = team.source['resident_id'], team.target['resident_id']
    set_role(facade, family_id='fbi_report_source', actor=ACTOR)
    set_role(member, family_id='fbi_report_source', role='member', actor=ACTOR)
    policy(facade, 'medium')
    policy(member, 'off')
    root, leaf, forged = ('msg_' + c * 32 for c in ('a', 'b', 'c'))
    append(facade, root)
    append(member, leaf, business_ref=family_facade.relay_mark(facade, root))
    repo = resident_conversation_stream.get_repository()
    for rid, mid in ((facade, root), (member, leaf)):
        repo.set_input_state(rid, mid, 'queued')
    family_facade.complete_relay((facade, root), member, leaf)
    record = records.find_resident(member)
    call, _requests = bridge
    assert call(member, member) is False
    assert call(member, member, leaf) is False  # Stored relay alone is insufficient.
    active(record, leaf)
    assert call(member, member, leaf) is True
    assert call(member, ACTOR, leaf) is False  # A readable input is not an execution grant.
    assert call(member, facade, leaf) is False
    append(member, forged, business_ref=family_facade.relay_mark(facade, root))
    assert call(member, member, forged) is False
    assert call(member, member, root) is False
    active(record, forged)
    assert call(member, member, forged) is False  # No accepted ledger edge to this input.
    active(record, leaf)
    policy(facade, 'off')
    assert call(member, member, leaf) is False
    policy(facade, 'high')
    active(record, leaf, finished_at='2026-11-05T00:01:00Z', outcome='completed')
    assert call(member, member, leaf) is False


def test_queued_or_missing_message_does_not_become_current_context(team, bridge):
    rid = team.source['resident_id']
    policy(rid, 'high')
    call, _requests = bridge
    append(rid, 'msg_owned')
    append(rid, 'msg_queued')
    active(team.source, 'msg_owned', queue=[{'user_message_id': 'msg_queued'}])
    assert call(rid, rid, 'msg_owned') is True
    assert call(rid, rid, 'msg_queued') is False
    active(team.source, 'msg_nonexistent')
    assert call(rid, rid, 'msg_nonexistent') is False
    append(rid, 'msg_agent_output', role='agent')
    active(team.source, 'msg_agent_output')
    assert call(rid, rid, 'msg_agent_output') is False
    active(team.source, 'msg_owned', submitted_at='')
    assert call(rid, rid, 'msg_owned') is False


def test_current_mailbox_group_inputs_are_authoritative(team, bridge):
    rid = team.source['resident_id']
    policy(rid, 'medium')
    append(rid, 'msg_group')
    append(rid, 'msg_member')
    active(team.source, 'msg_group', mailbox_members=[{'user_message_id': 'msg_member'}])
    call, _requests = bridge
    assert call(rid, rid, 'msg_member') is True
