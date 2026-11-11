"""Fresh business input must use canonical stateless sessions, not a key hint."""
import pytest
from management_portal.execution.requests import build_agent_turn_request
from management_portal.execution.session_manager import SessionManager
from management_portal.execution.contracts import SessionOptions

class NoBindingReads:
    def __getattr__(self,name):
        raise AssertionError('fresh accessed persistent store: '+name)

@pytest.mark.parametrize('provider',['codex','kimi','claude'])
def test_independent_input_prepares_without_old_provider_session(provider):
    request=build_agent_turn_request(actor='reader',agent_id='resident-test',prompt='read authorized material',provider=provider,correlation_id='independent-input',cwd='/tmp',binding_id='',runtime_options={'session_key':''})
    assert request.session==SessionOptions(backend_route_id='')
    prepared=SessionManager(agent_data=NoBindingReads()).prepare(request)
    assert prepared.mode=='stateless' and prepared.backend_session_id==''
    assert prepared.binding_id=='' and prepared.is_new_backend_session

def test_stateless_cannot_import_source_binding():
    with pytest.raises(ValueError,match='cannot declare session identities'):
        SessionOptions(mode='stateless',context_source_binding_id='resident:old').validate()
