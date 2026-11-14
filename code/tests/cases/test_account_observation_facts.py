"""Request-local account facts, isolated from live login and account databases."""
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace as NS
import threading

import pytest

from management_portal.execution import account_login as login, account_monitor, account_registrations, accounts, isa


@pytest.fixture
def facts_case(monkeypatch):
    rows = [{'account_id': name, 'provider': 'codex', 'kind': 'subscription_login', 'enabled': True, 'version': 1}
            for name in ('known', 'legacy', 'registered_only')]
    states = {'known': 'logged_in', 'registered_only': 'login_expired', 'unregistered': 'logged_in'}
    counts = {'registration': 0, 'batch': 0, 'single': 0, 'probe': 0}
    store = login.LoginSessionStore.__new__(login.LoginSessionStore)
    @contextmanager
    def connection():
        def execute(sql, params):
            if 'ANY' in sql:
                counts['batch'] += 1
                return NS(fetchall=lambda: [{'account_id': key, 'state': states[key]} for key in params[0] if key in states])
            counts['single'] += 1
            return NS(fetchone=lambda: {'state': states[params[0]]} if params[0] in states else None)
        yield NS(execute=execute)
    store.connection = connection
    store.observe = lambda account_id, state, **kw: states.update({account_id: state})
    monkeypatch.setattr(login, 'login_store', lambda: store)
    def registrations(provider=None):
        counts['registration'] += 1
        return deepcopy(rows)
    monkeypatch.setattr(account_registrations, 'registrations', registrations)
    def probe(*args):
        counts['probe'] += 1
        return True
    monkeypatch.setattr(login, 'provider_logged_in', probe)
    from management_portal import permission_system
    monkeypatch.setattr(permission_system, 'allows', lambda *a, **kw: False)
    monkeypatch.setattr(accounts, 'default_execution_accounts', lambda **kw: NS(adapters={}))
    entries = tuple(NS(account_id=name, provider='codex', kind='subscription_login', route_id=name,
                       billing_account_id='', capacity_key=name, max_concurrency=1)
                    for name in ('known', 'legacy', 'unregistered'))
    monitor = account_monitor.AccountMonitor.__new__(account_monitor.AccountMonitor)
    monitor.__dict__.update(scheduler=NS(health=lambda: {}), accounts=NS(accounts=lambda: entries),
                            _lock=threading.RLock(), _samples={}, _refreshing=False,
                            wakeup=threading.Event(), _inflight={}, _refresh_refused=False, _profiles={},
                            _login_store=store, billing=NS(exchange_rates_snapshot=lambda: {}))
    monkeypatch.setattr(isa, '_SERVER', NS(quota_monitor=monitor))
    invoke = NS(caller='fixture', argument=lambda key: None)
    return NS(rows=rows, states=states, counts=counts, store=store, monitor=monitor, invoke=invoke)


def test_list_reuses_registration_and_batch_login_facts(facts_case):
    case = facts_case
    result = isa.list_provider_accounts(case.invoke)
    states = {r['account_id']: r['login_state'] for r in result['accounts']}
    assert states == {'known': 'logged_in', 'legacy': 'logged_in', 'unregistered': 'logged_in', 'registered_only': 'login_expired'}
    assert case.counts == {'registration': 1, 'batch': 1, 'single': 1, 'probe': 1}
    assert set(result) == {'accounts', 'default_routes'}
    case.states['known'] = 'login_expired'
    second = isa.list_provider_accounts(case.invoke)
    assert next(r for r in second['accounts'] if r['account_id'] == 'known')['login_state'] == 'login_expired'
    assert case.counts == {'registration': 2, 'batch': 2, 'single': 1, 'probe': 1}


def test_monitor_is_passive_and_missing_is_not_a_known_logged_out(facts_case):
    case = facts_case
    facts = {}
    result = case.monitor.snapshot(account_facts=facts)
    assert next(r for r in result['accounts'] if r['account_id'] == 'legacy')['login_state'] == 'logged_out'
    assert 'legacy' not in facts['login_states']
    assert 'registered_only' in facts['login_states']
    assert case.counts == {'registration': 1, 'batch': 1, 'single': 0, 'probe': 0}
    assert 'account_facts' not in result


def test_legacy_probe_failure_remains_unavailable(facts_case, monkeypatch):
    def unavailable(*args): raise OSError('synthetic unavailable')
    monkeypatch.setattr(login, 'provider_logged_in', unavailable)
    result = isa.list_provider_accounts(facts_case.invoke)
    assert next(r for r in result['accounts'] if r['account_id'] == 'legacy')['login_state'] == 'unavailable'
    assert 'legacy' not in facts_case.states


def test_legacy_probe_concurrent_state_wins(facts_case, monkeypatch):
    def late_probe(*args):
        facts_case.states['legacy'] = 'login_expired'
        raise OSError('synthetic late failure')
    monkeypatch.setattr(login, 'provider_logged_in', late_probe)
    assert login.get_login_state(facts_case.rows[1], states={}) == 'login_expired'


def test_empty_batch_never_opens_connection(facts_case):
    assert facts_case.store.login_states([]) == {}
    assert facts_case.counts['batch'] == facts_case.counts['single'] == 0


def test_client_fallback_reuses_internal_facts_without_public_leak(facts_case, monkeypatch):
    from management_portal.execution import service_rpc
    from management_portal.execution.service import ExecutionService
    requests = []
    def request(method, params):
        requests.append((method, params))
        assert method == 'accounts.list'
        return service_rpc.list_accounts(NS(quota_monitor=facts_case.monitor), params)
    service = ExecutionService(client=NS(request=request))
    monkeypatch.setattr(isa, '_SERVER', None)
    monkeypatch.setattr(isa, '_client', lambda: service)
    result = isa.list_provider_accounts(facts_case.invoke)
    assert set(result) == {'accounts', 'default_routes'}
    assert facts_case.counts == {'registration': 1, 'batch': 1, 'single': 1, 'probe': 1}
    ordinary = service.accounts()
    assert 'account_facts' not in ordinary
    assert requests == [('accounts.list', {'force_refresh': False, 'include_read_facts': True}),
                        ('accounts.list', {'force_refresh': False})]


@pytest.mark.parametrize('remote', [False, True])
def test_facts_keep_caller_authorization_and_refresh(facts_case, monkeypatch, remote):
    from management_portal import permission_system
    from management_portal.execution import account_hosting, service_rpc
    from management_portal.execution.service import ExecutionService
    case = facts_case
    calls = []
    case.monitor.request_refresh = lambda: calls.append('refresh')
    if remote:
        service = ExecutionService(client=NS(request=lambda method, params:
            service_rpc.list_accounts(NS(quota_monitor=case.monitor), params)))
        monkeypatch.setattr(isa, '_SERVER', None)
        monkeypatch.setattr(isa, '_client', lambda: service)
    monkeypatch.setattr(permission_system, 'allows', lambda actor, action: actor == 'manager')
    monkeypatch.setattr(account_hosting, 'account_home', lambda *a: '/synthetic/home')
    monkeypatch.setattr(account_hosting, 'codex_socket_path', lambda *a: '/synthetic/socket')
    for actor in ('manager', 'viewer'):
        result = isa.list_provider_accounts(NS(caller=actor, argument=lambda key: key == 'refresh'))
        assert set(result) == {'accounts', 'default_routes'}
        for item in result['accounts']:
            if 'registration' in item:
                assert ('home_path' in item['registration']) == (actor == 'manager')
                assert ('socket_path' in item['registration']) == (actor == 'manager')
            assert 'account_facts' not in item
    assert calls == ['refresh', 'refresh']
    assert case.counts == {'registration': 2, 'batch': 2, 'single': 1, 'probe': 1}
