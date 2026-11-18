"""Automatic callers freeze the current model/effort in their restart intent."""
from types import SimpleNamespace

import pytest

from management_portal.society_service import family_quota, resident_record_domain as records
from management_portal.society_service import resident_restart_domain as restart, resident_work_recovery as recovery
from management_portal.society_service import resident_input_queue_domain
from management_portal.society_service.domain_ports import execution_port
from tests.cases.test_family_quota import quota_family, team, owners, _record  # noqa: F401
from tests.cases.test_resident_quota_recovery_switch import exhausted  # noqa: F401
from tests.cases.test_account_migration_settings import LUNA, SOL, catalog


def configure(rid, accounts):
    record=_record(rid)
    record['execution_settings'].update(model=LUNA,reasoning_effort='max')
    records.save_resident_record(record,commit_configuration=True)
    for account in accounts:
        account['selection_catalog']=catalog([SOL,LUNA],SOL,{LUNA:['high','max'],SOL:['high']})
    return _record(rid)


@pytest.mark.parametrize('exhausted_gate',[False,True])
def test_family_reserve_and_quota_gate_keep_luna_max(quota_family,monkeypatch,exhausted_gate):
    f=quota_family
    record=configure(f.steward,f.accounts)
    resident_input_queue_domain.commit_resident_intake_state(record,'open')
    if exhausted_gate:
        record=_record(f.steward)
        record['execution_settings'].pop('auto_switch_on_quota',None)
        records.save_resident_record(record,commit_configuration=True)
        f.accounts[0]['quota_reserve'].update(used_up=True,remaining=0)
        gate=dict(state='closed',reason='quota_used_up',account_id=f.accounts[0]['account_id'])
        monkeypatch.setattr(execution_port,'service',lambda:SimpleNamespace(accounts=lambda:{'accounts':f.accounts},binding_quota_states=lambda ids:{'resident:'+f.steward:gate}))
    calls=[]
    monkeypatch.setattr(restart,'restart_resident_session',lambda rid,actor,payload,**kwargs:calls.append(payload))
    assert family_quota.ensure_role_reserve(_record(f.steward),f.accounts)
    assert len(calls)==1
    assert (calls[0]['model'],calls[0]['reasoning_effort'])==(LUNA,'max')
    assert calls[0]['context_strategy']=='preserve'
    assert _record(f.steward)['backend_route_id']=='one'


def test_failed_input_recovery_freezes_luna_max(exhausted):
    f=exhausted
    record=configure(f.rid,f.accounts)
    assert recovery.reconcile_failed_work([record])==1
    payload,=f.calls
    assert (payload['model'],payload['reasoning_effort'])==(LUNA,'max')
    assert payload['idempotency_key']==f'work-recovery:{f.mid}:1'
    assert _record(f.rid)['backend_route_id']=='one'


def test_preventive_catalog_failure_is_durable_and_preserves_source(quota_family, monkeypatch):
    f=quota_family
    resident_input_queue_domain.commit_resident_intake_state(_record(f.steward),'open')
    monkeypatch.setattr(family_quota,'migration_target_options',lambda *a,**kw:(_ for _ in ()).throw(ValueError('catalog unavailable')))
    monkeypatch.setattr(restart,'restart_resident_session',lambda *a,**kw:pytest.fail('must not start migration'))
    assert family_quota.ensure_role_reserve(_record(f.steward),f.accounts)
    assert family_quota.role_migration(_record(f.steward))['switch_reason']=='migration_failed'
    assert _record(f.steward)['backend_route_id']=='one'


def test_recovery_cannot_overwrite_settings_changed_during_selection(exhausted,monkeypatch):
    f=exhausted
    record=configure(f.rid,f.accounts)
    choose=recovery.migration_target_options
    def concurrent_update(*args,**kwargs):
        options=choose(*args,**kwargs)
        current=_record(f.rid)
        current['execution_settings']['model']=SOL
        records.save_resident_record(current,commit_configuration=True)
        return options
    monkeypatch.setattr(recovery,'migration_target_options',concurrent_update)
    assert recovery.reconcile_failed_work([record])==0
    assert not f.calls
    assert _record(f.rid)['execution_settings']['model']==SOL
