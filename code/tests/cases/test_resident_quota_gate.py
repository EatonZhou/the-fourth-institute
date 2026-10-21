"""Quota projection uses the real scheduler and isolated Resident storage.

:design: decision_d40f59f5508f, decision_c524c775e0be
"""
from types import SimpleNamespace
import time
from management_portal.execution import scheduler as scheduler_module

import pytest

from management_portal.execution.service import ExecutionService, ExecutionServiceServer
from management_portal.execution import account_registrations
from management_portal.society_service import resident_catalog, resident_protocol
from management_portal.society_service.domain_ports import execution_port
from tests.cases.test_conversation_account_resolution_batch import catalog, populate, ACTOR
from tests.cases.test_codex_quota_reserve import scheduler, sample, submit, claim
from tests.cases.agent_data_test_support import register_session


def test_idle_quota_gate_preserves_manual_intake_and_original_queue(catalog, scheduler, monkeypatch):
    ids = populate(3, routes=[('codex', 'one'), ('codex', 'two'), ('portal', '')])
    service = SimpleNamespace(binding_quota_states=lambda bindings: scheduler.binding_quota_states(bindings))
    monkeypatch.setattr(execution_port, 'service', lambda: service)
    scheduler.configure_capacity('codex:one', 8, 0, 0, actor=ACTOR, close_threshold={'value': 0, 'unit': '%'})
    scheduler.update_account_quota('codex_one', sample(100))
    scheduler.update_account_quota('codex_two', sample(10))
    before = resident_catalog.get_catalog().get(ids[0])
    items = resident_protocol.get_resident_states(ids, ACTOR)['items']
    assert items[0]['state']['execution_gate']['reason'] == 'quota_used_up'
    assert items[0]['state']['execution_gate']['state'] == 'closed'
    assert items[0]['state']['intake_state'] == 'open'
    assert items[0]['state']['work_state'] == 'idle'
    assert items[0]['controls']['can_send'] is True
    assert '额度' in items[0]['controls']['send_hint']
    assert items[1]['state']['execution_gate']['state'] == 'open'
    assert items[2]['state']['execution_gate']['state'] == 'open'
    assert resident_catalog.get_catalog().get(ids[0]) == before
    original = submit(scheduler, binding_id='resident:' + ids[0])
    assert claim(scheduler)['reason'] == 'quota_reserve'
    scheduler.update_account_quota('codex_one', {'status': 'unknown', 'observed_at': 0})
    with monkeypatch.context() as clock:
        clock.setattr(scheduler_module, 'time', SimpleNamespace(time=lambda: time.time() + 61))
        assert resident_protocol.get_resident_states(ids[:1], ACTOR)['items'][0]['state']['execution_gate']['reason'] == 'quota_recovery_unconfirmed'
    scheduler.update_account_quota('codex_one', sample(10))
    assert resident_protocol.get_resident_states(ids[:1], ACTOR)['items'][0]['state']['execution_gate']['state'] == 'open'
    assert claim(scheduler)['execution']['execution_id'] == original.execution_id


def test_manual_close_override_and_missing_owner_are_distinct(catalog, scheduler, monkeypatch):
    ids = populate(2, routes=[('codex', 'one')])
    store = resident_catalog.get_catalog().state_store
    record = store.resident_record(ids[0]); record['intake_open'] = False
    store.put_resident_record(ids[0], record, commit_intake=True)
    scheduler.update_account_quota('codex_one', sample(100))
    binding = 'resident:' + ids[1]
    scheduler.configure_binding_quota_overrides([{'binding_id': binding, 'revision': 1, 'enabled': True}])
    monkeypatch.setattr(execution_port, 'service', lambda: SimpleNamespace(binding_quota_states=scheduler.binding_quota_states))
    items = resident_protocol.get_resident_states(ids, ACTOR)['items']
    assert items[0]['state']['execution_gate']['reason'] == 'manual'
    assert items[1]['state']['execution_gate']['state'] == 'open'
    assert items[1]['state']['execution_gate']['override_enabled'] is True
    def unavailable(_bindings):
        raise RuntimeError('isolated unavailable execution service')
    monkeypatch.setattr(execution_port, 'service', lambda: SimpleNamespace(binding_quota_states=unavailable))
    items = resident_protocol.get_resident_states(ids, ACTOR)['items']
    assert items[0]['state']['execution_gate']['reason'] == 'manual'
    assert items[1]['state']['execution_gate']['state'] == 'unknown'
    assert items[1]['controls']['can_send'] is True
    assert '确认' in items[1]['controls']['send_hint']


def test_quota_gate_rpc_and_read_access_batch(catalog, scheduler, monkeypatch):
    ids = populate(5, hidden=(4,), routes=[('codex', 'one')])
    scheduler.configure_capacity('codex:one', 8, 0, 0, actor=ACTOR, close_threshold={'value': 2, 'unit': '%'})
    scheduler.update_account_quota('codex_one', sample(99))
    calls = []
    server = SimpleNamespace(scheduler=scheduler)
    def request(method, params, **kwargs):
        assert kwargs == {'timeout': 3.0, 'restart_wait': 0.0, 'retry_on_unavailable': False}
        calls.append((method, params))
        return ExecutionServiceServer.dispatch(server, method, params)
    service = ExecutionService(client=SimpleNamespace(request=request))
    monkeypatch.setattr(execution_port, 'service', lambda: service)
    result = resident_protocol.get_resident_states(ids, ACTOR)['items']
    assert len(result) == 4
    assert len(calls) == 1
    assert set(calls[0][1]['bindings']) == {'resident:' + rid for rid in ids[:4]}
    assert all(x['state']['execution_gate']['state'] == 'closed' for x in result)
    calls.clear()
    assert resident_protocol.get_resident_states([], ACTOR)['items'] == []
    assert calls == []


def test_bound_route_and_only_acknowledged_override_control_projection(catalog, scheduler, monkeypatch):
    rid = populate(1, routes=[('codex', 'one')])[0]
    store = resident_catalog.get_catalog().state_store
    record = store.resident_record(rid)
    register_session(record, 'isolated-session-two', backend_route_id='two')
    scheduler.update_account_quota('codex_one', sample(10))
    scheduler.update_account_quota('codex_two', sample(100))
    policy = {'binding_id': 'resident:' + rid, 'enabled': True, 'revision': 1}
    record['quota_reserve_override'] = policy
    store.put_resident_record(rid, record, commit_configuration=True)
    monkeypatch.setattr(execution_port, 'service', lambda: SimpleNamespace(binding_quota_states=scheduler.binding_quota_states))
    snapshot = resident_protocol.get_resident_conversation_snapshot(rid, ACTOR)
    gate = snapshot['state']['execution_gate']
    assert (gate['account_id'], gate['state'], gate['override_enabled']) == ('codex_two', 'closed', False)
    assert snapshot['controls']['can_send'] is True
    assert snapshot['controls']['send_hint']
    scheduler.configure_binding_quota_overrides([policy])
    updated = resident_protocol.get_resident_state(rid, ACTOR)
    assert updated['state']['execution_gate']['state'] == 'open'
    assert updated['state']['intake_state'] == snapshot['state']['intake_state'] == 'open'
    assert updated['controls']['send_hint'] == ''


def test_disabled_and_missing_accounts_cannot_be_opened_by_quota_override(catalog, scheduler):
    binding = 'resident:resident_000000000001'
    scheduler.update_account_quota('codex_one', sample(10))
    scheduler.configure_binding_quota_overrides([{'binding_id': binding, 'enabled': True, 'revision': 1}])
    account_registrations.update_registration('codex_one', {'enabled': False}, 1, ACTOR)
    assert scheduler.binding_quota_states({binding: 'codex_one'})[binding]['reason'] == 'account_disabled'
    missing = scheduler.binding_quota_states({binding: 'missing'})[binding]
    assert (missing['state'], missing['reason']) == ('unknown', 'account_unavailable')


def test_invalid_or_empty_gate_requests_do_not_read_accounts_or_database(scheduler, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('invalid requests must not read accounts or database')
    monkeypatch.setattr(scheduler.accounts, 'accounts', forbidden)
    monkeypatch.setattr(scheduler, '_connect', forbidden)
    assert scheduler.binding_quota_states({}) == {}
    for invalid in ([], {'invalid binding': 'codex_one'}, {'resident:r': ''},
                    {f'resident:resident_{n:012x}': 'codex_one' for n in range(501)}):
        with pytest.raises(ValueError):
            scheduler.binding_quota_states(invalid)


def test_older_execution_service_is_unknown_not_open(catalog, monkeypatch):
    rid = populate(1, routes=[('codex', 'one')])[0]
    def old_service(_bindings):
        raise ValueError('unsupported ExecutionService method: scheduler.quota_states')
    monkeypatch.setattr(execution_port, 'service', lambda: SimpleNamespace(binding_quota_states=old_service))
    state = resident_protocol.get_resident_state(rid, ACTOR)['state']
    assert state['intake_state'] == 'open'
    assert state['execution_gate'] == {'state': 'unknown', 'reason': 'execution_unavailable'}


def test_disappearing_identity_never_uses_a_default_account(monkeypatch):
    from management_portal.society_service import resident_execution_gate, resident_backend
    def forbidden(*args, **kwargs):
        raise AssertionError('no route or account reads for an absent identity')
    monkeypatch.setattr(resident_backend, 'session_routes_for_bindings', forbidden)
    rid = 'resident_000000000001'
    before = {rid: {'state': {'intake_state': 'open', 'work_state': 'idle'}}}
    result = resident_execution_gate.project_states(before, resident_records={})
    assert result[rid]['state']['execution_gate'] == {'state': 'unknown', 'reason': 'account_unavailable'}
    assert before == {rid: {'state': {'intake_state': 'open', 'work_state': 'idle'}}}


def test_family_facade_uses_live_role_health_not_its_retained_account(catalog, scheduler, monkeypatch):
    """门面不执行模型；角色耗尽与缺失告警联系人仍须真实显示。

    :design: decision_44cf587c1721
    """
    from management_portal.society_service import family_facade, resident_intake_domain
    from tests.cases.family_test_support import set_role

    facade, router, steward = populate(3, routes=[('codex', 'one'), ('codex', 'two'), ('codex', 'two')])
    for rid, role in ((facade, 'facade'), (router, 'router'), (steward, 'steward')):
        set_role(rid, family_id='fbi_quota_gate', role=role, actor=ACTOR)
    queried = []
    def quota_states(bindings):
        queried.append(dict(bindings))
        return scheduler.binding_quota_states(bindings)
    monkeypatch.setattr(execution_port, 'service', lambda: SimpleNamespace(binding_quota_states=quota_states))
    for route in ('one', 'two'):
        scheduler.configure_capacity('codex:' + route, 8, 0, 0, actor=ACTOR,
                                     close_threshold={'value': 0, 'unit': '%'})
    scheduler.update_account_quota('codex_one', sample(100))
    scheduler.update_account_quota('codex_two', sample(20))
    record = resident_catalog.get_catalog().get(facade)
    projected = resident_protocol.get_resident_state(facade, ACTOR)
    assert projected['state']['execution_gate'] == {'state': 'open', 'reason': ''}
    assert projected['controls']['send_hint'] == ''
    assert queried == []  # No account lookup or admission override for the facade.
    presentation = resident_protocol.get_resident_conversation_snapshot(facade, ACTOR)['family_facade']
    roles = presentation['nodes']
    assert {n['availability']['status'] for n in roles} == {'available'}
    assert presentation['alert_contact']['status'] == 'missing'
    assert queried == [{'resident:' + router: 'codex_two', 'resident:' + steward: 'codex_two'}]

    # No migration ledger exists yet. The live gate still exposes the failure.
    scheduler.update_account_quota('codex_two', sample(100))
    roles = resident_protocol.get_resident_conversation_snapshot(facade, ACTOR)['family_facade']['nodes']
    assert {n['availability']['status'] for n in roles} == {'unavailable'}
    assert all('额度已用尽' in n['availability']['reason'] for n in roles)
    assert family_facade.chat_presentation(record)['alert_contact']['status'] == 'missing'
    assert resident_protocol.get_resident_state(facade, ACTOR)['state']['execution_gate']['state'] == 'open'

    # A physical-account problem never undoes the user's explicit intake switch.
    resident_intake_domain._commit_resident_intake_state(record, 'closed')
    assert resident_protocol.get_resident_state(facade, ACTOR)['state']['execution_gate'] == {
        'state': 'closed', 'reason': 'manual'}


def test_family_role_unknown_quota_is_not_projected_as_healthy(catalog, monkeypatch):
    from management_portal.society_service import family_facade
    from tests.cases.family_test_support import set_role
    facade, router = populate(2, routes=[('codex', 'one')])
    set_role(facade, family_id='fbi_missing_role', actor=ACTOR)
    set_role(router, family_id='fbi_missing_role', role='router', actor=ACTOR)
    def unavailable(_bindings):
        raise OSError('execution service unavailable')
    monkeypatch.setattr(execution_port, 'service', lambda: SimpleNamespace(binding_quota_states=unavailable))
    roles = {n['role']: n for n in family_facade.chat_presentation(resident_catalog.get_catalog().get(facade))['nodes']}
    assert roles['router']['availability']['status'] == 'unknown'
    assert '执行服务暂不可达' in roles['router']['availability']['reason']
    assert roles['steward']['availability']['status'] == 'unavailable'
    assert '角色不存在' in roles['steward']['availability']['reason']
