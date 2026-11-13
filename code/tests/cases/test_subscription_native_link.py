"""Subscription identity persists independently of stateless binding/runtime data."""
from dataclasses import replace
import pytest

from management_common.json_columns import json_value
from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.execution.agent_data_projection import AgentDataProjector
from management_portal.execution.contracts import SessionOptions
from tests.cases.test_agent_data_projection import _turn_request, _runtime_session, _invocation


def links(store, execution_id):
    with store.database.connect() as c:
        return [json_value(r['data_json']) for r in c.execute(
            "SELECT data_json FROM agent_operations WHERE kind='subscription_native_link' "
            "AND data_json::jsonb->>'execution_id'=%s", (execution_id,))]


def setup(monkeypatch, provider='codex', kind='subscription_login'):
    from management_portal.execution import account_registrations
    calls=[]
    def registrations(name):
        calls.append(name)
        return [{'kind':kind,'account_id':'subscription','route_id':'selected'}]
    monkeypatch.setattr(account_registrations,'registrations',registrations)
    request=replace(_turn_request('agent_native_link'),provider=provider,
                    session=SessionOptions(mode='stateless',backend_route_id='selected'))
    session=replace(_runtime_session(request,''),mode='stateless',backend_route_id='selected')
    store=AgentDataStore();projector=AgentDataProjector(store)
    projector.begin(request,session)
    projector.observe_invocation(request.execution_id,_invocation(request,session))
    return store,projector,request,calls


@pytest.mark.parametrize('provider',['codex','claude','kimi','antigravity'])
def test_subscription_native_identity_survives_stateless_isolation(monkeypatch,provider):
    store,projector,request,calls=setup(monkeypatch,provider)
    for _ in range(10):
        projector.observe_runtime_event(request.execution_id,'provider_input_bound',
            {'provider':provider,'turn_id':'native-turn','backend_session_id':'native-session',
             'backend_route_id':'selected'})
    projector.complete(execution_id=request.execution_id,backend_session_id='native-session',
        backend_route_id='selected',turn_id='native-turn',response='done',usage={'total_tokens':10})
    projector.discard(request.execution_id)
    facts=links(store,request.execution_id)
    assert len(facts)==1
    assert facts[0]['native_session_id']=='native-session' and facts[0]['native_turn_id']=='native-turn'
    assert facts[0]['account_id']=='subscription' and facts[0]['identity_status']=='observed'
    assert set(facts[0]['raw_locator'])=={'account_id','native_session_id'}
    assert len(calls)==1
    with store.database.connect() as c:
        row=c.execute('SELECT p.backend_session_id FROM executions e JOIN provider_sessions p '
                      'USING(provider_session_ref) WHERE e.execution_id=%s',(request.execution_id,)).fetchone()
    assert row['backend_session_id']==request.execution_id


@pytest.mark.parametrize('kind',['api_profile','service'])
def test_non_subscription_accounts_do_not_gain_new_link_facts(monkeypatch,kind):
    store,projector,request,calls=setup(monkeypatch,kind=kind)
    projector.complete(execution_id=request.execution_id,backend_session_id='native-session',
        backend_route_id='selected',turn_id='turn',response='done')
    assert links(store,request.execution_id)==[]


def test_missing_native_id_is_explicit_not_a_fake_execution_session(monkeypatch):
    store,projector,request,calls=setup(monkeypatch)
    projector.complete(execution_id=request.execution_id,backend_session_id='',
        backend_route_id='selected',turn_id='turn',response='done')
    fact,=links(store,request.execution_id)
    assert fact['identity_status']=='unavailable' and fact['native_session_id']==''


def test_replayed_completion_is_idempotent_without_in_memory_projection(monkeypatch):
    store,projector,request,calls=setup(monkeypatch)
    values=dict(execution_id=request.execution_id,provider='codex',account_id='subscription',
                backend_route_id='selected',native_session_id='native-session',native_turn_id='turn')
    first=store.record_subscription_native_link(**values)
    assert store.record_subscription_native_link(**values)==first
    assert len(links(store,request.execution_id))==1


def test_write_failure_is_not_silently_cached_as_a_saved_link(monkeypatch):
    store,projector,request,calls=setup(monkeypatch)
    original=store.record_subscription_native_link
    def fail(**identity):
        raise RuntimeError('audit write failed')
    monkeypatch.setattr(store,'record_subscription_native_link',fail)
    details={'provider':'codex','turn_id':'turn','backend_session_id':'native-session','backend_route_id':'selected'}
    with pytest.raises(RuntimeError,match='audit write failed'):
        projector.observe_runtime_event(request.execution_id,'provider_input_bound',details)
    monkeypatch.setattr(store,'record_subscription_native_link',original)
    projector.observe_runtime_event(request.execution_id,'provider_input_bound',details)
    assert len(links(store,request.execution_id))==1


def test_non_subscription_backend_does_not_even_load_subscription_registration(monkeypatch):
    store,projector,request,calls=setup(monkeypatch,provider='dsh',kind='api_profile')
    projector.complete(execution_id=request.execution_id,backend_session_id='dsh-native',
        backend_route_id='selected',turn_id='turn',response='done')
    assert calls==[] and links(store,request.execution_id)==[]
