from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from management_portal.kimi_code_client import KimiCodeClient,KimiCodeTurnResult,KimiCodeTurnFailed

@pytest.mark.parametrize('valid',[True,False])
def test_actual_submission_receipt_is_not_invented(valid):
    c=object.__new__(KimiCodeClient)
    c.update_session_profile=Mock(return_value={});c.get_snapshot=Mock(return_value={})
    receipt={'version':1,'policy':'deny_all','session_id':'s','prompt_id':'p','effective_tools':[],'source':'kimi.native.prompt'}
    c._request=Mock(return_value={'prompt_id':'p','metadata':{'portal_tool_policy':receipt} if valid else {}})
    c._follow_prompt=Mock(return_value=KimiCodeTurnResult('s','p','译文',{},1))
    kwargs=dict(session_id='s',prompt='test',tools=[],live_stream=None,deny_all_tools=True)
    if valid:
        result=c._run_turn(**kwargs);assert result.tool_policy_receipt==receipt
    else:
        with pytest.raises(KimiCodeTurnFailed,match='receipt missing'):c._run_turn(**kwargs)
        c._follow_prompt.assert_not_called()
    assert c._request.call_args.args[2]['deny_all_tools'] is True
