'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), http = require('node:http');
const {execFileSync} = require('node:child_process');
const {chatWorkspaceScripts} = require('./chat_workspace_modules.cjs');
const {portalWorkbenchScripts} = require('./portal_workbench_scripts.cjs');
const {LINEAGE_TEMPLATES_SCRIPTS} = require('./lineage_templates_scripts.cjs');
const repo = path.resolve(__dirname, '../../..');
const {chromium} = require(path.join(repo, 'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const modules = [...new Set(['portal_state.js','portal_page_state.js','portal_text.js','portal_i18n.js',
 'portal_ui_primitives.js','portal_ui.js','portal_overlay.js','portal_resource_chips.js','portal_image_editor.js',
 'portal_editor_open_service.js','portal_command_registry.js','portal_workbench_context.js',...portalWorkbenchScripts(),
 'resident_protocol.js',...chatWorkspaceScripts(),...LINEAGE_TEMPLATES_SCRIPTS.map(n=>n+'.js'),
 'hatg_panel_template_shelf.js','lineage_workbench_selection.js'])];
const routes = execFileSync(process.env.PYTHON || 'python3', ['-c',
 'from management_portal.resources.portal import portal_route_script; print(portal_route_script().decode())'],{cwd:repo,encoding:'utf8'});
(async()=>{
 const server=http.createServer((req,res)=>{
  const name=new URL(req.url,'http://fixture').pathname.slice(1);
  const css=/^[a-z_]+\.css$/.test(name);
  const body=!name ? '<!doctype html><meta charset="utf-8">'+['portal_design_system','portal_components','portal_components_chat_frame','portal_components_composer','portal_components_resource_chips','lineage_templates'].map(n=>`<link rel="stylesheet" href="/${n}.css">`).join('')+'<style>#templateShelf button,#hatgCanvas button{margin:15px;padding:20px}.composerInput{min-height:120px;border:1px solid}#chat{max-width:800px;height:450px}</style><div id="templateShelf"></div><div id="hatgCanvas"><button data-node-id="resident-a">Resident A</button></div><main id="chat"></main><script src="/routes.js"></script>'+modules.map(n=>`<script src="/${n}"></script>`).join('')
   : name==='routes.js'?routes:modules.includes(name)||css?fs.readFileSync(path.join(repo,'management_portal/static',name)):null;
  res.writeHead(body===null?404:200,{'Content-Type':!name?'text/html':css?'text/css':'text/javascript'});res.end(body||'');
 });
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const browser=await chromium.launch({headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1300,height:1000}}),errors=[];
  page.setDefaultTimeout(5000);
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:'+server.address().port);
  await page.evaluate(async()=>{
   window.sent=[];window.calls=[];window.opened=[];window.scope='resident-a';window.dragEvents=[];
   for(const type of ['dragstart','dragover','drop']) document.addEventListener(type,event=>{
    dragEvents.push({type,tag:event.target.outerHTML.slice(0,200),types:[...(event.dataTransfer?.types||[])],effect:event.dataTransfer?.effectAllowed,text:event.dataTransfer?.getData('text/plain')});
   });
   window.workspace=PortalChatWorkspace.createWorkspace({root:document.querySelector('#chat'),id:'prompt-test',
    historyScope:()=>scope,draftOwner:'fixture',onSend:p=>sent.push(p),
    uploadAttachment:async()=>({upload_id:'upload_fixture',filename:'audio.wav'}),
    contract:{schema:'portal.chat_workspace.v3',messages:[],composer:{can_send:true,can_attach:true,attachment_upload_context:{conversation:scope},queue:{items:[]}}}});
   workspace.setDraftText('已有草稿\n');
   workspace.setAttachments([{upload_id:'upload_fixture',filename:'audio.wav',content_type:'audio/wav',size:7}]);
   const template={template_id:'template_prompt',kind:'prompt',title:'录音整理',category:'apply',targets:['resident'],
    parameters:[{name:'kind',description:'条目类型'}],body:{message:'整理 [音频文件] 为 {{param.kind}}。<img src=x onerror=alert(1)>\n保留原意。'},behavior:{steps:[],persistent:false}};
   const structure={template_id:'template_structure',kind:'structure',title:'结构',category:'structure',targets:['resident','selection'],parameters:[],body:{roles:[],components:[],wires:[]},behavior:{steps:[],persistent:true}};
   const request=async(instruction,args)=>{calls.push(instruction);return instruction==='ListTemplates'?{templates:[template,structure],kinds:[]}:instruction==='GetTemplate'?{template:args.template_id==='template_prompt'?template:structure}:{};};
   window.PortalInstructionClient={execute:request};
   window.host={hatgComponentOptions:{emit:async(event,intent)=>{opened.push(intent);return selection.activateLineageChatWorkspace(panel,intent.resource,intent);}}};
   window.panel={workspace:{...workspace,focusComposer:()=>workspace.els.input.focus()},target:()=>({resident_id:scope,lineage_id:'lineage-fixture'}),isReadyFor:()=>true};
   window.currentPanel=panel;
   const part=PortalLineageWorkbenchSelection;
   const deps=Object.fromEntries(part.dependencies.map(k=>[k,()=>{}]));
   Object.assign(deps,{state:{chatTarget:{lineage_id:'lineage-fixture'}},bindLineageChatWorkspace:()=>{},
    currentAgentChatPanel:()=>currentPanel,currentLineageWorkbench:()=>null,
    activeChatTargetId:()=>scope,residentChatTarget:(id,lineage_id)=>({id,key:id,lineage_id}),
    resolveResidentNavigationTarget:async t=>t,lineageHatgTargetMatches:()=>true});
   window.selection=part.createSelection(deps);
   PortalHatgPanelTemplateShelf.createTemplateShelf({$id:id=>document.getElementById(id),
    applyProgramMutation:fn=>fn(),executeHatgInstruction:request,nodeById:id=>id==='resident-a'?{id,title:'Resident A'}:null,
    panelHost:host,renderGraph:()=>{},state:{lineage:{lineage_id:'lineage-fixture',nodes:[]},selectedCanvasObjectIds:new Set(['resident-a','resident-b'])},toast:()=>{}}).mountTemplates();
   await host.templateController.load();
   workspace.els.input.focus();
   const range=document.createRange();range.selectNodeContents(workspace.els.input);range.collapse(false);
   getSelection().removeAllRanges();getSelection().addRange(range);
  });
  async function dragTemplate(id, target) {
   const from=await page.locator(`[data-template-id="${id}"]`).boundingBox(),to=await page.locator(target).boundingBox();
   console.log('drag geometry',JSON.stringify({from,to,hit:await page.evaluate(({x,y})=>document.elementFromPoint(x,y)?.outerHTML.slice(0,400),{x:to.x+to.width/2,y:to.y+to.height/2})}));
   await page.mouse.move(from.x+from.width/2,from.y+from.height/2);
   await page.mouse.down();
   await page.mouse.move(to.x+to.width/2,to.y+to.height/2,{steps:12});
   await page.mouse.move(to.x+to.width/2+1,to.y+to.height/2+1);
   await page.mouse.up();
  }
  await dragTemplate('template_prompt','.composerInput');
  if(process.env.PROMPT_DRAFT_SCREENSHOT)await page.screenshot({path:process.env.PROMPT_DRAFT_SCREENSHOT});
  const first=await page.evaluate(()=>({text:workspace.draftText(),attachments:workspace.attachments().length,sent:sent.length,html:workspace.els.input.querySelectorAll('img[src="x"]').length,calls,dragEvents}));
  assert.ok(first.text.includes('已有草稿')&&first.text.includes('[音频文件]')&&first.text.includes('[条目类型]'),JSON.stringify(first));
  assert.ok(first.text.includes('<img src=x onerror=alert(1)>'),'text remains literal');
  assert.equal(first.html,0);assert.equal(first.attachments,1);assert.equal(first.sent,0);assert.ok(!first.calls.includes('ApplyTemplate'));
  // A copied prompt is one undoable edit; existing text and audio survive.
  await page.locator('.composerInput').press('Control+z');
  assert.ok(!(await page.evaluate(()=>workspace.draftText())).includes('[音频文件]'));
  assert.equal(await page.evaluate(()=>workspace.attachments().length),1);
  await dragTemplate('template_prompt','[data-node-id="resident-a"]');
  await page.waitForFunction(()=>opened.length===1&&workspace.draftText().includes('[音频文件]'));
  assert.equal(await page.evaluate(()=>opened[0].resource.resident_id),'resident-a');
  assert.equal(await page.evaluate(()=>sent.length),0);
  assert.equal(await page.evaluate(()=>calls.filter(x=>x==='ApplyTemplate').length),0);
  // Navigation races do not append the prompt into a different active chat.
  await page.evaluate(async()=>{
   const before=workspace.draftText();currentPanel={...panel};
   await selection.activateLineageChatWorkspace(panel,{resident_id:'resident-a',lineage_id:'lineage-fixture'},{draftText:'MUST NOT INSERT'});
   if(workspace.draftText()!==before)throw new Error('stale chat received draft');currentPanel=panel;
   workspace.saveDraft({immediate:true});scope='resident-b';workspace.setComposer({can_send:true});
  });
  assert.ok(!(await page.evaluate(()=>workspace.draftText())).includes('[音频文件]'));
  await page.evaluate(()=>{scope='resident-a';workspace.setComposer({can_send:true});});
  assert.ok((await page.evaluate(()=>workspace.draftText())).includes('[音频文件]'),'draft restored in original chat');
  await dragTemplate('template_structure','[data-node-id="resident-a"]');
  await page.waitForFunction(()=>calls.includes('ApplyTemplate'));
  assert.equal(await page.evaluate(()=>opened.length),1,'structure keeps its original application path');
  assert.equal(await page.evaluate(()=>sent.length),0);
  if(process.env.PROMPT_DRAFT_SCREENSHOT)await page.screenshot({path:process.env.PROMPT_DRAFT_SCREENSHOT});
  assert.deepEqual(errors,[]);
  console.log('PASS native shelf drag to chat and node; placeholders, literal text, undo, audio, scoped draft restore, stale target and structure application');
 }finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(e=>{console.error(e);process.exitCode=1;});
