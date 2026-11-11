"""Temporary PG + actual API request assembly, only the final transport is fake."""
from copy import deepcopy
from types import SimpleNamespace
import pytest
from tests.cases.test_paper_translation_service import owner, ask, request, FakeExecution, SYSTEM
from management_portal.paper_translation_kimi_api import configuration, TranslationConfigurationUnavailable

ENTRY = dict(id='kimi_primary', provider='moonshot', kind='openai_compatible_chat',
    enabled=True, base_url='https://api.moonshot.cn/v1', default_model='kimi-k2.6',
    models=[{'id':'kimi-k2.6','enabled':True}], version=4)

class Registry:
    def __init__(self): self.entry=deepcopy(ENTRY); self.wire=[]
    def get(self, key): assert key=='kimi_primary'; return self.entry
    def configuration_revision(self, key): assert key=='kimi_primary'; return self.entry['version']
    def request(self, key, endpoint, body, **kwargs):
        self.wire.append((key,endpoint,body))
        return {'model':'actual-moonshot-wire','choices':[{'message':{'content':'译文'},'finish_reason':'stop'}]}


def setup(owner):
    registry=Registry()
    owner.kimi_configuration=lambda context:configuration(registry,SYSTEM)
    owner.execution.status=lambda *a,**k: {'status':'completed','result':{'output':{'message':'译文','response':{'model':'wire-kimi','choices':[{'finish_reason':'stop'}]}}}}
    return registry


def test_explicit_kimi_private_cache_and_final_official_wire(owner):
    from management_portal.execution.contracts import ExecutionRequest
    from management_portal.execution.chat_completion_service import ChatCompletionService
    setup(owner)
    result=ask(owner,mode='kimi')
    assert result['provider']=='kimi' and result['model']=='wire-kimi'
    assert result['model_source']=='wire.response.model' and result['requested_model']=='kimi-k2.6'
    assert owner.google.calls==0 and owner.execution.submits==1
    assert ask(owner,'same-text-new-operation',mode='kimi')['cache']=='hit'
    planned=next(iter(owner.execution.requests.values()))
    assert planned.provider=='kimi_primary' and planned.operation=='generate' and planned.session.mode=='stateless'
    registry=Registry()
    invocation=SimpleNamespace(request=planned,user_prompt=planned.prompt,
        system_context='\n'.join(p.content for p in planned.system_instructions.parts),attach_system_context=True)
    ChatCompletionService(registry).execute(invocation,SimpleNamespace(is_cancelled=lambda:False,emit=lambda *a:None))
    key,endpoint,wire=registry.wire[0]
    assert key=='kimi_primary' and endpoint=='chat/completions'
    assert wire['messages']==[{'role':'system','content':planned.system_instructions.parts[0].content},{'role':'user','content':planned.prompt}]
    assert set(wire)=={'model','messages','thinking','max_tokens','stream'}
    assert wire['thinking']=={'type':'disabled'} and wire['stream'] is False
    assert ask(owner,'other-user',mode='kimi',actor='b')['cache']=='miss'


@pytest.mark.parametrize('change',[
    {'enabled':False},{'provider':'openrouter'},{'kind':'anthropic_messages'},
    {'default_model':'unknown'}, {'base_url':'http://api.moonshot.cn/v1'},
    {'base_url':'https://api.moonshot.cn.evil.test/v1'},
    {'default_parameters':{'tools':[]}}])
def test_incompatible_registration_rejected_before_submit(owner,change):
    registry=setup(owner);registry.entry.update(change)
    with pytest.raises(TranslationConfigurationUnavailable):ask(owner,mode='kimi')
    assert owner.execution.submits==0 and owner.google.calls==0


def test_no_configuration_never_borrows_deepseek_or_subscription(owner):
    with pytest.raises(TranslationConfigurationUnavailable):ask(owner,mode='kimi')
    assert owner.execution.submits==0 and owner.google.calls==0


def test_config_revision_miss_wire_missing_model_and_cancel_recovery(owner):
    registry=setup(owner)
    first=ask(owner,mode='kimi');registry.entry['version']=5
    second=ask(owner,'config-change',mode='kimi')
    assert first['task_id']!=second['task_id'] and owner.execution.submits==2
    owner.execution.status=lambda *a,**k:{'status':'running'}
    pending=ask(owner,'pending',mode='kimi',refresh=True);count=owner.execution.submits
    owner.get('a',pending['task_id']);assert owner.execution.submits==count
    result=owner.cancel('a',pending['task_id'])
    assert result['status']=='cancelled' and owner.execution.cancels
    owner.execution.status=lambda *a,**k:{'status':'completed','result':{'output':{'message':'译文','response':{}}}}
    assert ask(owner,'unknown-wire',mode='kimi',refresh=True)['model'] is None
    assert owner.google.calls==0


def test_failed_or_truncated_kimi_never_changes_source_or_caches_success(owner):
    setup(owner)
    owner.execution.status=lambda *a,**k:{'status':'completed','result':{'output':{'message':'截断','response':{'choices':[{'finish_reason':'length'}]}}}}
    result=ask(owner,mode='kimi')
    assert result['status']=='failed' and result['error']['code']=='kimi_incomplete_response'
    ask(owner,'retry-new-id',mode='kimi');assert owner.execution.submits==2 and owner.google.calls==0


def test_registry_revision_owner_does_not_read_secrets(monkeypatch):
    from management_portal.ai_provider_registry import AIProviderRegistry
    from management_portal import model_api_store
    registry=AIProviderRegistry()
    monkeypatch.setattr(registry,'get',lambda key:{'id':key})
    monkeypatch.setattr(model_api_store,'api_version',lambda key:7 if key=='kimi_primary' else None)
    assert registry.configuration_revision('kimi_primary')==7


def test_http_reports_missing_selected_api_as_503():
    from urllib.parse import urlsplit
    from management_portal.http_paper_translation import routes
    class Missing:
        def request(self,*args,**kwargs):
            raise TranslationConfigurationUnavailable('no registration')
    payload=dict(request('kimi'),operation_id='missing')
    definitions=routes(service=Missing(),get_auth=lambda req:'a',authenticate=lambda ctx:ctx,
        read_json=lambda req:payload,respond=lambda req,status,value:(status,value))
    definition=next(d for d in definitions if d.method=='POST' and d.path.endswith('/translation'))
    req=SimpleNamespace(headers={'Host':'fixture','Origin':'http://fixture','Content-Type':'application/json','Content-Length':'200','X-Reader-Principal':'a'})
    result=definition.handler(req,urlsplit('/paper/api/reader/translation'),'a')
    assert result[0]==503 and result[1]['error']['code']=='service_unavailable'
