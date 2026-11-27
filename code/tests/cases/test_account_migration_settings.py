"""Automatic migration preserves model intent without weakening quota scope.

:module: resident.session
"""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from management_portal.execution.account_migration_settings import migration_settings
from management_portal.execution.account_selection import same_model_candidates, priority_candidates, quota_candidates
from management_portal.society_service import resident_account_policy as policy


LUNA = 'gpt-5.6-luna'
SOL = 'gpt-6.1-sol'
HAIKU = 'claude-haiku-4-5'


def catalog(models, default, efforts=None, defaults=None):
    return dict(fresh=True, models=models, default_model=default,
                efforts_by_model=efforts or {}, default_efforts_by_model=defaults or {})


@pytest.mark.parametrize('source,target,model,effort,cat,expected', [
    ('codex','codex',LUNA,'max',catalog([SOL,LUNA],SOL,{LUNA:['low','max']}),(LUNA,'max')),
    ('codex','claude',LUNA,'max',catalog(['opus',HAIKU],'opus',{HAIKU:['low','high']}),(HAIKU,'high')),
    ('claude','codex',HAIKU,'high',catalog([SOL,LUNA],SOL,{LUNA:['low','high','max']}),(LUNA,'high')),
    ('codex','claude',SOL,'max',catalog(['opus',HAIKU],'opus',{'opus':['high','max']}),('opus','max')),
    ('codex','codex','removed','low',catalog([SOL],SOL,{SOL:['high']},{SOL:'high'}),(SOL,'high')),
    ('codex','codex',LUNA,'ultra',catalog([LUNA],LUNA,{LUNA:['low','xhigh']}),(LUNA,'xhigh')),
    ('codex','codex',LUNA,'',catalog([LUNA],LUNA,{LUNA:['low','max']},{LUNA:'max'}),(LUNA,'max')),
    ('codex','kimi',LUNA,'max',catalog(['kimi'],'kimi'),('kimi','')),
    ('codex','claude',LUNA,'max',catalog(['gateway-model'],'gateway-model'),('gateway-model','')),
    ('codex','claude',LUNA,'future',catalog([HAIKU],HAIKU,{HAIKU:['low','high']},{HAIKU:'low'}),(HAIKU,'low')),
])
def test_model_and_effort_compatibility(source,target,model,effort,cat,expected):
    before=deepcopy(cat)
    assert migration_settings(cat,source_provider=source,target_provider=target,model=model,effort=effort)==dict(model=expected[0],reasoning_effort=expected[1])
    assert cat==before


def account(name, cat, provider='codex'):
    return dict(account_id=name,backend_route_id=name,provider=provider,kind='subscription_login',
                enabled=True,login_state='logged_in',selection_catalog=cat,
                quota_reserve=dict(fresh=True,status='available',remaining=80,windows=[]))


def test_same_kind_fallback_keeps_strict_pinned_model_scope():
    source=account('source',{})
    fallback=account('fallback',catalog([SOL],SOL))
    exact=account('exact',catalog([LUNA],LUNA,{LUNA:['max']}))
    lower = account('lower', catalog([LUNA], LUNA, {LUNA: ['high']}))
    assert same_model_candidates([lower, fallback, exact], source, model=LUNA, effort='max') == [exact, lower]
    assert quota_candidates([fallback],source,model=LUNA,effort='max')==[]


def test_priority_and_restart_intent_select_identical_cross_provider_settings():
    source=account('source',{})
    target=account('target',catalog([HAIKU,'opus'],'opus',{HAIKU:['high']}),'claude')
    assert priority_candidates([target],['claude:subscription_login'],source=source,model=LUNA,effort='max',reserve=5)==[target]
    options=policy.migration_target_options(target,'codex',source_settings=dict(model=LUNA,reasoning_effort='max'))
    assert options==dict(target_provider='claude',target_account='target',context_strategy='preserve',model=HAIKU,reasoning_effort='high')


def test_stale_catalog_is_not_a_settings_authority(monkeypatch):
    target=account('target',dict(fresh=False,models=[SOL],default_model=SOL))
    monkeypatch.setitem(policy.AGENT_PROVIDERS,'codex',SimpleNamespace(catalog=lambda route:catalog([LUNA,SOL],SOL,{LUNA:['max']})))
    result=policy.migration_target_options(target,'codex',source_settings=dict(model=LUNA,reasoning_effort='max'))
    assert (result['model'],result['reasoning_effort'])==(LUNA,'max')


def test_missing_default_cannot_invent_a_model():
    with pytest.raises(ValueError,match='default model'):
        migration_settings(catalog(['x'],'removed'),source_provider='codex',target_provider='kimi',model=LUNA)


def test_restart_reconciliation_preserves_explicit_same_provider_tuple(monkeypatch):
    from management_portal.execution.codex_runtime_settings import _resolve_codex_runtime_configuration
    from management_portal.society_service import resident_restart_domain as restart
    cat=catalog([SOL,LUNA],SOL,{LUNA:['high','max'],SOL:['high']},{SOL:'high'})
    adapter=SimpleNamespace(reconcile=lambda definition,route,settings:_resolve_codex_runtime_configuration(settings,cat))
    definition=SimpleNamespace(runtime_adapter=lambda:adapter,validate_settings=lambda route,model,effort:(model,effort))
    monkeypatch.setattr(restart.resident_runtime_domain,'read_resident_runtime_settings',lambda r:dict(model=LUNA,reasoning_effort='max'))
    from management_portal.execution import resident_configuration
    monkeypatch.setattr(resident_configuration,'normalize_configuration_settings',lambda *a:{})
    target=SimpleNamespace(definition=definition,provider='codex',route='other',source_provider='codex')
    body=policy.migration_target_options(account('other',cat),'codex',source_settings=dict(model=LUNA,reasoning_effort='max'))
    result=restart._target_runtime_settings({},body,target,'case-actor')
    assert (result['model'],result['reasoning_effort'])==(LUNA,'max')


def test_monitor_keeps_target_default_effort_in_migration_catalog():
    from tests.cases.test_account_observation_store import make_monitor
    monitor, account_row, accounts, history = make_monitor()
    accounts.runtime_catalog.return_value=catalog([LUNA],LUNA,{LUNA:['high','max']},{LUNA:'max'})
    monitor.refresh_account(account_row)
    observed=monitor._samples['one']['selection_catalog']
    assert observed['default_efforts_by_model']=={LUNA:'max'}
    assert migration_settings(observed,source_provider='codex',target_provider='codex',model=LUNA)['reasoning_effort']=='max'
    assert accounts.observe.call_count==1
