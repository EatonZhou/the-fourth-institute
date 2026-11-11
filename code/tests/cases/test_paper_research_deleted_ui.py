"""Actual HTTP catalog + shared Chat deletion recovery; real disposable owners."""
import os
import json
import subprocess
import pytest
from pathlib import Path
_REAL_POPEN=subprocess.Popen
from tests.cases.test_paper_research_deleted import owners,ACTOR,prepare

# Candidate bytes are collected before the filesystem-isolation fixture starts.
_CANDIDATE=os.environ.get('PAPER_DELETE_CANDIDATE')
_READER_SOURCE=Path(_CANDIDATE).joinpath('paper_fullscreen_reader.js').read_bytes() if _CANDIDATE else None
_CHAT_SOURCE=Path(_CANDIDATE).joinpath('paper_chat.js').read_text() if _CANDIDATE else None


@pytest.mark.parametrize('late',['catalog','selection'])
def test_deleted_current_http_ui_and_late_catalog(owners,monkeypatch,tmp_path,late):
    from starlette.applications import Starlette
    from starlette.routing import Route
    from starlette.responses import HTMLResponse
    from starlette.testclient import TestClient
    from playwright.sync_api import sync_playwright
    from management_portal import asgi,web_accounts,http_auth,paper_chat_research as research
    from tests.cases.test_portal_security_repair_wave1 import _configure_portal_session
    _,create=prepare(owners,monkeypatch)
    first=create('First research');second=create('Second research')
    research.select_slot(actor=ACTOR,slot_id=first['slot_id'])
    _configure_portal_session(tmp_path,monkeypatch,owners.access)
    token=web_accounts._issue_session_token(ACTOR)
    from management_portal import paper_store,paper_resources,web_resources,resident_workspace_adapter
    from management_portal.resources.providers import paper as paper_provider
    from management_portal.resources.directory_registrations import register_owned
    from management_portal.paper_resource_index import PaperManifestAdapter
    from management_portal.permission_system.facts import collect_portal_facts
    monkeypatch.setattr(resident_workspace_adapter,'PORTAL_ROOT',tmp_path)
    paper_provider.install_resolver(owners.resource)
    monkeypatch.setattr(paper_resources,'get_resource_service',lambda:owners.resource)
    monkeypatch.setattr(web_resources,'get_resource_service',lambda:owners.resource)
    repo=Path(__file__).resolve().parents[2]
    bundle=tmp_path/'synthetic-paper';bundle.mkdir();paper_id='paper_0123456789ab'
    (bundle/'paper.pdf').write_bytes((repo/'tests/cases/fixtures/paper-synthetic-means.pdf').read_bytes())
    (bundle/'manifest.json').write_text(json.dumps({'paper_id':paper_id,'title':'Disposable paper','files':{'pdf':'paper.pdf'}}))
    register_owned('paper',paper_id,[bundle]);store=paper_store.PaperStore(tmp_path)
    monkeypatch.setattr(paper_store,'PAPER_STORE',store);monkeypatch.setattr(paper_resources,'PAPER_STORE',store)
    adapter=PaperManifestAdapter();owners.index.adapter_layer.adapters[adapter.name]=adapter
    owners.index_store.publish_snapshot('paper',[adapter._record_for_manifest(bundle/'manifest.json')],{})
    owners.permissions.instance.sync(collect_portal_facts(owners.resource))
    async def root(request):return HTMLResponse('<div id="research"></div><script type="module">import {openReader} from "/static/paper_fullscreen_reader.js";window.openDocument=()=>window.readerHost=openReader({paper:{id:"paper_0123456789ab",title:"Disposable paper",pdf_url:"/unused.pdf"},principal:'+json.dumps(ACTOR)+',isCurrent:()=>true});window.readerModuleReady=true;</script>')
    app=Starlette(routes=[Route('/',root),Route('/api/instructions/execute',asgi.instruction_execute,methods=['POST']),Route('/{path:path}',asgi.portal_http,methods=['GET','POST'])])
    monkeypatch.setattr(subprocess,'Popen',_REAL_POPEN)
    repo=Path(__file__).resolve().parents[2]
    with TestClient(app,base_url='http://localhost') as http,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,executable_path=str(sorted((Path.home()/'.cache/ms-playwright').glob('chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell'))[-1]))
        try:
            context=browser.new_context()
            context.add_cookies([{'name':http_auth._SESSION_COOKIE,'value':token,'url':'http://localhost'}])
            page=context.new_page();page.set_default_timeout(10000);trace=[]
            def forward(route):
                req=route.request
                if _READER_SOURCE is not None and req.url.endswith('/static/paper_fullscreen_reader.js'):
                    route.fulfill(status=200,content_type='text/javascript',body=_READER_SOURCE);return
                response=http.request(req.method,req.url,headers=req.headers,content=req.post_data_buffer)
                trace.append((req.method,req.url,response.status_code))
                route.fulfill(status=response.status_code,headers=dict(response.headers),body=response.content)
            page.route('**/*',forward);page.goto('http://localhost/')
            def mount():
                page.evaluate('actor=>{window.PortalIdentity={username:()=>actor};window.PortalEvents={isConnected:()=>false,connect(){}};window.PortalI18n={message:s=>s,t:s=>s,requestLanguage:()=>"zh-CN"};}',ACTOR)
                script_list=subprocess.check_output(['node','-e',"const a=require('./tests/cases/javascript/chat_workspace_modules.cjs');const b=require('./tests/cases/javascript/portal_workbench_scripts.cjs');console.log(JSON.stringify([...b.portalWorkbenchScripts(),...a.chatWorkspaceScripts()]));"],cwd=repo,text=True)
                names=['portal_state.js','portal_page_state.js','portal_text.js','portal_ui_primitives.js','portal_ui.js','portal_overlay.js','portal_resource_chips.js','portal_image_editor.js','portal_editor_open_service.js','portal_command_registry.js','portal_workbench_context.js','portal_api.js','portal_instruction_client.js','portal_resident_service.js','resident_protocol.js','resident_projection.js','portal_resident_runtime_controller.js',*json.loads(script_list)]
                from management_portal.resources.portal import portal_route_script
                page.add_script_tag(content=portal_route_script().decode())
                for name in dict.fromkeys(names):page.add_script_tag(content=(repo/'management_portal/static'/name).read_text())
                page.add_script_tag(content=_CHAT_SOURCE or (repo/'management_portal/static/paper_chat.js').read_text())
                page.evaluate('''actor=>{
                  window.researchState={username:actor,principalId:actor,selectedId:''};
                  window.PortalUI.loadCurrentUser=async()=>({ok:true,data:{username:actor,principal:{id:actor}}});
                  const deps=Object.fromEntries(PortalPaperChat.dependencies.map(k=>[k,()=>{}]));
                  Object.assign(deps,{$:id=>document.getElementById(id),state:researchState,esc:s=>s,md:s=>s,isCollectionMode:()=>false,
                    paperApi:{request:async(url,options={})=>{const r=await fetch(url,{credentials:'same-origin',...options,headers:{'Content-Type':'application/json'}});const v=await r.json();if(!r.ok)throw Error(JSON.stringify(v));return v;}},alertError:e=>window.researchError=String(e),currentPaperWorkbench:()=>null});
                  window.research=PortalPaperChat.createChat(deps);const descriptor=research.paperChatDescriptor();
                  window.chat=PortalChatWorkspace.panelPackage.create({...descriptor.options,root:document.getElementById('research')});
                  descriptor.onShow({panel:{runtime:chat}});window.refresh=()=>research.loadMe();
                  window.selectionSignals=[];window.addEventListener('portal:paper-research-selected',e=>selectionSignals.push(e.detail));
                  refresh();
                }''',ACTOR)
            mount()
            page.wait_for_function('id=>chat.target()?.resident_id===id',arg=first['resident_id'])
            page.evaluate("chat.workspace.setDraftText('Keep this draft after deletion');window.oldCatalog=null;")
            page.wait_for_function('window.readerModuleReady');page.evaluate('openDocument()')
            page.locator('iframe').wait_for();page.wait_for_function('document.querySelector(".paperReadingStatus")?.textContent?.includes("完成") || document.querySelector("iframe")')
            page.locator('.paperReadingToolbar button').filter(has_text='打开 AI').click()
            page.locator('.paperAIPanel .paperAIChat [role="textbox"]').wait_for()
            iframe=page.locator('iframe').element_handle()
            if late=='catalog':
                # Capture a real old HTTP result, then delay its browser delivery.
                page.evaluate('''async()=>{
                  const actual=window.fetch;window.oldCatalog=await (await actual('/paper/api/research/chats')).json();
                  window.holdNext=true;window.fetch=(url,...rest)=>String(url)==='/paper/api/research/chats'&&holdNext
                    ?(holdNext=false,new Promise(resolve=>window.releaseOld=()=>resolve(new Response(JSON.stringify(oldCatalog),{status:200,headers:{'Content-Type':'application/json'}}))))
                    :actual(url,...rest);
                  refresh();
                }''')
                dead,surviving=first,second
            else:
                # The actual select owner commits; only the response is delayed.
                page.evaluate('''()=>{
                  const actual=window.fetch;window.holdNext=true;
                  window.fetch=(url,...rest)=>String(url)==='/paper/api/research/chats/select'&&holdNext
                    ?(holdNext=false,actual(url,...rest).then(response=>new Promise(resolve=>window.releaseOld=()=>resolve(response))))
                    :actual(url,...rest);
                }''')
                page.locator('.paperResearchSelector select').select_option(second['slot_id'],force=True)
                dead,surviving=second,first
            page.wait_for_function('!!window.releaseOld')
            research.isa_client.call(ACTOR,'DeleteResident',{'lineage_id':dead['lineage_id'],'resident_id':dead['resident_id']},key='delete-ui-current')
            page.evaluate('refresh()')
            page.wait_for_function('!researchState.researchSlot && !chat.target()?.resident_id')
            assert page.locator('.paperResearchSelector select').input_value()==''
            assert page.locator('.paperResearchSelector select option').count()==2
            assert page.evaluate('chat.workspace.draftText()')=='Keep this draft after deletion'
            page.evaluate('releaseOld()');page.wait_for_timeout(50)
            assert page.evaluate('researchState.researchSlot') is None
            assert not page.evaluate('chat.target()?.resident_id')
            assert page.evaluate('selectionSignals.at(-1).slot') is None
            assert page.locator('.paperAIPanel').count()==0
            assert iframe.evaluate('(node)=>node===document.querySelector("iframe") && node.isConnected')
            page.locator('.paperReadingToolbar button').filter(has_text='退出全屏').click()
            page.locator('.paperResearchSelector select').select_option(surviving['slot_id'],force=True)
            page.wait_for_function('id=>chat.target()?.resident_id===id',arg=surviving['resident_id'])
            assert page.evaluate('researchState.researchSlot.slot_id')==surviving['slot_id']
            page.evaluate('refresh()');page.wait_for_function('id=>researchState.researchSlot?.slot_id===id',arg=surviving['slot_id'])
            assert not any('/create' in url for _,url,_ in trace)
            assert not any('DeliverMessage' in url for _,url,_ in trace)
            page.reload();mount()
            page.wait_for_function('id=>chat.target()?.resident_id===id',arg=surviving['resident_id'])
            assert page.locator('.paperResearchSelector select').input_value()==surviving['slot_id']
            assert page.locator('.paperResearchSelector select option').count()==2
            page.screenshot(path=str(tmp_path/'deleted-chat-recovered.png'))
            print('DELETE_UI_SCREENSHOT',tmp_path/'deleted-chat-recovered.png')
        finally:browser.close()
