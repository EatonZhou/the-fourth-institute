"""Default routes reuse one observed catalog; no live accounts or Provider calls."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from management_portal.execution import account_registrations, account_selection, accounts, isa


@pytest.mark.parametrize('detail', ['', 'codex-low'])
@pytest.mark.parametrize('state', ['available', 'exhausted', 'paid', 'api_only'])
def test_default_routes_reuse_full_observation(monkeypatch, detail, state):
    samples, registrations = [], []
    for provider in ('codex', 'claude'):
        for suffix, remaining in [('low', 20), ('high', 80)]:
            name = f'{provider}-{suffix}'
            kind = 'api_profile' if state == 'api_only' else 'subscription_login'
            used_up = state in {'exhausted', 'paid'}
            samples.append({'account_id': name, 'provider': provider, 'backend_route_id': name,
                            'kind': kind, 'enabled': True, 'login_state': 'logged_in',
                            'quota_reserve': {'status': 'reserved' if used_up else 'available',
                                              'fresh': True, 'used_up': used_up,
                                              'remaining': 0 if used_up else remaining,
                                              'automatic_paused': state == 'exhausted',
                                              'credits_active': state == 'paid'}})
            registrations.append({'account_id': name, 'provider': provider, 'route_id': name,
                                  'kind': kind, 'enabled': True, 'version': 1})
    calls = {'snapshot': 0, 'observations': 0, 'registrations': 0}
    def snapshot(refresh, *, account_facts=None):
        calls['snapshot'] += 1
        if account_facts is not None:
            account_facts.update(registrations=deepcopy(registrations), login_states={r['account_id']: 'logged_in' for r in registrations})
        return {'accounts': deepcopy(samples)}
    def observations():
        calls['observations'] += 1
        return deepcopy(samples)
    monkeypatch.setattr(isa, '_accounts_snapshot', snapshot)
    monkeypatch.setattr(account_selection, 'observations', observations)
    def read_registrations(provider=None):
        calls['registrations'] += 1
        return [r for r in registrations if not provider or r['provider'] == provider]
    monkeypatch.setattr(account_registrations, 'registrations', read_registrations)
    monkeypatch.setattr(isa, '_registration_public', lambda row, actor, **kw: {**row, 'login_state': 'logged_in'})

    class Adapter:
        def __init__(self, provider): self.provider = provider
        def accounts(self):
            return tuple(accounts.ExecutionAccount(r['account_id'], self.provider, r['route_id'], r['account_id'], kind=r['kind'])
                         for r in registrations if r['provider'] == self.provider)
        def resolve(self, route_id, options, **kwargs):
            return next(a for a in self.accounts() if a.route_id == route_id)

    catalog = accounts.ExecutionAccounts(tuple(Adapter(p) for p in ('codex', 'claude')), provider_registry=object())
    monkeypatch.setattr(accounts, 'default_execution_accounts', lambda **kw: catalog)
    result = isa.list_provider_accounts(SimpleNamespace(caller='fixture', argument=lambda name: {'refresh': False, 'account_id': detail}.get(name)))
    expected = '' if state in {'exhausted', 'api_only'} else 'high'
    assert result['default_routes'] == {p: f'{p}-{expected}' if expected else '' for p in ('codex', 'claude')}
    assert len(result['accounts']) == (1 if detail else 4)
    assert calls == {'snapshot': 1, 'observations': 0, 'registrations': 0}


def test_non_agent_default_and_registration_only_rows_are_preserved(monkeypatch):
    row = {'account_id': 'unobserved', 'provider': 'provider_api', 'kind': 'api_profile', 'enabled': True, 'version': 1}
    monkeypatch.setattr(isa, '_accounts_snapshot', lambda refresh, **kw: {'accounts': []})
    monkeypatch.setattr(account_registrations, 'registrations', lambda provider=None: [row])
    monkeypatch.setattr(isa, '_registration_public', lambda row, actor, **kw: {**row, 'login_state': 'not_applicable'})
    calls = []
    def resolve(provider):
        calls.append(provider)
        return SimpleNamespace(route_id='unobserved')
    catalog = SimpleNamespace(adapters={'provider_api': SimpleNamespace(accounts=lambda: [row])}, resolve=resolve)
    monkeypatch.setattr(accounts, 'default_execution_accounts', lambda **kw: catalog)
    result = isa.list_provider_accounts(SimpleNamespace(caller='fixture', argument=lambda name: None))
    assert result['default_routes'] == {'provider_api': 'unobserved'}
    assert result['accounts'][0]['registration'] == {**row, 'login_state': 'not_applicable'}
    assert calls == ['provider_api']


def test_select_request_registration_snapshot_and_legacy_default(monkeypatch):
    rows = [dict(account_id=p, route_id=p, provider=p, kind='subscription_login', enabled=True)
            for p in ('codex', 'claude')]
    samples = [{**row, 'login_state': 'logged_in',
                'quota_reserve': {'fresh': True, 'used_up': False, 'remaining': 50}}
               for row in rows]
    queries = []
    def registrations(provider):
        queries.append(provider)
        return [row for row in rows if row['provider'] == provider]
    monkeypatch.setattr(account_registrations, 'registrations', registrations)
    assert account_selection.select('codex', accounts=samples)['account_id'] == 'codex'
    assert queries == ['codex']
    assert account_selection.select('claude', accounts=samples, registered_accounts=rows)['account_id'] == 'claude'
    with pytest.raises(accounts.DefaultAccountUnavailable):
        account_selection.select('codex', accounts=samples, registered_accounts=[])
    with pytest.raises(accounts.DefaultAccountUnavailable):
        account_selection.select('codex', anchor='claude', accounts=samples, registered_accounts=rows)
    disabled = [{**row, 'enabled': False} for row in rows]
    with pytest.raises(accounts.DefaultAccountUnavailable):
        account_selection.select('codex', accounts=samples, registered_accounts=disabled)
    assert queries == ['codex']
