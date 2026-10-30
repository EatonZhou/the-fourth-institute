'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),{execFileSync}=require('node:child_process');
const code=process.env.TAU_CODE_ROOT||path.resolve(__dirname,'../../..');
const {chatWorkspaceScripts}=require('./chat_workspace_modules.cjs');
const {portalWorkbenchScripts}=require('./portal_workbench_scripts.cjs');
const {PORTAL_COMPONENTS_STYLES}=require(path.join(code,'tests/cases/javascript/portal_components_styles.cjs'));
const helper=process.env.TAU_WATCHBOARD_MESSAGE_HELPER||path.join(code,'management_portal/test_framework/tau_watchboard_message_contract');
const {readWatchboardComposer,readWatchboardMessageFold,readWatchboardForkWorkspace}=require(helper);
const {chromium}=require(path.join(code,'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const rid='resident_123456789abc',lineage='lineage_abcdef123456',key='msg_fixture_inherited';
const text='布局验收样本：'+Array.from({length:12},(_,i)=>'第'+(i+1)+'段用于检查窄栏中文换行与正文滚动；只需回复 ACK。').join('\n');
const oldOwned='workspace_111111111111',newOwned='workspace_222222222222';
const bindings=[{workspace_id:newOwned,is_owned:true},{workspace_id:oldOwned,is_owned:false}];
assert.equal(readWatchboardForkWorkspace(bindings,{sourceWorkspaceId:oldOwned}).workspace_id,newOwned);
assert.equal(readWatchboardForkWorkspace(bindings,{sourceWorkspaceId:oldOwned}).ready,true);
for(const b of [[],[{workspace_id:oldOwned,is_owned:true}],[...bindings,{workspace_id:'workspace_333333333333',is_owned:true}]])
 assert.equal(readWatchboardForkWorkspace(b,{sourceWorkspaceId:oldOwned}).ready,false);
const routeScript=execFileSync(process.env.TAU_PYTHON||'python3',['-c','from management_portal.resources.portal import portal_route_script; print(portal_route_script().decode())'],{cwd:code,encoding:'utf8'});
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
 try{
  const context=await browser.newContext(),page=await context.newPage(),calls=[],errors=[];
  const message={message_id:key,role:'user',author_id:'fixture',created_at:'2026-10-25T00:00:00Z',content_parts:[{type:'text',text:text.repeat(6)}]};
  await context.route('**/*',async route=>{
   const req=route.request(),url=new URL(req.url());
   if(url.pathname==='/'){await route.fulfill({contentType:'text/html',body:'<!doctype html><html lang="en"><body><section id="fixture"></section></body></html>'});return;}
   if(url.pathname.startsWith('/static/')){const file=path.join(code,'management_portal/static',path.basename(url.pathname));if(!fs.existsSync(file))throw Error('Missing fixture static file '+url.pathname);await route.fulfill({contentType:url.pathname.endsWith('.css')?'text/css':'text/javascript',body:fs.readFileSync(file,'utf8')});return;}
   if(req.method()!=='POST'){await route.fulfill({json:{restart:null}});return;}
   const body=req.postDataJSON();calls.push(body);const args=body.arguments;
   let result;
   if(body.instruction==='GetConversation')result={resident:{resident_id:rid},messages:[message],cursor:1,state:{work_state:'idle'},controls:{can_send:true},queue:{items:[]}};
   else if(body.instruction==='ListInputHistory')result={items:[]};
   else if(body.instruction==='DeliverMessage')result={resident_id:rid,message_id:args.message.message_id,accepted:true,input_state:{state:'queued'}};
   else throw Error('Unexpected fixture instruction '+body.instruction);
   await route.fulfill({json:{instruction:body.instruction,instruction_id:'inst_fixture',status:'committed',replayed:false,result}});
  });
  page.on('pageerror',e=>errors.push(e.message));await page.goto('http://watchboard.fixture.invalid/');
  await page.evaluate(()=>{
   window.PortalIdentity={username:()=> 'fixture'};
   window.PortalEvents={isConnected:()=>true,connect(){}};
   window.PortalAPI={get:url=>fetch(url).then(r=>r.json())};
   window.PortalInstructionClient={execute:(instruction,args)=>fetch('/api/instructions/execute',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({instruction,arguments:args})}).then(r=>r.json()).then(r=>r.result)};
  });
  await page.addScriptTag({content:routeScript});
  for(const name of ['portal_state.js','portal_page_state.js','portal_text.js','portal_ui_primitives.js','portal_ui.js','portal_overlay.js','portal_resource_chips.js','portal_image_editor.js','portal_editor_open_service.js','portal_command_registry.js','portal_workbench_context.js',...portalWorkbenchScripts(path.join(code,'management_portal/static')),'portal_resident_service.js','resident_protocol.js',...chatWorkspaceScripts(path.join(code,'management_portal/static'))])
   await page.addScriptTag({path:path.join(code,'management_portal/static',name)});
  for(const name of PORTAL_COMPONENTS_STYLES)await page.addStyleTag({path:path.join(code,'management_portal/static',name+'.css')});
  await page.addStyleTag({content:'#fixture{width:320px;height:800px}.chatMessages{height:450px;overflow:auto}'});
  await page.evaluate(async({rid,lineage})=>{
   window.panel=PortalChatWorkspace.panelPackage.create({root:document.getElementById('fixture'),id:'fixture-chat',autoLoad:false,target:{resident_id:rid,lineage_id:lineage}});
   await panel.setResident(panel.target(),{detail:{resident:{resident_id:rid,lineage_id:lineage},chat_workspace:{schema:'portal.chat_workspace.v3',target:{kind:'resident',id:rid,lineage_id:lineage},messages:[],composer:{can_send:true}}}});
  },{rid,lineage});
  const root=page.locator('#fixture'),input=root.getByRole('textbox');
  await input.fill(text);
  const beforeDom=await input.innerHTML(),beforeCalls=calls.length;
  const pre=await readWatchboardComposer(input,{text});assert(pre.ready);
  assert.equal(await input.innerHTML(),beforeDom);assert.equal(calls.length,beforeCalls);
  assert.equal((await readWatchboardComposer(input,{text:text+'WRONG'})).ready,false);
  const original=page.waitForResponse(r=>r.request().method()==='POST'&&r.request().postDataJSON()?.instruction==='DeliverMessage',{timeout:5000});original.catch(()=>{});
  const send=await page.evaluateHandle(()=>panel.workspace.els.send);await send.asElement().click({timeout:3000});await send.dispose();
  const response=await original,body=response.request().postDataJSON(),receipt=await response.json();
  assert.equal(body.arguments.resident_id,rid);assert.equal(body.arguments.message.content_parts[0].text,pre.draft_text);
  assert.equal(receipt.result.message_id,body.arguments.message.message_id);
  assert.equal(calls.filter(x=>x.instruction==='DeliverMessage').length,1);
  const expand=root.locator('article[data-message-key="'+key+'"] [data-message-fold-toggle]');
  await expand.waitFor();
  const oldName=await expand.innerText();
  const oldLocator=root.getByRole('button',{name:oldName,exact:true}).locator('xpath=ancestor::article[1]');
  const article=root.locator('article[data-message-key="'+key+'"]');
  const folded=await readWatchboardMessageFold(article,{messageKey:key});assert(folded.ready);assert.equal(folded.fold,'collapsed');
  const articleHtml=await article.innerHTML();await readWatchboardMessageFold(article,{messageKey:key});assert.equal(await article.innerHTML(),articleHtml);
  await expand.click();
  const expanded=await readWatchboardMessageFold(article,{messageKey:key});assert(expanded.ready);assert.equal(expanded.fold,'expanded');
  assert.notEqual(expanded.label,folded.label);assert.equal(await oldLocator.count(),0,'BB name-bound locator is lost after native toggle');
  assert(expanded.rect.height>folded.rect.height);
  await assert.rejects(readWatchboardMessageFold(article,{messageKey:'wrong'}),/identity/);
  await expand.click();assert.equal((await readWatchboardMessageFold(article,{messageKey:key})).fold,'collapsed');
  await page.evaluate(()=>panel.destroy());assert.deepEqual(errors,[]);await context.close();
  console.log(JSON.stringify({status:'passed',browser:'Chromium',isolated:true,business_acceptance:false,production_requests:0,
   native_composer:{intended_length:text.length,serialized_length:pre.draft_text.length,raw_fill_predicate_matches:text===pre.draft_text},
   checks:['native Chat fill and original one Send receipt','read-only native serialization preflight','fixed message identity survives native Expand/Collapse label and height changes','old name-bound locator reproduced','independent owned Fork Workspace distinguished from shared source binding','wrong identity and ambiguous owned binding rejected','no production requests or accepted-input replay']}));
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
