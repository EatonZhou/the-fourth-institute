"""Global Resident policy and authoritative admission, not snapshot ACL proof."""
from types import SimpleNamespace
import pytest
from management_portal.execution.scheduler_admission import Admission

@pytest.mark.parametrize('disabled,paused,dispatch,expected',[
    (True,False,0,'account_disabled'),(False,True,0,'quota_reserve'),
    (False,False,101,'dispatch_interval'),(False,False,0,'ready')])
def test_actual_admission_blocks_even_after_candidate_selection(disabled,paused,dispatch,expected):
    owner=Admission(SimpleNamespace(for_request=lambda request:SimpleNamespace(account_id='chosen')),capacity=SimpleNamespace(capacity_key=lambda request:'account:chosen'),quota=None,active_requests=None)
    data={'now':100,'disabled_accounts':{'chosen'} if disabled else set(),'quotas':{'account:chosen':{'automatic_paused':paused}},'overrides':{},'limits':{'account:chosen':2},'active_counts':{'account:chosen':0},'dispatch':{'account:chosen':{'next_dispatch_at':dispatch}},'active_bindings':set()}
    assert owner.admission_reason({'session':{'binding_id':'resident:any-business'}},0,False,data)==expected

@pytest.mark.parametrize('role',['general-research','non-paper-custom-role'])
def test_fixed_true_is_global_and_ignores_site_and_family_order(monkeypatch,role):
    from management_portal.society_service import family_quota,resident_default_backend
    monkeypatch.setattr(resident_default_backend,'selection_priority',lambda record:pytest.fail('fixed+true must not ask for cross-provider priority'))
    record={'execution_settings':{'account_selection':'fixed','auto_switch_on_quota':True,'quota_switch_strategy':'same_provider_model','backend_role':role},'family':{'role':'member','family_id':'any-family'}}
    assert family_quota.auto_migration_priority(record)==[]
    assert family_quota.quota_switch_policy(record) is True
    record['execution_settings']['auto_switch_on_quota']=False
    assert family_quota.quota_switch_policy(record) is False
