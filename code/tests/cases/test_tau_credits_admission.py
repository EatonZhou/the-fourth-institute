"""TAU uses the scheduler's acknowledged binding permission for paid work."""
from unittest.mock import Mock
import pytest
from management_portal.execution.service import ExecutionService
from management_portal.test_framework import tau_runtime
from tests.cases.test_tau_runtime import configured  # noqa: F401

read_quota=tau_runtime.reserved_account_quota

RESIDENT='resident_0123456789ab'
BINDING='resident:'+RESIDENT

@pytest.fixture
def quota_service(monkeypatch):
    reserved={'status':'reserved','remaining_percent':0.0,'used_up':True}
    service=Mock(spec=ExecutionService)
    service.accounts.return_value={'accounts':[{'account_id':'codex_two','backend_route_id':'two','quota_reserve':reserved,
        'credits':{'has_credits':True,'balance':43285.6},'login_state':'logged_in'}]}
    monkeypatch.setattr('management_portal.execution.service.ExecutionService',lambda:service)
    return service, reserved

def test_paid_binding_is_not_rejected_by_account_reserve(quota_service):
    service,_=quota_service
    service.binding_quota_states.return_value={BINDING:{'state':'open','override_enabled':True,'account_id':'codex_two'}}
    assert tau_runtime.reserved_account_quota('two',resident_id=RESIDENT) is None
    service.binding_quota_states.assert_called_once_with({BINDING:'codex_two'})

@pytest.mark.parametrize('gate',[
    {'state':'closed','override_enabled':False,'account_id':'codex_two'},
    {'state':'open','override_enabled':False,'account_id':'codex_two'},
    {'state':'closed','reason':'account_disabled','override_enabled':True,'account_id':'codex_two'},
    {'state':'unknown','override_enabled':True,'account_id':'codex_two'},
    {'state':'open','override_enabled':True,'account_id':'codex_other'},
    {},
])
def test_credits_alone_and_unconfirmed_or_disabled_bindings_stay_reserved(quota_service,gate):
    service,reserved=quota_service
    service.binding_quota_states.return_value={BINDING:gate}
    assert tau_runtime.reserved_account_quota('two',resident_id=RESIDENT)==reserved

def test_missing_resident_has_no_paid_exception(quota_service):
    service,reserved=quota_service
    assert tau_runtime.reserved_account_quota('two',resident_id='')==reserved
    service.binding_quota_states.assert_not_called()

def test_unreachable_binding_reader_does_not_grant_credits(quota_service):
    service,reserved=quota_service
    service.binding_quota_states.side_effect=OSError('unavailable')
    assert tau_runtime.reserved_account_quota('two',resident_id=RESIDENT)==reserved

def test_preparation_honors_existing_paid_binding(configured,quota_service,monkeypatch):
    service,_=quota_service
    monkeypatch.setattr(tau_runtime,'reserved_account_quota',read_quota)
    configured.state.options['source']['account_id']='two'
    configured.state.runtime['service_tier']='priority'
    assignment={**configured.assignment,'backend_route_id':'two'}
    service.binding_quota_states.return_value={BINDING:{'state':'open','override_enabled':True,'account_id':'codex_two'}}
    result=tau_runtime.prepare_configuration(assignment,cancelled=lambda:False,emit=lambda _:None,poll_interval=0)
    assert result['ready']
    configured.restart.assert_not_called()
