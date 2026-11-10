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


def test_reader_real_panel_prepare_and_send(owners, monkeypatch, tmp_path):
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
    async def root(request):
        return HTMLResponse('<link rel="stylesheet" href="/static/paper.css"><div id="chat"></div><script type="module">import {openReader} from "/static/paper_fullscreen_reader.js";window.signals=[];window.openDocument=()=>window.readerHost=openReader({paper:'+json.dumps({'id':paper_id,'title':'Isolated single paper','pdf_url':'/unused.pdf'})+',principal:'+json.dumps(ACTOR)+',isCurrent:()=>PortalIdentity.username()==='+json.dumps(ACTOR)+',onReady:s=>signals.push(s.paperId)});window.readerModuleReady=true;</script>')
    app=Starlette(routes=[Route('/',root),Route('/api/instructions/execute',asgi.instruction_execute,methods=['POST']),Route('/{path:path}',asgi.portal_http,methods=['GET','POST'])])
    # Browser transport is forwarded verbatim to the real ASGI endpoints in this
    # process. No prepare/instruction response is intercepted or synthesized.
    monkeypatch.setattr(subprocess,'Popen',_REAL_POPEN)
    with TestClient(app,base_url='http://localhost') as http, sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True, executable_path=str(sorted((Path.home()/'.cache/ms-playwright').glob('chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell'))[-1]))
        try:
            context=browser.new_context()
            context.add_cookies([{'name':http_auth._SESSION_COOKIE,'value':token,'url':'http://localhost'}])
            page=context.new_page();trace=[];prepares=[]
            def forward(route):
                request=route.request
                assert request.url.startswith('http://localhost/')
                response=http.request(request.method,request.url,headers=request.headers,content=request.post_data_buffer)
                trace.append((request.method,request.url,response.status_code,response.text[:300] if 'json' in response.headers.get('content-type','') else f'{len(response.content)} bytes'))
                if request.url.endswith('/chat-workspace/api/prepare'):
                    prepares.append({'request':request.post_data_json,'status':response.status_code,'value':response.json()})
                route.fulfill(status=response.status_code,headers=dict(response.headers),body=response.content)
            page.route('**/*',forward)
            page.goto('http://localhost/')
            page.evaluate('''actor=>{window.PortalIdentity={username:()=>actor};window.PortalEvents={isConnected:()=>false,connect(){}};window.PortalI18n={message:s=>s,t:s=>s,requestLanguage:()=> 'zh-CN'};}''',ACTOR)
            repo=Path(__file__).resolve().parents[2]
            # Reuse the existing ordered production Chat package dependency lists.
            script_list=subprocess.check_output(['node','-e',"const a=require('./tests/cases/javascript/chat_workspace_modules.cjs');const b=require('./tests/cases/javascript/portal_workbench_scripts.cjs');console.log(JSON.stringify([...b.portalWorkbenchScripts(),...a.chatWorkspaceScripts()]));"],cwd=repo,text=True)
            names=['portal_state.js','portal_page_state.js','portal_text.js','portal_ui_primitives.js','portal_ui.js','portal_overlay.js','portal_resource_chips.js','portal_image_editor.js','portal_editor_open_service.js','portal_command_registry.js','portal_workbench_context.js','portal_api.js','portal_instruction_client.js','portal_resident_service.js','resident_protocol.js','resident_projection.js','portal_resident_runtime_controller.js',*json.loads(script_list)]
            from management_portal.resources.portal import portal_route_script
            page.add_script_tag(content=portal_route_script().decode())
            for name in dict.fromkeys(names):page.add_script_tag(content=(repo/'management_portal/static'/name).read_text())
            page.wait_for_function('window.readerModuleReady')
            page.evaluate('openDocument()')
            page.wait_for_function('signals.length===1')
            outer=page.frames[1]
            inner=outer.frame_locator('iframe').first
            inner.locator('.textLayer span').first.dblclick()
            outer.locator('.portalAISelection').click()
            page.get_by_role('complementary',name='论文 AI 侧栏').wait_for()
            print('REAL_HTTP_READY', json.dumps(trace[-5:],ensure_ascii=False))
            from management_portal.workbench_projections.store import Store
            assert len(Store().get('workbench_030303030177',ACTOR)['bindings']['main']['residents'])==1
            assert not any('DeliverMessage' in t[3] for t in trace)
            assert prepares[-1]['request']['read_only'] is True and prepares[-1]['value']['resident_id']==''
            page.locator('.paperAIChat [role="textbox"]').fill('Explain synthetic selection')
            with page.expect_response(lambda r: r.url.endswith('/api/instructions/execute') and r.request.post_data_json.get('instruction')=='DeliverMessage') as delivery:
                page.locator('.paperAIChat [role="textbox"]').press('Control+Enter')
            accepted=delivery.value.json()
            assert accepted['status']=='committed', accepted
            page.wait_for_function("document.querySelector('.paperAIChat').textContent.includes('Explain synthetic selection')")
            print('REAL_HTTP_SEND', json.dumps(trace[-10:],ensure_ascii=False))
            assert len([p for p in prepares if not p['request']['read_only']])==1
            first=prepares[-1]
            resident=first['value']['resident_id']
            completed=support.run_until_idle(resident,timeout=15)
            assert completed['input_state']['state']=='completed'
            print('REAL_EXECUTION_FIRST',completed['execution_id'])
            page.evaluate('id=>PortalResidentProtocol.loadSnapshot(id,{force:true})',resident)
            page.get_by_text('确认保存回答为便签',exact=True).click()
            page.get_by_label('选择 AI 回答').select_option(index=1)
            assert page.get_by_label('便签预览').input_value()=='Synthetic Reader answer.'
            assert production_service().read(ACTOR,document)['annotations']==[]
            page.get_by_role('button',name='确认保存便签',exact=True).click()
            page.get_by_text('便签已保存',exact=True).wait_for()
            saved=production_service().read(ACTOR,document)['annotations']
            assert len(saved)==1 and saved[0]['comment']=='Synthetic Reader answer.'
            print('REAL_NOTE_CONFIRMED',saved[0]['id'])
            assert resident and first['value']['lineage_id']==owners.lineage_id
            from management_portal.workbench_projections.store import Store
            def roles():return Store().get('workbench_030303030177',ACTOR)['bindings']['main']['residents']
            assert len(roles())==2  # coordinator plus exactly one discussion
            # Followup keeps the existing discussion, never calls ensure again.
            with page.expect_response(lambda r: r.url.endswith('/api/instructions/execute') and r.request.post_data_json.get('instruction')=='DeliverMessage') as followup:
                page.locator('.paperAIChat [role="textbox"]').fill('Follow up in this discussion only')
                page.locator('.paperAIChat [role="textbox"]').press('Control+Enter')
            assert followup.value.json()['status']=='committed'
            followed=support.run_until_idle(resident,timeout=15)
            assert followed['input_state']['state']=='completed'
            assert followup.value.request.post_data_json['arguments']['resident_id']==resident
            assert len(roles())==2 and len(prepares)==2
            # A new discussion is readonly until first send; then the official
            # owner assigns a different Resident in the same main Lineage.
            page.get_by_role('button',name='新 AI 讨论',exact=True).click()
            page.wait_for_function("document.querySelector('.paperAIChat [role=textbox]')!==null")
            page.locator('.paperAIChat [role="textbox"]').fill('Independent discussion')
            with page.expect_response(lambda r: r.url.endswith('/api/instructions/execute') and r.request.post_data_json.get('instruction')=='DeliverMessage') as independent:
                page.locator('.paperAIChat [role="textbox"]').press('Control+Enter')
            assert independent.value.json()['status']=='committed'
            independent_id=independent.value.request.post_data_json['arguments']['resident_id']
            independent_done=support.run_until_idle(independent_id,timeout=15)
            assert independent_done['input_state']['state']=='completed'
            assert independent.value.request.post_data_json['arguments']['resident_id']!=resident
            assert len(roles())==3
            assert prepares[-1]['request']['workspace_key']!=first['request']['workspace_key']
            # The server, not a browser actor field, owns authentication.
            other='storage-outsider';other_token=web_accounts._issue_session_token(other)
            headers={'Cookie':http_auth._SESSION_COOKIE+'='+other_token,'Origin':'http://localhost'}
            stale=http.post('/chat-workspace/api/prepare',json=first['request'],headers=headers)
            assert stale.status_code==403, stale.text
            other_request={**first['request'],'context':{**first['request']['context'],'expected_principal':other}}
            other_result=http.post('/chat-workspace/api/prepare',json=other_request,headers=headers)
            assert other_result.status_code==200,other_result.text
            assert other_result.json()['resident_id'] not in (resident,independent.value.request.post_data_json['arguments']['resident_id'])
            # A second real registered synthetic paper has its own immutable
            # descriptor and discussion. Mismatched key/document is rejected.
            second=tmp_path/'second';second.mkdir()
            second_id='paper_abcdef012345'
            (second/'paper.pdf').write_bytes((bundle/'paper.pdf').read_bytes())
            (second/'manifest.json').write_text(json.dumps({'paper_id':second_id,'title':'Second isolated paper','files':{'pdf':'paper.pdf'}}))
            register_owned('paper',second_id,[second])
            second_record=paper_index._record_for_manifest(second/'manifest.json')
            owners.index_store.publish_snapshot('paper',[record,second_record],{})
            owners.permissions.instance.sync(collect_portal_facts(owners.resource))
            second_doc=production_service().open(ACTOR,second_id)['document']
            second_doc={k:second_doc[k] for k in document}
            second_request={**first['request'],'context':{**first['request']['context'],'document':second_doc,'selection':'Second paper selected sentence'}}
            actor_headers={'Cookie':http_auth._SESSION_COOKIE+'='+token,'Origin':'http://localhost'}
            mismatch=http.post('/chat-workspace/api/prepare',json=second_request,headers=actor_headers)
            assert mismatch.status_code==400,mismatch.text
            second_request['workspace_key']=second_request['workspace_key'].replace(paper_id,second_id)
            second_result=http.post('/chat-workspace/api/prepare',json=second_request,headers=actor_headers)
            assert second_result.status_code==200,second_result.text
            assert second_result.json()['resident_id']!=resident
            assert len(roles())==5
            context_text=second_result.json()['initial_context']['context_text']
            assert second_id in context_text and 'Isolated single paper' not in context_text
            assert len(support.transport.turns)==3
            turns=support.transport.turns
            assert turns[0]['thread_id']==turns[1]['thread_id']
            assert turns[2]['thread_id']!=turns[0]['thread_id']
            assert 'Explain synthetic selection' in turns[0]['prompt'] and first['request']['context']['selection'] in turns[0]['prompt']
            assert 'Follow up in this discussion only' in turns[1]['prompt']
            assert 'Independent discussion' in turns[2]['prompt'] and 'Follow up in this discussion only' not in turns[2]['prompt']
            role_count=len(roles())
            owners.permissions.deny(ACTOR,paper_id)
            denied=http.post('/chat-workspace/api/prepare',json={**first['request'],'read_only':True},headers=actor_headers)
            # IndexStore deliberately hides unreadable identities as KeyError;
            # the existing prepare HTTP contract maps that to operation.invalid.
            assert denied.status_code==400 and denied.json()['code']=='operation.invalid',denied.text
            assert len(roles())==role_count
            anonymous=http.post('/chat-workspace/api/prepare',json=first['request'],headers={'Origin':'http://localhost'})
            assert anonymous.status_code==401,anonymous.text
            print('REAL_MODEL_BOUNDARY',{'model_transport':'synthetic Codex client only','turns':len(turns),'executions':[o.get('snapshot',{}).get('request',{}).get('execution_id') for o in support.outcomes],'permission_denied':denied.status_code,'anonymous':anonymous.status_code})
            print('READER_AI_FORMAL_EVIDENCE',json.dumps({'browser':'chromium','first_resident':resident,'first_execution':completed['execution_id'],'first_input':completed['input_state'].get('message_id'),'followup_execution':followed['execution_id'],'independent_resident':independent_id,'independent_execution':independent_done['execution_id'],'same_thread_followup':turns[0]['thread_id']==turns[1]['thread_id'],'fresh_thread_independent':turns[0]['thread_id']!=turns[2]['thread_id'],'turn_count':len(turns),'explicit_note_id':saved[0]['id'],'saved_comment':saved[0]['comment'],'other_actor_resident':other_result.json()['resident_id'],'second_paper_resident':second_result.json()['resident_id'],'mismatch_status':mismatch.status_code,'stale_principal_status':stale.status_code,'permission_denied_status':denied.status_code,'anonymous_status':anonymous.status_code,'permission_engine':'PAR default copy on disposable PG','external_model':'Codex app-server client test double only'},ensure_ascii=False))
            print('REAL_ISOLATION', {'first':resident,'independent':independent.value.request.post_data_json['arguments']['resident_id'],'other_actor':other_result.json()['resident_id'],'second_paper':second_result.json()['resident_id'],'stale_principal':stale.status_code,'mismatched_paper':mismatch.status_code})
        finally:
            print('REAL_HTTP_FINAL', json.dumps(trace[-14:],ensure_ascii=False))
            browser.close()
