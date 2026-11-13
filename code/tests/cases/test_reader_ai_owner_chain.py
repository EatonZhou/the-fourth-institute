"""Reader AI integration using existing disposable cross-owner fixture.

No production identities or model traffic. Each owner below is the real local
implementation. External sockets remain forbidden; subprocess access is restored
only to launch the browser, whose HTTP requests are bridged to disposable ASGI.
"""
import subprocess
_REAL_POPEN = subprocess.Popen
from tests.cases.test_cross_owner_storage_integration import owners, ACTOR, PROJECT


def bind_paper(owners, monkeypatch):
    from management_portal.workbench_projections.store import Store
    from management_portal.workbench_projections import service, native
    from management_portal.resources.providers import workbench
    from management_portal import society_service, resident_workspace_adapter as adapter
    monkeypatch.setenv('PORTAL_SOCIETY_SERVICE_MODE', 'local')
    monkeypatch.setenv('PORTAL_RESOURCE_SERVICE_PROCESS', '1')
    for install in (workbench.install_resolver, workbench.install_mutation_provider, workbench.install_action_provider):
        install(owners.resource)
    from management_portal.resource_index_registries import WorkbenchAdapter
    index_adapter = WorkbenchAdapter()
    owners.index.adapter_layer.adapters[index_adapter.name] = index_adapter
    owners.index_store.publish_snapshot(index_adapter.name, [], {})
    from tests.cases.permission_test_support import enable, Fact
    from management_portal.permission_system.facts import collect_portal_facts
    owners.permissions = enable(monkeypatch)
    owners.permissions.instance.sync(collect_portal_facts(owners.resource))
    owners.permissions.place(Fact('workbench', 'workbench_030303030177', {'lineage':owners.lineage_id}, creator=ACTOR))
    store = Store()
    projection = service.ProjectionService(store)
    monkeypatch.setattr(service, 'get_service', lambda: projection)
    monkeypatch.setattr(native, 'get_service', lambda: projection)
    monkeypatch.setattr(service, 'get_resource_service', lambda: owners.resource)
    app_id = 'workbench_030303030177'
    app = store.create(app_id, {'title':'Isolated Paper', 'builtin':'paper', 'project_ids':[PROJECT],
        'bindings': {'main': {'lineage_id':owners.lineage_id, 'residents':{'coordinator':owners.resident_id}}}}, ACTOR)
    store.publish(app_id, ACTOR, app['revision'])
    store.activate_builtin(app_id, ACTOR, previous_app_id='', name='paper')
    return native


def test_reader_discussion_real_binding(owners, monkeypatch):
    from management_portal import resident_workspace_adapter as adapter
    native = bind_paper(owners, monkeypatch)
    scope=adapter._workspace_identity(ACTOR, 'paper', 'paper-reader:paper_0123456789ab:00000000-0000-4000-8000-000000000001')
    assert native.lookup('paper', actor=ACTOR, resident_scope=scope['resident_scope']) == {}
    result=native.ensure('paper', actor=ACTOR, **scope, title='Reader isolated discussion', backend='codex', create_role=True)
    print('REAL_READER_BINDING', result)
    assert result['lineage_id'] == owners.lineage_id
    assert result['resident_id'] != owners.resident_id
    assert native.lookup('paper', actor=ACTOR, resident_scope=scope['resident_scope']) == result


def test_reader_legacy_prepare_http_scope_isolation(owners, monkeypatch, tmp_path):
    import json, subprocess, os, tempfile
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    monkeypatch.setenv('TMPDIR', str(tmp_path))
    from pathlib import Path
    from starlette.applications import Starlette
    from starlette.routing import Route
    from starlette.responses import HTMLResponse
    from starlette.testclient import TestClient
    from management_portal import asgi, web_accounts, http_auth, paper_store, paper_resources, web_resources, resident_workspace_adapter
    monkeypatch.setattr(resident_workspace_adapter, 'PORTAL_ROOT', tmp_path)
    from management_portal.resources.providers import paper as paper_provider
    from management_portal.paper_reader_service import production_service
    from management_portal.resources.directory_registrations import register_owned
    from tests.cases.test_portal_security_repair_wave1 import _configure_portal_session
    from playwright.sync_api import sync_playwright
    bind_paper(owners, monkeypatch)
    from tests.cases.reader_ai_execution_support import install_execution_support
    support=install_execution_support(owners,monkeypatch,tmp_path,response_text='Synthetic Reader answer.')
    # Same real Resource owner; do not bypass Reader authorization.
    paper_provider.install_resolver(owners.resource)
    monkeypatch.setattr(paper_resources, 'get_resource_service', lambda: owners.resource)
    monkeypatch.setattr(web_resources, 'get_resource_service', lambda: owners.resource)
    bundle = tmp_path/'paper'; bundle.mkdir()
    paper_id='paper_0123456789ab'
    repo=Path(__file__).resolve().parents[2]
    (bundle/'paper.pdf').write_bytes((repo/'tests/cases/fixtures/paper-synthetic-means.pdf').read_bytes())
    (bundle/'manifest.json').write_text(json.dumps({'paper_id':paper_id,'title':'Isolated single paper','files':{'pdf':'paper.pdf'}}))
    register_owned('paper',paper_id,[bundle])
    store=paper_store.PaperStore(tmp_path)
    monkeypatch.setattr(paper_store,'PAPER_STORE',store)
    monkeypatch.setattr(paper_resources,'PAPER_STORE',store)
    from management_portal.paper_resource_index import PaperManifestAdapter
    paper_index = PaperManifestAdapter()
    owners.index.adapter_layer.adapters[paper_index.name] = paper_index
    record = paper_index._record_for_manifest(bundle/'manifest.json')
    owners.index_store.publish_snapshot('paper', [record], {})
    from management_portal.permission_system.facts import collect_portal_facts
    owners.permissions.instance.sync(collect_portal_facts(owners.resource))
    _configure_portal_session(tmp_path, monkeypatch, owners.access)
    token=web_accounts._issue_session_token(ACTOR)
    document=production_service().open(ACTOR,paper_id)['document']
    document={k:document[k] for k in ('paper_id','attachment_id','attachment_version')}
    # Reader now borrows the persistent Paper Chat (covered in research_joint).
    # Historical single-paper discussions retain their owner/authorization API.
    app=Starlette(routes=[Route('/{path:path}',asgi.portal_http,methods=['GET','POST'])])
    from management_portal.workbench_projections.store import Store
    def roles(): return Store().get('workbench_030303030177',ACTOR)['bindings']['main']['residents']
    request={'surface':'paper','workspace_key':'paper-reader:'+paper_id+':00000000-0000-4000-8000-000000000001',
        'message':'Inspect selected text','context':{'expected_principal':ACTOR,'document':document,'selection':'Synthetic selection'}}
    headers={'Cookie':http_auth._SESSION_COOKIE+'='+token,'Origin':'http://localhost'}
    with TestClient(app,base_url='http://localhost') as http:
        readonly=http.post('/chat-workspace/api/prepare',json={**request,'read_only':True},headers=headers)
        assert readonly.status_code==200, readonly.text
        assert readonly.json()['resident_id']==''
        assert len(roles())==1
        first=http.post('/chat-workspace/api/prepare',json=request,headers=headers)
        assert first.status_code==200,first.text
        resident=first.json()['resident_id']
        assert resident and first.json()['lineage_id']==owners.lineage_id
        assert len(roles())==2
        followup=http.post('/chat-workspace/api/prepare',json=request,headers=headers)
        assert followup.status_code==200 and followup.json()['resident_id']==resident
        assert len(roles())==2
        independent_request={**request,'workspace_key':request['workspace_key'][:-1]+'2'}
        independent=http.post('/chat-workspace/api/prepare',json=independent_request,headers=headers)
        assert independent.status_code==200,independent.text
        assert independent.json()['resident_id']!=resident and len(roles())==3
        stale=http.post('/chat-workspace/api/prepare',json={**request,'context':{**request['context'],'expected_principal':'someone-else'}},headers=headers)
        assert stale.status_code==403,stale.text
        mismatch=http.post('/chat-workspace/api/prepare',json={**request,'workspace_key':request['workspace_key'].replace(paper_id,'paper_222222222222')},headers=headers)
        assert mismatch.status_code==400,mismatch.text
        before=len(roles())
        owners.permissions.deny(ACTOR,paper_id)
        denied=http.post('/chat-workspace/api/prepare',json={**request,'read_only':True},headers=headers)
        assert denied.status_code==400 and denied.json()['code']=='operation.invalid',denied.text
        assert len(roles())==before
        anonymous=http.post('/chat-workspace/api/prepare',json=request,headers={'Origin':'http://localhost'})
        assert anonymous.status_code==401,anonymous.text
        assert not support.transport.turns, 'preparing a legacy binding dispatches no model work'
