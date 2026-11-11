import urllib.error
import pytest
from tests.cases.test_paper_translation_service import owner,ask
from management_portal.paper_translation_service import ReaderTranslationService
from tests.cases.test_paper_translation_auto_chain import failure

@pytest.mark.parametrize('status',[402,429])
def test_google_known_rejection_uses_deepseek(owner,status):
    owner.google.fail=urllib.error.HTTPError('https://example.invalid',status,'rejected',{},None)
    result=ask(owner)
    assert result['status']=='succeeded' and result['provider']=='deepseek'
    assert owner.google.calls==1 and owner.execution.submits==1

@pytest.mark.parametrize('status',[402,429])
def test_recorded_deepseek_rejection_admits_kimi(status):
    assert ReaderTranslationService._fallback_reason(failure('http_error',status))=='http_'+str(status)

def test_unknown_response_timeout_does_not_charge_next_source():
    assert ReaderTranslationService._fallback_reason(failure('read_timeout')) is None


def test_google_connection_refused_falls_back(owner):
    owner.google.fail=urllib.error.URLError(ConnectionRefusedError('refused'))
    assert ask(owner)['provider']=='deepseek'

def test_all_stages_rejected_explains_each_in_chinese(owner):
    from tests.cases.test_paper_translation_auto_chain import setup
    setup(owner, failure('http_error',429))
    owner.google.fail=urllib.error.HTTPError('https://example.invalid',429,'rejected',{},None)
    owner.execution.status=lambda *a,**kw:failure('http_error',429)
    result=ask(owner)
    assert result['status']=='failed' and owner.execution.submits==2
    message=result['error']['message']
    assert all(name+'：' in message for name in ['Google','DeepSeek','Kimi'])
    assert '限流' in message and 'no automatic fallback' not in message

@pytest.mark.parametrize('status',[401,403])
def test_portal_denial_never_bypassed_even_with_forged_http_status(status):
    assert ReaderTranslationService._fallback_reason(failure('http_error',status,code='permission_denied')) is None
