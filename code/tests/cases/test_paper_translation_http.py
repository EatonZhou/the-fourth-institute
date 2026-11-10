"""Real temporary account cookies/PG, production routes; no external requests."""
from copy import deepcopy
import json
from types import SimpleNamespace
from urllib.parse import urlsplit
import pytest

from management_portal import http_paper_translation, paper_translation_service, web_accounts, http_auth
from management_portal.portal_access_control import PortalAccessStore
from management_portal.paper_reader_service import production_service as reader
from tests.cases.test_paper_reader_hooks import setup_paper
from tests.cases.test_portal_security_repair_wave1 import _configure_portal_session
from tests.cases.test_paper_translation_service import FakeExecution, FakeGoogle


def test_formal_cookie_principal_csrf_private_cache_and_account_freeze(tmp_path, monkeypatch):
    paper, bundle, store = setup_paper(tmp_path, monkeypatch)
    access = PortalAccessStore()
    from management_portal import personal_ama
    monkeypatch.setattr(personal_ama, 'provision_account', lambda *a,**kw:None)
    for user in ('reader_one','reader_two'): access.upsert_user(user, actor='CASE')
    _configure_portal_session(tmp_path, monkeypatch, access)
    token = web_accounts._issue_session_token('reader_one')
    token2 = web_accounts._issue_session_token('reader_two')
    document = reader().open('reader_one', paper)['document']
    document = {k:document[k] for k in ('paper_id','attachment_id','attachment_version')}
    monkeypatch.setattr(paper_translation_service, '_production', None)
    owner = paper_translation_service.production_service()
    owner.google, owner.execution = FakeGoogle(), FakeExecution()
    definitions = http_paper_translation.production_routes()
    def call(method='POST', path='/paper/api/reader/translation', payload=None, cookie=token, principal='reader_one', origin='http://fixture'):
        parsed = urlsplit(path)
        definition = next(d for d in definitions if d.method==method and d.path==parsed.path)
        body = json.dumps(payload).encode()
        import io
        request = SimpleNamespace(headers={'Cookie':http_auth._SESSION_COOKIE+'='+cookie, 'X-Reader-Principal':principal,
            'Host':'fixture','Origin':origin,'Content-Type':'application/json','Content-Length':str(len(body))}, rfile=io.BytesIO(body),
            client_address=('127.0.0.1',1))
        return definition.handler(request, parsed, '')
    from management_portal import http_responses
    monkeypatch.setattr(http_responses, '_serve_json', lambda req,status,value:(status,value))
    # Bind after monkeypatch: production route keeps its injected responder.
    definitions = http_paper_translation.production_routes()
    payload = dict(operation_id='op1', text='The experiment uses three samples.',
        source_language='en',target_language='zh-CN',mode='google',document=document)
    assert call(payload=payload,cookie='')[0] == 401
    assert call(payload=payload,cookie=token2)[0] == 409
    assert call(payload=payload,origin='http://evil')[0] == 403
    bad = deepcopy(payload); bad['actor']='reader_two'
    assert call(payload=bad)[0] == 400
    code, first = call(payload=payload)
    assert code == 200 and first['status']=='succeeded'
    assert first['provider']=='google' and first['total_ms'] >= first['google_request_ms'] - 12
    other = deepcopy(payload); other['operation_id']='op2'
    assert call(payload=other)[1]['cache']=='hit' and owner.google.calls==1
    path='/paper/api/reader/translation?task_id='+first['task_id']
    assert call(method='GET',path=path)[1]['status']=='succeeded'
    assert call(method='GET',path=path,cookie=token2,principal='reader_two')[0]==403
    access.upsert_user('reader_one',actor='CASE',status='disabled')
    assert call(method='GET',path=path)[0]==401
