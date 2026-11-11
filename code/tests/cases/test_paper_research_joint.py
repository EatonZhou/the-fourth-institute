import pytest
import subprocess
_REAL_POPEN = subprocess.Popen
from tests.cases.test_cross_owner_storage_integration import owners, ACTOR, PROJECT
from tests.cases.test_reader_ai_owner_chain import bind_paper

@pytest.mark.parametrize("browser_name", ["chromium", "firefox"])
def test_persistent_research_http_reader_and_execution(owners, monkeypatch, tmp_path, browser_name):
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
    from management_portal.portal_ui_state import get_portal_ui_state_store
    monkeypatch.setattr(get_portal_ui_state_store(), 'key_lock_root', tmp_path/'ui-locks')
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
    second=tmp_path/'paper-two';second.mkdir()
    (second/'paper.pdf').write_bytes((bundle/'paper.pdf').read_bytes())
    (second/'manifest.json').write_text(json.dumps({'paper_id':'paper_222222222222','title':'Second synthetic paper','files':{'pdf':'paper.pdf'}}))
    register_owned('paper','paper_222222222222',[second])
    record_two=paper_index._record_for_manifest(second/'manifest.json')
    owners.index_store.publish_snapshot('paper', [record,record_two], {})
    from management_portal.permission_system.facts import collect_portal_facts
    owners.permissions.instance.sync(collect_portal_facts(owners.resource))
    _configure_portal_session(tmp_path, monkeypatch, owners.access)
    token=web_accounts._issue_session_token(ACTOR)
    document=production_service().open(ACTOR,paper_id)['document']
    document={k:document[k] for k in ('paper_id','attachment_id','attachment_version')}
    async def root(request):
        return HTMLResponse('<div id=research></div><link rel="stylesheet" href="/static/paper.css"><div id="chat"></div><script type="module">import {openReader} from "/static/paper_fullscreen_reader.js";window.signals=[];window.openDocument=()=>window.readerHost=openReader({paper:'+json.dumps({'id':paper_id,'title':'Isolated single paper','pdf_url':'/unused.pdf'})+',principal:'+json.dumps(ACTOR)+',isCurrent:()=>PortalIdentity.username()==='+json.dumps(ACTOR)+',onReady:s=>signals.push(s.paperId)});window.readerModuleReady=true;</script>')
    app=Starlette(routes=[Route('/',root),Route('/api/instructions/execute',asgi.instruction_execute,methods=['POST']),Route('/{path:path}',asgi.portal_http,methods=['GET','POST'])])
    # Browser transport is forwarded verbatim to the real ASGI endpoints in this
    # process. No prepare/instruction response is intercepted or synthesized.
    monkeypatch.setattr(subprocess,'Popen',_REAL_POPEN)
    with TestClient(app,base_url='http://localhost') as http, sync_playwright() as pw:
        browser=getattr(pw,browser_name).launch(headless=True, **({'executable_path':str(sorted((Path.home()/'.cache/ms-playwright').glob('chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell'))[-1])} if browser_name=='chromium' else {'executable_path':str(sorted((Path.home()/'.cache/ms-playwright').glob('firefox-*/firefox/firefox'))[-1])}))
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
            if os.environ.get('PAPER_RESEARCH_VISUAL_ONLY'):
                import re
                for css in re.findall(r'href="(/static/[^"?]+\.css)"', (repo/'management_portal/paper.html').read_text()):
                    page.add_style_tag(content=(repo/'management_portal'/css.removeprefix('/')).read_text())
                page.evaluate('window.__PORTAL_LANGUAGE__="zh-CN"')
                for name in ['portal_translations.js','portal_i18n.js','portal_button_tooltips.js']:
                    page.add_script_tag(content=(repo/'management_portal/static'/name).read_text())
            page.add_script_tag(content=(repo/'management_portal/static/paper_chat.js').read_text())
            page.evaluate("""actor=>{
              window.researchState={username:actor,principalId:actor,selectedId:'paper_0123456789ab'};
              const deps=Object.fromEntries(PortalPaperChat.dependencies.map(k=>[k,()=>{}]));
              Object.assign(deps,{$:id=>document.getElementById(id),state:researchState,
                esc:s=>s,md:s=>s,isCollectionMode:()=>false,
                paperApi:{request:async(url,options={})=>{const r=await fetch(url,{credentials:'same-origin',...options,headers:{'Content-Type':'application/json'}});const v=await r.json();if(!r.ok)throw Error(JSON.stringify(v));return v;}},
                alertError:e=>{window.researchError=String(e)},currentPaperWorkbench:()=>null});
              window.research=PortalPaperChat.createChat(deps);
              const descriptor=research.paperChatDescriptor();
              window.researchChat=PortalChatWorkspace.panelPackage.create({...descriptor.options,root:document.getElementById('research')});
              descriptor.onShow({panel:{runtime:researchChat}});
            }""",ACTOR)
            # Explicit nonfullscreen creation through the real production route.
            page.once('dialog',lambda dialog:dialog.accept('Cross-paper research'))
            page.get_by_role('button',name='新建研究助手',exact=True).click()
            page.wait_for_function('researchState.researchSlot?.state==="bound" || window.researchError')
            assert page.evaluate('window.researchError||null') is None
            selected=page.evaluate('researchState.researchSlot')
            resident=selected['resident_id']
            assert selected['lineage_id']!=owners.lineage_id
            if os.environ.get('PAPER_RESEARCH_IDENTITY_ONLY'):
                from tests.cases.paper_research_ui_support import verify_identity_and_late_rename
                verify_identity_and_late_rename(page,http,selected,ACTOR,tmp_path,support)
                return
            if os.environ.get('PAPER_RESEARCH_INTERACTIONS_ONLY'):
                from tests.cases.paper_research_ui_support import verify_interactions
                verify_interactions(page,http,selected,ACTOR,tmp_path,support)
                assert len(support.transport.turns)==1
                return
            if os.environ.get('PAPER_RESEARCH_VISUAL_ONLY'):
                from pathlib import Path
                evidence=tmp_path/'research-chat-visual';evidence.mkdir(parents=True,exist_ok=True)
                page.wait_for_function('researchChat.target()?.resident_id')
                before=page.evaluate('researchChat.target().resident_id')
                page.evaluate("researchChat.workspace.setDraftText('Visual inspection draft')")
                for language in ['zh-CN','en']:
                    page.evaluate('language=>PortalI18n.apply(language)',language)
                    for width in [320,640]:
                        page.set_viewport_size({'width':width,'height':850})
                        page.evaluate("document.documentElement.setAttribute('data-portal-theme','light')")
                        select=page.locator('.paperResearchSelector select')
                        assert select.input_value()==selected['slot_id']
                        assert select.locator('option:checked').inner_text()=='Cross-paper research'
                        assert page.locator('.paperResearchSelector button').first.inner_text()==('新建研究助手' if language=='zh-CN' else 'New assistant')
                        assert page.locator('.paperResearchSelector').evaluate('(e)=>e.scrollWidth<=e.clientWidth')
                        select.focus()
                        page.screenshot(path=str(evidence/f'{browser_name}-{language}-{width}-focus.png'))
                        button=page.locator('#research [data-workspace-history-search]')
                        button.hover()
                        expected=page.evaluate("PortalI18n.t('浏览当前对话片段，输入关键词查找并跳转')")
                        page.wait_for_function('expected=>Array.from(document.querySelectorAll("[role=tooltip]")).some(e=>!e.hidden && e.textContent===expected && Number(getComputedStyle(e).opacity)===1)',arg=expected,timeout=3000)
                        page.screenshot(path=str(evidence/f'{browser_name}-{language}-{width}-hover.png'))
                        page.keyboard.press('Tab');button.focus()
                        page.wait_for_function('expected=>Array.from(document.querySelectorAll("[role=tooltip]")).some(e=>!e.hidden && e.textContent===expected && Number(getComputedStyle(e).opacity)===1)',arg=expected,timeout=3000)
                        assert page.evaluate('researchChat.target().resident_id')==before
                        assert page.locator('#research [role=textbox]').inner_text()=='Visual inspection draft'
                assert not support.transport.turns
                return
            # The independent Reader obtains this same selected slot, no creation.
            page.wait_for_function('window.readerModuleReady')
            page.evaluate('openDocument()')
            page.wait_for_function('signals.length===1')
            outer=page.frames[1]
            outer.frame_locator('iframe').first.locator('.textLayer span').first.dblclick()
            outer.locator('.portalAISelection').click()
            page.get_by_role('complementary',name='论文 AI 侧栏').wait_for()
            page.locator('.paperAIChat [role="textbox"]').fill('Compare this synthetic paper')
            try:
                with page.expect_response(lambda r:r.url.endswith('/api/instructions/execute') and r.request.post_data_json.get('instruction')=='DeliverMessage', timeout=10000) as delivered:
                    page.locator('.paperAIChat [role="textbox"]').press('Control+Enter')
            except Exception:
                print('JOINT_TRACE', json.dumps(trace[-12:], ensure_ascii=False))
                print('JOINT_PANEL',page.locator('.paperAIPanel').inner_text())
                raise
            result=delivered.value.json()
            assert result['status']=='committed',result
            assert delivered.value.request.post_data_json['arguments']['resident_id']==resident
            assert prepares[-1]['request']['workspace_key']=='research:'+selected['slot_id']
            completed=support.run_until_idle(resident,timeout=15)
            assert completed['input_state']['state']=='completed'
            actual=owners.data.execution(completed['execution_id'])
            # Use the canonical Execution owner result, not a browser fake agent.
            assert len(support.transport.turns)==1
            assert 'Compare this synthetic paper' in str(support.transport.turns)
            page.evaluate('id=>PortalResidentProtocol.loadSnapshot(id,{force:true})',resident)
            page.wait_for_function("document.querySelector('.paperAIChat').textContent.includes('Synthetic Reader answer.')")
            page.screenshot(path=str(tmp_path/'persistent-research.png'))
            print('RESEARCH_SCREENSHOT',str(tmp_path/'persistent-research.png'))
            page.evaluate('readerHost.close()')
            page.evaluate("""async()=>{
              window.chatRootBefore=researchChat.workspace.root;
              researchChat.workspace.setDraftText('Keep this research draft');
              researchState.selectedId='paper_222222222222';
              await research.loadChatHistory();
            }""")
            assert page.evaluate('researchChat.workspace.root===chatRootBefore')
            assert page.locator('#research [role=textbox]').inner_text()=='Keep this research draft'
            from management_portal import paper_chat_research
            assert len(paper_chat_research.material_index(actor=ACTOR,slot_id=selected['slot_id'])['documents'])==2
            assert page.evaluate('researchChat.target().resident_id')==resident
            assert len(support.transport.turns)==1
            page.once('dialog',lambda dialog:dialog.accept('Second persistent assistant'))
            page.get_by_role('button',name='新建研究助手',exact=True).click()
            page.wait_for_function('old=>researchState.researchSlot?.slot_id!==old',arg=selected['slot_id'])
            second_slot=page.evaluate('researchState.researchSlot')
            assert second_slot['resident_id']!=resident
            assert second_slot['materials_workspace_id']==selected['materials_workspace_id']
            assert second_slot['workspace_id']!=selected['workspace_id']
            page.get_by_label('选择研究助手',exact=True).select_option(selected['slot_id'])
            page.wait_for_function('id=>researchState.researchSlot?.slot_id===id',arg=selected['slot_id'])
            assert len(support.transport.turns)==1
            # Final admission rejects forged target/expired versions/revocation.
            from copy import deepcopy
            from management_portal.society_service import resident_conversation_stream
            original=delivered.value.request.post_data_json
            def rejected(change, number, token_override=token):
                request=deepcopy(original)
                request['arguments']['message']['message_id']='msg_'+format(number,'032x')
                reference=next(p for p in request['arguments']['message']['content_parts'] if p.get('type')=='context_reference')['context']['structured_reference']
                change(request,reference)
                response=http.post('/api/instructions/execute',json=request,headers={
                    'Cookie':http_auth._SESSION_COOKIE+'='+token_override,'Origin':'http://localhost'})
                assert response.json()['status']=='failed',response.text
                assert resident_conversation_stream.get_repository().message(request['arguments']['resident_id'],request['arguments']['message']['message_id']) is None
            rejected(lambda request,ref:ref['documents'][0].update(attachment_version='stale'),401)
            rejected(lambda request,ref:ref.update(slot_id='research_'+'0'*24),402)
            rejected(lambda request,ref:request['arguments'].update(resident_id=second_slot['resident_id']),405)
            other_token=web_accounts._issue_session_token('storage-outsider')
            rejected(lambda request,ref:ref.update(expected_principal='storage-outsider'),403,other_token)
            owners.permissions.deny(ACTOR,paper_id,'@viewer')
            rejected(lambda request,ref:None,404)
            assert len(support.transport.turns)==1

        finally:
            browser.close()
