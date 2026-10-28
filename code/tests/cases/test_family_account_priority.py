"""Family account kinds, priority and quota eligibility.

:module: lineage.hatg
"""
import pytest
from management_portal.society_service.resident_account_policy import fallback_account
from management_portal.society_service.templates.familize import _values


def account(provider, route, kind='subscription_login', remaining=80, api=''):
    return dict(provider=provider, backend_route_id=route, account_id=route, kind=kind,
                billing_account_id=api, quota_reserve=dict(status='available', fresh=True,
                    remaining=remaining, windows=[dict(window='five_hour', remaining_percent=remaining)]))


def test_cc_subscription_does_not_prefer_cc_api_over_codex():
    source=account('claude','claude_one',remaining=0)
    api=account('claude','deepseek','api_profile',api='deepseek_primary')
    codex=account('codex','codex_one')
    assert fallback_account([source,api,codex], 'claude','claude_one',[], reserve=True)==codex


def test_priority_same_kind_and_unavailable_accounts():
    source=account('claude','claude_one',remaining=0)
    same=account('claude','claude_two')
    codex=account('codex','codex_one')
    kimi=account('kimi','kimi_one','service')
    order=['kimi:service','codex:subscription_login']
    choices=[source,same,codex,kimi]
    def pick(tried):
        return fallback_account(choices,'claude','claude_one',tried,reserve=True,priority=order)
    assert pick([])==same
    assert pick(['claude_two'])==kimi
    kimi['quota_reserve']['windows'][0]['remaining_percent']=5
    assert pick(['claude_two'])==codex


@pytest.mark.parametrize('value', ['codex', ['codex:subscription_login']*2, ['bad key'], [42]])
def test_invalid_priority_rejected(value):
    with pytest.raises(ValueError):
        _values({'auto_migrate_priority':value})


def test_priority_round_trip_values():
    order=['codex:subscription_login','claude:api_profile:deepseek_primary']
    assert _values({'auto_migrate_priority':order})['auto_migrate_priority']==order


def test_api_types_share_identity_only_with_the_same_api():
    from management_portal.execution.account_migration_types import migration_type
    first = account('claude', 'route_one', 'api_profile', api='deepseek_primary')
    second = account('claude', 'route_two', 'api_profile', api='deepseek_primary')
    other = account('claude', 'route_three', 'api_profile', api='openrouter_primary')
    assert migration_type(first)['id'] == migration_type(second)['id']
    assert migration_type(first)['id'] != migration_type(other)['id']
    assert 'deepseek_primary' in migration_type(first)['label']


def test_different_api_on_same_harness_uses_its_own_model(monkeypatch):
    from types import SimpleNamespace
    from management_portal.society_service import resident_account_policy as policy
    monkeypatch.setitem(policy.AGENT_PROVIDERS, 'claude', SimpleNamespace(validate_settings=lambda route, model: ('deepseek-chat', {})))
    source = account('claude', 'subscription')
    target = account('claude', 'deepseek', 'api_profile', api='deepseek_primary')
    assert policy.migration_target_options(target, 'claude', source)['model'] == 'deepseek-chat'


from tests.cases.test_family_familize import lineage, charters, resident, graph, ok, future_creation_store


def test_priority_persists_and_unrelated_update_preserves_it(lineage, charters):
    from management_portal.society_service.family_storage import auto_migration_priority
    from management_portal.society_service.resident_record_domain import find_resident
    member = resident(lineage, 'Member')
    result = ok('Familize', {'lineage_id': lineage, 'resident_ids': [member]})
    family_id = result['family_id']
    order = ['kimi:service', 'codex:subscription_login', 'claude:api_profile:deleted_api']
    assert next(b['family'] for b in graph(lineage)['function_blocks'] if b['id'] == family_id)['auto_migrate_priority'] == []
    ok('UpdateFamily', {'lineage_id': lineage, 'family_id': family_id, 'auto_migrate_priority': order})
    ok('UpdateFamily', {'lineage_id': lineage, 'family_id': family_id, 'goal': 'New goal'})
    assert auto_migration_priority(find_resident(member)) == order
    assert next(b['family'] for b in graph(lineage)['function_blocks'] if b['id'] == family_id)['auto_migrate_priority'] == order
    ok('UpdateFamily', {'lineage_id': lineage, 'family_id': family_id, 'auto_migrate_priority': []})
    assert auto_migration_priority(find_resident(member)) == []

from tests.cases.test_family_quota import quota_family, team, owners, _record


def test_admission_uses_family_type_order(quota_family, monkeypatch):
    from types import SimpleNamespace
    from management_portal.society_service import family_quota, resident_input_queue_domain, resident_restart_domain
    from management_portal.society_service import resident_account_policy as policy
    from management_portal.society_service.state_store import get_lineage_data_store
    f = quota_family
    resident_input_queue_domain.commit_resident_intake_state(_record(f.steward), 'open')
    accounts = [account('codex','one', remaining=0), account('claude','claude_one'), account('kimi','kimi_one','service')]
    get_lineage_data_store().database.write(lambda c: c.execute("UPDATE families SET auto_migrate_priority=%s WHERE family_id='fbi_test'", (['kimi:service','claude:subscription_login'],)))
    monkeypatch.setitem(policy.AGENT_PROVIDERS, 'kimi', SimpleNamespace(validate_settings=lambda route, model: ('kimi-default', '')))
    calls = []
    monkeypatch.setattr(resident_restart_domain, 'restart_resident_session', lambda *a, **k: calls.append(a[2]))
    assert family_quota.ensure_role_reserve(_record(f.steward), accounts)
    assert calls[0]['target_provider'] == 'kimi'
    assert calls[0]['model'] == 'kimi-default'


def test_priority_schema_migration_preserves_family_switch(quota_family):
    from scripts.migrate_family_account_priority import upgrade
    from management_portal.society_service.state_store import get_lineage_data_store, lineage_data_schema_matches
    with get_lineage_data_store().database.transaction() as c:
        c.execute('ALTER TABLE families DROP COLUMN auto_migrate_priority')
        c.execute("UPDATE lineage_meta SET value='tfi.lineage-data.v63' WHERE key='schema'")
        c.execute("UPDATE families SET auto_migrate=false WHERE family_id='fbi_test'")
        assert upgrade(c)['schema'] == 'tfi.lineage-data.v64'
        assert lineage_data_schema_matches(c)
        row = c.execute("SELECT auto_migrate, auto_migrate_priority FROM families WHERE family_id='fbi_test'").fetchone()
        assert dict(row) == {'auto_migrate': False, 'auto_migrate_priority': []}
        assert upgrade(c)['previous_schema'] == 'tfi.lineage-data.v64'
